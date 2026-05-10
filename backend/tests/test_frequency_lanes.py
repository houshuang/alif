from datetime import datetime, timezone, timedelta

from app.models import FrequencyCoreEntry, Lemma, UserLemmaKnowledge
from app.services.frequency_lanes import due_lane_snapshot, select_slow_lane_sample


def _lemma(db, arabic, source, frequency_rank=None):
    lemma = Lemma(
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        gloss_en=arabic,
        source=source,
        frequency_rank=frequency_rank,
        gates_completed_at=datetime.now(timezone.utc),
    )
    db.add(lemma)
    db.flush()
    return lemma


def _known_due(db, lemma, ulk_source):
    db.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="known",
        source=ulk_source,
        fsrs_card_json={"due": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()},
    ))


def test_due_lane_snapshot_keeps_acquiring_and_core_main_but_defers_artifact(db_session):
    core = _lemma(db_session, "مهم", "book", frequency_rank=9000)
    artifact = _lemma(db_session, "نادر", "book", frequency_rank=9000)
    acquiring = _lemma(db_session, "جديد", "textbook_scan", frequency_rank=None)

    _known_due(db_session, core, "book")
    _known_due(db_session, artifact, "book")
    db_session.add(UserLemmaKnowledge(
        lemma_id=acquiring.lemma_id,
        knowledge_state="acquiring",
        source="textbook_scan",
        acquisition_box=1,
        acquisition_next_due=datetime.now(timezone.utc) - timedelta(hours=1),
    ))
    db_session.add(FrequencyCoreEntry(
        core_rank=2500,
        lemma_id=core.lemma_id,
        lemma_key=f"lemma:{core.lemma_id}",
        display_form=core.lemma_ar,
        score=1.0,
        confidence_tier="high",
    ))
    db_session.commit()

    snap = due_lane_snapshot(db_session)

    assert core.lemma_id in snap.main_due_ids
    assert acquiring.lemma_id in snap.main_due_ids
    assert artifact.lemma_id in snap.slow_due_ids


def test_slow_lane_sample_is_capped_to_ten_percent():
    sample = select_slow_lane_sample(
        slow_due_ids={1, 2, 3, 4, 5},
        overdue_days_map={1: 0.1, 2: 4.0, 3: 2.0, 4: 1.0, 5: 3.0},
        session_limit=10,
    )
    assert sample == {2}


def test_slow_lane_sample_prefers_higher_frequency_before_oldest_debt():
    sample = select_slow_lane_sample(
        slow_due_ids={1, 2, 3},
        overdue_days_map={1: 30.0, 2: 10.0, 3: 1.0},
        session_limit=10,
        frequency_rank_map={1: 50000, 2: 5000, 3: 100},
    )
    assert sample == {3}
