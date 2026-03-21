"""Tests for stuck mnemonic detection logic (find_stuck_hook_words)."""

from datetime import datetime, timedelta, timezone

from app.models import Lemma, ReviewLog, UserLemmaKnowledge
from app.services.memory_hooks import (
    STUCK_MAX_ACCURACY,
    STUCK_MIN_REVIEWS,
    STUCK_RECENT_WINDOW,
    STUCK_REGEN_COOLDOWN_DAYS,
    find_stuck_hook_words,
)


def _create_lemma(db, arabic="كتاب", english="book", hooks=None):
    lemma = Lemma(
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        gloss_en=english,
        pos="noun",
        memory_hooks_json=hooks,
    )
    db.add(lemma)
    db.flush()
    return lemma


def _add_ulk(db, lemma_id, state="acquiring", times_seen=10, times_correct=3, **kwargs):
    ulk = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state=state,
        times_seen=times_seen,
        times_correct=times_correct,
        **kwargs,
    )
    db.add(ulk)
    db.flush()
    return ulk


def _add_reviews(db, lemma_id: int, ratings: list[int]):
    now = datetime.now(timezone.utc)
    for i, rating in enumerate(ratings):
        db.add(ReviewLog(
            lemma_id=lemma_id,
            rating=rating,
            reviewed_at=now - timedelta(hours=len(ratings) - i),
        ))
    db.flush()


# --- find_stuck_hook_words ---


def test_finds_stuck_word_with_bad_accuracy(db_session):
    """Word with hooks, acquiring state, low accuracy should be found."""
    hooks = {"mnemonic": "A CAT reads a BOOK", "cognates": [], "collocations": []}
    lemma = _create_lemma(db_session, hooks=hooks)
    _add_ulk(db_session, lemma.lemma_id, state="acquiring", times_seen=6, times_correct=1)
    _add_reviews(db_session, lemma.lemma_id, [1, 1, 3, 1, 1, 1])
    db_session.commit()

    stuck = find_stuck_hook_words(db_session)
    assert len(stuck) == 1
    assert stuck[0][0].lemma_id == lemma.lemma_id
    assert stuck[0][2] < STUCK_MAX_ACCURACY


def test_skips_word_without_hooks(db_session):
    """Word without memory_hooks_json should not be found."""
    lemma = _create_lemma(db_session, hooks=None)
    _add_ulk(db_session, lemma.lemma_id, state="acquiring", times_seen=10, times_correct=1)
    _add_reviews(db_session, lemma.lemma_id, [1, 1, 1, 1, 1, 1, 1, 1, 1, 1])
    db_session.commit()

    stuck = find_stuck_hook_words(db_session)
    assert len(stuck) == 0


def test_skips_word_with_good_accuracy(db_session):
    """Word with hooks but good recent accuracy should not be found."""
    hooks = {"mnemonic": "test mnemonic", "cognates": [], "collocations": []}
    lemma = _create_lemma(db_session, hooks=hooks)
    _add_ulk(db_session, lemma.lemma_id, state="acquiring", times_seen=10, times_correct=8)
    _add_reviews(db_session, lemma.lemma_id, [3, 3, 3, 3, 3, 3, 3, 1, 3, 3])
    db_session.commit()

    stuck = find_stuck_hook_words(db_session)
    assert len(stuck) == 0


def test_skips_known_state(db_session):
    """Word in 'known' state (not acquiring/lapsed) should not be found."""
    hooks = {"mnemonic": "test mnemonic", "cognates": [], "collocations": []}
    lemma = _create_lemma(db_session, hooks=hooks)
    _add_ulk(db_session, lemma.lemma_id, state="known", times_seen=10, times_correct=2)
    _add_reviews(db_session, lemma.lemma_id, [1, 1, 3, 1, 1, 1, 1, 3, 1, 1])
    db_session.commit()

    stuck = find_stuck_hook_words(db_session)
    assert len(stuck) == 0


def test_includes_lapsed_state(db_session):
    """Word in 'lapsed' state should be found."""
    hooks = {"mnemonic": "test mnemonic", "cognates": [], "collocations": []}
    lemma = _create_lemma(db_session, hooks=hooks)
    _add_ulk(db_session, lemma.lemma_id, state="lapsed", times_seen=8, times_correct=2)
    _add_reviews(db_session, lemma.lemma_id, [1, 1, 3, 1, 1, 1, 1, 1])
    db_session.commit()

    stuck = find_stuck_hook_words(db_session)
    assert len(stuck) == 1


def test_skips_too_few_reviews(db_session):
    """Word with fewer than STUCK_MIN_REVIEWS should not be found."""
    hooks = {"mnemonic": "test mnemonic", "cognates": [], "collocations": []}
    lemma = _create_lemma(db_session, hooks=hooks)
    _add_ulk(db_session, lemma.lemma_id, state="acquiring", times_seen=3, times_correct=0)
    _add_reviews(db_session, lemma.lemma_id, [1, 1, 1])
    db_session.commit()

    stuck = find_stuck_hook_words(db_session)
    assert len(stuck) == 0


