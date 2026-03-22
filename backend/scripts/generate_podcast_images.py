#!/usr/bin/env python3
"""Generate cover art for podcast episodes using Nano Banana 2 (Gemini Image Gen).

Reads podcast metadata, generates evocative cover images, saves as PNG.

Usage:
    docker exec -w /app -e PYTHONPATH=/app alif-backend-1 \
        python3 scripts/generate_podcast_images.py
"""

import base64
import json
import logging
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

PODCAST_DIR = Path(__file__).resolve().parent.parent / "data" / "podcasts"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-image-preview:generateContent"

# Consistent style directive — pencil + watercolor children's book illustration
ART_STYLE = (
    "Children's book illustration. Hand-drawn pencil outlines on off-white textured watercolor "
    "paper, with loose transparent watercolor washes in a limited palette of warm ochre, dusty "
    "blue, soft coral, and charcoal grey. Visible pencil sketch lines left in, slightly wobbly "
    "and imperfect. Large areas of white and cream negative space. Simple composition with one "
    "focal subject. No gradients, no photorealistic elements, no lens blur, no HDR lighting. "
    "Matte finish, flat natural lighting. No text, no words, no letters, no watermarks. "
    "Square format, centered composition, reads clearly at small sizes. "
    "The look of a real physical illustration photographed from a sketchbook."
)

# Image prompts for each theme — keep subjects simple, let the style do the work
THEME_PROMPTS = {
    "magical-library": (
        "A small child standing in the doorway of a tiny old bookshop. One book on a shelf "
        "is open, with a few delicate flowers drifting out of its pages. A single blue lantern "
        "hangs from the ceiling. Warm, quiet, full of wonder. " + ART_STYLE
    ),
    "clever-cat": (
        "A proud orange cat walking with a fish in its mouth, tail held high. Behind it, "
        "a man in an apron throws his hands up in surprise. A few simple market stall shapes "
        "in the background. Playful and humorous. " + ART_STYLE
    ),
    "lost-letter": (
        "A small blue notebook lying open on old wooden floorboards, with a single dried "
        "flower pressed between its pages. Warm lamplight from the side. Simple, nostalgic, "
        "bittersweet. Mostly empty space around the notebook. " + ART_STYLE
    ),
    "night-sky": (
        "A small girl sitting on a grassy hill at night, looking up at a large crescent moon. "
        "A thin line of light connects the moon to the open notebook in her lap. A few stars. "
        "Deep quiet blue sky. Poetic and gentle. " + ART_STYLE
    ),
    "two-trees": (
        "Two flowers side by side on a simple path — one very tall and stiff, one small and "
        "bending gently in the wind. A few leaves blowing. Simple sky with one cloud. "
        "Fable-like, quiet. " + ART_STYLE
    ),
    "sampler": (
        "A simple brass microphone on a small wooden table, with delicate curved lines "
        "suggesting sound waves floating upward like smoke. A few tiny stars around it. "
        "Centered on cream paper. Quiet and inviting. " + ART_STYLE
    ),
}


def generate_image(prompt: str, api_key: str) -> bytes | None:
    """Generate an image using Gemini's native image generation."""
    logger.info("Generating image... (prompt: %s...)", prompt[:60])

    resp = httpx.post(
        GEMINI_API_URL,
        params={"key": api_key},
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {
                    "aspectRatio": "1:1",
                },
            },
        },
        timeout=120.0,
    )

    if resp.status_code != 200:
        logger.error("Gemini API error %d: %s", resp.status_code, resp.text[:300])
        return None

    try:
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            logger.error("No candidates in response")
            return None

        parts = candidates[0].get("content", {}).get("parts", [])
        for part in parts:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and "data" in inline:
                return base64.b64decode(inline["data"])

        logger.error("No image data in response parts: %s", [list(p.keys()) for p in parts])
        return None
    except Exception as e:
        logger.error("Failed to parse response: %s", e)
        return None


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Regenerate existing images")
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_KEY")
    if not api_key:
        logger.error("GEMINI_KEY not set")
        sys.exit(1)

    PODCAST_DIR.mkdir(parents=True, exist_ok=True)

    # Find all podcast metadata files
    meta_files = sorted(PODCAST_DIR.glob("*.json"))
    if not meta_files:
        logger.error("No podcast metadata files found in %s", PODCAST_DIR)
        sys.exit(1)

    for meta_path in meta_files:
        stem = meta_path.stem
        image_path = PODCAST_DIR / f"{stem}.png"

        # Skip if image already exists (unless --force)
        if image_path.exists() and not args.force:
            logger.info("SKIP %s — image already exists (use --force to regenerate)", stem)
            continue

        # Load metadata
        try:
            meta = json.loads(meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("SKIP %s — invalid metadata", stem)
            continue

        # Determine prompt
        theme_id = meta.get("theme_id", "")
        if theme_id in THEME_PROMPTS:
            prompt = THEME_PROMPTS[theme_id]
        elif "sampler" in stem:
            prompt = THEME_PROMPTS["sampler"]
        else:
            # Generate a prompt from the summary
            summary = meta.get("summary", "An Arabic language learning podcast episode")
            prompt = f"Illustration for a story: {summary}. {ART_STYLE}"

        # Generate image
        image_bytes = generate_image(prompt, api_key)
        if not image_bytes:
            logger.error("FAIL %s — could not generate image", stem)
            continue

        # Save image
        image_path.write_bytes(image_bytes)
        logger.info("OK %s — %s (%.1f KB)", stem, meta.get("title_en", "?"), len(image_bytes) / 1024)

        # Update metadata with image filename
        meta["image_filename"] = image_path.name
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    logger.info("Done!")


if __name__ == "__main__":
    main()
