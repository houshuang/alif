"""Tests for the LLM service.

Tests prompt construction and fallback behavior with mocked litellm.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.llm import (
    AllProvidersFailed,
    SentenceResult,
    generate_completion,
    generate_sentence,
)


@patch("app.services.llm.litellm.completion")
@patch("app.services.llm._get_api_key")
def test_generate_completion_uses_first_available_model(mock_key, mock_completion):
    """Should try Gemini first when key is available."""
    mock_key.side_effect = lambda cfg: "fake-key" if cfg["name"] == "gemini" else None

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"result": "ok"}'
    mock_completion.return_value = mock_response

    result = generate_completion("test prompt", system_prompt="be helpful")

    assert result == {"result": "ok"}
    call_kwargs = mock_completion.call_args.kwargs
    assert "gemini" in call_kwargs["model"]


@patch("app.services.llm.litellm.completion")
@patch("app.services.llm._get_api_key")
def test_fallback_to_openai_when_gemini_fails(mock_key, mock_completion):
    """Should fall back to OpenAI when Gemini raises an error."""
    mock_key.return_value = "fake-key"

    # First call (gemini) fails, second (openai) succeeds
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"result": "from openai"}'

    mock_completion.side_effect = [
        Exception("Gemini is down"),
        mock_response,
    ]

    result = generate_completion("test prompt")
    assert result == {"result": "from openai"}
    assert mock_completion.call_count == 2


@patch("app.services.llm.litellm.completion")
@patch("app.services.llm._get_api_key")
def test_all_providers_fail_raises(mock_key, mock_completion):
    """Should raise AllProvidersFailed when all providers fail."""
    mock_key.return_value = "fake-key"
    mock_completion.side_effect = Exception("down")

    with pytest.raises(AllProvidersFailed):
        generate_completion("test prompt")


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
