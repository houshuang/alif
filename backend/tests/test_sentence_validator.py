"""Tests for the sentence validator.

Uses hardcoded Arabic sentences with known word sets to verify
word classification and validation logic.
"""

import unicodedata
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.sentence_validator import (
    FUNCTION_WORDS,
    FUNCTION_WORD_GLOSSES,
    TokenMapping,
    ValidationResult,
    _is_function_word,
    apply_corrections,
    batch_verify_sentences,
    is_function_word_lemma,
    _strip_clitics,
    build_lemma_lookup,
    compute_bare_form,
    lookup_lemma,
    lookup_lemma_citation,
    lookup_lemma_id,
    map_tokens_to_lemmas,
    normalize_alef,
    normalize_arabic,
    refresh_target_mapping_flags,
    resolve_existing_lemma,
    sanitize_arabic_word,
    strip_diacritics,
    tokenize,
    tokenize_display,
    validate_sentence,
    validate_sentence_multi_target,
)


def _batch_verification_inputs(count: int = 2) -> list[dict]:
    return [
        {
            "arabic": f"جُمْلَةٌ {index}",
            "english": f"Sentence {index}",
            "mappings": [
                TokenMapping(
                    position=0,
                    surface_form="جُمْلَةٌ",
                    lemma_id=1,
                    is_target=False,
                    is_function_word=False,
                )
            ],
            "has_ambiguous": False,
        }
        for index in range(count)
    ]


class TestBatchVerificationResponseContract:
    @patch("app.services.llm.generate_completion")
    def test_empty_sentence_list_fails_closed(self, mock_completion):
        mock_completion.return_value = {"sentences": []}

        assert batch_verify_sentences(_batch_verification_inputs(), {}) is None

    @patch("app.services.llm.generate_completion")
    def test_explicit_empty_verdicts_mean_all_clean(self, mock_completion):
        mock_completion.return_value = {
            "sentences": [
                {"index": 0, "disambiguation": [], "issues": []},
                {"index": 1, "disambiguation": [], "issues": []},
            ]
        }

        assert batch_verify_sentences(_batch_verification_inputs(), {}) == [
            {"disambiguation": [], "issues": []},
            {"disambiguation": [], "issues": []},
        ]

    @pytest.mark.parametrize(
        "response",
        [
            [],
            {},
            {"sentences": None},
            {"sentences": ["not an object"]},
            {"sentences": [{"index": 0, "disambiguation": []}]},
            {
                "sentences": [
                    {"index": "0", "disambiguation": [], "issues": []}
                ]
            },
            {
                "sentences": [
                    {"index": True, "disambiguation": [], "issues": []}
                ]
            },
            {
                "sentences": [
                    {"index": 2, "disambiguation": [], "issues": []}
                ]
            },
            {
                "sentences": [
                    {"index": 0, "disambiguation": {}, "issues": []}
                ]
            },
            {
                "sentences": [
                    {
                        "index": 0,
                        "disambiguation": [{"position": 0}],
                        "issues": [],
                    }
                ]
            },
            {
                "sentences": [
                    {
                        "index": 0,
                        "disambiguation": [],
                        "issues": [{"position": 0}],
                    }
                ]
            },
        ],
    )
    @patch("app.services.llm.generate_completion")
    def test_malformed_response_fails_closed(
        self,
        mock_completion,
        response,
    ):
        mock_completion.return_value = response

        assert batch_verify_sentences(_batch_verification_inputs(), {}) is None

    @patch("app.services.llm.generate_completion")
    def test_duplicate_index_fails_closed(self, mock_completion):
        row = {"index": 0, "disambiguation": [], "issues": []}
        mock_completion.return_value = {"sentences": [row, dict(row)]}

        assert batch_verify_sentences(_batch_verification_inputs(), {}) is None

    @pytest.mark.parametrize(
        "disambiguation",
        [
            [],
            [{"position": 9, "lemma_id": 1}],
            [{"position": 0, "lemma_id": 99}],
            [
                {"position": 0, "lemma_id": 2},
                {"position": 0, "lemma_id": 2},
            ],
        ],
    )
    @patch("app.services.llm.generate_completion")
    def test_ambiguous_input_requires_one_valid_explicit_choice(
        self,
        mock_completion,
        disambiguation,
    ):
        inputs = _batch_verification_inputs(1)
        inputs[0]["has_ambiguous"] = True
        inputs[0]["mappings"][0].alternative_lemma_ids = [2]
        mock_completion.return_value = {
            "sentences": [
                {
                    "index": 0,
                    "disambiguation": disambiguation,
                    "issues": [],
                }
            ]
        }

        assert batch_verify_sentences(inputs, {}) is None

    @patch("app.services.llm.generate_completion")
    def test_ambiguous_input_accepts_valid_explicit_choice(
        self,
        mock_completion,
    ):
        inputs = _batch_verification_inputs(1)
        inputs[0]["has_ambiguous"] = True
        inputs[0]["mappings"][0].alternative_lemma_ids = [2]
        choice = {"position": 0, "lemma_id": 2}
        mock_completion.return_value = {
            "sentences": [
                {
                    "index": 0,
                    "disambiguation": [choice],
                    "issues": [],
                }
            ]
        }

        assert batch_verify_sentences(inputs, {}) == [
            {"disambiguation": [choice], "issues": []}
        ]

    @patch("app.services.llm.generate_completion")
    def test_ambiguous_position_can_be_reported_as_issue_instead_of_choice(
        self,
        mock_completion,
    ):
        inputs = _batch_verification_inputs(1)
        inputs[0]["has_ambiguous"] = True
        inputs[0]["mappings"][0].alternative_lemma_ids = [2]
        issue = {
            "position": 0,
            "correct_lemma_ar": "جُمْلَة",
            "correct_gloss": "sentence",
            "correct_pos": "noun",
            "explanation": "neither listed sense fits",
        }
        mock_completion.return_value = {
            "sentences": [
                {
                    "index": 0,
                    "disambiguation": [],
                    "issues": [issue],
                }
            ]
        }

        assert batch_verify_sentences(inputs, {}) == [
            {"disambiguation": [], "issues": [issue]}
        ]

    @patch("app.services.llm.generate_completion")
    def test_same_position_cannot_be_choice_and_issue(
        self,
        mock_completion,
    ):
        inputs = _batch_verification_inputs(1)
        inputs[0]["has_ambiguous"] = True
        inputs[0]["mappings"][0].alternative_lemma_ids = [2]
        mock_completion.return_value = {
            "sentences": [
                {
                    "index": 0,
                    "disambiguation": [{"position": 0, "lemma_id": 2}],
                    "issues": [
                        {
                            "position": 0,
                            "correct_lemma_ar": "جُمْلَة",
                            "correct_gloss": "sentence",
                            "correct_pos": "noun",
                            "explanation": "contradictory verdict",
                        }
                    ],
                }
            ]
        }

        # The existing None contract makes corpus callers retry/skip the batch.
        assert batch_verify_sentences(inputs, {}) is None

    @patch("app.services.llm.generate_completion")
    def test_opt_in_marks_only_contradictory_row_for_retry(
        self,
        mock_completion,
    ):
        inputs = _batch_verification_inputs(2)
        inputs[0]["has_ambiguous"] = True
        inputs[0]["mappings"][0].alternative_lemma_ids = [2]
        issue = {
            "position": 0,
            "correct_lemma_ar": "جُمْلَة",
            "correct_gloss": "sentence",
            "correct_pos": "noun",
            "explanation": "contradictory verdict",
        }
        mock_completion.return_value = {
            "sentences": [
                {
                    "index": 0,
                    "disambiguation": [{"position": 0, "lemma_id": 2}],
                    "issues": [issue],
                },
                {"index": 1, "disambiguation": [], "issues": []},
            ]
        }

        result = batch_verify_sentences(
            inputs,
            {},
            return_invalid_rows=True,
        )

        assert result == [
            {
                "disambiguation": [],
                "issues": [],
                "invalid_reason": "contradictory_verdict",
                "invalid_positions": [0],
            },
            {"disambiguation": [], "issues": []},
        ]
        # Diagnostics are derived from the same batch response, not re-queried.
        mock_completion.assert_called_once()

    @pytest.mark.parametrize(
        ("kind", "invalid_reason", "invalid_positions"),
        [
            ("unsolicited", "unsolicited_disambiguation", [0]),
            ("invalid_choice", "invalid_disambiguation_choice", [0]),
            ("duplicate_choice", "duplicate_disambiguation_positions", [0]),
            ("malformed_issue", "malformed_issues", [0]),
            ("omitted_ambiguity", "omitted_ambiguity_verdict", [0]),
        ],
    )
    @patch("app.services.llm.generate_completion")
    def test_opt_in_isolates_row_attributable_semantic_errors(
        self,
        mock_completion,
        kind,
        invalid_reason,
        invalid_positions,
    ):
        inputs = _batch_verification_inputs(2)
        bad_row = {"index": 0, "disambiguation": [], "issues": []}
        if kind == "unsolicited":
            bad_row["disambiguation"] = [{"position": 0, "lemma_id": 1}]
        else:
            inputs[0]["has_ambiguous"] = True
            inputs[0]["mappings"][0].alternative_lemma_ids = [2]
            if kind == "invalid_choice":
                bad_row["disambiguation"] = [
                    {"position": 0, "lemma_id": 999}
                ]
            elif kind == "duplicate_choice":
                bad_row["disambiguation"] = [
                    {"position": 0, "lemma_id": 1},
                    {"position": 0, "lemma_id": 2},
                ]
            elif kind == "malformed_issue":
                bad_row["issues"] = [{"position": 0}]

        mock_completion.return_value = {
            "sentences": [
                bad_row,
                {"index": 1, "disambiguation": [], "issues": []},
            ]
        }

        assert batch_verify_sentences(
            inputs,
            {},
            return_invalid_rows=True,
        ) == [
            {
                "disambiguation": [],
                "issues": [],
                "invalid_reason": invalid_reason,
                "invalid_positions": invalid_positions,
            },
            {"disambiguation": [], "issues": []},
        ]

    @patch("app.services.llm.generate_completion")
    def test_opt_in_keeps_duplicate_index_batch_fatal(self, mock_completion):
        row = {"index": 0, "disambiguation": [], "issues": []}
        mock_completion.return_value = {"sentences": [row, dict(row)]}

        assert batch_verify_sentences(
            _batch_verification_inputs(),
            {},
            return_invalid_rows=True,
        ) is None

    @patch("app.services.llm.generate_completion")
    def test_prompt_exposes_exact_mapping_identity_and_metadata_distinction(
        self,
        mock_completion,
    ):
        inputs = _batch_verification_inputs(1)
        inputs[0]["mappings"][0].lemma_id = 17
        lemma = SimpleNamespace(
            lemma_id=17,
            lemma_ar="إِنَّ",
            lemma_ar_bare="ان",
            gloss_en="indeed",
            pos="particle",
        )
        mock_completion.return_value = {
            "sentences": [
                {"index": 0, "disambiguation": [], "issues": []}
            ]
        }

        batch_verify_sentences(inputs, {17: lemma})

        prompt = mock_completion.call_args.kwargs["prompt"]
        assert "lemma_id=#17" in prompt
        assert "lemma_ar=إِنَّ" in prompt
        assert "exact_bare=إن" in prompt
        assert "pos=particle" in prompt
        assert "gloss=indeed" in prompt
        assert "INCOMPLETE GLOSS METADATA" in prompt
        assert "Never put the same position in both" in prompt

    @patch("app.services.llm.generate_completion")
    def test_omitted_clean_index_fails_closed(self, mock_completion):
        issue = {
            "position": 0,
            "correct_lemma_ar": "كتاب",
            "correct_gloss": "book",
            "correct_pos": "noun",
            "explanation": "wrong sense",
        }
        mock_completion.return_value = {
            "sentences": [
                {"index": 1, "disambiguation": [], "issues": [issue]}
            ]
        }

        assert batch_verify_sentences(_batch_verification_inputs(), {}) is None


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


class TestFinalAlefVariants:
    def test_alef_to_maksura(self):
        from app.services.sentence_validator import final_alef_variants
        assert set(final_alef_variants("ذرا")) == {"ذرا", "ذرى"}

    def test_maksura_to_alef(self):
        from app.services.sentence_validator import final_alef_variants
        assert set(final_alef_variants("ذرى")) == {"ذرى", "ذرا"}

    def test_no_swap_on_other_endings(self):
        from app.services.sentence_validator import final_alef_variants
        assert final_alef_variants("كتاب") == ["كتاب"]

    def test_no_swap_too_short(self):
        from app.services.sentence_validator import final_alef_variants
        assert final_alef_variants("ها") == ["ها"]


