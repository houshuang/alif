#!/usr/bin/env python3
"""Story generation benchmark: models × prompting strategies.

Tests different LLM models and prompting strategies for Arabic story generation.
Evaluates vocabulary compliance, narrative quality (via LLM judge), and cost.

Usage:
    # Pull latest backup first
    ./scripts/backup.sh

    # Run benchmark (use most recent backup)
    python3 scripts/benchmark_stories.py --db ~/alif-backups/latest/alif.db

    # Specific models/strategies
    python3 scripts/benchmark_stories.py --db data/alif.db --models gemini,opus --strategies A,B

    # Inside Docker container on server
    python3 scripts/benchmark_stories.py --db /app/data/alif.db
"""

import argparse
import json
import os
import re
import random
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import litellm

from app.services.sentence_validator import (
    FUNCTION_WORD_GLOSSES,
    FUNCTION_WORD_FORMS,
    build_lemma_lookup,
    lookup_lemma,
    normalize_alef,
    strip_diacritics,
    strip_tatweel,
    tokenize,
)
from app.services.llm import ARABIC_STYLE_RULES, DIFFICULTY_STYLE_GUIDE

litellm.set_verbose = False

# ---------------------------------------------------------------------------
# Model definitions
# ---------------------------------------------------------------------------

BENCHMARK_MODELS = {
    "gemini": {
        "model": "gemini/gemini-3-flash-preview",
        "key_env": "GEMINI_KEY",
    },
    "openai": {
        "model": "gpt-5.2",
        "key_env": "OPENAI_KEY",
    },
    "opus": {
        "model": "claude-opus-4-6",
        "key_env": "ANTHROPIC_API_KEY",
        "key_alt": "ANTHROPIC_KEY",
    },
    "sonnet": {
        "model": "claude-sonnet-4-5-20250929",
        "key_env": "ANTHROPIC_API_KEY",
        "key_alt": "ANTHROPIC_KEY",
    },
}

# Cost per million tokens (USD)
COST_PER_M_INPUT = {
    "gemini": 0.15,
    "openai": 2.50,
    "opus": 15.00,
    "sonnet": 3.00,
}
COST_PER_M_OUTPUT = {
    "gemini": 0.60,
    "openai": 10.00,
    "opus": 75.00,
    "sonnet": 15.00,
}

# ---------------------------------------------------------------------------
# Strategy definitions
# ---------------------------------------------------------------------------

STRATEGIES = {
    "A": "Baseline (flat vocab, random genre)",
    "B": "POS-grouped vocabulary",
    "C": "Expanded narrative structures",
    "D": "Two-pass (generate freely, then constrain)",
}

BASELINE_GENRES = [
    "a funny story with a punchline at the end",
    "a mystery — something is not what it seems",
    "a heartwarming story about an unexpected friendship",
    "a story with an ironic twist ending",
    "a short adventure with a moment of danger",
    "a story where someone learns a surprising lesson",
]

EXPANDED_STRUCTURES = [
    "Pure narration in third person, past tense. A character does something and something unexpected results.",
    "Dialogue-heavy story (>50% dialogue) between two characters who disagree about something.",
    "First-person recount: 'I' tells about something that happened today.",
    "A short fable with animal characters and a moral at the end.",
    "Mystery/reveal: something seems wrong, and the truth is discovered at the end.",
    "Day-in-the-life slice: a mundane but charming sequence of events.",
    "Problem-solution: a character faces a specific obstacle and finds a creative solution.",
    "Encounter story: two strangers meet and something changes for both.",
    "Discovery story: a character finds or learns something unexpected about their world.",
    "Humor with setup-escalation-punchline structure.",
    "Letter or message from one character to another, revealing a situation.",
    "Ironic twist: the story sets up one expectation and delivers the opposite.",
]

