"""Cumulative figures for the learner's current UTC day, shown between batches."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models import ReviewLog, SentenceReviewLog, UserLemmaKnowledge
from app.services.acquisition_service import true_new_acquisition_episode_filter
from app.services.fsrs_service import parse_json_column
from app.services.sentence_review_service import _parse_card_datetime


def _card_key(log_id: int, client_review_id: str | None) -> str:
    """One passage card logs several child sentences as ``<id>:s<n>``."""
    if not client_review_id:
        return f"legacy:{log_id}"
    base, marker, suffix = client_review_id.rpartition(":s")
    return base if marker and suffix.isdigit() else client_review_id


def _was_due(log: object, reviewed_at: datetime | None) -> bool:
    """Acquisition logs record due status; FSRS logs keep the card before the review."""
    if not isinstance(log, dict):
        return False
    if log.get("is_due_at_review") is True:
        return True
    pre_card = log.get("pre_card")
    if not isinstance(pre_card, dict) or reviewed_at is None:
        return False
    due_at = _parse_card_datetime(pre_card.get("due"))
    reviewed = reviewed_at if reviewed_at.tzinfo else reviewed_at.replace(tzinfo=timezone.utc)
    return due_at is not None and due_at <= reviewed


def today_progress(db: Session, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)

    cards = {
        _card_key(log_id, client_review_id)
        for log_id, client_review_id in db.query(
            SentenceReviewLog.id, SentenceReviewLog.client_review_id
        ).filter(SentenceReviewLog.reviewed_at >= start)
    }
    words = (
        db.query(
            func.count(ReviewLog.id),
            func.sum(case((ReviewLog.rating >= 3, 1), else_=0)),
        )
        .filter(ReviewLog.reviewed_at >= start, ReviewLog.sentence_id.isnot(None))
        .first()
    )
    new_words = (
        db.query(func.count(UserLemmaKnowledge.id))
        .filter(
            UserLemmaKnowledge.acquisition_started_at >= start,
            true_new_acquisition_episode_filter(),
        )
        .scalar()
    )
    graduated = (
        db.query(func.count(UserLemmaKnowledge.id))
        .filter(UserLemmaKnowledge.graduated_at >= start)
        .scalar()
    )
    due_or_target: set[int] = set()
    for lemma_id, credit_type, reviewed_at, log in db.query(
        ReviewLog.lemma_id, ReviewLog.credit_type, ReviewLog.reviewed_at, ReviewLog.fsrs_log_json
    ).filter(ReviewLog.reviewed_at >= start, ReviewLog.sentence_id.isnot(None)):
        if credit_type == "primary" or _was_due(parse_json_column(log), reviewed_at):
            due_or_target.add(lemma_id)
    moved_up: set[int] = set()
    for lemma_id, log in db.query(ReviewLog.lemma_id, ReviewLog.fsrs_log_json).filter(
        ReviewLog.reviewed_at >= start, ReviewLog.is_acquisition.is_(True)
    ):
        log = parse_json_column(log)
        if not isinstance(log, dict) or log.get("graduated"):
            continue
        before, after = log.get("acquisition_box_before"), log.get("acquisition_box_after")
        if isinstance(before, int) and isinstance(after, int) and after > before:
            moved_up.add(lemma_id)
    known = (
        db.query(func.count(UserLemmaKnowledge.id))
        .filter(UserLemmaKnowledge.knowledge_state.in_(["known", "learning"]))
        .scalar()
    )
    return {
        "cards": len(cards),
        "due_words": len(due_or_target),
        "words_reviewed": int(words[0] or 0),
        "words_correct": int(words[1] or 0),
        "new_words": int(new_words or 0),
        "moved_up": len(moved_up),
        "graduated": int(graduated or 0),
        "known": int(known or 0),
    }
