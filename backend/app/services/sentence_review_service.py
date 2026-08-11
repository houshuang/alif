"""Sentence-level review submission.

Translates sentence comprehension signals into per-word FSRS reviews.
"""

from datetime import datetime, timezone
import logging
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import (
    ConfusionCapture,
    GrammarFeature,
    Lemma,
    ReviewLog,
    Sentence,
    SentenceGrammarFeature,
    SentenceReviewLog,
    SentenceWord,
    UserLemmaKnowledge,
    WordReviewEvidence,
)
from app.services.acquisition_service import MIXED_UP_TOTAL_LAPSE_VERSION
from app.services.confusion_service import (
    classify_surface_morphology,
    normalize_surface_form,
)
from app.services.fsrs_service import STATE_MAP, parse_json_column, submit_review
from app.services.form_recovery_service import (
    FORM_RECOVERY_CAUSES,
    FORM_RECOVERY_VERSION,
    is_meaningful_form_failure,
    process_form_recovery_review,
    undo_form_recovery_reviews,
)
from app.services.grammar_service import record_grammar_exposure
from app.services.sentence_validator import (
    is_function_word_lemma,
    strip_diacritics,
)
from app.services.surface_form_experiment import (
    process_surface_experiment_review,
    undo_surface_experiment_reviews,
)

logger = logging.getLogger(__name__)

WORD_REVIEW_EVIDENCE_PROTOCOL_VERSION = 3
_SUPPORTED_WORD_REVIEW_EVIDENCE_PROTOCOLS = {1, 2, 3}
_WORD_FAILURE_CAUSES = {
    "retrieval_lapse",
    "mixed_up",
    "unfamiliar_form",
    "missing_tashkeel",
}