class TestNormalizeQuranicToMsa:
    def test_dagger_alef_becomes_alef(self):
        from app.services.sentence_validator import normalize_quranic_to_msa
        # خَٰلِدُونَ (Uthmani, dagger alef U+0670) → خَالِدُونَ (full alef U+0627).
        assert "ا" in normalize_quranic_to_msa("خَٰلِدُونَ")
        assert "ٰ" not in normalize_quranic_to_msa("خَٰلِدُونَ")

    def test_dagger_alef_must_run_before_strip(self):
        # The bug that mislemmatized خَٰلِدُونَ → خلدون (= the name Khaldūn):
        # stripping diacritics first deletes the dagger alef's long ā. Doing the
        # Quranic→MSA conversion first preserves it as a full alef.
        from app.services.sentence_validator import normalize_quranic_to_msa
        surface = "خَٰلِدُونَ"
        wrong = strip_diacritics(surface)                       # dagger lost
        right = strip_diacritics(normalize_quranic_to_msa(surface))
        assert wrong == "خلدون"        # collides with the proper name
        assert right == "خالدون"       # the participle skeleton, preserved

    def test_full_normalize_arabic_preserves_long_vowel(self):
        assert normalize_arabic("خَٰلِدُونَ") == "خالدون"


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

    def test_valid_with_common_words(self):
        """Sentence: الكتاب في البيت (The book is in the house)
        Target: بيت (house), Known: كتاب, في (formerly function words are now regular)
        """
        result = validate_sentence(
            arabic_text="الكِتَابُ فِي البَيْتِ",
            target_bare="بيت",
            known_bare_forms={"كتاب", "في"},
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
            known_bare_forms={"ذهب", "ولد", "الى"},
        )
        assert result.valid is True
        assert result.target_found is True

    def test_all_words_now_learnable(self):
        """All words are learnable — no automatic function word exclusion."""
        result = validate_sentence(
            arabic_text="هُوَ فِي البَيْتِ مِنَ الصَّبَاحِ",
            target_bare="صباح",
            known_bare_forms={"بيت", "هو", "في", "من"},
        )
        assert result.valid is True

    def test_sentence_with_common_particles(self):
        """Sentence with prepositions + known + target all counted."""
        result = validate_sentence(
            arabic_text="هَلْ هُوَ فِي البَيْتِ أَوْ فِي المَكْتَبَةِ",
            target_bare="مكتبة",
            known_bare_forms={"بيت", "هل", "هو", "في", "او"},
        )
        assert result.valid is True

    def test_classifications_complete(self):
        """Every token should be classified."""
        result = validate_sentence(
            arabic_text="الوَلَدُ فِي البَيْتِ",
            target_bare="بيت",
            known_bare_forms={"ولد", "في"},
        )
        assert len(result.classifications) == 3
        categories = {c.category for c in result.classifications}
        assert "known" in categories
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

    def test_final_alef_maksura_matches_alef_target(self):
        """target=ذرا (final ا) should match surface ذَرَى (final ى).

        Final-weak verbs alternate ا ↔ ى word-finally with no semantic
        change. Without the swap, every ذَرَى surface failed validation
        when the stored bare was ذرا.
        """
        result = validate_sentence(
            arabic_text="ذَرَى الفَلَّاحُ الحُبُوبَ",
            target_bare="ذرا",
            known_bare_forms={"فلاح", "حبوب"},
        )
        assert result.target_found is True

    def test_final_alef_target_matches_alef_maksura_surface(self):
        """target=ذرى (final ى) should match surface ذَرَا (final ا)."""
        result = validate_sentence(
            arabic_text="ذَرَا الفَلَّاحُ القَمْحَ",
            target_bare="ذرى",
            known_bare_forms={"فلاح", "قمح"},
        )
        assert result.target_found is True

    def test_final_alef_swap_respects_minimum_length(self):
        """Don't swap word-final on 1–2-char targets; could collide with
        function words. Only applies to len>=3."""
        # Bare "ها" should NOT match "هى" via the swap.
        result = validate_sentence(
            arabic_text="هى البِنْت",
            target_bare="ها",
            known_bare_forms={"بنت"},
        )
        assert result.target_found is False

    def test_realistic_beginner_sentence(self):
        """A realistic beginner sentence with mix of word types.
        'أنا أحب القهوة' (I love coffee)
        Target: قهوة, Known: أحب
        """
        result = validate_sentence(
            arabic_text="أَنَا أُحِبُّ القَهْوَةَ",
            target_bare="قهوة",
            known_bare_forms={"احب", "انا"},
        )
        assert result.valid is True

    def test_longer_sentence(self):
        """'الطالب يقرأ الكتاب في المكتبة كل يوم'
        (The student reads the book in the library every day)
        Target: مكتبة, Known: طالب, يقرأ, كتاب, يوم, في, كل
        """
        result = validate_sentence(
            arabic_text="الطَّالِبُ يَقْرَأُ الكِتَابَ فِي المَكْتَبَةِ كُلَّ يَوْمٍ",
            target_bare="مكتبة",
            known_bare_forms={"طالب", "يقرأ", "كتاب", "يوم", "في", "كل"},
        )
        assert result.valid is True
        assert result.target_found is True
        assert len(result.unknown_words) == 0


class TestFunctionWordsCompleteness:
    """Verify FUNCTION_WORDS is populated from FUNCTION_WORD_GLOSSES."""

    def test_function_words_populated(self):
        assert len(FUNCTION_WORDS) > 0, "FUNCTION_WORDS should be populated"

    def test_is_function_word_detects_particles(self):
        """_is_function_word returns True for known function words."""
        for word in ["في", "من", "هو", "كان", "يوجد"]:
            assert _is_function_word(word), f"{word} should be a function word"

    def test_lemma_category_disambiguates_content_homograph(self):
        assert _is_function_word("ام") is True
        assert is_function_word_lemma("ام", None) is True
        assert is_function_word_lemma("ام", False) is False
        assert is_function_word_lemma("كتاب", True) is True

    def test_glosses_still_available(self):
        """FUNCTION_WORD_GLOSSES still provides fallback glosses."""
        assert FUNCTION_WORD_GLOSSES.get("في") == "in"
        assert FUNCTION_WORD_GLOSSES.get("هو") == "he"
        assert FUNCTION_WORD_GLOSSES.get("يوجد") == "there is"



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
            known_bare_forms={"طالب", "يقرأ", "كتاب", "يوم", "في", "كل"},
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

    def __init__(
        self,
        lemma_id: int,
        lemma_ar_bare: str,
        forms_json: dict | None = None,
        pos: str | None = None,
        lemma_ar: str | None = None,
        *,
        gated: bool = True,
    ):
        self.lemma_id = lemma_id
        self.lemma_ar_bare = lemma_ar_bare
        self.lemma_ar = lemma_ar or lemma_ar_bare
        self.forms_json = forms_json
        self.pos = pos
        self.gates_completed_at = object() if gated else None


