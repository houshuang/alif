"""Sentence-level review submission.

Translates one sentence-shaped comprehension signal (understood / partial /
no_idea) into per-word ratings, applies them via the FSRS or acquisition
pipeline, and writes a sentence-level audit row.

Ported from Alif's `app/services/sentence_review_service.py`. Differences
called out at the relevant sites:

- No `_record_sentence_grammar` — polyglot has no GrammarFeature tables yet.
- No `_match_surface_form` / variant_stats_json tracking — Alif uses the
  Arabic `forms_json` paradigm to match a surface to a paradigm key; the
  polyglot ULK has no `variant_stats_json` column. The canonical ULK still
  gets `total_encounters` bumped, just without per-surface breakdown.
- No multi-sentence passages — polyglot reviews one sentence at a time. Add
  `sentence_ids` plumbing if/when passage review lands.
- No listening-mode fields — Greek has no TTS yet. Only `last_reading_*` is
  touched.

Honours Hard Invariant FOUNDATIONAL ("every word in every sentence earns
review credit") and #9 (canonical is the unit of scheduling — defensive
resolution even though sentence_harvest already canonicalised at storage).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import (
    Lemma,
    ReviewLog,
    Sentence,
    SentenceReviewLog,
    SentenceWord,
    UserLemmaKnowledge,
)
from app.services.acquisition_service import (
    start_acquisition,
    submit_acquisition_review,
)
from app.services.canonical_resolution import resolve_canonical_lemma_id
from app.services.fsrs_service import (
    parse_json_column,
    record_scaffold_confirmation,
    submit_review,
)
from app.services.interaction_logger import log_interaction
from app.services.leech_service import check_single_word_leech
from app.services.lemma_quality import FUNCTION_WORD_SETS, is_noncontent_lemma

logger = logging.getLogger(__name__)


def submit_sentence_review(
    db: Session,
    sentence_id: int,
    comprehension_signal: str,
    primary_lemma_id: Optional[int] = None,
    missed_lemma_ids: Optional[list[int]] = None,
    confused_lemma_ids: Optional[list[int]] = None,
    response_ms: Optional[int] = None,
    session_id: Optional[str] = None,
    review_mode: str = "reading",
    client_review_id: Optional[str] = None,
) -> dict:
    """Apply a sentence-shaped review to every content lemma in the sentence.

    Rating distribution:
        understood            → all content lemmas rate 3 (Good)
        partial + missed_ids  → those rate 1 (Again)
        partial + confused_ids→ those rate 2 (Hard) + was_confused=True
        partial + other       → rate 3
        no_idea               → all rate 1

    Content-lemma filter: function words (word_category='function_word' OR in
    FUNCTION_WORD_SETS for the sentence's language), proper names
    (word_category='proper_name'), and junk/OCR fragments
    (word_category='not_word') are skipped — they carry a lemma_id only so the
    sentence passes the reviewability gate.

    `primary_lemma_id` may be omitted for textbook-harvested sentences (no
    target word, all credit is collateral). When supplied, that lemma's
    ReviewLog gets `credit_type='primary'` and the others `'collateral'`.

    Returns `{"word_results": [...], "duplicate": bool,
    "leech_suspended_lemma_ids": [...]}`.
    """
    if client_review_id:
        sentence_log_id = client_review_id
        word_log_prefix = f"{client_review_id}:"
        existing_sentence_log = (
            db.query(SentenceReviewLog)
            .filter(SentenceReviewLog.client_review_id == sentence_log_id)
            .first()
        )
        if existing_sentence_log:
            log_interaction(
                event="sentence_review",
                app="polyglot",
                context="sentence_review_service",
                session_id=session_id,
                sentence_id=sentence_id,
                duplicate=True,
                duplicate_source="sentence_log",
                client_review_id=client_review_id,
            )
            return {"word_results": [], "duplicate": True, "leech_suspended_lemma_ids": []}
        existing_word_log = (
            db.query(ReviewLog)
            .filter(ReviewLog.client_review_id.like(f"{word_log_prefix}%"))
            .first()
        )
        if existing_word_log:
            log_interaction(
                event="sentence_review",
                app="polyglot",
                context="sentence_review_service",
                session_id=session_id,
                sentence_id=sentence_id,
                duplicate=True,
                duplicate_source="word_log",
                client_review_id=client_review_id,
            )
            return {"word_results": [], "duplicate": True, "leech_suspended_lemma_ids": []}

    sentence = (
        db.query(Sentence)
        .filter(Sentence.id == sentence_id)
        .first()
    )
    if not sentence:
        raise ValueError(f"Sentence {sentence_id} not found")
    if sentence.mappings_verified_at is None:
        raise ValueError(
            f"Sentence {sentence_id} has no mappings_verified_at — "
            f"reviewability gate (Hard Invariant #2) blocks submission"
        )

    now = datetime.now(timezone.utc)
    missed_set = set(missed_lemma_ids or [])
    confused_set = set(confused_lemma_ids or [])

    sentence_words = (
        db.query(SentenceWord)
        .filter(SentenceWord.sentence_id == sentence_id)
        .order_by(SentenceWord.position)
        .all()
    )
    lemma_ids_in_sentence: set[int] = {sw.lemma_id for sw in sentence_words if sw.lemma_id}
    if not lemma_ids_in_sentence:
        # Nothing to credit (pure-punctuation sentence shouldn't reach here, but
        # be defensive). Still log the sentence-level row so the user's action
        # is reflected in stats.
        _log_sentence_review(
            db, sentence, now, comprehension_signal,
            response_ms, session_id, review_mode, client_review_id,
        )
        db.commit()
        _log_sentence_review_interaction(
            sentence=sentence,
            comprehension_signal=comprehension_signal,
            primary_lemma_id=primary_lemma_id,
            missed_lemma_ids=missed_lemma_ids or [],
            confused_lemma_ids=confused_lemma_ids or [],
            response_ms=response_ms,
            session_id=session_id,
            review_mode=review_mode,
            client_review_id=client_review_id,
            word_results=[],
            leech_suspended=[],
        )
        return {"word_results": [], "duplicate": False, "leech_suspended_lemma_ids": []}

    language_code = sentence.language_code
    function_word_bares = FUNCTION_WORD_SETS.get(language_code, set())

    lemma_objs = (
        db.query(Lemma)
        .filter(Lemma.lemma_id.in_(lemma_ids_in_sentence))
        .all()
    )
    lemma_map: dict[int, Lemma] = {lo.lemma_id: lo for lo in lemma_objs}

    noncontent_lemma_ids: set[int] = set()
    for lo in lemma_objs:
        if is_noncontent_lemma(lo, function_words=function_word_bares):
            noncontent_lemma_ids.add(lo.lemma_id)

    # Defensive canonical resolution. Sentence_harvest already writes
    # canonicals to SentenceWord.lemma_id at storage time, but if an
    # external import path ever wrote a variant we re-resolve here so
    # Hard Invariant #9 holds without trusting the caller. Use the
    # DB-backed resolver so multi-hop chains (A→B→C) follow correctly
    # even when the intermediate canonical isn't in this sentence —
    # `resolve_canonical_via_map` with a sentence-local map would stop at
    # the first hop. Sentences are small (~10 lemmas) so the N queries
    # are cheap.
    variant_to_canonical: dict[int, int] = {}
    for lid in lemma_ids_in_sentence:
        canonical = resolve_canonical_lemma_id(db, lid)
        if canonical != lid:
            variant_to_canonical[lid] = canonical

    # Multi-hop chains may reference canonicals not yet in lemma_map. Load them
    # so the function-word / proper-name filters above also apply to canonicals.
    canonical_ids_needed = set(variant_to_canonical.values()) - set(lemma_map.keys())
    if canonical_ids_needed:
        for lo in db.query(Lemma).filter(Lemma.lemma_id.in_(canonical_ids_needed)).all():
            lemma_map[lo.lemma_id] = lo
            if is_noncontent_lemma(lo, function_words=function_word_bares):
                noncontent_lemma_ids.add(lo.lemma_id)

    all_ulk_ids = lemma_ids_in_sentence | set(variant_to_canonical.values())
    ulk_objs = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id.in_(all_ulk_ids))
        .all()
    )
    knowledge_map: dict[int, UserLemmaKnowledge] = {ulk.lemma_id: ulk for ulk in ulk_objs}
    inactive_lemma_ids = {
        lid for lid, ulk in knowledge_map.items()
        if ulk.knowledge_state in {"suspended", "ignore"}
    }

    word_results: list[dict] = []
    processed_effective_ids: set[int] = set()

    for lemma_id in lemma_ids_in_sentence:
        effective_lemma_id = variant_to_canonical.get(lemma_id, lemma_id)

        # Check both variant and canonical for filter membership — handles the
        # rare case of a variant pointing to a non-content root.
        if lemma_id in noncontent_lemma_ids or effective_lemma_id in noncontent_lemma_ids:
            continue
        if lemma_id in inactive_lemma_ids or effective_lemma_id in inactive_lemma_ids:
            continue

        # Rating + is_confused decided BEFORE acquisition promotion so the
        # same logic applies whether the lemma was already in ULK or fresh.
        is_confused = False
        if comprehension_signal == "understood":
            rating = 3
        elif comprehension_signal == "partial":
            if lemma_id in missed_set or effective_lemma_id in missed_set:
                rating = 1
            elif lemma_id in confused_set or effective_lemma_id in confused_set:
                rating = 2
                is_confused = True
            else:
                rating = 3
        else:  # no_idea
            rating = 1

        # Auto-introduce encountered/unknown content lemmas: every word in
        # every sentence earns review credit. The daily intro cap inside
        # start_acquisition may keep the word in 'encountered' state, in
        # which case we bump total_encounters but skip the review.
        cap_deferred = False
        existing_ulk = knowledge_map.get(effective_lemma_id)
        needs_promotion = (
            existing_ulk is None
            or existing_ulk.knowledge_state == "encountered"
        )
        if needs_promotion:
            promoted_ulk = start_acquisition(
                db,
                lemma_id=effective_lemma_id,
                source="collateral",
                due_immediately=False,
            )
            knowledge_map[effective_lemma_id] = promoted_ulk
            if promoted_ulk.knowledge_state != "acquiring":
                cap_deferred = True

        # Dedup after canonical resolution: two variant rows in one sentence
        # collapse to a single credit.
        if effective_lemma_id in processed_effective_ids:
            continue
        processed_effective_ids.add(effective_lemma_id)

        if cap_deferred:
            knowledge = knowledge_map.get(effective_lemma_id)
            if knowledge:
                knowledge.total_encounters = (knowledge.total_encounters or 0) + 1
            continue

        credit_type = (
            "primary"
            if primary_lemma_id is not None and (
                lemma_id == primary_lemma_id or effective_lemma_id == primary_lemma_id
            )
            else "collateral"
        )

        knowledge = knowledge_map.get(effective_lemma_id)
        if (
            knowledge
            and knowledge.knowledge_state == "known"
            and knowledge.fsrs_card_json is None
        ):
            # Assumed-known scaffold (bulk-marked / cognate-known, no FSRS card).
            # Every lemma in a shown sentence is evaluated equally (Hard
            # Invariant FOUNDATIONAL): a red miss is an explicit lapse → the word
            # restarts acquisition and we fall through to record the rating-1
            # review; a green/confused exposure is VERIFICATION evidence and is
            # recorded durably (ReviewLog + confirmed_at) WITHOUT creating an
            # FSRS card — confirmed scaffold stays out of the review rotation
            # until a future red lapses it. See Hard Invariant 6.
            if rating == 1:
                knowledge = start_acquisition(
                    db,
                    lemma_id=effective_lemma_id,
                    source="review_lapse",
                    due_immediately=True,
                    restart_known=True,
                )
                knowledge_map[effective_lemma_id] = knowledge
            elif rating >= 3:
                # Clean green exposure → verification evidence.
                word_client_id = (
                    f"{client_review_id}:{effective_lemma_id}"
                    if client_review_id
                    else None
                )
                conf = record_scaffold_confirmation(
                    db,
                    lemma_id=effective_lemma_id,
                    rating_int=rating,
                    response_ms=response_ms if credit_type == "primary" else None,
                    session_id=session_id,
                    review_mode=review_mode,
                    comprehension_signal=comprehension_signal,
                    client_review_id=word_client_id,
                    sentence_id=sentence_id,
                    credit_type=credit_type,
                )
                if not conf.get("duplicate"):
                    knowledge.total_encounters = (knowledge.total_encounters or 0) + 1
                    word_results.append({
                        "lemma_id": effective_lemma_id,
                        "rating": rating,
                        "credit_type": credit_type,
                        "new_state": "known",
                        "next_due": "",
                        "confirmation": True,
                    })
                continue
            else:
                # rating == 2 (confused): ambiguous — not a clean confirmation,
                # not a miss. Count the encounter but don't confirm or lapse.
                knowledge.total_encounters = (knowledge.total_encounters or 0) + 1
                continue

        word_client_id = (
            f"{client_review_id}:{effective_lemma_id}"
            if client_review_id
            else None
        )

        if knowledge and knowledge.knowledge_state == "acquiring":
            result = submit_acquisition_review(
                db,
                lemma_id=effective_lemma_id,
                rating_int=rating,
                response_ms=response_ms if credit_type == "primary" else None,
                session_id=session_id,
                review_mode=review_mode,
                comprehension_signal=comprehension_signal,
                client_review_id=word_client_id,
                sentence_id=sentence_id,
                commit=False,
            )
        else:
            result = submit_review(
                db,
                lemma_id=effective_lemma_id,
                rating_int=rating,
                response_ms=response_ms if credit_type == "primary" else None,
                session_id=session_id,
                review_mode=review_mode,
                comprehension_signal=comprehension_signal,
                client_review_id=word_client_id,
                sentence_id=sentence_id,
                commit=False,
            )
        is_duplicate = bool(result.get("duplicate"))

        # Tag the just-written ReviewLog with credit_type + was_confused.
        # sentence_id is already set by the submit_* call.
        if not is_duplicate:
            latest_log = (
                db.query(ReviewLog)
                .filter(ReviewLog.lemma_id == effective_lemma_id)
                .order_by(ReviewLog.id.desc())
                .first()
            )
            if latest_log:
                latest_log.credit_type = credit_type
                latest_log.was_confused = is_confused

            knowledge = knowledge_map.get(effective_lemma_id)
            if not knowledge:
                knowledge = (
                    db.query(UserLemmaKnowledge)
                    .filter(UserLemmaKnowledge.lemma_id == effective_lemma_id)
                    .first()
                )
                if knowledge:
                    knowledge_map[effective_lemma_id] = knowledge
            if knowledge:
                knowledge.total_encounters = (knowledge.total_encounters or 0) + 1

            word_results.append({
                "lemma_id": effective_lemma_id,
                "rating": rating,
                "credit_type": credit_type,
                "new_state": result["new_state"],
                "next_due": result.get("next_due", ""),
            })

    # Per-word leech check. Runs on all ratings — a correct review can evict
    # an older correct one from the sliding window and tip the lemma into
    # leech territory.
    leech_suspended: list[int] = []
    for wr in word_results:
        if check_single_word_leech(db, wr["lemma_id"]):
            leech_suspended.append(wr["lemma_id"])

    _log_sentence_review(
        db, sentence, now, comprehension_signal,
        response_ms, session_id, review_mode, client_review_id,
        missed_lemma_ids=missed_lemma_ids, confused_lemma_ids=confused_lemma_ids,
    )

    db.commit()

    _log_sentence_review_interaction(
        sentence=sentence,
        comprehension_signal=comprehension_signal,
        primary_lemma_id=primary_lemma_id,
        missed_lemma_ids=missed_lemma_ids or [],
        confused_lemma_ids=confused_lemma_ids or [],
        response_ms=response_ms,
        session_id=session_id,
        review_mode=review_mode,
        client_review_id=client_review_id,
        word_results=word_results,
        leech_suspended=leech_suspended,
    )

    return {
        "word_results": word_results,
        "duplicate": False,
        "leech_suspended_lemma_ids": leech_suspended,
    }


def _log_sentence_review(
    db: Session,
    sentence: Sentence,
    now: datetime,
    comprehension_signal: str,
    response_ms: Optional[int],
    session_id: Optional[str],
    review_mode: str,
    client_review_id: Optional[str],
    missed_lemma_ids: Optional[list[int]] = None,
    confused_lemma_ids: Optional[list[int]] = None,
) -> None:
    sent_log = SentenceReviewLog(
        sentence_id=sentence.id,
        session_id=session_id,
        reviewed_at=now,
        comprehension=comprehension_signal,
        response_ms=response_ms,
        review_mode=review_mode,
        client_review_id=client_review_id,
        missed_lemma_ids=missed_lemma_ids or None,
        confused_lemma_ids=confused_lemma_ids or None,
    )
    db.add(sent_log)
    sentence.times_shown = (sentence.times_shown or 0) + 1
    sentence.last_reading_shown_at = now
    sentence.last_reading_comprehension = comprehension_signal


def _log_sentence_review_interaction(
    *,
    sentence: Sentence,
    comprehension_signal: str,
    primary_lemma_id: Optional[int],
    missed_lemma_ids: list[int],
    confused_lemma_ids: list[int],
    response_ms: Optional[int],
    session_id: Optional[str],
    review_mode: str,
    client_review_id: Optional[str],
    word_results: list[dict],
    leech_suspended: list[int],
) -> None:
    """Append the interaction log event used for production analysis."""
    log_interaction(
        event="sentence_review",
        app="polyglot",
        context="sentence_review_service",
        session_id=session_id,
        response_ms=response_ms,
        sentence_id=sentence.id,
        sentence_source=sentence.source,
        sentence_target_lemma_id=sentence.target_lemma_id,
        language_code=sentence.language_code,
        comprehension_signal=comprehension_signal,
        primary_lemma_id=primary_lemma_id,
        missed_lemma_ids=missed_lemma_ids,
        confused_lemma_ids=confused_lemma_ids,
        review_mode=review_mode,
        client_review_id=client_review_id,
        duplicate=False,
        reviewed_word_count=len(word_results),
        word_results=word_results,
        leech_suspended_lemma_ids=leech_suspended,
    )


def undo_sentence_review(
    db: Session,
    client_review_id: str,
) -> dict:
    """Reverse a previously submitted sentence review.

    Walks every ReviewLog with `client_review_id` matching
    `{base}:{lemma_id}`, restores the pre-review FSRS card + counts +
    knowledge_state from each row's `fsrs_log_json` snapshot, then deletes
    the rows. Finally drops the SentenceReviewLog audit row and decrements
    Sentence.times_shown.

    Returns `{undone: bool, reviews_removed: int}`. `undone=False` when no
    matching ReviewLog rows exist (idempotent for replayed undos).
    """
    review_logs = (
        db.query(ReviewLog)
        .filter(ReviewLog.client_review_id.like(f"{client_review_id}:%"))
        .all()
    )

    if not review_logs:
        return {"undone": False, "reviews_removed": 0}

    for log in review_logs:
        fsrs_data = parse_json_column(log.fsrs_log_json)

        ulk = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id == log.lemma_id)
            .first()
        )
        if ulk and fsrs_data:
            if "pre_card" in fsrs_data:
                ulk.fsrs_card_json = fsrs_data.get("pre_card")
            if "pre_times_seen" in fsrs_data:
                ulk.times_seen = fsrs_data.get("pre_times_seen")
            if "pre_times_correct" in fsrs_data:
                ulk.times_correct = fsrs_data.get("pre_times_correct")
            if "pre_total_encounters" in fsrs_data:
                ulk.total_encounters = fsrs_data.get("pre_total_encounters")
            if "pre_distinct_contexts" in fsrs_data:
                ulk.distinct_contexts = fsrs_data.get("pre_distinct_contexts")
            if "pre_clean_exposures" in fsrs_data:
                ulk.clean_exposures = fsrs_data.get("pre_clean_exposures")
            if "pre_confirmed_at" in fsrs_data:
                ulk.confirmed_at = _parse_snapshot_datetime(
                    fsrs_data.get("pre_confirmed_at")
                )
            if "pre_knowledge_state" in fsrs_data:
                ulk.knowledge_state = fsrs_data.get("pre_knowledge_state")
            if "pre_acquisition_box" in fsrs_data:
                ulk.acquisition_box = fsrs_data.get("pre_acquisition_box")
            if "pre_acquisition_next_due" in fsrs_data:
                ulk.acquisition_next_due = _parse_snapshot_datetime(
                    fsrs_data.get("pre_acquisition_next_due")
                )
            if "pre_graduated_at" in fsrs_data:
                ulk.graduated_at = _parse_snapshot_datetime(
                    fsrs_data.get("pre_graduated_at")
                )
            if "pre_knowledge_origin" in fsrs_data:
                ulk.knowledge_origin = fsrs_data.get("pre_knowledge_origin")
            if "pre_first_failed_at" in fsrs_data:
                ulk.first_failed_at = _parse_snapshot_datetime(
                    fsrs_data.get("pre_first_failed_at")
                )
            if "pre_last_failed_at" in fsrs_data:
                ulk.last_failed_at = _parse_snapshot_datetime(
                    fsrs_data.get("pre_last_failed_at")
                )
            if "pre_failure_count" in fsrs_data:
                ulk.failure_count = fsrs_data.get("pre_failure_count")
            if "pre_first_correct_after_failure_at" in fsrs_data:
                ulk.first_correct_after_failure_at = _parse_snapshot_datetime(
                    fsrs_data.get("pre_first_correct_after_failure_at")
                )

        db.delete(log)

    sent_logs = (
        db.query(SentenceReviewLog)
        .filter(
            or_(
                SentenceReviewLog.client_review_id == client_review_id,
                SentenceReviewLog.client_review_id.like(f"{client_review_id}:%"),
            )
        )
        .all()
    )
    for sent_log in sent_logs:
        sentence = (
            db.query(Sentence).filter(Sentence.id == sent_log.sentence_id).first()
        )
        if sentence:
            sentence.times_shown = max(0, (sentence.times_shown or 1) - 1)
            sentence.last_reading_shown_at = None
            sentence.last_reading_comprehension = None
        db.delete(sent_log)

    db.commit()
    return {"undone": True, "reviews_removed": len(review_logs)}


def _parse_snapshot_datetime(value) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None
