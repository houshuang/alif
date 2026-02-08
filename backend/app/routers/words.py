from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Lemma, UserLemmaKnowledge
from app.services.word_selector import get_root_family

router = APIRouter(prefix="/api/words", tags=["words"])


@router.get("")
def list_words(
    status: Optional[str] = Query(None, description="Filter by knowledge state"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(Lemma)
    if status:
        q = q.join(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.knowledge_state == status
        )
    lemmas = q.offset(offset).limit(limit).all()
    results = []
    for lemma in lemmas:
        k = lemma.knowledge
        results.append({
            "lemma_id": lemma.lemma_id,
            "lemma_ar": lemma.lemma_ar,
            "lemma_ar_bare": lemma.lemma_ar_bare,
            "pos": lemma.pos or "",
            "gloss_en": lemma.gloss_en or "",
            "transliteration": lemma.transliteration_ala_lc or "",
            "root": lemma.root.root if lemma.root else None,
            "knowledge_state": k.knowledge_state if k else "new",
            "frequency_rank": lemma.frequency_rank,
            "audio_url": lemma.audio_url,
        })
    return results


@router.get("/{lemma_id}")
def get_word(lemma_id: int, db: Session = Depends(get_db)):
    lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
    if not lemma:
        raise HTTPException(404, "Word not found")

    k = lemma.knowledge
    root_family = []
    if lemma.root_id:
        root_family = [
            {"id": w["lemma_id"], "arabic": w["lemma_ar"], "english": w["gloss_en"]}
            for w in get_root_family(db, lemma.root_id)
            if w["lemma_id"] != lemma.lemma_id
        ]

    return {
        "lemma_id": lemma.lemma_id,
        "lemma_ar": lemma.lemma_ar,
        "lemma_ar_bare": lemma.lemma_ar_bare,
        "pos": lemma.pos or "",
        "gloss_en": lemma.gloss_en or "",
        "transliteration": lemma.transliteration_ala_lc or "",
        "root": lemma.root.root if lemma.root else None,
        "knowledge_state": k.knowledge_state if k else "new",
        "frequency_rank": lemma.frequency_rank,
        "audio_url": lemma.audio_url,
        "times_seen": k.times_seen if k else 0,
        "times_correct": k.times_correct if k else 0,
        "root_family": root_family,
    }
