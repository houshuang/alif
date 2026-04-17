"""Tests for per-lemma generation backoff.

Chronically-failing words get a 7-day skip after 3 consecutive zero-result
generation attempts, so the 3h cron stops wasting LLM calls on them.
"""
from datetime import datetime, timedelta

from app.models import Lemma, UserLemmaKnowledge
from app.services.material_generator import (
    GENERATION_BACKOFF_DURATION,
    GENERATION_BACKOFF_THRESHOLD,
    lemmas_on_backoff,
    record_generation_result,
)


def _make_lemma_with_knowledge(db, lemma_ar: str, state: str = "known") -> Lemma:
    lemma = Lemma(lemma_ar=lemma_ar, lemma_ar_bare=lemma_ar, gloss_en="x")
    db.add(lemma)
    db.flush()
    ulk = UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state=state)
    db.add(ulk)
    db.commit()
    return lemma


def test_single_failure_increments_but_does_not_back_off(db_session):
    lemma = _make_lemma_with_knowledge(db_session, "مَبْسُوطَة")

    record_generation_result(db_session, lemma.lemma_id, 0)
    db_session.expire_all()

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.generation_failed_count == 1
    assert ulk.generation_backoff_until is None


def test_third_failure_sets_backoff(db_session):
    lemma = _make_lemma_with_knowledge(db_session, "طَيِّب")

    for _ in range(GENERATION_BACKOFF_THRESHOLD):
        record_generation_result(db_session, lemma.lemma_id, 0)
    db_session.expire_all()

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.generation_failed_count == GENERATION_BACKOFF_THRESHOLD
    assert ulk.generation_backoff_until is not None
    expected_floor = datetime.utcnow() + GENERATION_BACKOFF_DURATION - timedelta(minutes=1)
    assert ulk.generation_backoff_until >= expected_floor


def test_success_resets_failed_count_and_clears_backoff(db_session):
    lemma = _make_lemma_with_knowledge(db_session, "عَجِيب")

    # Drive into backoff
    for _ in range(GENERATION_BACKOFF_THRESHOLD):
        record_generation_result(db_session, lemma.lemma_id, 0)
    # A later successful run should reset both fields.
    record_generation_result(db_session, lemma.lemma_id, 2)
    db_session.expire_all()

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.generation_failed_count == 0
    assert ulk.generation_backoff_until is None


def test_record_ignores_lemma_without_knowledge(db_session):
    lemma = Lemma(lemma_ar="خَفِيف", lemma_ar_bare="خفيف", gloss_en="light")
    db_session.add(lemma)
    db_session.commit()

    # No ULK exists — should silently no-op, not raise.
    record_generation_result(db_session, lemma.lemma_id, 0)
    assert (
        db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
        is None
    )


def test_lemmas_on_backoff_filters_expired_entries(db_session):
    active_lemma = _make_lemma_with_knowledge(db_session, "صَعْب")
    expired_lemma = _make_lemma_with_knowledge(db_session, "سَهْل")
    untouched_lemma = _make_lemma_with_knowledge(db_session, "حُلْو")

    active_ulk = (
        db_session.query(UserLemmaKnowledge)
        .filter_by(lemma_id=active_lemma.lemma_id)
        .first()
    )
    active_ulk.generation_backoff_until = datetime.utcnow() + timedelta(days=3)

    expired_ulk = (
        db_session.query(UserLemmaKnowledge)
        .filter_by(lemma_id=expired_lemma.lemma_id)
        .first()
    )
    expired_ulk.generation_backoff_until = datetime.utcnow() - timedelta(hours=1)
    db_session.commit()

    result = lemmas_on_backoff(
        db_session,
        [active_lemma.lemma_id, expired_lemma.lemma_id, untouched_lemma.lemma_id],
    )
    assert result == {active_lemma.lemma_id}


def test_lemmas_on_backoff_empty_input(db_session):
    assert lemmas_on_backoff(db_session, []) == set()
