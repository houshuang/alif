#!/usr/bin/env python3
"""Benchmark Claude Code CLI (Sonnet/Haiku) vs Gemini Flash for key LLM tasks.

Compares sentence generation quality, forms accuracy, quality gate precision/recall,
and memory hooks quality across models.

Requires: claude CLI installed (for Claude models), API keys in .env (for Gemini/Haiku API).

Usage:
    python3 scripts/benchmark_claude_code.py                           # all tasks
    python3 scripts/benchmark_claude_code.py --tasks sentence_gen      # specific task
    python3 scripts/benchmark_claude_code.py --tasks sentence_gen,forms
    python3 scripts/benchmark_claude_code.py --count 5                 # fewer samples
    python3 scripts/benchmark_claude_code.py --models gemini,sonnet    # specific models
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("TESTING", "1")
# Allow running Claude CLI from within a Claude Code session
os.environ.pop("CLAUDECODE", None)
# Clear SOCKS proxy that breaks local litellm calls
for proxy_var in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
    os.environ.pop(proxy_var, None)

from app.database import SessionLocal
from app.models import Lemma, Sentence, UserLemmaKnowledge
from app.services.claude_code import generate_structured, is_available as claude_available
from app.services.llm import (
    SENTENCE_SYSTEM_PROMPT,
    BATCH_SENTENCE_SYSTEM_PROMPT,
    SentenceResult,
    SentenceReviewResult,
    format_known_words_by_pos,
    generate_completion,
    generate_sentences_batch,
    review_sentences_quality,
)
from app.services.sentence_validator import (
    build_comprehensive_lemma_lookup,
    normalize_alef,
    strip_diacritics,
    validate_sentence,
)

# ── JSON schemas for Claude Code CLI ──────────────────────────────────

SENTENCE_BATCH_SCHEMA = {
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
                },
                "required": ["arabic", "english", "transliteration"],
            },
        }
    },
    "required": ["sentences"],
}

# Schema for multi-word batch: all target words in one call
MULTI_WORD_BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "words": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target_word": {"type": "string"},
                    "sentences": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "arabic": {"type": "string"},
                                "english": {"type": "string"},
                                "transliteration": {"type": "string"},
                            },
                            "required": ["arabic", "english", "transliteration"],
                        },
                    },
                },
                "required": ["target_word", "sentences"],
            },
        }
    },
    "required": ["words"],
}

QUALITY_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "natural": {"type": "boolean"},
                    "translation_correct": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "natural", "translation_correct", "reason"],
            },
        }
    },
    "required": ["reviews"],
}

FORMS_SCHEMA = {
    "type": "object",
    "properties": {
        "plural": {"type": "string"},
        "gender": {"type": "string"},
        "present": {"type": "string"},
        "past_3fs": {"type": "string"},
        "past_3p": {"type": "string"},
        "masdar": {"type": "string"},
        "active_participle": {"type": "string"},
        "passive_participle": {"type": "string"},
        "imperative": {"type": "string"},
        "verb_form": {"type": "string"},
        "feminine": {"type": "string"},
        "elative": {"type": "string"},
    },
    "additionalProperties": False,
}

HOOKS_SCHEMA = {
    "type": "object",
    "properties": {
        "mnemonic": {"type": "string"},
        "cognates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lang": {"type": "string"},
                    "word": {"type": "string"},
                    "note": {"type": "string"},
                },
            },
        },
        "collocations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ar": {"type": "string"},
                    "en": {"type": "string"},
                },
            },
        },
        "usage_context": {"type": "string"},
        "fun_fact": {"type": ["string", "null"]},
    },
    "required": ["mnemonic", "cognates", "collocations", "usage_context"],
}


# ── Model definitions ─────────────────────────────────────────────────

# Models that use generate_completion() (API)
API_MODELS = {
    "gemini": "gemini",
    "haiku_api": "anthropic",
}

# Models that use generate_structured() (Claude Code CLI) — one call per word
CLI_MODELS = {
    "sonnet": "sonnet",
    "haiku_cli": "haiku",
}

# Models that batch ALL words into a single CLI call (amortizes ~30s startup)
CLI_BATCH_MODELS = {
    "sonnet_batch": "sonnet",
    "haiku_batch": "haiku",
}

ALL_MODELS = list(API_MODELS.keys()) + list(CLI_MODELS.keys()) + list(CLI_BATCH_MODELS.keys())


# ── Data loading ──────────────────────────────────────────────────────

def load_test_data(db, count: int = 10) -> dict:
    """Load test vocabulary and sentences from DB using raw SQL to avoid column mismatches."""
    import sqlite3
    db_url = str(db.bind.url).replace("sqlite:///", "")
    conn = sqlite3.connect(db_url)
    conn.row_factory = sqlite3.Row

    # Get known/acquiring words for vocabulary context
    rows = conn.execute("""
        SELECT l.lemma_id, l.lemma_ar, l.lemma_ar_bare, l.gloss_en, l.pos,
               l.forms_json, r.root AS root_ar, l.transliteration_ala_lc,
               ulk.knowledge_state
        FROM lemmas l
        JOIN user_lemma_knowledge ulk ON ulk.lemma_id = l.lemma_id
        LEFT JOIN roots r ON r.root_id = l.root_id
        WHERE ulk.knowledge_state IN ('learning', 'known', 'acquiring')
          AND l.canonical_lemma_id IS NULL
    """).fetchall()

    known_words = [
        {"arabic": r["lemma_ar"], "english": r["gloss_en"] or ""}
        for r in rows
    ]

    # Pick target words (mix of POS, acquiring preferred)
    acquiring = [r for r in rows if r["knowledge_state"] == "acquiring" and r["pos"] in ("noun", "verb", "adj")]
    learning = [r for r in rows if r["knowledge_state"] == "learning" and r["pos"] in ("noun", "verb", "adj")]
    target_pool = acquiring[:count] if len(acquiring) >= count else (acquiring + learning)[:count]

    # Build known bare forms for validator
    lemma_lookup = build_comprehensive_lemma_lookup(db)
    known_bare = set(lemma_lookup.keys())

    # Get existing good sentences for quality gate benchmark
    sent_rows = conn.execute("""
        SELECT arabic_text, english_translation
        FROM sentences
        WHERE is_active = 1 AND source = 'llm'
        ORDER BY id DESC
        LIMIT 20
    """).fetchall()

    conn.close()

    return {
        "known_words": known_words,
        "target_words": [
            {"lemma_id": r["lemma_id"], "arabic": r["lemma_ar"], "english": r["gloss_en"] or "",
             "pos": r["pos"], "bare": r["lemma_ar_bare"], "root_ar": r["root_ar"],
             "transliteration": r["transliteration_ala_lc"]}
            for r in target_pool
        ],
        "known_bare": known_bare,
        "lemma_lookup": lemma_lookup,
        "good_sentences": [
            {"arabic": r["arabic_text"], "english": r["english_translation"] or ""}
            for r in sent_rows
        ],
    }


# ── Benchmark tasks ───────────────────────────────────────────────────

def benchmark_sentence_gen(
    data: dict, models: list[str], count: int = 3,
) -> dict:
    """Benchmark sentence generation across models.

    For each target word, generate 3 sentences with each model,
    then validate with the rule-based validator.

    Batch models (sonnet_batch, haiku_batch) send ALL words in one CLI call.
    """
    results = {m: {"generated": 0, "valid": 0, "errors": 0, "times": [], "sentences": []} for m in models}
    targets = data["target_words"][:count]

    # Separate batch vs per-word models
    per_word_models = [m for m in models if m not in CLI_BATCH_MODELS]
    batch_models = [m for m in models if m in CLI_BATCH_MODELS]

    # Per-word models: one call per target word
    for i, target in enumerate(targets):
        print(f"  [{i+1}/{len(targets)}] {target['arabic']} ({target['english']})")
        target_bare = normalize_alef(strip_diacritics(target["arabic"]))

        for model in per_word_models:
            start = time.time()
            try:
                sentences = _generate_batch(
                    model=model,
                    target_word=target["arabic"],
                    target_translation=target["english"],
                    known_words=data["known_words"],
                    count=3,
                )
                elapsed = time.time() - start
                results[model]["times"].append(elapsed)
                results[model]["generated"] += len(sentences)

                for s in sentences:
                    vr = validate_sentence(s["arabic"], target_bare, data["known_bare"])
                    results[model]["sentences"].append({
                        "target": target["arabic"],
                        "arabic": s["arabic"],
                        "english": s["english"],
                        "valid": vr.valid,
                        "unknown": vr.unknown_words,
                    })
                    if vr.valid:
                        results[model]["valid"] += 1

                status = f"ok ({len(sentences)} sentences, {elapsed:.1f}s)"
            except Exception as e:
                elapsed = time.time() - start
                results[model]["errors"] += 1
                status = f"ERROR: {e}"

            print(f"    {model:12s}: {status}")

    # Batch models: ALL words in one CLI call
    for model in batch_models:
        print(f"\n  {model}: generating for all {len(targets)} words in one call...")
        start = time.time()
        try:
            all_sentences = _generate_multi_word_batch(
                model=model,
                targets=targets,
                known_words=data["known_words"],
                sentences_per_word=3,
            )
            elapsed = time.time() - start
            results[model]["times"].append(elapsed)

            for target_word, sentences in all_sentences.items():
                target_bare = normalize_alef(strip_diacritics(target_word))
                results[model]["generated"] += len(sentences)
                for s in sentences:
                    vr = validate_sentence(s["arabic"], target_bare, data["known_bare"])
                    results[model]["sentences"].append({
                        "target": target_word,
                        "arabic": s["arabic"],
                        "english": s["english"],
                        "valid": vr.valid,
                        "unknown": vr.unknown_words,
                    })
                    if vr.valid:
                        results[model]["valid"] += 1

            total_gen = results[model]["generated"]
            total_valid = results[model]["valid"]
            print(f"    {model:12s}: {total_gen} sentences, {total_valid} valid, {elapsed:.1f}s total")

        except Exception as e:
            elapsed = time.time() - start
            results[model]["errors"] += 1
            print(f"    {model:12s}: ERROR: {e}")

    return results


def _generate_batch(
    model: str,
    target_word: str,
    target_translation: str,
    known_words: list[dict],
    count: int = 3,
) -> list[dict]:
    """Generate a batch of sentences using either API or CLI."""
    if model in API_MODELS:
        # Use existing generate_sentences_batch
        results = generate_sentences_batch(
            target_word=target_word,
            target_translation=target_translation,
            known_words=known_words,
            count=count,
            model_override=API_MODELS[model],
        )
        return [{"arabic": r.arabic, "english": r.english, "transliteration": r.transliteration} for r in results]

    # CLI model — build prompt and call generate_structured
    known_list = format_known_words_by_pos(known_words)
    prompt = f"""Create {count} different natural MSA sentences for a beginner Arabic learner.

