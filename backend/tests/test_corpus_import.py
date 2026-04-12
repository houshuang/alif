"""Tests for corpus import and enrichment pipeline."""
import pytest
from app.services.sentence_validator import (
    detect_proper_names,
    map_tokens_to_lemmas,
    tokenize_display,
    strip_diacritics,
    normalize_alef,
    FUNCTION_WORDS,
    TokenMapping,
)


class TestDetectProperNames:
    def test_known_names_detected(self):
        unmapped = {"بيتر": 50, "توم": 30, "ماري": 20}
        names = detect_proper_names(unmapped, {}, min_frequency=3)
        assert "بيتر" in names
        assert "توم" in names
        assert "ماري" in names

    def test_low_frequency_ignored(self):
        unmapped = {"بيتر": 2, "توم": 1}
        names = detect_proper_names(unmapped, {}, min_frequency=3)
        assert len(names) == 0

    def test_words_in_lookup_ignored(self):
        unmapped = {"بيتر": 50, "كتاب": 50}
        lookup = {"كتاب": 42}
        names = detect_proper_names(unmapped, lookup, min_frequency=3)
        assert "بيتر" in names
        assert "كتاب" not in names


class TestProperNamesInMapping:
    def test_proper_names_get_flag(self):
        tokens = tokenize_display("ذَهَبَ بيتر إلى المدرسة")
        names = {"بيتر"}
        # Need a lookup with at least the non-name words
        lookup = {
            "ذهب": 1,
            "المدرسه": 2, "المدرسة": 2, "مدرسه": 2, "مدرسة": 2,
        }
        mappings = map_tokens_to_lemmas(
            tokens=tokens,
            lemma_lookup=lookup,
            target_lemma_id=0,
            target_bare="",
            proper_names=names,
        )
        name_mappings = [m for m in mappings if m.is_proper_name]
        assert len(name_mappings) == 1
        assert "بيتر" in name_mappings[0].surface_form
        assert name_mappings[0].lemma_id is None

    def test_no_names_default(self):
        """Without proper_names param, no names detected."""
        tokens = tokenize_display("بيتر")
        mappings = map_tokens_to_lemmas(
            tokens=tokens,
            lemma_lookup={},
            target_lemma_id=0,
            target_bare="",
        )
        assert all(not m.is_proper_name for m in mappings)


class TestFunctionWordsPrepositionPronoun:
    """Test that preposition+pronoun fused forms are recognized."""

    @pytest.mark.parametrize("word,expected_in_fw", [
        ("به", True),
        ("بها", True),
        ("عليه", True),
        ("عليها", True),
        ("منه", True),
        ("فيه", True),
        ("لديك", True),
        ("عندي", True),
        ("معه", True),
        ("إليه", True),
        # Regular words should NOT be function words
        ("كتاب", False),
        ("مدرسة", False),
    ])
    def test_prep_pronoun_in_function_words(self, word, expected_in_fw):
        norm = normalize_alef(strip_diacritics(word))
        norm_fw = {normalize_alef(strip_diacritics(w)) for w in FUNCTION_WORDS}
        assert (norm in norm_fw) == expected_in_fw
