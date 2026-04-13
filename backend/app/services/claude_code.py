"""Thin shim over limbic.cerebellum.claude_cli — preserves the legacy Alif API.

All real work (subprocess, JSON parsing, env stripping, cost_log writes) lives
in `limbic.cerebellum.claude_cli`. This module keeps `generate_structured`,
`generate_with_tools`, and the `dump_vocabulary_for_claude` utility stable so
existing callers (audit_sentences_claude.py, generate_sentences_claude.py,
benchmark_claude_code.py) don't need edits, and pins `project="alif"` for
cost_log attribution.

The `CLAUDE_MAX_BUDGET=0.50` safety cap for tool-enabled sessions is preserved.
"""

from __future__ import annotations

import json
import logging
import os
import shutil

from limbic.cerebellum.claude_cli import (
    ClaudeCLIError,
    generate as _limbic_generate,
)

logger = logging.getLogger(__name__)

PROJECT = "alif"

CLAUDE_TIMEOUT = 120
CLAUDE_TOOLS_TIMEOUT = 240
CLAUDE_MAX_BUDGET = 0.50  # dollars, safety cap for tool-enabled sessions


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
    """Call claude -p for structured JSON generation (no tools).

    Returns parsed dict from structured_output. Raises RuntimeError (not
    ClaudeCLIError) to preserve the exception type existing callers catch.
    """
    if not is_available():
        raise FileNotFoundError(
            "claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
        )

    try:
        result, meta = _limbic_generate(
            prompt=prompt,
            project=PROJECT,
            purpose="generate_structured",
            system=system_prompt,
            schema=json_schema,
            model=model,
            timeout=timeout,
        )
    except ClaudeCLIError as e:
        raise RuntimeError(str(e)) from e

    logger.info(
        "claude_code generation: model=%s duration=%.1fs cost=$%.3f turns=%d",
        model,
        meta.get("duration_s", 0.0),
        meta.get("cost", 0.0),
        meta.get("turns", 0),
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

    Preserves the `CLAUDE_MAX_BUDGET=0.50` safety cap from the original
    module as the default — callers that pass an explicit `max_budget_usd`
    override it.
    """
    if not is_available():
        raise FileNotFoundError(
            "claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
        )

    try:
        result, meta = _limbic_generate(
            prompt=prompt,
            project=PROJECT,
            purpose="generate_with_tools",
            system=system_prompt,
            schema=json_schema,
            model=model,
            tools=tools,
            max_budget=max_budget_usd,
            work_dir=work_dir,
            dangerously_skip_permissions=True,
            timeout=timeout,
        )
    except ClaudeCLIError as e:
        raise RuntimeError(str(e)) from e

    logger.info(
        "claude_code tool session: model=%s duration=%.1fs cost=$%.3f turns=%d",
        model,
        meta.get("duration_s", 0.0),
        meta.get("cost", 0.0),
        meta.get("turns", 0),
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

    Returns:
        (prompt_file_path, lookup_file_path)
    """
    import sqlite3

    os.makedirs(output_dir, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT l.lemma_id, l.lemma_ar, l.lemma_ar_bare, l.gloss_en, l.pos,
               l.forms_json, ulk.knowledge_state
        FROM lemmas l
        JOIN user_lemma_knowledge ulk ON ulk.lemma_id = l.lemma_id
        WHERE ulk.knowledge_state IN ('learning', 'known', 'acquiring', 'lapsed')
          AND l.canonical_lemma_id IS NULL
    """).fetchall()
    conn.close()

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

    from app.services.sentence_validator import normalize_alef, strip_diacritics

    lookup: dict[str, int] = {}
    for row in rows:
        bare_norm = normalize_alef(row["lemma_ar_bare"])
        lookup[bare_norm] = row["lemma_id"]
        if bare_norm.startswith("ال") and len(bare_norm) > 2:
            lookup[bare_norm[2:]] = row["lemma_id"]
        elif not bare_norm.startswith("ال"):
            lookup["ال" + bare_norm] = row["lemma_id"]

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
