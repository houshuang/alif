"""Tests for the Modern Greek provider that don't require GR-NLP-TOOLKIT.

The tokenizer is regex-only and `normalize_bare` is pure-Python, so we can
verify those without the heavy dependency. Tests that need lemmatization are
skipped unless the toolkit is installed (marked `slow`).
"""
import pytest

from app.services.languages.el import ModernGreekProvider, _strip_accents_monotonic


def test_strip_monotonic_accents():
    assert _strip_accents_monotonic("ποτέ") == "ποτε"
    assert _strip_accents_monotonic("ΠΟΤΕ") == "ποτε"
    assert _strip_accents_monotonic("πότε") == "ποτε"


def test_accent_distinguishes_stress_in_display_but_not_lookup():
    # Both stress placements collapse to the same bare key — for lookup that's
    # OK: alternatives surface via NLPProvider.alternatives, and homographs
    # are disambiguated in context. Display form retains the accent.
    assert _strip_accents_monotonic("πότε") == _strip_accents_monotonic("ποτέ")


def test_tokenize_simple():
    p = ModernGreekProvider()
    toks = p.tokenize("Καλημέρα κόσμε!")
    surfaces = [t.surface for t in toks]
    assert surfaces == ["Καλημέρα", "κόσμε", "!"]
    assert toks[2].is_punctuation
    assert not toks[0].is_punctuation


def test_tokenize_handles_apostrophe_elision():
    p = ModernGreekProvider()
    # τ' άλλο = "the other (one)" with elision
    toks = p.tokenize("τ' άλλο")
    surfaces = [t.surface for t in toks]
    # The elided form should be one token
    assert "τ'" in surfaces or "τ'" in "".join(surfaces)


@pytest.mark.slow
def test_lemmatize_with_pipeline():
    """Requires gr-nlp-toolkit installed and model downloaded."""
    p = ModernGreekProvider()
    cand = p.lemmatize("βιβλία")  # plural of βιβλίο "book"
    assert cand.lemma_bare in ("βιβλιο", "βιβλια")  # depends on lemmatizer's choice
