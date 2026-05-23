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
   body_clean has non-empty text unless ``--force``.
2. Deactivates harvested ``Sentence`` rows attached to the page without
   deleting them. Review logs point at ``sentences.id`` directly, so those
   ids must remain stable.
3. Deletes ``PageWord`` rows for the page.
4. Nulls ``processed_at``, ``mappings_verified_at``, ``quality_gate_failures``.
   The next ``GET /api/texts/{sid}/pages/{n}`` triggers re-tokenization
   from ``body_clean`` and the quality gate from scratch. With
   ``--reprocess``, this script immediately re-tokenizes and rewrites
   ``SentenceWord`` rows from the newly cleaned ``PageWord`` rows.

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
from collections import defaultdict
import json
import logging
import os
import sys
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models import Page, PageWord, Sentence, SentenceWord, Story
from app.services import body_clean


def _reset_page_processing(db, page: Page) -> int:
    """Reset a page for re-tokenization without deleting reviewed sentences.

    Review logs reference ``sentences.id`` directly, so a reprocess must keep
    those ids stable. Existing harvested sentences are made inactive until the
    new PageWord rows are built and synced back into SentenceWord below.
    Returns the number of PageWord rows deleted for progress logging.
    """
    db.query(Sentence).filter(Sentence.page_id == page.id).update(
        {
            Sentence.is_active: False,
            Sentence.mappings_verified_at: None,
        },
        synchronize_session=False,
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


def _sync_harvested_sentences_for_page(db, page: Page) -> dict[str, int]:
    """Refresh textbook Sentence/SentenceWord rows from current PageWord rows.

    This mirrors ``sentence_harvest.harvest_page_sentences`` but updates
    existing ``Sentence`` rows in place so historical review logs keep their
    foreign keys. Rows for sentence indices that no longer survive cleanup
    remain in the table as inactive provenance records.
    """
    stats = {
        "sentences_created": 0,
        "sentences_updated": 0,
        "sentences_deactivated": 0,
        "sentence_words_rewritten": 0,
    }
    if page.mappings_verified_at is None:
        return stats

    from app.services.canonical_resolution import resolve_canonical_via_map
    from app.services.lemma_quality import _detect_heading_sentence_indices
    from app.services.sentence_harvest import (
        _canonical_map_for,
        _detect_page_boundary_sentence_indices,
        _reconstruct_sentence_text,
    )

    page_words = (
        db.query(PageWord)
        .filter(PageWord.page_id == page.id)
        .order_by(PageWord.position)
        .all()
    )
    heading_indices = _detect_heading_sentence_indices(page_words)
    boundary_indices = _detect_page_boundary_sentence_indices(db, page, page_words)

    by_idx: dict[int, list[PageWord]] = defaultdict(list)
    for word in page_words:
        by_idx[word.sentence_index].append(word)

    lemma_ids = {word.lemma_id for word in page_words if word.lemma_id is not None}
    canonical_map = _canonical_map_for(db, lemma_ids)
    existing = {
        sentence.sentence_index_in_page: sentence
        for sentence in db.query(Sentence).filter(Sentence.page_id == page.id).all()
        if sentence.sentence_index_in_page is not None
    }

    keep_indices: set[int] = set()
    language_code = page.story.language_code
    for sentence_index in sorted(by_idx):
        if sentence_index in heading_indices or sentence_index in boundary_indices:
            continue

        words = sorted(by_idx[sentence_index], key=lambda word: word.position)
        if not any(word.lemma_id is not None for word in words):
            continue

        text = _reconstruct_sentence_text(words)
        if not text:
            continue

        keep_indices.add(sentence_index)
        sentence = existing.get(sentence_index)
        if sentence is None:
            sentence = Sentence(
                language_code=language_code,
                text=text,
                source="textbook",
                story_id=page.story_id,
                page_id=page.id,
                sentence_index_in_page=sentence_index,
                is_active=True,
                mappings_verified_at=page.mappings_verified_at,
            )
            db.add(sentence)
            db.flush()
            stats["sentences_created"] += 1
        else:
            if sentence.text != text:
                sentence.translation_en = None
                sentence.transliteration = None
                sentence.audio_url = None
            sentence.language_code = language_code
            sentence.text = text
            sentence.source = "textbook"
            sentence.story_id = page.story_id
            sentence.page_id = page.id
            sentence.sentence_index_in_page = sentence_index
            sentence.is_active = True
            sentence.mappings_verified_at = page.mappings_verified_at
            db.query(SentenceWord).filter(
                SentenceWord.sentence_id == sentence.id
            ).delete(synchronize_session=False)
            stats["sentences_updated"] += 1

        for word in words:
            lemma_id = word.lemma_id
            if lemma_id is not None:
                lemma_id = resolve_canonical_via_map(lemma_id, canonical_map)
            db.add(
                SentenceWord(
                    sentence_id=sentence.id,
                    position=word.position,
                    surface_form=word.surface_form,
                    lemma_id=lemma_id,
                    is_target_word=False,
                )
            )
            stats["sentence_words_rewritten"] += 1

    stale_q = db.query(Sentence).filter(Sentence.page_id == page.id)
    if keep_indices:
        stale_q = stale_q.filter(~Sentence.sentence_index_in_page.in_(keep_indices))
    stats["sentences_deactivated"] = stale_q.update(
        {
            Sentence.is_active: False,
            Sentence.mappings_verified_at: None,
        },
        synchronize_session=False,
    )
    db.commit()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill body_clean on existing Pages.")
    parser.add_argument("--language", default="el", help="Language code. Default: el")
    parser.add_argument("--story-id", type=int, default=None,
                        help="Only process this story_id. Default: all active stories.")
    parser.add_argument("--page-number", type=int, default=None,
                        help="Only process this page number within the selected story.")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="Stop after this many pages cleaned (across all stories).")
    parser.add_argument("--force", action="store_true",
                        help="Re-clean pages whose body_clean is already set.")
    parser.add_argument("--reprocess", action="store_true",
                        help="Immediately rerun page tokenization, quality gate, and sentence harvest.")
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
        "sentences_created": 0,
        "sentences_updated": 0,
        "sentences_deactivated": 0,
        "sentence_words_rewritten": 0,
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
            if args.page_number is not None:
                pages = [p for p in pages if p.page_number == args.page_number]
            for page in pages:
                if args.max_pages is not None and summary["pages_cleaned"] >= args.max_pages:
                    log.info("Hit --max-pages cap of %d, stopping.", args.max_pages)
                    break
                summary["pages_examined"] += 1
                has_usable_clean = (
                    page.body_clean is not None
                    and (page.body_clean.strip() or not (page.body_src or "").strip())
                )
                if has_usable_clean and not args.force:
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
                if args.reprocess:
                    from app.services.reading_intake import process_page
                    db.refresh(page)
                    process_page(db, page)
                    db.refresh(page)
                    sync_stats = _sync_harvested_sentences_for_page(db, page)
                    for key, value in sync_stats.items():
                        summary[key] += value
                    log.info(
                        "Reprocessed story=%d page=%d: processed_at=%s "
                        "mappings_verified_at=%s sentence_sync=%s",
                        story.id, page.page_number,
                        page.processed_at, page.mappings_verified_at,
                        sync_stats,
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
