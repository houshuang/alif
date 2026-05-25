"""Acquisition service: Leitner box transitions, tiered graduation, daily cap,
variant redirect."""
from datetime import datetime, timedelta, timezone

from app.models import Lemma, ReviewLog, UserLemmaKnowledge
from app.services import acquisition_service
from app.services.acquisition_service import (
    BOX_INTERVALS,
    DAILY_INTRO_CAP,
    FAST_GRAD_INTRO_GAP,
    FAST_INTRO_RETRY_INTERVAL,
    get_acquisition_due,
    get_acquisition_stats,
    start_acquisition,
    submit_acquisition_review,
)


def _seed_lemma(
    db,
    *,
    form="βιβλίο",
    bare="βιβλιο",
    canonical=None,
    word_category=None,
    language_code="el",
) -> Lemma:
    lemma = Lemma(
        language_code=language_code, lemma_form=form, lemma_bare=bare, source="test",
        canonical_lemma_id=canonical, word_category=word_category,
    )
    db.add(lemma)
    db.flush()
    return lemma


def test_start_acquisition_creates_box1_ulk(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        ulk = start_acquisition(db, lemma_id=lemma.lemma_id, source="reading_intake")
        db.commit()
        assert ulk.knowledge_state == "acquiring"
        assert ulk.acquisition_box == 1
        assert ulk.source == "reading_intake"
        assert ulk.acquisition_started_at is not None
        assert ulk.entered_acquiring_at is not None


def test_start_acquisition_due_immediately(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        ulk = start_acquisition(
            db, lemma_id=lemma.lemma_id, due_immediately=True, source="reading_intake",
        )
        db.commit()
        now = datetime.now(timezone.utc)
        due = ulk.acquisition_next_due
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        # Due either now or in the very recent past (within the second
        # of the function call). Crucially NOT the 4h Box-1 delay.
        assert due <= now
        assert (now - due) < timedelta(minutes=1)


def test_start_acquisition_redirects_variants_to_canonical(tmp_db):
    """A variant must end up scheduling on its canonical, not on itself."""
    with tmp_db() as db:
        canonical = _seed_lemma(db, form="C", bare="c")
        variant = _seed_lemma(db, form="V", bare="v", canonical=canonical.lemma_id)
        db.commit()

        ulk = start_acquisition(db, lemma_id=variant.lemma_id, source="reading_intake")
        db.commit()
        assert ulk.lemma_id == canonical.lemma_id
        # No ULK should exist for the variant
        variant_ulks = (
            db.query(UserLemmaKnowledge).filter_by(lemma_id=variant.lemma_id).all()
        )
        assert variant_ulks == []


def test_start_acquisition_idempotent_for_acquiring(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        ulk1 = start_acquisition(db, lemma_id=lemma.lemma_id, source="reading_intake")
        db.commit()
        original_started_at = ulk1.acquisition_started_at
        ulk2 = start_acquisition(db, lemma_id=lemma.lemma_id, source="study")
        db.commit()
        assert ulk1.id == ulk2.id
        # Did not reset the timer
        assert ulk2.acquisition_started_at == original_started_at


def test_start_acquisition_does_not_demote_known(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="known",
            fsrs_card_json={"due": "2030-01-01T00:00:00+00:00"},
            source="test",
        )
        db.add(ulk)
        db.commit()
        returned = start_acquisition(db, lemma_id=lemma.lemma_id, source="reading_intake")
        db.commit()
        assert returned.knowledge_state == "known"
        # FSRS card preserved
        assert returned.fsrs_card_json is not None


def test_daily_cap_routes_overflow_to_encountered(tmp_db, monkeypatch):
    """Once the daily intro cap is hit, additional lemmas should stay
    encountered rather than promote to acquiring. Uses ``source='study'``
    because cap-exempt sources (``reading_intake`` / ``leech_reintro``) bypass
    the cap by design."""
    monkeypatch.setattr(acquisition_service, "DAILY_INTRO_CAP", 2)
    monkeypatch.setattr(
        acquisition_service, "_recovery_mode_intro_budget",
        lambda db, now, today_start, language_code=None: 2,
    )

    with tmp_db() as db:
        for i in range(3):
            lemma = _seed_lemma(db, form=f"w{i}", bare=f"w{i}")
            start_acquisition(db, lemma_id=lemma.lemma_id, source="study")
            db.commit()

        states = [u.knowledge_state for u in db.query(UserLemmaKnowledge).order_by(UserLemmaKnowledge.id).all()]
        assert states[:2] == ["acquiring", "acquiring"]
        assert states[2] == "encountered"


def test_reading_intake_bypasses_daily_cap(tmp_db, monkeypatch):
    """User reading-screen red taps must always enrol in acquiring, even when
    the cap is exhausted. The 'I don't know this' signal is data — capturing
    it is separate from how the scheduler paces practice. Regression for the
    2026-05-20 incident where 23/28 first-day red taps silently downgraded."""
    monkeypatch.setattr(acquisition_service, "DAILY_INTRO_CAP", 1)
    monkeypatch.setattr(
        acquisition_service, "_recovery_mode_intro_budget",
        lambda db, now, today_start, language_code=None: 0,
    )

    with tmp_db() as db:
        for i in range(5):
            lemma = _seed_lemma(db, form=f"r{i}", bare=f"r{i}")
            start_acquisition(
                db,
                lemma_id=lemma.lemma_id,
                source="reading_intake",
                due_immediately=True,
            )
            db.commit()

        states = [u.knowledge_state for u in db.query(UserLemmaKnowledge).all()]
        assert states == ["acquiring"] * 5


def test_cap_exempt_intros_dont_consume_budget(tmp_db, monkeypatch):
    """A reading_intake tap must not eat into the cap quota that gates other
    sources. Otherwise a heavy reading session would lock out study/auto-intro
    flows for the rest of the day."""
    monkeypatch.setattr(acquisition_service, "DAILY_INTRO_CAP", 2)
    monkeypatch.setattr(
        acquisition_service, "_recovery_mode_intro_budget",
        lambda db, now, today_start, language_code=None: 2,
    )

    with tmp_db() as db:
        # 10 exempt acquisitions burn no cap budget
        for i in range(10):
            lemma = _seed_lemma(db, form=f"rx{i}", bare=f"rx{i}")
            start_acquisition(db, lemma_id=lemma.lemma_id, source="reading_intake")
            db.commit()
        # Both non-exempt acquisitions still fit under the cap of 2
        for i in range(2):
            lemma = _seed_lemma(db, form=f"sx{i}", bare=f"sx{i}")
            start_acquisition(db, lemma_id=lemma.lemma_id, source="study")
            db.commit()

        acquiring = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.knowledge_state == "acquiring")
            .count()
        )
        assert acquiring == 12


def test_daily_cap_is_per_language(tmp_db, monkeypatch):
    """The daily intro cap is scoped per language: exhausting Greek's budget
    must not block Latin acquisitions. Regression for the cross-language
    pacing leakage found in the 2026-05-25 Latin audit — UserLemmaKnowledge
    carries no language_code, so an unscoped count mixed el + la into one cap."""
    monkeypatch.setattr(acquisition_service, "DAILY_INTRO_CAP", 2)
    monkeypatch.setattr(
        acquisition_service, "_recovery_mode_intro_budget",
        lambda db, now, today_start, language_code=None: 2,
    )

    with tmp_db() as db:
        # Exhaust the Greek cap (2): third Greek word overflows to encountered.
        for i in range(3):
            lemma = _seed_lemma(db, form=f"g{i}", bare=f"g{i}", language_code="el")
            start_acquisition(db, lemma_id=lemma.lemma_id, source="study")
            db.commit()
        # Latin starts with a fresh budget despite Greek being exhausted.
        for i in range(2):
            lemma = _seed_lemma(db, form=f"l{i}", bare=f"l{i}", language_code="la")
            start_acquisition(db, lemma_id=lemma.lemma_id, source="study")
            db.commit()

        def _states(lang):
            return [
                u.knowledge_state
                for u in (
                    db.query(UserLemmaKnowledge)
                    .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
                    .filter(Lemma.language_code == lang)
                    .order_by(UserLemmaKnowledge.id)
                    .all()
                )
            ]

        assert _states("el") == ["acquiring", "acquiring", "encountered"]
        assert _states("la") == ["acquiring", "acquiring"]


def test_tier0_first_correct_graduates_immediately(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        start_acquisition(db, lemma_id=lemma.lemma_id, due_immediately=True, source="test")
        db.commit()
        result = submit_acquisition_review(db, lemma_id=lemma.lemma_id, rating_int=3)
        assert result["graduated"] is True
        assert result["new_state"] == "learning"
        ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).one()
        assert ulk.acquisition_box is None
        assert ulk.graduated_at is not None
        assert ulk.fsrs_card_json is not None


def test_collateral_first_correct_does_not_graduate_or_advance_before_due(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        ulk = start_acquisition(
            db,
            lemma_id=lemma.lemma_id,
            due_immediately=False,
            source="collateral",
        )
        original_due = ulk.acquisition_next_due
        db.commit()

        result = submit_acquisition_review(db, lemma_id=lemma.lemma_id, rating_int=3)

        assert result["graduated"] is not True
        assert result["new_state"] == "acquiring"
        assert result["acquisition_box"] == 1
        ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).one()
        assert ulk.fsrs_card_json is None
        assert ulk.acquisition_box == 1
        assert ulk.acquisition_next_due.replace(tzinfo=timezone.utc) == original_due.replace(tzinfo=timezone.utc)
        log = db.query(ReviewLog).filter_by(lemma_id=lemma.lemma_id).one()
        assert log.fsrs_log_json["collateral_fast_graduation_blocked"] is True
        assert log.fsrs_log_json["early_review_advancement_blocked"] is True


def test_due_collateral_first_correct_can_advance_but_not_fast_graduate(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        ulk = start_acquisition(
            db,
            lemma_id=lemma.lemma_id,
            due_immediately=False,
            source="collateral",
        )
        ulk.acquisition_next_due = datetime.now(timezone.utc) - timedelta(minutes=5)
        db.commit()

        result = submit_acquisition_review(db, lemma_id=lemma.lemma_id, rating_int=3)

        assert result["graduated"] is not True
        assert result["new_state"] == "acquiring"
        assert result["acquisition_box"] == 2
        ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).one()
        assert ulk.fsrs_card_json is None
        assert ulk.acquisition_box == 2


def test_rating_again_resets_to_box1(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        # Pre-existing acquiring at Box 2
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="acquiring",
            acquisition_box=2,
            acquisition_next_due=datetime.now(timezone.utc) - timedelta(hours=1),
            acquisition_started_at=datetime.now(timezone.utc),
            entered_acquiring_at=datetime.now(timezone.utc),
            source="test",
            times_seen=1,
            times_correct=1,
        )
        db.add(ulk)
        db.commit()
        result = submit_acquisition_review(db, lemma_id=lemma.lemma_id, rating_int=1)
        assert result["acquisition_box"] == 1
        assert result["graduated"] is not True


def test_rating_hard_stays_in_same_box(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="acquiring",
            acquisition_box=2,
            acquisition_next_due=datetime.now(timezone.utc) - timedelta(minutes=5),
            acquisition_started_at=datetime.now(timezone.utc),
            entered_acquiring_at=datetime.now(timezone.utc),
            source="test",
            times_seen=2,
            times_correct=1,
        )
        db.add(ulk)
        db.commit()
        result = submit_acquisition_review(db, lemma_id=lemma.lemma_id, rating_int=2)
        assert result["acquisition_box"] == 2


def test_box_advances_on_good_when_due(tmp_db):
    """Box 1 → 2 via Good. (Tier 0 graduation kicks in on first review for a
    truly-new word; here we simulate a word that already has prior failure
    history so the times_seen>0 path is taken.)"""
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="acquiring",
            acquisition_box=1,
            acquisition_next_due=datetime.now(timezone.utc) - timedelta(minutes=5),
            acquisition_started_at=datetime.now(timezone.utc),
            entered_acquiring_at=datetime.now(timezone.utc),
            source="test",
            times_seen=1,
            times_correct=0,
        )
        db.add(ulk)
        db.commit()
        result = submit_acquisition_review(db, lemma_id=lemma.lemma_id, rating_int=3)
        # 50% accuracy after this review (1 correct of 2 seen) — no Tier 2 grad
        assert result["graduated"] is not True
        assert result["acquisition_box"] == 2