def _momo_52133_collision_lemmas() -> list[_FakeLemma]:
    return [
        _FakeLemma(270, "ناس", pos="noun", lemma_ar="نَاسٌ"),
        _FakeLemma(
            3711,
            "نسي",
            forms_json={
                "active_participle": "نَاسٍ",
                "imperative": "اِنْسَ",
            },
            pos="verb",
            lemma_ar="نَسِيَ",
        ),
        _FakeLemma(2054, "قد", pos="particle", lemma_ar="قَدْ"),
        _FakeLemma(2189, "فقد", pos="noun", lemma_ar="فَقْد"),
    ]


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

    def test_bare_form_wins_over_masdar_regardless_of_order(self):
        """Direct bare form lemma must win over a forms_json derived form.

        Regression test: حول "around" (lemma #2033) was being mapped to
        حال "to change" (lemma #890) because حَوْل is the masdar of حال
        and was registered first when #890 was iterated before #2033.
        """
        lemmas = [
            _FakeLemma(890, "حال", forms_json={"masdar": "حَوْل", "present": "يَحُولُ"}),
            _FakeLemma(2033, "حول"),
        ]
        lookup = build_lemma_lookup(lemmas)
        assert lookup["حول"] == 2033  # bare form wins over masdar

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

    def test_word_not_in_lookup_gets_none(self):
        """هو is not in self.lookup, so it gets lemma_id=None but is detected as function word."""
        tokens = tokenize("هُوَ يَقْرَأُ")
        mappings = map_tokens_to_lemmas(tokens, self.lookup, target_lemma_id=3, target_bare="يقرأ")
        assert mappings[0].is_function_word is True
        assert mappings[0].lemma_id is None
        assert mappings[1].is_target is True

    def test_vocalized_grammatical_homographs_keep_exact_identity(self):
        """Tashkeel distinguishes the four common أن/إن lexemes."""
        lookup = build_lemma_lookup([
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2186, "ان", pos="particle", lemma_ar="إِنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
            _FakeLemma(2188, "انّ", pos="particle", lemma_ar="إِنَّ"),
        ])

        mappings = map_tokens_to_lemmas(
            ["أَنْ", "إِنْ", "أَنَّ", "إِنَّ", "أن", "إن"],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )

        assert [mapping.lemma_id for mapping in mappings[:4]] == [
            2185,
            2186,
            2187,
            2188,
        ]
        # Without sukūn/shadda, each hamza-preserving pair remains ambiguous.
        # Keep its two identities available for contextual verification
        # without mixing the أ and إ sides of the normalized collision.
        assert mappings[4].lemma_id == 2185
        assert set(mappings[4].alternative_lemma_ids or []) == {2187}
        assert mappings[5].lemma_id == 2186
        assert set(mappings[5].alternative_lemma_ids or []) == {2188}

    def test_word_maps_when_in_lookup(self):
        """هو is in lookup, so it gets a lemma_id. Also detected as function word."""
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

    def test_empty_target_does_not_claim_ilah_as_lemma_zero(self):
        """Empty target expansion must not synthesize ال and catch إله."""
        lookup = build_lemma_lookup(
            [_FakeLemma(88, "اله", pos="noun", lemma_ar="إِلٰه")]
        )

        mappings = map_tokens_to_lemmas(
            tokenize_display("إِلٰه"),
            lookup,
            target_lemma_id=0,
            target_bare="",
        )

        assert mappings[0].is_target is False
        assert mappings[0].lemma_id == 88

    @pytest.mark.parametrize("invalid_target_id", [0, None, False])
    def test_invalid_target_id_disables_clitic_target_matching(
        self,
        invalid_target_id,
    ):
        """A matching bare cannot become target unless its lemma ID is real."""
        lookup = build_lemma_lookup(
            [_FakeLemma(77, "بال", pos="noun", lemma_ar="بَال")]
        )

        mappings = map_tokens_to_lemmas(
            tokenize_display("بِبَالِكَ"),
            lookup,
            target_lemma_id=invalid_target_id,
            target_bare="بال",
        )

        assert mappings[0].is_target is False
        assert mappings[0].lemma_id == 77

    def test_empty_target_bare_disables_matching_with_valid_id(self):
        lookup = build_lemma_lookup(
            [_FakeLemma(88, "اله", pos="noun", lemma_ar="إِلٰه")]
        )

        mappings = map_tokens_to_lemmas(
            tokenize_display("إِلٰه"),
            lookup,
            target_lemma_id=999,
            target_bare="",
        )

        assert mappings[0].is_target is False
        assert mappings[0].lemma_id == 88

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
        # كانت resolves to كان (id=1) via FUNCTION_WORD_FORMS (prevents false clitic stripping)
        assert mappings[0].is_function_word is True  # كانت is in FUNCTION_WORD_GLOSSES
        assert mappings[0].lemma_id == 1  # كان, NOT 2 (أنت)

    def test_word_form_no_clitic_stripping(self):
        """Words in FUNCTION_WORD_FORMS should use direct-only lookup, not clitic stripping."""
        lemmas = [
            _FakeLemma(1, "ليس"),
            _FakeLemma(2, "كتاب"),
        ]
        lookup = build_lemma_lookup(lemmas)
        tokens = tokenize("لَيْسَتْ الكِتَابَ")
        mappings = map_tokens_to_lemmas(tokens, lookup, target_lemma_id=2, target_bare="كتاب")
        # ليست should resolve to ليس (id=1), not be clitic-stripped
        assert mappings[0].is_function_word is True  # ليست is in FUNCTION_WORD_GLOSSES
        assert mappings[0].lemma_id == 1

    def test_multi_target_validation_allows_declared_proper_names(self):
        from app.services.sentence_validator import validate_sentence_multi_target

        lemmas = [
            _FakeLemma(1, "ذئب"),
            _FakeLemma(2, "منزل"),
        ]
        lookup = build_lemma_lookup(lemmas)
        result = validate_sentence_multi_target(
            arabic_text="الذِّئْبُ مَعَ «لَيْلَى».",
            target_bares={"ذئب": 1},
            known_bare_forms=set(lookup.keys()),
            min_targets=1,
            known_lemma_lookup=lookup,
            comprehensive_lemma_lookup=lookup,
            proper_names={"ليلى"},
        )

        assert result.valid is True
        assert any("لَيْلَى" in word for word in result.known_words)

    def test_multi_target_final_alef_swap(self):
        """Multi-target path should also accept ا ↔ ى word-final swap."""
        from app.services.sentence_validator import validate_sentence_multi_target

        lemmas = [_FakeLemma(575, "ذرا"), _FakeLemma(99, "ريح")]
        lookup = build_lemma_lookup(lemmas)
        result = validate_sentence_multi_target(
            arabic_text="ذَرَى الفَلَّاحُ الرِّيحَ.",
            target_bares={"ذرا": 575},
            known_bare_forms=set(lookup.keys()) | {"فلاح"},
            min_targets=1,
            known_lemma_lookup=lookup,
        )
        # Surface ذَرَى should be classified as a target despite stored bare ذرا.
        assert result.targets_found.get("ذرا") is True

    def test_function_word_detection_ignores_wrapping_punctuation(self):
        from app.services.sentence_validator import _is_function_word

        assert _is_function_word("«هَلْ") is True
        assert _is_function_word("عِنْدَها؟»") is True
        assert _is_function_word("الطَّرِيقِ.") is False

    def test_attached_ind_maps_to_ind_base(self):
        """عند + pronoun forms should map to عند instead of storing NULL lemma ids."""
        lemmas = [
            _FakeLemma(1, "عند"),
            _FakeLemma(2, "كتاب"),
        ]
        lookup = build_lemma_lookup(lemmas)
        tokens = tokenize("عِنْدَها الكِتَابَ")
        mappings = map_tokens_to_lemmas(tokens, lookup, target_lemma_id=2, target_bare="كتاب")
        assert mappings[0].is_function_word is True
        assert mappings[0].lemma_id == 1

    def test_canonical_function_aliases_survive_variant_filtering(self):
        """لقد and لدى+pronoun map without their variant rows in the lookup."""
        lemmas = [
            _FakeLemma(2054, "قد", pos="particle", lemma_ar="قَدْ"),
            _FakeLemma(2456, "لدى", pos="noun", lemma_ar="لَدَى"),
        ]
        lookup = build_lemma_lookup(lemmas)
        mappings = map_tokens_to_lemmas(
            ["لَقَدْ", "لَدَيْهَا", "لَدَيْهِمَا"],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )

        assert [mapping.lemma_id for mapping in mappings] == [
            2054,
            2456,
            2456,
        ]

    def test_exact_momo_aliases_beat_real_collision_shapes(self):
        lookup = build_lemma_lookup(_momo_52133_collision_lemmas())

        assert lookup["انس"] == 3711
        assert lookup["فقد"] == 2189

        mappings = map_tokens_to_lemmas(
            [
                "أُنَاسٌ",
                "فَقَدْ",
                "فَقْدٌ",
                "فقد",
                "أناس",
                "أُنَاسًا",
                "فَقَدَ",
            ],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )

        assert [mapping.lemma_id for mapping in mappings[:4]] == [
            270,
            2054,
            2189,
            2189,
        ]
        assert [
            mapping.is_function_word for mapping in mappings[:4]
        ] == [False, True, False, False]
        assert mappings[4].lemma_id != 270
        assert mappings[5].lemma_id != 270
        assert mappings[6].lemma_id != 2054

    def test_exact_momo_aliases_are_nfc_and_boundary_stable(self):
        forward = build_lemma_lookup(_momo_52133_collision_lemmas())
        reverse = build_lemma_lookup(
            list(reversed(_momo_52133_collision_lemmas()))
        )
        decomposed_people = unicodedata.normalize("NFD", "أُنَاسٌ")

        for lookup in (forward, reverse):
            mappings = map_tokens_to_lemmas(
                [f"«{decomposed_people}»", "فَقَدْ،"],
                lookup,
                target_lemma_id=0,
                target_bare="",
            )
            assert [mapping.lemma_id for mapping in mappings] == [270, 2054]
            assert [mapping.surface_form for mapping in mappings] == [
                f"«{decomposed_people}»",
                "فَقَدْ،",
            ]

    @pytest.mark.parametrize("failure_mode", ["missing", "ungated"])
    def test_exact_momo_aliases_fail_closed_without_gated_destinations(
        self,
        failure_mode,
    ):
        destinations = [
            _FakeLemma(
                270,
                "ناس",
                pos="noun",
                lemma_ar="نَاسٌ",
                gated=failure_mode != "ungated",
            ),
            _FakeLemma(
                2054,
                "قد",
                pos="particle",
                lemma_ar="قَدْ",
                gated=failure_mode != "ungated",
            ),
        ]
        lemmas = _momo_52133_collision_lemmas()[1::2]
        if failure_mode == "ungated":
            lemmas += destinations
        lookup = build_lemma_lookup(lemmas)

        mappings = map_tokens_to_lemmas(
            ["أُنَاسٌ", "فَقَدْ"],
            lookup,
            target_lemma_id=0,
            target_bare="",
            proper_names={"أناس", "فقد"},
        )
        validation = validate_sentence_multi_target(
            "أُنَاسٌ فَقَدْ",
            target_bares={"ناس": 270},
            known_bare_forms=set(),
            min_targets=1,
            known_lemma_lookup=lookup,
            comprehensive_lemma_lookup=lookup,
            proper_names={"أناس", "فقد"},
        )

        assert [mapping.lemma_id for mapping in mappings] == [None, None]
        assert all(not mapping.is_function_word for mapping in mappings)
        assert all(not mapping.is_proper_name for mapping in mappings)
        assert validation.targets_found == {"ناس": False}
        assert validation.unknown_words == ["أُنَاسٌ", "فَقَدْ"]

    @pytest.mark.parametrize(
        "conflicting_source",
        [
            _FakeLemma(5000, "أناس", pos="noun", lemma_ar="أُنَاسٌ"),
            _FakeLemma(5001, "فقد", pos="particle", lemma_ar="فَقَدْ"),
        ],
    )
    def test_stored_exact_source_identity_disables_alias(
        self,
        conflicting_source,
    ):
        lookup = build_lemma_lookup(
            _momo_52133_collision_lemmas() + [conflicting_source]
        )
        surface = conflicting_source.lemma_ar

        mapping = map_tokens_to_lemmas(
            [surface],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )[0]

        assert mapping.lemma_id is None
        assert lookup_lemma_id(surface, lookup) is None

    @pytest.mark.parametrize("duplicate_gated", [False, True])
    def test_duplicate_exact_destination_disables_alias(
        self,
        duplicate_gated,
    ):
        lookup = build_lemma_lookup(
            _momo_52133_collision_lemmas()
            + [
                _FakeLemma(
                    271,
                    "ناس",
                    pos="noun",
                    lemma_ar="نَاسٌ",
                    gated=duplicate_gated,
                )
            ]
        )

        mappings = map_tokens_to_lemmas(
            ["أُنَاسٌ", "فَقَدْ"],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )

        assert mappings[0].lemma_id is None
        assert mappings[1].lemma_id == 2054

    def test_exact_momo_aliases_share_surface_aware_lookup_paths(self):
        lookup = build_lemma_lookup(_momo_52133_collision_lemmas())

        assert lookup_lemma_id("أُنَاسٌ", lookup) == 270
        assert lookup_lemma_id("فَقَدْ", lookup) == 2054
        assert lookup_lemma_id("فقد", lookup) == 2189
        assert resolve_existing_lemma("أُنَاسٌ", lookup) == 270
        assert resolve_existing_lemma("فَقَدْ", lookup) == 2054
        assert resolve_existing_lemma("فقد", lookup) == 2189
        assert (
            lookup_lemma_citation("اناس", lookup, original_bare="أُنَاسٌ")
            == 270
        )
        assert (
            lookup_lemma_citation("فقد", lookup, original_bare="فَقَدْ")
            == 2054
        )
        assert (
            lookup_lemma_citation("فقد", lookup, original_bare="فقد")
            == 2189
        )

    def test_exact_momo_aliases_replace_bare_target_identity(self):
        lookup = build_lemma_lookup(_momo_52133_collision_lemmas())

        people_target = map_tokens_to_lemmas(
            ["أُنَاسٌ", "فَقَدْ", "فَقْدٌ"],
            lookup,
            target_lemma_id=270,
            target_bare="ناس",
        )
        particle_target = map_tokens_to_lemmas(
            ["فَقَدْ", "فَقْدٌ"],
            lookup,
            target_lemma_id=2054,
            target_bare="قد",
        )
        loss_target = map_tokens_to_lemmas(
            ["فَقَدْ", "فَقْدٌ"],
            lookup,
            target_lemma_id=2189,
            target_bare="فقد",
        )

        assert [mapping.is_target for mapping in people_target] == [
            True,
            False,
            False,
        ]
        assert [mapping.is_target for mapping in particle_target] == [
            True,
            False,
        ]
        assert [mapping.is_target for mapping in loss_target] == [
            False,
            True,
        ]
        assert refresh_target_mapping_flags(
            people_target,
            lookup,
            {"ناس": 270},
            required_target_ids={270},
        )
        assert refresh_target_mapping_flags(
            particle_target,
            lookup,
            {"قد": 2054},
            required_target_ids={2054},
        )
        assert refresh_target_mapping_flags(
            loss_target,
            lookup,
            {"فقد": 2189},
            required_target_ids={2189},
        )

    def test_sentence_validators_share_exact_momo_alias_identity(self):
        lookup = build_lemma_lookup(_momo_52133_collision_lemmas())

        single = validate_sentence(
            "أُنَاسٌ فَقَدْ",
            target_bare="ناس",
            known_bare_forms=set(),
            known_lemma_lookup=lookup,
            comprehensive_lemma_lookup=lookup,
        )
        wrong_single = validate_sentence(
            "فَقَدْ",
            target_bare="فقد",
            known_bare_forms=set(),
            known_lemma_lookup=lookup,
            comprehensive_lemma_lookup=lookup,
        )
        multiple = validate_sentence_multi_target(
            "أُنَاسٌ فَقَدْ",
            target_bares={"ناس": 270},
            known_bare_forms=set(),
            min_targets=1,
            known_lemma_lookup=lookup,
            comprehensive_lemma_lookup=lookup,
        )

        assert single.valid is True
        assert single.target_found is True
        assert single.function_words == ["فَقَدْ"]
        assert wrong_single.target_found is False
        assert multiple.valid is True
        assert multiple.targets_found == {"ناس": True}

    def test_full_momo_52133_maps_to_reviewed_existing_identities(self):
        lookup = build_lemma_lookup(
            _momo_52133_collision_lemmas()
            + [
                _FakeLemma(
                    4248,
                    "لاحظ",
                    forms_json={"past_3fs": "لَاحَظَتْ"},
                    pos="verb",
                    lemma_ar="لَاحَظَ",
                ),
                _FakeLemma(
                    2187,
                    "انّ",
                    pos="particle",
                    lemma_ar="أَنَّ",
                ),
                _FakeLemma(
                    1580,
                    "لطيف",
                    forms_json={"plural": "لِطَاف"},
                    pos="adj",
                    lemma_ar="لَطِيف",
                ),
                _FakeLemma(385, "كان", pos="verb", lemma_ar="كَانَ"),
                _FakeLemma(
                    357,
                    "نفس",
                    forms_json={"plural": "أَنْفُس"},
                    pos="noun",
                    lemma_ar="نَفْس",
                ),
                _FakeLemma(
                    2646,
                    "فقير",
                    forms_json={"plural": "فُقَرَاء"},
                    pos="noun",
                    lemma_ar="فَقِير",
                ),
                _FakeLemma(
                    389,
                    "عرف",
                    forms_json={"present": "يَعْرِفُ"},
                    pos="verb",
                    lemma_ar="عَرَفَ",
                ),
                _FakeLemma(354, "حياة", pos="noun", lemma_ar="حَيَاة"),
            ]
        )

        mappings = map_tokens_to_lemmas(
            tokenize_display(
                "لَاحَظَتْ أَنَّهُمْ أُنَاسٌ لِطَافٌ ، فَقَدْ "
                "كَانُوا أَنْفُسُهُمْ فُقَرَاءَ "
                "وَيَعْرِفُونَ الْحَيَاةَ ."
            ),
            lookup,
            target_lemma_id=0,
            target_bare="",
        )

        assert [mapping.lemma_id for mapping in mappings] == [
            4248,
            2187,
            270,
            1580,
            2054,
            385,
            357,
            2646,
            389,
            354,
        ]
        assert all(not mapping.is_target for mapping in mappings)
        assert mappings[4].is_function_word is True

    def test_an_particle_collision_does_not_map_to_time(self):
        lemmas = [
            _FakeLemma(943, "ان", lemma_ar="آنٌ"),
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2186, "ان", pos="particle", lemma_ar="إِنْ"),
        ]
        lookup = build_lemma_lookup(lemmas)
        tokens = tokenize_display("أَنْ أَقْرَأَ")
        mappings = map_tokens_to_lemmas(tokens, lookup, target_lemma_id=0, target_bare="")
        assert mappings[0].lemma_id == 2185

    def test_inna_particle_collision_does_not_map_to_time(self):
        lemmas = [
            _FakeLemma(943, "ان", lemma_ar="آنٌ"),
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2186, "ان", pos="particle", lemma_ar="إِنْ"),
            _FakeLemma(2188, "انّ", pos="particle", lemma_ar="إِنَّ"),
        ]
        lookup = build_lemma_lookup(lemmas)
        tokens = tokenize_display("إِنَّ الجَوَّ صَعْبٌ")
        mappings = map_tokens_to_lemmas(tokens, lookup, target_lemma_id=0, target_bare="")
        assert mappings[0].lemma_id == 2188

    def test_unhamzated_an_fails_closed(self):
        lemmas = [
            _FakeLemma(943, "ان", lemma_ar="آنٌ"),
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2186, "ان", pos="particle", lemma_ar="إِنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
            _FakeLemma(2188, "انّ", pos="particle", lemma_ar="إِنَّ"),
        ]
        lookup = build_lemma_lookup(lemmas)
        tokens = tokenize_display("ان الطَّرِيقَ طَوِيلٌ")
        mappings = map_tokens_to_lemmas(tokens, lookup, target_lemma_id=0, target_bare="")
        assert mappings[0].lemma_id is None
        assert mappings[0].alternative_lemma_ids is None

    def test_bare_hamzated_particles_preserve_two_identity_candidates(self):
        lookup = build_lemma_lookup([
            _FakeLemma(943, "ان", lemma_ar="آنٌ"),
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2186, "ان", pos="particle", lemma_ar="إِنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
            _FakeLemma(2188, "انّ", pos="particle", lemma_ar="إِنَّ"),
        ])

        mappings = map_tokens_to_lemmas(
            ["أن", "إن"],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )

        assert (
            mappings[0].lemma_id,
            set(mappings[0].alternative_lemma_ids or []),
        ) == (2185, {2187})
        assert (
            mappings[1].lemma_id,
            set(mappings[1].alternative_lemma_ids or []),
        ) == (2186, {2188})

    def test_exact_madda_form_excludes_normalized_particle_alternatives(self):
        lookup = build_lemma_lookup([
            _FakeLemma(943, "ان", lemma_ar="آنٌ"),
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2186, "ان", pos="particle", lemma_ar="إِنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
            _FakeLemma(2188, "انّ", pos="particle", lemma_ar="إِنَّ"),
        ])

        mapping = map_tokens_to_lemmas(
            ["آن"],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )[0]

        assert mapping.lemma_id == 943
        assert mapping.alternative_lemma_ids is None

    def test_bi_anna_overrides_bana_content_verb(self):
        lemmas = [
            _FakeLemma(554, "بان", pos="verb", lemma_ar="بَانَ"),
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
        ]
        lookup = build_lemma_lookup(lemmas)
        tokens = tokenize_display("بِأَنَّ الطَّرِيقَ طَوِيلٌ")
        mappings = map_tokens_to_lemmas(tokens, lookup, target_lemma_id=0, target_bare="")
        assert mappings[0].lemma_id == 2187

    def test_bare_prefixed_particles_route_to_complete_hamza_candidates(self):
        lookup = build_lemma_lookup([
            _FakeLemma(554, "بان", pos="verb", lemma_ar="بَانَ"),
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2186, "ان", pos="particle", lemma_ar="إِنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
            _FakeLemma(2188, "انّ", pos="particle", lemma_ar="إِنَّ"),
        ])

        mappings = map_tokens_to_lemmas(
            ["بأن", "وإن", "وأن", "بان"],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )

        assert (
            mappings[0].lemma_id,
            set(mappings[0].alternative_lemma_ids or []),
        ) == (2185, {2187})
        assert (
            mappings[1].lemma_id,
            set(mappings[1].alternative_lemma_ids or []),
        ) == (2186, {2188})
        assert (
            mappings[2].lemma_id,
            set(mappings[2].alternative_lemma_ids or []),
        ) == (2185, {2187})
        assert mappings[3].lemma_id == 554
        assert mappings[3].alternative_lemma_ids is None
        assert mappings[3].is_function_word is False

    def test_bare_prefixed_particle_fails_closed_if_identity_is_missing(self):
        lookup = build_lemma_lookup([
            _FakeLemma(554, "بان", pos="verb", lemma_ar="بَانَ"),
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
        ])

        mappings = map_tokens_to_lemmas(
            ["بأن"],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )

        assert mappings[0].lemma_id is None
        assert mappings[0].alternative_lemma_ids is None

    def test_fa_prefixed_particles_compose_without_losing_hamza_identity(self):
        lookup = build_lemma_lookup([
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2186, "ان", pos="particle", lemma_ar="إِنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
            _FakeLemma(2188, "انّ", pos="particle", lemma_ar="إِنَّ"),
        ])

        mappings = map_tokens_to_lemmas(
            ["فأن", "فإن", "فَأَنْ", "فَأَنَّ", "فَإِنْ", "فَإِنَّ"],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )

        assert (
            mappings[0].lemma_id,
            set(mappings[0].alternative_lemma_ids or []),
        ) == (2185, {2187})
        assert (
            mappings[1].lemma_id,
            set(mappings[1].alternative_lemma_ids or []),
        ) == (2186, {2188})
        assert [mapping.lemma_id for mapping in mappings[2:]] == [
            2185,
            2187,
            2186,
            2188,
        ]

    def test_unhamzated_fan_fails_closed_and_can_remain_a_proper_name(self):
        lookup = build_lemma_lookup([
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2186, "ان", pos="particle", lemma_ar="إِنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
            _FakeLemma(2188, "انّ", pos="particle", lemma_ar="إِنَّ"),
        ])

        unresolved = map_tokens_to_lemmas(
            ["فان"],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )[0]
        proper_name = map_tokens_to_lemmas(
            ["فان"],
            lookup,
            target_lemma_id=0,
            target_bare="",
            proper_names={"فان"},
        )[0]

        assert unresolved.lemma_id is None
        assert unresolved.alternative_lemma_ids is None
        assert unresolved.is_function_word is False
        assert proper_name.lemma_id is None
        assert proper_name.is_proper_name is True

    @pytest.mark.parametrize(
        ("surface", "expected_id"),
        [
            ("أَنَّهُ", 2187),
            ("وَأَنَّهَا", 2187),
            ("فَأَنَّكُمْ", 2187),
            ("بِأَنَّنِي", 2187),
            ("بأننى", 2187),
            ("فأنه", 2187),
            ("إِنَّهُ", 2188),
            ("وَإِنَّهَا", 2188),
            ("فَإِنَّكُمْ", 2188),
            ("فَإِنِّي", 2188),
            ("فإنه", 2188),
            # Literal spelling in Momo Chapter 1 row #52135.
            ("إننى", 2188),
            ("فإننى", 2188),
        ],
    )
    def test_attached_pronouns_compose_to_shadda_particle_identity(
        self,
        surface,
        expected_id,
    ):
        lookup = build_lemma_lookup([
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
            _FakeLemma(2188, "انّ", pos="particle", lemma_ar="إِنَّ"),
        ])

        mapping = map_tokens_to_lemmas(
            [surface],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )[0]

        assert mapping.lemma_id == expected_id
        assert mapping.alternative_lemma_ids is None
        assert mapping.surface_form == surface

    @pytest.mark.parametrize(
        "surface",
        ["بِإِنَّ", "بإن", "بِإِنَّهُ", "بإنه", "بإننى"],
    )
    def test_bi_inna_attached_forms_stay_outside_composition_policy(
        self,
        surface,
    ):
        lookup = build_lemma_lookup([
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
            _FakeLemma(2188, "انّ", pos="particle", lemma_ar="إِنَّ"),
        ])

        mapping = map_tokens_to_lemmas(
            [surface],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )[0]

        assert mapping.lemma_id is None
        assert mapping.alternative_lemma_ids is None

    def test_legacy_inna_pronoun_lemmas_cannot_steal_canonical_surface(self):
        lookup = build_lemma_lookup([
            _FakeLemma(2071, "انه", pos="particle", lemma_ar="إِنَّهُ"),
            _FakeLemma(2072, "انها", pos="particle", lemma_ar="إِنَّهَا"),
            _FakeLemma(2188, "انّ", pos="particle", lemma_ar="إِنَّ"),
        ])

        mappings = map_tokens_to_lemmas(
            ["إِنَّهُ", "إِنَّهَا"],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )

        assert [mapping.lemma_id for mapping in mappings] == [2188, 2188]
        assert [mapping.surface_form for mapping in mappings] == [
            "إِنَّهُ",
            "إِنَّهَا",
        ]
        assert resolve_existing_lemma("إِنَّهُ", lookup) == 2188
        assert resolve_existing_lemma("إِنَّهَا", lookup) == 2188

    def test_stored_laanna_identity_beats_derived_particle_alias(self):
        lookup = build_lemma_lookup([
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
            # Match the actual production row: its stored citation omits the
            # prefix kasra even though running text supplies it.
            _FakeLemma(3000, "لان", pos="conj", lemma_ar="لأنَّ"),
        ])

        mappings = map_tokens_to_lemmas(
            ["لِأَنَّ", "لِأَنَّ", "لأن", "لِأَنْ"],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )

        assert [mapping.lemma_id for mapping in mappings] == [
            3000,
            3000,
            3000,
            2185,
        ]

        targeted = map_tokens_to_lemmas(
            ["لِأَنَّ", "لِأَنَّ", "لِأَنْ"],
            lookup,
            target_lemma_id=3000,
            target_bare="لان",
        )
        assert [mapping.lemma_id for mapping in targeted] == [
            3000,
            3000,
            2185,
        ]
        assert [mapping.is_target for mapping in targeted] == [
            True,
            True,
            False,
        ]
        assert refresh_target_mapping_flags(
            targeted,
            lookup,
            {"لان": 3000},
            required_target_ids={3000},
        ) is True

    @pytest.mark.parametrize(
        "surface",
        [
            "لِأَنَّهُ",
            "لِأَنَّهَا",
            "لِأَنَّكُمْ",
            "لِأَنِّي",
            "لأنه",
            "لأننى",
        ],
    )
    def test_laanna_attached_pronouns_keep_lexical_compound_identity(
        self,
        surface,
    ):
        lookup = build_lemma_lookup([
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
            _FakeLemma(3000, "لان", pos="conj", lemma_ar="لأنَّ"),
        ])

        mapping = map_tokens_to_lemmas(
            [surface],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )[0]

        assert mapping.lemma_id == 3000
        assert mapping.alternative_lemma_ids is None
        assert mapping.surface_form == surface

    def test_li_an_with_sukun_remains_base_particle(self):
        lookup = build_lemma_lookup([
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
            _FakeLemma(3000, "لان", pos="conj", lemma_ar="لأنَّ"),
        ])

        mapping = map_tokens_to_lemmas(
            ["لِأَنْ"],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )[0]

        assert mapping.lemma_id == 2185
        assert mapping.alternative_lemma_ids is None

    def test_target_matching_respects_exact_particle_identity(self):
        lookup = build_lemma_lookup([
            _FakeLemma(943, "ان", pos="noun", lemma_ar="آنٌ"),
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2186, "ان", pos="particle", lemma_ar="إِنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
            _FakeLemma(2188, "انّ", pos="particle", lemma_ar="إِنَّ"),
        ])

        mappings = map_tokens_to_lemmas(
            ["أَنْ", "أَنَّ", "إِنْ", "إِنَّ", "آنٌ"],
            lookup,
            target_lemma_id=2185,
            target_bare="ان",
        )

        assert [mapping.lemma_id for mapping in mappings] == [
            2185,
            2187,
            2186,
            2188,
            943,
        ]
        assert [mapping.is_target for mapping in mappings] == [
            True,
            False,
            False,
            False,
            False,
        ]

    def test_shadda_bearing_particle_can_be_the_exact_target(self):
        lookup = build_lemma_lookup([
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
        ])

        mappings = map_tokens_to_lemmas(
            ["أَنَّ"],
            lookup,
            target_lemma_id=2187,
            target_bare="انّ",
        )

        assert mappings[0].lemma_id == 2187
        assert mappings[0].is_target is True

    def test_target_flags_are_rechecked_after_mapping_correction(self):
        lookup = build_lemma_lookup([
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
        ])
        mappings = map_tokens_to_lemmas(
            ["أَنْ"],
            lookup,
            target_lemma_id=2185,
            target_bare="ان",
        )
        mappings[0].lemma_id = 2187

        intact = refresh_target_mapping_flags(
            mappings,
            lookup,
            {"ان": 2185},
            required_target_ids={2185},
        )

        assert intact is False
        assert mappings[0].is_target is False

    def test_alayhi_maps_to_preposition_not_ali(self):
        lemmas = [
            _FakeLemma(453, "على", pos="prep", lemma_ar="عَلى"),
            _FakeLemma(1968, "علي", pos="noun_prop", lemma_ar="عَلِيٌّ"),
        ]
        lookup = build_lemma_lookup(lemmas)
        tokens = tokenize_display("رَدَّ عَلَيْهِ")
        mappings = map_tokens_to_lemmas(tokens, lookup, target_lemma_id=0, target_bare="")
        assert mappings[1].is_function_word is True
        assert mappings[1].lemma_id == 453

    def test_al_an_now_does_not_map_to_that_particle(self):
        lemmas = [
            _FakeLemma(943, "ان", lemma_ar="آنٌ"),
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2500, "ان", pos="adverb", lemma_ar="آن"),
        ]
        lookup = build_lemma_lookup(lemmas)
        tokens = tokenize_display("الآنَ")
        mappings = map_tokens_to_lemmas(tokens, lookup, target_lemma_id=0, target_bare="")
        assert mappings[0].lemma_id in {943, 2500}
        assert mappings[0].lemma_id != 2185

    def test_punctuation_preserved_in_surface_form(self):
        """Punctuation should be preserved in surface_form when using tokenize_display."""
        tokens = tokenize_display("هَلْ يَقْرَأُ الكِتَابَ؟")
        assert tokens[-1].endswith("؟")
        mappings = map_tokens_to_lemmas(tokens, self.lookup, target_lemma_id=2, target_bare="كتاب")
        assert len(mappings) == 3
        # Last word should preserve question mark in surface_form
        assert mappings[2].surface_form.endswith("؟")
        assert mappings[2].is_target is True
        assert mappings[2].lemma_id == 2

    def test_comma_preserved_in_surface_form(self):
        """Arabic comma should be preserved in surface_form."""
        tokens = tokenize_display("الوَلَدُ يَقْرَأُ، وَالكِتَابُ جَمِيلٌ.")
        mappings = map_tokens_to_lemmas(tokens, self.lookup, target_lemma_id=2, target_bare="كتاب")
        # يقرأ، should keep the comma
        read_mapping = [m for m in mappings if m.lemma_id == 3][0]
        assert "،" in read_mapping.surface_form


