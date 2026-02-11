"""Tests for the sentence validator.

Uses hardcoded Arabic sentences with known word sets to verify
word classification and validation logic.
"""

import pytest

from app.services.sentence_validator import (
    FUNCTION_WORDS,
    TokenMapping,
    ValidationResult,
    _strip_clitics,
    build_lemma_lookup,
    compute_bare_form,
    map_tokens_to_lemmas,
    normalize_alef,
    normalize_arabic,
    sanitize_arabic_word,
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


class TestStripClitics:
    """Test the _strip_clitics helper directly."""

    def test_suffix_ha(self):
        # بيتها = بيت + ها
        stems = _strip_clitics("بيتها")
        assert "بيت" in stems

    def test_suffix_hum(self):
        # اولادهم = اولاد + هم
        stems = _strip_clitics("اولادهم")
        assert "اولاد" in stems

    def test_prefix_wa(self):
        # والكتب = و + ال + كتب
        stems = _strip_clitics("والكتب")
        assert "كتب" in stems or "الكتب" in stems

    def test_prefix_bal(self):
        # بالمدرسة = ب + ال + مدرسة
        stems = _strip_clitics("بالمدرسة")
        assert "مدرسة" in stems or "المدرسة" in stems

    def test_taa_marbuta_restoration(self):
        # مدرسته = مدرسة + ه (ة→ت before suffix)
        stems = _strip_clitics("مدرسته")
        assert "مدرسة" in stems

    def test_taa_marbuta_with_ha(self):
        # معلمتها = معلمة + ها
        stems = _strip_clitics("معلمتها")
        assert "معلمة" in stems

    def test_prefix_and_suffix_combined(self):
        # وبيته = و + بيت + ه (with ت→ة)
        stems = _strip_clitics("وبيته")
        assert "بيت" in stems or "بيتة" in stems

    def test_prefix_lil(self):
        # للمدرسة = لل + مدرسة
        stems = _strip_clitics("للمدرسة")
        assert "مدرسة" in stems or "المدرسة" in stems

    def test_short_word_not_stripped_too_aggressively(self):
        # Stripping should not produce empty or single-char stems
        stems = _strip_clitics("به")
        for s in stems:
            assert len(s) >= 2

    def test_no_match_returns_candidates(self):
        stems = _strip_clitics("كتاب")
        # No clitics to strip, but prefix-based candidates may exist
        assert "كتاب" not in stems  # original should not be in candidates

    def test_suffix_na(self):
        # معلمتنا = معلمة + نا
        stems = _strip_clitics("معلمتنا")
        assert "معلمة" in stems

    def test_prefix_fa(self):
        # فالبيت = ف + ال + بيت
        stems = _strip_clitics("فالبيت")
        assert "بيت" in stems or "البيت" in stems

    def test_prefix_kal(self):
        # كالماء = ك + ال + ماء
        stems = _strip_clitics("كالماء")
        assert "ماء" in stems or "الماء" in stems


class TestCliticIntegration:
    """Test clitic handling within validate_sentence()."""

    def test_possessive_suffix_ha(self):
        """بيتها should match known word بيت"""
        result = validate_sentence(
            arabic_text="بيتها كبير",
            target_bare="كبير",
            known_bare_forms={"بيت"},
        )
        assert result.valid is True
        assert result.target_found is True
        assert len(result.unknown_words) == 0

    def test_prefix_wa_al(self):
        """والكتب should match known word كتب"""
        result = validate_sentence(
            arabic_text="قرأت والكتب جميلة",
            target_bare="جميلة",
            known_bare_forms={"قرأت", "كتب"},
        )
        assert result.valid is True
        assert len(result.unknown_words) == 0

    def test_prefix_bal(self):
        """بالمدرسة should match known word مدرسة"""
        result = validate_sentence(
            arabic_text="ذهبت بالمدرسة",
            target_bare="ذهبت",
            known_bare_forms={"مدرسة"},
        )
        assert result.valid is True

    def test_taa_marbuta_possessive(self):
        """معلمتنا should match known word معلمة"""
        result = validate_sentence(
            arabic_text="معلمتنا جيدة",
            target_bare="جيدة",
            known_bare_forms={"معلمة"},
        )
        assert result.valid is True
        assert len(result.unknown_words) == 0

    def test_taa_marbuta_suffix_hu(self):
        """مدرسته should match known word مدرسة"""
        result = validate_sentence(
            arabic_text="مدرسته كبيرة",
            target_bare="كبيرة",
            known_bare_forms={"مدرسة"},
        )
        assert result.valid is True

    def test_prefix_lil(self):
        """للمدرسة should match known word مدرسة"""
        result = validate_sentence(
            arabic_text="ذهب للمدرسة",
            target_bare="ذهب",
            known_bare_forms={"مدرسة"},
        )
        assert result.valid is True

    def test_clitic_word_still_unknown_if_stem_not_known(self):
        """Clitic stripping shouldn't make truly unknown words pass."""
        result = validate_sentence(
            arabic_text="وكتابها جميل",
            target_bare="جميل",
            known_bare_forms=set(),  # no known words at all
        )
        assert result.valid is False
        assert len(result.unknown_words) >= 1

    def test_existing_tests_still_pass_with_diacritics(self):
        """Existing diacritized sentence should still work."""
        result = validate_sentence(
            arabic_text="الطَّالِبُ يَقْرَأُ الكِتَابَ فِي المَكْتَبَةِ كُلَّ يَوْمٍ",
            target_bare="مكتبة",
            known_bare_forms={"طالب", "يقرأ", "كتاب", "يوم"},
        )
        assert result.valid is True

    def test_prefix_with_diacritics(self):
        """Diacritized cliticized word should still be recognized."""
        result = validate_sentence(
            arabic_text="ذَهَبَ بِالْمَدْرَسَةِ",
            target_bare="ذهب",
            known_bare_forms={"مدرسة"},
        )
        assert result.valid is True

    def test_multiple_cliticized_words(self):
        """Multiple cliticized words in the same sentence."""
        result = validate_sentence(
            arabic_text="وبيتها بالمدرسة",
            target_bare="مدرسة",
            known_bare_forms={"بيت"},
        )
        assert result.valid is True
        # وبيتها matched via و+بيت+ها, بالمدرسة is target via بال+مدرسة
        assert result.target_found is True


class _FakeLemma:
    """Minimal lemma-like object for testing build_lemma_lookup."""
    def __init__(self, lemma_id: int, lemma_ar_bare: str, forms_json: dict | None = None):
        self.lemma_id = lemma_id
        self.lemma_ar_bare = lemma_ar_bare
        self.forms_json = forms_json


class TestBuildLemmaLookup:
    def test_basic_lookup(self):
        lemmas = [
            _FakeLemma(1, "كتاب"),
            _FakeLemma(2, "ولد"),
        ]
        lookup = build_lemma_lookup(lemmas)
        assert lookup["كتاب"] == 1
        assert lookup["الكتاب"] == 1
        assert lookup["ولد"] == 2
        assert lookup["الولد"] == 2

    def test_al_prefix_lemma(self):
        lemmas = [_FakeLemma(10, "القهوة")]
        lookup = build_lemma_lookup(lemmas)
        assert lookup["القهوة"] == 10
        assert lookup["قهوة"] == 10

    def test_alef_normalization(self):
        lemmas = [_FakeLemma(5, "أكل")]
        lookup = build_lemma_lookup(lemmas)
        assert lookup[normalize_alef("أكل")] == 5  # "اكل"


class TestBuildLemmaLookupInflectedForms:
    """Test that inflected forms from forms_json are indexed in the lookup."""

    def test_noun_plural(self):
        lemmas = [_FakeLemma(1, "مدرسة", forms_json={"plural": "مَدارِس"})]
        lookup = build_lemma_lookup(lemmas)
        assert lookup["مدارس"] == 1
        assert lookup["المدارس"] == 1

    def test_verb_present(self):
        lemmas = [_FakeLemma(2, "فهم", forms_json={"present": "يَفْهَمُ"})]
        lookup = build_lemma_lookup(lemmas)
        assert lookup["يفهم"] == 2

    def test_adjective_feminine(self):
        lemmas = [_FakeLemma(3, "جميل", forms_json={"feminine": "جَمِيلَة"})]
        lookup = build_lemma_lookup(lemmas)
        assert lookup["جميلة"] == 3

    def test_adjective_elative(self):
        lemmas = [_FakeLemma(4, "كبير", forms_json={"elative": "أَكْبَر"})]
        lookup = build_lemma_lookup(lemmas)
        assert lookup[normalize_alef("أكبر")] == 4

    def test_verb_masdar(self):
        lemmas = [_FakeLemma(5, "درس", forms_json={"masdar": "دِراسَة"})]
        lookup = build_lemma_lookup(lemmas)
        assert lookup["دراسة"] == 5

    def test_active_participle(self):
        lemmas = [_FakeLemma(6, "كتب", forms_json={"active_participle": "كاتِب"})]
        lookup = build_lemma_lookup(lemmas)
        assert lookup["كاتب"] == 6

    def test_base_form_not_overwritten_by_inflected(self):
        """If two lemmas share a form, the base-form lemma keeps priority."""
        lemmas = [
            _FakeLemma(1, "كتب"),
            _FakeLemma(2, "كاتب", forms_json={"plural": "كُتُب"}),
        ]
        lookup = build_lemma_lookup(lemmas)
        assert lookup["كتب"] == 1  # base form, not overwritten

    def test_none_forms_json(self):
        lemmas = [_FakeLemma(1, "بيت", forms_json=None)]
        lookup = build_lemma_lookup(lemmas)
        assert lookup["بيت"] == 1

    def test_empty_forms_json(self):
        lemmas = [_FakeLemma(1, "بيت", forms_json={})]
        lookup = build_lemma_lookup(lemmas)
        assert lookup["بيت"] == 1

    def test_no_forms_json_attr(self):
        """Lemma object without forms_json attribute should not break."""
        class _BareLemma:
            def __init__(self):
                self.lemma_id = 1
                self.lemma_ar_bare = "بيت"
        lookup = build_lemma_lookup([_BareLemma()])
        assert lookup["بيت"] == 1


class TestMapTokensToLemmas:
    def setup_method(self):
        self.lemmas = [
            _FakeLemma(1, "ولد"),
            _FakeLemma(2, "كتاب"),
            _FakeLemma(3, "يقرأ"),
        ]
        self.lookup = build_lemma_lookup(self.lemmas)

    def test_basic_sentence(self):
        tokens = tokenize("الوَلَدُ يَقْرَأُ الكِتَابَ")
        mappings = map_tokens_to_lemmas(tokens, self.lookup, target_lemma_id=2, target_bare="كتاب")
        assert len(mappings) == 3

        # الولد → lemma 1
        assert mappings[0].lemma_id == 1
        assert mappings[0].is_target is False

        # يقرأ → lemma 3
        assert mappings[1].lemma_id == 3

        # الكتاب → target (lemma 2)
        assert mappings[2].lemma_id == 2
        assert mappings[2].is_target is True

    def test_function_word_gets_none(self):
        tokens = tokenize("هُوَ يَقْرَأُ")
        mappings = map_tokens_to_lemmas(tokens, self.lookup, target_lemma_id=3, target_bare="يقرأ")
        assert mappings[0].is_function_word is True
        assert mappings[0].lemma_id is None
        assert mappings[1].is_target is True

    def test_function_word_maps_when_in_lookup(self):
        lemmas = [
            _FakeLemma(1, "هو"),
            _FakeLemma(2, "يقرأ"),
        ]
        lookup = build_lemma_lookup(lemmas)
        tokens = tokenize("هُوَ يَقْرَأُ")
        mappings = map_tokens_to_lemmas(tokens, lookup, target_lemma_id=2, target_bare="يقرأ")
        assert mappings[0].is_function_word is True
        assert mappings[0].lemma_id == 1
        assert mappings[1].is_target is True

    def test_unknown_word_gets_none(self):
        tokens = tokenize("يَقْرَأُ سَيَّارَة")
        mappings = map_tokens_to_lemmas(tokens, self.lookup, target_lemma_id=3, target_bare="يقرأ")
        assert mappings[0].is_target is True
        # سيارة not in lookup
        assert mappings[1].lemma_id is None
        assert mappings[1].is_function_word is False

    def test_cliticized_word_maps_to_lemma(self):
        tokens = tokenize("وَالكِتَابَ")
        mappings = map_tokens_to_lemmas(tokens, self.lookup, target_lemma_id=1, target_bare="ولد")
        # والكتاب should resolve to lemma 2 via clitic stripping
        assert mappings[0].lemma_id == 2

    def test_possessive_suffix_maps_to_lemma(self):
        lemmas = [_FakeLemma(10, "مدرسة")]
        lookup = build_lemma_lookup(lemmas)
        tokens = tokenize("مَدْرَسَتُهَا")
        mappings = map_tokens_to_lemmas(tokens, lookup, target_lemma_id=99, target_bare="xxx")
        # مدرستها → مدرسة via taa marbuta + suffix stripping
        assert mappings[0].lemma_id == 10

    def test_kanat_maps_to_kana_not_anta(self):
        """كانت should map to كان's lemma_id, NOT أنت via false clitic stripping."""
        lemmas = [
            _FakeLemma(1, "كان"),
            _FakeLemma(2, "انت"),
            _FakeLemma(3, "كتاب"),
        ]
        lookup = build_lemma_lookup(lemmas)
        tokens = tokenize("كَانَتْ الكِتَابَ")
        mappings = map_tokens_to_lemmas(tokens, lookup, target_lemma_id=3, target_bare="كتاب")
        # كانت is a function word, should resolve to كان (id=1) via FUNCTION_WORD_FORMS
        assert mappings[0].is_function_word is True
        assert mappings[0].lemma_id == 1  # كان, NOT 2 (أنت)

    def test_function_word_no_clitic_stripping(self):
        """Function words should use direct-only lookup, not clitic stripping."""
        lemmas = [
            _FakeLemma(1, "ليس"),
            _FakeLemma(2, "كتاب"),
        ]
        lookup = build_lemma_lookup(lemmas)
        tokens = tokenize("لَيْسَتْ الكِتَابَ")
        mappings = map_tokens_to_lemmas(tokens, lookup, target_lemma_id=2, target_bare="كتاب")
        # ليست should resolve to ليس (id=1), not be clitic-stripped
        assert mappings[0].is_function_word is True
        assert mappings[0].lemma_id == 1


class TestFunctionWordForms:
    """Test FUNCTION_WORD_FORMS dict and _is_function_word with conjugated forms."""

    def test_kanat_is_function_word(self):
        from app.services.sentence_validator import _is_function_word
        assert _is_function_word("كانت") is True

    def test_kanat_diacritized_is_function_word(self):
        from app.services.sentence_validator import _is_function_word
        assert _is_function_word("كَانَتْ") is True

    def test_laysat_is_function_word(self):
        from app.services.sentence_validator import _is_function_word
        assert _is_function_word("ليست") is True

    def test_yakun_is_function_word(self):
        from app.services.sentence_validator import _is_function_word
        assert _is_function_word("يكون") is True

    def test_kanoo_is_function_word(self):
        from app.services.sentence_validator import _is_function_word
        assert _is_function_word("كانوا") is True

    def test_build_lookup_includes_function_word_forms(self):
        """FUNCTION_WORD_FORMS should be indexed in build_lemma_lookup."""
        lemmas = [_FakeLemma(1, "كان")]
        lookup = build_lemma_lookup(lemmas)
        # كانت should map to كان's lemma_id
        assert lookup.get("كانت") == 1
        assert lookup.get("كانوا") == 1
        assert lookup.get("يكون") == 1


class TestSanitizeArabicWord:
    def test_clean_word_unchanged(self):
        result, warnings = sanitize_arabic_word("كِتَاب")
        assert result == "كِتَاب"
        assert warnings == []

    def test_trailing_question_mark(self):
        result, warnings = sanitize_arabic_word("النَّرْوِيج؟")
        assert result == "النَّرْوِيج"
        assert warnings == []

    def test_trailing_period(self):
        result, warnings = sanitize_arabic_word("سنة.")
        assert result == "سنة"
        assert warnings == []

    def test_trailing_exclamation(self):
        result, warnings = sanitize_arabic_word("مرحباً!")
        assert result == "مرحباً"
        assert warnings == []

    def test_trailing_arabic_comma(self):
        result, warnings = sanitize_arabic_word("نعم،")
        assert result == "نعم"
        assert warnings == []

    def test_parentheses_stripped(self):
        result, warnings = sanitize_arabic_word("(كتاب)")
        assert result == "كتاب"
        assert warnings == []

    def test_slash_separated(self):
        result, warnings = sanitize_arabic_word("الصَّفُّ/السَّنَةُ")
        assert result == "الصَّفُّ"
        assert "slash_split" in warnings

    def test_multi_word_phrase(self):
        result, warnings = sanitize_arabic_word("الْمَدْرَسة الثّانَوِيّة")
        assert result == "الْمَدْرَسة"
        assert "multi_word" in warnings

    def test_multi_word_with_trailing_punct(self):
        result, warnings = sanitize_arabic_word("روضة الأطفال.")
        assert result == "روضة"
        assert "multi_word" in warnings

    def test_empty_string(self):
        result, warnings = sanitize_arabic_word("")
        assert result == ""
        assert "empty" in warnings

    def test_only_punctuation(self):
        result, warnings = sanitize_arabic_word("؟!")
        assert result == ""
        assert "empty_after_clean" in warnings

    def test_diacritics_preserved(self):
        result, warnings = sanitize_arabic_word("كِتَابٌ!")
        assert result == "كِتَابٌ"
        assert warnings == []

    def test_whitespace_only(self):
        result, warnings = sanitize_arabic_word("   ")
        assert result == ""
        assert "empty" in warnings

    def test_multiple_trailing_punctuation(self):
        result, warnings = sanitize_arabic_word("كتاب...")
        assert result == "كتاب"
        assert warnings == []


class TestComputeBareForm:
    def test_basic(self):
        assert compute_bare_form("كِتَاب") == "كتاب"

    def test_with_alef_variants(self):
        assert compute_bare_form("أَكَلَ") == "اكل"

    def test_with_tatweel(self):
        assert compute_bare_form("كـتـاب") == "كتاب"
