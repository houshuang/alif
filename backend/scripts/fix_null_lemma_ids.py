#!/usr/bin/env python3
"""Fix sentence_words with NULL lemma_id using comprehensive lookup.

Builds a lookup from ALL lemmas (including function word lemmas),
re-maps NULL sentence_words, and retires sentences that still have
unmapped words.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import Sentence, SentenceWord
from app.services.sentence_validator import (
    build_comprehensive_lemma_lookup,
    lookup_lemma,
    normalize_alef,
    strip_diacritics,
    strip_tatweel,
)


def main():
    db = SessionLocal()

    lookup = build_comprehensive_lemma_lookup(db)
    print(f"Built comprehensive lookup with {len(lookup)} entries")

    # Find NULL lemma_id sentence_words in active sentences
    null_words = (
        db.query(SentenceWord)
        .join(Sentence)
        .filter(SentenceWord.lemma_id.is_(None), Sentence.is_active == True)
        .all()
    )
    print(f"Found {len(null_words)} sentence_words with NULL lemma_id\n")

    fixed = 0
    still_null: dict[int, list[str]] = {}  # sentence_id → list of unmapped surface forms

    for sw in null_words:
        bare = strip_diacritics(sw.surface_form)
        bare_clean = strip_tatweel(bare)
        bare_norm = normalize_alef(bare_clean)

        lemma_id = lookup_lemma(bare_norm, lookup)
        if lemma_id is not None:
            print(f"  FIXED: sent={sw.sentence_id} '{sw.surface_form}' -> lemma_id={lemma_id}")
            sw.lemma_id = lemma_id
            fixed += 1
        else:
            still_null.setdefault(sw.sentence_id, []).append(sw.surface_form)

    db.flush()

    # Retire sentences that still have unmapped words
    retired = 0
    for sent_id, unmapped in still_null.items():
        sent = db.query(Sentence).filter(Sentence.id == sent_id).first()
        if sent and sent.is_active:
            print(f"  RETIRE: sent={sent_id} unmapped={unmapped} text={sent.arabic_text[:60]}")
            sent.is_active = False
            retired += 1

    db.commit()

    # Verify
    remaining = (
        db.query(SentenceWord)
        .join(Sentence)
        .filter(SentenceWord.lemma_id.is_(None), Sentence.is_active == True)
        .count()
    )

    db.close()

    print(f"\nFixed {fixed} sentence_words")
    print(f"Retired {retired} sentences with unfixable words")
    print(f"Remaining NULL lemma_id in active sentences: {remaining}")


if __name__ == "__main__":
    main()
