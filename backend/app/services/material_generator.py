"""Shared material generation: sentences + audio for words.

Used by both learn.py (word introduction) and ocr_service.py (post-import).
"""

import asyncio
import logging
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge

logger = logging.getLogger(__name__)


def generate_material_for_word(lemma_id: int, needed: int = 2, model_override: str = "gemini") -> None:
    """Background task: generate sentences + audio for a word.

    Opens its own DB session so it can run in a background thread.
    Uses dynamic difficulty based on the word's familiarity level.
    """
    db = SessionLocal()
    try:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        if not lemma:
            return

        # GPT prompt words: known/learning/lapsed/acquiring (active vocabulary)
        # Validation words: also include encountered (passive vocabulary)
        active_lemmas = (
            db.query(Lemma)
            .join(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.knowledge_state.in_(
                ["known", "learning", "lapsed", "acquiring"]
            ))
            .all()
        )
        all_lemmas = (
            db.query(Lemma)
            .join(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.knowledge_state.in_(
                ["known", "learning", "lapsed", "acquiring", "encountered"]
            ))
            .all()
        )
        known_words = [
            {"arabic": lem.lemma_ar, "english": lem.gloss_en or "", "lemma_id": lem.lemma_id, "pos": lem.pos or ""}
            for lem in active_lemmas
        ]

        from app.services.llm import generate_sentences_batch, AllProvidersFailed
        from app.services.sentence_generator import (
            get_content_word_counts,
            get_avoid_words,
            sample_known_words_weighted,
            KNOWN_SAMPLE_SIZE,
        )
        from app.services.sentence_validator import (
            build_comprehensive_lemma_lookup,
            build_lemma_lookup,
            map_tokens_to_lemmas,
            sanitize_arabic_word,
            strip_diacritics,
            tokenize_display,
            validate_sentence,
        )
        from app.services.word_selector import get_sentence_difficulty_params

        lemma_lookup = build_lemma_lookup(all_lemmas)
        mapping_lookup = build_comprehensive_lemma_lookup(db)
        # Defensive: clean target word in case DB has dirty data
        clean_target, san_warnings = sanitize_arabic_word(lemma.lemma_ar)
        if not clean_target or " " in clean_target or "too_short" in san_warnings:
            logger.warning(
                f"Skipping generation for uncleanable lemma {lemma_id}: {lemma.lemma_ar!r}"
            )
            return
        target_bare = strip_diacritics(clean_target)
        all_bare_forms = set(lemma_lookup.keys())

        content_word_counts = get_content_word_counts(db)
        sample = sample_known_words_weighted(
            known_words, content_word_counts, KNOWN_SAMPLE_SIZE, target_lemma_id=lemma_id
        )
        avoid_words = get_avoid_words(content_word_counts, known_words)

        # Dynamic difficulty based on word familiarity
        diff_params = get_sentence_difficulty_params(db, lemma_id)
        difficulty_hint = diff_params["difficulty_hint"]

        try:
            results = generate_sentences_batch(
                target_word=clean_target,
                target_translation=lemma.gloss_en or "",
                known_words=sample,
                count=needed + 2,
                difficulty_hint=difficulty_hint,
                avoid_words=avoid_words,
                max_words=diff_params["max_words"],
                model_override=model_override,
            )
        except AllProvidersFailed:
            logger.warning(f"LLM unavailable for sentence generation (lemma {lemma_id})")
            return

        stored = 0
        for res in results:
            if stored >= needed:
                break

            validation = validate_sentence(
                arabic_text=res.arabic,
                target_bare=target_bare,
                known_bare_forms=all_bare_forms,
            )
            if not validation.valid:
                continue

            sent = Sentence(
                arabic_text=res.arabic,
                arabic_diacritized=res.arabic,
                english_translation=res.english,
                transliteration=res.transliteration,
                source="llm",
                target_lemma_id=lemma.lemma_id,
                created_at=datetime.now(timezone.utc),
            )
            db.add(sent)
            db.flush()

            tokens = tokenize_display(res.arabic)
            mappings = map_tokens_to_lemmas(
                tokens=tokens,
                lemma_lookup=mapping_lookup,
                target_lemma_id=lemma.lemma_id,
                target_bare=target_bare,
            )
            unmapped = [m.surface_form for m in mappings if m.lemma_id is None]
            if unmapped:
                logger.warning(f"Skipping sentence with unmapped words: {unmapped}")
                db.delete(sent)
                continue

            # LLM verification of word-lemma mappings
            from app.config import settings as _settings
            if _settings.verify_mappings_llm:
                from app.services.sentence_validator import verify_word_mappings_llm
                lemma_map_for_verify = {l.lemma_id: l for l in db.query(Lemma).filter(
                    Lemma.lemma_id.in_([m.lemma_id for m in mappings if m.lemma_id])
                ).all()}
                wrong_positions = verify_word_mappings_llm(
                    res.arabic, res.english, mappings, lemma_map_for_verify,
                )
                if wrong_positions:
                    logger.warning(
                        f"LLM flagged mapping issues at positions {wrong_positions} "
                        f"in sentence for lemma {lemma_id}, discarding"
                    )
                    db.delete(sent)
                    continue

            for m in mappings:
                sw = SentenceWord(
                    sentence_id=sent.id,
                    position=m.position,
                    surface_form=m.surface_form,
                    lemma_id=m.lemma_id,
                    is_target_word=m.is_target,
                )
                db.add(sw)

            stored += 1

        db.commit()
        logger.info(f"Generated {stored} sentences for lemma {lemma_id}")

        # Audio generation disabled — existing backlog is sufficient.
        # Re-enable when ElevenLabs credits are plentiful.
        # _generate_audio_for_lemma(db, lemma_id)

    except Exception:
        logger.exception(f"Error generating material for lemma {lemma_id}")
    finally:
        db.close()