# System prompt shared across strategies A-C
STORY_SYSTEM_PROMPT = f"""\
You are a creative Arabic storyteller writing for language learners. Write genuinely \
engaging mini-stories in MSA (fusha) with a real narrative arc, characters, and a satisfying ending.

CRITICAL: Write a COHESIVE STORY with beginning, middle, and end. Every sentence must \
connect to the previous one and advance the narrative.

{ARABIC_STYLE_RULES}

Story craft:
- Give the main character a name and a situation/problem
- Build tension or curiosity
- End with a twist, punchline, resolution, or poetic moment
- Use dialogue (with قَالَ/قَالَتْ) when it serves the story
- Use VSO for narration (ذَهَبَ الرَّجُلُ), SVO for emphasis/contrast (الرَّجُلُ ذَهَبَ وَحْدَهُ)
- Nominal sentences for scene-setting (اللَّيْلُ طَوِيلٌ)

{DIFFICULTY_STYLE_GUIDE}

Vocabulary constraint:
- Use ONLY words from the provided vocabulary list and common function words
- Common function words you may freely use: في، من، على، إلى، و، ب، ل، ك، هذا، هذه، \
ذلك، تلك، هو، هي، أنا، أنت، نحن، هم، ما، لا، أن، إن، كان، كانت، ليس، هل، لم، \
لن، قد، الذي، التي، كل، بعض، هنا، هناك، الآن، جدا، فقط، أيضا، أو، ثم، لكن، يا
- Do NOT invent or use Arabic content words not in the vocabulary list
- Include full diacritics (tashkeel) on ALL Arabic words with correct i'rab
- Include Arabic punctuation: use ؟ for questions, . for statements, ، between clauses
- Transliteration: ALA-LC standard with macrons for long vowels

Respond with JSON only: {{"title_ar": "...", "title_en": "...", "body_ar": "...", "body_en": "...", "transliteration": "..."}}"""

TWO_PASS_SYSTEM_PASS1 = f"""\
You are a creative Arabic storyteller. Write genuinely engaging mini-stories in MSA (fusha) \
with a real narrative arc, characters, and a satisfying ending.

{ARABIC_STYLE_RULES}

Story craft:
- Give the main character a name and a situation/problem
- Build tension or curiosity
- End with a twist, punchline, resolution, or poetic moment
- Use dialogue when it serves the story
- Include full diacritics (tashkeel) on ALL Arabic words

You have COMPLETE FREEDOM in vocabulary choice. Use whatever Arabic words create the best story.

Respond with JSON: {{"title_ar": "...", "title_en": "...", "body_ar": "...", "body_en": "...", "transliteration": "..."}}"""

TWO_PASS_SYSTEM_PASS2 = f"""\
You are an Arabic language expert who adapts stories for learners. You will receive a story \
and a vocabulary list. Rewrite the story using ONLY the provided vocabulary and function words.

CRITICAL RULES:
- Preserve the narrative arc (setup → development → resolution) from the original
- Keep character names from the original
- Replace any content word NOT in the vocabulary list with a synonym that IS in the list
- If no synonym exists, restructure the sentence to avoid the word
- Maintain full diacritics (tashkeel) on all Arabic words
- The rewritten story should feel natural, not forced

{ARABIC_STYLE_RULES}

Common function words you may freely use: في، من، على، إلى، و، ب، ل، ك، هذا، هذه، \
ذلك، تلك، هو، هي، أنا، أنت، نحن، هم، ما، لا، أن، إن، كان، كانت، ليس، هل، لم، \
لن، قد، الذي، التي، كل، بعض، هنا، هناك، الآن، جدا، فقط، أيضا، أو، ثم، لكن، يا

Respond with JSON: {{"title_ar": "...", "title_en": "...", "body_ar": "...", "body_en": "...", "transliteration": "..."}}"""

# LLM judge prompt
JUDGE_SYSTEM = """\
You are an expert Arabic linguist and literary critic evaluating mini-stories written \
for Arabic language learners. Rate each dimension honestly — do not inflate scores."""

JUDGE_PROMPT_TEMPLATE = """\
Evaluate this Arabic mini-story on 7 dimensions (1-5 scale each).

STORY:
Title (Arabic): {title_ar}
Title (English): {title_en}
Body (Arabic):
{body_ar}
Body (English):
{body_en}

RATING DIMENSIONS:
1. NARRATIVE_ARC: Does it have beginning, middle, end? Setup, development, resolution? \
(1=disconnected sentences, 5=compelling arc with satisfying ending)

2. INTERESTINGNESS: Would an adult reader find this engaging? Surprise, humor, emotion, insight? \
(1=boring vocabulary exercise, 5=genuinely want to know what happens)

3. NATURALNESS: Does the Arabic sound like a native speaker wrote it? Correct idioms, collocations? \
(1=machine-translated feel, 5=completely natural MSA)

4. COHERENCE: Do sentences flow logically? Pronouns resolved? Temporal consistency? \
(1=random sentences, 5=seamless flow)

5. GRAMMAR: Verb agreements, case endings, idafa, word order correct? \
(1=multiple errors, 5=flawless)

6. DIACRITICS: Are tashkeel marks present and correct? \
(1=missing/wrong, 5=fully correct)

7. TRANSLATION: Does the English translation accurately reflect the Arabic? \
(1=wrong meaning, 5=perfect translation)

Respond with JSON: {{"narrative_arc": N, "interestingness": N, "naturalness": N, \
"coherence": N, "grammar": N, "diacritics": N, "translation": N, "comments": "brief notes"}}"""


