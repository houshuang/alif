"""Tests for the hybrid Codex / Claude CLI routing in generate_completion.

Covers the 2026-05-26 migration that flips audit + enrichment pipelines from
Claude Haiku CLI to Codex `gpt-5.5` when ``ALIF_AUDIT_PROVIDER=codex`` is set.
Generation (``claude_sonnet``) is never routed through Codex regardless.

Plan / A/B background:
  research/alif-codex-migration-plan-2026-05-26.md
  research/codex-vs-claude-enrichment-arabic-2026-05-26.md
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

import app.services.llm as llm_module
from app.services.llm import AllProvidersFailed, LLMError, generate_completion
import app.services.codex_cli as codex_module
from app.services.codex_cli import CodexCLIError, strict_response_schema


@pytest.fixture(autouse=True)
def _reset_cli_state(monkeypatch):
    """Reset the process-local Claude + Codex cool-down state between tests."""
    llm_module._CLAUDE_CLI_DISABLED_UNTIL = 0.0
    llm_module._CLAUDE_CLI_DISABLED_REASON = ""
    codex_module._CODEX_CLI_DISABLED_UNTIL = 0.0
    codex_module._CODEX_CLI_DISABLED_REASON = ""
    # Default: provider routing OFF
    monkeypatch.delenv("ALIF_AUDIT_PROVIDER", raising=False)
    yield


# ─── ALIF_AUDIT_PROVIDER routing ───────────────────────────────────────────


@patch("app.services.llm._generate_via_codex_cli_with_logging")
@patch("app.services.llm._generate_via_claude_cli")
def test_default_provider_is_codex(mock_claude, mock_codex):
    """Without ALIF_AUDIT_PROVIDER, claude_haiku now routes through Codex.

    Default flipped 2026-05-26 after the two A/Bs landed (sentence-gen +
    enrichment). Claude CLI remains the fallback under failover.
    """
    mock_codex.return_value = {"ok": "from codex"}

    result = generate_completion("hi", model_override="claude_haiku")

    assert result == {"ok": "from codex"}
    mock_codex.assert_called_once()
    mock_claude.assert_not_called()


@patch("app.services.llm._generate_via_codex_cli_with_logging")
@patch("app.services.llm._generate_via_claude_cli")
def test_explicit_claude_opt_out(mock_claude, mock_codex, monkeypatch):
    """ALIF_AUDIT_PROVIDER=claude is the escape hatch — Codex must be skipped."""
    monkeypatch.setenv("ALIF_AUDIT_PROVIDER", "claude")
    mock_claude.return_value = {"ok": "from claude"}

    result = generate_completion("hi", model_override="claude_haiku")

    assert result == {"ok": "from claude"}
    mock_claude.assert_called_once()
    mock_codex.assert_not_called()


@patch("app.services.llm._generate_via_codex_cli_with_logging")
@patch("app.services.llm._generate_via_claude_cli")
def test_explicit_codex_routes_through_codex(mock_claude, mock_codex, monkeypatch):
    """ALIF_AUDIT_PROVIDER=codex matches the new default — Codex first."""
    monkeypatch.setenv("ALIF_AUDIT_PROVIDER", "codex")
    mock_codex.return_value = {"ok": "from codex"}

    result = generate_completion("hi", model_override="claude_haiku")

    assert result == {"ok": "from codex"}
    mock_codex.assert_called_once()
    mock_claude.assert_not_called()


@patch("app.services.llm._generate_via_codex_cli_with_logging")
@patch("app.services.llm._generate_via_claude_cli")
def test_default_leaves_sonnet_on_claude(mock_claude, mock_codex):
    """Sonnet (generation) must stay on Claude even though Codex is now default
    for haiku. The 2026-05-26 sentence-gen A/B showed Codex weaker on Arabic
    naturalness under vocab constraint, so generation is explicitly excluded.
    """
    mock_claude.return_value = {"sentences": []}

    generate_completion("sentence prompt", model_override="claude_sonnet")

    mock_claude.assert_called_once()
    assert mock_claude.call_args.kwargs["model"] == "sonnet"
    mock_codex.assert_not_called()


@patch("app.services.llm._generate_via_codex_cli_with_logging")
@patch("app.services.llm._generate_via_claude_cli")
def test_default_routes_implicit_haiku(mock_claude, mock_codex):
    """When no model_override is given, the implicit claude_haiku routes through Codex."""
    mock_codex.return_value = {"ok": "from codex"}

    generate_completion("hi")

    mock_codex.assert_called_once()
    mock_claude.assert_not_called()


@patch("app.services.llm._generate_via_codex_cli_with_logging")
@patch("app.services.llm._generate_via_claude_cli")
def test_invalid_env_value_falls_back_to_codex_default(mock_claude, mock_codex, monkeypatch):
    """ALIF_AUDIT_PROVIDER=garbage should fall back to the codex default (not
    silently break to Claude — would invert the post-A/B intent)."""
    monkeypatch.setenv("ALIF_AUDIT_PROVIDER", "anthropic-via-zapier")
    mock_codex.return_value = {"ok": "from codex"}

    generate_completion("hi", model_override="claude_haiku")

    mock_codex.assert_called_once()
    mock_claude.assert_not_called()


# ─── Failover chain ────────────────────────────────────────────────────────


@patch("app.services.llm._generate_via_codex_cli_with_logging")
@patch("app.services.llm._generate_via_claude_cli")
def test_codex_failure_falls_back_to_claude_cli(mock_claude, mock_codex, monkeypatch):
    """When Codex fails, Claude CLI should still be tried before the API chain."""
    monkeypatch.setenv("ALIF_AUDIT_PROVIDER", "codex")
    mock_codex.side_effect = LLMError("codex CLI exit 1")
    mock_claude.return_value = {"ok": "from claude"}

    result = generate_completion("hi", model_override="claude_haiku")

    assert result == {"ok": "from claude"}
    mock_codex.assert_called_once()
    mock_claude.assert_called_once()


@patch("app.services.llm.litellm.completion")
@patch("app.services.llm._get_api_key")
@patch("app.services.llm._generate_via_codex_cli_with_logging")
@patch("app.services.llm._generate_via_claude_cli")
def test_codex_then_claude_cli_then_api_chain(
    mock_claude, mock_codex, mock_key, mock_completion, monkeypatch
):
    """Full failover: Codex → Claude CLI → API chain."""
    monkeypatch.setenv("ALIF_AUDIT_PROVIDER", "codex")
    mock_codex.side_effect = LLMError("codex unavailable")
    mock_claude.side_effect = LLMError("claude CLI down")
    mock_key.return_value = "fake-key"

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = '{"ok": "from api"}'
    mock_completion.return_value = response

    result = generate_completion("hi", model_override="claude_haiku")

    assert result == {"ok": "from api"}
    mock_codex.assert_called_once()
    mock_claude.assert_called_once()
    mock_completion.assert_called()


@patch("app.services.llm._generate_via_codex_cli_with_logging")
@patch("app.services.llm._generate_via_claude_cli")
def test_codex_failure_cli_only_still_tries_claude_cli(mock_claude, mock_codex, monkeypatch):
    """cli_only=True means skip the API chain on total failure, but BOTH CLIs are still tried.

    cli_only is for tasks where API models perform poorly (e.g. Arabic
    morphology verification — GPT-5.2 is too aggressive). It is not a hint to
    skip the second CLI; both Codex and Claude CLI are acceptable.
    """
    monkeypatch.setenv("ALIF_AUDIT_PROVIDER", "codex")
    mock_codex.side_effect = LLMError("codex down")
    mock_claude.return_value = {"ok": "from claude"}

    result = generate_completion("hi", model_override="claude_haiku", cli_only=True)

    assert result == {"ok": "from claude"}
    mock_codex.assert_called_once()
    mock_claude.assert_called_once()


# ─── Codex cool-down ───────────────────────────────────────────────────────


@patch("app.services.llm._generate_via_claude_cli")
def test_codex_quota_cooldown_routes_directly_to_claude(mock_claude, monkeypatch):
    """After Codex's quota cool-down is active, the routing skips Codex entirely
    on subsequent calls in the same process."""
    monkeypatch.setenv("ALIF_AUDIT_PROVIDER", "codex")
    codex_module._CODEX_CLI_DISABLED_UNTIL = time.time() + 60
    codex_module._CODEX_CLI_DISABLED_REASON = "quota"
    mock_claude.return_value = {"ok": "from claude"}

    try:
        # Codex helper raises LLMError due to cool-down; falls through to Claude CLI.
        result = generate_completion("hi", model_override="claude_haiku")
    finally:
        codex_module._CODEX_CLI_DISABLED_UNTIL = 0.0
        codex_module._CODEX_CLI_DISABLED_REASON = ""

    assert result == {"ok": "from claude"}
    mock_claude.assert_called_once()


def test_codex_quota_marker_sets_cooldown():
    """A Codex quota error must engage the process-local cool-down."""
    codex_module._CODEX_CLI_DISABLED_UNTIL = 0.0
    codex_module._CODEX_CLI_DISABLED_REASON = ""
    try:
        codex_module.mark_codex_cli_unavailable_from_error(
            "codex exit 1: usage limit exceeded"
        )
        assert codex_module.codex_cli_temporarily_disabled() is True
    finally:
        codex_module._CODEX_CLI_DISABLED_UNTIL = 0.0
        codex_module._CODEX_CLI_DISABLED_REASON = ""


def test_codex_non_quota_error_does_not_cooldown():
    """A transient Codex error (e.g. network blip) should not pin the cool-down."""
    codex_module._CODEX_CLI_DISABLED_UNTIL = 0.0
    codex_module._CODEX_CLI_DISABLED_REASON = ""
    codex_module.mark_codex_cli_unavailable_from_error(
        "codex exit 2: connection refused"
    )
    assert codex_module.codex_cli_temporarily_disabled() is False


# ─── strict_response_schema (Alif → Codex schema shape) ────────────────────


def test_strict_response_schema_marks_all_properties_required():
    """Codex requires every property in `required`; optional fields become
    nullable. Mirrors polyglot/app/services/llm_cli.py::strict_response_schema."""
    src = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"},
            "nick": {"type": "string"},  # not in required
        },
        "required": ["name", "age"],
    }
    out = strict_response_schema(src)

    assert out["additionalProperties"] is False
    assert set(out["required"]) == {"name", "age", "nick"}
    # Originally-required fields keep their original type
    assert out["properties"]["name"] == {"type": "string"}
    assert out["properties"]["age"] == {"type": "integer"}
    # Originally-optional fields become nullable
    assert out["properties"]["nick"]["type"] == ["string", "null"]


def test_strict_response_schema_recurses_into_nested_objects():
    """Nested objects + arrays of objects must also become strict."""
    src = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "label": {"type": "string"},
                    },
                    "required": ["id"],
                },
            },
        },
        "required": ["items"],
    }
    out = strict_response_schema(src)
    inner = out["properties"]["items"]["items"]
    assert inner["additionalProperties"] is False
    assert set(inner["required"]) == {"id", "label"}
    assert inner["properties"]["id"] == {"type": "integer"}
    assert inner["properties"]["label"]["type"] == ["string", "null"]


def test_strict_response_schema_does_not_mutate_input():
    """Defensive: callers reuse the same schema dict across calls (Alif's
    _FORMS_BATCH_SCHEMA, _ETYMOLOGY_BATCH_SCHEMA, ...). The strict converter
    must never mutate the source object."""
    src = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": ["x"],
    }
    snapshot = {**src, "properties": dict(src["properties"]), "required": list(src["required"])}
    _ = strict_response_schema(src)
    assert src == snapshot
