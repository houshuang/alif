"""Structured LLM CLI runner shared by Polyglot pipelines.

The production pipelines used to shell out to Claude directly. Keep that path
available, but route calls through this module so deployments can switch to
Codex headless with ``POLYGLOT_LLM_PROVIDER=codex``.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

Runner = Callable[..., subprocess.CompletedProcess]

CODEX_DEFAULT_MODEL = "gpt-5.5"
_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_-]+")


def provider() -> str:
    return os.environ.get("POLYGLOT_LLM_PROVIDER", "claude").strip().lower() or "claude"


def resolve_model(raw: str | None, aliases: dict[str, str] | None = None) -> str:
    """Resolve short model aliases for the active provider.

    For Claude, local service aliases such as ``sonnet`` and ``haiku`` map to
    Anthropic model IDs. For Codex, any Claude-specific model name or alias is
    redirected to the configured Codex model, so existing env vars can stay in
    place while production flips provider.
    """
    value = (raw or "").strip()
    lower = value.lower()
    active = provider()

    if active == "codex":
        default = os.environ.get("POLYGLOT_CODEX_MODEL", CODEX_DEFAULT_MODEL).strip() or CODEX_DEFAULT_MODEL
        fast = os.environ.get("POLYGLOT_CODEX_FAST_MODEL", default).strip() or default
        if not value or lower in {"sonnet", "opus"} or lower.startswith("claude-"):
            return default
        if lower == "haiku":
            return fast
        return value

    aliases = aliases or {}
    return aliases.get(lower, value)


def call_structured_json(
    *,
    prompt: str,
    schema: dict,
    model: str,
    timeout_s: int,
    log_context: str,
    runner: Runner | None = None,
) -> Optional[dict]:
    """Return parsed structured JSON from the active CLI provider.

    ``None`` means a total LLM failure. Callers must treat that as retryable
    failure, never as an empty successful result.
    """
    runner = runner or subprocess.run
    active = provider()
    if active == "codex":
        return _call_codex(
            prompt=prompt,
            schema=schema,
            model=resolve_model(model),
            timeout_s=timeout_s,
            log_context=log_context,
            runner=runner,
        )
    if active != "claude":
        log.warning("%s: unknown POLYGLOT_LLM_PROVIDER=%r; falling back to claude", log_context, active)
    return _call_claude(
        prompt=prompt,
        schema=schema,
        model=resolve_model(model),
        timeout_s=timeout_s,
        log_context=log_context,
        runner=runner,
    )


def _call_claude(
    *,
    prompt: str,
    schema: dict,
    model: str,
    timeout_s: int,
    log_context: str,
    runner: Runner,
) -> Optional[dict]:
    cmd = [
        "claude",
        "--output-format", "json",
        "--model", model,
        "--json-schema", json.dumps(schema, ensure_ascii=False),
        "-p",
        prompt,
    ]
    try:
        proc = runner(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        log.warning("%s: Claude CLI timed out after %ds", log_context, timeout_s)
        return None
    if proc.returncode != 0:
        log.warning("%s: Claude CLI failed (exit %d): %s",
                    log_context, proc.returncode, redact_secrets(proc.stderr)[:500])
        return None
    return parse_claude_envelope(proc.stdout, log_context=log_context)


def _call_codex(
    *,
    prompt: str,
    schema: dict,
    model: str,
    timeout_s: int,
    log_context: str,
    runner: Runner,
) -> Optional[dict]:
    schema_path: str | None = None
    output_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".schema.json", delete=False) as sf:
            json.dump(strict_response_schema(schema), sf, ensure_ascii=False)
            schema_path = sf.name
        with tempfile.NamedTemporaryFile("w", suffix=".output.json", delete=False) as of:
            output_path = of.name

        cmd = [
            "codex", "exec",
            "--model", model,
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--color", "never",
            "--output-schema", schema_path,
            "--output-last-message", output_path,
        ]
        effort = os.environ.get("POLYGLOT_CODEX_REASONING_EFFORT", "medium").strip()
        if effort:
            cmd.extend(["-c", f'model_reasoning_effort="{effort}"'])
        codex_home = os.environ.get("POLYGLOT_CODEX_HOME") or os.environ.get("CODEX_HOME")
        env = os.environ.copy()
        if codex_home:
            env["CODEX_HOME"] = codex_home
        if env.get("OPENAI_KEY") and not env.get("OPENAI_API_KEY"):
            env["OPENAI_API_KEY"] = env["OPENAI_KEY"]
        cmd.append(prompt)

        try:
            proc = runner(cmd, capture_output=True, text=True, timeout=timeout_s, env=env)
        except TypeError:
            # Unit tests often patch subprocess.run with a small fake that
            # doesn't accept env. Fall back to the simpler call shape.
            proc = runner(cmd, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            log.warning("%s: Codex CLI timed out after %ds", log_context, timeout_s)
            return None

        if proc.returncode != 0:
            log.warning("%s: Codex CLI failed (exit %d): %s",
                        log_context, proc.returncode, redact_secrets(proc.stderr)[:500])
            return None

        text = Path(output_path).read_text(encoding="utf-8", errors="replace") if output_path else ""
        if not text.strip():
            text = proc.stdout
        return parse_json_object(text, log_context=log_context)
    finally:
        for path in (schema_path, output_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def parse_claude_envelope(stdout: str, *, log_context: str) -> Optional[dict]:
    try:
        wrapper = json.loads(stdout)
    except json.JSONDecodeError:
        log.warning("%s: could not parse CLI envelope JSON: %s", log_context, stdout[:300])
        return None
    if not isinstance(wrapper, dict):
        log.warning("%s: unexpected CLI output shape: %s", log_context, stdout[:300])
        return None
    structured = wrapper.get("structured_output")
    if isinstance(structured, dict):
        return structured
    result_str = wrapper.get("result", "")
    if not result_str:
        return None
    return parse_json_object(result_str, log_context=log_context)


def parse_json_object(text: str, *, log_context: str) -> Optional[dict]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            log.warning("%s: no JSON object in CLI output: %s", log_context, text[:300])
            return None
        try:
            parsed = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            log.warning("%s: could not parse JSON object: %s", log_context, text[:300])
            return None
    return parsed if isinstance(parsed, dict) else None


def strict_response_schema(schema: dict) -> dict:
    """Convert service JSON Schema into OpenAI strict structured-output shape."""
    return _stricten(copy.deepcopy(schema))


def _stricten(node: Any) -> Any:
    if isinstance(node, list):
        return [_stricten(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {k: _stricten(v) for k, v in node.items()}
    props = out.get("properties")
    if isinstance(props, dict):
        originally_required = set(node.get("required", []) or [])
        out["properties"] = {
            name: value if name in originally_required else _allow_null(value)
            for name, value in props.items()
        }
        out["additionalProperties"] = False
        out["required"] = list(props.keys())
    return out


def _allow_null(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return schema
    out = copy.deepcopy(schema)
    typ = out.get("type")
    if isinstance(typ, str):
        if typ != "null":
            out["type"] = [typ, "null"]
    elif isinstance(typ, list):
        if "null" not in typ:
            out["type"] = [*typ, "null"]
    enum = out.get("enum")
    if isinstance(enum, list) and None not in enum:
        out["enum"] = [*enum, None]
    return out


def redact_secrets(text: str | None) -> str:
    return _SECRET_RE.sub("<redacted-sk>", text or "")