# ---------------------------------------------------------------------------
# Vocabulary loading
# ---------------------------------------------------------------------------

class SimpleLemma:
    """Minimal lemma object for build_lemma_lookup compatibility."""
    def __init__(self, lemma_id: int, lemma_ar_bare: str, forms_json: dict | None = None):
        self.lemma_id = lemma_id
        self.lemma_ar_bare = lemma_ar_bare
        self.forms_json = forms_json


def load_vocabulary(db_path: str) -> dict:
    """Load vocabulary from SQLite DB.

    Returns dict with:
        - usable_words: list of dicts (lemma_id, arabic, arabic_bare, english, pos, state)
        - acquiring_ids: set of lemma_ids in acquiring state
        - compliance_lookup: lemma_lookup built from usable words only
        - all_lemma_lookup: lemma_lookup built from ALL lemmas
        - function_word_bares: set of bare function word forms
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Usable words: learning + known + acquiring (excluding variants)
    rows = conn.execute("""
        SELECT l.lemma_id, l.lemma_ar, l.lemma_ar_bare, l.gloss_en, l.pos,
               l.forms_json, ulk.knowledge_state, ulk.acquisition_box
        FROM lemmas l
        JOIN user_lemma_knowledge ulk ON l.lemma_id = ulk.lemma_id
        WHERE ulk.knowledge_state IN ('learning', 'known', 'acquiring')
          AND l.canonical_lemma_id IS NULL
    """).fetchall()

    usable_words = []
    acquiring_ids = set()
    usable_lemmas = []

    for r in rows:
        forms = None
        if r["forms_json"]:
            try:
                forms = json.loads(r["forms_json"]) if isinstance(r["forms_json"], str) else r["forms_json"]
            except (json.JSONDecodeError, TypeError):
                forms = None

        usable_words.append({
            "lemma_id": r["lemma_id"],
            "arabic": r["lemma_ar"],
            "arabic_bare": r["lemma_ar_bare"],
            "english": r["gloss_en"] or "",
            "pos": r["pos"] or "",
            "state": r["knowledge_state"],
            "box": r["acquisition_box"],
        })
        usable_lemmas.append(SimpleLemma(r["lemma_id"], r["lemma_ar_bare"], forms))

        if r["knowledge_state"] == "acquiring":
            acquiring_ids.add(r["lemma_id"])

    # All lemmas for full lookup
    all_rows = conn.execute("""
        SELECT lemma_id, lemma_ar_bare, forms_json FROM lemmas
    """).fetchall()
    all_lemmas = []
    for r in all_rows:
        forms = None
        if r["forms_json"]:
            try:
                forms = json.loads(r["forms_json"]) if isinstance(r["forms_json"], str) else r["forms_json"]
            except (json.JSONDecodeError, TypeError):
                forms = None
        all_lemmas.append(SimpleLemma(r["lemma_id"], r["lemma_ar_bare"], forms))

    conn.close()

    # Build function word bare forms
    func_bares = set()
    for fw in FUNCTION_WORD_GLOSSES:
        func_bares.add(normalize_alef(fw))
    for fw in FUNCTION_WORD_FORMS:
        func_bares.add(normalize_alef(fw))

    return {
        "usable_words": usable_words,
        "acquiring_ids": acquiring_ids,
        "compliance_lookup": build_lemma_lookup(usable_lemmas),
        "all_lemma_lookup": build_lemma_lookup(all_lemmas),
        "function_word_bares": func_bares,
    }


# ---------------------------------------------------------------------------
# LLM calls
# ---------------------------------------------------------------------------

def get_api_key(model_name: str) -> str | None:
    cfg = BENCHMARK_MODELS[model_name]
    key = os.environ.get(cfg["key_env"], "")
    if not key and "key_alt" in cfg:
        key = os.environ.get(cfg["key_alt"], "")
    return key or None


def call_llm(
    model_name: str,
    system: str,
    user: str,
    temperature: float = 0.9,
    json_mode: bool = True,
    timeout: int = 120,
) -> tuple[dict | None, dict]:
    """Call LLM. Returns (parsed_result, usage_stats)."""
    api_key = get_api_key(model_name)
    if not api_key:
        return None, {"error": f"No API key for {model_name}"}

    cfg = BENCHMARK_MODELS[model_name]
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    kwargs = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "api_key": api_key,
        "timeout": timeout,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    start = time.time()
    try:
        response = litellm.completion(**kwargs)
        elapsed = time.time() - start

        content = response.choices[0].message.content
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        usage = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
            "time_ms": int(elapsed * 1000),
        }

        return json.loads(text), usage

    except Exception as e:
        elapsed = time.time() - start
        return None, {"error": str(e), "time_ms": int(elapsed * 1000)}


# ---------------------------------------------------------------------------
# Vocabulary formatting
# ---------------------------------------------------------------------------

def format_vocab_flat(words: list[dict]) -> str:
    return "\n".join(f"- {w['arabic']} ({w['english']})" for w in words)


def format_vocab_pos(words: list[dict]) -> str:
    groups: dict[str, list[str]] = {"NOUNS": [], "VERBS": [], "ADJECTIVES": [], "OTHER": []}
    for w in words:
        pos = (w.get("pos") or "").lower()
        entry = f"{w['arabic']} ({w['english']})"
        if pos in ("noun", "noun_prop"):
            groups["NOUNS"].append(entry)
        elif pos == "verb":
            groups["VERBS"].append(entry)
        elif pos in ("adj", "adj_comp"):
            groups["ADJECTIVES"].append(entry)
        else:
            groups["OTHER"].append(entry)
    lines = []
    for label, items in groups.items():
        if items:
            lines.append(f"{label}: {', '.join(items)}")
    return "\n".join(lines) if lines else format_vocab_flat(words)


# ---------------------------------------------------------------------------
# Strategy prompt builders
# ---------------------------------------------------------------------------

def build_prompt_A(words: list[dict], story_idx: int) -> tuple[str, str, float]:
    """Baseline: flat vocab, random genre. Returns (system, user, temperature)."""
    genre = BASELINE_GENRES[story_idx % len(BASELINE_GENRES)]
    vocab = format_vocab_flat(words)
    user = f"""Write a cohesive mini-story (4-7 sentences) for a beginner Arabic learner.

