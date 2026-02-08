"""Tests for the sentence validator.

Uses hardcoded Arabic sentences with known word sets to verify
word classification and validation logic.
"""

import pytest

from app.services.sentence_validator import (
    FUNCTION_WORDS,
    ValidationResult,
    normalize_alef,
    normalize_arabic,
    strip_diacritics,
    tokenize,
    validate_sentence,
)


class TestStripDiacritics:
    def test_fatha_kasra_damma(self):
        assert strip_diacritics("كِتَابٌ") == "كتاب"

    def test_shadda_sukun(self):
        assert strip_diacritics("مُدَرِّسٌ") == "مدرس"

    def test_no_diacritics(self):
        assert strip_diacritics("كتاب") == "كتاب"

    def test_tanwin(self):
        assert strip_diacritics("كِتَابًا") == "كتابا"

    def test_empty_string(self):
        assert strip_diacritics("") == ""


class TestNormalizeAlef:
    def test_hamza_above(self):
        assert normalize_alef("أحمد") == "احمد"

    def test_hamza_below(self):
        assert normalize_alef("إسلام") == "اسلام"

    def test_madda(self):
        assert normalize_alef("آمن") == "امن"

    def test_no_change(self):
        assert normalize_alef("كتاب") == "كتاب"


class TestTokenize:
    def test_simple_sentence(self):
        tokens = tokenize("الكتاب على الطاولة")
        assert tokens == ["الكتاب", "على", "الطاولة"]

    def test_with_punctuation(self):
        tokens = tokenize("هل قرأت الكتاب؟")
        assert tokens == ["هل", "قرأت", "الكتاب"]

    def test_with_comma(self):
        tokens = tokenize("قرأت كتابًا، ثم نمت")
        # Tokenize splits on whitespace/punctuation but does not strip diacritics
        assert tokens == ["قرأت", "كتابًا", "ثم", "نمت"]

    def test_empty(self):
        assert tokenize("") == []

    def test_only_punctuation(self):
        assert tokenize("،؟!") == []


