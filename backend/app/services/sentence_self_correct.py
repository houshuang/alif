"""Self-correcting sentence generator (EXPERIMENTAL — not yet wired into prod).

A single Claude CLI session that:
  1. Reads the learner's vocabulary from a temp file.
  2. Drafts an Arabic sentence with the target word.
  3. Calls a Bash-invoked validator script.
  4. On failure, edits ONLY the offending word(s) in place — does NOT regenerate
     the whole sentence — then re-validates.
  5. Repeats until ``needed`` validated sentences exist, then writes them to
     ``out.json`` and exits.

Replaces the legacy ``generate_sentences_batch`` → external rerank → external
validate → reject loop, which dropped ~70% of candidates.

Output shape matches ``app.services.llm.SentenceResult`` so callers (most
importantly ``generate_material_for_word``) can swap this in without
downstream changes.

This module is the prototype called by the test in
``backend/tests/test_self_correct_smoke.py`` and the ad-hoc benchmark in
``/tmp/claude/self_correct_test.py``. It is not imported by production code
yet — wiring happens in a follow-up only if the benchmark passes the gates.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from app.services.llm import SentenceResult

logger = logging.getLogger(__name__)


SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "arabic": {"type": "string"},
                    "english": {"type": "string"},
                    "transliteration": {"type": "string"},
                    "edits": {
                        "type": "integer",
                        "description": "Number of validator-driven word edits before this sentence passed",
                    },
                },
                "required": ["arabic", "english", "transliteration"],
            },
        },
    },
    "required": ["sentences"],
}


SYSTEM_PROMPT = """\
You are an Arabic (MSA / fusha) tutor writing standalone flashcard sentences.

Tools available in the work_dir:
- Read: vocab_prompt.txt (sample of learner vocabulary by POS).
- Bash: validator.py — `python3 validator.py "<arabic>" "<target_bare>"` prints \
JSON with valid, target_found, unknown_words, known_words, function_words, issues.

Per-sentence workflow:
  1. Draft a 4-10 word Arabic sentence using the target + vocabulary words.
  2. Validate it.
  3. On unknown_words: REPLACE each offending word with a word from \
vocab_prompt.txt — surgical swaps, not full rewrites — and re-validate.
  4. Drop after 4 failed edit cycles; try a fresh sentence with different \
supporting vocab.

Rules:
- Only Arabic content words from vocab_prompt.txt. Function words \
(في، من، على، إلى، و، ب، ل، هذا، هذه، أن، إن، كان، ليس، هل، لا، قد، \
الذي، التي…) are always free.
- Full tashkeel on every Arabic word.
- Transliteration: ALA-LC with macrons.
- English: natural complete-thought translation.
- No anaphor referring to unstated context. No continuations starting \
with و/ف/ثم.
- Vary sentence structures across the batch (VSO/SVO/idafa/nominal).

