#!/usr/bin/env python3
"""Generate repetition-focused podcast episodes targeting acquiring words.

Generates long stories (15 sentences) where each target word appears 3-4 times
in different contexts. Audio uses breakdown format: English → Arabic slow →
Arabic normal, with recap sections every 4 sentences, then full story replays.

Usage:
    python3 scripts/generate_repetition_podcasts.py [--count N] [--batch-size N]

Inside Docker:
    docker exec -w /app -e PYTHONPATH=/app alif-backend-1 \
        python3 scripts/generate_repetition_podcasts.py
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Local .env may use ELEVENLABS_KEY; code expects ELEVENLABS_API_KEY
if os.environ.get("ELEVENLABS_KEY") and not os.environ.get("ELEVENLABS_API_KEY"):
    os.environ["ELEVENLABS_API_KEY"] = os.environ["ELEVENLABS_KEY"]

from app.database import SessionLocal
from app.models import Lemma, Root, UserLemmaKnowledge
from app.services.podcast_service import (
    PODCAST_DIR,
    Seg,
    ar,
    ar_normal,
    ar_slow,
    en,
    save_metadata,
    silence,
    stitch_podcast,
)
# Import map_story_to_lemma_ids via sys.path manipulation
sys.path.insert(0, str(Path(__file__).parent))
from generate_story_podcasts import map_story_to_lemma_ids

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── DB queries ───────────────────────────────────────────────────────


def get_acquiring_words(db) -> list[dict]:
    """Get acquiring words sorted by times_seen (least seen first)."""
    from sqlalchemy import or_

    rows = (
        db.query(Lemma, UserLemmaKnowledge, Root)
        .join(UserLemmaKnowledge, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .outerjoin(Root, Lemma.root_id == Root.root_id)
        .filter(
            UserLemmaKnowledge.knowledge_state == "acquiring",
            or_(Lemma.word_category != "function", Lemma.word_category == None),
            Lemma.canonical_lemma_id == None,
        )
        .order_by(UserLemmaKnowledge.times_seen.asc())
        .all()
    )

    words = []
    for lemma, ulk, root in rows:
        words.append({
            "arabic": lemma.lemma_ar,
            "bare": lemma.lemma_ar_bare or "",
            "gloss": lemma.gloss_en or "",
            "pos": lemma.pos or "",
            "root": root.root if root else "",
            "times_seen": ulk.times_seen or 0,
            "lemma_id": lemma.lemma_id,
        })

    return words


def get_known_words(db) -> list[dict]:
    """Get known/learning words for scaffold vocabulary."""
    rows = (
        db.query(Lemma, Root)
        .join(UserLemmaKnowledge, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .outerjoin(Root, Lemma.root_id == Root.root_id)
        .filter(
            UserLemmaKnowledge.knowledge_state.in_(["learning", "known"]),
        )
        .all()
    )

    words = []
    for lemma, root in rows:
        words.append({
            "arabic": lemma.lemma_ar,
            "gloss": lemma.gloss_en or "",
            "pos": lemma.pos or "",
            "root": root.root if root else "",
            "lemma_id": lemma.lemma_id,
        })

    return words


# ── LLM story generation ────────────────────────────────────────────


def generate_all_stories(
    batches: list[list[dict]],
    scaffold_words: list[dict],
) -> list[dict | None]:
    """Generate all stories via LLM API (one call per story).

    Uses generate_completion() which tries Claude CLI first (free), then
    falls back to GPT-5.2 → Claude Haiku API.
    """
    from app.services.llm import generate_completion

    scaffold_str = ", ".join(f"{w['arabic']} ({w['gloss']})" for w in scaffold_words[:50])
    system_prompt = "Master Arabic storyteller. MSA/fusha with full tashkeel. Beautiful narratives."

    results = []
    for i, batch in enumerate(batches, 1):
        target_str = ", ".join(f"{w['arabic']} ({w['gloss']})" for w in batch)

        prompt = f"""Write a 15-sentence Arabic story with full tashkeel. Beautiful and engaging.
Each target word must appear 3-4 times in different sentences.

TARGET WORDS (repeat 3-4x each): {target_str}
SCAFFOLD (known words): {scaffold_str}