def submit_sentence_review(
    db: Session,
    sentence_id: Optional[int],
    primary_lemma_id: int,
    comprehension_signal: str,
    missed_lemma_ids: list[int] | None = None,
    confused_lemma_ids: list[int] | None = None,
    confusion_candidate_lemma_ids: dict[int, list[int]] | None = None,
    confusion_captures: list[dict] | None = None,
    response_ms: Optional[int] = None,
    session_id: Optional[str] = None,
    review_mode: str = "reading",
    client_review_id: Optional[str] = None,
    sentence_ids: list[int] | None = None,
    word_evidence_protocol_version: int | None = None,
    word_review_evidence: list[dict] | None = None,
) -> dict:
    """Submit a review for a whole sentence, distributing ratings to words.

    - "understood" -> all words get rating=3
    - "partial" + missed/confused -> missed get rating=1, confused get rating=2 (was_confused flag set), rest get rating=3
    - "no_idea" -> all words get rating=1

    Previously unseen words are routed through acquisition (Leitner box 1)
    rather than getting FSRS cards directly.
    """
    review_sentence_ids: list[int] = []
    for sid in sentence_ids or []:
        if sid is not None and sid not in review_sentence_ids:
            review_sentence_ids.append(sid)
    if sentence_id is not None:
        review_sentence_ids = [sentence_id] + [
            sid for sid in review_sentence_ids if sid != sentence_id
        ]
    primary_sentence_id = review_sentence_ids[0] if review_sentence_ids else None

    if client_review_id:
        if review_sentence_ids:
            sentence_log_ids = [client_review_id] + [
                f"{client_review_id}:s{sid}" for sid in review_sentence_ids
            ]
            existing = (
                db.query(SentenceReviewLog)
                .filter(SentenceReviewLog.client_review_id.in_(sentence_log_ids))
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
    lemma_ids_by_sentence: dict[int, set[int]] = {}
    surface_forms_by_lemma: dict[int, list[str]] = {}
    sentence_words: list[SentenceWord] = []
    if review_sentence_ids:
        sentence_words = (
            db.query(SentenceWord)
            .filter(SentenceWord.sentence_id.in_(review_sentence_ids))
            .order_by(SentenceWord.sentence_id, SentenceWord.position)
            .all()
        )
        lemma_ids_in_sentence = {sw.lemma_id for sw in sentence_words if sw.lemma_id}
        for sw in sentence_words:
            if sw.lemma_id:
                lemma_ids_by_sentence.setdefault(sw.sentence_id, set()).add(sw.lemma_id)
                surface_forms_by_lemma.setdefault(sw.lemma_id, []).append(sw.surface_form)
    else:
        lemma_ids_in_sentence = {primary_lemma_id}

    # Batch-fetch lemmas and ULK records to avoid N+1 queries in the loop
    lemma_map: dict[int, Lemma] = {}
    knowledge_map: dict[int, UserLemmaKnowledge] = {}
    function_word_lemma_ids: set[int] = set()
    proper_name_lemma_ids: set[int] = set()
    suspended_lemma_ids: set[int] = set()
    original_ids_by_effective: dict[int, set[int]] = {}
    surface_evidence_by_effective: dict[int, list[tuple[int, str]]] = {}

    # Build variant→canonical mapping so reviews credit the base lemma.
    # Must follow multi-hop chains (A→B→C) to the root canonical.
    variant_to_canonical: dict[int, int] = {}

    if lemma_ids_in_sentence:
        lemma_objs = (
            db.query(Lemma)
            .filter(Lemma.lemma_id.in_(lemma_ids_in_sentence))
            .all()
        )
        lemma_map = {lo.lemma_id: lo for lo in lemma_objs}
        for lo in lemma_objs:
            if is_function_word_lemma(
                lo.lemma_ar_bare, lo.function_word_override
            ):
                function_word_lemma_ids.add(lo.lemma_id)
            if lo.word_category == "proper_name":
                proper_name_lemma_ids.add(lo.lemma_id)
            if lo.canonical_lemma_id:
                variant_to_canonical[lo.lemma_id] = lo.canonical_lemma_id

        # Also fetch canonical lemmas that may not be in the sentence directly
        canonical_ids_needed = set(variant_to_canonical.values()) - lemma_ids_in_sentence
        if canonical_ids_needed:
            canonical_lemma_objs = (
                db.query(Lemma)
                .filter(Lemma.lemma_id.in_(canonical_ids_needed))
                .all()
            )
            for lo in canonical_lemma_objs:
                lemma_map[lo.lemma_id] = lo

        # Follow multi-hop chains: if A→B and B→C, resolve A→C
        # (e.g. الغرفة→غرفة→غرف where غرف is the root canonical)
        changed = True
        while changed:
            changed = False
            next_hop_ids = set()
            for vid, cid in list(variant_to_canonical.items()):
                # Check if the canonical is itself a variant
                canon_lemma = lemma_map.get(cid)
                if canon_lemma and canon_lemma.canonical_lemma_id:
                    variant_to_canonical[vid] = canon_lemma.canonical_lemma_id
                    next_hop_ids.add(canon_lemma.canonical_lemma_id)
                    changed = True
            # Fetch any new canonical lemmas we haven't loaded yet
            missing = next_hop_ids - set(lemma_map.keys())
            if missing:
                for lo in db.query(Lemma).filter(Lemma.lemma_id.in_(missing)).all():
                    lemma_map[lo.lemma_id] = lo

        # Fetch ULK for both sentence lemma_ids and their canonical targets
        all_ulk_ids = lemma_ids_in_sentence | set(variant_to_canonical.values())
        ulk_objs = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id.in_(all_ulk_ids))
            .all()
        )
        for ulk in ulk_objs:
            knowledge_map[ulk.lemma_id] = ulk
            if ulk.knowledge_state == "suspended":
                suspended_lemma_ids.add(ulk.lemma_id)

        # Scheduling and experiment evidence live on the canonical lemma. A
        # sentence can contain more than one lexical variant that resolves to
        # it, so aggregate every displayed form before deduplicating review
        # credit. Otherwise set iteration order decides which form survives.
        for original_id in lemma_ids_in_sentence:
            effective_id = variant_to_canonical.get(original_id, original_id)
            original_ids_by_effective.setdefault(effective_id, set()).add(original_id)
            surface_evidence_by_effective.setdefault(effective_id, []).extend(
                (original_id, surface)
                for surface in surface_forms_by_lemma.get(original_id, [])
            )

    # Identify acquiring words to route through acquisition service
    acquiring_lemma_ids: set[int] = set()
    encountered_lemma_ids: set[int] = set()
    for lid, ulk in knowledge_map.items():
        if ulk.knowledge_state == "acquiring":
            acquiring_lemma_ids.add(lid)
        elif ulk.knowledge_state == "encountered":
            encountered_lemma_ids.add(lid)

    validated_word_evidence = _validate_word_review_evidence(
        client_review_id=client_review_id,
        protocol_version=word_evidence_protocol_version,
        evidence_rows=word_review_evidence or [],
        sentence_words=sentence_words,
        review_sentence_ids=review_sentence_ids,
        comprehension_signal=comprehension_signal,
        missed_set=missed_set,
        confused_set=confused_set,
        review_mode=review_mode,
        variant_to_canonical=variant_to_canonical,
        function_word_lemma_ids=function_word_lemma_ids,
        proper_name_lemma_ids=proper_name_lemma_ids,
    )
    evidence_by_effective: dict[int, list[dict]] = {}
    for row in validated_word_evidence:
        evidence_by_effective.setdefault(row["effective_lemma_id"], []).append(row)
    protected_effective_ids = _form_recovery_protected_lemma_ids(
        protocol_version=word_evidence_protocol_version,
        comprehension_signal=comprehension_signal,
        evidence_by_effective=evidence_by_effective,
        surface_evidence_by_effective=surface_evidence_by_effective,
        lemma_map=lemma_map,
    )

    word_results = []
    latest_review_log_by_effective: dict[int, ReviewLog] = {}

    # Track which effective_lemma_ids we've already processed (dedup after redirect)
    processed_effective_ids: set[int] = set()

    for lemma_id in lemma_ids_in_sentence:
        # Skip FSRS credit for function words — they keep lemma_id in
        # SentenceWord for lookups but don't get spaced repetition cards
        if lemma_id in function_word_lemma_ids:
            continue

        # Proper names are decorative tokens. They have a real lemma_id so the
        # sentence passes the reviewability gate, but they never enter the SRS
        # pipeline — no FSRS card, no acquisition box, no review credit.
        if lemma_id in proper_name_lemma_ids:
            continue

        # Resolve variant→canonical: credit goes to the base lemma
        effective_lemma_id = variant_to_canonical.get(lemma_id, lemma_id)
        canonical_member_ids = original_ids_by_effective.get(
            effective_lemma_id,
            {lemma_id},
        )

        # Skip if canonical is suspended (or the variant itself)
        if lemma_id in suspended_lemma_ids or effective_lemma_id in suspended_lemma_ids:
            continue
        # Auto-introduce encountered words on collateral appearance —
        # every word in every sentence earns review credit, no exceptions.
        # Familiar words graduate instantly via Tier 0 (first correct → FSRS).
        # The daily intro cap inside start_acquisition may defer promotion
        # (leaves the word encountered); track the "deferred" state so we
        # bump total_encounters but skip the acquisition-review submission.
        cap_deferred = False
        if effective_lemma_id in encountered_lemma_ids:
            from app.services.acquisition_service import start_acquisition
            promoted_ulk = start_acquisition(
                db,
                lemma_id=effective_lemma_id,
                source="collateral",
                due_immediately=False,
            )
            if promoted_ulk.knowledge_state == "acquiring":
                acquiring_lemma_ids.add(effective_lemma_id)
                encountered_lemma_ids.discard(effective_lemma_id)
            else:
                cap_deferred = True
        # After redirect, multiple variant lemma_ids may map to the same canonical
        if effective_lemma_id in processed_effective_ids:
            continue
        processed_effective_ids.add(effective_lemma_id)

        is_confused = False
        if comprehension_signal == "understood":
            rating = 3
        elif comprehension_signal == "partial":
            # Check every displayed variant plus the canonical signal. A card
            # may contain two variants that resolve to one scheduling row.
            if canonical_member_ids & missed_set or effective_lemma_id in missed_set:
                rating = 1
            elif canonical_member_ids & confused_set or effective_lemma_id in confused_set:
                # Confused = knew the word but didn't recognize it in context.
                # Rating 2 means recognition only after reveal. The stored
                # rating stays 2; FSRS applies the assisted-lapse policy.
                rating = 2
                is_confused = True
            else:
                rating = 3
        else:  # no_idea
            rating = 1

        is_primary = (
            primary_lemma_id in canonical_member_ids
            or effective_lemma_id == primary_lemma_id
        )
        credit_type = "primary" if is_primary else "collateral"
        form_recovery_protected = (
            rating <= 2 and effective_lemma_id in protected_effective_ids
        )
        recovery_rows = evidence_by_effective.get(effective_lemma_id, [])
        mixed_up_rows = [
            row
            for row in recovery_rows
            if row["rating"] <= 2
            and row["rating_source"] == "token_mark"
            and "mixed_up" in row["causes"]
        ]
        mixed_up_total_lapse = (
            word_evidence_protocol_version
            == WORD_REVIEW_EVIDENCE_PROTOCOL_VERSION
            and rating == 2
            and bool(mixed_up_rows)
        )
        review_metadata = None
        if form_recovery_protected:
            review_metadata = {
                "form_recovery_policy_version": FORM_RECOVERY_VERSION,
                "form_recovery_protected": True,
                "form_recovery_product_rating": rating,
                "form_recovery_causes": sorted({
                    cause
                    for row in recovery_rows
                    if row["rating"] <= 2
                    for cause in row["causes"]
                }),
                "form_recovery_sentence_word_ids": [
                    row["sentence_word_id"]
                    for row in recovery_rows
                    if row["rating"] <= 2
                ],
            }
        elif mixed_up_total_lapse:
            review_metadata = {
                "mixed_up_total_lapse_policy_version": (
                    MIXED_UP_TOTAL_LAPSE_VERSION
                ),
                "mixed_up_total_lapse": True,
                "mixed_up_product_rating": rating,
                "mixed_up_sentence_word_ids": [
                    row["sentence_word_id"] for row in mixed_up_rows
                ],
            }

        review_client_id = (
            f"{client_review_id}:{effective_lemma_id}"
            if client_review_id and review_sentence_ids
            else (
                client_review_id
                if not review_sentence_ids and effective_lemma_id == primary_lemma_id
                else None
            )
        )

        # Auto-introduce unknown words into acquisition instead of straight to FSRS.
        # Cap may defer promotion — in that case the new ULK is in 'encountered'.
        if effective_lemma_id not in knowledge_map:
            from app.services.acquisition_service import start_acquisition, submit_acquisition_review as _sar
            new_ulk = start_acquisition(
                db,
                lemma_id=effective_lemma_id,
                source="collateral",
                due_immediately=False,
            )
            knowledge_map[effective_lemma_id] = new_ulk
            if new_ulk.knowledge_state == "acquiring":
                acquiring_lemma_ids.add(effective_lemma_id)
            else:
                cap_deferred = True

        if cap_deferred:
            # No review credit while word sits in encountered. Bump total_encounters
            # so it's tracked, then move on to the next word.
            knowledge = knowledge_map.get(effective_lemma_id)
            if knowledge:
                knowledge.total_encounters = (knowledge.total_encounters or 0) + 1
            continue

        # Route acquiring words through acquisition service
        if effective_lemma_id in acquiring_lemma_ids:
            from app.services.acquisition_service import submit_acquisition_review
            result = submit_acquisition_review(
                db,
                lemma_id=effective_lemma_id,
                rating_int=rating,
                response_ms=response_ms if is_primary else None,
                session_id=session_id,
                review_mode=review_mode,
                comprehension_signal=comprehension_signal,
                client_review_id=review_client_id,
                commit=False,
                was_confused=is_confused,
                effective_rating_int=2 if form_recovery_protected else None,
                review_metadata=review_metadata,
            )
        else:
            result = submit_review(
                db,
                lemma_id=effective_lemma_id,
                rating_int=rating,
                response_ms=response_ms if is_primary else None,
                session_id=session_id,
                review_mode=review_mode,
                comprehension_signal=comprehension_signal,
                client_review_id=review_client_id,
                commit=False,
                was_confused=is_confused,
                effective_rating_int=2 if form_recovery_protected else None,
                review_metadata=review_metadata,
            )
        is_duplicate = bool(result.get("duplicate"))
        # Tag the review log entry with sentence context
        latest_log = (
            db.query(ReviewLog)
            .filter(ReviewLog.lemma_id == effective_lemma_id)
            .order_by(ReviewLog.id.desc())
            .first()
        )
        if latest_log and not is_duplicate:
            latest_log.sentence_id = primary_sentence_id
            latest_log.credit_type = credit_type
            latest_review_log_by_effective[effective_lemma_id] = latest_log

        # Track encounters on the canonical ULK
        knowledge = knowledge_map.get(effective_lemma_id)
        if not knowledge:
            knowledge = (
                db.query(UserLemmaKnowledge)
                .filter(UserLemmaKnowledge.lemma_id == effective_lemma_id)
                .first()
            )
            if knowledge:
                knowledge_map[effective_lemma_id] = knowledge
        if knowledge and not is_duplicate:
            knowledge.total_encounters = (knowledge.total_encounters or 0) + 1

            # Track variant form stats on the canonical ULK
            surface_evidence = surface_evidence_by_effective.get(
                effective_lemma_id,
                [],
            )
            surfaces = [surface for _original_id, surface in surface_evidence]
            canonical_lemma_obj = lemma_map.get(effective_lemma_id)
            canonical_key = normalize_surface_form(
                canonical_lemma_obj.lemma_ar_bare if canonical_lemma_obj else ""
            )
            for original_id, surface in surface_evidence:
                # Preserve the established, human-readable stats key (tashkeel
                # stripped, hamza retained) so the pilot does not split years of
                # evidence across old and newly normalized keys. Canonical
                # normalization is used only for comparison and morphology.
                surface_key = normalize_surface_form(surface)
                surface_stats_key = strip_diacritics(surface)
                if surface_stats_key and surface_key != canonical_key:
                    vstats = parse_json_column(knowledge.variant_stats_json)
                    vstats = dict(vstats)
                    entry = dict(vstats.get(
                        surface_stats_key,
                        {"seen": 0, "missed": 0, "confused": 0},
                    ))
                    entry["seen"] = entry.get("seen", 0) + 1
                    surface_missed = (
                        comprehension_signal == "no_idea"
                        or original_id in missed_set
                        or effective_lemma_id in missed_set
                    )
                    surface_confused = (
                        original_id in confused_set
                        or effective_lemma_id in confused_set
                    )
                    if surface_missed:
                        entry["missed"] = entry.get("missed", 0) + 1
                    elif surface_confused:
                        entry["confused"] = entry.get("confused", 0) + 1
                    morph = classify_surface_morphology(surface, canonical_lemma_obj)
                    if morph:
                        entry["category"] = morph["category"]
                        if morph.get("form_key"):
                            entry["form_key"] = morph["form_key"]
                            entry["form_label"] = morph["form_key"].replace("_", " ")
                    vstats[surface_stats_key] = entry
                    knowledge.variant_stats_json = vstats

            process_surface_experiment_review(
                db,
                knowledge=knowledge,
                lemma=canonical_lemma_obj,
                surfaces=surfaces,
                review_log=latest_log,
                credit_type=credit_type,
                sentence_ids=review_sentence_ids,
                now=now,
            )
            process_form_recovery_review(
                knowledge=knowledge,
                lemma=canonical_lemma_obj,
                rows=recovery_rows,
                protected=form_recovery_protected,
                review_log=latest_log,
                client_review_id=client_review_id,
                now=now,
            )

        if not is_duplicate:
            word_results.append({
                "lemma_id": effective_lemma_id,
                "rating": rating,
                "credit_type": credit_type,
                "form_recovery_protected": form_recovery_protected,
                "new_state": result["new_state"],
                "next_due": result["next_due"],
            })

    # Post-review leech check for every word in the review. Runs on all
    # ratings (not just failures) because the sliding-window leech criterion
    # looks at the last N reviews — a correct review can evict an older
    # correct one and flip the window into leech territory.
    from app.services.leech_service import check_single_word_leech
    for wr in word_results:
        check_single_word_leech(db, wr["lemma_id"])

    # Log the sentence-level review
    if review_sentence_ids:
        sentence_map = {
            s.id: s
            for s in db.query(Sentence)
            .filter(Sentence.id.in_(review_sentence_ids))
            .all()
        }
        for idx, sid in enumerate(review_sentence_ids):
            sent_log = SentenceReviewLog(
                sentence_id=sid,
                session_id=session_id,
                reviewed_at=now,
                comprehension=comprehension_signal,
                response_ms=response_ms,
                review_mode=review_mode,
                client_review_id=(
                    client_review_id
                    if idx == 0 or not client_review_id
                    else f"{client_review_id}:s{sid}"
                ),
            )
            db.add(sent_log)

            sentence = sentence_map.get(sid)
            if sentence:
                sentence.times_shown = (sentence.times_shown or 0) + 1
                if review_mode == "listening":
                    sentence.last_listening_shown_at = now
                    sentence.last_listening_comprehension = comprehension_signal
                else:
                    sentence.last_reading_shown_at = now
                    sentence.last_reading_comprehension = comprehension_signal

    # Record grammar exposure from sentence's word lemmas
    for sid in review_sentence_ids:
        _record_sentence_grammar(
            db,
            sid,
            lemma_ids_by_sentence.get(sid, lemma_ids_in_sentence),
            comprehension_signal,
            commit=False,
        )

    for cap in confusion_captures or []:
        method = cap.get("capture_method")
        failed_lid = cap.get("failed_lemma_id")
        if not failed_lid or method not in ("suggested_pick", "free_text"):
            continue
        confused_with_lid = cap.get("confused_with_lemma_id")
        confused_with_text = cap.get("confused_with_text")
        if method == "suggested_pick":
            if not confused_with_lid:
                continue
            confused_with_text = None
        else:  # free_text
            text = (confused_with_text or "").strip()
            if not text:
                continue
            confused_with_text = text
            confused_with_lid = None
        rating_for_capture = 1 if comprehension_signal == "no_idea" else 2
        db.add(ConfusionCapture(
            failed_lemma_id=failed_lid,
            sentence_id=primary_sentence_id,
            session_id=session_id,
            rating=rating_for_capture,
            captured_at=now,
            capture_method=method,
            confused_with_lemma_id=confused_with_lid,
            confused_with_text=confused_with_text,
            candidates_shown_json=cap.get("candidates_shown") or None,
        ))

    word_evidence_saved = _persist_word_review_evidence(
        db,
        client_review_id=client_review_id,
        protocol_version=word_evidence_protocol_version,
        validated_rows=validated_word_evidence,
        review_mode=review_mode,
        latest_review_log_by_effective=latest_review_log_by_effective,
        now=now,
    )

    db.commit()

    return {
        "word_results": word_results,
        "word_evidence_saved": word_evidence_saved,
    }


