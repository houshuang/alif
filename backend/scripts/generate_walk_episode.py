#!/usr/bin/env python3
"""Walk episodes: a two-host Arabic conversation built from firmly known words.

Hands-off listening for a walk. The script is written by an LLM section by
section from the learner's firm vocabulary (FSRS stability >= FIRM_STABILITY_DAYS),
with a pool of weak/due words to repeat. Every token is then resolved with the
running-text mapper; turns that use a word outside the allowed set are sent
back for rewriting. Audio is produced only from a verified script file.

Two steps, so the script text can be inspected before any paid speech call:

    python3 scripts/generate_walk_episode.py write --plan plan.json --episode 1
    ALIF_AUDIO_ENABLED=1 python3 scripts/generate_walk_episode.py voice \
        --script data/podcasts/walk-scripts/<name>.json [--replay <previous name>]

The daily job plans a three-episode series from the topic queue, writes, checks
and voices one episode, and stops by itself when episodes go unacknowledged:

    ALIF_AUDIO_ENABLED=1 python3 scripts/generate_walk_episode.py daily

Read-only against the database: no learning state changes.
"""

import argparse
import asyncio
import hashlib
import io
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge
from app.services.podcast_service import PODCAST_DIR, SEGMENT_CACHE_DIR, save_metadata

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("walk")

FIRM_STABILITY_DAYS = 30.0
TARGET_POOL_SIZE = 45
TARGET_MIN_USES = 4
MAX_REWRITE_ROUNDS = 3
SECTION_WORDS = 270

SCRIPT_DIR = PODCAST_DIR / "walk-scripts"

ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"
TTS_MODEL = "eleven_multilingual_v2"
VOICES = {
    "A": "a1KZUXKFVFDOb33I1uqr",  # Salma: asks
    "B": "G1HOkzin3NMwRHSq60UI",  # Arabic Knight: explains
}
HOST_NAMES = {"A": "سَلْمَى", "B": "كَرِيم"}
VOICE_SETTINGS = {
    "stability": 0.6,
    "similarity_boost": 0.75,
    "style": 0.15,
    "speed": 0.88,
    "use_speaker_boost": True,
}
PAUSE_SENTENCE_MS = 380
PAUSE_TURN_MS = 700
PAUSE_SECTION_MS = 1600

SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "turns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string", "enum": ["A", "B"]},
                    "ar": {"type": "string"},
                    "en": {"type": "string"},
                },
                "required": ["speaker", "ar", "en"],
            },
        },
        "names": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["turns", "names"],
}

REWRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "turns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "ar": {"type": "string"},
                    "en": {"type": "string"},
                },
                "required": ["index", "ar", "en"],
            },
        },
    },
    "required": ["turns"],
}


# ── Vocabulary ───────────────────────────────────────────────────────


def _card(ulk: UserLemmaKnowledge) -> dict:
    card = ulk.fsrs_card_json
    if isinstance(card, str):
        try:
            card = json.loads(card)
        except ValueError:
            card = None
    return card or {}


def in_audio_arm(lemma_id: int) -> bool:
    """Half of the weak words are never offered as targets, as a comparison group."""
    return hashlib.sha256(f"walk_audio_v1:{lemma_id}".encode()).digest()[0] % 2 == 0


