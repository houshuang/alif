"""Sweep every active reviewable sentence through the current verifier.

Usage:
    python3 backend/scripts/reverify_active_sentences.py [--dry-run]
                                                          [--batch-size 15]
                                                          [--limit N]
                                                          [--sentence-id ID]

Walks the entire active-reviewable corpus (the reviewability gate cohort),
batch-verifies every sentence via the current verifier + vocabulary, applies
confident corrections, deactivates anything that can't be repaired even by
the frequency-gated proposal path. Deactivated sentences are appended to
``data/logs/mapping_reverify_failures_<date>.jsonl`` for offline triage.

Run as a maintenance sweep when you want confidence that every visible
sentence has been checked. Free via Claude CLI; ~15-20 minutes for the
~1700-sentence corpus.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.activity_log import log_activity
from app.database import SessionLocal
from app.services.mapping_rescue import reverify_all_active_sentences


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Run the verifier but don't deactivate or re-stamp anything.")
    parser.add_argument("--batch-size", type=int, default=15,
                        help="Sentences per LLM call (default 15).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Optional cap on number of sentences to verify (for spot checks).")
    parser.add_argument("--sentence-id", type=int, action="append", default=None,
                        help="Restrict to specific sentence IDs (repeatable).")
    args = parser.parse_args()

    ids: list[int] | None = None
    if args.sentence_id:
        ids = list(args.sentence_id)
    elif args.limit:
        # Materialize the eligible list, then slice.
        from app.services.mapping_rescue import _all_active_reviewable_sentence_ids
        db = SessionLocal()
        try:
            all_ids = _all_active_reviewable_sentence_ids(db)
        finally:
            db.close()
        ids = all_ids[: args.limit]

    print(f"Starting reverify (dry_run={args.dry_run}, batch_size={args.batch_size})")
    t0 = time.perf_counter()
    stats = reverify_all_active_sentences(
        batch_size=args.batch_size,
        sentence_ids=ids,
        dry_run=args.dry_run,
    )
    elapsed = time.perf_counter() - t0

    out = stats.to_dict()
    out["elapsed_seconds"] = round(elapsed, 1)
    print(json.dumps(out, indent=2))

    if not args.dry_run and args.sentence_id is None and args.limit is None:
        # Real corpus-wide run — log it for the activity feed.
        db = SessionLocal()
        try:
            log_activity(
                db, "manual_action",
                f"Reverify sweep: {stats.sentences_attempted} attempted, "
                f"{stats.sentences_passed} passed, "
                f"{stats.sentences_corrected} corrected, "
                f"{stats.sentences_unfixable} unfixable ({stats.positions_nulled} positions NULL'd)",
                detail=out,
            )
            db.commit()
        finally:
            db.close()


if __name__ == "__main__":
    main()
