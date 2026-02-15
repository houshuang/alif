"""Tests for client_review_id idempotency and bulk sync."""

from datetime import datetime, timezone, timedelta

import pytest

from app.models import (
    Lemma, UserLemmaKnowledge, Sentence, SentenceWord,
    ReviewLog, SentenceReviewLog, Story,
)
import app.services.sentence_review_service as sentence_review_service
import app.services.story_service as story_service_module
from app.services.fsrs_service import create_new_card, submit_review
from app.services.sentence_review_service import submit_sentence_review


def _make_card(stability_days=30.0, due_offset_hours=-1):
    card = create_new_card()
    card["stability"] = stability_days
    due = datetime.now(timezone.utc) + timedelta(hours=due_offset_hours)
    card["due"] = due.isoformat()
    return card


def _seed_word(db, lemma_id, arabic, english, with_card=True):
    lemma = Lemma(
        lemma_id=lemma_id,
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        pos="noun",
        gloss_en=english,
    )
    db.add(lemma)
    db.flush()

    if with_card:
        knowledge = UserLemmaKnowledge(
            lemma_id=lemma_id,
            knowledge_state="learning",
            fsrs_card_json=_make_card(),
            introduced_at=datetime.now(timezone.utc) - timedelta(days=10),
            last_reviewed=datetime.now(timezone.utc) - timedelta(hours=1),
            times_seen=5,
            times_correct=3,
            source="study",
        )
        db.add(knowledge)
        db.flush()
    return lemma


def _seed_sentence(db, sentence_id, arabic, english, target_lemma_id, word_ids):
    sent = Sentence(
        id=sentence_id,
        arabic_text=arabic,
        arabic_diacritized=arabic,
        english_translation=english,
        target_lemma_id=target_lemma_id,
    )
    db.add(sent)
    db.flush()

    for pos, lid in enumerate(word_ids):
        sw = SentenceWord(
            sentence_id=sentence_id,
            position=pos,
            surface_form=f"word_{pos}",
            lemma_id=lid,
        )
        db.add(sw)
    db.flush()
    return sent


