#!/usr/bin/env python3
"""Audit LLM API usage from call logs.

Parses llm_calls_*.jsonl and sentence_gen_*.jsonl to produce:
- Calls by model (success/fail rate)
- Calls by task_type (if tagged)
- Estimated costs by model
- Daily volume trends
- Prompt length distribution (task type inference for untagged calls)

Usage:
    python3 scripts/audit_llm_usage.py                    # default: backend/data/logs/
    python3 scripts/audit_llm_usage.py --log-dir /path    # custom log dir
    python3 scripts/audit_llm_usage.py --days 7           # last N days only
"""

import argparse
import collections
import glob
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Approximate pricing per 1M input tokens (USD)
INPUT_PRICING = {
    "gemini/gemini-3-flash-preview": 0.075,
    "gpt-5.2": 2.50,
    "claude-haiku-4-5": 0.80,
    "claude-opus-4-6": 15.0,
    "claude-sonnet-4-5-20250929": 3.0,
}

# Output pricing is typically higher; use multiplier for estimation
OUTPUT_MULTIPLIER = {
    "gemini/gemini-3-flash-preview": 4.0,  # $0.30/$0.075
    "gpt-5.2": 4.0,
    "claude-haiku-4-5": 5.0,  # $4.0/$0.80
    "claude-opus-4-6": 5.0,  # $75/$15
    "claude-sonnet-4-5-20250929": 5.0,
}

CHARS_PER_TOKEN = 4  # rough estimate for mixed Arabic/English


def parse_logs(log_dir: str, days: int | None = None) -> list[dict]:
    """Parse all llm_calls log files, optionally filtering by recency."""
    entries = []
    pattern = os.path.join(log_dir, "llm_calls_*.jsonl")
    files = sorted(glob.glob(pattern))

    if days:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        files = [f for f in files if os.path.basename(f).replace("llm_calls_", "").replace(".jsonl", "") >= cutoff]

    for f in files:
        with open(f) as fh:
            for line in fh:
                try:
                    entries.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    pass
    return entries


def infer_task_type(entry: dict) -> str:
    """Infer task type from prompt_length and model when task_type is not set."""
    if entry.get("task_type"):
        return entry["task_type"]

    model = entry.get("model", "")
    plen = entry.get("prompt_length", 0)

    # Haiku is used for quality_review and memory_hooks
    if "haiku" in model:
        if plen > 500:
            return "quality_review*"
        return "memory_hooks*"

    # Opus is story generation
    if "opus" in model:
        return "story_gen*"

    # GPT-5.2 is flag evaluation
    if "gpt-5" in model:
        return "flag_evaluation*"

    # Gemini by prompt length
    if "gemini" in model:
        if plen < 500:
            return "enrichment*"
        elif plen < 2000:
            return "enrichment_or_review*"
        elif plen < 4000:
            return "sentence_gen*"
        elif plen < 8000:
            return "sentence_gen*"
        else:
            return "sentence_gen_multi*"

    return "unknown"


def estimate_cost(model: str, prompt_chars: int) -> float:
    """Estimate total cost (input + output) for a single call."""
    tokens = prompt_chars / CHARS_PER_TOKEN
    price_per_m = INPUT_PRICING.get(model, 0.10)
    output_mult = OUTPUT_MULTIPLIER.get(model, 3.0)
    input_cost = tokens * price_per_m / 1_000_000
    # Assume output is ~50% of input tokens on average
    output_cost = (tokens * 0.5) * (price_per_m * output_mult) / 1_000_000
    return input_cost + output_cost


