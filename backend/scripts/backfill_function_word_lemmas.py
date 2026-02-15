#!/usr/bin/env python3
"""Create Lemma entries for function words that appear in sentences.

FUNCTION_WORD_GLOSSES in sentence_validator.py lists ~80 common Arabic
function words with English glosses. Many don't have Lemma rows yet,
causing NULL lemma_id in sentence_words when these words appear.

This script creates Lemma entries for any missing function words.
No ULK records are created — these are system vocabulary for mapping only.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import Lemma
from app.services.sentence_validator import (
    FUNCTION_WORD_GLOSSES,
    FUNCTION_WORD_FORMS,
    normalize_alef,
)

# POS categories for function words
POS_MAP = {
    # Prepositions
    "في": "particle", "من": "particle", "على": "particle", "الى": "particle",
    "إلى": "particle", "عن": "particle", "مع": "particle", "بين": "particle",
    "حتى": "particle", "منذ": "particle", "خلال": "particle", "عند": "particle",
    "نحو": "particle", "فوق": "particle", "تحت": "particle",
    "امام": "particle", "أمام": "particle", "وراء": "particle",
    "بعد": "particle", "قبل": "particle", "حول": "particle", "دون": "particle",
    # Single-letter
    "ب": "particle", "ل": "particle", "ك": "particle", "و": "particle", "ف": "particle",
    # Conjunctions
    "او": "particle", "أو": "particle", "ان": "particle", "أن": "particle",
    "إن": "particle", "لكن": "particle", "ثم": "particle", "بل": "particle",
    # Pronouns
    "انا": "pron", "أنا": "pron", "انت": "pron", "أنت": "pron",
    "انتم": "pron", "أنتم": "pron", "هو": "pron", "هي": "pron",
    "هم": "pron", "هن": "pron", "نحن": "pron", "انتما": "pron", "هما": "pron",
    # Demonstratives
    "هذا": "pron", "هذه": "pron", "ذلك": "pron", "تلك": "pron",
    "هؤلاء": "pron", "اولئك": "pron", "أولئك": "pron",
    # Relative pronouns
    "الذي": "pron", "التي": "pron", "الذين": "pron",
    "اللذان": "pron", "اللتان": "pron", "اللواتي": "pron",
    # Question words
    "ما": "particle", "ماذا": "particle", "لماذا": "particle", "كيف": "particle",
    "اين": "particle", "أين": "particle", "متى": "particle", "هل": "particle",
    "كم": "particle", "اي": "particle", "أي": "particle",
    # Negation
    "لا": "particle", "لم": "particle", "لن": "particle",
    "ليس": "verb", "ليست": "verb",
    # Auxiliary / modal
    "كان": "verb", "كانت": "verb", "يكون": "verb", "تكون": "verb",
    "قد": "particle", "سوف": "particle", "سـ": "particle",
    # Adverbs
    "ايضا": "adv", "أيضا": "adv", "جدا": "adv", "فقط": "adv",
    "كل": "particle", "بعض": "particle", "كلما": "particle",
    "هنا": "adv", "هناك": "adv", "الان": "adv", "الآن": "adv",
    "لذلك": "particle", "هكذا": "adv", "معا": "adv",
    # Conditional/temporal
    "اذا": "particle", "إذا": "particle", "لو": "particle", "عندما": "particle",
    "بينما": "particle", "حيث": "particle", "كما": "particle",
    "لان": "particle", "لأن": "particle", "كي": "particle", "لكي": "particle",
    "حين": "particle", "حينما": "particle",
    # Emphasis / structure
    "لقد": "particle", "اما": "particle", "أما": "particle",
    "الا": "particle", "إلا": "particle", "اذن": "particle", "إذن": "particle",
    "انه": "particle", "إنه": "particle", "انها": "particle", "إنها": "particle",
    "مثل": "particle", "غير": "particle",
    # Grammatical verbs
    "يوجد": "verb", "توجد": "verb",
}


def main():
    db = SessionLocal()

    # Build set of existing bare forms (normalized)
    existing = set()
    for lem in db.query(Lemma).all():
        existing.add(normalize_alef(lem.lemma_ar_bare))

    # Skip conjugated forms that map to a base form
    conjugated_bases = set(FUNCTION_WORD_FORMS.keys())

    created = 0
    skipped_existing = 0
    skipped_conjugated = 0

    for bare, gloss in FUNCTION_WORD_GLOSSES.items():
        norm = normalize_alef(bare)

        # Skip if already exists
        if norm in existing:
            skipped_existing += 1
            continue

        # Skip conjugated forms — they'll map to their base lemma
        if bare in conjugated_bases:
            skipped_conjugated += 1
            continue

        # Skip single-letter proclitics/enclitics (ب, ل, ك, و, ف)
        if len(bare) == 1:
            continue

        # Skip hamza-variant duplicates (e.g. إلى if الى already handled)
        if norm in existing:
            continue

        pos = POS_MAP.get(bare, "particle")

        lemma = Lemma(
            lemma_ar=bare,
            lemma_ar_bare=bare,
            gloss_en=gloss,
            pos=pos,
        )
        db.add(lemma)
        existing.add(norm)
        created += 1
        print(f"  Created: {bare} ({gloss}) pos={pos}")

    db.commit()
    db.close()

    print(f"\nCreated {created} function word lemmas")
    print(f"Skipped {skipped_existing} (already exist)")
    print(f"Skipped {skipped_conjugated} (conjugated forms)")


if __name__ == "__main__":
    main()
