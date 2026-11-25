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


def _book_entry(db, rank, lemma=None, key="x"):
    from app.models import BookFrequencyEntry

    db.add(BookFrequencyEntry(
        rank=rank, lemma_id=lemma.lemma_id if lemma else None,
        lemma_key=key, count=10, version="book_frequency_v1",
    ))


def test_book_lanes_keep_frequent_and_own_reading_main_and_defer_rare(db_session, monkeypatch):
    from app.services.frequency_lanes import effective_frequency_ranks

    frequent = _lemma(db_session, "كثير", "book", frequency_rank=90000)
    # In the fused core at a good rank, but rare in book text.
    rare = _lemma(db_session, "قليل", "duolingo", frequency_rank=300)
    absent = _lemma(db_session, "غائب", "textbook_scan", frequency_rank=300)
    snapped = _lemma(db_session, "صورة", "snap", frequency_rank=None)
    for lemma, source in ((frequent, "book"), (rare, "duolingo"), (absent, "textbook_scan"), (snapped, "snap")):
        _known_due(db_session, lemma, source)
    db_session.add(FrequencyCoreEntry(
        core_rank=100, lemma_id=rare.lemma_id, lemma_key="قليل", display_form="قليل",
        score=1.0, broad_source_count=1, confidence_tier="high",
    ))
    _book_entry(db_session, 150, frequent)
    _book_entry(db_session, 3500, rare)
    db_session.commit()

    snapshot = due_lane_snapshot(db_session)
    assert snapshot.main_due_ids == {frequent.lemma_id, snapped.lemma_id}
    assert snapshot.slow_due_ids == {rare.lemma_id, absent.lemma_id}
    ranks = effective_frequency_ranks(db_session, [frequent.lemma_id, rare.lemma_id, absent.lemma_id])
    assert ranks[frequent.lemma_id] < ranks[rare.lemma_id] < ranks[absent.lemma_id]

    # Rollback switch restores the core-rank rules.
    monkeypatch.setenv("ALIF_BOOK_FREQUENCY_LANES", "0")
    legacy = due_lane_snapshot(db_session)
    assert rare.lemma_id in legacy.main_due_ids
    assert frequent.lemma_id in legacy.slow_due_ids


def test_book_lanes_inactive_while_the_list_is_empty(db_session):
    rare = _lemma(db_session, "قليل", "duolingo", frequency_rank=300)
    _known_due(db_session, rare, "duolingo")
    db_session.commit()
    assert due_lane_snapshot(db_session).main_due_ids == {rare.lemma_id}


def test_book_frequency_progress_counts_missing_words_in_the_band(db_session):
    from app.services.frequency_lanes import book_frequency_progress

    firm = _lemma(db_session, "ثابت", "book")
    weak = _lemma(db_session, "ضعيف", "book")
    unstarted = _lemma(db_session, "جديد", "book")
    db_session.add(UserLemmaKnowledge(
        lemma_id=firm.lemma_id, knowledge_state="known", fsrs_card_json={"stability": 45.0}))
    db_session.add(UserLemmaKnowledge(
        lemma_id=weak.lemma_id, knowledge_state="known", fsrs_card_json={"stability": 4.0}))
    _book_entry(db_session, 1, firm)
    _book_entry(db_session, 2, weak)
    _book_entry(db_session, 3, unstarted)
    _book_entry(db_session, 4, None, key="مفقود")
    db_session.commit()

    band = book_frequency_progress(db_session)["bands"][0]
    assert (band["total"], band["firm"], band["in_review"], band["not_started"], band["not_in_alif"]) == (4, 1, 1, 1, 1)
    assert band["firm_pct"] == 25.0
