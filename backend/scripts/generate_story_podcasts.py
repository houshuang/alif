#!/usr/bin/env python3
"""Generate story-breakdown podcast episodes with LLM-crafted stories.

Queries the learner's highest-stability words, generates beautiful short stories
via Claude, then builds story-breakdown podcast episodes.

Usage:
    python3 scripts/generate_story_podcasts.py [--count N] [--theme THEME]

Inside Docker:
    docker exec -w /app -e PYTHONPATH=/app alif-backend-1 \
        python3 scripts/generate_story_podcasts.py
"""

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from app.database import SessionLocal
from app.models import Lemma, Root, UserLemmaKnowledge
from app.services.podcast_service import (
    Seg,
    ar,
    ar_normal,
    ar_slow,
    en,
    save_metadata,
    silence,
    stitch_podcast,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Story themes ──────────────────────────────────────────────────────

STORY_THEMES = [
    {
        "id": "magical-library",
        "title": "The Magical Library",
        "prompt_hint": (
            "A child discovers a small old library in Damascus where the books whisper "
            "at night. One book opens by itself and shows a beautiful garden inside. "
            "The child enters the book and finds something wonderful and unexpected. "
            "Gentle, dreamlike, with a moment of wonder."
        ),
    },
    {
        "id": "clever-cat",
        "title": "The Clever Cat",
        "prompt_hint": (
            "A cat in a busy market keeps stealing fish. The shopkeeper tries everything "
            "to stop it. Each attempt fails in a funnier way. In the end, a surprising "
            "twist — the cat was bringing the fish somewhere unexpected. Warm and funny."
        ),
    },
    {
        "id": "lost-letter",
        "title": "The Lost Letter",
        "prompt_hint": (
            "A man finds an old letter under the floor of his house. The letter is from "
            "50 years ago, from a young woman to someone she loved. He tries to find her. "
            "The ending is bittersweet and beautiful. Historical, thoughtful, with heart."
        ),
    },
    {
        "id": "night-sky",
        "title": "The Night and the Stars",
        "prompt_hint": (
            "A girl lives in a village with no electricity. Every night she watches the "
            "stars and talks to the moon. One night something changes — the moon seems "
            "to answer. Short, poetic, magical. Like a fable."
        ),
    },
    {
        "id": "two-trees",
        "title": "The Two Trees",
        "prompt_hint": (
            "Two trees grow next to each other — one tall, one small. The tall tree is "
            "proud. But when a storm comes, the small tree survives because it bends. "
            "A simple fable about strength and humility. Elegant and philosophical."
        ),
    },
]


# ── DB queries ────────────────────────────────────────────────────────


def get_high_stability_words(db, min_stability_days: float = 14.0) -> list[dict]:
    """Get words the learner knows well (high FSRS stability)."""
    rows = (
        db.query(Lemma, UserLemmaKnowledge, Root)
        .join(UserLemmaKnowledge, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .outerjoin(Root, Lemma.root_id == Root.root_id)
        .filter(
            UserLemmaKnowledge.knowledge_state.in_(["learning", "known"]),
            UserLemmaKnowledge.fsrs_card_json.isnot(None),
        )
        .all()
    )

    words = []
    for lemma, knowledge, root in rows:
        card_data = knowledge.fsrs_card_json
        if not card_data:
            continue

        # Extract stability from FSRS card JSON
        stability = None
        if isinstance(card_data, dict):
            stability = card_data.get("s") or card_data.get("stability")
        elif isinstance(card_data, str):
            try:
                parsed = json.loads(card_data)
                stability = parsed.get("s") or parsed.get("stability")
            except (json.JSONDecodeError, TypeError):
                pass

        if stability is None or stability < min_stability_days:
            continue

        words.append({
            "arabic": lemma.lemma_ar,
            "gloss": lemma.gloss_en or "",
            "pos": lemma.pos or "",
            "root": root.root if root else "",
            "stability": round(stability, 1),
        })

    # Sort by stability descending (most well-known first)
    words.sort(key=lambda w: w["stability"], reverse=True)
    return words


# ── LLM story generation ─────────────────────────────────────────────


def generate_story_via_llm(
    words: list[dict],
    theme: dict,
    max_words_per_sentence: int = 7,
) -> dict | None:
    """Generate a story using Claude CLI or Gemini API."""

    # Format word list by POS
    nouns = [w for w in words if w["pos"] in ("noun", "proper_noun", "")]
    verbs = [w for w in words if w["pos"] == "verb"]
    adjs = [w for w in words if w["pos"] in ("adjective", "adverb")]

    word_list = "NOUNS: " + ", ".join(f"{w['arabic']} ({w['gloss']})" for w in nouns[:80])
    word_list += "\nVERBS: " + ", ".join(f"{w['arabic']} ({w['gloss']})" for w in verbs[:40])
    word_list += "\nADJECTIVES: " + ", ".join(f"{w['arabic']} ({w['gloss']})" for w in adjs[:30])

    system_prompt = (
        "You are a master Arabic storyteller creating stories for an adult language learner. "
        "You write in Modern Standard Arabic (MSA/fusha) with full tashkeel (diacritics on every letter). "
        "Your stories are genuinely beautiful — not language textbook drills. "
        "Think Borges, Calvino, Khalil Gibran, One Thousand and One Nights."
    )

    prompt = f"""Write a short Arabic story for a listening podcast.

THEME: {theme['prompt_hint']}

VOCABULARY CONSTRAINT — CRITICAL:
Use ONLY these known words (plus standard function words like في، من، إلى، على، و، أن، هذا، هي، هو، كان، etc.):
{word_list}

RULES:
1. Each sentence must be SHORT: 4-7 words maximum. This is for listening comprehension.
2. Use simple grammar — no complex relative clauses, no passive voice, no rare conjugations.
3. Write exactly 10 sentences.
4. Every word must have full tashkeel (diacritics).
5. The story must be genuinely interesting — with emotion, surprise, or beauty.
6. Do NOT use words outside the provided list (except function words / particles).
7. Prefer concrete, vivid language over abstract.

Respond with JSON:
{{
  "title_ar": "القصة العنوان",
  "title_en": "The Story Title",
  "sentences": [
    {{"arabic": "...", "english": "..."}},
    ...
  ]
}}"""

    # Try Claude CLI first (free, higher quality)
    try:
        result = _call_claude_cli(prompt, system_prompt, model="opus")
        if result:
            return result
    except Exception as e:
        logger.warning("Claude CLI failed: %s, trying Gemini...", e)

    # Fallback to Gemini API
    try:
        result = _call_gemini(prompt, system_prompt)
        if result:
            return result
    except Exception as e:
        logger.warning("Gemini failed: %s", e)

    return None


def _call_claude_cli(prompt: str, system_prompt: str, model: str = "opus") -> dict | None:
    """Call Claude CLI for story generation."""
    import shutil

    if not shutil.which("claude"):
        raise RuntimeError("claude CLI not found")

    cmd = [
        "claude", "-p",
        "--tools", "",
        "--output-format", "json",
        "--model", model,
        "--no-session-persistence",
        "--system-prompt", system_prompt,
    ]

    logger.info("Calling Claude %s for story generation...", model)
    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=180,
    )

    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[:200]}")

    # Parse JSON from Claude's output
    text = proc.stdout.strip()

    # Claude --output-format json wraps in {"result": "..."}
    try:
        outer = json.loads(text)
        if "result" in outer:
            text = outer["result"]
    except (json.JSONDecodeError, TypeError):
        pass

    # Strip markdown fences if present
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*$", "", text)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON in the text
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return json.loads(match.group())
        logger.error("Could not parse JSON from Claude output: %s", text[:200])
        return None


