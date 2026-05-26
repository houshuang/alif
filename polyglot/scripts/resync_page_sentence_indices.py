"""Re-bucket PageWord.sentence_index using the current sentence splitter.

When the splitter changes (e.g. quote-aware dialog rule landed 2026-05-26
after the Reveal misalignment incident), pages previously processed under
the old rules keep their stale ``PageWord.sentence_index`` until they're
re-tokenized. A full ``process_page(force=True)`` would do it, but at the
cost of re-running body_clean + quality gate per page — wasteful when the
*only* thing that changed is sentence bucketing.

This script reuses the existing ``body_clean`` and the deterministic
tokenizer to recompute sentence indices and updates PageWord rows in place.
Lemma assignments, gloss work, citation repair, and quality-gate verdicts
are all preserved. After the in-place update, ``harvest_page_sentences``
runs with the drift detector from PR #160 — it sees the new sentence
grouping and refreshes ``Sentence`` rows (nulling ``translation_en`` for
rows whose text changed so the next reveal re-translates).

USAGE
    polyglot/.venv/bin/python scripts/resync_page_sentence_indices.py \\
        --languages la el grc --dry-run
    polyglot/.venv/bin/python scripts/resync_page_sentence_indices.py \\
        --languages la el grc --translate

NOTES
    - Skips pages without verified mappings (harvest precondition).
    - Skips pages whose retokenization produces a different token count
      (defensive — the tokenizer should be deterministic, but if a provider
      version drifted we don't want to corrupt PageWord.position alignment).
    - Idempotent: re-running once the indices are correct is a no-op.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models import Page, PageWord, Story  # noqa: E402
from app.services import body_clean as body_clean_svc  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402
from app.services.languages import get_provider  # noqa: E402
from app.services.reading_intake import _split_into_sentences  # noqa: E402
from app.services.sentence_harvest import harvest_page_sentences  # noqa: E402

LOG = logging.getLogger("resync_page_sentence_indices")


def _new_sentence_index_per_position(page: Page, language_code: str) -> list[int]:
    """Re-tokenize the page from body_clean and return a list of
    ``sentence_index`` values aligned with PageWord.position order."""
    raw_source = page.body_clean if (page.body_clean and page.body_clean.strip()) else page.body_src
    source_text = body_clean_svc.normalize_pdf_artifacts(raw_source or "", collapse_whitespace=True)
    sentences = _split_into_sentences(source_text, language_code)
    provider = get_provider(language_code)
    out: list[int] = []
    for s_idx, sentence in enumerate(sentences):
        for _ in provider.tokenize(sentence):
            out.append(s_idx)
    return out


def _resync_page(db, page: Page, language_code: str) -> dict:
    stats = {"changed": False, "skipped": None, "harvested": 0}
    pws = (
        db.query(PageWord)
        .filter(PageWord.page_id == page.id)
        .order_by(PageWord.position)
        .all()
    )
    if not pws:
        stats["skipped"] = "no_page_words"
        return stats

    try:
        new_sidx = _new_sentence_index_per_position(page, language_code)
    except Exception as e:
        LOG.warning("tokenize failed for page %d: %s", page.id, e)
        stats["skipped"] = f"tokenize_error: {e}"
        return stats

    if len(new_sidx) != len(pws):
        LOG.warning(
            "Token-count drift on page %d (%s): %d new vs %d existing — "
            "skipping to avoid PageWord misalignment",
            page.id, page.story.title, len(new_sidx), len(pws),
        )
        stats["skipped"] = f"token_count_drift:{len(new_sidx)}/{len(pws)}"
        return stats

    if all(pw.sentence_index == ns for pw, ns in zip(pws, new_sidx)):
        return stats

    for pw, ns in zip(pws, new_sidx):
        if pw.sentence_index != ns:
            pw.sentence_index = ns
    db.commit()
    stats["changed"] = True

    refreshed = harvest_page_sentences(db, page, force=True)
    stats["harvested"] = refreshed
    return stats


def _fill_translations(db, language_code: str, batch_size: int = 12) -> int:
    """Reused from resync_stale_page_sentences — same lazy-fill semantics."""
    from app.models import Sentence
    from app.services.material_generator import translate_sentences_batch

    pending = (
        db.query(Sentence)
        .filter(
            Sentence.language_code == language_code,
            Sentence.is_active == True,  # noqa: E712
            Sentence.source == "textbook",
            Sentence.page_id.isnot(None),
            Sentence.translation_en.is_(None),
        )
        .all()
    )
    if not pending:
        return 0
    LOG.info("[%s] translating %d sentences", language_code, len(pending))
    filled = 0
    for start in range(0, len(pending), batch_size):
        chunk = pending[start:start + batch_size]
        items = [{"id": s.id, "text": s.text} for s in chunk if s.text]
        if not items:
            continue
        translations = translate_sentences_batch(language_code, items)
        if not translations:
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
    ap.add_argument("--languages", nargs="+", default=["la", "el", "grc"])
    ap.add_argument("--story-id", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--translate", action="store_true")
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
        "pages_changed": 0,
        "pages_skipped": 0,
        "sentences_refreshed": 0,
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
            pages = (
                db.query(Page)
                .filter(Page.story_id == story.id)
                .filter(Page.mappings_verified_at.isnot(None))
                .order_by(Page.page_number)
                .all()
            )
            story_changed = 0
            for page in pages:
                summary["pages_scanned"] += 1
                if args.dry_run:
                    try:
                        new_sidx = _new_sentence_index_per_position(page, story.language_code)
                    except Exception:
                        continue
                    pws = (
                        db.query(PageWord)
                        .filter(PageWord.page_id == page.id)
                        .order_by(PageWord.position)
                        .all()
                    )
                    if (
                        pws
                        and len(new_sidx) == len(pws)
                        and any(pw.sentence_index != ns for pw, ns in zip(pws, new_sidx))
                    ):
                        LOG.info(
                            "WOULD CHANGE story=%d (%s) page=%d page_id=%d",
                            story.id, story.title or "(no title)", page.page_number, page.id,
                        )
                        story_changed += 1
                        summary["pages_changed"] += 1
                    continue

                stats = _resync_page(db, page, story.language_code)
                if stats["changed"]:
                    story_changed += 1
                    summary["pages_changed"] += 1
                    summary["sentences_refreshed"] += stats["harvested"]
                    LOG.info(
                        "CHANGED story=%d page=%d page_id=%d  refreshed %d sentences",
                        story.id, page.page_number, page.id, stats["harvested"],
                    )
                elif stats["skipped"]:
                    summary["pages_skipped"] += 1

            if story_changed:
                summary["per_story"].append({
                    "story_id": story.id,
                    "language_code": story.language_code,
                    "title": story.title,
                    "pages_changed": story_changed,
                })

        if not args.dry_run and args.translate:
            for lang in args.languages:
                filled = _fill_translations(db, lang)
                LOG.info("[%s] filled %d translation_en rows", lang, filled)
                summary["translations_filled"] += filled

        if not args.dry_run and summary["pages_changed"]:
            log_activity(
                db,
                event_type="page_sentence_index_resync",
                summary=(
                    f"Re-bucketed PageWord.sentence_index across "
                    f"{summary['pages_changed']} pages "
                    f"({summary['stories_scanned']} stories scanned)"
                ),
                detail=summary,
            )
    finally:
        db.close()

    LOG.info("DONE: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