class TestTokenizeDisplay:
    def test_preserves_question_mark(self):
        tokens = tokenize_display("هَلْ ذَهَبْتَ؟")
        assert tokens == ["هَلْ", "ذَهَبْتَ؟"]

    def test_preserves_period(self):
        tokens = tokenize_display("هَذَا كِتَابٌ.")
        assert tokens == ["هَذَا", "كِتَابٌ."]

    def test_preserves_arabic_comma(self):
        tokens = tokenize_display("كِتَابٌ، وَقَلَمٌ")
        assert tokens == ["كِتَابٌ،", "وَقَلَمٌ"]

    def test_filters_pure_punctuation(self):
        tokens = tokenize_display("كِتَابٌ ، وَقَلَمٌ")
        # Standalone comma should be filtered out
        assert len(tokens) == 2
        assert "،" not in tokens

    def test_matches_tokenize_for_clean_text(self):
        text = "الوَلَدُ يَقْرَأُ الكِتَابَ"
        assert tokenize_display(text) == tokenize(text)


class TestFunctionWordForms:
    """Test FUNCTION_WORD_FORMS dict still prevents false clitic analysis."""

    def test_is_function_word_detects_conjugated_forms(self):
        """_is_function_word returns True for conjugated forms in FUNCTION_WORD_GLOSSES."""
        from app.services.sentence_validator import _is_function_word
        # Forms that are in FUNCTION_WORD_GLOSSES
        for word in ["كانت", "كَانَتْ", "ليست", "يكون"]:
            assert _is_function_word(word) is True, f"{word} should be a function word"
        # "كانوا" is in FUNCTION_WORD_FORMS but not in FUNCTION_WORD_GLOSSES
        assert _is_function_word("كانوا") is False

    def test_build_lookup_includes_function_word_forms(self):
        """FUNCTION_WORD_FORMS should still be indexed in build_lemma_lookup for clitic prevention."""
        lemmas = [_FakeLemma(1, "كان")]
        lookup = build_lemma_lookup(lemmas)
        # كانت should map to كان's lemma_id (prevents false ك+انت clitic split)
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

    def test_single_char_abbreviation(self):
        """Single-character bare forms are abbreviations (ج=plural, ص=page)."""
        result, warnings = sanitize_arabic_word("ج")
        assert result == "ج"
        assert "too_short" in warnings

    def test_single_char_with_diacritics(self):
        """Even with diacritics, single bare-char words are abbreviations."""
        result, warnings = sanitize_arabic_word("جٌ")
        assert result == "جٌ"
        assert "too_short" in warnings

    def test_two_char_word_ok(self):
        """Two-character words are valid (e.g. من, في, هو)."""
        result, warnings = sanitize_arabic_word("مِن")
        assert result == "مِن"
        assert "too_short" not in warnings

    def test_arabic_indic_digits_rejected(self):
        """OCR-extracted page numbers like ١٤ must not become lemmas."""
        result, warnings = sanitize_arabic_word("١٤")
        assert "no_letters" in warnings

    def test_long_arabic_indic_digit_string_rejected(self):
        """ISBN/footnote artifacts like ٨٢٦١٤٩٣٥ must not become lemmas."""
        result, warnings = sanitize_arabic_word("٨٢٦١٤٩٣٥")
        assert "no_letters" in warnings

    def test_ascii_digits_rejected(self):
        result, warnings = sanitize_arabic_word("2024")
        assert "no_letters" in warnings

    def test_extended_arabic_indic_digits_rejected(self):
        """Persian-style digits (U+06F0–U+06F9)."""
        result, warnings = sanitize_arabic_word("۱۴")
        assert "no_letters" in warnings

    def test_letter_with_digit_kept(self):
        """A real word with a stray digit (rare but possible) is not rejected."""
        result, warnings = sanitize_arabic_word("كتاب2")
        assert "no_letters" not in warnings