def _validate_word_review_evidence(
    *,
    client_review_id: str | None,
    protocol_version: int | None,
    evidence_rows: list[dict],
    sentence_words: list[SentenceWord],
    review_sentence_ids: list[int],
    comprehension_signal: str,
    missed_set: set[int],
    confused_set: set[int],
    review_mode: str,
    variant_to_canonical: dict[int, int],
    function_word_lemma_ids: set[int],
    proper_name_lemma_ids: set[int],
) -> list[dict]:
    """Validate presentation evidence before it can affect scheduling.

    Invalid or stale rows are dropped without blocking the canonical review.
    Only the returned protocol-v3 rows may participate in form-recovery
    protection; persistence consumes this exact validated representation.
    """

    if not evidence_rows:
        return []
    if not isinstance(evidence_rows, list):
        logger.warning("Dropping non-list word review evidence payload")
        return []
    if not client_review_id:
        logger.warning("Dropping word review evidence without client_review_id")
        return []
    if protocol_version not in _SUPPORTED_WORD_REVIEW_EVIDENCE_PROTOCOLS:
        logger.warning(
            "Dropping word review evidence with unsupported protocol version %r",
            protocol_version,
        )
        return []
    if review_mode != "reading":
        logger.warning("Dropping word review evidence for review_mode=%s", review_mode)
        return []

    by_id = {sw.id: sw for sw in sentence_words}
    allowed_sentence_ids = set(review_sentence_ids)
    seen_sentence_word_ids: set[int] = set()
    validated: list[dict] = []

    for evidence in evidence_rows[:100]:
        if not isinstance(evidence, dict):
            continue
        sentence_word_id = evidence.get("sentence_word_id")
        if (
            not isinstance(sentence_word_id, int)
            or sentence_word_id in seen_sentence_word_ids
        ):
            continue
        seen_sentence_word_ids.add(sentence_word_id)

        sw = by_id.get(sentence_word_id)
        if (
            sw is None
            or sw.sentence_id not in allowed_sentence_ids
            or sw.lemma_id is None
        ):
            continue
        is_function_word = sw.lemma_id in function_word_lemma_ids
        is_proper_name = sw.lemma_id in proper_name_lemma_ids
        is_schedulable_content = not is_function_word and not is_proper_name
        # Protocol 1 clients deliberately omitted inert tokens. Do not broaden
        # a stale client's contract if it submits an unexpected row; protocol
        # 2 is the explicit all-token presentation ledger.
        if protocol_version == 1 and not is_schedulable_content:
            continue

        surface_form = evidence.get("surface_form")
        rendered_front_form = evidence.get("rendered_front_form")
        if (
            surface_form != sw.surface_form
            or not isinstance(rendered_front_form, str)
            or len(rendered_front_form) > 100
        ):
            logger.warning(
                "Dropping stale/invalid word evidence for sentence_word_id=%s",
                sentence_word_id,
            )
            continue

        rating = evidence.get("rating")
        effective_lemma_id = variant_to_canonical.get(sw.lemma_id, sw.lemma_id)
        marked_missed = (
            sw.lemma_id in missed_set or effective_lemma_id in missed_set
        )
        marked_confused = (
            sw.lemma_id in confused_set or effective_lemma_id in confused_set
        )
        rating_source = (
            "token_mark"
            if comprehension_signal == "partial"
            and (marked_missed or marked_confused)
            else "sentence_comprehension"
        )
        if comprehension_signal == "no_idea":
            valid_rating = rating == 1
        elif comprehension_signal == "understood":
            valid_rating = rating == 3
        else:
            valid_rating = (
                rating == 3
                or (rating == 1 and marked_missed)
                # Duplicate occurrences of one lemma may have different exact
                # token outcomes. Lemma-level missed/confused arrays can then
                # contain the same ID, so do not let the scheduling precedence
                # (rating 1) erase a sibling token's valid rating-2 evidence.
                or (rating == 2 and marked_confused)
            )
        if not valid_rating:
            continue

        causes = list(dict.fromkeys(evidence.get("failure_causes") or []))
        if (
            any(cause not in _WORD_FAILURE_CAUSES for cause in causes)
            or (
                causes
                and (
                    rating not in (1, 2)
                    or (rating == 1 and protocol_version < 3)
                )
            )
            or (
                "retrieval_lapse" in causes
                and len(causes) > 1
            )
        ):
            continue

        default_show = evidence.get("default_show_tashkeel")
        if not isinstance(default_show, bool):
            continue
        expected_front_form = (
            surface_form if default_show else strip_diacritics(surface_form)
        )
        if rendered_front_form != expected_front_form:
            continue

        initial_visible = (
            strip_diacritics(rendered_front_form) != rendered_front_form
        )
        stored_has_tashkeel = strip_diacritics(surface_form) != surface_form
        reported_initial_visible = evidence.get(
            "front_initial_tashkeel_visible"
        )
        ever_visible = evidence.get("front_ever_tashkeel_visible")
        visible_at_answer = evidence.get("front_tashkeel_visible_at_answer")
        front_toggle_count = evidence.get("front_toggle_count")
        answer_revealed = evidence.get("answer_revealed")
        back_visible = evidence.get("back_tashkeel_visible_at_rating")
        back_toggle_count = evidence.get("back_toggle_count")
        if (
            reported_initial_visible is not initial_visible
            or not isinstance(ever_visible, bool)
            or not isinstance(visible_at_answer, bool)
            or not isinstance(front_toggle_count, int)
            or not 0 <= front_toggle_count <= 20
            or not isinstance(answer_revealed, bool)
            or not isinstance(back_toggle_count, int)
            or not 0 <= back_toggle_count <= 20
            or (initial_visible and not ever_visible)
            or (visible_at_answer and not ever_visible)
            or (
                front_toggle_count == 0
                and (
                    ever_visible != initial_visible
                    or visible_at_answer != initial_visible
                )
            )
            or (not answer_revealed and back_visible is not None)
            or (answer_revealed and not isinstance(back_visible, bool))
            or (
                "missing_tashkeel" in causes
                and (initial_visible or not stored_has_tashkeel)
            )
        ):
            continue

        validated.append({
            "sentence_word_id": sw.id,
            "sentence_id": sw.sentence_id,
            "position": sw.position,
            "lemma_id": sw.lemma_id,
            "effective_lemma_id": effective_lemma_id,
            "rating": rating,
            "is_schedulable_content": is_schedulable_content,
            "is_function_word": is_function_word,
            "is_proper_name": is_proper_name,
            "rating_source": rating_source,
            "surface_form": surface_form,
            "rendered_front_form": rendered_front_form,
            "default_show_tashkeel": default_show,
            "front_initial_tashkeel_visible": initial_visible,
            "front_ever_tashkeel_visible": ever_visible,
            "front_tashkeel_visible_at_answer": visible_at_answer,
            "front_toggle_count": front_toggle_count,
            "answer_revealed": answer_revealed,
            "back_tashkeel_visible_at_rating": back_visible,
            "back_toggle_count": back_toggle_count,
            "causes": causes,
        })

    if len(evidence_rows) > 100:
        logger.warning(
            "Truncated word review evidence payload from %s to 100 rows",
            len(evidence_rows),
        )
    return validated


