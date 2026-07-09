from datetime import datetime, timedelta, timezone

from app.models import Lemma, ReviewLog, UserLemmaKnowledge
from app.services.leech_service import (
    LEECH_MAX_ACCURACY,
    LEECH_MIN_REVIEWS,
    LEECH_REINTRO_DAILY_CAP,
    LEECH_WINDOW_SIZE,
    LOW_PRIORITY_LEECH_DELAY_MULTIPLIER,
    REINTRO_DELAYS,
    _get_reintro_delay,
    check_and_manage_leeches,
    check_leech_reintroductions,
    check_single_word_leech,
    is_leech,
)
from app.services.acquisition_service import (
    ACQUISITION_EPISODE_LEECH_REINTRO,
    _daily_intro_count,
    submit_acquisition_review,
)


def _create_lemma(db, arabic="كتاب", english="book", frequency_rank=100):
    lemma = Lemma(
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        gloss_en=english,
        pos="noun",
        frequency_rank=frequency_rank,
    )
    db.add(lemma)
    db.flush()
    return lemma


def _add_reviews(db, lemma_id: int, ratings: list[int]):
    """Add ReviewLog entries with the given ratings (oldest first)."""
    now = datetime.now(timezone.utc)
    for i, rating in enumerate(ratings):
        db.add(ReviewLog(
            lemma_id=lemma_id,
            rating=rating,
            reviewed_at=now - timedelta(hours=len(ratings) - i),
        ))
    db.flush()


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
        times_correct=5,  # 50% accuracy — exactly at threshold
    )
    # LEECH_MAX_ACCURACY is 0.50, condition is < 0.50, so 50% is not a leech
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
        times_seen=3,  # < LEECH_MIN_REVIEWS (5)
        times_correct=0,  # 0% accuracy would be leech if enough reviews
    )
    assert is_leech(ulk) is False


def test_no_leech_exactly_min_reviews_boundary():
    ulk = UserLemmaKnowledge(
        times_seen=4,  # one below LEECH_MIN_REVIEWS (5)
        times_correct=0,  # 0% accuracy
    )
    assert is_leech(ulk) is False


def test_is_leech_exactly_at_min_reviews():
    ulk = UserLemmaKnowledge(
        times_seen=5,  # exactly LEECH_MIN_REVIEWS
        times_correct=2,  # 40% accuracy
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
    # Add review logs for sliding window detection
    _add_reviews(db_session, lemma.lemma_id, [1, 1, 3, 1, 1, 1, 1, 3, 1, 3, 1, 1])
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
    _add_reviews(db_session, lemma.lemma_id, [3, 3, 3, 1, 3, 3, 3, 3, 1, 3, 3, 1, 3, 3, 3])
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
        times_seen=3,  # < 5
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
    _add_reviews(db_session, lemma.lemma_id, [1, 1, 3, 1, 1, 1, 1, 3, 1, 1])
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
    _add_reviews(db_session, lemmas[0].lemma_id, [1, 1, 3, 1, 1, 1, 1, 3, 1, 1])
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemmas[1].lemma_id, knowledge_state="known",
        times_seen=20, times_correct=15,
    ))
    _add_reviews(db_session, lemmas[1].lemma_id, [3, 3, 3, 1, 3, 3, 3, 3, 1, 3] * 2)
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemmas[2].lemma_id, knowledge_state="lapsed",
        times_seen=9, times_correct=1,
    ))
    _add_reviews(db_session, lemmas[2].lemma_id, [1, 3, 1, 1, 1, 1, 1, 1, 1])
    db_session.commit()

    suspended = check_and_manage_leeches(db_session)
    assert lemmas[0].lemma_id in suspended
    assert lemmas[1].lemma_id not in suspended
    assert lemmas[2].lemma_id in suspended


# --- check_leech_reintroductions ---


def _disable_reintro_enrichment(monkeypatch):
    monkeypatch.setattr(
        "app.services.material_generator.generate_material_for_word",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.memory_hooks.generate_memory_hooks",
        lambda *_args, **_kwargs: None,
    )


