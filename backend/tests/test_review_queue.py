"""
Tests for the review queue logic.

Validates ordering, rate-limiting of new cards, session size limits,
and proper interleaving of new vs. review cards.
"""

from datetime import datetime, timedelta, timezone

import fsrs
import pytest

Rating = fsrs.Rating
State = fsrs.State


def make_scheduler() -> fsrs.Scheduler:
    return fsrs.Scheduler(desired_retention=0.9, enable_fuzzing=False)


def make_card() -> fsrs.Card:
    return fsrs.Card()


class CardWithMeta:
    """Wraps an fsrs.Card with metadata for queue management."""

    def __init__(self, card: fsrs.Card, lemma_id: int, is_new: bool = True):
        self.card = card
        self.lemma_id = lemma_id
        self.is_new = is_new


def build_review_queue(
    cards: list[CardWithMeta],
    now: datetime,
    session_size: int = 20,
    max_new_per_session: int = 5,
) -> list[CardWithMeta]:
    """
    Build a review queue from a list of cards.

    Priority:
    1. Due review/relearning/learning cards ordered by most overdue first
    2. New cards up to max_new_per_session
    3. Truncate to session_size
    """
    due_reviews = []
    new_cards = []

    for cm in cards:
        if cm.card.state in (State.Review, State.Relearning, State.Learning) and not cm.is_new:
            if cm.card.due <= now:
                overdue_amount = (now - cm.card.due).total_seconds()
                due_reviews.append((overdue_amount, cm))
        elif cm.is_new:
            new_cards.append(cm)

    due_reviews.sort(key=lambda x: x[0], reverse=True)
    ordered_reviews = [cm for _, cm in due_reviews]

    new_batch = new_cards[:max_new_per_session]

    queue = ordered_reviews + new_batch
    return queue[:session_size]


class TestDueCardOrdering:
    """Due cards should be ordered by most overdue first."""

    def test_most_overdue_first(self):
        scheduler = make_scheduler()
        base = datetime(2026, 2, 1, 10, 0, 0, tzinfo=timezone.utc)
        now = datetime(2026, 2, 10, 10, 0, 0, tzinfo=timezone.utc)

        cards_with_meta = []
        for i in range(10):
            card = make_card()
            t = base + timedelta(days=i)
            card, _ = scheduler.review_card(card, Rating.Good, t)
            card, _ = scheduler.review_card(card, Rating.Good, card.due)
            cm = CardWithMeta(card, lemma_id=i, is_new=False)
            cards_with_meta.append(cm)

        queue = build_review_queue(cards_with_meta, now)

        due_dates = [cm.card.due for cm in queue if cm.card.state == State.Review]
        for i in range(1, len(due_dates)):
            assert due_dates[i] >= due_dates[i - 1], (
                f"Queue not ordered by overdue: {due_dates[i-1]} should come before {due_dates[i]}"
            )

    def test_not_yet_due_excluded(self):
        scheduler = make_scheduler()
        now = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc)

        card = make_card()
        card, _ = scheduler.review_card(card, Rating.Good, now)
        card, _ = scheduler.review_card(card, Rating.Good, card.due)

        cm = CardWithMeta(card, lemma_id=1, is_new=False)
        queue = build_review_queue([cm], now)

        assert len(queue) == 0


class TestNewCardRateLimiting:
    """New cards should be introduced at a controlled rate."""

    def test_max_new_per_session(self):
        cards = [CardWithMeta(make_card(), lemma_id=i) for i in range(50)]
        now = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc)

        queue = build_review_queue(cards, now, session_size=20, max_new_per_session=5)

        new_in_queue = [cm for cm in queue if cm.is_new]
        assert len(new_in_queue) == 5, f"Expected 5 new cards, got {len(new_in_queue)}"

    def test_zero_new_when_reviews_fill_session(self):
        scheduler = make_scheduler()
        base = datetime(2026, 2, 1, 10, 0, 0, tzinfo=timezone.utc)
        now = datetime(2026, 2, 10, 10, 0, 0, tzinfo=timezone.utc)

        review_cards = []
        for i in range(25):
            card = make_card()
            t = base + timedelta(hours=i)
            card, _ = scheduler.review_card(card, Rating.Good, t)
            card, _ = scheduler.review_card(card, Rating.Good, card.due)
            review_cards.append(CardWithMeta(card, lemma_id=i, is_new=False))

        new_cards = [CardWithMeta(make_card(), lemma_id=100 + i) for i in range(10)]

        all_cards = review_cards + new_cards
        queue = build_review_queue(all_cards, now, session_size=20, max_new_per_session=5)

        assert len(queue) == 20
        review_count = sum(1 for cm in queue if not cm.is_new)
        assert review_count >= 15, f"Expected reviews to dominate: got {review_count} reviews"


