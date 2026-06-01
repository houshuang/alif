"""Review endpoints — submit reviews, list due lemmas, pipeline stats.

Three endpoints:
    POST /api/reviews/submit  — single review, routes acquisition vs FSRS
    GET  /api/reviews/due     — lemma ids whose next review is due
    GET  /api/reviews/stats   — counts by state + Box distribution

This router exposes the SRS engine. The actual review UX (sentence cards,
session loop) is a separate layer — for now it can be exercised via the
HTTP boundary, which keeps the engine testable without UI dependencies.
"""
from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Lemma, Language, UserLemmaKnowledge
from app.services.acquisition_service import (
    get_acquisition_stats,
    start_acquisition,
    submit_acquisition_review,
)
from app.services.canonical_resolution import resolve_canonical_lemma_id
from app.services.fsrs_service import (
    parse_json_column,
    reactivate_if_suspended,
    submit_review as submit_fsrs_review,
)
from app.services.interaction_logger import log_interaction
from app.services.leech_service import check_single_word_leech
from app.services.lemma_quality import FUNCTION_WORD_SETS, is_noncontent_lemma
from app.services.sentence_review_service import (
    submit_sentence_review,
    undo_sentence_review,
)
from app.services.sentence_selector import (
    DEFAULT_SESSION_LIMIT,
    IntroCardPayload,
    build_session,
    pick_sentence_for_lemma,
)

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


class ReviewRequest(BaseModel):
    lemma_id: int
    rating: int = Field(..., ge=1, le=4, description="1=Again 2=Hard 3=Good 4=Easy")
    response_ms: Optional[int] = None
    session_id: Optional[str] = None
    review_mode: str = "reading"
    comprehension_signal: Optional[Literal["understood", "partial", "no_idea"]] = None
    client_review_id: Optional[str] = None
    sentence_id: Optional[int] = None


class ReviewResponse(BaseModel):
    lemma_id: int
    new_state: str
    acquisition_box: Optional[int] = None
    graduated: Optional[bool] = None
    next_due: str = ""
    duplicate: bool = False
    leech_suspended: bool = False


@router.post("/submit", response_model=ReviewResponse)
def submit(req: ReviewRequest, db: Session = Depends(get_db)) -> ReviewResponse:
    """Submit a single review.

    Routing:
        - Variant lemmas are redirected to canonical before any state read.
        - Suspended (leech) lemmas auto-reactivate to learning state with a
          fresh FSRS card before applying the review.
        - acquiring → submit_acquisition_review
        - everything else → submit_review (FSRS)
        - After applying, the lemma is re-checked for leech status.
    """
    canonical_id = resolve_canonical_lemma_id(db, req.lemma_id)

    reactivate_if_suspended(db, canonical_id, source="leech_reintro")

    ulk = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == canonical_id)
        .first()
    )

    if ulk and ulk.knowledge_state == "acquiring":
        result = submit_acquisition_review(
            db,
            lemma_id=canonical_id,
            rating_int=req.rating,
            response_ms=req.response_ms,
            session_id=req.session_id,
            review_mode=req.review_mode,
            comprehension_signal=req.comprehension_signal,
            client_review_id=req.client_review_id,
            sentence_id=req.sentence_id,
        )
    else:
        result = submit_fsrs_review(
            db,
            lemma_id=canonical_id,
            rating_int=req.rating,
            response_ms=req.response_ms,
            session_id=req.session_id,
            review_mode=req.review_mode,
            comprehension_signal=req.comprehension_signal,
            client_review_id=req.client_review_id,
            sentence_id=req.sentence_id,
        )

    leech_suspended = False
    if not result.get("duplicate"):
        leech_suspended = check_single_word_leech(db, canonical_id)

    return ReviewResponse(
        lemma_id=canonical_id,
        new_state=result.get("new_state", "unknown"),
        acquisition_box=result.get("acquisition_box"),
        graduated=result.get("graduated"),
        next_due=result.get("next_due", "") or "",
        duplicate=result.get("duplicate", False),
        leech_suspended=leech_suspended,
    )