def test_tier1_perfect_accuracy_graduates(tmp_db):
    """100% accuracy after 3+ reviews → graduate from any box."""
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="acquiring",
            acquisition_box=1,
            acquisition_next_due=datetime.now(timezone.utc) - timedelta(minutes=5),
            acquisition_started_at=datetime.now(timezone.utc),
            entered_acquiring_at=datetime.now(timezone.utc),
            source="test",
            times_seen=2,
            times_correct=2,
        )
        db.add(ulk)
        db.commit()
        result = submit_acquisition_review(db, lemma_id=lemma.lemma_id, rating_int=3)
        # 3 seen, 3 correct → 100% → Tier 1 grad
        assert result["graduated"] is True
        assert result["new_state"] == "learning"


# ─── Intro-card working-memory gate (Hard Invariant #12) ────────────────


def test_recent_intro_blocks_tier0_first_correct_grad(tmp_db):
    """First-correct graduation must NOT fire within FAST_GRAD_INTRO_GAP of
    the intro card — three correct answers seconds after seeing the card is
    working memory, not learning.
    """
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        start_acquisition(db, lemma_id=lemma.lemma_id, due_immediately=True, source="test")
        ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).one()
        ulk.experiment_intro_shown_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        db.commit()

        result = submit_acquisition_review(db, lemma_id=lemma.lemma_id, rating_int=3)

        assert result["graduated"] is not True
        assert result["new_state"] == "acquiring"
        assert result["acquisition_box"] == 1
        # Reschedule comes via FAST_INTRO_RETRY_INTERVAL, not BOX_INTERVALS[2]
        ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).one()
        next_due = ulk.acquisition_next_due.replace(tzinfo=timezone.utc) if ulk.acquisition_next_due.tzinfo is None else ulk.acquisition_next_due
        gap = next_due - datetime.now(timezone.utc)
        # Should be ~FAST_INTRO_RETRY_INTERVAL (30 min), not BOX_INTERVALS[2] (1 day)
        assert gap < FAST_INTRO_RETRY_INTERVAL + timedelta(seconds=10)
        assert gap > FAST_INTRO_RETRY_INTERVAL - timedelta(seconds=10)


