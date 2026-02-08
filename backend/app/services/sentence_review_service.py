"""Sentence-level review submission.

Translates sentence comprehension signals into per-word FSRS reviews.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models import (
    ReviewLog,
    Sentence,
    SentenceReviewLog,
    SentenceWord,
    UserLemmaKnowledge,
)
from app.services.fsrs_service import submit_review


def submit_sentence_review(
    db: Session,
    sentence_id: Optional[int],
    primary_lemma_id: int,
    comprehension_signal: str,
    missed_lemma_ids: list[int] | None = None,
    response_ms: Optional[int] = None,
    session_id: Optional[str] = None,
    review_mode: str = "reading",
) -> dict:
    """Submit a review for a whole sentence, distributing ratings to words.

    - "understood" -> all words with FSRS cards get rating=3
    - "partial" + missed_lemma_ids -> missed get rating=1, rest get rating=3
    - "no_idea" -> all words get rating=1

    Words without UserLemmaKnowledge get encounter-only tracking.
    """
    now = datetime.now(timezone.utc)
    missed_set = set(missed_lemma_ids or [])

    # Collect lemma_ids from sentence words, or just primary for word-only items
    lemma_ids_in_sentence: set[int] = set()
    if sentence_id is not None:
        sentence_words = (
            db.query(SentenceWord)
            .filter(SentenceWord.sentence_id == sentence_id)
            .all()
        )
        lemma_ids_in_sentence = {sw.lemma_id for sw in sentence_words if sw.lemma_id}
    else:
        lemma_ids_in_sentence = {primary_lemma_id}

    word_results = []

    for lemma_id in lemma_ids_in_sentence:
        if comprehension_signal == "understood":
            rating = 3
        elif comprehension_signal == "partial":
            rating = 1 if lemma_id in missed_set else 3
        else:  # no_idea
            rating = 1

        credit_type = "primary" if lemma_id == primary_lemma_id else "collateral"

        knowledge = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id == lemma_id)
            .first()
        )

        if knowledge and knowledge.fsrs_card_json:
            result = submit_review(
                db,
                lemma_id=lemma_id,
                rating_int=rating,
                response_ms=response_ms if lemma_id == primary_lemma_id else None,
                session_id=session_id,
                review_mode=review_mode,
                comprehension_signal=comprehension_signal,
            )
            # Tag the review log entry with sentence context
            latest_log = (
                db.query(ReviewLog)
                .filter(ReviewLog.lemma_id == lemma_id)
                .order_by(ReviewLog.id.desc())
                .first()
            )
            if latest_log:
                latest_log.sentence_id = sentence_id
                latest_log.credit_type = credit_type

            word_results.append({
                "lemma_id": lemma_id,
                "rating": rating,
                "credit_type": credit_type,
                "new_state": result["new_state"],
                "next_due": result["next_due"],
            })
        else:
            # No FSRS card: create or update encounter record
            if not knowledge:
                knowledge = UserLemmaKnowledge(
                    lemma_id=lemma_id,
                    knowledge_state="new",
                    source="encountered",
                    total_encounters=0,
                )
                db.add(knowledge)
            knowledge.total_encounters = (knowledge.total_encounters or 0) + 1

            word_results.append({
                "lemma_id": lemma_id,
                "rating": rating,
                "credit_type": credit_type,
                "new_state": "encountered",
                "next_due": None,
            })

    # Log the sentence-level review
    if sentence_id is not None:
        sent_log = SentenceReviewLog(
            sentence_id=sentence_id,
            session_id=session_id,
            reviewed_at=now,
            comprehension=comprehension_signal,
            response_ms=response_ms,
            review_mode=review_mode,
        )
        db.add(sent_log)

        sentence = db.query(Sentence).filter(Sentence.id == sentence_id).first()
        if sentence:
            sentence.last_shown_at = now
            sentence.times_shown = (sentence.times_shown or 0) + 1

    db.commit()

    return {"word_results": word_results}
