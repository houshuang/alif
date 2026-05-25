"""Backfill scaffold confirmation from the review history.

Until 2026-05-25, a green collateral exposure on an assumed-known word
(knowledge_state='known' with no FSRS card) was discarded — only red misses
moved the word, so an assumption could only ever be disproved, never confirmed
(see polyglot CLAUDE.md Hard Invariant 6). The engine now records green
exposure as verification evidence; this script recovers the *historical*
confirmations from the logs so the existing pool isn't stuck at zero.

Evidence sources, in order of confidence:
  1. Interaction JSONL `sentence_review` events (full per-word detail: we know
     exactly which words were missed/confused).
  2. SentenceReviewLog rows carrying `missed_lemma_ids` in the DB (the new
     durable detail; empty for pre-2026-05-25 rows).
  3. SentenceReviewLog rows with comprehension='understood' NOT covered by (1):
     every content word was green, so no per-word detail is needed.

A `partial` review with no recoverable per-word detail is SKIPPED — we can't
tell survivors from misses, so we never guess a confirmation.

For each currently-assumed-known lemma (known, no card, confirmed_at IS NULL)
that has >=1 clean historical exposure, sets confirmed_at = earliest clean
exposure, clean_exposures = count, and bumps times_seen / distinct_contexts.
Idempotent: rows already confirmed (confirmed_at set) are skipped, so a re-run
is a no-op and it never double-counts against the live engine.

Usage::

    .venv/bin/python scripts/backfill_scaffold_confirmation.py --language el --dry-run
    .venv/bin/python scripts/backfill_scaffold_confirmation.py --language el --apply
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Lemma,
    SentenceReviewLog,
    SentenceWord,
    UserLemmaKnowledge,
)
from app.services.activity_log import log_activity  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _iter_log_events(log_dir: Path) -> Iterable[dict[str, Any]]:
    for path in sorted(log_dir.glob("interactions_*.jsonl*")):
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or '"sentence_review"' not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("event") == "sentence_review" and not event.get("duplicate"):
                        yield event
        except OSError as exc:
            log.warning("could not read %s: %s", path, exc)


def compute(db, language_code: str) -> tuple[dict[int, int], dict[int, datetime], dict[str, int]]:
    # assumed-known population eligible for backfill
    assumed = {
        lid for (lid,) in db.query(UserLemmaKnowledge.lemma_id)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            Lemma.language_code == language_code,
            UserLemmaKnowledge.knowledge_state == "known",
            UserLemmaKnowledge.fsrs_card_json.is_(None),
            UserLemmaKnowledge.confirmed_at.is_(None),
        ).all()
    }

    sent_lemmas: dict[int, set[int]] = defaultdict(set)
    for sid, lid in (
        db.query(SentenceWord.sentence_id, SentenceWord.lemma_id)
        .filter(SentenceWord.lemma_id.isnot(None)).all()
    ):
        sent_lemmas[sid].add(lid)

    exposures: Counter[int] = Counter()
    earliest: dict[int, datetime] = {}
    meta = Counter()

    def credit(sid: int, comp: str | None, missed, confused, ts: datetime | None):
        if comp == "no_idea" or not sid:
            return
        red = set(missed or []) | set(confused or [])
        survivors = (sent_lemmas.get(sid, set()) & assumed) - red
        for lid in survivors:
            exposures[lid] += 1
            if ts and (lid not in earliest or ts < earliest[lid]):
                earliest[lid] = ts

    # Source 1: JSONL events (full detail)
    seen_crids: set[str] = set()
    for ev in _iter_log_events(settings.log_dir):
        crid = ev.get("client_review_id")
        if crid:
            seen_crids.add(crid)
        credit(ev.get("sentence_id"), ev.get("comprehension_signal"),
               ev.get("missed_lemma_ids"), ev.get("confused_lemma_ids"),
               _parse_ts(ev.get("ts")))
        meta["jsonl_events"] += 1

    # Sources 2 & 3: DB SentenceReviewLog rows not already covered by JSONL.
    for row in db.query(SentenceReviewLog).all():
        if row.client_review_id and row.client_review_id in seen_crids:
            continue
        if row.missed_lemma_ids is not None or row.confused_lemma_ids is not None:
            credit(row.sentence_id, row.comprehension,
                   row.missed_lemma_ids, row.confused_lemma_ids, _parse_ts(row.reviewed_at))
            meta["db_detail_rows"] += 1
        elif row.comprehension == "understood":
            credit(row.sentence_id, "understood", [], [], _parse_ts(row.reviewed_at))
            meta["db_understood_rows"] += 1
        else:
            meta["db_partial_skipped"] += 1

    meta["assumed_pool"] = len(assumed)
    return dict(exposures), earliest, dict(meta)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit changes")
    parser.add_argument("--dry-run", action="store_true", help="report only (default)")
    parser.add_argument("--language", default="el")
    args = parser.parse_args()
    apply = args.apply and not args.dry_run

    with SessionLocal() as db:
        exposures, earliest, meta = compute(db, args.language)
        dist = Counter("1" if c == 1 else "2" if c == 2 else "3-4" if c <= 4 else "5+"
                       for c in exposures.values())
        total_exp = sum(exposures.values())
        log.info("assumed-known pool (unconfirmed): %s", meta.get("assumed_pool"))
        log.info("evidence rows — jsonl:%s db_detail:%s db_understood:%s partial_skipped:%s",
                 meta.get("jsonl_events", 0), meta.get("db_detail_rows", 0),
                 meta.get("db_understood_rows", 0), meta.get("db_partial_skipped", 0))
        log.info("confirmable words: %s  | total clean exposures: %s  | dist: %s",
                 len(exposures), total_exp, dict(dist))

        if not apply:
            log.info("DRY RUN — no changes written. Re-run with --apply to commit.")
            return 0

        now = datetime.now(timezone.utc)
        changed = 0
        for lid, count in exposures.items():
            ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lid).first()
            if not ulk or ulk.confirmed_at is not None or ulk.fsrs_card_json is not None:
                continue  # idempotent guard
            ulk.confirmed_at = earliest.get(lid) or now
            ulk.clean_exposures = (ulk.clean_exposures or 0) + count
            ulk.times_seen = (ulk.times_seen or 0) + count
            ulk.distinct_contexts = (ulk.distinct_contexts or 0) + count
            changed += 1

        # Reader-mark confirmations: pre_known rows are words the user marked
        # known while reading — reader exposure is confirmation (surface-agnostic
        # with sentence review). Stamp confirmed_at at their mark date so they
        # join the exposure-confirmed tier.
        reader_marks = (
            db.query(UserLemmaKnowledge)
            .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
            .filter(
                Lemma.language_code == args.language,
                UserLemmaKnowledge.knowledge_state == "known",
                UserLemmaKnowledge.fsrs_card_json.is_(None),
                UserLemmaKnowledge.knowledge_origin == "pre_known",
                UserLemmaKnowledge.confirmed_at.is_(None),
            ).all()
        )
        reader_confirmed = 0
        for ulk in reader_marks:
            ulk.confirmed_at = ulk.introduced_at or now
            ulk.clean_exposures = max(ulk.clean_exposures or 0, 1)
            reader_confirmed += 1

        db.commit()
        log.info("APPLIED — confirmed %s assumed-known (logs) + %s reader-mark words",
                 changed, reader_confirmed)
        changed += reader_confirmed
        try:
            log_activity(
                db,
                event_type="scaffold_confirmation_backfill",
                summary=f"Backfilled scaffold confirmation: {changed} words confirmed "
                        f"from {total_exp} historical clean exposures",
                detail={"confirmed": changed, "total_exposures": total_exp,
                        "distribution": dict(dist), **meta},
                language_code=args.language,
            )
        except Exception as exc:  # logging must never fail the backfill
            log.warning("activity log failed: %s", exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
