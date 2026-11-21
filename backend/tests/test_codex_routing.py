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
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import app.services.llm as llm_module
from app.services.llm import AllProvidersFailed, LLMError, generate_completion
import app.services.codex_cli as codex_module
from app.services.codex_cli import (
    CodexCLIError,
    _run_codex_command,
    strict_response_schema,
)


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
    codex_module._CODEX_CLI_DISABLED_UNTIL = 0.0
    codex_module._CODEX_CLI_DISABLED_REASON = ""


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


def test_codex_retries_without_sandbox_only_when_bubblewrap_is_missing():
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == 1:
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="warning: Codex could not find bubblewrap on PATH",
            )
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    result = _run_codex_command(
        cmd=["codex", "exec", "--sandbox", "read-only", "prompt"],
        runner=fake_runner,
        timeout=30,
    )

    assert result.returncode == 0
    assert len(calls) == 2
    assert calls[0][calls[0].index("--sandbox") + 1] == "read-only"
    assert calls[1][calls[1].index("--sandbox") + 1] == "danger-full-access"


def test_codex_does_not_weaken_sandbox_for_generic_failures():
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=1, stdout="", stderr="authentication failed")

    result = _run_codex_command(
        cmd=["codex", "exec", "--sandbox", "read-only", "prompt"],
        runner=fake_runner,
        timeout=30,
    )

    assert result.returncode == 1
    assert len(calls) == 1


def test_codex_prompt_is_streamed_over_stdin_not_argv():
    """Large passage vocabularies must not hit the OS ARG_MAX boundary."""
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    prompt = "vocabulary\n" * 100_000
    result = _run_codex_command(
        cmd=["codex", "exec", "-"],
        runner=fake_runner,
        timeout=30,
        prompt_input=prompt,
    )

    assert result.returncode == 0
    assert calls[0][0][-1] == "-"
    assert prompt not in calls[0][0]
    assert calls[0][1]["input"] == prompt


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


# ─── Dense due-word sentence writer (gpt_then_sonnet) ──────────────────────


@patch("app.services.llm._generate_via_codex_cli_with_logging")
@patch("app.services.llm._generate_via_claude_cli")
def test_due_menu_writer_uses_codex_first(mock_claude, mock_codex):
    mock_codex.return_value = {"sentences": []}

    generate_completion("menu", model_override=llm_module.CODEX_FIRST_WRITER)

    mock_codex.assert_called_once()
    mock_claude.assert_not_called()


@patch("app.services.llm._generate_via_codex_cli_with_logging")
@patch("app.services.llm._generate_via_claude_cli")
def test_due_menu_writer_falls_back_to_sonnet(mock_claude, mock_codex):
    mock_codex.side_effect = LLMError("codex down")
    mock_claude.return_value = {"sentences": []}

    generate_completion("menu", model_override=llm_module.CODEX_FIRST_WRITER)

    assert mock_claude.call_args.kwargs["model"] == "sonnet"


@patch("app.services.llm._generate_via_codex_cli_with_logging")
@patch("app.services.llm._generate_via_claude_cli")
def test_due_menu_writer_respects_claude_opt_out(mock_claude, mock_codex, monkeypatch):
    monkeypatch.setenv("ALIF_AUDIT_PROVIDER", "claude")
    mock_claude.return_value = {"sentences": []}

    generate_completion("menu", model_override=llm_module.CODEX_FIRST_WRITER)

    mock_codex.assert_not_called()
    assert mock_claude.call_args.kwargs["model"] == "sonnet"


def test_revoked_codex_login_starts_cool_down():
    codex_module.mark_codex_cli_unavailable_from_error(
        "codex CLI exit 1: failed to refresh available models: unexpected status "
        "401 Unauthorized: Encountered invalidated oauth token for user"
    )
    assert codex_module.codex_cli_temporarily_disabled()


# ─── json_mode without a schema ─────────────────────────────────────────────
# `__wrapped__` is the function under the provider guard's stub; the fake runner
# keeps these tests off the real CLI.


def test_json_mode_without_schema_makes_a_plain_call_and_parses_an_array(monkeypatch):
    from app.services import codex_cli

    monkeypatch.setattr(codex_cli, "codex_cli_available", lambda: True)
    calls = []

    def fake_runner(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout='```json\n[{"lemma_id": 1, "root": "ك.ت.ب"}]\n```',
            stderr="",
        )

    result = codex_cli.generate_via_codex_cli.__wrapped__(
        prompt="roots", json_mode=True, json_schema=None, runner=fake_runner,
    )

    assert result == [{"lemma_id": 1, "root": "ك.ت.ب"}]
    assert "--output-schema" not in calls[0][0]
    assert "JSON only" in calls[0][1]["input"]


def test_json_mode_without_schema_rejects_prose(monkeypatch):
    from app.services import codex_cli

    monkeypatch.setattr(codex_cli, "codex_cli_available", lambda: True)

    def fake_runner(cmd, **kwargs):
        return SimpleNamespace(returncode=0, stdout="I cannot do that.", stderr="")

    with pytest.raises(codex_cli.CodexCLIError, match="unparseable JSON"):
        codex_cli.generate_via_codex_cli.__wrapped__(
            prompt="roots", json_mode=True, json_schema=None, runner=fake_runner,
        )


def test_failure_message_carries_the_error_line_not_only_the_banner(monkeypatch):
    from app.services import codex_cli

    monkeypatch.setattr(codex_cli, "codex_cli_available", lambda: True)
    stderr = (
        "OpenAI Codex v0.159.2\n--------\n" + "prompt echo\n" * 200
        + 'ERROR: {"error":{"message":"Invalid schema for response_format"}}\n'
        + 'ERROR: {"error":{"message":"Invalid schema for response_format"}}\n'
    )

    def fake_runner(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr=stderr)

    with pytest.raises(codex_cli.CodexCLIError) as excinfo:
        codex_cli.generate_via_codex_cli.__wrapped__(
            prompt="x", json_schema={"type": "object", "properties": {"a": {"type": "string"}}},
            runner=fake_runner,
        )

    message = str(excinfo.value)
    assert message.startswith("codex CLI exit 1: ERROR:")
    assert message.count("Invalid schema") == 1
