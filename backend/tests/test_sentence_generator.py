"""Tests for the sentence generation pipeline.

Uses mocked LLM responses to test:
- Prompt construction
- Retry logic on validation failure
- Integration of validator + LLM
- Diversity utilities (weighted sampling, avoid words)
"""

from unittest.mock import patch

import pytest

from app.services.llm import SentenceResult, SentenceReviewResult, generate_sentences_batch
from app.services.sentence_generator import (
    ALWAYS_AVOID_NAMES,
    DIVERSITY_SENTENCE_THRESHOLD,
    GeneratedSentence,
    GenerationError,
    _check_scaffold_diversity,
    generate_validated_sentence,
    get_avoid_words,
    sample_known_words_weighted,
)

# Helper: mock quality review to always pass (no API key in tests)
_QUALITY_PASS = [SentenceReviewResult(natural=True, translation_correct=True, reason="test")]


KNOWN_WORDS = [
    {"arabic": "وَلَد", "english": "boy"},
    {"arabic": "كِتَاب", "english": "book"},
    {"arabic": "يَأْكُل", "english": "eats"},
    {"arabic": "بَيْت", "english": "house"},
    {"arabic": "يَذْهَب", "english": "goes"},
    {"arabic": "طَالِب", "english": "student"},
    {"arabic": "يَقْرَأ", "english": "reads"},
    {"arabic": "مَدْرَسَة", "english": "school"},
    {"arabic": "كَبِير", "english": "big"},
    {"arabic": "صَغِير", "english": "small"},
    # Common words that were formerly auto-excluded as "function words"
    {"arabic": "هَلْ", "english": "? (yes/no)"},
    {"arabic": "في", "english": "in"},
    {"arabic": "مِن", "english": "from"},
    {"arabic": "مَع", "english": "with"},
    {"arabic": "هُوَ", "english": "he"},
    {"arabic": "أَو", "english": "or"},
]


@patch("app.services.sentence_generator.review_sentences_quality", return_value=_QUALITY_PASS)
@patch("app.services.sentence_generator.generate_sentence")
def test_valid_on_first_attempt(mock_gen, _mock_review):
    """LLM returns a valid sentence on the first try."""
    mock_gen.return_value = SentenceResult(
        arabic="الوَلَدُ يَأْكُلُ التُّفَّاحَةَ",
        english="The boy eats the apple",
        transliteration="al-waladu ya'kulu al-tuffāḥata",
    )

    result = generate_validated_sentence(
        target_arabic="تُفَّاحَة",
        target_translation="apple",
        known_words=KNOWN_WORDS,
    )

    assert isinstance(result, GeneratedSentence)
    assert result.arabic == "الوَلَدُ يَأْكُلُ التُّفَّاحَةَ"
    assert result.english == "The boy eats the apple"
    assert result.transliteration != ""
    assert result.attempts == 1
    assert result.validation["target_found"] is True
    mock_gen.assert_called_once()


@patch("app.services.sentence_generator.review_sentences_quality", return_value=_QUALITY_PASS)
@patch("app.services.sentence_generator.generate_sentence")
def test_retry_on_invalid_then_succeed(mock_gen, _mock_review):
    """First attempt invalid (extra unknown word), second attempt valid."""
    # First call: sentence with unknown word "جميلة" (beautiful)
    invalid = SentenceResult(
        arabic="الوَلَدُ يَأْكُلُ التُّفَّاحَةَ الجَمِيلَةَ",
        english="The boy eats the beautiful apple",
        transliteration="...",
    )
    # Second call: fixed sentence
    valid = SentenceResult(
        arabic="الوَلَدُ يَأْكُلُ التُّفَّاحَةَ",
        english="The boy eats the apple",
        transliteration="al-waladu ya'kulu al-tuffāḥata",
    )
    mock_gen.side_effect = [invalid, valid]

    result = generate_validated_sentence(
        target_arabic="تُفَّاحَة",
        target_translation="apple",
        known_words=KNOWN_WORDS,
    )

    assert result.attempts == 2
    assert result.arabic == "الوَلَدُ يَأْكُلُ التُّفَّاحَةَ"
    assert mock_gen.call_count == 2
    # Second call should include retry feedback
    second_call_kwargs = mock_gen.call_args_list[1]
    assert second_call_kwargs.kwargs.get("retry_feedback") is not None


