"""Tests for the corpus-import regex pre-filter.

Hand-graded representative cases from the Hindawi corpus eval set
(see /tmp/claude/awkward/grades_corpus.csv on the dev box). These
locked-in expectations protect against rule drift.
"""
from app.services.sentence_quality import fails_corpus_regex_filter


class TestFailingCases:
    """Sentences that the regex filter SHOULD reject."""

    def test_r1_opens_with_waw(self):
        # "And it remained like that for a whole week."
        text = "وَمَا زَالَتْ هَكَذَا أُسْبُوعًا كَامِلًا."
        fails, rule = fails_corpus_regex_filter(text)
        assert fails is True
        assert rule == "R1_ANAPHORIC_OPENER"

    def test_r1_opens_with_fa(self):
        # "So she asked him: 'What are you thinking about, my son?'"
        text = "فَسَأَلَتْهُ: «فِيمَ تُفَكِّرُ، يَا وَلَدِي؟»"
        fails, rule = fails_corpus_regex_filter(text)
        assert fails is True
        assert rule == "R1_ANAPHORIC_OPENER"

    def test_r1_opens_with_wa_indama(self):
        text = "وَعِنْدَمَا شَاهَدَ النَّاسُ هَٰذَا اعْتَرَفُوا."
        fails, rule = fails_corpus_regex_filter(text)
        assert fails is True
        assert rule == "R1_ANAPHORIC_OPENER"

    def test_r3_no_terminal_punctuation(self):
        # Bare fragment with no .؟!»…؛ — Hindawi lifts these as chunks.
        text = "جَعَلَ ذَلِكَ سِيَّا الْأَكْبَرَ حَزِينًا جِدًّا"
        fails, rule = fails_corpus_regex_filter(text)
        assert fails is True
        assert rule == "R3_NO_TERMINAL"

    def test_r5_dialogue_only(self):
        text = "«قَرِيبٌ. قَرِيبٌ. قَرِيبٌ.»"
        fails, rule = fails_corpus_regex_filter(text)
        assert fails is True
        assert rule == "R5_DIALOGUE_ONLY"

    def test_r7_demonstrative_subject(self):
        text = "هَذَا رَجُلٌ طَيِّبٌ."
        fails, rule = fails_corpus_regex_filter(text)
        assert fails is True
        assert rule == "R7_DEMONSTRATIVE_SUBJECT"

    def test_r7_demonstrative_with_diacritics(self):
        # "He told her that he had bought all THESE things from the market."
        # هَٰذِهِ in position 4 — outside the first 3 words, so R7 should NOT
        # fire here. But the sentence has no anaphor opener and ends with a
        # period. This sentence should pass (the simulator graded it as F
        # for anaphors but our regex doesn't catch every fragment — this is
        # the recall gap we accept in Phase 1).
        text = "أَخْبَرَهَا أَنَّهُ اشْتَرَىٰ كُلَّ هَٰذِهِ الْأَشْيَاءِ مِنَ السُّوقِ."
        fails, _ = fails_corpus_regex_filter(text)
        assert fails is False

    def test_r8_pronoun_subject(self):
        text = "هُوَ يَعْمَلُ فِي الْمَصْنَعِ."
        fails, rule = fails_corpus_regex_filter(text)
        assert fails is True
        assert rule == "R8_PRONOUN_SUBJECT"


class TestPassingCases:
    """Sentences that should NOT be flagged."""

    def test_proper_noun_subject(self):
        text = "يَاسَمِينُ سَيِّدَةٌ كَرِيمَةٌ، بِنْتُ نَاسٍ طَيِّبِينَ."
        fails, rule = fails_corpus_regex_filter(text)
        assert fails is False
        assert rule is None

    def test_proverbial_with_terminal(self):
        text = "الثَّعْلَبُ يَتَعَلَّمُ مِنَ التَّجْرِبَةِ."
        fails, _ = fails_corpus_regex_filter(text)
        assert fails is False

    def test_simple_declarative(self):
        text = "الْكِتَابُ عَلَىٰ الطَّاوِلَةِ."
        fails, _ = fails_corpus_regex_filter(text)
        assert fails is False


class TestEdgeCases:
    def test_empty_string(self):
        fails, rule = fails_corpus_regex_filter("")
        assert fails is False
        assert rule is None

    def test_whitespace_only(self):
        fails, rule = fails_corpus_regex_filter("   \n  ")
        assert fails is False
        assert rule is None

    def test_question_mark_is_terminal(self):
        text = "هَلْ دَبَّ الْخَوْفُ إِلَى قَلْبِهِ؟"
        fails, _ = fails_corpus_regex_filter(text)
        # Doesn't start with و/ف, has terminal ؟, no demonstrative/pronoun
        # in first 3 words — should pass.
        assert fails is False
