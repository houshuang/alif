#!/usr/bin/env python3
"""LLM benchmarking script for Arabic language tasks.

Compares model quality across diacritization, translation, transliteration,
sentence generation, and grammar tagging using ground truth data.

Usage:
    python scripts/benchmark_llm.py --task all
    python scripts/benchmark_llm.py --task diacritization --models gemini,anthropic
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.llm import MODELS, AllProvidersFailed, generate_completion, _get_api_key
from app.services.sentence_validator import (
    strip_diacritics,
    normalize_arabic,
    validate_sentence,
)

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_FILE = SCRIPT_DIR / "benchmark_data.json"
RESULTS_DIR = Path(__file__).resolve().parent.parent / "data"

ARABIC_DIACRITICS = re.compile(
    "[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC"
    "\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]"
)

COST_PER_1K_INPUT = {
    "gemini": 0.00010,
    "openai": 0.00015,
    "anthropic": 0.00025,
}
COST_PER_1K_OUTPUT = {
    "gemini": 0.00040,
    "openai": 0.00060,
    "anthropic": 0.00125,
}


def load_data() -> dict:
    with open(DATA_FILE) as f:
        return json.load(f)


def available_models(filter_names: list[str] | None = None) -> list[dict]:
    models = []
    for m in MODELS:
        if filter_names and m["name"] not in filter_names:
            continue
        if _get_api_key(m):
            models.append(m)
    return models


def call_model(model_name: str, prompt: str, system_prompt: str = "", json_mode: bool = True) -> dict | None:
    try:
        return generate_completion(
            prompt=prompt,
            system_prompt=system_prompt,
            json_mode=json_mode,
            temperature=0.1,
            timeout=30,
            model_override=model_name,
        )
    except (AllProvidersFailed, Exception) as e:
        print(f"    [{model_name}] Error: {e}")
        return None


# --- Diacritization ---

def compute_der(gold: str, predicted: str) -> float:
    """Diacritic Error Rate: fraction of characters where diacritics differ.

    Compares base characters aligned, only counting diacritic differences.
    """
    gold_base = strip_diacritics(gold).replace(" ", "")
    pred_base = strip_diacritics(predicted).replace(" ", "")

    if gold_base != pred_base:
        # Base text mismatch — do character-level alignment
        pass

    gold_chars = list(gold.replace(" ", ""))
    pred_chars = list(predicted.replace(" ", ""))

    # Extract diacritics following each base character
    def extract_diacritic_map(chars: list[str]) -> list[tuple[str, str]]:
        result = []
        i = 0
        while i < len(chars):
            if not ARABIC_DIACRITICS.match(chars[i]):
                base = chars[i]
                diacritics = ""
                i += 1
                while i < len(chars) and ARABIC_DIACRITICS.match(chars[i]):
                    diacritics += chars[i]
                    i += 1
                result.append((base, diacritics))
            else:
                i += 1
        return result

    gold_map = extract_diacritic_map(gold_chars)
    pred_map = extract_diacritic_map(pred_chars)

    if not gold_map:
        return 0.0

    total = len(gold_map)
    errors = 0
    for i in range(min(len(gold_map), len(pred_map))):
        if gold_map[i][1] != pred_map[i][1]:
            errors += 1
    errors += abs(len(gold_map) - len(pred_map))

    return errors / total


def benchmark_diacritization(models: list[dict], data: list[dict]) -> dict:
    results = {}
    for m in models:
        model_name = m["name"]
        scores = []
        total_time = 0.0
        print(f"  [{model_name}] Diacritization ({len(data)} cases)...")
        for case in data:
            prompt = (
                f'Add full Arabic diacritics (tashkeel) to this text. '
                f'Return JSON: {{"diacritized": "..."}}\n\n'
                f'Text: {case["bare"]}'
            )
            start = time.time()
            resp = call_model(model_name, prompt)
            elapsed = time.time() - start
            total_time += elapsed

            if resp and "diacritized" in resp:
                der = compute_der(case["gold"], resp["diacritized"])
                scores.append(der)
            else:
                scores.append(1.0)

        avg_der = sum(scores) / len(scores) if scores else 1.0
        results[model_name] = {
            "avg_der": round(avg_der, 4),
            "accuracy": round(1.0 - avg_der, 4),
            "total_time_s": round(total_time, 1),
            "cases": len(data),
        }
    return results


# --- Translation ---

JUDGE_SYSTEM = (
    "You are an Arabic-English translation quality judge. "
    "Score the translation 0-3:\n"
    "0 = Wrong or incomprehensible\n"
    "1 = Partially correct, major meaning lost\n"
    "2 = Mostly correct, minor issues\n"
    "3 = Fully correct and natural\n"
    'Respond with JSON: {"score": N, "reason": "..."}'
)


def benchmark_translation(models: list[dict], data: list[dict]) -> dict:
    results = {}
    for m in models:
        model_name = m["name"]
        scores = []
        key_term_hits = []
        total_time = 0.0
        print(f"  [{model_name}] Translation ({len(data)} cases)...")
        for case in data:
            prompt = (
                f'Translate this Arabic sentence to English. '
                f'Return JSON: {{"translation": "..."}}\n\n'
                f'Arabic: {case["arabic"]}'
            )
            start = time.time()
            resp = call_model(model_name, prompt)
            elapsed = time.time() - start
            total_time += elapsed

            if resp and "translation" in resp:
                translation = resp["translation"]

                # Key term check
                hit = sum(
                    1 for term in case["key_terms"]
                    if term.lower() in translation.lower()
                )
                key_term_hits.append(hit / len(case["key_terms"]))

                # LLM-as-judge scoring (use first available model)
                judge_prompt = (
                    f"Arabic source: {case['arabic']}\n"
                    f"Reference translation: {case['reference']}\n"
                    f"Model translation: {translation}\n"
                    f"Key terms that should appear: {', '.join(case['key_terms'])}\n\n"
                    f"Score the model translation 0-3."
                )
                judge_resp = call_model(model_name, judge_prompt, system_prompt=JUDGE_SYSTEM)
                if judge_resp and "score" in judge_resp:
                    scores.append(judge_resp["score"])
                else:
                    scores.append(0)
            else:
                scores.append(0)
                key_term_hits.append(0.0)

        avg_score = sum(scores) / len(scores) if scores else 0
        avg_key = sum(key_term_hits) / len(key_term_hits) if key_term_hits else 0
        results[model_name] = {
            "avg_score_0_3": round(avg_score, 2),
            "key_term_recall": round(avg_key, 4),
            "total_time_s": round(total_time, 1),
            "cases": len(data),
        }
    return results


# --- Transliteration ---

def levenshtein(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def normalize_translit(text: str) -> str:
    """Normalize transliteration for comparison: lowercase, collapse whitespace."""
    text = text.lower().strip()
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"\s+", " ", text)
    return text


def benchmark_transliteration(models: list[dict], data: list[dict]) -> dict:
    results = {}
    for m in models:
        model_name = m["name"]
        exact_matches = 0
        distances = []
        total_time = 0.0
        print(f"  [{model_name}] Transliteration ({len(data)} cases)...")
        for case in data:
            prompt = (
                f'Transliterate this Arabic word to the ALA-LC romanization standard '
                f'(Library of Congress). Use macrons for long vowels (ā, ī, ū), '
                f'dots under emphatics (ṭ, ṣ, ḍ, ẓ), and ʿ for ayn, ʾ for hamza. '
                f'Return JSON: {{"transliteration": "..."}}\n\n'
                f'Arabic: {case["arabic"]}'
            )
            start = time.time()
            resp = call_model(model_name, prompt)
            elapsed = time.time() - start
            total_time += elapsed

            if resp and "transliteration" in resp:
                predicted = normalize_translit(resp["transliteration"])
                gold = normalize_translit(case["ala_lc"])
                if predicted == gold:
                    exact_matches += 1
                    distances.append(0)
                else:
                    distances.append(levenshtein(predicted, gold))
            else:
                distances.append(len(case["ala_lc"]))

        avg_dist = sum(distances) / len(distances) if distances else 999
        results[model_name] = {
            "exact_match_rate": round(exact_matches / len(data), 4) if data else 0,
            "avg_levenshtein": round(avg_dist, 2),
            "total_time_s": round(total_time, 1),
            "cases": len(data),
        }
    return results


# --- Sentence Generation ---

def benchmark_sentence_generation(models: list[dict], data: list[dict]) -> dict:
    results = {}
    for m in models:
        model_name = m["name"]
        passed = 0
        target_found_count = 0
        total_time = 0.0
        print(f"  [{model_name}] Sentence Generation ({len(data)} cases)...")
        for case in data:
            known_list = "\n".join(
                f"- {w['arabic']} ({w['english']})" for w in case["known_words"]
            )
            prompt = (
                f"Create a short MSA Arabic sentence (5-10 words) using this target word.\n\n"
                f"TARGET WORD: {case['target_word']} ({case['target_translation']})\n\n"
                f"KNOWN WORDS (you may use these):\n{known_list}\n\n"
                f"Do NOT use content words outside these lists (function words are fine).\n"
                f"Include full diacritics on all Arabic text.\n\n"
                f'Return JSON: {{"arabic": "...", "english": "...", "transliteration": "..."}}'
            )
            system = (
                "You are an Arabic language tutor. Create natural MSA sentences "
                "using only the provided vocabulary plus common function words."
            )
            start = time.time()
            resp = call_model(model_name, prompt, system_prompt=system)
            elapsed = time.time() - start
            total_time += elapsed

            if resp and "arabic" in resp:
                arabic_text = resp["arabic"]
                target_bare = strip_diacritics(case["target_word"])
                known_bare = {strip_diacritics(w["arabic"]) for w in case["known_words"]}

                result = validate_sentence(arabic_text, target_bare, known_bare)
                if result.target_found:
                    target_found_count += 1
                if result.valid:
                    passed += 1

        results[model_name] = {
            "validation_pass_rate": round(passed / len(data), 4) if data else 0,
            "target_found_rate": round(target_found_count / len(data), 4) if data else 0,
            "total_time_s": round(total_time, 1),
            "cases": len(data),
        }
    return results


# --- Grammar Tagging ---

POS_ALIASES = {
    "DEM": {"DET", "PRON", "DEM"},
    "PART": {"PART", "ADV"},
    "PROPN": {"PROPN", "NOUN"},
}


def benchmark_grammar_tagging(models: list[dict], data: list[dict]) -> dict:
    results = {}
    for m in models:
        model_name = m["name"]
        pos_correct = 0
        pos_total = 0
        feature_correct = 0
        feature_total = 0
        total_time = 0.0
        print(f"  [{model_name}] Grammar Tagging ({len(data)} cases)...")
        for case in data:
            prompt = (
                f"Analyze each word in this Arabic sentence. For each word provide:\n"
                f"- pos: part of speech (NOUN, VERB, ADJ, PREP, PRON, DEM, PART, PROPN, CONJ)\n"
                f"- features: grammatical features (case, definiteness, gender, number, tense, person)\n\n"
                f"Sentence: {case['arabic']}\n\n"
                f'Return JSON: {{"words": [{{"word": "...", "pos": "...", "features": {{...}}}}]}}'
            )
            start = time.time()
            resp = call_model(model_name, prompt)
            elapsed = time.time() - start
            total_time += elapsed

            if resp and "words" in resp:
                pred_words = resp["words"]
                gold_words = case["words"]

                for i, gw in enumerate(gold_words):
                    pos_total += 1
                    if i < len(pred_words):
                        pw = pred_words[i]
                        pred_pos = pw.get("pos", "").upper()
                        gold_pos = gw["pos"].upper()

                        # Allow aliases
                        aliases = POS_ALIASES.get(gold_pos, {gold_pos})
                        if pred_pos in aliases or pred_pos == gold_pos:
                            pos_correct += 1

                        # Feature comparison
                        for feat_key, feat_val in gw.get("features", {}).items():
                            feature_total += 1
                            pred_features = pw.get("features", {})
                            if str(pred_features.get(feat_key, "")).lower() == str(feat_val).lower():
                                feature_correct += 1
                    else:
                        feature_total += len(gw.get("features", {}))
            else:
                for gw in case["words"]:
                    pos_total += 1
                    feature_total += len(gw.get("features", {}))

        results[model_name] = {
            "pos_accuracy": round(pos_correct / pos_total, 4) if pos_total else 0,
            "feature_accuracy": round(feature_correct / feature_total, 4) if feature_total else 0,
            "total_time_s": round(total_time, 1),
            "cases": len(data),
        }
    return results


# --- Output ---

def print_table(title: str, task_results: dict, metric_keys: list[str]):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")

    header = f"  {'Model':<12}"
    for key in metric_keys:
        header += f" {key:<20}"
    header += f" {'Time (s)':<10}"
    print(header)
    print(f"  {'-' * (len(header) - 2)}")

    for model_name, metrics in task_results.items():
        row = f"  {model_name:<12}"
        for key in metric_keys:
            val = metrics.get(key, "N/A")
            if isinstance(val, float):
                row += f" {val:<20.4f}"
            else:
                row += f" {str(val):<20}"
        row += f" {metrics.get('total_time_s', 'N/A'):<10}"
        print(row)
    print()


def estimate_costs(all_results: dict, data: dict):
    print(f"\n{'=' * 60}")
    print(f"  Cost Projection (per 1000 benchmark runs)")
    print(f"{'=' * 60}")

    task_case_counts = {
        "diacritization": len(data.get("diacritization", [])),
        "translation": len(data.get("translation", [])) * 2,  # includes judge call
        "transliteration": len(data.get("transliteration", [])),
        "sentence_generation": len(data.get("sentence_generation", [])),
        "grammar_tagging": len(data.get("grammar_tagging", [])),
    }

    avg_tokens_per_call = 300  # rough estimate input+output

    for model_name in set().union(*[r.keys() for r in all_results.values()]):
        total_calls = sum(task_case_counts.values())
        input_cost = (total_calls * avg_tokens_per_call / 1000) * COST_PER_1K_INPUT.get(model_name, 0.0002)
        output_cost = (total_calls * avg_tokens_per_call / 1000) * COST_PER_1K_OUTPUT.get(model_name, 0.0006)
        per_run = input_cost + output_cost
        per_1k = per_run * 1000
        print(f"  {model_name:<12} ~${per_run:.4f}/run  ~${per_1k:.2f}/1000 runs")
    print()


def print_recommendations(all_results: dict):
    print(f"\n{'=' * 60}")
    print(f"  Recommended Model Allocation")
    print(f"{'=' * 60}")

    task_primary_metrics = {
        "diacritization": ("accuracy", True),
        "translation": ("avg_score_0_3", True),
        "transliteration": ("exact_match_rate", True),
        "sentence_generation": ("validation_pass_rate", True),
        "grammar_tagging": ("pos_accuracy", True),
    }

    for task, (metric, higher_better) in task_primary_metrics.items():
        if task not in all_results:
            continue
        task_results = all_results[task]
        if not task_results:
            continue

        best_model = None
        best_val = None
        for model_name, metrics in task_results.items():
            val = metrics.get(metric)
            if val is None:
                continue
            if best_val is None or (higher_better and val > best_val) or (not higher_better and val < best_val):
                best_val = val
                best_model = model_name

        if best_model:
            print(f"  {task:<25} -> {best_model:<12} ({metric}={best_val:.4f})")
    print()


TASK_RUNNERS = {
    "diacritization": (
        benchmark_diacritization,
        ["accuracy", "avg_der"],
    ),
    "translation": (
        benchmark_translation,
        ["avg_score_0_3", "key_term_recall"],
    ),
    "transliteration": (
        benchmark_transliteration,
        ["exact_match_rate", "avg_levenshtein"],
    ),
    "sentence_generation": (
        benchmark_sentence_generation,
        ["validation_pass_rate", "target_found_rate"],
    ),
    "grammar_tagging": (
        benchmark_grammar_tagging,
        ["pos_accuracy", "feature_accuracy"],
    ),
}


def main():
    parser = argparse.ArgumentParser(description="Benchmark LLM models on Arabic tasks")
    parser.add_argument(
        "--task",
        default="all",
        help="Task to benchmark: diacritization, translation, transliteration, sentence_generation, grammar_tagging, or all",
    )
    parser.add_argument(
        "--models",
        default=None,
        help="Comma-separated model names to test (default: all configured)",
    )
    args = parser.parse_args()

    data = load_data()
    model_filter = args.models.split(",") if args.models else None
    models = available_models(model_filter)

    if not models:
        print("No models available. Check API keys in .env or environment.")
        sys.exit(1)

    print(f"Models: {', '.join(m['name'] for m in models)}")
    print(f"Task: {args.task}")
    print()

    tasks = list(TASK_RUNNERS.keys()) if args.task == "all" else [args.task]
    all_results = {}

    for task in tasks:
        if task not in TASK_RUNNERS:
            print(f"Unknown task: {task}")
            continue
        if task not in data:
            print(f"No benchmark data for task: {task}")
            continue

        runner, metric_keys = TASK_RUNNERS[task]
        print(f"Running {task}...")
        task_results = runner(models, data[task])
        all_results[task] = task_results
        print_table(task.replace("_", " ").title(), task_results, metric_keys)

    if all_results:
        estimate_costs(all_results, data)
        print_recommendations(all_results)

        # Save results
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_file = RESULTS_DIR / f"benchmark_results_{timestamp}.json"
        with open(out_file, "w") as f:
            json.dump(
                {
                    "timestamp": datetime.now().isoformat(),
                    "models": [m["name"] for m in models],
                    "results": all_results,
                },
                f,
                indent=2,
            )
        print(f"Results saved to {out_file}")


if __name__ == "__main__":
    main()