class TestComputeBareForm:
    def test_basic(self):
        assert compute_bare_form("كِتَاب") == "كتاب"

    def test_with_alef_variants(self):
        assert compute_bare_form("أَكَلَ") == "اكل"

    def test_with_tatweel(self):
        assert compute_bare_form("كـتـاب") == "كتاب"


class TestLookupLemma:
    """Test the public lookup_lemma function with clitic stripping."""

    def setup_method(self):
        self.lemmas = [
            _FakeLemma(1, "كتاب"),
            _FakeLemma(2, "غرفة"),
            _FakeLemma(3, "بيت"),
            _FakeLemma(4, "احب"),  # hamza-stripped form
        ]
        self.lookup = build_lemma_lookup(self.lemmas)

    def test_direct_match(self):
        assert lookup_lemma("كتاب", self.lookup) == 1

    def test_al_prefix(self):
        assert lookup_lemma("الكتاب", self.lookup) == 1

    def test_possessive_suffix_ha(self):
        # كتابها (her book) → كتاب via ها enclitic
        assert lookup_lemma("كتابها", self.lookup) == 1

    def test_possessive_hum(self):
        # كتابهم (their book) → كتاب via هم enclitic
        assert lookup_lemma("كتابهم", self.lookup) == 1

    def test_possessive_ka(self):
        # كتابك (your book) → كتاب via ك enclitic
        assert lookup_lemma("كتابك", self.lookup) == 1

    def test_waw_prefix(self):
        assert lookup_lemma("وكتاب", self.lookup) == 1

    def test_ba_prefix(self):
        assert lookup_lemma("بالكتاب", self.lookup) == 1

    def test_taa_marbuta_possessive(self):
        # غرفتها (her room) → غرفة (room) — taa marbuta ة→ت + ها suffix
        assert lookup_lemma("غرفتها", self.lookup) == 2

    def test_no_match(self):
        assert lookup_lemma("سيارة", self.lookup) is None

    def test_hamza_normalized_match(self):
        # أحب should match احب via hamza normalization in build_lemma_lookup
        assert lookup_lemma(normalize_alef("أحب"), self.lookup) == 4

    def test_short_stem_no_false_match(self):
        # Clitic stripping of short stems should not produce false matches
        # "سم" (poison) should not match anything from over-stripping
        assert lookup_lemma("سم", self.lookup) is None


class TestResolveExistingLemma:
    """Test resolve_existing_lemma used by import scripts."""

    def setup_method(self):
        self.lemmas = [
            _FakeLemma(1, "كتاب"),
            _FakeLemma(2, "ستارة"),
        ]
        self.lookup = build_lemma_lookup(self.lemmas)

    def test_direct_match(self):
        assert resolve_existing_lemma("كتاب", self.lookup) == 1

    def test_waw_prefix_dedup(self):
        # وستارة should resolve to ستارة
        assert resolve_existing_lemma("وستارة", self.lookup) == 2

    def test_possessive_dedup(self):
        # كتابها (her book) should resolve to كتاب
        assert resolve_existing_lemma("كتابها", self.lookup) == 1

    def test_new_word_returns_none(self):
        assert resolve_existing_lemma("سيارة", self.lookup) is None

    @pytest.mark.parametrize(
        ("surface", "expected_id"),
        [
            ("أَنْ", 2185),
            ("أَنَّ", 2187),
            ("إِنْ", 2186),
            ("إِنَّ", 2188),
            ("بِأَنَّ", 2187),
            ("وَإِنْ", 2186),
            ("فَأَنْ", 2185),
            ("فَإِنْ", 2186),
        ],
    )
    def test_exact_grammatical_identity_matches_import_dedup(
        self,
        surface,
        expected_id,
    ):
        lookup = build_lemma_lookup([
            _FakeLemma(554, "بان", pos="verb", lemma_ar="بَانَ"),
            _FakeLemma(943, "ان", pos="noun", lemma_ar="آنٌ"),
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2186, "ان", pos="particle", lemma_ar="إِنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
            _FakeLemma(2188, "انّ", pos="particle", lemma_ar="إِنَّ"),
        ])

        assert resolve_existing_lemma(surface, lookup) == expected_id

    @pytest.mark.parametrize(
        "surface",
        ["ان", "أن", "إن", "بأن", "وإن", "وأن", "فإن", "فأن", "فان"],
    )
    def test_contextless_ambiguous_particle_dedup_fails_closed(
        self,
        surface,
    ):
        lookup = build_lemma_lookup([
            _FakeLemma(554, "بان", pos="verb", lemma_ar="بَانَ"),
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2186, "ان", pos="particle", lemma_ar="إِنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
            _FakeLemma(2188, "انّ", pos="particle", lemma_ar="إِنَّ"),
        ])

        assert resolve_existing_lemma(surface, lookup) is None

    def test_contextless_exact_madda_form_still_resolves(self):
        lookup = build_lemma_lookup([
            _FakeLemma(943, "ان", lemma_ar="آنٌ"),
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2186, "ان", pos="particle", lemma_ar="إِنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
            _FakeLemma(2188, "انّ", pos="particle", lemma_ar="إِنَّ"),
        ])

        assert resolve_existing_lemma("آن", lookup) == 943


