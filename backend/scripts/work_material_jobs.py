#!/usr/bin/env python3
"""Lease and execute bounded material generation jobs."""

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
from app.services.material_jobs import STATUS_QUEUED, lease_material_jobs


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
    return parser.parse_args()


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def main() -> int:
    args = parse_args()
    worker_id = args.worker_id or _default_worker_id()
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

        if not args.no_lock:
            lock_handle = _try_acquire_material_update_lock()
            if lock_handle is None:
                print("Another material update is active; skipping material job worker.")
                return 0

        jobs = lease_material_jobs(
            db,
            worker_id=worker_id,
            kinds=[KIND_SENTENCE_SHARD],
            limit=args.max_jobs,
            lease_seconds=args.lease_seconds,
        )
        if not jobs:
            print("No queued material jobs.")
            return 0

        print(f"Leased {len(jobs)} material job(s) as {worker_id}")
        for job in jobs:
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
        return 0
    finally:
        db.close()
        if lock_handle is not None:
            _release_material_update_lock(lock_handle)


if __name__ == "__main__":
    raise SystemExit(main())
