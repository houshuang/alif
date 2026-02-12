"""Leech auto-management — detect and handle chronically failing words.

A word is a leech if: times_seen >= 8 AND accuracy < 40%.
Leeches are auto-suspended and scheduled for reintroduction after 14 days.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import Lemma, ReviewLog, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.interaction_logger import log_interaction

logger = logging.getLogger(__name__)

LEECH_MIN_REVIEWS = 8
LEECH_MAX_ACCURACY = 0.40
REINTRO_DELAY = timedelta(days=14)


def check_and_manage_leeches(db: Session) -> list[int]:
    """Check all active words for leech status and auto-suspend leeches.

    Returns list of lemma_ids that were suspended.
    """
    candidates = (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.knowledge_state.in_(["learning", "known", "lapsed", "acquiring"]),
            UserLemmaKnowledge.times_seen >= LEECH_MIN_REVIEWS,
        )
        .all()
    )

    suspended = []
    for ulk in candidates:
        accuracy = (ulk.times_correct or 0) / (ulk.times_seen or 1)
        if accuracy < LEECH_MAX_ACCURACY:
            ulk.knowledge_state = "suspended"
            ulk.leech_suspended_at = datetime.now(timezone.utc)
            ulk.acquisition_box = None
            ulk.acquisition_next_due = None
            suspended.append(ulk.lemma_id)

            log_interaction(
                event="leech_suspended",
                lemma_id=ulk.lemma_id,
                times_seen=ulk.times_seen,
                times_correct=ulk.times_correct,
                accuracy=round(accuracy, 3),
            )

    if suspended:
        db.commit()
        log_activity(
            db,
            event_type="leech_suspended",
            summary=f"Auto-suspended {len(suspended)} leech words",
            detail={"lemma_ids": suspended},
        )

    return suspended


def check_leech_reintroductions(db: Session) -> list[int]:
    """Check for leeches ready for reintroduction (14+ days since suspension).

    Resets them to acquisition box 1 for a fresh start.
    """
    from app.services.acquisition_service import start_acquisition

    cutoff = datetime.now(timezone.utc) - REINTRO_DELAY

    ready = (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.knowledge_state == "suspended",
            UserLemmaKnowledge.leech_suspended_at.isnot(None),
            UserLemmaKnowledge.leech_suspended_at <= cutoff,
        )
        .all()
    )

    reintroduced = []
    for ulk in ready:
        # Reset review stats for fresh start
        ulk.times_seen = 0
        ulk.times_correct = 0
        ulk.leech_suspended_at = None
        start_acquisition(db, ulk.lemma_id, source="leech_reintro")
        reintroduced.append(ulk.lemma_id)

        log_interaction(
            event="leech_reintroduced",
            lemma_id=ulk.lemma_id,
        )

    if reintroduced:
        db.commit()
        log_activity(
            db,
            event_type="leech_reintroduced",
            summary=f"Reintroduced {len(reintroduced)} leech words to acquisition",
            detail={"lemma_ids": reintroduced},
        )

    return reintroduced


def is_leech(ulk: UserLemmaKnowledge) -> bool:
    """Check if a word meets leech criteria."""
    if (ulk.times_seen or 0) < LEECH_MIN_REVIEWS:
        return False
    accuracy = (ulk.times_correct or 0) / (ulk.times_seen or 1)
    return accuracy < LEECH_MAX_ACCURACY


def check_single_word_leech(db: Session, lemma_id: int) -> bool:
    """Check if a specific word just became a leech after a review.

    Call this after each review submission. Returns True if word was suspended.
    """
    ulk = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )
    if not ulk or ulk.knowledge_state == "suspended":
        return False

    if is_leech(ulk):
        ulk.knowledge_state = "suspended"
        ulk.leech_suspended_at = datetime.now(timezone.utc)
        ulk.acquisition_box = None
        ulk.acquisition_next_due = None

        log_interaction(
            event="leech_suspended",
            lemma_id=lemma_id,
            times_seen=ulk.times_seen,
            times_correct=ulk.times_correct,
        )
        return True

    return False
