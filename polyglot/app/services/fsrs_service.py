"""py-fsrs v6 integration for polyglot.

Language-agnostic — FSRS scheduling doesn't depend on script or morphology.
Differences from Alif's port:

- No Arabic-specific imports (Root, mnemonic regen, root-sibling boost).
- No `experiment_group` / `experiment_intro_shown_at` (no A/B testing here yet).
- No memory-hooks regeneration hook (polyglot has no mnemonics service yet);
  the failure path just logs.

Idempotency: when a `client_review_id` is supplied and matches a prior
review row, we short-circuit and return the existing post-state without
applying a second FSRS step. Mirrors Alif's offline-queue contract so the
React Native sync layer can replay safely.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fsrs import Scheduler, Card, Rating, State
from sqlalchemy.orm import Session

from app.models import UserLemmaKnowledge, ReviewLog
from app.services.knowledge_lifecycle import (
    record_review_result,
    snapshot as lifecycle_snapshot,
)

logger = logging.getLogger(__name__)

# Default desired_retention=0.95 from Alif's optimizer fit. Polyglot will
# re-fit once we have ~1k reviews of its own; until then this is a safe prior.
scheduler = Scheduler(desired_retention=0.95)


STATE_MAP = {
    State.Learning: "learning",
    State.Review: "known",
    State.Relearning: "lapsed",
}

RATING_MAP = {
    1: Rating.Again,
    2: Rating.Hard,
    3: Rating.Good,
    4: Rating.Easy,
}


def parse_json_column(data, default=None):
    """Safely parse a JSON column that may already be dict/list, or a JSON
    string, or corrupted text. SQLite's JSON type can come back as either
    depending on how the row was written, so we accept both."""
    if default is None:
        default = {}
    if data is None:
        return default
    if isinstance(data, (dict, list)):
        return data
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Corrupted JSON column data, returning default")
        return default


def create_new_card() -> dict:
    """Fresh py-fsrs card serialized for storage in `fsrs_card_json`."""
    card = Card()
    return card.to_dict()


def reactivate_if_suspended(db: Session, lemma_id: int, source: str) -> bool:
    """Reactivate a suspended (leech) word with a fresh FSRS card."""
    from app.services.interaction_logger import log_interaction

    ulk = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )
    if ulk and ulk.knowledge_state == "suspended":
        ulk.knowledge_state = "learning"
        ulk.fsrs_card_json = create_new_card()
        ulk.source = source
        ulk.introduced_at = datetime.now(timezone.utc)
        db.commit()
        log_interaction(
            event="word_auto_reactivated",
            lemma_id=lemma_id,
            context=f"source:{source}",
        )
        return True
    return False


def submit_review(
    db: Session,
    lemma_id: int,
    rating_int: int,
    response_ms: Optional[int] = None,
    session_id: Optional[str] = None,
    review_mode: str = "reading",
    comprehension_signal: Optional[str] = None,
    client_review_id: Optional[str] = None,
    sentence_id: Optional[int] = None,
    commit: bool = True,
) -> dict:
    """Apply a learner rating to the FSRS card for `lemma_id`.

    Variant lemmas are redirected to their canonical at function entry per
    Hard Invariant #9 — the canonical is the unit of scheduling, and ULK
    rows must never grow on variants.

    Returns a dict with `lemma_id`, `new_state`, `next_due`. Sets `duplicate=True`
    when a matching `client_review_id` already exists.
    """
    from app.services.canonical_resolution import resolve_canonical_lemma_id

    lemma_id = resolve_canonical_lemma_id(db, lemma_id)

    if client_review_id:
        existing = (
            db.query(ReviewLog)
            .filter(ReviewLog.client_review_id == client_review_id)
            .first()
        )
        if existing:
            knowledge = (
                db.query(UserLemmaKnowledge)
                .filter(UserLemmaKnowledge.lemma_id == lemma_id)
                .first()
            )
            card_data = parse_json_column(knowledge.fsrs_card_json if knowledge else None)
            return {
                "lemma_id": lemma_id,
                "new_state": knowledge.knowledge_state if knowledge else "new",
                "next_due": card_data.get("due", ""),
                "duplicate": True,
            }

    knowledge = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )
    if not knowledge:
        knowledge = UserLemmaKnowledge(
            lemma_id=lemma_id,
            knowledge_state="learning",
            source="encountered",
            total_encounters=0,
        )
        db.add(knowledge)

    card_data = parse_json_column(knowledge.fsrs_card_json)
    card = Card() if not card_data else Card.from_dict(card_data)
    fsrs_rating = RATING_MAP[rating_int]

    old_card_dict = card.to_dict() if card_data else None
    old_times_seen = knowledge.times_seen or 0
    old_times_correct = knowledge.times_correct or 0
    old_total_encounters = knowledge.total_encounters or 0
    old_knowledge_state = knowledge.knowledge_state
    old_lifecycle = lifecycle_snapshot(knowledge)

    now = datetime.now(timezone.utc)
    new_card, _review_log = scheduler.review_card(card, fsrs_rating, now)

    new_state = STATE_MAP.get(new_card.state, "learning")
    card_dict = new_card.to_dict()
    stability = card_dict.get("stability", 0)
    if new_state == "known" and stability < 1.0:
        new_state = "lapsed"
    knowledge.fsrs_card_json = card_dict
    knowledge.knowledge_state = new_state
    knowledge.last_reviewed = now
    knowledge.times_seen = old_times_seen + 1
    if rating_int >= 3:
        knowledge.times_correct = old_times_correct + 1
    record_review_result(knowledge, rating_int, now)

    log_entry = ReviewLog(
        lemma_id=lemma_id,
        rating=rating_int,
        reviewed_at=now,
        response_ms=response_ms,
        session_id=session_id,
        review_mode=review_mode,
        comprehension_signal=comprehension_signal,
        client_review_id=client_review_id,
        sentence_id=sentence_id,
        is_acquisition=False,
        event_type="fsrs_review",
        fsrs_log_json={
            "rating": rating_int,
            "state": new_state,
            "stability": card_dict.get("stability"),
            "pre_card": old_card_dict,
            "pre_times_seen": old_times_seen,
            "pre_times_correct": old_times_correct,
            "pre_total_encounters": old_total_encounters,
            "pre_knowledge_state": old_knowledge_state,
            **old_lifecycle,
        },
    )
    db.add(log_entry)
    if commit:
        db.commit()
    else:
        db.flush()

    return {
        "lemma_id": lemma_id,
        "new_state": new_state,
        "next_due": new_card.due.isoformat(),
    }


def record_scaffold_confirmation(
    db: Session,
    lemma_id: int,
    *,
    rating_int: int = 3,
    response_ms: Optional[int] = None,
    session_id: Optional[str] = None,
    review_mode: str = "reading",
    comprehension_signal: Optional[str] = None,
    client_review_id: Optional[str] = None,
    sentence_id: Optional[int] = None,
    credit_type: str = "collateral",
) -> dict:
    """Confirm an assumed-known scaffold lemma via collateral exposure.

    For a word in knowledge_state='known' with NO FSRS card (bulk-marked /
    cognate-known scaffold): a green, non-missed appearance in a shown sentence
    is verification evidence. We record it durably — a ReviewLog row plus
    counter bumps and a `confirmed_at` stamp — WITHOUT creating an FSRS card or
    scheduling review. Confirmed scaffold stays out of the rotation until a
    future red miss lapses it into acquisition (handled by the caller). This
    honours the equal-evaluation principle (every lemma in a shown sentence is
    evaluated) while respecting Hard Invariant 6's no-flood intent.

    The caller resolves canonical + filters non-content/inactive before calling;
    this is the leaf write. Idempotent on `client_review_id`. Never commits —
    the sentence-review caller commits once for the whole sentence.
    """
    if client_review_id:
        existing = (
            db.query(ReviewLog)
            .filter(ReviewLog.client_review_id == client_review_id)
            .first()
        )
        if existing:
            return {"lemma_id": lemma_id, "new_state": "known", "next_due": "", "duplicate": True}

    knowledge = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )
    if not knowledge:
        return {"lemma_id": lemma_id, "new_state": "new", "next_due": "", "duplicate": False}

    now = datetime.now(timezone.utc)
    old_times_seen = knowledge.times_seen or 0
    old_times_correct = knowledge.times_correct or 0
    old_total_encounters = knowledge.total_encounters or 0
    old_distinct_contexts = knowledge.distinct_contexts or 0
    old_clean_exposures = knowledge.clean_exposures or 0
    old_confirmed_at = knowledge.confirmed_at

    knowledge.times_seen = old_times_seen + 1
    if rating_int >= 3:
        knowledge.times_correct = old_times_correct + 1
    knowledge.distinct_contexts = old_distinct_contexts + 1
    knowledge.clean_exposures = old_clean_exposures + 1
    knowledge.last_reviewed = now
    if knowledge.confirmed_at is None:
        knowledge.confirmed_at = now

    log_entry = ReviewLog(
        lemma_id=lemma_id,
        rating=rating_int,
        reviewed_at=now,
        response_ms=response_ms,
        session_id=session_id,
        review_mode=review_mode,
        comprehension_signal=comprehension_signal,
        client_review_id=client_review_id,
        sentence_id=sentence_id,
        is_acquisition=False,
        credit_type=credit_type,
        event_type="scaffold_confirmation",
        fsrs_log_json={
            "rating": rating_int,
            "scaffold_confirmation": True,
            "state": "known",
            "pre_card": None,
            "pre_times_seen": old_times_seen,
            "pre_times_correct": old_times_correct,
            "pre_total_encounters": old_total_encounters,
            "pre_distinct_contexts": old_distinct_contexts,
            "pre_clean_exposures": old_clean_exposures,
            "pre_confirmed_at": old_confirmed_at.isoformat() if old_confirmed_at else None,
            "pre_knowledge_state": "known",
        },
    )
    db.add(log_entry)
    db.flush()

    return {
        "lemma_id": lemma_id,
        "new_state": "known",
        "next_due": "",
        "confirmed": True,
        "clean_exposures": knowledge.clean_exposures,
        "duplicate": False,
    }