When you have the requested validated count, return the JSON. The `edits` \
field per sentence = number of validator-driven word swaps before it passed."""


@dataclass
class SelfCorrectResult:
    sentences: list[SentenceResult]
    cost_usd: float
    duration_s: float
    turns: int
    raw: dict


def _load_active_lemma_rows(db_path: str) -> list[dict]:
    """Pull all 'active' lemmas the learner can use as supporting vocab.

    Mirrors `dump_vocabulary_for_claude`'s SELECT — kept inline so the
    prototype is self-contained and can be invoked without going through
    the legacy helper (which mutates a separate working dir).
    """
    import sqlite3

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
    return [dict(r) for r in rows]


def _write_vocab_files(
    rows: list[dict],
    work_dir: str,
    target_lemma_id: int,
    target_word: str,
    target_translation: str,
    prompt_sample_size: int = 200,
) -> None:
    """Build vocab_prompt.txt + vocab_lookup.tsv inside ``work_dir``.

    The lookup TSV always lists ALL active forms — Sonnet's validator needs
    the full set to classify words correctly. The vocab_prompt.txt, however,
    is sampled down to ``prompt_sample_size`` per POS bucket (acquiring +
    learning words always included) so we don't burn cache tokens on a 50KB
    file every CLI turn. The first session burned $0.45 reading a 13K-token
    vocab dump.
    """
    import random

    from app.services.sentence_validator import (
        FUNCTION_WORD_FORMS,
        normalize_alef,
        strip_diacritics,
    )

    os.makedirs(work_dir, exist_ok=True)

    # Always-include rows: acquiring + learning. They're the high-value
    # supporting vocab the user should be encountering.
    priority_rows = [
        r for r in rows
        if r["lemma_id"] != target_lemma_id
        and r["knowledge_state"] in ("acquiring", "learning", "lapsed")
    ]
    other_rows = [
        r for r in rows
        if r["lemma_id"] != target_lemma_id
        and r["knowledge_state"] == "known"
    ]

    sampled_rows = list(priority_rows)
    remaining = max(0, prompt_sample_size - len(sampled_rows))
    if remaining and other_rows:
        rng = random.Random(target_lemma_id)  # deterministic per target
        sampled_rows.extend(rng.sample(other_rows, min(remaining, len(other_rows))))

    acquiring_entries: list[str] = []
    pos_groups: dict[str, list[str]] = {
        "NOUNS": [], "VERBS": [], "ADJECTIVES": [], "OTHER": [],
    }
    for row in sampled_rows:
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

    prompt_path = Path(work_dir) / "vocab_prompt.txt"
    with prompt_path.open("w") as f:
        f.write(f"TARGET WORD (must appear in every sentence): {target_word} ({target_translation})\n\n")
        if acquiring_entries:
            f.write(
                "CURRENTLY LEARNING (prefer these as supporting vocabulary): "
                + ", ".join(acquiring_entries) + "\n\n"
            )
        for label, words in pos_groups.items():
            if words:
                f.write(f"{label}: {', '.join(words)}\n")

    # Build the bare-form lookup TSV consumed by validator.py.
    lookup: dict[str, int] = {}
    bare_to_id: dict[str, int] = {}
    for row in rows:
        bare_norm = normalize_alef(row["lemma_ar_bare"])
        lookup[bare_norm] = row["lemma_id"]
        bare_to_id.setdefault(bare_norm, row["lemma_id"])
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
                    if key in (
                        "plural", "present", "masdar", "active_participle",
                        "feminine", "elative",
                    ) or key.startswith("variant_"):
                        if form_val and isinstance(form_val, str):
                            form_bare = normalize_alef(strip_diacritics(form_val))
                            lookup.setdefault(form_bare, row["lemma_id"])
                            al_form = "ال" + form_bare
                            if not form_bare.startswith("ال"):
                                lookup.setdefault(al_form, row["lemma_id"])

    for form, base in FUNCTION_WORD_FORMS.items():
        form_norm = normalize_alef(form)
        if form_norm not in lookup:
            base_id = bare_to_id.get(normalize_alef(base))
            if base_id is not None:
                lookup[form_norm] = base_id

    lookup_path = Path(work_dir) / "vocab_lookup.tsv"
    with lookup_path.open("w") as f:
        f.write("# bare_form\tlemma_id\n")
        for bare, lid in sorted(lookup.items(), key=lambda x: x[1]):
            f.write(f"{bare}\t{lid}\n")


_VALIDATOR_SCRIPT = '''#!/usr/bin/env python3
"""Tiny self-contained validator used by the self-correcting Sonnet session.

Call:  python3 validator.py "<arabic sentence>" "<target bare form>"

