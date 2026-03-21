import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel as PydanticBaseModel
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db, SessionLocal
from app.models import GrammarFeature, Lemma, ReviewLog, Root, SentenceReviewLog, UserLemmaKnowledge
from app.schemas import (
    BulkSyncIn,
    ConfusionAnalysisOut,
    PartialRootOut,
    ReintroResultIn,
    SentenceSessionOut,
    SentenceReviewSubmitIn,
    SentenceReviewSubmitOut,
    SessionEndOut,
    SessionSummaryOut,
    WordJourneyItem,
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
from app.services.sentence_validator import _is_function_word, FUNCTION_WORDS, FUNCTION_WORD_GLOSSES, strip_diacritics
from app.services.transliteration import transliterate_arabic

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
    exclude: list[int] = Query(default=[]),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
):
    """Get a sentence-based review session.

    No LLM calls — session builds from pre-generated sentences (<1s).
    Background warm_sentence_cache generates for uncovered words after.
    """
    result = build_session(
        db, limit=limit, mode=mode,
        log_events=not prefetch,
        exclude_sentence_ids=set(exclude) if exclude else None,
    )

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
        # Trigger background generation so next session has more sentences
        from app.services.material_generator import warm_sentence_cache
        background_tasks.add_task(warm_sentence_cache)

    return result


@router.post("/warm-sentences", status_code=202)
def warm_sentences(background_tasks: BackgroundTasks):
    """Pre-generate sentences for words likely in the next session.

    Called by the frontend near the end of a session so the next session
    builds faster (sentences already in DB, no on-demand generation needed).
    Returns 202 immediately; generation runs in background.
    """
    from app.services.material_generator import warm_sentence_cache
    background_tasks.add_task(warm_sentence_cache)
    return {"status": "warming"}


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


