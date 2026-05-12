"""Workers for executing queued material generation jobs."""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models import MaterialJob
from app.services.material_generator import batch_generate_material, record_generation_result
from app.services.material_job_planner import KIND_SENTENCE_SHARD
from app.services.material_jobs import complete_material_job, fail_material_job


BatchGenerator = Callable[[list[int], int, str], dict[str, Any]]


def _default_batch_generator(
    lemma_ids: list[int],
    count_per_word: int,
    model_override: str,
) -> dict[str, Any]:
    return batch_generate_material(
        lemma_ids,
        count_per_word=count_per_word,
        model_override=model_override,
    )


def process_material_job(
    db: Session,
    job: MaterialJob,
    *,
    model: str = "claude_sonnet",
    retry_delay_seconds: int = 900,
    generator: BatchGenerator = _default_batch_generator,
) -> MaterialJob:
    if job.kind != KIND_SENTENCE_SHARD:
        return fail_material_job(
            db,
            job,
            error=f"unsupported material job kind: {job.kind}",
            retry_delay_seconds=retry_delay_seconds,
        )

    payload = job.payload_json or {}
    lemma_ids = [int(lid) for lid in payload.get("lemma_ids") or []]
    if not lemma_ids:
        return fail_material_job(
            db,
            job,
            error="sentence shard has no lemma_ids",
            retry_delay_seconds=retry_delay_seconds,
        )

    count_per_word = int(payload.get("count_per_word") or 1)
    try:
        result = generator(lemma_ids, max(1, count_per_word), model)
    except Exception as exc:
        return fail_material_job(
            db,
            job,
            error=f"{type(exc).__name__}: {exc}",
            retry_delay_seconds=retry_delay_seconds,
        )

    failed_ids = {int(lid) for lid in result.get("words_failed", [])}
    for lemma_id in lemma_ids:
        record_generation_result(
            db,
            lemma_id,
            0 if lemma_id in failed_ids else 1,
        )

    return complete_material_job(db, job, result=result)
