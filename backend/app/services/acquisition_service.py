"""Acquisition system — Leitner 3-box for newly introduced words.

Words go through three acquisition boxes before graduating to FSRS:
  Box 1: 4-hour interval
  Box 2: 1-day interval
  Box 3: 3-day interval

Graduation requires: box >= 3 + times_seen >= 5 + accuracy >= 60% (regardless of current rating)
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Lemma, ReviewLog, UserLemmaKnowledge
from app.services.fsrs_service import create_new_card, parse_json_column, STATE_MAP
from app.services.interaction_logger import log_interaction

logger = logging.getLogger(__name__)

BOX_INTERVALS = {
    1: timedelta(hours=4),
    2: timedelta(days=1),
    3: timedelta(days=3),
}

GRADUATION_MIN_REVIEWS = 5
GRADUATION_MIN_ACCURACY = 0.60


def start_acquisition(
    db: Session,
    lemma_id: int,
    source: str = "study",
    due_immediately: bool = False,
) -> UserLemmaKnowledge:
    """Start the acquisition process for a word.

    Creates or transitions ULK to acquiring state with box 1.
    If due_immediately=True, word is due right now (for auto-intro in current session).
    Otherwise, first review is due after BOX_INTERVALS[1] (4 hours).
    """
    now = datetime.now(timezone.utc)
    next_due = now if due_immediately else now + BOX_INTERVALS[1]

    ulk = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )

    if ulk:
        # Transition existing record (e.g. from "encountered")
        ulk.knowledge_state = "acquiring"
        ulk.acquisition_box = 1
        ulk.acquisition_next_due = next_due
        ulk.acquisition_started_at = now
        ulk.introduced_at = now
        ulk.source = source
        ulk.fsrs_card_json = None  # No FSRS card during acquisition
    else:
        ulk = UserLemmaKnowledge(
            lemma_id=lemma_id,
            knowledge_state="acquiring",
            acquisition_box=1,
            acquisition_next_due=next_due,
            acquisition_started_at=now,
            introduced_at=now,
            source=source,
            fsrs_card_json=None,
            times_seen=0,
            times_correct=0,
            total_encounters=0,
        )
        db.add(ulk)

    db.flush()
    return ulk


def submit_acquisition_review(
    db: Session,
    lemma_id: int,
    rating_int: int,
    response_ms: Optional[int] = None,
    session_id: Optional[str] = None,
    review_mode: str = "reading",
    comprehension_signal: Optional[str] = None,
    client_review_id: Optional[str] = None,
) -> dict:
    """Submit a review for a word in the acquisition phase.

    Rating >= 3: advance box (1→2→3), graduate from box 3 if criteria met
    Rating == 2: stay in same box, reset interval
    Rating == 1: reset to box 1

    Returns dict with new state info.
    """
    if client_review_id:
        existing = (
            db.query(ReviewLog)
            .filter(ReviewLog.client_review_id == client_review_id)
            .first()
        )
        if existing:
            ulk = (
                db.query(UserLemmaKnowledge)
                .filter(UserLemmaKnowledge.lemma_id == lemma_id)
                .first()
            )
            return {
                "lemma_id": lemma_id,
                "new_state": ulk.knowledge_state if ulk else "acquiring",
                "acquisition_box": ulk.acquisition_box if ulk else None,
                "next_due": ulk.acquisition_next_due.isoformat() if ulk and ulk.acquisition_next_due else "",
                "duplicate": True,
            }

    now = datetime.now(timezone.utc)

    ulk = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )
    if not ulk or ulk.knowledge_state != "acquiring":
        logger.warning(f"submit_acquisition_review called for non-acquiring lemma {lemma_id}")
        # Fall back to normal FSRS review
        from app.services.fsrs_service import submit_review
        return submit_review(
            db, lemma_id=lemma_id, rating_int=rating_int,
            response_ms=response_ms, session_id=session_id,
            review_mode=review_mode, comprehension_signal=comprehension_signal,
            client_review_id=client_review_id,
        )

    old_box = ulk.acquisition_box or 1
    old_times_seen = ulk.times_seen or 0
    old_times_correct = ulk.times_correct or 0
    old_knowledge_state = ulk.knowledge_state

    # Update review counts
    ulk.times_seen = old_times_seen + 1
    if rating_int >= 3:
        ulk.times_correct = old_times_correct + 1
    ulk.last_reviewed = now
    ulk.total_encounters = (ulk.total_encounters or 0) + 1

    # Box advancement logic
    graduated = False
    if rating_int >= 3:
        # Good/Easy: advance box
        if old_box >= 3:
            # Stay in box 3, schedule next review (graduation checked below)
            ulk.acquisition_box = 3
            ulk.acquisition_next_due = now + BOX_INTERVALS[3]
        else:
            new_box = old_box + 1
            ulk.acquisition_box = new_box
            ulk.acquisition_next_due = now + BOX_INTERVALS[new_box]
    elif rating_int == 2:
        # Hard: stay in same box, reset interval
        ulk.acquisition_box = old_box
        ulk.acquisition_next_due = now + BOX_INTERVALS[old_box]
    else:
        # Again: reset to box 1
        ulk.acquisition_box = 1
        ulk.acquisition_next_due = now + BOX_INTERVALS[1]

    # Check graduation regardless of this review's rating
    # A word in box 3 with strong cumulative stats should graduate even after a weaker review
    if not graduated and ulk.acquisition_box >= 3:
        new_times_seen = ulk.times_seen
        new_times_correct = ulk.times_correct
        accuracy = new_times_correct / new_times_seen if new_times_seen > 0 else 0
        if new_times_seen >= GRADUATION_MIN_REVIEWS and accuracy >= GRADUATION_MIN_ACCURACY:
            graduated = True

    if graduated:
        _graduate(ulk, now)

    # Log review
    log_entry = ReviewLog(
        lemma_id=lemma_id,
        rating=rating_int,
        reviewed_at=now,
        response_ms=response_ms,
        session_id=session_id,
        review_mode=review_mode,
        comprehension_signal=comprehension_signal,
        client_review_id=client_review_id,
        is_acquisition=True,
        fsrs_log_json={
            "rating": rating_int,
            "state": ulk.knowledge_state,
            "acquisition_box_before": old_box,
            "acquisition_box_after": ulk.acquisition_box,
            "graduated": graduated,
            "pre_times_seen": old_times_seen,
            "pre_times_correct": old_times_correct,
            "pre_knowledge_state": old_knowledge_state,
        },
    )
    db.add(log_entry)
    db.commit()

    next_due = ""
    if ulk.acquisition_next_due:
        next_due = ulk.acquisition_next_due.isoformat()
    elif ulk.fsrs_card_json:
        card_data = parse_json_column(ulk.fsrs_card_json)
        next_due = card_data.get("due", "")

    return {
        "lemma_id": lemma_id,
        "new_state": ulk.knowledge_state,
        "acquisition_box": ulk.acquisition_box,
        "graduated": graduated,
        "next_due": next_due,
    }


def _graduate(ulk: UserLemmaKnowledge, now: datetime) -> None:
    """Graduate a word from acquisition to FSRS."""
    from fsrs import Scheduler, Card, Rating

    ulk.knowledge_state = "learning"
    ulk.acquisition_box = None
    ulk.acquisition_next_due = None
    ulk.graduated_at = now

    # Create FSRS card with initial Good review to set baseline stability
    scheduler = Scheduler()
    card = Card()
    new_card, _ = scheduler.review_card(card, Rating.Good, now)
    ulk.fsrs_card_json = new_card.to_dict()

    log_interaction(
        event="word_graduated",
        lemma_id=ulk.lemma_id,
        times_seen=ulk.times_seen,
        times_correct=ulk.times_correct,
    )


def get_acquisition_due(
    db: Session,
    now: Optional[datetime] = None,
) -> list[int]:
    """Get lemma_ids of words due for acquisition review."""
    if now is None:
        now = datetime.now(timezone.utc)

    rows = (
        db.query(UserLemmaKnowledge.lemma_id)
        .filter(
            UserLemmaKnowledge.knowledge_state == "acquiring",
            UserLemmaKnowledge.acquisition_box.isnot(None),
            UserLemmaKnowledge.acquisition_next_due <= now,
        )
        .all()
    )
    return [r[0] for r in rows]


def get_acquisition_stats(db: Session) -> dict:
    """Get summary stats about the acquisition pipeline."""
    acquiring = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.knowledge_state == "acquiring")
        .all()
    )

    box_counts = {1: 0, 2: 0, 3: 0}
    for ulk in acquiring:
        box = ulk.acquisition_box or 1
        if box in box_counts:
            box_counts[box] += 1

    now = datetime.now(timezone.utc)
    due_count = 0
    for ulk in acquiring:
        if ulk.acquisition_next_due:
            due_dt = ulk.acquisition_next_due
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=timezone.utc)
            if due_dt <= now:
                due_count += 1

    return {
        "total_acquiring": len(acquiring),
        "box_1": box_counts[1],
        "box_2": box_counts[2],
        "box_3": box_counts[3],
        "due_now": due_count,
    }