def _compute_forms_translit(forms_json: dict | None) -> dict | None:
    if not forms_json:
        return None
    result = {}
    for key, val in forms_json.items():
        if key == "gender" or key == "verb_form" or not val or not isinstance(val, str):
            continue
        tr = transliterate_arabic(val)
        if tr:
            result[key] = tr
    return result or None


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
        "memory_hooks_json": lemma.memory_hooks_json,
        "word_category": lemma.word_category,
        "wazn": lemma.wazn,
        "wazn_meaning": lemma.wazn_meaning,
        "forms_translit": lemma.forms_translit_json or _compute_forms_translit(lemma.forms_json),
        "etymology_json": lemma.etymology_json,
        "root_family": [],
        "pattern_examples": [],
    }

    # Ensure transliteration is always present (compute on-the-fly if missing)
    if not result["transliteration"] and lemma.lemma_ar:
        result["transliteration"] = transliterate_arabic(lemma.lemma_ar)

    if root_obj:
        siblings = (
            db.query(Lemma)
            .filter(Lemma.root_id == root_obj.root_id, Lemma.lemma_id != lemma_id, Lemma.canonical_lemma_id.is_(None))
            .all()
        )
        sibling_ids = [sib.lemma_id for sib in siblings]
        sibling_ulk_map = {}
        if sibling_ids:
            sibling_ulks = (
                db.query(UserLemmaKnowledge)
                .filter(UserLemmaKnowledge.lemma_id.in_(sibling_ids))
                .all()
            )
            sibling_ulk_map = {u.lemma_id: u for u in sibling_ulks}

        for sib in siblings:
            sib_knowledge = sibling_ulk_map.get(sib.lemma_id)
            result["root_family"].append({
                "lemma_id": sib.lemma_id,
                "lemma_ar": sib.lemma_ar,
                "gloss_en": sib.gloss_en,
                "pos": sib.pos,
                "transliteration": sib.transliteration_ala_lc,
                "state": sib_knowledge.knowledge_state if sib_knowledge else "new",
            })

    # Pattern examples: same-wazn words from roots the user has touched
    if lemma.wazn:
        touched_root_ids = (
            db.query(Lemma.root_id)
            .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
            .filter(Lemma.root_id.isnot(None))
            .distinct()
            .subquery()
        )
        examples = (
            db.query(Lemma, UserLemmaKnowledge.knowledge_state)
            .outerjoin(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
            .filter(
                Lemma.wazn == lemma.wazn,
                Lemma.root_id.in_(touched_root_ids),
                Lemma.lemma_id != lemma.lemma_id,
                Lemma.canonical_lemma_id.is_(None),
            )
            .order_by(
                case(
                    (UserLemmaKnowledge.knowledge_state.in_(["known", "learning"]), 0),
                    else_=1,
                ),
                Lemma.frequency_rank.asc().nullslast(),
            )
            .limit(5)
            .all()
        )
        for ex, ks in examples:
            ex_root = db.query(Root).filter(Root.root_id == ex.root_id).first() if ex.root_id else None
            result["pattern_examples"].append({
                "lemma_id": ex.lemma_id,
                "lemma_ar": ex.lemma_ar,
                "gloss_en": ex.gloss_en,
                "transliteration": ex.transliteration_ala_lc,
                "root": ex_root.root if ex_root else None,
                "root_meaning": ex_root.core_meaning_en if ex_root else None,
                "knowledge_state": ks,
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


class ExperimentIntroAckIn(PydanticBaseModel):
    lemma_id: int
    session_id: str | None = None


@router.post("/experiment-intro-ack")
def acknowledge_experiment_intro(
    body: ExperimentIntroAckIn,
    db: Session = Depends(get_db),
):
    """Acknowledge that an experiment intro card was shown."""
    from datetime import datetime
    from app.models import UserLemmaKnowledge

    ulk = db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == body.lemma_id,
    ).first()
    if ulk:
        ulk.experiment_intro_shown_at = datetime.utcnow()
        try:
            db.commit()
        except Exception:
            db.rollback()

    log_interaction(
        event="experiment_intro_shown",
        lemma_id=body.lemma_id,
        session_id=body.session_id,
        experiment_group="intro_ab_card",
    )
    return {"status": "ok", "lemma_id": body.lemma_id}


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
        # Pattern examples for wrap-up (compact, limit 3)
        pe = []
        if lemma.wazn:
            touched_root_ids = (
                db.query(Lemma.root_id)
                .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
                .filter(Lemma.root_id.isnot(None))
                .distinct()
                .subquery()
            )
            examples = (
                db.query(Lemma, UserLemmaKnowledge.knowledge_state)
                .outerjoin(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
                .filter(
                    Lemma.wazn == lemma.wazn,
                    Lemma.root_id.in_(touched_root_ids),
                    Lemma.lemma_id != lemma.lemma_id,
                    Lemma.canonical_lemma_id.is_(None),
                )
                .order_by(
                    case(
                        (UserLemmaKnowledge.knowledge_state.in_(["known", "learning"]), 0),
                        else_=1,
                    ),
                    Lemma.frequency_rank.asc().nullslast(),
                )
                .limit(3)
                .all()
            )
            for ex, ks in examples:
                ex_root = db.query(Root).filter(Root.root_id == ex.root_id).first() if ex.root_id else None
                pe.append({
                    "lemma_id": ex.lemma_id,
                    "lemma_ar": ex.lemma_ar,
                    "gloss_en": ex.gloss_en,
                    "transliteration": ex.transliteration_ala_lc,
                    "root": ex_root.root if ex_root else None,
                    "root_meaning": ex_root.core_meaning_en if ex_root else None,
                    "knowledge_state": ks,
                })

        # Root family for wrap-up card
        rf = []
        if root_obj:
            siblings = (
                db.query(Lemma)
                .filter(Lemma.root_id == root_obj.root_id, Lemma.lemma_id != lemma.lemma_id, Lemma.canonical_lemma_id.is_(None))
                .all()
            )
            sibling_ulk = {
                ulk.lemma_id: ulk.knowledge_state
                for ulk in db.query(UserLemmaKnowledge)
                .filter(UserLemmaKnowledge.lemma_id.in_([s.lemma_id for s in siblings]))
                .all()
            } if siblings else {}
            for sib in siblings:
                rf.append({
                    "lemma_id": sib.lemma_id,
                    "lemma_ar": sib.lemma_ar,
                    "gloss_en": sib.gloss_en,
                    "transliteration": sib.transliteration_ala_lc,
                    "state": sibling_ulk.get(sib.lemma_id, "new"),
                })

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
            root_id=root_obj.root_id if root_obj else None,
            etymology_json=lemma.etymology_json,
            memory_hooks_json=lemma.memory_hooks_json,
            wazn=lemma.wazn,
            wazn_meaning=lemma.wazn_meaning,
            forms_translit=lemma.forms_translit_json or _compute_forms_translit(lemma.forms_json),
            pattern_examples=pe,
            is_acquiring=lemma.lemma_id in acquiring_ids,
            root_family=rf,
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

    # Build word metadata for each sentence
    all_sw = (
        db.query(SentenceWord)
        .filter(SentenceWord.sentence_id.in_([s.id for s in sentences]))
        .order_by(SentenceWord.position)
        .all()
    )
    sw_by_sent: dict[int, list] = {}
    lemma_ids_needed = set()
    for sw in all_sw:
        sw_by_sent.setdefault(sw.sentence_id, []).append(sw)
        if sw.lemma_id:
            lemma_ids_needed.add(sw.lemma_id)

    lemma_map = {}
    if lemma_ids_needed:
        lemmas = db.query(Lemma).options(joinedload(Lemma.root)).filter(Lemma.lemma_id.in_(lemma_ids_needed)).all()
        lemma_map = {l.lemma_id: l for l in lemmas}

    # Build session items (reuse build_session item format)
    items = []
    for sent in sentences:
        word_dicts = []
        for sw in sw_by_sent.get(sent.id, []):
            lemma = lemma_map.get(sw.lemma_id) if sw.lemma_id else None
            root_obj = lemma.root if lemma else None
            bare = strip_diacritics(sw.surface_form)
            is_func = _is_function_word(bare)
            gloss = lemma.gloss_en if lemma else FUNCTION_WORD_GLOSSES.get(bare)
            word_dicts.append({
                "lemma_id": sw.lemma_id,
                "surface_form": sw.surface_form,
                "gloss_en": gloss,
                "is_function_word": is_func,
                "root": root_obj.root if root_obj else None,
                "root_meaning": root_obj.core_meaning_en if root_obj else None,
                "root_id": root_obj.root_id if root_obj else None,
            })

        # Determine primary lemma from acquiring words in this sentence
        primary_lid = None
        for sw in sw_by_sent.get(sent.id, []):
            if sw.lemma_id in acquiring_ids:
                primary_lid = sw.lemma_id
                break
        if primary_lid is None and word_dicts:
            primary_lid = word_dicts[0].get("lemma_id")

        primary_lemma = lemma_map.get(primary_lid) if primary_lid else None

        items.append({
            "sentence_id": sent.id,
            "arabic_text": sent.arabic_diacritized or sent.arabic_text,
            "english_translation": sent.english_translation,
            "transliteration": sent.transliteration,
            "audio_url": sent.audio_url,
            "primary_lemma_id": primary_lid or 0,
            "primary_lemma_ar": primary_lemma.lemma_ar if primary_lemma else "",
            "primary_gloss_en": primary_lemma.gloss_en if primary_lemma else "",
            "words": word_dicts,
            "is_recap": True,
        })

    return {"items": items, "recap_word_count": len(acquiring_ids)}


@router.get("/confusion-help/{lemma_id}", response_model=ConfusionAnalysisOut)
def confusion_help(
    lemma_id: int,
    surface_form: str = Query(...),
    db: Session = Depends(get_db),
):
    """Analyze why a word was confusing — morphological complexity or visual similarity."""
    from app.services.confusion_service import analyze_confusion

    result = analyze_confusion(db, lemma_id, surface_form)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])

    log_interaction(
        event="confusion_help",
        lemma_id=lemma_id,
        surface_form=surface_form,
        confusion_type=result.get("confusion_type"),
        similar_count=len(result.get("similar_words", [])),
        has_decomposition=result.get("decomposition") is not None,
    )

    return result


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


@router.get("/session-summary/{session_id}", response_model=SessionSummaryOut)
def get_session_summary(session_id: str, db: Session = Depends(get_db)):
    """Return per-word journey data and sentence stats for a completed session."""
    review_logs = (
        db.query(ReviewLog, Lemma.lemma_ar, Lemma.gloss_en)
        .join(Lemma, Lemma.lemma_id == ReviewLog.lemma_id)
        .filter(ReviewLog.session_id == session_id)
        .filter(ReviewLog.credit_type == "primary")
        .order_by(ReviewLog.id)
        .all()
    )

    # Deduplicate by lemma_id: first review's old state, last review's new state
    lemma_first: dict[int, dict] = {}
    lemma_last: dict[int, dict] = {}
    for rl, lemma_ar, gloss_en in review_logs:
        lid = rl.lemma_id
        log_json = rl.fsrs_log_json or {}
        entry = {
            "lemma_id": lid,
            "lemma_ar": lemma_ar,
            "gloss_en": gloss_en,
            "is_acquisition": rl.is_acquisition,
            "log_json": log_json,
        }
        if lid not in lemma_first:
            lemma_first[lid] = entry
        lemma_last[lid] = entry

    word_journeys = []
    for lid, first in lemma_first.items():
        last = lemma_last[lid]
        first_json = first["log_json"]
        last_json = last["log_json"]

        old_state = first_json.get("pre_knowledge_state", "")
        if last["is_acquisition"]:
            new_state = last_json.get("state", "acquiring")
            graduated = last_json.get("graduated", False)
            old_box = first_json.get("acquisition_box_before")
            new_box = last_json.get("acquisition_box_after")
            if graduated:
                new_state = "learning"
                new_box = None
        else:
            new_state = last_json.get("state", "")
            graduated = False
            old_box = None
            new_box = None

        word_journeys.append(WordJourneyItem(
            lemma_id=lid,
            lemma_ar=first["lemma_ar"],
            gloss_en=first["gloss_en"],
            old_state=old_state,
            new_state=new_state,
            graduated=graduated,
            old_box=old_box,
            new_box=new_box,
        ))

    # Sentence-level stats
    sentence_logs = (
        db.query(SentenceReviewLog)
        .filter(SentenceReviewLog.session_id == session_id)
        .all()
    )
    sentence_count = len(sentence_logs)
    sentences_understood = sum(1 for s in sentence_logs if s.comprehension == "understood")
    sentences_partial = sum(1 for s in sentence_logs if s.comprehension == "partial")
    sentences_no_idea = sum(1 for s in sentence_logs if s.comprehension == "no_idea")
    response_times = [s.response_ms for s in sentence_logs if s.response_ms is not None]
    avg_response_ms = sum(response_times) / len(response_times) if response_times else None

    return SessionSummaryOut(
        word_journeys=word_journeys,
        sentence_count=sentence_count,
        sentences_understood=sentences_understood,
        sentences_partial=sentences_partial,
        sentences_no_idea=sentences_no_idea,
        avg_response_ms=avg_response_ms,
    )


@router.get("/session-end/{session_id}", response_model=SessionEndOut)
def get_session_end(session_id: str, db: Session = Depends(get_db)):
    """Lightweight endpoint returning everything the session-end card needs in one call."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # --- Word journeys (same logic as get_session_summary) ---
    review_logs = (
        db.query(ReviewLog, Lemma.lemma_ar, Lemma.gloss_en)
        .join(Lemma, Lemma.lemma_id == ReviewLog.lemma_id)
        .filter(ReviewLog.session_id == session_id, ReviewLog.credit_type == "primary")
        .order_by(ReviewLog.id)
        .all()
    )

    lemma_first: dict[int, dict] = {}
    lemma_last: dict[int, dict] = {}
    for rl, lemma_ar, gloss_en in review_logs:
        lid = rl.lemma_id
        entry = {
            "lemma_id": lid, "lemma_ar": lemma_ar, "gloss_en": gloss_en,
            "is_acquisition": rl.is_acquisition, "log_json": rl.fsrs_log_json or {},
        }
        if lid not in lemma_first:
            lemma_first[lid] = entry
        lemma_last[lid] = entry

    word_journeys = []
    for lid, first in lemma_first.items():
        last = lemma_last[lid]
        first_json, last_json = first["log_json"], last["log_json"]
        old_state = first_json.get("pre_knowledge_state", "")
        if last["is_acquisition"]:
            new_state = last_json.get("state", "acquiring")
            graduated = last_json.get("graduated", False)
            old_box = first_json.get("acquisition_box_before")
            new_box = last_json.get("acquisition_box_after")
            if graduated:
                new_state = "learning"
                new_box = None
        else:
            new_state = last_json.get("state", "")
            graduated = False
            old_box = new_box = None

        word_journeys.append(WordJourneyItem(
            lemma_id=lid, lemma_ar=first["lemma_ar"], gloss_en=first["gloss_en"],
            old_state=old_state, new_state=new_state, graduated=graduated,
            old_box=old_box, new_box=new_box,
        ))

    # --- Sentence stats for this session ---
    sent_row = (
        db.query(
            func.count(SentenceReviewLog.id).label("cnt"),
            func.sum(case((SentenceReviewLog.comprehension == "understood", 1), else_=0)).label("understood"),
            func.sum(case((SentenceReviewLog.comprehension == "partial", 1), else_=0)).label("partial"),
            func.sum(case((SentenceReviewLog.comprehension == "no_idea", 1), else_=0)).label("no_idea"),
            func.avg(SentenceReviewLog.response_ms).label("avg_ms"),
        )
        .filter(SentenceReviewLog.session_id == session_id)
        .first()
    )
    sentence_count = sent_row.cnt or 0
    sentences_understood = sent_row.understood or 0
    sentences_partial = sent_row.partial or 0
    sentences_no_idea = sent_row.no_idea or 0
    avg_response_ms = round(sent_row.avg_ms, 1) if sent_row.avg_ms else None

    # --- Known count + reviews today (simple counts) ---
    known_count = (
        db.query(func.count(UserLemmaKnowledge.id))
        .filter(UserLemmaKnowledge.knowledge_state.in_(["known", "learning"]))
        .scalar() or 0
    )
    reviews_today = (
        db.query(func.count(ReviewLog.id))
        .filter(ReviewLog.reviewed_at >= today_start)
        .scalar() or 0
    )
    graduated_today_count = (
        db.query(func.count(UserLemmaKnowledge.id))
        .filter(UserLemmaKnowledge.graduated_at >= today_start)
        .scalar() or 0
    )

    # --- Pipeline box counts (just counts, no word lists) ---
    box_counts = (
        db.query(
            UserLemmaKnowledge.acquisition_box,
            func.count(UserLemmaKnowledge.id),
        )
        .filter(UserLemmaKnowledge.knowledge_state == "acquiring")
        .group_by(UserLemmaKnowledge.acquisition_box)
        .all()
    )
    box_map = {box: cnt for box, cnt in box_counts}

    # --- Historical avg response time (from recent sessions) ---
    recent_avg = (
        db.query(func.avg(SentenceReviewLog.response_ms))
        .filter(
            SentenceReviewLog.session_id != session_id,
            SentenceReviewLog.response_ms.isnot(None),
            SentenceReviewLog.response_ms < 300_000,
            SentenceReviewLog.reviewed_at >= now - timedelta(days=30),
        )
        .scalar()
    )
    historical_avg_response_ms = round(float(recent_avg), 1) if recent_avg else None

    # --- Top partial roots (reuses root coverage logic, targeted) ---
    root_rows = (
        db.query(
            Root.root,
            Root.core_meaning_en,
            func.count(Lemma.lemma_id).label("total"),
            func.sum(
                case(
                    (UserLemmaKnowledge.knowledge_state.in_(["known", "learning"]), 1),
                    else_=0,
                )
            ).label("known"),
        )
        .join(Lemma, Lemma.root_id == Root.root_id)
        .outerjoin(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        .filter(Lemma.canonical_lemma_id.is_(None))
        .group_by(Root.root_id)
        .having(
            func.sum(case((UserLemmaKnowledge.knowledge_state.in_(["known", "learning"]), 1), else_=0)) > 0,
            func.sum(case((UserLemmaKnowledge.knowledge_state.in_(["known", "learning"]), 1), else_=0)) < func.count(Lemma.lemma_id),
        )
        .all()
    )
    partial_roots = sorted(root_rows, key=lambda r: (r.known or 0) / max(r.total, 1), reverse=True)[:3]
    top_partial_roots = [
        PartialRootOut(root=r.root, root_meaning=r.core_meaning_en, known=r.known or 0, total=r.total)
        for r in partial_roots
    ]

    from app.routers.stats import _count_due_cards, _count_fsrs_cleared_today
    _, fsrs_due, acquisition_due = _count_due_cards(db, now)
    fsrs_reviewed_today = _count_fsrs_cleared_today(db, today_start, now)

    return SessionEndOut(
        word_journeys=word_journeys,
        sentence_count=sentence_count,
        sentences_understood=sentences_understood,
        sentences_partial=sentences_partial,
        sentences_no_idea=sentences_no_idea,
        avg_response_ms=avg_response_ms,
        known_count=known_count,
        reviews_today=reviews_today,
        fsrs_reviewed_today=fsrs_reviewed_today,
        fsrs_due=fsrs_due,
        acquisition_due=acquisition_due,
        graduated_today_count=graduated_today_count,
        pipeline_box_1=box_map.get(1, 0),
        pipeline_box_2=box_map.get(2, 0),
        pipeline_box_3=box_map.get(3, 0),
        historical_avg_response_ms=historical_avg_response_ms,
        top_partial_roots=top_partial_roots,
    )
