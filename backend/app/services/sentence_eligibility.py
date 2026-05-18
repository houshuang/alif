"""Reviewability gate for stored sentences.

Two distinct concerns govern a sentence's lifecycle:

1. **Storage** — a row may exist in `sentences` with one or more
   `sentence_words.lemma_id IS NULL`. The book/corpus import paths
   intentionally retain authentic passages even when some surface forms have
   no lemma in the user's vocabulary yet, so the sentence can be remapped
   later when the lemma gets added.

2. **Reviewability** — the user must NEVER see a sentence with an unmapped
   word or a stale mapping-verification stamp. Without a trustworthy lemma_id
   we cannot show a gloss, route to a word-info card, give review credit, or
   run the comprehensibility gate correctly.

This module is the single source of truth for concern (2). Every selection
path that returns a sentence to the user must apply
`reviewable_sentence_clauses()`. Storage paths are unchanged.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, exists

from app.models import Sentence, SentenceWord


def not_has_unmapped_words():
    """SQL clause: True iff the Sentence has zero SentenceWord with NULL lemma_id."""
    return ~exists().where(
        SentenceWord.sentence_id == Sentence.id,
        SentenceWord.lemma_id.is_(None),
    )


MAPPING_VERIFICATION_MIN_AT = datetime(2026, 4, 16)

# The correction resolver became sense-aware at this deploy. Rows verified
# before this timestamp passed the older bare-form-only repair path and must be
# rechecked before they are safe to show in new sessions.
MAPPING_VERIFICATION_HARDENED_AT = datetime(2026, 5, 17, 18, 59)


def has_current_mapping_verification():
    """SQL clause: sentence passed the current generation-time mapping gate.

    The mapping verifier has been hardened repeatedly. Rows stamped before the
    2026-04-16 same-lemma rejection fix predate the current fail-closed
    semantics, and the 2000-01-01 sentinel used by corpus enrichment is only a
    processing claim. Neither should be reviewable without re-verification.
    """
    return and_(
        Sentence.mappings_verified_at.isnot(None),
        Sentence.mappings_verified_at >= MAPPING_VERIFICATION_MIN_AT,
        Sentence.mappings_verified_at != datetime(2000, 1, 1),
    )


def reviewable_sentence_clauses():
    """Combined clause for review-facing selection."""
    return and_(
        Sentence.is_active == True,  # noqa: E712
        not_has_unmapped_words(),
        has_current_mapping_verification(),
    )
