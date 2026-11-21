#!/usr/bin/env python3
"""Bridge episodes: authentic Arabic audio with a spoken bridge, for hands-off listening.

Takes a real recording (podcast, audiobook) and its word-timed transcript and
builds an audio-only study episode. For every fragment of about ten to twenty
words the listener hears:

    the authentic fragment
    its hard words, each with an English meaning
    the same content retold in simple Arabic from the learner's firm words
    the authentic fragment again

After every few fragments the whole stretch plays without interruption, and
the complete excerpt plays once more at the end.

Hard words are the fragment's tokens that the running-text mapper does not
resolve to a firm word. The simple retelling is checked with the same
verifier as walk episodes. Read-only against the database.

    python3 scripts/generate_bridge_episode.py prepare --tokens soniox.json \
        --start 40 --end 250 --slug rumooz-ibn-khaldun --title-en "..." --title-ar "..." --source "..."
    ALIF_AUDIO_ENABLED=1 python3 scripts/generate_bridge_episode.py voice \
        --script data/podcasts/bridge-scripts/<name>.json --audio source.mp3

The source recording is third-party content for private study: keep it and the
episode out of Git.
"""

import argparse
import asyncio
import io
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from generate_walk_episode import (  # noqa: E402
    FIRM_STABILITY_DAYS,
    VOICES,
    Verifier,
    _name_key,
    _vocab_block,
    bad_tokens,
    load_vocabulary,
    pausal,
    speak,
)

from app.database import SessionLocal  # noqa: E402
from app.services.podcast_service import PODCAST_DIR, save_metadata  # noqa: E402

logger = logging.getLogger("bridge")

SCRIPT_DIR = PODCAST_DIR / "bridge-scripts"
MIN_WORDS = 5
SOFT_MAX_WORDS = 16
HARD_MAX_WORDS = 24
BATCH = 6
BLOCK = 5
MAX_KEY_WORDS = 3
MAX_REWRITE_ROUNDS = 2

_SENTENCE_END = re.compile(r"[.!?؟…]$")
_CLAUSE_END = re.compile(r"[،,؛:]$")

FRAGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "fragments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "ar": {"type": "string"},
                    "en": {"type": "string"},
                    "key_words": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"ar": {"type": "string"}, "en": {"type": "string"}},
                            "required": ["ar", "en"],
                        },
                    },
                    "simple_ar": {"type": "string"},
                    "simple_en": {"type": "string"},
                },
                "required": ["index", "ar", "en", "key_words", "simple_ar", "simple_en"],
            },
        },
        "names": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["fragments", "names"],
}

REWRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "fragments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "simple_ar": {"type": "string"},
                    "simple_en": {"type": "string"},
                },
                "required": ["index", "simple_ar", "simple_en"],
            },
        },
    },
    "required": ["fragments"],
}


# ── Fragments from the transcript ────────────────────────────────────


def words_from_tokens(tokens: list[dict]) -> list[dict]:
    """Join Soniox sub-word tokens into words with start and end times (ms)."""
    words: list[dict] = []
    for token in tokens:
        text = token.get("text") or ""
        if not text.strip():
            continue
        if text.startswith(" ") or not words:
            words.append({"text": text.strip(), "start": token["start_ms"], "end": token["end_ms"]})
        else:
            words[-1]["text"] += text
            words[-1]["end"] = max(words[-1]["end"], token["end_ms"])
    return [w for w in words if w["text"]]


def split_fragments(words: list[dict]) -> list[list[dict]]:
    """Cut at sentence ends; cut long sentences at a clause mark or a pause."""
    fragments: list[list[dict]] = []
    current: list[dict] = []
    for i, word in enumerate(words):
        current.append(word)
        gap = words[i + 1]["start"] - word["end"] if i + 1 < len(words) else 10_000
        sentence_end = bool(_SENTENCE_END.search(word["text"]))
        soft = len(current) >= SOFT_MAX_WORDS and (bool(_CLAUSE_END.search(word["text"])) or gap >= 400)
        if (sentence_end and len(current) >= MIN_WORDS) or soft or len(current) >= HARD_MAX_WORDS:
            fragments.append(current)
            current = []
    if current:
        if fragments and len(current) < MIN_WORDS:
            fragments[-1].extend(current)
        else:
            fragments.append(current)
    return fragments


