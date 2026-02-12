from datetime import datetime, timedelta, timezone

from app.models import Lemma, UserLemmaKnowledge
from app.services.leech_service import (
    LEECH_MAX_ACCURACY,
    LEECH_MIN_REVIEWS,
    REINTRO_DELAY,
    check_and_manage_leeches,
    check_leech_reintroductions,
    check_single_word_leech,
    is_leech,
)


def _create_lemma(db, arabic="كتاب", english="book"):
    lemma = Lemma(lemma_ar=arabic, lemma_ar_bare=arabic, gloss_en=english, pos="noun")
    db.add(lemma)
    db.flush()
    return lemma


# --- is_leech ---


def test_is_leech_true():
    ulk = UserLemmaKnowledge(
        times_seen=10,
        times_correct=3,  # 30% accuracy
    )
    assert is_leech(ulk) is True


def test_is_leech_boundary_accuracy():
    ulk = UserLemmaKnowledge(
        times_seen=10,
        times_correct=4,  # 40% accuracy — exactly at threshold
    )
    # LEECH_MAX_ACCURACY is 0.40, condition is < 0.40, so 40% is not a leech
    assert is_leech(ulk) is False


def test_is_leech_below_boundary_accuracy():
    ulk = UserLemmaKnowledge(
        times_seen=10,
        times_correct=3,  # 30% accuracy
    )
    assert is_leech(ulk) is True


def test_no_leech_when_accurate():
    ulk = UserLemmaKnowledge(
        times_seen=15,
        times_correct=10,  # 67% accuracy
    )
    assert is_leech(ulk) is False


def test_no_leech_few_reviews():
    ulk = UserLemmaKnowledge(
        times_seen=5,  # < LEECH_MIN_REVIEWS (8)
        times_correct=1,  # 20% accuracy would be leech if enough reviews
    )
    assert is_leech(ulk) is False


def test_no_leech_exactly_min_reviews_boundary():
    ulk = UserLemmaKnowledge(
        times_seen=7,  # one below LEECH_MIN_REVIEWS
        times_correct=0,  # 0% accuracy
    )
    assert is_leech(ulk) is False


def test_is_leech_exactly_at_min_reviews():
    ulk = UserLemmaKnowledge(
        times_seen=8,  # exactly LEECH_MIN_REVIEWS
        times_correct=2,  # 25% accuracy
    )
    assert is_leech(ulk) is True


# --- check_and_manage_leeches ---


def test_detect_leech(db_session):
    lemma = _create_lemma(db_session)
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="learning",
        times_seen=12,
        times_correct=3,  # 25% accuracy
    ))
    db_session.commit()

    suspended = check_and_manage_leeches(db_session)
    assert lemma.lemma_id in suspended

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.knowledge_state == "suspended"
    assert ulk.leech_suspended_at is not None
    assert ulk.acquisition_box is None
    assert ulk.acquisition_next_due is None


def test_no_leech_when_accurate_full(db_session):
    lemma = _create_lemma(db_session)
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="learning",
        times_seen=15,
        times_correct=12,  # 80% accuracy
    ))
    db_session.commit()

    suspended = check_and_manage_leeches(db_session)
    assert suspended == []

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.knowledge_state == "learning"


def test_no_leech_few_reviews_full(db_session):
    lemma = _create_lemma(db_session)
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="learning",
        times_seen=5,  # < 8
        times_correct=0,
    ))
    db_session.commit()

    suspended = check_and_manage_leeches(db_session)
    assert suspended == []


def test_detect_leech_from_acquiring_state(db_session):
    lemma = _create_lemma(db_session)
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="acquiring",
        acquisition_box=2,
        acquisition_next_due=datetime.now(timezone.utc) + timedelta(hours=1),
        times_seen=10,
        times_correct=2,  # 20% accuracy
    ))
    db_session.commit()

    suspended = check_and_manage_leeches(db_session)
    assert lemma.lemma_id in suspended

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.knowledge_state == "suspended"
    assert ulk.acquisition_box is None
    assert ulk.acquisition_next_due is None


def test_detect_leech_skips_already_suspended(db_session):
    lemma = _create_lemma(db_session)
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="suspended",
        times_seen=20,
        times_correct=1,
    ))
    db_session.commit()

    suspended = check_and_manage_leeches(db_session)
    assert suspended == []


