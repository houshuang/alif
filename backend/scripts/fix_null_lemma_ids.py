#!/usr/bin/env python3
"""Re-map sentence_words with NULL lemma_id and auto-create proper-name lemmas.

Two responsibilities:
1. Comprehensive lookup retry — surface forms that did not map at import time
   may map now (new lemmas, normalization fixes, clitic-stripping improvements).
2. Proper-name auto-creation — for residual unmapped words flagged as proper
   names by `detect_proper_names`, create a `word_category="proper_name"`
   lemma so the SentenceWord can carry a real lemma_id and the runtime
   reviewability gate (`sentence_eligibility.not_has_unmapped_words`) lets
   the sentence through.

The script does NOT retire sentences for unresolvable common-word gaps —
those stay `is_active=True` (storage concern) and are filtered at review
time by the eligibility gate (review concern). When the missing common-word
lemma is added later, a subsequent run picks it up automatically.

Safe to run repeatedly; idempotent.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import Sentence, SentenceWord
from app.services.proper_name_lemmas import get_or_create_proper_name_lemma
from app.services.sentence_validator import (
    build_comprehensive_lemma_lookup,
    detect_proper_names,
    lookup_lemma,
    normalize_alef,
    strip_diacritics,
    strip_punctuation,
    strip_tatweel,
)


def _canonical_bare(surface_form: str) -> str:
    return normalize_alef(
        strip_tatweel(strip_diacritics(strip_punctuation(surface_form or "")))
    )


def remap_unmapped_sentence_words(db, *, dry_run: bool = False) -> dict[str, int]:
    """Single pass over active SentenceWord rows with lemma_id IS NULL.

    Returns counts: {fixed_by_lookup, fixed_by_proper_name, still_unmapped, sentences_touched}.
    """
    lookup = build_comprehensive_lemma_lookup(db)
    print(f"Built comprehensive lookup with {len(lookup)} entries")

    null_words = (
        db.query(SentenceWord)
        .join(Sentence)
        .filter(SentenceWord.lemma_id.is_(None), Sentence.is_active == True)  # noqa: E712
        .all()
    )
    print(f"Found {len(null_words)} sentence_word rows with NULL lemma_id\n")

    by_sentence: dict[int, list[SentenceWord]] = {}
    for sw in null_words:
        by_sentence.setdefault(sw.sentence_id, []).append(sw)

    # First pass: comprehensive lookup
    fixed_by_lookup = 0
    still_unmapped: list[SentenceWord] = []
    for sw in null_words:
        bare = _canonical_bare(sw.surface_form)
        if not bare:
            continue
        lemma_id = lookup_lemma(bare, lookup, original_bare=strip_diacritics(sw.surface_form))
        if lemma_id is not None:
            sw.lemma_id = lemma_id
            fixed_by_lookup += 1
            print(f"  REMAP: sent={sw.sentence_id} '{sw.surface_form}' -> lemma_id={lemma_id}")
        else:
            still_unmapped.append(sw)

    # Second pass: proper-name detection on residual unmapped surface forms
    surface_freq: Counter[str] = Counter()
    for sw in still_unmapped:
        bare = _canonical_bare(sw.surface_form)
        if bare:
            surface_freq[bare] += 1
    proper_names = detect_proper_names(surface_freq, lookup, min_frequency=1)

    fixed_by_proper_name = 0
    for sw in still_unmapped:
        bare = _canonical_bare(sw.surface_form)
        if not bare or bare not in proper_names:
            continue
        # Use the original surface form (with tashkeel) as display, the helper
        # canonicalizes for the lookup key and dedups on bare.
        lemma_id = get_or_create_proper_name_lemma(db, sw.surface_form, source="book")
        if lemma_id is not None:
            sw.lemma_id = lemma_id
            fixed_by_proper_name += 1
            print(
                f"  PROPER NAME: sent={sw.sentence_id} '{sw.surface_form}' "
                f"-> created/reused lemma_id={lemma_id}"
            )

    if dry_run:
        db.rollback()
        print("\n[dry-run] rolled back, no changes persisted")
    else:
        db.commit()

    remaining = (
        db.query(SentenceWord)
        .join(Sentence)
        .filter(SentenceWord.lemma_id.is_(None), Sentence.is_active == True)  # noqa: E712
        .count()
    )
    return {
        "fixed_by_lookup": fixed_by_lookup,
        "fixed_by_proper_name": fixed_by_proper_name,
        "still_unmapped": remaining,
        "sentences_touched": len(by_sentence),
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        stats = remap_unmapped_sentence_words(db, dry_run=args.dry_run)
    finally:
        db.close()

    print(f"\nFixed by lookup:        {stats['fixed_by_lookup']}")
    print(f"Fixed by proper-name:   {stats['fixed_by_proper_name']}")
    print(f"Sentences touched:      {stats['sentences_touched']}")
    print(f"Still unmapped (active): {stats['still_unmapped']}")


if __name__ == "__main__":
    main()
