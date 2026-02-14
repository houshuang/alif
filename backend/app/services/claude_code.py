"""Claude Code CLI wrapper for structured LLM generation via Max plan.

Uses `claude -p` (print mode) with --tools "" (no tool access) and --json-schema
for reliable, predictable structured output. Requires `claude` CLI installed and
authenticated (e.g. via `claude setup-token`).

This is an OPTIONAL alternative to litellm API calls. When claude CLI is available
(e.g. developer machine with Max plan), it provides free Opus access. When not
available, callers should fall back to litellm via generate_completion().

Usage:
    from app.services.claude_code import generate_structured, is_available

    if is_available():
        result = generate_structured(
            prompt="Write a story about a cat",
            system_prompt="You are a storyteller",
            json_schema={"type": "object", "properties": {"story": {"type": "string"}}, "required": ["story"]},
        )
    else:
        # Fall back to litellm
        result = generate_completion(prompt=..., model_override="opus")
"""

import json
import logging
import re
import shutil
import subprocess
import time

logger = logging.getLogger(__name__)

CLAUDE_TIMEOUT = 120  # seconds


def is_available() -> bool:
    """Check if claude CLI is installed and accessible."""
    return shutil.which("claude") is not None


def generate_structured(
    prompt: str,
    system_prompt: str,
    json_schema: dict,
    model: str = "opus",
    timeout: int = CLAUDE_TIMEOUT,
) -> dict:
    """Call claude -p for structured JSON generation.

    Args:
        prompt: User prompt text.
        system_prompt: System prompt text.
        json_schema: JSON Schema dict for structured output validation.
        model: Model alias (opus, sonnet, haiku). Default: opus.
        timeout: Max seconds to wait. Default: 120.

    Returns:
        Parsed dict from structured_output.

    Raises:
        RuntimeError: If claude fails or returns invalid output.
        FileNotFoundError: If claude CLI is not installed.
    """
    if not is_available():
        raise FileNotFoundError("claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code")

    cmd = [
        "claude", "-p",
        "--tools", "",
        "--output-format", "json",
        "--model", model,
        "--no-session-persistence",
        "--json-schema", json.dumps(json_schema),
        "--system-prompt", system_prompt,
    ]

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude timed out after {timeout}s")

    elapsed = time.time() - start

    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[:500]}")

    response = json.loads(proc.stdout)

    if response.get("is_error"):
        raise RuntimeError(f"claude error: {response.get('result', 'unknown')}")

    # Prefer structured_output (clean parsed JSON from --json-schema)
    result = response.get("structured_output")
    if not result:
        # Fall back to parsing result text (may have markdown fences)
        text = response.get("result", "")
        text = re.sub(r"^```(?:json)?\s*", "", text.strip())
        text = re.sub(r"\s*```$", "", text)
        if text:
            result = json.loads(text)

    if not result:
        raise RuntimeError("Empty response from claude")

    logger.info(
        "claude_code generation: model=%s duration=%.1fs cost=$%.3f turns=%d",
        model,
        elapsed,
        response.get("total_cost_usd", 0),
        response.get("num_turns", 0),
    )

    return result
