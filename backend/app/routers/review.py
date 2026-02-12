import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db, SessionLocal
from app.models import GrammarFeature, Lemma, Root, UserLemmaKnowledge
from app.schemas import (
    BulkSyncIn,
    ReintroResultIn,
    SentenceSessionOut,
    SentenceReviewSubmitIn,
    SentenceReviewSubmitOut,
    WrapUpIn,
    WrapUpOut,
    WrapUpCardOut,
    RecapIn,
)
from app.services.fsrs_service import submit_review
from app.services.listening import get_listening_candidates
from app.services.interaction_logger import log_interaction
from app.services.sentence_selector import build_session
from app.services.sentence_review_service import submit_sentence_review, undo_sentence_review
from app.services.sentence_validator import _is_function_word

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review", tags=["review"])


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


@router.get("/next-sentences", response_model=SentenceSessionOut)
def next_sentences(
    limit: int = Query(10, ge=1, le=20),
    mode: str = Query("reading"),
    prefetch: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Get a sentence-based review session."""
    result = build_session(db, limit=limit, mode=mode, log_events=not prefetch)

    # Listening mode is only for already-learned words — no intro candidates
    if mode == "listening":
        result["intro_candidates"] = []

    if not prefetch:
        log_interaction(
            event="session_start",
            session_id=result["session_id"],
            review_mode=mode,
            total_due_words=result["total_due_words"],
            covered_due_words=result["covered_due_words"],
            sentence_count=len([i for i in result["items"] if i.get("sentence_id")]),
            fallback_count=len([i for i in result["items"] if not i.get("sentence_id")]),
            intro_candidates=len(result.get("intro_candidates", [])),
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
        confused_lemma_ids=body.confused_lemma_ids,
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
        confused_lemma_ids=body.confused_lemma_ids,
        response_ms=body.response_ms,
        session_id=body.session_id,
        review_mode=body.review_mode,
        words_reviewed=len(result.get("word_results", [])),
        collateral_count=len([w for w in result.get("word_results", []) if w.get("credit_type") == "collateral"]),
        word_ratings={w["lemma_id"]: w["rating"] for w in result.get("word_results", []) if "lemma_id" in w and "rating" in w},
        audio_play_count=body.audio_play_count,
        lookup_count=body.lookup_count,
    )

    return result


@router.get("/word-lookup/{lemma_id}")
def word_lookup(lemma_id: int, db: Session = Depends(get_db)):
    """Look up a word's details during sentence review. Returns root family for known-root prediction."""
    lemma = db.query(Lemma).options(joinedload(Lemma.root)).filter(Lemma.lemma_id == lemma_id).first()
    if not lemma:
        raise HTTPException(status_code=404, detail=f"Lemma {lemma_id} not found")

    root_obj = lemma.root

    # Parse grammar features
    grammar_keys = []
    if lemma.grammar_features_json:
        raw = lemma.grammar_features_json
        if isinstance(raw, str):
            import json
            try:
                raw = json.loads(raw)
            except Exception:
                raw = []
        if isinstance(raw, list):
            grammar_keys = [k for k in raw if isinstance(k, str)]

    grammar_details = []
    if grammar_keys:
        rows = db.query(GrammarFeature).filter(GrammarFeature.feature_key.in_(grammar_keys)).all()
        by_key = {r.feature_key: r for r in rows}
        for key in grammar_keys:
            feat = by_key.get(key)
            if feat:
                grammar_details.append({
                    "feature_key": key,
                    "category": feat.category,
                    "label_en": feat.label_en,
                    "label_ar": feat.label_ar,
                })
            else:
                grammar_details.append({
                    "feature_key": key,
                    "category": None,
                    "label_en": key.replace("_", " "),
                    "label_ar": None,
                })

    result = {
        "lemma_id": lemma.lemma_id,
        "lemma_ar": lemma.lemma_ar,
        "gloss_en": lemma.gloss_en,
        "transliteration": lemma.transliteration_ala_lc,
        "root": root_obj.root if root_obj else None,
        "root_meaning": root_obj.core_meaning_en if root_obj else None,
        "root_id": root_obj.root_id if root_obj else None,
        "pos": lemma.pos,
        "forms_json": lemma.forms_json,
        "example_ar": lemma.example_ar,
        "example_en": lemma.example_en,
        "grammar_details": grammar_details,
        "is_function_word": _is_function_word(lemma.lemma_ar_bare) if lemma.lemma_ar_bare else False,
        "frequency_rank": lemma.frequency_rank,
        "cefr_level": lemma.cefr_level,
        "root_family": [],
    }

    if root_obj:
        siblings = (
            db.query(Lemma)
            .filter(Lemma.root_id == root_obj.root_id, Lemma.lemma_id != lemma_id, Lemma.canonical_lemma_id.is_(None))
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
                "transliteration": sib.transliteration_ala_lc,
                "state": sib_knowledge.knowledge_state if sib_knowledge else "new",
            })

    log_interaction(
        event="review_word_lookup",
        lemma_id=lemma_id,
        word_ar=lemma.lemma_ar,
        word_en=lemma.gloss_en,
        root=root_obj.root if root_obj else None,
    )

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
                    confused_lemma_ids=payload.get("confused_lemma_ids", []),
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
                        confused_lemma_ids=payload.get("confused_lemma_ids", []),
                        response_ms=payload.get("response_ms"),
                        session_id=payload.get("session_id"),
                        review_mode=payload.get("review_mode", "reading"),
                        words_reviewed=len(result.get("word_results", [])),
                        collateral_count=len([w for w in result.get("word_results", []) if w.get("credit_type") == "collateral"]),
                        word_ratings={w["lemma_id"]: w["rating"] for w in result.get("word_results", []) if "lemma_id" in w and "rating" in w},
                        audio_play_count=payload.get("audio_play_count"),
                        lookup_count=payload.get("lookup_count"),
                        source="sync",
                    )
                results.append({"client_review_id": item.client_review_id, "status": status})
            else:
                results.append({"client_review_id": item.client_review_id, "status": "error", "error": f"Unknown type: {item.type}"})
        except Exception as e:
            db.rollback()
            results.append({"client_review_id": item.client_review_id, "status": "error", "error": str(e)})
    return {"results": results}


