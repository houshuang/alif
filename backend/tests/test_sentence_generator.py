"""Tests for the sentence generation pipeline.

Uses mocked LLM responses to test:
- Prompt construction
- Retry logic on validation failure
- Integration of validator + LLM
- Diversity utilities (weighted sampling, avoid words)
"""

from unittest.mock import patch

import pytest

from app.services.llm import SentenceResult, generate_sentences_batch
from app.services.sentence_generator import (
    GeneratedSentence,
    GenerationError,
    generate_validated_sentence,
    get_avoid_words,
    sample_known_words_weighted,
)


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
]


@patch("app.services.sentence_generator.generate_sentence")
def test_valid_on_first_attempt(mock_gen):
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


@patch("app.services.sentence_generator.generate_sentence")
def test_retry_on_invalid_then_succeed(mock_gen):
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
    """All 3 attempts produce invalid sentences → GenerationError."""
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

    assert mock_gen.call_count == 3


@patch("app.services.sentence_generator.generate_sentence")
def test_target_word_missing_triggers_retry(mock_gen):
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


@patch("app.services.sentence_generator.generate_sentence")
def test_function_words_in_sentence_are_ok(mock_gen):
    """Sentence with many function words should validate."""
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
    assert len(result.validation["function_words"]) >= 2


@patch("app.services.sentence_generator.generate_sentence")
def test_transliteration_included(mock_gen):
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


@patch("app.services.sentence_generator.generate_sentence")
def test_known_words_sampling(mock_gen):
    """When known_words > 50, only a sample is sent to LLM."""
    mock_gen.return_value = SentenceResult(
        arabic="الوَلَدُ يَأْكُلُ التُّفَّاحَةَ",
        english="The boy eats the apple",
        transliteration="...",
    )

    big_known = [
        {"arabic": f"كلمة{i}", "english": f"word{i}"}
        for i in range(200)
    ]
    # Add the words we need for validation
    big_known.extend(KNOWN_WORDS)

    result = generate_validated_sentence(
        target_arabic="تُفَّاحَة",
        target_translation="apple",
        known_words=big_known,
    )

    # Should have called generate_sentence with <= 50 known words
    call_kwargs = mock_gen.call_args
    sent_known = call_kwargs.kwargs.get("known_words") or call_kwargs.args[2]
    assert len(sent_known) <= 50


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
        """Should return at most MAX_AVOID_WORDS items."""
        counts = {i: 2 for i in range(50)}
        for i in range(20):
            counts[i] = 100  # 20 words over threshold
        result = get_avoid_words(counts, KNOWN_WORDS_WITH_IDS)
        assert result is not None
        assert len(result) <= 10

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


@patch("app.services.sentence_generator.generate_sentence")
def test_weighted_sampling_used_when_counts_provided(mock_gen):
    """When content_word_counts is passed, weighted sampling is used."""
    mock_gen.return_value = SentenceResult(
        arabic="الوَلَدُ يَأْكُلُ التُّفَّاحَةَ",
        english="The boy eats the apple",
        transliteration="...",
    )

    big_known = [
        {"arabic": f"كلمة{i}", "english": f"word{i}", "lemma_id": i}
        for i in range(200)
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
    assert len(sent_known) <= 50


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