def store_multi_target_sentence(
    db,
    result,
    lemma_lookup: dict[str, int],
    target_bares: dict[str, int],
) -> Sentence | None:
    """Store a multi-target generated sentence with SentenceWord rows.

    Args:
        db: SQLAlchemy session.
        result: MultiTargetGeneratedSentence with arabic, english, etc.
        lemma_lookup: Bare form -> lemma_id lookup.
        target_bares: Dict of bare_form -> lemma_id for all target words.

    Returns:
        The stored Sentence object, or None if storage failed.
    """
    from app.services.sentence_validator import (
        map_tokens_to_lemmas,
        strip_diacritics,
        strip_punctuation,
        tokenize_display,
        normalize_alef,
        _strip_clitics,
    )

    sent = Sentence(
        arabic_text=result.arabic,
        arabic_diacritized=result.arabic,
        english_translation=result.english,
        transliteration=result.transliteration,
        source="llm",
        target_lemma_id=result.primary_target_lemma_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(sent)
    db.flush()

    # Build expanded target forms for matching
    target_normalized: dict[str, int] = {}
    for bare, lid in target_bares.items():
        norm = normalize_alef(bare)
        target_normalized[norm] = lid
        if not norm.startswith("ال"):
            target_normalized["ال" + norm] = lid
        if norm.startswith("ال") and len(norm) > 2:
            target_normalized[norm[2:]] = lid

    tokens = tokenize_display(result.arabic)
    # Use map_tokens_to_lemmas with primary target for base mapping
    primary_bare = None
    for bare, lid in target_bares.items():
        if lid == result.primary_target_lemma_id:
            primary_bare = bare
            break
    if not primary_bare:
        primary_bare = next(iter(target_bares.keys()), "")

    mappings = map_tokens_to_lemmas(
        tokens=tokens,
        lemma_lookup=lemma_lookup,
        target_lemma_id=result.primary_target_lemma_id,
        target_bare=primary_bare,
    )

    unmapped = [m.surface_form for m in mappings if m.lemma_id is None]
    if unmapped:
        logger.warning(f"Skipping multi-target sentence with unmapped words: {unmapped}")
        db.delete(sent)
        return None

    # LLM verification of word-lemma mappings
    from app.config import settings as _settings
    if _settings.verify_mappings_llm:
        from app.services.sentence_validator import verify_word_mappings_llm
        lemma_map_for_verify = {l.lemma_id: l for l in db.query(Lemma).filter(
            Lemma.lemma_id.in_([m.lemma_id for m in mappings if m.lemma_id])
        ).all()}
        wrong_positions = verify_word_mappings_llm(
            result.arabic, result.english, mappings, lemma_map_for_verify,
        )
        if wrong_positions:
            logger.warning(
                f"LLM flagged mapping issues at positions {wrong_positions} "
                f"in multi-target sentence, discarding"
            )
            db.delete(sent)
            return None

    for m in mappings:
        # Check if this token matches any of the other targets
        is_target = m.is_target
        if not is_target:
            bare = strip_punctuation(strip_diacritics(m.surface_form))
            bare_norm = normalize_alef(bare.replace("\u0640", ""))
            if bare_norm in target_normalized:
                is_target = True
            else:
                for stem in _strip_clitics(bare_norm):
                    if normalize_alef(stem) in target_normalized:
                        is_target = True
                        break

        sw = SentenceWord(
            sentence_id=sent.id,
            position=m.position,
            surface_form=m.surface_form,
            lemma_id=m.lemma_id,
            is_target_word=is_target,
        )
        db.add(sw)

    return sent


def generate_word_audio(lemma_id: int) -> None:
    """Background task: generate TTS audio for the word itself."""
    from app.services.tts import (
        DEFAULT_VOICE_ID,
        TTSError,
        TTSKeyMissing,
        cache_key_for,
        generate_and_cache,
        get_cached_path,
    )

    db = SessionLocal()
    try:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        if not lemma or lemma.audio_url:
            return

        key = cache_key_for(lemma.lemma_ar, DEFAULT_VOICE_ID)
        if get_cached_path(key):
            lemma.audio_url = f"/api/tts/audio/{key}.mp3"
            db.commit()
            return

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                generate_and_cache(lemma.lemma_ar, DEFAULT_VOICE_ID, cache_key=key)
            )
            lemma.audio_url = f"/api/tts/audio/{key}.mp3"
            db.commit()
        except (TTSError, TTSKeyMissing):
            logger.warning(f"TTS failed for word {lemma_id}")
        finally:
            loop.close()
    except Exception:
        logger.exception(f"Error generating word audio for lemma {lemma_id}")
    finally:
        db.close()


