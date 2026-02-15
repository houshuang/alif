"""Claude Code CLI wrapper for structured LLM generation via Max plan.

Uses `claude -p` (print mode) for reliable structured output. Two modes:

1. generate_structured() — no tools, --json-schema for single-turn generation
2. generate_with_tools() — Read/Bash tools enabled for multi-turn agentic sessions
   where Claude can read files, run validation scripts, and self-correct

Requires `claude` CLI installed and authenticated (e.g. via `claude setup-token`).
Optional: callers should fall back to litellm when is_available() returns False.

Usage:
    from app.services.claude_code import generate_structured, generate_with_tools, is_available

    if is_available():
        # Simple structured generation (no tools)
        result = generate_structured(prompt=..., system_prompt=..., json_schema=...)

        # Tool-enabled generation (reads files, runs scripts, self-validates)
        result = generate_with_tools(prompt=..., system_prompt=..., json_schema=..., work_dir="/tmp/claude/alif")
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time

logger = logging.getLogger(__name__)

CLAUDE_TIMEOUT = 120  # seconds
CLAUDE_TOOLS_TIMEOUT = 240  # higher for multi-turn sessions (10-word batches)
CLAUDE_MAX_BUDGET = 0.50  # dollars, safety cap for tool-enabled sessions


def is_available() -> bool:
    """Check if claude CLI is installed and accessible."""
    return shutil.which("claude") is not None


def _parse_response(proc: subprocess.CompletedProcess) -> tuple[dict, dict]:
    """Parse claude CLI JSON response, returning (result, metadata).

    Returns:
        (result_dict, metadata_dict) where metadata has cost, turns, duration info.

    Raises:
        RuntimeError on error or empty response.
    """
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
            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                raise RuntimeError(f"Failed to parse claude response as JSON: {text[:200]}")

    if not result:
        raw_result = response.get("result", "")
        raw_so = response.get("structured_output")
        raise RuntimeError(
            f"Empty response from claude. "
            f"structured_output={raw_so!r}, "
            f"result={raw_result[:200]!r}, "
            f"stop_reason={response.get('stop_reason')}, "
            f"num_turns={response.get('num_turns')}"
        )

    metadata = {
        "total_cost_usd": response.get("total_cost_usd", 0),
        "num_turns": response.get("num_turns", 0),
        "session_id": response.get("session_id"),
    }

    return result, metadata


def generate_structured(
    prompt: str,
    system_prompt: str,
    json_schema: dict,
    model: str = "opus",
    timeout: int = CLAUDE_TIMEOUT,
) -> dict:
    """Call claude -p for structured JSON generation (no tools).

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

    result, metadata = _parse_response(proc)

    logger.info(
        "claude_code generation: model=%s duration=%.1fs cost=$%.3f turns=%d",
        model,
        elapsed,
        metadata["total_cost_usd"],
        metadata["num_turns"],
    )

    return result


