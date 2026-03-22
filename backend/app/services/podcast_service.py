"""Podcast generation service.

Generates language learning podcast episodes by:
1. Querying the learner's word knowledge and available sentences
2. Planning segments in various pedagogical formats
3. Generating TTS audio for each segment (Arabic + English)
4. Stitching segments into a single MP3 file with pydub
"""

import hashlib
import io
import json
import logging
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import httpx
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.models import (
    Lemma,
    Root,
    Sentence,
    SentenceWord,
    Story,
    StoryWord,
    UserLemmaKnowledge,
)

logger = logging.getLogger(__name__)

PODCAST_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "podcasts"
SEGMENT_CACHE_DIR = PODCAST_DIR / "segments"

ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"
DEFAULT_MODEL = "eleven_multilingual_v2"
ARABIC_VOICE_ID = "G1HOkzin3NMwRHSq60UI"  # Chaouki — MSA male
ENGLISH_VOICE_ID = "G1HOkzin3NMwRHSq60UI"  # Same voice for teacher feel

ARABIC_VOICE_SETTINGS = {
    "stability": 0.85,
    "similarity_boost": 0.75,
    "style": 0.0,
    "speed": 0.75,
    "use_speaker_boost": True,
}

ENGLISH_VOICE_SETTINGS = {
    "stability": 0.80,
    "similarity_boost": 0.75,
    "style": 0.15,
    "speed": 1.0,
    "use_speaker_boost": True,
}


@dataclass
class Seg:
    """A single audio segment in a podcast."""

    lang: Literal["ar", "en", "silence"]
    text: str = ""
    speed: float = 1.0
    slow_mode: bool = False
    duration_ms: int = 0  # for silence only
    label: str = ""  # debug label


def silence(ms: int, label: str = "") -> Seg:
    return Seg(lang="silence", duration_ms=ms, label=label or f"silence_{ms}ms")


def en(text: str, speed: float = 1.0) -> Seg:
    return Seg(lang="en", text=text, speed=speed, label=text[:40])


def ar(text: str, speed: float = 0.75, slow_mode: bool = False) -> Seg:
    return Seg(lang="ar", text=text, speed=speed, slow_mode=slow_mode, label=text[:40])


def ar_slow(text: str) -> Seg:
    return ar(text, speed=0.7, slow_mode=True)


def ar_normal(text: str) -> Seg:
    return ar(text, speed=0.9)


def _get_api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY not set")
    return key


def _cache_key(text: str, lang: str, speed: float, slow_mode: bool) -> str:
    raw = f"{text}|{lang}|{speed}|{slow_mode}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _add_learner_pauses(text: str) -> str:
    words = text.split()
    if len(words) <= 2:
        return text
    parts = []
    for i, w in enumerate(words):
        parts.append(w)
        if i < len(words) - 1 and (i + 1) % 2 == 0:
            parts.append("،")
    return " ".join(parts)


async def generate_tts(seg: Seg) -> bytes:
    """Generate TTS audio for a single segment, with caching."""
    from pydub import AudioSegment as PydubSegment

    if seg.lang == "silence":
        silent = PydubSegment.silent(duration=seg.duration_ms)
        buf = io.BytesIO()
        silent.export(buf, format="mp3")
        return buf.getvalue()

    SEGMENT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ck = _cache_key(seg.text, seg.lang, seg.speed, seg.slow_mode)
    cache_path = SEGMENT_CACHE_DIR / f"{ck}.mp3"
    if cache_path.exists():
        return cache_path.read_bytes()

    api_key = _get_api_key()
    voice_id = ARABIC_VOICE_ID if seg.lang == "ar" else ENGLISH_VOICE_ID
    settings = dict(ARABIC_VOICE_SETTINGS if seg.lang == "ar" else ENGLISH_VOICE_SETTINGS)
    settings["speed"] = seg.speed

    text = seg.text
    if seg.slow_mode and seg.lang == "ar":
        text = _add_learner_pauses(text)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ELEVENLABS_BASE_URL}/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={
                "text": text,
                "model_id": DEFAULT_MODEL,
                "language_code": seg.lang,
                "apply_text_normalization": "on",
                "voice_settings": settings,
            },
            timeout=60.0,
        )

    if resp.status_code != 200:
        logger.error("TTS failed for %r: %s %s", seg.label, resp.status_code, resp.text[:200])
        raise RuntimeError(f"TTS failed: {resp.status_code}")

    cache_path.write_bytes(resp.content)
    return resp.content