def _form_recovery_protected_lemma_ids(
    *,
    protocol_version: int | None,
    comprehension_signal: str,
    evidence_by_effective: dict[int, list[dict]],
    surface_evidence_by_effective: dict[int, list[tuple[int, str]]],
    lemma_map: dict[int, Lemma],
) -> set[int]:
    """Find canonicals whose every failed token is form/tashkeel-isolated."""
    if protocol_version != WORD_REVIEW_EVIDENCE_PROTOCOL_VERSION:
        return set()
    if comprehension_signal != "partial":
        return set()

    protected: set[int] = set()
    for effective_id, rows in evidence_by_effective.items():
        content_rows = [row for row in rows if row["is_schedulable_content"]]
        # Protocol v3 is an all-token ledger. Missing even one mapped occurrence
        # makes the attribution ambiguous and therefore ineligible.
        expected_count = len(surface_evidence_by_effective.get(effective_id, []))
        if not content_rows or len(content_rows) != expected_count:
            continue
        failed_rows = [row for row in content_rows if row["rating"] <= 2]
        if not failed_rows:
            continue
        lemma = lemma_map.get(effective_id)
        eligible = True
        for row in failed_rows:
            causes = set(row["causes"])
            if (
                row["rating_source"] != "token_mark"
                or not causes
                or not causes <= FORM_RECOVERY_CAUSES
            ):
                eligible = False
                break
            if (
                "unfamiliar_form" in causes
                and not is_meaningful_form_failure(row["surface_form"], lemma)
            ):
                eligible = False
                break
        if eligible:
            protected.add(effective_id)
    return protected


