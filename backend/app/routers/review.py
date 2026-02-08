import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db, SessionLocal
from app.models import Lemma, Root, Sentence, UserLemmaKnowledge
from app.schemas import (
    BulkSyncIn,
    ReviewCardOut,
    ReviewSubmitIn,
    ReviewSubmitOut,
    SentenceSessionOut,
    SentenceReviewSubmitIn,
    SentenceReviewSubmitOut,
)
from app.services.fsrs_service import get_due_cards, submit_review
from app.services.listening import get_listening_candidates, process_comprehension_signal
from app.services.interaction_logger import log_interaction
from app.services.sentence_selector import build_session
from app.services.sentence_review_service import submit_sentence_review
from app.services.word_selector import introduce_word
from app.routers.learn import (
    _generate_material_for_word,
    _generate_word_audio,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review", tags=["review"])


@router.get("/next", response_model=list[ReviewCardOut])
def next_cards(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    return get_due_cards(db, limit)


@router.get("/next-listening")
def next_listening_cards(
    limit: int = Query(10, ge=1, le=50),
    max_words: int = Query(10, ge=3, le=20),
    min_confidence: float = Query(0.6, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
):
    """Get due cards suitable for listening practice.

    Only returns cards where the sentence words (excluding target)
    are well-known enough for the user to focus on aural recognition.
    """
    return get_listening_candidates(
        db, limit=limit, max_word_count=max_words, min_confidence=min_confidence
    )


@router.post("/submit", response_model=ReviewSubmitOut)
def submit(body: ReviewSubmitIn, db: Session = Depends(get_db)):
    result = submit_review(
        db,
        lemma_id=body.lemma_id,
        rating_int=body.rating,
        response_ms=body.response_ms,
        session_id=body.session_id,
        review_mode=body.review_mode,
        comprehension_signal=body.comprehension_signal,
        client_review_id=body.client_review_id,
    )

    log_interaction(
        event="review",
        lemma_id=body.lemma_id,
        rating=body.rating,
        response_ms=body.response_ms,
        session_id=body.session_id,
        review_mode=body.review_mode,
        comprehension_signal=body.comprehension_signal,
    )

    # Process additional comprehension signals (missed words in listening, etc.)
    if body.comprehension_signal and body.missed_word_lemma_ids:
        process_comprehension_signal(
            db,
            session_id=body.session_id,
            review_mode=body.review_mode,
            comprehension_signal=body.comprehension_signal,
            target_lemma_id=body.lemma_id,
            missed_word_lemma_ids=body.missed_word_lemma_ids,
        )

    return result


MIN_SENTENCES_PER_WORD = 3


@router.get("/next-sentences", response_model=SentenceSessionOut)
def next_sentences(
    limit: int = Query(10, ge=1, le=20),
    mode: str = Query("reading"),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    """Get a sentence-based review session."""
    result = build_session(db, limit=limit, mode=mode)

    # Auto-introduce intro candidates and pre-generate material
    for cand in result.get("intro_candidates", []):
        try:
            intro_result = introduce_word(db, cand["lemma_id"])
            if not intro_result.get("already_known") and background_tasks:
                existing_count = (
                    db.query(func.count(Sentence.id))
                    .filter(Sentence.target_lemma_id == cand["lemma_id"])
                    .scalar() or 0
                )
                if existing_count < MIN_SENTENCES_PER_WORD:
                    needed = MIN_SENTENCES_PER_WORD - existing_count
                    background_tasks.add_task(
                        _generate_material_for_word, cand["lemma_id"], needed
                    )
                lemma = db.query(Lemma).filter(Lemma.lemma_id == cand["lemma_id"]).first()
                if lemma and not lemma.audio_url and background_tasks:
                    background_tasks.add_task(_generate_word_audio, cand["lemma_id"])
            log_interaction(event="word_auto_introduced", lemma_id=cand["lemma_id"])
        except Exception:
            logger.exception("Failed to auto-introduce lemma %s", cand["lemma_id"])

    log_interaction(
        event="session_start",
        session_id=result["session_id"],
        review_mode=mode,
        total_due_words=result["total_due_words"],
        covered_due_words=result["covered_due_words"],
        sentence_count=len([i for i in result["items"] if i.get("sentence_id")]),
        fallback_count=len([i for i in result["items"] if not i.get("sentence_id")]),
        auto_introduced=len(result.get("intro_candidates", [])),
    )

    return result


@router.post("/submit-sentence", response_model=SentenceReviewSubmitOut)
def submit_sentence(body: SentenceReviewSubmitIn, db: Session = Depends(get_db)):
    """Submit a sentence-level review."""
    result = submit_sentence_review(
        db,
        sentence_id=body.sentence_id,
        primary_lemma_id=body.primary_lemma_id,
        comprehension_signal=body.comprehension_signal,
        missed_lemma_ids=body.missed_lemma_ids,
        response_ms=body.response_ms,
        session_id=body.session_id,
        review_mode=body.review_mode,
        client_review_id=body.client_review_id,
    )

    log_interaction(
        event="sentence_review",
        sentence_id=body.sentence_id,
        lemma_id=body.primary_lemma_id,
        comprehension_signal=body.comprehension_signal,
        missed_lemma_ids=body.missed_lemma_ids,
        response_ms=body.response_ms,
        session_id=body.session_id,
        review_mode=body.review_mode,
        words_reviewed=len(result.get("word_results", [])),
        collateral_count=len([w for w in result.get("word_results", []) if w.get("credit_type") == "collateral"]),
    )

    return result


@router.get("/word-lookup/{lemma_id}")
def word_lookup(lemma_id: int, db: Session = Depends(get_db)):
    """Look up a word's details during sentence review. Returns root family for known-root prediction."""
    lemma = db.query(Lemma).options(joinedload(Lemma.root)).filter(Lemma.lemma_id == lemma_id).first()
    if not lemma:
        raise HTTPException(status_code=404, detail=f"Lemma {lemma_id} not found")

    root_obj = lemma.root
    result = {
        "lemma_id": lemma.lemma_id,
        "lemma_ar": lemma.lemma_ar,
        "gloss_en": lemma.gloss_en,
        "transliteration": lemma.transliteration_ala_lc,
        "root": root_obj.root if root_obj else None,
        "root_meaning": root_obj.core_meaning_en if root_obj else None,
        "root_id": root_obj.root_id if root_obj else None,
        "pos": lemma.pos,
        "root_family": [],
    }

    if root_obj:
        siblings = (
            db.query(Lemma)
            .filter(Lemma.root_id == root_obj.root_id, Lemma.lemma_id != lemma_id)
            .all()
        )
        for sib in siblings:
            sib_knowledge = (
                db.query(UserLemmaKnowledge)
                .filter(UserLemmaKnowledge.lemma_id == sib.lemma_id)
                .first()
            )
            result["root_family"].append({
                "lemma_id": sib.lemma_id,
                "lemma_ar": sib.lemma_ar,
                "gloss_en": sib.gloss_en,
                "pos": sib.pos,
                "state": sib_knowledge.knowledge_state if sib_knowledge else "new",
            })

    log_interaction(event="review_word_lookup", lemma_id=lemma_id)

    return result


@router.post("/sync")
def sync_reviews(body: BulkSyncIn, db: Session = Depends(get_db)):
    results = []
    for item in body.reviews:
        try:
            if item.type == "sentence":
                payload = item.payload
                result = submit_sentence_review(
                    db,
                    sentence_id=payload.get("sentence_id"),
                    primary_lemma_id=payload["primary_lemma_id"],
                    comprehension_signal=payload["comprehension_signal"],
                    missed_lemma_ids=payload.get("missed_lemma_ids", []),
                    response_ms=payload.get("response_ms"),
                    session_id=payload.get("session_id"),
                    review_mode=payload.get("review_mode", "reading"),
                    client_review_id=item.client_review_id,
                )
                status = "duplicate" if result.get("duplicate") else "ok"
                if status != "duplicate":
                    log_interaction(
                        event="sentence_review",
                        sentence_id=payload.get("sentence_id"),
                        lemma_id=payload["primary_lemma_id"],
                        comprehension_signal=payload["comprehension_signal"],
                        missed_lemma_ids=payload.get("missed_lemma_ids", []),
                        response_ms=payload.get("response_ms"),
                        session_id=payload.get("session_id"),
                        review_mode=payload.get("review_mode", "reading"),
                        words_reviewed=len(result.get("word_results", [])),
                        collateral_count=len([w for w in result.get("word_results", []) if w.get("credit_type") == "collateral"]),
                        source="sync",
                    )
                results.append({"client_review_id": item.client_review_id, "status": status})
            elif item.type == "legacy":
                payload = item.payload
                result = submit_review(
                    db,
                    lemma_id=payload["lemma_id"],
                    rating_int=payload["rating"],
                    response_ms=payload.get("response_ms"),
                    session_id=payload.get("session_id"),
                    review_mode=payload.get("review_mode", "reading"),
                    comprehension_signal=payload.get("comprehension_signal"),
                    client_review_id=item.client_review_id,
                )
                status = "duplicate" if result.get("duplicate") else "ok"
                if status != "duplicate":
                    log_interaction(
                        event="legacy_review",
                        lemma_id=payload["lemma_id"],
                        rating=payload["rating"],
                        response_ms=payload.get("response_ms"),
                        session_id=payload.get("session_id"),
                        review_mode=payload.get("review_mode", "reading"),
                        source="sync",
                    )
                results.append({"client_review_id": item.client_review_id, "status": status})
            else:
                results.append({"client_review_id": item.client_review_id, "status": "error", "error": f"Unknown type: {item.type}"})
        except Exception as e:
            results.append({"client_review_id": item.client_review_id, "status": "error", "error": str(e)})
    return {"results": results}