TARGET WORD (must appear in every sentence):
- {target_word} ({target_translation})

VOCABULARY (you may ONLY use these Arabic content words, plus function words):
{known_list}

IMPORTANT: Do NOT use any Arabic content words that are not in the list above.
Each sentence should be 6-10 words, with a different structure or context.
Include full diacritics on all Arabic text.
Respond with JSON: {{"sentences": [{{"arabic": "...", "english": "...", "transliteration": "..."}}, ...]}}"""

    result = generate_structured(
        prompt=prompt,
        system_prompt=BATCH_SENTENCE_SYSTEM_PROMPT,
        json_schema=SENTENCE_BATCH_SCHEMA,
        model=CLI_MODELS[model],
    )
    return result.get("sentences", [])


def _generate_multi_word_batch(
    model: str,
    targets: list[dict],
    known_words: list[dict],
    sentences_per_word: int = 3,
) -> dict[str, list[dict]]:
    """Generate sentences for ALL target words in a single CLI call.

    Returns {target_word_arabic: [sentence_dicts]}.
    """
    known_list = format_known_words_by_pos(known_words)

    target_lines = []
    for i, t in enumerate(targets, 1):
        target_lines.append(f"{i}. {t['arabic']} ({t['english']})")
    targets_str = "\n".join(target_lines)

    prompt = f"""Create {sentences_per_word} different natural MSA sentences for EACH of the following target words.