# ── Writing the bridge ───────────────────────────────────────────────

SYSTEM_PROMPT = """You prepare listening material for one adult learner of Arabic. He reads Modern \
Standard Arabic and knows about two thousand words well, but real spoken Arabic is still too fast and \
too rich for him. You are given fragments of an authentic recording. For each fragment you give the \
exact text with vowel marks, an English translation, the few words he most needs explained, and a \
retelling of the same content in simple Arabic that uses only his vocabulary list."""


def _bridge_prompt(batch: list[dict], context: str, firm) -> str:
    listing = "\n".join(
        f"[{f['index']}] {f['raw']}\n    words outside his vocabulary: {'، '.join(f['hard']) or '(none)'}" for f in batch
    )
    return f"""The recording so far said:
{context or '(this is the beginning)'}

FRAGMENTS (speech-recognition text, may contain small errors):
{listing}

For each fragment return:
- index
- ar: the fragment's own words, unchanged in wording, with recognition errors corrected and full vowel marks.
- en: a faithful English translation.
- key_words: at most {MAX_KEY_WORDS} words or short phrases from the fragment that carry its meaning and \
that he probably does not know, chosen first from the "outside his vocabulary" list. Give each in its \
dictionary form with vowel marks ("ar") and a short English meaning of two to five words ("en"). An empty \
list is right when the fragment is easy.
- simple_ar: the same content retold in simple spoken Modern Standard Arabic, in one to three short \
sentences of at most fourteen words, fully vowelled. Keep every fact and the speaker's point of view \
(first person stays first person). Every noun, verb, adjective and adverb must be an inflected form of a \
word in the VOCABULARY list; grammar particles, pronouns and prepositions are free; proper names may be \
kept. Where the original uses a rare or ornate word, say what it means with common words. No digits.
- simple_en: an English translation of simple_ar.
List every proper name you used (without vowel marks) in "names".

VOCABULARY (Arabic = meaning):
{_vocab_block(firm)}"""


def _generate(prompt: str, schema: dict, task: str) -> dict:
    from app.services.llm import generate_completion

    return generate_completion(
        prompt,
        system_prompt=SYSTEM_PROMPT,
        json_schema=schema,
        model_override=os.environ.get("ALIF_WALK_WRITER", "claude_sonnet"),
        timeout=600,
        task_type=task,
    )


