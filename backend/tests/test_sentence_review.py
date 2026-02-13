"""Tests for sentence-level review submission."""

from datetime import datetime, timezone, timedelta

import pytest

from app.models import (
    Lemma, UserLemmaKnowledge, Sentence, SentenceWord,
    ReviewLog, SentenceReviewLog,
)
from app.models import GrammarFeature, SentenceGrammarFeature
from app.services.fsrs_service import create_new_card, submit_review
from app.services.grammar_service import record_grammar_exposure
from app.services.sentence_review_service import submit_sentence_review, undo_sentence_review
from tests.conftest import count_commits


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

    def test_all_words_get_credit(self, db_session):
        """All words (including في) now get FSRS credit."""
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "في", "in")
        _seed_sentence(db_session, 1, "في الكتاب", "in the book",
                       target_lemma_id=1, word_ids=[2, 1])
        db_session.commit()

        result = submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="partial",
            missed_lemma_ids=[2],
            session_id="test-1",
        )

        rated_ids = {wr["lemma_id"] for wr in result["word_results"]}
        assert 2 in rated_ids  # في now gets credit
        assert 1 in rated_ids
        ratings = {wr["lemma_id"]: wr["rating"] for wr in result["word_results"]}
        assert ratings[1] == 3

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


class TestConfused:
    def test_confused_words_get_rating_2(self, db_session):
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
            confused_lemma_ids=[2],
            session_id="test-1",
        )

        ratings = {wr["lemma_id"]: wr["rating"] for wr in result["word_results"]}
        assert ratings[2] == 2  # confused
        assert ratings[1] == 3  # not confused
        assert ratings[3] == 3  # not confused

    def test_mixed_missed_and_confused(self, db_session):
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
            missed_lemma_ids=[3],
            confused_lemma_ids=[2],
            session_id="test-1",
        )

        ratings = {wr["lemma_id"]: wr["rating"] for wr in result["word_results"]}
        assert ratings[3] == 1  # missed
        assert ratings[2] == 2  # confused
        assert ratings[1] == 3  # understood

    def test_confused_ignored_on_no_idea(self, db_session):
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
            confused_lemma_ids=[2],
            session_id="test-1",
        )

        for wr in result["word_results"]:
            assert wr["rating"] == 1

    def test_confused_api_endpoint(self, client, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy")
        _seed_sentence(db_session, 1, "الولد الكتاب", "boy book",
                       target_lemma_id=1, word_ids=[2, 1])
        db_session.commit()

        resp = client.post("/api/review/submit-sentence", json={
            "sentence_id": 1,
            "primary_lemma_id": 1,
            "comprehension_signal": "partial",
            "confused_lemma_ids": [2],
            "session_id": "api-test",
        })
        assert resp.status_code == 200
        data = resp.json()
        ratings = {wr["lemma_id"]: wr["rating"] for wr in data["word_results"]}
        assert ratings[2] == 2
        assert ratings[1] == 3


class TestGrammarConfused:
    def test_all_words_get_rating_3(self, db_session):
        """grammar_confused means all words are fine — only grammar is the issue."""
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy")
        _seed_sentence(db_session, 1, "الولد الكتاب", "the boy the book",
                       target_lemma_id=1, word_ids=[2, 1])
        db_session.commit()

        result = submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="grammar_confused",
            session_id="test-gc",
        )

        assert len(result["word_results"]) == 2
        for wr in result["word_results"]:
            assert wr["rating"] == 3

    def test_grammar_confused_api(self, client, db_session):
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_sentence(db_session, 1, "الكتاب", "the book",
                       target_lemma_id=1, word_ids=[1])
        db_session.commit()

        resp = client.post("/api/review/submit-sentence", json={
            "sentence_id": 1,
            "primary_lemma_id": 1,
            "comprehension_signal": "grammar_confused",
            "session_id": "api-gc",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["word_results"][0]["rating"] == 3


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
    def test_word_without_card_starts_acquisition(self, db_session):
        """Words without ULK records start acquisition (not straight to FSRS)."""
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

        word2_result = [wr for wr in result["word_results"] if wr["lemma_id"] == 2]
        assert len(word2_result) == 1
        assert word2_result[0]["new_state"] == "acquiring"

    def test_unknown_word_creates_acquiring_record(self, db_session):
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
        assert k.source == "collateral"
        assert k.knowledge_state == "acquiring"
        assert k.acquisition_box == 2  # started box 1, rating=3 advanced to box 2
        assert k.fsrs_card_json is None


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
        assert sent.last_reading_shown_at is not None
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


class TestVariantStats:
    def test_variant_surface_form_tracked(self, db_session):
        """When surface form differs from lemma bare, variant_stats_json is updated."""
        _seed_word(db_session, 1, "بنت", "girl")
        sent = Sentence(
            id=1, arabic_text="بنتي جميلة", arabic_diacritized="بنتي جميلة",
            english_translation="my daughter is beautiful", target_lemma_id=1,
        )
        db_session.add(sent)
        db_session.flush()
        # Surface form "بنتي" differs from lemma bare "بنت"
        sw = SentenceWord(sentence_id=1, position=0, surface_form="بنتي", lemma_id=1)
        db_session.add(sw)
        db_session.flush()
        db_session.commit()

        submit_sentence_review(
            db_session, sentence_id=1, primary_lemma_id=1,
            comprehension_signal="understood", session_id="test-variant",
        )

        knowledge = db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == 1
        ).first()
        assert knowledge.variant_stats_json is not None
        vstats = knowledge.variant_stats_json
        assert "بنتي" in vstats
        assert vstats["بنتي"]["seen"] == 1
        assert vstats["بنتي"]["missed"] == 0

    def test_variant_missed_increments(self, db_session):
        """Missed variant form increments missed counter."""
        _seed_word(db_session, 1, "بنت", "girl")
        _seed_word(db_session, 2, "كبير", "big")
        sent = Sentence(
            id=1, arabic_text="بنتك كبيرة", arabic_diacritized="بنتك كبيرة",
            english_translation="your daughter is big", target_lemma_id=1,
        )
        db_session.add(sent)
        db_session.flush()
        sw1 = SentenceWord(sentence_id=1, position=0, surface_form="بنتك", lemma_id=1)
        sw2 = SentenceWord(sentence_id=1, position=1, surface_form="كبيرة", lemma_id=2)
        db_session.add_all([sw1, sw2])
        db_session.flush()
        db_session.commit()

        submit_sentence_review(
            db_session, sentence_id=1, primary_lemma_id=1,
            comprehension_signal="partial", missed_lemma_ids=[1],
            session_id="test-variant-miss",
        )

        knowledge = db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == 1
        ).first()
        vstats = knowledge.variant_stats_json
        assert vstats is not None
        assert vstats["بنتك"]["seen"] == 1
        assert vstats["بنتك"]["missed"] == 1

    def test_same_surface_as_lemma_not_tracked(self, db_session):
        """When surface form equals lemma bare, no variant stats are recorded."""
        _seed_word(db_session, 1, "كتاب", "book")
        sent = Sentence(
            id=1, arabic_text="كتاب", arabic_diacritized="كتاب",
            english_translation="book", target_lemma_id=1,
        )
        db_session.add(sent)
        db_session.flush()
        sw = SentenceWord(sentence_id=1, position=0, surface_form="كتاب", lemma_id=1)
        db_session.add(sw)
        db_session.flush()
        db_session.commit()

        submit_sentence_review(
            db_session, sentence_id=1, primary_lemma_id=1,
            comprehension_signal="understood", session_id="test-no-variant",
        )

        knowledge = db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == 1
        ).first()
        assert knowledge.variant_stats_json is None


