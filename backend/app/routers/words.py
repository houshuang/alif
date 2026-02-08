import json
import math
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Lemma, UserLemmaKnowledge, ReviewLog, Sentence
from app.services.word_selector import get_root_family


def knowledge_score(fsrs_card_json, times_seen: int, times_correct: int) -> int:
    """Compute 0-100 knowledge score for a word.

    Weights: 70% stability (memory durability, log-scaled),
    30% accuracy, scaled by confidence (review count with diminishing returns).
    """
    if not times_seen:
        return 0

    stability = 0.0
    if fsrs_card_json:
        card = fsrs_card_json
        if isinstance(card, str):
            card = json.loads(card)
        stability = card.get("stability") or 0.0

    # Log-scaled stability: S=1d→0.11, S=7d→0.33, S=30d→0.58, S=90d→0.76, S=365d→1.0
    s_score = min(1.0, math.log(1 + stability) / math.log(366))

    accuracy = times_correct / times_seen

    # Confidence ramp: 1→0.18, 3→0.45, 5→0.63, 10→0.86, 20→0.98
    confidence = 1 - math.exp(-times_seen / 5)

    return round((0.7 * s_score + 0.3 * accuracy) * confidence * 100)

router = APIRouter(prefix="/api/words", tags=["words"])


@router.get("")
def list_words(
    status: Optional[str] = Query(None, description="Filter by knowledge state"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    q = db.query(Lemma).join(UserLemmaKnowledge)
    if status:
        q = q.filter(UserLemmaKnowledge.knowledge_state == status)
    lemmas = q.offset(offset).limit(limit).all()
    results = []
    for lemma in lemmas:
        k = lemma.knowledge
        times_seen = k.times_seen if k else 0
        times_correct = k.times_correct if k else 0
        score = knowledge_score(
            k.fsrs_card_json if k else None, times_seen, times_correct
        )
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
            "times_seen": times_seen,
            "times_correct": times_correct,
            "last_reviewed": k.last_reviewed.isoformat() if k and k.last_reviewed else None,
            "knowledge_score": score,
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

    reviews = (
        db.query(ReviewLog)
        .filter(ReviewLog.lemma_id == lemma_id)
        .order_by(ReviewLog.reviewed_at.desc())
        .limit(50)
        .all()
    )

    review_history = []
    for r in reviews:
        entry = {
            "rating": r.rating,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "response_ms": r.response_ms,
            "credit_type": r.credit_type,
            "comprehension_signal": r.comprehension_signal,
            "review_mode": r.review_mode,
        }
        if r.sentence_id:
            sent = db.query(Sentence).filter(Sentence.id == r.sentence_id).first()
            if sent:
                entry["sentence_arabic"] = sent.arabic_diacritized or sent.arabic_text
                entry["sentence_english"] = sent.english_translation
        review_history.append(entry)

    ts = k.times_seen if k else 0
    tc = k.times_correct if k else 0
    score = knowledge_score(k.fsrs_card_json if k else None, ts, tc)

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
        "times_seen": ts,
        "times_correct": tc,
        "knowledge_score": score,
        "root_family": root_family,
        "review_history": review_history,
    }
