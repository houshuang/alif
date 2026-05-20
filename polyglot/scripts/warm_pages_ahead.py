"""Pre-warm the next few unread pages of active stories.

Iterates every active Story in the configured language and runs the lazy
page pipeline (tokenize + simplemma + LLM quality gate) on the next
``--buffer`` pages beyond the user's last-viewed position. By the time the
user opens those pages, no waiting is required.

Bounded by design:
- A page is only warmed if it sits between the last-viewed page and the
  buffer ceiling. Once the buffer is full, the script exits without work.
- ``--max-per-story`` caps a single run, so a very long book can't burn
  the whole 20-minute cron timeout on one story.

Cost shape: one Sonnet quality-gate call per warmed page (~$0.30-0.50,
~2-3 min). With ``--buffer 5``, worst case per cron pass is 5 pages per
active story.

Usage::

    .venv/bin/python scripts/warm_pages_ahead.py --language el --buffer 5

Env vars (alternative to CLI flags)::

    POLYGLOT_PAGES_AHEAD_BUFFER       default buffer size
    POLYGLOT_PAGES_AHEAD_MAX_PER_RUN  cap per story per run (None = no cap)
    POLYGLOT_QUALITY_GATE             must be "1" for the gate to actually run

Exits 0 on success (including "no work needed"), 1 on hard failure.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from app.database import SessionLocal
from app.services.reading_intake import warm_all_active_stories


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-warm pages for active stories.")
    parser.add_argument("--language", default="el",
                        help="Language code (el/grc/la). Default: el")
    parser.add_argument("--buffer", type=int,
                        default=int(os.environ.get("POLYGLOT_PAGES_AHEAD_BUFFER", "5")),
                        help="Verified pages to keep ahead of the user. Default: 5")
    parser.add_argument("--max-per-story", type=int, default=None,
                        help="Cap on pages warmed per story per run. Default: no cap.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("warm_pages_ahead")

    if os.environ.get("POLYGLOT_QUALITY_GATE", "0") != "1":
        log.warning(
            "POLYGLOT_QUALITY_GATE is not set to 1 — page processing will skip "
            "the LLM verification step. Set POLYGLOT_QUALITY_GATE=1 for a "
            "real warm pass.",
        )

    max_per_run_env = os.environ.get("POLYGLOT_PAGES_AHEAD_MAX_PER_RUN")
    max_per_story = args.max_per_story
    if max_per_story is None and max_per_run_env:
        max_per_story = int(max_per_run_env)

    db = SessionLocal()
    try:
        summaries = warm_all_active_stories(
            db,
            language_code=args.language,
            buffer=args.buffer,
            max_to_warm_per_story=max_per_story,
        )
    finally:
        db.close()

    total_warmed = sum(len(s.get("pages_warmed", [])) for s in summaries)
    total_errors = sum(len(s.get("errors", [])) for s in summaries)
    log.info(
        "warm_pages_ahead %s: %d stories processed, %d pages warmed, %d errors",
        args.language, len(summaries), total_warmed, total_errors,
    )
    print(json.dumps(
        {
            "language_code": args.language,
            "buffer": args.buffer,
            "stories_processed": len(summaries),
            "pages_warmed": total_warmed,
            "errors": total_errors,
            "summaries": summaries,
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    ))
    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
