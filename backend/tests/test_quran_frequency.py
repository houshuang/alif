"""Tests for the Quranic Arabic Corpus → Alif lemma frequency mapping.

Guards the QAC-specific normalization that standard MSA normalization misses,
which inflated the unmapped residue before being fixed:

- The QAC maddah caret U+005E (bw2ar leaves it unmapped) must be stripped, so
  سَمَا^ء → سماء and ا^خِر → اخر map to their Alif lemmas.
- The decomposed hamza+alef ءا (QAC encodes آ as 'aA) must fold to ا, so
  'aAyap (آية "sign/verse") and 'aAmana (آمن "to believe", the most frequent
  word that was unmapped before the fix) resolve.

Plus the POS class table used to disambiguate homographs (أَمَرَ verb vs أَمْر
noun) — the conflation class the whole frequency-core rebuild targets.
"""
from camel_tools.utils.charmap import CharMapper

from app.services.quran_frequency import normalize_qac_lemma, pos_match

_BW2AR = CharMapper.builtin_mapper("bw2ar")


class TestNormalizeQacLemma:
    def test_maddah_caret_stripped_samaa(self):
        # samaA^' (sky) — the U+005E caret must not survive into the bare form.
        assert "^" not in normalize_qac_lemma(_BW2AR("samaA^'"))
        assert normalize_qac_lemma(_BW2AR("samaA^'")) == "سماء"

    def test_alef_maddah_caret_akhir(self):
        # A^xir (آخِر "other/last") — alef+caret encodes آ → bare اخر.
        assert normalize_qac_lemma(_BW2AR("A^xir")) == "اخر"

    def test_decomposed_hamza_alef_aya(self):
        # 'aAyap (آية) — decomposed ءا must fold to ا → اية.
        assert normalize_qac_lemma(_BW2AR("'aAyap")) == "اية"

    def test_decomposed_hamza_alef_amana(self):
        # 'aAmana (آمن "to believe") — the most frequent previously-unmapped word.
        assert normalize_qac_lemma(_BW2AR("'aAmana")) == "امن"

    def test_dagger_alef_converts_to_alef_not_dropped(self):
        # xa`liduwn (خٰلِدُون) — the dagger alef U+0670 must convert to ا (long ā
        # preserved), giving the participle skeleton خالدون, never خلدون (=Khaldūn).
        assert normalize_qac_lemma(_BW2AR("xa`liduwn")) == "خالدون"
        assert normalize_qac_lemma(_BW2AR("xa`liduwn")) != "خلدون"

    def test_plain_lemma_unchanged(self):
        assert normalize_qac_lemma(_BW2AR("kitaAb")) == "كتاب"

    def test_final_hamza_preserved(self):
        # A real final hamza (شَيْء) is NOT a decomposed madda — must survive.
        assert normalize_qac_lemma(_BW2AR("$ay'")) == "شيء"


class TestPosMatch:
    def test_noun_matches(self):
        assert pos_match("N", "noun")
        assert pos_match("PN", "noun")

    def test_verb_matches(self):
        assert pos_match("V", "verb")

    def test_adj_matches(self):
        assert pos_match("ADJ", "adj")
        assert pos_match("ADJ", "adjective")

    def test_cross_pos_does_not_match(self):
        # The homograph fix hinges on this: a QAC verb must NOT match a noun lemma.
        assert not pos_match("V", "noun")
        assert not pos_match("N", "verb")

    def test_none_safe(self):
        assert not pos_match(None, "noun")
        assert not pos_match("N", None)
        assert not pos_match("PART", "particle")
