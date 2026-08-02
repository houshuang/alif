"""Import a processed bilingual artifact into Alif's slow-reading library.

Accepted JSON shapes:
  1. {"title_ar": ..., "title_en": ..., "pages": [{"arabic": ..., "english": ...}]}
  2. Alif bookify: {"title": ..., "author": ..., "paragraphs": [{"ar": ..., "en": ...}]}
  3. Bookifier cache: {"<hash>": {"ar": ..., "en": ...}, ...}

The import creates Story/StoryWord/Lemma data but no UserLemmaKnowledge rows.
Learning state begins only when a page is completed in the reader.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.services.book_import_service import import_processed_book


def normalize_artifact(payload: object) -> tuple[dict, list[dict]]:
    if not isinstance(payload, dict):
        raise ValueError("Top-level JSON must be an object")

    if isinstance(payload.get("pages"), list):
        pages = [
            {
                "arabic": page.get("arabic") or page.get("ar"),
                "english": page.get("english") or page.get("en"),
                "source_page_number": page.get("source_page_number"),
                "pdf_page_number": page.get("pdf_page_number"),
            }
            for page in payload["pages"]
            if isinstance(page, dict)
        ]
        return payload, pages

    if isinstance(payload.get("paragraphs"), list):
        pages = [
            {"arabic": page.get("ar"), "english": page.get("en")}
            for page in payload["paragraphs"]
            if isinstance(page, dict)
        ]
        return {
            "title_ar": payload.get("title"),
            "author": payload.get("author"),
        }, pages

    cache_rows = [row for row in payload.values() if isinstance(row, dict)]
    if cache_rows and all((row.get("ar") or row.get("src")) for row in cache_rows):
        return {}, [
            {"arabic": row.get("ar") or row.get("src"), "english": row.get("en")}
            for row in cache_rows
        ]

    raise ValueError("Unrecognized artifact shape")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--title-ar")
    parser.add_argument("--title-en")
    parser.add_argument("--author")
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    metadata, pages = normalize_artifact(payload)
    title_ar = args.title_ar or metadata.get("title_ar") or metadata.get("title")
    if not title_ar:
        raise SystemExit("--title-ar is required for artifacts without a title")

    with SessionLocal() as db:
        story, new_ids = import_processed_book(
            db,
            title_ar=title_ar,
            title_en=args.title_en or metadata.get("title_en"),
            author=args.author or metadata.get("author"),
            pages=pages,
            book_metadata={
                key: metadata.get(key)
                for key in (
                    "chapter_number",
                    "chapter_title_ar",
                    "chapter_title_en",
                    "source",
                )
                if metadata.get(key) is not None
            },
            curated_lexicon=metadata.get("lexicon") or [],
            strict_lexicon=bool(metadata.get("strict_lexicon")),
        )
        print(json.dumps({
            "story_id": story.id,
            "title_ar": story.title_ar,
            "title_en": story.title_en,
            "page_count": story.page_count,
            "new_lemma_count": len(new_ids),
            "learning_rows_created": 0,
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
