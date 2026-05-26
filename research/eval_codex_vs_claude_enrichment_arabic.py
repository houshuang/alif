"""A/B: Codex gpt-5.5 vs Claude Haiku on Alif's Arabic enrichment pipeline.

Companion to the deferred Alif Codex hybrid migration plan
(`research/alif-codex-migration-plan-2026-05-26.md`). The migration would flip
audit-pipeline calls from Claude Haiku to Codex `gpt-5.5`; enrichment was the
one pipeline the plan singled out for an A/B before flipping. This is that A/B.

What it does
------------
For 10 hand-picked Arabic lemmas covering verb forms I/II/III/IV/VIII/X (two
hollow), a derived noun, a nisba adjective, and a loanword, it issues each of
the three Alif enrichment batch calls — `roots`, `forms`, `etymology` — once
through Claude Haiku CLI and once through Codex headless, with the same prompt
and the same JSON schema. Raw outputs and timings are saved as JSON so the
grading pass can compare side by side.

Why standalone
--------------
Mirrors `backend/scripts/eval_codex_vs_claude_sentence_gen.py` — no Alif venv
import (avoids arm64/x86_64 mismatch and lets the script run from any working
copy). The Alif enrichment prompts are inlined verbatim so the comparison is
honest about what production sends.

Usage
-----
    python3 research/eval_codex_vs_claude_enrichment_arabic.py \
        --out research/codex-vs-claude-enrichment-arabic-2026-05-26.json
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

# ─── Lemma fixtures ────────────────────────────────────────────────────────
# (lemma_id is for traceability with prod alif.db; everything else is the
# payload the prompts actually see.)
LEMMAS: list[dict] = [
    {
        "lemma_id": 408, "lemma_ar": "قَالَ", "lemma_ar_bare": "قال",
        "pos": "verb", "gloss_en": "to say", "root": "ق.و.ل",
        "root_meaning": "to say, to speak",
        "kind": "verb_I_hollow",
    },
    {
        "lemma_id": 385, "lemma_ar": "كَانَ", "lemma_ar_bare": "كان",
        "pos": "verb", "gloss_en": "to be", "root": "ك.و.ن",
        "root_meaning": "to be, to exist, to become",
        "kind": "verb_I_hollow",
    },
    {
        "lemma_id": 3081, "lemma_ar": "قَدَّمَ", "lemma_ar_bare": "قدم",
        "pos": "verb", "gloss_en": "to submit, to present", "root": "ق.د.م",
        "root_meaning": "to advance, to precede",
        "kind": "verb_II",
    },
    {
        "lemma_id": 2942, "lemma_ar": "جَادَلَ", "lemma_ar_bare": "جادل",
        "pos": "verb", "gloss_en": "to argue", "root": "ج.د.ل",
        "root_meaning": "to twist, to argue, to dispute",
        "kind": "verb_III",
    },
    {
        "lemma_id": 3082, "lemma_ar": "أَخْبَرَ", "lemma_ar_bare": "أخبر",
        "pos": "verb", "gloss_en": "to inform, to tell", "root": "خ.ب.ر",
        "root_meaning": "to know, to be informed",
        "kind": "verb_IV",
    },
    {
        "lemma_id": 3022, "lemma_ar": "اعْتَرَفَ", "lemma_ar_bare": "اعترف",
        "pos": "verb", "gloss_en": "to confess", "root": "ع.ر.ف",
        "root_meaning": "to know, to recognize",
        "kind": "verb_VIII",
    },
    {
        "lemma_id": 3008, "lemma_ar": "اسْتَعْمَلَ", "lemma_ar_bare": "استعمل",
        "pos": "verb", "gloss_en": "to use", "root": "ع.م.ل",
        "root_meaning": "to do, to work, to make",
        "kind": "verb_X",
    },
    {
        "lemma_id": 2674, "lemma_ar": "جِرَاحَة", "lemma_ar_bare": "جراحة",
        "pos": "noun", "gloss_en": "surgery", "root": "ج.ر.ح",
        "root_meaning": "to wound, to injure",
        "kind": "noun_fi'aala",
    },
    {
        "lemma_id": 2531, "lemma_ar": "مَدَنِيّ", "lemma_ar_bare": "مدني",
        "pos": "adjective", "gloss_en": "civil", "root": "م.د.ن",
        "root_meaning": "city, civilization",
        "kind": "adj_nisba",
    },
    {
        "lemma_id": 314, "lemma_ar": "سِينِمَا", "lemma_ar_bare": "سينما",
        "pos": "noun", "gloss_en": "cinema", "root": None,
        "root_meaning": None,
        "kind": "loanword",
    },
]


# ─── Prompts (inlined verbatim from backend/app/services/lemma_enrichment.py) ─

ROOTS_SYSTEM_PROMPT = """You are an Arabic morphology expert. For each Arabic word, extract its consonantal root (جذر).