Each sentence must contain its target word. Total: {len(targets)} words × {sentences_per_word} sentences = {len(targets) * sentences_per_word} sentences.

TARGET WORDS (generate {sentences_per_word} sentences for each):
{targets_str}

VOCABULARY (you may ONLY use these Arabic content words, plus the target words, plus function words):
{known_list}

IMPORTANT: Do NOT use any Arabic content words that are not in the list above.
Each sentence should be 6-10 words, with a different structure or context.
Include full diacritics on all Arabic text.

Return JSON with this structure:
{{"words": [{{"target_word": "...", "sentences": [{{"arabic": "...", "english": "...", "transliteration": "..."}}, ...]}}, ...]}}"""

    result = generate_structured(
        prompt=prompt,
        system_prompt=BATCH_SENTENCE_SYSTEM_PROMPT,
        json_schema=MULTI_WORD_BATCH_SCHEMA,
        model=CLI_BATCH_MODELS[model],
        timeout=300,  # longer timeout for multi-word batch
    )

    # Parse into {target_word: [sentences]}
    output: dict[str, list[dict]] = {}
    for word_entry in result.get("words", []):
        tw = word_entry.get("target_word", "")
        # Match to our targets by finding closest Arabic match
        matched_target = tw
        for t in targets:
            if strip_diacritics(t["arabic"]) == strip_diacritics(tw):
                matched_target = t["arabic"]
                break
        output[matched_target] = word_entry.get("sentences", [])

    return output


def benchmark_quality_gate(
    data: dict, models: list[str], count: int = 10,
) -> dict:
    """Benchmark quality gate (sentence review) across models.

    Uses existing good sentences from DB. Each model reviews the same set
    and we compare agreement rates.
    """
    sentences = data["good_sentences"][:count]
    if not sentences:
        print("  No sentences available for quality gate benchmark")
        return {}

    results = {m: {"approved": 0, "rejected": 0, "time": 0, "reviews": []} for m in models}

    quality_system_prompt = (
        "You are an expert Arabic linguist reviewing sentences for a "
        "language learning app. Focus on grammar correctness and translation "
        "accuracy. Accept simple or textbook-style sentences — they are "
        "appropriate for learners. Only reject sentences with clear errors."
    )

    review_prompt = """Review each Arabic sentence for a language learning app. For each:
