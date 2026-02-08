#!/usr/bin/env python3
"""Batch generate sentences for all known words.

Populates the sentences + sentence_words tables so the sentence-centric
review mode has content to work with.

Usage:
    python scripts/generate_sentences.py                    # generate 3 sentences per word
    python scripts/generate_sentences.py --target-count 2   # generate 2 per word
    python scripts/generate_sentences.py --word-id 42       # single word only
    python scripts/generate_sentences.py --dry-run           # validate but don't write to DB
    python scripts/generate_sentences.py --model openai      # use a specific model
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func

from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.llm import (
    AllProvidersFailed,
    generate_sentences_batch,
)
from app.services.sentence_validator import (
    ValidationResult,
    build_lemma_lookup,
    map_tokens_to_lemmas,
    strip_diacritics,
    tokenize,
    validate_sentence,
)


def get_existing_sentence_counts(db) -> dict[int, int]:
    """Return {lemma_id: count} for existing sentences."""
    rows = (
        db.query(Sentence.target_lemma_id, func.count(Sentence.id))
        .filter(Sentence.target_lemma_id.isnot(None))
        .group_by(Sentence.target_lemma_id)
        .all()
    )
    return {lid: cnt for lid, cnt in rows}


def store_sentence(
    db,
    gen_result,
    target_lemma: Lemma,
    lemma_lookup: dict[str, int],
    dry_run: bool = False,
) -> bool:
    """Validate and store a generated sentence + its word mappings.

    Returns True if stored successfully, False if validation failed.
    """
    target_bare = strip_diacritics(target_lemma.lemma_ar)
    all_bare_forms = set(lemma_lookup.keys())

    validation = validate_sentence(
        arabic_text=gen_result.arabic,
        target_bare=target_bare,
        known_bare_forms=all_bare_forms,
    )

    if not validation.valid:
        return False

    if dry_run:
        print(f"    [dry-run] Valid: {gen_result.arabic}")
        print(f"              EN: {gen_result.english}")
        return True

    # Create Sentence record
    sent = Sentence(
        arabic_text=gen_result.arabic,
        arabic_diacritized=gen_result.arabic,
        english_translation=gen_result.english,
        transliteration=gen_result.transliteration,
        source="llm",
        target_lemma_id=target_lemma.lemma_id,
    )
    db.add(sent)
    db.flush()

    # Tokenize and map to lemmas
    tokens = tokenize(gen_result.arabic)
    mappings = map_tokens_to_lemmas(
        tokens=tokens,
        lemma_lookup=lemma_lookup,
        target_lemma_id=target_lemma.lemma_id,
        target_bare=target_bare,
    )

    for m in mappings:
        sw = SentenceWord(
            sentence_id=sent.id,
            position=m.position,
            surface_form=m.surface_form,
            lemma_id=m.lemma_id,
            is_target_word=1 if m.is_target else 0,
        )
        db.add(sw)

    return True


def generate_for_word(
    db,
    target_lemma: Lemma,
    known_words: list[dict[str, str]],
    lemma_lookup: dict[str, int],
    needed: int,
    dry_run: bool = False,
    model: str = "gemini",
    delay: float = 0,
) -> tuple[int, int]:
    """Generate sentences for a single word. Returns (stored, failed)."""
    stored = 0
    failed = 0
    max_batches = 3  # at most 3 LLM calls per word

    for batch_num in range(max_batches):
        if stored >= needed:
            break

        remaining = needed - stored
        if delay > 0 and batch_num > 0:
            time.sleep(delay)
        try:
            results = generate_sentences_batch(
                target_word=target_lemma.lemma_ar,
                target_translation=target_lemma.gloss_en or "",
                known_words=known_words,
                count=min(remaining + 1, 3),  # ask for 1 extra to account for validation failures
                difficulty_hint="beginner",
                model_override=model,
            )
        except AllProvidersFailed as e:
            print(f"    LLM error: {e}")
            failed += 1
            break

        if not results:
            failed += 1
            continue

        for res in results:
            if stored >= needed:
                break
            if store_sentence(db, res, target_lemma, lemma_lookup, dry_run):
                stored += 1
            else:
                failed += 1

    return stored, failed


def main():
    parser = argparse.ArgumentParser(description="Batch generate sentences for known words")
    parser.add_argument("--target-count", type=int, default=3, help="Sentences per word (default: 3)")
    parser.add_argument("--word-id", type=int, help="Generate for a single lemma_id only")
    parser.add_argument("--dry-run", action="store_true", help="Validate without writing to DB")
    parser.add_argument("--model", default="gemini", help="LLM model: gemini/openai/anthropic (default: gemini)")
    parser.add_argument("--delay", type=float, default=0, help="Seconds to wait between LLM calls (for rate limiting)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Load all lemmas with FSRS cards (the user's vocabulary)
        all_lemmas = (
            db.query(Lemma)
            .join(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.fsrs_card_json.isnot(None))
            .all()
        )

        if not all_lemmas:
            print("No lemmas with FSRS cards found. Run import first.")
            return

        # Build known words list and lemma lookup
        known_words = [
            {"arabic": lem.lemma_ar, "english": lem.gloss_en or ""}
            for lem in all_lemmas
        ]
        lemma_lookup = build_lemma_lookup(all_lemmas)

        # Existing sentence counts
        existing_counts = get_existing_sentence_counts(db)

        # Filter to words that need sentences
        if args.word_id:
            targets = [l for l in all_lemmas if l.lemma_id == args.word_id]
            if not targets:
                print(f"Lemma {args.word_id} not found or has no FSRS card.")
                return
        else:
            targets = all_lemmas

        # Sort by existing sentence count (fewest first)
        targets.sort(key=lambda l: existing_counts.get(l.lemma_id, 0))

        total_stored = 0
        total_failed = 0
        total_skipped = 0
        start_time = time.time()

        print(f"Generating sentences for {len(targets)} words (target: {args.target_count} each)")
        print(f"Model: {args.model} | Dry run: {args.dry_run}")
        print(f"Known vocabulary: {len(known_words)} words")
        print("-" * 60)

        for i, lemma in enumerate(targets):
            existing = existing_counts.get(lemma.lemma_id, 0)
            needed = args.target_count - existing

            if needed <= 0:
                total_skipped += 1
                continue

            print(f"[{i+1}/{len(targets)}] {lemma.lemma_ar} ({lemma.gloss_en}) — need {needed}, have {existing}")

            if i > 0 and args.delay > 0:
                time.sleep(args.delay)

            stored, failed = generate_for_word(
                db=db,
                target_lemma=lemma,
                known_words=known_words,
                lemma_lookup=lemma_lookup,
                needed=needed,
                dry_run=args.dry_run,
                model=args.model,
                delay=args.delay,
            )

            total_stored += stored
            total_failed += failed

            if stored > 0:
                print(f"    -> stored {stored}" + (f", {failed} failed validation" if failed else ""))
            elif failed > 0:
                print(f"    -> 0 stored, {failed} failed")

            # Commit after each word so partial runs are useful
            if not args.dry_run and stored > 0:
                db.commit()

        elapsed = time.time() - start_time
        print("-" * 60)
        print(f"Done in {elapsed:.1f}s")
        print(f"  Stored: {total_stored}")
        print(f"  Failed validation: {total_failed}")
        print(f"  Skipped (already have enough): {total_skipped}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