def load_vocabulary(db, firm_days: float = FIRM_STABILITY_DAYS) -> tuple[dict[int, Lemma], list[Lemma]]:
    """Return (firm lemmas by id, weak target pool ordered by frequency)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    firm: dict[int, Lemma] = {}
    weak: list[Lemma] = []
    rows = (
        db.query(UserLemmaKnowledge, Lemma)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(Lemma.canonical_lemma_id.is_(None), Lemma.gates_completed_at.isnot(None))
        .all()
    )
    for ulk, lemma in rows:
        if lemma.word_category in ("proper_name", "onomatopoeia") or not lemma.gloss_en:
            continue
        card = _card(ulk)
        stability = card.get("stability") or 0
        if ulk.knowledge_state in ("known", "learning") and stability >= firm_days:
            firm[lemma.lemma_id] = lemma
        elif (
            ulk.knowledge_state in ("known", "learning", "lapsed")
            and ulk.attention_disposition == "maintain"
            and (ulk.times_seen or 0) >= 3
            and (ulk.knowledge_state == "lapsed" or (card.get("due") or "9")[:19] <= now[:19])
            and in_audio_arm(lemma.lemma_id)
        ):
            weak.append(lemma)
    weak.sort(key=lambda lem: lem.frequency_rank or 10**9)
    return firm, weak[:TARGET_POOL_SIZE]


# ── Verification ─────────────────────────────────────────────────────

_ARABIC_LETTER = re.compile(r"[ء-ي]")


def _name_key(token: str) -> str:
    from app.services.sentence_validator import normalize_arabic, strip_punctuation

    bare = normalize_arabic(strip_punctuation(token))
    bare = re.sub(r"^[وفبلك]{0,2}(ال)?", "", bare) if len(bare) > 4 else bare
    return bare


class Verifier:
    def __init__(self, db, firm: dict[int, Lemma], targets: list[Lemma]):
        from app.services.sentence_validator import build_comprehensive_lemma_lookup

        self.db = db
        self.lookup = build_comprehensive_lemma_lookup(db)
        self.firm = firm
        self.targets = {lem.lemma_id: lem for lem in targets}
        self._canonical: dict[int, int] = {}
        self._lemma: dict[int, Lemma | None] = {}

    def _resolve(self, lemma_id: int) -> tuple[int, Lemma | None]:
        from app.services.canonical_resolution import resolve_canonical_lemma_id

        if lemma_id not in self._canonical:
            canonical = resolve_canonical_lemma_id(self.db, lemma_id)
            self._canonical[lemma_id] = canonical
            self._lemma[canonical] = self.db.get(Lemma, canonical)
        canonical = self._canonical[lemma_id]
        return canonical, self._lemma[canonical]

    def check(self, text: str, names: set[str]) -> list[dict]:
        """Classify every token of one turn."""
        from app.services.sentence_validator import (
            LemmaMatchProvenance,
            _is_function_word,
            lookup_lemma_id,
            resolve_exact_running_text_alias,
            strip_diacritics,
            strip_punctuation,
        )

        out = []
        for token in strip_punctuation(text).split():
            token = strip_punctuation(token)
            if not token or not _ARABIC_LETTER.search(token):
                continue
            alias = resolve_exact_running_text_alias(token, self.lookup)
            guess = False
            if alias.applicable:
                lemma_id = alias.lemma_id
                surface_function = alias.is_function_word
            else:
                provenance = LemmaMatchProvenance()
                lemma_id = lookup_lemma_id(token, self.lookup, provenance)
                guess = provenance.is_guess
                surface_function = _is_function_word(strip_diacritics(token))
            item = {"surface": token, "lemma_id": None, "status": "unmapped"}
            if lemma_id:
                canonical, lemma = self._resolve(lemma_id)
                item["lemma_id"] = canonical
                if lemma is None:
                    pass
                elif surface_function or _is_function_word(lemma.lemma_ar_bare):
                    item["status"] = "function"
                elif canonical in self.targets:
                    item["status"] = "target"
                elif canonical in self.firm:
                    item["status"] = "firm"
                elif lemma.word_category in ("proper_name", "practiced_name"):
                    item["status"] = "name"
                elif not guess:
                    item["status"] = "outside"
                    item["lemma_ar"] = lemma.lemma_ar
            if item["status"] == "unmapped" and _name_key(token) in names:
                item["status"] = "name"
            out.append(item)
        return out


def bad_tokens(checked: list[dict]) -> list[str]:
    return [t["surface"] for t in checked if t["status"] in ("unmapped", "outside")]


# ── Writing ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You write scripts for a private Arabic listening programme. One adult learner \
listens while walking, with no screen. He reads Modern Standard Arabic and knows about two thousand \
words well in print, but has heard very little Arabic, so the script must use ONLY the vocabulary \
list you are given. He is a well-read adult who enjoys history and ideas: write real, interesting \
content with concrete details, small surprises and some humour. Never write like a children's story \
or a language lesson, and never mention vocabulary or learning."""