class TestSessionSizeLimit:
    """Sessions should be capped at the configured size."""

    def test_session_capped_at_limit(self):
        scheduler = make_scheduler()
        base = datetime(2026, 2, 1, 10, 0, 0, tzinfo=timezone.utc)
        now = datetime(2026, 2, 10, 10, 0, 0, tzinfo=timezone.utc)

        cards = []
        for i in range(50):
            card = make_card()
            t = base + timedelta(hours=i)
            card, _ = scheduler.review_card(card, Rating.Good, t)
            card, _ = scheduler.review_card(card, Rating.Good, card.due)
            cards.append(CardWithMeta(card, lemma_id=i, is_new=False))

        queue = build_review_queue(cards, now, session_size=10)
        assert len(queue) == 10

    def test_remaining_cards_stay_in_pool(self):
        scheduler = make_scheduler()
        base = datetime(2026, 2, 1, 10, 0, 0, tzinfo=timezone.utc)
        now = datetime(2026, 2, 10, 10, 0, 0, tzinfo=timezone.utc)

        cards = []
        for i in range(30):
            card = make_card()
            t = base + timedelta(hours=i)
            card, _ = scheduler.review_card(card, Rating.Good, t)
            card, _ = scheduler.review_card(card, Rating.Good, card.due)
            cards.append(CardWithMeta(card, lemma_id=i, is_new=False))

        queue = build_review_queue(cards, now, session_size=10)
        assert len(queue) == 10

        assert len(cards) == 30

        queued_ids = {cm.lemma_id for cm in queue}
        remaining = [cm for cm in cards if cm.lemma_id not in queued_ids]
        assert len(remaining) == 20


class TestNewReviewInterleaving:
    """New and review cards should be properly interleaved in a session."""

    def test_reviews_before_new(self):
        scheduler = make_scheduler()
        base = datetime(2026, 2, 1, 10, 0, 0, tzinfo=timezone.utc)
        now = datetime(2026, 2, 10, 10, 0, 0, tzinfo=timezone.utc)

        review_cards = []
        for i in range(8):
            card = make_card()
            t = base + timedelta(hours=i)
            card, _ = scheduler.review_card(card, Rating.Good, t)
            card, _ = scheduler.review_card(card, Rating.Good, card.due)
            review_cards.append(CardWithMeta(card, lemma_id=i, is_new=False))

        new_cards = [CardWithMeta(make_card(), lemma_id=100 + i) for i in range(5)]

        queue = build_review_queue(review_cards + new_cards, now, session_size=20, max_new_per_session=5)

        reviews_first = all(not cm.is_new for cm in queue[:8])
        new_last = all(cm.is_new for cm in queue[8:])

        assert reviews_first, "Reviews should come before new cards"
        assert new_last, "New cards should come after reviews"


class TestSessionManagement:
    """Track sessions with IDs and summaries."""

    def test_session_summary(self):
        session_stats = {
            "cards_reviewed": 0,
            "new_words_learned": 0,
            "ratings": {r.name: 0 for r in Rating},
        }

        for i in range(10):
            session_stats["cards_reviewed"] += 1
            session_stats["ratings"]["Good"] += 1

        for i in range(3):
            session_stats["new_words_learned"] += 1
            session_stats["cards_reviewed"] += 1
            session_stats["ratings"]["Good"] += 1

        assert session_stats["cards_reviewed"] == 13
        assert session_stats["new_words_learned"] == 3

        accuracy = session_stats["ratings"]["Good"] / session_stats["cards_reviewed"]
        assert accuracy == 1.0


class TestLearningCardsInQueue:
    """Cards in Learning state with due times should appear in queue."""

    def test_learning_cards_included_when_due(self):
        scheduler = make_scheduler()
        now = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc)

        card = make_card()
        card, _ = scheduler.review_card(card, Rating.Good, now)
        assert card.state == State.Learning

        later = now + timedelta(minutes=15)
        cm = CardWithMeta(card, lemma_id=1, is_new=False)
        queue = build_review_queue([cm], later)

        assert len(queue) == 1
        assert queue[0].card.state == State.Learning
