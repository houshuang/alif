"""Leech service: detection, suspension, graduated reintroduction."""
from datetime import datetime, timedelta, timezone

from app.models import Lemma, ReviewLog, UserLemmaKnowledge
from app.services import leech_service
from app.services.fsrs_service import create_new_card
from app.services.leech_service import (
    LEECH_MIN_REVIEWS,
    REINTRO_DELAYS,
    check_and_manage_leeches,
    check_leech_reintroductions,
    check_single_word_leech,
    is_leech,
)


def _seed_lemma(db, *, form="βιβλίο", bare="βιβλιο", freq=None) -> Lemma:
    lemma = Lemma(
        language_code="el", lemma_form=form, lemma_bare=bare, source="test",
        frequency_rank=freq,
    )
    db.add(lemma)
    db.flush()
    return lemma


def _add_review_log(db, lemma_id: int, rating: int, *, ago_seconds: int = 0):
    db.add(ReviewLog(
        lemma_id=lemma_id,
        rating=rating,
        reviewed_at=datetime.now(timezone.utc) - timedelta(seconds=ago_seconds),
        review_mode="reading",
    ))


def test_is_leech_below_min_reviews_is_false(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id, knowledge_state="learning",
            fsrs_card_json=create_new_card(),
            times_seen=2, times_correct=0, source="test",
        )
        db.add(ulk)
        for _ in range(2):
            _add_review_log(db, lemma.lemma_id, rating=1)
        db.commit()
        # Below LEECH_MIN_REVIEWS → not flagged regardless of accuracy
        assert is_leech(ulk, db=db) is False


def test_check_single_word_leech_suspends_chronic_failure(tmp_db):
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id, knowledge_state="learning",
            fsrs_card_json=create_new_card(),
            times_seen=8, times_correct=1, source="test",
        )
        db.add(ulk)
        # 8 reviews, only 1 correct → 12.5% accuracy < 50%
        for i in range(8):
            _add_review_log(db, lemma.lemma_id, rating=3 if i == 0 else 1, ago_seconds=i)
        db.commit()

        suspended = check_single_word_leech(db, lemma.lemma_id)
        assert suspended is True
        db.refresh(ulk)
        assert ulk.knowledge_state == "suspended"
        assert ulk.leech_suspended_at is not None
        assert ulk.leech_count == 1
        assert ulk.acquisition_box is None


def test_sliding_window_lets_word_escape_leech(tmp_db):
    """Cumulative accuracy is bad but the LAST 8 reviews are all correct —
    the sliding window should override and not flag this as a leech."""
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id, knowledge_state="learning",
            fsrs_card_json=create_new_card(),
            times_seen=18, times_correct=8, source="test",
        )
        db.add(ulk)
        # 10 old failures, 8 recent successes — sliding window sees only the 8
        for i in range(10):
            _add_review_log(db, lemma.lemma_id, rating=1, ago_seconds=1000 + i)
        for i in range(8):
            _add_review_log(db, lemma.lemma_id, rating=3, ago_seconds=i)
        db.commit()
        assert is_leech(ulk, db=db) is False
        assert check_single_word_leech(db, lemma.lemma_id) is False


def test_check_and_manage_leeches_sweeps_multiple(tmp_db):
    with tmp_db() as db:
        bad_lemma = _seed_lemma(db, form="bad", bare="bad")
        good_lemma = _seed_lemma(db, form="good", bare="good")
        for lemma, times_correct in [(bad_lemma, 1), (good_lemma, 7)]:
            db.add(UserLemmaKnowledge(
                lemma_id=lemma.lemma_id, knowledge_state="learning",
                fsrs_card_json=create_new_card(),
                times_seen=8, times_correct=times_correct, source="test",
            ))
        for i in range(8):
            _add_review_log(db, bad_lemma.lemma_id, rating=3 if i == 0 else 1, ago_seconds=i)
            _add_review_log(db, good_lemma.lemma_id, rating=3 if i > 0 else 1, ago_seconds=i)
        db.commit()

        suspended = check_and_manage_leeches(db)
        assert bad_lemma.lemma_id in suspended
        assert good_lemma.lemma_id not in suspended


def test_reintroduction_respects_cooldown(tmp_db):
    """A word suspended 1 day ago should NOT be eligible (1st cooldown=3 days)."""
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="suspended",
            leech_suspended_at=datetime.now(timezone.utc) - timedelta(days=1),
            leech_count=1, source="test",
            times_seen=8, times_correct=1,
        )
        db.add(ulk)
        db.commit()
        reintroduced = check_leech_reintroductions(db)
        assert lemma.lemma_id not in reintroduced
        db.refresh(ulk)
        assert ulk.knowledge_state == "suspended"


def test_reintroduction_after_cooldown_routes_to_acquisition(tmp_db):
    """Suspended 4 days ago + first suspension (3d cooldown) → eligible.

    Origin source is preserved through leech cycles — the word's provenance
    doesn't change just because it failed and recovered. Same policy as Alif.
    """
    with tmp_db() as db:
        lemma = _seed_lemma(db)
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="suspended",
            leech_suspended_at=datetime.now(timezone.utc) - timedelta(days=4),
            leech_count=1, source="reading_intake",
            times_seen=8, times_correct=1,
        )
        db.add(ulk)
        db.commit()
        reintroduced = check_leech_reintroductions(db)
        assert lemma.lemma_id in reintroduced
        db.refresh(ulk)
        assert ulk.knowledge_state == "acquiring"
        assert ulk.acquisition_box == 1
        # Origin preserved: high-priority source 'reading_intake' is not
        # overwritten by the weaker 'leech_reintro' marker.
        assert ulk.source == "reading_intake"
        # Stats preserved through the suspend → reintroduce cycle
        assert ulk.times_seen == 8
        assert ulk.times_correct == 1


def test_low_priority_lemma_has_longer_cooldown(tmp_db):
    """Low-frequency lemma (rank > 5000) gets 4x the standard delay."""
    with tmp_db() as db:
        lemma = _seed_lemma(db, freq=10000)  # rare word
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="suspended",
            leech_suspended_at=datetime.now(timezone.utc) - timedelta(days=4),
            leech_count=1, source="test",
            times_seen=8, times_correct=1,
        )
        db.add(ulk)
        db.commit()
        # First suspension normally = 3d. Low-priority × 4 = 12d. So 4 days isn't enough.
        reintroduced = check_leech_reintroductions(db)
        assert lemma.lemma_id not in reintroduced


def test_graduated_cooldowns_match_constants(tmp_db):
    """Sanity: 3d / 7d / 14d for leech_count 0 / 1 / 2+."""
    assert REINTRO_DELAYS[0] == timedelta(days=3)
    assert REINTRO_DELAYS[1] == timedelta(days=7)
    assert REINTRO_DELAYS[2] == timedelta(days=14)
