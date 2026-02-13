#!/usr/bin/env python3
"""Pre-generate sentences + audio for top word candidates.

Queries the word selector for the top N next candidates and ensures
each has at least 3 sentences with audio. Run periodically so material
is ready when the user introduces words.

Usage:
    python scripts/pregenerate_material.py                  # top 20 candidates
    python scripts/pregenerate_material.py --count 50       # top 50
    python scripts/pregenerate_material.py --sentences 5    # 5 sentences each
    python scripts/pregenerate_material.py --dry-run
    python scripts/pregenerate_material.py --skip-audio     # sentences only
"""

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import func

from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.word_selector import select_next_words
from app.services.llm import AllProvidersFailed, generate_sentences_batch
from app.services.sentence_generator import (
    get_content_word_counts,
    get_avoid_words,
    sample_known_words_weighted,
    KNOWN_SAMPLE_SIZE,
)
from app.services.sentence_validator import (
    build_lemma_lookup,
    map_tokens_to_lemmas,
    strip_diacritics,
    tokenize,
    validate_sentence,
)
from app.services.tts import (
    DEFAULT_VOICE_ID,
    TTSError,
    TTSKeyMissing,
    cache_key_for,
    generate_and_cache,
    get_cached_path,
)


def get_existing_counts(db) -> dict[int, int]:
    rows = (
        db.query(Sentence.target_lemma_id, func.count(Sentence.id))
        .filter(
            Sentence.target_lemma_id.isnot(None),
            Sentence.is_active == True,  # noqa: E712
        )
        .group_by(Sentence.target_lemma_id)
        .all()
    )
    return {lid: cnt for lid, cnt in rows}


def generate_sentences_for_word(
    db,
    lemma: Lemma,
    known_words: list[dict[str, str]],
    lemma_lookup: dict[str, int],
    needed: int,
    model: str = "openai",
    delay: float = 1.0,
    avoid_words: list[str] | None = None,
) -> int:
    target_bare = strip_diacritics(lemma.lemma_ar)
    all_bare = set(lemma_lookup.keys())
    stored = 0

    for batch in range(3):
        if stored >= needed:
            break
        if batch > 0 and delay > 0:
            time.sleep(delay)

        try:
            results = generate_sentences_batch(
                target_word=lemma.lemma_ar,
                target_translation=lemma.gloss_en or "",
                known_words=known_words,
                count=min(needed - stored + 1, 3),
                difficulty_hint="beginner",
                model_override=model,
                avoid_words=avoid_words,
            )
        except AllProvidersFailed as e:
            print(f"    LLM error: {e}")
            break

        for res in results:
            if stored >= needed:
                break

            validation = validate_sentence(
                arabic_text=res.arabic,
                target_bare=target_bare,
                known_bare_forms=all_bare,
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
    return stored


async def generate_audio_for_sentences(db, lemma_id: int) -> int:
    sentences = (
        db.query(Sentence)
        .filter(
            Sentence.target_lemma_id == lemma_id,
            Sentence.audio_url.is_(None),
        )
        .all()
    )

    generated = 0
    for sent in sentences:
        key = cache_key_for(sent.arabic_text, DEFAULT_VOICE_ID)
        if get_cached_path(key):
            sent.audio_url = f"/api/tts/audio/{key}.mp3"
            generated += 1
            continue
        try:
            path = await generate_and_cache(sent.arabic_text, DEFAULT_VOICE_ID, cache_key=key)
            sent.audio_url = f"/api/tts/audio/{key}.mp3"
            generated += 1
            await asyncio.sleep(0.5)
        except (TTSError, TTSKeyMissing) as e:
            print(f"    TTS error: {e}")
            continue

    db.commit()
    return generated


async def main():
    parser = argparse.ArgumentParser(description="Pre-generate material for top candidates")
    parser.add_argument("--count", type=int, default=20, help="Number of candidates (default: 20)")
    parser.add_argument("--sentences", type=int, default=3, help="Sentences per word (default: 3)")
    parser.add_argument("--model", default="openai", help="LLM model (default: openai)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between LLM calls")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-audio", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Get top candidates from the word selector
        candidates = select_next_words(db, count=args.count)
        if not candidates:
            print("No candidates available. Import more words first.")
            return

        print(f"Found {len(candidates)} candidates for pre-generation")

        # Build known words and lemma lookup
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
        lemma_lookup = build_lemma_lookup(all_lemmas)

        # Diversity: compute content word counts and avoid list
        content_word_counts = get_content_word_counts(db)
        avoid_words = get_avoid_words(content_word_counts, known_words)
        if avoid_words:
            print(f"Avoiding overused words: {', '.join(avoid_words)}")

        existing_counts = get_existing_counts(db)

        total_sentences = 0
        total_audio = 0
        start_time = time.time()

        for i, cand in enumerate(candidates):
            lid = cand["lemma_id"]
            existing = existing_counts.get(lid, 0)
            needed = args.sentences - existing

            print(f"[{i+1}/{len(candidates)}] {cand['lemma_ar']} ({cand['gloss_en']}) — "
                  f"have {existing}, need {max(needed, 0)}")

            if needed > 0:
                if args.dry_run:
                    print(f"    [dry-run] Would generate {needed} sentences")
                    total_sentences += needed
                else:
                    lemma = db.query(Lemma).filter(Lemma.lemma_id == lid).first()
                    if lemma:
                        word_sample = sample_known_words_weighted(
                            known_words, content_word_counts, KNOWN_SAMPLE_SIZE,
                            target_lemma_id=lemma.lemma_id,
                        )
                        stored = generate_sentences_for_word(
                            db, lemma, word_sample, lemma_lookup,
                            needed=needed, model=args.model, delay=args.delay,
                            avoid_words=avoid_words,
                        )
                        total_sentences += stored
                        print(f"    Generated {stored} sentences")

            if not args.skip_audio and not args.dry_run:
                audio_count = await generate_audio_for_sentences(db, lid)
                total_audio += audio_count
                if audio_count:
                    print(f"    Generated {audio_count} audio files")

        elapsed = time.time() - start_time
        print("-" * 60)
        print(f"Done in {elapsed:.1f}s")
        print(f"  Sentences generated: {total_sentences}")
        print(f"  Audio files generated: {total_audio}")

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