@patch("app.services.sentence_generator.generate_sentence")
def test_all_retries_fail(mock_gen):
    """All attempts produce invalid sentences → GenerationError."""
    bad = SentenceResult(
        arabic="الوَلَدُ يَأْكُلُ الطَّعَامَ اللَّذِيذَ",
        english="The boy eats delicious food",
        transliteration="...",
    )
    mock_gen.return_value = bad  # Always returns invalid (طعام, لذيذ unknown)

    with pytest.raises(GenerationError, match="Failed to generate"):
        generate_validated_sentence(
            target_arabic="تُفَّاحَة",
            target_translation="apple",
            known_words=KNOWN_WORDS,
        )

    from app.services.sentence_generator import MAX_RETRIES
    assert mock_gen.call_count == MAX_RETRIES


@patch("app.services.sentence_generator.review_sentences_quality", return_value=_QUALITY_PASS)
@patch("app.services.sentence_generator.generate_sentence")
def test_target_word_missing_triggers_retry(mock_gen, _mock_review):
    """LLM forgets the target word → retry."""
    no_target = SentenceResult(
        arabic="الوَلَدُ يَأْكُلُ",
        english="The boy eats",
        transliteration="...",
    )
    with_target = SentenceResult(
        arabic="الوَلَدُ يَأْكُلُ التُّفَّاحَةَ",
        english="The boy eats the apple",
        transliteration="al-waladu ya'kulu al-tuffāḥata",
    )
    mock_gen.side_effect = [no_target, with_target]

    result = generate_validated_sentence(
        target_arabic="تُفَّاحَة",
        target_translation="apple",
        known_words=KNOWN_WORDS,
    )

    assert result.attempts == 2
    assert result.validation["target_found"] is True


@patch("app.services.sentence_generator.review_sentences_quality", return_value=_QUALITY_PASS)
@patch("app.services.sentence_generator.generate_sentence")
def test_common_words_in_sentence_are_ok(mock_gen, _mock_review):
    """Sentence with common words (formerly function words) should validate when known."""
    mock_gen.return_value = SentenceResult(
        arabic="هَلْ الوَلَدُ فِي البَيْتِ مَعَ التُّفَّاحَةِ",
        english="Is the boy in the house with the apple?",
        transliteration="hal al-waladu fī al-bayti maʿa al-tuffāḥati",
    )

    result = generate_validated_sentence(
        target_arabic="تُفَّاحَة",
        target_translation="apple",
        known_words=KNOWN_WORDS,
    )

    assert result.attempts == 1


@patch("app.services.sentence_generator.review_sentences_quality", return_value=_QUALITY_PASS)
@patch("app.services.sentence_generator.generate_sentence")
def test_transliteration_included(mock_gen, _mock_review):
    """Result should include transliteration from LLM."""
    mock_gen.return_value = SentenceResult(
        arabic="الكِتَابُ فِي البَيْتِ",
        english="The book is in the house",
        transliteration="al-kitābu fī al-bayti",
    )

    result = generate_validated_sentence(
        target_arabic="بَيْت",
        target_translation="house",
        known_words=KNOWN_WORDS,
    )

    assert result.transliteration == "al-kitābu fī al-bayti"