def _vocab_block(firm: dict[int, Lemma]) -> str:
    lines = [f"{lem.lemma_ar} = {(lem.gloss_en or '').split(';')[0][:40]}" for lem in firm.values()]
    return "\n".join(sorted(lines))


def _rules(targets: list[Lemma], uses: dict[int, int]) -> str:
    target_lines = "\n".join(
        f"{lem.lemma_ar} = {(lem.gloss_en or '').split(';')[0][:40]} (used {uses.get(lem.lemma_id, 0)} times so far)"
        for lem in targets
    )
    return f"""RULES
1. Vocabulary: every noun, verb, adjective and adverb must be an inflected form of a word in the \
VOCABULARY list or the REPEAT list. Grammar particles, pronouns, prepositions and conjunctions are \
free. If the natural word is not on the list, say it another way with words that are.
2. Proper names of real people and places are allowed when the content needs them; list each one \
(without vowel marks) in "names". Keep them few and repeat the same ones.
3. Write every Arabic word with full vowel marks (tashkeel), including shadda and case endings.
4. No digits. Avoid exact dates; say "more than a thousand years ago" in words.
5. Spoken sentences of at most fourteen words. One idea per sentence.
6. Two hosts. B ({HOST_NAMES['B']}) knows the subject and tells it like a good storyteller: his turns \
are usually two to four sentences, with concrete details. A ({HOST_NAMES['A']}) is quick and curious. \
Her turns vary: a one-word reaction, a real question, a wrong guess that B corrects, a joke, a \
comparison with life today, or a summary in her own words of what the last few turns said. Do not \
make her echo the last phrase of every turn. Each important fact should still be heard two or three \
times in different wording across the section, through her summaries and his answers.
7. REPEAT list: choose the words from it that fit this subject naturally and use each chosen word in \
several different sentences. Prefer words already used earlier in the episode, until a word has been \
used {TARGET_MIN_USES} times. Never force a word that does not fit.
8. "en" is a plain English translation of the turn.

REPEAT list:
{target_lines}"""


def write_section(
    plan_ep: dict, index: int, previous_turns: list[dict], firm, targets, uses, extend_words: int = 0
) -> dict:
    from app.services.llm import generate_completion

    section = plan_ep["sections"][index]
    shown = previous_turns[-12:] if extend_words else previous_turns[-4:]
    last = "\n".join(f"{t['speaker']}: {t['ar']}" for t in shown) or "(this is the opening)"
    position = (
        "This is the opening section: the hosts greet the listener in one line and go straight into the subject."
        if index == 0
        else "This is the last section: end with a short natural goodbye that says the conversation continues tomorrow."
        if index == len(plan_ep["sections"]) - 1
        else "This section continues the conversation without any greeting."
    )
    length_line = (
        f"Continue this same section with {extend_words} more Arabic words in 6 to 10 further turns: "
        "add new concrete detail and one summary by A. Do not greet, do not close the section's subject early."
        if extend_words
        else f"Write at least {SECTION_WORDS} Arabic words in 12 to 18 turns. Count them: a shorter section is rejected."
    )
    prompt = f"""Episode subject: {plan_ep['title_en']}
Section {index + 1} of {len(plan_ep['sections'])}. {position}
What this section covers: {section}

The conversation so far ended with:
{last}

{length_line}

{_rules(targets, uses)}

VOCABULARY (Arabic = meaning):
{_vocab_block(firm)}"""
    return generate_completion(
        prompt,
        system_prompt=SYSTEM_PROMPT,
        json_schema=SECTION_SCHEMA,
        model_override=os.environ.get("ALIF_WALK_WRITER", "claude_sonnet"),
        timeout=600,
        task_type="walk_episode_write",
    )