Rules:
- Return the root in dotted Arabic notation (e.g. ك.ت.ب for كتاب)
- Most roots are 3 consonants (trilateral), some are 4 (quadrilateral)
- For particles, pronouns, and words without a clear root, return null

Return a JSON array: [{"lemma_id": 1, "root": "ك.ت.ب"}]
Use null for root if the word has no meaningful root."""


FORMS_SYSTEM_PROMPT = """\
You are an Arabic morphology expert. Given an Arabic word with its POS and meaning, \
return its key morphological forms as JSON.

For verbs, return:
- "present": the present/imperfect 3rd person masculine singular (e.g. يَكْتُبُ)
- "past_3fs": past tense 3rd person feminine singular (e.g. كَتَبَتْ)
- "past_3p": past tense 3rd person masculine plural (e.g. كَتَبُوا)
- "past_1s": past tense 1st person singular (e.g. كَتَبْتُ). CRITICAL for weak verbs where the stem changes: قُلْتُ (not قَالْتُ), مَشَيْتُ (not مَشَىتُ), نِمْتُ (not نَامْتُ).
- "past_3fp": past tense 3rd person feminine plural (e.g. كَتَبْنَ)
- "present_3fp": present 3rd person feminine plural (e.g. يَكْتُبْنَ). Important for weak verbs: يَقُلْنَ, يَمْشِينَ.
- "present_3mp": present 3rd person masculine plural (e.g. يَكْتُبُونَ). Important for weak/defective verbs where stem changes: يَمْشُونَ (not يَمْشِيُونَ), يَدْعُونَ.
- "masdar": the verbal noun (e.g. كِتَابَة)
- "active_participle": the active participle (e.g. كَاتِب)
- "passive_participle": the passive participle (e.g. مَكْتُوب)
- "imperative": the imperative 2nd person masculine singular (e.g. اُكْتُبْ)
- "verb_form": the verb form number as Roman numeral (I, II, III, IV, V, VI, VII, VIII, IX, X)

For nouns, return:
- "plural": the most common plural form (broken plural) with full diacritics
- "gender": "m" or "f"
- "sound_f_plural": sound feminine plural (ـات form) if applicable (e.g. كِتَابَات, مُعَلِّمَات). Omit if no sound feminine plural exists.
- "sound_m_plural": sound masculine plural (ـون form) if applicable (e.g. مُعَلِّمُون, مُهَنْدِسُون). Omit if no sound masculine plural exists.
- "dual": dual form if applicable (e.g. كِتَابَان)

For adjectives, return:
- "feminine": the feminine form (e.g. كَبِيرَة)
- "plural": the most common plural form
- "sound_f_plural": sound feminine plural if applicable (e.g. كَبِيرَات)
- "sound_m_plural": sound masculine plural if applicable (e.g. كَبِيرُون)
- "elative": the comparative/superlative form if it exists (e.g. أَكْبَر)

Always include full diacritics on Arabic text. Only include fields you are confident about. \
Return empty object {} if the word doesn't have meaningful forms (particles, pronouns, etc.)."""


FORMS_VALID_KEYS = {
    "gender", "plural", "present", "past_3fs", "past_3p",
    "past_1s", "past_3fp", "present_3fp", "present_3mp",
    "masdar", "active_participle", "passive_participle",
    "imperative", "verb_form", "feminine", "elative",
    "sound_f_plural", "sound_m_plural", "dual",
}

_FORMS_OBJECT_SCHEMA = {
    "type": "object",
    "properties": {key: {"type": "string"} for key in sorted(FORMS_VALID_KEYS)},
    "additionalProperties": False,
}