class IntroduceRequest(BaseModel):
    lemma_id: int
    source: str = "study"
    due_immediately: bool = True


class IntroduceResponse(BaseModel):
    lemma_id: int
    state: str
    acquisition_box: Optional[int] = None
    next_due: str = ""


@router.post("/introduce", response_model=IntroduceResponse)
def introduce(req: IntroduceRequest, db: Session = Depends(get_db)) -> IntroduceResponse:
    """Bring a lemma into acquisition.

    Use when the learner has explicitly indicated they want to start
    learning a specific word (e.g., from a manual lookup). The reading-
    intake "mark unknown" flow uses this internally.
    """
    canonical_id = resolve_canonical_lemma_id(db, req.lemma_id)
    ulk = start_acquisition(
        db,
        lemma_id=canonical_id,
        source=req.source,
        due_immediately=req.due_immediately,
    )
    db.commit()

    next_due = ""
    if ulk.acquisition_next_due:
        next_due = ulk.acquisition_next_due.isoformat()
    elif ulk.fsrs_card_json:
        card = parse_json_column(ulk.fsrs_card_json)
        next_due = card.get("due", "")

    return IntroduceResponse(
        lemma_id=canonical_id,
        state=ulk.knowledge_state,
        acquisition_box=ulk.acquisition_box,
        next_due=next_due,
    )


class DueLemmaSummary(BaseModel):
    lemma_id: int
    lemma_form: str
    lemma_bare: str
    gloss_en: Optional[str]
    state: str
    acquisition_box: Optional[int]
    next_due: str


@router.get("/due")
def due(
    language_code: str,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[DueLemmaSummary]:
    """Lemmas whose next review is due, scoped to one language.

    Returns acquisition-due first (in box order), then FSRS-due. Capped at
    ``limit`` rows; clients should paginate when polyglot grows beyond
    that scale.
    """
    if not db.query(Language).filter(Language.code == language_code).first():
        raise HTTPException(status_code=400, detail=f"Unknown language: {language_code}")

    now = datetime.now(timezone.utc)

    function_words = FUNCTION_WORD_SETS.get(language_code, set())

    acquiring_due = (
        db.query(UserLemmaKnowledge, Lemma)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            Lemma.language_code == language_code,
            UserLemmaKnowledge.knowledge_state == "acquiring",
            UserLemmaKnowledge.acquisition_next_due.isnot(None),
            UserLemmaKnowledge.acquisition_next_due <= now,
        )
        .order_by(
            UserLemmaKnowledge.acquisition_box.asc(),
            UserLemmaKnowledge.acquisition_next_due.asc(),
        )
        .all()
    )

    out: list[DueLemmaSummary] = []
    for ulk, lemma in acquiring_due:
        if is_noncontent_lemma(
            lemma,
            language_code=language_code,
            function_words=function_words,
        ):
            continue
        out.append(DueLemmaSummary(
            lemma_id=lemma.lemma_id,
            lemma_form=lemma.lemma_form,
            lemma_bare=lemma.lemma_bare,
            gloss_en=lemma.gloss_en,
            state=ulk.knowledge_state,
            acquisition_box=ulk.acquisition_box,
            next_due=ulk.acquisition_next_due.isoformat() if ulk.acquisition_next_due else "",
        ))
        if len(out) >= limit:
            return out

    if len(out) >= limit:
        return out

    remaining = limit - len(out)
    fsrs_candidates = (
        db.query(UserLemmaKnowledge, Lemma)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            Lemma.language_code == language_code,
            UserLemmaKnowledge.knowledge_state.in_(["learning", "known", "lapsed"]),
            UserLemmaKnowledge.fsrs_card_json.isnot(None),
        )
        .all()
    )
    fsrs_due_pairs: list[tuple[datetime, UserLemmaKnowledge, Lemma]] = []
    for ulk, lemma in fsrs_candidates:
        if is_noncontent_lemma(
            lemma,
            language_code=language_code,
            function_words=function_words,
        ):
            continue
        card = parse_json_column(ulk.fsrs_card_json)
        due_str = card.get("due")
        if not due_str:
            continue
        try:
            due_dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if due_dt.tzinfo is None:
            due_dt = due_dt.replace(tzinfo=timezone.utc)
        if due_dt <= now:
            fsrs_due_pairs.append((due_dt, ulk, lemma))

    fsrs_due_pairs.sort(key=lambda t: t[0])
    for due_dt, ulk, lemma in fsrs_due_pairs[:remaining]:
        out.append(DueLemmaSummary(
            lemma_id=lemma.lemma_id,
            lemma_form=lemma.lemma_form,
            lemma_bare=lemma.lemma_bare,
            gloss_en=lemma.gloss_en,
            state=ulk.knowledge_state,
            acquisition_box=None,
            next_due=due_dt.isoformat(),
        ))

    return out


