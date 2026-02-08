from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
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


@router.get("/next-sentences", response_model=SentenceSessionOut)
def next_sentences(
    limit: int = Query(10, ge=1, le=20),
    mode: str = Query("reading"),
    db: Session = Depends(get_db),
):
    """Get a sentence-based review session."""
    result = build_session(db, limit=limit, mode=mode)

    log_interaction(
        event="session_start",
        session_id=result["session_id"],
        review_mode=mode,
        total_due_words=result["total_due_words"],
        covered_due_words=result["covered_due_words"],
        sentence_count=len([i for i in result["items"] if i.get("sentence_id")]),
        fallback_count=len([i for i in result["items"] if not i.get("sentence_id")]),
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
