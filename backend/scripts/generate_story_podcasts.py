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
import random
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

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
    # ── Extended themes for ongoing auto-generation ──
    {
        "id": "old-musician",
        "title": "The Old Musician",
        "prompt_hint": (
            "An old man plays the oud in the same café every evening. Nobody listens anymore. "
            "One night a child sits next to him and asks to learn. The music changes the room. "
            "Bittersweet, warm, about passing knowledge between generations."
        ),
    },
    {
        "id": "hidden-garden",
        "title": "The Hidden Garden",
        "prompt_hint": (
            "Behind a wall in a busy city, there is a secret garden nobody knows about. "
            "A boy finds the door. Inside, the flowers speak different languages and the "
            "water remembers stories. Magical realism, Borges-like wonder."
        ),
    },
    {
        "id": "brave-sparrow",
        "title": "The Brave Sparrow",
        "prompt_hint": (
            "A small sparrow decides to fly across the desert alone. The other birds say "
            "it is impossible. Along the way the sparrow meets a wind that helps and a sun "
            "that tests. A fable about courage and believing in yourself."
        ),
    },
    {
        "id": "desert-rain",
        "title": "The Rain in the Desert",
        "prompt_hint": (
            "A village in the desert waits for rain. The children play in the dust. "
            "An old woman says the rain will come if they sing. They sing and something "
            "unexpected happens. Magical, joyful, with a surprise twist."
        ),
    },
    {
        "id": "silver-key",
        "title": "The Silver Key",
        "prompt_hint": (
            "A girl finds a small silver key in the market. It doesn't fit any door. "
            "She searches the city — the baker, the teacher, the old clockmaker. "
            "The key opens something she didn't expect. A mystery with a gentle ending."
        ),
    },
    {
        "id": "fisherman-dream",
        "title": "The Fisherman's Dream",
        "prompt_hint": (
            "A fisherman catches no fish for weeks. One night he dreams of a golden fish "
            "that tells him a secret. The next morning he follows the dream. "
            "A story about patience and listening to your heart."
        ),
    },
    {
        "id": "painted-door",
        "title": "The Painted Door",
        "prompt_hint": (
            "In an old street, there is a door painted with beautiful colors. Everyone "
            "walks past it. One day a woman opens it and finds a room full of light. "
            "Inside is something she lost years ago. Mysterious and touching."
        ),
    },
    {
        "id": "sleeping-village",
        "title": "The Sleeping Village",
        "prompt_hint": (
            "A traveler arrives at a village where everyone is asleep in the middle of the day. "
            "He tries to wake them. Finally he finds one person awake — a child reading a book. "
            "The child explains why the village sleeps. Surreal and philosophical."
        ),
    },
    {
        "id": "broken-clock",
        "title": "The Broken Clock",
        "prompt_hint": (
            "The clock in the town square stops at 3:15. The clockmaker cannot fix it. "
            "A boy notices that something special happens at exactly 3:15 every day. "
            "A story about time, attention, and small miracles."
        ),
    },
    {
        "id": "traveling-merchant",
        "title": "The Traveling Merchant",
        "prompt_hint": (
            "A merchant travels between cities selling spices. In each city he hears "
            "part of a riddle. When he puts the pieces together, the answer changes "
            "his life. A journey story with wisdom and humor."
        ),
    },
    {
        "id": "paper-boat",
        "title": "The Paper Boat",
        "prompt_hint": (
            "A boy makes a paper boat and puts it in the river. He writes a message inside. "
            "The boat travels far. Months later, someone sends a message back. "
            "A story about connection across distance. Simple and beautiful."
        ),
    },
    {
        "id": "forgotten-well",
        "title": "The Forgotten Well",
        "prompt_hint": (
            "Behind the mosque there is an old well that nobody uses anymore. A girl drops "
            "a coin and hears a voice. The well tells stories from a hundred years ago. "
            "Historical, atmospheric, a portal to the past."
        ),
    },
    {
        "id": "moon-door",
        "title": "The Door to the Moon",
        "prompt_hint": (
            "On the highest roof in the city, there is a small door. The neighbors say "
            "it goes nowhere. A boy opens it one night and steps onto the moon. "
            "He brings something back. Whimsical and dreamlike."
        ),
    },
    {
        "id": "kind-baker",
        "title": "The Kind Baker",
        "prompt_hint": (
            "A baker gives free bread to the poor every Friday. One day a rich man asks "
            "him why. The baker tells a story about his grandmother. The rich man learns "
            "something important. A story about generosity and memory."
        ),
    },
    {
        "id": "two-rivers",
        "title": "The Two Rivers",
        "prompt_hint": (
            "Two rivers flow through the same valley. One is fast, one is slow. "
            "The fast river mocks the slow one. But when they reach the sea, the slow river "
            "has carried more life. A fable about patience versus speed."
        ),
    },
    {
        "id": "lost-shadow",
        "title": "The Boy Who Lost His Shadow",
        "prompt_hint": (
            "A boy wakes up one morning and his shadow is gone. He searches everywhere. "
            "His shadow went on an adventure without him. When it returns, it has stories to tell. "
            "Playful, funny, and a little philosophical."
        ),
    },
    {
        "id": "singing-stones",
        "title": "The Singing Stones",
        "prompt_hint": (
            "In the mountains, there are stones that sing when the wind blows. A shepherd "
            "discovers that each stone sings a different note. He arranges them into a melody. "
            "A story about music, nature, and creation."
        ),
    },
    {
        "id": "glass-bird",
        "title": "The Glass Bird",
        "prompt_hint": (
            "A glassmaker creates a perfect glass bird. One morning it flies away. "
            "He chases it through the city. When he catches it, he must choose — keep it "
            "or let it go. A parable about art and freedom."
        ),
    },
    {
        "id": "last-train",
        "title": "The Last Train",
        "prompt_hint": (
            "A woman runs to catch the last train. She misses it. At the empty station "
            "she meets another person who also missed it. They talk all night. "
            "Sometimes missing something leads to something better. Romantic and warm."
        ),
    },
    {
        "id": "wise-donkey",
        "title": "The Wise Donkey",
        "prompt_hint": (
            "A farmer has a donkey that seems stupid. But the donkey always knows "
            "which path is safe and where to find water. The farmer finally realizes "
            "the donkey is the wisest creature on the farm. A gentle, funny fable."
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
            "lemma_id": lemma.lemma_id,
        })

    # Sort by stability descending (most well-known first)
    words.sort(key=lambda w: w["stability"], reverse=True)
    return words


