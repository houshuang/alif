#!/usr/bin/env python3
"""Link three Greek participle/verbal-adjective lemmas to their canonical verbs.

Audit 2026-05-27 found three Modern Greek lemmas standing alone with no
``canonical_lemma_id``, where they are in fact inflections of an already-known
verb. Sentence-gen kept failing for them in the warm cache because the LLM was
producing sentences with the *verb* surface and the deterministic validator
treated those as "target missing":

  699  στρωμένος (passive participle "covered, spread") → 3664 στρώνω
  2756 σπασμένος (passive participle "broken")          → 1611 σπάζω
  331  σπαρτός   (verbal adjective in -τός, "sown")     → 5377 σπέρνω

σπαρτός is borderline — some dictionaries treat -τός verbal adjectives as
standalone adjective lemmas. Pass ``--skip-borderline`` to fix only the two
clear-cut participles.

The fix per pair:

1. Migrate the variant's ``user_lemma_knowledge`` row to the canonical (merge
   weights if both exist, reassign if only the variant has one).
2. Repoint ``sentence_words.lemma_id`` and ``page_words.lemma_id`` — direct
   tables that ``material_generator._observed_surfaces_for_lemmas`` reads
   without canonical resolution. After repoint, ``σπασμένο`` becomes a known
   form of ``σπάζω`` in the validator's eyes.
3. Repoint ``review_log.lemma_id`` and ``sentences.target_lemma_id`` for the
   same reason (review history credits the verb; the sentence selector picks
   for the verb).
4. Set ``lemmas.canonical_lemma_id`` so the variant is now a redirect.

Usage:
    python3 scripts/cleanup_participle_variants.py --dry-run
    python3 scripts/cleanup_participle_variants.py
    python3 scripts/cleanup_participle_variants.py --skip-borderline
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

POLYGLOT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POLYGLOT_ROOT))

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Lemma, UserLemmaKnowledge  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402


REPORT_FILE = POLYGLOT_ROOT / "data" / "participle_variant_cleanup.json"


# (variant_id, canonical_id, variant_bare, canonical_bare, note, borderline)
LINKS: list[tuple[int, int, str, str, str, bool]] = [
    (699,  3664, "στρωμενος", "στρωνω",
     "στρωμένος → στρώνω (passive participle, 'spread')",     False),
    (2756, 1611, "σπασμενος", "σπαζω",
     "σπασμένος → σπάζω  (passive participle, 'broken')",     False),
    (331,  5377, "σπαρτος",   "σπερνω",
     "σπαρτός  → σπέρνω (verbal adjective -τός, 'sown')",     True),
]


def reassign_refs(db, variant_id: int, canonical_id: int) -> dict[str, int]:
    sw = db.execute(
        text("UPDATE sentence_words SET lemma_id=:c WHERE lemma_id=:v"),
        {"v": variant_id, "c": canonical_id},
    ).rowcount or 0
    pw = db.execute(
        text("UPDATE page_words SET lemma_id=:c WHERE lemma_id=:v"),
        {"v": variant_id, "c": canonical_id},
    ).rowcount or 0
    rl = db.execute(
        text("UPDATE review_log SET lemma_id=:c WHERE lemma_id=:v"),
        {"v": variant_id, "c": canonical_id},
    ).rowcount or 0
    st = db.execute(
        text("UPDATE sentences SET target_lemma_id=:c WHERE target_lemma_id=:v"),
        {"v": variant_id, "c": canonical_id},
    ).rowcount or 0
    return {"sentence_words": sw, "page_words": pw,
            "review_logs": rl, "sent_targets": st}


def merge_or_move_ulk(db, variant_id: int, canonical_id: int) -> dict[str, Any]:
    variant_ulk = (db.query(UserLemmaKnowledge)
                   .filter(UserLemmaKnowledge.lemma_id == variant_id).first())
    canon_ulk = (db.query(UserLemmaKnowledge)
                 .filter(UserLemmaKnowledge.lemma_id == canonical_id).first())
    if variant_ulk is None:
        return {"action": "no_variant_ulk_noop"}
    if canon_ulk is None:
        variant_ulk.lemma_id = canonical_id
        return {"action": "reassigned_variant_ulk_to_canonical",
                "times_seen": variant_ulk.times_seen}
    v_seen = variant_ulk.times_seen or 0
    c_seen = canon_ulk.times_seen or 0
    canon_ulk.times_seen = v_seen + c_seen
    canon_ulk.times_correct = (canon_ulk.times_correct or 0) + (variant_ulk.times_correct or 0)
    canon_ulk.total_encounters = (canon_ulk.total_encounters or 0) + (variant_ulk.total_encounters or 0)
    if v_seen > c_seen and variant_ulk.fsrs_card_json:
        canon_ulk.fsrs_card_json = variant_ulk.fsrs_card_json
        canon_ulk.knowledge_state = variant_ulk.knowledge_state
        canon_ulk.acquisition_box = variant_ulk.acquisition_box
        canon_ulk.acquisition_next_due = variant_ulk.acquisition_next_due
        if variant_ulk.last_reviewed:
            canon_ulk.last_reviewed = variant_ulk.last_reviewed
    db.delete(variant_ulk)
    return {"action": "merged_variant_into_canonical_ulk",
            "merged_times_seen": canon_ulk.times_seen}


def process_link(db, variant_id: int, canonical_id: int, vbare: str, cbare: str,
                 note: str, dry_run: bool) -> dict[str, Any]:
    variant = db.get(Lemma, variant_id)
    canon = db.get(Lemma, canonical_id)
    if variant is None or canon is None:
        return {"variant_id": variant_id, "canonical_id": canonical_id,
                "outcome": "missing_row",
                "exists": {"variant": variant is not None,
                           "canonical": canon is not None}}
    if variant.lemma_bare != vbare:
        return {"variant_id": variant_id, "outcome": "variant_bare_mismatch",
                "expected": vbare, "actual": variant.lemma_bare}
    if canon.lemma_bare != cbare:
        return {"variant_id": variant_id, "canonical_id": canonical_id,
                "outcome": "canonical_bare_mismatch",
                "expected": cbare, "actual": canon.lemma_bare}
    if variant.canonical_lemma_id is not None:
        return {"variant_id": variant_id,
                "outcome": "already_linked",
                "current_canonical": variant.canonical_lemma_id}
    sw = db.execute(text("SELECT COUNT(*) FROM sentence_words WHERE lemma_id=:v"),
                    {"v": variant_id}).scalar()
    pw = db.execute(text("SELECT COUNT(*) FROM page_words WHERE lemma_id=:v"),
                    {"v": variant_id}).scalar()
    rl = db.execute(text("SELECT COUNT(*) FROM review_log WHERE lemma_id=:v"),
                    {"v": variant_id}).scalar()
    st = db.execute(text("SELECT COUNT(*) FROM sentences WHERE target_lemma_id=:v"),
                    {"v": variant_id}).scalar()
    ulk = db.execute(text("SELECT COUNT(*) FROM user_lemma_knowledge WHERE lemma_id=:v"),
                     {"v": variant_id}).scalar()
    if dry_run:
        return {"variant_id": variant_id, "canonical_id": canonical_id,
                "outcome": "dry_run", "note": note,
                "stale": {"sw": sw, "pw": pw, "rl": rl, "st": st, "ulk": ulk}}
    refs = reassign_refs(db, variant_id, canonical_id)
    ulk_action = merge_or_move_ulk(db, variant_id, canonical_id)
    variant.canonical_lemma_id = canonical_id
    return {"variant_id": variant_id, "canonical_id": canonical_id,
            "outcome": "linked", "note": note, "refs": refs,
            "ulk": ulk_action, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would change, write nothing.")
    p.add_argument("--skip-borderline", action="store_true",
                   help="Skip σπαρτός (verbal adjective, borderline).")
    args = p.parse_args()

    links = [item for item in LINKS
             if not (args.skip_borderline and item[5])]

    print(f"=== participle variant cleanup ({'DRY RUN' if args.dry_run else 'APPLY'}) ===",
          flush=True)
    print(f"  processing {len(links)} variant→canonical pairs", flush=True)
    log: list[dict] = []
    canonicals_touched: set[int] = set()
    with SessionLocal() as db:
        for vid, cid, vbare, cbare, note, _borderline in links:
            entry = process_link(db, vid, cid, vbare, cbare, note, args.dry_run)
            log.append(entry)
            print(f"  #{vid} -> #{cid}: {entry['outcome']}", flush=True)
            if entry["outcome"] == "linked":
                canonicals_touched.add(cid)
        if not args.dry_run:
            db.commit()
            if canonicals_touched:
                log_activity(
                    db,
                    event_type="participle_variants_linked",
                    summary=f"Linked {len(canonicals_touched)} Greek participle "
                            f"variants to verb canonicals",
                    language_code="el",
                    detail={"links": log,
                            "canonicals_touched": sorted(canonicals_touched)},
                )
                db.commit()

    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(json.dumps(
        {"dry_run": args.dry_run, "links": log,
         "canonicals_touched": sorted(canonicals_touched)},
        ensure_ascii=False, indent=2))
    print(f"\nReport: {REPORT_FILE}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
