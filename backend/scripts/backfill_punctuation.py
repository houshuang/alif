#!/usr/bin/env python3
"""Backfill punctuation in SentenceWord surface_form from original sentence text.

Existing SentenceWord records had punctuation stripped because tokenize()
was used instead of tokenize_display(). This script re-tokenizes from the
stored arabic_diacritized text and updates surface_forms to include punctuation.

Usage:
    cd backend && python3 scripts/backfill_punctuation.py [--dry-run]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import Sentence, SentenceWord
from app.services.sentence_validator import tokenize, tokenize_display


def main():
    parser = argparse.ArgumentParser(description="Backfill punctuation in SentenceWord surface_forms")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        sentences = db.query(Sentence).filter(Sentence.is_active == True).all()
        updated_sentences = 0
        updated_words = 0

        for sent in sentences:
            source_text = sent.arabic_diacritized or sent.arabic_text
            if not source_text:
                continue

            display_tokens = tokenize_display(source_text)
            clean_tokens = tokenize(source_text)

            # Only process if display tokens differ from clean tokens
            # (meaning there's punctuation to restore)
            if display_tokens == clean_tokens:
                continue

            # Get existing sentence_words ordered by position
            words = (
                db.query(SentenceWord)
                .filter(SentenceWord.sentence_id == sent.id)
                .order_by(SentenceWord.position)
                .all()
            )

            if len(words) != len(clean_tokens):
                # Token count mismatch — skip (sentence may have been manually edited)
                continue

            if len(display_tokens) != len(clean_tokens):
                # Display vs clean token count differs (standalone punctuation filtered)
                # Need to align by matching clean tokens
                continue

            changed = False
            for word, display_tok in zip(words, display_tokens):
                if word.surface_form != display_tok:
                    if not args.dry_run:
                        word.surface_form = display_tok
                    else:
                        print(f"  sentence {sent.id}: '{word.surface_form}' -> '{display_tok}'")
                    updated_words += 1
                    changed = True

            if changed:
                updated_sentences += 1

        if not args.dry_run:
            db.commit()
            print(f"Updated {updated_words} words across {updated_sentences} sentences")
        else:
            print(f"[DRY RUN] Would update {updated_words} words across {updated_sentences} sentences")

    finally:
        db.close()


if __name__ == "__main__":
    main()
