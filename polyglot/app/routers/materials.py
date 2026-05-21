"""Material generation endpoints.

Two routes:

    POST /api/materials/generate
        Generate sentences for a specific lemma or batch of lemmas. Synchronous;
        returns counts. Use for manual kicking from the frontend or CLI.

    POST /api/materials/warm-cache
        Find lemmas below ``ACTIVE_TARGET`` and fill them in bounded batches.
        Used by the cron wrapper. Long-running (multi-minute) — fire-and-check
        rather than fire-and-wait if the caller is interactive.

Generation is bounded by the env vars in ``material_generator.py``
(``POLYGLOT_BATCH_WORD_SIZE``, ``POLYGLOT_SENTENCES_PER_TARGET``,
``POLYGLOT_ACTIVE_TARGET``). The endpoint doesn't expose model overrides —
those stay in the service module via env so production stays stable.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.material_generator import (
    SENTENCES_PER_TARGET,
    batch_generate_material,
    warm_sentence_cache,
)
from app.services.lemma_philology import (
    batch_enrich,
    find_unenriched_lemmas,
)


router = APIRouter(prefix="/api/materials", tags=["materials"])


class GenerateRequest(BaseModel):
    language_code: str = Field(..., description="e.g. 'el', 'grc', 'la'")
    lemma_ids: list[int] = Field(..., min_length=1)
    sentences_per_target: int = Field(default=SENTENCES_PER_TARGET, ge=1, le=5)


class GenerateResponse(BaseModel):
    generated: int
    words_covered: int
    words_failed: list[int]


@router.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest) -> GenerateResponse:
    result = batch_generate_material(
        language_code=req.language_code,
        lemma_ids=req.lemma_ids,
        sentences_per_target=req.sentences_per_target,
    )
    return GenerateResponse(**result)


class WarmCacheRequest(BaseModel):
    language_code: str = "el"
    max_lemmas: int = Field(default=16, ge=1, le=64)
    sentences_per_target: int = Field(default=SENTENCES_PER_TARGET, ge=1, le=5)


class WarmCacheResponse(BaseModel):
    run_id: Optional[str] = None
    skipped: bool = False
    reason: Optional[str] = None
    gap_count: int = 0
    generated: int = 0
    words_covered: int = 0
    words_failed: list[int] = Field(default_factory=list)


@router.post("/warm-cache", response_model=WarmCacheResponse)
def warm_cache(req: WarmCacheRequest) -> WarmCacheResponse:
    result = warm_sentence_cache(
        language_code=req.language_code,
        max_lemmas=req.max_lemmas,
        sentences_per_target=req.sentences_per_target,
    )
    return WarmCacheResponse(**result)


# ─── Lemma philology enrichment ─────────────────────────────────────────────


class EnrichRequest(BaseModel):
    language_code: str = "el"
    lemma_ids: Optional[list[int]] = None
    max_lemmas: int = Field(default=10, ge=1, le=50)
    include_failed: bool = False


class EnrichResponse(BaseModel):
    enriched: int
    failed_lemma_ids: list[int]
    skipped_lemma_ids: list[int]


@router.post("/enrich-philology", response_model=EnrichResponse)
def enrich_philology(req: EnrichRequest) -> EnrichResponse:
    """Run philology enrichment.

    Two modes:
    - Caller supplies ``lemma_ids`` explicitly → enrich exactly those.
    - Caller omits ``lemma_ids`` → pick up to ``max_lemmas`` un-enriched
      lemmas from the engaged-vocabulary pool (those with a ULK row), ranked
      by frequency. Used by the cron wrapper.
    """
    if req.lemma_ids:
        ids = req.lemma_ids
    else:
        ids = find_unenriched_lemmas(
            language_code=req.language_code,
            limit=req.max_lemmas,
            include_failed=req.include_failed,
        )
    if not ids:
        return EnrichResponse(enriched=0, failed_lemma_ids=[], skipped_lemma_ids=[])
    result = batch_enrich(language_code=req.language_code, lemma_ids=ids)
    return EnrichResponse(**result)