def main():
    parser = argparse.ArgumentParser(description="Audit LLM API usage")
    parser.add_argument("--log-dir", default=None, help="Log directory (default: backend/data/logs/)")
    parser.add_argument("--days", type=int, default=None, help="Only analyze last N days")
    args = parser.parse_args()

    log_dir = args.log_dir
    if not log_dir:
        # Try to find log dir relative to script location
        script_dir = Path(__file__).resolve().parent
        candidates = [
            script_dir.parent / "data" / "logs",
            Path("/app/data/logs"),  # Docker path
        ]
        for c in candidates:
            if c.exists():
                log_dir = str(c)
                break
        if not log_dir:
            print("ERROR: No log directory found. Use --log-dir.", file=sys.stderr)
            sys.exit(1)

    entries = parse_logs(log_dir, args.days)
    if not entries:
        print(f"No log entries found in {log_dir}")
        sys.exit(0)

    # Date range
    dates = sorted(set(e.get("ts", "")[:10] for e in entries if e.get("ts")))
    print("=" * 70)
    print(f"LLM Usage Audit — {len(entries)} calls from {dates[0]} to {dates[-1]}")
    print(f"Log directory: {log_dir}")
    print("=" * 70)

    # === By Model ===
    print("\n--- Calls by Model ---")
    model_stats: dict[str, dict] = {}
    for e in entries:
        model = e.get("model", "?")
        if model not in model_stats:
            model_stats[model] = {"ok": 0, "fail": 0, "chars": 0, "cost": 0.0}
        s = model_stats[model]
        if e.get("success"):
            s["ok"] += 1
        else:
            s["fail"] += 1
        plen = e.get("prompt_length", 0)
        s["chars"] += plen
        if e.get("success"):
            s["cost"] += estimate_cost(model, plen)

    total_cost = 0
    for model in sorted(model_stats, key=lambda m: model_stats[m]["ok"] + model_stats[m]["fail"], reverse=True):
        s = model_stats[model]
        total = s["ok"] + s["fail"]
        rate = s["ok"] / total * 100 if total else 0
        total_cost += s["cost"]
        print(f"  {model:42s}  total={total:>5}  ok={s['ok']:>5}  fail={s['fail']:>3}  rate={rate:5.1f}%  est_cost=${s['cost']:.3f}")
    print(f"\n  TOTAL: {len(entries)} calls, estimated cost ${total_cost:.3f}")
    num_days = len(dates)
    if num_days > 1:
        print(f"  Average: ${total_cost / num_days:.3f}/day over {num_days} days")

    # === By Task Type ===
    print("\n--- Calls by Task Type ---")
    print("  (* = inferred from prompt_length/model, not explicitly tagged)")
    task_stats: dict[str, dict] = {}
    for e in entries:
        if not e.get("success"):
            continue
        task = infer_task_type(e)
        if task not in task_stats:
            task_stats[task] = {"count": 0, "cost": 0.0, "avg_prompt": 0, "total_chars": 0}
        t = task_stats[task]
        t["count"] += 1
        plen = e.get("prompt_length", 0)
        t["total_chars"] += plen
        t["cost"] += estimate_cost(e.get("model", ""), plen)

    for task in sorted(task_stats, key=lambda t: task_stats[t]["count"], reverse=True):
        t = task_stats[task]
        avg_p = t["total_chars"] / t["count"] if t["count"] else 0
        print(f"  {task:42s}  calls={t['count']:>5}  est_cost=${t['cost']:.3f}  avg_prompt={avg_p:.0f} chars")

    # === Daily Volume ===
    print("\n--- Daily Volume ---")
    daily = collections.Counter()
    daily_cost: dict[str, float] = collections.defaultdict(float)
    for e in entries:
        day = e.get("ts", "")[:10]
        daily[day] += 1
        if e.get("success"):
            daily_cost[day] += estimate_cost(e.get("model", ""), e.get("prompt_length", 0))

    for day in sorted(daily):
        count = daily[day]
        cost = daily_cost.get(day, 0)
        bar = "#" * (count // 40)
        print(f"  {day}  {count:>5}  ${cost:.3f}  {bar}")

    # === Failure Analysis ===
    failures = [e for e in entries if not e.get("success")]
    if failures:
        print(f"\n--- Failure Analysis ({len(failures)} failures) ---")
        err_counts = collections.Counter()
        for e in failures:
            err = e.get("error", "unknown")
            # Truncate for grouping
            short_err = err[:80] if len(err) > 80 else err
            err_counts[f"{e.get('model', '?')}: {short_err}"] += 1
        for err, count in err_counts.most_common(10):
            print(f"  {count:>4}x  {err}")

    # === Recommendations ===
    print("\n--- Recommendations ---")

    # Check for expensive fallback models
    gpt_stats = model_stats.get("gpt-5.2", {})
    if gpt_stats.get("ok", 0) > 0:
        gpt_cost = gpt_stats.get("cost", 0)
        gemini_cost = model_stats.get("gemini/gemini-3-flash-preview", {}).get("cost", 0)
        if gpt_cost > gemini_cost * 0.5:
            print(f"  [!] GPT-5.2 costs ${gpt_cost:.3f} ({gpt_stats['ok']} calls) — consider replacing with Claude Code CLI")

    # Check for high-volume enrichment
    enrichment_count = sum(t["count"] for task, t in task_stats.items() if "enrichment" in task)
    if enrichment_count > 100:
        print(f"  [!] {enrichment_count} enrichment calls — good candidate for Claude Code batch agent")

    # Check quality review volume
    review_count = sum(t["count"] for task, t in task_stats.items() if "review" in task or "quality" in task)
    if review_count > 50:
        print(f"  [!] {review_count} quality review calls — could use Claude Haiku via CLI instead of API")

    print()


if __name__ == "__main__":
    main()
