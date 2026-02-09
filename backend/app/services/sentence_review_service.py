"""Sentence-level review submission.

Translates sentence comprehension signals into per-word FSRS reviews.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models import (
    Lemma,
    ReviewLog,
    Sentence,
    SentenceReviewLog,
    SentenceWord,
    UserLemmaKnowledge,
)
from app.services.fsrs_service import submit_review
from app.services.sentence_validator import strip_diacritics


def submit_sentence_review(
    db: Session,
    sentence_id: Optional[int],
    primary_lemma_id: int,
    comprehension_signal: str,
    missed_lemma_ids: list[int] | None = None,
    confused_lemma_ids: list[int] | None = None,
    response_ms: Optional[int] = None,
    session_id: Optional[str] = None,
    review_mode: str = "reading",
    client_review_id: Optional[str] = None,
) -> dict:
    """Submit a review for a whole sentence, distributing ratings to words.

    - "understood" -> all words get rating=3
    - "partial" + missed/confused -> missed get rating=1, confused get rating=2, rest get rating=3
    - "no_idea" -> all words get rating=1

    All words (including previously unseen) get full FSRS cards.
    """
    if client_review_id:
        if sentence_id is not None:
            existing = (
                db.query(SentenceReviewLog)
                .filter(SentenceReviewLog.client_review_id == client_review_id)
                .first()
            )
            if existing:
                return {"word_results": [], "duplicate": True}
        else:
            # Word-only sentence items do not create SentenceReviewLog rows.
            # Use the primary ReviewLog's client_review_id for idempotency.
            existing_primary = (
                db.query(ReviewLog)
                .filter(ReviewLog.client_review_id == client_review_id)
                .first()
            )
            if existing_primary:
                return {"word_results": [], "duplicate": True}

    now = datetime.now(timezone.utc)
    missed_set = set(missed_lemma_ids or [])
    confused_set = set(confused_lemma_ids or [])

    # Collect lemma_ids from sentence words, or just primary for word-only items
    lemma_ids_in_sentence: set[int] = set()
    surface_forms_by_lemma: dict[int, list[str]] = {}
    if sentence_id is not None:
        sentence_words = (
            db.query(SentenceWord)
            .filter(SentenceWord.sentence_id == sentence_id)
            .all()
        )
        lemma_ids_in_sentence = {sw.lemma_id for sw in sentence_words if sw.lemma_id}
        for sw in sentence_words:
            if sw.lemma_id:
                surface_forms_by_lemma.setdefault(sw.lemma_id, []).append(sw.surface_form)
    else:
        lemma_ids_in_sentence = {primary_lemma_id}

    word_results = []

    for lemma_id in lemma_ids_in_sentence:
        if comprehension_signal == "understood":
            rating = 3
        elif comprehension_signal == "partial":
            if lemma_id in missed_set:
                rating = 1
            elif lemma_id in confused_set:
                rating = 2
            else:
                rating = 3
        else:  # no_idea
            rating = 1

        credit_type = "primary" if lemma_id == primary_lemma_id else "collateral"

        result = submit_review(
            db,
            lemma_id=lemma_id,
            rating_int=rating,
            response_ms=response_ms if lemma_id == primary_lemma_id else None,
            session_id=session_id,
            review_mode=review_mode,
            comprehension_signal=comprehension_signal,
            client_review_id=(
                client_review_id
                if sentence_id is None and lemma_id == primary_lemma_id
                else None
            ),
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

        # Track encounters
        knowledge = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id == lemma_id)
            .first()
        )
        if knowledge:
            knowledge.total_encounters = (knowledge.total_encounters or 0) + 1

            # Track variant form stats
            if lemma_id in surface_forms_by_lemma:
                lemma_obj = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
                if lemma_obj:
                    lemma_bare = lemma_obj.lemma_ar_bare or ""
                    for surface in surface_forms_by_lemma[lemma_id]:
                        surface_bare = strip_diacritics(surface)
                        if surface_bare and surface_bare != lemma_bare:
                            vstats = knowledge.variant_stats_json or {}
                            if isinstance(vstats, str):
                                import json
                                vstats = json.loads(vstats)
                            vstats = dict(vstats)
                            entry = vstats.get(surface_bare, {"seen": 0, "missed": 0, "confused": 0})
                            entry["seen"] = entry.get("seen", 0) + 1
                            if rating == 1:
                                entry["missed"] = entry.get("missed", 0) + 1
                            elif rating == 2:
                                entry["confused"] = entry.get("confused", 0) + 1
                            vstats[surface_bare] = entry
                            knowledge.variant_stats_json = vstats

        word_results.append({
            "lemma_id": lemma_id,
            "rating": rating,
            "credit_type": credit_type,
            "new_state": result["new_state"],
            "next_due": result["next_due"],
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
            client_review_id=client_review_id,
        )
        db.add(sent_log)

        sentence = db.query(Sentence).filter(Sentence.id == sentence_id).first()
        if sentence:
            sentence.times_shown = (sentence.times_shown or 0) + 1
            if review_mode == "listening":
                sentence.last_listening_shown_at = now
                sentence.last_listening_comprehension = comprehension_signal
            else:
                sentence.last_reading_shown_at = now
                sentence.last_reading_comprehension = comprehension_signal

    db.commit()

    return {"word_results": word_results}
