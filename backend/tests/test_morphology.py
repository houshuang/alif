"""Tests for morphology service (stub fallback when CAMeL not installed)."""

import pytest

from app.services.morphology import (
    CAMEL_AVAILABLE,
    analyze_word_camel,
    analyze_sentence,
    find_best_db_match,
    find_matching_analysis,
    get_base_lemma,
    get_best_lemma_mle,
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


@pytest.mark.skipif(not CAMEL_AVAILABLE, reason="needs CAMeL Tools")
class TestMleSurfaceFidelity:
    """The MLE disambiguator can pick an analysis whose vocalized form drops
    gemination present in the input. get_best_lemma_mle must override MLE in
    that case by scanning analyzer analyses for a shadda-preserving one."""

    def test_form2_preserves_shadda(self):
        """نَزَّلْنَا (Form II "we sent down") must lemmatize to a Form II lex
        (shadda preserved). Without this fix, MLE alone picks نَزِل (Form I,
        weak-verb misanalysis) — confirmed via _get_disambiguator probe."""
        from app.services.sentence_validator import strip_diacritics
        SHADDA = "ّ"
        result = get_best_lemma_mle("نَزَّلْنَا")
        assert result is not None
        # Form II has gemination — shadda must survive in the lex
        assert SHADDA in result["lex"], f"shadda dropped in lex: {result['lex']!r}"
        # And the consonantal skeleton is نزل
        assert strip_diacritics(result["lex"]) == "نزل"

    def test_unmarked_form1_unchanged(self):
        """No shadda in input → no fidelity override; behaviour identical
        to the previous MLE-only path."""
        from app.services.sentence_validator import strip_diacritics
        result = get_best_lemma_mle("كَتَبَ")
        assert result is not None
        assert strip_diacritics(result["lex"]) == "كتب"

    def test_definite_noun_strips_al(self):
        """Regression: CAMeL identifies prc0='Al_det' for الماشي and lex='ماشِي'
        — no shadda involved, MLE pick is fine, but we want to confirm the
        surface-fidelity check doesn't accidentally regress this path."""
        from app.services.sentence_validator import strip_diacritics
        result = get_best_lemma_mle("الماشي")
        assert result is not None
        assert strip_diacritics(result["lex"]) == "ماشي"