@router.post("/reintro-result")
def submit_reintro_result(
    body: ReintroResultIn,
    db: Session = Depends(get_db),
):
    """Submit result of a re-introduction card: 'remember' or 'show_again'."""
    rating = 3 if body.result == "remember" else 1

    result = submit_review(
        db,
        lemma_id=body.lemma_id,
        rating_int=rating,
        session_id=body.session_id,
        review_mode="reintro",
        comprehension_signal="understood" if body.result == "remember" else "no_idea",
        client_review_id=body.client_review_id,
    )

    log_interaction(
        event=f"reintro_{body.result}",
        lemma_id=body.lemma_id,
        rating=rating,
        session_id=body.session_id,
    )

    return {"status": "ok", "result": body.result, "lemma_id": body.lemma_id}


@router.post("/wrap-up", response_model=WrapUpOut)
def wrap_up_quiz(body: WrapUpIn, db: Session = Depends(get_db)):
    """Get word-level recall cards for acquiring and missed words in current session."""
    if not body.seen_lemma_ids and not body.missed_lemma_ids:
        return {"cards": []}

    # Acquiring words seen in session
    acquiring_ids: set[int] = set()
    if body.seen_lemma_ids:
        ulks = (
            db.query(UserLemmaKnowledge)
            .filter(
                UserLemmaKnowledge.lemma_id.in_(body.seen_lemma_ids),
                UserLemmaKnowledge.knowledge_state == "acquiring",
            )
            .all()
        )
        acquiring_ids = {u.lemma_id for u in ulks}

    # Missed words (non-function, with active FSRS cards)
    missed_ids: set[int] = set()
    if body.missed_lemma_ids:
        missed_ulks = (
            db.query(UserLemmaKnowledge)
            .filter(
                UserLemmaKnowledge.lemma_id.in_(body.missed_lemma_ids),
                UserLemmaKnowledge.knowledge_state.in_(["learning", "known", "lapsed", "acquiring"]),
            )
            .all()
        )
        missed_ids = {u.lemma_id for u in missed_ulks} - acquiring_ids

    all_ids = acquiring_ids | missed_ids
    if not all_ids:
        return {"cards": []}

    lemmas = (
        db.query(Lemma)
        .filter(Lemma.lemma_id.in_(all_ids))
        .all()
    )

    cards = []
    # Acquiring words first, then missed
    for lemma in sorted(lemmas, key=lambda l: (l.lemma_id not in acquiring_ids, l.lemma_id)):
        root_obj = lemma.root
        cards.append(WrapUpCardOut(
            lemma_id=lemma.lemma_id,
            lemma_ar=lemma.lemma_ar,
            lemma_ar_bare=lemma.lemma_ar_bare,
            gloss_en=lemma.gloss_en,
            transliteration=lemma.transliteration_ala_lc,
            pos=lemma.pos,
            forms_json=lemma.forms_json,
            root=root_obj.root if root_obj else None,
            root_meaning=root_obj.core_meaning_en if root_obj else None,
            etymology_json=lemma.etymology_json,
            is_acquiring=lemma.lemma_id in acquiring_ids,
        ))

    log_interaction(
        event="wrap_up_quiz",
        session_id=body.session_id,
        card_count=len(cards),
        acquiring_count=len(acquiring_ids),
        missed_count=len(missed_ids),
    )

    return {"cards": cards}