def rewrite_turns(turns: list[dict], problems: dict[int, list[str]], firm, targets, uses) -> dict:
    from app.services.llm import generate_completion

    listing = "\n".join(
        f"[{i}] {turns[i]['speaker']}: {turns[i]['ar']}\n    words not allowed: {'، '.join(words)}"
        for i, words in problems.items()
    )
    prompt = f"""These turns of a two-host conversation use words that are not in the listener's vocabulary. \
Rewrite each turn so that it keeps its meaning and its place in the conversation but replaces the \
words that are not allowed. Return every listed turn with its index.

{listing}

{_rules(targets, uses)}

VOCABULARY (Arabic = meaning):
{_vocab_block(firm)}"""
    return generate_completion(
        prompt,
        system_prompt=SYSTEM_PROMPT,
        json_schema=REWRITE_SCHEMA,
        model_override=os.environ.get("ALIF_WALK_WRITER", "claude_sonnet"),
        timeout=600,
        task_type="walk_episode_rewrite",
    )


def write_episode(plan: dict, episode: int, firm_days: float = FIRM_STABILITY_DAYS) -> Path:
    """Write and verify one episode's script; returns the script file."""
    plan_ep = plan["episodes"][episode - 1]
    db = SessionLocal()
    try:
        firm, targets = load_vocabulary(db, firm_days)
        logger.info("firm words %d (stability >= %s days), repeat pool %d", len(firm), firm_days, len(targets))
        verifier = Verifier(db, firm, targets)
        turns: list[dict] = []
        names: set[str] = set()
        uses: dict[int, int] = {}
        for index in range(len(plan_ep["sections"])):
            result = write_section(plan_ep, index, turns, firm, targets, uses)
            section_turns = [dict(t, section=index) for t in result["turns"]]
            names |= {_name_key(n) for n in result.get("names", [])}
            for _ in range(2):
                words = sum(len(t["ar"].split()) for t in section_turns)
                if words >= SECTION_WORDS * 0.85:
                    break
                logger.info("section %d has %d words, extending", index + 1, words)
                more = write_section(
                    plan_ep, index, turns + section_turns, firm, targets, uses, extend_words=SECTION_WORDS - words
                )
                closing = section_turns[-2:] if index == len(plan_ep["sections"]) - 1 else []
                kept = section_turns[: len(section_turns) - len(closing)]
                section_turns = kept + [dict(t, section=index) for t in more["turns"]] + closing
                names |= {_name_key(n) for n in more.get("names", [])}
            for round_no in range(MAX_REWRITE_ROUNDS):
                problems = {}
                for i, turn in enumerate(section_turns):
                    words = bad_tokens(verifier.check(turn["ar"], names))
                    if words:
                        problems[i] = words
                if not problems:
                    break
                logger.info("section %d round %d: %d turns to rewrite", index + 1, round_no + 1, len(problems))
                fixed = rewrite_turns(section_turns, problems, firm, targets, uses)
                for item in fixed.get("turns", []):
                    if item.get("index") in problems:
                        section_turns[item["index"]].update(ar=item["ar"], en=item["en"])
            for turn in section_turns:
                turn["tokens"] = verifier.check(turn["ar"], names)
                for token in turn["tokens"]:
                    if token["status"] == "target":
                        uses[token["lemma_id"]] = uses.get(token["lemma_id"], 0) + 1
            turns.extend(section_turns)
            logger.info("section %d done: %d turns", index + 1, len(section_turns))

        tokens = [t for turn in turns for t in turn["tokens"]]
        counts: dict[str, int] = {}
        for token in tokens:
            counts[token["status"]] = counts.get(token["status"], 0) + 1
        leftovers = sorted({t["surface"] for t in tokens if t["status"] in ("unmapped", "outside")})
        name = f"walk-{plan['slug']}-{episode}-{datetime.now().strftime('%Y%m%d-%H%M')}"
        script = {
            "name": name,
            "slug": plan["slug"],
            "episode": episode,
            "firm_days": firm_days,
            "title_en": plan_ep["title_en"],
            "title_ar": plan_ep["title_ar"],
            "intro_en": plan_ep["intro_en"],
            "turns": turns,
            "names": sorted(names),
            "token_counts": counts,
            "not_allowed": leftovers,
            "targets": [
                {"lemma_id": lem.lemma_id, "arabic": lem.lemma_ar, "gloss": lem.gloss_en, "uses": uses[lem.lemma_id]}
                for lem in targets
                if uses.get(lem.lemma_id)
            ],
        }
        SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        path = SCRIPT_DIR / f"{name}.json"
        path.write_text(json.dumps(script, ensure_ascii=False, indent=1))
        total = sum(counts.values())
        logger.info("script %s: %d words, %s", path, total, counts)
        logger.info("not allowed (%d): %s", len(leftovers), " ".join(leftovers))
        logger.info("repeat words: %s", ", ".join(f"{t['arabic']}×{t['uses']}" for t in script["targets"]))
        return path
    finally:
        db.close()