MIN_SENTENCES_PER_WORD = 2
PIPELINE_CAP = 300


def rotate_stale_sentences(db, min_shown: int = 1, min_active: int = 2) -> int:
    """Retire stale sentences where all scaffold words are fully known.

    Returns the number of sentences retired.
    """
    from scripts.rotate_stale_sentences import compute_diversity_score

    sentences = db.query(Sentence).filter(Sentence.is_active == True).all()  # noqa: E712
    all_sw = db.query(SentenceWord).all()
    all_ulk = db.query(UserLemmaKnowledge).all()

    knowledge_map = {k.lemma_id: k for k in all_ulk}
    sw_by_sentence: dict[int, list] = {}
    for sw in all_sw:
        sw_by_sentence.setdefault(sw.sentence_id, []).append(sw)

    active_per_target: dict[int | None, int] = {}
    for s in sentences:
        active_per_target[s.target_lemma_id] = active_per_target.get(s.target_lemma_id, 0) + 1

    stale: list[tuple] = []
    for sent in sentences:
        sws = sw_by_sentence.get(sent.id, [])
        if not sws:
            continue
        scores = compute_diversity_score(sws, knowledge_map)
        is_stale = (
            scores["acquiring_count"] == 0
            and scores["scaffold_count"] >= 2
            and (sent.times_shown or 0) >= min_shown
        )
        if is_stale:
            stale.append((sent, scores))

    stale.sort(key=lambda x: (x[1]["diversity_score"], -x[1]["scaffold_count"]))

    retire_per_target: dict[int | None, int] = {}
    retired = 0
    for sent, scores in stale:
        target_id = sent.target_lemma_id
        already_retiring = retire_per_target.get(target_id, 0)
        active = active_per_target.get(target_id, 0)
        if active - already_retiring > min_active:
            sent.is_active = False
            retire_per_target[target_id] = already_retiring + 1
            retired += 1

    if retired:
        db.commit()
        logger.info(f"Rotated {retired} stale sentences")
        from app.services.activity_log import log_activity
        log_activity(
            db,
            event_type="sentences_retired",
            summary=f"Rotated {retired} stale sentences (background warm cache)",
            detail={"retired": retired, "total_active": len(sentences)},
        )

    return retired


