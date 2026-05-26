"""Resync Sentence rows whose text has drifted from current PageWord rows.

The polyglot reader's Reveal interleaves per-sentence English under each
foreign sentence (PR #152). When a Story is reseeded with new prose, the old
``Sentence`` rows orphan past the Page→Sentence non-cascade and stay attached
to the new page via id reuse. The frontend then renders the new Latin/Greek
text alongside the OLD translation, mis-paired. Detected 2026-05-26 on the
LLPSI Familia Romana Coverage Reader.

This script walks every active Story for the configured languages, compares
each Page's stored Sentence text against what would be reconstructed from
current PageWord rows, and force-harvests any page that drifted. The harvest
nulls ``translation_en`` on rows whose text changed so the lazy translation
fetch (or this script's optional ``--translate`` pass) re-fills them.

USAGE
    polyglot/.venv/bin/python scripts/resync_stale_page_sentences.py \\
        --languages la el \\
        --dry-run

    polyglot/.venv/bin/python scripts/resync_stale_page_sentences.py \\
        --languages la el grc \\
        --translate

NOTES
    - Pages without ``mappings_verified_at`` are skipped (harvest precondition).
    - Sentence rows whose ``page_id`` IS NULL (LLM-generated) are not affected.
    - Idempotent: running again is a no-op once drift is resolved.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models import Page, PageWord, Sentence, Story  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402
from app.services.lemma_quality import _detect_heading_sentence_indices  # noqa: E402
from app.services.sentence_harvest import (  # noqa: E402
    _detect_page_boundary_sentence_indices,
    _reconstruct_sentence_text,
    harvest_page_sentences,
)

LOG = logging.getLogger("resync_stale_sentences")


def _current_sentence_texts(db, page: Page) -> dict[int, str]:
    """Mirror the harvest's logic to compute the text it WOULD store per
    sentence index, given current PageWord rows. Used for drift detection
    here so we don't unnecessarily call harvest on clean pages."""
    page_words = (
        db.query(PageWord)
        .filter(PageWord.page_id == page.id)
        .order_by(PageWord.position)
        .all()
    )
    if not page_words:
        return {}

    heading_indices = _detect_heading_sentence_indices(page_words)
    boundary_indices = _detect_page_boundary_sentence_indices(db, page, page_words)

    by_idx: dict[int, list] = {}
    for w in page_words:
        by_idx.setdefault(w.sentence_index, []).append(w)

    out: dict[int, str] = {}
    for s_idx, words in by_idx.items():
        if s_idx in heading_indices or s_idx in boundary_indices:
            continue
        if not any(w.lemma_id is not None for w in words):
            continue
        text = _reconstruct_sentence_text(words)
        if text:
            out[s_idx] = text
    return out


def _stored_active_texts(db, page: Page) -> dict[int, str]:
    rows = (
        db.query(Sentence)
        .filter(Sentence.page_id == page.id, Sentence.is_active == True)  # noqa: E712
        .all()
    )
    return {
        s.sentence_index_in_page: s.text
        for s in rows
        if s.sentence_index_in_page is not None
    }


def _detect_drift(db, page: Page) -> tuple[bool, int, int]:
    """Return (drifted, n_stored_active, n_current). Drift = stored text map
    differs from current reconstruction."""
    current = _current_sentence_texts(db, page)
    stored = _stored_active_texts(db, page)
    return (stored != current, len(stored), len(current))


