from datetime import datetime, timedelta, timezone

import pytest

from app.models import Lemma, ReviewLog, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.fsrs_service import parse_json_column
from app.services.surface_form_experiment import (
    EXACT_SURFACE_EXPERIMENT_KEY,
    active_treatment_episodes,
    deterministic_arm,
    eligible_surface_morphology,
    process_surface_experiment_review,
)


def _lemma(db, arabic="أفسد", gloss="to spoil", pos="verb", forms=None):
    lemma = Lemma(
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        gloss_en=gloss,
        pos=pos,
        forms_json=forms,
    )
    db.add(lemma)
    db.flush()
    return lemma


def _reviewable_sentence(db, lemma_id, surface="يُفْسِدُ", sentence_id=None):
    sentence = Sentence(
        id=sentence_id,
        arabic_text=f"{surface} الشيء",
        english_translation="He spoils the thing",
        target_lemma_id=lemma_id,
        source="book",
        is_active=True,
        mappings_verified_at=datetime.now(timezone.utc),
    )
    db.add(sentence)
    db.flush()
    db.add(SentenceWord(
        sentence_id=sentence.id,
        position=0,
        surface_form=surface,
        lemma_id=lemma_id,
        is_target_word=True,
    ))
    db.flush()
    return sentence


def _review(
    db,
    lemma_id,
    *,
    confused=True,
    acquisition=False,
    identity="review-1",
    review_mode="reading",
):
    row = ReviewLog(
        lemma_id=lemma_id,
        rating=2 if confused else 3,
        reviewed_at=datetime.now(timezone.utc),
        was_confused=confused,
        is_acquisition=acquisition,
        credit_type="collateral" if confused else "primary",
        client_review_id=identity,
        review_mode=review_mode,
    )
    db.add(row)
    db.flush()
    return row


def _episodes(knowledge):
    stats = parse_json_column(knowledge.variant_stats_json)
    return stats.get(EXACT_SURFACE_EXPERIMENT_KEY, {}).get("episodes", [])


def test_deterministic_arm_is_stable_and_balanced():
    assert deterministic_arm("same", 42, "يكتب") == deterministic_arm(
        "same", 42, "يكتب"
    )
    arms = {deterministic_arm(f"r-{i}", 42, "يكتب") for i in range(100)}
    assert arms == {"control", "treatment"}


def test_eligibility_excludes_citation_and_pure_prefix_but_keeps_forms(db_session):
    lemma = _lemma(db_session)
    assert eligible_surface_morphology("أَفْسَدَ،", lemma) is None
    assert eligible_surface_morphology("وَأَفْسَدَ", lemma) is None
    assert eligible_surface_morphology("يُفْسِدُ", lemma)["category"] == "verb_present"

    noun = _lemma(
        db_session,
        arabic="ورقة",
        gloss="paper",
        pos="noun",
        forms={"plural": "أَوْرَاق"},
    )
    assert eligible_surface_morphology("أَوْرَاق،", noun)["form_key"] == "plural"


def test_yellow_fsrs_event_assigns_once_when_different_material_exists(db_session):
    lemma = _lemma(db_session)
    knowledge = UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state="known")
    db_session.add(knowledge)
    alternate = _reviewable_sentence(db_session, lemma.lemma_id)
    trigger = _review(db_session, lemma.lemma_id)
    now = datetime.now(timezone.utc)

    process_surface_experiment_review(
        db_session,
        knowledge,
        lemma,
        ["يُفْسِدُ،"],
        trigger,
        "collateral",
        [999],
        now,
    )
    process_surface_experiment_review(
        db_session,
        knowledge,
        lemma,
        ["يفسد"],
        _review(db_session, lemma.lemma_id, identity="review-2"),
        "collateral",
        [998],
        now + timedelta(minutes=1),
    )
    db_session.commit()
    db_session.refresh(knowledge)

    episodes = _episodes(knowledge)
    assert len(episodes) == 1
    assert episodes[0]["surface_key"] == "يفسد"
    assert episodes[0]["candidate_count_at_trigger"] == 1
    assert episodes[0]["arm"] in {"control", "treatment"}
    assert alternate.id not in episodes[0]["trigger_sentence_ids"]