def _persist_word_review_evidence(
    db: Session,
    *,
    client_review_id: str | None,
    protocol_version: int | None,
    validated_rows: list[dict],
    review_mode: str,
    latest_review_log_by_effective: dict[int, ReviewLog],
    now: datetime,
) -> int:
    if not client_review_id or protocol_version is None:
        return 0
    for row in validated_rows:
        latest_log = latest_review_log_by_effective.get(row["effective_lemma_id"])
        db.add(WordReviewEvidence(
            client_review_id=client_review_id,
            review_log_id=latest_log.id if latest_log else None,
            sentence_word_id=row["sentence_word_id"],
            sentence_id=row["sentence_id"],
            position=row["position"],
            lemma_id=row["lemma_id"],
            canonical_lemma_id=row["effective_lemma_id"],
            rating=row["rating"],
            review_mode=review_mode,
            protocol_version=protocol_version,
            is_schedulable_content=row["is_schedulable_content"],
            is_function_word=row["is_function_word"],
            is_proper_name=row["is_proper_name"],
            rating_source=row["rating_source"],
            surface_form=row["surface_form"],
            rendered_front_form=row["rendered_front_form"],
            default_show_tashkeel=row["default_show_tashkeel"],
            front_initial_tashkeel_visible=row["front_initial_tashkeel_visible"],
            front_ever_tashkeel_visible=row["front_ever_tashkeel_visible"],
            front_tashkeel_visible_at_answer=row["front_tashkeel_visible_at_answer"],
            front_toggle_count=row["front_toggle_count"],
            answer_revealed=row["answer_revealed"],
            back_tashkeel_visible_at_rating=row["back_tashkeel_visible_at_rating"],
            back_toggle_count=row["back_toggle_count"],
            failure_causes_json=row["causes"] or None,
            created_at=now,
        ))
    return len(validated_rows)


