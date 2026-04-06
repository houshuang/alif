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
from app.models import Sentence
from app.services.word_selector import select_next_words
from app.services.material_generator import generate_material_for_word
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
    parser.add_argument("--model", default="claude_sonnet", help="LLM model (default: gemini)")
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
                    stored = generate_material_for_word(
                        lid, needed=needed, model_override=args.model,
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
