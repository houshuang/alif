from datetime import datetime, timedelta, timezone

from app.models import Lemma, ReviewLog, Root, UserLemmaKnowledge
from app.services.acquisition_service import (
    BOX_INTERVALS,
    FAST_GRAD_INTRO_GAP,
    GRADUATION_MIN_ACCURACY,
    GRADUATION_MIN_CALENDAR_DAYS,
    GRADUATION_MIN_REVIEWS,
    ROOT_SIBLING_THRESHOLD,
    _count_known_root_siblings,
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


def _make_due(db, lemma_id):
    """Set a word's acquisition_next_due to the past so it's considered due."""
    ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma_id).first()
    if ulk:
        ulk.acquisition_next_due = datetime.now(timezone.utc) - timedelta(hours=1)
        db.flush()
    return ulk


def _add_review_on_date(db, lemma_id, date_val):
    """Add a review log entry on a specific date for calendar-day checks."""
    db.add(ReviewLog(
        lemma_id=lemma_id,
        rating=3,
        reviewed_at=datetime(date_val.year, date_val.month, date_val.day, 12, 0, tzinfo=timezone.utc),
        is_acquisition=True,
    ))
    db.flush()


def _bypass_tier0(db, lemma_id):
    """Submit a Hard first review so tier-0 (first-correct instant grad) doesn't fire.

    After: times_seen=1, times_correct=0, box=1.
    """
    submit_acquisition_review(db, lemma_id, rating_int=2)


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


# --- submit_acquisition_review: tiered graduation ---


def test_tier0_first_correct_graduates(db_session):
    """First correct review (times_seen=0) → instant graduation."""
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    assert result["graduated"] is True
    assert result["new_state"] == "learning"

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.knowledge_state == "learning"
    assert ulk.acquisition_box is None
    assert ulk.fsrs_card_json is not None


def test_tier0_blocked_when_intro_just_shown(db_session):
    """First correct review within FAST_GRAD_INTRO_GAP of intro card stays in box 1.

    Working memory after seeing the intro card should not bypass the encoding phase.
    """
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    ulk.experiment_intro_shown_at = datetime.now(timezone.utc) - timedelta(seconds=20)
    db_session.flush()

    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    assert result.get("graduated") is not True
    assert result["new_state"] == "acquiring"

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.knowledge_state == "acquiring"
    assert ulk.acquisition_box == 2  # advanced box 1→2 (encoding handoff)
    assert ulk.fsrs_card_json is None


def test_tier0_allowed_when_intro_was_long_ago(db_session):
    """First correct review well past FAST_GRAD_INTRO_GAP still fast-grads."""
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    ulk.experiment_intro_shown_at = (
        datetime.now(timezone.utc) - FAST_GRAD_INTRO_GAP - timedelta(minutes=5)
    )
    db_session.flush()

    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    assert result.get("graduated") is True
    assert result["new_state"] == "learning"


def test_tier0_allowed_when_no_intro_shown(db_session):
    """First correct review still fast-grads when no intro was shown (e.g. textbook word)."""
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.experiment_intro_shown_at is None  # no intro card was shown

    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    assert result.get("graduated") is True


def test_tier0_first_hard_does_not_graduate(db_session):
    """Hard rating on first review does NOT trigger tier-0."""
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=2)
    assert result["new_state"] == "acquiring"
    assert result.get("graduated") is not True


def test_tier0_first_again_does_not_graduate(db_session):
    """Again rating on first review does NOT trigger tier-0."""
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=1)
    assert result["new_state"] == "acquiring"
    assert result.get("graduated") is not True


def test_tier1_perfect_accuracy_graduates(db_session):
    """100% accuracy + 3 reviews → graduate from any box."""
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    # Simulate prior reviews (e.g., leech re-intro with preserved stats)
    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    ulk.times_seen = 2
    ulk.times_correct = 2
    db_session.flush()

    # Next correct review: ts=3, tc=3, acc=100% → tier 1 fires
    _make_due(db_session, lemma.lemma_id)
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    assert result["graduated"] is True
    assert result["new_state"] == "learning"


def test_tier2_high_accuracy_graduates(db_session):
    """≥80% accuracy + 4 reviews + box ≥ 2 → graduate."""
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    # Set up: box 2, 4 reviews, 3 correct (75% before this review)
    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    ulk.times_seen = 4
    ulk.times_correct = 3
    ulk.acquisition_box = 2
    db_session.flush()

    # Next correct: ts=5, tc=4, acc=80%, box=2 → tier 2 fires
    _make_due(db_session, lemma.lemma_id)
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    assert result["graduated"] is True
    assert result["new_state"] == "learning"


