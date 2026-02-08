"""Batch generate TTS audio for sentences in the database.

Usage:
    python -m scripts.generate_audio [--voice-id VOICE_ID] [--limit N]

Requires ELEVENLABS_API_KEY in environment or .env file.
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add backend to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.services.tts import (
    TTSError,
    TTSKeyMissing,
    cache_key_for,
    generate_and_cache,
    get_cached_path,
    update_index,
)

DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"


async def generate_batch(
    sentences: list[dict],
    voice_id: str,
    delay: float = 1.0,
) -> dict:
    stats = {"generated": 0, "skipped": 0, "errors": 0, "total": len(sentences)}

    for i, sentence in enumerate(sentences):
        text = sentence["text"]
        sid = sentence["id"]
        key = cache_key_for(text, voice_id)

        if get_cached_path(key):
            stats["skipped"] += 1
            print(f"  [{i+1}/{stats['total']}] Skipped (cached): {text[:40]}...")
            continue

        try:
            path = await generate_and_cache(text, voice_id, cache_key=key)
            update_index(sid, path.name)
            stats["generated"] += 1
            print(f"  [{i+1}/{stats['total']}] Generated: {text[:40]}...")
        except TTSError as e:
            stats["errors"] += 1
            print(f"  [{i+1}/{stats['total']}] Error: {e}")
            if "rate limit" in str(e).lower():
                print("  Rate limited, waiting 60s...")
                await asyncio.sleep(60)

        await asyncio.sleep(delay)

    return stats


async def main():
    parser = argparse.ArgumentParser(description="Batch generate TTS audio")
    parser.add_argument("--voice-id", default=DEFAULT_VOICE_ID)
    parser.add_argument("--limit", type=int, default=0, help="Max sentences to process (0=all)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between API calls")
    args = parser.parse_args()

    try:
        from app.services.tts import _get_api_key
        _get_api_key()
    except TTSKeyMissing:
        print("Error: ELEVENLABS_API_KEY not set. Add it to .env or environment.")
        sys.exit(1)

    # TODO: Once the data model (task #1) is complete, load sentences from DB
    # For now, print instructions
    print("Batch audio generation ready.")
    print(f"Voice ID: {args.voice_id}")
    print()
    print("Once the data model is built, this script will load sentences from the DB.")
    print("For now, you can use the API endpoint POST /api/tts/generate directly.")


if __name__ == "__main__":
    asyncio.run(main())