class TestValidateSentence:
    """Test validation with realistic Arabic sentences."""

    def test_valid_sentence_all_known_plus_target(self):
        """Sentence: الولد يأكل التفاحة (The boy eats the apple)
        Target: تفاحة (apple), Known: ولد (boy), يأكل (eats)
        """
        result = validate_sentence(
            arabic_text="الوَلَدُ يَأْكُلُ التُّفَّاحَةَ",
            target_bare="تفاحة",
            known_bare_forms={"ولد", "يأكل"},
        )
        assert result.valid is True
        assert result.target_found is True
        assert len(result.unknown_words) == 0

    def test_valid_with_function_words(self):
        """Sentence: الكتاب في البيت (The book is in the house)
        Target: بيت (house), Known: كتاب (book)
        Function words: في, ال
        """
        result = validate_sentence(
            arabic_text="الكِتَابُ فِي البَيْتِ",
            target_bare="بيت",
            known_bare_forms={"كتاب"},
        )
        assert result.valid is True
        assert result.target_found is True
        assert len(result.unknown_words) == 0

    def test_invalid_extra_unknown_word(self):
        """Sentence has 2 unknown words → invalid"""
        result = validate_sentence(
            arabic_text="الوَلَدُ يَأْكُلُ التُّفَّاحَةَ الكَبِيرَةَ",
            target_bare="تفاحة",
            known_bare_forms={"ولد", "يأكل"},
            # "كبيرة" (big) is not known
        )
        assert result.valid is False
        assert result.target_found is True
        assert len(result.unknown_words) == 1  # كبيرة

    def test_target_word_missing(self):
        """Target word not in sentence → invalid"""
        result = validate_sentence(
            arabic_text="الوَلَدُ يَأْكُلُ",
            target_bare="تفاحة",
            known_bare_forms={"ولد", "يأكل"},
        )
        assert result.valid is False
        assert result.target_found is False

    def test_empty_sentence(self):
        result = validate_sentence(
            arabic_text="",
            target_bare="كتاب",
            known_bare_forms={"ولد"},
        )
        assert result.valid is False
        assert result.target_found is False
        assert "Empty sentence" in result.issues

    def test_al_prefix_matching(self):
        """Known word 'كتاب' should match 'الكتاب' in sentence."""
        result = validate_sentence(
            arabic_text="الكِتَابُ جَمِيلٌ",
            target_bare="جميل",
            known_bare_forms={"كتاب"},
        )
        assert result.valid is True
        assert result.target_found is True

    def test_known_with_al_matches_bare(self):
        """Known word stored as 'الكتاب' matches 'كتاب' without ال."""
        result = validate_sentence(
            arabic_text="كِتَابٌ جَمِيلٌ",
            target_bare="جميل",
            known_bare_forms={"الكتاب"},
        )
        assert result.valid is True

    def test_diacritics_stripped_for_matching(self):
        """Diacritized text should still match bare forms."""
        result = validate_sentence(
            arabic_text="ذَهَبَ الوَلَدُ إِلَى المَدْرَسَةِ",
            target_bare="مدرسة",
            known_bare_forms={"ذهب", "ولد"},
        )
        assert result.valid is True
        assert result.target_found is True

    def test_function_words_not_counted(self):
        """Function words (في، من، على، etc.) don't count as unknown."""
        result = validate_sentence(
            arabic_text="هُوَ فِي البَيْتِ مِنَ الصَّبَاحِ",
            target_bare="صباح",
            known_bare_forms={"بيت"},
        )
        assert result.valid is True
        # هو, في, من are all function words
        assert len(result.function_words) >= 2

    def test_multiple_function_words(self):
        """Sentence full of function words + known + target."""
        result = validate_sentence(
            arabic_text="هَلْ هُوَ فِي البَيْتِ أَوْ فِي المَكْتَبَةِ",
            target_bare="مكتبة",
            known_bare_forms={"بيت"},
        )
        assert result.valid is True

    def test_classifications_complete(self):
        """Every token should be classified."""
        result = validate_sentence(
            arabic_text="الوَلَدُ فِي البَيْتِ",
            target_bare="بيت",
            known_bare_forms={"ولد"},
        )
        assert len(result.classifications) == 3
        categories = {c.category for c in result.classifications}
        assert "known" in categories
        assert "function_word" in categories
        assert "target_word" in categories

    def test_alef_normalization_in_matching(self):
        """Words with أ/إ/آ should match normalized forms."""
        result = validate_sentence(
            arabic_text="أَكَلَ الوَلَدُ",
            target_bare="اكل",  # normalized alef
            known_bare_forms={"ولد"},
        )
        assert result.valid is True
        assert result.target_found is True

    def test_realistic_beginner_sentence(self):
        """A realistic beginner sentence with mix of word types.
        'أنا أحب القهوة' (I love coffee)
        Target: قهوة, Known: أحب
        """
        result = validate_sentence(
            arabic_text="أَنَا أُحِبُّ القَهْوَةَ",
            target_bare="قهوة",
            known_bare_forms={"احب"},
        )
        assert result.valid is True

    def test_longer_sentence(self):
        """'الطالب يقرأ الكتاب في المكتبة كل يوم'
        (The student reads the book in the library every day)
        Target: مكتبة, Known: طالب, يقرأ, كتاب, يوم
        """
        result = validate_sentence(
            arabic_text="الطَّالِبُ يَقْرَأُ الكِتَابَ فِي المَكْتَبَةِ كُلَّ يَوْمٍ",
            target_bare="مكتبة",
            known_bare_forms={"طالب", "يقرأ", "كتاب", "يوم"},
        )
        assert result.valid is True
        assert result.target_found is True
        assert len(result.unknown_words) == 0


class TestFunctionWordsCompleteness:
    """Verify the function word list covers essential Arabic particles."""

    def test_prepositions_covered(self):
        prepositions = ["في", "من", "على", "الى", "عن", "مع", "بين", "حتى"]
        for p in prepositions:
            assert p in FUNCTION_WORDS or normalize_alef(p) in {
                normalize_alef(fw) for fw in FUNCTION_WORDS
            }, f"Missing preposition: {p}"

    def test_pronouns_covered(self):
        pronouns = ["هو", "هي", "هم", "نحن"]
        for p in pronouns:
            assert p in FUNCTION_WORDS, f"Missing pronoun: {p}"

    def test_demonstratives_covered(self):
        demos = ["هذا", "هذه", "ذلك", "تلك"]
        for d in demos:
            assert d in FUNCTION_WORDS, f"Missing demonstrative: {d}"

    def test_negation_covered(self):
        neg = ["لا", "لم", "لن", "ليس"]
        for n in neg:
            assert n in FUNCTION_WORDS, f"Missing negation: {n}"

    def test_question_words_covered(self):
        questions = ["هل", "ما", "ماذا", "كيف", "اين", "متى"]
        for q in questions:
            assert q in FUNCTION_WORDS or normalize_alef(q) in {
                normalize_alef(fw) for fw in FUNCTION_WORDS
            }, f"Missing question word: {q}"
