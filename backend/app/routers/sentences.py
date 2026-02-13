"""Sentence generation and validation API endpoints."""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Sentence, SentenceWord, SentenceReviewLog, Lemma, UserLemmaKnowledge
from app.services.sentence_generator import (
    GeneratedSentence,
    GenerationError,
    generate_validated_sentence,
)
from app.services.sentence_validator import (
    ValidationResult,
    strip_diacritics,
    validate_sentence,
)

router = APIRouter(prefix="/api/sentences", tags=["sentences"])


class GenerateRequest(BaseModel):
    target_arabic: str
    target_translation: str
    known_words: list[dict[str, str]]
    difficulty_hint: str = "beginner"


class ValidateRequest(BaseModel):
    arabic_text: str
    target_bare: str
    known_bare_forms: list[str]


class ValidateResponse(BaseModel):
    valid: bool
    target_found: bool
    unknown_words: list[str]
    known_words: list[str]
    function_words: list[str]
    issues: list[str]


@router.post("/generate", response_model=GeneratedSentence)
def generate_sentence_endpoint(req: GenerateRequest):
    """Generate a validated sentence for a target word."""
    try:
        return generate_validated_sentence(
            target_arabic=req.target_arabic,
            target_translation=req.target_translation,
            known_words=req.known_words,
            difficulty_hint=req.difficulty_hint,
        )
    except GenerationError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/validate", response_model=ValidateResponse)
def validate_sentence_endpoint(req: ValidateRequest):
    """Validate an Arabic sentence against known vocabulary."""
    result = validate_sentence(
        arabic_text=req.arabic_text,
        target_bare=req.target_bare,
        known_bare_forms=set(req.known_bare_forms),
    )
    return ValidateResponse(
        valid=result.valid,
        target_found=result.target_found,
        unknown_words=result.unknown_words,
        known_words=result.known_words,
        function_words=result.function_words,
        issues=result.issues,
    )


@router.get("/{sentence_id}/info")
def sentence_info(sentence_id: int, db: Session = Depends(get_db)):
    """Debug info for a sentence: metadata, review history, per-word difficulty."""
    sent = db.query(Sentence).filter(Sentence.id == sentence_id).first()
    if not sent:
        raise HTTPException(404, "Sentence not found")

    # Review history
    reviews = (
        db.query(SentenceReviewLog)
        .filter(SentenceReviewLog.sentence_id == sentence_id)
        .order_by(SentenceReviewLog.reviewed_at.desc())
        .all()
    )

    # Words with FSRS difficulty
    sw_rows = (
        db.query(SentenceWord)
        .filter(SentenceWord.sentence_id == sentence_id)
        .order_by(SentenceWord.position)
        .all()
    )
    lemma_ids = [sw.lemma_id for sw in sw_rows if sw.lemma_id]
    ulk_map: dict[int, UserLemmaKnowledge] = {}
    lemma_map: dict[int, Lemma] = {}
    if lemma_ids:
        ulks = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id.in_(lemma_ids)).all()
        ulk_map = {u.lemma_id: u for u in ulks}
        lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all()
        lemma_map = {l.lemma_id: l for l in lemmas}

    words = []
    for sw in sw_rows:
        ulk = ulk_map.get(sw.lemma_id) if sw.lemma_id else None
        lemma = lemma_map.get(sw.lemma_id) if sw.lemma_id else None
        fsrs_difficulty = None
        fsrs_stability = None
        if ulk and ulk.fsrs_card_json:
            card_data = ulk.fsrs_card_json
            if isinstance(card_data, str):
                card_data = json.loads(card_data)
            fsrs_difficulty = card_data.get("difficulty") or card_data.get("d")
            fsrs_stability = card_data.get("stability") or card_data.get("s")

        words.append({
            "position": sw.position,
            "surface_form": sw.surface_form,
            "lemma_id": sw.lemma_id,
            "gloss_en": lemma.gloss_en if lemma else None,
            "is_target_word": sw.is_target_word,
            "knowledge_state": ulk.knowledge_state if ulk else None,
            "times_seen": ulk.times_seen if ulk else 0,
            "times_correct": ulk.times_correct if ulk else 0,
            "fsrs_difficulty": round(fsrs_difficulty, 3) if fsrs_difficulty is not None else None,
            "fsrs_stability": round(fsrs_stability, 2) if fsrs_stability is not None else None,
            "acquisition_box": ulk.acquisition_box if ulk else None,
        })

    return {
        "sentence_id": sent.id,
        "created_at": sent.created_at.isoformat() if sent.created_at else None,
        "source": sent.source,
        "difficulty_score": sent.difficulty_score,
        "is_active": sent.is_active,
        "times_shown": sent.times_shown,
        "target_lemma_id": sent.target_lemma_id,
        "last_reading_shown_at": sent.last_reading_shown_at.isoformat() if sent.last_reading_shown_at else None,
        "last_reading_comprehension": sent.last_reading_comprehension,
        "last_listening_shown_at": sent.last_listening_shown_at.isoformat() if sent.last_listening_shown_at else None,
        "last_listening_comprehension": sent.last_listening_comprehension,
        "reviews": [
            {
                "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
                "comprehension": r.comprehension,
                "review_mode": r.review_mode,
                "response_ms": r.response_ms,
            }
            for r in reviews
        ],
        "words": words,
    }