def test_reintroduction_after_delay_first_time(db_session, monkeypatch):
    """First leech (leech_count=1) reintroduces after 3 days, preserves stats."""
    _disable_reintro_enrichment(monkeypatch)
    lemma = _create_lemma(db_session)
    now = datetime.now(timezone.utc)

    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="suspended",
        leech_suspended_at=now - timedelta(days=3, hours=1),
        leech_count=1,
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
    assert ulk.times_seen == 10  # preserved, not zeroed
    assert ulk.times_correct == 2  # preserved, not zeroed
    assert ulk.source == "study"
    assert ulk.acquisition_episode_kind == ACQUISITION_EPISODE_LEECH_REINTRO


def test_reintroduction_preserves_meaningful_source_and_excludes_intro_count(
    db_session, monkeypatch
):
    _disable_reintro_enrichment(monkeypatch)
    lemma = _create_lemma(db_session, arabic="رواية", english="novel")
    now = datetime.now(timezone.utc)
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="suspended",
        source="book",
        leech_suspended_at=now - timedelta(days=3, hours=1),
        leech_count=1,
        times_seen=10,
        times_correct=2,
    ))
    db_session.commit()

    assert lemma.lemma_id in check_leech_reintroductions(db_session)
    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).one()
    assert ulk.source == "book"
    assert ulk.acquisition_episode_kind == ACQUISITION_EPISODE_LEECH_REINTRO
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    assert _daily_intro_count(db_session, today_start) == 0


def test_reintroduction_second_time_needs_7_days(db_session):
    """Second leech (leech_count=2) needs 7 days."""
    lemma = _create_lemma(db_session)
    now = datetime.now(timezone.utc)

    # After 4 days — too soon for second suspension
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="suspended",
        leech_suspended_at=now - timedelta(days=4),
        leech_count=2,
        times_seen=15,
        times_correct=5,
    ))
    db_session.commit()

    reintroduced = check_leech_reintroductions(db_session)
    assert reintroduced == []

    # After 7+ days — ready
    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    ulk.leech_suspended_at = now - timedelta(days=7, hours=1)
    db_session.commit()

    reintroduced = check_leech_reintroductions(db_session)
    assert lemma.lemma_id in reintroduced


def test_reintroduction_third_time_needs_14_days(db_session):
    """Third+ leech (leech_count=3) needs 14 days."""
    lemma = _create_lemma(db_session)
    now = datetime.now(timezone.utc)

    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="suspended",
        leech_suspended_at=now - timedelta(days=10),
        leech_count=3,
        times_seen=20,
        times_correct=6,
    ))
    db_session.commit()

    reintroduced = check_leech_reintroductions(db_session)
    assert reintroduced == []  # only 10 days, need 14

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    ulk.leech_suspended_at = now - timedelta(days=14, hours=1)
    db_session.commit()

    reintroduced = check_leech_reintroductions(db_session)
    assert lemma.lemma_id in reintroduced


def test_no_reintroduction_too_soon(db_session):
    """First leech suspended 2 days ago (need 3 days)."""
    lemma = _create_lemma(db_session)
    now = datetime.now(timezone.utc)

    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="suspended",
        leech_suspended_at=now - timedelta(days=2),
        leech_count=1,
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
    """First leech reintroduces at exactly 3 days."""
    lemma = _create_lemma(db_session)
    now = datetime.now(timezone.utc)

    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="suspended",
        leech_suspended_at=now - timedelta(days=3),  # exactly 3 days for first
        leech_count=1,
        times_seen=10,
        times_correct=2,
    ))
    db_session.commit()

    reintroduced = check_leech_reintroductions(db_session)
    assert lemma.lemma_id in reintroduced


def test_low_priority_leech_reintroduction_uses_longer_cooldown(db_session):
    lemma = _create_lemma(db_session, arabic="جحرية", english="burrowing", frequency_rank=100000)
    now = datetime.now(timezone.utc)

    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="suspended",
        leech_suspended_at=now - timedelta(days=3, hours=1),
        leech_count=1,
        times_seen=10,
        times_correct=2,
    ))
    db_session.commit()

    reintroduced = check_leech_reintroductions(db_session)
    assert reintroduced == []

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    ulk.leech_suspended_at = now - (
        REINTRO_DELAYS[0] * LOW_PRIORITY_LEECH_DELAY_MULTIPLIER
    ) - timedelta(hours=1)
    db_session.commit()

    reintroduced = check_leech_reintroductions(db_session)
    assert lemma.lemma_id in reintroduced


