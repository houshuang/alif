"""Core simulation loop — drives real services over simulated time.

Models realistic multi-session-per-day usage patterns:
- Multiple short sessions spread throughout the day (matching real user data)
- Time gaps between sessions enable same-day acquisition box progression (4h interval)
- Each session is a fresh build_session() + submit_sentence_review() cycle
"""

import logging
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from freezegun import freeze_time
from sqlalchemy.orm import Session

from app.models import UserLemmaKnowledge
from app.simulation.student import StudentProfile

logger = logging.getLogger(__name__)


@dataclass
class DaySnapshot:
    day: int
    date: str
    skipped: bool = False
    # Word state counts
    encountered: int = 0
    acquiring: int = 0
    learning: int = 0
    known: int = 0
    lapsed: int = 0
    suspended: int = 0
    # Acquisition box breakdown
    box_1: int = 0
    box_2: int = 0
    box_3: int = 0
    graduated_today: int = 0
    # Session metrics (aggregated across all sessions in the day)
    num_sessions: int = 0
    session_limit: int = 0  # total sentence slots across all sessions
    items_received: int = 0
    reviews_submitted: int = 0
    auto_introduced: int = 0
    # Comprehension distribution
    understood: int = 0
    partial: int = 0
    no_idea: int = 0
    # Review load
    total_due: int = 0
    covered_due: int = 0
    # Events
    leeches_detected: int = 0
    leeches_reintroduced: int = 0
    # Cohort
    cohort_size: int = 0


def _fill_state_counts(db: Session, snap: DaySnapshot) -> None:
    """Fill word state counts on a snapshot from current DB state."""
    all_ulk = db.query(UserLemmaKnowledge).all()
    for ulk in all_ulk:
        state = ulk.knowledge_state
        if state == "encountered":
            snap.encountered += 1
        elif state == "acquiring":
            snap.acquiring += 1
        elif state == "learning":
            snap.learning += 1
        elif state == "known":
            snap.known += 1
        elif state == "lapsed":
            snap.lapsed += 1
        elif state == "suspended":
            snap.suspended += 1

    acquiring = [u for u in all_ulk if u.knowledge_state == "acquiring"]
    for u in acquiring:
        box = u.acquisition_box or 1
        if box == 1:
            snap.box_1 += 1
        elif box == 2:
            snap.box_2 += 1
        elif box >= 3:
            snap.box_3 += 1


def _count_state(db: Session, state: str) -> int:
    return (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.knowledge_state == state)
        .count()
    )


def _session_times(num_sessions: int, base_morning: datetime) -> list[datetime]:
    """Generate realistic session start times spread throughout the day.

    Real user pattern: sessions at ~7am, 10am, 1pm, 3pm, 6pm etc.
    We spread sessions evenly across a 7am-9pm window (14 hours).
    """
    if num_sessions == 1:
        return [base_morning]

    start_hour = 7
    end_hour = 21
    window_minutes = (end_hour - start_hour) * 60
    gap = window_minutes // (num_sessions - 1) if num_sessions > 1 else 0
    # Add some jitter
    times = []
    for i in range(num_sessions):
        offset_minutes = i * gap + random.randint(-15, 15)
        offset_minutes = max(0, min(offset_minutes, window_minutes))
        t = base_morning.replace(hour=start_hour, minute=0, second=0) + timedelta(minutes=offset_minutes)
        times.append(t)
    return sorted(times)


