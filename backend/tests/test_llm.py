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