def test_get_reintro_delay_core_rank_overrides_sparse_frequency_rank():
    """A frequent core word (good core_rank) is not throttled even when its
    per-lemma frequency_rank is unset or sparse/huge."""
    lemma = Lemma(
        lemma_ar="دفاع", lemma_ar_bare="دفاع", gloss_en="defense",
        source="book", frequency_rank=None,  # would be low-priority on its own
    )
    # No core_rank → low-priority → 4x cooldown
    assert _get_reintro_delay(0, lemma) == REINTRO_DELAYS[0] * LOW_PRIORITY_LEECH_DELAY_MULTIPLIER
    # In-main-lane core_rank → full priority → normal cooldown
    assert _get_reintro_delay(0, lemma, core_rank=1705) == REINTRO_DELAYS[0]
    # core_rank past the main lane → still low-priority
    assert (
        _get_reintro_delay(0, lemma, core_rank=9000)
        == REINTRO_DELAYS[0] * LOW_PRIORITY_LEECH_DELAY_MULTIPLIER
    )


def test_frequent_core_leech_reintroduced_on_normal_cooldown(db_session):
    """A leech that is frequent by core_rank but has a sparse frequency_rank is
    reintroduced on the normal 3-day cooldown, not the 4x low-priority delay."""
    from app.models import FrequencyCoreEntry

    lemma = _create_lemma(db_session, arabic="دفاع", english="defense", frequency_rank=None)
    db_session.add(FrequencyCoreEntry(
        core_rank=1705, lemma_id=lemma.lemma_id, lemma_key="دفاع",
        display_form="دفاع", score=1.0,
    ))
    now = datetime.now(timezone.utc)
    # Suspended just past the NORMAL cooldown but well within the 4x low-priority one.
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="suspended",
        leech_suspended_at=now - (REINTRO_DELAYS[0] + timedelta(hours=1)),
        leech_count=1,
        times_seen=10,
        times_correct=2,
    ))
    db_session.commit()

    reintroduced = check_leech_reintroductions(db_session)
    assert lemma.lemma_id in reintroduced


def test_leech_count_incremented_on_suspension(db_session):
    """leech_count is incremented each time a word is leech-suspended."""
    lemma = _create_lemma(db_session)
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="learning",
        times_seen=10,
        times_correct=2,
        leech_count=0,
    ))
    _add_reviews(db_session, lemma.lemma_id, [1, 1, 3, 1, 1, 1, 1, 3, 1, 1])
    db_session.commit()

    suspended = check_and_manage_leeches(db_session)
    assert lemma.lemma_id in suspended

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.leech_count == 1

    # Simulate reintroduction and re-leeching (reviews still bad)
    ulk.knowledge_state = "learning"
    ulk.leech_suspended_at = None
    db_session.commit()

    suspended = check_and_manage_leeches(db_session)
    assert lemma.lemma_id in suspended

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.leech_count == 2


# --- check_single_word_leech ---


def test_check_single_word_leech(db_session):
    lemma = _create_lemma(db_session)
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="learning",
        times_seen=10,
        times_correct=2,  # 20% accuracy — leech
    ))
    _add_reviews(db_session, lemma.lemma_id, [1, 1, 3, 1, 1, 1, 1, 3, 1, 1])
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
    _add_reviews(db_session, lemma.lemma_id, [3, 3, 3, 1, 3, 3, 3, 3, 1, 3])
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
    _add_reviews(db_session, lemma.lemma_id, [1, 1, 3, 1, 1, 1, 1, 3, 1, 1])
    db_session.commit()

    result = check_single_word_leech(db_session, lemma.lemma_id)
    assert result is True

    ulk = db_session.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    assert ulk.acquisition_box is None
    assert ulk.acquisition_next_due is None