class TestUndoSentenceReview:
    def test_undo_restores_fsrs_state(self, db_session):
        """Undo should restore pre-review FSRS card state."""
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy")
        _seed_sentence(db_session, 1, "الولد الكتاب", "boy book",
                       target_lemma_id=1, word_ids=[2, 1])
        db_session.commit()

        # Record pre-review state
        k1_before = db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == 1).first()
        ts1_before = k1_before.times_seen
        tc1_before = k1_before.times_correct
        state1_before = k1_before.knowledge_state

        # Submit review
        submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="understood",
            client_review_id="undo-test-1",
        )

        # Verify review was applied
        db_session.expire_all()
        k1_after = db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == 1).first()
        assert k1_after.times_seen == ts1_before + 1

        # Undo
        result = undo_sentence_review(db_session, "undo-test-1")
        assert result["undone"] is True
        assert result["reviews_removed"] == 2

        # Verify state restored
        db_session.expire_all()
        k1_restored = db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == 1).first()
        assert k1_restored.times_seen == ts1_before
        assert k1_restored.times_correct == tc1_before
        assert k1_restored.knowledge_state == state1_before

    def test_undo_deletes_review_logs(self, db_session):
        """Undo should delete ReviewLog and SentenceReviewLog entries."""
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_sentence(db_session, 1, "الكتاب", "the book",
                       target_lemma_id=1, word_ids=[1])
        db_session.commit()

        submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="understood",
            client_review_id="undo-test-2",
        )

        # Verify logs exist
        assert db_session.query(ReviewLog).count() == 1
        assert db_session.query(SentenceReviewLog).count() == 1

        # Undo
        undo_sentence_review(db_session, "undo-test-2")

        # Verify logs deleted
        assert db_session.query(ReviewLog).count() == 0
        assert db_session.query(SentenceReviewLog).count() == 0

    def test_undo_restores_sentence_metadata(self, db_session):
        """Undo should decrement times_shown and clear comprehension."""
        _seed_word(db_session, 1, "كتاب", "book")
        sent = _seed_sentence(db_session, 1, "الكتاب", "the book",
                              target_lemma_id=1, word_ids=[1])
        db_session.commit()

        submit_sentence_review(
            db_session,
            sentence_id=1,
            primary_lemma_id=1,
            comprehension_signal="understood",
            client_review_id="undo-test-3",
        )

        db_session.refresh(sent)
        assert sent.times_shown == 1
        assert sent.last_reading_comprehension == "understood"

        undo_sentence_review(db_session, "undo-test-3")

        db_session.refresh(sent)
        assert sent.times_shown == 0
        assert sent.last_reading_comprehension is None

    def test_undo_idempotent_when_not_found(self, db_session):
        """Undo with unknown client_review_id should return undone=False."""
        result = undo_sentence_review(db_session, "nonexistent-id")
        assert result["undone"] is False
        assert result["reviews_removed"] == 0

    def test_undo_endpoint(self, client, db_session):
        """Test the API endpoint for undo."""
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_sentence(db_session, 1, "الكتاب", "the book",
                       target_lemma_id=1, word_ids=[1])
        db_session.commit()

        # Submit via API
        client.post("/api/review/submit-sentence", json={
            "sentence_id": 1,
            "primary_lemma_id": 1,
            "comprehension_signal": "understood",
            "session_id": "undo-api-test",
            "review_mode": "reading",
            "client_review_id": "undo-api-1",
        })

        # Undo via API
        resp = client.post("/api/review/undo-sentence", json={
            "client_review_id": "undo-api-1",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["undone"] is True

        # Verify ReviewLog cleaned up
        assert db_session.query(ReviewLog).count() == 0


class TestVariantRedirect:
    """Reviews of variant words should credit the canonical lemma."""

    def test_variant_credits_canonical(self, db_session):
        """When a sentence contains a variant, review credit goes to canonical."""
        # Create canonical lemma with ULK
        canonical = Lemma(lemma_id=100, lemma_ar="كِتَاب", lemma_ar_bare="كتاب",
                         pos="noun", gloss_en="book")
        db_session.add(canonical)
        db_session.flush()
        canonical_ulk = UserLemmaKnowledge(
            lemma_id=100, knowledge_state="learning",
            fsrs_card_json=_make_card(), times_seen=5, times_correct=3, source="study",
        )
        db_session.add(canonical_ulk)

        # Create variant lemma pointing to canonical (NO ULK needed)
        variant = Lemma(lemma_id=101, lemma_ar="الكِتَاب", lemma_ar_bare="الكتاب",
                       pos="noun", gloss_en="the book", canonical_lemma_id=100)
        db_session.add(variant)

        # Create sentence with the variant form
        sent = Sentence(id=50, arabic_text="الكتاب جميل", arabic_diacritized="الكتاب جميل",
                       english_translation="the book is beautiful", target_lemma_id=101)
        db_session.add(sent)
        db_session.flush()
        sw = SentenceWord(sentence_id=50, position=0, surface_form="الكتاب", lemma_id=101)
        db_session.add(sw)
        db_session.commit()

        result = submit_sentence_review(
            db_session, sentence_id=50, primary_lemma_id=101,
            comprehension_signal="understood", session_id="test-variant",
        )

        # Credit should go to canonical (100), not variant (101)
        assert len(result["word_results"]) == 1
        assert result["word_results"][0]["lemma_id"] == 100

        # Canonical ULK should be updated
        db_session.refresh(canonical_ulk)
        assert canonical_ulk.times_seen == 6  # was 5
        assert canonical_ulk.times_correct == 4  # was 3

        # Review log should reference canonical
        log = db_session.query(ReviewLog).filter(ReviewLog.lemma_id == 100).first()
        assert log is not None

    def test_variant_tracks_surface_form_stats(self, db_session):
        """Variant surface forms tracked in variant_stats_json on canonical ULK."""
        canonical = Lemma(lemma_id=200, lemma_ar="كِتَاب", lemma_ar_bare="كتاب",
                         pos="noun", gloss_en="book")
        db_session.add(canonical)
        db_session.flush()
        canonical_ulk = UserLemmaKnowledge(
            lemma_id=200, knowledge_state="learning",
            fsrs_card_json=_make_card(), times_seen=3, times_correct=2, source="study",
        )
        db_session.add(canonical_ulk)

        variant = Lemma(lemma_id=201, lemma_ar="الكِتَاب", lemma_ar_bare="الكتاب",
                       pos="noun", gloss_en="the book", canonical_lemma_id=200)
        db_session.add(variant)

        sent = Sentence(id=60, arabic_text="الكتاب", arabic_diacritized="الكتاب",
                       english_translation="the book", target_lemma_id=201)
        db_session.add(sent)
        db_session.flush()
        sw = SentenceWord(sentence_id=60, position=0, surface_form="الكِتَاب", lemma_id=201)
        db_session.add(sw)
        db_session.commit()

        submit_sentence_review(
            db_session, sentence_id=60, primary_lemma_id=201,
            comprehension_signal="understood", session_id="test-vstats",
        )

        db_session.refresh(canonical_ulk)
        vstats = canonical_ulk.variant_stats_json
        assert isinstance(vstats, dict)
        assert "الكتاب" in vstats
        assert vstats["الكتاب"]["seen"] >= 1

    def test_suspended_variant_skipped(self, db_session):
        """If variant ULK is suspended, it should be skipped entirely."""
        canonical = Lemma(lemma_id=300, lemma_ar="كِتَاب", lemma_ar_bare="كتاب",
                         pos="noun", gloss_en="book")
        db_session.add(canonical)
        db_session.flush()
        canonical_ulk = UserLemmaKnowledge(
            lemma_id=300, knowledge_state="learning",
            fsrs_card_json=_make_card(), times_seen=5, times_correct=3, source="study",
        )
        db_session.add(canonical_ulk)

        variant = Lemma(lemma_id=301, lemma_ar="الكِتَاب", lemma_ar_bare="الكتاب",
                       pos="noun", gloss_en="the book", canonical_lemma_id=300)
        db_session.add(variant)
        db_session.flush()
        # Variant ULK is suspended
        variant_ulk = UserLemmaKnowledge(
            lemma_id=301, knowledge_state="suspended",
            times_seen=1, times_correct=0, source="study",
        )
        db_session.add(variant_ulk)

        sent = Sentence(id=70, arabic_text="الكتاب", arabic_diacritized="الكتاب",
                       english_translation="the book", target_lemma_id=301)
        db_session.add(sent)
        db_session.flush()
        sw = SentenceWord(sentence_id=70, position=0, surface_form="الكتاب", lemma_id=301)
        db_session.add(sw)
        db_session.commit()

        result = submit_sentence_review(
            db_session, sentence_id=70, primary_lemma_id=301,
            comprehension_signal="understood", session_id="test-susp",
        )

        # Suspended variant should be skipped — canonical still gets credit
        # (variant is suspended, but canonical is not)
        # Actually, the current logic skips if the variant lemma_id is in suspended_lemma_ids.
        # The canonical is NOT suspended, so credit should still go through.
        # Let's check: the variant's ULK is suspended, so lemma_id 301 is in suspended_lemma_ids.
        # The code checks: if lemma_id in suspended_lemma_ids or effective_lemma_id in suspended_lemma_ids
        # lemma_id=301 IS suspended, so it's skipped.
        # This is correct — we don't want to credit the canonical when the specific variant form is suspended.
        assert len(result["word_results"]) == 0

    def test_dedup_multiple_variants_same_canonical(self, db_session):
        """Two variants of the same canonical in a sentence should only credit once."""
        canonical = Lemma(lemma_id=400, lemma_ar="كِتَاب", lemma_ar_bare="كتاب",
                         pos="noun", gloss_en="book")
        db_session.add(canonical)
        db_session.flush()
        canonical_ulk = UserLemmaKnowledge(
            lemma_id=400, knowledge_state="learning",
            fsrs_card_json=_make_card(), times_seen=5, times_correct=3, source="study",
        )
        db_session.add(canonical_ulk)

        variant1 = Lemma(lemma_id=401, lemma_ar="الكِتَاب", lemma_ar_bare="الكتاب",
                        pos="noun", gloss_en="the book", canonical_lemma_id=400)
        db_session.add(variant1)
        variant2 = Lemma(lemma_id=402, lemma_ar="كِتَابي", lemma_ar_bare="كتابي",
                        pos="noun", gloss_en="my book", canonical_lemma_id=400)
        db_session.add(variant2)

        sent = Sentence(id=80, arabic_text="الكتاب كتابي", arabic_diacritized="الكتاب كتابي",
                       english_translation="the book is my book", target_lemma_id=401)
        db_session.add(sent)
        db_session.flush()
        SentenceWord(sentence_id=80, position=0, surface_form="الكتاب", lemma_id=401)
        sw1 = SentenceWord(sentence_id=80, position=0, surface_form="الكتاب", lemma_id=401)
        sw2 = SentenceWord(sentence_id=80, position=1, surface_form="كتابي", lemma_id=402)
        db_session.add_all([sw1, sw2])
        db_session.commit()

        result = submit_sentence_review(
            db_session, sentence_id=80, primary_lemma_id=401,
            comprehension_signal="understood", session_id="test-dedup",
        )

        # Should credit canonical only once, not twice
        assert len(result["word_results"]) == 1
        assert result["word_results"][0]["lemma_id"] == 400


class TestRecapEndpoint:
    def test_recap_returns_words(self, client, db_session):
        """Recap items must include words array with surface_form, gloss_en, etc."""
        _seed_word(db_session, 1, "كتاب", "book", with_card=False)
        # Create acquiring ULK
        ulk = UserLemmaKnowledge(
            lemma_id=1,
            knowledge_state="acquiring",
            introduced_at=datetime.now(timezone.utc) - timedelta(hours=4),
            times_seen=1,
            source="study",
        )
        db_session.add(ulk)
        _seed_sentence(db_session, 1, "الكتاب جميل", "the book is beautiful",
                       target_lemma_id=1, word_ids=[1])
        db_session.commit()

        resp = client.post("/api/review/recap", json={
            "last_session_lemma_ids": [1],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1

        item = data["items"][0]
        assert "words" in item, "recap items must include 'words'"
        assert len(item["words"]) == 1
        assert item["words"][0]["surface_form"] == "word_0"
        assert item["words"][0]["gloss_en"] == "book"
        assert "primary_lemma_id" in item
        assert item["primary_lemma_id"] == 1

    def test_recap_empty_when_no_acquiring(self, client, db_session):
        """Recap returns empty when no words are still acquiring."""
        _seed_word(db_session, 1, "كتاب", "book")  # learning, not acquiring
        db_session.commit()

        resp = client.post("/api/review/recap", json={
            "last_session_lemma_ids": [1],
        })
        assert resp.status_code == 200
        assert resp.json()["items"] == []


class TestSingleCommitTransaction:
    """Verify that sentence review consolidates into a single db.commit()."""

    def test_single_commit_for_full_sentence_review(self, db_session):
        """A sentence with multiple words + grammar features should fire exactly 1 commit."""
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy")
        _seed_word(db_session, 3, "قرأ", "read")

        # Add a grammar feature and tag the sentence with it
        gf = GrammarFeature(
            feature_id=1, category="verb_tense", feature_key="past",
            label_en="Past Tense", label_ar="الماضي", sort_order=20,
        )
        db_session.add(gf)
        _seed_sentence(db_session, 1, "الولد قرأ الكتاب", "boy read book",
                       target_lemma_id=1, word_ids=[2, 3, 1])
        db_session.flush()
        sgf = SentenceGrammarFeature(
            sentence_id=1, feature_id=1, is_primary=False, source="derived",
        )
        db_session.add(sgf)
        db_session.commit()

        with count_commits(db_session) as counter:
            submit_sentence_review(
                db_session,
                sentence_id=1,
                primary_lemma_id=1,
                comprehension_signal="understood",
                session_id="commit-test",
            )

        assert counter["count"] == 1

    def test_atomicity_failure_mid_review_rolls_back(self, db_session):
        """If submit_review raises mid-loop, no ReviewLog or ULK changes persist."""
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy")
        _seed_sentence(db_session, 1, "الولد الكتاب", "boy book",
                       target_lemma_id=1, word_ids=[2, 1])
        db_session.commit()

        original_ts_1 = db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == 1).first().times_seen
        original_ts_2 = db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == 2).first().times_seen

        # Monkeypatch submit_review to fail on the second word
        call_count = {"n": 0}
        original_submit = submit_review.__wrapped__ if hasattr(submit_review, '__wrapped__') else None

        from app.services import sentence_review_service
        _real_submit = sentence_review_service.submit_review

        def _exploding_submit(db, lemma_id, **kwargs):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                raise RuntimeError("Simulated failure")
            return _real_submit(db, lemma_id=lemma_id, **kwargs)

        sentence_review_service.submit_review = _exploding_submit
        try:
            with pytest.raises(RuntimeError, match="Simulated failure"):
                submit_sentence_review(
                    db_session,
                    sentence_id=1,
                    primary_lemma_id=1,
                    comprehension_signal="understood",
                    session_id="atomicity-test",
                )
            db_session.rollback()
        finally:
            sentence_review_service.submit_review = _real_submit

        # No ReviewLog entries should persist
        assert db_session.query(ReviewLog).count() == 0
        # ULK unchanged
        ts_1 = db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == 1).first().times_seen
        ts_2 = db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == 2).first().times_seen
        assert ts_1 == original_ts_1
        assert ts_2 == original_ts_2

    def test_standalone_submit_review_still_commits(self, db_session):
        """Calling submit_review() directly (default commit=True) persists state."""
        _seed_word(db_session, 1, "كتاب", "book")
        db_session.commit()

        submit_review(
            db_session, lemma_id=1, rating_int=3,
            review_mode="reading", session_id="standalone",
        )

        # Data should be persisted
        log = db_session.query(ReviewLog).filter(ReviewLog.lemma_id == 1).first()
        assert log is not None
        ulk = db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == 1).first()
        assert ulk.times_seen == 6  # was 5 from _seed_word

    def test_standalone_grammar_exposure_still_commits(self, db_session):
        """Calling record_grammar_exposure() directly persists state."""
        gf = GrammarFeature(
            feature_id=1, category="verb_tense", feature_key="past",
            label_en="Past Tense", label_ar="الماضي", sort_order=20,
        )
        db_session.add(gf)
        db_session.commit()

        record_grammar_exposure(db_session, "past", correct=True)

        from app.models import UserGrammarExposure
        exp = db_session.query(UserGrammarExposure).filter(
            UserGrammarExposure.feature_id == 1).first()
        assert exp is not None
        assert exp.times_seen == 1

    def test_story_complete_single_commit(self, db_session):
        """complete_story() with multiple FSRS words should fire exactly 1 commit."""
        from app.models import Story, StoryWord

        # Create 3 words with FSRS cards
        _seed_word(db_session, 1, "كتاب", "book")
        _seed_word(db_session, 2, "ولد", "boy")
        _seed_word(db_session, 3, "قرأ", "read")

        story = Story(
            id=1, title_ar="test", body_ar="الولد قرأ الكتاب",
            source="imported", status="active",
        )
        db_session.add(story)
        db_session.flush()
        for i, lid in enumerate([2, 3, 1]):
            sw = StoryWord(
                story_id=1, position=i, surface_form=f"w{i}",
                lemma_id=lid, is_function_word=False,
            )
            db_session.add(sw)
        db_session.commit()

        from app.services.story_service import complete_story
        with count_commits(db_session) as counter:
            complete_story(db_session, story_id=1, looked_up_lemma_ids=[])

        assert counter["count"] == 1
