from datetime import datetime, timedelta, timezone

from app.models import Lemma, ReviewLog, UserLemmaKnowledge
from app.services.acquisition_service import (
    BOX_INTERVALS,
    GRADUATION_MIN_ACCURACY,
    GRADUATION_MIN_REVIEWS,
    get_acquisition_due,
    get_acquisition_stats,
    start_acquisition,
    submit_acquisition_review,
)


def _create_lemma(db, arabic="كتاب", english="book"):
    lemma = Lemma(lemma_ar=arabic, lemma_ar_bare=arabic, gloss_en=english, pos="noun")
    db.add(lemma)
    db.flush()
    return lemma


# --- start_acquisition ---


def test_start_acquisition_new_word(db_session):
    lemma = _create_lemma(db_session)
    ulk = start_acquisition(db_session, lemma.lemma_id, source="study")

    assert ulk.knowledge_state == "acquiring"
    assert ulk.acquisition_box == 1
    assert ulk.acquisition_next_due is not None
    assert ulk.introduced_at is not None
    assert ulk.acquisition_started_at is not None
    assert ulk.source == "study"
    assert ulk.fsrs_card_json is None
    assert ulk.times_seen == 0
    assert ulk.times_correct == 0


def test_start_acquisition_from_encountered(db_session):
    lemma = _create_lemma(db_session)
    ulk = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="encountered",
        source="encountered",
        times_seen=0,
        times_correct=0,
        total_encounters=5,
    )
    db_session.add(ulk)
    db_session.flush()

    result = start_acquisition(db_session, lemma.lemma_id, source="study")

    assert result.knowledge_state == "acquiring"
    assert result.acquisition_box == 1
    assert result.source == "study"
    assert result.fsrs_card_json is None


def test_start_acquisition_sets_correct_due_time(db_session):
    lemma = _create_lemma(db_session)
    before = datetime.now(timezone.utc)
    ulk = start_acquisition(db_session, lemma.lemma_id)
    after = datetime.now(timezone.utc)

    expected_min = before + BOX_INTERVALS[1]
    expected_max = after + BOX_INTERVALS[1]
    assert expected_min <= ulk.acquisition_next_due <= expected_max


# --- submit_acquisition_review: box advancement ---


def test_box_advancement(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    # Box 1 -> 2 with rating 3 (Good)
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    assert result["acquisition_box"] == 2
    assert result["new_state"] == "acquiring"

    # Box 2 -> 3 with rating 3
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    assert result["acquisition_box"] == 3
    assert result["new_state"] == "acquiring"


def test_box_advancement_with_easy_rating(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    # Rating 4 (Easy) should also advance
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=4)
    assert result["acquisition_box"] == 2


def test_box_reset_on_again(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    # Advance to box 2
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)

    # Rating 1 (Again) resets to box 1
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=1)
    assert result["acquisition_box"] == 1
    assert result["new_state"] == "acquiring"


def test_box_reset_from_box_3(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    # Advance to box 3
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.acquisition_box == 3

    # Again from box 3 drops to box 1
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=1)
    assert result["acquisition_box"] == 1


def test_hard_stays_in_box(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    # Rating 2 (Hard) in box 1 stays in box 1
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=2)
    assert result["acquisition_box"] == 1
    assert result["new_state"] == "acquiring"


def test_hard_stays_in_box_2(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    # Advance to box 2
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)

    # Hard stays in box 2
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=2)
    assert result["acquisition_box"] == 2


# --- submit_acquisition_review: graduation ---


def test_graduation(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    # Advance to box 3: two Good reviews (times_seen=2, times_correct=2)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.acquisition_box == 3

    # Need GRADUATION_MIN_REVIEWS total. Already have 2, need 3 more Good reviews.
    for _ in range(GRADUATION_MIN_REVIEWS - 2):
        submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)

    # That last review should have triggered graduation
    db_session.refresh(ulk)
    assert ulk.knowledge_state == "learning"
    assert ulk.acquisition_box is None
    assert ulk.acquisition_next_due is None
    assert ulk.graduated_at is not None
    assert ulk.fsrs_card_json is not None


def test_graduation_returns_graduated_flag(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    # Get to box 3 and accumulate enough reviews
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    for _ in range(GRADUATION_MIN_REVIEWS - 3):
        submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)

    # The graduating review
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    assert result["graduated"] is True
    assert result["new_state"] == "learning"