Mirrors the slimmer invariants of ``app.services.sentence_validator.validate_sentence``
but with no project imports — runs in any work_dir without PYTHONPATH setup.
We re-use the project validator by adding the backend path and importing it.
"""
import json
import os
import sys

# The work_dir is /tmp/claude/<run-id>. The project lives at a known path
# baked in below. The wrapper script is generated per-run with the right path.
sys.path.insert(0, "{backend_path}")

from app.services.sentence_validator import validate_sentence

VOCAB_TSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vocab_lookup.tsv")


def load_bare_set(path):
    s = set()
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\\t")
            if parts:
                s.add(parts[0])
    return s


def main():
    if len(sys.argv) < 3:
        print(json.dumps({{"valid": False, "issues": ["usage: validator.py <arabic> <target_bare>"]}}))
        sys.exit(2)
    arabic = sys.argv[1]
    target_bare = sys.argv[2]
    bare_set = load_bare_set(VOCAB_TSV)
    res = validate_sentence(
        arabic_text=arabic,
        target_bare=target_bare,
        known_bare_forms=bare_set,
    )
    print(json.dumps({{
        "valid": res.valid,
        "target_found": res.target_found,
        "unknown_words": res.unknown_words,
        "known_words": res.known_words,
        "function_words": res.function_words,
        "issues": res.issues,
    }}, ensure_ascii=False))


if __name__ == "__main__":
    main()
'''


def _write_validator_script(work_dir: str) -> None:
    """Drop a self-contained validator.py into the work_dir.

    The script imports from the project's installed package (via the
    backend path baked in at write time). We don't ship the project's
    validate_sentence_cli.py because it relies on argparse flags Sonnet
    would have to remember; this gives Sonnet the simplest possible CLI:
    `python3 validator.py "<arabic>" "<target_bare>"`.
    """
    backend_path = str(Path(__file__).resolve().parent.parent.parent)
    script_text = _VALIDATOR_SCRIPT.format(backend_path=backend_path)
    out_path = Path(work_dir) / "validator.py"
    out_path.write_text(script_text)
    out_path.chmod(0o755)


def generate_sentences_self_correct(
    target_lemma_id: int,
    target_word: str,
    target_translation: str,
    target_example_ar: str | None,
    target_example_en: str | None,
    db_path: str,
    needed: int = 2,
    max_budget_usd: float = 0.30,
    timeout: int = 300,
    work_dir: str | None = None,
    keep_work_dir: bool = False,
) -> SelfCorrectResult:
    """Single Sonnet session that drafts → validates → surgically corrects → returns N sentences.

    Args:
        target_lemma_id: Lemma DB id (used only to skip the target itself in the vocab dump).
        target_word: Diacritized Arabic form to feature.
        target_translation: English gloss to display.
        target_example_ar/en: Optional canonical example anchoring the sense.
        db_path: SQLite path to draw the user's active vocabulary from.
        needed: Number of validated sentences to return.
        max_budget_usd: Hard cap passed to `claude -p --max-budget-usd`.
        timeout: Subprocess timeout in seconds.
        work_dir: Optional pre-existing dir for vocab + validator + out.json.
            Defaults to a fresh tempdir under $TMPDIR.
        keep_work_dir: If False (default) and `work_dir` was auto-generated,
            the dir is removed after the call. Tests pass True so they can
            inspect.

    Returns:
        SelfCorrectResult with the parsed SentenceResult list plus session
        metadata (cost, duration, turns, raw structured output).
    """
    from app.services.claude_code import is_available
    from limbic.cerebellum.claude_cli import ClaudeCLIError, generate as _limbic_generate

    if not is_available():
        raise FileNotFoundError("claude CLI not installed")

    auto_dir = work_dir is None
    if auto_dir:
        work_dir = tempfile.mkdtemp(prefix=f"alif-self-correct-{target_lemma_id}-")
    else:
        os.makedirs(work_dir, exist_ok=True)

    try:
        rows = _load_active_lemma_rows(db_path)
        _write_vocab_files(
            rows, work_dir,
            target_lemma_id=target_lemma_id,
            target_word=target_word,
            target_translation=target_translation,
        )
        _write_validator_script(work_dir)

        example_block = ""
        if target_example_ar and target_example_en:
            example_block = (
                f"\nCanonical example anchoring the right sense:\n"
                f"  AR: {target_example_ar}\n"
                f"  EN: {target_example_en}\n"
            )

        prompt = f"""Generate {needed} validated MSA sentence(s) featuring \
{target_word} ({target_translation}).
{example_block}
1. Read {work_dir}/vocab_prompt.txt once.
2. For each sentence: draft, validate via \
`python3 {work_dir}/validator.py "<arabic>" "{target_word}"`, surgically \
swap each unknown_word with a vocab word and re-validate. Drop after 4 \
failed edits.
3. Vary sentence structures across the batch.
4. Return the JSON when {needed} sentences are validated. Set `edits` per \
sentence = number of validator-driven word swaps before it passed."""

        start = time.time()
        try:
            result, meta = _limbic_generate(
                prompt=prompt,
                project="alif",
                purpose="sentence_self_correct",
                system=SYSTEM_PROMPT,
                schema=SCHEMA,
                model="sonnet",
                tools="Read,Bash",
                # alif-backend runs as root which makes
                # `--dangerously-skip-permissions` impossible. Headless mode
                # also can't interactively approve tool calls, so without
                # `--allowedTools` Sonnet's first Bash invocation is denied
                # and the session ends with no structured output.
                # `Bash Read` whitelists every Bash + Read call.
                allowed_tools="Bash Read",
                # Cap turns to keep cached-token replay bounded. Empirically
                # Sonnet drafts + validates 2 sentences in 10-15 turns; 24
                # leaves headroom for 2 retry cycles per sentence without
                # blowing the budget on context replay.
                max_turns=24,
                max_budget=max_budget_usd,
                work_dir=work_dir,
                dangerously_skip_permissions=False,
                timeout=timeout,
            )
        except ClaudeCLIError as exc:
            raise RuntimeError(str(exc)) from exc
        elapsed = time.time() - start

        sentences: list[SentenceResult] = []
        for s in result.get("sentences", [])[:needed]:
            arabic = (s.get("arabic") or "").strip()
            english = (s.get("english") or "").strip()
            translit = (s.get("transliteration") or "").strip()
            if arabic and english:
                sentences.append(SentenceResult(
                    arabic=arabic, english=english, transliteration=translit,
                ))

        return SelfCorrectResult(
            sentences=sentences,
            cost_usd=float(meta.get("cost", 0.0) or 0.0),
            duration_s=elapsed,
            turns=int(meta.get("turns", 0) or 0),
            raw=result,
        )
    finally:
        if auto_dir and not keep_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)


# ────────────────────────────────────────────────────────────────────────────
# Batch wrapper — multiple targets per Sonnet session.
#
# Per-turn cached-token replay was eating ~$0.247/lemma in the single-target
# version because each target spawned its own session with the full vocab dump
# replayed every tool turn. Batching N targets into one session amortizes the
# vocab + system-prompt context across all of them — same trick the existing
# `batch_generate_material` uses for the legacy generate-then-validate path.
# ────────────────────────────────────────────────────────────────────────────


BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target_lemma_id": {"type": "integer"},
                    "sentences": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "arabic": {"type": "string"},
                                "english": {"type": "string"},
                                "transliteration": {"type": "string"},
                                "edits": {"type": "integer"},
                            },
                            "required": ["arabic", "english", "transliteration"],
                        },
                    },
                },
                "required": ["target_lemma_id", "sentences"],
            },
        },
    },
    "required": ["results"],
}


BATCH_SYSTEM_PROMPT = """\
You are an Arabic (MSA / fusha) tutor writing standalone flashcard sentences \
for MULTIPLE target words in ONE session.