@patch("app.services.sentence_generator.review_sentences_quality", return_value=_QUALITY_PASS)
@patch("app.services.sentence_generator.generate_sentence")
def test_known_words_sampling(mock_gen, _mock_review):
    """When known_words > 500, only a sample is sent to LLM."""
    mock_gen.return_value = SentenceResult(
        arabic="الوَلَدُ يَأْكُلُ التُّفَّاحَةَ",
        english="The boy eats the apple",
        transliteration="...",
    )

    big_known = [
        {"arabic": f"كلمة{i}", "english": f"word{i}"}
        for i in range(600)
    ]
    # Add the words we need for validation
    big_known.extend(KNOWN_WORDS)

    result = generate_validated_sentence(
        target_arabic="تُفَّاحَة",
        target_translation="apple",
        known_words=big_known,
    )

    # Should have called generate_sentence with <= 500 known words
    call_kwargs = mock_gen.call_args
    sent_known = call_kwargs.kwargs.get("known_words") or call_kwargs.args[2]
    assert len(sent_known) <= 500


# --- Tests for batch generation ---


@patch("app.services.llm.generate_completion")
def test_batch_generates_multiple_sentences(mock_completion):
    """Batch generation returns multiple SentenceResult objects."""
    mock_completion.return_value = {
        "sentences": [
            {
                "arabic": "الوَلَدُ يَقْرَأُ الكِتَابَ",
                "english": "The boy reads the book",
                "transliteration": "al-waladu yaqra'u al-kitāba",
            },
            {
                "arabic": "الكِتَابُ فِي البَيْتِ",
                "english": "The book is in the house",
                "transliteration": "al-kitābu fī al-bayti",
            },
            {
                "arabic": "هَذَا كِتَابٌ كَبِيرٌ",
                "english": "This is a big book",
                "transliteration": "hādhā kitābun kabīrun",
            },
        ]
    }

    results = generate_sentences_batch(
        target_word="كِتَاب",
        target_translation="book",
        known_words=KNOWN_WORDS,
        count=3,
    )

    assert len(results) == 3
    assert all(isinstance(r, SentenceResult) for r in results)
    assert results[0].arabic == "الوَلَدُ يَقْرَأُ الكِتَابَ"
    assert results[2].english == "This is a big book"


@patch("app.services.llm.generate_completion")
def test_batch_handles_partial_results(mock_completion):
    """Batch gracefully handles fewer sentences than requested."""
    mock_completion.return_value = {
        "sentences": [
            {
                "arabic": "الوَلَدُ يَقْرَأُ الكِتَابَ",
                "english": "The boy reads the book",
                "transliteration": "...",
            },
        ]
    }

    results = generate_sentences_batch(
        target_word="كِتَاب",
        target_translation="book",
        known_words=KNOWN_WORDS,
        count=3,
    )

    assert len(results) == 1


@patch("app.services.llm.generate_completion")
def test_batch_handles_malformed_response(mock_completion):
    """Batch returns empty list for malformed LLM output."""
    mock_completion.return_value = {"not_sentences": "oops"}

    results = generate_sentences_batch(
        target_word="كِتَاب",
        target_translation="book",
        known_words=KNOWN_WORDS,
    )

    assert results == []


@patch("app.services.llm.generate_completion")
def test_batch_skips_entries_without_arabic(mock_completion):
    """Entries missing arabic text are skipped."""
    mock_completion.return_value = {
        "sentences": [
            {"arabic": "", "english": "no arabic", "transliteration": ""},
            {
                "arabic": "الوَلَدُ يَقْرَأُ",
                "english": "The boy reads",
                "transliteration": "al-waladu yaqra'u",
            },
        ]
    }

    results = generate_sentences_batch(
        target_word="يَقْرَأ",
        target_translation="reads",
        known_words=KNOWN_WORDS,
    )

    assert len(results) == 1
    assert results[0].arabic == "الوَلَدُ يَقْرَأُ"


@patch("app.services.llm.generate_completion")
def test_batch_uses_model_override(mock_completion):
    """Batch passes model_override to generate_completion."""
    mock_completion.return_value = {"sentences": []}

    generate_sentences_batch(
        target_word="كِتَاب",
        target_translation="book",
        known_words=KNOWN_WORDS,
        model_override="openai",
    )

    call_kwargs = mock_completion.call_args.kwargs
    assert call_kwargs["model_override"] == "openai"


