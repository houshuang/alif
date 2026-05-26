"""A/B sentence-generation: Claude Sonnet (Alif's current default) vs Codex
`gpt-5.5` on the same Arabic prompts.

Earlier testing found Codex under-performing on Arabic. With `gpt-5.5` we want
to revisit. This script generates `count` candidate sentences per target word
with BOTH providers using the SAME prompt/system_prompt as Alif's production
`generate_sentences_batch` (`backend/app/services/llm.py:649`), and dumps a
side-by-side markdown report for human evaluation.

Self-contained — calls both CLIs as subprocesses, no Alif venv import. The
prompt template and the Arabic style/difficulty rule blocks are inlined verbatim
from `llm.py` (kept in sync manually; if Alif's prompts change, this script
needs to be re-synced).

USAGE
    polyglot/.venv/bin/python backend/scripts/eval_codex_vs_claude_sentence_gen.py
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

LOG = logging.getLogger("eval_sentence_gen")
THIS = Path(__file__).resolve()
BACKEND_ROOT = THIS.parent.parent
REPO_ROOT = BACKEND_ROOT.parent


# ─── Inlined Alif prompt blocks (kept in sync with backend/app/services/llm.py) ─

ARABIC_STYLE_RULES = """\
Arabic naturalness rules:
- Mix VSO and SVO word order. VSO is more formal/classical; SVO more contemporary
- VSO agreement: verb matches person + gender only, NOT number: ذَهَبَ الطُّلَّابُ
- SVO agreement: verb matches person + gender + number: الطُّلَّابُ ذَهَبُوا
- Mix nominal sentences (descriptions/states) with verbal sentences (actions/events)
- NO copula: never insert هُوَ/هِيَ as "is" with indefinite predicates. \
Write مُحَمَّدٌ طَبِيبٌ NOT مُحَمَّدٌ هُوَ طَبِيبٌ
- Separator pronoun (ضمير الفصل) ONLY when both subject AND predicate are definite: \
مُحَمَّدٌ هُوَ المُدِيرُ
- Idafa: first noun has NO ال and NO tanween: كِتَابُ الطَّالِبِ
- Correct i'rab: nominative ضمة, accusative فتحة, genitive كسرة. Tanween on indefinites.
- Use connectors naturally: و (and), فَ (so/immediately), ثُمَّ (then/after delay), لَكِنَّ (but)
- Vary sentence length — mix short and long clauses
- Do NOT translate English syntax literally into Arabic

Sentence completeness — CRITICAL:
- NEVER start a nominal sentence with an indefinite noun (نكرة). Use definite or verb-first:
  ✓ الوَلَدُ يَلْعَبُ (The boy plays) — definite subject
  ✓ يَلْعَبُ وَلَدٌ فِي الحَدِيقَةِ (A boy plays in the park) — verb-first OK with indefinite
  ✓ هُنَاكَ وَلَدٌ فِي الحَدِيقَةِ (There is a boy in the park) — existential OK
  ✗ وَلَدٌ يَلْعَبُ (A boy plays) — bare indefinite start, sounds like a caption
- Every sentence must express a complete thought — an action, state, or relation. \
Avoid catalog-style adjective stacking or noun lists.

Redundant pronouns — CRITICAL:
- In VSO order, the verb conjugation already encodes the subject. Do NOT add a pronoun:
  ✓ ذَهَبَتْ إِلَى المَدْرَسَةِ (She went to school)
  ✗ ذَهَبَتْ هِيَ إِلَى المَدْرَسَةِ (She went she to school — redundant)
- Use explicit pronouns only for emphasis/contrast: ذَهَبَ هُوَ وَلَمْ تَذْهَبْ هِيَ

Semantic coherence in compound sentences:
- When joining clauses with و/ثُمَّ/لَكِنَّ, they MUST be logically related
- Do NOT combine unrelated facts into one sentence