@router.get("/stats")
def stats(language_code: str | None = None, db: Session = Depends(get_db)) -> dict:
    """Pipeline stats: acquisition boxes + due counts, optionally per-language."""
    return get_acquisition_stats(db, language_code=language_code)


class SentenceReviewRequest(BaseModel):
    sentence_id: int
    comprehension_signal: Literal["understood", "partial", "no_idea"]
    primary_lemma_id: Optional[int] = None
    missed_lemma_ids: list[int] = Field(default_factory=list)
    confused_lemma_ids: list[int] = Field(default_factory=list)
    response_ms: Optional[int] = None
    session_id: Optional[str] = None
    review_mode: str = "reading"
    client_review_id: Optional[str] = None


class SentenceReviewWordResult(BaseModel):
    lemma_id: int
    rating: int
    credit_type: str
    new_state: str
    next_due: str


class SentenceReviewResponse(BaseModel):
    word_results: list[SentenceReviewWordResult]
    duplicate: bool
    leech_suspended_lemma_ids: list[int]


@router.post("/submit-sentence", response_model=SentenceReviewResponse)
def submit_sentence(
    req: SentenceReviewRequest,
    db: Session = Depends(get_db),
) -> SentenceReviewResponse:
    """Submit a sentence-level review.

    Distributes the comprehension signal across every content lemma in the
    sentence (function words and proper names are skipped). Variant lemmas
    are credited to their canonical at function entry.

    Rejects sentences that haven't passed the quality gate
    (``mappings_verified_at IS NULL``) with HTTP 400 — Hard Invariant #2.
    """
    from app.models import Sentence as _Sentence
    sentence = db.query(_Sentence).filter(_Sentence.id == req.sentence_id).first()
    if not sentence:
        raise HTTPException(status_code=404, detail=f"Sentence {req.sentence_id} not found")
    if sentence.mappings_verified_at is None:
        raise HTTPException(
            status_code=400,
            detail=f"Sentence {req.sentence_id} has no mappings_verified_at — "
                   f"not yet eligible for review (reviewability gate)",
        )

    result = submit_sentence_review(
        db,
        sentence_id=req.sentence_id,
        comprehension_signal=req.comprehension_signal,
        primary_lemma_id=req.primary_lemma_id,
        missed_lemma_ids=req.missed_lemma_ids,
        confused_lemma_ids=req.confused_lemma_ids,
        response_ms=req.response_ms,
        session_id=req.session_id,
        review_mode=req.review_mode,
        client_review_id=req.client_review_id,
    )

    return SentenceReviewResponse(
        word_results=[SentenceReviewWordResult(**wr) for wr in result["word_results"]],
        duplicate=result["duplicate"],
        leech_suspended_lemma_ids=result["leech_suspended_lemma_ids"],
    )


class SentenceUndoRequest(BaseModel):
    client_review_id: str