def generate_with_tools(
    prompt: str,
    system_prompt: str,
    json_schema: dict,
    work_dir: str,
    model: str = "opus",
    tools: str = "Read,Bash",
    max_budget_usd: float = CLAUDE_MAX_BUDGET,
    timeout: int = CLAUDE_TOOLS_TIMEOUT,
) -> dict:
    """Call claude -p with tools enabled for multi-turn agentic sessions.

    Claude can read files and run scripts within the session, enabling
    self-validation loops (generate → validate → fix) in a single call.

    Args:
        prompt: User prompt text.
        system_prompt: System prompt text.
        json_schema: JSON Schema dict for final structured output.
        work_dir: Directory to grant tool access to (contains vocab files,
                  validator scripts, etc.). Must exist.
        model: Model alias (opus, sonnet, haiku). Default: opus.
        tools: Comma-separated tool names. Default: "Read,Bash".
        max_budget_usd: Safety spending cap. Default: 0.50.
        timeout: Max seconds to wait. Default: 180.

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
        "--tools", tools,
        "--dangerously-skip-permissions",
        "--output-format", "json",
        "--model", model,
        "--no-session-persistence",
        "--json-schema", json.dumps(json_schema),
        "--system-prompt", system_prompt,
        "--add-dir", work_dir,
        "--max-budget-usd", str(max_budget_usd),
    ]

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=work_dir,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude with tools timed out after {timeout}s")

    elapsed = time.time() - start

    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[:500]}")

    result, metadata = _parse_response(proc)

    logger.info(
        "claude_code tool session: model=%s duration=%.1fs cost=$%.3f turns=%d",
        model,
        elapsed,
        metadata["total_cost_usd"],
        metadata["num_turns"],
    )

    return result


def dump_vocabulary_for_claude(
    db_path: str,
    output_dir: str,
) -> tuple[str, str]:
    """Dump learner vocabulary to files for Claude Code sessions.

    Creates two files:
    1. vocab_prompt.txt — Human-readable POS-grouped vocabulary for the prompt
    2. vocab_lookup.tsv — bare_form<TAB>lemma_id for the validator CLI

    Args:
        db_path: Path to SQLite database.
        output_dir: Directory to write files to. Created if needed.

    Returns:
        (prompt_file_path, lookup_file_path)
    """
    import sqlite3

    os.makedirs(output_dir, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Fetch all known/learning/acquiring lemmas (non-variant)
    rows = conn.execute("""
        SELECT l.lemma_id, l.lemma_ar, l.lemma_ar_bare, l.gloss_en, l.pos,
               l.forms_json, ulk.knowledge_state
        FROM lemmas l
        JOIN user_lemma_knowledge ulk ON ulk.lemma_id = l.lemma_id
        WHERE ulk.knowledge_state IN ('learning', 'known', 'acquiring', 'lapsed')
          AND l.canonical_lemma_id IS NULL
    """).fetchall()
    conn.close()

    # Separate acquiring words (for diversity prompt)
    acquiring_entries: list[str] = []
    pos_groups: dict[str, list[str]] = {"NOUNS": [], "VERBS": [], "ADJECTIVES": [], "OTHER": []}
    for row in rows:
        pos = (row["pos"] or "").lower()
        entry = f"{row['lemma_ar']} ({row['gloss_en']})"
        if row["knowledge_state"] == "acquiring":
            acquiring_entries.append(entry)
        if pos in ("noun", "noun_prop"):
            pos_groups["NOUNS"].append(entry)
        elif pos == "verb":
            pos_groups["VERBS"].append(entry)
        elif pos in ("adj", "adj_comp"):
            pos_groups["ADJECTIVES"].append(entry)
        else:
            pos_groups["OTHER"].append(entry)

    prompt_path = os.path.join(output_dir, "vocab_prompt.txt")
    with open(prompt_path, "w") as f:
        if acquiring_entries:
            f.write(f"CURRENTLY LEARNING (use these as supporting words when possible): "
                    f"{', '.join(acquiring_entries)}\n\n")
        for label, words in pos_groups.items():
            if words:
                f.write(f"{label}: {', '.join(words)}\n")

    # Build lookup file using the same logic as build_lemma_lookup()
    from app.services.sentence_validator import normalize_alef, strip_diacritics

    lookup: dict[str, int] = {}
    for row in rows:
        bare_norm = normalize_alef(row["lemma_ar_bare"])
        lookup[bare_norm] = row["lemma_id"]
        if bare_norm.startswith("ال") and len(bare_norm) > 2:
            lookup[bare_norm[2:]] = row["lemma_id"]
        elif not bare_norm.startswith("ال"):
            lookup["ال" + bare_norm] = row["lemma_id"]

        # Expand forms_json
        forms_raw = row["forms_json"]
        if forms_raw:
            try:
                forms = json.loads(forms_raw) if isinstance(forms_raw, str) else forms_raw
            except (json.JSONDecodeError, TypeError):
                forms = {}
            if isinstance(forms, dict):
                for key, form_val in forms.items():
                    if key in ("plural", "present", "masdar", "active_participle",
                               "feminine", "elative") or key.startswith("variant_"):
                        if form_val and isinstance(form_val, str):
                            form_bare = normalize_alef(strip_diacritics(form_val))
                            if form_bare not in lookup:
                                lookup[form_bare] = row["lemma_id"]
                            al_form = "ال" + form_bare
                            if not form_bare.startswith("ال") and al_form not in lookup:
                                lookup[al_form] = row["lemma_id"]

    # Add FUNCTION_WORD_FORMS
    from app.services.sentence_validator import FUNCTION_WORD_FORMS

    bare_to_id = {normalize_alef(row["lemma_ar_bare"]): row["lemma_id"] for row in rows}
    for form, base in FUNCTION_WORD_FORMS.items():
        form_norm = normalize_alef(form)
        if form_norm not in lookup:
            base_norm = normalize_alef(base)
            base_id = bare_to_id.get(base_norm)
            if base_id is not None:
                lookup[form_norm] = base_id

    lookup_path = os.path.join(output_dir, "vocab_lookup.tsv")
    with open(lookup_path, "w") as f:
        f.write("# bare_form\tlemma_id\n")
        for bare, lid in sorted(lookup.items(), key=lambda x: x[1]):
            f.write(f"{bare}\t{lid}\n")

    logger.info(
        "Dumped vocabulary: %d lemmas, %d lookup entries to %s",
        len(rows), len(lookup), output_dir,
    )

    return prompt_path, lookup_path
