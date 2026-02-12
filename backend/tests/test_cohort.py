import json
from datetime import datetime, timedelta, timezone

from app.models import Lemma, UserLemmaKnowledge
from app.services.cohort_service import MAX_COHORT_SIZE, get_cohort_stats, get_focus_cohort


def _create_lemma(db, arabic="كتاب", english="book"):
    lemma = Lemma(lemma_ar=arabic, lemma_ar_bare=arabic, gloss_en=english, pos="noun")
    db.add(lemma)
    db.flush()
    return lemma


def _make_fsrs_card_json(due_dt: datetime, stability: float = 1.0) -> dict:
    """Create a minimal FSRS card dict with due time and stability."""
    return {
        "due": due_dt.isoformat(),
        "stability": stability,
        "difficulty": 0.3,
        "elapsed_days": 0,
        "scheduled_days": 1,
        "reps": 1,
        "lapses": 0,
        "state": 1,
        "last_review": datetime.now(timezone.utc).isoformat(),
    }


# --- Acquiring words always included ---


def test_acquiring_words_always_included(db_session):
    now = datetime.now(timezone.utc)
    lemmas = [
        _create_lemma(db_session, arabic=f"acq{i}", english=f"acq{i}")
        for i in range(5)
    ]

    for i, lemma in enumerate(lemmas):
        db_session.add(UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="acquiring",
            acquisition_box=i % 3 + 1,
            acquisition_next_due=now + timedelta(hours=i),
            times_seen=0, times_correct=0,
        ))
    db_session.commit()

    cohort = get_focus_cohort(db_session)
    for lemma in lemmas:
        assert lemma.lemma_id in cohort


def test_acquiring_words_included_even_when_not_due(db_session):
    now = datetime.now(timezone.utc)
    lemma = _create_lemma(db_session)

    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="acquiring",
        acquisition_box=2,
        acquisition_next_due=now + timedelta(days=5),  # far in the future
        times_seen=1, times_correct=1,
    ))
    db_session.commit()

    cohort = get_focus_cohort(db_session)
    assert lemma.lemma_id in cohort


# --- Cohort capped at MAX_COHORT_SIZE ---


def test_cohort_capped_at_max(db_session):
    now = datetime.now(timezone.utc)
    due_time = now - timedelta(hours=1)

    # Create more than MAX_COHORT_SIZE due FSRS words
    lemmas = []
    for i in range(MAX_COHORT_SIZE + 15):
        lemma = _create_lemma(db_session, arabic=f"w{i}", english=f"w{i}")
        lemmas.append(lemma)
        db_session.add(UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="learning",
            fsrs_card_json=_make_fsrs_card_json(due_time, stability=float(i)),
            times_seen=5, times_correct=3,
        ))
    db_session.commit()

    cohort = get_focus_cohort(db_session)
    assert len(cohort) == MAX_COHORT_SIZE


def test_acquiring_words_dont_count_toward_fsrs_cap(db_session):
    now = datetime.now(timezone.utc)
    due_time = now - timedelta(hours=1)

    # 5 acquiring words
    acq_lemmas = []
    for i in range(5):
        lemma = _create_lemma(db_session, arabic=f"acq{i}", english=f"acq{i}")
        acq_lemmas.append(lemma)
        db_session.add(UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="acquiring",
            acquisition_box=1,
            acquisition_next_due=now + timedelta(hours=1),
            times_seen=0, times_correct=0,
        ))

    # MAX_COHORT_SIZE due FSRS words
    fsrs_lemmas = []
    for i in range(MAX_COHORT_SIZE):
        lemma = _create_lemma(db_session, arabic=f"fsrs{i}", english=f"fsrs{i}")
        fsrs_lemmas.append(lemma)
        db_session.add(UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="learning",
            fsrs_card_json=_make_fsrs_card_json(due_time, stability=float(i)),
            times_seen=5, times_correct=3,
        ))
    db_session.commit()

    cohort = get_focus_cohort(db_session)

    # All 5 acquiring words must be included
    for lemma in acq_lemmas:
        assert lemma.lemma_id in cohort

    # Total cohort = 5 acquiring + 35 FSRS = MAX_COHORT_SIZE
    assert len(cohort) == MAX_COHORT_SIZE
    # Only MAX_COHORT_SIZE - 5 FSRS words fit
    fsrs_in_cohort = sum(1 for l in fsrs_lemmas if l.lemma_id in cohort)
    assert fsrs_in_cohort == MAX_COHORT_SIZE - 5