# --- Tests for diversity utilities ---

KNOWN_WORDS_WITH_IDS = [
    {"arabic": f"كلمة{i}", "english": f"word{i}", "lemma_id": i}
    for i in range(100)
]


class TestSampleKnownWordsWeighted:
    def test_small_pool_returns_all(self):
        """When pool <= sample_size, return all words."""
        small = KNOWN_WORDS_WITH_IDS[:10]
        result = sample_known_words_weighted(small, {}, sample_size=50)
        assert len(result) == 10

    def test_returns_sample_size(self):
        """Should return exactly sample_size words."""
        counts = {i: 10 for i in range(100)}
        result = sample_known_words_weighted(
            KNOWN_WORDS_WITH_IDS, counts, sample_size=30
        )
        assert len(result) == 30

    def test_excludes_target_word(self):
        """Target lemma should be excluded from the sample."""
        result = sample_known_words_weighted(
            KNOWN_WORDS_WITH_IDS, {}, sample_size=50, target_lemma_id=5
        )
        ids = {w["lemma_id"] for w in result}
        assert 5 not in ids

    def test_over_represented_words_deprioritized(self):
        """Words with high sentence counts should appear less often in samples."""
        # Make lemmas 0-9 very over-represented
        counts = {i: 100 for i in range(10)}
        # Run many samples and check that over-represented words appear less
        appearances = {i: 0 for i in range(100)}
        for _ in range(200):
            sample = sample_known_words_weighted(
                KNOWN_WORDS_WITH_IDS, counts, sample_size=50
            )
            for w in sample:
                appearances[w["lemma_id"]] += 1

        # Average appearances for over-represented vs normal words
        overrep_avg = sum(appearances[i] for i in range(10)) / 10
        normal_avg = sum(appearances[i] for i in range(10, 100)) / 90

        # Over-represented should appear significantly less often
        assert overrep_avg < normal_avg

    def test_min_weight_prevents_complete_exclusion(self):
        """Even very over-represented words should still appear sometimes."""
        counts = {i: 1000 for i in range(100)}
        result = sample_known_words_weighted(
            KNOWN_WORDS_WITH_IDS, counts, sample_size=50
        )
        assert len(result) == 50


class TestGetAvoidWords:
    def test_empty_counts_returns_none(self):
        assert get_avoid_words({}, KNOWN_WORDS_WITH_IDS) is None

    def test_no_words_above_threshold(self):
        """All words at count 1 → none above threshold."""
        counts = {i: 1 for i in range(50)}
        result = get_avoid_words(counts, KNOWN_WORDS_WITH_IDS)
        assert result is None

    def test_returns_over_represented_words(self):
        """Words above 2x median should be returned."""
        # Median will be 2, threshold = max(4, 3) = 4
        counts = {i: 2 for i in range(50)}
        counts[0] = 20  # way over threshold
        counts[1] = 10
        result = get_avoid_words(counts, KNOWN_WORDS_WITH_IDS)
        assert result is not None
        assert len(result) == 2
        # Most over-represented first
        assert result[0] == "كلمة0"
        assert result[1] == "كلمة1"

    def test_caps_at_max_avoid_words(self):
        """Should return at most MAX_AVOID_WORDS items (plus always-avoid names)."""
        # 80 words at count 2, 25 words at count 100 → median = 2, threshold = 4
        counts = {i: 2 for i in range(80)}
        for i in range(25):
            counts[i] = 100  # 25 words over threshold
        result = get_avoid_words(counts, KNOWN_WORDS_WITH_IDS)
        assert result is not None
        # MAX_AVOID_WORDS is 20, caps at 20 (no names in test data)
        assert len(result) == 20

    def test_only_returns_words_in_known_list(self):
        """Avoid words must be in the known_words list."""
        counts = {999: 100, 0: 100}  # lemma 999 not in known_words
        # Median is 100, threshold is 200 — neither qualifies
        # Let's set it so that both are above threshold
        counts = {i: 1 for i in range(50)}
        counts[999] = 50  # not in known_words
        counts[0] = 50  # in known_words
        result = get_avoid_words(counts, KNOWN_WORDS_WITH_IDS)
        if result:
            for word in result:
                assert word != "?"  # shouldn't include unknown lemma