Tools available in the work_dir:
- Read: vocab_prompt.txt (sample of learner vocabulary by POS).
- Read: targets.json (list of {target_lemma_id, target_word, target_translation, \
example_ar, example_en} you must write sentences for).
- Bash: validator.py — `python3 validator.py "<arabic>" "<target_bare>"` prints \
JSON with valid, target_found, unknown_words, known_words, function_words, issues.

Per-target workflow:
  1. Draft a 4-10 word Arabic sentence using the target + vocabulary words.
  2. Validate it.
  3. On unknown_words: REPLACE each offending word with a word from \
vocab_prompt.txt — surgical swaps, not full rewrites — and re-validate.
  4. Drop after 4 failed edit cycles; try a fresh sentence with different \
supporting vocab.

After all targets are processed, return the JSON {results: [{target_lemma_id, \
sentences: [...]}]}.

Rules:
- Only Arabic content words from vocab_prompt.txt. Function words \
(في، من، على، إلى، و، ب، ل، هذا، هذه، أن، إن، كان، ليس، هل، لا، قد، \
الذي، التي…) are always free.
- Full tashkeel on every Arabic word.
- Transliteration: ALA-LC with macrons.
- English: natural complete-thought translation.
- No anaphor referring to unstated context. No continuations starting \
with و/ف/ثم.
- Vary sentence structures across targets (VSO/SVO/idafa/nominal).
- The sentence MUST sound like something a literate Arabic speaker would \
actually say. Reject your own draft if the topic combination is bizarre, \
forced, or non-sequitur (e.g. "He has an allergy to prayer"). Pick a \
DIFFERENT supporting vocab combination.

