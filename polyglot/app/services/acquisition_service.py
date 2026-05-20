"""Acquisition phase — Leitner 3-box for newly introduced words.

Ported from Alif's `acquisition_service`. Same box intervals (4h / 1d / 3d)
and same tiered graduation (first-correct, perfect-accuracy, high-accuracy,
standard). Differences:

- No Arabic-specific enrichment (root/pattern). Polyglot doesn't have those
  models.
- No memory-hooks regeneration. Mnemonics are deferred (see polyglot/IDEAS).

Lifecycle:
    encountered -> acquiring (Box 1, 4h) -> Box 2 (1d) -> Box 3 (3d) -> learning (FSRS)

Variant resolution is enforced at function entry: any caller-supplied
``lemma_id`` is redirected to its canonical via ``resolve_canonical_lemma_id``
before any ULK creation or mutation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models import ReviewLog, UserLemmaKnowledge
from app.services.fsrs_service import parse_json_column, STATE_MAP
from app.services.interaction_logger import log_interaction

logger = logging.getLogger(__name__)

BOX_INTERVALS = {
    1: timedelta(hours=4),
    2: timedelta(days=1),
    3: timedelta(days=3),
}

GRADUATION_MIN_REVIEWS = 5
GRADUATION_MIN_ACCURACY = 0.60
GRADUATION_MIN_CALENDAR_DAYS = 2

# Tier-0 / Tier-1 / Tier-2 fast-grad and Box 1→2 advancement must NOT fire
# when the intro card was shown moments ago — that's working memory, not
# learning. Require this much elapsed time since the intro card before
# allowing fast paths.
FAST_GRAD_INTRO_GAP = timedelta(minutes=10)
# Correct reviews inside the intro-card gap count for exposure/accuracy, but
# should stay in Box 1 and come back soon instead of proving consolidation.
FAST_INTRO_RETRY_INTERVAL = timedelta(minutes=30)

# Daily intro budget. Mirrors Alif's policy: 30 net-new acquisitions per UTC day
# under normal load; recovery-mode budget kicks in when Box 1/2 debt piles up.
DAILY_INTRO_CAP = 30

RECOVERY_BOX1_UNREVIEWED_LIMIT = 5
RECOVERY_BOX2_DUE_LIMIT = 30
RECOVERY_MIN_REVIEWS_FOR_ANY_INTRO = 20
RECOVERY_MIN_REVIEWS_FOR_FULL_BUDGET = 60
RECOVERY_LOW_ACCURACY_FLOOR = 0.80
RECOVERY_GOOD_ACCURACY_FLOOR = 0.85
RECOVERY_MID_INTRO_BUDGET = 4
RECOVERY_FULL_INTRO_BUDGET = 8

# Sources that subsequent callers are allowed to overwrite. Strong sources
# (`textbook_scan`, `reading_intake`) should win when they upgrade a weak
# provisional source like ``encountered`` / ``collateral``.
_OVERRIDABLE_SOURCES = {None, "study", "encountered", "auto_intro", "collateral", "leech_reintro"}
_HIGH_PRIORITY_SOURCES = {"textbook_scan", "reading_intake", "frequency_core"}


def _daily_intro_count(db: Session, today_start: datetime) -> int:
    """Count today's net-new acquisitions (excludes leech reintroductions)."""
    return (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.acquisition_started_at >= today_start,
            (
                UserLemmaKnowledge.source.is_(None)
                | (UserLemmaKnowledge.source != "leech_reintro")
            ),
        )
        .count()
    )


def _recovery_backlog_counts(db: Session, now: datetime) -> tuple[int, int]:
    """Return (unreviewed box-1 count, due box-2 count) — recovery-mode signal."""
    box1_unreviewed = (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.knowledge_state == "acquiring",
            UserLemmaKnowledge.acquisition_box == 1,
            (UserLemmaKnowledge.times_seen == 0) | (UserLemmaKnowledge.times_seen.is_(None)),
        )
        .count()
    )
    box2_due = (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.knowledge_state == "acquiring",
            UserLemmaKnowledge.acquisition_box == 2,
            UserLemmaKnowledge.acquisition_next_due <= now,
        )
        .count()
    )
    return box1_unreviewed, box2_due


