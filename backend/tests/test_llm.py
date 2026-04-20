"""Tests for the LLM service.

Tests prompt construction and fallback behavior with mocked litellm.
"""

from unittest.mock import MagicMock, patch

import inspect

import pytest

from app.services.llm import (
    AllProvidersFailed,
    LLMError,
    SentenceResult,
    generate_completion,
    generate_sentence,
    generate_sentences_batch,
    rerank_sentences_by_naturalness,
)


@patch("app.services.llm._generate_via_claude_cli")
def test_generate_completion_uses_claude_cli_by_default(mock_cli):
    """Default (no model_override) should try Claude CLI haiku first."""
    mock_cli.return_value = {"result": "ok"}

    result = generate_completion("test prompt", system_prompt="be helpful")

    assert result == {"result": "ok"}
    mock_cli.assert_called_once()
    assert mock_cli.call_args.kwargs["model"] == "haiku"


@patch("app.services.llm.litellm.completion")
@patch("app.services.llm._get_api_key")
@patch("app.services.llm._generate_via_claude_cli")
def test_fallback_to_api_when_cli_fails(mock_cli, mock_key, mock_completion):
    """Should fall back to API chain when Claude CLI fails."""
    from app.services.llm import LLMError
    mock_cli.side_effect = LLMError("CLI not available")
    mock_key.return_value = "fake-key"

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"result": "from openai"}'
    mock_completion.return_value = mock_response

    result = generate_completion("test prompt")
    assert result == {"result": "from openai"}
    # Should have tried OpenAI (first in MODELS list)
    call_kwargs = mock_completion.call_args.kwargs
    assert "gpt" in call_kwargs["model"]


@patch("app.services.llm.litellm.completion")
@patch("app.services.llm._get_api_key")
@patch("app.services.llm._generate_via_claude_cli")
def test_all_providers_fail_raises(mock_cli, mock_key, mock_completion):
    """Should raise AllProvidersFailed when all providers fail."""
    from app.services.llm import LLMError
    mock_cli.side_effect = LLMError("CLI not available")
    mock_key.return_value = "fake-key"
    mock_completion.side_effect = Exception("down")

    with pytest.raises(AllProvidersFailed):
        generate_completion("test prompt")


@patch("app.services.llm._generate_via_claude_cli")
def test_explicit_claude_sonnet_override(mock_cli):
    """model_override='claude_sonnet' should use CLI with sonnet."""
    mock_cli.return_value = {"sentences": []}

    generate_completion("test", model_override="claude_sonnet")

    assert mock_cli.call_args.kwargs["model"] == "sonnet"


@patch("app.services.llm.generate_completion")
def test_generate_sentence_returns_sentence_result(mock_completion):
    """generate_sentence should return a SentenceResult."""
    mock_completion.return_value = {
        "arabic": "الكِتَابُ كَبِيرٌ",
        "english": "The book is big",
        "transliteration": "al-kitābu kabīrun",
    }

    result = generate_sentence(
        target_word="كَبِير",
        target_translation="big",
        known_words=[{"arabic": "كِتَاب", "english": "book"}],
    )

    assert isinstance(result, SentenceResult)
    assert result.arabic == "الكِتَابُ كَبِيرٌ"
    assert result.english == "The book is big"
    assert result.transliteration == "al-kitābu kabīrun"


@patch("app.services.llm.generate_completion")
def test_generate_sentence_includes_retry_feedback(mock_completion):
    """Retry feedback should be included in the prompt."""
    mock_completion.return_value = {
        "arabic": "test",
        "english": "test",
        "transliteration": "test",
    }

    generate_sentence(
        target_word="كِتَاب",
        target_translation="book",
        known_words=[],
        retry_feedback="Target word was missing",
    )

    call_args = mock_completion.call_args
    prompt = call_args.kwargs.get("prompt") or call_args.args[0]
    assert "PREVIOUS ATTEMPT FAILED" in prompt
    assert "Target word was missing" in prompt


@patch("app.services.llm.generate_completion")
def test_generate_sentence_difficulty_in_prompt(mock_completion):
    """Difficulty hint should appear in the prompt."""
    mock_completion.return_value = {
        "arabic": "test",
        "english": "test",
        "transliteration": "test",
    }

    generate_sentence(
        target_word="كِتَاب",
        target_translation="book",
        known_words=[],
        difficulty_hint="advanced",
    )

    prompt = mock_completion.call_args.kwargs.get("prompt") or mock_completion.call_args.args[0]
    assert "advanced" in prompt


# -------------------------------------------------------------------------
# Phase 4: prompt enrichment (example_ar / example_en) + candidate ranker
# -------------------------------------------------------------------------


@patch("app.services.llm.generate_completion")
def test_generate_sentence_includes_example_block_when_populated(mock_completion):
    """Populated example_ar/example_en produce an EXAMPLE block in the prompt."""
    mock_completion.return_value = {
        "arabic": "t", "english": "t", "transliteration": "t",
    }

    generate_sentence(
        target_word="اِسْتَوَى",
        target_translation="to sit upright / settle",
        known_words=[],
        target_example_ar="اِسْتَوَى الشَّيْخُ عَلَى كُرْسِيِّهِ",
        target_example_en="The elder settled onto his chair.",
    )

    prompt = mock_completion.call_args.kwargs.get("prompt") or mock_completion.call_args.args[0]
    assert "Example of correct usage" in prompt
    assert "اِسْتَوَى الشَّيْخُ عَلَى كُرْسِيِّهِ" in prompt
    assert "The elder settled onto his chair." in prompt


