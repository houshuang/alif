"""Backfill existing stories: resolve null lemma_ids using morphological fallback + unknown word import.

Usage:
    python scripts/backfill_story_words.py [--dry-run]
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Story, StoryWord, Lemma
from app.services.sentence_validator import (
    build_lemma_lookup,
    normalize_alef,
    strip_diacritics,
    strip_tatweel,
    lookup_lemma,
)
from app.services.morphology import find_best_db_match
from app.services.story_service import _import_unknown_words, _recalculate_story_counts


def main():
    dry_run = "--dry-run" in sys.argv
    db = SessionLocal()

    try:
        # Build lookup
        all_lemmas = db.query(Lemma).all()
        lemma_lookup = build_lemma_lookup(all_lemmas)
        known_bare_forms = {normalize_alef(lem.lemma_ar_bare) for lem in all_lemmas}

        stories = db.query(Story).all()
        total_resolved = 0
        total_imported = 0

        for story in stories:
            null_words = (
                db.query(StoryWord)
                .filter(
                    StoryWord.story_id == story.id,
                    StoryWord.lemma_id == None,
                    StoryWord.is_function_word == False,
                )
                .all()
            )

            if not null_words:
                print(f"Story {story.id}: no null lemma_ids, skipping")
                continue

            print(f"\nStory {story.id}: {story.title_en or story.title_ar or 'Untitled'}")
            print(f"  {len(null_words)} words with null lemma_id")

            # Phase 1: Try morphological fallback for each null word
            resolved = 0
            for sw in null_words:
                bare = strip_diacritics(sw.surface_form)
                bare_clean = strip_tatweel(bare)
                bare_norm = normalize_alef(bare_clean)

                # Try lookup_lemma first (may work now with updated lookup)
                lid = lookup_lemma(bare_norm, lemma_lookup)
                if not lid:
                    # Try CAMeL morphological analysis
                    match = find_best_db_match(bare_clean, known_bare_forms)
                    if match:
                        lex_norm = normalize_alef(match["lex_bare"])
                        lid = lemma_lookup.get(lex_norm)

                if lid:
                    lemma = db.query(Lemma).filter(Lemma.lemma_id == lid).first()
                    print(f"  Resolved: {sw.surface_form} -> {lemma.lemma_ar_bare if lemma else '?'} (id={lid})")
                    if not dry_run:
                        sw.lemma_id = lid
                        if lemma:
                            sw.gloss_en = lemma.gloss_en
                    resolved += 1

            total_resolved += resolved
            print(f"  Phase 1: resolved {resolved}/{len(null_words)} via morphology")

            if not dry_run:
                db.flush()
                # Rebuild lookup with any new entries
                all_lemmas = db.query(Lemma).all()
                lemma_lookup = build_lemma_lookup(all_lemmas)

            # Phase 2: Import remaining unknown words via LLM
            remaining = (
                db.query(StoryWord)
                .filter(
                    StoryWord.story_id == story.id,
                    StoryWord.lemma_id == None,
                    StoryWord.is_function_word == False,
                )
                .count()
            )

            if remaining > 0 and not dry_run:
                print(f"  Phase 2: importing {remaining} unknown words via LLM...")
                new_ids = _import_unknown_words(db, story, lemma_lookup)
                total_imported += len(new_ids)
                print(f"  Created {len(new_ids)} new lemma entries")

                # Rebuild lookup
                all_lemmas = db.query(Lemma).all()
                lemma_lookup = build_lemma_lookup(all_lemmas)
                known_bare_forms = {normalize_alef(lem.lemma_ar_bare) for lem in all_lemmas}
            elif remaining > 0:
                print(f"  Phase 2: {remaining} words would be imported (dry-run)")

            # Recalculate counts
            if not dry_run:
                _recalculate_story_counts(db, story)
                print(f"  Updated: readiness={story.readiness_pct}%, unknown={story.unknown_count}")

        if not dry_run:
            db.commit()
            print(f"\nDone! Resolved {total_resolved} words, imported {total_imported} new lemmas")
        else:
            print(f"\nDry run complete. Would resolve {total_resolved} words")

    finally:
        db.close()


if __name__ == "__main__":
    main()