Rules: 4-8 words per sentence, simple grammar, full tashkeel, varied themes.
JSON: {{"title_ar": "...", "title_en": "...", "sentences": [{{"arabic": "...", "english": "..."}}]}}"""

        try:
            result = generate_completion(
                prompt=prompt, system_prompt=system_prompt,
                json_mode=True, temperature=0.8, timeout=120,
            )
            if result and "sentences" in result:
                logger.info("Story %d: %s (%d sentences)",
                            i, result.get("title_en", "?"), len(result["sentences"]))
                results.append(result)
                continue
        except Exception as e:
            logger.error("Story %d generation failed: %s", i, e)
        results.append(None)

    return results


# ── Podcast building ─────────────────────────────────────────────────


def build_repetition_episode(
    story: dict, target_words: list[dict], episode_num: int,
) -> list[Seg]:
    """Build a breakdown podcast with heavy repetition teaching."""
    sents = story["sentences"]
    title_en = story.get("title_en", f"Repetition Story {episode_num}")
    title_ar = story.get("title_ar", "")

    # Format target word summary for intro
    target_summary = ", ".join(f"{w['arabic']} ({w['gloss']})" for w in target_words)

    segments: list[Seg] = []

    # Opening — introduce the target words
    segments.extend([
        en(f"Story {episode_num}: {title_en}.", speed=0.95),
        silence(1500),
        en(f"Today's focus words: {target_summary}.", speed=0.9),
        silence(2000),
    ])

    if title_ar:
        segments.extend([
            ar(title_ar, speed=0.8),
            silence(2000),
        ])

    # Recap points every 4 sentences
    n_sents = len(sents)
    recap_size = 4
    recap_points: set[int] = set()
    if n_sents > recap_size:
        pos = recap_size
        while pos < n_sents:
            recap_points.add(pos)
            remaining = n_sents - pos
            pos += recap_size if remaining > recap_size + 1 else remaining

    # Teach each sentence: English → Arabic slow → Arabic normal
    taught = []
    for i, s in enumerate(sents):
        arabic = s["arabic"]
        english = s["english"]

        # English meaning first
        segments.extend([
            en(english, speed=0.95),
            silence(1200),
        ])

        # Arabic slow, pause, Arabic normal
        segments.extend([
            ar_slow(arabic),
            silence(2000),
            ar_normal(arabic),
            silence(2000),
        ])

        taught.append(s)

        # Recap at balanced points
        if (i + 1) in recap_points:
            n = len(taught)
            segments.append(en(f"Let's hear those {n} sentences together."))
            segments.append(silence(1200))
            for t in taught:
                segments.extend([
                    ar_normal(t["arabic"]),
                    silence(1500),
                ])
            segments.append(silence(1000))

    # Full story — Arabic only, normal speed
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

    # Full story — faster pacing
    segments.extend([
        silence(1500),
        en("One more time, a bit faster."),
        silence(2000),
    ])
    for s in sents:
        segments.extend([
            ar(s["arabic"], speed=0.95),
            silence(1200),
        ])

    # Closing — remind of target words
    segments.extend([
        silence(2000),
        en(f"Great work! You practiced: {target_summary}.", speed=0.9),
        silence(2000),
    ])

    return segments


# ── Main ─────────────────────────────────────────────────────────────


async def generate_one_episode_from_story(
    db,
    target_batch: list[dict],
    story: dict,
    episode_num: int,
) -> Path | None:
    """Build a podcast episode from an already-generated story."""
    target_summary = ", ".join(f"{w['arabic']}" for w in target_batch)
    logger.info("=== Episode %d: targeting %s ===", episode_num, target_summary)

    n_sents = len(story["sentences"])
    logger.info("Story generated: %s — %d sentences", story.get("title_en", "?"), n_sents)

    # Log word usage if provided
    usage = story.get("word_usage", {})
    for word, sents_used in usage.items():
        logger.info("  %s → appears in sentences %s", word, sents_used)

    for i, s in enumerate(story["sentences"]):
        logger.info("  [%d] %s", i + 1, s["arabic"])
        logger.info("       %s", s["english"])

    # Map words to lemma_ids for listening credit
    word_lemma_ids, enriched_sentences = map_story_to_lemma_ids(story, db)
    logger.info("Mapped %d unique content-word lemma_ids", len(word_lemma_ids))

    # Build podcast segments
    segments = build_repetition_episode(story, target_batch, episode_num)

    # Stitch audio
    output_name = f"rep-story-{episode_num}-{datetime.now().strftime('%Y%m%d-%H%M')}"
    logger.info("Generating audio: %d segments", len(segments))
    path = await stitch_podcast(segments, output_name)

    duration_s = int(path.stat().st_size / 16000)

    # Key words from targets
    key_words = [{"arabic": w["arabic"], "gloss": w["gloss"]} for w in target_batch]

    summary_en = " ".join(s["english"] for s in story["sentences"][:3])
    if len(story["sentences"]) > 3:
        summary_en += f" ... ({n_sents} sentences total)"

    meta = {
        "title_en": story.get("title_en", f"Repetition Story {episode_num}"),
        "title_ar": story.get("title_ar", ""),
        "theme_id": f"repetition-{episode_num}",
        "format_type": "story",
        "summary": summary_en,
        "sentences": enriched_sentences,
        "key_words": key_words,
        "word_lemma_ids": word_lemma_ids,
        "target_words": [{"arabic": w["arabic"], "gloss": w["gloss"], "lemma_id": w["lemma_id"]} for w in target_batch],
        "duration_seconds": duration_s,
        "generated_at": datetime.now().isoformat(),
        "listened_at": None,
        "listen_progress": 0,
    }
    save_metadata(output_name, meta)

    logger.info("Saved: %s (%.1f min, %d words tracked)", path, duration_s / 60, len(word_lemma_ids))
    return path


async def main():
    parser = argparse.ArgumentParser(description="Generate repetition-focused podcast episodes")
    parser.add_argument("--count", type=int, default=4,
                        help="Number of episodes to generate (default: 4)")
    parser.add_argument("--batch-size", type=int, default=7,
                        help="Target words per episode (default: 7)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate stories but skip TTS (prints text only)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        acquiring = get_acquiring_words(db)
        known = get_known_words(db)

        logger.info("Found %d acquiring words, %d known scaffold words", len(acquiring), len(known))

        if len(acquiring) < args.batch_size:
            logger.error("Not enough acquiring words (%d). Need at least %d.",
                         len(acquiring), args.batch_size)
            sys.exit(1)

        if len(known) < 30:
            logger.warning("Only %d known words — stories may be vocabulary-constrained", len(known))

        # Filter out function-word-like items that don't make good story targets
        SKIP_BARE = {"انها", "انه", "لك", "بـ", "لقد", "سوف", "لذلك", "لكن", "أن", "حول", "نحو", "يلا", "مرحبا", "أما"}
        acquiring = [w for w in acquiring if w["bare"] not in SKIP_BARE]
        logger.info("After filtering function-like words: %d acquiring words", len(acquiring))

        # Split acquiring words into batches
        total_words = min(len(acquiring), args.count * args.batch_size)
        target_words = acquiring[:total_words]

        batches = []
        for i in range(0, len(target_words), args.batch_size):
            batch = target_words[i:i + args.batch_size]
            if len(batch) >= 3:  # minimum viable batch
                batches.append(batch)

        logger.info("Will generate %d episodes covering %d target words",
                     len(batches), sum(len(b) for b in batches))

        for i, batch in enumerate(batches[:args.count], 1):
            batch_summary = ", ".join(f"{w['arabic']} ({w['gloss']}, seen:{w['times_seen']})" for w in batch)
            logger.info("Batch %d: %s", i, batch_summary)

        active_batches = batches[:args.count]
        stories = generate_all_stories(active_batches, known)

        # Process each story → podcast
        for i, (batch, story) in enumerate(zip(active_batches, stories), 1):
            if not story:
                logger.error("No story for episode %d, skipping", i)
                continue

            if args.dry_run:
                print(f"\n{'='*60}")
                print(f"Episode {i}: {story.get('title_en', '?')}")
                print(f"{'='*60}")
                for j, s in enumerate(story["sentences"], 1):
                    print(f"  {j:2d}. {s['arabic']}")
                    print(f"      {s['english']}")
                continue

            path = await generate_one_episode_from_story(db, batch, story, i)
            if path:
                print(f"\nEpisode {i} ready: {path}")
                print(f"API URL: /api/podcasts/audio/{path.name}")
                print()

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