def test_no_graduation_low_accuracy(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    # Advance to box 3
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # box 1->2
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # box 2->3

    # Fail a bunch to tank accuracy, resetting to box 1 each time
    for _ in range(6):
        submit_acquisition_review(db_session, lemma.lemma_id, rating_int=1)  # reset to box 1

    # Now climb back to box 3
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # box 1->2
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # box 2->3

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    # times_seen >= GRADUATION_MIN_REVIEWS but accuracy = 4/10 = 40% which is < 60%
    assert ulk.times_seen >= GRADUATION_MIN_REVIEWS
    accuracy = ulk.times_correct / ulk.times_seen
    assert accuracy < GRADUATION_MIN_ACCURACY

    # Try a Good review from box 3 - should NOT graduate
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    assert result["new_state"] == "acquiring"
    assert result.get("graduated") is not True


def test_no_graduation_few_reviews(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    # Advance to box 3 with only 2 reviews (less than GRADUATION_MIN_REVIEWS=5)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # box 1->2
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # box 2->3

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.times_seen == 2
    assert ulk.acquisition_box == 3

    # Good review from box 3 but not enough total reviews
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    assert result["new_state"] == "acquiring"
    assert ulk.acquisition_box == 3  # stays in box 3


# --- submit_acquisition_review: review counting ---


def test_review_increments_times_seen(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.times_seen == 1
    assert ulk.times_correct == 1

    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=1)
    db_session.refresh(ulk)
    assert ulk.times_seen == 2
    assert ulk.times_correct == 1  # Again doesn't increment times_correct


def test_review_creates_review_log(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    submit_acquisition_review(
        db_session, lemma.lemma_id, rating_int=3,
        response_ms=1500, session_id="sess-1", review_mode="reading",
    )

    logs = db_session.query(ReviewLog).filter_by(lemma_id=lemma.lemma_id).all()
    assert len(logs) == 1
    assert logs[0].rating == 3
    assert logs[0].response_ms == 1500
    assert logs[0].session_id == "sess-1"
    assert logs[0].is_acquisition is True
    assert logs[0].fsrs_log_json is not None
    assert logs[0].fsrs_log_json["acquisition_box_before"] == 1
    assert logs[0].fsrs_log_json["acquisition_box_after"] == 2


def test_review_updates_total_encounters(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=2)

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.total_encounters == 2


# --- submit_acquisition_review: deduplication ---


def test_duplicate_client_review_id(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    result1 = submit_acquisition_review(
        db_session, lemma.lemma_id, rating_int=3, client_review_id="dup-1"
    )
    assert result1["acquisition_box"] == 2

    # Submit the same client_review_id again
    result2 = submit_acquisition_review(
        db_session, lemma.lemma_id, rating_int=1, client_review_id="dup-1"
    )
    assert result2["duplicate"] is True

    # State should NOT have changed from the duplicate
    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.acquisition_box == 2  # still box 2 from first review

    # Only one ReviewLog entry
    logs = db_session.query(ReviewLog).filter_by(lemma_id=lemma.lemma_id).all()
    assert len(logs) == 1


# --- submit_acquisition_review: non-acquiring word ---


def test_submit_review_for_non_acquiring_word_falls_back(db_session):
    from app.services.fsrs_service import create_new_card

    lemma = _create_lemma(db_session)
    ulk = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="learning",
        fsrs_card_json=create_new_card(),
        source="study",
        times_seen=0,
        times_correct=0,
    )
    db_session.add(ulk)
    db_session.commit()

    # Should fall back to normal FSRS review
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    assert result["lemma_id"] == lemma.lemma_id
    assert "new_state" in result


# --- get_acquisition_due ---


def test_get_acquisition_due(db_session):
    lemma1 = _create_lemma(db_session, arabic="كتب", english="wrote")
    lemma2 = _create_lemma(db_session, arabic="قرأ", english="read")
    lemma3 = _create_lemma(db_session, arabic="ذهب", english="went")

    now = datetime.now(timezone.utc)

    # Due word (past due)
    ulk1 = UserLemmaKnowledge(
        lemma_id=lemma1.lemma_id,
        knowledge_state="acquiring",
        acquisition_box=1,
        acquisition_next_due=now - timedelta(hours=1),
        times_seen=0, times_correct=0,
    )
    # Not yet due (future)
    ulk2 = UserLemmaKnowledge(
        lemma_id=lemma2.lemma_id,
        knowledge_state="acquiring",
        acquisition_box=2,
        acquisition_next_due=now + timedelta(hours=5),
        times_seen=0, times_correct=0,
    )
    # Not acquiring
    ulk3 = UserLemmaKnowledge(
        lemma_id=lemma3.lemma_id,
        knowledge_state="learning",
        acquisition_box=None,
        times_seen=5, times_correct=3,
    )

    db_session.add_all([ulk1, ulk2, ulk3])
    db_session.commit()

    due = get_acquisition_due(db_session, now=now)
    assert lemma1.lemma_id in due
    assert lemma2.lemma_id not in due
    assert lemma3.lemma_id not in due


def test_get_acquisition_due_empty(db_session):
    due = get_acquisition_due(db_session)
    assert due == []


def test_get_acquisition_due_exactly_at_due_time(db_session):
    lemma = _create_lemma(db_session)
    now = datetime.now(timezone.utc)

    ulk = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="acquiring",
        acquisition_box=1,
        acquisition_next_due=now,  # exactly now
        times_seen=0, times_correct=0,
    )
    db_session.add(ulk)
    db_session.commit()

    due = get_acquisition_due(db_session, now=now)
    assert lemma.lemma_id in due


# --- get_acquisition_stats ---


def test_get_acquisition_stats(db_session):
    now = datetime.now(timezone.utc)

    lemmas = [
        _create_lemma(db_session, arabic=f"word{i}", english=f"word{i}")
        for i in range(5)
    ]

    # 2 in box 1 (1 due, 1 not due)
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemmas[0].lemma_id, knowledge_state="acquiring",
        acquisition_box=1, acquisition_next_due=now - timedelta(hours=1),
        times_seen=0, times_correct=0,
    ))
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemmas[1].lemma_id, knowledge_state="acquiring",
        acquisition_box=1, acquisition_next_due=now + timedelta(hours=3),
        times_seen=0, times_correct=0,
    ))
    # 1 in box 2 (due)
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemmas[2].lemma_id, knowledge_state="acquiring",
        acquisition_box=2, acquisition_next_due=now - timedelta(minutes=30),
        times_seen=1, times_correct=1,
    ))
    # 1 in box 3 (not due)
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemmas[3].lemma_id, knowledge_state="acquiring",
        acquisition_box=3, acquisition_next_due=now + timedelta(days=2),
        times_seen=3, times_correct=3,
    ))
    # 1 not acquiring
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemmas[4].lemma_id, knowledge_state="learning",
        times_seen=5, times_correct=4,
    ))
    db_session.commit()

    stats = get_acquisition_stats(db_session)
    assert stats["total_acquiring"] == 4
    assert stats["box_1"] == 2
    assert stats["box_2"] == 1
    assert stats["box_3"] == 1
    assert stats["due_now"] == 2  # lemmas[0] and lemmas[2]