def _record_sentence_grammar(
    db: Session,
    sentence_id: int,
    lemma_ids: set[int],
    comprehension_signal: str,
    commit: bool = True,
) -> None:
    """Derive grammar features from sentence words and record exposure."""
    # Collect grammar features from lemma tags
    feature_keys: set[str] = set()

    # First check if sentence already has grammar features tagged
    existing_sgf = (
        db.query(SentenceGrammarFeature)
        .filter(SentenceGrammarFeature.sentence_id == sentence_id)
        .all()
    )
    if existing_sgf:
        for sgf in existing_sgf:
            if sgf.feature and sgf.feature.feature_key:
                feature_keys.add(sgf.feature.feature_key)
    else:
        # Derive from lemma grammar_features_json
        lemmas = (
            db.query(Lemma)
            .filter(Lemma.lemma_id.in_(lemma_ids))
            .all()
        )
        for lemma in lemmas:
            if lemma.grammar_features_json:
                feats = lemma.grammar_features_json
                if isinstance(feats, str):
                    import json
                    feats = json.loads(feats)
                if isinstance(feats, list):
                    feature_keys.update(feats)

        # Auto-populate SentenceGrammarFeature rows for future use
        if feature_keys:
            known_features = {
                f.feature_key: f.feature_id
                for f in db.query(GrammarFeature)
                .filter(GrammarFeature.feature_key.in_(feature_keys))
                .all()
            }
            for key in feature_keys:
                fid = known_features.get(key)
                if fid:
                    db.add(SentenceGrammarFeature(
                        sentence_id=sentence_id,
                        feature_id=fid,
                        is_primary=False,
                        source="derived",
                    ))

    # Record exposure: understood/partial → correct, no_idea → incorrect
    correct = comprehension_signal in ("understood", "partial")
    for key in feature_keys:
        record_grammar_exposure(db, key, correct=correct, commit=commit)