# ── Voicing ──────────────────────────────────────────────────────────

_FINAL_SHORT_VOWEL = re.compile(r"[ٌٍَُِ]$")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?؟…])\s+")
_TRAILING_PUNCT = re.compile(r"[.!?؟…،,:؛\"»«]+$")


def pausal(sentence: str) -> str:
    """Drop the final short vowel before a pause, as spoken MSA does."""
    words = sentence.split()
    for i, word in enumerate(words):
        match = _TRAILING_PUNCT.search(word)
        if not match and i != len(words) - 1:
            continue
        stem = word[: match.start()] if match else word
        words[i] = _FINAL_SHORT_VOWEL.sub("", stem) + (match.group(0) if match else "")
    return " ".join(words)


async def speak(client, text: str, voice_id: str, lang: str, before: str = "", after: str = "") -> bytes:
    from app.services.tts import TTSDisabled, audio_generation_enabled

    settings = dict(VOICE_SETTINGS, speed=1.0) if lang == "en" else VOICE_SETTINGS
    key = hashlib.sha256(
        json.dumps(["walk_v1", text, voice_id, lang, settings, TTS_MODEL], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    SEGMENT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = SEGMENT_CACHE_DIR / f"{key}.mp3"
    if cache.exists():
        return cache.read_bytes()
    if not audio_generation_enabled():
        raise TTSDisabled("Audio generation is disabled. Set ALIF_AUDIO_ENABLED=1 to enable it.")
    payload = {
        "text": text,
        "model_id": TTS_MODEL,
        "language_code": lang,
        "voice_settings": settings,
    }
    if before:
        payload["previous_text"] = before
    if after:
        payload["next_text"] = after
    for attempt in range(3):
        resp = await client.post(
            f"{ELEVENLABS_BASE_URL}/text-to-speech/{voice_id}",
            headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Accept": "audio/mpeg"},
            json=payload,
            timeout=90.0,
        )
        if resp.status_code == 200:
            cache.write_bytes(resp.content)
            return resp.content
        logger.warning("speech call failed (%s): %s", resp.status_code, resp.text[:200])
        if resp.status_code not in (429, 500, 502, 503):
            break
        await asyncio.sleep(5 * (attempt + 1))
    raise RuntimeError(f"speech call failed: {resp.status_code}")


async def build_conversation(client, turns: list[dict]):
    """Voice the turns and join them with pauses. Returns (audio, Arabic characters)."""
    from pydub import AudioSegment

    body = AudioSegment.empty()
    characters = 0
    for i, turn in enumerate(turns):
        if i:
            new_section = turn.get("section") != turns[i - 1].get("section")
            body += AudioSegment.silent(PAUSE_SECTION_MS if new_section else PAUSE_TURN_MS)
        sentences = [pausal(s) for s in _SENTENCE_SPLIT.split(turn["ar"].strip()) if s.strip()]
        for j, sentence in enumerate(sentences):
            audio = await speak(
                client,
                sentence,
                VOICES[turn["speaker"]],
                "ar",
                before=" ".join(sentences[:j])[-300:],
                after=" ".join(sentences[j + 1 :])[:300],
            )
            characters += len(sentence)
            if j:
                body += AudioSegment.silent(PAUSE_SENTENCE_MS)
            body += AudioSegment.from_mp3(io.BytesIO(audio))
        if i % 20 == 0:
            logger.info("turn %d/%d, %.1f min so far", i + 1, len(turns), len(body) / 60000)
    return body, characters


async def voice_episode(
    script_path: Path,
    replay_path: Path | None = None,
    replay_sections: int = 3,
    name: str | None = None,
    max_turns: int | None = None,
) -> Path:
    """Voice a verified script into an MP3 with metadata for the Podcast screen."""
    import httpx
    from pydub import AudioSegment

    script = json.loads(Path(script_path).read_text())
    turns = script["turns"][:max_turns] if max_turns else script["turns"]
    name = name or script["name"]
    async with httpx.AsyncClient() as client:
        body, characters = await build_conversation(client, turns)
        final = AudioSegment.from_mp3(io.BytesIO(await speak(client, script["intro_en"], VOICES["B"], "en")))
        final += AudioSegment.silent(1200) + body
        if replay_path:
            previous = json.loads(Path(replay_path).read_text())
            replay_turns = [t for t in previous["turns"] if t.get("section", 0) < replay_sections]
            replay, replay_characters = await build_conversation(client, replay_turns)
            characters += replay_characters
            bridge = await speak(client, "And now, the first part of yesterday's conversation once more.", VOICES["B"], "en")
            final += AudioSegment.silent(2500) + AudioSegment.from_mp3(io.BytesIO(bridge))
            final += AudioSegment.silent(1200) + replay

    output = PODCAST_DIR / f"{name}.mp3"
    final.export(str(output), format="mp3", bitrate="96k")
    lemma_ids = sorted(
        {t["lemma_id"] for turn in turns for t in turn.get("tokens", []) if t["status"] in ("firm", "target")}
    )
    save_metadata(
        name,
        {
            "title_en": script["title_en"],
            "title_ar": script["title_ar"],
            "theme_id": f"walk-{script['slug']}-{script['episode']}",
            "format_type": "walk",
            "summary": script["intro_en"],
            "sentences": [{"arabic": t["ar"], "english": t["en"], "speaker": t["speaker"]} for t in turns],
            "key_words": [{"arabic": t["arabic"], "gloss": t["gloss"]} for t in script["targets"]],
            "target_words": script["targets"],
            "word_lemma_ids": lemma_ids,
            "token_counts": script["token_counts"],
            "replay_of": Path(replay_path).stem if replay_path else None,
            "firm_days": script.get("firm_days"),
            "duration_seconds": int(len(final) / 1000),
            "generated_at": datetime.now().isoformat(),
            "listened_at": None,
            "listen_progress": 0,
        },
    )
    logger.info("saved %s: %.1f min (new part %.1f min), %d Arabic characters voiced", output, len(final) / 60000, len(body) / 60000, characters)
    return output


# ── Daily job ────────────────────────────────────────────────────────

PLAN_DIR = PODCAST_DIR / "walk-plans"
MAX_NOT_ALLOWED_SHARE = 0.01

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "episodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title_en": {"type": "string"},
                    "title_ar": {"type": "string"},
                    "intro_en": {"type": "string"},
                    "sections": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title_en", "title_ar", "intro_en", "sections"],
            },
        },
    },
    "required": ["episodes"],
}


