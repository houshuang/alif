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

    def test_function_word_lemma_skipped_for_credit(self, db_session):
        """Function words in a sentence do NOT get FSRS credit."""
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
        assert 2 not in rated_ids  # function word skipped
        assert 1 in rated_ids  # content word rated
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
    def test_word_without_card_gets_fsrs_card(self, db_session):
        """Words without FSRS cards get full FSRS cards when seen in a sentence."""
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

        # All words now get FSRS cards, no more "encountered" state
        word2_result = [wr for wr in result["word_results"] if wr["lemma_id"] == 2]
        assert len(word2_result) == 1
        assert word2_result[0]["new_state"] in ("learning", "known")
        assert word2_result[0]["next_due"] is not None

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
        assert k.fsrs_card_json is not None


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
