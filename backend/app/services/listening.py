"""Listening comprehension service.

Selects sentences the user is likely to understand aurally.
For listening mode, we need higher confidence than reading —
the user can't rely on visual cues, so surrounding words must
be well-known (not just "learning").

Also handles comprehension signal processing — when a user marks
"didn't catch any of that" or specific missed words in listening mode,
we need to downgrade those words' listening confidence without necessarily
treating them as failed reading reviews.
"""

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import (
    Lemma, UserLemmaKnowledge, Sentence, SentenceWord, ReviewLog,
)


# Minimum FSRS stability (in days) for a word to be considered
# "listening-ready" — the user must know the word well enough
# that they can recognize it without seeing it written
MIN_LISTENING_STABILITY_DAYS = 7.0

# Maximum sentence length for listening mode
# (shorter sentences are easier to parse aurally)
MAX_LISTENING_WORDS = 10

# Minimum times a word must have been reviewed to be listening-ready
MIN_REVIEWS_FOR_LISTENING = 3


def _get_word_listening_confidence(knowledge: Optional[UserLemmaKnowledge]) -> float:
    """Score how confidently a user knows a word for listening (0.0-1.0).

    Higher scores mean the word is very well-known and unlikely to
    cause confusion in a listening context.
    """
    if knowledge is None:
        return 0.0

    if knowledge.knowledge_state == "new":
        return 0.0

    if knowledge.knowledge_state == "lapsed":
        return 0.1

    times_seen = knowledge.times_seen or 0
    if times_seen < MIN_REVIEWS_FOR_LISTENING:
        return 0.2

    # Check FSRS stability
    stability_days = 0.0
    if knowledge.fsrs_card_json:
        card_data = knowledge.fsrs_card_json
        if isinstance(card_data, str):
            card_data = json.loads(card_data)
        stability_days = card_data.get("stability", 0.0)

    if stability_days < 1.0:
        return 0.3
    if stability_days < MIN_LISTENING_STABILITY_DAYS:
        return 0.5
    if stability_days < 30.0:
        return 0.7

    # Very well-known word
    accuracy = (knowledge.times_correct or 0) / max(times_seen, 1)
    return min(0.7 + accuracy * 0.3, 1.0)


def score_sentence_for_listening(
    db: Session,
    sentence_id: int,
    target_lemma_id: Optional[int] = None,
) -> dict:
    """Score how suitable a sentence is for listening practice.

    Returns a dict with:
    - confidence: 0.0-1.0 overall confidence
    - word_count: number of words
    - weakest_word: the word with lowest confidence (excluding target)
    - all_words_known: whether all words are known state
    """
    words = (
        db.query(SentenceWord)
        .filter(SentenceWord.sentence_id == sentence_id)
        .order_by(SentenceWord.position)
        .all()
    )

    if not words:
        return {"confidence": 0.0, "word_count": 0, "all_words_known": False}

    confidences = []
    weakest = None
    weakest_conf = 1.0

    for sw in words:
        if sw.lemma_id is None:
            # Function word or unlinked — assume known
            confidences.append(0.9)
            continue

        if sw.lemma_id == target_lemma_id:
            # Skip target word — we're testing this one
            continue

        knowledge = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id == sw.lemma_id)
            .first()
        )
        conf = _get_word_listening_confidence(knowledge)
        confidences.append(conf)

        if conf < weakest_conf:
            weakest_conf = conf
            lemma = db.query(Lemma).filter(Lemma.lemma_id == sw.lemma_id).first()
            weakest = {
                "lemma_id": sw.lemma_id,
                "lemma_ar": lemma.lemma_ar if lemma else "?",
                "confidence": conf,
            }

    if not confidences:
        return {"confidence": 0.0, "word_count": len(words), "all_words_known": False}

    # Overall confidence is the minimum word confidence
    # (chain is only as strong as weakest link for listening)
    min_conf = min(confidences)
    avg_conf = sum(confidences) / len(confidences)

    return {
        "confidence": round(min_conf * 0.6 + avg_conf * 0.4, 3),
        "word_count": len(words),
        "all_words_known": min_conf >= 0.5,
        "weakest_word": weakest,
    }