_FORMS_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "words": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lemma_id": {"type": "integer"},
                    "forms": _FORMS_OBJECT_SCHEMA,
                },
                "required": ["lemma_id", "forms"],
            },
        },
    },
    "required": ["words"],
}


ETYMOLOGY_SYSTEM_PROMPT = """You are an Arabic etymology and morphology expert. For each word, generate structured etymology data that helps a language learner understand word origins.

CRITICAL — the etymology MUST match the word's given meaning:
- The `derivation` you produce must explain how THIS word came to mean its given `meaning`. Never give an origin that describes a different, unrelated word.
- If the word has a consonantal `root`, treat it as a native Arabic word and derive it from that root. Do NOT invent a foreign/loanword origin for a word that has an Arabic root, UNLESS the given meaning is itself obviously a borrowed modern concept (e.g. television, radio, computer, pizza).
- Beware surface-string coincidences: a word's letters may resemble an unrelated foreign word. Anchor on the MEANING and ROOT you are given, never on what the letters look or sound like.

There are TWO types of words:

1. NATIVE ARABIC WORDS (have a consonantal root):
- root_meaning: the core semantic field of the consonantal root (2-5 words)
- pattern: the morphological pattern (wazan) in Arabic transliteration (e.g. "maf'al", "fa'ala", "taf'īl", "maf'ūl", "fi'āla", "fu'ūl"). Use standard pattern notation with f-'-l representing the root consonants.
- pattern_meaning: what this pattern generally produces (e.g. "place of doing X", "one who does X", "the act of doing X")
- derivation: a short formula showing how root + pattern = meaning (e.g. "maktab = place of writing = office/desk")
- semantic_field: 2-4 related concepts (e.g. "literacy, education, correspondence")
- related_loanwords: English or other European words borrowed from this Arabic root, if any. Return empty array [] if none.
- cultural_note: brief cultural context if relevant; omit if none.

2. LOANWORDS and FOREIGN-ORIGIN WORDS (pizza, chocolate, cinema, tea, computer, etc.) — ONLY when the given meaning is itself a borrowed concept:
- omit root_meaning, pattern, pattern_meaning
- derivation: "From [source language] '[original word]' ([meaning])" — the source word's meaning must match THIS word's given meaning; trace the borrowing path if it went through intermediate languages
- semantic_field: 2-4 related concepts
- related_loanwords: cognates in other languages borrowed from the same source. Return [] if none.
- cultural_note: when/how the word entered Arabic, or interesting cultural context; omit if nothing notable.

Omit (do not include) any field that does not apply rather than setting it to null. Return an empty object {} for the etymology of closed-class function words (particles, pronouns).

Return JSON array: [{"lemma_id": 1, "etymology": {...}}]"""


_ETYMOLOGY_STRING_FIELDS = (
    "root_meaning", "pattern", "pattern_meaning",
    "derivation", "semantic_field", "cultural_note",
)

_ETYMOLOGY_OBJECT_SCHEMA = {
    "type": "object",
    "properties": {
        **{f: {"type": "string"} for f in _ETYMOLOGY_STRING_FIELDS},
        "related_loanwords": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

_ETYMOLOGY_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "words": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lemma_id": {"type": "integer"},
                    "etymology": _ETYMOLOGY_OBJECT_SCHEMA,
                },
                "required": ["lemma_id", "etymology"],
            },
        },
    },
    "required": ["words"],
}

# Roots: Alif uses json_mode (no schema). We add a permissive schema here so
# both providers can be invoked via the same structured path. Production-side
# roots is the lowest-stakes call (Haiku, json_mode); equivalent behavior.
_ROOTS_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "roots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lemma_id": {"type": "integer"},
                    "root": {"type": ["string", "null"]},
                },
                "required": ["lemma_id", "root"],
            },
        },
    },
    "required": ["roots"],
}


# ─── Prompt builders ───────────────────────────────────────────────────────


def build_roots_prompt(lemmas: list[dict]) -> str:
    lines = []
    for l in lemmas:
        pos_hint = f", pos={l['pos']}" if l.get("pos") else ""
        gloss = f', meaning="{l["gloss_en"]}"' if l.get("gloss_en") else ""
        lines.append(f"- lemma_id={l['lemma_id']}, word={l['lemma_ar']}{pos_hint}{gloss}")
    return (
        "Extract the Arabic consonantal root for each word:\n\n"
        + "\n".join(lines)
        + '\n\nReturn JSON exactly in this shape:\n'
        '{"roots": [{"lemma_id": 1, "root": "ك.ت.ب"}, ...]} '
        '(use null for root if no root).'
    )