def test_tier2_blocked_by_low_accuracy(db_session):
    """Tier 2 requires ≥80% accuracy. Word with 60% at box 2 stays acquiring."""
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    ulk.times_seen = 4
    ulk.times_correct = 2  # 50% before this review
    ulk.acquisition_box = 2
    db_session.flush()

    # ts=5, tc=3, acc=60%, box=2 → tier 1: no (60%≠100%), tier 2: no (60%<80%)
    _make_due(db_session, lemma.lemma_id)
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    assert result["new_state"] == "acquiring"
    assert result.get("graduated") is not True


# --- submit_acquisition_review: box advancement ---
# (These tests bypass tier-0 with a Hard first review)


def test_box_advancement(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)
    _bypass_tier0(db_session, lemma.lemma_id)

    # Box 1 -> 2 with rating 3 (Good) — always allowed within session
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    assert result["acquisition_box"] == 2
    assert result["new_state"] == "acquiring"

    # Box 2 -> 3: must be due first (simulate time passing)
    _make_due(db_session, lemma.lemma_id)
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    assert result["acquisition_box"] == 3
    assert result["new_state"] == "acquiring"


def test_box2_no_advance_when_not_due(db_session):
    """Box 2→3 is blocked when word is not due (within-session review)."""
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)
    _bypass_tier0(db_session, lemma.lemma_id)

    # Box 1 -> 2
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    # Review again immediately (not due yet) — should NOT advance to box 3
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    assert result["acquisition_box"] == 2  # stays in box 2

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.times_seen == 3  # Hard + Good + Good


def test_box_advancement_with_easy_rating(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)
    _bypass_tier0(db_session, lemma.lemma_id)

    # Rating 4 (Easy) should also advance
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=4)
    assert result["acquisition_box"] == 2


def test_box_reset_on_again(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)
    _bypass_tier0(db_session, lemma.lemma_id)

    # Advance to box 2
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)

    # Rating 1 (Again) resets to box 1
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=1)
    assert result["acquisition_box"] == 1
    assert result["new_state"] == "acquiring"


def test_box_reset_from_box_3(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)
    _bypass_tier0(db_session, lemma.lemma_id)

    # Advance to box 3 (with proper due dates)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # box 1->2
    _make_due(db_session, lemma.lemma_id)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # box 2->3
    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.acquisition_box == 3

    # Again from box 3 drops to box 1 (failure always resets, regardless of due)
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=1)
    assert result["acquisition_box"] == 1


def test_hard_stays_in_box(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    # Rating 2 (Hard) in box 1 stays in box 1 (also bypasses tier-0)
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=2)
    assert result["acquisition_box"] == 1
    assert result["new_state"] == "acquiring"


def test_hard_stays_in_box_2(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)
    _bypass_tier0(db_session, lemma.lemma_id)

    # Advance to box 2
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)

    # Hard stays in box 2
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=2)
    assert result["acquisition_box"] == 2


# --- submit_acquisition_review: tier 3 (standard) graduation ---


def test_graduation_tier3(db_session):
    """Standard graduation: box ≥ 3, 5+ reviews, ≥60% accuracy, 2 calendar days."""
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    # Hard + Again to keep accuracy below 80% (blocks tier 1/2)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=2)  # ts=1, tc=0
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=1)  # ts=2, tc=0, box→1

    # Advance to box 3
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # box 1→2, ts=3, tc=1 (33%)
    _make_due(db_session, lemma.lemma_id)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # box 2→3, ts=4, tc=2 (50%)

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.acquisition_box == 3

    # Add review history spanning 2 calendar days
    from datetime import date
    today = date.today()
    yesterday = today - timedelta(days=1)
    _add_review_on_date(db_session, lemma.lemma_id, yesterday)
    _add_review_on_date(db_session, lemma.lemma_id, today)

    # Need GRADUATION_MIN_REVIEWS=5. Already have 4, need 1 more.
    _make_due(db_session, lemma.lemma_id)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # ts=5, tc=3, acc=60%

    db_session.refresh(ulk)
    assert ulk.knowledge_state == "learning"
    assert ulk.acquisition_box is None
    assert ulk.acquisition_next_due is None
    assert ulk.graduated_at is not None
    assert ulk.fsrs_card_json is not None


