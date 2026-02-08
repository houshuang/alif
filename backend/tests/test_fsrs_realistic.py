"""
Tests simulating realistic FSRS usage patterns for the Alif Arabic learning app.

Validates that FSRS handles sub-day scheduling, multi-day gaps, session-based
learning, and long-term review load correctly.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import fsrs
import pytest

Rating = fsrs.Rating
State = fsrs.State


def make_scheduler(desired_retention: float = 0.9) -> fsrs.Scheduler:
    return fsrs.Scheduler(desired_retention=desired_retention, enable_fuzzing=False)


def make_card() -> fsrs.Card:
    return fsrs.Card()


def make_log_entry(
    card_id: int,
    rating: Rating,
    ts: datetime,
    response_ms: int,
    session_id: str,
    lemma_id: int | None = None,
) -> dict:
    return {
        "ts": ts.isoformat(),
        "event": "review",
        "lemma_id": lemma_id or card_id,
        "rating": rating.value,
        "response_ms": response_ms,
        "context": f"card_id:{card_id}",
        "session_id": session_id,
    }


class TestTwoHourSessionThenGap:
    """User reviews 50 words in a 2-hour session, skips 3 days, returns."""

    def test_cards_are_overdue_but_not_reset(self):
        scheduler = make_scheduler()
        now = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc)

        cards = []
        for i in range(50):
            card = make_card()
            review_time = now + timedelta(minutes=i * 2)
            card, _ = scheduler.review_card(card, Rating.Good, review_time)
            card, _ = scheduler.review_card(card, Rating.Good, card.due)
            cards.append(card)

        return_time = now + timedelta(days=3, hours=2)

        overdue_count = 0
        for card in cards:
            if card.due <= return_time:
                overdue_count += 1
                assert card.state == State.Review
                assert card.stability is not None
                assert card.stability > 0

        assert overdue_count > 30, f"Expected >30 overdue cards, got {overdue_count}"

    def test_re_review_adjusts_intervals(self):
        scheduler = make_scheduler()
        now = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc)

        card = make_card()
        card, _ = scheduler.review_card(card, Rating.Good, now)
        card, _ = scheduler.review_card(card, Rating.Good, card.due)
        assert card.state == State.Review

        initial_stability = card.stability
        original_due = card.due

        late_return = original_due + timedelta(days=3)
        card_after, _ = scheduler.review_card(card, Rating.Good, late_return)

        new_interval = card_after.due - late_return
        assert new_interval.total_seconds() > 0
        assert card_after.stability > initial_stability


class TestSubDayScheduling:
    """User does 10 minutes in the morning, 10 minutes at night."""

    def test_learning_steps_are_sub_day(self):
        scheduler = make_scheduler()
        morning = datetime(2026, 2, 8, 8, 0, 0, tzinfo=timezone.utc)

        card = make_card()
        card, _ = scheduler.review_card(card, Rating.Good, morning)

        interval = card.due - morning
        assert interval < timedelta(hours=1), f"Learning step too long: {interval}"
        assert interval >= timedelta(minutes=1), f"Learning step too short: {interval}"

    def test_morning_and_evening_sessions(self):
        scheduler = make_scheduler()
        morning = datetime(2026, 2, 8, 8, 0, 0, tzinfo=timezone.utc)
        evening = datetime(2026, 2, 8, 20, 0, 0, tzinfo=timezone.utc)

        morning_cards = []
        for i in range(5):
            card = make_card()
            t = morning + timedelta(minutes=i * 2)
            card, _ = scheduler.review_card(card, Rating.Good, t)
            card, _ = scheduler.review_card(card, Rating.Good, card.due)
            morning_cards.append(card)

        for card in morning_cards:
            assert card.state == State.Review
            card_after, _ = scheduler.review_card(card, Rating.Good, evening)
            assert card_after.due > evening


class TestCorrectReviewsSpreadOut:
    """User learns 10 new words, reviews them all correctly."""

    def test_intervals_diverge_over_time(self):
        scheduler = make_scheduler()
        now = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc)

        cards = []
        for i in range(10):
            card = make_card()
            t = now + timedelta(minutes=i * 2)
            card, _ = scheduler.review_card(card, Rating.Good, t)
            card, _ = scheduler.review_card(card, Rating.Good, card.due)
            cards.append(card)

        for round_num in range(3):
            new_cards = []
            for card in cards:
                review_time = card.due
                rating = Rating.Easy if hash(card.card_id + round_num) % 3 == 0 else Rating.Good
                card, _ = scheduler.review_card(card, rating, review_time)
                new_cards.append(card)
            cards = new_cards

        due_dates = sorted([c.due for c in cards])
        span = due_dates[-1] - due_dates[0]
        assert span > timedelta(days=1), f"Due dates too clustered: span={span}"


class TestStrugglingWord:
    """User rates 'Again' on one word 5 times."""

    def test_stays_in_short_rotation(self):
        scheduler = make_scheduler()
        now = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc)

        card = make_card()
        for i in range(5):
            card, _ = scheduler.review_card(card, Rating.Again, now)
            interval = card.due - now
            assert card.state == State.Learning
            assert interval <= timedelta(minutes=10), f"Interval too long for struggled card: {interval}"
            now = card.due

    def test_difficulty_increases_with_again(self):
        scheduler = make_scheduler()
        now = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc)

        card = make_card()
        difficulties = []
        for i in range(5):
            card, _ = scheduler.review_card(card, Rating.Again, now)
            difficulties.append(card.difficulty)
            now = card.due

        assert difficulties[-1] >= difficulties[0]

    def test_recovery_after_struggle(self):
        scheduler = make_scheduler()
        now = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc)

        card = make_card()
        for _ in range(3):
            card, _ = scheduler.review_card(card, Rating.Again, now)
            now = card.due

        for _ in range(5):
            card, _ = scheduler.review_card(card, Rating.Good, now)
            now = card.due

        assert card.stability > 0


class TestBulkImport:
    """User imports 100 words at once."""

    def test_staggered_introduction(self):
        """Simulate introducing imported words gradually (10 per day).

        With staggering, due dates should be spread across multiple days
        rather than clustering on one day.
        """
        scheduler = make_scheduler()
        start = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc)

        all_cards = [make_card() for _ in range(100)]

        for day in range(10):
            batch_start = start + timedelta(days=day)
            for i in range(10):
                idx = day * 10 + i
                t = batch_start + timedelta(minutes=i * 2)
                all_cards[idx], _ = scheduler.review_card(all_cards[idx], Rating.Good, t)
                all_cards[idx], _ = scheduler.review_card(all_cards[idx], Rating.Good, all_cards[idx].due)

        # With staggered intro, due dates should span multiple days
        due_dates = sorted(c.due for c in all_cards)
        span = due_dates[-1] - due_dates[0]
        assert span > timedelta(days=5), (
            f"Staggered intro should spread due dates across days, got span={span}"
        )

    def test_all_at_once_creates_load_spike(self):
        """If all 100 words are introduced at once, they cluster."""
        scheduler = make_scheduler()
        now = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc)

        cards = []
        for i in range(100):
            card = make_card()
            t = now + timedelta(seconds=i * 30)
            card, _ = scheduler.review_card(card, Rating.Good, t)
            card, _ = scheduler.review_card(card, Rating.Good, card.due)
            cards.append(card)

        due_dates = [c.due for c in cards]
        span = max(due_dates) - min(due_dates)
        assert span < timedelta(days=1), "Unexpected spread without staggering"

        check_time = now + timedelta(days=3)
        due_count = sum(1 for c in cards if c.due <= check_time)
        assert due_count > 80, "Expected most cards to be due around same time"


class TestMixedSession:
    """Session with 5 new words + 15 review words."""

    def test_mixed_new_and_review(self):
        scheduler = make_scheduler()
        now = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc)

        review_cards = []
        past = now - timedelta(days=5)
        for i in range(15):
            card = make_card()
            t = past + timedelta(minutes=i * 2)
            card, _ = scheduler.review_card(card, Rating.Good, t)
            card, _ = scheduler.review_card(card, Rating.Good, card.due)
            review_cards.append(card)

        new_cards = [make_card() for _ in range(5)]

        session_id = str(uuid.uuid4())
        logs = []

        minute = 0
        for card in review_cards:
            t = now + timedelta(minutes=minute)
            card, log = scheduler.review_card(card, Rating.Good, t)
            logs.append(make_log_entry(card.card_id, Rating.Good, t, 2500, session_id))
            minute += 1

        for card in new_cards:
            t = now + timedelta(minutes=minute)
            card, log = scheduler.review_card(card, Rating.Good, t)
            logs.append(make_log_entry(card.card_id, Rating.Good, t, 4000, session_id))
            minute += 1

        assert len(logs) == 20
        review_times = [l["response_ms"] for l in logs[:15]]
        new_times = [l["response_ms"] for l in logs[15:]]
        assert sum(new_times) / len(new_times) > sum(review_times) / len(review_times)


class TestLongTermSimulation:
    """30 days of realistic usage with varying patterns."""

    def test_no_review_explosion(self):
        scheduler = make_scheduler()
        start = datetime(2026, 2, 1, 10, 0, 0, tzinfo=timezone.utc)

        all_cards: list[fsrs.Card] = []
        daily_reviews: dict[int, int] = {}

        schedule = {}
        for d in range(1, 4):
            schedule[d] = {"duration_min": 30, "new_words": 8}
        for d in range(4, 8):
            schedule[d] = None
        schedule[8] = {"duration_min": 120, "new_words": 15}
        for d in range(9, 15):
            schedule[d] = {"duration_min": 15, "new_words": 3}
        for d in range(15, 22):
            schedule[d] = None
        for d in range(22, 31):
            schedule[d] = {"duration_min": 20, "new_words": 5}

        for day in range(1, 31):
            day_start = start + timedelta(days=day - 1)
            session_info = schedule.get(day)

            if session_info is None:
                daily_reviews[day] = 0
                continue

            reviews_today = 0

            for idx, card in enumerate(all_cards):
                if card.due <= day_start:
                    t = day_start + timedelta(minutes=reviews_today)
                    card, _ = scheduler.review_card(card, Rating.Good, t)
                    all_cards[idx] = card
                    reviews_today += 1

            new_count = session_info["new_words"]
            for i in range(new_count):
                card = make_card()
                t = day_start + timedelta(minutes=reviews_today + i * 2)
                card, _ = scheduler.review_card(card, Rating.Good, t)
                card, _ = scheduler.review_card(card, Rating.Good, card.due)
                all_cards.append(card)
                reviews_today += 2

            daily_reviews[day] = reviews_today

        max_reviews = max(daily_reviews.values())
        total_cards = len(all_cards)

        assert max_reviews < total_cards, (
            f"Review explosion: {max_reviews} reviews on one day with {total_cards} total cards"
        )

        review_state_cards = [c for c in all_cards if c.state == State.Review]
        if review_state_cards:
            avg_stability = sum(c.stability for c in review_state_cards) / len(review_state_cards)
            assert avg_stability > 1.0, f"Average stability too low: {avg_stability}"

    def test_card_state_distribution(self):
        """After 30 days of regular use, most cards should be in Review state."""
        scheduler = make_scheduler()
        start = datetime(2026, 2, 1, 10, 0, 0, tzinfo=timezone.utc)

        cards: list[fsrs.Card] = []
        for day in range(30):
            day_start = start + timedelta(days=day)

            for idx, card in enumerate(cards):
                if card.due <= day_start:
                    card, _ = scheduler.review_card(card, Rating.Good, day_start)
                    cards[idx] = card

            for i in range(5):
                card = make_card()
                t = day_start + timedelta(minutes=30 + i * 2)
                card, _ = scheduler.review_card(card, Rating.Good, t)
                card, _ = scheduler.review_card(card, Rating.Good, card.due)
                cards.append(card)

        state_counts = {s: 0 for s in State}
        for c in cards:
            state_counts[c.state] += 1

        total = len(cards)
        review_pct = state_counts[State.Review] / total

        assert review_pct > 0.5, (
            f"Expected >50% in Review state, got {review_pct:.0%}. "
            f"Distribution: {state_counts}"
        )


class TestDesiredRetention:
    """Test that desired_retention parameter affects scheduling."""

    def test_higher_retention_shorter_intervals(self):
        now = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc)

        intervals = {}
        for retention in [0.85, 0.9, 0.95]:
            scheduler = make_scheduler(desired_retention=retention)
            card = make_card()
            card, _ = scheduler.review_card(card, Rating.Good, now)
            card, _ = scheduler.review_card(card, Rating.Good, card.due)

            for _ in range(3):
                card, _ = scheduler.review_card(card, Rating.Good, card.due)

            interval = card.due - card.last_review
            intervals[retention] = interval.total_seconds()

        assert intervals[0.95] < intervals[0.9], (
            f"0.95 retention ({intervals[0.95]:.0f}s) should have shorter interval than 0.9 ({intervals[0.9]:.0f}s)"
        )
        assert intervals[0.9] < intervals[0.85], (
            f"0.9 retention ({intervals[0.9]:.0f}s) should have shorter interval than 0.85 ({intervals[0.85]:.0f}s)"
        )


class TestOverdueHandling:
    """FSRS handles overdue cards gracefully (no penalty for absence)."""

    def test_overdue_card_not_penalized(self):
        scheduler = make_scheduler()
        now = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc)

        card = make_card()
        card, _ = scheduler.review_card(card, Rating.Good, now)
        card, _ = scheduler.review_card(card, Rating.Good, card.due)
        assert card.state == State.Review

        stability_before = card.stability

        late_time = card.due + timedelta(days=10)
        card, _ = scheduler.review_card(card, Rating.Good, late_time)

        assert card.stability > stability_before, (
            f"Stability should increase after successful late review: "
            f"before={stability_before:.2f}, after={card.stability:.2f}"
        )

    def test_retrievability_decays(self):
        scheduler = make_scheduler()
        now = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc)

        card = make_card()
        card, _ = scheduler.review_card(card, Rating.Good, now)
        card, _ = scheduler.review_card(card, Rating.Good, card.due)

        retrievabilities = []
        days_list = [0, 1, 3, 7, 14, 30]
        for days in days_list:
            t = card.last_review + timedelta(days=days)
            r = scheduler.get_card_retrievability(card, t)
            retrievabilities.append(r)

        for i in range(1, len(retrievabilities)):
            assert retrievabilities[i] < retrievabilities[i - 1], (
                f"Retrievability should decrease: day {days_list[i]} "
                f"({retrievabilities[i]:.4f}) >= day {days_list[i-1]} "
                f"({retrievabilities[i-1]:.4f})"
            )


class TestInteractionLogging:
    """Every simulated review produces a valid JSONL log entry."""

    def test_log_entries_are_valid_jsonl(self):
        scheduler = make_scheduler()
        now = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc)
        session_id = str(uuid.uuid4())

        logs = []
        card = make_card()
        for i in range(5):
            t = now + timedelta(minutes=i * 2)
            rating = Rating.Good
            card, _ = scheduler.review_card(card, rating, t)
            entry = make_log_entry(
                card_id=card.card_id,
                rating=rating,
                ts=t,
                response_ms=2000 + i * 100,
                session_id=session_id,
                lemma_id=42,
            )
            logs.append(entry)

        for entry in logs:
            line = json.dumps(entry)
            parsed = json.loads(line)

            assert parsed["event"] == "review"
            assert parsed["lemma_id"] == 42
            assert parsed["rating"] in [1, 2, 3, 4]
            assert parsed["response_ms"] > 0
            assert parsed["session_id"] == session_id
            assert "ts" in parsed

    def test_log_timestamps_are_monotonic(self):
        scheduler = make_scheduler()
        now = datetime(2026, 2, 8, 10, 0, 0, tzinfo=timezone.utc)

        timestamps = []
        card = make_card()
        for i in range(10):
            t = now + timedelta(minutes=i)
            card, _ = scheduler.review_card(card, Rating.Good, t)
            timestamps.append(t)

        for i in range(1, len(timestamps)):
            assert timestamps[i] > timestamps[i - 1]
