"""Codex headless CLI runner for Alif audit + enrichment pipelines.

Background
----------
The 2026-05-26 A/B (`research/codex-vs-claude-{sentence-gen,enrichment-arabic}-2026-05-26.md`)
established that Codex `gpt-5.5` is comparable-to-better than Claude Haiku on
Alif's audit + enrichment work — fewer non-canonical Arabic pattern names,
fuller diacritization, richer cultural notes, 1.7× faster — while leaving the
naturalness gap on sentence generation untouched. The hybrid migration plan
(`research/alif-codex-migration-plan-2026-05-26.md`) flips audit + enrichment
to Codex; generation stays on Claude.

This module is the Codex side of that flip. It mirrors polyglot's
`polyglot/app/services/llm_cli.py` shape on purpose so the eventual
`alif_core/` extraction (6-week plan in `polyglot/CLAUDE.md`) is mechanical.

Wiring
------
`generate_via_codex_cli` is called from `llm.generate_completion` whenever
the call targets the ``claude_haiku`` alias (the default since 2026-05-26;
opt out with ``ALIF_AUDIT_PROVIDER=claude``). ``claude_sonnet`` (generation)
is never routed through Codex. On Codex failure the caller falls back to
Claude CLI then to the API chain (see `llm.generate_completion`).

Cost logging note
-----------------
Codex is free under the user's subscription, so we do not enter the limbic
cost-log for Codex calls (limbic has no Codex adapter today). Analytics
still land in Alif's per-day `llm_calls_*.jsonl` via `_log_call`.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


CODEX_DEFAULT_MODEL = os.environ.get("ALIF_CODEX_MODEL", "gpt-5.5")
CODEX_REASONING_EFFORT = os.environ.get("ALIF_CODEX_REASONING_EFFORT", "medium")
CODEX_CLI_QUOTA_COOLDOWN_S = int(os.environ.get("ALIF_CODEX_CLI_QUOTA_COOLDOWN_S", "21600"))

_CODEX_CLI_DISABLED_UNTIL = 0.0
_CODEX_CLI_DISABLED_REASON = ""

# Strip CLAUDECODE env var to allow nested invocation from Claude Code sessions
# (mirrors llm._CLEAN_ENV). Codex is a separate binary but the precaution
# costs nothing.
_CLEAN_ENV = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}


def codex_cli_available() -> bool:
    """Whether the codex binary is on PATH."""
    return shutil.which("codex") is not None


def _is_codex_cli_quota_error(message: str) -> bool:
    msg = (message or "").lower()
    quota_markers = (
        "out of extra usage",
        "usage limit",
        "rate limit",
        "too many requests",
        "quota",
    )
    return any(marker in msg for marker in quota_markers)


def mark_codex_cli_unavailable_from_error(error: Exception | str) -> None:
    """Cool down on quota errors (mirrors llm.mark_claude_cli_unavailable_from_error).

    Process-local — every new cron invocation gets one chance to discover the
    CLI has recovered.
    """
    global _CODEX_CLI_DISABLED_UNTIL, _CODEX_CLI_DISABLED_REASON
    message = str(error)
    if not _is_codex_cli_quota_error(message):
        return
    _CODEX_CLI_DISABLED_UNTIL = max(
        _CODEX_CLI_DISABLED_UNTIL,
        time.time() + max(1, CODEX_CLI_QUOTA_COOLDOWN_S),
    )
    _CODEX_CLI_DISABLED_REASON = message[:200]


def codex_cli_temporarily_disabled() -> bool:
    return time.time() < _CODEX_CLI_DISABLED_UNTIL


def codex_cli_disabled_reason() -> str:
    return _CODEX_CLI_DISABLED_REASON


def _codex_env() -> dict:
    env = dict(_CLEAN_ENV)
    codex_home = os.environ.get("ALIF_CODEX_HOME") or os.environ.get("CODEX_HOME")
    if codex_home:
        env["CODEX_HOME"] = codex_home
    # OPENAI_KEY / OPENAI_API_KEY aliasing (codex respects either)
    if env.get("OPENAI_KEY") and not env.get("OPENAI_API_KEY"):
        env["OPENAI_API_KEY"] = env["OPENAI_KEY"]
    return env


def _allow_null(node: Any) -> Any:
    if not isinstance(node, dict):
        return node
    out = copy.deepcopy(node)
    typ = out.get("type")
    if isinstance(typ, str) and typ != "null":
        out["type"] = [typ, "null"]
    elif isinstance(typ, list) and "null" not in typ:
        out["type"] = [*typ, "null"]
    return out


def strict_response_schema(schema: dict) -> dict:
    """Convert Alif's permissive JSON Schema into Codex strict structured-output
    shape: ``additionalProperties:false``, every property in ``required``,
    formerly-optional fields become nullable. Mirrors polyglot's same-named
    helper in `polyglot/app/services/llm_cli.py`.
    """

    def _walk(node: Any) -> Any:
        if isinstance(node, list):
            return [_walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        out = {k: _walk(v) for k, v in node.items()}
        props = out.get("properties")
        if isinstance(props, dict):
            req = set(node.get("required", []) or [])
            out["properties"] = {
                name: value if name in req else _allow_null(value)
                for name, value in props.items()
            }
            out["additionalProperties"] = False
            out["required"] = list(props.keys())
        return out

    return _walk(copy.deepcopy(schema))


def _parse_json_object(text: str) -> dict | None:
    """Best-effort JSON object parse. Handles raw JSON and embedded objects."""
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e > s:
        try:
            parsed = json.loads(text[s:e + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
    return None


class CodexCLIError(RuntimeError):
    pass


def generate_via_codex_cli(
    *,
    prompt: str,
    system_prompt: str = "",
    json_mode: bool = True,
    json_schema: dict | None = None,
    timeout: int = 120,
    model: str | None = None,
    reasoning_effort: str | None = None,
    runner=subprocess.run,
) -> dict[str, Any]:
    """Run a structured-output Codex call. Returns parsed JSON.

    Raises:
        CodexCLIError: any failure (binary missing, non-zero exit, timeout,
        unparseable output). The caller (llm.generate_completion) catches
        this and falls back to Claude CLI / API.
    """
    if not codex_cli_available():
        raise CodexCLIError(
            "codex CLI not available — install from https://github.com/openai/codex "
            "and authenticate with `codex auth`"
        )
    if codex_cli_temporarily_disabled():
        raise CodexCLIError(
            f"codex CLI temporarily disabled (quota): {_CODEX_CLI_DISABLED_REASON}"
        )

    if not json_mode and json_schema is None:
        return _generate_via_codex_cli_text(
            prompt=prompt, system_prompt=system_prompt, timeout=timeout,
            model=model, reasoning_effort=reasoning_effort, runner=runner,
        )

    # Structured-JSON path: write the schema to a temp file, point Codex at it.
    schema_path: str | None = None
    output_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".schema.json", delete=False) as sf:
            schema_for_codex = strict_response_schema(json_schema or _PERMISSIVE_JSON_SCHEMA)
            json.dump(schema_for_codex, sf, ensure_ascii=False)
            schema_path = sf.name
        with tempfile.NamedTemporaryFile("w", suffix=".output.json", delete=False) as of:
            output_path = of.name

        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        cmd = [
            "codex", "exec",
            "--model", model or CODEX_DEFAULT_MODEL,
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--color", "never",
            "--output-schema", schema_path,
            "--output-last-message", output_path,
            "-c", f'model_reasoning_effort="{reasoning_effort or CODEX_REASONING_EFFORT}"',
            full_prompt,
        ]
        try:
            proc = runner(
                cmd, capture_output=True, text=True, timeout=timeout, env=_codex_env(),
            )
        except TypeError:
            # Some test fakes don't accept env kwarg.
            proc = runner(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise CodexCLIError(f"codex CLI timed out after {timeout}s") from exc
        except (FileNotFoundError, OSError) as exc:
            raise CodexCLIError(f"codex CLI subprocess error: {exc}") from exc

        if proc.returncode != 0:
            stderr = (proc.stderr or "")[:400]
            raise CodexCLIError(f"codex CLI exit {proc.returncode}: {stderr}")

        text = Path(output_path).read_text(encoding="utf-8", errors="replace") if output_path else ""
        if not text.strip():
            text = proc.stdout or ""
        parsed = _parse_json_object(text)
        if parsed is None:
            raise CodexCLIError(f"codex CLI returned unparseable JSON: {text[:300]}")
        return parsed
    finally:
        for p in (schema_path, output_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass


def _generate_via_codex_cli_text(
    *,
    prompt: str,
    system_prompt: str,
    timeout: int,
    model: str | None,
    reasoning_effort: str | None,
    runner,
) -> dict[str, Any]:
    """Free-text (non-JSON) variant. Wraps the result in {"content": "..."} to
    match `_generate_via_claude_cli`'s legacy return shape.
    """
    output_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".output.txt", delete=False) as of:
            output_path = of.name
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        cmd = [
            "codex", "exec",
            "--model", model or CODEX_DEFAULT_MODEL,
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--color", "never",
            "--output-last-message", output_path,
            "-c", f'model_reasoning_effort="{reasoning_effort or CODEX_REASONING_EFFORT}"',
            full_prompt,
        ]
        try:
            proc = runner(
                cmd, capture_output=True, text=True, timeout=timeout, env=_codex_env(),
            )
        except TypeError:
            proc = runner(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise CodexCLIError(f"codex CLI timed out after {timeout}s") from exc
        except (FileNotFoundError, OSError) as exc:
            raise CodexCLIError(f"codex CLI subprocess error: {exc}") from exc
        if proc.returncode != 0:
            raise CodexCLIError(f"codex CLI exit {proc.returncode}: {(proc.stderr or '')[:400]}")
        text = Path(output_path).read_text(encoding="utf-8", errors="replace") if output_path else ""
        if not text.strip():
            text = proc.stdout or ""
        return {"content": text.strip()}
    finally:
        if output_path:
            try:
                os.unlink(output_path)
            except OSError:
                pass


# A permissive default for the rare json_mode=True / no-schema caller. Codex
# requires a schema; we accept any object.
_PERMISSIVE_JSON_SCHEMA = {"type": "object"}