def test_matching_later_primary_review_records_first_outcome(db_session):
    lemma = _lemma(db_session)
    knowledge = UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state="known")
    db_session.add(knowledge)
    _reviewable_sentence(db_session, lemma.lemma_id)
    trigger = _review(db_session, lemma.lemma_id)
    now = datetime.now(timezone.utc)
    process_surface_experiment_review(
        db_session, knowledge, lemma, ["يفسد"], trigger,
        "collateral", [999], now,
    )

    outcome = _review(
        db_session,
        lemma.lemma_id,
        confused=False,
        identity="review-outcome",
    )
    outcome.sentence_id = 123
    process_surface_experiment_review(
        db_session, knowledge, lemma, ["يُفْسِدُ"], outcome,
        "primary", [123], now + timedelta(days=2),
    )
    db_session.commit()
    db_session.refresh(knowledge)

    episode = _episodes(knowledge)[0]
    assert episode["outcome_rating"] == 3
    assert episode["outcome_was_confused"] is False
    assert episode["outcome_credit_type"] == "primary"
    assert episode["outcome_sentence_id"] == 123


def test_repeat_of_trigger_sentence_does_not_resolve_episode(db_session):
    lemma = _lemma(db_session)
    knowledge = UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state="known")
    db_session.add(knowledge)
    _reviewable_sentence(db_session, lemma.lemma_id)
    now = datetime.now(timezone.utc)
    process_surface_experiment_review(
        db_session,
        knowledge,
        lemma,
        ["يفسد"],
        _review(db_session, lemma.lemma_id),
        "collateral",
        [999],
        now,
    )

    process_surface_experiment_review(
        db_session,
        knowledge,
        lemma,
        ["يفسد"],
        _review(
            db_session,
            lemma.lemma_id,
            confused=False,
            identity="same-sentence",
        ),
        "primary",
        [999],
        now + timedelta(days=2),
    )

    assert _episodes(knowledge)[0]["outcome_rating"] is None


@pytest.mark.parametrize("arm", ["control", "treatment"])
def test_first_next_primary_any_form_is_recorded_for_both_arms(db_session, arm):
    lemma = _lemma(db_session)
    now = datetime.now(timezone.utc)
    knowledge = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="known",
        variant_stats_json={
            EXACT_SURFACE_EXPERIMENT_KEY: {
                "version": "exact_surface_v1",
                "episodes": [{
                    "id": f"any-{arm}",
                    "arm": arm,
                    "surface_key": "يفسد",
                    "trigger_review_id": 0,
                    "trigger_sentence_ids": [999],
                    "triggered_at": now.isoformat(),
                    "expires_at": (now + timedelta(days=14)).isoformat(),
                    "outcome_rating": None,
                    "any_form_outcome_rating": None,
                }],
            }
        },
    )
    db_session.add(knowledge)
    later = _review(
        db_session,
        lemma.lemma_id,
        confused=False,
        identity=f"any-{arm}-outcome",
    )

    process_surface_experiment_review(
        db_session,
        knowledge,
        lemma,
        ["أفسد"],
        later,
        "primary",
        [123],
        now + timedelta(days=1),
    )

    episode = _episodes(knowledge)[0]
    assert episode["any_form_review_id"] == later.id
    assert episode["any_form_outcome_rating"] == 3
    assert episode["any_form_was_exact"] is False
    assert episode["outcome_rating"] is None


def test_acquisition_yellow_and_no_material_do_not_assign(db_session):
    lemma = _lemma(db_session)
    knowledge = UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state="acquiring")
    db_session.add(knowledge)
    acquisition = _review(db_session, lemma.lemma_id, acquisition=True)

    process_surface_experiment_review(
        db_session, knowledge, lemma, ["يفسد"], acquisition,
        "collateral", [999], datetime.now(timezone.utc),
    )
    assert _episodes(knowledge) == []


def test_fsrs_yellow_without_different_material_does_not_assign(db_session):
    lemma = _lemma(db_session)
    knowledge = UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state="known")
    db_session.add(knowledge)

    process_surface_experiment_review(
        db_session,
        knowledge,
        lemma,
        ["يفسد"],
        _review(db_session, lemma.lemma_id),
        "collateral",
        [999],
        datetime.now(timezone.utc),
    )

    assert _episodes(knowledge) == []


