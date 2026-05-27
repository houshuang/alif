"""Re-split existing Story pages into screen-sized polyglot Pages.

The original PDF intake stored one Page per PDF page, which for the Greek
"Ιστορία" textbook averages ~2,280 chars (max ~4,655) — too long for a phone
screen. This script walks each Page, paginates its ``body_src`` at sentence
boundaries (default 500 chars / page), and replaces the old Page rows with
the smaller ones in a single transaction per story.

USAGE
    polyglot/.venv/bin/python scripts/split_long_pages.py --all --dry-run
    polyglot/.venv/bin/python scripts/split_long_pages.py --story-id 1
    polyglot/.venv/bin/python scripts/split_long_pages.py --all --max-chars 800

WHAT IT TOUCHES
    - Reads Page.body_src per story (PDF page boundaries are preserved as
      natural section breaks — we never merge across original Pages).
    - Deactivates harvested Sentence rows attached to the old Pages
      (``is_active=False``). Sentences are NEVER deleted because review_log
      and sentence_review_log hold FK references — Alif/polyglot's repair
      policy is "deactivate, don't delete." Going-forward harvests build
      fresh Sentence rows for the new Pages.
    - Deletes the old Page rows. PageWord cascades via the Page model's
      ``cascade="all, delete-orphan"`` relationship.
    - Deletes PageReviewLog rows for the story — their (story_id, page_number)
      coordinates no longer map to anything, and their ``client_review_id``
      shape ``pr:{story_id}:{page_number}`` would otherwise silently no-op
      the user's first re-read of a page (the page-level idempotency check).
    - Inserts new Page rows with sequential page_numbers, ``processed_at=NULL``,
      ``body_clean=NULL``, ``translation_en=NULL``. The next page-view
      re-tokenizes lazily as usual.
    - Bumps Story.page_count.
    - Logs to ActivityLog (event_type='pages_resplit').

DOES NOT touch:
    - Lemma rows (citation forms persist across the split).
    - UserLemmaKnowledge (study state is per-lemma, not per-page).
    - ReviewLog / SentenceReviewLog history (FKs land on Sentence ids that
      stay around, just marked inactive).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Page, PageReviewLog, Sentence, Story  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402
from app.services.reading_intake import (  # noqa: E402
    DEFAULT_MAX_CHARS_PER_PAGE,
    _paginate_by_length,
)

LOG = logging.getLogger("split_long_pages")


def _resplit_one_story(
    db: Session,
    story: Story,
    *,
    max_chars: int,
    dry_run: bool,
) -> dict[str, int]:
    """Return a stats dict for the resplit of one Story."""
    pages = (
        db.query(Page)
        .filter(Page.story_id == story.id)
        .order_by(Page.page_number)
        .all()
    )
    if not pages:
        return {"old_pages": 0, "new_pages": 0, "deactivated_sentences": 0,
                "deleted_page_reviews": 0}

    # Build the new body list. Each old Page may fan out into 1+ new Pages;
    # we never merge across an old PDF page boundary, so an over-paginated
    # old page can grow while a short one stays short.
    new_bodies: list[str] = []
    for old in pages:
        chunks = _paginate_by_length(old.body_src or "", story.language_code, max_chars)
        if not chunks:
            # Old page had only whitespace / empty text — drop it. Original
            # PDF page numbering isn't preserved here (we renumber the
            # surviving pages 1..N).
            continue
        new_bodies.extend(chunks)

    if not new_bodies:
        LOG.warning("Story id=%d (%r) produced no non-empty pages — skipping",
                    story.id, story.title)
        return {"old_pages": len(pages), "new_pages": 0,
                "deactivated_sentences": 0, "deleted_page_reviews": 0}

    page_ids = [p.id for p in pages]

    # Deactivate harvested Sentences for the old pages. Keep them around
    # because review_log / sentence_review_log hold FKs.
    sentence_q = db.query(Sentence).filter(
        Sentence.page_id.in_(page_ids),
        Sentence.is_active.is_(True),
    )
    deactivated = sentence_q.count()

    # Page review logs for this story would silently dedup the next review
    # if their `client_review_id` (pr:{sid}:{n}) collides with a new advance.
    page_review_q = db.query(PageReviewLog).filter(PageReviewLog.story_id == story.id)
    page_review_count = page_review_q.count()

    stats = {
        "old_pages": len(pages),
        "new_pages": len(new_bodies),
        "deactivated_sentences": deactivated,
        "deleted_page_reviews": page_review_count,
    }

    LOG.info(
        "Story id=%d %r (%s): %d old pages → %d new pages "
        "(max_chars=%d), %d sentences to deactivate, %d page reviews to clear",
        story.id, story.title, story.language_code,
        stats["old_pages"], stats["new_pages"], max_chars,
        deactivated, page_review_count,
    )

    if dry_run:
        return stats

    # Apply in one transaction. Note: deleting Pages cascades to PageWord via
    # the SQLAlchemy relationship cascade. Sentence rows are kept (FK history
    # in review_log/sentence_review_log) but flipped inactive AND have their
    # `page_id` nulled — PRAGMA foreign_keys=ON would otherwise block the
    # Page delete (sentences.page_id has no ondelete cascade). Going-forward
    # harvest inserts fresh Sentence rows under the new Page ids.
    sentence_q.update(
        {Sentence.is_active: False, Sentence.page_id: None},
        synchronize_session=False,
    )
    page_review_q.delete(synchronize_session=False)

    # Delete old pages BEFORE inserting new ones to avoid the
    # (story_id, page_number) unique-constraint collision.
    for p in pages:
        db.delete(p)
    db.flush()

    for idx, body in enumerate(new_bodies, start=1):
        db.add(Page(story_id=story.id, page_number=idx, body_src=body))

    story.page_count = len(new_bodies)
    db.commit()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser()
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--story-id", type=int, help="Resplit one story by id.")
    g.add_argument("--all", action="store_true",
                   help="Resplit every active Story across languages.")
    parser.add_argument(
        "--max-chars", type=int, default=DEFAULT_MAX_CHARS_PER_PAGE,
        help=f"Target max chars per page (default {DEFAULT_MAX_CHARS_PER_PAGE}).",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Report only — don't write.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db = SessionLocal()
    try:
        q = db.query(Story).filter(Story.status == "active")
        if args.story_id is not None:
            q = q.filter(Story.id == args.story_id)
        stories = q.order_by(Story.id).all()

        if not stories:
            LOG.warning("No matching stories.")
            return 1

        totals = {"old_pages": 0, "new_pages": 0,
                  "deactivated_sentences": 0, "deleted_page_reviews": 0,
                  "stories": 0}
        for story in stories:
            stats = _resplit_one_story(
                db, story, max_chars=args.max_chars, dry_run=args.dry_run,
            )
            if stats["new_pages"] > 0:
                totals["stories"] += 1
                for k in ("old_pages", "new_pages",
                          "deactivated_sentences", "deleted_page_reviews"):
                    totals[k] += stats[k]

        LOG.info("Totals: %s", totals)

        if not args.dry_run and totals["stories"] > 0:
            log_activity(
                "pages_resplit",
                f"Re-split {totals['stories']} story/stories "
                f"({totals['old_pages']} → {totals['new_pages']} pages, "
                f"max_chars={args.max_chars})",
                detail={
                    "story_ids": [s.id for s in stories],
                    "max_chars": args.max_chars,
                    **totals,
                },
            )
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
