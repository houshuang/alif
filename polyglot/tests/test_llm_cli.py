import json
import subprocess
from pathlib import Path

from app.services import llm_cli
from app.services.llm_cli import (
    CLAUDE_DEFAULT_MODEL,
    CLAUDE_FAST_MODEL,
    _call_claude,
    call_structured_json,
    call_text,
    provider_order,
    resolve_model,
    strict_response_schema,
)


def _ok_claude(structured):
    return subprocess.CompletedProcess(
        ["claude"], 0, stdout=json.dumps({"structured_output": structured}), stderr=""
    )


def _fail(cmd, code=1, stderr="quota exceeded"):
    return subprocess.CompletedProcess(cmd, code, stdout="", stderr=stderr)


def _write_codex_output(cmd, payload):
    """Mimic codex writing its final message to --output-last-message."""
    out_path = cmd[cmd.index("--output-last-message") + 1]
    Path(out_path).write_text(payload, encoding="utf-8")


def test_strict_response_schema_requires_every_property_and_preserves_optional_as_null():
    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "reason": {"type": "string"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
        },
        "required": ["id"],
    }

    out = strict_response_schema(schema)

    assert out["additionalProperties"] is False
    assert out["required"] == ["id", "reason", "items"]
    assert out["properties"]["reason"]["type"] == ["string", "null"]
    assert out["properties"]["items"]["type"] == ["array", "null"]
    nested = out["properties"]["items"]["items"]
    assert nested["additionalProperties"] is False
    assert nested["required"] == ["name", "note"]
    assert nested["properties"]["note"]["type"] == ["string", "null"]


def test_resolve_model_maps_claude_aliases_to_codex_when_configured(monkeypatch):
    monkeypatch.setenv("POLYGLOT_LLM_PROVIDER", "codex")
    monkeypatch.setenv("POLYGLOT_CODEX_MODEL", "gpt-5.5")
    monkeypatch.setenv("POLYGLOT_CODEX_FAST_MODEL", "gpt-5.5-mini")

    assert resolve_model("sonnet", {"sonnet": "claude-sonnet"}) == "gpt-5.5"
    assert resolve_model("claude-sonnet-4-5-20250929") == "gpt-5.5"
    assert resolve_model("haiku", {"haiku": "claude-haiku"}) == "gpt-5.5-mini"
    assert resolve_model("gpt-5.4") == "gpt-5.4"


def test_claude_command_places_print_after_options():
    seen = {}

    def fake_run(cmd, capture_output=False, text=False, timeout=None):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({"structured_output": {"ok": True}}),
            stderr="",
        )

    result = _call_claude(
        prompt="return ok",
        schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        model="sonnet",
        timeout_s=10,
        log_context="test",
        runner=fake_run,
    )

    cmd = seen["cmd"]
    assert result == {"ok": True}
    assert cmd[0] == "claude"
    assert "--json-schema" in cmd
    assert "-p" in cmd
    assert cmd.index("--json-schema") < cmd.index("-p")
    assert cmd[-1] == "return ok"


# ── provider_order + failover ──────────────────────────────────────────────

def test_provider_order_defaults_to_primary_then_other(monkeypatch):
    monkeypatch.delenv("POLYGLOT_LLM_FALLBACK", raising=False)
    monkeypatch.setenv("POLYGLOT_LLM_PROVIDER", "claude")
    assert provider_order() == ["claude", "codex"]
    monkeypatch.setenv("POLYGLOT_LLM_PROVIDER", "codex")
    assert provider_order() == ["codex", "claude"]


def test_provider_order_can_disable_or_pin_fallback(monkeypatch):
    monkeypatch.setenv("POLYGLOT_LLM_PROVIDER", "codex")
    monkeypatch.setenv("POLYGLOT_LLM_FALLBACK", "0")
    assert provider_order() == ["codex"]
    monkeypatch.setenv("POLYGLOT_LLM_FALLBACK", "claude")
    assert provider_order() == ["codex", "claude"]