def test_detect_multiple_leeches(db_session):
    lemmas = [
        _create_lemma(db_session, arabic=f"l{i}", english=f"l{i}")
        for i in range(3)
    ]
    # Two leeches, one good
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemmas[0].lemma_id, knowledge_state="learning",
        times_seen=10, times_correct=2,
    ))
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemmas[1].lemma_id, knowledge_state="known",
        times_seen=20, times_correct=15,
    ))
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemmas[2].lemma_id, knowledge_state="lapsed",
        times_seen=9, times_correct=1,
    ))
    db_session.commit()

    suspended = check_and_manage_leeches(db_session)
    assert lemmas[0].lemma_id in suspended
    assert lemmas[1].lemma_id not in suspended
    assert lemmas[2].lemma_id in suspended


# --- check_leech_reintroductions ---


def test_reintroduction_after_delay(db_session):
    lemma = _create_lemma(db_session)
    now = datetime.now(timezone.utc)

    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="suspended",
        leech_suspended_at=now - REINTRO_DELAY - timedelta(hours=1),
        times_seen=10,
        times_correct=2,
    ))
    db_session.commit()

    reintroduced = check_leech_reintroductions(db_session)
    assert lemma.lemma_id in reintroduced

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.knowledge_state == "acquiring"
    assert ulk.acquisition_box == 1
    assert ulk.leech_suspended_at is None
    assert ulk.times_seen == 0  # reset
    assert ulk.times_correct == 0  # reset
    assert ulk.source == "leech_reintro"


def test_no_reintroduction_too_soon(db_session):
    lemma = _create_lemma(db_session)
    now = datetime.now(timezone.utc)

    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="suspended",
        leech_suspended_at=now - timedelta(days=7),  # only 7 days, need 14
        times_seen=10,
        times_correct=2,
    ))
    db_session.commit()

    reintroduced = check_leech_reintroductions(db_session)
    assert reintroduced == []

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.knowledge_state == "suspended"


def test_no_reintroduction_manual_suspend(db_session):
    """Manually suspended words (no leech_suspended_at) should not be reintroduced."""
    lemma = _create_lemma(db_session)

    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="suspended",
        leech_suspended_at=None,  # not a leech suspension
        times_seen=5,
        times_correct=3,
    ))
    db_session.commit()

    reintroduced = check_leech_reintroductions(db_session)
    assert reintroduced == []


def test_reintroduction_exactly_at_delay(db_session):
    lemma = _create_lemma(db_session)
    now = datetime.now(timezone.utc)

    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="suspended",
        leech_suspended_at=now - REINTRO_DELAY,  # exactly 14 days
        times_seen=10,
        times_correct=2,
    ))
    db_session.commit()

    reintroduced = check_leech_reintroductions(db_session)
    assert lemma.lemma_id in reintroduced


# --- check_single_word_leech ---


def test_check_single_word_leech(db_session):
    lemma = _create_lemma(db_session)
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="learning",
        times_seen=10,
        times_correct=2,  # 20% accuracy — leech
    ))
    db_session.commit()

    result = check_single_word_leech(db_session, lemma.lemma_id)
    assert result is True

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.knowledge_state == "suspended"
    assert ulk.leech_suspended_at is not None


def test_check_single_word_not_leech(db_session):
    lemma = _create_lemma(db_session)
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="learning",
        times_seen=10,
        times_correct=8,  # 80% accuracy — not leech
    ))
    db_session.commit()

    result = check_single_word_leech(db_session, lemma.lemma_id)
    assert result is False

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.knowledge_state == "learning"


def test_check_single_word_already_suspended(db_session):
    lemma = _create_lemma(db_session)
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="suspended",
        times_seen=10,
        times_correct=2,
    ))
    db_session.commit()

    result = check_single_word_leech(db_session, lemma.lemma_id)
    assert result is False  # already suspended, no action


def test_check_single_word_nonexistent(db_session):
    result = check_single_word_leech(db_session, lemma_id=99999)
    assert result is False


def test_check_single_word_clears_acquisition_fields(db_session):
    lemma = _create_lemma(db_session)
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="acquiring",
        acquisition_box=2,
        acquisition_next_due=datetime.now(timezone.utc) + timedelta(hours=1),
        times_seen=10,
        times_correct=2,
    ))
    db_session.commit()

    result = check_single_word_leech(db_session, lemma.lemma_id)
    assert result is True

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.acquisition_box is None
    assert ulk.acquisition_next_due is None