# ── Word-to-lemma mapping ────────────────────────────────────────────


def map_story_to_lemma_ids(story: dict, db) -> tuple[list[int], list[dict]]:
    """Map all content words in a story to lemma_ids using the NLP pipeline.

    Returns:
        (word_lemma_ids, enriched_sentences)
        - word_lemma_ids: unique non-function-word lemma_ids across all sentences
        - enriched_sentences: story sentences with word_mappings added
    """
    from app.services.sentence_validator import (
        _is_function_word,
        build_comprehensive_lemma_lookup,
        strip_diacritics,
        strip_punctuation,
    )

    lookup = build_comprehensive_lemma_lookup(db)
    all_lemma_ids: set[int] = set()
    enriched = []

    for sent in story["sentences"]:
        arabic = sent["arabic"]
        tokens = strip_punctuation(arabic).split()
        mappings = []
        for token in tokens:
            bare = strip_diacritics(token)
            is_func = _is_function_word(bare)
            lemma_id = lookup.get(bare)
            mappings.append({
                "surface": token,
                "lemma_id": lemma_id,
                "is_function_word": is_func,
            })
            if lemma_id and not is_func:
                all_lemma_ids.add(lemma_id)

        enriched.append({
            **sent,
            "word_mappings": mappings,
        })

    return sorted(all_lemma_ids), enriched


# ── Theme selection ───────────────────────────────────────────────────


def _get_used_theme_ids() -> set[str]:
    """Read existing podcast metadata to find which theme_ids have been used."""
    used = set()
    PODCAST_DIR.mkdir(parents=True, exist_ok=True)
    for f in PODCAST_DIR.glob("*.json"):
        try:
            meta = json.loads(f.read_text())
            tid = meta.get("theme_id")
            if tid:
                used.add(tid)
        except (json.JSONDecodeError, OSError):
            pass
    return used