# --- Lowest stability first ---


def test_lowest_stability_first(db_session):
    now = datetime.now(timezone.utc)
    due_time = now - timedelta(hours=1)

    # Create words with different stabilities
    stabilities = [10.0, 0.5, 5.0, 0.1, 20.0, 1.0]
    lemmas = []
    for i, stab in enumerate(stabilities):
        lemma = _create_lemma(db_session, arabic=f"s{i}", english=f"s{i}")
        lemmas.append(lemma)
        db_session.add(UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="learning",
            fsrs_card_json=_make_fsrs_card_json(due_time, stability=stab),
            times_seen=5, times_correct=3,
        ))
    db_session.commit()

    # Cap cohort to only allow 3 FSRS words
    import app.services.cohort_service as cs
    original_max = cs.MAX_COHORT_SIZE
    cs.MAX_COHORT_SIZE = 3
    try:
        cohort = get_focus_cohort(db_session)
    finally:
        cs.MAX_COHORT_SIZE = original_max

    # The three lowest stabilities are 0.1, 0.5, 1.0
    expected_ids = {
        lemmas[3].lemma_id,  # stability 0.1
        lemmas[1].lemma_id,  # stability 0.5
        lemmas[5].lemma_id,  # stability 1.0
    }
    assert cohort == expected_ids


# --- Empty and edge cases ---


def test_empty_when_no_active_words(db_session):
    cohort = get_focus_cohort(db_session)
    assert cohort == set()


def test_excludes_suspended_words(db_session):
    lemma = _create_lemma(db_session)
    now = datetime.now(timezone.utc)

    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="suspended",
        fsrs_card_json=_make_fsrs_card_json(now - timedelta(hours=1)),
        times_seen=5, times_correct=3,
    ))
    db_session.commit()

    cohort = get_focus_cohort(db_session)
    assert lemma.lemma_id not in cohort


def test_excludes_encountered_words(db_session):
    lemma = _create_lemma(db_session)

    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="encountered",
        times_seen=0, times_correct=0,
    ))
    db_session.commit()

    cohort = get_focus_cohort(db_session)
    assert lemma.lemma_id not in cohort


def test_excludes_not_yet_due_fsrs_words(db_session):
    lemma = _create_lemma(db_session)
    now = datetime.now(timezone.utc)

    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="learning",
        fsrs_card_json=_make_fsrs_card_json(now + timedelta(days=5)),  # future due
        times_seen=5, times_correct=3,
    ))
    db_session.commit()

    cohort = get_focus_cohort(db_session)
    assert lemma.lemma_id not in cohort


# --- Cohort stats ---


def test_cohort_stats(db_session):
    now = datetime.now(timezone.utc)

    # 2 acquiring
    for i in range(2):
        lemma = _create_lemma(db_session, arabic=f"a{i}", english=f"a{i}")
        db_session.add(UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="acquiring",
            acquisition_box=1,
            acquisition_next_due=now + timedelta(hours=1),
            times_seen=0, times_correct=0,
        ))

    # 3 due FSRS
    for i in range(3):
        lemma = _create_lemma(db_session, arabic=f"d{i}", english=f"d{i}")
        db_session.add(UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="learning",
            fsrs_card_json=_make_fsrs_card_json(now - timedelta(hours=1)),
            times_seen=5, times_correct=3,
        ))

    # 1 not-due FSRS
    lemma = _create_lemma(db_session, arabic="nd", english="nd")
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="known",
        fsrs_card_json=_make_fsrs_card_json(now + timedelta(days=10)),
        times_seen=20, times_correct=18,
    ))
    db_session.commit()

    stats = get_cohort_stats(db_session)
    assert stats["acquiring"] == 2
    assert stats["fsrs_due"] == 3
    assert stats["fsrs_not_due"] == 1
    assert stats["cohort_size"] == 5  # 2 acquiring + 3 due
    assert stats["max_cohort_size"] == MAX_COHORT_SIZE
