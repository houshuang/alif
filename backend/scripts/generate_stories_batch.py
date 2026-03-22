#!/usr/bin/env python3
"""Generate multiple stories with varied formats for diversity testing.

Usage:
    python scripts/generate_stories_batch.py --count 5 --vary
    python scripts/generate_stories_batch.py --count 3 --format long
    python scripts/generate_stories_batch.py --count 5 --vary --audio
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


FORMAT_ROTATION = ["standard", "standard", "long", "breakdown", "arabic_explanation"]
LENGTH_ROTATION = ["medium", "short", "long", "medium", "medium"]


def main():
    parser = argparse.ArgumentParser(description="Batch-generate stories")
    parser.add_argument("--count", type=int, default=5, help="Number of stories to generate")
    parser.add_argument("--vary", action="store_true", help="Rotate formats for diversity")
    parser.add_argument("--format", default=None, help="Force a specific format (standard/long/breakdown/arabic_explanation)")
    parser.add_argument("--audio", action="store_true", help="Also generate audio for each story")
    parser.add_argument("--topic", default=None, help="Optional topic for all stories")
    args = parser.parse_args()

    from app.database import SessionLocal
    from app.services.story_service import generate_story, generate_story_audio

    db = SessionLocal()
    try:
        print(f"Generating {args.count} stories...")
        for i in range(args.count):
            if args.format:
                fmt = args.format
            elif args.vary:
                fmt = FORMAT_ROTATION[i % len(FORMAT_ROTATION)]
            else:
                fmt = "standard"

            length = LENGTH_ROTATION[i % len(LENGTH_ROTATION)]
            print(f"\n[{i+1}/{args.count}] format={fmt}, length={length}")

            start = time.time()
            try:
                story, new_ids = generate_story(
                    db,
                    difficulty="beginner",
                    length=length,
                    format_type=fmt,
                    topic=args.topic,
                )
                elapsed = time.time() - start
                print(f"  Generated: '{story.title_en}' ({story.total_words} words, {elapsed:.1f}s)")

                if args.audio:
                    print(f"  Generating audio...")
                    audio_start = time.time()
                    result = asyncio.run(generate_story_audio(db, story.id))
                    audio_elapsed = time.time() - audio_start
                    print(f"  Audio: {result.get('audio_filename')} ({result.get('duration_s', 0):.1f}s, generated in {audio_elapsed:.1f}s)")

            except Exception as e:
                elapsed = time.time() - start
                print(f"  FAILED ({elapsed:.1f}s): {e}")

        print(f"\nDone! Generated {args.count} stories.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