CRITICAL — STANDALONE-FLASHCARD RULES (a Haiku reviewer will reject \
violations and your work will be discarded):

A. NEVER write a verb with an implicit 3rd-person subject ("he/she/it/they"). \
Arabic verbs conjugate for person, so "تَأَخَّرَ" alone means "he was late" \
which is unanchored. ALWAYS supply an explicit subject noun:
   - BAD:  تَأَخَّرَ بِسَبَبِ الْمَرَضِ.   ("he was late because of the illness")
   - GOOD: تَأَخَّرَ الطَّبِيبُ بِسَبَبِ الْمَرَضِ.   ("the doctor was late because of the illness")

B. NEVER use a bare definite-article noun ("the X") as the topic without \
anchoring it. "الْمَرَضُ صَعْبٌ" reads as "THE [particular] disease is hard" \
and presupposes prior context. Either (i) make it generic with an indefinite \
("مَرَضٌ مُعْدٍ خَطِيرٌ" — "a contagious disease is dangerous"), (ii) make \
it a generic-class statement ("الطُّفُولَةُ زَمَنُ الْبَرَاءَةِ" — "childhood \
is the time of innocence", a proverb-like generalization), or (iii) anchor it \
to a within-sentence referent ("الْمَرَضُ الَّذِي أَصَابَ الطِّفْلَ خَطِيرٌ").

C. NO demonstrative pronouns (هذا/هذه/ذلك) as subjects pointing at unspecified \
things. "هَذَا الْجَمَالُ جَمِيلٌ" is rejected.

D. NO tautologies / semantic redundancies ("the beauty is beautiful").