GENRE: {genre}

KNOWN VOCABULARY (the reader knows these words — use ONLY these plus function words):
{vocab}

IMPORTANT: Do NOT use Arabic content words not in the list above.
Write a REAL STORY with narrative arc: setup → development → resolution/punchline.
Give the main character a name.
Include full diacritics on ALL Arabic words.

Respond with JSON: {{"title_ar": "...", "title_en": "...", "body_ar": "...", "body_en": "...", "transliteration": "..."}}"""
    return STORY_SYSTEM_PROMPT, user, 0.9


def build_prompt_B(words: list[dict], story_idx: int) -> tuple[str, str, float]:
    """POS-grouped vocab, same genre system."""
    genre = BASELINE_GENRES[story_idx % len(BASELINE_GENRES)]
    vocab = format_vocab_pos(words)
    user = f"""Write a cohesive mini-story (4-7 sentences) for a beginner Arabic learner.

GENRE: {genre}

KNOWN VOCABULARY grouped by part of speech (use ONLY these plus function words):
{vocab}

IMPORTANT: Do NOT use Arabic content words not in the vocabulary above.
Write a REAL STORY with narrative arc: setup → development → resolution/punchline.
Give the main character a name.
Include full diacritics on ALL Arabic words.

Respond with JSON: {{"title_ar": "...", "title_en": "...", "body_ar": "...", "body_en": "...", "transliteration": "..."}}"""
    return STORY_SYSTEM_PROMPT, user, 0.9


def build_prompt_C(words: list[dict], story_idx: int) -> tuple[str, str, float]:
    """Expanded narrative structures."""
    structure = EXPANDED_STRUCTURES[story_idx % len(EXPANDED_STRUCTURES)]
    vocab = format_vocab_pos(words)
    user = f"""Write a cohesive mini-story (4-7 sentences) for a beginner Arabic learner.

NARRATIVE STRUCTURE: {structure}

KNOWN VOCABULARY grouped by part of speech (use ONLY these plus function words):
{vocab}

IMPORTANT: Do NOT use Arabic content words not in the vocabulary above.
Follow the narrative structure specified above.
Give characters names.
Include full diacritics on ALL Arabic words.

Respond with JSON: {{"title_ar": "...", "title_en": "...", "body_ar": "...", "body_en": "...", "transliteration": "..."}}"""
    return STORY_SYSTEM_PROMPT, user, 0.9


def build_prompt_D_pass1(story_idx: int) -> tuple[str, str, float]:
    """Two-pass: pass 1 — generate freely."""
    structure = EXPANDED_STRUCTURES[story_idx % len(EXPANDED_STRUCTURES)]
    user = f"""Write a compelling mini-story (4-7 sentences) in Arabic for an adult reader.

NARRATIVE STRUCTURE: {structure}

Focus entirely on narrative quality. Make it genuinely interesting.
Give the main character a name.
Include full diacritics on ALL Arabic words.
Write a natural, engaging story — vocabulary choice is completely free.

