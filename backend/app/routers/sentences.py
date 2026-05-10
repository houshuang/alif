"""Sentence generation and validation API endpoints."""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Sentence, SentenceWord, SentenceReviewLog, Lemma, Story, UserLemmaKnowledge
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


@router.get("/{sentence_id}/story-info")
def story_info_for_sentence(sentence_id: int, db: Session = Depends(get_db)):
    """Debug info for the passage/story containing a review sentence."""
    sent = db.query(Sentence).filter(Sentence.id == sentence_id).first()
    if not sent:
        raise HTTPException(404, "Sentence not found")
    if not sent.story_id:
        raise HTTPException(404, "Sentence is not attached to a story")

    story = db.query(Story).filter(Story.id == sent.story_id).first()
    if not story:
        raise HTTPException(404, "Story not found")

    story_sentences = (
        db.query(Sentence)
        .filter(Sentence.story_id == story.id)
        .order_by(Sentence.id)
        .all()
    )

    metadata = story.metadata_json or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    target_ids = [
        int(lid)
        for lid in (metadata.get("target_lemma_ids") or [])
        if isinstance(lid, int) or (isinstance(lid, str) and lid.isdigit())
    ]
    if not target_ids:
        seen_target_ids: set[int] = set()
        for row in story_sentences:
            if row.target_lemma_id and row.target_lemma_id not in seen_target_ids:
                target_ids.append(row.target_lemma_id)
                seen_target_ids.add(row.target_lemma_id)

    sentence_ids = [row.id for row in story_sentences]
    target_word_rows = (
        db.query(SentenceWord)
        .filter(
            SentenceWord.sentence_id.in_(sentence_ids),
            SentenceWord.lemma_id.in_(target_ids),
        )
        .all()
        if sentence_ids and target_ids
        else []
    )
    occurrences_by_lid: dict[int, dict[str, object]] = {}
    for sw in target_word_rows:
        if not sw.lemma_id:
            continue
        entry = occurrences_by_lid.setdefault(
            sw.lemma_id,
            {"count": 0, "surface_forms": []},
        )
        entry["count"] = int(entry["count"]) + 1
        forms = entry["surface_forms"]
        if isinstance(forms, list) and sw.surface_form not in forms:
            forms.append(sw.surface_form)

    lemma_map: dict[int, Lemma] = {}
    ulk_map: dict[int, UserLemmaKnowledge] = {}
    if target_ids:
        lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(target_ids)).all()
        lemma_map = {lemma.lemma_id: lemma for lemma in lemmas}
        ulks = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id.in_(target_ids)).all()
        ulk_map = {ulk.lemma_id: ulk for ulk in ulks}

    def _fsrs_due(ulk: UserLemmaKnowledge | None) -> str | None:
        if not ulk or not ulk.fsrs_card_json:
            return None
        card_data = ulk.fsrs_card_json
        if isinstance(card_data, str):
            try:
                card_data = json.loads(card_data)
            except json.JSONDecodeError:
                return None
        if not isinstance(card_data, dict):
            return None
        due = card_data.get("due")
        return str(due) if due else None

    last_shown = [
        dt for row in story_sentences
        for dt in (row.last_reading_shown_at, row.last_listening_shown_at)
        if dt
    ]

    return {
        "story_id": story.id,
        "title_ar": story.title_ar,
        "title_en": story.title_en,
        "source": story.source,
        "format_type": story.format_type,
        "status": story.status,
        "created_at": story.created_at.isoformat() if story.created_at else None,
        "completed_at": story.completed_at.isoformat() if story.completed_at else None,
        "readiness_pct": story.readiness_pct,
        "total_words": story.total_words,
        "known_count": story.known_count,
        "unknown_count": story.unknown_count,
        "sentence_count": len(story_sentences),
        "active_sentence_count": sum(1 for row in story_sentences if row.is_active),
        "times_shown_total": sum(row.times_shown or 0 for row in story_sentences),
        "last_shown_at": max(last_shown).isoformat() if last_shown else None,
        "style_tag": metadata.get("style_tag"),
        "authentic_source": metadata.get("authentic_source"),
        "hindawi": metadata.get("hindawi"),
        "target_lemma_ids": target_ids,
        "target_lemmas": [
            {
                "lemma_id": lid,
                "lemma_ar": lemma_map[lid].lemma_ar if lid in lemma_map else "",
                "lemma_ar_bare": lemma_map[lid].lemma_ar_bare if lid in lemma_map else "",
                "gloss_en": lemma_map[lid].gloss_en if lid in lemma_map else None,
                "pos": lemma_map[lid].pos if lid in lemma_map else None,
                "knowledge_state": ulk_map[lid].knowledge_state if lid in ulk_map else None,
                "times_seen": ulk_map[lid].times_seen if lid in ulk_map else 0,
                "times_correct": ulk_map[lid].times_correct if lid in ulk_map else 0,
                "fsrs_due": _fsrs_due(ulk_map.get(lid)),
                "occurrence_count": int(occurrences_by_lid.get(lid, {}).get("count", 0)),
                "surface_forms": occurrences_by_lid.get(lid, {}).get("surface_forms", []),
            }
            for lid in target_ids
        ],
        "sentences": [
            {
                "sentence_id": row.id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "source": row.source,
                "is_active": row.is_active,
                "times_shown": row.times_shown,
                "target_lemma_id": row.target_lemma_id,
                "last_reading_shown_at": row.last_reading_shown_at.isoformat() if row.last_reading_shown_at else None,
                "last_reading_comprehension": row.last_reading_comprehension,
                "last_listening_shown_at": row.last_listening_shown_at.isoformat() if row.last_listening_shown_at else None,
                "last_listening_comprehension": row.last_listening_comprehension,
            }
            for row in story_sentences
        ],
    }
