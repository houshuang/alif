"""Reviewability gate for stored sentences.

Two distinct concerns govern a sentence's lifecycle:

1. **Storage** — a row may exist in `sentences` with one or more
   `sentence_words.lemma_id IS NULL`. The book/corpus import paths
   intentionally retain authentic passages even when some surface forms have
   no lemma in the user's vocabulary yet, so the sentence can be remapped
   later when the lemma gets added.

2. **Reviewability** — the user must NEVER see a sentence with an unmapped
   word. Without a lemma_id we cannot show a gloss, route to a word-info
   card, give review credit, or run the comprehensibility gate correctly.

This module is the single source of truth for concern (2). Every selection
path that returns a sentence to the user must apply
`reviewable_sentence_clauses()` (or include `not_has_unmapped_words()` in its
own filter chain). Storage paths are unchanged.
"""

from __future__ import annotations

from sqlalchemy import and_, exists

from app.models import Sentence, SentenceWord


def not_has_unmapped_words():
    """SQL clause: True iff the Sentence has zero SentenceWord with NULL lemma_id."""
    return ~exists().where(
        SentenceWord.sentence_id == Sentence.id,
        SentenceWord.lemma_id.is_(None),
    )


def reviewable_sentence_clauses():
    """Combined clause for review-facing selection: active AND fully mapped."""
    return and_(
        Sentence.is_active == True,  # noqa: E712
        not_has_unmapped_words(),
    )
