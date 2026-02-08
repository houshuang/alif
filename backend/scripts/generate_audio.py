"""Batch generate TTS audio for sentences in the database.

Usage:
    python -m scripts.generate_audio                          # all sentences without audio
    python -m scripts.generate_audio --limit 10               # first 10 only
    python -m scripts.generate_audio --concurrency 3          # 3 parallel requests
    python -m scripts.generate_audio --voice-id VOICE_ID      # custom voice

Requires ELEVENLABS_API_KEY in environment or .env file.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from app.models import Sentence
from app.services.tts import (
    DEFAULT_VOICE_ID,
    TTSError,
    TTSKeyMissing,
    cache_key_for,
    generate_and_cache,
    get_cached_path,
    update_index,
)


async def generate_one(
    sentence_id: int,
    text: str,
    voice_id: str,
    index: int,
    total: int,
) -> dict:
    """Generate audio for a single sentence. Returns result dict."""
    key = cache_key_for(text, voice_id)

    if get_cached_path(key):
        return {"id": sentence_id, "status": "skipped", "cache_key": key}

    try:
        path = await generate_and_cache(text, voice_id, cache_key=key)
        update_index(sentence_id, path.name)
        return {"id": sentence_id, "status": "generated", "cache_key": key, "filename": path.name}
    except TTSError as e:
        return {"id": sentence_id, "status": "error", "error": str(e)}


async def generate_batch(
    sentences: list[dict],
    voice_id: str,
    concurrency: int = 1,
    delay: float = 0.5,
) -> dict:
    stats = {"generated": 0, "skipped": 0, "errors": 0, "total": len(sentences)}
    semaphore = asyncio.Semaphore(concurrency)
    results = []

    async def throttled(sent, idx):
        async with semaphore:
            result = await generate_one(
                sentence_id=sent["id"],
                text=sent["text"],
                voice_id=voice_id,
                index=idx,
                total=stats["total"],
            )
            if delay > 0 and result["status"] == "generated":
                await asyncio.sleep(delay)
            return result

    # Process in chunks to show progress and handle rate limits
    chunk_size = max(concurrency * 2, 5)
    for chunk_start in range(0, len(sentences), chunk_size):
        chunk = sentences[chunk_start:chunk_start + chunk_size]
        tasks = [throttled(sent, chunk_start + i) for i, sent in enumerate(chunk)]
        chunk_results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(chunk_results):
            idx = chunk_start + i
            sent = sentences[idx]

            if isinstance(result, Exception):
                stats["errors"] += 1
                print(f"  [{idx+1}/{stats['total']}] Exception: {result}")
                if "rate limit" in str(result).lower():
                    print("  Rate limited, waiting 60s...")
                    await asyncio.sleep(60)
                continue

            results.append(result)
            if result["status"] == "generated":
                stats["generated"] += 1
                print(f"  [{idx+1}/{stats['total']}] Generated: {sent['text'][:50]}...")
            elif result["status"] == "skipped":
                stats["skipped"] += 1
            elif result["status"] == "error":
                stats["errors"] += 1
                print(f"  [{idx+1}/{stats['total']}] Error: {result['error']}")
                if "rate limit" in result.get("error", "").lower():
                    print("  Rate limited, waiting 60s...")
                    await asyncio.sleep(60)

    return {"stats": stats, "results": results}


async def main():
    parser = argparse.ArgumentParser(description="Batch generate TTS audio")
    parser.add_argument("--voice-id", default=DEFAULT_VOICE_ID)
    parser.add_argument("--limit", type=int, default=0, help="Max sentences to process (0=all)")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between API calls")
    parser.add_argument("--concurrency", type=int, default=1, help="Parallel requests")
    args = parser.parse_args()

    try:
        from app.services.tts import _get_api_key
        _get_api_key()
    except TTSKeyMissing:
        print("Error: ELEVENLABS_API_KEY not set. Add it to .env or environment.")
        sys.exit(1)

    db = SessionLocal()
    try:
        query = db.query(Sentence).filter(
            Sentence.arabic_text.isnot(None),
            Sentence.arabic_text != "",
        )

        if args.limit > 0:
            query = query.limit(args.limit)

        all_sentences = query.all()

        # Filter to those needing audio (no audio_url or no cached file)
        sentences = []
        for s in all_sentences:
            key = cache_key_for(s.arabic_text, args.voice_id)
            if s.audio_url and get_cached_path(key):
                continue
            sentences.append({"id": s.id, "text": s.arabic_text})

        if not sentences:
            print("All sentences already have audio. Nothing to do.")
            return

        print(f"Generating audio for {len(sentences)} sentences (of {len(all_sentences)} total)")
        print(f"Voice: {args.voice_id} | Concurrency: {args.concurrency} | Delay: {args.delay}s")
        print("-" * 60)

        start_time = time.time()
        result = await generate_batch(
            sentences=sentences,
            voice_id=args.voice_id,
            concurrency=args.concurrency,
            delay=args.delay,
        )

        # Update audio_url in DB for generated + already-cached sentences
        updated = 0
        for r in result["results"]:
            if r["status"] in ("generated", "skipped"):
                sent = db.query(Sentence).get(r["id"])
                if sent and not sent.audio_url:
                    sent.audio_url = f"/api/tts/audio/{r['cache_key']}.mp3"
                    updated += 1

        db.commit()

        elapsed = time.time() - start_time
        s = result["stats"]
        print("-" * 60)
        print(f"Done in {elapsed:.1f}s")
        print(f"  Generated: {s['generated']}")
        print(f"  Skipped (cached): {s['skipped']}")
        print(f"  Errors: {s['errors']}")
        print(f"  DB audio_url updated: {updated}")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