Semantic plausibility — CRITICAL:
- Every sentence must be something a real Arabic speaker could plausibly say or write \
in some real context (news, conversation, story, instruction, poetry).
- Do NOT invent proper names by combining content words.
- Proper names: use ONLY proper names that appear in the provided vocabulary list. \
If no suitable name is available, use a generic definite noun \
(الوَلَد، المُعَلِّم، الفَتَاة، الرَّجُل، الطِّفْل، الجَارَة)."""

DIFFICULTY_STYLE_GUIDE = """\
Style by difficulty level:
- very simple / simple: prefer SVO. Short nominal sentences. Basic connectors (و). \
Simple tenses. No embedded clauses. Only modern everyday vocabulary.
- beginner: mix SVO and VSO. Simple idafa. Introduce فَ and ثُمَّ. One clause per sentence. \
Do NOT use classical/archaic particles. Keep language modern and conversational.
- intermediate: more VSO. Relative clauses (الَّذِي/الَّتِي). \
Negation with لَمْ/لَنْ. Idafa chains. Dialogue with قَالَ.
- advanced: VSO default. Embedded clauses. Classical particles (إِنَّ، لَعَلَّ، كَأَنَّ). \
Formal register approaching classical style."""

BATCH_SENTENCE_SYSTEM_PROMPT = f"""\
You are an Arabic language tutor creating MSA (fusha) sentences for reading practice. \
You generate multiple varied sentences for a target word. Each sentence must sound natural, \
not like a textbook exercise.

{ARABIC_STYLE_RULES}

{DIFFICULTY_STYLE_GUIDE}

Vocabulary constraint:
- Use ONLY words from the provided vocabulary, the target word, and common function words
- Common function words you may freely use: في، من، على، إلى، و، ب، ل، ك، هذا، هذه، \
ذلك، تلك، هو، هي، أنا، أنت، نحن، هم، ما، لا، أن، إن، كان، كانت، ليس، هل، لم، \
لن، قد، الذي، التي، كل، بعض، هنا، هناك، الآن، جدا، فقط، أيضا، أو، ثم، لكن
- Do NOT invent or use Arabic content words not in the vocabulary list
- Include full diacritics (tashkeel) on ALL Arabic words with correct i'rab
- Include Arabic punctuation: use ؟ for questions, . for statements، ، between clauses
- Match the word count range specified in the user prompt
- Each sentence should use a DIFFERENT syntactic structure (vary VSO/SVO, nominal/verbal, question/statement)
- Transliteration: ALA-LC standard with macrons for long vowels

Sentence structure variety:
- Do NOT default to هَلْ questions — only use هَلْ when the target word specifically requires a question
- Vary starters across sentences: verbal (verb-first), nominal (definite noun/adjective), prepositional, time expressions
- Prefer pronouns (أنا، هو، هي، نحن) and generic definite nouns (المعلم، الولد، الفتاة) as subjects
- Use proper names sparingly — avoid محمد/أحمد/فاطمة/علي unless they are in the vocabulary list
- Never start more than one sentence in a batch with the same word.