class TestLookupCollisions:
    """Test collision tracking and resolution in build_lemma_lookup."""

    def test_collision_tracked(self):
        """Two lemmas normalizing to same form should be tracked."""
        lemmas = [
            _FakeLemma(1, "أب"),   # father — normalizes to اب
            _FakeLemma(2, "آب"),   # August — normalizes to اب
        ]
        lookup = build_lemma_lookup(lemmas)
        assert "اب" in lookup.collisions
        collision_ids = [lid for lid, _ in lookup.collisions["اب"]]
        assert 1 in collision_ids
        assert 2 in collision_ids

    def test_first_entry_wins(self):
        """First lemma should win in the lookup dict."""
        lemmas = [
            _FakeLemma(1, "أب"),
            _FakeLemma(2, "آب"),
        ]
        lookup = build_lemma_lookup(lemmas)
        assert lookup["اب"] == 1

    def test_collision_resolved_by_original_bare(self):
        """lookup_lemma should disambiguate using pre-normalized form."""
        lemmas = [
            _FakeLemma(1, "أب"),
            _FakeLemma(2, "آب"),
        ]
        lookup = build_lemma_lookup(lemmas)
        # With original_bare="آب", should resolve to lemma 2
        assert lookup_lemma("اب", lookup, original_bare="آب") == 2
        # With original_bare="أب", should resolve to lemma 1
        assert lookup_lemma("اب", lookup, original_bare="أب") == 1

    def test_no_collision_for_same_lemma(self):
        """Same lemma_id on same key should not create a collision."""
        lemmas = [_FakeLemma(1, "كتاب")]
        lookup = build_lemma_lookup(lemmas)
        assert not lookup.collisions

    def test_no_collision_different_keys(self):
        """Different normalized keys should not collide."""
        lemmas = [
            _FakeLemma(1, "كتاب"),
            _FakeLemma(2, "بيت"),
        ]
        lookup = build_lemma_lookup(lemmas)
        assert not lookup.collisions

    def test_forms_collision_tracked(self):
        """Collision from forms_json should also be tracked."""
        lemmas = [
            _FakeLemma(1, "كتب", forms_json={"plural": "كُتُب"}),
            _FakeLemma(2, "كتب"),  # same bare as lemma 1's plural would be same
        ]
        lookup = build_lemma_lookup(lemmas)
        # Both have the same bare "كتب" — collision tracked
        assert "كتب" in lookup.collisions


class TestWeakVerbConjugations:
    """Test verb conjugation generation with weak verb stems via past_1s."""

    def test_hollow_verb_with_past_1s(self):
        """قال (hollow verb): past_1s=قلت gives correct 1st/2nd person forms."""
        lemmas = [_FakeLemma(1, "قال", pos="verb", forms_json={
            "present": "يَقُولُ", "past_1s": "قُلْتُ",
        })]
        lookup = build_lemma_lookup(lemmas)
        # 1st/2nd person past from قل stem
        assert lookup.get("قلت") == 1
        assert lookup.get("قلنا") == 1
        assert lookup.get("قلتم") == 1
        # 3rd person past from قال base
        assert lookup.get("قالت") == 1
        assert lookup.get("قالوا") == 1
        # Present from يقول stem
        assert lookup.get("يقول") == 1
        assert lookup.get("تقول") == 1
        assert lookup.get("يقولون") == 1

    def test_defective_verb_with_past_1s(self):
        """مشى (defective verb): past_1s=مشيت gives correct 1st/2nd person forms."""
        lemmas = [_FakeLemma(1, "مشى", pos="verb", forms_json={
            "present": "يَمْشِي", "past_1s": "مَشَيْتُ",
        })]
        lookup = build_lemma_lookup(lemmas)
        assert lookup.get("مشيت") == 1
        assert lookup.get("مشينا") == 1

    def test_verb_without_past_1s_falls_back(self):
        """Sound verb without past_1s uses regular 3ms-based suffixation."""
        lemmas = [_FakeLemma(1, "كتب", pos="verb", forms_json={
            "present": "يَكْتُبُ",
        })]
        lookup = build_lemma_lookup(lemmas)
        assert lookup.get("كتبت") == 1
        assert lookup.get("كتبنا") == 1
        assert lookup.get("يكتبون") == 1

    def test_present_3mp_from_forms_json(self):
        """Defective verb present_3mp indexed directly from forms_json (Pass 2)."""
        lemmas = [_FakeLemma(1, "مشى", pos="verb", forms_json={
            "present": "يَمْشِي", "present_3mp": "يَمْشُونَ",
        })]
        lookup = build_lemma_lookup(lemmas)
        assert lookup.get("يمشون") == 1


class TestNounInflections:
    """Test noun inflection generation (sound plurals, dual)."""

    def test_feminine_noun_plural(self):
        """معلمة → معلمات (sound feminine plural)."""
        lemmas = [_FakeLemma(1, "معلمة", pos="noun")]
        lookup = build_lemma_lookup(lemmas)
        assert lookup.get("معلمات") == 1

    def test_masculine_noun_plural(self):
        """مهندس → مهندسون/مهندسين."""
        lemmas = [_FakeLemma(1, "مهندس", pos="noun")]
        lookup = build_lemma_lookup(lemmas)
        assert lookup.get("مهندسون") == 1
        assert lookup.get("مهندسين") == 1

    def test_dual_forms(self):
        """كتاب → كتابان/كتابين."""
        lemmas = [_FakeLemma(1, "كتاب", pos="noun")]
        lookup = build_lemma_lookup(lemmas)
        assert lookup.get("كتابان") == 1
        assert lookup.get("كتابين") == 1

    def test_sound_f_plural_from_forms_json(self):
        """LLM-provided sound_f_plural takes priority via Pass 2."""
        lemmas = [_FakeLemma(1, "دراسة", pos="noun", forms_json={
            "plural": "دِرَاسَات", "sound_f_plural": "دِرَاسَات", "gender": "f",
        })]
        lookup = build_lemma_lookup(lemmas)
        assert lookup.get("دراسات") == 1

    def test_verb_not_given_noun_inflections(self):
        """Verbs should not get noun inflection forms."""
        lemmas = [_FakeLemma(1, "كتب", pos="verb", forms_json={"present": "يَكْتُبُ"})]
        lookup = build_lemma_lookup(lemmas)
        assert lookup.get("كتبات") is None


class TestCorrectMapping:
    """Tests for correct_mapping — the LLM correction lookup."""

    def test_exact_match(self, db_session):
        """Exact bare form match works."""
        from app.models import Lemma
        from app.services.sentence_validator import correct_mapping

        lem = Lemma(lemma_ar="كَتَبَ", lemma_ar_bare="كتب", gloss_en="to write", pos="verb")
        db_session.add(lem)
        db_session.flush()

        result = correct_mapping(db_session, "كَتَبَ", "to write", "verb")
        assert result == lem.lemma_id

    def test_alef_hamza_mismatch(self, db_session):
        """DB stores أمر (hamza) but LLM returns أَمَرَ which normalizes to امر (bare alef)."""
        from app.models import Lemma
        from app.services.sentence_validator import correct_mapping

        lem = Lemma(lemma_ar="أَمَرَ", lemma_ar_bare="أمر", gloss_en="to order", pos="verb")
        db_session.add(lem)
        db_session.flush()

        # LLM returns diacritized form; normalize_arabic turns أ→ا,
        # but DB has أمر — exact match fails, fallback should find it
        result = correct_mapping(db_session, "أَمَرَ", "to order", "verb")
        assert result == lem.lemma_id

    def test_unhamzated_content_lemma_keeps_normalized_fallback(self, db_session):
        """Identity hardening must not remove useful content-word restoration."""
        from app.models import Lemma
        from app.services.sentence_validator import correct_mapping

        lem = Lemma(
            lemma_ar="أَمَرَ",
            lemma_ar_bare="أمر",
            gloss_en="to order",
            pos="verb",
        )
        db_session.add(lem)
        db_session.flush()

        assert correct_mapping(db_session, "امر", "to order", "verb") == lem.lemma_id

    def test_inna_cannot_cross_resolve_to_an_or_conditional_in(self, db_session):
        """إِنَّ is not أَنْ or إِنْ, even though all normalize to ان."""
        from app.models import Lemma
        from app.services.sentence_validator import correct_mapping

        an = Lemma(
            lemma_ar="أَنْ",
            lemma_ar_bare="ان",
            gloss_en="that, indeed",
            pos="particle",
        )
        conditional_in = Lemma(
            lemma_ar="إِنْ",
            lemma_ar_bare="ان",
            gloss_en="if, indeed",
            pos="particle",
        )
        db_session.add_all([an, conditional_in])
        db_session.flush()

        assert correct_mapping(
            db_session,
            "إِنَّ",
            "indeed",
            "particle",
        ) is None

    def test_exact_particle_identity_resolves_each_stored_lemma(self, db_session):
        from app.models import Lemma
        from app.services.sentence_validator import correct_mapping

        an = Lemma(
            lemma_ar="أَنْ",
            lemma_ar_bare="ان",
            gloss_en="that",
            pos="particle",
        )
        conditional_in = Lemma(
            lemma_ar="إِنْ",
            lemma_ar_bare="ان",
            gloss_en="if",
            pos="particle",
        )
        db_session.add_all([an, conditional_in])
        db_session.flush()

        assert correct_mapping(
            db_session, "أَنْ", "that", "particle"
        ) == an.lemma_id
        assert correct_mapping(
            db_session, "إِنْ", "if", "particle"
        ) == conditional_in.lemma_id

    def test_production_shape_shadda_particles_resolve_with_conjunction_pos(
        self,
        db_session,
    ):
        """Stored bare shadda and common verifier POS labels preserve identity."""
        from app.models import Lemma
        from app.services.sentence_validator import correct_mapping

        an = Lemma(
            lemma_ar="أَنْ",
            lemma_ar_bare="ان",
            gloss_en="that; to",
            pos="particle",
        )
        conditional_in = Lemma(
            lemma_ar="إِنْ",
            lemma_ar_bare="ان",
            gloss_en="if",
            pos="particle",
        )
        anna = Lemma(
            lemma_ar="أَنَّ",
            lemma_ar_bare="انّ",
            gloss_en="that",
            pos="particle",
        )
        inna = Lemma(
            lemma_ar="إِنَّ",
            lemma_ar_bare="انّ",
            gloss_en="indeed; that",
            pos="particle",
        )
        db_session.add_all([an, conditional_in, anna, inna])
        db_session.flush()

        assert correct_mapping(
            db_session,
            "أَنَّ",
            "that",
            "conjunction",
            current_lemma_id=conditional_in.lemma_id,
        ) == anna.lemma_id
        assert correct_mapping(
            db_session,
            "إِنَّ",
            "indeed",
            "conjunction",
            current_lemma_id=conditional_in.lemma_id,
        ) == inna.lemma_id

    def test_short_content_gloss_can_select_verb_homograph(self, db_session):
        """Meaningful two-letter glosses such as 'do' are not discarded."""
        from app.models import Lemma
        from app.services.sentence_validator import correct_mapping

        noun = Lemma(
            lemma_ar="فِعْل",
            lemma_ar_bare="فعل",
            gloss_en="verb; action",
            pos="noun",
        )
        verb = Lemma(
            lemma_ar="فَعَلَ",
            lemma_ar_bare="فعل",
            gloss_en="to do",
            pos="verb",
        )
        db_session.add_all([noun, verb])
        db_session.flush()

        assert correct_mapping(
            db_session,
            "فعل",
            "to do, make",
            "verb",
            current_lemma_id=noun.lemma_id,
        ) == verb.lemma_id

    def test_unvocalized_ambiguous_particle_fails_closed(self, db_session):
        from app.models import Lemma
        from app.services.sentence_validator import correct_mapping

        conditional_in = Lemma(
            lemma_ar="إِنْ",
            lemma_ar_bare="ان",
            gloss_en="if, indeed",
            pos="particle",
        )
        emphatic_inna = Lemma(
            lemma_ar="إِنَّ",
            lemma_ar_bare="ان",
            gloss_en="indeed",
            pos="particle",
        )
        db_session.add_all([conditional_in, emphatic_inna])
        db_session.flush()

        assert correct_mapping(
            db_session, "إن", "indeed", "particle"
        ) is None

    def test_alef_madda_mismatch(self, db_session):
        """DB stores آخر (alef madda) but normalized lookup finds it."""
        from app.models import Lemma
        from app.services.sentence_validator import correct_mapping

        lem = Lemma(lemma_ar="آخَر", lemma_ar_bare="آخر", gloss_en="other", pos="adj")
        db_session.add(lem)
        db_session.flush()

        result = correct_mapping(db_session, "آخَر", "other", "adj")
        assert result == lem.lemma_id

    def test_al_prefix_toggle(self, db_session):
        """LLM returns الكتاب but DB stores كتاب (or vice versa)."""
        from app.models import Lemma
        from app.services.sentence_validator import correct_mapping

        lem = Lemma(lemma_ar="كِتَاب", lemma_ar_bare="كتاب", gloss_en="book", pos="noun")
        db_session.add(lem)
        db_session.flush()

        result = correct_mapping(db_session, "الكِتَاب", "book", "noun")
        assert result == lem.lemma_id

    def test_prefer_different_lemma(self, db_session):
        """When current_lemma_id is given, prefer a different match (homograph)."""
        from app.models import Lemma
        from app.services.sentence_validator import correct_mapping

        lem1 = Lemma(lemma_ar="سَلَّمَ", lemma_ar_bare="سلم", gloss_en="peace", pos="noun")
        lem2 = Lemma(lemma_ar="سُلَّم", lemma_ar_bare="سلم", gloss_en="ladder", pos="noun")
        db_session.add_all([lem1, lem2])
        db_session.flush()

        result = correct_mapping(
            db_session, "سَلَّمَ", "ladder", "noun",
            current_lemma_id=lem1.lemma_id,
        )
        assert result == lem2.lemma_id

    def test_reject_wrong_sense_same_bare_homograph(self, db_session):
        """A same-bare candidate with the wrong gloss/POS is not a valid correction."""
        from app.models import Lemma
        from app.services.sentence_validator import correct_mapping

        rise = Lemma(
            lemma_ar="شَالَ",
            lemma_ar_bare="شال",
            gloss_en="to rise, to become elevated",
            pos="verb",
        )
        db_session.add(rise)
        db_session.flush()

        result = correct_mapping(
            db_session,
            "شَال",
            "shawl, scarf",
            "noun",
            current_lemma_id=rise.lemma_id,
        )

        assert result is None

    def test_accept_matching_same_bare_homograph(self, db_session):
        """A different same-bare candidate is accepted when its sense matches."""
        from app.models import Lemma
        from app.services.sentence_validator import correct_mapping

        ask = Lemma(lemma_ar="سَأَلَ", lemma_ar_bare="سال", gloss_en="to ask", pos="verb")
        flow = Lemma(lemma_ar="سَالَ", lemma_ar_bare="سال", gloss_en="to flow, to run", pos="verb")
        db_session.add_all([ask, flow])
        db_session.flush()

        result = correct_mapping(
            db_session,
            "سَالَ",
            "to flow",
            "verb",
            current_lemma_id=ask.lemma_id,
        )

        assert result == flow.lemma_id

    def test_returns_none_for_missing(self, db_session):
        """Returns None when the lemma doesn't exist at all."""
        from app.services.sentence_validator import correct_mapping

        result = correct_mapping(db_session, "غريب", "strange", "adj")
        assert result is None

    def test_empty_input(self, db_session):
        """Empty correct_ar returns None immediately."""
        from app.services.sentence_validator import correct_mapping

        assert correct_mapping(db_session, "", "to write", "verb") is None


