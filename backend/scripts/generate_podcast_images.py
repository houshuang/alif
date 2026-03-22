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

# Art style directive shared across all prompts
ART_STYLE = (
    "Digital painting in a warm, dreamlike Middle Eastern storybook illustration style. "
    "Rich jewel tones (deep blue, amber, emerald), soft golden lighting, slightly stylized. "
    "No text, no words, no letters, no watermarks. Square composition."
)

# Image prompts for each theme
THEME_PROMPTS = {
    "magical-library": (
        "An ancient Damascus bookshop at twilight. A single blue lantern glows on a wooden shelf "
        "full of old leather-bound books. One book lies open, and from its pages spill delicate "
        "luminous flowers floating upward. A small child's silhouette stands in the doorway, "
        "bathed in warm light. " + ART_STYLE
    ),
    "clever-cat": (
        "A mischievous orange tabby cat trotting proudly through a busy Middle Eastern souk market, "
        "carrying a large silver fish in its mouth. Market stalls with colorful fabrics and spices "
        "in the background. A bewildered shopkeeper watches from behind his stall. Humorous, warm, "
        "charming atmosphere. " + ART_STYLE
    ),
    "lost-letter": (
        "An old blue notebook lying open on a wooden floor, with dried flowers pressed between "
        "its yellowed pages. Warm lamplight illuminates handwritten Arabic calligraphy. "
        "In the background, a window shows a twilight cityscape. Nostalgic, bittersweet mood. "
        + ART_STYLE
    ),
    "night-sky": (
        "A young girl sitting alone on a hillside of blue-green grass at night, her notebook "
        "open on her lap. Above her, an enormous luminous crescent moon glows with golden light, "
        "with a thin thread of light streaming down toward the notebook. Stars scattered across "
        "a deep indigo sky. Magical, serene, poetic. " + ART_STYLE
    ),
    "two-trees": (
        "Two flowers on a country road — one towering and proud with elaborate petals, the other "
        "small and humble but bending gracefully in the wind. A dramatic sky with swirling clouds. "
        "The small flower seems to dance while the large one strains. Fable-like, philosophical mood. "
        + ART_STYLE
    ),
    "sampler": (
        "An ornate brass microphone with Arabic geometric patterns etched into it, sitting on "
        "a mosaic table. Sound waves emanate as delicate golden arabesques. Background is deep "
        "midnight blue with scattered stars. " + ART_STYLE
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

        # Skip if image already exists
        if image_path.exists():
            logger.info("SKIP %s — image already exists", stem)
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
