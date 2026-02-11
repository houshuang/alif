import logging
import json

from fastapi import APIRouter, BackgroundTasks, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    GrammarFeature,
    Lemma,
    Root,
    Sentence,
    SentenceWord,
    UserLemmaKnowledge,
)
from app.services.grammar_service import seed_grammar_features
from app.services.material_generator import generate_material_for_word, generate_word_audio
from app.services.word_selector import (
    select_next_words,
    introduce_word,
    get_root_family,
    get_sentence_difficulty_params,
    MAX_NEW_PER_SESSION,
)
from app.services.fsrs_service import submit_review
from app.services.interaction_logger import log_interaction

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learn", tags=["learn"])

MIN_SENTENCES_PER_WORD = 3


def _coerce_grammar_keys(value: object) -> list[str]:
    if value is None:
        return []
    payload = value
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, list):
        return []
    return [v for v in payload if isinstance(v, str)]


def _attach_grammar_details(db: Session, words: list[dict]) -> None:
    if not words:
        return

    all_keys: set[str] = set()
    for word in words:
        keys = _coerce_grammar_keys(word.get("grammar_features"))
        word["grammar_features"] = keys
        all_keys.update(keys)

    if not all_keys:
        return

    seed_grammar_features(db)
    rows = (
        db.query(GrammarFeature)
        .filter(GrammarFeature.feature_key.in_(all_keys))
        .all()
    )
    by_key = {r.feature_key: r for r in rows}

    for word in words:
        details: list[dict] = []
        for key in word.get("grammar_features", []):
            feature = by_key.get(key)
            if feature:
                details.append({
                    "feature_key": key,
                    "category": feature.category,
                    "label_en": feature.label_en,
                    "label_ar": feature.label_ar,
                })
            else:
                details.append({
                    "feature_key": key,
                    "category": None,
                    "label_en": key.replace("_", " "),
                    "label_ar": None,
                })
        word["grammar_details"] = details


class IntroduceRequest(BaseModel):
    lemma_id: int


class IntroduceBatchRequest(BaseModel):
    lemma_ids: list[int]


class QuizResultRequest(BaseModel):
    lemma_id: int
    got_it: bool


@router.get("/next-words")
def next_words(
    count: int = Query(3, ge=1, le=MAX_NEW_PER_SESSION),
    exclude: str = Query("", description="Comma-separated lemma IDs to exclude"),
    db: Session = Depends(get_db),
):
    """Get the next best words to introduce, ranked by the selection algorithm."""
    exclude_ids = [int(x) for x in exclude.split(",") if x.strip().isdigit()]
    words = select_next_words(db, count=count, exclude_lemma_ids=exclude_ids)
    _attach_grammar_details(db, words)
    return {"words": words, "count": len(words)}