def _call_gemini(prompt: str, system_prompt: str) -> dict | None:
    """Fallback to Gemini API."""
    import litellm

    gemini_key = os.environ.get("GEMINI_KEY")
    if not gemini_key:
        raise RuntimeError("GEMINI_KEY not set")

    resp = litellm.completion(
        model="gemini/gemini-2.5-flash-preview",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        api_key=gemini_key,
        response_format={"type": "json_object"},
        timeout=60,
    )

    text = resp.choices[0].message.content.strip()
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*$", "", text)
    return json.loads(text)


# ── Podcast building ──────────────────────────────────────────────────


def build_story_episode(story: dict, theme: dict) -> list[Seg]:
    """Build a story-breakdown podcast from a generated story."""
    sents = story["sentences"]
    title_en = story.get("title_en", theme["title"])
    title_ar = story.get("title_ar", "")

    segments: list[Seg] = []

    # Opening
    segments.extend([
        en(f"Today's story: {title_en}.", speed=0.95),
        silence(1500),
    ])

    if title_ar:
        segments.extend([
            ar(title_ar, speed=0.8),
            silence(2000),
        ])

    # Teach each sentence with build-up
    taught = []
    for i, s in enumerate(sents):
        arabic = s["arabic"]
        english = s["english"]

        # English meaning first
        segments.extend([
            en(english, speed=0.95),
            silence(1200),
        ])

        # Arabic slow, then pause, then Arabic at normal speed
        segments.extend([
            ar_slow(arabic),
            silence(2000),
            ar_normal(arabic),
            silence(2000),
        ])

        taught.append(s)

        # After every 3 sentences, replay the growing sequence
        if (i + 1) % 3 == 0 and i > 0:
            n = len(taught)
            segments.append(en(f"Let's hear those {n} sentences together."))
            segments.append(silence(1200))
            for t in taught:
                segments.extend([
                    ar_normal(t["arabic"]),
                    silence(1800),
                ])
            segments.append(silence(1000))

    # Full story — Arabic only
    segments.extend([
        silence(1500),
        en("Now the full story in Arabic."),
        silence(2000),
    ])
    for s in sents:
        segments.extend([
            ar_normal(s["arabic"]),
            silence(1500),
        ])

    # Bilingual replay
    segments.extend([
        silence(1500),
        en("Once more, with English after each sentence."),
        silence(1500),
    ])
    for s in sents:
        segments.extend([
            ar_normal(s["arabic"]),
            silence(1500),
            en(s["english"], speed=0.95),
            silence(2000),
        ])

    # Final Arabic-only replay (faster pacing)
    segments.extend([
        silence(1500),
        en("One last time. Just Arabic, a little faster."),
        silence(2000),
    ])
    for s in sents:
        segments.extend([
            ar(s["arabic"], speed=0.95),
            silence(1200),
        ])

    segments.append(silence(2000))
    return segments


