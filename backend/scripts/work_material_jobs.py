#!/usr/bin/env python3
"""Lease and execute bounded material generation jobs.

RETIRED from the production cron 2026-06-16 (no longer invoked by
deploy/alif-update-material.sh). See plan_material_jobs.py and
research/experiment-log.md 2026-06-16. Kept dormant/reusable.
"""

import argparse
import os
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from app.models import MaterialJob
from app.services.material_generator import (
    _release_material_update_lock,
    _try_acquire_material_update_lock,
)
from app.services.material_job_planner import KIND_SENTENCE_SHARD
from app.services.material_job_worker import process_material_job
from app.services.material_jobs import STATUS_QUEUED, lease_material_jobs_locked


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    default_legacy_batch = _env_bool(
        "ALIF_MATERIAL_USE_LEGACY_BATCH",
        _env_bool("ALIF_USE_LEGACY_BATCH", True),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print queue candidates without leasing")
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=_env_int("ALIF_MATERIAL_WORKER_MAX_JOBS", 1),
        help="Maximum jobs to lease and execute in this process",
    )
    parser.add_argument(
        "--lease-seconds",
        type=int,
        default=_env_int("ALIF_MATERIAL_JOB_LEASE_SECONDS", 1800),
        help="Lease duration before another worker may reclaim the job",
    )
    parser.add_argument(
        "--retry-delay-seconds",
        type=int,
        default=_env_int("ALIF_MATERIAL_JOB_RETRY_DELAY_SECONDS", 900),
        help="Delay before retrying a failed job",
    )
    parser.add_argument("--model", default=os.environ.get("ALIF_MATERIAL_MODEL", "claude_sonnet"))
    parser.add_argument("--worker-id", default=None)
    parser.add_argument(
        "--no-lock",
        action="store_true",
        help="Do not acquire the legacy material update lock. Use only when legacy cron is disabled.",
    )
    parser.add_argument(
        "--legacy-batch",
        dest="legacy_batch",
        action="store_true",
        default=default_legacy_batch,
        help="Use the legacy generate-then-validate batch path for queued sentence jobs",
    )
    parser.add_argument(
        "--self-correct-batch",
        dest="legacy_batch",
        action="store_false",
        help="Use the tool-enabled self-correct batch path for queued sentence jobs",
    )
    return parser.parse_args()


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def main() -> int:
    args = parse_args()
    worker_id = args.worker_id or _default_worker_id()
    os.environ["ALIF_USE_LEGACY_BATCH"] = "1" if args.legacy_batch else "0"
    db = SessionLocal()
    lock_handle = None
    try:
        if args.dry_run:
            jobs = (
                db.query(MaterialJob)
                .filter(
                    MaterialJob.kind == KIND_SENTENCE_SHARD,
                    MaterialJob.status == STATUS_QUEUED,
                )
                .order_by(MaterialJob.priority.asc(), MaterialJob.created_at.asc())
                .limit(max(1, args.max_jobs))
                .all()
            )
            print(f"Queued sentence shard jobs: {len(jobs)}")
            for job in jobs:
                payload = job.payload_json or {}
                print(
                    f"  job={job.id} priority={job.priority} "
                    f"lemmas={payload.get('lemma_ids') or []} "
                    f"planned={payload.get('planned_sentences')}"
                )
            return 0

        print(
            "Material worker batch mode: "
            f"{'legacy' if args.legacy_batch else 'self-correct'}"
        )

        if not args.no_lock:
            lock_handle = _try_acquire_material_update_lock()
            if lock_handle is None:
                print("Another material update is active; skipping material job worker.")
                return 0

        processed = 0
        while processed < max(0, args.max_jobs):
            jobs = lease_material_jobs_locked(
                db,
                worker_id=worker_id,
                kinds=[KIND_SENTENCE_SHARD],
                limit=1,
                lease_seconds=args.lease_seconds,
            )
            if not jobs:
                if processed == 0:
                    print("No queued material jobs.")
                break
            job = jobs[0]
            payload = job.payload_json or {}
            print(
                f"  Running job={job.id} priority={job.priority} "
                f"lemmas={payload.get('lemma_ids') or []}"
            )
            updated = process_material_job(
                db,
                job,
                model=args.model,
                retry_delay_seconds=args.retry_delay_seconds,
            )
            print(f"    status={updated.status} result={updated.result_json or {}}")
            processed += 1
        if processed:
            print(f"Processed {processed} material job(s) as {worker_id}")
        return 0
    finally:
        db.close()
        if lock_handle is not None:
            _release_material_update_lock(lock_handle)


if __name__ == "__main__":
    raise SystemExit(main())