1. Is the Arabic grammatically correct and comprehensible?
2. Is the English translation accurate?

Only reject sentences with:
- Grammar errors (wrong gender agreement, incorrect verb forms, broken syntax)
- Translation errors (English doesn't match Arabic meaning, singular/plural mismatch)
- Nonsensical or incomprehensible meaning (word salad, contradictory clauses)

Do NOT reject sentences just because:
- The scenario is unusual (a boy putting a book in a kitchen is fine)
- The style is simple or textbook-like (these are for language learners)
- Word choices are slightly informal or formal

Respond with JSON: {"reviews": [{"id": 1, "natural": true/false, "translation_correct": true/false, "reason": "..."}]}

Sentences:
"""
    for i, s in enumerate(sentences, 1):
        review_prompt += f'{i}. Arabic: {s["arabic"]}\n   English: {s["english"]}\n\n'

    for model in models:
        print(f"  {model:12s}: reviewing {len(sentences)} sentences...")
        start = time.time()
        try:
            if model in API_MODELS:
                reviews = review_sentences_quality(sentences)
                for r in reviews:
                    approved = r.natural and r.translation_correct
                    if approved:
                        results[model]["approved"] += 1
                    else:
                        results[model]["rejected"] += 1
                    results[model]["reviews"].append({
                        "natural": r.natural,
                        "correct": r.translation_correct,
                        "reason": r.reason,
                    })
            else:
                result = generate_structured(
                    prompt=review_prompt,
                    system_prompt=quality_system_prompt,
                    json_schema=QUALITY_REVIEW_SCHEMA,
                    model=CLI_MODELS[model],
                )
                for item in result.get("reviews", []):
                    approved = item.get("natural", True) and item.get("translation_correct", True)
                    if approved:
                        results[model]["approved"] += 1
                    else:
                        results[model]["rejected"] += 1
                    results[model]["reviews"].append({
                        "natural": item.get("natural"),
                        "correct": item.get("translation_correct"),
                        "reason": item.get("reason", ""),
                    })

            elapsed = time.time() - start
            results[model]["time"] = elapsed
            print(f"    {results[model]['approved']} approved, {results[model]['rejected']} rejected ({elapsed:.1f}s)")

        except Exception as e:
            elapsed = time.time() - start
            results[model]["time"] = elapsed
            print(f"    ERROR: {e}")

    return results


def benchmark_forms(
    data: dict, models: list[str], count: int = 10,
) -> dict:
    """Benchmark morphological forms generation across models.

    Generates forms for words and compares against existing verified forms_json.
    """
    # Find words WITH existing forms_json for comparison (raw SQL)
    import sqlite3
    db = SessionLocal()
    db_url = str(db.bind.url).replace("sqlite:///", "")
    conn = sqlite3.connect(db_url)
    conn.row_factory = sqlite3.Row
    words_with_forms = conn.execute("""
        SELECT lemma_id, lemma_ar, lemma_ar_bare, gloss_en, pos, forms_json
        FROM lemmas
        WHERE forms_json IS NOT NULL AND forms_json != '{}'
          AND canonical_lemma_id IS NULL
          AND pos IN ('noun', 'verb', 'adj')
        LIMIT ?
    """, (count,)).fetchall()
    conn.close()
    db.close()

    if not words_with_forms:
        print("  No words with existing forms for comparison")
        return {}

    from app.services.lemma_enrichment import FORMS_SYSTEM_PROMPT

    results = {m: {"tested": 0, "matches": 0, "mismatches": 0, "errors": 0, "times": [], "details": []} for m in models}

    for i, lemma in enumerate(words_with_forms):
        existing_forms = json.loads(lemma["forms_json"]) if isinstance(lemma["forms_json"], str) else lemma["forms_json"]
        print(f"  [{i+1}/{len(words_with_forms)}] {lemma['lemma_ar']} ({lemma['gloss_en']}) pos={lemma['pos']}")

        forms_prompt = f"Word: {lemma['lemma_ar']} (bare: {lemma['lemma_ar_bare']})\nPOS: {lemma['pos']}\nMeaning: {lemma['gloss_en']}"

        for model in models:
            start = time.time()
            try:
                if model in API_MODELS:
                    generated = generate_completion(
                        prompt=forms_prompt,
                        system_prompt=FORMS_SYSTEM_PROMPT,
                        json_mode=True,
                        temperature=0.1,
                        model_override=API_MODELS[model],
                        task_type="benchmark_forms",
                    )
                else:
                    generated = generate_structured(
                        prompt=forms_prompt,
                        system_prompt=FORMS_SYSTEM_PROMPT,
                        json_schema=FORMS_SCHEMA,
                        model=CLI_MODELS[model],
                    )

                elapsed = time.time() - start
                results[model]["times"].append(elapsed)
                results[model]["tested"] += 1

                # Compare against existing forms
                matched_keys = 0
                total_keys = 0
                for key in existing_forms:
                    if key in generated:
                        total_keys += 1
                        existing_bare = normalize_alef(strip_diacritics(str(existing_forms[key])))
                        generated_bare = normalize_alef(strip_diacritics(str(generated.get(key, ""))))
                        if existing_bare == generated_bare:
                            matched_keys += 1

                if total_keys > 0:
                    match_rate = matched_keys / total_keys
                    if match_rate >= 0.7:
                        results[model]["matches"] += 1
                    else:
                        results[model]["mismatches"] += 1

                results[model]["details"].append({
                    "word": lemma["lemma_ar"],
                    "existing": existing_forms,
                    "generated": generated,
                    "matched_keys": matched_keys,
                    "total_keys": total_keys,
                })

            except Exception as e:
                elapsed = time.time() - start
                results[model]["errors"] += 1
                results[model]["details"].append({"word": lemma["lemma_ar"], "error": str(e)})

            print(f"    {model:12s}: {elapsed:.1f}s")

    return results


def benchmark_hooks(
    data: dict, models: list[str], count: int = 5,
) -> dict:
    """Benchmark memory hook generation across models.

    Generates hooks and displays them for subjective comparison.
    """
    from app.services.memory_hooks import SYSTEM_PROMPT as HOOKS_SYSTEM_PROMPT

    import sqlite3
    db = SessionLocal()
    db_url = str(db.bind.url).replace("sqlite:///", "")
    conn = sqlite3.connect(db_url)
    conn.row_factory = sqlite3.Row
    words = conn.execute("""
        SELECT l.lemma_id, l.lemma_ar, l.lemma_ar_bare, l.gloss_en, l.pos,
               r.root AS root_ar, l.transliteration_ala_lc
        FROM lemmas l
        LEFT JOIN roots r ON r.root_id = l.root_id
        WHERE r.root IS NOT NULL
          AND l.canonical_lemma_id IS NULL
          AND l.pos IN ('noun', 'verb', 'adj')
        LIMIT ?
    """, (count,)).fetchall()
    conn.close()
    db.close()

    if not words:
        print("  No words with roots for hook benchmark")
        return {}

    results = {m: {"generated": 0, "errors": 0, "times": [], "hooks": []} for m in models}

    for i, lemma in enumerate(words):
        print(f"  [{i+1}/{len(words)}] {lemma['lemma_ar']} ({lemma['gloss_en']})")

        root_info = f", root={lemma['root_ar']}" if lemma["root_ar"] else ""
        hooks_prompt = (
            f"Generate memory hooks for this Arabic word:\n\n"
            f"word={lemma['lemma_ar']}, bare={lemma['lemma_ar_bare']}, "
            f"transliteration={lemma['transliteration_ala_lc'] or 'unknown'}, "
            f"pos={lemma['pos'] or 'unknown'}, meaning=\"{lemma['gloss_en'] or 'unknown'}\"{root_info}\n\n"
            f"Return JSON object with keys: mnemonic, cognates, collocations, usage_context, fun_fact.\n"
            f"Return null (not a JSON object) if the word is a particle/pronoun/function word."
        )

        for model in models:
            start = time.time()
            try:
                if model in API_MODELS:
                    generated = generate_completion(
                        prompt=hooks_prompt,
                        system_prompt=HOOKS_SYSTEM_PROMPT,
                        json_mode=True,
                        temperature=0.7,
                        model_override=API_MODELS[model],
                        task_type="benchmark_hooks",
                    )
                else:
                    generated = generate_structured(
                        prompt=hooks_prompt,
                        system_prompt=HOOKS_SYSTEM_PROMPT,
                        json_schema=HOOKS_SCHEMA,
                        model=CLI_MODELS[model],
                    )

                elapsed = time.time() - start
                results[model]["times"].append(elapsed)
                results[model]["generated"] += 1
                results[model]["hooks"].append({
                    "word": lemma["lemma_ar"],
                    "gloss": lemma["gloss_en"],
                    "mnemonic": generated.get("mnemonic", ""),
                    "cognates": generated.get("cognates", []),
                })

            except Exception as e:
                elapsed = time.time() - start
                results[model]["errors"] += 1
                results[model]["hooks"].append({"word": lemma["lemma_ar"], "error": str(e)})

            print(f"    {model:12s}: {elapsed:.1f}s")

    return results


# ── Reporting ─────────────────────────────────────────────────────────

def print_sentence_report(results: dict):
    """Print sentence generation comparison table."""
    print("\n" + "=" * 70)
    print("SENTENCE GENERATION RESULTS")
    print("=" * 70)
    print(f"{'Model':<16} {'Generated':>10} {'Valid':>8} {'Pass%':>8} {'Errors':>8} {'Total':>8} {'Per Word':>10}")
    print("-" * 78)
    for model, r in results.items():
        pass_rate = (r["valid"] / r["generated"] * 100) if r["generated"] else 0
        total_time = sum(r["times"])
        n_words = max(1, len(set(s["target"] for s in r["sentences"]))) if r["sentences"] else 1
        per_word = total_time / n_words
        print(f"{model:<16} {r['generated']:>10} {r['valid']:>8} {pass_rate:>7.1f}% {r['errors']:>8} {total_time:>7.1f}s {per_word:>9.1f}s")

    # Show invalid sentences per model
    for model, r in results.items():
        invalid = [s for s in r["sentences"] if not s["valid"]]
        if invalid:
            print(f"\n  {model} — invalid sentences:")
            for s in invalid[:5]:
                unknown_str = ", ".join(s["unknown"][:3])
                print(f"    {s['arabic'][:60]}")
                print(f"      unknown: {unknown_str}")


def print_quality_report(results: dict):
    """Print quality gate comparison."""
    print("\n" + "=" * 70)
    print("QUALITY GATE RESULTS (reviewing known-good sentences)")
    print("=" * 70)
    print(f"{'Model':<14} {'Approved':>10} {'Rejected':>10} {'Approval%':>10} {'Time':>8}")
    print("-" * 70)
    for model, r in results.items():
        total = r["approved"] + r["rejected"]
        approval_rate = (r["approved"] / total * 100) if total else 0
        print(f"{model:<14} {r['approved']:>10} {r['rejected']:>10} {approval_rate:>9.1f}% {r['time']:>7.1f}s")


def print_forms_report(results: dict):
    """Print forms generation comparison."""
    print("\n" + "=" * 70)
    print("FORMS GENERATION RESULTS")
    print("=" * 70)
    print(f"{'Model':<14} {'Tested':>8} {'Match':>8} {'Mismatch':>10} {'Errors':>8} {'Avg Time':>10}")
    print("-" * 70)
    for model, r in results.items():
        avg_time = sum(r["times"]) / len(r["times"]) if r["times"] else 0
        print(f"{model:<14} {r['tested']:>8} {r['matches']:>8} {r['mismatches']:>10} {r['errors']:>8} {avg_time:>9.1f}s")


def print_hooks_report(results: dict):
    """Print memory hooks comparison."""
    print("\n" + "=" * 70)
    print("MEMORY HOOKS RESULTS")
    print("=" * 70)
    for model, r in results.items():
        avg_time = sum(r["times"]) / len(r["times"]) if r["times"] else 0
        print(f"\n  {model} ({r['generated']} generated, {r['errors']} errors, avg {avg_time:.1f}s):")
        for hook in r["hooks"]:
            if "error" in hook:
                print(f"    {hook['word']}: ERROR — {hook['error'][:60]}")
            else:
                mnemonic = hook.get("mnemonic", "")[:80]
                cognate_count = len(hook.get("cognates", []))
                print(f"    {hook['word']} ({hook['gloss']}): {mnemonic}...")
                if cognate_count:
                    print(f"      + {cognate_count} cognates")


def save_results(all_results: dict, output_dir: str):
    """Save raw results as JSON for later analysis."""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"benchmark_{timestamp}.json")

    # Convert non-serializable objects
    def make_serializable(obj):
        if isinstance(obj, set):
            return list(obj)
        return str(obj)

    with open(path, "w") as f:
        json.dump(all_results, f, indent=2, default=make_serializable, ensure_ascii=False)
    print(f"\nRaw results saved to: {path}")


# ── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Benchmark Claude Code CLI vs Gemini Flash")
    parser.add_argument("--tasks", default="sentence_gen,quality_gate,forms,hooks",
                        help="Comma-separated tasks (default: all)")
    parser.add_argument("--models", default=None,
                        help=f"Comma-separated models (default: all). Available: {','.join(ALL_MODELS)}")
    parser.add_argument("--count", type=int, default=10,
                        help="Number of test items per task (default: 10)")
    parser.add_argument("--output-dir", default=None,
                        help="Directory for JSON results (default: backend/data/benchmarks/)")
    args = parser.parse_args()

    tasks = args.tasks.split(",")
    models = args.models.split(",") if args.models else ALL_MODELS

    # Validate models
    cli_models_requested = [m for m in models if m in CLI_MODELS or m in CLI_BATCH_MODELS]
    if cli_models_requested and not claude_available():
        print("WARNING: Claude CLI not available. Removing CLI models:", cli_models_requested)
        models = [m for m in models if m not in CLI_MODELS and m not in CLI_BATCH_MODELS]
        if not models:
            print("ERROR: No models available to benchmark.")
            sys.exit(1)

    print("=" * 70)
    print(f"LLM Benchmark — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Tasks: {', '.join(tasks)}")
    print(f"Models: {', '.join(models)}")
    print(f"Count: {args.count} items per task")
    print("=" * 70)

    # Load test data
    print("\nLoading test data from DB...")
    db = SessionLocal()
    try:
        data = load_test_data(db, count=args.count)
    finally:
        db.close()

    print(f"  {len(data['known_words'])} known words")
    print(f"  {len(data['target_words'])} target words")
    print(f"  {len(data['good_sentences'])} good sentences")
    print(f"  {len(data['known_bare'])} known bare forms")

    all_results = {"meta": {"tasks": tasks, "models": models, "count": args.count, "timestamp": datetime.now().isoformat()}}

    # Run benchmarks
    if "sentence_gen" in tasks:
        print("\n--- Sentence Generation ---")
        all_results["sentence_gen"] = benchmark_sentence_gen(data, models, args.count)
        print_sentence_report(all_results["sentence_gen"])

    if "quality_gate" in tasks:
        print("\n--- Quality Gate ---")
        all_results["quality_gate"] = benchmark_quality_gate(data, models, args.count)
        if all_results["quality_gate"]:
            print_quality_report(all_results["quality_gate"])

    if "forms" in tasks:
        print("\n--- Forms Generation ---")
        all_results["forms"] = benchmark_forms(data, models, args.count)
        if all_results["forms"]:
            print_forms_report(all_results["forms"])

    if "hooks" in tasks:
        print("\n--- Memory Hooks ---")
        hook_count = min(args.count, 5)  # hooks are slow, cap at 5
        all_results["hooks"] = benchmark_hooks(data, models, hook_count)
        if all_results["hooks"]:
            print_hooks_report(all_results["hooks"])

    # Save results
    output_dir = args.output_dir or str(Path(__file__).resolve().parent.parent / "data" / "benchmarks")
    save_results(all_results, output_dir)


if __name__ == "__main__":
    main()