def _fill_translations(db, language_code: str, batch_size: int = 12) -> int:
    """Optional --translate pass: hit translate_sentences_batch for any
    active textbook Sentence row in this language with NULL translation_en.
    Mirrors material_generator.translate_untranslated_sentences but scoped
    to refresh-eligible rows. Lock-safe: read → LLM → write per batch."""
    from app.services.material_generator import translate_sentences_batch

    pending = (
        db.query(Sentence)
        .filter(
            Sentence.language_code == language_code,
            Sentence.is_active == True,  # noqa: E712
            Sentence.source == "textbook",
            Sentence.page_id.isnot(None),
            (Sentence.translation_en.is_(None)),
        )
        .all()
    )
    if not pending:
        return 0

    LOG.info("[%s] translating %d sentences (batch_size=%d)",
             language_code, len(pending), batch_size)
    filled = 0
    for start in range(0, len(pending), batch_size):
        chunk = pending[start:start + batch_size]
        items = [{"id": s.id, "text": s.text} for s in chunk if s.text]
        if not items:
            continue
        translations = translate_sentences_batch(language_code, items)
        if not translations:
            LOG.warning("[%s] translate batch returned no results (batch start=%d)",
                        language_code, start)
            continue
        for sid, english in translations.items():
            (
                db.query(Sentence)
                .filter(Sentence.id == sid)
                .update({"translation_en": english})
            )
            filled += 1
        db.commit()
    return filled


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--languages", nargs="+", default=["la", "el", "grc"],
                    help="Language codes to scan. Default: la el grc")
    ap.add_argument("--story-id", type=int, default=None,
                    help="Restrict to one story (still filtered by --languages).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Detect and report drift without writing.")
    ap.add_argument("--translate", action="store_true",
                    help="After refreshing, batch-translate any active "
                         "textbook sentence with NULL translation_en.")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db = SessionLocal()
    summary: dict = {
        "languages": args.languages,
        "stories_scanned": 0,
        "pages_scanned": 0,
        "pages_drifted": 0,
        "pages_refreshed": 0,
        "translations_filled": 0,
        "per_story": [],
    }
    try:
        story_q = db.query(Story).filter(Story.language_code.in_(args.languages))
        if args.story_id is not None:
            story_q = story_q.filter(Story.id == args.story_id)
        stories = story_q.order_by(Story.id).all()

        for story in stories:
            summary["stories_scanned"] += 1
            story_drift = 0
            story_refreshed = 0
            pages = (
                db.query(Page)
                .filter(Page.story_id == story.id)
                .filter(Page.mappings_verified_at.isnot(None))
                .order_by(Page.page_number)
                .all()
            )
            for page in pages:
                summary["pages_scanned"] += 1
                drifted, n_stored, n_current = _detect_drift(db, page)
                if not drifted:
                    continue
                story_drift += 1
                summary["pages_drifted"] += 1
                LOG.info(
                    "DRIFT story=%d (%s) page=%d page_id=%d  stored=%d current=%d",
                    story.id, story.title or "(no title)", page.page_number,
                    page.id, n_stored, n_current,
                )
                if args.dry_run:
                    continue
                try:
                    refreshed = harvest_page_sentences(db, page, force=True)
                except Exception as e:
                    LOG.warning("Harvest failed for page %d: %s", page.id, e)
                    continue
                story_refreshed += 1
                summary["pages_refreshed"] += refreshed and 1 or 0
                LOG.info(
                    "  refreshed %d active sentence rows on page %d", refreshed, page.id,
                )

            if story_drift:
                summary["per_story"].append({
                    "story_id": story.id,
                    "language_code": story.language_code,
                    "title": story.title,
                    "pages_drifted": story_drift,
                    "pages_refreshed": story_refreshed,
                })

        if not args.dry_run and args.translate:
            for lang in args.languages:
                filled = _fill_translations(db, lang)
                LOG.info("[%s] filled %d translation_en rows", lang, filled)
                summary["translations_filled"] += filled

        if not args.dry_run and summary["pages_refreshed"]:
            log_activity(
                db,
                event_type="page_sentence_resync",
                summary=(
                    f"Resync stale page Sentence rows: "
                    f"{summary['pages_refreshed']} pages refreshed across "
                    f"{summary['stories_scanned']} stories"
                ),
                detail=summary,
            )

    finally:
        db.close()

    LOG.info("DONE: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
