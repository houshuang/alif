#!/usr/bin/env python3
"""Generate a podcast format sampler episode.

Queries the learner's word knowledge, selects sentences for each format,
generates TTS audio, and stitches into a single MP3.

Usage:
    python3 scripts/generate_podcast_sampler.py [--output NAME]

Runs inside Docker:
    docker exec -w /app alif-backend-1 python3 scripts/generate_podcast_sampler.py
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from app.database import SessionLocal
from app.services.podcast_service import plan_sampler, stitch_podcast

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser(description="Generate podcast sampler")
    parser.add_argument("--output", default=None, help="Output filename (without .mp3)")
    args = parser.parse_args()

    output_name = args.output or f"sampler-{datetime.now().strftime('%Y%m%d-%H%M')}"

    db = SessionLocal()
    try:
        segments = plan_sampler(db)
        if not segments:
            logger.error("No segments planned — not enough data?")
            sys.exit(1)

        logger.info("Generating podcast: %s (%d segments)", output_name, len(segments))
        path = await stitch_podcast(segments, output_name)
        logger.info("Done! Podcast saved to: %s", path)
        print(f"\nPodcast ready: {path}")
        print(f"API URL: /api/podcasts/audio/{path.name}")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