@router.post("/introduce")
def introduce(
    req: IntroduceRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Introduce a single word — create FSRS card, generate sentences+audio."""
    try:
        result = introduce_word(db, req.lemma_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    log_interaction(
        event="word_introduced",
        lemma_id=req.lemma_id,
    )

    # Auto-generate sentences if word doesn't have enough
    if not result.get("already_known"):
        existing_count = (
            db.query(func.count(Sentence.id))
            .filter(Sentence.target_lemma_id == req.lemma_id)
            .scalar() or 0
        )
        if existing_count < MIN_SENTENCES_PER_WORD:
            needed = MIN_SENTENCES_PER_WORD - existing_count
            background_tasks.add_task(
                generate_material_for_word, req.lemma_id, needed
            )
            result["sentences_generating"] = needed

        # Word-level TTS audio generation disabled — saving ElevenLabs credits.
        # Re-enable when credits are plentiful.

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
    root_obj = db.query(Root).filter(Root.root_id == root_id).first()
    family = get_root_family(db, root_id)
    if not family:
        raise HTTPException(status_code=404, detail="Root not found")
    return {
        "root_id": root_id,
        "root": root_obj.root if root_obj else None,
        "root_meaning": root_obj.core_meaning_en if root_obj else None,
        "words": family,
    }


@router.post("/quiz-result")
def quiz_result(req: QuizResultRequest, db: Session = Depends(get_db)):
    """Submit FSRS review from learn-mode quiz. Got it → rating 3, Missed → rating 1."""
    knowledge = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == req.lemma_id)
        .first()
    )
    if not knowledge or not knowledge.fsrs_card_json:
        raise HTTPException(status_code=404, detail="No FSRS card for this word")

    rating = 3 if req.got_it else 1
    result = submit_review(
        db,
        lemma_id=req.lemma_id,
        rating_int=rating,
        review_mode="quiz",
        comprehension_signal="understood" if req.got_it else "no_idea",
    )

    log_interaction(
        event="quiz_review",
        lemma_id=req.lemma_id,
        rating=rating,
        comprehension_signal="understood" if req.got_it else "no_idea",
    )
    return result


class SuspendRequest(BaseModel):
    lemma_id: int


@router.post("/suspend")
def suspend_word(req: SuspendRequest, db: Session = Depends(get_db)):
    """Suspend a word so it never appears in learn suggestions."""
    existing = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == req.lemma_id)
        .first()
    )
    if existing:
        return {"lemma_id": req.lemma_id, "already_exists": True}

    lemma = db.query(Lemma).filter(Lemma.lemma_id == req.lemma_id).first()
    if not lemma:
        raise HTTPException(status_code=404, detail="Lemma not found")

    from datetime import datetime, timezone
    ulk = UserLemmaKnowledge(
        lemma_id=req.lemma_id,
        knowledge_state="suspended",
        introduced_at=datetime.now(timezone.utc),
        source="study",
    )
    db.add(ulk)
    db.commit()
    log_interaction(event="word_suspended", lemma_id=req.lemma_id)
    return {"lemma_id": req.lemma_id, "state": "suspended"}


@router.get("/sentences/{lemma_id}")
def get_lemma_sentence(lemma_id: int, db: Session = Depends(get_db)):
    """Get a sentence for a lemma (for quiz). Prefers sentences with audio."""
    sentence = (
        db.query(Sentence)
        .filter(Sentence.target_lemma_id == lemma_id)
        .order_by(
            Sentence.audio_url.is_(None).asc(),  # prefer with audio
            Sentence.id,
        )
        .first()
    )
    if not sentence:
        return {"ready": False, "sentence": None}

    words = (
        db.query(SentenceWord)
        .filter(SentenceWord.sentence_id == sentence.id)
        .order_by(SentenceWord.position)
        .all()
    )

    lemma_ids = {sw.lemma_id for sw in words if sw.lemma_id}
    lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all() if lemma_ids else []
    lemma_map = {l.lemma_id: l for l in lemmas}

    target_lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()

    return {
        "ready": True,
        "word_audio_url": target_lemma.audio_url if target_lemma else None,
        "sentence": {
            "sentence_id": sentence.id,
            "arabic_text": sentence.arabic_diacritized or sentence.arabic_text,
            "english_translation": sentence.english_translation or "",
            "transliteration": sentence.transliteration,
            "audio_url": sentence.audio_url,
            "words": [
                {
                    "lemma_id": sw.lemma_id,
                    "surface_form": sw.surface_form,
                    "gloss_en": lemma_map[sw.lemma_id].gloss_en if sw.lemma_id and sw.lemma_id in lemma_map else None,
                }
                for sw in words
            ],
        },
    }


@router.get("/sentence-params/{lemma_id}")
def sentence_params(lemma_id: int, db: Session = Depends(get_db)):
    """Get recommended sentence generation parameters for a word based on familiarity."""
    params = get_sentence_difficulty_params(db, lemma_id)
    return {"lemma_id": lemma_id, **params}


