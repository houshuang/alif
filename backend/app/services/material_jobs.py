"""Persistent coordinator queue for bounded material generation work."""

from __future__ import annotations

import fcntl
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import MaterialJob


STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_kinds(kinds: Iterable[str] | str | None) -> list[str] | None:
    if kinds is None:
        return None
    if isinstance(kinds, str):
        return [kinds]
    return list(kinds)


def material_job_lease_lock_path() -> Path:
    return Path(os.environ.get("ALIF_MATERIAL_JOB_LEASE_LOCK", "/tmp/alif-material-job-lease.lock"))


def try_acquire_material_job_lease_lock():
    """Acquire the short critical-section lock used while claiming queue rows."""

    lock_path = material_job_lease_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.write(f"{os.getpid()} {_now().isoformat()}\n")
    handle.flush()
    return handle


def release_material_job_lease_lock(handle) -> None:
    try:
        fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


def release_expired_leases(
    db: Session,
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> int:
    """Return expired running jobs to the queue for another worker attempt."""

    now = now or _now()
    jobs = (
        db.query(MaterialJob)
        .filter(
            MaterialJob.status == STATUS_RUNNING,
            MaterialJob.lease_until.isnot(None),
            MaterialJob.lease_until <= now,
        )
        .all()
    )
    for job in jobs:
        if (job.attempts or 0) >= (job.max_attempts or 1):
            job.status = STATUS_FAILED
            job.completed_at = now
            if not job.last_error:
                job.last_error = "lease expired after max attempts"
        else:
            job.status = STATUS_QUEUED
            job.completed_at = None
        job.lease_owner = None
        job.lease_until = None
        job.updated_at = now
    if jobs and commit:
        db.commit()
    return len(jobs)


def enqueue_material_job(
    db: Session,
    *,
    kind: str,
    payload: dict[str, Any] | None = None,
    priority: int = 100,
    dedupe_key: str | None = None,
    not_before: datetime | None = None,
    max_attempts: int = 3,
    now: datetime | None = None,
    commit: bool = True,
) -> MaterialJob:
    """Enqueue a job, reusing active jobs with the same dedupe key."""

    now = now or _now()
    payload = payload or {}
    if dedupe_key:
        existing = (
            db.query(MaterialJob)
            .filter(
                MaterialJob.kind == kind,
                MaterialJob.dedupe_key == dedupe_key,
                MaterialJob.status.in_(ACTIVE_STATUSES),
            )
            .order_by(MaterialJob.created_at.desc(), MaterialJob.id.desc())
            .first()
        )
        if existing:
            if existing.status == STATUS_QUEUED:
                existing.payload_json = payload
                existing.not_before = not_before
            existing_priority = existing.priority if existing.priority is not None else priority
            existing_max_attempts = existing.max_attempts if existing.max_attempts is not None else max_attempts
            existing.priority = min(existing_priority, priority)
            existing.max_attempts = max(existing_max_attempts, max_attempts)
            existing.updated_at = now
            if commit:
                db.commit()
                db.refresh(existing)
            return existing

    job = MaterialJob(
        kind=kind,
        status=STATUS_QUEUED,
        priority=priority,
        dedupe_key=dedupe_key,
        payload_json=payload,
        attempts=0,
        max_attempts=max_attempts,
        not_before=not_before,
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    if commit:
        db.commit()
        db.refresh(job)
    return job


def lease_material_jobs(
    db: Session,
    *,
    worker_id: str,
    limit: int = 1,
    lease_seconds: int = 1800,
    kinds: Iterable[str] | str | None = None,
    now: datetime | None = None,
    commit: bool = True,
) -> list[MaterialJob]:
    """Lease queued jobs in deterministic priority order."""

    if limit <= 0:
        return []
    now = now or _now()
    expired_count = release_expired_leases(db, now=now, commit=False)

    query = db.query(MaterialJob).filter(
        MaterialJob.status == STATUS_QUEUED,
        MaterialJob.attempts < MaterialJob.max_attempts,
        or_(MaterialJob.not_before.is_(None), MaterialJob.not_before <= now),
    )
    normalized_kinds = _normalize_kinds(kinds)
    if normalized_kinds:
        query = query.filter(MaterialJob.kind.in_(normalized_kinds))

    jobs = (
        query.order_by(
            MaterialJob.priority.asc(),
            MaterialJob.created_at.asc(),
            MaterialJob.id.asc(),
        )
        .limit(limit)
        .all()
    )
    lease_until = now + timedelta(seconds=lease_seconds)
    for job in jobs:
        job.status = STATUS_RUNNING
        job.lease_owner = worker_id
        job.lease_until = lease_until
        job.attempts = (job.attempts or 0) + 1
        job.updated_at = now

    if (jobs or expired_count) and commit:
        db.commit()
        for job in jobs:
            db.refresh(job)
    return jobs


def lease_material_jobs_locked(
    db: Session,
    *,
    worker_id: str,
    limit: int = 1,
    lease_seconds: int = 1800,
    kinds: Iterable[str] | str | None = None,
    now: datetime | None = None,
    commit: bool = True,
) -> list[MaterialJob]:
    """Lease jobs under a short filesystem lock for multi-worker cron runs."""

    lock_handle = try_acquire_material_job_lease_lock()
    if lock_handle is None:
        return []
    try:
        return lease_material_jobs(
            db,
            worker_id=worker_id,
            limit=limit,
            lease_seconds=lease_seconds,
            kinds=kinds,
            now=now,
            commit=commit,
        )
    finally:
        release_material_job_lease_lock(lock_handle)


def complete_material_job(
    db: Session,
    job: MaterialJob,
    *,
    result: dict[str, Any] | None = None,
    now: datetime | None = None,
    commit: bool = True,
) -> MaterialJob:
    now = now or _now()
    job.status = STATUS_DONE
    job.lease_owner = None
    job.lease_until = None
    job.result_json = result or {}
    job.last_error = None
    job.completed_at = now
    job.updated_at = now
    if commit:
        db.commit()
        db.refresh(job)
    return job


def fail_material_job(
    db: Session,
    job: MaterialJob,
    *,
    error: str,
    retry_delay_seconds: int = 900,
    now: datetime | None = None,
    commit: bool = True,
) -> MaterialJob:
    now = now or _now()
    job.lease_owner = None
    job.lease_until = None
    job.last_error = error[:4000]
    job.updated_at = now

    if (job.attempts or 0) >= (job.max_attempts or 1):
        job.status = STATUS_FAILED
        job.completed_at = now
        job.not_before = None
    else:
        job.status = STATUS_QUEUED
        job.not_before = now + timedelta(seconds=retry_delay_seconds)
        job.completed_at = None

    if commit:
        db.commit()
        db.refresh(job)
    return job
