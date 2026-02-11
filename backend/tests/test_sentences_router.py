"""Tests for the sentences router (generate + validate endpoints)."""

from unittest.mock import patch

import pytest

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
