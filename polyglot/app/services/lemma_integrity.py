"""Lemma integrity primitives — repair a Lemma row to its correct citation form.

simplemma frequently stores an inflected surface form as if it were the lemma
(εξελίχθηκαν instead of εξελίσσομαι, πλεονάσματος instead of πλεόνασμα). This
module is the single, FK-safe way to fix such a row, used by:

- the one-time bulk cleanup (``scripts/audit_lemmas.py``), and
- the going-forward reading-intake LLM citation pass.

Two outcomes:

- **rename** — the correct citation form has no existing Lemma row, so we update
  this row in place (``lemma_form`` / ``lemma_bare`` / ``pos`` / ``gloss_en``).
- **merge** — the correct citation form already exists as a separate Lemma, so
  this row is a duplicate. We repoint every inbound FK to the canonical row,
  consolidate the ``UserLemmaKnowledge`` study record, and delete the duplicate.
  ``surface_form`` text on ``sentence_words`` / ``page_words`` preserves which
  inflection actually appeared, so no information is lost by repointing lemma_id.

Inbound FK columns to ``lemmas.lemma_id`` (must all be handled on merge):
  user_lemma_knowledge.lemma_id  (UNIQUE, NOT NULL)
  review_log.lemma_id            (NOT NULL)
  sentence_words.lemma_id
  sentences.target_lemma_id
  page_words.lemma_id
  frequency_entries.lemma_id
  content_flags.lemma_id
  lemmas.canonical_lemma_id      (self)
  lemmas.cognate_lemma_id        (self)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Lemma, UserLemmaKnowledge
from app.services.languages import get_provider

log = logging.getLogger(__name__)

# Tables/columns holding a plain (non-self) FK to lemmas.lemma_id.
_FK_REFS: list[tuple[str, str]] = [
    ("review_log", "lemma_id"),
    ("sentence_words", "lemma_id"),
    ("sentences", "target_lemma_id"),
    ("page_words", "lemma_id"),
    ("frequency_entries", "lemma_id"),
    ("content_flags", "lemma_id"),
]
# Self-referential FK columns on lemmas (rows that *point at* the source).
_SELF_REFS: list[str] = ["canonical_lemma_id", "cognate_lemma_id"]

# Which knowledge_state wins when consolidating two study records.
_STATE_RANK = {
    "new": 0, "encountered": 1, "acquiring": 2,
    "lapsed": 3, "learning": 4, "known": 5,
}


@dataclass
class FixResult:
    lemma_id: int
    action: str                       # "rename" | "merge" | "noop" | "skip"
    old_form: str = ""
    new_form: str = ""
    target_id: Optional[int] = None   # canonical row kept, on merge
    detail: dict = field(default_factory=dict)


def _merge_ulk(db: Session, source: UserLemmaKnowledge, target: UserLemmaKnowledge) -> None:
    """Fold the source study record into the target, keeping the more-advanced
    state. Counters sum; timestamps take the earliest start / latest activity.
    The source row is deleted by the caller (after FK repoint)."""
    target.times_seen = (target.times_seen or 0) + (source.times_seen or 0)
    target.times_correct = (target.times_correct or 0) + (source.times_correct or 0)
    target.total_encounters = (target.total_encounters or 0) + (source.total_encounters or 0)
    target.distinct_contexts = max(target.distinct_contexts or 0, source.distinct_contexts or 0)
    target.leech_count = (target.leech_count or 0) + (source.leech_count or 0)

    # Keep the more-advanced knowledge state and its scheduling payload.
    if _STATE_RANK.get(source.knowledge_state or "new", 0) > _STATE_RANK.get(target.knowledge_state or "new", 0):
        target.knowledge_state = source.knowledge_state
        target.fsrs_card_json = source.fsrs_card_json
        target.acquisition_box = source.acquisition_box
        target.acquisition_next_due = source.acquisition_next_due
        target.graduated_at = source.graduated_at

    def _earliest(a, b):
        xs = [x for x in (a, b) if x is not None]
        return min(xs) if xs else None

    def _latest(a, b):
        xs = [x for x in (a, b) if x is not None]
        return max(xs) if xs else None

    target.introduced_at = _earliest(target.introduced_at, source.introduced_at)
    target.acquisition_started_at = _earliest(target.acquisition_started_at, source.acquisition_started_at)
    target.entered_acquiring_at = _earliest(target.entered_acquiring_at, source.entered_acquiring_at)
    target.last_reviewed = _latest(target.last_reviewed, source.last_reviewed)
    target.experiment_intro_shown_at = _latest(target.experiment_intro_shown_at, source.experiment_intro_shown_at)
    if source.leech_suspended_at and not target.leech_suspended_at:
        target.leech_suspended_at = source.leech_suspended_at


def merge_lemma_into(db: Session, source_id: int, target_id: int) -> dict:
    """Repoint every inbound FK from ``source_id`` to ``target_id``, consolidate
    the ULK study record, then delete the source row. Caller commits.

    Returns a summary of rows touched. No-op-safe if source == target.
    """
    if source_id == target_id:
        return {"skipped": "same_lemma"}

    counts: dict[str, int] = {}
    for table, col in _FK_REFS:
        res = db.execute(
            text(f"UPDATE {table} SET {col} = :tgt WHERE {col} = :src"),
            {"tgt": target_id, "src": source_id},
        )
        if res.rowcount:
            counts[f"{table}.{col}"] = res.rowcount

    # Rows pointing AT the source via a self-ref move to the target. A row whose
    # canonical/cognate becomes itself is cleared (no self-loops).
    for col in _SELF_REFS:
        res = db.execute(
            text(f"UPDATE lemmas SET {col} = :tgt WHERE {col} = :src"),
            {"tgt": target_id, "src": source_id},
        )
        if res.rowcount:
            counts[f"lemmas.{col}"] = res.rowcount
    db.execute(
        text(f"UPDATE lemmas SET canonical_lemma_id = NULL WHERE canonical_lemma_id = lemma_id"),
    )
    db.execute(
        text(f"UPDATE lemmas SET cognate_lemma_id = NULL WHERE cognate_lemma_id = lemma_id"),
    )

    # Consolidate ULK. UNIQUE(lemma_id) means at most one each.
    src_ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == source_id).first()
    tgt_ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == target_id).first()
    if src_ulk is not None:
        if tgt_ulk is None:
            src_ulk.lemma_id = target_id
            counts["ulk_repointed"] = 1
        else:
            _merge_ulk(db, src_ulk, tgt_ulk)
            db.delete(src_ulk)
            counts["ulk_merged"] = 1
    db.flush()

    # Delete the source row via raw SQL. An ORM ``db.delete(Lemma)`` would fire
    # the ``Lemma.knowledge`` relationship cascade and NULL the FK on the ULK we
    # just repointed; raw SQL (after the FK migration above) is the safe path.
    # Expunge the now-deleted row from the identity map so a later ``db.get``
    # doesn't return the stale cached object.
    src_obj = db.get(Lemma, source_id)
    db.execute(text("DELETE FROM lemmas WHERE lemma_id = :src"), {"src": source_id})
    if src_obj is not None:
        db.expunge(src_obj)
    counts["lemma_deleted"] = 1
    return counts


def apply_citation_fix(
    db: Session,
    lemma_id: int,
    citation: str,
    pos: Optional[str] = None,
    gloss: Optional[str] = None,
    *,
    word_category: Optional[str] = None,
) -> FixResult:
    """Make ``lemma_id`` carry the correct citation form.

    - If an existing *canonical* (non-variant) lemma already holds that citation
      form, this row is a duplicate → ``merge_lemma_into`` it.
    - Otherwise rename this row in place.

    ``citation`` should be the dictionary form with accents. ``pos`` /
    ``gloss`` / ``word_category`` are written when provided. Caller commits.
    """
    lemma = db.get(Lemma, lemma_id)
    if lemma is None:
        return FixResult(lemma_id=lemma_id, action="skip", detail={"reason": "missing"})

    provider = get_provider(lemma.language_code)
    citation = (citation or "").strip()
    if not citation:
        # No proposed form (e.g. unidentifiable fragment) — just stamp metadata.
        if pos and not lemma.pos:
            lemma.pos = pos
        if word_category:
            lemma.word_category = word_category
        return FixResult(lemma_id=lemma_id, action="noop", old_form=lemma.lemma_form,
                         detail={"reason": "blank_citation"})

    new_bare = provider.normalize_bare(citation)
    old_form = lemma.lemma_form

    # Already correct (form matches up to accents) → only fill metadata.
    if new_bare == lemma.lemma_bare and citation == lemma.lemma_form:
        changed = {}
        if pos and lemma.pos != pos:
            lemma.pos = pos; changed["pos"] = pos
        if gloss and gloss.strip() and gloss.strip() != (lemma.gloss_en or "").strip():
            lemma.gloss_en = gloss.strip(); changed["gloss"] = True
        if word_category and lemma.word_category != word_category:
            lemma.word_category = word_category; changed["word_category"] = word_category
        return FixResult(lemma_id=lemma_id, action="noop", old_form=old_form,
                         new_form=citation, detail=changed)

    # Is there another canonical lemma already holding this citation form?
    target = (
        db.query(Lemma)
        .filter(
            Lemma.language_code == lemma.language_code,
            Lemma.lemma_bare == new_bare,
            Lemma.lemma_id != lemma_id,
            Lemma.canonical_lemma_id.is_(None),
        )
        .order_by(Lemma.lemma_id.asc())
        .first()
    )

    if target is not None:
        # Make sure the kept row carries the best metadata before we fold in.
        if citation and target.lemma_form != citation and provider.normalize_bare(target.lemma_form) == new_bare:
            target.lemma_form = citation
        if pos and not target.pos:
            target.pos = pos
        if gloss and gloss.strip() and not (target.gloss_en or "").strip():
            target.gloss_en = gloss.strip()
        if word_category and not target.word_category:
            target.word_category = word_category
        counts = merge_lemma_into(db, lemma_id, target.lemma_id)
        return FixResult(lemma_id=lemma_id, action="merge", old_form=old_form,
                         new_form=citation, target_id=target.lemma_id, detail=counts)

    # Rename in place.
    lemma.lemma_form = citation
    lemma.lemma_bare = new_bare
    if pos:
        lemma.pos = pos
    if gloss and gloss.strip():
        lemma.gloss_en = gloss.strip()
    if word_category:
        lemma.word_category = word_category
    # Re-link Modern↔Ancient cognate now that the bare form is correct.
    try:
        from app.services.reading_intake import link_intra_greek_cognates
        link_intra_greek_cognates(db, lemma)
    except Exception:
        pass
    return FixResult(lemma_id=lemma_id, action="rename", old_form=old_form,
                     new_form=citation, detail={"new_bare": new_bare})