@router.post("/recap")
def get_recap_items(body: RecapIn, db: Session = Depends(get_db)):
    """Get sentence-level recap cards for acquisition words from last session."""
    from app.models import Sentence, SentenceWord

    if not body.last_session_lemma_ids:
        return {"items": []}

    # Filter to still-acquiring words
    ulks = (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.lemma_id.in_(body.last_session_lemma_ids),
            UserLemmaKnowledge.knowledge_state == "acquiring",
        )
        .all()
    )
    acquiring_ids = {u.lemma_id for u in ulks}

    if not acquiring_ids:
        return {"items": []}

    # Find sentences for these words (prefer different ones from last session)
    sentence_words = (
        db.query(SentenceWord)
        .filter(SentenceWord.lemma_id.in_(acquiring_ids))
        .all()
    )
    sentence_ids = {sw.sentence_id for sw in sentence_words}

    if not sentence_ids:
        return {"items": []}

    sentences = (
        db.query(Sentence)
        .filter(Sentence.id.in_(sentence_ids), Sentence.is_active == True)
        .limit(3)
        .all()
    )

    # Build session items (reuse build_session item format)
    items = []
    for sent in sentences:
        items.append({
            "sentence_id": sent.id,
            "arabic_text": sent.arabic_diacritized or sent.arabic_text,
            "english_translation": sent.english_translation,
            "transliteration": sent.transliteration,
            "audio_url": sent.audio_url,
            "is_recap": True,
        })

    return {"items": items, "recap_word_count": len(acquiring_ids)}


@router.post("/undo-sentence")
def undo_sentence(
    body: dict,
    db: Session = Depends(get_db),
):
    """Undo a previously submitted sentence review, restoring pre-review FSRS state."""
    client_review_id = body.get("client_review_id")
    if not client_review_id:
        raise HTTPException(400, "client_review_id required")

    result = undo_sentence_review(db, client_review_id)

    if result["undone"]:
        log_interaction(
            event="review_undone",
            client_review_id=client_review_id,
            reviews_removed=result["reviews_removed"],
        )

    return result