def cmd_prepare(args) -> None:
    transcript = json.loads(Path(args.tokens).read_text())
    words = [
        w
        for w in words_from_tokens(transcript["tokens"])
        if w["start"] >= args.start * 1000 and (not args.end or w["start"] < args.end * 1000)
    ]
    raw_fragments = split_fragments(words)
    fragments = []
    for i, group in enumerate(raw_fragments):
        following = raw_fragments[i + 1][0]["start"] if i + 1 < len(raw_fragments) else group[-1]["end"] + 600
        fragments.append(
            {
                "index": i,
                "raw": " ".join(w["text"] for w in group),
                "start_ms": max(0, group[0]["start"] - 150),
                "end_ms": max(group[-1]["end"] + 120, min(group[-1]["end"] + 450, following - 80)),
            }
        )
    logger.info("%d words in %d fragments", len(words), len(fragments))

    db = SessionLocal()
    try:
        firm, _ = load_vocabulary(db, args.firm_days)
        verifier = Verifier(db, firm, [])
        names: set[str] = set()
        for fragment in fragments:
            checked = verifier.check(fragment["raw"], names)
            fragment["hard"] = list(dict.fromkeys(bad_tokens(checked)))
            fragment["known_share"] = round(1 - len(bad_tokens(checked)) / max(1, len(checked)), 2)
        for offset in range(0, len(fragments), BATCH):
            batch = fragments[offset : offset + BATCH]
            context = " ".join(f["raw"] for f in fragments[max(0, offset - 3) : offset])
            result = _generate(_bridge_prompt(batch, context, firm), FRAGMENT_SCHEMA, "bridge_episode_write")
            names |= {_name_key(n) for n in result.get("names", [])}
            by_index = {item["index"]: item for item in result["fragments"]}
            for fragment in batch:
                item = by_index.get(fragment["index"])
                if not item:
                    raise RuntimeError(f"fragment {fragment['index']} missing from the writer's answer")
                fragment.update(
                    ar=item["ar"],
                    en=item["en"],
                    key_words=item["key_words"][:MAX_KEY_WORDS],
                    simple_ar=item["simple_ar"],
                    simple_en=item["simple_en"],
                )
            for round_no in range(MAX_REWRITE_ROUNDS):
                problems = {f["index"]: bad_tokens(verifier.check(f["simple_ar"], names)) for f in batch}
                problems = {i: w for i, w in problems.items() if w}
                if not problems:
                    break
                logger.info("fragments %d-%d round %d: %d retellings to rewrite", offset, offset + len(batch) - 1, round_no + 1, len(problems))
                listing = "\n".join(
                    f"[{f['index']}] original: {f['ar']}\n    retelling: {f['simple_ar']}\n    words not allowed: {'، '.join(problems[f['index']])}"
                    for f in batch
                    if f["index"] in problems
                )
                fixed = _generate(
                    "These simple retellings use words that are not in the listener's vocabulary. Rewrite each "
                    "retelling so it keeps the content of the original but replaces the words that are not allowed "
                    "with words from the VOCABULARY list (proper names may stay). Fully vowelled, short sentences.\n\n"
                    f"{listing}\n\nVOCABULARY (Arabic = meaning):\n{_vocab_block(firm)}",
                    REWRITE_SCHEMA,
                    "bridge_episode_rewrite",
                )
                for item in fixed.get("fragments", []):
                    if item.get("index") in problems:
                        fragments[item["index"]].update(simple_ar=item["simple_ar"], simple_en=item["simple_en"])
            for fragment in batch:
                fragment["simple_not_allowed"] = bad_tokens(verifier.check(fragment["simple_ar"], names))
            logger.info("fragments %d-%d done", offset, offset + len(batch) - 1)
    finally:
        db.close()

    name = f"bridge-{args.slug}-{datetime.now().strftime('%Y%m%d-%H%M')}"
    script = {
        "name": name,
        "slug": args.slug,
        "title_en": args.title_en,
        "title_ar": args.title_ar,
        "source": args.source,
        "firm_days": args.firm_days,
        "fragments": fragments,
        "names": sorted(names),
    }
    SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCRIPT_DIR / f"{name}.json"
    path.write_text(json.dumps(script, ensure_ascii=False, indent=1))
    share = sum(f["known_share"] for f in fragments) / len(fragments)
    leftovers = sorted({w for f in fragments for w in f["simple_not_allowed"]})
    logger.info("script %s: source words known %.0f%%, key words %d", path, share * 100, sum(len(f["key_words"]) for f in fragments))
    logger.info("retelling words not allowed (%d): %s", len(leftovers), " ".join(leftovers))


# ── Voicing ──────────────────────────────────────────────────────────

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?؟…])\s+")