def pick_unused_theme() -> dict:
    """Pick a theme that hasn't been used yet, or a random one if all used."""
    used = _get_used_theme_ids()
    unused = [t for t in STORY_THEMES if t["id"] not in used]
    if unused:
        return random.choice(unused)
    # All used — pick random from full pool
    return random.choice(STORY_THEMES)


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
                "lemma_id": w["lemma_id"],
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


# ── Story-to-podcast (from existing DB stories) ──────────────────────

LONG_SENTENCE_THRESHOLD = 8  # sentences with more words get broken down into chunks


def _split_into_chunks(arabic: str, chunk_size: int = 4) -> list[str]:
    """Split a long Arabic sentence into pedagogical chunks of ~chunk_size words."""
    words = arabic.split()
    if len(words) <= chunk_size:
        return [arabic]
    chunks = []
    for i in range(0, len(words), chunk_size):
        chunk = " ".join(words[i:i + chunk_size])
        chunks.append(chunk)
    return chunks


def _build_sentence_breakdown(segments: list[Seg], arabic: str, english: str) -> None:
    """Teach a single sentence with optional chunk-by-chunk breakdown."""
    words = arabic.split()

    if len(words) >= LONG_SENTENCE_THRESHOLD:
        # Long sentence: break into chunks, teach each, then build up
        chunks = _split_into_chunks(arabic, chunk_size=4)

        # English meaning first for full context (if available)
        if english:
            segments.extend([
                en(english, speed=0.95),
                silence(1500),
            ])

        # Teach each chunk
        for j, chunk in enumerate(chunks):
            segments.extend([
                ar_slow(chunk),
                silence(1500),
                ar(chunk, speed=0.85),
                silence(1500),
            ])

        # Now full sentence slow
        segments.extend([
            en("Now the whole sentence together."),
            silence(800),
            ar_slow(arabic),
            silence(2000),
            ar_normal(arabic),
            silence(2500),
        ])
    else:
        # Short sentence: standard teach
        if english:
            segments.extend([
                en(english, speed=0.95),
                silence(1200),
            ])
        segments.extend([
            ar_slow(arabic),
            silence(2000),
            ar_normal(arabic),
            silence(2000),
        ])


def build_story_from_db_episode(
    sentences: list[dict], title_en: str, title_ar: str = "",
) -> list[Seg]:
    """Build a breakdown podcast from existing DB story sentences.

    Each sentence dict has: {arabic, english}
    Long sentences are automatically broken into chunks.
    """
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

    # Teach each sentence
    taught = []
    for i, s in enumerate(sentences):
        _build_sentence_breakdown(segments, s["arabic"], s["english"])
        taught.append(s)

        # After every 4 sentences, replay the growing sequence
        if (i + 1) % 4 == 0 and i > 0:
            n = len(taught)
            segments.append(en(f"Let's hear those {n} sentences together."))
            segments.append(silence(1200))
            for t in taught:
                segments.extend([
                    ar_normal(t["arabic"]),
                    silence(1500),
                ])
            segments.append(silence(1000))

    # Full story — Arabic only
    segments.extend([
        silence(1500),
        en("Now the full story in Arabic."),
        silence(2000),
    ])
    for s in sentences:
        segments.extend([
            ar_normal(s["arabic"]),
            silence(1500),
        ])

    # Bilingual replay (only if we have English translations)
    has_english = any(s.get("english") for s in sentences)
    if has_english:
        segments.extend([
            silence(1500),
            en("Once more, with English after each sentence."),
            silence(1500),
        ])
        for s in sentences:
            segments.extend([
                ar_normal(s["arabic"]),
                silence(1500),
            ])
            if s.get("english"):
                segments.extend([
                    en(s["english"], speed=0.95),
                    silence(2000),
                ])
            else:
                segments.append(silence(2000))

    # Final Arabic-only replay
    segments.extend([
        silence(1500),
        en("One last time. Just Arabic, a little faster."),
        silence(2000),
    ])
    for s in sentences:
        segments.extend([
            ar(s["arabic"], speed=0.95),
            silence(1200),
        ])

    segments.append(silence(2000))
    return segments


