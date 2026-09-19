"""Reading attention v1: preserve memory, choose current obligations explicitly."""
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Lemma, UserLemmaKnowledge
from app.services.canonical_resolution import resolve_canonical_lemma_id

POLICY_VERSION = "reading_attention_v1"
DISPOSITIONS = {"maintain", "reading_support", "parked"}
STAGED_SOURCES = {"book", "bookifier", "dragoman", "book_ocr", "story_import", "textbook_scan"}


def is_maintained(knowledge: UserLemmaKnowledge | None) -> bool:
    # Unflushed legacy constructors have None until SQLAlchemy applies defaults.
    return knowledge is None or (knowledge.attention_disposition or "maintain") == "maintain"


def excluded_ids_query():
    """Exclude opted-out canonicals AND their legacy variant scheduling rows.

    Recursive UNION (not UNION ALL) terminates even with corrupt identity cycles.
    This never hides rows from dictionary/knowledge reads.
    """
    blocked = select(UserLemmaKnowledge.lemma_id).where(
        UserLemmaKnowledge.attention_disposition.in_(["reading_support", "parked"])
    ).cte(recursive=True)
    blocked = blocked.union(select(Lemma.lemma_id).join(
        blocked, Lemma.canonical_lemma_id == blocked.c.lemma_id
    ))
    return select(blocked.c.lemma_id)


def maintenance_clause():
    return UserLemmaKnowledge.lemma_id.notin_(excluded_ids_query())


def excluded_lemma_ids(db: Session) -> set[int]:
    blocked = {lid for lid, in db.query(UserLemmaKnowledge.lemma_id).filter(
        UserLemmaKnowledge.attention_disposition.in_(["reading_support", "parked"])
    )}
    parents = dict(db.query(Lemma.lemma_id, Lemma.canonical_lemma_id).filter(Lemma.canonical_lemma_id.isnot(None)))
    while True:
        added = {lid for lid, parent in parents.items() if parent in blocked} - blocked
        if not added:
            return blocked
        blocked.update(added)


def set_disposition(db: Session, lemma_id: int, disposition: str, reason: str) -> UserLemmaKnowledge:
    if disposition not in DISPOSITIONS:
        raise ValueError("Unknown attention disposition")
    canonical = resolve_canonical_lemma_id(db, lemma_id)
    lemma = db.get(Lemma, canonical)
    if lemma is None:
        raise ValueError("Word not found")
    knowledge = db.query(UserLemmaKnowledge).filter_by(lemma_id=canonical).first()
    if knowledge is None:
        knowledge = UserLemmaKnowledge(lemma_id=canonical, knowledge_state="encountered", source=lemma.source or "study")
        db.add(knowledge)
    knowledge.attention_disposition = disposition
    knowledge.attention_reason = reason
    knowledge.attention_updated_at = datetime.now(timezone.utc)
    db.flush()
    # Caller owns commit. No acquisition, resets, due-date changes or slow work.
    return knowledge


def stage_import(db: Session, knowledge: UserLemmaKnowledge, source: str) -> None:
    """For NEW vocabulary only. Existing commitments must never be overwritten."""
    if source in STAGED_SOURCES:
        knowledge.attention_disposition = "reading_support"
        knowledge.attention_reason = "Available for reading; maintenance requires explicit opt-in"
        knowledge.attention_updated_at = datetime.now(timezone.utc)
