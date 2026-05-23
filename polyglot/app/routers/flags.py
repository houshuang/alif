from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ActivityLog, ContentFlag, Lemma, Sentence

router = APIRouter(prefix="/api/flags", tags=["flags"])

VALID_CONTENT_TYPES = {
    "word_gloss",
    "sentence",
    "sentence_text",
    "sentence_translation",
    "word_mapping",
}


class FlagRequest(BaseModel):
    content_type: str
    lemma_id: int | None = None
    sentence_id: int | None = None


@router.post("")
def create_flag(req: FlagRequest, db: Session = Depends(get_db)):
    if req.content_type not in VALID_CONTENT_TYPES:
        raise HTTPException(
            400,
            f"Invalid content_type. Must be one of: {sorted(VALID_CONTENT_TYPES)}",
        )

    lemma: Lemma | None = None
    sentence: Sentence | None = None
    if req.lemma_id is not None:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == req.lemma_id).first()
        if not lemma:
            raise HTTPException(404, "Lemma not found")

    if req.content_type == "word_gloss":
        if req.lemma_id is None:
            raise HTTPException(400, "lemma_id required for word_gloss flags")
    else:
        if req.sentence_id is None:
            raise HTTPException(400, "sentence_id required for sentence flags")
        sentence = db.query(Sentence).filter(Sentence.id == req.sentence_id).first()
        if not sentence:
            raise HTTPException(404, "Sentence not found")

    existing = (
        db.query(ContentFlag)
        .filter(
            ContentFlag.content_type == req.content_type,
            ContentFlag.status.in_(["pending", "reviewing"]),
        )
    )
    if req.content_type == "word_gloss":
        existing = existing.filter(ContentFlag.lemma_id == req.lemma_id)
    else:
        existing = existing.filter(ContentFlag.sentence_id == req.sentence_id)
    dup = existing.first()
    if dup:
        return {"flag_id": dup.id, "status": "already_flagged"}

    flag = ContentFlag(
        content_type=req.content_type,
        lemma_id=req.lemma_id,
        sentence_id=req.sentence_id,
    )
    db.add(flag)
    db.flush()

    language_code = sentence.language_code if sentence else lemma.language_code if lemma else None
    target = f"sentence #{req.sentence_id}" if req.sentence_id is not None else f"lemma #{req.lemma_id}"
    db.add(ActivityLog(
        event_type="content_reported",
        language_code=language_code,
        summary=f"Reported {target}",
        detail_json={
            "flag_id": flag.id,
            "content_type": req.content_type,
            "lemma_id": req.lemma_id,
            "sentence_id": req.sentence_id,
        },
    ))
    db.commit()
    db.refresh(flag)

    return {"flag_id": flag.id, "status": "pending"}


@router.get("")
def list_flags(
    status: str | None = Query(None),
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