Respond with JSON: {{"title_ar": "...", "title_en": "...", "body_ar": "...", "body_en": "...", "transliteration": "..."}}"""
    return TWO_PASS_SYSTEM_PASS1, user, 0.9


def build_prompt_D_pass2(original_story: dict, words: list[dict]) -> tuple[str, str, float]:
    """Two-pass: pass 2 — rewrite with vocab constraint."""
    vocab = format_vocab_pos(words)
    user = f"""Rewrite this Arabic story using ONLY the vocabulary provided below.

ORIGINAL STORY:
Title: {original_story.get('title_ar', '')}
Body: {original_story.get('body_ar', '')}
English: {original_story.get('body_en', '')}

ALLOWED VOCABULARY (you may ONLY use these content words, plus function words):
{vocab}

Rewrite the story preserving:
- The narrative arc (setup → development → resolution)
- Character names
- The emotional tone

Replace any content word NOT in the vocabulary with a synonym that IS, or restructure.
Keep it 4-7 sentences. Include full diacritics.

Respond with JSON: {{"title_ar": "...", "title_en": "...", "body_ar": "...", "body_en": "...", "transliteration": "..."}}"""
    return TWO_PASS_SYSTEM_PASS2, user, 0.3


# ---------------------------------------------------------------------------
# Vocabulary compliance analysis
# ---------------------------------------------------------------------------

def analyze_compliance(
    body_ar: str,
    compliance_lookup: dict[str, int],
    function_word_bares: set[str],
    acquiring_ids: set[int],
) -> dict:
    """Analyze vocabulary compliance of a generated story."""
    tokens = tokenize(body_ar)
    content_total = 0
    content_known = 0
    content_acquiring = 0
    content_unknown = 0
    func_count = 0
    unknown_list = []

    for token in tokens:
        bare = strip_diacritics(token)
        bare = strip_tatweel(bare)
        bare = normalize_alef(bare)

        # Function word check
        if bare in function_word_bares:
            func_count += 1
            continue
        # Also check FUNCTION_WORD_FORMS keys
        if bare in {normalize_alef(k) for k in FUNCTION_WORD_FORMS}:
            func_count += 1
            continue

        content_total += 1
        lid = lookup_lemma(bare, compliance_lookup)
        if lid:
            if lid in acquiring_ids:
                content_acquiring += 1
            content_known += 1
        else:
            content_unknown += 1
            if bare not in unknown_list:
                unknown_list.append(bare)

    compliance_pct = round(content_known / content_total * 100, 1) if content_total > 0 else 0
    sentence_count = len(re.split(r'[.\n؟!]', body_ar))
    sentence_count = max(1, len([s for s in re.split(r'[.\n؟!]', body_ar) if s.strip()]))

    return {
        "total_content_words": content_total,
        "known_content_words": content_known,
        "acquiring_content_words": content_acquiring,
        "unknown_content_words": content_unknown,
        "function_word_count": func_count,
        "compliance_pct": compliance_pct,
        "unknown_word_list": unknown_list,
        "word_count": len(tokens),
        "sentence_count": sentence_count,
    }


# ---------------------------------------------------------------------------
# LLM judge
# ---------------------------------------------------------------------------

def judge_story(story: dict, generator_model: str) -> dict:
    """Evaluate story quality using cross-model LLM judge."""
    # Cross-model judging: never let a model judge its own output
    judge_order = ["gemini", "sonnet", "openai"]
    # Remove the generator model from candidates
    candidates = [m for m in judge_order if m != generator_model and get_api_key(m)]
    if not candidates:
        return {"error": "No judge model available"}

    prompt = JUDGE_PROMPT_TEMPLATE.format(
        title_ar=story.get("title_ar", ""),
        title_en=story.get("title_en", ""),
        body_ar=story.get("body_ar", ""),
        body_en=story.get("body_en", ""),
    )

    # Try each judge candidate until one works
    result = None
    judge_model = candidates[0]
    for candidate in candidates:
        result, usage = call_llm(candidate, JUDGE_SYSTEM, prompt, temperature=0.0, timeout=60)
        if result:
            judge_model = candidate
            break

    if not result:
        return {"error": usage.get("error", "all judges failed")}

    dims = ["narrative_arc", "interestingness", "naturalness", "coherence",
            "grammar", "diacritics", "translation"]
    scores = {}
    for d in dims:
        val = result.get(d)
        if isinstance(val, (int, float)) and 1 <= val <= 5:
            scores[d] = val
        else:
            scores[d] = 0

    # Composite score
    weights = {
        "narrative_arc": 0.25, "interestingness": 0.25, "naturalness": 0.20,
        "coherence": 0.10, "grammar": 0.10, "diacritics": 0.05, "translation": 0.05,
    }
    composite = sum(scores.get(d, 0) * w for d, w in weights.items())
    scores["composite"] = round(composite, 2)
    scores["comments"] = result.get("comments", "")
    scores["judge_model"] = judge_model
    return scores


# ---------------------------------------------------------------------------
# Main benchmark loop
# ---------------------------------------------------------------------------

def run_single(
    model_name: str,
    strategy: str,
    story_idx: int,
    vocab: dict,
) -> dict:
    """Generate one story and evaluate it. Returns full result dict."""
    words = vocab["usable_words"]
    result = {
        "id": f"{model_name}_{strategy}_{story_idx}",
        "model": model_name,
        "strategy": strategy,
        "strategy_name": STRATEGIES[strategy],
        "story_idx": story_idx,
    }

    print(f"  [{model_name}] Strategy {strategy} #{story_idx}...", end=" ", flush=True)

    # Generate story
    gen_usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "time_ms": 0}

    if strategy == "D":
        # Two-pass: generate freely, then constrain
        sys1, user1, temp1 = build_prompt_D_pass1(story_idx)
        story_pass1, usage1 = call_llm(model_name, sys1, user1, temp1)
        gen_usage_total["prompt_tokens"] += usage1.get("prompt_tokens", 0)
        gen_usage_total["completion_tokens"] += usage1.get("completion_tokens", 0)
        gen_usage_total["time_ms"] += usage1.get("time_ms", 0)

        if not story_pass1 or not story_pass1.get("body_ar"):
            result["error"] = f"Pass 1 failed: {usage1.get('error', 'empty response')}"
            print("FAIL (pass 1)")
            return result

        result["pass1_body_ar"] = story_pass1.get("body_ar", "")

        # Pass 2: constrain vocabulary (use same model for fair comparison)
        sys2, user2, temp2 = build_prompt_D_pass2(story_pass1, words)
        story, usage2 = call_llm(model_name, sys2, user2, temp2)
        gen_usage_total["prompt_tokens"] += usage2.get("prompt_tokens", 0)
        gen_usage_total["completion_tokens"] += usage2.get("completion_tokens", 0)
        gen_usage_total["time_ms"] += usage2.get("time_ms", 0)
    else:
        # Single-pass strategies
        builders = {"A": build_prompt_A, "B": build_prompt_B, "C": build_prompt_C}
        sys_p, user_p, temp = builders[strategy](words, story_idx)
        story, usage = call_llm(model_name, sys_p, user_p, temp)
        gen_usage_total = usage

    if not story or not story.get("body_ar"):
        result["error"] = gen_usage_total.get("error", "empty response")
        print("FAIL")
        return result

    result["title_ar"] = story.get("title_ar", "")
    result["title_en"] = story.get("title_en", "")
    result["body_ar"] = story.get("body_ar", "")
    result["body_en"] = story.get("body_en", "")
    result["transliteration"] = story.get("transliteration", "")
    result["gen_prompt_tokens"] = gen_usage_total.get("prompt_tokens", 0)
    result["gen_completion_tokens"] = gen_usage_total.get("completion_tokens", 0)
    result["gen_time_ms"] = gen_usage_total.get("time_ms", 0)

    # Estimate cost
    pt = result["gen_prompt_tokens"]
    ct = result["gen_completion_tokens"]
    cost = (pt / 1_000_000 * COST_PER_M_INPUT.get(model_name, 1)) + \
           (ct / 1_000_000 * COST_PER_M_OUTPUT.get(model_name, 1))
    result["gen_cost_usd"] = round(cost, 5)

    # Vocabulary compliance
    compliance = analyze_compliance(
        result["body_ar"],
        vocab["compliance_lookup"],
        vocab["function_word_bares"],
        vocab["acquiring_ids"],
    )
    result.update(compliance)

    comp_pct = compliance["compliance_pct"]
    unk = compliance["unknown_content_words"]
    print(f"compliance={comp_pct}% unknown={unk}", end=" ", flush=True)

    # LLM judge
    scores = judge_story(story, model_name)
    if "error" not in scores:
        result.update(scores)
        print(f"composite={scores['composite']}")
    else:
        result["judge_error"] = scores["error"]
        print(f"judge_error={scores['error']}")

    return result


def run_benchmark(
    db_path: str,
    model_names: list[str],
    strategy_names: list[str],
    count: int,
) -> list[dict]:
    """Run full benchmark. Returns list of result dicts."""
    print(f"Loading vocabulary from {db_path}...")
    vocab = load_vocabulary(db_path)
    n_words = len(vocab["usable_words"])
    n_acq = len(vocab["acquiring_ids"])
    print(f"  Usable words: {n_words} ({n_acq} acquiring)")
    by_state = {}
    for w in vocab["usable_words"]:
        by_state[w["state"]] = by_state.get(w["state"], 0) + 1
    for state, cnt in sorted(by_state.items()):
        print(f"    {state}: {cnt}")

    # Check API keys
    available_models = []
    for m in model_names:
        if m not in BENCHMARK_MODELS:
            print(f"  WARNING: Unknown model '{m}', skipping")
            continue
        if get_api_key(m):
            available_models.append(m)
            print(f"  {m}: OK ({BENCHMARK_MODELS[m]['model']})")
        else:
            print(f"  {m}: NO API KEY, skipping")

    if not available_models:
        print("ERROR: No models available. Set API keys in environment.")
        sys.exit(1)

    total = len(available_models) * len(strategy_names) * count
    print(f"\nRunning {total} stories: {len(available_models)} models × {len(strategy_names)} strategies × {count} each\n")

    results = []
    for strategy in strategy_names:
        if strategy not in STRATEGIES:
            print(f"  WARNING: Unknown strategy '{strategy}', skipping")
            continue
        print(f"Strategy {strategy}: {STRATEGIES[strategy]}")
        for model in available_models:
            for i in range(count):
                result = run_single(model, strategy, i, vocab)
                results.append(result)

    return results


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(results: list[dict], output_dir: Path):
    """Generate JSONL data file and markdown report."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save JSONL
    jsonl_path = output_dir / "all_stories.jsonl"
    with open(jsonl_path, "w") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nSaved {len(results)} stories to {jsonl_path}")

    # Filter successful results
    ok = [r for r in results if "body_ar" in r]
    if not ok:
        print("No successful stories to report on.")
        return

    # --- Build markdown report ---
    lines = ["# Story Generation Benchmark Report", ""]
    lines.append(f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**Stories generated**: {len(ok)} successful / {len(results)} total")
    lines.append(f"**Models**: {', '.join(sorted(set(r['model'] for r in ok)))}")
    lines.append(f"**Strategies**: {', '.join(sorted(set(r['strategy'] for r in ok)))}")
    lines.append("")

    # Summary by model
    lines.append("## Results by Model")
    lines.append("")
    lines.append("| Model | Composite | Narrative | Interest | Natural | Compliance% | Unknown | Cost/story | Time(s) |")
    lines.append("|-------|-----------|-----------|----------|---------|-------------|---------|------------|---------|")

    models_in_results = sorted(set(r["model"] for r in ok))
    for model in models_in_results:
        model_results = [r for r in ok if r["model"] == model]
        n = len(model_results)
        avg = lambda key: sum(r.get(key, 0) for r in model_results) / n if n else 0

        lines.append(
            f"| {model} | {avg('composite'):.2f} | {avg('narrative_arc'):.1f} | "
            f"{avg('interestingness'):.1f} | {avg('naturalness'):.1f} | "
            f"{avg('compliance_pct'):.0f}% | {avg('unknown_content_words'):.1f} | "
            f"${avg('gen_cost_usd'):.4f} | {avg('gen_time_ms')/1000:.1f} |"
        )
    lines.append("")

    # Summary by strategy
    lines.append("## Results by Strategy")
    lines.append("")
    lines.append("| Strategy | Composite | Narrative | Interest | Compliance% | Unknown |")
    lines.append("|----------|-----------|-----------|----------|-------------|---------|")

    strategies_in_results = sorted(set(r["strategy"] for r in ok))
    for strat in strategies_in_results:
        strat_results = [r for r in ok if r["strategy"] == strat]
        n = len(strat_results)
        avg = lambda key: sum(r.get(key, 0) for r in strat_results) / n if n else 0

        lines.append(
            f"| {strat} ({STRATEGIES.get(strat, '')[:30]}) | {avg('composite'):.2f} | "
            f"{avg('narrative_arc'):.1f} | {avg('interestingness'):.1f} | "
            f"{avg('compliance_pct'):.0f}% | {avg('unknown_content_words'):.1f} |"
        )
    lines.append("")

    # Model × Strategy matrix
    lines.append("## Model × Strategy Matrix (Composite Score)")
    lines.append("")
    header = "| Model |"
    sep = "|-------|"
    for s in strategies_in_results:
        header += f" {s} |"
        sep += "------|"
    lines.append(header)
    lines.append(sep)

    for model in models_in_results:
        row = f"| {model} |"
        for strat in strategies_in_results:
            cell_results = [r for r in ok if r["model"] == model and r["strategy"] == strat]
            if cell_results:
                avg_comp = sum(r.get("composite", 0) for r in cell_results) / len(cell_results)
                avg_compl = sum(r.get("compliance_pct", 0) for r in cell_results) / len(cell_results)
                row += f" {avg_comp:.2f} ({avg_compl:.0f}%) |"
            else:
                row += " — |"
        lines.append(row)
    lines.append("")

    # Top stories
    ranked = sorted(ok, key=lambda r: r.get("composite", 0), reverse=True)
    lines.append("## Top 5 Stories")
    lines.append("")
    for i, r in enumerate(ranked[:5], 1):
        lines.append(f"### #{i} — {r['model']} / Strategy {r['strategy']} (composite {r.get('composite', 0):.2f}, compliance {r.get('compliance_pct', 0):.0f}%)")
        lines.append(f"**{r.get('title_en', '')}** ({r.get('title_ar', '')})")
        lines.append("")
        lines.append(f"> {r.get('body_ar', '')}")
        lines.append("")
        lines.append(f"*{r.get('body_en', '')}*")
        lines.append("")
        if r.get("unknown_word_list"):
            lines.append(f"Unknown words: {', '.join(r['unknown_word_list'][:10])}")
        if r.get("comments"):
            lines.append(f"Judge: {r['comments']}")
        lines.append("")

    # Bottom stories
    lines.append("## Bottom 5 Stories")
    lines.append("")
    for i, r in enumerate(ranked[-5:], 1):
        lines.append(f"### #{i} — {r['model']} / Strategy {r['strategy']} (composite {r.get('composite', 0):.2f}, compliance {r.get('compliance_pct', 0):.0f}%)")
        lines.append(f"**{r.get('title_en', '')}** ({r.get('title_ar', '')})")
        lines.append("")
        lines.append(f"> {r.get('body_ar', '')}")
        lines.append("")
        if r.get("unknown_word_list"):
            lines.append(f"Unknown words: {', '.join(r['unknown_word_list'][:10])}")
        if r.get("comments"):
            lines.append(f"Judge: {r['comments']}")
        lines.append("")

    # Cost summary
    lines.append("## Cost Summary")
    lines.append("")
    total_cost = sum(r.get("gen_cost_usd", 0) for r in ok)
    lines.append(f"**Total generation cost**: ${total_cost:.4f}")
    lines.append("")
    for model in models_in_results:
        model_cost = sum(r.get("gen_cost_usd", 0) for r in ok if r["model"] == model)
        model_count = len([r for r in ok if r["model"] == model])
        lines.append(f"- {model}: ${model_cost:.4f} ({model_count} stories, ${model_cost/model_count:.4f}/story)" if model_count else f"- {model}: $0")
    lines.append("")

    # Unknown words analysis
    all_unknown = {}
    for r in ok:
        for w in r.get("unknown_word_list", []):
            all_unknown[w] = all_unknown.get(w, 0) + 1
    if all_unknown:
        lines.append("## Most Common Unknown Words")
        lines.append("")
        for word, cnt in sorted(all_unknown.items(), key=lambda x: -x[1])[:20]:
            lines.append(f"- {word} (appeared in {cnt} stories)")
        lines.append("")

    report_path = output_dir / "benchmark_report.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))
    print(f"Report saved to {report_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Benchmark story generation models × strategies")
    parser.add_argument("--db", required=True, help="Path to SQLite database file")
    parser.add_argument("--models", default="gemini,openai,opus,sonnet",
                        help="Comma-separated model names (default: gemini,openai,opus,sonnet)")
    parser.add_argument("--strategies", default="A,B,C,D",
                        help="Comma-separated strategy names (default: A,B,C,D)")
    parser.add_argument("--count", type=int, default=2,
                        help="Stories per model×strategy combination (default: 2)")
    parser.add_argument("--output", default=None,
                        help="Output directory (default: research/story-benchmark-YYYY-MM-DD/)")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"ERROR: Database not found: {args.db}")
        sys.exit(1)

    model_names = [m.strip() for m in args.models.split(",")]
    strategy_names = [s.strip().upper() for s in args.strategies.split(",")]

    output_dir = Path(args.output) if args.output else (
        Path(__file__).resolve().parent.parent.parent
        / "research"
        / f"story-benchmark-{datetime.now().strftime('%Y-%m-%d')}"
    )

    # Load env from .env if present
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                val = val.strip().strip('"').strip("'")
                if val and key not in os.environ:
                    os.environ[key] = val

    results = run_benchmark(args.db, model_names, strategy_names, args.count)
    generate_report(results, output_dir)

    print("\nDone!")


if __name__ == "__main__":
    main()