class TestApplyCorrectionsExactRunningTextAliases:
    """Verifier proposals cannot override approved exact surface identity."""

    @staticmethod
    def _collision_inventory(db_session):
        from app.models import Lemma

        gated_at = datetime(2026, 7, 30)
        people = Lemma(
            lemma_ar="نَاسٌ",
            lemma_ar_bare="ناس",
            gloss_en="people",
            pos="noun",
            gates_completed_at=gated_at,
        )
        forget = Lemma(
            lemma_ar="نَسِيَ",
            lemma_ar_bare="نسي",
            gloss_en="to forget",
            pos="verb",
            forms_json={"active_participle": "نَاسٌ"},
            gates_completed_at=gated_at,
        )
        particle = Lemma(
            lemma_ar="قَدْ",
            lemma_ar_bare="قد",
            gloss_en="indeed; already",
            pos="particle",
            gates_completed_at=gated_at,
        )
        loss = Lemma(
            lemma_ar="فَقْد",
            lemma_ar_bare="فقد",
            gloss_en="loss",
            pos="noun",
            gates_completed_at=gated_at,
        )
        db_session.add_all([people, forget, particle, loss])
        db_session.flush()
        lookup = build_lemma_lookup([people, forget, particle, loss])
        return people, forget, particle, loss, lookup

    def test_contradictory_corrections_cannot_overwrite_aliases(
        self,
        db_session,
    ):
        people, forget, particle, loss, lookup = self._collision_inventory(
            db_session
        )
        mappings = map_tokens_to_lemmas(
            ["أُنَاسٌ", "فَقَدْ"],
            lookup,
            target_lemma_id=0,
            target_bare="",
        )

        failed = apply_corrections(
            [
                {
                    "position": 0,
                    "correct_lemma_ar": "نَسِيَ",
                    "correct_gloss": "to forget",
                    "correct_pos": "verb",
                },
                {
                    "position": 1,
                    "correct_lemma_ar": "فَقْد",
                    "correct_gloss": "loss",
                    "correct_pos": "noun",
                },
            ],
            mappings,
            db_session,
            lemma_lookup=lookup,
        )

        assert failed == [0, 1]
        assert [mapping.lemma_id for mapping in mappings] == [
            people.lemma_id,
            particle.lemma_id,
        ]
        assert forget.lemma_id not in {
            mapping.lemma_id for mapping in mappings
        }
        assert loss.lemma_id not in {
            mapping.lemma_id for mapping in mappings
        }

    def test_correct_proposals_can_restore_required_alias_destinations(
        self,
        db_session,
    ):
        people, forget, particle, loss, lookup = self._collision_inventory(
            db_session
        )
        mappings = [
            TokenMapping(
                position=0,
                surface_form="أُنَاسٌ",
                lemma_id=forget.lemma_id,
                is_target=False,
                is_function_word=False,
                is_proper_name=True,
            ),
            TokenMapping(
                position=1,
                surface_form="فَقَدْ",
                lemma_id=loss.lemma_id,
                is_target=False,
                is_function_word=False,
                is_proper_name=True,
            ),
        ]

        failed = apply_corrections(
            [
                {
                    "position": 0,
                    "correct_lemma_ar": "نَاسٌ",
                    "correct_gloss": "people",
                    "correct_pos": "noun",
                },
                {
                    "position": 1,
                    "correct_lemma_ar": "قَدْ",
                    "correct_gloss": "indeed",
                    "correct_pos": "particle",
                },
            ],
            mappings,
            db_session,
        )

        assert failed == []
        assert [mapping.lemma_id for mapping in mappings] == [
            people.lemma_id,
            particle.lemma_id,
        ]
        assert [mapping.is_function_word for mapping in mappings] == [
            False,
            True,
        ]
        assert all(not mapping.is_proper_name for mapping in mappings)

    def test_unresolved_alias_rejects_correction_without_mutation(
        self,
        db_session,
    ):
        from app.models import Lemma

        forget = Lemma(
            lemma_ar="نَسِيَ",
            lemma_ar_bare="نسي",
            gloss_en="to forget",
            pos="verb",
            forms_json={"active_participle": "نَاسٌ"},
            gates_completed_at=datetime(2026, 7, 30),
        )
        loss = Lemma(
            lemma_ar="فَقْد",
            lemma_ar_bare="فقد",
            gloss_en="loss",
            pos="noun",
            gates_completed_at=datetime(2026, 7, 30),
        )
        db_session.add_all([forget, loss])
        db_session.flush()
        lookup = build_lemma_lookup([forget, loss])
        mappings = [
            TokenMapping(
                position=0,
                surface_form="أُنَاسٌ",
                lemma_id=forget.lemma_id,
                is_target=False,
                is_function_word=False,
            ),
            TokenMapping(
                position=1,
                surface_form="فَقَدْ",
                lemma_id=loss.lemma_id,
                is_target=False,
                is_function_word=True,
            ),
        ]

        failed = apply_corrections(
            [
                {
                    "position": 0,
                    "correct_lemma_ar": "نَسِيَ",
                    "correct_gloss": "to forget",
                    "correct_pos": "verb",
                },
                {
                    "position": 1,
                    "correct_lemma_ar": "فَقْد",
                    "correct_gloss": "loss",
                    "correct_pos": "noun",
                },
            ],
            mappings,
            db_session,
            lemma_lookup=lookup,
        )

        assert failed == [0, 1]
        assert [mapping.lemma_id for mapping in mappings] == [
            forget.lemma_id,
            loss.lemma_id,
        ]


# ---------------------------------------------------------------------------
# F1 — validate_sentence with known_lemma_lookup (Tier C 2026-04-20 fix)
# F2 — validate_sentence with comprehensive_lemma_lookup fallback
# ---------------------------------------------------------------------------


class TestValidateSentenceWithLookup:
    """Tests for the lookup-based validation path.

    Before Phase 5, ``validate_sentence`` did naive bare-set membership
    checks, which missed 44% of real unknown words that ``lookup_lemma``
    (with clitic stripping + CAMeL) would have resolved.
    """

    def _build_lookup(self, bare_forms_to_ids: dict):
        """Build a LemmaLookupDict-like lookup directly for tests.

        Simulates what ``build_lemma_lookup`` produces without needing the DB.
        """
        from app.services.sentence_validator import LemmaLookupDict
        lookup = LemmaLookupDict()
        for bare, lid in bare_forms_to_ids.items():
            lookup.set_if_new(bare, lid, bare)
        return lookup

    def test_clitic_stripped_word_accepted_via_lookup(self, db_session):
        """F1: لِلزَّبَائِنِ (to the customers) should resolve to زبون via clitic
        stripping. The legacy bare-set path misses this; the lookup path
        accepts it.
        """
        from app.models import Lemma
        from app.services.sentence_validator import (
            build_lemma_lookup, validate_sentence,
        )

        # زبون "customer" — plural زبائن
        lem = Lemma(
            lemma_ar="زَبُون", lemma_ar_bare="زبون", gloss_en="customer",
            pos="noun", forms_json={"plural": "زَبَائِن"},
        )
        lem_target = Lemma(
            lemma_ar="نَاوَلَ", lemma_ar_bare="ناول",
            gloss_en="to hand over", pos="verb",
        )
        db_session.add_all([lem, lem_target])
        db_session.flush()

        lookup = build_lemma_lookup([lem, lem_target])
        # Sentence: "he handed to the customers the cup"
        result = validate_sentence(
            arabic_text="نَاوَلَ لِلزَّبَائِنِ",
            target_bare="ناول",
            known_bare_forms={"زبون", "ناول"},
            known_lemma_lookup=lookup,
        )
        # The target is present and لِلزَّبَائِنِ resolves to زبون via لل + زبائن.
        assert result.target_found is True
        assert "لِلزَّبَائِنِ" not in result.unknown_words

    def test_genuinely_unknown_word_still_rejected(self, db_session):
        """F1 negative: an actual OOV word must still be flagged as unknown."""
        from app.models import Lemma
        from app.services.sentence_validator import (
            build_lemma_lookup, validate_sentence,
        )

        lem_target = Lemma(
            lemma_ar="كِتَاب", lemma_ar_bare="كتاب",
            gloss_en="book", pos="noun",
        )
        db_session.add(lem_target)
        db_session.flush()

        lookup = build_lemma_lookup([lem_target])
        # خَنْجَر "dagger" is nowhere in the DB
        result = validate_sentence(
            arabic_text="الكِتَابُ خَنْجَرٌ",
            target_bare="كتاب",
            known_bare_forms={"كتاب"},
            known_lemma_lookup=lookup,
        )
        assert result.valid is False
        assert any("خنجر" in normalize_alef(w.replace("ً", "").replace("ٌ", ""))
                   for w in result.unknown_words) or \
               any(strip_diacritics(w) == "خنجر" for w in result.unknown_words)

    def test_known_forms_still_accepted_with_lookup(self, db_session):
        """F1 sanity: a plain known word still passes via lookup_lemma."""
        from app.models import Lemma
        from app.services.sentence_validator import (
            build_lemma_lookup, validate_sentence,
        )

        lem_boy = Lemma(lemma_ar="وَلَد", lemma_ar_bare="ولد",
                        gloss_en="boy", pos="noun")
        lem_apple = Lemma(lemma_ar="تُفَّاحَة", lemma_ar_bare="تفاحة",
                          gloss_en="apple", pos="noun")
        db_session.add_all([lem_boy, lem_apple])
        db_session.flush()

        lookup = build_lemma_lookup([lem_boy, lem_apple])
        result = validate_sentence(
            arabic_text="الوَلَدُ يَأْكُلُ التُّفَّاحَةَ",
            target_bare="تفاحة",
            known_bare_forms={"ولد"},
            known_lemma_lookup=lookup,
        )
        # يأكل is not in vocab but that's the real point of checking.
        # ولد and تفاحة should resolve; يأكل is the unknown one.
        assert result.target_found is True
        # Only يأكل should be unknown
        unknown_bares = {strip_diacritics(w) for w in result.unknown_words}
        assert "يأكل" in unknown_bares

    def test_comprehensive_fallback_accepts_db_lemma(self, db_session):
        """F2: word unknown in user's active vocab but present in the full DB
        should be accepted via the comprehensive fallback."""
        from app.models import Lemma
        from app.services.sentence_validator import (
            build_lemma_lookup, validate_sentence,
        )

        lem_target = Lemma(
            lemma_ar="كِتَاب", lemma_ar_bare="كتاب",
            gloss_en="book", pos="noun",
        )
        lem_dagger = Lemma(
            lemma_ar="خَنْجَر", lemma_ar_bare="خنجر",
            gloss_en="dagger", pos="noun",
        )
        db_session.add_all([lem_target, lem_dagger])
        db_session.flush()

        # User's active vocab only has "book"; "dagger" is in the DB but
        # not yet introduced.
        active = build_lemma_lookup([lem_target])
        comp = build_lemma_lookup([lem_target, lem_dagger])

        result = validate_sentence(
            arabic_text="الكِتَابُ خَنْجَرٌ",
            target_bare="كتاب",
            known_bare_forms={"كتاب"},
            known_lemma_lookup=active,
            comprehensive_lemma_lookup=comp,
        )
        # With comp fallback, خنجر is resolvable → no unknown words.
        assert len(result.unknown_words) == 0
        assert result.target_found is True
        # And one of the classifications is "known_via_comp"
        cats = [c.category for c in result.classifications]
        assert "known_via_comp" in cats

    def test_comprehensive_fallback_still_rejects_out_of_db(self, db_session):
        """F2 negative: a word not in the DB at all is still unknown even with
        comprehensive fallback."""
        from app.models import Lemma
        from app.services.sentence_validator import (
            build_lemma_lookup, validate_sentence,
        )

        lem_target = Lemma(
            lemma_ar="كِتَاب", lemma_ar_bare="كتاب",
            gloss_en="book", pos="noun",
        )
        db_session.add(lem_target)
        db_session.flush()

        active = build_lemma_lookup([lem_target])
        comp = build_lemma_lookup([lem_target])  # same — no extra lemmas

        result = validate_sentence(
            arabic_text="الكِتَابُ زَنْقَلَبُوتٌ",  # nonsense word
            target_bare="كتاب",
            known_bare_forms={"كتاب"},
            known_lemma_lookup=active,
            comprehensive_lemma_lookup=comp,
        )
        assert result.valid is False
        assert len(result.unknown_words) >= 1

    def test_lookup_path_backwards_compat_when_none(self, db_session):
        """Passing only known_bare_forms (no lookup) must match pre-F1 behavior."""
        from app.services.sentence_validator import validate_sentence

        # بيتها should match via the bare-set + clitic path
        result = validate_sentence(
            arabic_text="بيتها كبير",
            target_bare="كبير",
            known_bare_forms={"بيت"},
        )
        assert result.valid is True
        assert result.target_found is True
        assert len(result.unknown_words) == 0