def test_intro_older_than_window_allows_tier0_grad(tmp_db):
    """Once the working-memory window has passed, Tier 0 graduation works."""
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        start_acquisition(db, lemma_id=lemma.lemma_id, due_immediately=True, source="test")
        ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).one()
        ulk.experiment_intro_shown_at = (
            datetime.now(timezone.utc) - FAST_GRAD_INTRO_GAP - timedelta(seconds=1)
        )
        db.commit()

        result = submit_acquisition_review(db, lemma_id=lemma.lemma_id, rating_int=3)
        assert result["graduated"] is True


def test_recent_intro_blocks_box1_to_box2_advancement(tmp_db):
    """When a Box-1 word with prior failure history gets a correct review
    inside the intro window, it stays in Box 1 (FAST_INTRO_RETRY) instead of
    advancing to Box 2 — the encoding phase isn't done yet.
    """
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        now = datetime.now(timezone.utc)
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="acquiring",
            acquisition_box=1,
            acquisition_next_due=now - timedelta(minutes=5),
            acquisition_started_at=now,
            entered_acquiring_at=now,
            experiment_intro_shown_at=now - timedelta(minutes=2),
            source="test",
            times_seen=1,
            times_correct=0,
        )
        db.add(ulk)
        db.commit()

        result = submit_acquisition_review(db, lemma_id=lemma.lemma_id, rating_int=3)
        assert result["graduated"] is not True
        assert result["acquisition_box"] == 1