class SentenceUndoResponse(BaseModel):
    undone: bool
    reviews_removed: int


@router.post("/undo-sentence", response_model=SentenceUndoResponse)
def undo_sentence(
    req: SentenceUndoRequest,
    db: Session = Depends(get_db),
) -> SentenceUndoResponse:
    """Reverse a previously submitted sentence review by client_review_id.

    Restores pre-review FSRS card state from each ReviewLog's fsrs_log_json
    snapshot, then deletes the rows. Idempotent — second call returns
    ``undone=False``.
    """
    result = undo_sentence_review(db, client_review_id=req.client_review_id)
    return SentenceUndoResponse(**result)


class WordRenderOut(BaseModel):
    position: int
    surface_form: str
    lemma_id: Optional[int]
    lemma_form: Optional[str]
    gloss_en: Optional[str]
    is_target: bool
    is_function_word: bool
    is_proper_name: bool
    is_punctuation: bool
    knowledge_state: str


class SentencePayloadOut(BaseModel):
    sentence_id: int
    text: str
    translation_en: Optional[str]
    target_lemma_id: int
    source: Optional[str]
    page_id: Optional[int]
    words: list[WordRenderOut]
    selection_reason: str
    score: float
    candidate_count: int = 0
    llm_candidate_count: int = 0
    selected_times_shown: int = 0
    selected_recently_shown: bool = False


def _payload_to_pydantic(payload) -> SentencePayloadOut:
    return SentencePayloadOut(
        sentence_id=payload.sentence_id,
        text=payload.text,
        translation_en=payload.translation_en,
        target_lemma_id=payload.target_lemma_id,
        source=payload.source,
        page_id=payload.page_id,
        words=[
            WordRenderOut(
                position=w.position,
                surface_form=w.surface_form,
                lemma_id=w.lemma_id,
                lemma_form=w.lemma_form,
                gloss_en=w.gloss_en,
                is_target=w.is_target,
                is_function_word=w.is_function_word,
                is_proper_name=w.is_proper_name,
                is_punctuation=w.is_punctuation,
                knowledge_state=w.knowledge_state,
            )
            for w in payload.words
        ],
        selection_reason=payload.selection_reason,
        score=payload.score,
        candidate_count=payload.candidate_count,
        llm_candidate_count=payload.llm_candidate_count,
        selected_times_shown=payload.selected_times_shown,
        selected_recently_shown=payload.selected_recently_shown,
    )


@router.get("/next-sentence", response_model=Optional[SentencePayloadOut])
def next_sentence(
    lemma_id: int,
    language_code: str,
    db: Session = Depends(get_db),
) -> Optional[SentencePayloadOut]:
    """Pick the best sentence covering ``lemma_id`` for review.

    Returns ``null`` (HTTP 200 with empty body) when no eligible sentence
    exists yet — caller should defer to generation (PR #4) or surface a
    "no material" UX state.
    """
    if not db.query(Language).filter(Language.code == language_code).first():
        raise HTTPException(status_code=400, detail=f"Unknown language: {language_code}")
    payload = pick_sentence_for_lemma(db, lemma_id=lemma_id, language_code=language_code)
    if payload is None:
        log_interaction(
            event="next_sentence_selected",
            app="polyglot",
            context="reviews/next-sentence",
            lemma_id=lemma_id,
            language_code=language_code,
            selected=False,
        )
        return None
    log_interaction(
        event="next_sentence_selected",
        app="polyglot",
        context="reviews/next-sentence",
        lemma_id=payload.target_lemma_id,
        language_code=language_code,
        selected=True,
        sentence_id=payload.sentence_id,
        source=payload.source,
        selection_reason=payload.selection_reason,
        score=payload.score,
        candidate_count=payload.candidate_count,
        llm_candidate_count=payload.llm_candidate_count,
        selected_times_shown=payload.selected_times_shown,
        selected_recently_shown=payload.selected_recently_shown,
    )
    return _payload_to_pydantic(payload)


