"""Tests for sentence-level review submission."""

from datetime import datetime, timezone, timedelta

import pytest

from app.models import (
    Lemma, UserLemmaKnowledge, Sentence, SentenceWord,
    ReviewLog, SentenceReviewLog,
)
from app.services.fsrs_service import create_new_card
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


class TestUnderstood:
    def test_all_words_get_rating_3(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy")
        _seed_sentence(db_session, 1, "الولد الكتاب", "the boy the book",
                       target_lemma_id=1, word_ids=[2, 1])
        db_session.commit()

        result = submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="understood",
            session_id="test-1",
        )

        assert len(result["word_results"]) == 2
        for wr in result["word_results"]:
            assert wr["rating"] == 3

    def test_primary_gets_primary_credit(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy")
        _seed_sentence(db_session, 1, "الولد الكتاب", "the boy the book",
                       target_lemma_id=1, word_ids=[2, 1])
        db_session.commit()

        result = submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="understood",
            session_id="test-1",
        )

        credits = {wr["lemma_id"]: wr["credit_type"] for wr in result["word_results"]}
        assert credits[1] == "primary"
        assert credits[2] == "collateral"


class TestPartial:
    def test_missed_words_get_rating_1(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy")
        _seed_word(db_session, 3, "قرأ", "read")
        _seed_sentence(db_session, 1, "الولد قرأ الكتاب", "boy read book",
                       target_lemma_id=1, word_ids=[2, 3, 1])
        db_session.commit()

        result = submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="partial",
            missed_lemma_ids=[2],
            session_id="test-1",
        )

        ratings = {wr["lemma_id"]: wr["rating"] for wr in result["word_results"]}
        assert ratings[2] == 1  # missed
        assert ratings[1] == 3  # not missed
        assert ratings[3] == 3  # not missed

    def test_missed_primary_gets_rating_1(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_sentence(db_session, 1, "الكتاب", "the book",
                       target_lemma_id=1, word_ids=[1])
        db_session.commit()

        result = submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="partial",
            missed_lemma_ids=[1],
        )

        assert result["word_results"][0]["rating"] == 1
        assert result["word_results"][0]["credit_type"] == "primary"


class TestNoIdea:
    def test_all_words_get_rating_1(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy")
        _seed_sentence(db_session, 1, "الولد الكتاب", "the boy the book",
                       target_lemma_id=1, word_ids=[2, 1])
        db_session.commit()

        result = submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="no_idea",
            session_id="test-1",
        )

        for wr in result["word_results"]:
            assert wr["rating"] == 1


class TestEncounterOnly:
    def test_word_without_card_gets_encounter(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy", with_card=False)
        _seed_sentence(db_session, 1, "الولد الكتاب", "boy book",
                       target_lemma_id=1, word_ids=[2, 1])
        db_session.commit()

        result = submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="understood",
        )

        encountered = [wr for wr in result["word_results"] if wr["new_state"] == "encountered"]
        assert len(encountered) == 1
        assert encountered[0]["lemma_id"] == 2

    def test_unknown_word_creates_knowledge_record(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        # lemma_id=2 exists but has no knowledge record
        lemma2 = Lemma(lemma_id=2, lemma_ar="ولد", lemma_ar_bare="ولد",
                       pos="noun", gloss_en="boy")
        db_session.add(lemma2)
        _seed_sentence(db_session, 1, "الولد الكتاب", "boy book",
                       target_lemma_id=1, word_ids=[2, 1])
        db_session.commit()

        submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="understood",
        )

        k = db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == 2
        ).first()
        assert k is not None
        assert k.source == "encountered"
        assert k.total_encounters == 1


class TestSentenceReviewLog:
    def test_creates_sentence_review_log(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_sentence(db_session, 1, "الكتاب", "the book",
                       target_lemma_id=1, word_ids=[1])
        db_session.commit()

        submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="understood",
            session_id="sess-1",
            response_ms=1500,
            review_mode="reading",
        )

        logs = db_session.query(SentenceReviewLog).all()
        assert len(logs) == 1
        assert logs[0].sentence_id == 1
        assert logs[0].comprehension == "understood"
        assert logs[0].response_ms == 1500
        assert logs[0].session_id == "sess-1"

    def test_updates_sentence_last_shown(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        sent = _seed_sentence(db_session, 1, "الكتاب", "the book",
                              target_lemma_id=1, word_ids=[1])
        db_session.commit()

        submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="understood",
        )

        db_session.refresh(sent)
        assert sent.last_shown_at is not None
        assert sent.times_shown == 1

    def test_no_sentence_log_for_word_only(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        db_session.commit()

        submit_sentence_review(
            db_session,
            sentence_id=None,
            primary_lemma_id=1,
            comprehension_signal="understood",
        )

        logs = db_session.query(SentenceReviewLog).all()
        assert len(logs) == 0


class TestReviewLogTags:
    def test_review_logs_tagged_with_sentence_and_credit(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy")
        _seed_sentence(db_session, 1, "الولد الكتاب", "boy book",
                       target_lemma_id=1, word_ids=[2, 1])
        db_session.commit()

        submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="understood",
            session_id="tag-test",
        )

        logs = db_session.query(ReviewLog).all()
        assert len(logs) == 2
        for log in logs:
            assert log.sentence_id == 1
            if log.lemma_id == 1:
                assert log.credit_type == "primary"
            else:
                assert log.credit_type == "collateral"


class TestAPIEndpoints:
    def test_next_sentences_endpoint(self, client, db_session):
        _seed_word(db_session, 1, "كتاب", "book", with_card=True)
        # Make it due
        k = db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == 1
        ).first()
        card = _make_card(stability_days=30.0, due_offset_hours=-1)
        k.fsrs_card_json = card

        _seed_sentence(db_session, 1, "الكتاب", "the book",
                       target_lemma_id=1, word_ids=[1])
        db_session.commit()

        resp = client.get("/api/review/next-sentences?limit=5&mode=reading")
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert "items" in data
        assert "total_due_words" in data
        assert "covered_due_words" in data

    def test_submit_sentence_endpoint(self, client, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_sentence(db_session, 1, "الكتاب", "the book",
                       target_lemma_id=1, word_ids=[1])
        db_session.commit()

        resp = client.post("/api/review/submit-sentence", json={
            "sentence_id": 1,
            "primary_lemma_id": 1,
            "comprehension_signal": "understood",
            "session_id": "api-test",
            "review_mode": "reading",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "word_results" in data
        assert len(data["word_results"]) == 1

    def test_submit_sentence_partial(self, client, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy")
        _seed_sentence(db_session, 1, "الولد الكتاب", "boy book",
                       target_lemma_id=1, word_ids=[2, 1])
        db_session.commit()

        resp = client.post("/api/review/submit-sentence", json={
            "sentence_id": 1,
            "primary_lemma_id": 1,
            "comprehension_signal": "partial",
            "missed_lemma_ids": [2],
            "session_id": "api-test",
        })
        assert resp.status_code == 200
        data = resp.json()
        ratings = {wr["lemma_id"]: wr["rating"] for wr in data["word_results"]}
        assert ratings[2] == 1
        assert ratings[1] == 3