def test_resolve_model_maps_codex_back_to_claude_on_failover(monkeypatch):
    monkeypatch.setenv("POLYGLOT_CODEX_FAST_MODEL", "gpt-5.5-mini")
    # A model resolved for codex, re-resolved for a claude failover attempt:
    assert resolve_model("gpt-5.5", active="claude") == CLAUDE_DEFAULT_MODEL
    assert resolve_model("gpt-5.5-mini", active="claude") == CLAUDE_FAST_MODEL
    # Bare aliases map to full Claude IDs for the chat/text path.
    assert resolve_model("haiku", active="claude") == CLAUDE_FAST_MODEL
    assert resolve_model("sonnet", active="claude") == CLAUDE_DEFAULT_MODEL


def test_structured_json_fails_over_to_claude_when_codex_quota_exhausted(monkeypatch):
    monkeypatch.setenv("POLYGLOT_LLM_PROVIDER", "codex")
    monkeypatch.delenv("POLYGLOT_LLM_FALLBACK", raising=False)

    def runner(cmd, capture_output=False, text=False, timeout=None, env=None):
        if cmd[0] == "codex":
            return _fail(cmd)  # quota exhausted, no output file written
        return _ok_claude({"verdict": "ok"})

    result = call_structured_json(
        prompt="p",
        schema={"type": "object", "properties": {"verdict": {"type": "string"}}, "required": ["verdict"]},
        model="sonnet",
        timeout_s=5,
        log_context="test",
        runner=runner,
    )
    assert result == {"verdict": "ok"}


def test_structured_json_returns_none_when_all_providers_fail(monkeypatch):
    monkeypatch.setenv("POLYGLOT_LLM_PROVIDER", "claude")
    monkeypatch.delenv("POLYGLOT_LLM_FALLBACK", raising=False)

    def runner(cmd, capture_output=False, text=False, timeout=None, env=None):
        return _fail(cmd)

    result = call_structured_json(
        prompt="p",
        schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        model="sonnet",
        timeout_s=5,
        log_context="test",
        runner=runner,
    )
    assert result is None


def test_missing_primary_binary_fails_over(monkeypatch):
    monkeypatch.setenv("POLYGLOT_LLM_PROVIDER", "claude")
    monkeypatch.delenv("POLYGLOT_LLM_FALLBACK", raising=False)

    def runner(cmd, capture_output=False, text=False, timeout=None, env=None):
        if cmd[0] == "claude":
            raise FileNotFoundError("claude not installed")
        _write_codex_output(cmd, json.dumps({"ok": True}))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    result = call_structured_json(
        prompt="p",
        schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        model="sonnet",
        timeout_s=5,
        log_context="test",
        runner=runner,
    )
    assert result == {"ok": True}


def test_call_text_uses_claude_result_envelope(monkeypatch):
    monkeypatch.setenv("POLYGLOT_LLM_PROVIDER", "claude")
    monkeypatch.delenv("POLYGLOT_LLM_FALLBACK", raising=False)

    def runner(cmd, capture_output=False, text=False, timeout=None, env=None):
        assert cmd[0] == "claude"
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"result": "kosmos = world"}), stderr="")

    out = call_text(prompt="explain", model="haiku", timeout_s=5, log_context="test", runner=runner)
    assert out == "kosmos = world"


def test_call_text_fails_over_to_codex(monkeypatch):
    monkeypatch.setenv("POLYGLOT_LLM_PROVIDER", "claude")
    monkeypatch.delenv("POLYGLOT_LLM_FALLBACK", raising=False)

    def runner(cmd, capture_output=False, text=False, timeout=None, env=None):
        if cmd[0] == "claude":
            return _fail(cmd)
        _write_codex_output(cmd, "kosmos means world")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    out = call_text(prompt="explain", model="haiku", timeout_s=5, log_context="test", runner=runner)
    assert out == "kosmos means world"