def test_reintro_episode_ignores_old_failures_until_five_fresh_reviews(db_session):
    lemma = _create_lemma(db_session)
    now = datetime.now(timezone.utc)
    started = now - timedelta(hours=2)
    ulk = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="acquiring",
        acquisition_box=1,
        acquisition_started_at=started,
        acquisition_episode_kind=ACQUISITION_EPISODE_LEECH_REINTRO,
        leech_count=2,
        times_seen=20,
        times_correct=4,
    )
    db_session.add(ulk)
    # Historical window is a leech, but only one observation belongs to this
    # treatment episode.
    for i, rating in enumerate([1, 1, 1, 3, 1, 1, 1, 1]):
        db_session.add(ReviewLog(
            lemma_id=lemma.lemma_id,
            rating=rating,
            reviewed_at=started - timedelta(hours=10 - i),
        ))
    db_session.add(ReviewLog(
        lemma_id=lemma.lemma_id,
        rating=3,
        reviewed_at=now - timedelta(minutes=1),
    ))
    db_session.commit()

    assert check_single_word_leech(db_session, lemma.lemma_id) is False
    assert ulk.knowledge_state == "acquiring"


def test_reintro_episode_can_suspend_after_five_fresh_failures(db_session):
    lemma = _create_lemma(db_session)
    now = datetime.now(timezone.utc)
    started = now - timedelta(hours=6)
    ulk = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="acquiring",
        acquisition_box=1,
        acquisition_started_at=started,
        acquisition_episode_kind=ACQUISITION_EPISODE_LEECH_REINTRO,
        leech_count=1,
        times_seen=15,
        times_correct=3,
    )
    db_session.add(ulk)
    for i, rating in enumerate([1, 1, 3, 1, 1]):
        db_session.add(ReviewLog(
            lemma_id=lemma.lemma_id,
            rating=rating,
            reviewed_at=started + timedelta(hours=i + 1),
        ))
    db_session.commit()

    assert check_single_word_leech(db_session, lemma.lemma_id) is True
    assert ulk.knowledge_state == "suspended"


def test_tier_e_graduation_does_not_immediately_resuspend_reintroduced_leech(
    db_session,
):
    lemma = _create_lemma(db_session)
    now = datetime.now(timezone.utc)
    started = now - timedelta(hours=1)
    ulk = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="acquiring",
        acquisition_box=1,
        acquisition_next_due=now - timedelta(minutes=1),
        acquisition_started_at=started,
        acquisition_episode_kind=ACQUISITION_EPISODE_LEECH_REINTRO,
        last_reviewed=now - timedelta(days=4),
        leech_count=2,
        times_seen=20,
        times_correct=4,
    )
    db_session.add(ulk)
    for i in range(8):
        db_session.add(ReviewLog(
            lemma_id=lemma.lemma_id,
            rating=1,
            reviewed_at=started - timedelta(hours=10 - i),
        ))
    db_session.commit()

    result = submit_acquisition_review(
        db_session,
        lemma_id=lemma.lemma_id,
        rating_int=3,
    )

    assert result["graduated"] is True
    assert ulk.knowledge_state == "learning"
    assert check_single_word_leech(db_session, lemma.lemma_id) is False
    assert ulk.knowledge_state == "learning"


def test_reintro_daily_cap_defers_excess_ready_words(db_session, monkeypatch):
    _disable_reintro_enrichment(monkeypatch)
    now = datetime.now(timezone.utc)
    lemma_ids = []
    for i in range(LEECH_REINTRO_DAILY_CAP + 2):
        lemma = _create_lemma(
            db_session,
            arabic=f"كلمة{i}",
            english=f"word{i}",
            frequency_rank=100 + i,
        )
        lemma_ids.append(lemma.lemma_id)
        db_session.add(UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="suspended",
            leech_suspended_at=now - timedelta(days=4),
            leech_count=1,
            times_seen=10,
            times_correct=2,
        ))
    db_session.commit()

    reintroduced = check_leech_reintroductions(db_session)

    assert len(reintroduced) == LEECH_REINTRO_DAILY_CAP
    assert reintroduced == lemma_ids[:LEECH_REINTRO_DAILY_CAP]
    remaining = (
        db_session.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.knowledge_state == "suspended")
        .count()
    )
    assert remaining == 2
    # A second scheduler pass in the same UTC day must observe the explicit
    # episode starts and leave the deferred words for tomorrow.
    assert check_leech_reintroductions(db_session) == []