E. Self-check before adding to results: read your draft aloud as a standalone \
sentence with no prior context. Can a learner understand WHO and WHAT without \
asking? If not, rewrite."""


def _write_batch_files(
    rows: list[dict],
    work_dir: str,
    targets: list[dict],
    prompt_sample_size: int = 250,
) -> None:
    """Build vocab_prompt.txt + vocab_lookup.tsv + targets.json for a batch session.

    Larger sample size than single-target (250 vs 200) because the same vocab
    dump is reused across many targets — a one-time cost paid by the session,
    not per target. Excludes ALL target lemmas so Sonnet won't accidentally
    use one target as supporting vocab for another.
    """
    import random

    from app.services.sentence_validator import (
        FUNCTION_WORD_FORMS,
        normalize_alef,
        strip_diacritics,
    )

    os.makedirs(work_dir, exist_ok=True)

    target_ids = {t["target_lemma_id"] for t in targets}

    priority_rows = [
        r for r in rows
        if r["lemma_id"] not in target_ids
        and r["knowledge_state"] in ("acquiring", "learning", "lapsed")
    ]
    other_rows = [
        r for r in rows
        if r["lemma_id"] not in target_ids
        and r["knowledge_state"] == "known"
    ]

    sampled_rows = list(priority_rows)
    remaining = max(0, prompt_sample_size - len(sampled_rows))
    if remaining and other_rows:
        # Stable seed across runs so cache hits when re-running same batch.
        rng = random.Random(sum(target_ids))
        sampled_rows.extend(rng.sample(other_rows, min(remaining, len(other_rows))))

    pos_groups: dict[str, list[str]] = {
        "NOUNS": [], "VERBS": [], "ADJECTIVES": [], "OTHER": [],
    }
    acquiring_entries: list[str] = []
    for row in sampled_rows:
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

    prompt_path = Path(work_dir) / "vocab_prompt.txt"
    with prompt_path.open("w") as f:
        f.write(f"Batch of {len(targets)} target words — see targets.json for the list.\n\n")
        if acquiring_entries:
            f.write(
                "CURRENTLY LEARNING (prefer these as supporting vocabulary): "
                + ", ".join(acquiring_entries) + "\n\n"
            )
        for label, words in pos_groups.items():
            if words:
                f.write(f"{label}: {', '.join(words)}\n")

    targets_path = Path(work_dir) / "targets.json"
    with targets_path.open("w") as f:
        json.dump(targets, f, ensure_ascii=False, indent=2)

    # Build the bare-form lookup TSV consumed by validator.py — same as single-target.
    lookup: dict[str, int] = {}
    bare_to_id: dict[str, int] = {}
    for row in rows:
        bare_norm = normalize_alef(row["lemma_ar_bare"])
        lookup[bare_norm] = row["lemma_id"]
        bare_to_id.setdefault(bare_norm, row["lemma_id"])
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
                    if key in (
                        "plural", "present", "masdar", "active_participle",
                        "feminine", "elative",
                    ) or key.startswith("variant_"):
                        if form_val and isinstance(form_val, str):
                            form_bare = normalize_alef(strip_diacritics(form_val))
                            lookup.setdefault(form_bare, row["lemma_id"])
                            al_form = "ال" + form_bare
                            if not form_bare.startswith("ال"):
                                lookup.setdefault(al_form, row["lemma_id"])

    for form, base in FUNCTION_WORD_FORMS.items():
        form_norm = normalize_alef(form)
        if form_norm not in lookup:
            base_id = bare_to_id.get(normalize_alef(base))
            if base_id is not None:
                lookup[form_norm] = base_id

    lookup_path = Path(work_dir) / "vocab_lookup.tsv"
    with lookup_path.open("w") as f:
        f.write("# bare_form\tlemma_id\n")
        for bare, lid in sorted(lookup.items(), key=lambda x: x[1]):
            f.write(f"{bare}\t{lid}\n")


@dataclass
class BatchSelfCorrectResult:
    sentences_by_target: dict[int, list[SentenceResult]]
    cost_usd: float
    duration_s: float
    turns: int
    raw: dict


def generate_sentences_self_correct_batch(
    target_lemma_ids: list[int],
    db_path: str,
    needed_per_target: int = 2,
    max_budget_usd: float = 0.50,
    timeout: int = 600,
    work_dir: str | None = None,
    keep_work_dir: bool = False,
) -> BatchSelfCorrectResult:
    """One Sonnet session writes ``needed_per_target`` sentences for EACH target.

    Amortizes per-turn cached-token replay across N targets. Empirically the
    single-target version costs ~$0.25/lemma at ~16 turns; with N=4 in one
    session we expect ~$0.06/lemma (vocab dump + system prompt cached once).
    """
    import sqlite3

    from app.services.claude_code import is_available
    from limbic.cerebellum.claude_cli import ClaudeCLIError, generate as _limbic_generate

    if not is_available():
        raise FileNotFoundError("claude CLI not installed")

    if not target_lemma_ids:
        return BatchSelfCorrectResult({}, 0.0, 0.0, 0, {})

    auto_dir = work_dir is None
    if auto_dir:
        work_dir = tempfile.mkdtemp(prefix=f"alif-self-correct-batch-{target_lemma_ids[0]}-")
    else:
        os.makedirs(work_dir, exist_ok=True)

    try:
        rows = _load_active_lemma_rows(db_path)

        # Hydrate per-target metadata from DB so callers only need to pass IDs.
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        target_rows = conn.execute(
            f"SELECT lemma_id, lemma_ar, lemma_ar_bare, gloss_en, example_ar, example_en "
            f"FROM lemmas WHERE lemma_id IN ({','.join('?' * len(target_lemma_ids))})",
            target_lemma_ids,
        ).fetchall()
        conn.close()

        targets = []
        for r in target_rows:
            targets.append({
                "target_lemma_id": r["lemma_id"],
                "target_word": r["lemma_ar"],
                "target_bare": r["lemma_ar_bare"],
                "target_translation": r["gloss_en"] or "",
                "example_ar": r["example_ar"] or "",
                "example_en": r["example_en"] or "",
            })

        if not targets:
            return BatchSelfCorrectResult({}, 0.0, 0.0, 0, {})

        _write_batch_files(rows, work_dir, targets)
        _write_validator_script(work_dir)

        prompt = f"""Generate {needed_per_target} validated MSA sentence(s) for \