def build_forms_prompt(lemmas: list[dict]) -> str:
    lines = []
    for l in lemmas:
        parts = [f"lemma_id={l['lemma_id']}", f"word={l['lemma_ar']}"]
        if l.get("pos"):
            parts.append(f"pos={l['pos']}")
        if l.get("gloss_en"):
            parts.append(f'meaning="{l["gloss_en"]}"')
        lines.append("- " + ", ".join(parts))
    return (
        "Return the morphological forms for each Arabic word below.\n\n"
        + "\n".join(lines)
        + "\n\nReturn JSON exactly in this shape:\n"
        '{"words": [{"lemma_id": 1, "forms": {"plural": "..."}}, ...]}\n'
        "Use an empty forms object when a word has no meaningful forms."
    )


def build_etymology_prompt(lemmas: list[dict]) -> str:
    lines = []
    for l in lemmas:
        pos_hint = f", pos={l['pos']}" if l.get("pos") else ""
        gloss = f', meaning="{l["gloss_en"]}"' if l.get("gloss_en") else ""
        root_info = f", root={l['root']}" if l.get("root") else ""
        rm = f', root_meaning="{l["root_meaning"]}"' if l.get("root_meaning") else ""
        lines.append(
            f"- lemma_id={l['lemma_id']}, word={l['lemma_ar_bare']}"
            f"{pos_hint}{gloss}{root_info}{rm}"
        )
    return (
        "Generate etymology data for each Arabic word:\n\n"
        + "\n".join(lines)
        + "\n\nReturn JSON exactly in this shape:\n"
        '{"words": [{"lemma_id": 1, "etymology": {"root_meaning": "...", "pattern": "...", '
        '"pattern_meaning": "...", "derivation": "...", "semantic_field": "...", '
        '"related_loanwords": [], "cultural_note": "..."}}]}\n'
        "Omit any field that does not apply. Use an empty etymology object {} for function words."
    )


# ─── Provider invocations ──────────────────────────────────────────────────
# Two providers, one schema each.

def _strict_codex_schema(schema: dict) -> dict:
    """Convert an Alif-style JSON Schema into Codex's strict structured-output
    shape (additionalProperties:false, all properties required, optional fields
    nullable). Mirrors `polyglot/app/services/llm_cli.py::strict_response_schema`."""

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


def call_claude_haiku(
    *, system_prompt: str, prompt: str, schema: dict, timeout_s: int,
) -> tuple[Optional[dict], float, str]:
    """Returns (parsed_json, elapsed_s, raw_text). parsed_json is None on failure."""
    cmd = [
        "claude",
        "--output-format", "json",
        "--model", "claude-haiku-4-5-20251001",
        "--system-prompt", system_prompt,
        "--json-schema", json.dumps(schema, ensure_ascii=False),
        "-p", prompt,
    ]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return None, time.time() - t0, "<timeout>"
    elapsed = time.time() - t0
    if proc.returncode != 0:
        return None, elapsed, f"<exit {proc.returncode}>\n{proc.stderr[:400]}"
    raw = proc.stdout or ""
    try:
        wrapper = json.loads(raw)
    except json.JSONDecodeError:
        return None, elapsed, raw[:1500]
    if not isinstance(wrapper, dict):
        return None, elapsed, raw[:1500]
    structured = wrapper.get("structured_output")
    if isinstance(structured, dict):
        return structured, elapsed, raw[:4000]
    result_str = wrapper.get("result", "") or ""
    # Fall back to embedded JSON in the result string
    try:
        return json.loads(result_str), elapsed, raw[:4000]
    except json.JSONDecodeError:
        s, e = result_str.find("{"), result_str.rfind("}")
        if s != -1 and e > s:
            try:
                return json.loads(result_str[s:e + 1]), elapsed, raw[:4000]
            except json.JSONDecodeError:
                pass
    return None, elapsed, raw[:4000]