Respond with JSON: {{"sentences": [{{"arabic": "...", "english": "...", "transliteration": "..."}}, ...]}}"""


def format_known_words_by_pos(known_words: list[dict]) -> str:
    """Inlined from llm.py:403 — group vocab by POS for clearer prompts."""
    groups: dict[str, list[str]] = {"NOUNS": [], "VERBS": [], "ADJECTIVES": [], "OTHER": []}
    for w in known_words:
        pos = (w.get("pos") or "").lower()
        entry = f"{w['arabic']} ({w['translation']})"
        if pos in ("noun", "noun_prop"):
            groups["NOUNS"].append(entry)
        elif pos == "verb":
            groups["VERBS"].append(entry)
        elif pos in ("adj", "adj_comp", "adjective"):
            groups["ADJECTIVES"].append(entry)
        else:
            groups["OTHER"].append(entry)
    lines = []
    for label, words in groups.items():
        if words:
            lines.append(f"{label}: {', '.join(words)}")
    return "\n".join(lines) if lines else "\n".join(
        f"- {w['arabic']} ({w['translation']})" for w in known_words
    )


# ─── Test corpus ─────────────────────────────────────────────────────────────

SHARED_VOCAB: list[dict[str, str]] = [
    {"arabic": "كَتَبَ", "translation": "to write", "pos": "verb"},
    {"arabic": "قَرَأَ", "translation": "to read", "pos": "verb"},
    {"arabic": "دَرَسَ", "translation": "to study", "pos": "verb"},
    {"arabic": "ذَهَبَ", "translation": "to go", "pos": "verb"},
    {"arabic": "رَجَعَ", "translation": "to return", "pos": "verb"},
    {"arabic": "أَكَلَ", "translation": "to eat", "pos": "verb"},
    {"arabic": "شَرِبَ", "translation": "to drink", "pos": "verb"},
    {"arabic": "نَامَ", "translation": "to sleep", "pos": "verb"},
    {"arabic": "عَمِلَ", "translation": "to work", "pos": "verb"},
    {"arabic": "وَجَدَ", "translation": "to find", "pos": "verb"},
    {"arabic": "قَالَ", "translation": "to say", "pos": "verb"},
    {"arabic": "رَأَى", "translation": "to see", "pos": "verb"},
    {"arabic": "سَمِعَ", "translation": "to hear", "pos": "verb"},
    {"arabic": "أَرَادَ", "translation": "to want", "pos": "verb"},
    {"arabic": "بَدَأَ", "translation": "to begin", "pos": "verb"},
    {"arabic": "وَلَدٌ", "translation": "boy", "pos": "noun"},
    {"arabic": "بِنْتٌ", "translation": "girl", "pos": "noun"},
    {"arabic": "رَجُلٌ", "translation": "man", "pos": "noun"},
    {"arabic": "اِمْرَأَةٌ", "translation": "woman", "pos": "noun"},
    {"arabic": "مُعَلِّمٌ", "translation": "teacher (m)", "pos": "noun"},
    {"arabic": "طَالِبٌ", "translation": "student (m)", "pos": "noun"},
    {"arabic": "مَدْرَسَةٌ", "translation": "school", "pos": "noun"},
    {"arabic": "بَيْتٌ", "translation": "house", "pos": "noun"},
    {"arabic": "مَدِينَةٌ", "translation": "city", "pos": "noun"},
    {"arabic": "شَارِعٌ", "translation": "street", "pos": "noun"},
    {"arabic": "سَيَّارَةٌ", "translation": "car", "pos": "noun"},
    {"arabic": "بَابٌ", "translation": "door", "pos": "noun"},
    {"arabic": "نَافِذَةٌ", "translation": "window", "pos": "noun"},
    {"arabic": "صَدِيقٌ", "translation": "friend (m)", "pos": "noun"},
    {"arabic": "كَلْبٌ", "translation": "dog", "pos": "noun"},
    {"arabic": "قِطَّةٌ", "translation": "cat", "pos": "noun"},
    {"arabic": "شَجَرَةٌ", "translation": "tree", "pos": "noun"},
    {"arabic": "مَاءٌ", "translation": "water", "pos": "noun"},
    {"arabic": "طَعَامٌ", "translation": "food", "pos": "noun"},
    {"arabic": "يَوْمٌ", "translation": "day", "pos": "noun"},
    {"arabic": "صَبَاحٌ", "translation": "morning", "pos": "noun"},
    {"arabic": "لَيْلٌ", "translation": "night", "pos": "noun"},
    {"arabic": "كَبِيرٌ", "translation": "big", "pos": "adjective"},
    {"arabic": "صَغِيرٌ", "translation": "small", "pos": "adjective"},
    {"arabic": "جَدِيدٌ", "translation": "new", "pos": "adjective"},
    {"arabic": "قَدِيمٌ", "translation": "old", "pos": "adjective"},
    {"arabic": "جَمِيلٌ", "translation": "beautiful", "pos": "adjective"},
    {"arabic": "سَعِيدٌ", "translation": "happy", "pos": "adjective"},
    {"arabic": "صَعْبٌ", "translation": "difficult", "pos": "adjective"},
    {"arabic": "سَهْلٌ", "translation": "easy", "pos": "adjective"},
    {"arabic": "كَثِيرٌ", "translation": "many / much", "pos": "adjective"},
    {"arabic": "قَلِيلٌ", "translation": "few / little", "pos": "adjective"},
]


@dataclass(frozen=True)
class Target:
    arabic: str
    translation: str
    difficulty: str
    note: str = ""


TARGETS: list[Target] = [
    Target("كِتَابٌ", "book", "beginner",
           "High-freq concrete noun; easy baseline."),
    Target("فَهِمَ", "to understand", "beginner",
           "Common verb, polysemous."),
    Target("مَكْتَبَةٌ", "library", "intermediate",
           "Mim-prefix derived noun (place-of pattern)."),
    Target("حَضَارَةٌ", "civilization", "intermediate",
           "Abstract noun, register-sensitive."),
    Target("اِسْتَطَاعَ", "to be able to", "intermediate",
           "Form X verb, complex morphology + governs subjunctive."),
    Target("عَلَى الرَّغْمِ مِنْ", "despite", "advanced",
           "Compound preposition; multi-word target."),
    Target("نَاسِبٌ", "suitable / appropriate", "advanced",
           "Active participle as adjective."),
    Target("مُحَاوَلَةٌ", "attempt", "intermediate",
           "Form III verbal noun (maṣdar)."),
]


# ─── Shared schema for both providers' JSON output ───────────────────────────

SENTENCES_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "arabic": {"type": "string"},
                    "english": {"type": "string"},
                    "transliteration": {"type": "string"},
                },
                "required": ["arabic", "english", "transliteration"],
            },
        },
    },
    "required": ["sentences"],
}


def build_user_prompt(target: Target, count: int, known_list: str) -> str:
    """Mirror llm.py:generate_sentences_batch body — same fields, same shape."""
    word_range = "6-10"
    return f"""Create {count} different natural MSA sentences for a {target.difficulty} Arabic learner.

