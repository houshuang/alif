"""Tests for morphology service (stub fallback when CAMeL not installed)."""

import pytest

from app.services.morphology import (
    CAMEL_AVAILABLE,
    analyze_word_camel,
    analyze_sentence,
    find_best_db_match,
    find_matching_analysis,
    get_base_lemma,
    get_word_features,
    is_variant_form,
)


class TestStubFallback:
    """Tests that always pass regardless of CAMeL Tools availability."""

    def test_analyze_word_returns_list(self):
        result = analyze_word_camel("كتاب")
        assert isinstance(result, list)

    def test_get_base_lemma_returns_str_or_none(self):
        result = get_base_lemma("كتاب")
        assert result is None or isinstance(result, str)

    def test_is_variant_form_returns_bool(self):
        result = is_variant_form("كتابي", "كتاب")
        assert isinstance(result, bool)

    def test_find_matching_analysis_returns_dict_or_none(self):
        result = find_matching_analysis("كتاب", "كتاب")
        assert result is None or isinstance(result, dict)

    def test_get_word_features_returns_dict(self):
        result = get_word_features("كتاب")
        assert isinstance(result, dict)
        assert "word" in result
        assert "lex" in result
        assert "root" in result
        assert "pos" in result
        assert "source" in result
        if not CAMEL_AVAILABLE:
            assert result["source"] == "stub"
            assert result["pos"] == "UNK"

    def test_find_best_db_match_empty_forms(self):
        result = find_best_db_match("كتاب", set())
        assert result is None

    def test_analyze_sentence_returns_words(self):
        result = analyze_sentence("هذا كتاب")
        assert isinstance(result, dict)
        assert "sentence" in result
        assert "words" in result
        assert len(result["words"]) == 2
        assert result["words"][0]["word"] == "هذا"
        assert result["words"][1]["word"] == "كتاب"
        if not CAMEL_AVAILABLE:
            assert result["source"] == "stub"
