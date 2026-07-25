"""Rapid re-exposure re-test credit guard (2026-07-25).

A correct quiz-mode (bare-recall checkpoint / wrap-up) review within
RETEST_CREDIT_GAP of a rating-1 failure must not advance the acquisition box,
graduate the word, or count as a success in the leech sliding window. Misses
and out-of-window quiz reviews keep today's behavior.
"""

from datetime import datetime, timedelta, timezone

from app.models import Lemma, ReviewLog, UserLemmaKnowledge
from app.services.acquisition_service import (
    BOX_INTERVALS,
    RETEST_CREDIT_GAP,
    submit_acquisition_review,
)
from app.services.leech_service import _recent_accuracy


def _lemma(db, arabic="كلمة", gloss="word"):
    lemma = Lemma(
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        gloss_en=gloss,
        pos="noun",
    )
    db.add(lemma)
    db.flush()
    return lemma


def _acquiring(db, lemma, box=1, times_seen=0, times_correct=0):
    ulk = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="acquiring",
        acquisition_box=box,
        times_seen=times_seen,
        times_correct=times_correct,
        acquisition_started_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    db.add(ulk)
    db.flush()
    return ulk


def _log_failure(db, lemma_id, minutes_ago):
    db.add(ReviewLog(
        lemma_id=lemma_id,
        rating=1,
        reviewed_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        review_mode="reading",
        is_acquisition=True,
    ))
    db.flush()


def test_quiz_success_after_recent_failure_does_not_advance_box(db_session):
    lemma = _lemma(db_session)
    ulk = _acquiring(db_session, lemma, box=1, times_seen=3, times_correct=1)
    _log_failure(db_session, lemma.lemma_id, minutes_ago=5)

    result = submit_acquisition_review(
        db_session, lemma_id=lemma.lemma_id, rating_int=3,
        review_mode="quiz", comprehension_signal="understood", commit=False,
    )

    assert result["new_state"] == "acquiring"
    assert result["graduated"] is False
    assert ulk.acquisition_box == 1
    # Short retry due-date is cleared to the box interval
    due = ulk.acquisition_next_due
    if due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)
    assert due > datetime.now(timezone.utc) + BOX_INTERVALS[1] - timedelta(minutes=5)
    # Counter neutrality (conformance v2): the guarded success enters neither
    # the graduation numerator nor denominator — otherwise a later organic
    # review would see inflated (or unfairly deflated) cumulative accuracy.
    assert ulk.times_seen == 3
    assert ulk.times_correct == 1
    assert ulk.total_encounters == 1  # still recorded as an encounter


def test_reading_success_after_recent_failure_still_advances(db_session):
    """Control-arm behavior unchanged: an organic sentence review minutes
    after a failure advances Box 1→2 exactly as before."""
    lemma = _lemma(db_session)
    ulk = _acquiring(db_session, lemma, box=1, times_seen=3, times_correct=1)
    _log_failure(db_session, lemma.lemma_id, minutes_ago=5)

    submit_acquisition_review(
        db_session, lemma_id=lemma.lemma_id, rating_int=3,
        review_mode="reading", comprehension_signal="understood", commit=False,
    )
    assert ulk.acquisition_box == 2


def test_quiz_success_outside_gap_advances(db_session):
    """Manual wrap-up long after a failure is a genuine spaced test."""
    lemma = _lemma(db_session)
    ulk = _acquiring(db_session, lemma, box=1, times_seen=3, times_correct=1)
    _log_failure(
        db_session, lemma.lemma_id,
        minutes_ago=int(RETEST_CREDIT_GAP.total_seconds() / 60) + 10,
    )

    submit_acquisition_review(
        db_session, lemma_id=lemma.lemma_id, rating_int=3,
        review_mode="quiz", comprehension_signal="understood", commit=False,
    )
    assert ulk.acquisition_box == 2


def test_quiz_success_blocks_first_correct_graduation(db_session):
    lemma = _lemma(db_session)
    ulk = _acquiring(db_session, lemma, box=1, times_seen=0, times_correct=0)
    _log_failure(db_session, lemma.lemma_id, minutes_ago=5)

    result = submit_acquisition_review(
        db_session, lemma_id=lemma.lemma_id, rating_int=3,
        review_mode="quiz", comprehension_signal="understood", commit=False,
    )
    assert result["graduated"] is False
    assert ulk.knowledge_state == "acquiring"


def test_quiz_miss_still_resets_box(db_session):
    """A failed bare recall is genuine evidence — resets to Box 1 as usual."""
    lemma = _lemma(db_session)
    ulk = _acquiring(db_session, lemma, box=2, times_seen=4, times_correct=3)
    _log_failure(db_session, lemma.lemma_id, minutes_ago=5)

    submit_acquisition_review(
        db_session, lemma_id=lemma.lemma_id, rating_int=1,
        review_mode="quiz", comprehension_signal="no_idea", commit=False,
    )
    assert ulk.acquisition_box == 1
    # Misses are genuine evidence and DO count in the lifetime counters
    assert ulk.times_seen == 5
    assert ulk.times_correct == 3


def test_leech_window_excludes_quiz_successes(db_session):
    """Quiz Got-its must not mask a leech; quiz misses still count."""
    lemma = _lemma(db_session)
    now = datetime.now(timezone.utc)
    # 5 reading failures + 4 quiz successes interleaved
    for i in range(5):
        db_session.add(ReviewLog(
            lemma_id=lemma.lemma_id, rating=1,
            reviewed_at=now - timedelta(hours=10 - i),
            review_mode="reading",
        ))
    for i in range(4):
        db_session.add(ReviewLog(
            lemma_id=lemma.lemma_id, rating=3,
            reviewed_at=now - timedelta(hours=5 - i),
            review_mode="quiz",
        ))
    db_session.flush()

    acc = _recent_accuracy(db_session, lemma.lemma_id)
    assert acc == 0.0  # the 4 quiz successes are invisible


def test_leech_window_counts_reading_successes(db_session):
    lemma = _lemma(db_session)
    now = datetime.now(timezone.utc)
    for i, rating in enumerate([1, 1, 3, 3, 3]):
        db_session.add(ReviewLog(
            lemma_id=lemma.lemma_id, rating=rating,
            reviewed_at=now - timedelta(hours=5 - i),
            review_mode="reading",
        ))
    db_session.flush()
    assert _recent_accuracy(db_session, lemma.lemma_id) == 0.6
