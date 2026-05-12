"""Planner for splitting material generation into bounded queue jobs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Lemma, Sentence
from app.services.material_generator import (
    active_sentence_counts_by_lemma,
    acquiring_material_gaps,
    lemmas_on_backoff,
)
from app.services.material_jobs import enqueue_material_job
from app.services.pipeline_tiers import compute_word_tiers, tier_summary
from app.services.sentence_validator import _is_function_word


KIND_SENTENCE_SHARD = "sentence_shard"
DEFAULT_SENTENCE_BUDGET = 40
DEFAULT_SHARD_SIZE = 4
DEFAULT_MAX_JOBS = 10


@dataclass
class SentenceShard:
    lemma_ids: list[int]
    payload: dict[str, Any]
    priority: int
    dedupe_key: str
    planned_sentences: int


@dataclass
class SentenceShardPlan:
    shards: list[SentenceShard]
    dry_run_words: list[dict[str, Any]]
    budget: int
    capacity: int
    total_active_sentences: int
    tier_counts: dict[int, int]
    skipped_backoff: int
    rescue_gaps: int

    @property
    def planned_sentences(self) -> int:
        return sum(shard.planned_sentences for shard in self.shards)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _is_generation_inert_lemma(lemma: Lemma) -> bool:
    if lemma.word_category in {"proper_name", "onomatopoeia"}:
        return True
    return _is_function_word(lemma.lemma_ar_bare or "")


def _window_key(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y%m%d%H")


def plan_sentence_shards(
    db: Session,
    *,
    max_sentences: int = 2000,
    sentence_budget: int | None = None,
    max_jobs: int = DEFAULT_MAX_JOBS,
    shard_size: int = DEFAULT_SHARD_SIZE,
    count_per_word: int = 1,
    acquiring_rescue_limit: int | None = None,
    now: datetime | None = None,
) -> SentenceShardPlan:
    """Build bounded sentence-generation jobs without executing LLM work."""

    now = now or datetime.now(timezone.utc)
    sentence_budget = (
        sentence_budget
        if sentence_budget is not None
        else _env_int("ALIF_STEP_A_SENTENCE_BUDGET", DEFAULT_SENTENCE_BUDGET)
    )
    acquiring_rescue_limit = (
        acquiring_rescue_limit
        if acquiring_rescue_limit is not None
        else _env_int("ALIF_ACQUIRING_RESCUE_LIMIT", 80)
    )
    shard_size = max(1, shard_size)
    max_jobs = max(0, max_jobs)
    count_per_word = max(1, count_per_word)

    total_active = int(
        db.query(func.count(Sentence.id))
        .filter(Sentence.is_active == True)  # noqa: E712
        .scalar()
        or 0
    )
    capacity = max(0, max_sentences - total_active)
    budget = max(0, min(sentence_budget, capacity))

    word_tiers = compute_word_tiers(db, now=now)
    tier_counts = tier_summary(word_tiers)
    if budget <= 0 or max_jobs <= 0:
        return SentenceShardPlan(
            shards=[],
            dry_run_words=[],
            budget=budget,
            capacity=capacity,
            total_active_sentences=total_active,
            tier_counts=tier_counts,
            skipped_backoff=0,
            rescue_gaps=0,
        )

    rescue_words = acquiring_material_gaps(db, limit=max(0, acquiring_rescue_limit))
    rescue_ids = {w["lemma_id"] for w in rescue_words}
    tier_candidate_ids = {wt.lemma_id for wt in word_tiers if wt.backfill_target > 0}
    backoff_ids = lemmas_on_backoff(db, list(tier_candidate_ids | rescue_ids))
    ordinary_backoff_ids = backoff_ids - rescue_ids

    all_candidate_ids = list(tier_candidate_ids | rescue_ids)
    existing_counts = active_sentence_counts_by_lemma(db, all_candidate_ids)

    words_needing: list[dict[str, Any]] = []
    seen: set[int] = set()
    for word in rescue_words:
        lid = word["lemma_id"]
        needed = min(int(word.get("needed") or 0), budget)
        if needed <= 0:
            continue
        words_needing.append({**word, "needed": needed, "priority_tier": 0})
        seen.add(lid)

    for wt in word_tiers:
        if wt.backfill_target <= 0 or wt.lemma_id in seen:
            continue
        if wt.lemma_id in ordinary_backoff_ids:
            continue
        lemma = db.query(Lemma).filter(Lemma.lemma_id == wt.lemma_id).first()
        if not lemma or not (lemma.gloss_en or "").strip():
            continue
        if _is_generation_inert_lemma(lemma):
            continue
        existing = existing_counts.get(wt.lemma_id, 0)
        needed = wt.backfill_target - existing
        if needed <= 0:
            continue
        words_needing.append({
            "lemma_id": wt.lemma_id,
            "lemma_ar": lemma.lemma_ar,
            "gloss_en": lemma.gloss_en or "",
            "pos": lemma.pos or "",
            "root_id": lemma.root_id,
            "due_str": wt.due_dt.isoformat() if wt.due_dt else "none",
            "existing": existing,
            "needed": min(needed, budget),
            "tier": wt.tier,
            "priority_tier": wt.tier,
            "backfill_target": wt.backfill_target,
            "source": "due_tier",
        })

    shards: list[SentenceShard] = []
    remaining_budget = budget
    window = _window_key(now)
    current: list[dict[str, Any]] = []
    current_planned = 0

    def flush_current() -> None:
        nonlocal current, current_planned, remaining_budget
        if not current or len(shards) >= max_jobs:
            current = []
            current_planned = 0
            return
        lemma_ids = [int(w["lemma_id"]) for w in current]
        priority_tier = min(int(w.get("priority_tier", w.get("tier", 4))) for w in current)
        priority = priority_tier * 10 + len(shards)
        payload = {
            "lemma_ids": lemma_ids,
            "count_per_word": count_per_word,
            "planned_sentences": current_planned,
            "needed_by_lemma": {str(w["lemma_id"]): int(w.get("needed") or 1) for w in current},
            "sources_by_lemma": {str(w["lemma_id"]): w.get("source", "unknown") for w in current},
            "due_by_lemma": {str(w["lemma_id"]): w.get("due_str", "none") for w in current},
            "planned_at": now.isoformat(),
        }
        shards.append(SentenceShard(
            lemma_ids=lemma_ids,
            payload=payload,
            priority=priority,
            dedupe_key=f"{KIND_SENTENCE_SHARD}:{window}:{'-'.join(map(str, lemma_ids))}:x{count_per_word}",
            planned_sentences=current_planned,
        ))
        remaining_budget -= current_planned
        current = []
        current_planned = 0

    for word in words_needing:
        if remaining_budget <= 0 or len(shards) >= max_jobs:
            break
        planned_for_word = min(int(word.get("needed") or 1), count_per_word, remaining_budget - current_planned)
        if planned_for_word <= 0:
            flush_current()
            if remaining_budget <= 0 or len(shards) >= max_jobs:
                break
            planned_for_word = min(int(word.get("needed") or 1), count_per_word, remaining_budget)
        current.append(word)
        current_planned += planned_for_word
        if len(current) >= shard_size or current_planned >= remaining_budget:
            flush_current()

    flush_current()

    return SentenceShardPlan(
        shards=shards,
        dry_run_words=words_needing,
        budget=budget,
        capacity=capacity,
        total_active_sentences=total_active,
        tier_counts=tier_counts,
        skipped_backoff=len(ordinary_backoff_ids),
        rescue_gaps=len(rescue_words),
    )


def enqueue_sentence_shards(db: Session, plan: SentenceShardPlan) -> list[int]:
    job_ids: list[int] = []
    for shard in plan.shards:
        job = enqueue_material_job(
            db,
            kind=KIND_SENTENCE_SHARD,
            payload=shard.payload,
            priority=shard.priority,
            dedupe_key=shard.dedupe_key,
        )
        job_ids.append(job.id)
    return job_ids