async def cmd_voice(args) -> None:
    import httpx
    from pydub import AudioSegment

    script = json.loads(Path(args.script).read_text())
    fragments = script["fragments"]
    source = AudioSegment.from_file(args.audio)

    def clip(start_ms: int, end_ms: int):
        return source[start_ms:end_ms].fade_in(40).fade_out(60)

    async with httpx.AsyncClient() as client:

        async def say(text: str, voice: str, lang: str):
            if lang == "ar":
                parts = [pausal(s) for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]
            else:
                parts = [text]
            audio = AudioSegment.empty()
            for i, part in enumerate(parts):
                if i:
                    audio += AudioSegment.silent(350)
                audio += AudioSegment.from_mp3(io.BytesIO(await speak(client, part, VOICES[voice], lang)))
            return audio

        intro = (
            f"{args.intro} You will hear each part of the recording, then its hard words, "
            "then the same thing in simple Arabic, then the part again."
        )
        final = await say(intro, "B", "en") + AudioSegment.silent(1500)
        for i, fragment in enumerate(fragments):
            original = clip(fragment["start_ms"], fragment["end_ms"])
            final += original + AudioSegment.silent(700)
            for word in fragment["key_words"]:
                final += await say(word["ar"], "B", "ar") + AudioSegment.silent(250)
                final += await say(word["en"], "B", "en") + AudioSegment.silent(450)
            final += AudioSegment.silent(250) + await say(fragment["simple_ar"], "A", "ar")
            final += AudioSegment.silent(800) + original + AudioSegment.silent(1300)
            block_end = (i + 1) % BLOCK == 0 or i == len(fragments) - 1
            if block_end:
                first = fragments[i - (i % BLOCK)]
                final += AudioSegment.silent(600) + clip(first["start_ms"], fragment["end_ms"]) + AudioSegment.silent(1800)
            if i % 5 == 0:
                logger.info("fragment %d/%d, %.1f min so far", i + 1, len(fragments), len(final) / 60000)
        if not args.no_final_replay:
            final += await say("And now the whole recording, without stopping.", "B", "en") + AudioSegment.silent(1000)
            final += clip(fragments[0]["start_ms"], fragments[-1]["end_ms"])

    name = script["name"]
    output = PODCAST_DIR / f"{name}.mp3"
    final.export(str(output), format="mp3", bitrate="96k")
    sentences = []
    for fragment in fragments:
        sentences.append({"arabic": fragment["ar"], "english": fragment["en"]})
        sentences.append({"arabic": fragment["simple_ar"], "english": fragment["simple_en"]})
    key_words = [{"arabic": w["ar"], "gloss": w["en"]} for f in fragments for w in f["key_words"]]
    save_metadata(
        name,
        {
            "title_en": script["title_en"],
            "title_ar": script["title_ar"],
            "theme_id": f"bridge-{script['slug']}",
            "format_type": "bridge",
            "summary": f"{args.intro} Source: {script['source']}",
            "sentences": sentences,
            "key_words": key_words,
            "word_lemma_ids": [],
            "source": script["source"],
            "source_seconds": round((fragments[-1]["end_ms"] - fragments[0]["start_ms"]) / 1000),
            "duration_seconds": int(len(final) / 1000),
            "generated_at": datetime.now().isoformat(),
            "listened_at": None,
            "listen_progress": 0,
        },
    )
    logger.info("saved %s: %.1f min from %.1f min of source", output, len(final) / 60000, (fragments[-1]["end_ms"] - fragments[0]["start_ms"]) / 60000)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--tokens", required=True, help="Soniox transcript JSON with word-timed tokens")
    prepare.add_argument("--start", type=float, default=0, help="excerpt start in seconds")
    prepare.add_argument("--end", type=float, default=0, help="excerpt end in seconds (0 = to the end)")
    prepare.add_argument("--slug", required=True)
    prepare.add_argument("--title-en", required=True)
    prepare.add_argument("--title-ar", required=True)
    prepare.add_argument("--source", required=True, help="what the recording is and where it came from")
    prepare.add_argument("--firm-days", type=float, default=FIRM_STABILITY_DAYS)
    voice = sub.add_parser("voice")
    voice.add_argument("--script", required=True)
    voice.add_argument("--audio", required=True, help="the source recording the transcript was made from")
    voice.add_argument("--intro", required=True, help="one English sentence that opens the episode")
    voice.add_argument("--no-final-replay", action="store_true")
    args = parser.parse_args()
    if args.command == "prepare":
        cmd_prepare(args)
    else:
        asyncio.run(cmd_voice(args))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    sys.exit(main())