def test_graduation_returns_graduated_flag(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    # Hard + Again for low accuracy path
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=2)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=1)

    # Advance to box 3
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # box 1→2
    _make_due(db_session, lemma.lemma_id)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # box 2→3

    # Add calendar day spread
    from datetime import date
    _add_review_on_date(db_session, lemma.lemma_id, date.today() - timedelta(days=1))

    # The graduating review (must be due)
    _make_due(db_session, lemma.lemma_id)
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # ts=5, tc=3, acc=60%
    assert result["graduated"] is True
    assert result["new_state"] == "learning"


def test_no_graduation_single_calendar_day(db_session):
    """Tier 3 needs 2+ calendar days; tiers 1/2 blocked by low accuracy."""
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    # Hard + Again for low accuracy (blocks tier 1/2)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=2)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=1)

    # Advance to box 3
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # box 1→2
    _make_due(db_session, lemma.lemma_id)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # box 2→3

    # One more to reach GRADUATION_MIN_REVIEWS
    _make_due(db_session, lemma.lemma_id)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # ts=5, tc=3, acc=60%

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    # All reviews same calendar day, accuracy=60% blocks tier 1/2 → should NOT graduate
    assert ulk.knowledge_state == "acquiring"
    assert ulk.acquisition_box == 3


def test_no_graduation_low_accuracy(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)
    _bypass_tier0(db_session, lemma.lemma_id)

    # Advance to box 2
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # box 1->2

    # Fail a bunch to tank accuracy, resetting to box 1 each time
    for _ in range(6):
        submit_acquisition_review(db_session, lemma.lemma_id, rating_int=1)  # reset to box 1

    # Now climb back to box 3
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # box 1->2
    _make_due(db_session, lemma.lemma_id)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # box 2->3

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.times_seen >= GRADUATION_MIN_REVIEWS
    accuracy = ulk.times_correct / ulk.times_seen
    assert accuracy < GRADUATION_MIN_ACCURACY

    # Good review from box 3 (due) — should NOT graduate due to low accuracy
    _make_due(db_session, lemma.lemma_id)
    result = submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    assert result["new_state"] == "acquiring"
    assert result.get("graduated") is not True


def test_no_graduation_few_reviews(db_session):
    """Two reviews aren't enough for any graduation tier."""
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)
    _bypass_tier0(db_session, lemma.lemma_id)

    # Good to box 2 (must be due for graduation check)
    _make_due(db_session, lemma.lemma_id)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)  # ts=2, tc=1, box=2

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.times_seen == 2
    assert ulk.acquisition_box == 2
    # Tier 1: 50% ≠ 100%. Tier 2: ts=2 < 4. Tier 3: box=2 < 3.
    assert ulk.knowledge_state == "acquiring"


# --- submit_acquisition_review: review counting ---


def test_review_increments_times_seen(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    # First correct review triggers tier-0 graduation, but still increments counters
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.times_seen == 1
    assert ulk.times_correct == 1


def test_review_creates_review_log(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)
    _bypass_tier0(db_session, lemma.lemma_id)

    submit_acquisition_review(
        db_session, lemma.lemma_id, rating_int=3,
        response_ms=1500, session_id="sess-1", review_mode="reading",
    )

    logs = db_session.query(ReviewLog).filter_by(lemma_id=lemma.lemma_id).all()
    assert len(logs) == 2  # Hard + Good
    good_log = [log for log in logs if log.rating == 3][0]
    assert good_log.response_ms == 1500
    assert good_log.session_id == "sess-1"
    assert good_log.is_acquisition is True
    assert good_log.fsrs_log_json is not None
    assert good_log.fsrs_log_json["acquisition_box_before"] == 1
    assert good_log.fsrs_log_json["acquisition_box_after"] == 2


def test_review_updates_total_encounters(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)

    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=2)
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=2)

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.total_encounters == 2


# --- submit_acquisition_review: deduplication ---


def test_duplicate_client_review_id(db_session):
    lemma = _create_lemma(db_session)
    start_acquisition(db_session, lemma.lemma_id)
    _bypass_tier0(db_session, lemma.lemma_id)

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

    # Only two ReviewLog entries (Hard bypass + Good)
    logs = db_session.query(ReviewLog).filter_by(lemma_id=lemma.lemma_id).all()
    assert len(logs) == 2


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

    # Hard to bypass tier-0, then make due for next review
    _bypass_tier0(db_session, lemma.lemma_id)
    _make_due(db_session, lemma.lemma_id)

    # Advance to box 2 (box 1→2 always allowed) and check interval: 1 day
    before = _strip_tz(datetime.now(timezone.utc))
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    db_session.refresh(ulk)
    due = _strip_tz(ulk.acquisition_next_due)
    assert due >= before + timedelta(days=1) - timedelta(seconds=5)
    assert due <= before + timedelta(days=1) + timedelta(seconds=5)

    # Advance to box 3 (must be due first) and check interval: 3 days
    _make_due(db_session, lemma.lemma_id)
    before = _strip_tz(datetime.now(timezone.utc))
    submit_acquisition_review(db_session, lemma.lemma_id, rating_int=3)
    db_session.refresh(ulk)
    due = _strip_tz(ulk.acquisition_next_due)
    assert due >= before + timedelta(days=3) - timedelta(seconds=5)
    assert due <= before + timedelta(days=3) + timedelta(seconds=5)


