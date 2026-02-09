"""Tests for deep analytics endpoint."""

import json
from datetime import datetime, timezone, timedelta

import pytest

from app.models import (
    Lemma,
    ReviewLog,
    Root,
    Sentence,
    SentenceReviewLog,
    UserLemmaKnowledge,
)
from app.services.fsrs_service import create_new_card


def _make_card(stability_days=30.0, due_offset_hours=-1):
    card = create_new_card()
    card["stability"] = stability_days
    due = datetime.now(timezone.utc) + timedelta(hours=due_offset_hours)
    card["due"] = due.isoformat()
    return card


def _seed_word(db, lemma_id, arabic, english, state="known",
               stability=30.0, due_hours=-1, times_seen=10, times_correct=8,
               root_id=None):
    lemma = Lemma(
        lemma_id=lemma_id,
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        pos="noun",
        gloss_en=english,
        root_id=root_id,
    )
    db.add(lemma)
    db.flush()

    knowledge = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state=state,
        fsrs_card_json=_make_card(stability, due_hours),
        introduced_at=datetime.now(timezone.utc) - timedelta(days=30),
        last_reviewed=datetime.now(timezone.utc) - timedelta(hours=1),
        times_seen=times_seen,
        times_correct=times_correct,
        source="study",
    )
    db.add(knowledge)
    db.flush()
    return lemma, knowledge


class TestDeepAnalyticsEndpoint:
    def test_empty_db(self, client):
        resp = client.get("/api/stats/deep-analytics")
        assert resp.status_code == 200
        data = resp.json()
        # Buckets returned but all with count 0
        assert all(b["count"] == 0 for b in data["stability_distribution"])
        assert data["struggling_words"] == []
        assert data["retention_7d"]["total_reviews"] == 0
        assert data["root_coverage"]["total_roots"] == 0

    def test_stability_distribution(self, db_session, client):
        _seed_word(db_session, 1, "كتاب", "book", stability=0.01)
        _seed_word(db_session, 2, "قلم", "pen", stability=2.0)
        _seed_word(db_session, 3, "بيت", "house", stability=15.0)
        db_session.commit()

        resp = client.get("/api/stats/deep-analytics")
        data = resp.json()
        buckets = {b["label"]: b["count"] for b in data["stability_distribution"]}
        assert buckets.get("<1h", 0) == 1  # 0.01 days ~= 14 min
        assert buckets.get("1-3d", 0) == 1  # 2.0 days
        assert buckets.get("7-30d", 0) == 1  # 15 days

    def test_struggling_words(self, db_session, client):
        _seed_word(db_session, 1, "صعب", "difficult", times_seen=5, times_correct=0)
        _seed_word(db_session, 2, "سهل", "easy", times_seen=5, times_correct=4)
        db_session.commit()

        resp = client.get("/api/stats/deep-analytics")
        data = resp.json()
        struggling = data["struggling_words"]
        assert len(struggling) == 1
        assert struggling[0]["lemma_id"] == 1
        assert struggling[0]["lemma_ar"] == "صعب"
        assert struggling[0]["times_seen"] == 5

    def test_retention_stats(self, db_session, client):
        _seed_word(db_session, 1, "كتاب", "book")
        now = datetime.now(timezone.utc)
        for i in range(5):
            db_session.add(ReviewLog(
                lemma_id=1,
                rating=3 if i < 4 else 1,
                review_mode="reading",
                reviewed_at=now - timedelta(days=i),
            ))
        db_session.commit()

        resp = client.get("/api/stats/deep-analytics")
        data = resp.json()
        r7d = data["retention_7d"]
        assert r7d["total_reviews"] == 5
        assert r7d["correct_reviews"] == 4
        assert r7d["retention_pct"] == 80.0

    def test_comprehension_breakdown(self, db_session, client):
        # Need a sentence to FK reference
        sent = Sentence(
            id=1, arabic_text="test", english_translation="test",
            target_lemma_id=1,
        )
        db_session.add(sent)
        db_session.flush()

        now = datetime.now(timezone.utc)
        for signal in ["understood", "understood", "partial", "no_idea"]:
            db_session.add(SentenceReviewLog(
                sentence_id=1,
                comprehension=signal,
                session_id="test-session",
                reviewed_at=now - timedelta(days=1),
            ))
        db_session.commit()

        resp = client.get("/api/stats/deep-analytics")
        data = resp.json()
        comp = data["comprehension_7d"]
        assert comp["understood"] == 2
        assert comp["partial"] == 1
        assert comp["no_idea"] == 1
        assert comp["total"] == 4

    def test_root_coverage(self, db_session, client):
        r1 = Root(root="كتب", core_meaning_en="writing")
        r2 = Root(root="قرأ", core_meaning_en="reading")
        db_session.add_all([r1, r2])
        db_session.flush()

        _seed_word(db_session, 1, "كتاب", "book", state="known", root_id=r1.root_id)
        _seed_word(db_session, 2, "كاتب", "writer", state="known", root_id=r1.root_id)
        _seed_word(db_session, 3, "قارئ", "reader", state="learning", root_id=r2.root_id)
        _seed_word(db_session, 4, "قراءة", "reading", state="new", root_id=r2.root_id)
        db_session.commit()

        resp = client.get("/api/stats/deep-analytics")
        data = resp.json()
        rc = data["root_coverage"]
        assert rc["total_roots"] == 2
        assert rc["roots_with_known"] >= 1

    def test_recent_sessions(self, db_session, client):
        # Need sentences to FK reference
        for i in range(3):
            db_session.add(Sentence(
                id=i + 1, arabic_text=f"test {i}", english_translation=f"test {i}",
                target_lemma_id=1,
            ))
        db_session.flush()

        now = datetime.now(timezone.utc)
        for i, signal in enumerate(["understood", "partial", "no_idea"]):
            db_session.add(SentenceReviewLog(
                sentence_id=i + 1,
                comprehension=signal,
                session_id="sess-1",
                reviewed_at=now - timedelta(minutes=i),
                response_ms=2000 + i * 100,
            ))
        db_session.commit()

        resp = client.get("/api/stats/deep-analytics")
        data = resp.json()
        sessions = data["recent_sessions"]
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "sess-1"
        assert sessions[0]["sentence_count"] == 3
