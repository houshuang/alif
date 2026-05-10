"""Tests for the sentences router (generate + validate + info endpoints)."""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.models import Lemma, Sentence, SentenceWord, SentenceReviewLog, Story, UserLemmaKnowledge
from app.services.sentence_generator import GeneratedSentence, GenerationError


class TestGenerateEndpoint:
    @patch("app.routers.sentences.generate_validated_sentence")
    def test_success(self, mock_gen, client):
        mock_gen.return_value = GeneratedSentence(
            arabic="هذا كتاب جديد",
            english="This is a new book",
            transliteration="hādhā kitāb jadīd",
            target_word="كتاب",
            target_translation="book",
            validation={"valid": True, "unknown_words": []},
            attempts=1,
        )

        resp = client.post("/api/sentences/generate", json={
            "target_arabic": "كتاب",
            "target_translation": "book",
            "known_words": [{"arabic": "كتاب", "english": "book"}],
            "difficulty_hint": "beginner",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["arabic"] == "هذا كتاب جديد"
        assert data["english"] == "This is a new book"
        assert data["transliteration"] == "hādhā kitāb jadīd"

    @patch("app.routers.sentences.generate_validated_sentence")
    def test_generation_error_returns_422(self, mock_gen, client):
        mock_gen.side_effect = GenerationError("Failed after 3 attempts")

        resp = client.post("/api/sentences/generate", json={
            "target_arabic": "كتاب",
            "target_translation": "book",
            "known_words": [],
        })

        assert resp.status_code == 422
        assert "Failed after 3 attempts" in resp.json()["detail"]


class TestValidateEndpoint:
    def test_valid_sentence(self, client):
        resp = client.post("/api/sentences/validate", json={
            "arabic_text": "هذا كتاب",
            "target_bare": "كتاب",
            "known_bare_forms": ["كتاب"],
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["target_found"] is True

    def test_unknown_words_detected(self, client):
        resp = client.post("/api/sentences/validate", json={
            "arabic_text": "هذا كتاب جديد",
            "target_bare": "كتاب",
            "known_bare_forms": ["كتاب"],
        })

        assert resp.status_code == 200
        data = resp.json()
        assert "جديد" in data["unknown_words"]


class TestSentenceInfo:
    def _make_sentence(self, db_session):
        lemma = Lemma(lemma_ar="كِتَاب", lemma_ar_bare="كتاب", gloss_en="book", pos="noun")
        db_session.add(lemma)
        db_session.flush()

        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="learning",
            times_seen=5,
            times_correct=4,
            fsrs_card_json={"difficulty": 5.123, "stability": 12.45},
        )
        db_session.add(ulk)

        created = datetime(2026, 2, 10, 14, 30, 0)
        sent = Sentence(
            arabic_text="هَذَا كِتَابٌ",
            english_translation="This is a book",
            transliteration="hādhā kitāb",
            source="llm",
            difficulty_score=0.35,
            target_lemma_id=lemma.lemma_id,
            created_at=created,
        )
        db_session.add(sent)
        db_session.flush()

        sw = SentenceWord(
            sentence_id=sent.id, position=0,
            surface_form="هَذَا", lemma_id=None, is_target_word=False,
        )
        sw2 = SentenceWord(
            sentence_id=sent.id, position=1,
            surface_form="كِتَابٌ", lemma_id=lemma.lemma_id, is_target_word=True,
        )
        db_session.add_all([sw, sw2])

        review = SentenceReviewLog(
            sentence_id=sent.id, comprehension="understood",
            review_mode="reading", response_ms=2100,
        )
        db_session.add(review)
        db_session.commit()
        return sent, lemma

    def test_returns_sentence_metadata(self, client, db_session):
        sent, lemma = self._make_sentence(db_session)
        resp = client.get(f"/api/sentences/{sent.id}/info")
        assert resp.status_code == 200
        data = resp.json()

        assert data["sentence_id"] == sent.id
        assert data["source"] == "llm"
        assert data["difficulty_score"] == pytest.approx(0.35)
        assert data["created_at"] is not None
        assert "2026-02-10" in data["created_at"]

    def test_returns_word_difficulty(self, client, db_session):
        sent, lemma = self._make_sentence(db_session)
        resp = client.get(f"/api/sentences/{sent.id}/info")
        data = resp.json()

        words = data["words"]
        assert len(words) == 2

        # Function word (no lemma)
        assert words[0]["surface_form"] == "هَذَا"
        assert words[0]["fsrs_difficulty"] is None

        # Target word with FSRS card
        assert words[1]["surface_form"] == "كِتَابٌ"
        assert words[1]["is_target_word"] is True
        assert words[1]["knowledge_state"] == "learning"
        assert words[1]["fsrs_difficulty"] == pytest.approx(5.123)
        assert words[1]["fsrs_stability"] == pytest.approx(12.45)
        assert words[1]["times_seen"] == 5
        assert words[1]["times_correct"] == 4

    def test_returns_review_history(self, client, db_session):
        sent, _ = self._make_sentence(db_session)
        resp = client.get(f"/api/sentences/{sent.id}/info")
        data = resp.json()

        reviews = data["reviews"]
        assert len(reviews) == 1
        assert reviews[0]["comprehension"] == "understood"
        assert reviews[0]["review_mode"] == "reading"
        assert reviews[0]["response_ms"] == 2100

    def test_404_for_missing_sentence(self, client):
        resp = client.get("/api/sentences/99999/info")
        assert resp.status_code == 404


class TestStoryInfo:
    def test_returns_story_metadata_and_target_words(self, client, db_session):
        lemma = Lemma(lemma_ar="خُفّ", lemma_ar_bare="خف", gloss_en="slipper", pos="noun")
        db_session.add(lemma)
        db_session.flush()
        ulk = UserLemmaKnowledge(
            lemma_id=lemma.lemma_id,
            knowledge_state="known",
            times_seen=7,
            times_correct=6,
            fsrs_card_json={"due": "2026-05-10T12:00:00+00:00"},
        )
        db_session.add(ulk)
        story = Story(
            title_ar="الخُفُّ القَدِيمُ",
            title_en="The Old Slipper",
            body_ar="الخُفُّ القَدِيمُ.",
            body_en="The old slipper.",
            source="maintenance",
            format_type="maintenance_passage",
            metadata_json={"style_tag": "nostalgic", "target_lemma_ids": [lemma.lemma_id]},
            total_words=4,
            known_count=4,
            unknown_count=0,
            readiness_pct=100.0,
        )
        db_session.add(story)
        db_session.flush()
        created = datetime(2026, 5, 10, 17, 47, tzinfo=timezone.utc)
        sent1 = Sentence(
            arabic_text="الخُفُّ القَدِيمُ.",
            english_translation="The old slipper.",
            source="passage",
            story_id=story.id,
            target_lemma_id=lemma.lemma_id,
            created_at=created,
        )
        sent2 = Sentence(
            arabic_text="الخُفُّ عِنْدِي.",
            english_translation="The slipper is with me.",
            source="passage",
            story_id=story.id,
            target_lemma_id=lemma.lemma_id,
            created_at=created,
        )
        db_session.add_all([sent1, sent2])
        db_session.flush()
        db_session.add_all([
            SentenceWord(sentence_id=sent1.id, position=0, surface_form="الخُفُّ", lemma_id=lemma.lemma_id, is_target_word=True),
            SentenceWord(sentence_id=sent2.id, position=0, surface_form="الخُفُّ", lemma_id=lemma.lemma_id, is_target_word=True),
        ])
        db_session.commit()

        resp = client.get(f"/api/sentences/{sent1.id}/story-info")

        assert resp.status_code == 200
        data = resp.json()
        assert data["story_id"] == story.id
        assert data["title_en"] == "The Old Slipper"
        assert data["format_type"] == "maintenance_passage"
        assert data["style_tag"] == "nostalgic"
        assert data["sentence_count"] == 2
        assert data["target_lemma_ids"] == [lemma.lemma_id]
        assert data["target_lemmas"][0]["lemma_id"] == lemma.lemma_id
        assert data["target_lemmas"][0]["lemma_ar"] == "خُفّ"
        assert data["target_lemmas"][0]["occurrence_count"] == 2
        assert data["target_lemmas"][0]["fsrs_due"] == "2026-05-10T12:00:00+00:00"

    def test_404_for_sentence_without_story(self, client, db_session):
        sent = Sentence(arabic_text="هَذَا كِتَابٌ", source="llm")
        db_session.add(sent)
        db_session.commit()

        resp = client.get(f"/api/sentences/{sent.id}/story-info")

        assert resp.status_code == 404