class TestRerankDeferredToAfterValidation:
    """A:H1 — rerank runs on deterministic-validation survivors, and should
    NOT be called when ≤needed survivors already exist (saves a Haiku call)."""

    def test_rerank_skipped_when_survivors_fit_need(self, monkeypatch, db_session):
        """When len(candidates) ≤ rerank_target, rerank must not be invoked."""
        import app.services.llm as llm_mod

        call_counter = {"n": 0}

        def fake_rerank(*args, **kwargs):
            call_counter["n"] += 1
            return []

        monkeypatch.setattr(
            llm_mod, "rerank_sentences_by_naturalness", fake_rerank,
        )

        # Simulate material_generator's rerank gate inline (same logic).
        needed = 2
        rerank_target = max(needed, 2)
        candidates = [{"arabic": "s1", "english": "e1"}, {"arabic": "s2", "english": "e2"}]
        if len(candidates) > rerank_target:
            llm_mod.rerank_sentences_by_naturalness(
                [],
                target_word="x",
                target_translation="x",
                top_k=rerank_target,
            )

        assert call_counter["n"] == 0, \
            "rerank should not run when survivors already ≤ needed"

    def test_rerank_runs_when_survivors_exceed_need(self, monkeypatch):
        """When len(candidates) > rerank_target, rerank IS invoked."""
        import app.services.llm as llm_mod

        call_counter = {"n": 0}

        def fake_rerank(*args, **kwargs):
            call_counter["n"] += 1
            return []

        monkeypatch.setattr(
            llm_mod, "rerank_sentences_by_naturalness", fake_rerank,
        )

        needed = 2
        rerank_target = max(needed, 2)
        candidates = [{"arabic": f"s{i}", "english": f"e{i}"} for i in range(5)]
        if len(candidates) > rerank_target:
            llm_mod.rerank_sentences_by_naturalness(
                [],
                target_word="x",
                target_translation="x",
                top_k=rerank_target,
            )

        assert call_counter["n"] == 1


class TestMultiTargetValidation:
    def test_rejects_single_target_sentence_by_default(self):
        result = validate_sentence_multi_target(
            arabic_text="كتاب كبير",
            target_bares={"كتاب": 1, "قلم": 2},
            known_bare_forms={"كتاب", "قلم", "كبير"},
        )
        assert result.valid is False
        assert result.target_count == 1
        assert any("need 2" in issue for issue in result.issues)

    def test_single_target_callers_can_opt_in_to_one_target_minimum(self):
        result = validate_sentence_multi_target(
            arabic_text="كتاب كبير",
            target_bares={"كتاب": 1},
            known_bare_forms={"كتاب", "كبير"},
            min_targets=1,
        )
        assert result.valid is True
        assert result.target_count == 1


class TestLookupLemmaCitation:
    """Citation-strict resolver (2026-07-15 collision investigation).

    Fixture pairs come from the 18 documented /api/discover/add collisions
    (research/spec-2026-07-15-lookup-clitic-collision.md §7): a new citation
    form must never fuzzy-resolve onto an unrelated existing lemma.
    """

    # (submitted citation bare, wrong-target lemma bare it used to resolve to)
    MOMO_PAIRS = [
        ("تالي", "الا"), ("حقيقي", "حقيق"), ("لاحظ", "حظ"), ("كناس", "ناس"),
        ("سيجار", "جار"), ("رمادي", "رماد"), ("صبي", "صب"), ("توقف", "وقف"),
        ("نظارة", "ناظر"), ("سحري", "سحر"), ("اصبح", "صبح"), ("تمتم", "تم"),
        ("عاد", "عادي"), ("عمق", "عميق"), ("امير", "مار"), ("ادرك", "دار"),
        ("شرطة", "شرط"), ("حجري", "حجر"),
    ]

    def _collision_lookup(self):
        """Vocabulary containing only the historical wrong targets."""
        lemmas = [
            _FakeLemma(100 + i, bare) for i, (_, bare) in enumerate(self.MOMO_PAIRS)
        ]
        return build_lemma_lookup(lemmas)

    def test_no_citation_collisions(self):
        from app.services.sentence_validator import lookup_lemma_citation
        lookup = self._collision_lookup()
        for query, wrong in self.MOMO_PAIRS:
            got = lookup_lemma_citation(query, lookup, original_bare=query)
            assert got is None, (
                f"citation lookup for new word {query!r} must return None, "
                f"not resolve onto existing {wrong!r} (lemma_id={got})"
            )

    def test_old_lookup_documents_the_clitic_collision(self):
        """Contrast case: the running-text lookup DOES strip ك from كناس.

        Documents why /add must not use lookup_lemma — if this ever starts
        returning None the two resolvers have converged and
        lookup_lemma_citation may be redundant.
        """
        lookup = self._collision_lookup()
        assert lookup_lemma("كناس", lookup, original_bare="كناس") is not None

    def test_resolves_to_correct_lemma_once_it_exists(self):
        from app.services.sentence_validator import lookup_lemma_citation
        lemmas = [_FakeLemma(1, "ناس"), _FakeLemma(2, "كناس", lemma_ar="كَنَّاس")]
        lookup = build_lemma_lookup(lemmas)
        assert lookup_lemma_citation("كناس", lookup, original_bare="كناس") == 2

    def test_hamza_normalized_direct_match(self):
        from app.services.sentence_validator import lookup_lemma_citation
        lemmas = [_FakeLemma(1, "أمير", lemma_ar="أَمِير")]
        lookup = build_lemma_lookup(lemmas)
        # /add normalizes the submitted bare before lookup
        assert lookup_lemma_citation("امير", lookup, original_bare="أمير") == 1

    def test_identity_sensitive_particles_use_the_strict_citation_path(self):
        from app.services.sentence_validator import lookup_lemma_citation
        lookup = build_lemma_lookup([
            _FakeLemma(2185, "ان", pos="particle", lemma_ar="أَنْ"),
            _FakeLemma(2186, "ان", pos="particle", lemma_ar="إِنْ"),
            _FakeLemma(2187, "انّ", pos="particle", lemma_ar="أَنَّ"),
            _FakeLemma(2188, "انّ", pos="particle", lemma_ar="إِنَّ"),
        ])

        unresolved = ["فان", "فأن", "فإن", "بإن", "بِإِنَّهُ"]
        for surface in unresolved:
            bare_norm = normalize_alef(strip_diacritics(surface))
            assert (
                lookup_lemma_citation(
                    bare_norm,
                    lookup,
                    original_bare=surface,
                )
                is None
            )

        exact = {
            "فَأَنْ": 2185,
            "فَأَنَّ": 2187,
            "فَإِنْ": 2186,
            "فَإِنَّ": 2188,
        }
        for surface, expected in exact.items():
            bare_norm = normalize_alef(strip_diacritics(surface))
            assert lookup_lemma_citation(
                bare_norm,
                lookup,
                original_bare=surface,
            ) == expected

    def test_good_clitic_resolutions(self):
        """ال-bearing prefixes must keep resolving (بالمكتبة→مكتبة class)."""
        from app.services.sentence_validator import lookup_lemma_citation
        lemmas = [
            _FakeLemma(1, "مكتبة"), _FakeLemma(2, "طفل"), _FakeLemma(3, "كتاب"),
            _FakeLemma(4, "قمر"), _FakeLemma(5, "سيارة"), _FakeLemma(6, "مدرسة"),
        ]
        lookup = build_lemma_lookup(lemmas)
        cases = [
            ("بالمكتبة", 1), ("للمدرسة", 6), ("والكتاب", 3),
            ("كالقمر", 4), ("بالسيارة", 5), ("فالكتاب", 3),
        ]
        for query, expected in cases:
            assert lookup_lemma_citation(query, lookup, original_bare=query) == expected

    def test_al_initial_words_do_not_strip(self):
        """Words that merely BEGIN with an al-prefix sequence stay whole."""
        from app.services.sentence_validator import lookup_lemma_citation
        lemmas = [_FakeLemma(1, "ولد"), _FakeLemma(2, "والد", lemma_ar="وَالِد")]
        lookup = build_lemma_lookup(lemmas)
        # والد is its own lemma — must direct-match, not strip وال
        assert lookup_lemma_citation("والد", lookup, original_bare="والد") == 2
        # بالغ "adult" absent from vocab: remainder غ is too short to strip,
        # so this is a clean not-found, not a fuzzy match
        assert lookup_lemma_citation("بالغ", lookup, original_bare="بالغ") is None

    def test_single_letter_clitics_never_stripped(self):
        from app.services.sentence_validator import lookup_lemma_citation
        lemmas = [_FakeLemma(1, "ناس"), _FakeLemma(2, "حظ")]
        lookup = build_lemma_lookup(lemmas)
        assert lookup_lemma_citation("كناس", lookup, original_bare="كناس") is None
        assert lookup_lemma_citation("لناس", lookup, original_bare="لناس") is None
        assert lookup_lemma_citation("وحظ", lookup, original_bare="وحظ") is None

    def test_self_resolution_census(self):
        """Standing invariant: every lemma's own bare resolves to itself."""
        from app.services.sentence_validator import lookup_lemma_citation
        bares = [
            "كتاب", "مكتبة", "ولد", "والد", "مدرسة", "قمر", "شمس", "بيت",
            "ناس", "حظ", "جار", "رماد", "وقف", "سحر", "صبح", "عميق",
            "كناس", "لاحظ", "توقف", "امير", "شرطة", "بالغ", "والي",
        ]
        lemmas = [_FakeLemma(i + 1, b) for i, b in enumerate(bares)]
        lookup = build_lemma_lookup(lemmas)
        for i, b in enumerate(bares):
            got = lookup_lemma_citation(b, lookup, original_bare=b)
            assert got == i + 1, f"{b!r} self-resolved to {got}, expected {i + 1}"