def test_recent_intro_blocks_tier1_perfect_grad(tmp_db):
    """100% accuracy + 3 reviews shouldn't graduate when all three correct
    answers happened inside the working-memory window — same anti-pattern,
    just spread across reviews instead of a single first-correct.
    """
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        now = datetime.now(timezone.utc)
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="acquiring",
            acquisition_box=1,
            acquisition_next_due=now - timedelta(minutes=5),
            acquisition_started_at=now,
            entered_acquiring_at=now,
            experiment_intro_shown_at=now - timedelta(minutes=2),
            source="test",
            times_seen=2,
            times_correct=2,
        )
        db.add(ulk)
        db.commit()

        # This is the third correct review → would normally trigger Tier 1.
        result = submit_acquisition_review(db, lemma_id=lemma.lemma_id, rating_int=3)
        assert result["graduated"] is not True


def test_acquisition_due_filter(tmp_db):
    with tmp_db() as db:
        future = _seed_lemma(db, form="future", bare="future")
        past = _seed_lemma(db, form="past", bare="past")
        db.add(UserLemmaKnowledge(
            lemma_id=future.lemma_id, knowledge_state="acquiring",
            acquisition_box=1, acquisition_next_due=datetime.now(timezone.utc) + timedelta(hours=1),
            acquisition_started_at=datetime.now(timezone.utc),
            entered_acquiring_at=datetime.now(timezone.utc),
            source="test",
        ))
        db.add(UserLemmaKnowledge(
            lemma_id=past.lemma_id, knowledge_state="acquiring",
            acquisition_box=1, acquisition_next_due=datetime.now(timezone.utc) - timedelta(minutes=5),
            acquisition_started_at=datetime.now(timezone.utc),
            entered_acquiring_at=datetime.now(timezone.utc),
            source="test",
        ))
        db.commit()
        due = get_acquisition_due(db)
        assert past.lemma_id in due
        assert future.lemma_id not in due