class IntroCardOut(BaseModel):
    lemma_id: int
    lemma_form: str
    lemma_bare: str
    gloss_en: Optional[str]
    pos: Optional[str]
    intro_kind: str
    times_seen: int
    cognate_lemma_id: Optional[int] = None
    cognate_lemma_form: Optional[str] = None


class SessionBundleOut(BaseModel):
    sentences: list[SentencePayloadOut]
    intro_cards: list[IntroCardOut] = []


def _intro_card_to_pydantic(card: IntroCardPayload) -> IntroCardOut:
    return IntroCardOut(
        lemma_id=card.lemma_id,
        lemma_form=card.lemma_form,
        lemma_bare=card.lemma_bare,
        gloss_en=card.gloss_en,
        pos=card.pos,
        intro_kind=card.intro_kind,
        times_seen=card.times_seen,
        cognate_lemma_id=card.cognate_lemma_id,
        cognate_lemma_form=card.cognate_lemma_form,
    )


# The client (`polyglot-api.ts`) aborts the live session fetch at
# SESSION_TIMEOUT_MS=12s. `build_session` is meant to be DB-only and <1s, so a
# build slower than this is the line between "we were slow" and "the network
# died" when triaging a client "Network request failed".
SESSION_BUILD_SLOW_MS = 4000


@router.get("/session", response_model=SessionBundleOut)
def session(
    language_code: str,
    limit: int = DEFAULT_SESSION_LIMIT,
    prefetch: bool = False,
    db: Session = Depends(get_db),
) -> SessionBundleOut:
    """Build a sentence review session for the language.

    Walks acquisition-due (Box 1/2/3) then FSRS-due, picks one sentence per
    lemma, dedupes within the session. Returns sentences + intro cards for
    never-shown acquiring lemmas appearing in those sentences (the frontend
    interleaves intro cards before their target sentence and posts
    ``/api/reviews/experiment-intro-ack`` on display). Lemmas with no
    eligible sentence are silently skipped — a shorter-than-``limit``
    response is the right signal for the frontend to surface "generate more
    material" UX.

    ``prefetch=True`` is sent by the client's background next-session warm-up
    (so the session→session transition is a cache hit, not a live fetch on a
    flaky connection). It suppresses the ``session_built`` interaction log so a
    prefetch — which may never be shown — doesn't get counted as a session the
    learner actually did. Mirrors Alif's ``prefetch=true`` flag. Selection is
    otherwise identical and side-effect-free (DB-only, no state mutation).
    """
    if not db.query(Language).filter(Language.code == language_code).first():
        raise HTTPException(status_code=400, detail=f"Unknown language: {language_code}")
    _build_start = perf_counter()
    bundle = build_session(db, language_code=language_code, limit=limit)
    build_ms = int((perf_counter() - _build_start) * 1000)
    if prefetch:
        return SessionBundleOut(
            sentences=[_payload_to_pydantic(p) for p in bundle.sentences],
            intro_cards=[_intro_card_to_pydantic(c) for c in bundle.intro_cards],
        )
    if build_ms >= SESSION_BUILD_SLOW_MS:
        log_interaction(
            event="session_build_slow",
            app="polyglot",
            context="reviews/session",
            language_code=language_code,
            build_ms=build_ms,
            requested_limit=limit,
            selected_count=len(bundle.sentences),
        )
    log_interaction(
        event="session_built",
        app="polyglot",
        context="reviews/session",
        language_code=language_code,
        build_ms=build_ms,
        requested_limit=limit,
        selected_count=len(bundle.sentences),
        intro_count=len(bundle.intro_cards),
        selected=[
            {
                "sentence_id": p.sentence_id,
                "target_lemma_id": p.target_lemma_id,
                "source": p.source,
                "selection_reason": p.selection_reason,
                "score": p.score,
                "candidate_count": p.candidate_count,
                "llm_candidate_count": p.llm_candidate_count,
                "selected_times_shown": p.selected_times_shown,
                "selected_recently_shown": p.selected_recently_shown,
            }
            for p in bundle.sentences
        ],
        intro_lemma_ids=[c.lemma_id for c in bundle.intro_cards],
        skipped_due=[
            {
                "lemma_id": skipped.lemma_id,
                "queue": skipped.queue,
                "reason": skipped.reason,
            }
            for skipped in bundle.skipped_due_lemmas
        ],
    )
    return SessionBundleOut(
        sentences=[_payload_to_pydantic(p) for p in bundle.sentences],
        intro_cards=[_intro_card_to_pydantic(c) for c in bundle.intro_cards],
    )


