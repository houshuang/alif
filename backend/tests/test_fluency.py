"""Tests for per-word fluency scoring based on response time."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Lemma, ReviewLog, UserLemmaKnowledge
from app.services.fluency_service import (
    MIN_REVIEWS_FOR_FLUENCY,
    _median,
    compute_fluency_batch,
    compute_word_fluency,
)


def _seed_lemma(db, lemma_id, arabic="كلمة", english="word"):
    lemma = Lemma(
        lemma_id=lemma_id,
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        pos="noun",
        gloss_en=english,
    )
    db.add(lemma)
    db.flush()
    return lemma


def _seed_reviews(db, lemma_id, response_times_ms, days_ago_start=0):
    """Create ReviewLog entries with given response times.

    Reviews are spaced 1 hour apart starting from days_ago_start days ago.
    """
    now = datetime.now(timezone.utc)
    for i, ms in enumerate(response_times_ms):
        review = ReviewLog(
            lemma_id=lemma_id,
            rating=3,
            reviewed_at=(now - timedelta(days=days_ago_start, hours=i)).replace(tzinfo=None),
            response_ms=ms,
            review_mode="reading",
        )
        db.add(review)
    db.flush()


class TestMedian:
    def test_odd_count(self):
        assert _median([3, 1, 2]) == 2.0

    def test_even_count(self):
        assert _median([1, 2, 3, 4]) == 2.5

    def test_single_value(self):
        assert _median([42]) == 42.0

    def test_empty_list(self):
        assert _median([]) == 0.0


class TestComputeWordFluency:
    def test_insufficient_reviews_returns_none(self, db_session):
        _seed_lemma(db_session, 1)
        # Only 2 reviews, below MIN_REVIEWS_FOR_FLUENCY (3)
        _seed_reviews(db_session, 1, [2000, 3000])
        db_session.commit()

        result = compute_word_fluency(db_session, 1)
        assert result is None

    def test_insufficient_global_reviews_returns_none(self, db_session):
        _seed_lemma(db_session, 1)
        # 3 reviews for word, but only 3 total (need 10 for global median)
        _seed_reviews(db_session, 1, [2000, 3000, 4000])
        db_session.commit()

        result = compute_word_fluency(db_session, 1)
        assert result is None

    def test_slow_word_returns_below_one(self, db_session):
        """A word with response times 2x the global average should score ~0.5."""
        # Set up 3 "normal" words with fast response times to establish global median
        for lid in range(1, 5):
            _seed_lemma(db_session, lid, arabic=f"word{lid}", english=f"word {lid}")

        # Normal words: median around 2000ms
        _seed_reviews(db_session, 1, [2000, 2100, 1900])
        _seed_reviews(db_session, 2, [1800, 2200, 2000])
        _seed_reviews(db_session, 3, [2100, 1900, 2000])

        # Slow word: median around 4000ms (2x global)
        _seed_reviews(db_session, 4, [4000, 4100, 3900])

        db_session.commit()

        fluency = compute_word_fluency(db_session, 4)
        assert fluency is not None
        # Global median ~2000, word median ~4000, ratio ~0.5
        assert fluency < 0.7

    def test_fast_word_returns_above_one(self, db_session):
        """A word with response times below global average should score >1.0."""
        for lid in range(1, 5):
            _seed_lemma(db_session, lid, arabic=f"word{lid}", english=f"word {lid}")

        # Normal words: median around 3000ms
        _seed_reviews(db_session, 1, [3000, 3100, 2900])
        _seed_reviews(db_session, 2, [2800, 3200, 3000])
        _seed_reviews(db_session, 3, [3100, 2900, 3000])

        # Fast word: median around 1500ms (0.5x global)
        _seed_reviews(db_session, 4, [1500, 1400, 1600])

        db_session.commit()

        fluency = compute_word_fluency(db_session, 4)
        assert fluency is not None
        assert fluency > 1.0

    def test_lookback_window_excludes_old_reviews(self, db_session):
        _seed_lemma(db_session, 1)
        # 3 reviews but all > 14 days old
        _seed_reviews(db_session, 1, [2000, 3000, 4000], days_ago_start=20)
        db_session.commit()

        result = compute_word_fluency(db_session, 1, lookback_days=14)
        assert result is None

    def test_zero_response_ms_ignored(self, db_session):
        _seed_lemma(db_session, 1)
        # Some reviews with 0 ms should be filtered out
        _seed_reviews(db_session, 1, [0, 0, 2000, 3000, 4000])
        db_session.commit()

        # Only 3 valid reviews (the 0s are filtered), still need 10 global
        result = compute_word_fluency(db_session, 1)
        assert result is None  # not enough global data


class TestComputeFluencyBatch:
    def test_empty_input(self, db_session):
        result = compute_fluency_batch(db_session, set())
        assert result == {}

    def test_batch_matches_individual(self, db_session):
        """Batch computation should give same results as individual calls."""
        for lid in range(1, 6):
            _seed_lemma(db_session, lid, arabic=f"word{lid}", english=f"word {lid}")

        # Create enough reviews for global median (need 10+)
        _seed_reviews(db_session, 1, [2000, 2100, 1900, 2000])
        _seed_reviews(db_session, 2, [1800, 2200, 2000, 1900])
        _seed_reviews(db_session, 3, [2100, 1900, 2000, 2100])
        _seed_reviews(db_session, 4, [4000, 4100, 3900, 4000])  # slow
        _seed_reviews(db_session, 5, [1000, 1100, 900, 1000])   # fast

        db_session.commit()

        batch = compute_fluency_batch(db_session, {1, 2, 3, 4, 5})

        # All words should have scores (4+ reviews each, 20 total)
        assert len(batch) == 5
        # Word 4 should be slowest (lowest fluency)
        assert batch[4] < batch[1]
        # Word 5 should be fastest (highest fluency)
        assert batch[5] > batch[1]

    def test_words_below_min_reviews_omitted(self, db_session):
        """Words with fewer than MIN_REVIEWS_FOR_FLUENCY reviews should be missing."""
        for lid in range(1, 5):
            _seed_lemma(db_session, lid, arabic=f"word{lid}", english=f"word {lid}")

        # 3 words with enough reviews
        _seed_reviews(db_session, 1, [2000, 2100, 1900, 2000])
        _seed_reviews(db_session, 2, [1800, 2200, 2000, 1900])
        _seed_reviews(db_session, 3, [2100, 1900, 2000, 2100])
        # Word 4 has only 2 reviews
        _seed_reviews(db_session, 4, [4000, 4100])

        db_session.commit()

        batch = compute_fluency_batch(db_session, {1, 2, 3, 4})

        assert 4 not in batch
        assert len(batch) == 3

    def test_insufficient_global_returns_empty(self, db_session):
        """If total reviews < 10, batch should return empty."""
        _seed_lemma(db_session, 1)
        _seed_reviews(db_session, 1, [2000, 3000, 4000])
        db_session.commit()

        result = compute_fluency_batch(db_session, {1})
        assert result == {}
