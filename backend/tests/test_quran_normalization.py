"""Regression tests for Quran surface-form normalization.

Guards the dagger-alef (U+0670) handling: Uthmani orthography writes the long ā
in some words with a dagger alef, which lives in the Unicode diacritic range. A
plain strip_diacritics() deletes it, collapsing the word onto a consonant
skeleton that can be a *different* word — the bug that turned خَٰلِدُونَ
("abiding forever") into خلدون, the proper name Khaldūn (ابن خلدون).
"""
from app.services.quran_service import _quran_bare


class TestQuranBare:
    def test_dagger_alef_preserved_as_alef(self):
        # خَٰلِدُونَ must normalize to the participle skeleton خالدون (with alef),
        # NOT the proper-name skeleton خلدون.
        assert _quran_bare("خَٰلِدُونَ") == "خالدون"

    def test_plain_alef_word_unchanged(self):
        assert _quran_bare("خَالِدُونَ") == "خالدون"

    def test_dagger_alef_not_dropped(self):
        # The defining regression: the long ā must survive, so the result is the
        # participle skeleton (with alef), never the proper-name skeleton خلدون.
        assert _quran_bare("خَٰلِدُونَ") != "خلدون"
