#!/usr/bin/env python3
"""Batch generate sentences for all known words.

Populates the sentences + sentence_words tables so the sentence-centric
review mode has content to work with. Uses generate_material_for_word()
which includes LLM disambiguation and verification of word mappings.

Usage:
    python scripts/generate_sentences.py                    # generate 3 sentences per word
    python scripts/generate_sentences.py --target-count 2   # generate 2 per word
    python scripts/generate_sentences.py --word-id 42       # single word only
    python scripts/generate_sentences.py --dry-run           # preview only
    python scripts/generate_sentences.py --model gemini      # use a specific model
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func

from app.database import SessionLocal
from app.models import Lemma, Sentence, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.material_generator import generate_material_for_word


def get_existing_sentence_counts(db) -> dict[int, int]:
    """Return {lemma_id: count} for existing sentences."""
    rows = (
        db.query(Sentence.target_lemma_id, func.count(Sentence.id))
        .filter(Sentence.target_lemma_id.isnot(None))
        .group_by(Sentence.target_lemma_id)
        .all()
    )
    return {lid: cnt for lid, cnt in rows}


def main():
    parser = argparse.ArgumentParser(description="Batch generate sentences for known words")
    parser.add_argument("--target-count", type=int, default=3, help="Sentences per word (default: 3)")
    parser.add_argument("--word-id", type=int, help="Generate for a single lemma_id only")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
    parser.add_argument("--model", default="claude_sonnet", help="LLM model (default: gemini)")
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
        total_skipped = 0
        start_time = time.time()

        print(f"Generating sentences for {len(targets)} words (target: {args.target_count} each)")
        print(f"Model: {args.model} | Dry run: {args.dry_run}")
        print(f"Known vocabulary: {len(all_lemmas)} words")
        print("-" * 60)

        for i, lemma in enumerate(targets):
            existing = existing_counts.get(lemma.lemma_id, 0)
            needed = args.target_count - existing

            if needed <= 0:
                total_skipped += 1
                continue

            print(f"[{i+1}/{len(targets)}] {lemma.lemma_ar} ({lemma.gloss_en}) — need {needed}, have {existing}")

            if args.dry_run:
                print(f"    [dry-run] Would generate {needed} sentences")
                total_stored += needed
                continue

            stored = generate_material_for_word(
                lemma.lemma_id, needed=needed, model_override=args.model,
            )
            total_stored += stored

            if stored > 0:
                print(f"    -> stored {stored}")

        elapsed = time.time() - start_time
        print("-" * 60)
        print(f"Done in {elapsed:.1f}s")
        print(f"  Stored: {total_stored}")
        print(f"  Skipped (already have enough): {total_skipped}")

        if not args.dry_run and total_stored > 0:
            log_activity(
                db,
                event_type="sentences_generated",
                summary=f"Generated {total_stored} sentences for {len(targets) - total_skipped} words in {elapsed:.0f}s",
                detail={
                    "stored": total_stored,
                    "skipped": total_skipped,
                    "elapsed_seconds": round(elapsed, 1),
                },
            )

    finally:
        db.close()


if __name__ == "__main__":
    main()