def call_codex(
    *, system_prompt: str, prompt: str, schema: dict, timeout_s: int,
) -> tuple[Optional[dict], float, str]:
    """Returns (parsed_json, elapsed_s, raw_text). parsed_json is None on failure."""
    schema_path: Optional[str] = None
    output_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".schema.json", delete=False) as sf:
            json.dump(_strict_codex_schema(schema), sf, ensure_ascii=False)
            schema_path = sf.name
        with tempfile.NamedTemporaryFile("w", suffix=".output.json", delete=False) as of:
            output_path = of.name

        full_prompt = f"{system_prompt}\n\n{prompt}"
        cmd = [
            "codex", "exec",
            "--model", "gpt-5.5",
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--ignore-user-config",
            "--color", "never",
            "--output-schema", schema_path,
            "--output-last-message", output_path,
            "-c", 'model_reasoning_effort="medium"',
            full_prompt,
        ]
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return None, time.time() - t0, "<timeout>"
        elapsed = time.time() - t0
        if proc.returncode != 0:
            return None, elapsed, f"<exit {proc.returncode}>\n{proc.stderr[:400]}"
        text = Path(output_path).read_text(encoding="utf-8", errors="replace") if output_path else ""
        if not text.strip():
            text = proc.stdout or ""
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed, elapsed, text[:4000]
        except json.JSONDecodeError:
            s, e = text.find("{"), text.rfind("}")
            if s != -1 and e > s:
                try:
                    parsed = json.loads(text[s:e + 1])
                    if isinstance(parsed, dict):
                        return parsed, elapsed, text[:4000]
                except json.JSONDecodeError:
                    pass
        return None, elapsed, text[:4000]
    finally:
        for p in (schema_path, output_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass


# ─── Orchestration ─────────────────────────────────────────────────────────


CALLS = [
    {
        "name": "roots",
        "system": ROOTS_SYSTEM_PROMPT,
        "build_prompt": build_roots_prompt,
        "schema": _ROOTS_BATCH_SCHEMA,
        "timeout_s": 120,
    },
    {
        "name": "forms",
        "system": FORMS_SYSTEM_PROMPT,
        "build_prompt": build_forms_prompt,
        "schema": _FORMS_BATCH_SCHEMA,
        "timeout_s": 180,
    },
    {
        "name": "etymology",
        "system": ETYMOLOGY_SYSTEM_PROMPT,
        "build_prompt": build_etymology_prompt,
        "schema": _ETYMOLOGY_BATCH_SCHEMA,
        "timeout_s": 240,
    },
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="output JSON path")
    ap.add_argument("--only", choices=["roots", "forms", "etymology"], help="run one call only")
    args = ap.parse_args()

    out: dict = {
        "lemmas": LEMMAS,
        "providers": {
            "claude_haiku": {"model": "claude-haiku-4-5-20251001", "cli": "claude"},
            "codex": {"model": "gpt-5.5", "cli": "codex", "reasoning_effort": "medium"},
        },
        "calls": {},
    }

    for call in CALLS:
        if args.only and call["name"] != args.only:
            continue
        name = call["name"]
        prompt = call["build_prompt"](LEMMAS)
        print(f"\n=== {name}  ({len(LEMMAS)} lemmas, prompt len={len(prompt)}) ===")

        # Claude Haiku
        print(f"  → claude_haiku ...", flush=True)
        claude_parsed, claude_t, claude_raw = call_claude_haiku(
            system_prompt=call["system"], prompt=prompt,
            schema=call["schema"], timeout_s=call["timeout_s"],
        )
        print(f"    done in {claude_t:.1f}s  parsed={'OK' if claude_parsed else 'FAIL'}")

        # Codex
        print(f"  → codex gpt-5.5 ...", flush=True)
        codex_parsed, codex_t, codex_raw = call_codex(
            system_prompt=call["system"], prompt=prompt,
            schema=call["schema"], timeout_s=call["timeout_s"],
        )
        print(f"    done in {codex_t:.1f}s  parsed={'OK' if codex_parsed else 'FAIL'}")

        out["calls"][name] = {
            "prompt_chars": len(prompt),
            "claude_haiku": {
                "elapsed_s": round(claude_t, 1),
                "parsed": claude_parsed,
                "raw_preview": claude_raw,
            },
            "codex": {
                "elapsed_s": round(codex_t, 1),
                "parsed": codex_parsed,
                "raw_preview": codex_raw,
            },
        }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
