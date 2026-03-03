"""Tests for confusion analysis service."""

import pytest
from unittest.mock import MagicMock

from app.services.confusion_service import (
    edit_distance,
    to_rasm,
    decompose_surface,
    find_similar_words,
    analyze_confusion,
    RASM_MAP,
)


class TestEditDistance:
    def test_identical(self):
        assert edit_distance("كتب", "كتب") == 0

    def test_one_char_diff(self):
        assert edit_distance("كلب", "قلب") == 1

    def test_insertion(self):
        assert edit_distance("كتب", "كتاب") == 1

    def test_empty(self):
        assert edit_distance("", "abc") == 3
        assert edit_distance("abc", "") == 3

    def test_symmetric(self):
        assert edit_distance("كلب", "كتب") == edit_distance("كتب", "كلب")


class TestRasm:
    def test_identical_skeleton(self):
        # كلب and قلب differ only by dots on first letter (ك=ك, ق=ف group)
        r1 = to_rasm("كلب")
        r2 = to_rasm("قلب")
        # ك and ق map to different groups (ك→ك, ق→ف)
        assert r1 != r2

    def test_ba_ta_tha_same_skeleton(self):
        # ب ت ث share the same skeleton
        assert to_rasm("ب") == to_rasm("ت")
        assert to_rasm("ب") == to_rasm("ث")

    def test_sin_shin_same(self):
        assert to_rasm("س") == to_rasm("ش")

    def test_dal_dhal_same(self):
        assert to_rasm("د") == to_rasm("ذ")

    def test_different_groups(self):
        assert to_rasm("ك") != to_rasm("ل")

    def test_words_with_dots_only_diff(self):
        # عنب and عتب: ن and ت share skeleton (both in ب group)
        r1 = to_rasm("عنب")
        r2 = to_rasm("عتب")
        assert r1 == r2  # same rasm skeleton

    def test_ha_ta_marbuta(self):
        assert to_rasm("ه") == to_rasm("ة")


class TestDecomposeSurface:
    def test_proclitic_wa(self):
        result = decompose_surface("وكتاب", "كتاب", None)
        assert result is not None
        assert len(result["prefix_clitics"]) >= 1
        assert any(c["text"] == "و" for c in result["prefix_clitics"])

    def test_proclitic_wal(self):
        result = decompose_surface("والكتاب", "كتاب", None)
        assert result is not None
        prefixes = [c["text"] for c in result["prefix_clitics"]]
        assert "و" in prefixes
        assert "ال" in prefixes

    def test_enclitic_ha(self):
        result = decompose_surface("كتابه", "كتاب", None)
        assert result is not None
        assert len(result["suffix_clitics"]) >= 1
        assert any(c["text"] == "ه" for c in result["suffix_clitics"])

    def test_both_clitics(self):
        result = decompose_surface("وكتابها", "كتاب", None)
        assert result is not None
        assert len(result["prefix_clitics"]) >= 1
        assert len(result["suffix_clitics"]) >= 1

    def test_al_prefix(self):
        result = decompose_surface("الكتاب", "كتاب", None)
        assert result is not None
        prefixes = [c["text"] for c in result["prefix_clitics"]]
        assert "ال" in prefixes

    def test_form_matching(self):
        forms = {"plural": "كُتُب"}
        result = decompose_surface("كتب", "كتاب", forms)
        assert result is not None
        assert result["matched_form_key"] == "plural"

    def test_no_decomposition_same_word(self):
        result = decompose_surface("كتاب", "كتاب", None)
        assert result is None

    def test_taa_marbuta(self):
        result = decompose_surface("والمدرسة", "مدرسة", None)
        assert result is not None
        prefixes = [c["text"] for c in result["prefix_clitics"]]
        assert "و" in prefixes
        assert "ال" in prefixes

    def test_proclitic_ba(self):
        result = decompose_surface("بالكتاب", "كتاب", None)
        assert result is not None
        prefixes = [c["text"] for c in result["prefix_clitics"]]
        assert "ب" in prefixes
        assert "ال" in prefixes

    def test_proclitic_li(self):
        result = decompose_surface("للكتاب", "كتاب", None)
        assert result is not None
        prefixes = [c["text"] for c in result["prefix_clitics"]]
        assert "ل" in prefixes
        assert "ال" in prefixes


