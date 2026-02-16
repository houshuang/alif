#!/usr/bin/env python3
"""Fix conjugated glosses and enrich book-imported lemmas.

Finds lemmas created via story/book import that have conjugated glosses
(e.g. "she woke up" → "to wake up") and missing enrichment data
(forms, etymology, memory hooks, transliteration).

Usage:
    cd backend && python3 scripts/fix_book_glosses.py [--dry-run] [--limit=200]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from app.models import Lemma, Story
from app.services.activity_log import log_activity


def fix_glosses(db, lemmas: list[Lemma], dry_run: bool) -> int:
    """Re-generate dictionary-form glosses for a batch of lemmas."""
    from app.services.llm import generate_completion, AllProvidersFailed

    if not lemmas:
        return 0

    # Build word list using lemma_ar (base form from CAMeL)
    words_for_llm = []
    for lem in lemmas:
        words_for_llm.append(f"- id={lem.lemma_id}, word={lem.lemma_ar}, pos={lem.pos or 'unknown'}, current_gloss=\"{lem.gloss_en}\"")

    prompt = f"""Fix these Arabic word glosses to be proper dictionary-form entries.

Current glosses may be conjugated/contextual (e.g. "she woke up" should be "to wake up").

Rules:
- Verbs: use infinitive ("to write", "to wake up"), NOT conjugated ("she wrote", "he woke up")
- Nouns: use bare singular ("book", "school"), NOT inflected ("his books", "the schools")
- Adjectives: use base form ("big", "beautiful"), NOT inflected ("bigger", "the big one")
- Keep glosses concise: 1-3 words

Words:
{chr(10).join(words_for_llm)}

Return JSON array: [{{"id": 1, "gloss": "corrected dictionary gloss"}}]
Only include entries where the gloss actually needs correction."""

    try:
        result = generate_completion(
            prompt=prompt,
            system_prompt="You fix Arabic-English glosses to dictionary form. Respond with JSON only.",
            json_mode=True,
            temperature=0.1,
        )
    except AllProvidersFailed as e:
        print(f"  LLM failed: {e}")
        return 0

    items = result if isinstance(result, list) else result.get("corrections", result.get("words", []))
    if not isinstance(items, list):
        print(f"  Unexpected response: {type(result)}")
        return 0

    lemma_map = {l.lemma_id: l for l in lemmas}
    fixed = 0
    for item in items:
        lid = item.get("id")
        new_gloss = item.get("gloss", "").strip()
        if not lid or not new_gloss or lid not in lemma_map:
            continue
        lemma = lemma_map[lid]
        if new_gloss != lemma.gloss_en:
            print(f"  {lid} {lemma.lemma_ar_bare}: \"{lemma.gloss_en}\" → \"{new_gloss}\"")
            if not dry_run:
                lemma.gloss_en = new_gloss
            fixed += 1

    if not dry_run and fixed:
        db.commit()

    return fixed


def main():
    parser = argparse.ArgumentParser(description="Fix book-imported word glosses and run enrichment")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    print(f"fix_book_glosses.py — {'DRY RUN' if args.dry_run else 'LIVE RUN'}")

    db = SessionLocal()
    try:
        # Find book-imported lemmas (via source_story_id → book_ocr stories, or source=story_import)
        book_story_ids = [
            r[0] for r in db.query(Story.id).filter(Story.source == "book_ocr").all()
        ]

        candidates = (
            db.query(Lemma)
            .filter(
                Lemma.canonical_lemma_id.is_(None),
                Lemma.source == "story_import",
            )
            .limit(args.limit)
            .all()
        )

        # Also include any lemma linked to a book story
        if book_story_ids:
            book_linked = (
                db.query(Lemma)
                .filter(
                    Lemma.canonical_lemma_id.is_(None),
                    Lemma.source_story_id.in_(book_story_ids),
                )
                .limit(args.limit)
                .all()
            )
            seen_ids = {l.lemma_id for l in candidates}
            for l in book_linked:
                if l.lemma_id not in seen_ids:
                    candidates.append(l)

        print(f"Found {len(candidates)} story/book-imported lemmas")

        if not candidates:
            return

        # Step 1: Fix glosses in batches
        print("\n═══ Step 1: Fix conjugated glosses ═══")
        total_fixed = 0
        batch_size = 20
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i:i + batch_size]
            fixed = fix_glosses(db, batch, args.dry_run)
            total_fixed += fixed
            if i + batch_size < len(candidates):
                time.sleep(1)
        print(f"Fixed {total_fixed} glosses")

        # Step 2: Run full enrichment
        need_enrichment = [
            l for l in candidates
            if not l.forms_json or not l.etymology_json or not l.transliteration_ala_lc or not l.memory_hooks_json
        ]
        print(f"\n═══ Step 2: Enrich {len(need_enrichment)} lemmas ═══")

        if need_enrichment and not args.dry_run:
            from app.services.lemma_enrichment import enrich_lemmas_batch
            result = enrich_lemmas_batch([l.lemma_id for l in need_enrichment])
            print(f"  Forms: {result.get('forms', 0)}")
            print(f"  Etymology: {result.get('etymology', 0)}")
            print(f"  Transliteration: {result.get('transliteration', 0)}")
            print(f"  Memory hooks: {result.get('memory_hooks', 0)}")
        elif need_enrichment:
            print(f"  Would enrich {len(need_enrichment)} lemmas (dry run)")

        # Log activity
        if not args.dry_run and (total_fixed > 0 or need_enrichment):
            log_activity(
                db,
                event_type="manual_action",
                summary=f"Fixed {total_fixed} glosses, enriched {len(need_enrichment)} book-imported lemmas",
                detail={
                    "glosses_fixed": total_fixed,
                    "lemmas_enriched": len(need_enrichment),
                    "book_stories": len(book_story_ids),
                },
            )

        print("\nDone!")

    finally:
        db.close()


if __name__ == "__main__":
    main()