def test_respects_cooldown(db_session):
    """Word regenerated recently should be skipped."""
    recent_regen = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    hooks = {
        "mnemonic": "test mnemonic",
        "cognates": [],
        "collocations": [],
        "regenerated_at": recent_regen,
        "regeneration_count": 1,
    }
    lemma = _create_lemma(db_session, hooks=hooks)
    _add_ulk(db_session, lemma.lemma_id, state="acquiring", times_seen=8, times_correct=1)
    _add_reviews(db_session, lemma.lemma_id, [1, 1, 1, 1, 1, 1, 1, 1])
    db_session.commit()

    stuck = find_stuck_hook_words(db_session)
    assert len(stuck) == 0


def test_cooldown_expired_is_eligible(db_session):
    """Word regenerated more than cooldown_days ago should be eligible."""
    old_regen = (datetime.now(timezone.utc) - timedelta(days=STUCK_REGEN_COOLDOWN_DAYS + 1)).isoformat()
    hooks = {
        "mnemonic": "old mnemonic",
        "cognates": [],
        "collocations": [],
        "regenerated_at": old_regen,
        "regeneration_count": 1,
    }
    lemma = _create_lemma(db_session, hooks=hooks)
    _add_ulk(db_session, lemma.lemma_id, state="acquiring", times_seen=8, times_correct=1)
    _add_reviews(db_session, lemma.lemma_id, [1, 1, 1, 1, 1, 1, 1, 1])
    db_session.commit()

    stuck = find_stuck_hook_words(db_session)
    assert len(stuck) == 1


def test_limit_respected(db_session):
    """Limit parameter caps the number of results."""
    hooks = {"mnemonic": "test", "cognates": [], "collocations": []}
    for i in range(5):
        lemma = _create_lemma(db_session, arabic=f"word{i}", english=f"meaning{i}", hooks=hooks)
        _add_ulk(db_session, lemma.lemma_id, state="acquiring", times_seen=6, times_correct=1)
        _add_reviews(db_session, lemma.lemma_id, [1, 1, 1, 1, 1, 1])
    db_session.commit()

    stuck = find_stuck_hook_words(db_session, limit=2)
    assert len(stuck) == 2


def test_sorted_by_accuracy_ascending(db_session):
    """Results should be sorted by accuracy ascending (worst first)."""
    hooks = {"mnemonic": "test", "cognates": [], "collocations": []}

    # Word A: 0% recent accuracy
    la = _create_lemma(db_session, arabic="wordA", english="a", hooks=hooks)
    _add_ulk(db_session, la.lemma_id, state="acquiring", times_seen=5, times_correct=0)
    _add_reviews(db_session, la.lemma_id, [1, 1, 1, 1, 1])

    # Word B: 20% recent accuracy (1/5)
    lb = _create_lemma(db_session, arabic="wordB", english="b", hooks=hooks)
    _add_ulk(db_session, lb.lemma_id, state="acquiring", times_seen=5, times_correct=1)
    _add_reviews(db_session, lb.lemma_id, [1, 3, 1, 1, 1])

    db_session.commit()

    stuck = find_stuck_hook_words(db_session)
    assert len(stuck) == 2
    assert stuck[0][0].lemma_id == la.lemma_id  # 0% first
    assert stuck[1][0].lemma_id == lb.lemma_id  # 20% second


def test_skips_variant_lemmas(db_session):
    """Variant lemmas (canonical_lemma_id set) should be skipped."""
    hooks = {"mnemonic": "test", "cognates": [], "collocations": []}
    canonical = _create_lemma(db_session, arabic="canonical", english="main", hooks=hooks)
    variant = _create_lemma(db_session, arabic="variant", english="var", hooks=hooks)
    variant.canonical_lemma_id = canonical.lemma_id
    db_session.flush()

    _add_ulk(db_session, variant.lemma_id, state="acquiring", times_seen=6, times_correct=1)
    _add_reviews(db_session, variant.lemma_id, [1, 1, 1, 1, 1, 1])
    db_session.commit()

    stuck = find_stuck_hook_words(db_session)
    assert len(stuck) == 0


def test_boundary_accuracy_not_stuck(db_session):
    """Exactly 50% accuracy should not be flagged (threshold is strict <50%)."""
    hooks = {"mnemonic": "test", "cognates": [], "collocations": []}
    lemma = _create_lemma(db_session, hooks=hooks)
    _add_ulk(db_session, lemma.lemma_id, state="acquiring", times_seen=6, times_correct=3)
    # 3 correct out of last 6 = 50%, but we only look at last STUCK_RECENT_WINDOW=5
    # So ratings: [1, 3, 1, 3, 3, 1] -> last 5 = [3, 1, 3, 3, 1] = 3/5 = 60%
    _add_reviews(db_session, lemma.lemma_id, [1, 3, 1, 3, 3, 1])
    db_session.commit()

    stuck = find_stuck_hook_words(db_session)
    assert len(stuck) == 0