def run_simulation(
    db: Session,
    days: int,
    profile: StudentProfile,
    start_date: datetime | None = None,
    seed: int = 42,
) -> list[DaySnapshot]:
    """Run a multi-day simulation using real services against the given DB session.

    Models multiple short sessions per day with realistic time gaps.
    All LLM-dependent code paths are mocked out. Time is controlled via freezegun.
    The DB session is modified in-place (use a copy of the production DB).
    """
    os.environ["TESTING"] = "1"
    random.seed(seed)

    # Suppress noisy logging from mocked LLM calls
    logging.getLogger("app.services.sentence_selector").setLevel(logging.ERROR)
    logging.getLogger("app.services.sentence_generator").setLevel(logging.ERROR)
    logging.getLogger("app.services.material_generator").setLevel(logging.ERROR)

    if start_date is None:
        start_date = datetime(2026, 3, 1, tzinfo=timezone.utc)

    snapshots: list[DaySnapshot] = []

    for day in range(1, days + 1):
        day_start = start_date + timedelta(days=day - 1)
        snap = DaySnapshot(day=day, date=day_start.strftime("%Y-%m-%d"))

        if not profile.should_study_today(day_start.weekday(), day):
            snap.skipped = True
            _fill_state_counts(db, snap)
            snapshots.append(snap)
            continue

        pre_learning = _count_state(db, "learning")
        pre_suspended = _count_state(db, "suspended")
        pre_acquiring = _count_state(db, "acquiring")

        num_sessions = profile.sessions_today()
        snap.num_sessions = num_sessions
        session_times = _session_times(num_sessions, day_start)

        from app.services.sentence_generator import GenerationError

        for session_idx, session_time in enumerate(session_times):
            session_limit = profile.session_size()
            snap.session_limit += session_limit

            with (
                freeze_time(session_time),
                patch(
                    "app.services.material_generator.generate_material_for_word",
                    return_value=None,
                ),
                patch(
                    "app.services.sentence_generator.generate_validated_sentence",
                    side_effect=GenerationError("Simulation mode — no LLM"),
                ),
            ):
                from app.services.leech_service import check_leech_reintroductions
                from app.services.sentence_selector import build_session

                # Only check leech reintroductions on first session of the day
                if session_idx == 0:
                    reintro = check_leech_reintroductions(db)
                    snap.leeches_reintroduced = len(reintro)

                session = build_session(
                    db, limit=session_limit, mode="reading", log_events=False
                )

            # Track due/covered from the last session of the day (most representative)
            snap.total_due = session["total_due_words"]
            snap.covered_due = session["covered_due_words"]
            snap.items_received += len(session["items"])

            # Review each item with advancing time within this session
            for i, item in enumerate(session["items"]):
                review_time = session_time + timedelta(minutes=1 + i * 1.5)

                words = item.get("words", [])
                comprehension = profile.decide_comprehension(words)
                missed, confused = profile.decide_missed_words(words, comprehension)

                if comprehension == "understood":
                    snap.understood += 1
                elif comprehension == "partial":
                    snap.partial += 1
                else:
                    snap.no_idea += 1

                with freeze_time(review_time):
                    from app.services.sentence_review_service import (
                        submit_sentence_review,
                    )

                    submit_sentence_review(
                        db,
                        sentence_id=item.get("sentence_id"),
                        primary_lemma_id=item["primary_lemma_id"],
                        comprehension_signal=comprehension,
                        missed_lemma_ids=missed,
                        confused_lemma_ids=confused,
                        session_id=session["session_id"],
                        review_mode="reading",
                    )
                    snap.reviews_submitted += 1

        # End-of-day snapshot
        _fill_state_counts(db, snap)
        post_learning = _count_state(db, "learning")
        snap.graduated_today = max(0, post_learning - pre_learning)
        snap.leeches_detected = max(0, _count_state(db, "suspended") - pre_suspended)
        post_acquiring = _count_state(db, "acquiring")
        snap.auto_introduced = max(0, post_acquiring - pre_acquiring + snap.graduated_today)

        from app.services.cohort_service import get_focus_cohort

        snap.cohort_size = len(get_focus_cohort(db))

        snapshots.append(snap)

        if day % 10 == 0 or day == days:
            logger.info(
                f"Day {day}: {snap.reviews_submitted} reviews across {snap.num_sessions} sessions, "
                f"{snap.acquiring} acquiring, {snap.known} known"
            )

    return snapshots
