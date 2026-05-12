from datetime import datetime, timedelta, timezone

from app.models import MaterialJob
from app.services.material_jobs import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    complete_material_job,
    enqueue_material_job,
    fail_material_job,
    lease_material_jobs,
    lease_material_jobs_locked,
    release_material_job_lease_lock,
    release_expired_leases,
    try_acquire_material_job_lease_lock,
)


def _now() -> datetime:
    return datetime(2026, 5, 12, 8, 0, tzinfo=timezone.utc)


def test_enqueue_dedupes_active_jobs_but_allows_new_after_done(db_session):
    first = enqueue_material_job(
        db_session,
        kind="sentence_shard",
        payload={"lemma_ids": [1]},
        priority=50,
        dedupe_key="sentence:1",
        now=_now(),
    )

    second = enqueue_material_job(
        db_session,
        kind="sentence_shard",
        payload={"lemma_ids": [1, 2]},
        priority=20,
        dedupe_key="sentence:1",
        now=_now() + timedelta(seconds=1),
    )

    assert second.id == first.id
    assert second.priority == 20
    assert second.payload_json == {"lemma_ids": [1, 2]}
    assert db_session.query(MaterialJob).count() == 1

    complete_material_job(db_session, second, result={"sentences": 3}, now=_now())
    replacement = enqueue_material_job(
        db_session,
        kind="sentence_shard",
        payload={"lemma_ids": [1]},
        dedupe_key="sentence:1",
        now=_now() + timedelta(seconds=2),
    )

    assert replacement.id != first.id
    assert replacement.status == STATUS_QUEUED
    assert db_session.query(MaterialJob).count() == 2


def test_lease_jobs_filters_kind_and_orders_by_priority(db_session):
    low_priority = enqueue_material_job(
        db_session,
        kind="sentence_shard",
        payload={"lemma_ids": [1]},
        priority=50,
        now=_now(),
    )
    enqueue_material_job(
        db_session,
        kind="corpus_enrichment",
        payload={"limit": 10},
        priority=1,
        now=_now(),
    )
    high_priority = enqueue_material_job(
        db_session,
        kind="sentence_shard",
        payload={"lemma_ids": [2]},
        priority=10,
        now=_now(),
    )

    leased = lease_material_jobs(
        db_session,
        worker_id="worker-a",
        kinds=["sentence_shard"],
        limit=2,
        lease_seconds=120,
        now=_now() + timedelta(minutes=1),
    )

    assert [job.id for job in leased] == [high_priority.id, low_priority.id]
    assert all(job.status == STATUS_RUNNING for job in leased)
    assert all(job.lease_owner == "worker-a" for job in leased)
    assert all(job.attempts == 1 for job in leased)

    corpus = db_session.query(MaterialJob).filter_by(kind="corpus_enrichment").one()
    assert corpus.status == STATUS_QUEUED


def test_locked_lease_returns_empty_when_claim_lock_busy(db_session, monkeypatch, tmp_path):
    lock_path = tmp_path / "material-job-lease.lock"
    monkeypatch.setenv("ALIF_MATERIAL_JOB_LEASE_LOCK", str(lock_path))
    enqueue_material_job(
        db_session,
        kind="sentence_shard",
        payload={"lemma_ids": [1]},
        now=_now(),
    )

    handle = try_acquire_material_job_lease_lock()
    assert handle is not None
    try:
        leased = lease_material_jobs_locked(
            db_session,
            worker_id="worker-a",
            kinds=["sentence_shard"],
            now=_now(),
        )
    finally:
        release_material_job_lease_lock(handle)

    assert leased == []
    job = db_session.query(MaterialJob).one()
    assert job.status == STATUS_QUEUED


def test_not_before_prevents_early_leasing(db_session):
    enqueue_material_job(
        db_session,
        kind="sentence_shard",
        payload={"lemma_ids": [1]},
        not_before=_now() + timedelta(hours=1),
        now=_now(),
    )

    assert lease_material_jobs(db_session, worker_id="worker-a", now=_now()) == []
    leased = lease_material_jobs(
        db_session,
        worker_id="worker-a",
        now=_now() + timedelta(hours=2),
    )

    assert len(leased) == 1
    assert leased[0].status == STATUS_RUNNING


def test_expired_leases_return_to_queue_and_can_be_released(db_session):
    job = enqueue_material_job(
        db_session,
        kind="sentence_shard",
        payload={"lemma_ids": [1]},
        now=_now(),
    )
    leased = lease_material_jobs(
        db_session,
        worker_id="worker-a",
        lease_seconds=10,
        now=_now(),
    )
    assert leased[0].id == job.id

    released = release_expired_leases(
        db_session,
        now=_now() + timedelta(seconds=11),
    )
    assert released == 1

    leased_again = lease_material_jobs(
        db_session,
        worker_id="worker-b",
        lease_seconds=10,
        now=_now() + timedelta(seconds=12),
    )
    assert leased_again[0].id == job.id
    assert leased_again[0].lease_owner == "worker-b"
    assert leased_again[0].attempts == 2


def test_expired_final_attempt_marks_failed(db_session):
    enqueue_material_job(
        db_session,
        kind="sentence_shard",
        payload={"lemma_ids": [1]},
        max_attempts=1,
        now=_now(),
    )
    job = lease_material_jobs(
        db_session,
        worker_id="worker-a",
        lease_seconds=10,
        now=_now(),
    )[0]

    assert lease_material_jobs(
        db_session,
        worker_id="worker-b",
        lease_seconds=10,
        now=_now() + timedelta(seconds=11),
    ) == []

    db_session.refresh(job)
    assert job.status == STATUS_FAILED
    assert job.completed_at is not None
    assert job.last_error == "lease expired after max attempts"


def test_fail_retries_until_max_attempts(db_session):
    enqueue_material_job(
        db_session,
        kind="sentence_shard",
        payload={"lemma_ids": [1]},
        max_attempts=2,
        now=_now(),
    )
    job = lease_material_jobs(db_session, worker_id="worker-a", now=_now())[0]
    fail_material_job(
        db_session,
        job,
        error="temporary",
        retry_delay_seconds=300,
        now=_now(),
    )

    assert job.status == STATUS_QUEUED
    assert lease_material_jobs(
        db_session,
        worker_id="worker-b",
        now=_now() + timedelta(seconds=299),
    ) == []

    job = lease_material_jobs(
        db_session,
        worker_id="worker-b",
        now=_now() + timedelta(seconds=301),
    )[0]
    fail_material_job(
        db_session,
        job,
        error="permanent",
        now=_now() + timedelta(seconds=302),
    )

    assert job.status == STATUS_FAILED
    assert job.completed_at is not None
    assert job.last_error == "permanent"
