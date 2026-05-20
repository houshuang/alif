"""Backfill body_clean on existing Pages and re-tokenize them.

The Haiku body-cleaner is new (2026-05-20). Pages imported before it
existed have NULL body_clean and their PageWord / Sentence rows reflect
the raw polluted text (page numbers, footnote markers, soft-hyphens
breaking words, bibliographic citations, etc.). This script re-cleans
every Page, then resets the tokenization so the lazy pipeline re-runs
from the cleaned text on the next page view.

Per page, the script:

1. Calls ``body_clean.clean_body(page.body_src, language_code)`` and
   stores the result in ``page.body_clean``. Skips pages where
   body_clean is already set unless ``--force``.
2. Deletes harvested ``Sentence`` rows attached to the page (and their
   ``SentenceWord`` children via cascade). Without this, harvested
   sentences would point at PageWords that no longer exist.
3. Deletes ``PageWord`` rows for the page.
4. Nulls ``processed_at``, ``mappings_verified_at``, ``quality_gate_failures``.
   The next ``GET /api/texts/{sid}/pages/{n}`` triggers re-tokenization
   from ``body_clean`` and the quality gate from scratch.

The currently-unreferenced Lemmas (junk like ``σιτηρών1``) are left in
place — a separate cleanup script can prune them once the dust settles.
They're inert: nothing in the new pipeline will create a PageWord pointing
at them.

Usage::

    .venv/bin/python scripts/backfill_body_clean.py --language el --dry-run
    .venv/bin/python scripts/backfill_body_clean.py --language el --story-id 1
    .venv/bin/python scripts/backfill_body_clean.py --language el --max-pages 5

Cost: one Haiku call per page (~$0.001-0.002 raw, free under Max plan).
Latency: ~3-5s per page including subprocess startup, so a 300-page
textbook takes ~15-25 minutes. Run as a background job (nohup) when
processing a full book on the server.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models import Page, PageWord, Sentence, Story
from app.services import body_clean


def _reset_page_processing(db, page: Page) -> int:
    """Delete PageWord + harvested Sentence rows for the page. Returns the
    number of PageWord rows deleted (useful for progress logging)."""
    sentence_ids = [
        s.id for s in db.query(Sentence).filter(Sentence.page_id == page.id).all()
    ]
    if sentence_ids:
        # SentenceWord cascade via FK is set on the relationship; explicit
        # delete here keeps it independent of ORM-cascade configuration.
        from app.models import SentenceWord
        db.query(SentenceWord).filter(
            SentenceWord.sentence_id.in_(sentence_ids)
        ).delete(synchronize_session=False)
        db.query(Sentence).filter(Sentence.id.in_(sentence_ids)).delete(
            synchronize_session=False
        )
    pw_count = (
        db.query(PageWord).filter(PageWord.page_id == page.id).delete(
            synchronize_session=False
        )
    )
    page.processed_at = None
    page.mappings_verified_at = None
    page.quality_gate_failures = 0
    page.total_words = 0
    return pw_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill body_clean on existing Pages.")
    parser.add_argument("--language", default="el", help="Language code. Default: el")
    parser.add_argument("--story-id", type=int, default=None,
                        help="Only process this story_id. Default: all active stories.")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="Stop after this many pages cleaned (across all stories).")
    parser.add_argument("--force", action="store_true",
                        help="Re-clean pages whose body_clean is already set.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change without calling the LLM or writing.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("backfill_body_clean")

    if not body_clean.BODY_CLEAN_ENABLED and not args.dry_run:
        log.error(
            "POLYGLOT_BODY_CLEAN is not enabled — the cleaner is gated off. "
            "Set POLYGLOT_BODY_CLEAN=1 to actually call the LLM.",
        )
        return 2

    db = SessionLocal()
    summary = {
        "language_code": args.language,
        "pages_examined": 0,
        "pages_cleaned": 0,
        "pages_skipped_already_clean": 0,
        "pages_failed": 0,
        "pagewords_deleted": 0,
        "errors": [],
    }

    try:
        story_q = db.query(Story).filter(Story.language_code == args.language)
        if args.story_id is not None:
            story_q = story_q.filter(Story.id == args.story_id)
        else:
            story_q = story_q.filter(Story.status == "active")
        stories = story_q.order_by(Story.created_at.asc()).all()

        for story in stories:
            log.info("Story %d (%s): %d pages total",
                     story.id, story.title, story.page_count)
            pages = (
                db.query(Page)
                .filter(Page.story_id == story.id)
                .order_by(Page.page_number.asc())
                .all()
            )
            for page in pages:
                if args.max_pages is not None and summary["pages_cleaned"] >= args.max_pages:
                    log.info("Hit --max-pages cap of %d, stopping.", args.max_pages)
                    break
                summary["pages_examined"] += 1
                if page.body_clean is not None and not args.force:
                    summary["pages_skipped_already_clean"] += 1
                    continue

                if args.dry_run:
                    log.info("[dry-run] would clean page %d of story %d (%d chars)",
                             page.page_number, story.id, len(page.body_src or ""))
                    summary["pages_cleaned"] += 1
                    continue

                try:
                    result = body_clean.clean_body(page.body_src, args.language)
                except Exception as e:
                    log.exception("body_clean threw for page %d of story %d", page.page_number, story.id)
                    summary["pages_failed"] += 1
                    summary["errors"].append((story.id, page.page_number, repr(e)))
                    continue

                if result is None:
                    log.warning("body_clean returned None for page %d of story %d",
                                page.page_number, story.id)
                    summary["pages_failed"] += 1
                    summary["errors"].append(
                        (story.id, page.page_number, "clean_body returned None")
                    )
                    continue

                page.body_clean = result.cleaned
                pw_deleted = _reset_page_processing(db, page)
                summary["pagewords_deleted"] += pw_deleted
                summary["pages_cleaned"] += 1
                db.commit()
                log.info(
                    "Cleaned story=%d page=%d: %d→%d chars, %d removed, "
                    "%d hyphen-joins, %d PageWords purged",
                    story.id, page.page_number,
                    len(page.body_src or ""), len(result.cleaned),
                    len(result.removed), len(result.hyphen_joins), pw_deleted,
                )
            if args.max_pages is not None and summary["pages_cleaned"] >= args.max_pages:
                break
    finally:
        db.close()

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if summary["pages_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