def get_listening_candidates(
    db: Session,
    limit: int = 10,
    max_word_count: int = MAX_LISTENING_WORDS,
    min_confidence: float = 0.6,
) -> list[dict]:
    """Get sentences suitable for listening practice.

    Returns due cards where the sentence's non-target words are all
    well-known, so the user can focus on aural recognition.
    """
    now = datetime.now(timezone.utc)

    # Get all due knowledge records
    knowledges = (
        db.query(UserLemmaKnowledge)
        .join(Lemma)
        .filter(UserLemmaKnowledge.fsrs_card_json.isnot(None))
        .all()
    )

    due_items = []
    for k in knowledges:
        card_data = k.fsrs_card_json
        if isinstance(card_data, str):
            card_data = json.loads(card_data)
        due_str = card_data.get("due")
        if due_str:
            due_dt = datetime.fromisoformat(due_str)
            if due_dt.tzinfo is None:
                due_dt = due_dt.replace(tzinfo=timezone.utc)
            if due_dt <= now:
                due_items.append((k, due_dt))

    due_items.sort(key=lambda x: x[1])

    # For each due item, find a suitable sentence
    results = []
    for k, due_dt in due_items:
        if len(results) >= limit:
            break

        lemma = k.lemma

        # Find sentences for this lemma that are short enough
        sentences = (
            db.query(Sentence)
            .filter(Sentence.target_lemma_id == lemma.lemma_id)
            .filter(
                (Sentence.max_word_count <= max_word_count)
                | (Sentence.max_word_count.is_(None))
            )
            .all()
        )

        best_sentence = None
        best_confidence = 0.0

        for sent in sentences:
            score = score_sentence_for_listening(db, sent.id, lemma.lemma_id)
            if score["confidence"] >= min_confidence and score["confidence"] > best_confidence:
                best_confidence = score["confidence"]
                best_sentence = sent

        if best_sentence:
            results.append({
                "lemma_id": lemma.lemma_id,
                "lemma_ar": lemma.lemma_ar,
                "lemma_ar_bare": lemma.lemma_ar_bare,
                "gloss_en": lemma.gloss_en,
                "audio_url": lemma.audio_url,
                "knowledge_state": k.knowledge_state,
                "due": due_dt.isoformat(),
                "sentence": {
                    "id": best_sentence.id,
                    "arabic": best_sentence.arabic_diacritized or best_sentence.arabic_text,
                    "english": best_sentence.english_translation,
                    "transliteration": best_sentence.transliteration,
                    "audio_url": best_sentence.audio_url,
                },
                "listening_confidence": best_confidence,
            })

    return results


def process_comprehension_signal(
    db: Session,
    session_id: Optional[str],
    review_mode: str,
    comprehension_signal: str,
    target_lemma_id: int,
    missed_word_lemma_ids: Optional[list[int]] = None,
) -> list[dict]:
    """Process comprehension signals and create additional review log entries.

    When user says "no_idea" in listening mode, we log a mild negative signal
    for all sentence words. When specific words are missed, we log those individually.

    Returns list of additional log entries created.
    """
    additional_logs = []
    now = datetime.now(timezone.utc)

    if comprehension_signal == "no_idea" and review_mode == "listening":
        # Sentence-level failure in listening: mild negative for target word
        # The main review submission already handles the target word rating,
        # so we just log the signal
        additional_logs.append({
            "lemma_id": target_lemma_id,
            "signal": "listening_no_idea",
            "review_mode": review_mode,
        })

    if missed_word_lemma_ids:
        for missed_id in missed_word_lemma_ids:
            if missed_id == target_lemma_id:
                continue  # Already handled by main review

            # Log a listening miss for non-target words
            log_entry = ReviewLog(
                lemma_id=missed_id,
                rating=2,  # Hard — listening miss is less severe than reading miss
                reviewed_at=now,
                session_id=session_id,
                review_mode=review_mode,
                comprehension_signal="partial",
                fsrs_log_json={"source": "listening_word_miss"},
            )
            db.add(log_entry)
            additional_logs.append({
                "lemma_id": missed_id,
                "signal": "listening_word_miss",
            })

    if additional_logs:
        db.commit()

    return additional_logs
