import json
import math
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    GrammarFeature,
    Lemma,
    ReviewLog,
    Sentence,
    SentenceWord,
    UserLemmaKnowledge,
)
from app.services.fsrs_service import create_new_card
from app.services.grammar_service import seed_grammar_features
from app.services.interaction_logger import log_interaction
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


def _build_grammar_details(
    db: Session,
    grammar_keys: list[str],
) -> list[dict]:
    if not grammar_keys:
        return []

    seed_grammar_features(db)
    features = (
        db.query(GrammarFeature)
        .filter(GrammarFeature.feature_key.in_(grammar_keys))
        .all()
    )
    by_key = {f.feature_key: f for f in features}

    details: list[dict] = []
    for key in grammar_keys:
        f = by_key.get(key)
        if f:
            details.append({
                "feature_key": key,
                "category": f.category,
                "label_en": f.label_en,
                "label_ar": f.label_ar,
            })
        else:
            details.append({
                "feature_key": key,
                "category": None,
                "label_en": key.replace("_", " "),
                "label_ar": None,
            })
    return details


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
            "cefr_level": lemma.cefr_level,
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

    sentence_ids_from_reviews = [r.sentence_id for r in reviews if r.sentence_id]
    sentence_ids = list(dict.fromkeys(sentence_ids_from_reviews))
    sentence_map: dict[int, Sentence] = {}
    if sentence_ids:
        sentences = db.query(Sentence).filter(Sentence.id.in_(sentence_ids)).all()
        sentence_map = {s.id: s for s in sentences}

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
            sent = sentence_map.get(r.sentence_id)
            if sent:
                entry["sentence_arabic"] = sent.arabic_diacritized or sent.arabic_text
                entry["sentence_english"] = sent.english_translation
        review_history.append(entry)

    # All sentence contexts this lemma appears in + per-sentence performance stats
    sentence_words = (
        db.query(SentenceWord)
        .filter(SentenceWord.lemma_id == lemma_id)
        .order_by(SentenceWord.sentence_id.asc(), SentenceWord.position.asc())
        .all()
    )
    sentence_surface_map: dict[int, list[str]] = {}
    sentence_order: list[int] = []
    for sw in sentence_words:
        if sw.sentence_id not in sentence_surface_map:
            sentence_surface_map[sw.sentence_id] = []
            sentence_order.append(sw.sentence_id)
        sentence_surface_map[sw.sentence_id].append(sw.surface_form)

    sentence_stats: list[dict] = []
    if sentence_order:
        stats_rows = (
            db.query(
                ReviewLog.sentence_id.label("sentence_id"),
                func.count(ReviewLog.id).label("seen_count"),
                func.sum(case((ReviewLog.rating == 1, 1), else_=0)).label("missed_count"),
                func.sum(case((ReviewLog.rating == 2, 1), else_=0)).label("confused_count"),
                func.sum(case((ReviewLog.rating >= 3, 1), else_=0)).label("understood_count"),
                func.sum(case((ReviewLog.credit_type == "primary", 1), else_=0)).label("primary_count"),
                func.sum(case((ReviewLog.credit_type == "collateral", 1), else_=0)).label("collateral_count"),
                func.max(ReviewLog.reviewed_at).label("last_reviewed_at"),
            )
            .filter(
                ReviewLog.lemma_id == lemma_id,
                ReviewLog.sentence_id.isnot(None),
                ReviewLog.sentence_id.in_(sentence_order),
            )
            .group_by(ReviewLog.sentence_id)
            .all()
        )
        stats_map = {row.sentence_id: row for row in stats_rows}

        sentence_rows = (
            db.query(Sentence)
            .filter(Sentence.id.in_(sentence_order))
            .all()
        )
        sentence_by_id = {s.id: s for s in sentence_rows}

        for sid in sentence_order:
            sent = sentence_by_id.get(sid)
            if sent is None:
                continue
            row = stats_map.get(sid)
            seen_count = int(row.seen_count) if row else 0
            missed_count = int(row.missed_count) if row and row.missed_count is not None else 0
            confused_count = int(row.confused_count) if row and row.confused_count is not None else 0
            understood_count = int(row.understood_count) if row and row.understood_count is not None else 0
            primary_count = int(row.primary_count) if row and row.primary_count is not None else 0
            collateral_count = int(row.collateral_count) if row and row.collateral_count is not None else 0
            accuracy_pct = round((understood_count / seen_count) * 100, 1) if seen_count > 0 else None

            sentence_stats.append({
                "sentence_id": sid,
                "surface_forms": sentence_surface_map.get(sid, []),
                "sentence_arabic": sent.arabic_diacritized or sent.arabic_text,
                "sentence_english": sent.english_translation,
                "sentence_transliteration": sent.transliteration,
                "seen_count": seen_count,
                "missed_count": missed_count,
                "confused_count": confused_count,
                "understood_count": understood_count,
                "primary_count": primary_count,
                "collateral_count": collateral_count,
                "accuracy_pct": accuracy_pct,
                "last_reviewed_at": row.last_reviewed_at.isoformat() if row and row.last_reviewed_at else None,
            })

    ts = k.times_seen if k else 0
    tc = k.times_correct if k else 0
    score = knowledge_score(k.fsrs_card_json if k else None, ts, tc)
    grammar_keys = _coerce_grammar_keys(lemma.grammar_features_json)
    grammar_details = _build_grammar_details(db, grammar_keys)

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
        "cefr_level": lemma.cefr_level,
        "audio_url": lemma.audio_url,
        "times_seen": ts,
        "times_correct": tc,
        "knowledge_score": score,
        "forms_json": lemma.forms_json,
        "grammar_features": grammar_details,
        "root_family": root_family,
        "review_history": review_history,
        "sentence_stats": sentence_stats,
    }


@router.post("/{lemma_id}/suspend")
def suspend_word(lemma_id: int, db: Session = Depends(get_db)):
    """Suspend a word — stops appearing in reviews."""
    lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
    if not lemma:
        raise HTTPException(404, "Word not found")

    existing = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )

    if existing:
        previous_state = existing.knowledge_state
        if previous_state == "suspended":
            return {"lemma_id": lemma_id, "state": "suspended", "already_suspended": True}
        existing.knowledge_state = "suspended"
        db.commit()
    else:
        previous_state = None
        ulk = UserLemmaKnowledge(
            lemma_id=lemma_id,
            knowledge_state="suspended",
            source="study",
        )
        db.add(ulk)
        db.commit()

    log_interaction(event="word_suspended", lemma_id=lemma_id, previous_state=previous_state)
    return {"lemma_id": lemma_id, "state": "suspended", "previous_state": previous_state}


@router.post("/{lemma_id}/unsuspend")
def unsuspend_word(lemma_id: int, db: Session = Depends(get_db)):
    """Reactivate a suspended word with a fresh FSRS card."""
    ulk = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id)
        .first()
    )
    if not ulk:
        raise HTTPException(404, "No knowledge record for this word")
    if ulk.knowledge_state != "suspended":
        return {"lemma_id": lemma_id, "state": ulk.knowledge_state, "was_suspended": False}

    ulk.knowledge_state = "learning"
    ulk.fsrs_card_json = create_new_card()
    db.commit()

    log_interaction(event="word_unsuspended", lemma_id=lemma_id)
    return {"lemma_id": lemma_id, "state": "learning", "was_suspended": True}