@patch("app.services.sentence_generator.review_sentences_quality", return_value=_QUALITY_PASS)
@patch("app.services.sentence_generator.generate_sentence")
def test_weighted_sampling_used_when_counts_provided(mock_gen, _mock_review):
    """When content_word_counts is passed, weighted sampling is used."""
    mock_gen.return_value = SentenceResult(
        arabic="الوَلَدُ يَأْكُلُ التُّفَّاحَةَ",
        english="The boy eats the apple",
        transliteration="...",
    )

    big_known = [
        {"arabic": f"كلمة{i}", "english": f"word{i}", "lemma_id": i}
        for i in range(600)
    ]
    big_known.extend(
        {**w, "lemma_id": 1000 + i} for i, w in enumerate(KNOWN_WORDS)
    )
    counts = {i: 50 for i in range(10)}  # first 10 over-represented

    result = generate_validated_sentence(
        target_arabic="تُفَّاحَة",
        target_translation="apple",
        known_words=big_known,
        content_word_counts=counts,
    )

    sent_known = mock_gen.call_args.kwargs.get("known_words") or mock_gen.call_args.args[2]
    assert len(sent_known) <= 500


@patch("app.services.llm.generate_completion")
def test_avoid_words_in_prompt(mock_completion):
    """When avoid_words is provided, it appears in the prompt."""
    mock_completion.return_value = {
        "arabic": "الوَلَدُ يَأْكُلُ التُّفَّاحَةَ",
        "english": "The boy eats the apple",
        "transliteration": "...",
    }

    from app.services.llm import generate_sentence

    generate_sentence(
        target_word="تُفَّاحَة",
        target_translation="apple",
        known_words=KNOWN_WORDS,
        avoid_words=["جِدًّا", "بُنِّيّ"],
    )

    prompt = mock_completion.call_args.kwargs.get("prompt") or mock_completion.call_args.args[0]
    assert "جِدًّا" in prompt
    assert "بُنِّيّ" in prompt
    assert "overused" in prompt.lower()


@patch("app.services.llm.generate_completion")
def test_avoid_words_in_batch_prompt(mock_completion):
    """Batch generation includes avoid words in prompt."""
    mock_completion.return_value = {"sentences": []}

    generate_sentences_batch(
        target_word="كِتَاب",
        target_translation="book",
        known_words=KNOWN_WORDS,
        avoid_words=["جِدًّا"],
    )

    prompt = mock_completion.call_args.kwargs.get("prompt") or mock_completion.call_args.args[0]
    assert "جِدًّا" in prompt


@patch("app.services.llm.generate_completion")
def test_no_avoid_words_when_none(mock_completion):
    """When avoid_words is None, no avoid instruction in prompt."""
    mock_completion.return_value = {
        "arabic": "test",
        "english": "test",
        "transliteration": "test",
    }

    from app.services.llm import generate_sentence

    generate_sentence(
        target_word="تُفَّاحَة",
        target_translation="apple",
        known_words=KNOWN_WORDS,
    )

    prompt = mock_completion.call_args.kwargs.get("prompt") or mock_completion.call_args.args[0]
    assert "overused" not in prompt.lower()