def test_acquisition_due_filter_skips_noncontent_lemmas(tmp_db):
    with tmp_db() as db:
        content = _seed_lemma(db, form="content", bare="content")
        function_word = _seed_lemma(
            db,
            form="εξαιτίας",
            bare="εξαιτιας",
            word_category="function_word",
        )
        now = datetime.now(timezone.utc)
        for lemma in (content, function_word):
            db.add(UserLemmaKnowledge(
                lemma_id=lemma.lemma_id,
                knowledge_state="acquiring",
                acquisition_box=1,
                acquisition_next_due=now - timedelta(minutes=5),
                acquisition_started_at=now,
                entered_acquiring_at=now,
                source="test",
            ))
        db.commit()

        due = get_acquisition_due(db)
        stats = get_acquisition_stats(db)
        assert due == [content.lemma_id]
        assert stats["total_acquiring"] == 1
        assert stats["due_now"] == 1


def test_acquisition_stats(tmp_db):
    with tmp_db() as db:
        for box in (1, 2, 3):
            lemma = _seed_lemma(db, form=f"b{box}", bare=f"b{box}")
            db.add(UserLemmaKnowledge(
                lemma_id=lemma.lemma_id, knowledge_state="acquiring",
                acquisition_box=box, acquisition_next_due=datetime.now(timezone.utc),
                acquisition_started_at=datetime.now(timezone.utc),
                entered_acquiring_at=datetime.now(timezone.utc),
                source="test",
            ))
        db.commit()
        stats = get_acquisition_stats(db)
        assert stats["total_acquiring"] == 3
        assert stats["box_1"] == 1
        assert stats["box_2"] == 1
        assert stats["box_3"] == 1
        assert stats["due_now"] == 3


def test_falls_back_to_fsrs_for_non_acquiring(tmp_db):
    """If submit_acquisition_review is called for a learning-state ULK, it
    must delegate to FSRS rather than mutate the box. Defensive bug-net."""
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        from app.services.fsrs_service import create_new_card
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="learning",
            fsrs_card_json=create_new_card(),
            source="test",
        )
        db.add(ulk)
        db.commit()
        result = submit_acquisition_review(db, lemma_id=lemma.lemma_id, rating_int=3)
        # FSRS path: no acquisition_box in result
        assert result.get("acquisition_box") in (None, 0)
        # ReviewLog row should have is_acquisition=False
        log = db.query(ReviewLog).filter_by(lemma_id=lemma.lemma_id).one()
        assert log.is_acquisition is False
