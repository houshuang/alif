"""Structured + free-text LLM CLI runner shared by Polyglot pipelines.

Every LLM call in Polyglot goes through this module so that:

- both Claude (``claude -p``) and Codex headless (``codex exec``) are usable at
  every call site, selected by ``POLYGLOT_LLM_PROVIDER`` (default ``claude``);
- a failure of the primary provider (quota exhaustion, rate limit, timeout,
  missing CLI binary, parse failure) **fails over** to the other provider
  rather than stalling the pipeline. Set ``POLYGLOT_LLM_FALLBACK=0`` to disable,
  or to an explicit provider name to pin the fallback.

A ``None`` return from a call means *all* configured providers failed; callers
must treat that as a retryable failure, never as an empty successful result.
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
# Claude model IDs used when a call fails over to Claude with a model string
# that was resolved for the other provider (or for the few callers that pass a
# bare alias). Env-overridable so a model bump never needs a code change.
CLAUDE_DEFAULT_MODEL = os.environ.get("POLYGLOT_CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
CLAUDE_FAST_MODEL = os.environ.get("POLYGLOT_CLAUDE_FAST_MODEL", "claude-haiku-4-5-20251001")
_VALID_PROVIDERS = ("claude", "codex")
_SECRET_RE = re.compile(r"sk-[A-Za-z0-9_-]+")


def provider() -> str:
    primary = os.environ.get("POLYGLOT_LLM_PROVIDER", "claude").strip().lower() or "claude"
    return primary if primary in _VALID_PROVIDERS else "claude"


def provider_order() -> list[str]:
    """Return the providers to try, in order: primary first, then fallback.

    Primary is ``POLYGLOT_LLM_PROVIDER`` (default ``claude``). Failover is on by
    default and targets the *other* provider; ``POLYGLOT_LLM_FALLBACK`` overrides:
    a falsy value (``0``/``off``/``none``/``no``/``false``) disables failover, and
    an explicit provider name pins the fallback to that provider.
    """
    primary = provider()
    fb = os.environ.get("POLYGLOT_LLM_FALLBACK", "auto").strip().lower()
    if fb in {"0", "off", "none", "no", "false"}:
        return [primary]
    if fb in _VALID_PROVIDERS:
        order = [primary, fb]
    else:
        order = [primary, *[p for p in _VALID_PROVIDERS if p != primary]]
    seen: set[str] = set()
    return [p for p in order if not (p in seen or seen.add(p))]


def resolve_model(
    raw: str | None,
    aliases: dict[str, str] | None = None,
    active: str | None = None,
) -> str:
    """Resolve a model name for ``active`` provider (defaults to the primary).

    For Claude, local service aliases such as ``sonnet`` and ``haiku`` map to
    Anthropic model IDs. For Codex, any Claude-specific model name or alias is
    redirected to the configured Codex model. Because services pre-resolve their
    model for the import-time provider, the failover loop re-resolves per
    attempt — so this also maps a Codex model string back to a Claude model
    (by tier) when a call fails over to Claude.
    """
    value = (raw or "").strip()
    lower = value.lower()
    active = (active or provider())

    if active == "codex":
        default = os.environ.get("POLYGLOT_CODEX_MODEL", CODEX_DEFAULT_MODEL).strip() or CODEX_DEFAULT_MODEL
        fast = os.environ.get("POLYGLOT_CODEX_FAST_MODEL", default).strip() or default
        if not value or lower in {"sonnet", "opus"}:
            return default
        if lower == "haiku":
            return fast
        if lower.startswith("claude-"):
            return fast if "haiku" in lower else default
        return value

    # Claude (or unknown provider → Claude). Service-supplied aliases win.
    aliases = aliases or {}
    if lower in aliases:
        return aliases[lower]
    if lower == "haiku":
        return CLAUDE_FAST_MODEL
    if lower == "sonnet":
        return CLAUDE_DEFAULT_MODEL
    if value.startswith("claude-") or lower == "opus":
        return value
    if not value:
        return CLAUDE_DEFAULT_MODEL
    # A non-Claude (Codex) model resolved for the other provider: map by tier.
    codex_fast = os.environ.get("POLYGLOT_CODEX_FAST_MODEL", "").strip().lower()
    if (codex_fast and lower == codex_fast) or "mini" in lower or "fast" in lower:
        return CLAUDE_FAST_MODEL
    if lower.startswith(("gpt-", "o1", "o3", "o4", "codex")):
        return CLAUDE_DEFAULT_MODEL
    return value


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
    order = provider_order()
    for i, prov in enumerate(order):
        fn = _call_codex if prov == "codex" else _call_claude
        result = fn(
            prompt=prompt,
            schema=schema,
            model=resolve_model(model, active=prov),
            timeout_s=timeout_s,
            log_context=log_context,
            runner=runner,
        )
        if result is not None:
            if i > 0:
                log.warning("%s: recovered on fallback provider %r after %r failed",
                            log_context, prov, order[:i])
            return result
    log.warning("%s: all LLM providers failed (%s)", log_context, ", ".join(order))
    return None


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
    except (FileNotFoundError, OSError) as exc:
        log.warning("%s: Claude CLI unavailable: %s", log_context, exc)
        return None
    if proc.returncode != 0:
        log.warning("%s: Claude CLI failed (exit %d): %s",
                    log_context, proc.returncode, redact_secrets(proc.stderr)[:500])
        return None
    return parse_claude_envelope(proc.stdout, log_context=log_context)


def _codex_env() -> dict:
    codex_home = os.environ.get("POLYGLOT_CODEX_HOME") or os.environ.get("CODEX_HOME")
    env = os.environ.copy()
    if codex_home:
        env["CODEX_HOME"] = codex_home
    if env.get("OPENAI_KEY") and not env.get("OPENAI_API_KEY"):
        env["OPENAI_API_KEY"] = env["OPENAI_KEY"]
    return env


def _codex_effort_args() -> list[str]:
    effort = os.environ.get("POLYGLOT_CODEX_REASONING_EFFORT", "medium").strip()
    return ["-c", f'model_reasoning_effort="{effort}"'] if effort else []


def _run_codex(
    *,
    cmd: list[str],
    timeout_s: int,
    log_context: str,
    runner: Runner,
) -> Optional[subprocess.CompletedProcess]:
    """Invoke the codex CLI; return the completed process or None on failure."""
    env = _codex_env()
    try:
        proc = runner(cmd, capture_output=True, text=True, timeout=timeout_s, env=env)
    except TypeError:
        # Unit tests often patch subprocess.run with a small fake that doesn't
        # accept env. Fall back to the simpler call shape.
        proc = runner(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        log.warning("%s: Codex CLI timed out after %ds", log_context, timeout_s)
        return None
    except (FileNotFoundError, OSError) as exc:
        log.warning("%s: Codex CLI unavailable: %s", log_context, exc)
        return None
    if proc.returncode != 0:
        log.warning("%s: Codex CLI failed (exit %d): %s",
                    log_context, proc.returncode, redact_secrets(proc.stderr)[:500])
        return None
    return proc


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
            *_codex_effort_args(),
            prompt,
        ]
        proc = _run_codex(cmd=cmd, timeout_s=timeout_s, log_context=log_context, runner=runner)
        if proc is None:
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


def call_text(
    *,
    prompt: str,
    model: str,
    timeout_s: int,
    log_context: str,
    system_prompt: str | None = None,
    runner: Runner | None = None,
) -> Optional[str]:
    """Return a free-text (non-schema) completion, with provider failover.

    Used by conversational paths (e.g. the Ask-AI tutor) that want prose, not
    constrained JSON. ``None`` means every configured provider failed.
    """
    runner = runner or subprocess.run
    order = provider_order()
    for i, prov in enumerate(order):
        fn = _call_codex_text if prov == "codex" else _call_claude_text
        result = fn(
            prompt=prompt,
            model=resolve_model(model, active=prov),
            timeout_s=timeout_s,
            log_context=log_context,
            system_prompt=system_prompt,
            runner=runner,
        )
        if result is not None:
            if i > 0:
                log.warning("%s: recovered on fallback provider %r after %r failed",
                            log_context, prov, order[:i])
            return result
    log.warning("%s: all LLM providers failed (%s)", log_context, ", ".join(order))
    return None


def _call_claude_text(
    *,
    prompt: str,
    model: str,
    timeout_s: int,
    log_context: str,
    system_prompt: str | None,
    runner: Runner,
) -> Optional[str]:
    cmd = ["claude", "--output-format", "json", "--model", model, "--tools", ""]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    cmd += ["--no-session-persistence", "-p", prompt]
    try:
        proc = runner(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        log.warning("%s: Claude CLI timed out after %ds", log_context, timeout_s)
        return None
    except (FileNotFoundError, OSError) as exc:
        log.warning("%s: Claude CLI unavailable: %s", log_context, exc)
        return None
    if proc.returncode != 0:
        log.warning("%s: Claude CLI failed (exit %d): %s",
                    log_context, proc.returncode, redact_secrets(proc.stderr)[:500])
        return None
    text = (proc.stdout or "").strip()
    try:
        wrapper = json.loads(text)
        if isinstance(wrapper, dict) and isinstance(wrapper.get("result"), str):
            return wrapper["result"].strip() or None
    except json.JSONDecodeError:
        pass
    return text or None


def _call_codex_text(
    *,
    prompt: str,
    model: str,
    timeout_s: int,
    log_context: str,
    system_prompt: str | None,
    runner: Runner,
) -> Optional[str]:
    output_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".output.txt", delete=False) as of:
            output_path = of.name
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        cmd = [
            "codex", "exec",
            "--model", model,
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--color", "never",
            "--output-last-message", output_path,
            *_codex_effort_args(),
            full_prompt,
        ]
        proc = _run_codex(cmd=cmd, timeout_s=timeout_s, log_context=log_context, runner=runner)
        if proc is None:
            return None
        text = Path(output_path).read_text(encoding="utf-8", errors="replace") if output_path else ""
        if not text.strip():
            text = proc.stdout or ""
        return text.strip() or None
    finally:
        if output_path:
            try:
                os.unlink(output_path)
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
