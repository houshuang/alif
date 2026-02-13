"""Shared material generation: sentences + audio for words.

Used by both learn.py (word introduction) and ocr_service.py (post-import).
"""

import asyncio
import logging
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge

logger = logging.getLogger(__name__)


def generate_material_for_word(lemma_id: int, needed: int = 2) -> None:
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
            build_lemma_lookup,
            map_tokens_to_lemmas,
            sanitize_arabic_word,
            strip_diacritics,
            tokenize,
            validate_sentence,
        )
        from app.services.word_selector import get_sentence_difficulty_params

        lemma_lookup = build_lemma_lookup(all_lemmas)
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

            tokens = tokenize(res.arabic)
            mappings = map_tokens_to_lemmas(
                tokens=tokens,
                lemma_lookup=lemma_lookup,
                target_lemma_id=lemma.lemma_id,
                target_bare=target_bare,
            )
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
        tokenize,
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

    tokens = tokenize(result.arabic)
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

    for m in mappings:
        # Check if this token matches any of the other targets
        is_target = m.is_target
        if not is_target:
            bare = strip_diacritics(m.surface_form)
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