def _get_story_sentences_from_db(db, story_id: int) -> tuple[list[dict], list[int], str, str, str]:
    """Pull sentences and word mappings from a DB story.

    Returns: (sentences, word_lemma_ids, title_en, title_ar, body_en)
    """
    from collections import defaultdict
    from app.models import Story, StoryWord

    story = db.query(Story).filter(Story.id == story_id).first()
    if not story:
        raise ValueError(f"Story {story_id} not found")

    # Get story words grouped by sentence_index
    swords = (
        db.query(StoryWord)
        .filter(StoryWord.story_id == story_id)
        .order_by(StoryWord.sentence_index, StoryWord.position)
        .all()
    )

    by_sent: dict[int, list] = defaultdict(list)
    all_lemma_ids: set[int] = set()
    for sw in swords:
        by_sent[sw.sentence_index].append(sw)
        if sw.lemma_id and not sw.is_function_word:
            all_lemma_ids.add(sw.lemma_id)

    # Parse English translations from body_en (one per line)
    english_lines = []
    if story.body_en:
        english_lines = [line.strip() for line in story.body_en.strip().split("\n") if line.strip()]

    sentences = []
    for idx in sorted(by_sent.keys()):
        words = by_sent[idx]
        arabic = " ".join(sw.surface_form for sw in words)
        # Try to match English by sentence index
        english = english_lines[idx] if idx < len(english_lines) else ""
        sentences.append({"arabic": arabic, "english": english})

    return (
        sentences,
        sorted(all_lemma_ids),
        story.title_en or "",
        story.title_ar or "",
        story.body_en or "",
    )


async def generate_podcast_from_story(db, story_id: int) -> Path | None:
    """Generate a podcast episode from an existing DB story.

    No LLM needed — uses the story's existing sentences and word mappings.
    """
    sentences, word_lemma_ids, title_en, title_ar, _body_en = (
        _get_story_sentences_from_db(db, story_id)
    )

    if not sentences:
        logger.error("Story %d has no sentences", story_id)
        return None

    # Filter out empty/noise sentences (like "عَآآآآآآآآآآ")
    sentences = [s for s in sentences if len(s["arabic"].split()) >= 2]

    logger.info("=== Building podcast from story #%d: %s (%d sentences) ===",
                story_id, title_en, len(sentences))

    # Build episode segments
    segments = build_story_from_db_episode(sentences, title_en, title_ar)

    # Stitch audio
    slug = re.sub(r"[^a-z0-9]+", "-", title_en.lower()).strip("-")[:30]
    output_name = f"book-{slug}-{datetime.now().strftime('%Y%m%d-%H%M')}"
    logger.info("Generating audio: %d segments", len(segments))
    path = await stitch_podcast(segments, output_name)

    duration_s = int(path.stat().st_size / 16000)

    # Key words: pick content words with glosses
    key_words = []
    seen_lemmas: set[int] = set()
    from app.models import StoryWord as SW, Lemma as L
    swords = (
        db.query(SW, L)
        .join(L, SW.lemma_id == L.lemma_id)
        .filter(SW.story_id == story_id, SW.is_function_word == False, SW.lemma_id.isnot(None))
        .all()
    )
    for sw, lemma in swords:
        if lemma.lemma_id not in seen_lemmas and lemma.gloss_en:
            key_words.append({
                "arabic": lemma.lemma_ar,
                "gloss": lemma.gloss_en,
                "lemma_id": lemma.lemma_id,
            })
            seen_lemmas.add(lemma.lemma_id)
        if len(key_words) >= 15:
            break

    # Summary from English
    en_sents = [s["english"] for s in sentences if s["english"]]
    summary = " ".join(en_sents[:3]) if en_sents else title_en

    meta = {
        "title_en": title_en,
        "title_ar": title_ar,
        "theme_id": f"book-{story_id}",
        "format_type": "book",
        "source_story_id": story_id,
        "summary": summary,
        "sentences": sentences,
        "key_words": key_words,
        "word_lemma_ids": word_lemma_ids,
        "duration_seconds": duration_s,
        "generated_at": datetime.now().isoformat(),
        "listened_at": None,
        "listen_progress": 0,
    }
    save_metadata(output_name, meta)

    logger.info("Saved: %s (%.1f min, %d words tracked)", path, duration_s / 60, len(word_lemma_ids))
    return path


