"""Backfill missed assumed-known words from interaction JSONL logs.

Before 2026-05-24, sentence review skipped words that were marked ``known``
without an FSRS card. If the learner marked one of those scaffold words missed,
the sentence-level interaction log kept ``missed_lemma_ids`` but no per-word
ReviewLog or acquisition transition was written. This script recovers those
misses and moves still-assumed-known rows into Box 1 acquisition.

Usage::

    .venv/bin/python scripts/backfill_missed_assumed_known.py --language el --dry-run
    .venv/bin/python scripts/backfill_missed_assumed_known.py --language el --apply
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Lemma, ReviewLog, UserLemmaKnowledge  # noqa: E402
from app.services.canonical_resolution import resolve_canonical_lemma_id  # noqa: E402
from app.services.knowledge_lifecycle import (  # noqa: E402
    ORIGIN_MARKED_UNKNOWN,
    record_failure,
    snapshot as lifecycle_snapshot,
)
from app.services.lemma_quality import is_noncontent_lemma  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _iter_log_events(log_dir: Path) -> Iterable[tuple[Path, int, dict[str, Any]]]:
    for path in sorted(log_dir.glob("interactions_*.jsonl*")):
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        yield path, line_no, {"_parse_error": True}
                        continue
                    yield path, line_no, payload
        except OSError as e:
            log.warning("Could not read %s: %s", path, e)


def _review_id(event: dict[str, Any], lemma_id: int, path: Path, line_no: int) -> str:
    client_id = event.get("client_review_id")
    if isinstance(client_id, str) and client_id:
        base = client_id[-24:]
        return f"bf-miss:{base}:{lemma_id}"[-50:]
    sentence_id = event.get("sentence_id") or "s"
    ts = int(_parse_ts(event.get("ts")).timestamp())
    fallback = f"bf-miss:{sentence_id}:{lemma_id}:{ts}:{line_no}"
    return fallback[-50:]


def backfill_from_logs(
    db,
    *,
    log_dir: Path,
    language_code: str | None,
    apply_changes: bool,
) -> Counter:
    counts: Counter = Counter()

    for path, line_no, event in _iter_log_events(log_dir):
        if event.get("_parse_error"):
            counts["parse_errors"] += 1
            continue
        if event.get("event") != "sentence_review":
            continue
        if language_code and event.get("language_code") != language_code:
            counts["skipped_language"] += 1
            continue

        missed = event.get("missed_lemma_ids") or []
        if not isinstance(missed, list) or not missed:
            continue
        counts["events_with_misses"] += 1
        processed = {
            wr.get("lemma_id")
            for wr in event.get("word_results", []) or []
            if isinstance(wr, dict)
        }
        when = _parse_ts(event.get("ts"))

        for raw_lemma_id in missed:
            counts["missed_ids_seen"] += 1
            if not isinstance(raw_lemma_id, int):
                counts["skipped_bad_lemma_id"] += 1
                continue
            if raw_lemma_id in processed:
                counts["skipped_already_in_word_results"] += 1
                continue

            lemma_id = resolve_canonical_lemma_id(db, raw_lemma_id)
            lemma = db.get(Lemma, lemma_id)
            if lemma is None:
                counts["skipped_missing_lemma"] += 1
                continue
            if language_code and lemma.language_code != language_code:
                counts["skipped_language"] += 1
                continue
            if is_noncontent_lemma(lemma, language_code=lemma.language_code):
                counts["skipped_noncontent"] += 1
                continue

            ulk = (
                db.query(UserLemmaKnowledge)
                .filter(UserLemmaKnowledge.lemma_id == lemma_id)
                .first()
            )
            if ulk is None:
                counts["skipped_missing_ulk"] += 1
                continue
            if not (ulk.knowledge_state == "known" and ulk.fsrs_card_json is None):
                counts["skipped_not_assumed_known"] += 1
                continue

            client_review_id = _review_id(event, lemma_id, path, line_no)
            if db.query(ReviewLog).filter(ReviewLog.client_review_id == client_review_id).first():
                counts["skipped_existing_backfill_log"] += 1
                continue

            counts["eligible"] += 1
            log.info(
                "%s lemma_id=%s form=%s state=%s origin=%s event=%s:%d",
                "Would backfill" if not apply_changes else "Backfilling",
                lemma_id,
                lemma.lemma_form,
                ulk.knowledge_state,
                ulk.knowledge_origin,
                path.name,
                line_no,
            )
            if not apply_changes:
                continue

            old_times_seen = ulk.times_seen or 0
            old_times_correct = ulk.times_correct or 0
            old_total_encounters = ulk.total_encounters or 0
            old_state = ulk.knowledge_state
            old_lifecycle = lifecycle_snapshot(ulk)

            ulk.knowledge_state = "acquiring"
            ulk.acquisition_box = 1
            ulk.acquisition_next_due = datetime.now(timezone.utc)
            ulk.acquisition_started_at = when
            ulk.entered_acquiring_at = when
            if ulk.introduced_at is None:
                ulk.introduced_at = when
            ulk.fsrs_card_json = None
            ulk.times_seen = old_times_seen + 1
            ulk.total_encounters = old_total_encounters + 1
            ulk.source = "review_lapse"
            record_failure(ulk, when, origin=ORIGIN_MARKED_UNKNOWN)

            db.add(ReviewLog(
                lemma_id=lemma_id,
                rating=1,
                reviewed_at=when,
                context="backfill_missed_assumed_known",
                session_id=event.get("session_id"),
                review_mode=event.get("review_mode") or "reading",
                sentence_id=event.get("sentence_id"),
                is_acquisition=True,
                client_review_id=client_review_id,
                comprehension_signal=event.get("comprehension_signal"),
                credit_type="collateral",
                was_confused=False,
                fsrs_log_json={
                    "rating": 1,
                    "state": "acquiring",
                    "backfilled_from_interaction_log": True,
                    "log_file": path.name,
                    "log_line": line_no,
                    "pre_times_seen": old_times_seen,
                    "pre_times_correct": old_times_correct,
                    "pre_total_encounters": old_total_encounters,
                    "pre_knowledge_state": old_state,
                    "pre_card": None,
                    **old_lifecycle,
                },
            ))
            counts["updated"] += 1

    if apply_changes and counts["updated"]:
        db.commit()
    else:
        db.rollback()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Write changes. Default is dry-run.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only (default).")
    parser.add_argument("--language", default="el",
                        help="Limit to one language code; use '' for all.")
    parser.add_argument("--log-dir", type=Path, default=settings.log_dir,
                        help="Directory containing interactions_*.jsonl logs.")
    args = parser.parse_args()

    apply_changes = args.apply and not args.dry_run
    language_code = args.language or None
    db = SessionLocal()
    try:
        counts = backfill_from_logs(
            db,
            log_dir=args.log_dir,
            language_code=language_code,
            apply_changes=apply_changes,
        )
    finally:
        db.close()

    log.info("Summary: %s", dict(sorted(counts.items())))
    if not apply_changes:
        log.info("Dry-run: no changes written. Pass --apply to update rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