TARGET WORD (must appear in every sentence):
- {target.arabic} ({target.translation})

VOCABULARY (you may ONLY use these Arabic content words, plus function words):
{known_list}

IMPORTANT: Do NOT use any Arabic content words that are not in the list above.
Each sentence should be {word_range} words, with a different structure or context.
Include full diacritics on all Arabic text.

Respond with JSON: {{"sentences": [{{"arabic": "...", "english": "...", "transliteration": "..."}}, ...]}}"""


# ─── Claude CLI ──────────────────────────────────────────────────────────────


def call_claude(
    *, prompt: str, system_prompt: str, model: str, timeout_s: int = 180
) -> tuple[dict | None, float, str]:
    """Free-tier Claude CLI call with JSON envelope, same shape used by Alif's
    `_generate_via_claude_cli`. We rely on the system_prompt to force JSON."""
    started = time.monotonic()
    cmd = [
        "claude",
        "--output-format", "json",
        "--model", model,
        "--tools", "",
        "--system-prompt", system_prompt,
        "--no-session-persistence",
        "-p", prompt,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return None, time.monotonic() - started, f"claude timed out after {timeout_s}s"
    except (FileNotFoundError, OSError) as exc:
        return None, time.monotonic() - started, f"claude unavailable: {exc}"
    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        return None, elapsed, f"claude exit {proc.returncode}: {proc.stderr[:400]}"
    # Outer envelope is JSON with a "result" field (free text).
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return None, elapsed, f"envelope decode failed: {exc}; raw: {proc.stdout[:400]}"
    body = envelope.get("result") if isinstance(envelope, dict) else None
    if not body:
        return None, elapsed, f"empty result in envelope: {str(envelope)[:400]}"
    # Strip markdown code fences if present (Anthropic CLI often wraps).
    body = re.sub(r"^```(?:json)?\s*", "", body.strip())
    body = re.sub(r"\s*```$", "", body)
    try:
        return json.loads(body), elapsed, ""
    except json.JSONDecodeError as exc:
        return None, elapsed, f"body decode failed: {exc}; raw: {body[:400]}"


# ─── Codex CLI (mirror of polyglot _call_codex) ──────────────────────────────


def _codex_env() -> dict:
    env = os.environ.copy()
    if env.get("OPENAI_KEY") and not env.get("OPENAI_API_KEY"):
        env["OPENAI_API_KEY"] = env["OPENAI_KEY"]
    return env


def _codex_reasoning_args() -> list[str]:
    effort = os.environ.get("POLYGLOT_CODEX_REASONING_EFFORT", "medium").strip()
    return ["-c", f'model_reasoning_effort="{effort}"'] if effort else []


def call_codex(
    *, prompt: str, system_prompt: str, model: str = "gpt-5.5", timeout_s: int = 180
) -> tuple[dict | None, float, str]:
    schema_path: str | None = None
    output_path: str | None = None
    full_prompt = f"{system_prompt}\n\n{prompt}"
    started = time.monotonic()
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".schema.json", delete=False) as sf:
            json.dump(SENTENCES_SCHEMA, sf, ensure_ascii=False)
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
            *_codex_reasoning_args(),
            full_prompt,
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s, env=_codex_env(),
        )
        elapsed = time.monotonic() - started
        if proc.returncode != 0:
            return None, elapsed, f"codex exit {proc.returncode}: {proc.stderr[:400]}"
        text = Path(output_path).read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            text = proc.stdout
        try:
            return json.loads(text), elapsed, ""
        except json.JSONDecodeError as exc:
            return None, elapsed, f"json decode failed: {exc}; raw: {text[:400]}"
    except subprocess.TimeoutExpired:
        return None, time.monotonic() - started, f"codex timed out after {timeout_s}s"
    except (FileNotFoundError, OSError) as exc:
        return None, time.monotonic() - started, f"codex unavailable: {exc}"
    finally:
        for path in (schema_path, output_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


# ─── Report ──────────────────────────────────────────────────────────────────


@dataclass
class TargetResult:
    target: Target
    claude_sentences: list = field(default_factory=list)
    claude_time: float = 0.0
    claude_error: str = ""
    codex_sentences: list = field(default_factory=list)
    codex_time: float = 0.0
    codex_error: str = ""


def _format_sentence(s) -> str:
    ar = s.get("arabic", "") or ""
    en = s.get("english", "") or ""
    tr = s.get("transliteration", "") or ""
    out = [f"  AR: {ar}"]
    if tr:
        out.append(f"  TR: {tr}")
    out.append(f"  EN: {en}")
    return "\n".join(out)


def format_report(results: list[TargetResult], count: int, codex_model: str) -> str:
    lines = [
        "# Alif sentence-gen A/B — Claude Sonnet vs Codex `" + codex_model + "`",
        "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}.",
        f"`count={count}` candidates per target. Same prompt/system_prompt for both providers "
        f"(taken verbatim from `backend/app/services/llm.py:generate_sentences_batch`).",
        "",
        "## Summary",
        "",
        "| Target | Claude time | Codex time | Claude n | Codex n | Notes |",
        "|---|---:|---:|---:|---:|---|",
    ]
    claude_total = 0.0
    codex_total = 0.0
    for r in results:
        notes = []
        if r.claude_error:
            notes.append(f"Claude err: `{r.claude_error[:50]}`")
        if r.codex_error:
            notes.append(f"Codex err: `{r.codex_error[:50]}`")
        lines.append(
            f"| `{r.target.arabic}` ({r.target.translation}) | "
            f"{r.claude_time:.1f}s | {r.codex_time:.1f}s | "
            f"{len(r.claude_sentences)} | {len(r.codex_sentences)} | "
            f"{'; '.join(notes) if notes else ''} |"
        )
        claude_total += r.claude_time
        codex_total += r.codex_time
    if claude_total > 0:
        lines.append("")
        lines.append(f"**Totals:** Claude {claude_total:.1f}s, Codex {codex_total:.1f}s "
                     f"(Codex ratio: {codex_total / claude_total:.2f}×).")
    lines.extend(["", "---", ""])
    for r in results:
        lines.append(f"## `{r.target.arabic}` — {r.target.translation} "
                     f"({r.target.difficulty})")
        lines.append("")
        if r.target.note:
            lines.append(f"*{r.target.note}*")
            lines.append("")
        lines.append(f"### Claude Sonnet — {r.claude_time:.1f}s, "
                     f"{len(r.claude_sentences)} sentences")
        if r.claude_error:
            lines.append(f"**ERROR:** `{r.claude_error}`")
            lines.append("")
        for i, s in enumerate(r.claude_sentences, 1):
            lines.append(f"**Claude #{i}**")
            lines.append("```")
            lines.append(_format_sentence(s))
            lines.append("```")
            lines.append("")
        lines.append(f"### Codex {codex_model} — {r.codex_time:.1f}s, "
                     f"{len(r.codex_sentences)} sentences")
        if r.codex_error:
            lines.append(f"**ERROR:** `{r.codex_error}`")
            lines.append("")
        for i, s in enumerate(r.codex_sentences, 1):
            lines.append(f"**Codex #{i}**")
            lines.append("```")
            lines.append(_format_sentence(s))
            lines.append("```")
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


# ─── Entrypoint ──────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--count", type=int, default=5,
                    help="Sentences per target per provider")
    ap.add_argument("--codex-model", default="gpt-5.5")
    ap.add_argument("--claude-model", default="claude-sonnet-4-5-20250929")
    ap.add_argument("--targets", type=int, default=None,
                    help="Limit to first N targets (default: all)")
    ap.add_argument("--report-out", type=Path, default=None,
                    help="Markdown report path")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    targets = TARGETS[: args.targets] if args.targets else TARGETS
    LOG.info("Evaluating %d targets, count=%d per provider", len(targets), args.count)

    known_list = format_known_words_by_pos(SHARED_VOCAB)
    results: list[TargetResult] = []

    if args.report_out is None:
        report_dir = REPO_ROOT / "research"
        report_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).date().isoformat()
        args.report_out = report_dir / f"codex-vs-claude-sentence-gen-{today}.md"

    def _checkpoint():
        args.report_out.write_text(
            format_report(results, args.count, args.codex_model), encoding="utf-8"
        )

    for i, t in enumerate(targets, start=1):
        LOG.info("[%d/%d] %s (%s, %s)",
                 i, len(targets), t.arabic, t.translation, t.difficulty)
        prompt = build_user_prompt(t, args.count, known_list)

        LOG.info("   Claude…")
        claude_json, claude_time, claude_err = call_claude(
            prompt=prompt,
            system_prompt=BATCH_SENTENCE_SYSTEM_PROMPT,
            model=args.claude_model,
        )
        claude_sentences = claude_json.get("sentences", []) if claude_json else []
        LOG.info("   Claude: %d in %.1fs%s",
                 len(claude_sentences), claude_time,
                 f"  err={claude_err}" if claude_err else "")

        LOG.info("   Codex…")
        codex_json, codex_time, codex_err = call_codex(
            prompt=prompt,
            system_prompt=BATCH_SENTENCE_SYSTEM_PROMPT,
            model=args.codex_model,
        )
        codex_sentences = codex_json.get("sentences", []) if codex_json else []
        LOG.info("   Codex: %d in %.1fs%s",
                 len(codex_sentences), codex_time,
                 f"  err={codex_err}" if codex_err else "")

        results.append(TargetResult(
            target=t,
            claude_sentences=claude_sentences,
            claude_time=claude_time,
            claude_error=claude_err,
            codex_sentences=codex_sentences,
            codex_time=codex_time,
            codex_error=codex_err,
        ))
        _checkpoint()

    LOG.info("Wrote report → %s", args.report_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
