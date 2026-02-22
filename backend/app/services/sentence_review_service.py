"""Sentence-level review submission.

Translates sentence comprehension signals into per-word FSRS reviews.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models import (
    GrammarFeature,
    Lemma,
    ReviewLog,
    Sentence,
    SentenceGrammarFeature,
    SentenceReviewLog,
    SentenceWord,
    UserLemmaKnowledge,
)
from app.services.fsrs_service import STATE_MAP, parse_json_column, submit_review
from app.services.grammar_service import record_grammar_exposure
from app.services.sentence_validator import strip_diacritics, _is_function_word


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

    Previously unseen words are routed through acquisition (Leitner box 1)
    rather than getting FSRS cards directly.
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

    # Batch-fetch lemmas and ULK records to avoid N+1 queries in the loop
    lemma_map: dict[int, Lemma] = {}
    knowledge_map: dict[int, UserLemmaKnowledge] = {}
    function_word_lemma_ids: set[int] = set()
    suspended_lemma_ids: set[int] = set()

    # Build variant→canonical mapping so reviews credit the base lemma
    variant_to_canonical: dict[int, int] = {}

    if lemma_ids_in_sentence:
        lemma_objs = (
            db.query(Lemma)
            .filter(Lemma.lemma_id.in_(lemma_ids_in_sentence))
            .all()
        )
        lemma_map = {lo.lemma_id: lo for lo in lemma_objs}
        for lo in lemma_objs:
            if lo.lemma_ar_bare and _is_function_word(lo.lemma_ar_bare):
                function_word_lemma_ids.add(lo.lemma_id)
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

    # Identify acquiring words to route through acquisition service
    acquiring_lemma_ids: set[int] = set()
    encountered_lemma_ids: set[int] = set()
    for lid, ulk in knowledge_map.items():
        if ulk.knowledge_state == "acquiring":
            acquiring_lemma_ids.add(lid)
        elif ulk.knowledge_state == "encountered":
            encountered_lemma_ids.add(lid)

    word_results = []

    # Track which effective_lemma_ids we've already processed (dedup after redirect)
    processed_effective_ids: set[int] = set()

    for lemma_id in lemma_ids_in_sentence:
        # Skip FSRS credit for function words — they keep lemma_id in
        # SentenceWord for lookups but don't get spaced repetition cards
        if lemma_id in function_word_lemma_ids:
            continue

        # Resolve variant→canonical: credit goes to the base lemma
        effective_lemma_id = variant_to_canonical.get(lemma_id, lemma_id)

        # Skip if canonical is suspended (or the variant itself)
        if lemma_id in suspended_lemma_ids or effective_lemma_id in suspended_lemma_ids:
            continue
        # Skip encountered words — they need to be introduced first
        if effective_lemma_id in encountered_lemma_ids:
            continue
        # After redirect, multiple variant lemma_ids may map to the same canonical
        if effective_lemma_id in processed_effective_ids:
            continue
        processed_effective_ids.add(effective_lemma_id)

        if comprehension_signal == "understood":
            rating = 3
        elif comprehension_signal == "partial":
            # Check both original and effective for missed/confused signals
            if lemma_id in missed_set or effective_lemma_id in missed_set:
                rating = 1
            elif lemma_id in confused_set or effective_lemma_id in confused_set:
                rating = 2
            else:
                rating = 3
        else:  # no_idea
            rating = 1

        credit_type = "primary" if lemma_id == primary_lemma_id or effective_lemma_id == primary_lemma_id else "collateral"

        review_client_id = (
            f"{client_review_id}:{effective_lemma_id}"
            if client_review_id and sentence_id is not None
            else (
                client_review_id
                if sentence_id is None and effective_lemma_id == primary_lemma_id
                else None
            )
        )

        # Auto-introduce unknown words into acquisition instead of straight to FSRS
        if effective_lemma_id not in knowledge_map:
            from app.services.acquisition_service import start_acquisition, submit_acquisition_review as _sar
            new_ulk = start_acquisition(
                db,
                lemma_id=effective_lemma_id,
                source="collateral",
                due_immediately=False,
            )
            knowledge_map[effective_lemma_id] = new_ulk
            acquiring_lemma_ids.add(effective_lemma_id)

        # Route acquiring words through acquisition service
        if effective_lemma_id in acquiring_lemma_ids:
            from app.services.acquisition_service import submit_acquisition_review
            result = submit_acquisition_review(
                db,
                lemma_id=effective_lemma_id,
                rating_int=rating,
                response_ms=response_ms if lemma_id == primary_lemma_id else None,
                session_id=session_id,
                review_mode=review_mode,
                comprehension_signal=comprehension_signal,
                client_review_id=review_client_id,
                commit=False,
            )
        else:
            result = submit_review(
                db,
                lemma_id=effective_lemma_id,
                rating_int=rating,
                response_ms=response_ms if lemma_id == primary_lemma_id else None,
                session_id=session_id,
                review_mode=review_mode,
                comprehension_signal=comprehension_signal,
                client_review_id=review_client_id,
                commit=False,
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
            latest_log.sentence_id = sentence_id
            latest_log.credit_type = credit_type

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
            surfaces = surface_forms_by_lemma.get(lemma_id, [])
            canonical_lemma_obj = lemma_map.get(effective_lemma_id)
            canonical_bare = canonical_lemma_obj.lemma_ar_bare if canonical_lemma_obj else ""
            for surface in surfaces:
                surface_bare = strip_diacritics(surface)
                if surface_bare and surface_bare != canonical_bare:
                    vstats = parse_json_column(knowledge.variant_stats_json)
                    vstats = dict(vstats)
                    entry = vstats.get(surface_bare, {"seen": 0, "missed": 0, "confused": 0})
                    entry["seen"] = entry.get("seen", 0) + 1
                    if rating == 1:
                        entry["missed"] = entry.get("missed", 0) + 1
                    elif rating == 2:
                        entry["confused"] = entry.get("confused", 0) + 1
                    vstats[surface_bare] = entry
                    knowledge.variant_stats_json = vstats

        if not is_duplicate:
            word_results.append({
                "lemma_id": effective_lemma_id,
                "rating": rating,
                "credit_type": credit_type,
                "new_state": result["new_state"],
                "next_due": result["next_due"],
            })

    # Post-review leech check for words that got bad ratings
    from app.services.leech_service import check_single_word_leech
    for wr in word_results:
        if wr["rating"] <= 2:
            check_single_word_leech(db, wr["lemma_id"])

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

    # Record grammar exposure from sentence's word lemmas
    if sentence_id is not None:
        _record_sentence_grammar(db, sentence_id, lemma_ids_in_sentence, comprehension_signal, commit=False)

    db.commit()

    return {"word_results": word_results}


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

    if not review_logs:
        return {"undone": False, "reviews_removed": 0}

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

        db.delete(log)

    # Delete the sentence-level review log
    sent_log = (
        db.query(SentenceReviewLog)
        .filter(SentenceReviewLog.client_review_id == client_review_id)
        .first()
    )
    if sent_log:
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