class TestFindSimilarWords:
    def _make_lemma(self, lemma_id, ar, bare, gloss, pos="noun", forms=None):
        m = MagicMock()
        m.lemma_id = lemma_id
        m.lemma_ar = ar
        m.lemma_ar_bare = bare
        m.gloss_en = gloss
        m.pos = pos
        m.forms_json = forms
        m.canonical_lemma_id = None
        return m

    def test_finds_similar(self):
        """Test with a mock DB session that returns similar words."""
        db = MagicMock()

        # Mock query results: two similar words
        lemma1 = self._make_lemma(10, "قلب", "قلب", "heart")
        lemma2 = self._make_lemma(20, "كتب", "كتب", "write", "verb")

        db.query.return_value.join.return_value.filter.return_value.all.return_value = [
            (lemma1, "known"),
            (lemma2, "learning"),
        ]

        results = find_similar_words(db, 1, "كلب", max_results=5)
        # كلب vs قلب = edit distance 1
        # كلب vs كتب = edit distance 1
        assert len(results) >= 1
        # Both should be found
        ids = [r["lemma_id"] for r in results]
        assert 10 in ids
        assert 20 in ids

    def test_filters_by_length(self):
        """Words with length difference > 1 should be filtered out."""
        db = MagicMock()

        lemma_long = self._make_lemma(10, "مدرسة", "مدرسة", "school")  # len=5 vs len=3
        db.query.return_value.join.return_value.filter.return_value.all.return_value = [
            (lemma_long, "known"),
        ]

        results = find_similar_words(db, 1, "كلب", max_results=5)
        assert len(results) == 0  # too different in length

    def test_no_results_when_empty_vocab(self):
        db = MagicMock()
        db.query.return_value.join.return_value.filter.return_value.all.return_value = []
        results = find_similar_words(db, 1, "كلب", max_results=5)
        assert results == []


class TestAnalyzeConfusion:
    def _make_lemma_obj(self, lemma_id, ar, bare, gloss, forms=None):
        m = MagicMock()
        m.lemma_id = lemma_id
        m.lemma_ar = ar
        m.lemma_ar_bare = bare
        m.gloss_en = gloss
        m.forms_json = forms
        m.canonical_lemma_id = None
        m.pos = "noun"
        return m

    def test_morphological_only(self):
        db = MagicMock()

        lemma = self._make_lemma_obj(42, "كِتَاب", "كتاب", "book")
        db.query.return_value.filter.return_value.first.return_value = lemma
        # No similar words
        db.query.return_value.join.return_value.filter.return_value.all.return_value = []

        result = analyze_confusion(db, 42, "والكتاب")
        assert result["confusion_type"] == "morphological"
        assert result["decomposition"] is not None

    def test_visual_only(self):
        db = MagicMock()

        lemma = self._make_lemma_obj(42, "كَلْب", "كلب", "dog")
        db.query.return_value.filter.return_value.first.return_value = lemma

        # Provide a similar word
        similar = self._make_lemma_obj(43, "قَلْب", "قلب", "heart")
        db.query.return_value.join.return_value.filter.return_value.all.return_value = [
            (similar, "known"),
        ]

        result = analyze_confusion(db, 42, "كلب")
        assert result["confusion_type"] == "visual"
        assert len(result["similar_words"]) >= 1

    def test_lemma_not_found(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        result = analyze_confusion(db, 999, "test")
        assert result.get("error") == "Lemma not found"

    def test_no_confusion(self):
        db = MagicMock()

        lemma = self._make_lemma_obj(42, "مَدْرَسَة", "مدرسة", "school")
        db.query.return_value.filter.return_value.first.return_value = lemma
        db.query.return_value.join.return_value.filter.return_value.all.return_value = []

        result = analyze_confusion(db, 42, "مدرسة")
        assert result["confusion_type"] is None
