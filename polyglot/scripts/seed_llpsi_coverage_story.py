"""Seed (or re-seed) the "LLPSI Familia Romana — Coverage Reader" Story
into the polyglot reader from a JSON dump produced by
`scripts/generate_llpsi_coverage_texts.py`.

One Story, one Page per LLPSI chapter — paragraphs joined with blank lines.
``source='generated_coverage'`` is the sentinel: re-seed (``--force``)
deletes any prior matching Story (and its Pages via cascade) so the chapter
texts can be regenerated without leaving an old copy behind.

USAGE
    polyglot/.venv/bin/python scripts/seed_llpsi_coverage_story.py \\
        --json research/polyglot-llpsi-coverage-2026-05-26.json --force

    # against a non-default DB:
    polyglot/.venv/bin/python scripts/seed_llpsi_coverage_story.py \\
        --json /tmp/coverage.json --force \\
        --db-url sqlite:////opt/alif/polyglot/polyglot.db

NOTES
    - Pages are NOT tokenized at seed time. First view in the reader triggers
      the normal lazy `process_page` pipeline (body_clean → tokenize → lemmas
      → quality gate).
    - `page_review_log` rows have an FK on ``story_id`` (NOT NULL); the
      cascade-delete on Story.pages doesn't cascade to page_review_log because
      that's a separate table without a relationship from Story. We log
      orphan-count for visibility but don't delete them (history is preserved
      by design — Hard Invariant 11 idempotency).
    - The Pydantic schemas and route layer enforce most invariants; this
      script bypasses them deliberately because the source-of-truth is the
      generation script and we want one transaction for the whole reseed.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.models import Page, PageReviewLog, Story  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402

LOG = logging.getLogger("seed_llpsi_coverage")

LANG = "la"
TITLE = "LLPSI Familia Romana — Coverage Reader"
SOURCE = "generated_coverage"


# ─── Loaders ────────────────────────────────────────────────────────────────


def load_dump(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"JSON dump not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if "results" not in data:
        raise ValueError(f"{path}: missing 'results' key — not a coverage dump")
    return data


def body_for_chapter(result: dict) -> str:
    """Join paragraphs into one page body. Blank line between paragraphs (so
    `body_clean` will see them as separate sentence groups)."""
    paras = [p.strip() for p in (result.get("paragraphs") or []) if p.strip()]
    return "\n\n".join(paras)


# ─── Re-seed ────────────────────────────────────────────────────────────────


def delete_existing(db) -> int:
    """Delete any Story with this title. Returns the number of Stories
    deleted (typically 0 or 1). Match is title-only because the very first
    seed of this Story (pre-2026-05-26) went through the paste-import
    endpoint with ``source='paste'``, not the ``generated_coverage`` sentinel
    we use going forward — so source-strict matching would miss the
    pre-existing prod row and create a duplicate."""
    stories = (
        db.query(Story)
        .filter(Story.language_code == LANG,
                Story.title == TITLE)
        .all()
    )
    n = 0
    for story in stories:
        # Audit any orphan page_review_log rows that will be left behind.
        orphan_prs = (
            db.query(PageReviewLog)
            .filter(PageReviewLog.story_id == story.id)
            .count()
        )
        if orphan_prs:
            LOG.warning("Story %d will be deleted leaving %d page_review_log "
                        "rows orphaned (history preserved by design)",
                        story.id, orphan_prs)
        db.delete(story)  # cascade-deletes its Pages
        n += 1
    db.commit()
    return n


def seed(db, dump: dict) -> tuple[int, int, int]:
    """Create the Story + Pages. Returns ``(story_id, pages_created,
    total_words)``."""
    results = dump["results"]
    story = Story(
        language_code=LANG,
        title=TITLE,
        author="Generated — Codex/Claude via generate_llpsi_coverage_texts.py",
        source=SOURCE,
        page_count=len(results),
        metadata_json={
            "config": dump.get("config", {}),
            "summary": {
                "chapters": len(results),
                "total_target_lemmas": sum(r["target_count"] for r in results),
                "total_covered": sum(r["covered_count"] for r in results),
                "total_words": sum(r.get("word_count", 0) for r in results),
            },
        },
    )
    db.add(story)
    db.flush()  # populate story.id

    total_words = 0
    for r in results:
        body = body_for_chapter(r)
        if not body:
            LOG.warning("Chapter %s has no paragraphs; skipping", r.get("chapter"))
            continue
        db.add(Page(
            story_id=story.id,
            page_number=int(r["chapter"]),
            body_src=body,
        ))
        total_words += int(r.get("word_count", 0) or 0)

    story.total_words = total_words
    db.commit()
    return story.id, len(results), total_words


# ─── CLI ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", type=Path, required=True,
                    help="Coverage JSON dump from generate_llpsi_coverage_texts.py")
    ap.add_argument("--db-url", default=os.environ.get("POLYGLOT_DATABASE_URL")
                    or os.environ.get("DATABASE_URL")
                    or f"sqlite:///{REPO_ROOT / 'polyglot.db'}")
    ap.add_argument("--force", action="store_true",
                    help="Delete any existing Story with the sentinel title+source "
                         "before seeding (cascade-deletes its Pages).")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    dump = load_dump(args.json)
    LOG.info("Loaded coverage dump: %d chapters from %s",
             len(dump["results"]), args.json)

    engine = create_engine(args.db_url, future=True)
    Sess = sessionmaker(bind=engine, future=True)
    with Sess() as db:
        # Idempotency: if any prior Story with same title exists (any source),
        # refuse unless --force. Reading state on those pages is real, do not
        # stomp. Title-only match catches the pre-2026-05-26 row that was
        # created via paste-import (source='paste') — see delete_existing.
        existing = (
            db.query(Story)
            .filter(Story.language_code == LANG, Story.title == TITLE)
            .count()
        )
        if existing and not args.force:
            LOG.error("Found %d existing Story row(s) with title=%r. "
                      "Pass --force to delete + reseed (page_review_log rows "
                      "are preserved as audit history).", existing, TITLE)
            return 2

        if existing:
            n_deleted = delete_existing(db)
            LOG.info("Deleted %d existing Story row(s)", n_deleted)

        story_id, pages_created, total_words = seed(db, dump)
        LOG.info("Seeded Story id=%d (%d pages, %d total words)",
                 story_id, pages_created, total_words)

        log_activity(
            db,
            event_type="llpsi_coverage_story_seeded",
            summary=f"Seeded LLPSI Coverage Reader Story id={story_id} "
                    f"({pages_created} pages, {total_words} words)",
            detail={
                "story_id": story_id,
                "pages_created": pages_created,
                "total_words": total_words,
                "json_source": str(args.json),
                "force": args.force,
            },
            language_code=LANG,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