def test_get_acquisition_stats_empty(db_session):
    stats = get_acquisition_stats(db_session)
    assert stats["total_acquiring"] == 0
    assert stats["box_1"] == 0
    assert stats["box_2"] == 0
    assert stats["box_3"] == 0
    assert stats["due_now"] == 0


# --- Interval correctness ---


def _strip_tz(dt):
    """Strip timezone for comparison (SQLite stores naive datetimes)."""
    if dt and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


def test_box_intervals_are_correct(db_session):
    lemma = _create_lemma(db_session)
    before = _strip_tz(datetime.now(timezone.utc))
    start_acquisition(db_session, lemma.lemma_id)
    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()

    # Box 1 interval: 4 hours
    due = _strip_tz(ulk.acquisition_next_due)
    assert due >= before + timedelta(hours=4) - timedelta(seconds=5)
    assert due <= before + timedelta(hours=4) + timedelta(seconds=5)

    # Advance to box 2 and check interval: 1 day
    before = _strip_tz(datetime.now(timezone.utc))
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    db_session.refresh(ulk)
    due = _strip_tz(ulk.acquisition_next_due)
    assert due >= before + timedelta(days=1) - timedelta(seconds=5)
    assert due <= before + timedelta(days=1) + timedelta(seconds=5)

    # Advance to box 3 and check interval: 3 days
    before = _strip_tz(datetime.now(timezone.utc))
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    db_session.refresh(ulk)
    due = _strip_tz(ulk.acquisition_next_due)
    assert due >= before + timedelta(days=3) - timedelta(seconds=5)
    assert due <= before + timedelta(days=3) + timedelta(seconds=5)
