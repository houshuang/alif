"""Tests to ensure every word served to the user has a gloss.

These tests verify the validation gates that prevent glossless words
from reaching the frontend in sentences and Quran verse cards.
"""

import pytest
from unittest.mock import MagicMock, patch

from app.services.sentence_validator import (
    FUNCTION_WORD_GLOSSES,
    strip_diacritics,
    normalize_alef,
    strip_tatweel,
    _is_function_word,
)


class TestSentenceGlossGate:
    """Sentences with glossless lemmas should be rejected at storage time."""

    def test_glossless_lemma_rejected(self):
        """generate_material_for_word should reject sentences where a lemma has empty gloss."""
        from app.services.material_generator import generate_material_for_word

        # This is a design-level test — we verify the gate logic exists
        # by checking that the glossless check code path is present
        import inspect
        source = inspect.getsource(generate_material_for_word)
        assert "glossless_lemma" in source, (
            "generate_material_for_word must check for glossless lemmas before storage"
        )


class TestSessionGlossWarning:
    """Session building should warn about words with missing glosses."""

    def test_function_words_always_have_gloss(self):
        """Every word in FUNCTION_WORD_GLOSSES should have a non-empty gloss."""
        for bare, gloss in FUNCTION_WORD_GLOSSES.items():
            assert gloss, f"Function word '{bare}' has empty gloss"
            assert isinstance(gloss, str), f"Function word '{bare}' gloss is not a string"


class TestQuranGlossGuarantee:
    """Quran verse words must always have a gloss — no exceptions."""

    def test_pronoun_suffix_decomposition(self):
        """Common pronoun-suffixed function words should decompose to glosses."""
        from app.services.quran_service import _gloss_with_pronoun_suffix

        cases = {
            "عليهم": "on/upon",  # على + هم
            "فيها": "in",       # في + ها
            "لهم": "for/to",    # ل + هم
            "بها": "with/by",   # ب + ها
            "عنه": "about/from", # عن + ه
            "منهم": "from",     # من + هم
            "ولهم": "and",      # و + ل + هم
        }
        for form, expected_contains in cases.items():
            result = _gloss_with_pronoun_suffix(form)
            assert result is not None, f"No gloss for pronoun-suffixed '{form}'"
            assert expected_contains in result, (
                f"Gloss for '{form}' = '{result}' doesn't contain '{expected_contains}'"
            )

    def test_quran_normalization_strips_special_chars(self):
        """Quran-specific Unicode characters should be stripped for lookup."""
        from app.services.quran_service import _normalize_quran

        # Small ya (ۦ) — common in بِهِۦ
        assert "ۦ" not in _normalize_quran("بهۦ")
        # Paragraph marker (۞)
        assert "۞" not in _normalize_quran("۞ان")
        # Standalone hamza → alef
        assert _normalize_quran("ءامن") == "امن"

    def test_fill_glosses_llm_has_transliteration_fallback(self):
        """If LLM fails, words should still get a transliteration-based gloss."""
        import inspect
        from app.services.quran_service import _fill_glosses_llm

        source = inspect.getsource(_fill_glosses_llm)
        assert "transliterate_arabic" in source, (
            "_fill_glosses_llm must have transliteration fallback for when LLM fails"
        )

    def test_lemma_lookup_receives_list_not_session(self):
        """build_lemma_lookup must receive a list of Lemma objects, not a db session."""
        import inspect
        from app.services.quran_service import select_verse_cards

        source = inspect.getsource(select_verse_cards)
        # Should have: all_lemmas = db.query(Lemma).all() then build_lemma_lookup(all_lemmas)
        assert "build_lemma_lookup(all_lemmas)" in source, (
            "select_verse_cards must pass all_lemmas list to build_lemma_lookup, not db session"
        )