def _extract_key_words(story: dict, known_words: list[dict]) -> list[dict]:
    """Extract key vocabulary used in the story, matched against known words."""
    # Collect all Arabic words from the story
    story_text = " ".join(s["arabic"] for s in story["sentences"])
    # Match against known words
    key = []
    seen = set()
    for w in known_words:
        bare = w["arabic"].replace("\u064e", "").replace("\u064f", "").replace("\u0650", "")
        if bare in story_text and w["arabic"] not in seen and w["pos"] not in ("", "particle"):
            key.append({
                "arabic": w["arabic"],
                "gloss": w["gloss"],
                "stability_days": w["stability"],
            })
            seen.add(w["arabic"])
        if len(key) >= 12:
            break
    return key


def _generate_summary(story: dict, theme: dict) -> str:
    """Generate a short English summary from the story's English translations."""
    english_sents = [s["english"] for s in story["sentences"]]
    if len(english_sents) <= 3:
        return " ".join(english_sents)
    # Take first 2 and last sentence for a brief summary
    return f"{english_sents[0]} {english_sents[1]} ... {english_sents[-1]}"


async def main():
    parser = argparse.ArgumentParser(description="Generate story podcast episodes")
    parser.add_argument("--count", type=int, default=3, help="Number of stories")
    parser.add_argument("--theme", type=str, default=None,
                        help="Specific theme ID (e.g. 'magical-library')")
    parser.add_argument("--min-stability", type=float, default=14.0,
                        help="Minimum FSRS stability in days")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Get high-stability words
        words = get_high_stability_words(db, min_stability_days=args.min_stability)
        logger.info("Found %d words with stability >= %.0f days", len(words), args.min_stability)

        if len(words) < 30:
            logger.error("Not enough high-stability words (%d). Lower --min-stability?", len(words))
            sys.exit(1)

        # Log some stats
        avg_stability = sum(w["stability"] for w in words) / len(words)
        logger.info("Average stability: %.1f days. Top words: %s",
                    avg_stability,
                    ", ".join(f"{w['arabic']} ({w['stability']}d)" for w in words[:10]))

        # Select themes
        if args.theme:
            themes = [t for t in STORY_THEMES if t["id"] == args.theme]
            if not themes:
                logger.error("Unknown theme: %s. Options: %s",
                            args.theme, ", ".join(t["id"] for t in STORY_THEMES))
                sys.exit(1)
        else:
            themes = STORY_THEMES[:args.count]

        # Generate stories and podcasts
        for theme in themes:
            logger.info("=== Generating story: %s ===", theme["title"])

            story = generate_story_via_llm(words, theme)
            if not story or "sentences" not in story:
                logger.error("Failed to generate story for %s", theme["id"])
                continue

            n_sents = len(story["sentences"])
            logger.info("Story generated: %s — %d sentences", story.get("title_en", "?"), n_sents)

            # Log the story
            for i, s in enumerate(story["sentences"]):
                logger.info("  [%d] %s", i + 1, s["arabic"])
                logger.info("       %s", s["english"])

            # Build podcast segments
            segments = build_story_episode(story, theme)

            # Stitch audio
            output_name = f"story-{theme['id']}-{datetime.now().strftime('%Y%m%d-%H%M')}"
            logger.info("Generating audio: %d segments", len(segments))
            path = await stitch_podcast(segments, output_name)

            # Estimate duration from file size (128kbps)
            duration_s = int(path.stat().st_size / 16000)

            # Extract key words from the story (unique content words used)
            key_words = _extract_key_words(story, words)

            # Generate summary
            summary = _generate_summary(story, theme)

            # Save metadata
            meta = {
                "title_en": story.get("title_en", theme["title"]),
                "title_ar": story.get("title_ar", ""),
                "theme_id": theme["id"],
                "format_type": "story",
                "summary": summary,
                "sentences": story["sentences"],
                "key_words": key_words,
                "duration_seconds": duration_s,
                "generated_at": datetime.now().isoformat(),
                "listened_at": None,
                "listen_progress": 0,
            }
            save_metadata(output_name, meta)

            logger.info("Saved: %s (%.1f min)", path, duration_s / 60)
            print(f"\nPodcast ready: {path}")
            print(f"API URL: /api/podcasts/audio/{path.name}")
            print()

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