async def generate_single_podcast(
    db, words: list[dict], theme: dict,
) -> Path | None:
    """Generate one podcast episode end-to-end. Returns output path or None."""
    logger.info("=== Generating story: %s ===", theme["title"])

    story = generate_story_via_llm(words, theme)
    if not story or "sentences" not in story:
        logger.error("Failed to generate story for %s", theme["id"])
        return None

    n_sents = len(story["sentences"])
    logger.info("Story generated: %s — %d sentences", story.get("title_en", "?"), n_sents)

    for i, s in enumerate(story["sentences"]):
        logger.info("  [%d] %s", i + 1, s["arabic"])
        logger.info("       %s", s["english"])

    # Map words to lemma_ids for listening credit
    word_lemma_ids, enriched_sentences = map_story_to_lemma_ids(story, db)
    logger.info("Mapped %d unique content-word lemma_ids", len(word_lemma_ids))

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

    # Save metadata with lemma_id mappings for word credit
    meta = {
        "title_en": story.get("title_en", theme["title"]),
        "title_ar": story.get("title_ar", ""),
        "theme_id": theme["id"],
        "format_type": "story",
        "summary": summary,
        "sentences": enriched_sentences,
        "key_words": key_words,
        "word_lemma_ids": word_lemma_ids,
        "duration_seconds": duration_s,
        "generated_at": datetime.now().isoformat(),
        "listened_at": None,
        "listen_progress": 0,
    }
    save_metadata(output_name, meta)

    logger.info("Saved: %s (%.1f min, %d words tracked)", path, duration_s / 60, len(word_lemma_ids))
    return path


async def main():
    parser = argparse.ArgumentParser(description="Generate story podcast episodes")
    parser.add_argument("--count", type=int, default=3, help="Number of stories")
    parser.add_argument("--theme", type=str, default=None,
                        help="Specific theme ID (e.g. 'magical-library')")
    parser.add_argument("--min-stability", type=float, default=14.0,
                        help="Minimum FSRS stability in days")
    parser.add_argument("--from-story", type=int, default=None,
                        help="Generate podcast from existing DB story ID")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Mode 1: Generate from existing DB story
        if args.from_story:
            path = await generate_podcast_from_story(db, args.from_story)
            if path:
                print(f"\nPodcast ready: {path}")
                print(f"API URL: /api/podcasts/audio/{path.name}")
            else:
                print("Failed to generate podcast from story")
                sys.exit(1)
            return

        # Mode 2: Generate LLM stories
        words = get_high_stability_words(db, min_stability_days=args.min_stability)
        logger.info("Found %d words with stability >= %.0f days", len(words), args.min_stability)

        if len(words) < 30:
            logger.error("Not enough high-stability words (%d). Lower --min-stability?", len(words))
            sys.exit(1)

        avg_stability = sum(w["stability"] for w in words) / len(words)
        logger.info("Average stability: %.1f days. Top words: %s",
                    avg_stability,
                    ", ".join(f"{w['arabic']} ({w['stability']}d)" for w in words[:10]))

        if args.theme:
            themes = [t for t in STORY_THEMES if t["id"] == args.theme]
            if not themes:
                logger.error("Unknown theme: %s. Options: %s",
                            args.theme, ", ".join(t["id"] for t in STORY_THEMES))
                sys.exit(1)
        else:
            themes = []
            for _ in range(args.count):
                t = pick_unused_theme()
                themes.append(t)

        for theme in themes:
            path = await generate_single_podcast(db, words, theme)
            if path:
                print(f"\nPodcast ready: {path}")
                print(f"API URL: /api/podcasts/audio/{path.name}")
                print()

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