def test_listening_yellow_does_not_enter_visual_form_pilot(db_session):
    lemma = _lemma(db_session)
    knowledge = UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state="known")
    db_session.add(knowledge)
    _reviewable_sentence(db_session, lemma.lemma_id)

    process_surface_experiment_review(
        db_session,
        knowledge,
        lemma,
        ["يفسد"],
        _review(db_session, lemma.lemma_id, review_mode="listening"),
        "collateral",
        [999],
        datetime.now(timezone.utc),
    )

    assert _episodes(knowledge) == []


def test_candidate_with_two_forms_of_same_lemma_does_not_assign(db_session):
    lemma = _lemma(db_session)
    knowledge = UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state="known")
    db_session.add(knowledge)
    sentence = _reviewable_sentence(db_session, lemma.lemma_id, surface="يفسد")
    db_session.add(SentenceWord(
        sentence_id=sentence.id,
        position=1,
        surface_form="أفسد",
        lemma_id=lemma.lemma_id,
    ))
    db_session.flush()

    process_surface_experiment_review(
        db_session,
        knowledge,
        lemma,
        ["يفسد"],
        _review(db_session, lemma.lemma_id),
        "collateral",
        [999],
        datetime.now(timezone.utc),
    )

    assert _episodes(knowledge) == []


def test_acquisition_review_cannot_resolve_existing_fsrs_episode(db_session):
    lemma = _lemma(db_session)
    knowledge = UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state="known")
    db_session.add(knowledge)
    _reviewable_sentence(db_session, lemma.lemma_id)
    now = datetime.now(timezone.utc)
    process_surface_experiment_review(
        db_session,
        knowledge,
        lemma,
        ["يفسد"],
        _review(db_session, lemma.lemma_id),
        "collateral",
        [999],
        now,
    )

    knowledge.knowledge_state = "acquiring"
    acquisition_outcome = _review(
        db_session,
        lemma.lemma_id,
        confused=False,
        acquisition=True,
        identity="acquisition-outcome",
    )
    process_surface_experiment_review(
        db_session,
        knowledge,
        lemma,
        ["يفسد"],
        acquisition_outcome,
        "primary",
        [123],
        now + timedelta(days=1),
    )

    assert _episodes(knowledge)[0]["outcome_rating"] is None


def test_active_treatment_episodes_ignores_control_outcome_and_expiry(db_session):
    now = datetime.now(timezone.utc)
    rows = {}
    for index, (arm, expires, outcome) in enumerate([
        ("treatment", now + timedelta(days=1), None),
        ("control", now + timedelta(days=1), None),
        ("treatment", now + timedelta(days=1), 3),
        ("treatment", now - timedelta(seconds=1), None),
    ]):
        lemma = _lemma(db_session, arabic=f"فعل{index}")
        knowledge = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="known",
            variant_stats_json={
                EXACT_SURFACE_EXPERIMENT_KEY: {
                    "version": "exact_surface_v1",
                    "episodes": [{
                        "id": str(index),
                        "arm": arm,
                        "surface_key": f"يفعل{index}",
                        "triggered_at": now.isoformat(),
                        "expires_at": expires.isoformat(),
                        "outcome_rating": outcome,
                    }],
                }
            },
        )
        db_session.add(knowledge)
        rows[lemma.lemma_id] = knowledge
    db_session.flush()

    active = active_treatment_episodes(rows, now)
    assert list(active) == [next(iter(rows))]


def test_active_treatment_episode_pauses_during_acquisition(db_session):
    now = datetime.now(timezone.utc)
    lemma = _lemma(db_session)
    knowledge = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="acquiring",
        variant_stats_json={
            EXACT_SURFACE_EXPERIMENT_KEY: {
                "version": "exact_surface_v1",
                "episodes": [{
                    "id": "paused",
                    "arm": "treatment",
                    "surface_key": "يفسد",
                    "triggered_at": now.isoformat(),
                    "expires_at": (now + timedelta(days=1)).isoformat(),
                    "outcome_rating": None,
                }],
            }
        },
    )

    assert active_treatment_episodes({lemma.lemma_id: knowledge}, now) == {}
