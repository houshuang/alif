from datetime import datetime, timezone, timedelta
import pytest
from app.models import Root, Lemma, UserLemmaKnowledge, ReviewLog
from app.routers.stats import _estimate_cefr, _calculate_streak, CEFR_THRESHOLDS


def _make_lemma(db, i, state="known", days_ago=0):
    lemma = Lemma(
        lemma_ar=f"word{i}",
        lemma_ar_bare=f"word{i}",
        gloss_en=f"meaning{i}",
    )
    db.add(lemma)
    db.flush()
    reviewed_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    knowledge = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state=state,
        fsrs_card_json={"due": reviewed_at.isoformat()},
        last_reviewed=reviewed_at,
        times_seen=3,
        times_correct=2,
    )
    db.add(knowledge)
    return lemma


def _make_review(db, lemma_id, rating=3, days_ago=0, fsrs_state: str | None = None):
    reviewed_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    fsrs_log_json = {"state": fsrs_state} if fsrs_state else None
    review = ReviewLog(
        lemma_id=lemma_id,
        rating=rating,
        reviewed_at=reviewed_at,
        response_ms=1500,
        session_id="test",
        fsrs_log_json=fsrs_log_json,
    )
    db.add(review)
    return review


class TestCEFREstimate:
    def test_pre_a1(self):
        result = _estimate_cefr(50)
        assert result.level == "Pre-A1"
        assert result.next_level == "A1"
        assert result.words_to_next == 250

    def test_a1(self):
        result = _estimate_cefr(350)
        assert result.level == "A1"
        assert result.next_level == "A1+"

    def test_a2(self):
        result = _estimate_cefr(900)
        assert result.level == "A2"
        assert result.next_level == "A2+"

    def test_b1(self):
        result = _estimate_cefr(2500)
        assert result.level == "B1"

    def test_b2(self):
        result = _estimate_cefr(5000)
        assert result.level == "B2"

    def test_c1(self):
        result = _estimate_cefr(9000)
        assert result.level == "C1"

    def test_c2(self):
        result = _estimate_cefr(15000)
        assert result.level == "C2"
        assert result.next_level is None

    def test_reading_coverage_zero(self):
        result = _estimate_cefr(0)
        assert result.reading_coverage_pct == 0.0

    def test_reading_coverage_a2(self):
        result = _estimate_cefr(1000)
        assert 50 < result.reading_coverage_pct < 80

    def test_reading_coverage_b2(self):
        result = _estimate_cefr(5000)
        assert result.reading_coverage_pct == 90.0


class TestStreak:
    def test_empty(self):
        current, longest = _calculate_streak([])
        assert current == 0
        assert longest == 0

    def test_today_only(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        current, longest = _calculate_streak([today])
        assert current == 1
        assert longest == 1

    def test_consecutive_days(self):
        today = datetime.now(timezone.utc).date()
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(5)]
        current, longest = _calculate_streak(dates)
        assert current == 5
        assert longest == 5

    def test_gap_breaks_streak(self):
        today = datetime.now(timezone.utc).date()
        dates = [
            today.strftime("%Y-%m-%d"),
            (today - timedelta(days=1)).strftime("%Y-%m-%d"),
            (today - timedelta(days=3)).strftime("%Y-%m-%d"),
            (today - timedelta(days=4)).strftime("%Y-%m-%d"),
            (today - timedelta(days=5)).strftime("%Y-%m-%d"),
        ]
        current, longest = _calculate_streak(dates)
        assert current == 2
        assert longest == 3


class TestStatsAPI:
    def test_basic_stats(self, client, db_session):
        _make_lemma(db_session, 1, "known")
        _make_lemma(db_session, 2, "learning")
        _make_lemma(db_session, 3, "new")
        db_session.commit()

        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_words"] == 3
        assert data["known"] == 1
        assert data["learning"] == 1
        assert data["new"] == 1

    def test_due_today_uses_due_timestamps(self, client, db_session):
        # Due now (known) should count.
        _make_lemma(db_session, 1, "known", days_ago=1)
        # Future due (learning/new) should NOT count.
        _make_lemma(db_session, 2, "learning", days_ago=-1)
        _make_lemma(db_session, 3, "new", days_ago=-1)
        db_session.commit()

        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["due_today"] == 1

    def test_analytics_endpoint(self, client, db_session):
        lemma = _make_lemma(db_session, 1, "known")
        _make_review(db_session, lemma.lemma_id, rating=3, days_ago=0)
        _make_review(db_session, lemma.lemma_id, rating=3, days_ago=1)
        db_session.commit()

        resp = client.get("/api/stats/analytics")
        assert resp.status_code == 200
        data = resp.json()
        assert "stats" in data
        assert "pace" in data
        assert "cefr" in data
        assert "daily_history" in data
        assert data["cefr"]["level"] == "Pre-A1"
        assert data["pace"]["total_study_days"] >= 1

    def test_cefr_endpoint(self, client, db_session):
        for i in range(350):
            _make_lemma(db_session, i, "known", days_ago=i % 30)
        db_session.commit()

        resp = client.get("/api/stats/cefr")
        assert resp.status_code == 200
        data = resp.json()
        assert data["level"] == "A1"
        assert data["known_words"] == 350
        assert data["next_level"] == "A1+"
        assert data["reading_coverage_pct"] > 0

    def test_analytics_with_history(self, client, db_session):
        lemma = _make_lemma(db_session, 1, "known")
        for d in range(10):
            _make_review(db_session, lemma.lemma_id, rating=3, days_ago=d)
        db_session.commit()

        resp = client.get("/api/stats/analytics?days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["daily_history"]) >= 1
        assert data["pace"]["reviews_per_day_7d"] > 0

    def test_words_learned_uses_first_known_transition(self, client, db_session):
        lemma = _make_lemma(db_session, 1, "known")
        # First transition to known was 10 days ago.
        _make_review(db_session, lemma.lemma_id, rating=3, days_ago=10, fsrs_state="known")
        # A later known-state review should not count as a newly learned word.
        _make_review(db_session, lemma.lemma_id, rating=3, days_ago=0, fsrs_state="known")
        db_session.commit()

        resp = client.get("/api/stats/analytics?days=30")
        assert resp.status_code == 200
        data = resp.json()
        # Within last 7 days, this word should not be counted as newly learned.
        assert data["pace"]["words_per_day_7d"] == 0.0

    def test_daily_history_does_not_double_count_known_transitions(self, client, db_session):
        lemma = _make_lemma(db_session, 1, "known")
        _make_review(db_session, lemma.lemma_id, rating=3, days_ago=2, fsrs_state="known")
        _make_review(db_session, lemma.lemma_id, rating=3, days_ago=0, fsrs_state="known")
        db_session.commit()

        resp = client.get("/api/stats/analytics?days=30")
        assert resp.status_code == 200
        data = resp.json()
        total_learned = sum(point["words_learned"] for point in data["daily_history"])
        assert total_learned == 1

    def test_daily_history_includes_known_without_transition_in_baseline(self, client, db_session):
        # Legacy known word with no fsrs state-transition logs.
        _make_lemma(db_session, 1, "known")
        # Known word with a logged transition today.
        lemma2 = _make_lemma(db_session, 2, "known")
        _make_review(db_session, lemma2.lemma_id, rating=3, days_ago=0, fsrs_state="known")
        db_session.commit()

        resp = client.get("/api/stats/analytics?days=30")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["daily_history"]) >= 1
        latest = data["daily_history"][-1]
        assert latest["words_learned"] == 1
        assert latest["cumulative_known"] == 2
