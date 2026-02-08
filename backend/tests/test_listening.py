"""Tests for listening comprehension service and endpoints."""
import json
from datetime import datetime, timezone, timedelta

import pytest

from app.models import Root, Lemma, UserLemmaKnowledge, Sentence, SentenceWord, ReviewLog
from app.services.listening import (
    _get_word_listening_confidence,
    score_sentence_for_listening,
    get_listening_candidates,
    process_comprehension_signal,
    MIN_LISTENING_STABILITY_DAYS,
)
from app.services.fsrs_service import create_new_card


def _make_card_json(stability_days=30.0, due_offset_hours=-1):
    """Create an FSRS card dict with specific stability and due time."""
    card = create_new_card()
    card["stability"] = stability_days
    due = datetime.now(timezone.utc) + timedelta(hours=due_offset_hours)
    card["due"] = due.isoformat()
    return card


def _seed_word(db, lemma_id, arabic, english, root_id=None, state="known",
               stability=30.0, times_seen=10, times_correct=8, due_hours=-1):
    """Create a lemma with knowledge record."""
    lemma = Lemma(
        lemma_id=lemma_id,
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        root_id=root_id,
        pos="noun",
        gloss_en=english,
    )
    db.add(lemma)
    db.flush()

    knowledge = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state=state,
        fsrs_card_json=_make_card_json(stability, due_hours),
        introduced_at=datetime.now(timezone.utc) - timedelta(days=30),
        last_reviewed=datetime.now(timezone.utc) - timedelta(hours=1),
        times_seen=times_seen,
        times_correct=times_correct,
        source="study",
    )
    db.add(knowledge)
    db.flush()
    return lemma, knowledge


def _seed_sentence(db, sentence_id, arabic, english, target_lemma_id, word_lemma_ids):
    """Create a sentence with linked words."""
    sent = Sentence(
        id=sentence_id,
        arabic_text=arabic,
        arabic_diacritized=arabic,
        english_translation=english,
        target_lemma_id=target_lemma_id,
        max_word_count=len(word_lemma_ids),
    )
    db.add(sent)
    db.flush()

    for pos, lid in enumerate(word_lemma_ids):
        sw = SentenceWord(
            sentence_id=sentence_id,
            position=pos,
            surface_form=f"word_{pos}",
            lemma_id=lid,
        )
        db.add(sw)
    db.flush()
    return sent


# --- Word confidence tests ---

class TestWordListeningConfidence:
    def test_no_knowledge_zero_confidence(self):
        assert _get_word_listening_confidence(None) == 0.0

    def test_new_word_zero(self, db_session):
        _, k = _seed_word(db_session, 1, "test", "test", state="new")
        assert _get_word_listening_confidence(k) == 0.0

    def test_lapsed_word_low(self, db_session):
        _, k = _seed_word(db_session, 1, "test", "test", state="lapsed")
        assert _get_word_listening_confidence(k) == 0.1

    def test_few_reviews_low(self, db_session):
        _, k = _seed_word(db_session, 1, "test", "test", state="learning",
                         times_seen=1, stability=0.5)
        assert _get_word_listening_confidence(k) == 0.2

    def test_low_stability_medium(self, db_session):
        _, k = _seed_word(db_session, 1, "test", "test", state="learning",
                         times_seen=5, stability=3.0)
        assert _get_word_listening_confidence(k) == 0.5

    def test_good_stability_high(self, db_session):
        _, k = _seed_word(db_session, 1, "test", "test", state="known",
                         times_seen=10, stability=15.0)
        assert _get_word_listening_confidence(k) == 0.7

    def test_very_well_known(self, db_session):
        _, k = _seed_word(db_session, 1, "test", "test", state="known",
                         times_seen=20, times_correct=18, stability=60.0)
        conf = _get_word_listening_confidence(k)
        assert conf > 0.9


# --- Sentence scoring tests ---

