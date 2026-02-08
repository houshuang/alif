"""Tests for client_review_id idempotency and bulk sync."""

from datetime import datetime, timezone, timedelta

import pytest

from app.models import (
    Lemma, UserLemmaKnowledge, Sentence, SentenceWord,
    ReviewLog, SentenceReviewLog,
)
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
                {
                    "type": "legacy",
                    "client_review_id": "sync-l1",
                    "payload": {
                        "lemma_id": 2,
                        "rating": 3,
                        "session_id": "sync-sess",
                    },
                },
            ]
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 2
        assert data["results"][0]["status"] == "ok"
        assert data["results"][1]["status"] == "ok"

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
