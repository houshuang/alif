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
    lookup_lemma_id,
    normalize_alef,
    resolve_exact_running_text_alias,
    strip_diacritics,
    strip_tatweel,
)
from app.services.morphology import find_best_db_match
from app.services.story_service import _import_unknown_words, _recalculate_story_counts


def _resolve_story_word_from_existing_inventory(
    surface_form: str,
    lemma_lookup: dict[str, int],
    known_bare_forms: set[str],
) -> tuple[int | None, bool | None, bool]:
    """Resolve one stored surface without bypassing exact-only identity.

    Returns ``(lemma_id, function_override, exact_policy_applies)``. When the
    last value is true and ``lemma_id`` is absent, callers must leave the row
    unmapped rather than attempting CAMeL or unknown-word import.
    """
    alias = resolve_exact_running_text_alias(surface_form, lemma_lookup)
    if alias.applicable:
        return (
            alias.lemma_id,
            alias.is_function_word if alias.lemma_id is not None else None,
            True,
        )

    bare = strip_diacritics(surface_form)
    bare_clean = strip_tatweel(bare)
    lid = lookup_lemma_id(surface_form, lemma_lookup)
    if not lid:
        match = find_best_db_match(bare_clean, known_bare_forms)
        if match:
            lex_norm = normalize_alef(match["lex_bare"])
            lid = lemma_lookup.get(lex_norm)
    return lid, None, False


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
                lid, function_override, exact_policy_applies = (
                    _resolve_story_word_from_existing_inventory(
                        sw.surface_form,
                        lemma_lookup,
                        known_bare_forms,
                    )
                )
                if exact_policy_applies and lid is None:
                    print(
                        "  Protected exact alias remains unmapped: "
                        f"{sw.surface_form}"
                    )
                    continue

                if lid:
                    lemma = db.query(Lemma).filter(Lemma.lemma_id == lid).first()
                    print(f"  Resolved: {sw.surface_form} -> {lemma.lemma_ar_bare if lemma else '?'} (id={lid})")
                    if not dry_run:
                        sw.lemma_id = lid
                        if lemma:
                            sw.gloss_en = lemma.gloss_en
                        if function_override is not None:
                            sw.is_function_word = function_override
                            sw.name_type = None
                    resolved += 1

            total_resolved += resolved
            print(f"  Phase 1: resolved {resolved}/{len(null_words)} via morphology")

            if not dry_run:
                db.flush()
                # Rebuild lookup with any new entries
                all_lemmas = db.query(Lemma).all()
                lemma_lookup = build_lemma_lookup(all_lemmas)

            # Phase 2: Import remaining unknown words via LLM
            remaining_words = (
                db.query(StoryWord)
                .filter(
                    StoryWord.story_id == story.id,
                    StoryWord.lemma_id == None,
                    StoryWord.is_function_word == False,
                )
                .all()
            )
            remaining = 0
            protected = 0
            for sw in remaining_words:
                alias = resolve_exact_running_text_alias(
                    sw.surface_form,
                    lemma_lookup,
                )
                if alias.applicable:
                    protected += 1
                else:
                    remaining += 1
            if protected:
                print(
                    f"  Phase 2: keeping {protected} exact-alias word(s) "
                    "outside unknown-word import"
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
