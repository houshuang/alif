from datetime import datetime, timedelta, timezone

from app.models import BookFrequencyEntry, Lemma, ReadingTarget, UserLemmaKnowledge
from app.services import frequent_intake
from app.services.acquisition_service import start_acquisition
from app.services.frequent_intake import (
    FREQUENT_BOX1_LIMIT,
    frequent_new_room,
    interleave_frequent,
)

WORDS = ["كتاب", "بيت", "مدرسة", "شجرة", "نهر", "جبل", "قلم", "باب", "طريق", "سماء", "بحر", "ولد"]


def _lemma(db, arabic, rank=None):
    lemma = Lemma(
        lemma_ar=arabic, lemma_ar_bare=arabic, gloss_en=arabic, source="book",
        gates_completed_at=datetime.now(timezone.utc),
    )
    db.add(lemma)
    db.flush()
    if rank is not None:
        db.add(BookFrequencyEntry(
            rank=rank, lemma_id=lemma.lemma_id, lemma_key=arabic, count=10,
            version="book_frequency_v1",
        ))
    return lemma


def _box1(db, lemma, source="snap"):
    now = datetime.now(timezone.utc)
    db.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id, knowledge_state="acquiring", acquisition_box=1,
        acquisition_next_due=now - timedelta(hours=1), source=source,
        acquisition_started_at=now - timedelta(days=2), acquisition_episode_kind="new",
        times_seen=0, times_correct=0,
    ))


def _load_box1_with_rare_picks(db, count=6):
    for index in range(count):
        _box1(db, _lemma(db, f"{WORDS[index]}ات", rank=5000 + index))
    db.flush()


def test_frequent_word_starts_while_reading_picks_hold_box1(db_session):
    _load_box1_with_rare_picks(db_session)
    frequent = _lemma(db_session, "عام", rank=300)
    rare = _lemma(db_session, "نادر", rank=9000)
    db_session.commit()

    assert start_acquisition(db_session, frequent.lemma_id, source="frequency_core").knowledge_state == "acquiring"
    assert start_acquisition(db_session, rare.lemma_id, source="frequency_core").knowledge_state == "encountered"


def test_textbook_target_counts_as_frequent(db_session):
    _load_box1_with_rare_picks(db_session)
    _lemma(db_session, "مرجع", rank=100)
    target = _lemma(db_session, "درس")
    db_session.add(ReadingTarget(lemma_id=target.lemma_id, program="alkitaab_part1"))
    db_session.commit()

    assert start_acquisition(db_session, target.lemma_id, source="frequency_core").knowledge_state == "acquiring"


def test_allowance_closes_at_its_own_box1_ceiling(db_session, monkeypatch):
    monkeypatch.setattr(frequent_intake, "FREQUENT_BOX1_LIMIT", 6)
    for index in range(6):
        _box1(db_session, _lemma(db_session, WORDS[index], rank=10 + index), source="frequency_core")
    waiting = _lemma(db_session, "عام", rank=300)
    db_session.commit()

    assert frequent_new_room(db_session, datetime.now(timezone.utc)) == 0
    assert start_acquisition(db_session, waiting.lemma_id, source="frequency_core").knowledge_state == "encountered"


def test_daily_allowance_ignores_learner_picks(db_session, monkeypatch):
    monkeypatch.setattr(frequent_intake, "FREQUENT_DAILY_NEW", 1)
    now = datetime.now(timezone.utc)
    picked = _lemma(db_session, WORDS[0], rank=20)
    db_session.add(UserLemmaKnowledge(
        lemma_id=picked.lemma_id, knowledge_state="acquiring", acquisition_box=2,
        acquisition_next_due=now + timedelta(days=1), source="snap",
        acquisition_started_at=now, acquisition_episode_kind="new",
    ))
    db_session.commit()
    assert frequent_new_room(db_session, now) == 1

    started = _lemma(db_session, WORDS[1], rank=21)
    db_session.add(UserLemmaKnowledge(
        lemma_id=started.lemma_id, knowledge_state="acquiring", acquisition_box=2,
        acquisition_next_due=now + timedelta(days=1), source="frequency_core",
        acquisition_started_at=now, acquisition_episode_kind="new",
    ))
    db_session.commit()
    assert frequent_new_room(db_session, now) == 0


def test_switch_and_empty_list_turn_the_allowance_off(db_session, monkeypatch):
    lemma = _lemma(db_session, "عام")
    db_session.commit()
    now = datetime.now(timezone.utc)
    assert frequent_new_room(db_session, now) == 0

    db_session.add(BookFrequencyEntry(
        rank=5, lemma_id=lemma.lemma_id, lemma_key="عام", count=10, version="book_frequency_v1",
    ))
    db_session.commit()
    assert frequent_new_room(db_session, now) > 0
    monkeypatch.setenv("ALIF_FREQUENT_WORD_INTAKE", "0")
    assert frequent_new_room(db_session, now) == 0


def test_interleave_alternates_frequent_words_and_other_words(db_session):
    frequent = [_lemma(db_session, WORDS[i], rank=10 + i).lemma_id for i in range(2)]
    other = [_lemma(db_session, WORDS[5 + i], rank=4000 + i).lemma_id for i in range(3)]
    db_session.commit()

    order = interleave_frequent(db_session, other + frequent)
    assert order == [frequent[0], other[0], frequent[1], other[1], other[2]]


def test_frequent_leech_returns_while_box1_is_full(db_session, monkeypatch):
    from app.services import leech_service

    monkeypatch.setattr(leech_service, "generate_material_for_word", lambda *a, **k: None, raising=False)
    _load_box1_with_rare_picks(db_session)
    now = datetime.now(timezone.utc)
    frequent = _lemma(db_session, "رأى", rank=26)
    rare = _lemma(db_session, "غريب", rank=7000)
    for lemma in (frequent, rare):
        db_session.add(UserLemmaKnowledge(
            lemma_id=lemma.lemma_id, knowledge_state="suspended", source="book",
            leech_suspended_at=now - timedelta(days=120), leech_count=3,
            times_seen=10, times_correct=4,
        ))
    db_session.commit()

    reintroduced = leech_service.check_leech_reintroductions(db_session)
    assert reintroduced == [frequent.lemma_id]


def test_frequent_leech_waits_at_most_three_days(db_session):
    from app.services import leech_service

    now = datetime.now(timezone.utc)
    frequent = _lemma(db_session, "رأى", rank=26)
    db_session.add(UserLemmaKnowledge(
        lemma_id=frequent.lemma_id, knowledge_state="suspended", source="book",
        leech_suspended_at=now - timedelta(days=4), leech_count=5,
        times_seen=10, times_correct=4,
    ))
    db_session.commit()

    assert leech_service.check_leech_reintroductions(db_session) == [frequent.lemma_id]
    assert FREQUENT_BOX1_LIMIT > 0