def test_reintro_admission_closes_under_box1_recovery_debt(db_session, monkeypatch):
    _disable_reintro_enrichment(monkeypatch)
    now = datetime.now(timezone.utc)
    ready = _create_lemma(db_session, arabic="جاهز", english="ready", frequency_rank=100)
    db_session.add(UserLemmaKnowledge(
        lemma_id=ready.lemma_id,
        knowledge_state="suspended",
        leech_suspended_at=now - timedelta(days=4),
        leech_count=1,
        times_seen=10,
        times_correct=2,
    ))
    for i in range(20):
        debt = _create_lemma(db_session, arabic=f"دين{i}", english=f"debt{i}")
        db_session.add(UserLemmaKnowledge(
            lemma_id=debt.lemma_id,
            knowledge_state="acquiring",
            acquisition_box=1,
            acquisition_next_due=now - timedelta(hours=1),
            times_seen=1,
            times_correct=0,
        ))
    db_session.commit()

    assert check_leech_reintroductions(db_session) == []
    assert db_session.query(UserLemmaKnowledge).filter_by(
        lemma_id=ready.lemma_id
    ).one().knowledge_state == "suspended"


def test_reintro_admission_uses_remaining_box1_headroom(db_session, monkeypatch):
    _disable_reintro_enrichment(monkeypatch)
    now = datetime.now(timezone.utc)
    ready_ids = []
    for i in range(3):
        ready = _create_lemma(
            db_session, arabic=f"جاهز{i}", english=f"ready{i}", frequency_rank=100 + i,
        )
        ready_ids.append(ready.lemma_id)
        db_session.add(UserLemmaKnowledge(
            lemma_id=ready.lemma_id,
            knowledge_state="suspended",
            leech_suspended_at=now - timedelta(days=4),
            leech_count=1,
            times_seen=10,
            times_correct=2,
        ))
    for i in range(19):
        debt = _create_lemma(db_session, arabic=f"رصيد{i}", english=f"debt{i}")
        db_session.add(UserLemmaKnowledge(
            lemma_id=debt.lemma_id,
            knowledge_state="acquiring",
            acquisition_box=1,
            acquisition_next_due=now - timedelta(hours=1),
            times_seen=1,
            times_correct=0,
        ))
    db_session.commit()

    assert check_leech_reintroductions(db_session) == ready_ids[:1]


def test_reintro_admission_closes_under_main_fsrs_hiatus_debt(
    db_session, monkeypatch
):
    from app.services.acquisition_service import RECOVERY_FSRS_MAIN_DUE_LIMIT

    _disable_reintro_enrichment(monkeypatch)
    now = datetime.now(timezone.utc)
    ready = _create_lemma(db_session, arabic="راجع", english="return", frequency_rank=100)
    db_session.add(UserLemmaKnowledge(
        lemma_id=ready.lemma_id,
        knowledge_state="suspended",
        leech_suspended_at=now - timedelta(days=4),
        leech_count=1,
        times_seen=10,
        times_correct=2,
    ))
    db_session.commit()
    monkeypatch.setattr(
        "app.services.acquisition_service._main_fsrs_due_count",
        lambda _db, _now: RECOVERY_FSRS_MAIN_DUE_LIMIT,
    )

    assert check_leech_reintroductions(db_session) == []


def test_reintro_admission_closes_under_box2_due_debt(db_session, monkeypatch):
    _disable_reintro_enrichment(monkeypatch)
    now = datetime.now(timezone.utc)
    ready = _create_lemma(db_session, arabic="جاهز", english="ready", frequency_rank=100)
    db_session.add(UserLemmaKnowledge(
        lemma_id=ready.lemma_id,
        knowledge_state="suspended",
        leech_suspended_at=now - timedelta(days=4),
        leech_count=1,
        times_seen=10,
        times_correct=2,
    ))
    for i in range(30):
        debt = _create_lemma(db_session, arabic=f"ثان{i}", english=f"box2-{i}")
        db_session.add(UserLemmaKnowledge(
            lemma_id=debt.lemma_id,
            knowledge_state="acquiring",
            acquisition_box=2,
            acquisition_next_due=now - timedelta(hours=1),
            times_seen=3,
            times_correct=1,
        ))
    db_session.commit()

    assert check_leech_reintroductions(db_session) == []