def undo_sentence_review(
    db: Session,
    client_review_id: str,
) -> dict:
    """Undo a previously submitted sentence review.

    Finds ReviewLog entries by client_review_id prefix pattern ({base_id}:{lemma_id}),
    restores pre-review FSRS card state from fsrs_log_json snapshots, and deletes
    the log entries.
    """
    # Sentence reviews use composite client_review_ids: {base_id}:{lemma_id}
    review_logs = (
        db.query(ReviewLog)
        .filter(ReviewLog.client_review_id.like(f"{client_review_id}:%"))
        .all()
    )

    sent_logs = (
        db.query(SentenceReviewLog)
        .filter(
            or_(
                SentenceReviewLog.client_review_id == client_review_id,
                SentenceReviewLog.client_review_id.like(f"{client_review_id}:s%"),
            )
        )
        .all()
    )
    evidence_removed = (
        db.query(WordReviewEvidence)
        .filter(WordReviewEvidence.client_review_id == client_review_id)
        .delete(synchronize_session=False)
    )

    if not review_logs and not sent_logs and not evidence_removed:
        return {"undone": False, "reviews_removed": 0}

    deleted_review_ids_by_lemma: dict[int, set[int]] = {}
    for log in review_logs:
        deleted_review_ids_by_lemma.setdefault(log.lemma_id, set()).add(log.id)

    # Restore pre-review FSRS state for each word
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
            undo_surface_experiment_reviews(
                ulk,
                deleted_review_ids_by_lemma.get(log.lemma_id, set()),
            )
            undo_form_recovery_reviews(
                ulk,
                deleted_review_ids_by_lemma.get(log.lemma_id, set()),
            )

        db.delete(log)

    # Delete the sentence-level review log
    for sent_log in sent_logs:
        sentence = db.query(Sentence).filter(Sentence.id == sent_log.sentence_id).first()
        if sentence:
            sentence.times_shown = max(0, (sentence.times_shown or 1) - 1)
            if sent_log.review_mode == "listening":
                sentence.last_listening_comprehension = None
                sentence.last_listening_shown_at = None
            else:
                sentence.last_reading_comprehension = None
                sentence.last_reading_shown_at = None
        db.delete(sent_log)

    db.commit()
    return {"undone": True, "reviews_removed": len(review_logs)}