def warm_sentence_cache(llm_model: str = "gemini") -> dict:
    """Background task: pre-generate sentences for words likely in the next session.

    Uses multi-target generation to efficiently cover multiple words per sentence.
    Identifies focus cohort words + likely auto-introductions that have fewer
    than MIN_SENTENCES_PER_WORD active sentences, then generates for them.
    Rotates stale sentences first to stay within the pipeline cap.
    Opens its own DB session. Returns stats dict for logging.

    Args:
        llm_model: Model override for sentence generation. Use "claude_sonnet"
                   for free generation via Claude CLI, "gemini" for fast API calls.
    """
    from app.services.cohort_service import get_focus_cohort
    from app.services.word_selector import select_next_words
    from app.services.topic_service import ensure_active_topic
    from app.services.sentence_generator import (
        group_words_for_multi_target,
        generate_validated_sentences_multi_target,
    )
    from app.services.sentence_validator import (
        build_lemma_lookup,
        build_comprehensive_lemma_lookup,
        strip_diacritics,
    )
    from sqlalchemy import func
    from sqlalchemy.orm import joinedload

    db = SessionLocal()
    stats = {"cohort_gaps": 0, "intro_gaps": 0, "generated": 0, "multi_target": 0, "rotated": 0}
    try:
        # Check pipeline cap — rotate stale sentences first to make room
        total_active = (
            db.query(func.count(Sentence.id))
            .filter(Sentence.is_active == True)
            .scalar() or 0
        )
        if total_active >= PIPELINE_CAP:
            rotated = rotate_stale_sentences(db)
            stats["rotated"] = rotated
            total_active -= rotated

        if total_active >= PIPELINE_CAP + 10:
            logger.info(f"Warm cache: still over cap after rotation ({total_active} >= {PIPELINE_CAP + 10}), skipping")
            return stats

        # Collect all words needing sentences
        gap_word_ids: list[int] = []

        # 1. Focus cohort words with < 2 active sentences
        cohort = get_focus_cohort(db)
        if cohort:
            sentence_counts = dict(
                db.query(Sentence.target_lemma_id, func.count(Sentence.id))
                .filter(
                    Sentence.target_lemma_id.in_(cohort),
                    Sentence.is_active == True,
                )
                .group_by(Sentence.target_lemma_id)
                .all()
            )
            gaps = [lid for lid in cohort if sentence_counts.get(lid, 0) < MIN_SENTENCES_PER_WORD]
            stats["cohort_gaps"] = len(gaps)
            gap_word_ids.extend(gaps[:10])

        # 2. Likely auto-introduction candidates
        active_topic = ensure_active_topic(db)
        candidates = select_next_words(db, count=5, domain=active_topic)
        for cand in candidates:
            lid = cand["lemma_id"]
            if lid in gap_word_ids:
                continue
            count = (
                db.query(func.count(Sentence.id))
                .filter(Sentence.target_lemma_id == lid, Sentence.is_active == True)
                .scalar() or 0
            )
            if count < MIN_SENTENCES_PER_WORD:
                gap_word_ids.append(lid)
                stats["intro_gaps"] = stats.get("intro_gaps", 0) + 1

        if not gap_word_ids:
            logger.info(f"Warm cache: no gaps found")
            return stats

        # Load lemmas for gap words
        gap_lemmas = (
            db.query(Lemma).options(joinedload(Lemma.root))
            .filter(Lemma.lemma_id.in_(gap_word_ids))
            .all()
        )
        lemma_by_id = {l.lemma_id: l for l in gap_lemmas}

        # Build word dicts for multi-target grouping
        word_dicts = []
        for lid in gap_word_ids:
            lem = lemma_by_id.get(lid)
            if lem:
                word_dicts.append({
                    "lemma_id": lid,
                    "lemma_ar": lem.lemma_ar,
                    "gloss_en": lem.gloss_en or "",
                    "root_id": lem.root_id,
                })

        # Build known words for LLM prompt
        active_lemmas = (
            db.query(Lemma)
            .join(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.knowledge_state.in_(
                ["acquiring", "learning", "known", "lapsed"]
            ))
            .all()
        )
        known_words = [
            {"arabic": l.lemma_ar, "english": l.gloss_en or "", "lemma_id": l.lemma_id, "pos": l.pos or ""}
            for l in active_lemmas
        ]
        lemma_lookup = build_lemma_lookup(active_lemmas)
        mapping_lookup = build_comprehensive_lemma_lookup(db)

        # Multi-target generation
        groups = group_words_for_multi_target(word_dicts)
        for group in groups:
            try:
                results = generate_validated_sentences_multi_target(
                    target_words=group,
                    known_words=known_words,
                    count=len(group),
                    difficulty_hint="beginner",
                    max_words=12,
                    lemma_lookup=lemma_lookup,
                    model_override=llm_model,
                )
                target_bares = {strip_diacritics(tw["lemma_ar"]): tw["lemma_id"] for tw in group}
                for mres in results:
                    sent = store_multi_target_sentence(db, mres, mapping_lookup, target_bares)
                    if sent:
                        stats["generated"] += 1
                        stats["multi_target"] += 1
                db.commit()
            except Exception:
                logger.warning(f"Warm cache: multi-target failed for group")
                db.rollback()

        # Single-target fallback for any remaining ungrouped words
        covered = set()
        for group in groups:
            for w in group:
                covered.add(w["lemma_id"])
        remaining = [lid for lid in gap_word_ids if lid not in covered]

        for lid in remaining:
            try:
                generate_material_for_word(lid, needed=MIN_SENTENCES_PER_WORD, model_override=llm_model)
                stats["generated"] += 1
            except Exception:
                logger.warning(f"Warm cache: failed for word {lid}")

        logger.info(f"Warm cache complete: {stats}")
        return stats
    except Exception:
        logger.exception("Error in warm_sentence_cache")
        return stats
    finally:
        db.close()


def _generate_audio_for_lemma(db, lemma_id: int) -> None:
    """Generate TTS audio for sentences of a word."""
    from app.services.tts import (
        DEFAULT_VOICE_ID,
        TTSError,
        TTSKeyMissing,
        cache_key_for,
        generate_and_cache,
        get_cached_path,
    )

    sentences = (
        db.query(Sentence)
        .filter(
            Sentence.target_lemma_id == lemma_id,
            Sentence.audio_url.is_(None),
        )
        .all()
    )

    if not sentences:
        return

    loop = asyncio.new_event_loop()
    try:
        for sent in sentences:
            key = cache_key_for(sent.arabic_text, DEFAULT_VOICE_ID)
            if get_cached_path(key):
                sent.audio_url = f"/api/tts/audio/{key}.mp3"
                continue
            try:
                loop.run_until_complete(
                    generate_and_cache(sent.arabic_text, DEFAULT_VOICE_ID, cache_key=key)
                )
                sent.audio_url = f"/api/tts/audio/{key}.mp3"
            except (TTSError, TTSKeyMissing):
                logger.warning(f"TTS failed for sentence {sent.id}")
                continue

        db.commit()
    finally:
        loop.close()