def make_plan(topic: dict) -> dict:
    """Plan a three-episode series on one topic as English section briefs."""
    from app.services.llm import generate_completion

    prompt = f"""Plan a three-episode audio series for one adult listener who enjoys history and ideas.
Topic: {topic['title']}
{('His notes on what he wants: ' + topic['notes']) if topic.get('notes') else ''}

Each episode is a conversation between two hosts and will be written in simple Modern Standard Arabic
from a vocabulary of about two thousand common words, so choose material that can be told with
everyday words: people, places, journeys, objects, decisions, reversals. Avoid anything that needs
technical terms.

For each of the three episodes give:
- title_en: a short English title
- title_ar: a short Arabic title with full vowel marks
- intro_en: one English sentence beginning "Today:" that says what the episode is about
- sections: exactly four briefs in English, three to five sentences each. A brief lists the concrete
  facts, names and one vivid detail or small joke for that part of the conversation. Use only
  well-established facts. Where something is a legend or disputed, say so in the brief.

The last brief of episodes one and two ends with what tomorrow's episode is about; the last brief of
episode three closes the series."""
    result = generate_completion(
        prompt,
        json_schema=PLAN_SCHEMA,
        model_override=os.environ.get("ALIF_WALK_WRITER", "claude_sonnet"),
        timeout=600,
        task_type="walk_episode_plan",
    )
    episodes = [e for e in result["episodes"] if e.get("sections")][:3]
    if len(episodes) < 2:
        raise RuntimeError("the plan has too few episodes")
    slug = re.sub(r"[^a-z0-9]+", "-", topic["title"].lower()).strip("-")[:40] or "topic"
    return {"slug": slug, "topic": topic, "episodes": episodes}