def _recovery_mode_intro_budget(db: Session, now: datetime, today_start: datetime) -> int:
    """Effective daily intro cap under acquisition overload.

    Normal load → ``DAILY_INTRO_CAP``. Overload (lots of Box 1/2 debt) → new
    words must be earned by same-day review practice. Below the practice
    floor the budget is 0 entirely; above it scales with accuracy.
    """
    box1_unreviewed, box2_due = _recovery_backlog_counts(db, now)
    overloaded = (
        box1_unreviewed >= RECOVERY_BOX1_UNREVIEWED_LIMIT
        or box2_due >= RECOVERY_BOX2_DUE_LIMIT
    )
    if not overloaded:
        return DAILY_INTRO_CAP

    reviews_today = (
        db.query(ReviewLog)
        .filter(ReviewLog.reviewed_at >= today_start)
        .all()
    )
    if len(reviews_today) < RECOVERY_MIN_REVIEWS_FOR_ANY_INTRO:
        return 0

    accuracy: float | None = None
    if len(reviews_today) >= 10:
        accuracy = sum(1 for r in reviews_today if r.rating >= 3) / len(reviews_today)
        if accuracy < RECOVERY_LOW_ACCURACY_FLOOR:
            return 0

    if accuracy is not None and accuracy < RECOVERY_GOOD_ACCURACY_FLOOR:
        return RECOVERY_MID_INTRO_BUDGET

    if len(reviews_today) >= RECOVERY_MIN_REVIEWS_FOR_FULL_BUDGET:
        return RECOVERY_FULL_INTRO_BUDGET
    return RECOVERY_MID_INTRO_BUDGET


def _intro_shown_recently(ulk: UserLemmaKnowledge, now: datetime) -> bool:
    """True iff an intro card was shown for this lemma within the
    working-memory window. Tier 0/1/2 graduation and Box 1→2 advancement
    are blocked while this returns True.
    """
    intro_shown = ulk.experiment_intro_shown_at
    if intro_shown is None:
        return False
    if intro_shown.tzinfo is None:
        intro_shown = intro_shown.replace(tzinfo=timezone.utc)
    gap = now - intro_shown
    return timedelta(0) <= gap < FAST_GRAD_INTRO_GAP


def _reviews_span_calendar_days(db: Session, lemma_id: int, min_days: int) -> bool:
    """Check if acquisition reviews for a word span at least N UTC calendar days."""
    reviews = (
        db.query(ReviewLog.reviewed_at)
        .filter(
            ReviewLog.lemma_id == lemma_id,
            ReviewLog.is_acquisition == True,  # noqa: E712
        )
        .all()
    )
    dates = set()
    for (reviewed_at,) in reviews:
        if reviewed_at:
            dt = reviewed_at
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dates.add(dt.date())
    return len(dates) >= min_days