# --- Root-aware stability boost tests ---


def _create_root_family(db, root_str="ك.ت.ب", words=None):
    """Create a root with multiple lemmas sharing it."""
    root = Root(root=root_str)
    db.add(root)
    db.flush()
    if words is None:
        words = [("كتاب", "book"), ("كاتب", "writer"), ("مكتبة", "library")]
    lemmas = []
    for ar, en in words:
        lemma = Lemma(lemma_ar=ar, lemma_ar_bare=ar, gloss_en=en, pos="noun", root_id=root.root_id)
        db.add(lemma)
        db.flush()
        lemmas.append(lemma)
    return root, lemmas


def test_count_known_root_siblings(db_session):
    """Count known siblings sharing the same root."""
    root, lemmas = _create_root_family(db_session)
    # Mark first two as known
    for lemma in lemmas[:2]:
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="known",
            times_seen=10,
            times_correct=9,
        )
        db_session.add(ulk)
    db_session.flush()

    # Third lemma should see 2 known siblings
    count = _count_known_root_siblings(db_session, lemmas[2].lemma_id)
    assert count == 2


def test_count_known_root_siblings_no_root(db_session):
    """Returns 0 for lemmas without a root."""
    lemma = _create_lemma(db_session, arabic="هو", english="he")
    assert _count_known_root_siblings(db_session, lemma.lemma_id) == 0


def _graduate_word(db_session, lemma_id):
    """Helper: graduate a word via tier-0 (first correct review → instant graduation)."""
    start_acquisition(db_session, lemma_id)
    submit_acquisition_review(db_session, lemma_id, rating_int=3)


def test_root_boost_graduation_easy_rating(db_session):
    """Words with 2+ known root siblings get Rating.Easy (higher initial stability)."""
    from fsrs import Scheduler, Card, Rating

    root, lemmas = _create_root_family(db_session)
    target_lemma = lemmas[2]  # "library"

    # Mark 2 siblings as known
    for lemma in lemmas[:2]:
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="known",
            times_seen=10,
            times_correct=9,
        )
        db_session.add(ulk)
    db_session.flush()

    # Graduate the target word (tier-0: first correct review)
    _graduate_word(db_session, target_lemma.lemma_id)

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=target_lemma.lemma_id).first()
    assert ulk.knowledge_state == "learning"

    # Easy rating produces higher initial stability than Good
    scheduler = Scheduler()
    now = datetime.now(timezone.utc)
    card_good, _ = scheduler.review_card(Card(), Rating.Good, now)
    card_easy, _ = scheduler.review_card(Card(), Rating.Easy, now)
    assert card_easy.stability > card_good.stability

    # Graduated card should have Easy-level stability
    import json
    fsrs_data = json.loads(ulk.fsrs_card_json) if isinstance(ulk.fsrs_card_json, str) else ulk.fsrs_card_json
    assert fsrs_data["stability"] >= card_easy.stability * 0.95


def test_no_root_boost_without_siblings(db_session):
    """Words without enough known siblings get normal Rating.Good."""
    from fsrs import Scheduler, Card, Rating

    root, lemmas = _create_root_family(db_session)
    target_lemma = lemmas[2]

    # Only 1 sibling known (below threshold of 2)
    ulk_sibling = UserLemmaKnowledge(
        lemma_id=lemmas[0].lemma_id,
        knowledge_state="known",
        times_seen=10,
        times_correct=9,
    )
    db_session.add(ulk_sibling)
    db_session.flush()

    _graduate_word(db_session, target_lemma.lemma_id)

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=target_lemma.lemma_id).first()
    assert ulk.knowledge_state == "learning"

    scheduler = Scheduler()
    card_good, _ = scheduler.review_card(Card(), Rating.Good, datetime.now(timezone.utc))

    import json
    fsrs_data = json.loads(ulk.fsrs_card_json) if isinstance(ulk.fsrs_card_json, str) else ulk.fsrs_card_json
    assert fsrs_data["stability"] >= card_good.stability * 0.95
    assert fsrs_data["stability"] <= card_good.stability * 1.05