class TestSubmitReviewIdempotency:
    def test_duplicate_client_review_id_returns_duplicate(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        db_session.commit()

        r1 = submit_review(
            db_session, lemma_id=1, rating_int=3,
            client_review_id="rev-001",
        )
        assert "duplicate" not in r1

        r2 = submit_review(
            db_session, lemma_id=1, rating_int=3,
            client_review_id="rev-001",
        )
        assert r2.get("duplicate") is True

    def test_fsrs_state_unchanged_on_duplicate(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        db_session.commit()

        submit_review(
            db_session, lemma_id=1, rating_int=3,
            client_review_id="rev-002",
        )

        k_after_first = (
            db_session.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id == 1)
            .first()
        )
        state_after_first = k_after_first.knowledge_state
        card_after_first = k_after_first.fsrs_card_json

        submit_review(
            db_session, lemma_id=1, rating_int=1,
            client_review_id="rev-002",
        )

        db_session.refresh(k_after_first)
        assert k_after_first.knowledge_state == state_after_first
        assert k_after_first.fsrs_card_json == card_after_first

    def test_only_one_review_log_for_duplicate(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        db_session.commit()

        submit_review(
            db_session, lemma_id=1, rating_int=3,
            client_review_id="rev-003",
        )
        submit_review(
            db_session, lemma_id=1, rating_int=3,
            client_review_id="rev-003",
        )

        logs = db_session.query(ReviewLog).filter(
            ReviewLog.client_review_id == "rev-003"
        ).all()
        assert len(logs) == 1


class TestSentenceReviewIdempotency:
    def test_duplicate_sentence_review_returns_duplicate(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_sentence(db_session, 1, "الكتاب", "the book",
                       target_lemma_id=1, word_ids=[1])
        db_session.commit()

        r1 = submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="understood",
            client_review_id="sent-001",
        )
        assert "duplicate" not in r1

        r2 = submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="understood",
            client_review_id="sent-001",
        )
        assert r2.get("duplicate") is True
        assert r2["word_results"] == []

    def test_only_one_sentence_log_for_duplicate(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_sentence(db_session, 1, "الكتاب", "the book",
                       target_lemma_id=1, word_ids=[1])
        db_session.commit()

        submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="understood",
            client_review_id="sent-002",
        )
        submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="understood",
            client_review_id="sent-002",
        )

        logs = db_session.query(SentenceReviewLog).filter(
            SentenceReviewLog.client_review_id == "sent-002"
        ).all()
        assert len(logs) == 1

    def test_word_only_duplicate_is_idempotent(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        db_session.commit()

        r1 = submit_sentence_review(
            db_session,
            sentence_id=None,
            primary_lemma_id=1,
            comprehension_signal="partial",
            missed_lemma_ids=[1],
            client_review_id="sent-word-001",
        )
        assert "duplicate" not in r1

        r2 = submit_sentence_review(
            db_session,
            sentence_id=None,
            primary_lemma_id=1,
            comprehension_signal="partial",
            missed_lemma_ids=[1],
            client_review_id="sent-word-001",
        )
        assert r2.get("duplicate") is True
        assert r2["word_results"] == []

        logs = db_session.query(ReviewLog).filter(
            ReviewLog.client_review_id == "sent-word-001"
        ).all()
        assert len(logs) == 1

    def test_sentence_retry_resumes_without_duplicate_word_credit(self, db_session, monkeypatch):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy")
        _seed_sentence(db_session, 1, "الولد الكتاب", "the boy the book",
                       target_lemma_id=1, word_ids=[2, 1])
        db_session.commit()

        real_submit_review = sentence_review_service.submit_review
        calls = {"count": 0}

        def flaky_submit_review(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("simulated mid-sentence failure")
            return real_submit_review(*args, **kwargs)

        monkeypatch.setattr(sentence_review_service, "submit_review", flaky_submit_review)
        with pytest.raises(RuntimeError):
            submit_sentence_review(
                db_session,
                sentence_id=1,
                primary_lemma_id=1,
                comprehension_signal="understood",
                client_review_id="sent-retry-001",
            )

        monkeypatch.setattr(sentence_review_service, "submit_review", real_submit_review)
        resumed = submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="understood",
            client_review_id="sent-retry-001",
        )
        assert len(resumed["word_results"]) == 1
        assert resumed["word_results"][0]["lemma_id"] in {1, 2}

        assert (
            db_session.query(ReviewLog)
            .filter(ReviewLog.client_review_id == "sent-retry-001:1")
            .count()
        ) == 1
        assert (
            db_session.query(ReviewLog)
            .filter(ReviewLog.client_review_id == "sent-retry-001:2")
            .count()
        ) == 1
        assert (
            db_session.query(SentenceReviewLog)
            .filter(SentenceReviewLog.client_review_id == "sent-retry-001")
            .count()
        ) == 1


class TestBulkSyncEndpoint:
    def test_mixed_items(self, client, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy")
        _seed_sentence(db_session, 1, "الكتاب", "the book",
                       target_lemma_id=1, word_ids=[1])
        db_session.commit()

        resp = client.post("/api/review/sync", json={
            "reviews": [
                {
                    "type": "sentence",
                    "client_review_id": "sync-s1",
                    "payload": {
                        "sentence_id": 1,
                        "primary_lemma_id": 1,
                        "comprehension_signal": "understood",
                        "session_id": "sync-sess",
                    },
                },
            ]
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["status"] == "ok"

    def test_bulk_sync_handles_duplicates(self, client, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_sentence(db_session, 1, "الكتاب", "the book",
                       target_lemma_id=1, word_ids=[1])
        db_session.commit()

        # First sync
        client.post("/api/review/sync", json={
            "reviews": [
                {
                    "type": "sentence",
                    "client_review_id": "sync-dup1",
                    "payload": {
                        "sentence_id": 1,
                        "primary_lemma_id": 1,
                        "comprehension_signal": "understood",
                    },
                },
            ]
        })

        # Second sync with same client_review_id
        resp = client.post("/api/review/sync", json={
            "reviews": [
                {
                    "type": "sentence",
                    "client_review_id": "sync-dup1",
                    "payload": {
                        "sentence_id": 1,
                        "primary_lemma_id": 1,
                        "comprehension_signal": "understood",
                    },
                },
            ]
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"][0]["status"] == "duplicate"

    def test_bulk_sync_word_only_sentence_duplicate(self, client, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        db_session.commit()

        first = client.post("/api/review/sync", json={
            "reviews": [
                {
                    "type": "sentence",
                    "client_review_id": "sync-word-only-dup",
                    "payload": {
                        "sentence_id": None,
                        "primary_lemma_id": 1,
                        "comprehension_signal": "partial",
                        "missed_lemma_ids": [1],
                    },
                },
            ]
        })
        assert first.status_code == 200
        assert first.json()["results"][0]["status"] == "ok"

        second = client.post("/api/review/sync", json={
            "reviews": [
                {
                    "type": "sentence",
                    "client_review_id": "sync-word-only-dup",
                    "payload": {
                        "sentence_id": None,
                        "primary_lemma_id": 1,
                        "comprehension_signal": "partial",
                        "missed_lemma_ids": [1],
                    },
                },
            ]
        })
        assert second.status_code == 200
        assert second.json()["results"][0]["status"] == "duplicate"

    def test_bulk_sync_unknown_type(self, client, db_session):
        resp = client.post("/api/review/sync", json={
            "reviews": [
                {
                    "type": "unknown",
                    "client_review_id": "sync-err1",
                    "payload": {},
                },
            ]
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"][0]["status"] == "error"
        assert "Unknown type" in data["results"][0]["error"]

    def test_offline_replay_duplicate_and_story_retry_recovery(self, client, db_session, monkeypatch):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy")
        _seed_word(db_session, 3, "بيت", "house")
        _seed_sentence(db_session, 1, "الولد الكتاب", "the boy the book",
                       target_lemma_id=1, word_ids=[2, 1])
        db_session.commit()

        story_one = client.post("/api/stories/import", json={
            "arabic_text": "الولد في البيت",
            "title": "Manual Story",
        })
        assert story_one.status_code == 200
        story_one_id = story_one.json()["id"]

        story_retry = client.post("/api/stories/import", json={
            "arabic_text": "الولد في البيت",
            "title": "Retry Story",
        })
        assert story_retry.status_code == 200
        story_retry_id = story_retry.json()["id"]

        queued = [
            {
                "type": "sentence",
                "client_review_id": "manual-sync-sentence-1",
                "payload": {
                    "sentence_id": 1,
                    "primary_lemma_id": 1,
                    "comprehension_signal": "understood",
                    "session_id": "offline-session-1",
                    "review_mode": "reading",
                },
            },
        ]

        first_sync = client.post("/api/review/sync", json={"reviews": queued})
        assert first_sync.status_code == 200
        assert [r["status"] for r in first_sync.json()["results"]] == ["ok"]

        replay_sync = client.post("/api/review/sync", json={"reviews": queued})
        assert replay_sync.status_code == 200
        assert [r["status"] for r in replay_sync.json()["results"]] == ["duplicate"]

        assert (
            db_session.query(SentenceReviewLog)
            .filter(SentenceReviewLog.client_review_id == "manual-sync-sentence-1")
            .count()
        ) == 1
        assert (
            db_session.query(ReviewLog)
            .filter(ReviewLog.client_review_id == "manual-sync-sentence-1:1")
            .count()
        ) == 1
        assert (
            db_session.query(ReviewLog)
            .filter(ReviewLog.client_review_id == "manual-sync-sentence-1:2")
            .count()
        ) == 1

        complete_first = client.post(f"/api/stories/{story_one_id}/complete", json={
            "looked_up_lemma_ids": [2],
            "reading_time_ms": 14500,
        })
        assert complete_first.status_code == 200
        first_payload = complete_first.json()
        assert first_payload["status"] == "completed"
        # 3 words: ولد (looked up=again), بيت (good), في (encountered, no FSRS)
        assert first_payload["words_reviewed"] == 3
        assert first_payload["good_count"] == 1
        assert first_payload["again_count"] == 1

        complete_replay = client.post(f"/api/stories/{story_one_id}/complete", json={
            "looked_up_lemma_ids": [2],
            "reading_time_ms": 14500,
        })
        assert complete_replay.status_code == 200
        replay_payload = complete_replay.json()
        assert replay_payload["duplicate"] is True
        assert replay_payload["words_reviewed"] == 0

        assert (
            db_session.query(ReviewLog)
            .filter(ReviewLog.client_review_id.like(f"story:{story_one_id}:complete:%"))
            .count()
        ) == 2

        real_submit_review = story_service_module.submit_review
        calls = {"count": 0}

        def flaky_submit_review(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("simulated mid-story failure")
            return real_submit_review(*args, **kwargs)

        monkeypatch.setattr(story_service_module, "submit_review", flaky_submit_review)
        with pytest.raises(RuntimeError):
            client.post(f"/api/stories/{story_retry_id}/complete", json={
                "looked_up_lemma_ids": [2],
                "reading_time_ms": 9000,
            })

        monkeypatch.setattr(story_service_module, "submit_review", real_submit_review)
        retry_story = db_session.query(Story).filter(Story.id == story_retry_id).first()
        assert retry_story is not None
        assert retry_story.status == "active"

        retry_success = client.post(f"/api/stories/{story_retry_id}/complete", json={
            "looked_up_lemma_ids": [2],
            "reading_time_ms": 9100,
        })
        assert retry_success.status_code == 200
        retry_payload = retry_success.json()
        assert retry_payload["status"] == "completed"
        assert retry_payload["words_reviewed"] == 3
        assert retry_payload["good_count"] == 1
        assert retry_payload["again_count"] == 1

        assert (
            db_session.query(ReviewLog)
            .filter(ReviewLog.client_review_id.like(f"story:{story_retry_id}:complete:%"))
            .count()
        ) == 2
        db_session.refresh(retry_story)
        assert retry_story.status == "completed"
