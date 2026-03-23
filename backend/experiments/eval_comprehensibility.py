#!/usr/bin/env python3
"""Evaluation harness for comprehensibility gate experiments.

Runs the 30-day calibrated simulation and outputs a JSON score.
Used by the Karpathy autoresearch loop.

Usage:
    cd backend && python3 experiments/eval_comprehensibility.py
    cd backend && python3 experiments/eval_comprehensibility.py --days 15 --seeds 2
"""

import argparse
import json
import logging
import os
import sys
import time
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ALIF_SKIP_MIGRATIONS"] = "1"
os.environ["TESTING"] = "1"

from app.simulation.db_setup import create_simulation_db, find_latest_backup
from app.simulation.runner import run_simulation
from app.simulation.student import PROFILES

# Mock out all LLM-dependent services that aren't already mocked in runner.py
LLM_MOCKS = [
    patch("app.services.root_enrichment.maybe_enrich_root", return_value=None),
    patch("app.services.pattern_enrichment.maybe_enrich_pattern", return_value=None),
    patch("app.services.memory_hooks.generate_memory_hooks", return_value=None),
    patch("app.services.memory_hooks.regenerate_memory_hooks_premium", return_value=None),
]


def run_eval(days: int = 30, seeds: list[int] | None = None) -> dict:
    """Run simulation with multiple seeds and return aggregated metrics."""
    if seeds is None:
        seeds = [42, 137, 256]

    db_path = find_latest_backup()
    profile = PROFILES["calibrated"]

    all_runs = []
    for seed in seeds:
        engine, SessionFactory, tmp_path = create_simulation_db(db_path)
        db = SessionFactory()
        try:
            # Apply LLM mocks for the entire simulation
            mocks = [m.start() for m in [
                patch("app.services.root_enrichment.maybe_enrich_root", return_value=None),
                patch("app.services.pattern_enrichment.maybe_enrich_pattern", return_value=None),
                patch("app.services.memory_hooks.generate_memory_hooks", return_value=None),
                patch("app.services.memory_hooks.regenerate_memory_hooks_premium", return_value=None),
            ]]
            try:
                snapshots = run_simulation(db, days, profile, seed=seed)
            finally:
                patch.stopall()

            active = [s for s in snapshots if not s.skipped]
            total_understood = sum(s.understood for s in active)
            total_partial = sum(s.partial for s in active)
            total_no_idea = sum(s.no_idea for s in active)
            total_reviews = total_understood + total_partial + total_no_idea
            total_graduated = sum(s.graduated_today for s in active)
            total_items = sum(s.items_received for s in active)
            total_sessions = sum(s.num_sessions for s in active)

            comprehension_rate = total_understood / max(1, total_reviews)
            mean_session_size = total_items / max(1, total_sessions)

            final = snapshots[-1]

            all_runs.append({
                "seed": seed,
                "comprehension_rate": comprehension_rate,
                "words_graduated": total_graduated,
                "mean_session_size": mean_session_size,
                "total_reviews": total_reviews,
                "total_items": total_items,
                "final_acquiring": final.acquiring,
                "final_known": final.known,
                "final_learning": final.learning,
            })
        finally:
            db.close()
            engine.dispose()

    # Aggregate across seeds
    avg = lambda key: sum(r[key] for r in all_runs) / len(all_runs)

    result = {
        "comprehension_rate": avg("comprehension_rate"),
        "words_graduated": avg("words_graduated"),
        "mean_session_size": avg("mean_session_size"),
        "total_reviews": avg("total_reviews"),
        "total_items": avg("total_items"),
        "final_acquiring": avg("final_acquiring"),
        "final_known": avg("final_known"),
        "final_learning": avg("final_learning"),
        "num_seeds": len(seeds),
        "runs": all_runs,
    }
    return result


def compute_score(metrics: dict, baseline: dict | None = None) -> float:
    """Compute composite score.

    Score = comprehension_rate * 0.5
          + (total_items / baseline_items) * 0.3      # practice volume
          + (session_size / baseline_session_size) * 0.2  # session usability

    Without baseline, uses ratios of 1.0.
    """
    cr = metrics["comprehension_rate"]

    if baseline:
        items_ratio = metrics["total_items"] / max(1, baseline["total_items"])
        size_ratio = metrics["mean_session_size"] / max(0.1, baseline["mean_session_size"])
    else:
        items_ratio = 1.0
        size_ratio = 1.0

    return cr * 0.5 + items_ratio * 0.3 + size_ratio * 0.2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--seeds", type=int, default=3, help="Number of seeds to average over")
    parser.add_argument("--baseline", type=str, help="Path to baseline JSON for relative scoring")
    parser.add_argument("--output", type=str, default="experiments/results/latest.json",
                        help="Write results JSON to this file")
    args = parser.parse_args()

    # Suppress ALL noisy logging (litellm, sqlalchemy warnings, etc.)
    logging.basicConfig(level=logging.CRITICAL)
    import warnings
    warnings.filterwarnings("ignore")
    # Silence litellm completely
    logging.getLogger("LiteLLM").setLevel(logging.CRITICAL)
    logging.getLogger("litellm").setLevel(logging.CRITICAL)

    seeds = list(range(42, 42 + args.seeds))

    t0 = time.time()
    metrics = run_eval(days=args.days, seeds=seeds)
    elapsed = time.time() - t0

    baseline = None
    if args.baseline and os.path.exists(args.baseline):
        with open(args.baseline) as f:
            baseline = json.load(f)

    score = compute_score(metrics, baseline)
    metrics["composite_score"] = score
    metrics["elapsed_seconds"] = round(elapsed, 1)

    # Write to file (avoids stdout contamination from litellm)
    out_path = args.output
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(metrics, f, indent=2)

    # Also print summary to stderr (clean)
    summary = (
        f"score={score:.4f} | "
        f"comprehension={metrics['comprehension_rate']:.3f} | "
        f"graduated={metrics['words_graduated']:.0f} | "
        f"session_size={metrics['mean_session_size']:.1f} | "
        f"elapsed={elapsed:.0f}s"
    )
    sys.stderr.write(summary + "\n")


if __name__ == "__main__":
    main()
