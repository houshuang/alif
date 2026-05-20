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
from app.services.fsrs_service import parse_json_column, submit_review
from app.services.leech_service import check_single_word_leech
from app.services.lemma_quality import FUNCTION_WORD_SETS

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
    FUNCTION_WORD_SETS for the sentence's language) and proper names
    (word_category='proper_name') are skipped — they carry a lemma_id only so
    the sentence passes the reviewability gate.

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
            return {"word_results": [], "duplicate": True, "leech_suspended_lemma_ids": []}
        existing_word_log = (
            db.query(ReviewLog)
            .filter(ReviewLog.client_review_id.like(f"{word_log_prefix}%"))
            .first()
        )
        if existing_word_log:
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
        return {"word_results": [], "duplicate": False, "leech_suspended_lemma_ids": []}

    language_code = sentence.language_code
    function_word_bares = FUNCTION_WORD_SETS.get(language_code, set())

    lemma_objs = (
        db.query(Lemma)
        .filter(Lemma.lemma_id.in_(lemma_ids_in_sentence))
        .all()
    )
    lemma_map: dict[int, Lemma] = {lo.lemma_id: lo for lo in lemma_objs}

    function_word_lemma_ids: set[int] = set()
    proper_name_lemma_ids: set[int] = set()
    for lo in lemma_objs:
        is_fw_by_category = lo.word_category == "function_word"
        is_fw_by_bare = lo.lemma_bare in function_word_bares
        if is_fw_by_category or is_fw_by_bare:
            function_word_lemma_ids.add(lo.lemma_id)
        if lo.word_category == "proper_name":
            proper_name_lemma_ids.add(lo.lemma_id)

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
            if lo.word_category == "function_word" or lo.lemma_bare in function_word_bares:
                function_word_lemma_ids.add(lo.lemma_id)
            if lo.word_category == "proper_name":
                proper_name_lemma_ids.add(lo.lemma_id)

    all_ulk_ids = lemma_ids_in_sentence | set(variant_to_canonical.values())
    ulk_objs = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id.in_(all_ulk_ids))
        .all()
    )
    knowledge_map: dict[int, UserLemmaKnowledge] = {ulk.lemma_id: ulk for ulk in ulk_objs}
    suspended_lemma_ids = {
        lid for lid, ulk in knowledge_map.items()
        if ulk.knowledge_state == "suspended"
    }

    word_results: list[dict] = []
    processed_effective_ids: set[int] = set()

    for lemma_id in lemma_ids_in_sentence:
        effective_lemma_id = variant_to_canonical.get(lemma_id, lemma_id)

        # Check both variant and canonical for filter membership — handles the
        # rare case of a variant pointing to a function-word/proper-name root.
        if lemma_id in function_word_lemma_ids or effective_lemma_id in function_word_lemma_ids:
            continue
        if lemma_id in proper_name_lemma_ids or effective_lemma_id in proper_name_lemma_ids:
            continue
        if lemma_id in suspended_lemma_ids or effective_lemma_id in suspended_lemma_ids:
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

        word_client_id = (
            f"{client_review_id}:{effective_lemma_id}"
            if client_review_id
            else None
        )

        knowledge = knowledge_map.get(effective_lemma_id)
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
    )

    db.commit()

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
) -> None:
    sent_log = SentenceReviewLog(
        sentence_id=sentence.id,
        session_id=session_id,
        reviewed_at=now,
        comprehension=comprehension_signal,
        response_ms=response_ms,
        review_mode=review_mode,
        client_review_id=client_review_id,
    )
    db.add(sent_log)
    sentence.times_shown = (sentence.times_shown or 0) + 1
    sentence.last_reading_shown_at = now
    sentence.last_reading_comprehension = comprehension_signal


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
        pre_card = fsrs_data.get("pre_card") if fsrs_data else None
        pre_times_seen = fsrs_data.get("pre_times_seen") if fsrs_data else None
        pre_times_correct = fsrs_data.get("pre_times_correct") if fsrs_data else None
        pre_knowledge_state = fsrs_data.get("pre_knowledge_state") if fsrs_data else None

        ulk = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id == log.lemma_id)
            .first()
        )
        if ulk:
            if pre_card is not None:
                ulk.fsrs_card_json = pre_card
            if pre_times_seen is not None:
                ulk.times_seen = pre_times_seen
            if pre_times_correct is not None:
                ulk.times_correct = pre_times_correct
            if pre_knowledge_state is not None:
                ulk.knowledge_state = pre_knowledge_state

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
