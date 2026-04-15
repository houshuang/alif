"""Scheduler — ported directly from Alif's acquisition_service + fsrs_service.

Keeps Alif's hard-won behaviors:
  - Tiered graduation (Tier 1: 100% acc + 3 reviews → instant; Tier 2: ≥80% + 4
    reviews + box ≥ 2; Tier 3: due + box ≥ 3 + 5 reviews + ≥60% + 2 calendar days)
  - First-correct shortcut (times_seen=0 + rating ≥ 3 → skip Leitner, graduate)
  - Rating 2 (Hard) stays in box with short reschedule
  - Rating 1 (Again) resets to box 1; 5min if never-correct, else 4h
  - Due-gating for box 2→3 and box 3→graduate (inter-session spacing)
  - Box 1→2 always advances (within-session encoding allowed)
  - FSRS stability < 1.0 → classify as "lapsed" not "known"

Stripped from Alif (Arabic/app-specific): root sibling boost for Easy grad,
mnemonic regeneration on failure, pattern enrichment hooks.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fsrs import Scheduler as FsrsScheduler, Card as FsrsCard, Rating as FsrsRating
from sqlalchemy.orm import Session

from .models import Card, ReviewLog

# ---- Constants (direct port from Alif) ----
BOX_INTERVALS = {
    1: timedelta(hours=4),
    2: timedelta(days=1),
    3: timedelta(days=3),
}

GRADUATION_MIN_REVIEWS = 5
GRADUATION_MIN_ACCURACY = 0.60
GRADUATION_MIN_CALENDAR_DAYS = 2
KNOWN_STABILITY_THRESHOLD = 1.0

_fsrs = FsrsScheduler()


def _now() -> datetime:
    return datetime.utcnow()


def _to_fsrs_rating(rating: int) -> FsrsRating:
    if rating == 1:
        return FsrsRating.Again
    if rating == 2:
        return FsrsRating.Hard
    if rating == 3:
        return FsrsRating.Good
    return FsrsRating.Easy


def _deserialize_fsrs(d: Optional[dict]) -> FsrsCard:
    return FsrsCard.from_dict(d) if d else FsrsCard()


def _serialize_fsrs(c: FsrsCard) -> dict:
    return c.to_dict()


def _reviews_span_calendar_days(db: Session, student_id: int, lemma_id: int, min_days: int) -> bool:
    """Check if reviews for a word span at least N distinct UTC calendar days."""
    reviews = (
        db.query(ReviewLog.shown_at)
        .filter(ReviewLog.student_id == student_id, ReviewLog.lemma_id == lemma_id)
        .all()
    )
    dates = set()
    for (dt,) in reviews:
        if dt:
            d = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            dates.add(d.date())
    return len(dates) >= min_days


def _set_next_due(card: Card) -> None:
    if card.state in {"new", "acquiring"}:
        card.next_due = card.acquisition_next_due
    else:
        fc = _deserialize_fsrs(card.fsrs_state_json)
        due = fc.due
        if due.tzinfo is not None:
            due = due.astimezone(timezone.utc).replace(tzinfo=None)
        card.next_due = due


def _graduate(card: Card, now: datetime, rating: int) -> None:
    """Graduate from Leitner to FSRS. Bootstrap with Good rating for first FSRS card."""
    card.state = "learning"
    card.acquisition_box = None
    card.acquisition_next_due = None
    card.graduated_at = now
    fc = FsrsCard()
    # Bootstrap with Good (not the actual rating) — matches Alif's behavior
    fc, _log = _fsrs.review_card(fc, FsrsRating.Good, review_datetime=now.replace(tzinfo=timezone.utc))
    card.fsrs_state_json = _serialize_fsrs(fc)


def start_card(card: Card, now: datetime, due_immediately: bool = True) -> None:
    """Put a new card into acquiring state, box 1."""
    card.state = "acquiring"
    card.acquisition_box = 1
    card.acquisition_next_due = now if due_immediately else now + BOX_INTERVALS[1]
    card.introduced_at = now
    card.times_seen = card.times_seen or 0


def apply_review(card: Card, rating: int, db: Optional[Session] = None) -> None:
    """Apply a review outcome to a card.

    Matches Alif's apply_acquisition_review flow:
      - Increment times_seen/correct
      - First-correct fast-track: times_seen was 0, rating ≥ 3 → graduate to FSRS
      - Box advancement (rating ≥ 3): box 1→2 always, box 2→3 if due, box 3 stay if due
      - Rating 2: stay in box with short reschedule
      - Rating 1: reset to box 1 with short interval
      - Tiered graduation check (T1: 100% + 3 reviews; T2: ≥80% + 4 reviews + box ≥ 2;
        T3: due + box ≥ 3 + 5 reviews + 60% + 2 calendar days)
    """
    now = _now()
    old_times_seen = card.times_seen or 0
    old_times_correct = card.times_correct or 0
    old_box = card.acquisition_box or 1

    card.times_seen = old_times_seen + 1
    card.last_reviewed = now
    if rating >= 3:
        card.times_correct = old_times_correct + 1
    else:
        card.times_wrong = (card.times_wrong or 0) + 1

    if card.state == "new":
        start_card(card, now, due_immediately=True)
        # old_box stays at 1 for this review

    if card.state != "acquiring":
        _apply_fsrs(card, rating, now)
        _set_next_due(card)
        return

    # --- Acquiring branch ---
    is_due = True
    if card.acquisition_next_due:
        due = card.acquisition_next_due
        due = due if due.tzinfo else due.replace(tzinfo=timezone.utc)
        is_due = due <= now.replace(tzinfo=timezone.utc)

    graduated = False

    # Fast-track: first correct review → graduate immediately (Alif's 0% lapse rate observation)
    if old_times_seen == 0 and rating >= 3:
        _graduate(card, now, rating)
        graduated = True

    if not graduated:
        if rating >= 3:
            if old_box == 1:
                card.acquisition_box = 2
                card.acquisition_next_due = now + BOX_INTERVALS[2]
            elif old_box == 2 and is_due:
                card.acquisition_box = 3
                card.acquisition_next_due = now + BOX_INTERVALS[3]
            elif old_box >= 3 and is_due:
                card.acquisition_box = 3
                card.acquisition_next_due = now + BOX_INTERVALS[3]
            # else: not-due review, no advancement, don't reset timer
        elif rating == 2:
            # Hard: stay, short reschedule if due
            if is_due:
                if card.times_correct == 0:
                    card.acquisition_next_due = now + timedelta(minutes=10)
                else:
                    card.acquisition_next_due = now + BOX_INTERVALS[old_box]
            card.acquisition_box = old_box
        else:
            # Again: reset to box 1
            card.acquisition_box = 1
            if card.times_correct == 0:
                card.acquisition_next_due = now + timedelta(minutes=5)
            else:
                card.acquisition_next_due = now + BOX_INTERVALS[1]

    # Tiered graduation check (T1/T2: no due gate — collateral reviews can graduate;
    # T3: due-gated for real spacing)
    if not graduated and db is not None:
        total = card.times_seen
        correct = card.times_correct
        accuracy = correct / total if total > 0 else 0

        if accuracy >= 1.0 and total >= 3:
            graduated = True
        elif accuracy >= 0.80 and total >= 4 and (card.acquisition_box or 1) >= 2:
            graduated = True
        elif (is_due
              and (card.acquisition_box or 1) >= 3
              and total >= GRADUATION_MIN_REVIEWS
              and accuracy >= GRADUATION_MIN_ACCURACY
              and _reviews_span_calendar_days(db, card.student_id, card.lemma_id, GRADUATION_MIN_CALENDAR_DAYS)):
            graduated = True

        if graduated:
            _graduate(card, now, rating)

    _set_next_due(card)


def _apply_fsrs(card: Card, rating: int, now: datetime) -> None:
    fc = _deserialize_fsrs(card.fsrs_state_json)
    fc, _log = _fsrs.review_card(fc, _to_fsrs_rating(rating), review_datetime=now.replace(tzinfo=timezone.utc))
    card.fsrs_state_json = _serialize_fsrs(fc)

    stability = float(fc.stability) if fc.stability is not None else 0.0
    if rating == 1 and card.state in {"learning", "known"}:
        card.state = "lapsed"
    elif stability >= KNOWN_STABILITY_THRESHOLD and rating >= 3:
        card.state = "known"
    elif card.state == "lapsed" and rating >= 3:
        card.state = "learning"
    # Alif edge case: FSRS said "review" but stability < 1.0 → classify lapsed
    if card.state == "known" and stability < KNOWN_STABILITY_THRESHOLD:
        card.state = "lapsed"