async def stitch_podcast(segments: list[Seg], output_name: str) -> Path:
    """Generate TTS for all segments and stitch into a single MP3."""
    from pydub import AudioSegment as PydubSegment

    PODCAST_DIR.mkdir(parents=True, exist_ok=True)

    final = PydubSegment.empty()
    total = len(segments)

    for i, seg in enumerate(segments):
        logger.info("[%d/%d] Generating: %s (%s)", i + 1, total, seg.label, seg.lang)
        audio_bytes = await generate_tts(seg)
        clip = PydubSegment.from_mp3(io.BytesIO(audio_bytes))
        final += clip

    output_path = PODCAST_DIR / f"{output_name}.mp3"
    final.export(str(output_path), format="mp3", bitrate="128k")

    duration_s = len(final) / 1000
    logger.info("Podcast saved: %s (%.1f min, %.1f MB)",
                output_path.name, duration_s / 60, output_path.stat().st_size / 1e6)
    return output_path


# ── Data queries ──────────────────────────────────────────────────────


@dataclass
class SentenceInfo:
    id: int
    arabic: str
    english: str
    target_word_ar: str
    target_gloss: str
    knowledge_state: str
    root_ar: Optional[str] = None


def _get_known_sentences(db: Session, limit: int = 40) -> list[SentenceInfo]:
    """Get active sentences with known/learning target words."""
    rows = (
        db.query(Sentence, Lemma, UserLemmaKnowledge, Root)
        .join(Lemma, Sentence.target_lemma_id == Lemma.lemma_id)
        .join(UserLemmaKnowledge, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .outerjoin(Root, Lemma.root_id == Root.root_id)
        .filter(
            Sentence.is_active == True,  # noqa: E712
            Sentence.english_translation.isnot(None),
            Sentence.arabic_diacritized.isnot(None),
            UserLemmaKnowledge.knowledge_state.in_(["learning", "known"]),
        )
        .order_by(sa_func.random())
        .limit(limit)
        .all()
    )
    results = []
    for sent, lemma, knowledge, root in rows:
        results.append(SentenceInfo(
            id=sent.id,
            arabic=sent.arabic_diacritized or sent.arabic_text,
            english=sent.english_translation,
            target_word_ar=lemma.lemma_ar,
            target_gloss=lemma.gloss_en or "",
            knowledge_state=knowledge.knowledge_state,
            root_ar=root.root if root else None,
        ))
    return results


def _get_acquiring_sentences(db: Session, limit: int = 10) -> list[SentenceInfo]:
    """Get sentences for words currently being acquired (newer words)."""
    rows = (
        db.query(Sentence, Lemma, UserLemmaKnowledge, Root)
        .join(Lemma, Sentence.target_lemma_id == Lemma.lemma_id)
        .join(UserLemmaKnowledge, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .outerjoin(Root, Lemma.root_id == Root.root_id)
        .filter(
            Sentence.is_active == True,  # noqa: E712
            Sentence.english_translation.isnot(None),
            Sentence.arabic_diacritized.isnot(None),
            UserLemmaKnowledge.knowledge_state == "acquiring",
        )
        .order_by(sa_func.random())
        .limit(limit)
        .all()
    )
    results = []
    for sent, lemma, knowledge, root in rows:
        results.append(SentenceInfo(
            id=sent.id,
            arabic=sent.arabic_diacritized or sent.arabic_text,
            english=sent.english_translation,
            target_word_ar=lemma.lemma_ar,
            target_gloss=lemma.gloss_en or "",
            knowledge_state=knowledge.knowledge_state,
            root_ar=root.root if root else None,
        ))
    return results


@dataclass
class RootFamily:
    root_ar: str
    core_meaning: str
    words: list[tuple[str, str]]  # (arabic, gloss)
    sentences: list[SentenceInfo] = field(default_factory=list)


def _get_root_family(db: Session) -> Optional[RootFamily]:
    """Find a root with 3+ known lemmas and their sentences."""
    root_counts = (
        db.query(Root.root_id, Root.root, Root.core_meaning_en, sa_func.count(Lemma.lemma_id))
        .join(Lemma)
        .join(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.knowledge_state.in_(["learning", "known", "acquiring"]))
        .group_by(Root.root_id)
        .having(sa_func.count(Lemma.lemma_id) >= 3)
        .order_by(sa_func.random())
        .first()
    )
    if not root_counts:
        return None

    root_id, root_ar, core_meaning, _ = root_counts

    lemmas = (
        db.query(Lemma)
        .join(UserLemmaKnowledge)
        .filter(
            Lemma.root_id == root_id,
            UserLemmaKnowledge.knowledge_state.in_(["learning", "known", "acquiring"]),
        )
        .limit(5)
        .all()
    )

    words = [(l.lemma_ar, l.gloss_en or "") for l in lemmas]

    # Get one sentence per lemma
    sents = []
    for lemma in lemmas[:3]:
        sent_row = (
            db.query(Sentence)
            .filter(
                Sentence.target_lemma_id == lemma.lemma_id,
                Sentence.is_active == True,  # noqa: E712
                Sentence.arabic_diacritized.isnot(None),
                Sentence.english_translation.isnot(None),
            )
            .first()
        )
        if sent_row:
            sents.append(SentenceInfo(
                id=sent_row.id,
                arabic=sent_row.arabic_diacritized or sent_row.arabic_text,
                english=sent_row.english_translation,
                target_word_ar=lemma.lemma_ar,
                target_gloss=lemma.gloss_en or "",
                knowledge_state="known",
                root_ar=root_ar,
            ))

    return RootFamily(root_ar=root_ar, core_meaning=core_meaning or "", words=words, sentences=sents)


def _get_story_sentences(db: Session) -> list[SentenceInfo]:
    """Get sentences from a completed/active story."""
    story = (
        db.query(Story)
        .filter(Story.status.in_(["active", "completed"]))
        .order_by(sa_func.random())
        .first()
    )
    if not story:
        return []

    story_sents = (
        db.query(Sentence)
        .filter(
            Sentence.story_id == story.id,
            Sentence.is_active == True,  # noqa: E712
            Sentence.arabic_diacritized.isnot(None),
            Sentence.english_translation.isnot(None),
        )
        .order_by(Sentence.id)
        .limit(8)
        .all()
    )
    results = []
    for s in story_sents:
        # Get target lemma info
        target_lemma = db.query(Lemma).filter(Lemma.lemma_id == s.target_lemma_id).first() if s.target_lemma_id else None
        results.append(SentenceInfo(
            id=s.id,
            arabic=s.arabic_diacritized or s.arabic_text,
            english=s.english_translation,
            target_word_ar=target_lemma.lemma_ar if target_lemma else "",
            target_gloss=target_lemma.gloss_en if target_lemma else "",
            knowledge_state="known",
        ))
    return results


# ── Format builders ───────────────────────────────────────────────────


def _build_intro(segments: list[Seg]) -> None:
    segments.extend([
        en("Welcome to your Alif listening practice. This is a sampler of six different podcast formats. "
           "Just listen, no need to touch your phone. Notice which formats feel most useful to you."),
        silence(2000),
    ])


def _build_sentence_drill(segments: list[Seg], sentences: list[SentenceInfo]) -> None:
    """Format 1: Sentence drill — Arabic → English → Arabic, then recall pass."""
    sents = sentences[:4]
    if len(sents) < 2:
        return

    segments.extend([
        en("Format one. Sentence Drill. You'll hear an Arabic sentence, then its English meaning, "
           "then the Arabic again."),
        silence(2500),
    ])

    # Introduction pass
    for s in sents:
        segments.extend([
            ar_slow(s.arabic),
            silence(2000),
            en(s.english),
            silence(1500),
            ar_normal(s.arabic),
            silence(2500),
        ])

    # Echo pass: all Arabic back to back
    segments.append(en("Now all four sentences in Arabic."))
    segments.append(silence(1500))
    for s in sents:
        segments.extend([
            ar_normal(s.arabic),
            silence(2500),
        ])

    # Recall pass: English first, pause, then Arabic
    segments.append(en("Now try to recall the Arabic before you hear it."))
    segments.append(silence(1500))
    for s in sents:
        segments.extend([
            en(s.english),
            silence(4000),
            ar_normal(s.arabic),
            silence(2000),
        ])

    segments.append(silence(2000))


def _build_story_breakdown(segments: list[Seg], story_sents: list[SentenceInfo]) -> None:
    """Format 2: Story breakdown — teach each sentence, replay growing sequence."""
    sents = story_sents[:5]
    if len(sents) < 3:
        return

    segments.extend([
        en("Format two. Story Breakdown. We'll learn a short story sentence by sentence, "
           "building up until you can follow the whole thing in Arabic."),
        silence(2500),
    ])

    # Teach each sentence
    taught = []
    for i, s in enumerate(sents):
        segments.extend([
            en(s.english),
            silence(1000),
            ar_slow(s.arabic),
            silence(1500),
            ar_normal(s.arabic),
            silence(2000),
        ])
        taught.append(s)

        # After every 2-3 sentences, replay the sequence
        if (i + 1) % 2 == 0 and i > 0:
            segments.append(en(f"Let's hear sentences one through {i + 1} together."))
            segments.append(silence(1000))
            for t in taught:
                segments.extend([ar_normal(t.arabic), silence(1500)])
            segments.append(silence(1000))

    # Full story replay
    segments.append(en("Now the full story in Arabic, no interruptions."))
    segments.append(silence(2000))
    for s in sents:
        segments.extend([ar_normal(s.arabic), silence(1500)])

    # Bilingual replay
    segments.append(silence(1500))
    segments.append(en("Once more, with English after each sentence."))
    segments.append(silence(1500))
    for s in sents:
        segments.extend([
            ar_normal(s.arabic),
            silence(1500),
            en(s.english),
            silence(2000),
        ])

    segments.append(silence(2000))


def _build_comprehensible_input(segments: list[Seg], sentences: list[SentenceInfo]) -> None:
    """Format 3: Comprehensible input — mostly Arabic with inline English glosses."""
    sents = sentences[:5]
    if len(sents) < 2:
        return

    segments.extend([
        en("Format three. Comprehensible Input. Mostly Arabic now. "
           "I'll give brief English hints for key words, then the full Arabic sentence."),
        silence(2500),
    ])

    for s in sents:
        # Pattern: target word → English gloss → full sentence
        segments.extend([
            ar(s.target_word_ar, speed=0.8),
            silence(800),
            en(s.target_gloss, speed=1.0),
            silence(1000),
            ar_normal(s.arabic),
            silence(1200),
            ar_normal(s.arabic),
            silence(2500),
        ])

    # All sentences once more, no English
    segments.append(en("All sentences again, Arabic only."))
    segments.append(silence(1500))
    for s in sents:
        segments.extend([ar_normal(s.arabic), silence(2000)])

    segments.append(silence(2000))


def _build_root_explorer(segments: list[Seg], root_family: Optional[RootFamily]) -> None:
    """Format 4: Root explorer — explore a root family."""
    if not root_family or len(root_family.words) < 2:
        return

    rf = root_family
    root_display = rf.root_ar.replace(".", " ")

    segments.extend([
        en(f"Format four. Root Explorer. Let's explore the Arabic root {root_display}, "
           f"which has to do with {rf.core_meaning}."),
        silence(2000),
    ])

    for word_ar, gloss in rf.words:
        segments.extend([
            ar(word_ar, speed=0.8),
            silence(800),
            en(gloss),
            silence(1500),
        ])

    # Sentences using these words
    if rf.sentences:
        segments.append(en("Now listen for these words in sentences."))
        segments.append(silence(1500))
        for s in rf.sentences:
            segments.extend([
                en(f"The word {s.target_gloss}."),
                silence(800),
                ar_slow(s.arabic),
                silence(1500),
                en(s.english),
                silence(1000),
                ar_normal(s.arabic),
                silence(2500),
            ])

    segments.append(silence(2000))


def _build_review_reinforce(segments: list[Seg], sentences: list[SentenceInfo]) -> None:
    """Format 5: Review — sentences from recently studied words."""
    sents = sentences[:5]
    if len(sents) < 2:
        return

    segments.extend([
        en("Format five. Review and Reinforce. These are sentences with words you've been studying. "
           "See how much you can understand in Arabic before the English."),
        silence(2500),
    ])

    for s in sents:
        segments.extend([
            ar_normal(s.arabic),
            silence(3500),
            en(s.english),
            silence(2000),
        ])

    # Speed round
    segments.append(en("Quick round. Same sentences, less pause."))
    segments.append(silence(1500))
    for s in sents:
        segments.extend([
            ar_normal(s.arabic),
            silence(2000),
            en(s.english),
            silence(1000),
        ])

    segments.append(silence(2000))


def _build_dialogue(segments: list[Seg], sentences: list[SentenceInfo]) -> None:
    """Format 6: Pseudo-dialogue — pairs of sentences as exchanges."""
    sents = sentences[:4]
    if len(sents) < 4:
        return

    segments.extend([
        en("Format six. Dialogue. Imagine two people talking. "
           "I'll set the scene, then you'll hear each exchange."),
        silence(2000),
        en("Ahmad and Layla are making plans for the weekend."),
        silence(1500),
    ])

    pairs = [(sents[0], sents[1]), (sents[2], sents[3])]
    taught_lines = []

    for i, (a, b) in enumerate(pairs):
        segments.extend([
            en("Ahmad says:"),
            silence(500),
            ar_slow(a.arabic),
            silence(1500),
            en(a.english),
            silence(1000),
            ar_normal(a.arabic),
            silence(1500),
            en("Layla responds:"),
            silence(500),
            ar_slow(b.arabic),
            silence(1500),
            en(b.english),
            silence(1000),
            ar_normal(b.arabic),
            silence(2000),
        ])
        taught_lines.extend([a, b])

        if i == 0:
            segments.append(en("The exchange so far, Arabic only."))
            segments.append(silence(1000))
            for t in taught_lines:
                segments.extend([ar_normal(t.arabic), silence(1500)])
            segments.append(silence(1000))

    # Full conversation replay
    segments.append(en("The full conversation in Arabic."))
    segments.append(silence(1500))
    for t in taught_lines:
        segments.extend([ar_normal(t.arabic), silence(1500)])

    segments.append(silence(2000))


def _build_closing(segments: list[Seg]) -> None:
    segments.extend([
        en("That's all six formats. Think about which ones felt most engaging, "
           "which helped you understand the most, and which you'd want for a full thirty minute episode. "
           "Happy learning."),
        silence(2000),
    ])


def plan_sampler(db: Session) -> list[Seg]:
    """Plan all segments for the sampler episode."""
    logger.info("Planning sampler — querying DB for content...")

    known_sents = _get_known_sentences(db, limit=40)
    acquiring_sents = _get_acquiring_sentences(db, limit=10)
    root_family = _get_root_family(db)
    story_sents = _get_story_sentences(db)

    logger.info("Found: %d known sents, %d acquiring sents, root=%s, %d story sents",
                len(known_sents), len(acquiring_sents),
                root_family.root_ar if root_family else "None", len(story_sents))

    random.shuffle(known_sents)
    random.shuffle(acquiring_sents)

    segments: list[Seg] = []

    _build_intro(segments)

    # Sentence Drill: use 4 known sentences
    _build_sentence_drill(segments, known_sents[:4])

    # Story Breakdown: use story sentences or fallback to known
    story_pool = story_sents if len(story_sents) >= 3 else known_sents[4:9]
    _build_story_breakdown(segments, story_pool)

    # Comprehensible Input: use acquiring sentences (newer words)
    ci_pool = acquiring_sents if len(acquiring_sents) >= 3 else known_sents[9:14]
    _build_comprehensible_input(segments, ci_pool)

    # Root Explorer
    _build_root_explorer(segments, root_family)

    # Review: use different known sentences
    _build_review_reinforce(segments, known_sents[14:19])

    # Dialogue: use different known sentences
    _build_dialogue(segments, known_sents[19:23])

    _build_closing(segments)

    # Count stats
    ar_chars = sum(len(s.text) for s in segments if s.lang == "ar")
    en_chars = sum(len(s.text) for s in segments if s.lang == "en")
    n_tts = sum(1 for s in segments if s.lang != "silence")
    logger.info("Plan: %d segments (%d TTS calls), ~%d AR chars, ~%d EN chars",
                len(segments), n_tts, ar_chars, en_chars)

    return segments


def list_podcasts() -> list[dict]:
    """List all generated podcast files."""
    PODCAST_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for f in sorted(PODCAST_DIR.glob("*.mp3")):
        if f.parent == PODCAST_DIR:  # exclude segments subdir
            stat = f.stat()
            results.append({
                "filename": f.name,
                "size_mb": round(stat.st_size / 1e6, 1),
                "created_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
    return results


def get_podcast_path(filename: str) -> Optional[Path]:
    """Get path to a podcast file, validating it exists."""
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    path = PODCAST_DIR / filename
    if path.exists() and path.parent == PODCAST_DIR:
        return path
    return None
