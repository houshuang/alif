from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ContentFlag, Lemma, Sentence
from app.services.flag_evaluator import evaluate_flag

router = APIRouter(prefix="/api/flags", tags=["flags"])


class FlagRequest(BaseModel):
    content_type: str  # word_gloss, sentence_arabic, sentence_english, sentence_transliteration
    lemma_id: Optional[int] = None
    sentence_id: Optional[int] = None


@router.post("")
def create_flag(
    req: FlagRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    valid_types = {"word_gloss", "sentence_arabic", "sentence_english", "sentence_transliteration", "word_mapping"}
    if req.content_type not in valid_types:
        raise HTTPException(400, f"Invalid content_type. Must be one of: {valid_types}")

    if req.content_type == "word_gloss":
        if not req.lemma_id:
            raise HTTPException(400, "lemma_id required for word_gloss flags")
        if not db.query(Lemma).filter(Lemma.lemma_id == req.lemma_id).first():
            raise HTTPException(404, "Lemma not found")
    else:
        if not req.sentence_id:
            raise HTTPException(400, "sentence_id required for sentence flags")
        if not db.query(Sentence).filter(Sentence.id == req.sentence_id).first():
            raise HTTPException(404, "Sentence not found")

    flag = ContentFlag(
        content_type=req.content_type,
        lemma_id=req.lemma_id,
        sentence_id=req.sentence_id,
    )
    db.add(flag)
    db.commit()
    db.refresh(flag)

    background_tasks.add_task(evaluate_flag, flag.id)

    return {"flag_id": flag.id, "status": "pending"}


@router.get("")
def list_flags(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(ContentFlag).order_by(ContentFlag.created_at.desc())
    if status:
        q = q.filter(ContentFlag.status == status)
    flags = q.limit(limit).all()

    return [
        {
            "id": f.id,
            "content_type": f.content_type,
            "lemma_id": f.lemma_id,
            "sentence_id": f.sentence_id,
            "status": f.status,
            "original_value": f.original_value,
            "corrected_value": f.corrected_value,
            "resolution_note": f.resolution_note,
            "created_at": f.created_at.isoformat() if f.created_at else None,
            "resolved_at": f.resolved_at.isoformat() if f.resolved_at else None,
        }
        for f in flags
    ]