class TestSentenceScoring:
    def test_all_known_words_high_confidence(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", stability=30.0, times_seen=15)
        _seed_word(db_session, 2, "ولد", "boy", stability=30.0, times_seen=15)
        _seed_word(db_session, 3, "قرأ", "read", stability=30.0, times_seen=15)

        _seed_sentence(db_session, 1, "الولد قرأ الكتاب", "The boy read the book",
                      target_lemma_id=1, word_lemma_ids=[2, 3, 1])
        db_session.commit()

        score = score_sentence_for_listening(db_session, 1, target_lemma_id=1)
        assert score["confidence"] > 0.6
        assert score["all_words_known"]

    def test_unknown_word_low_confidence(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", stability=30.0)
        _seed_word(db_session, 2, "ولد", "boy", state="new", stability=0.0, times_seen=0)
        _seed_word(db_session, 3, "قرأ", "read", stability=30.0)

        _seed_sentence(db_session, 1, "الولد قرأ الكتاب", "The boy read the book",
                      target_lemma_id=1, word_lemma_ids=[2, 3, 1])
        db_session.commit()

        score = score_sentence_for_listening(db_session, 1, target_lemma_id=1)
        assert score["confidence"] < 0.3
        assert not score["all_words_known"]

    def test_function_words_assumed_known(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", stability=30.0, times_seen=15)

        _seed_sentence(db_session, 1, "في الكتاب", "in the book",
                      target_lemma_id=1, word_lemma_ids=[None, 1])
        db_session.commit()

        score = score_sentence_for_listening(db_session, 1, target_lemma_id=1)
        assert score["confidence"] > 0.5

    def test_empty_sentence(self, db_session):
        score = score_sentence_for_listening(db_session, 999)
        assert score["confidence"] == 0.0

    def test_weakest_word_identified(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", stability=60.0, times_seen=20)
        _seed_word(db_session, 2, "ولد", "boy", stability=5.0, times_seen=4)
        _seed_word(db_session, 3, "قرأ", "read", stability=60.0, times_seen=20)

        _seed_sentence(db_session, 1, "الولد قرأ الكتاب", "The boy read the book",
                      target_lemma_id=1, word_lemma_ids=[2, 3, 1])
        db_session.commit()

        score = score_sentence_for_listening(db_session, 1, target_lemma_id=1)
        assert score["weakest_word"]["lemma_id"] == 2


# --- Listening candidates tests ---

class TestListeningCandidates:
    def test_returns_eligible_cards(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", stability=30.0, times_seen=15, due_hours=-1)
        _seed_word(db_session, 2, "ولد", "boy", stability=30.0, times_seen=15, due_hours=-1)
        _seed_word(db_session, 3, "قرأ", "read", stability=30.0, times_seen=15, due_hours=-1)

        _seed_sentence(db_session, 1, "الولد قرأ الكتاب", "The boy read the book",
                      target_lemma_id=1, word_lemma_ids=[2, 3, 1])
        db_session.commit()

        candidates = get_listening_candidates(db_session, min_confidence=0.5)
        assert len(candidates) >= 1
        assert candidates[0]["lemma_id"] == 1
        assert candidates[0]["sentence"]["arabic"] == "الولد قرأ الكتاب"

    def test_excludes_low_confidence_sentences(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book", stability=30.0, times_seen=15, due_hours=-1)
        _seed_word(db_session, 2, "ولد", "boy", state="new", stability=0.0, times_seen=0, due_hours=24)

        _seed_sentence(db_session, 1, "الولد قرأ الكتاب", "The boy read the book",
                      target_lemma_id=1, word_lemma_ids=[2, 1])
        db_session.commit()

        candidates = get_listening_candidates(db_session, min_confidence=0.6)
        assert len(candidates) == 0


# --- Comprehension signal tests ---

class TestComprehensionSignals:
    def test_listening_word_miss_creates_log(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy")
        db_session.commit()

        logs = process_comprehension_signal(
            db_session,
            session_id="test-session",
            review_mode="listening",
            comprehension_signal="partial",
            target_lemma_id=1,
            missed_word_lemma_ids=[2],
        )

        assert len(logs) == 1
        assert logs[0]["lemma_id"] == 2
        assert logs[0]["signal"] == "listening_word_miss"

        review_logs = db_session.query(ReviewLog).filter(ReviewLog.lemma_id == 2).all()
        assert len(review_logs) == 1
        assert review_logs[0].rating == 2
        assert review_logs[0].review_mode == "listening"

    def test_no_idea_signal_logged(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        db_session.commit()

        logs = process_comprehension_signal(
            db_session,
            session_id="test-session",
            review_mode="listening",
            comprehension_signal="no_idea",
            target_lemma_id=1,
        )

        assert len(logs) == 1
        assert logs[0]["signal"] == "listening_no_idea"

    def test_skip_target_word_in_missed_list(self, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy")
        db_session.commit()

        logs = process_comprehension_signal(
            db_session,
            session_id="test-session",
            review_mode="listening",
            comprehension_signal="partial",
            target_lemma_id=1,
            missed_word_lemma_ids=[1, 2],  # 1 should be skipped (it's the target)
        )

        missed_logs = [l for l in logs if l["signal"] == "listening_word_miss"]
        assert len(missed_logs) == 1
        assert missed_logs[0]["lemma_id"] == 2


# --- API endpoint tests ---

class TestReviewEndpoints:
    def test_submit_with_review_mode(self, client, db_session):
        _seed_word(db_session, 1, "كتاب", "book", stability=1.0, due_hours=-1)
        db_session.commit()

        resp = client.post("/api/review/submit", json={
            "lemma_id": 1,
            "rating": 3,
            "review_mode": "listening",
            "comprehension_signal": "understood",
        })
        assert resp.status_code == 200

        log = db_session.query(ReviewLog).filter(ReviewLog.lemma_id == 1).first()
        assert log.review_mode == "listening"
        assert log.comprehension_signal == "understood"

    def test_submit_reading_no_idea(self, client, db_session):
        _seed_word(db_session, 1, "كتاب", "book", stability=1.0, due_hours=-1)
        db_session.commit()

        resp = client.post("/api/review/submit", json={
            "lemma_id": 1,
            "rating": 1,
            "review_mode": "reading",
            "comprehension_signal": "no_idea",
        })
        assert resp.status_code == 200

    def test_next_listening_endpoint(self, client, db_session):
        _seed_word(db_session, 1, "كتاب", "book", stability=30.0, times_seen=15, due_hours=-1)
        _seed_word(db_session, 2, "ولد", "boy", stability=30.0, times_seen=15, due_hours=-1)

        _seed_sentence(db_session, 1, "الولد في الكتاب", "The boy in the book",
                      target_lemma_id=1, word_lemma_ids=[2, None, 1])
        db_session.commit()

        resp = client.get("/api/review/next-listening")
        assert resp.status_code == 200