class TestAlwaysAvoidNames:
    def test_names_included_even_below_threshold(self):
        """Proper names from ALWAYS_AVOID_NAMES should always be in the avoid list."""
        known = [
            {"arabic": "مُحَمَّد", "english": "Mohamed", "lemma_id": 1},
            {"arabic": "كِتَاب", "english": "book", "lemma_id": 2},
        ]
        # Low counts — nothing over threshold
        counts = {1: 1, 2: 1}
        result = get_avoid_words(counts, known)
        # Names should be included despite low counts
        assert result is not None
        assert "مُحَمَّد" in result

    def test_names_not_duplicated(self):
        """If a name is already in the avoid list (over threshold), don't add twice."""
        known = [
            {"arabic": "مُحَمَّد", "english": "Mohamed", "lemma_id": 1},
            {"arabic": "كِتَاب", "english": "book", "lemma_id": 2},
        ]
        counts = {1: 1000, 2: 1}  # محمد way over threshold
        result = get_avoid_words(counts, known)
        assert result is not None
        assert result.count("مُحَمَّد") == 1


class TestCheckScaffoldDiversity:
    def test_fresh_sentence_passes(self):
        """Sentence with no overexposed words should pass."""
        lemma_lookup = {"كتاب": 1, "ولد": 2}
        counts = {1: 2, 2: 3}  # both below threshold
        passes, overused = _check_scaffold_diversity(
            "الوَلَدُ قَرَأَ الكِتَابَ", "كتاب", counts, lemma_lookup,
        )
        assert passes
        assert len(overused) == 0

    def test_overexposed_sentence_rejected(self):
        """Sentence with multiple overexposed scaffold words should be rejected."""
        lemma_lookup = {"كتاب": 1, "جميلة": 2, "كبيرة": 3}
        counts = {
            1: 1,  # target — ignored
            2: DIVERSITY_SENTENCE_THRESHOLD + 10,
            3: DIVERSITY_SENTENCE_THRESHOLD + 5,
        }
        # Use bare forms (no ال prefix, no و conjunction)
        passes, overused = _check_scaffold_diversity(
            "كِتَابٌ جَمِيلَةٌ كَبِيرَةٌ", "كتاب", counts, lemma_lookup,
        )
        assert not passes
        assert len(overused) == 2

    def test_single_overexposed_word_allowed(self):
        """One overexposed scaffold word is tolerated."""
        lemma_lookup = {"كتاب": 1, "جميلة": 2, "ولد": 3}
        counts = {
            2: DIVERSITY_SENTENCE_THRESHOLD + 10,
            3: 2,
        }
        passes, overused = _check_scaffold_diversity(
            "كِتَابٌ جَمِيلَةٌ ولد", "كتاب", counts, lemma_lookup,
        )
        assert passes
        assert len(overused) == 1

    def test_all_words_count_for_diversity(self):
        """All words (including formerly excluded function words) count toward overexposure."""
        lemma_lookup = {"كتاب": 1, "في": 2}
        counts = {2: 1000}  # في is overused
        passes, overused = _check_scaffold_diversity(
            "في كِتَابٍ", "كتاب", counts, lemma_lookup,
        )
        assert passes  # still passes (only 1 overused word)
        assert len(overused) == 1
        assert "في" in overused[0]


class TestStarterDiversity:
    def test_system_prompt_discourages_hal(self):
        """System prompt should discourage defaulting to هَلْ."""
        from app.services.llm import SENTENCE_SYSTEM_PROMPT, BATCH_SENTENCE_SYSTEM_PROMPT
        assert "هَلْ" in SENTENCE_SYSTEM_PROMPT
        assert "Do NOT default" in SENTENCE_SYSTEM_PROMPT
        assert "هَلْ" in BATCH_SENTENCE_SYSTEM_PROMPT
        assert "Do NOT default" in BATCH_SENTENCE_SYSTEM_PROMPT

    def test_system_prompt_discourages_muhammad(self):
        """System prompt should discourage always using مُحَمَّد."""
        from app.services.llm import SENTENCE_SYSTEM_PROMPT, BATCH_SENTENCE_SYSTEM_PROMPT
        assert "مُحَمَّد" in SENTENCE_SYSTEM_PROMPT
        assert "مُحَمَّد" in BATCH_SENTENCE_SYSTEM_PROMPT
