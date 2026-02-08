from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.word_selector import (
    select_next_words,
    introduce_word,
    get_root_family,
    get_sentence_difficulty_params,
    MAX_NEW_PER_SESSION,
)
from app.services.interaction_logger import log_interaction

router = APIRouter(prefix="/api/learn", tags=["learn"])


class IntroduceRequest(BaseModel):
    lemma_id: int


class IntroduceBatchRequest(BaseModel):
    lemma_ids: list[int]


@router.get("/next-words")
def next_words(
    count: int = Query(3, ge=1, le=MAX_NEW_PER_SESSION),
    exclude: str = Query("", description="Comma-separated lemma IDs to exclude"),
    db: Session = Depends(get_db),
):
    """Get the next best words to introduce, ranked by the selection algorithm."""
    exclude_ids = [int(x) for x in exclude.split(",") if x.strip().isdigit()]
    words = select_next_words(db, count=count, exclude_lemma_ids=exclude_ids)
    return {"words": words, "count": len(words)}


@router.post("/introduce")
def introduce(req: IntroduceRequest, db: Session = Depends(get_db)):
    """Introduce a single word — create FSRS card and return root family context."""
    try:
        result = introduce_word(db, req.lemma_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    log_interaction(
        event="word_introduced",
        lemma_id=req.lemma_id,
    )
    return result


@router.post("/introduce-batch")
def introduce_batch(req: IntroduceBatchRequest, db: Session = Depends(get_db)):
    """Introduce multiple words at once."""
    if len(req.lemma_ids) > MAX_NEW_PER_SESSION:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_NEW_PER_SESSION} words per session",
        )

    results = []
    for lemma_id in req.lemma_ids:
        try:
            result = introduce_word(db, lemma_id)
            results.append(result)
            log_interaction(event="word_introduced", lemma_id=lemma_id)
        except ValueError:
            results.append({"lemma_id": lemma_id, "error": "not found"})

    return {"introduced": results, "count": len(results)}


@router.get("/root-family/{root_id}")
def root_family(root_id: int, db: Session = Depends(get_db)):
    """Get all words from a root with their knowledge state."""
    family = get_root_family(db, root_id)
    if not family:
        raise HTTPException(status_code=404, detail="Root not found")
    return {"root_id": root_id, "words": family}


@router.get("/sentence-params/{lemma_id}")
def sentence_params(lemma_id: int, db: Session = Depends(get_db)):
    """Get recommended sentence generation parameters for a word based on familiarity."""
    params = get_sentence_difficulty_params(db, lemma_id)
    return {"lemma_id": lemma_id, **params}
