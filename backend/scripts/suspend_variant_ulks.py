"""Cleanup script: suspend variant `UserLemmaKnowledge` rows whose canonical is known/learning,
and remap `SentenceWord.lemma_id` + `Sentence.target_lemma_id` from variant to canonical.

Background (2026-05-06): A user-visible bug — sentence `أُمِّي تُحِبُّ الشِّوَاءَ عادَةً.`
kept appearing in review (8× shown, all "understood") because variant lemma #71
(`أُمّي`) had its own ULK in acquiring box-1 while canonical #76 (`أُمّ`) was
already known. Review credit was correctly going to canonical, but variant's
own box never advanced — the screenshot showed "Rescue (recently shown)" forever.

Found 36 such variants in production. This script:
1. Finds all variants where canonical's ULK state is `known`/`learning` (multi-hop).
2. Suspends those variant ULKs (keeps the row for audit; clears acquisition fields).
3. Remaps `SentenceWord.lemma_id` and `Sentence.target_lemma_id` from variant → canonical.
4. Logs the action via ActivityLog.

Run with `--dry-run` first to inspect counts. The companion code change in
`canonical_resolution.py` + `sentence_selector.build_session()` prevents
new variant ULKs from being created post-deploy.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict

from sqlalchemy import update

from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.canonical_resolution import resolve_canonical_via_map
from app.services.activity_log import log_activity

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("suspend_variant_ulks")


def find_overshadowed_variants(db) -> dict[int, int]:
    """Return {variant_lemma_id: canonical_lemma_id} for ULKs that should be suspended.

    Multi-hop: follows the chain to the root canonical. A variant is
    overshadowed when the root canonical's ULK is `known` or `learning`.
    """
    canonical_chain: dict[int, int | None] = {
        row.lemma_id: row.canonical_lemma_id
        for row in db.query(Lemma.lemma_id, Lemma.canonical_lemma_id).all()
    }
    knowledge_state_by_id: dict[int, str] = {
        row.lemma_id: row.knowledge_state
        for row in db.query(UserLemmaKnowledge.lemma_id, UserLemmaKnowledge.knowledge_state).all()
    }

    overshadowed: dict[int, int] = {}
    for lemma_id, state in knowledge_state_by_id.items():
        if state == "suspended":
            continue
        canon_id = resolve_canonical_via_map(lemma_id, canonical_chain)
        if canon_id == lemma_id:
            continue  # canonical itself
        if knowledge_state_by_id.get(canon_id) in ("known", "learning"):
            overshadowed[lemma_id] = canon_id
    return overshadowed


def remap_sentence_words(db, variant_to_canonical: dict[int, int], dry_run: bool) -> int:
    """Redirect SentenceWord.lemma_id from variant → canonical. Returns affected row count."""
    if not variant_to_canonical:
        return 0
    total = 0
    for variant_id, canonical_id in variant_to_canonical.items():
        q = db.query(SentenceWord).filter(SentenceWord.lemma_id == variant_id)
        affected = q.count()
        if affected == 0:
            continue
        total += affected
        if not dry_run:
            db.execute(
                update(SentenceWord)
                .where(SentenceWord.lemma_id == variant_id)
                .values(lemma_id=canonical_id)
            )
    return total


def remap_sentence_targets(db, variant_to_canonical: dict[int, int], dry_run: bool) -> int:
    """Redirect Sentence.target_lemma_id from variant → canonical. Returns affected row count."""
    if not variant_to_canonical:
        return 0
    total = 0
    for variant_id, canonical_id in variant_to_canonical.items():
        q = db.query(Sentence).filter(Sentence.target_lemma_id == variant_id)
        affected = q.count()
        if affected == 0:
            continue
        total += affected
        if not dry_run:
            db.execute(
                update(Sentence)
                .where(Sentence.target_lemma_id == variant_id)
                .values(target_lemma_id=canonical_id)
            )
    return total


def suspend_variants(db, variant_to_canonical: dict[int, int], dry_run: bool) -> int:
    """Suspend the variant ULKs. Preserves the row + audit fields."""
    if not variant_to_canonical:
        return 0
    count = 0
    for variant_id in variant_to_canonical:
        ulk = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id == variant_id)
            .first()
        )
        if not ulk:
            continue
        count += 1
        if not dry_run:
            ulk.knowledge_state = "suspended"
            ulk.acquisition_box = None
            ulk.acquisition_next_due = None
            ulk.acquisition_started_at = None
            ulk.entered_acquiring_at = None
            ulk.fsrs_card_json = None
    return count


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Print counts without writing.")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        overshadowed = find_overshadowed_variants(db)
        if not overshadowed:
            logger.info("No overshadowed variants found. Nothing to do.")
            return

        # Pretty preview
        canon_count = defaultdict(int)
        for v, c in overshadowed.items():
            canon_count[c] += 1
        logger.info(
            "Found %d overshadowed variants across %d canonicals.",
            len(overshadowed), len(canon_count),
        )
        for v, c in list(overshadowed.items())[:20]:
            v_lemma = db.query(Lemma).filter(Lemma.lemma_id == v).first()
            c_lemma = db.query(Lemma).filter(Lemma.lemma_id == c).first()
            logger.info(
                "  variant #%d (%s) → canonical #%d (%s)",
                v, v_lemma.lemma_ar if v_lemma else "?",
                c, c_lemma.lemma_ar if c_lemma else "?",
            )
        if len(overshadowed) > 20:
            logger.info("  …and %d more", len(overshadowed) - 20)

        suspended = suspend_variants(db, overshadowed, args.dry_run)
        sw_remapped = remap_sentence_words(db, overshadowed, args.dry_run)
        st_remapped = remap_sentence_targets(db, overshadowed, args.dry_run)

        logger.info(
            "%s: %d ULKs to suspend, %d sentence_word rows to remap, %d sentence target_lemma_id to remap.",
            "DRY-RUN" if args.dry_run else "APPLIED",
            suspended, sw_remapped, st_remapped,
        )

        if not args.dry_run:
            db.commit()
            log_activity(
                db,
                event_type="manual_action",
                summary=(
                    f"Suspended {suspended} variant ULKs whose canonical is known/learning, "
                    f"remapped {sw_remapped} sentence_word rows + {st_remapped} sentence targets."
                ),
                detail={
                    "variant_count": suspended,
                    "sentence_word_remapped": sw_remapped,
                    "sentence_target_remapped": st_remapped,
                    "script": "suspend_variant_ulks.py",
                },
            )
            db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