def cmd_daily(args) -> None:
    """Make today's episode if the decision rule allows it."""
    from app.services import walk_episodes

    ok, reason = walk_episodes.generation_decision()
    if not ok and not args.force:
        logger.info("no episode today: %s", reason)
        return
    state = walk_episodes.load_state()
    series = state.get("series")
    if not series or series["next_episode"] > series["episodes"]:
        topic = walk_episodes.next_topic(state)
        plan = make_plan(topic)
        PLAN_DIR.mkdir(parents=True, exist_ok=True)
        plan_path = PLAN_DIR / f"{plan['slug']}-{datetime.now().strftime('%Y%m%d')}.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=1))
        series = {"plan": str(plan_path), "title": topic["title"], "next_episode": 1, "episodes": len(plan["episodes"])}
        fresh = walk_episodes.load_state()
        fresh.update(series=series, used_topics=state["used_topics"])
        if fresh["queue"][:1] == [topic]:
            fresh["queue"].pop(0)
        walk_episodes.save_state(fresh)
        state = fresh
        logger.info("new series: %s", topic["title"])
    plan = json.loads(Path(series["plan"]).read_text())
    script_path = write_episode(plan, series["next_episode"], state["firm_days"])
    script = json.loads(script_path.read_text())
    total = sum(script["token_counts"].values())
    outside = script["token_counts"].get("unmapped", 0) + script["token_counts"].get("outside", 0)
    if outside > total * MAX_NOT_ALLOWED_SHARE:
        raise RuntimeError(f"{outside} of {total} words are outside the allowed vocabulary; not voicing {script_path}")
    replay = state.get("last_script")
    asyncio.run(voice_episode(script_path, Path(replay) if replay and Path(replay).exists() else None))
    fresh = walk_episodes.load_state()
    fresh["last_script"] = str(script_path)
    fresh["series"] = dict(series, next_episode=series["next_episode"] + 1)
    walk_episodes.save_state(fresh)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser("write")
    write.add_argument("--plan", required=True)
    write.add_argument("--episode", type=int, required=True)
    write.add_argument("--firm-days", type=float, default=FIRM_STABILITY_DAYS)
    voice = sub.add_parser("voice")
    voice.add_argument("--script", required=True)
    voice.add_argument("--replay", help="script file of the previous episode; its opening sections are replayed at the end")
    voice.add_argument("--replay-sections", type=int, default=3, help="how many sections of the previous episode to replay")
    voice.add_argument("--name", help="output name (default: the script's name)")
    voice.add_argument("--max-turns", type=int, help="voice only the first N turns (for a sample)")
    daily = sub.add_parser("daily", help="make today's episode from the topic queue if the decision rule allows it")
    daily.add_argument("--force", action="store_true", help="ignore the decision rule")
    args = parser.parse_args()
    if args.command == "write":
        write_episode(json.loads(Path(args.plan).read_text()), args.episode, args.firm_days)
    elif args.command == "voice":
        asyncio.run(voice_episode(args.script, args.replay, args.replay_sections, args.name, args.max_turns))
    else:
        cmd_daily(args)


if __name__ == "__main__":
    sys.exit(main())
