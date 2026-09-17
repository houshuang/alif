from datetime import datetime, timedelta, timezone

from fsrs import Card
from fsrs import State

from app.models import Lemma, ReviewLog, UserLemmaKnowledge
from app.services import fsrs_service
from app.services.fsrs_service import (
    FSRS_ASSISTED_LAPSE_DESIRED_RETENTION,
    FSRS_LIBRARY_VERSION,
    FSRS_PARAMETERS_SHA256,
    FSRS_SCHEDULER_POLICY_VERSION,
    LEGACY_FSRS_DESIRED_RETENTION,
    MAINTENANCE_FSRS_DESIRED_RETENTION,
    create_new_card,
    standard_scheduler,
    submit_review,
)
from app.services.learning_policy import LOW_ENERGY_MAINTENANCE_ENV


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
        == standard_scheduler().desired_retention
        == MAINTENANCE_FSRS_DESIRED_RETENTION
    )
    assert log.fsrs_log_json["fsrs_parameters_sha256"] == FSRS_PARAMETERS_SHA256
    assert log.fsrs_log_json["fsrs_policy"] == "standard_v2"
    assert log.fsrs_log_json["fsrs_assisted_lapse"] is False
    assert log.fsrs_log_json["fsrs_rating_applied"] == 3


def test_maintenance_switch_selects_standard_retention(monkeypatch):
    assert MAINTENANCE_FSRS_DESIRED_RETENTION == 0.90
    assert standard_scheduler().desired_retention == MAINTENANCE_FSRS_DESIRED_RETENTION
    monkeypatch.setenv(LOW_ENERGY_MAINTENANCE_ENV, "0")
    assert standard_scheduler().desired_retention == LEGACY_FSRS_DESIRED_RETENTION == 0.95
    # Both targets share one parameter set, so retrievability and the stamped
    # parameter hash do not depend on the switch.
    assert list(standard_scheduler().parameters) == list(
        fsrs_service.maintenance_scheduler.parameters
    )


def _mature_known_card(db, arabic, now):
    lemma = Lemma(lemma_ar=arabic, lemma_ar_bare=arabic, gloss_en="word")
    db.add(lemma)
    db.flush()
    card = Card(
        state=State.Review,
        stability=30.0,
        difficulty=5.0,
        due=now - timedelta(hours=1),
        last_review=now - timedelta(days=12),
    )
    db.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="known",
        fsrs_card_json=card.to_dict(),
        source="encountered",
        times_seen=8,
        times_correct=8,
    ))
    db.commit()
    return lemma


def test_maintenance_retention_spaces_successful_reviews_further(
    db_session, monkeypatch
):
    """2026-09-17: at 90% a mature card waits its stability; at 95% about 40% of it."""
    now = datetime.now(timezone.utc)
    maintenance = _mature_known_card(db_session, "بعيد", now)
    legacy = _mature_known_card(db_session, "قريب", now)

    maintenance_due = datetime.fromisoformat(
        submit_review(db_session, maintenance.lemma_id, rating_int=3)["next_due"]
    )
    monkeypatch.setenv(LOW_ENERGY_MAINTENANCE_ENV, "0")
    legacy_due = datetime.fromisoformat(
        submit_review(db_session, legacy.lemma_id, rating_int=3)["next_due"]
    )

    assert (maintenance_due - now) > 1.8 * (legacy_due - now)
    retention_stamps = {
        row.lemma_id: row.fsrs_log_json["fsrs_desired_retention"]
        for row in db_session.query(ReviewLog).all()
    }
    assert retention_stamps == {
        maintenance.lemma_id: MAINTENANCE_FSRS_DESIRED_RETENTION,
        legacy.lemma_id: LEGACY_FSRS_DESIRED_RETENTION,
    }


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


def test_rating2_is_logged_as_assisted_lapse_without_relearning_step(db_session):
    """Rating 2 failed pre-reveal recall even when recognition followed."""
    lemma = Lemma(
        lemma_ar="عَرَفَ",
        lemma_ar_bare="عرف",
        gloss_en="to recognize",
        memory_hooks_json={"hook": "existing"},
    )
    db_session.add(lemma)
    db_session.flush()

    now = datetime.now(timezone.utc)
    mature_card = Card(
        state=State.Review,
        stability=30.0,
        difficulty=5.0,
        due=now - timedelta(days=1),
        last_review=now - timedelta(days=31),
    )
    knowledge = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="known",
        fsrs_card_json=mature_card.to_dict(),
        source="encountered",
        times_seen=8,
        times_correct=7,
    )
    db_session.add(knowledge)
    db_session.commit()

    result = submit_review(db_session, lemma.lemma_id, rating_int=2)

    db_session.refresh(knowledge)
    log = db_session.query(ReviewLog).filter_by(lemma_id=lemma.lemma_id).one()
    next_due = datetime.fromisoformat(result["next_due"])

    # The user's rating remains intact for analysis and accuracy counters.
    assert log.rating == 2
    assert knowledge.times_seen == 9
    assert knowledge.times_correct == 7

    # FSRS receives a lapse, but no 10-minute step: the next review is soon,
    # not immediate, and much sooner than the ~30-day Hard interval here.
    assert log.fsrs_log_json["fsrs_policy"] == "assisted_lapse_v1"
    assert log.fsrs_log_json["fsrs_assisted_lapse"] is True
    assert log.fsrs_log_json["fsrs_rating_applied"] == 1
    assert (
        log.fsrs_log_json["fsrs_desired_retention"]
        == FSRS_ASSISTED_LAPSE_DESIRED_RETENTION
    )
    assert log.fsrs_log_json["fsrs_relearning_steps_seconds"] == []
    assert timedelta(hours=23) <= next_due - now <= timedelta(days=5)


def test_rating2_policy_has_single_switch_rollback(db_session, monkeypatch):
    """Disabling the feature restores the previous Hard mapping prospectively."""
    monkeypatch.setattr(fsrs_service, "FSRS_ASSISTED_LAPSE_ENABLED", False)
    lemma = Lemma(
        lemma_ar="ذَكَرَ",
        lemma_ar_bare="ذكر",
        gloss_en="to remember",
        memory_hooks_json={"hook": "existing"},
    )
    db_session.add(lemma)
    db_session.flush()
    knowledge = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="learning",
        fsrs_card_json=create_new_card(),
        source="encountered",
        times_seen=0,
        times_correct=0,
    )
    db_session.add(knowledge)
    db_session.commit()

    submit_review(db_session, lemma.lemma_id, rating_int=2)

    log = db_session.query(ReviewLog).filter_by(lemma_id=lemma.lemma_id).one()
    assert log.rating == 2
    assert log.fsrs_log_json["fsrs_policy"] == "standard_v2"
    assert log.fsrs_log_json["fsrs_assisted_lapse"] is False
    assert log.fsrs_log_json["fsrs_rating_applied"] == 2
