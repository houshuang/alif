import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
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
                _generate_material_for_word, req.lemma_id, needed
            )
            result["sentences_generating"] = needed

        # Generate word-level TTS audio if not cached
        lemma = db.query(Lemma).filter(Lemma.lemma_id == req.lemma_id).first()
        if lemma and not lemma.audio_url:
            background_tasks.add_task(_generate_word_audio, req.lemma_id)

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


def _generate_material_for_word(lemma_id: int, needed: int) -> None:
    """Background task: generate sentences + audio for a newly introduced word."""
    db = SessionLocal()
    try:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        if not lemma:
            return

        # Build known words list for the LLM prompt
        all_lemmas = (
            db.query(Lemma)
            .join(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.fsrs_card_json.isnot(None))
            .all()
        )
        known_words = [
            {"arabic": lem.lemma_ar, "english": lem.gloss_en or ""}
            for lem in all_lemmas
        ]

        from app.services.llm import generate_sentences_batch, AllProvidersFailed
        from app.services.sentence_validator import (
            build_lemma_lookup,
            map_tokens_to_lemmas,
            strip_diacritics,
            tokenize,
            validate_sentence,
        )

        lemma_lookup = build_lemma_lookup(all_lemmas)
        target_bare = strip_diacritics(lemma.lemma_ar)
        all_bare_forms = set(lemma_lookup.keys())

        try:
            results = generate_sentences_batch(
                target_word=lemma.lemma_ar,
                target_translation=lemma.gloss_en or "",
                known_words=known_words,
                count=needed + 1,
                difficulty_hint="beginner",
            )
        except AllProvidersFailed:
            logger.warning(f"LLM unavailable for sentence generation (lemma {lemma_id})")
            return

        stored = 0
        for res in results:
            if stored >= needed:
                break

            validation = validate_sentence(
                arabic_text=res.arabic,
                target_bare=target_bare,
                known_bare_forms=all_bare_forms,
            )
            if not validation.valid:
                continue

            sent = Sentence(
                arabic_text=res.arabic,
                arabic_diacritized=res.arabic,
                english_translation=res.english,
                transliteration=res.transliteration,
                source="llm",
                target_lemma_id=lemma.lemma_id,
            )
            db.add(sent)
            db.flush()

            tokens = tokenize(res.arabic)
            mappings = map_tokens_to_lemmas(
                tokens=tokens,
                lemma_lookup=lemma_lookup,
                target_lemma_id=lemma.lemma_id,
                target_bare=target_bare,
            )
            for m in mappings:
                sw = SentenceWord(
                    sentence_id=sent.id,
                    position=m.position,
                    surface_form=m.surface_form,
                    lemma_id=m.lemma_id,
                    is_target_word=1 if m.is_target else 0,
                )
                db.add(sw)

            stored += 1

        db.commit()
        logger.info(f"Generated {stored} sentences for lemma {lemma_id}")

        # Fire-and-forget audio generation
        _generate_audio_for_lemma(db, lemma_id)

    except Exception:
        logger.exception(f"Error generating material for lemma {lemma_id}")
    finally:
        db.close()


def _generate_word_audio(lemma_id: int) -> None:
    """Background task: generate TTS audio for the word itself."""
    import asyncio
    from app.services.tts import (
        DEFAULT_VOICE_ID,
        TTSError,
        TTSKeyMissing,
        cache_key_for,
        generate_and_cache,
        get_cached_path,
    )

    db = SessionLocal()
    try:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        if not lemma or lemma.audio_url:
            return

        key = cache_key_for(lemma.lemma_ar, DEFAULT_VOICE_ID)
        if get_cached_path(key):
            lemma.audio_url = f"/api/tts/audio/{key}.mp3"
            db.commit()
            return

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                generate_and_cache(lemma.lemma_ar, DEFAULT_VOICE_ID, cache_key=key)
            )
            lemma.audio_url = f"/api/tts/audio/{key}.mp3"
            db.commit()
        except (TTSError, TTSKeyMissing):
            logger.warning(f"TTS failed for word {lemma_id}")
        finally:
            loop.close()
    except Exception:
        logger.exception(f"Error generating word audio for lemma {lemma_id}")
    finally:
        db.close()


def _generate_audio_for_lemma(db: Session, lemma_id: int) -> None:
    """Generate TTS audio for sentences of a newly introduced word."""
    from app.services.tts import (
        DEFAULT_VOICE_ID,
        TTSError,
        TTSKeyMissing,
        cache_key_for,
        generate_and_cache,
        get_cached_path,
    )

    sentences = (
        db.query(Sentence)
        .filter(
            Sentence.target_lemma_id == lemma_id,
            Sentence.audio_url.is_(None),
        )
        .all()
    )

    if not sentences:
        return

    loop = asyncio.new_event_loop()
    try:
        for sent in sentences:
            key = cache_key_for(sent.arabic_text, DEFAULT_VOICE_ID)
            if get_cached_path(key):
                sent.audio_url = f"/api/tts/audio/{key}.mp3"
                continue
            try:
                path = loop.run_until_complete(
                    generate_and_cache(sent.arabic_text, DEFAULT_VOICE_ID, cache_key=key)
                )
                sent.audio_url = f"/api/tts/audio/{key}.mp3"
            except (TTSError, TTSKeyMissing):
                logger.warning(f"TTS failed for sentence {sent.id}")
                continue

        db.commit()
    finally:
        loop.close()
