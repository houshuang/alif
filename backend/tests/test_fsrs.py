from fsrs import Card

from app.models import Lemma, ReviewLog, UserLemmaKnowledge
from app.services.fsrs_service import (
    FSRS_LIBRARY_VERSION,
    FSRS_PARAMETERS_SHA256,
    FSRS_SCHEDULER_POLICY_VERSION,
    create_new_card,
    scheduler,
    submit_review,
)


def test_create_new_card():
    card_data = create_new_card()
    assert "due" in card_data
    assert "stability" in card_data or card_data.get("stability") is None
    assert "state" in card_data


def test_card_roundtrip():
    card = Card()
    data = card.to_dict()
    restored = Card.from_dict(data)
    assert restored.state == card.state
    assert restored.due == card.due


def test_submit_review_good(db_session):
    lemma = Lemma(lemma_ar="بَيْت", lemma_ar_bare="بيت", gloss_en="house")
    db_session.add(lemma)
    db_session.flush()

    knowledge = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="learning",
        fsrs_card_json=create_new_card(),
        source="duolingo",
        times_seen=0,
        times_correct=0,
    )
    db_session.add(knowledge)
    db_session.commit()

    result = submit_review(db_session, lemma.lemma_id, rating_int=3, response_ms=2000)
    assert result["lemma_id"] == lemma.lemma_id
    assert "next_due" in result
    assert result["new_state"] in ("new", "learning", "known", "lapsed")

    db_session.refresh(knowledge)
    assert knowledge.times_seen == 1
    assert knowledge.times_correct == 1
    log = db_session.query(ReviewLog).filter_by(lemma_id=lemma.lemma_id).one()
    assert (
        log.fsrs_log_json["fsrs_scheduler_policy_version"]
        == FSRS_SCHEDULER_POLICY_VERSION
    )
    assert log.fsrs_log_json["fsrs_library_version"] == FSRS_LIBRARY_VERSION
    assert (
        log.fsrs_log_json["fsrs_desired_retention"]
        == scheduler.desired_retention
    )
    assert log.fsrs_log_json["fsrs_parameters_sha256"] == FSRS_PARAMETERS_SHA256


def test_submit_review_again(db_session):
    lemma = Lemma(lemma_ar="صَعْب", lemma_ar_bare="صعب", gloss_en="difficult")
    db_session.add(lemma)
    db_session.flush()

    knowledge = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="learning",
        fsrs_card_json=create_new_card(),
        source="duolingo",
        times_seen=0,
        times_correct=0,
    )
    db_session.add(knowledge)
    db_session.commit()

    result = submit_review(db_session, lemma.lemma_id, rating_int=1)
    assert result["lemma_id"] == lemma.lemma_id

    db_session.refresh(knowledge)
    assert knowledge.times_seen == 1
    assert knowledge.times_correct == 0
