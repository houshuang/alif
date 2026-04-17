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
from scripts.import_hindawi import _split_on_terminators, extract_sentences


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


class TestHindawiSplitter:
    """Dialogue-aware sentence splitter for Hindawi corpus."""

    def test_keeps_closing_guillemet_with_terminator(self):
        chunks = _split_on_terminators("قال دوليتل.»")
        assert chunks == ["قال دوليتل.»"]

    def test_does_not_split_inside_open_quote(self):
        chunks = _split_on_terminators("قال: «أعلم ذلك. اللحوم هنا نادرة.»")
        assert chunks == ["قال: «أعلم ذلك. اللحوم هنا نادرة.»"]

    def test_splits_outside_quotes_normally(self):
        chunks = _split_on_terminators("الأول. الثاني. الثالث.")
        assert chunks == ["الأول.", " الثاني.", " الثالث."]

    def test_newline_always_splits_even_inside_quote(self):
        # Paragraph break resets depth — guards against source text that
        # leaves a quote unclosed at paragraph end.
        chunks = _split_on_terminators("قال: «أعلم\nاللحوم نادرة.")
        assert len(chunks) == 2

    def test_multiple_sentences_with_embedded_quotes(self):
        chunks = _split_on_terminators("«مرحبا.» ثم ذهب. «وداعا.»")
        # Embedded quote "مرحبا." stays with the preceding narration until
        # the outer terminator. "وداعا." is its own quoted sentence.
        assert chunks == ["«مرحبا.» ثم ذهب.", " «وداعا.»"]

    def test_absorbs_ascii_closing_quote(self):
        # Non-guillemet closers after terminator stay attached
        chunks = _split_on_terminators('he said "hi." he left.')
        assert chunks[0] == 'he said "hi."'

    def test_question_mark_splits(self):
        chunks = _split_on_terminators("ما اسمك؟ كيف حالك؟")
        assert chunks == ["ما اسمك؟", " كيف حالك؟"]

    def test_unbalanced_quote_in_source_still_chunks(self):
        # Source text may be malformed — unclosed «. Newline + EOF still
        # yields the chunk; we don't hang or lose the text.
        chunks = _split_on_terminators("قال: «لا ينتهي أبدا")
        assert chunks == ["قال: «لا ينتهي أبدا"]

    def test_extract_sentences_word_count_filter(self):
        # Balanced short sentence within word range is kept.
        result = extract_sentences(
            "ذهب الولد إلى المدرسة في الصباح الباكر.",
            min_words=5, max_words=14,
        )
        assert len(result) == 1

    def test_extract_sentences_drops_orphan_guillemet_chunks(self):
        # Under the dialogue-aware splitter, internal-to-dialogue periods
        # no longer produce orphan-guillemet sub-sentences.
        text = "قال بيل: «أعلم ذلك تماما. اللحوم نادرة هنا جدا.»"
        result = extract_sentences(text, min_words=5, max_words=30)
        orphan = [s for s in result if ("«" in s) != ("»" in s)]
        assert orphan == []