@patch("app.services.llm.generate_completion")
def test_generate_sentence_omits_example_block_when_null(mock_completion):
    """Missing example_ar → no EXAMPLE block (keeps prompt clean)."""
    mock_completion.return_value = {
        "arabic": "t", "english": "t", "transliteration": "t",
    }

    generate_sentence(
        target_word="كِتَاب",
        target_translation="book",
        known_words=[],
    )

    prompt = mock_completion.call_args.kwargs.get("prompt") or mock_completion.call_args.args[0]
    assert "Example of correct usage" not in prompt


@patch("app.services.llm.generate_completion")
def test_generate_sentence_omits_example_block_when_partial(mock_completion):
    """Only target_example_ar (no English) → block suppressed — both sides needed
    for sense grounding."""
    mock_completion.return_value = {
        "arabic": "t", "english": "t", "transliteration": "t",
    }

    generate_sentence(
        target_word="كِتَاب",
        target_translation="book",
        known_words=[],
        target_example_ar="هَذَا كِتَابٌ",
        target_example_en=None,
    )

    prompt = mock_completion.call_args.kwargs.get("prompt") or mock_completion.call_args.args[0]
    assert "Example of correct usage" not in prompt


def test_generate_sentences_batch_default_count_is_5():
    """Phase 4 bump: default count should be 5 (was 3)."""
    sig = inspect.signature(generate_sentences_batch)
    assert sig.parameters["count"].default == 5


@patch("app.services.llm.generate_completion")
def test_generate_sentences_batch_includes_example_in_prompt(mock_completion):
    """Batch prompt includes EXAMPLE block when both example_ar/en are given."""
    # First call: sentence generation
    # Second call: rerank (returns empty verdicts → fails, falls back to unranked)
    mock_completion.side_effect = [
        {"sentences": [{"arabic": "س", "english": "s", "transliteration": "s"}]},
        {"verdicts": []},
    ]

    generate_sentences_batch(
        target_word="اِسْتَوَى",
        target_translation="to settle",
        known_words=[],
        count=5,
        target_example_ar="اِسْتَوَى الشَّيْخُ عَلَى كُرْسِيِّهِ",
        target_example_en="The elder settled onto his chair.",
    )

    first_call = mock_completion.call_args_list[0]
    prompt = first_call.kwargs.get("prompt") or first_call.args[0]
    assert "Example of correct usage" in prompt
    assert "اِسْتَوَى الشَّيْخُ عَلَى كُرْسِيِّهِ" in prompt


@patch("app.services.llm.generate_completion")
def test_rerank_picks_good_returns_top_k(mock_completion):
    """5 candidates, 3 GOOD → top_k=2 returns 2 in LLM order, skipping BADs."""
    mock_completion.return_value = {
        "verdicts": [
            {"index": 0, "verdict": "BAD", "category": "WRONG_SENSE", "explanation": "..."},
            {"index": 1, "verdict": "GOOD", "category": "OK", "explanation": "..."},
            {"index": 2, "verdict": "BAD", "category": "FORCED_COMBINATION", "explanation": "..."},
            {"index": 3, "verdict": "GOOD", "category": "OK", "explanation": "..."},
            {"index": 4, "verdict": "GOOD", "category": "OK", "explanation": "..."},
        ]
    }

    candidates = [
        SentenceResult(arabic=f"s{i}", english=f"e{i}", transliteration="")
        for i in range(5)
    ]

    top = rerank_sentences_by_naturalness(
        candidates, target_word="كِتَاب", target_translation="book", top_k=2,
    )

    assert len(top) == 2
    # Preserves Haiku's order — first two GOOD are indices 1 and 3
    assert top[0].arabic == "s1"
    assert top[1].arabic == "s3"


@patch("app.services.llm.generate_completion")
def test_rerank_all_bad_returns_empty(mock_completion):
    """All candidates BAD → returns [] so caller can fall back."""
    mock_completion.return_value = {
        "verdicts": [
            {"index": 0, "verdict": "BAD", "category": "WRONG_SENSE", "explanation": "..."},
            {"index": 1, "verdict": "BAD", "category": "FORCED_COMBINATION", "explanation": "..."},
        ]
    }

    candidates = [
        SentenceResult(arabic="a", english="e", transliteration=""),
        SentenceResult(arabic="b", english="f", transliteration=""),
    ]

    top = rerank_sentences_by_naturalness(
        candidates, target_word="كِتَاب", target_translation="book", top_k=2,
    )

    assert top == []


@patch("app.services.llm.generate_completion")
def test_rerank_empty_verdicts_raises(mock_completion):
    """Empty/malformed verdicts list raises LLMError so caller falls back."""
    mock_completion.return_value = {"verdicts": []}

    candidates = [SentenceResult(arabic="a", english="e", transliteration="")]

    with pytest.raises(LLMError):
        rerank_sentences_by_naturalness(
            candidates, target_word="كِتَاب", target_translation="book",
        )


@patch("app.services.llm.generate_completion")
def test_batch_falls_back_to_unranked_when_rerank_fails(mock_completion):
    """When the rerank Haiku call fails, generate_sentences_batch returns the
    original unranked candidates rather than dropping everything."""
    # First call: gen → 3 sentences
    # Second call: rerank → empty verdicts → raises LLMError internally
    mock_completion.side_effect = [
        {"sentences": [
            {"arabic": "s0", "english": "e0", "transliteration": ""},
            {"arabic": "s1", "english": "e1", "transliteration": ""},
            {"arabic": "s2", "english": "e2", "transliteration": ""},
        ]},
        {"verdicts": []},  # triggers LLMError in rerank
    ]

    results = generate_sentences_batch(
        target_word="كِتَاب",
        target_translation="book",
        known_words=[],
        count=3,
    )

    # Fail-open: caller gets the 3 unranked candidates back.
    assert len(results) == 3
    assert results[0].arabic == "s0"