def start_acquisition(
    db: Session,
    lemma_id: int,
    source: str = "study",
    due_immediately: bool = False,
    enforce_daily_cap: bool = True,
) -> UserLemmaKnowledge:
    """Start (or re-start) the acquisition process for a word.

    - Creates or transitions the ULK to ``knowledge_state='acquiring'`` at Box 1.
    - ``due_immediately=True`` skips the 4h Box-1 interval (use for words the
      user just marked unknown — they should see practice immediately).
    - ``enforce_daily_cap=True`` (default) honours the daily intro budget; if
      the cap is hit, the row stays/becomes ``encountered`` instead.

    Variant lemmas are redirected to their canonical at entry. Existing
    ``learning``/``known`` rows are never demoted back to acquiring — the
    function returns them unchanged.
    """
    from app.services.canonical_resolution import resolve_canonical_lemma_id

    canonical_id = resolve_canonical_lemma_id(db, lemma_id)
    if canonical_id != lemma_id:
        logger.info(
            "start_acquisition redirecting variant %s → canonical %s (source=%r)",
            lemma_id, canonical_id, source,
        )
        lemma_id = canonical_id

    now = datetime.now(timezone.utc)
    next_due = now if due_immediately else now + BOX_INTERVALS[1]

    ulk = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )

    if ulk and ulk.knowledge_state in ("known", "learning"):
        return ulk

    if ulk and ulk.knowledge_state == "acquiring":
        return ulk

    cap_hit = False
    if enforce_daily_cap and source != "leech_reintro":
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        intro_count = _daily_intro_count(db, today_start)
        effective_cap = _recovery_mode_intro_budget(db, now, today_start)
        if intro_count >= effective_cap:
            cap_hit = True
            logger.info(
                "Daily intro budget (%d) reached; lemma %s stays in encountered "
                "(source=%r, today_count=%d)",
                effective_cap, lemma_id, source, intro_count,
            )

    if cap_hit:
        if ulk:
            return ulk
        ulk = UserLemmaKnowledge(
            lemma_id=lemma_id,
            knowledge_state="encountered",
            source=source,
            fsrs_card_json=None,
            times_seen=0,
            times_correct=0,
            total_encounters=0,
        )
        db.add(ulk)
        db.flush()
        return ulk

    if ulk:
        ulk.knowledge_state = "acquiring"
        ulk.acquisition_box = 1
        ulk.acquisition_next_due = next_due
        ulk.acquisition_started_at = now
        ulk.entered_acquiring_at = now
        ulk.introduced_at = now
        if source != "collateral" and (
            not ulk.source
            or ulk.source in _OVERRIDABLE_SOURCES
            or source in _HIGH_PRIORITY_SOURCES
        ):
            ulk.source = source
        ulk.fsrs_card_json = None
    else:
        ulk = UserLemmaKnowledge(
            lemma_id=lemma_id,
            knowledge_state="acquiring",
            acquisition_box=1,
            acquisition_next_due=next_due,
            acquisition_started_at=now,
            entered_acquiring_at=now,
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
    sentence_id: Optional[int] = None,
    commit: bool = True,
) -> dict:
    """Apply a review to a word that's in the acquisition phase.

    Rating semantics (same as FSRS):
        1 = Again → reset to Box 1 (failure resets)
        2 = Hard  → stay in current box, refresh timer if due
        3 = Good  → advance one box (or graduate)
        4 = Easy  → same as Good for box advancement (graduation pathway is
                    handled via tiered criteria below)

    Tiered graduation (in order, first match wins):
        Tier 0: first-correct → graduate immediately (times_seen was 0, rating ≥ 3)
        Tier 1: 100% accuracy, ≥ 3 reviews → graduate from any box
        Tier 2: ≥ 80% accuracy, ≥ 4 reviews, Box ≥ 2 → graduate
        Tier 3: Box 3, ≥ 5 reviews, ≥ 60% accuracy, spans ≥ 2 UTC days → graduate

    Falls back to FSRS submit when the lemma isn't actually in acquisition
    state (defensive — should rarely happen in practice).

    Variant lemmas are redirected to their canonical at function entry per
    Hard Invariant #9.
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
        logger.warning(
            "submit_acquisition_review called for non-acquiring lemma %s (state=%s); "
            "delegating to FSRS",
            lemma_id, getattr(ulk, "knowledge_state", None),
        )
        from app.services.fsrs_service import submit_review
        return submit_review(
            db, lemma_id=lemma_id, rating_int=rating_int,
            response_ms=response_ms, session_id=session_id,
            review_mode=review_mode, comprehension_signal=comprehension_signal,
            client_review_id=client_review_id, sentence_id=sentence_id,
            commit=commit,
        )

    old_box = ulk.acquisition_box or 1
    old_acquisition_box = ulk.acquisition_box
    old_acquisition_next_due = ulk.acquisition_next_due
    old_graduated_at = ulk.graduated_at
    old_fsrs_card_json = parse_json_column(ulk.fsrs_card_json) if ulk.fsrs_card_json is not None else None
    old_times_seen = ulk.times_seen or 0
    old_times_correct = ulk.times_correct or 0
    old_total_encounters = ulk.total_encounters or 0
    old_knowledge_state = ulk.knowledge_state
    recent_intro = _intro_shown_recently(ulk, now)

    ulk.times_seen = old_times_seen + 1
    if rating_int >= 3:
        ulk.times_correct = old_times_correct + 1
    ulk.last_reviewed = now
    ulk.total_encounters = (ulk.total_encounters or 0) + 1

    is_due = True
    if ulk.acquisition_next_due:
        acq_due = ulk.acquisition_next_due
        if acq_due.tzinfo is None:
            acq_due = acq_due.replace(tzinfo=timezone.utc)
        is_due = acq_due <= now

    graduated = False
    # Tier 0: first correct review → graduate immediately.
    # Blocked when the intro card was shown within FAST_GRAD_INTRO_GAP — a
    # correct rating seconds after the intro is working memory, not learning,
    # and bypassing acquisition robs the encoding phase.
    if old_times_seen == 0 and rating_int >= 3 and not recent_intro:
        _graduate(ulk, now)
        graduated = True

    if not graduated and rating_int >= 3:
        if old_box == 1:
            if recent_intro:
                # Still inside the intro-card working-memory window. Count the
                # correct exposure, but keep the word in encoding and come
                # back soon instead of advancing to next-day consolidation.
                ulk.acquisition_box = 1
                ulk.acquisition_next_due = now + FAST_INTRO_RETRY_INTERVAL
            else:
                ulk.acquisition_box = 2
                ulk.acquisition_next_due = now + BOX_INTERVALS[2]
        elif old_box == 2 and is_due:
            ulk.acquisition_box = 3
            ulk.acquisition_next_due = now + BOX_INTERVALS[3]
        elif old_box >= 3 and is_due:
            ulk.acquisition_box = 3
            ulk.acquisition_next_due = now + BOX_INTERVALS[3]
        # Else: not due yet — count the exposure but don't advance/reset timer.
    elif rating_int == 2:
        # Hard: stay in same box
        if is_due:
            if (ulk.times_correct or 0) == 0:
                ulk.acquisition_next_due = now + timedelta(minutes=10)
            else:
                ulk.acquisition_next_due = now + BOX_INTERVALS[old_box]
        ulk.acquisition_box = old_box
    else:
        # Again (rating == 1): reset to Box 1 (failure resets regardless of due)
        ulk.acquisition_box = 1
        if (ulk.times_correct or 0) == 0:
            ulk.acquisition_next_due = now + timedelta(minutes=5)
        else:
            ulk.acquisition_next_due = now + BOX_INTERVALS[1]

    if not graduated:
        new_times_seen = ulk.times_seen
        new_times_correct = ulk.times_correct
        accuracy = new_times_correct / new_times_seen if new_times_seen > 0 else 0

        # Tier 1: perfect accuracy, ≥ 3 reviews → graduate from any box.
        # The intro-card gap blocks this too; otherwise three immediate
        # same-session correct answers could still graduate on working memory.
        if not recent_intro and accuracy >= 1.0 and new_times_seen >= 3:
            graduated = True
        # Tier 2: ≥ 80% accuracy, ≥ 4 reviews → graduate from Box ≥ 2
        elif not recent_intro and accuracy >= 0.80 and new_times_seen >= 4 and (ulk.acquisition_box or 1) >= 2:
            graduated = True
        # Tier 3: standard — Box 3, ≥ 5 reviews, ≥ 60% accuracy, ≥ 2 calendar days
        elif (
            is_due
            and (ulk.acquisition_box or 1) >= 3
            and new_times_seen >= GRADUATION_MIN_REVIEWS
            and accuracy >= GRADUATION_MIN_ACCURACY
            and _reviews_span_calendar_days(db, ulk.lemma_id, GRADUATION_MIN_CALENDAR_DAYS)
        ):
            graduated = True

    if graduated:
        _graduate(ulk, now)

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
        is_acquisition=True,
        fsrs_log_json={
            "rating": rating_int,
            "state": ulk.knowledge_state,
            "acquisition_box_before": old_box,
            "acquisition_box_after": ulk.acquisition_box,
            "graduated": graduated,
            "pre_times_seen": old_times_seen,
            "pre_times_correct": old_times_correct,
            "pre_total_encounters": old_total_encounters,
            "pre_knowledge_state": old_knowledge_state,
            "pre_card": old_fsrs_card_json,
            "pre_acquisition_box": old_acquisition_box,
            "pre_acquisition_next_due": (
                old_acquisition_next_due.isoformat()
                if old_acquisition_next_due is not None
                else None
            ),
            "pre_graduated_at": (
                old_graduated_at.isoformat()
                if old_graduated_at is not None
                else None
            ),
            "intro_working_memory_blocked": recent_intro and rating_int >= 3 and old_box == 1,
        },
    )
    db.add(log_entry)
    if commit:
        db.commit()
    else:
        db.flush()

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
    """Graduate a word out of acquisition into FSRS."""
    from fsrs import Scheduler, Card, Rating

    ulk.knowledge_state = "learning"
    ulk.acquisition_box = None
    ulk.acquisition_next_due = None
    ulk.graduated_at = now

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
    """Lemma ids whose next acquisition review is due."""
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
    """Summary stats for the acquisition pipeline."""
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
