#!/usr/bin/env python3
"""Plan bounded material generation jobs without running Claude.

RETIRED from the production cron 2026-06-16 (no longer invoked by
deploy/alif-update-material.sh). The queue never drained — a rescue-word flood
starved everything else — while warm_sentence_cache does the bulk generation and
refill_due_deficit.py covers the deficit hole. The MaterialJob table and these
scripts are kept dormant/reusable. See research/experiment-log.md 2026-06-16.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from app.services.material_generator import (
    _release_material_update_lock,
    _try_acquire_material_update_lock,
)
from app.services.material_job_planner import (
    DEFAULT_MAX_JOBS,
    DEFAULT_SENTENCE_BUDGET,
    DEFAULT_SHARD_SIZE,
    enqueue_sentence_shards,
    plan_sentence_shards,
)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without enqueueing jobs")
    parser.add_argument("--max-sentences", type=int, default=2000, help="Absolute active sentence cap")
    parser.add_argument(
        "--sentence-budget",
        type=int,
        default=_env_int("ALIF_STEP_A_SENTENCE_BUDGET", DEFAULT_SENTENCE_BUDGET),
        help="Maximum planned sentences this planner run may enqueue",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=_env_int("ALIF_MATERIAL_MAX_JOBS", DEFAULT_MAX_JOBS),
        help="Maximum sentence shard jobs to enqueue",
    )
    parser.add_argument(
        "--shard-size",
        type=int,
        default=_env_int("ALIF_MATERIAL_JOB_SHARD_SIZE", DEFAULT_SHARD_SIZE),
        help="Target lemmas per Claude batch job",
    )
    parser.add_argument(
        "--count-per-word",
        type=int,
        default=_env_int("ALIF_MATERIAL_JOB_COUNT_PER_WORD", 1),
        help="Requested sentences per lemma in each shard",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable plan summary",
    )
    parser.add_argument("--no-lock", action="store_true", help="Do not acquire the material update lock")
    return parser.parse_args()


def _summary(plan, job_ids: list[int] | None = None) -> dict:
    return {
        "active_sentences": plan.total_active_sentences,
        "capacity": plan.capacity,
        "budget": plan.budget,
        "planned_sentences": plan.planned_sentences,
        "shards": len(plan.shards),
        "tier_counts": plan.tier_counts,
        "rescue_gaps": plan.rescue_gaps,
        "skipped_backoff": plan.skipped_backoff,
        "job_ids": job_ids or [],
        "shard_payloads": [
            {
                "dedupe_key": shard.dedupe_key,
                "priority": shard.priority,
                "lemma_ids": shard.lemma_ids,
                "planned_sentences": shard.planned_sentences,
            }
            for shard in plan.shards
        ],
    }


def main() -> int:
    args = parse_args()
    lock_handle = None
    if not args.dry_run and not args.no_lock:
        lock_handle = _try_acquire_material_update_lock()
        if lock_handle is None:
            print("Another material update is active; skipping material job planning.")
            return 0

    db = SessionLocal()
    try:
        plan = plan_sentence_shards(
            db,
            max_sentences=args.max_sentences,
            sentence_budget=args.sentence_budget,
            max_jobs=args.max_jobs,
            shard_size=args.shard_size,
            count_per_word=args.count_per_word,
            now=datetime.now(timezone.utc),
        )
        job_ids: list[int] = []
        if not args.dry_run:
            job_ids = enqueue_sentence_shards(db, plan)

        if args.json:
            print(json.dumps(_summary(plan, job_ids), ensure_ascii=False, indent=2))
        else:
            print("Material job plan")
            print(f"  Active sentences: {plan.total_active_sentences}")
            print(f"  Capacity: {plan.capacity}")
            print(f"  Budget: {plan.budget}")
            print(
                "  Tier distribution: "
                f"T1={plan.tier_counts.get(1, 0)} "
                f"T2={plan.tier_counts.get(2, 0)} "
                f"T3={plan.tier_counts.get(3, 0)} "
                f"T4={plan.tier_counts.get(4, 0)}"
            )
            print(f"  Acquiring rescue gaps: {plan.rescue_gaps}")
            print(f"  Skipped in backoff: {plan.skipped_backoff}")
            print(f"  Planned shards: {len(plan.shards)} ({plan.planned_sentences} sentences)")
            if args.dry_run:
                print("  Dry run: no jobs enqueued")
            else:
                print(f"  Enqueued jobs: {job_ids}")
            for shard in plan.shards:
                print(
                    "    "
                    f"priority={shard.priority} planned={shard.planned_sentences} "
                    f"lemmas={','.join(str(lid) for lid in shard.lemma_ids)}"
                )
        return 0
    finally:
        db.close()
        if lock_handle is not None:
            _release_material_update_lock(lock_handle)


if __name__ == "__main__":
    raise SystemExit(main())
