"""Shared material generation: sentences + audio for words.

Used by both learn.py (word introduction) and ocr_service.py (post-import).
"""

import asyncio
import logging

from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge

logger = logging.getLogger(__name__)


def generate_material_for_word(lemma_id: int, needed: int) -> None:
    """Background task: generate sentences + audio for a word.

    Opens its own DB session so it can run in a background thread.
    """
    db = SessionLocal()
    try:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        if not lemma:
            return

        all_lemmas = (
            db.query(Lemma)
            .join(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.fsrs_card_json.isnot(None))
            .all()
        )
        known_words = [
            {"arabic": lem.lemma_ar, "english": lem.gloss_en or "", "lemma_id": lem.lemma_id}
            for lem in all_lemmas
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

        try:
            results = generate_sentences_batch(
                target_word=clean_target,
                target_translation=lemma.gloss_en or "",
                known_words=sample,
                count=needed + 1,
                difficulty_hint="beginner",
                avoid_words=avoid_words,
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
