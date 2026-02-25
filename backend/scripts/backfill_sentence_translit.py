"""Recompute sentence transliterations from arabic_diacritized using ALA-LC.

One-time backfill: replaces LLM-generated transliterations with deterministic ALA-LC.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal
from app.models import Sentence
from app.services.transliteration import transliterate_arabic


def main():
    db = SessionLocal()
    try:
        sentences = (
            db.query(Sentence)
            .filter(Sentence.arabic_diacritized.isnot(None))
            .all()
        )
        updated = 0
        for sent in sentences:
            new_translit = transliterate_arabic(sent.arabic_diacritized)
            if new_translit and new_translit != sent.transliteration:
                sent.transliteration = new_translit
                updated += 1
        db.commit()
        print(f"Updated {updated} / {len(sentences)} sentence transliterations")
    finally:
        db.close()


if __name__ == "__main__":
    main()