EACH of {len(targets)} target words listed in {work_dir}/targets.json.

Workflow:
1. Read {work_dir}/vocab_prompt.txt and {work_dir}/targets.json once.
2. For EACH target in targets.json (process them sequentially):
   a. Draft a sentence featuring target_word.
   b. Validate via `python3 {work_dir}/validator.py "<arabic>" "<target_bare>"`.
   c. On unknown_words, surgically swap each offending word with a vocab \
word and re-validate. Drop after 4 failed edits; try a fresh sentence.
   d. Stop once {needed_per_target} validated sentences exist for this target.
3. Vary sentence structures across the batch (VSO/SVO/idafa/nominal).
4. After ALL targets, return JSON: {{"results": [{{"target_lemma_id": N, \
"sentences": [{{"arabic": "...", "english": "...", "transliteration": "...", \
"edits": K}}, ...]}}, ...]}}.

Quality bar: each sentence must be something a literate Arabic speaker \
would actually say in plain context. Reject your own draft and rewrite if \
the topic combination is bizarre or forced."""

        # Generous turn budget: ~6 turns per target (read + 2 sentences x 2-3 \
        # validate-edit cycles) plus 4 for setup/wrap-up.
        max_turns = 4 + 8 * len(targets)

        start = time.time()
        try:
            result, meta = _limbic_generate(
                prompt=prompt,
                project="alif",
                purpose="sentence_self_correct_batch",
                system=BATCH_SYSTEM_PROMPT,
                schema=BATCH_SCHEMA,
                model="sonnet",
                tools="Read,Bash",
                allowed_tools="Bash Read",
                max_turns=max_turns,
                max_budget=max_budget_usd,
                work_dir=work_dir,
                dangerously_skip_permissions=False,
                timeout=timeout,
            )
        except ClaudeCLIError as exc:
            raise RuntimeError(str(exc)) from exc
        elapsed = time.time() - start

        sentences_by_target: dict[int, list[SentenceResult]] = {}
        for entry in result.get("results", []):
            tid = entry.get("target_lemma_id")
            if tid is None:
                continue
            slist: list[SentenceResult] = []
            for s in (entry.get("sentences") or [])[:needed_per_target]:
                arabic = (s.get("arabic") or "").strip()
                english = (s.get("english") or "").strip()
                translit = (s.get("transliteration") or "").strip()
                if arabic and english:
                    slist.append(SentenceResult(
                        arabic=arabic, english=english, transliteration=translit,
                    ))
            if slist:
                sentences_by_target[int(tid)] = slist

        return BatchSelfCorrectResult(
            sentences_by_target=sentences_by_target,
            cost_usd=float(meta.get("cost", 0.0) or 0.0),
            duration_s=elapsed,
            turns=int(meta.get("turns", 0) or 0),
            raw=result,
        )
    finally:
        if auto_dir and not keep_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