class SessionFetchEventRequest(BaseModel):
    outcome: Literal["failed", "recovered"]
    language_code: str
    # "timeout" (12s AbortController fired), "transport" (bare RN "Network
    # request failed" — request never completed), or "http_<status>".
    error_kind: Optional[str] = None
    error_message: Optional[str] = None
    force_fresh: bool = False
    had_prefetch: bool = False
    prefetch_age_ms: Optional[int] = None
    used_fallback: bool = False
    session_id: Optional[str] = None


@router.post("/session-fetch-event")
def session_fetch_event(req: SessionFetchEventRequest) -> dict:
    """Record a client-observed session-fetch failure/recovery.

    The live session fetch is the one place a "Network request failed" wall is
    actually observed, and it's client-side — invisible to every server log
    (a request the client aborts at 12s may still be running here with nothing
    recording it; a prefetch failure is suppressed entirely). This beacon, sent
    best-effort and fire-and-forget by ``getReviewSessionResilient``, captures
    the classification needed to tell a timeout from a transport drop from a
    stale-prefetch miss. No DB dependency so it can't itself fail under lock
    contention.
    """
    log_interaction(
        event=f"session_fetch_{req.outcome}",
        app="polyglot",
        context="reviews/session",
        language_code=req.language_code,
        error_kind=req.error_kind,
        error_message=(req.error_message or "")[:300] or None,
        force_fresh=req.force_fresh,
        had_prefetch=req.had_prefetch,
        prefetch_age_ms=req.prefetch_age_ms,
        used_fallback=req.used_fallback,
        session_id=req.session_id,
    )
    return {"logged": True}


class ExperimentIntroAckRequest(BaseModel):
    lemma_id: int
    session_id: Optional[str] = None


class ExperimentIntroAckResponse(BaseModel):
    lemma_id: int
    stamped: bool


@router.post("/experiment-intro-ack", response_model=ExperimentIntroAckResponse)
def experiment_intro_ack(
    req: ExperimentIntroAckRequest,
    db: Session = Depends(get_db),
) -> ExperimentIntroAckResponse:
    """Acknowledge that an intro card was shown for ``lemma_id``.

    Stamps ``UserLemmaKnowledge.experiment_intro_shown_at`` so that:

    1. ``_intro_shown_recently`` blocks Tier 0/1/2 fast-graduation paths and
       Box 1→2 advancement for ~10 minutes — three correct answers within
       seconds of seeing the card is working memory, not learning.
    2. ``_build_intro_cards`` won't re-emit the same card in the next
       session (rescue cards observe a 7-day cooldown via the same field).

    Variant lemmas are redirected to canonical at entry. Missing ULKs are
    silently no-op'd — an intro card without a ULK is a frontend bug, not a
    state we want to fabricate.
    """
    canonical_id = resolve_canonical_lemma_id(db, req.lemma_id)
    ulk = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == canonical_id)
        .first()
    )
    if ulk is None:
        return ExperimentIntroAckResponse(lemma_id=canonical_id, stamped=False)
    ulk.experiment_intro_shown_at = datetime.now(timezone.utc)
    db.commit()
    return ExperimentIntroAckResponse(lemma_id=canonical_id, stamped=True)
