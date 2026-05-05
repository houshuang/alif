"""Helpers for proper-name lemma creation.

Proper names are the documented exception to the "no auto-create from
corrections" invariant. When a sentence import or remap step encounters an
Arabic surface form that is a proper name (Heidi, Rosie, محمد, القاهرة), we
create a lemma with `word_category="proper_name"` so the SentenceWord has a
real lemma_id and the runtime reviewability gate can let the sentence through.

Proper-name lemmas are inert in scheduling:
- `word_selector.py` filters them from auto-introduction.
- `sentence_selector.py` excludes them from comprehensibility scaffold counts.
- `sentence_review_service.py` excludes them from review credit (FSRS / acquisition).

So a proper name in a reviewed sentence is purely decorative — it never gates,
never scores, never gets a UserLemmaKnowledge row.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Lemma
from app.services.sentence_validator import (
    normalize_alef,
    strip_diacritics,
    strip_punctuation,
    strip_tatweel,
)


def _canonical_bare(surface_form: str) -> str:
    """Strip punctuation/tashkeel/tatweel for a stable lookup key."""
    return normalize_alef(
        strip_tatweel(strip_diacritics(strip_punctuation(surface_form or "")))
    )


def get_or_create_proper_name_lemma(
    db: Session,
    surface_form: str,
    *,
    source: str = "book",
) -> int | None:
    """Return the lemma_id for a proper-name lemma, creating one if needed.

    Returns None if the surface form is empty or too short to be a real word
    (so the caller can decide whether to drop the SentenceWord row).
    """
    bare = _canonical_bare(surface_form)
    if not bare or len(bare) < 2:
        return None

    existing = (
        db.query(Lemma)
        .filter(
            Lemma.lemma_ar_bare == bare,
            Lemma.word_category == "proper_name",
            Lemma.canonical_lemma_id.is_(None),
        )
        .first()
    )
    if existing:
        return existing.lemma_id

    lemma = Lemma(
        lemma_ar=surface_form.strip() or bare,
        lemma_ar_bare=bare,
        gloss_en="(proper name)",
        pos="noun",
        word_category="proper_name",
        source=source,
        gates_completed_at=datetime.now(timezone.utc),  # quality gates do not apply
    )
    db.add(lemma)
    db.flush()
    return lemma.lemma_id
