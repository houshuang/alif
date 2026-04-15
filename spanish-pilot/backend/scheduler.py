"""Scheduler: 3-phase lifecycle (new → Leitner 3-box → FSRS-6), matching Alif's model.

Ratings: 1=Again, 2=Hard, 3=Good, 4=Easy.

- new → acquiring (box 1) at first review
- Leitner box 1 (pass → box 2, +4h; fail → stay, +30min)
- Leitner box 2 (pass → box 3, +1d; fail → box 1, +30min)
- Leitner box 3 (pass → graduate to FSRS; fail → box 1, +30min)
- FSRS: use py-fsrs v6 with default scheduler
- Lapsed: FSRS-controlled. "Known" when FSRS stability ≥ 1.0.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fsrs import Scheduler as FsrsScheduler, Card as FsrsCard, Rating as FsrsRating

from .models import Card

# Leitner intervals
BOX_INTERVALS = {
    1: timedelta(hours=4),
    2: timedelta(days=1),
    3: timedelta(days=3),
}
LEITNER_FAIL_DELAY = timedelta(minutes=30)

KNOWN_STABILITY_THRESHOLD = 1.0

_fsrs = FsrsScheduler()


def _now() -> datetime:
    return datetime.utcnow()


def _to_fsrs_rating(rating: int) -> FsrsRating:
    """1-4 rating → FSRS Rating enum."""
    if rating == 1:
        return FsrsRating.Again
    if rating == 2:
        return FsrsRating.Hard
    if rating == 3:
        return FsrsRating.Good
    return FsrsRating.Easy


def _deserialize_fsrs(d: Optional[dict]) -> FsrsCard:
    if not d:
        return FsrsCard()
    return FsrsCard.from_dict(d)


def _serialize_fsrs(c: FsrsCard) -> dict:
    return c.to_dict()


def _set_next_due(card: Card) -> None:
    """Unify next_due from whichever phase the card is in."""
    if card.state in {"new", "acquiring"}:
        card.next_due = card.acquisition_next_due
    else:
        fc = _deserialize_fsrs(card.fsrs_state_json)
        due = fc.due
        if due.tzinfo is not None:
            due = due.astimezone(timezone.utc).replace(tzinfo=None)
        card.next_due = due


def start_card(card: Card) -> None:
    """Initialize a new card: put in acquisition box 1, due immediately."""
    now = _now()
    card.state = "acquiring"
    card.acquisition_box = 1
    card.acquisition_next_due = now
    card.next_due = now
    card.introduced_at = now


def apply_review(card: Card, rating: int) -> None:
    """Apply a review outcome and update all scheduling state."""
    now = _now()
    card.times_seen += 1
    card.last_reviewed = now
    if rating >= 3:
        card.times_correct += 1
    else:
        card.times_wrong += 1

    if card.state == "new":
        # First review — start the card, THEN apply rating.
        start_card(card)

    if card.state == "acquiring":
        _apply_leitner(card, rating, now)
    else:
        # learning / known / lapsed — FSRS territory
        _apply_fsrs(card, rating, now)

    _set_next_due(card)


def _apply_leitner(card: Card, rating: int, now: datetime) -> None:
    pass_ = rating >= 3
    box = card.acquisition_box or 1
    if pass_:
        if box >= 3:
            # Graduate to FSRS
            card.state = "learning"
            card.acquisition_box = None
            card.acquisition_next_due = None
            card.graduated_at = now
            # Fresh FSRS card reviewed with 'Good' → first scheduled interval
            fc = FsrsCard()
            fc, _log = _fsrs.review_card(fc, _to_fsrs_rating(rating), review_datetime=now.replace(tzinfo=timezone.utc))
            card.fsrs_state_json = _serialize_fsrs(fc)
        else:
            card.acquisition_box = box + 1
            card.acquisition_next_due = now + BOX_INTERVALS[box + 1]
    else:
        card.acquisition_box = 1
        card.acquisition_next_due = now + LEITNER_FAIL_DELAY


def _apply_fsrs(card: Card, rating: int, now: datetime) -> None:
    fc = _deserialize_fsrs(card.fsrs_state_json)
    fc, _log = _fsrs.review_card(fc, _to_fsrs_rating(rating), review_datetime=now.replace(tzinfo=timezone.utc))
    card.fsrs_state_json = _serialize_fsrs(fc)

    # Classify: lapsed if rating=1 and already learning/known; known if stability high.
    stability = float(fc.stability) if fc.stability is not None else 0.0
    if rating == 1 and card.state in {"learning", "known"}:
        card.state = "lapsed"
    elif stability >= KNOWN_STABILITY_THRESHOLD:
        card.state = "known"
    elif card.state == "lapsed" and rating >= 3:
        # Back on track
        card.state = "learning"
    # else: stay in current state


def leitner_box_label(card: Card) -> Optional[int]:
    """Returns 1/2/3 for acquiring cards, None otherwise."""
    return card.acquisition_box if card.state == "acquiring" else None
