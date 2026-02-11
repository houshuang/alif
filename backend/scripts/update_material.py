#!/usr/bin/env python3
"""Unified periodic update: backfill sentences, generate audio, pre-generate for upcoming words.

Designed to run as a cron job every 6 hours inside the Docker container.

Steps:
  A) Backfill sentences for introduced words (< 3 sentences each)
  B) Generate audio for review-eligible sentences (all words reviewed ≥1 time)
  C) Pre-generate sentences for top upcoming word candidates (no audio)

Usage:
    python scripts/update_material.py                  # full run
    python scripts/update_material.py --dry-run        # preview only
    python scripts/update_material.py --skip-audio     # skip TTS generation
    python scripts/update_material.py --limit 20       # max 20 audio generations
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.word_selector import select_next_words
from app.services.llm import AllProvidersFailed, generate_sentences_batch
from app.services.sentence_generator import (
    get_content_word_counts,
    get_avoid_words,
    sample_known_words_weighted,
    KNOWN_SAMPLE_SIZE,
)
from app.services.sentence_validator import (
    build_lemma_lookup,
    map_tokens_to_lemmas,
    strip_diacritics,
    tokenize,
    validate_sentence,
)
from app.services.tts import (
    DEFAULT_VOICE_ID,
    TTSError,
    TTSKeyMissing,
    cache_key_for,
    generate_and_cache,
    get_cached_path,
)

MIN_SENTENCES = 3


def get_existing_counts(db: Session) -> dict[int, int]:
    rows = (
        db.query(Sentence.target_lemma_id, func.count(Sentence.id))
        .filter(
            Sentence.target_lemma_id.isnot(None),
            Sentence.is_active == True,  # noqa: E712
        )
        .group_by(Sentence.target_lemma_id)
        .all()
    )
    return {lid: cnt for lid, cnt in rows}


def get_known_words_and_lookup(db: Session) -> tuple[list[dict[str, str]], dict[str, int]]:
    all_lemmas = (
        db.query(Lemma)
        .join(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.fsrs_card_json.isnot(None))
        .all()
    )
    known_words = [
        {"arabic": lem.lemma_ar, "english": lem.gloss_en or ""}
        for lem in all_lemmas
    ]
    lemma_lookup = build_lemma_lookup(all_lemmas)
    return known_words, lemma_lookup


def generate_sentences_for_word(
    db: Session,
    lemma: Lemma,
    known_words: list[dict[str, str]],
    lemma_lookup: dict[str, int],
    needed: int,
    model: str = "gemini",
    delay: float = 1.0,
    avoid_words: list[str] | None = None,
) -> int:
    target_bare = strip_diacritics(lemma.lemma_ar)
    all_bare = set(lemma_lookup.keys())
    stored = 0
    rejected_words: list[str] = []

    for batch in range(3):
        if stored >= needed:
            break
        if batch > 0 and delay > 0:
            time.sleep(delay)

        try:
            results = generate_sentences_batch(
                target_word=lemma.lemma_ar,
                target_translation=lemma.gloss_en or "",
                known_words=known_words,
                count=min(needed - stored + 1, 3),
                difficulty_hint="beginner",
                model_override=model,
                rejected_words=rejected_words if rejected_words else None,
                avoid_words=avoid_words,
            )
        except AllProvidersFailed as e:
            print(f"    LLM error: {e}")
            break

        for res in results:
            if stored >= needed:
                break

            validation = validate_sentence(
                arabic_text=res.arabic,
                target_bare=target_bare,
                known_bare_forms=all_bare,
            )
            if not validation.valid:
                for issue in validation.issues:
                    print(f"    ✗ Rejected: {issue}")
                for uw in validation.unknown_words:
                    bare = strip_diacritics(uw)
                    if bare not in rejected_words:
                        rejected_words.append(bare)
                continue

            sent = Sentence(
                arabic_text=res.arabic,
                arabic_diacritized=res.arabic,
                english_translation=res.english,
                transliteration=res.transliteration,
                source="llm",
                target_lemma_id=lemma.lemma_id,
            )
            db.add(sent)
            db.flush()

            tokens = tokenize(res.arabic)
            mappings = map_tokens_to_lemmas(
                tokens=tokens,
                lemma_lookup=lemma_lookup,
                target_lemma_id=lemma.lemma_id,
                target_bare=target_bare,
            )
            for m in mappings:
                sw = SentenceWord(
                    sentence_id=sent.id,
                    position=m.position,
                    surface_form=m.surface_form,
                    lemma_id=m.lemma_id,
                    is_target_word=m.is_target,
                )
                db.add(sw)

            stored += 1

    db.commit()
    return stored


# ── Step A: Backfill sentences for introduced words ──────────────────

def step_backfill_sentences(db: Session, dry_run: bool, model: str, delay: float) -> int:
    print("\n═══ Step A: Backfill sentences for introduced words ═══")

    introduced = (
        db.query(Lemma)
        .join(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.fsrs_card_json.isnot(None))
        .all()
    )
    if not introduced:
        print("  No introduced words found.")
        return 0

    existing_counts = get_existing_counts(db)
    known_words, lemma_lookup = get_known_words_and_lookup(db)

    # Diversity params — weight sampling and avoid overused words
    content_word_counts = get_content_word_counts(db)
    avoid_words = get_avoid_words(content_word_counts, known_words)

    total = 0
    for lemma in introduced:
        existing = existing_counts.get(lemma.lemma_id, 0)
        needed = MIN_SENTENCES - existing
        if needed <= 0:
            continue

        # Sample known words with diversity weighting per target
        word_sample = sample_known_words_weighted(
            known_words, content_word_counts, KNOWN_SAMPLE_SIZE,
            target_lemma_id=lemma.lemma_id,
        )

        print(f"  {lemma.lemma_ar} ({lemma.gloss_en}) — have {existing}, need {needed}")
        if dry_run:
            print(f"    [dry-run] Would generate {needed} sentences")
            total += needed
        else:
            stored = generate_sentences_for_word(
                db, lemma, word_sample, lemma_lookup,
                needed=needed, model=model, delay=delay,
                avoid_words=avoid_words,
            )
            total += stored
            print(f"    Generated {stored} sentences")

    print(f"  → Total sentences: {total}")
    return total


# ── Step B: Generate audio for review-eligible sentences ─────────────

def get_audio_eligible_sentences(db: Session) -> list[Sentence]:
    """A sentence is audio-eligible if every word in it has been reviewed correctly ≥1 time."""
    sentences = (
        db.query(Sentence)
        .filter(Sentence.audio_url.is_(None))
        .all()
    )

    eligible = []
    for sent in sentences:
        words = db.query(SentenceWord).filter(SentenceWord.sentence_id == sent.id).all()
        if not words:
            continue

        all_reviewed = True
        for sw in words:
            if sw.lemma_id is None:
                continue
            ulk = (
                db.query(UserLemmaKnowledge)
                .filter(UserLemmaKnowledge.lemma_id == sw.lemma_id)
                .first()
            )
            if not ulk or ulk.times_correct < 1:
                all_reviewed = False
                break

        if all_reviewed:
            eligible.append(sent)

    return eligible


async def step_generate_audio(db: Session, dry_run: bool, limit: int) -> int:
    print("\n═══ Step B: Generate audio for review-eligible sentences ═══")

    eligible = get_audio_eligible_sentences(db)
    if not eligible:
        print("  No audio-eligible sentences found.")
        return 0

    print(f"  Found {len(eligible)} eligible sentences without audio")
    if limit > 0:
        eligible = eligible[:limit]
        print(f"  Limited to {limit}")

    if dry_run:
        print(f"  [dry-run] Would generate {len(eligible)} audio files")
        return len(eligible)

    generated = 0
    for sent in eligible:
        key = cache_key_for(sent.arabic_text, DEFAULT_VOICE_ID)
        if get_cached_path(key):
            sent.audio_url = f"/api/tts/audio/{key}.mp3"
            generated += 1
            continue
        try:
            path = await generate_and_cache(
                sent.arabic_text, DEFAULT_VOICE_ID, cache_key=key, slow_mode=True,
            )
            sent.audio_url = f"/api/tts/audio/{path.name}"
            generated += 1
            print(f"    ✓ Sentence {sent.id}: {sent.arabic_text[:40]}...")
            await asyncio.sleep(0.5)
        except (TTSError, TTSKeyMissing) as e:
            print(f"    ✗ Sentence {sent.id}: {e}")
            continue

    db.commit()
    print(f"  → Total audio generated: {generated}")
    return generated


# ── Step C: Pre-generate for upcoming candidates ─────────────────────

def step_pregenerate_candidates(db: Session, dry_run: bool, count: int, model: str, delay: float) -> int:
    print("\n═══ Step C: Pre-generate sentences for upcoming candidates ═══")

    candidates = select_next_words(db, count=count)
    if not candidates:
        print("  No candidates available.")
        return 0

    print(f"  Found {len(candidates)} upcoming candidates")

    existing_counts = get_existing_counts(db)
    known_words, lemma_lookup = get_known_words_and_lookup(db)

    total = 0
    for i, cand in enumerate(candidates):
        lid = cand["lemma_id"]
        existing = existing_counts.get(lid, 0)
        needed = MIN_SENTENCES - existing
        if needed <= 0:
            continue

        print(f"  [{i+1}/{len(candidates)}] {cand['lemma_ar']} ({cand['gloss_en']}) — "
              f"have {existing}, need {needed}")
        if dry_run:
            print(f"    [dry-run] Would generate {needed} sentences")
            total += needed
        else:
            lemma = db.query(Lemma).filter(Lemma.lemma_id == lid).first()
            if lemma:
                stored = generate_sentences_for_word(
                    db, lemma, known_words, lemma_lookup,
                    needed=needed, model=model, delay=delay,
                )
                total += stored
                print(f"    Generated {stored} sentences")

    print(f"  → Total sentences: {total}")
    return total


# ── Main ─────────────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Unified material update workflow")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    parser.add_argument("--skip-audio", action="store_true", help="Skip TTS audio generation")
    parser.add_argument("--limit", type=int, default=0, help="Max audio generations (0=unlimited)")
    parser.add_argument("--candidates", type=int, default=10, help="Number of upcoming candidates (default: 10)")
    parser.add_argument("--model", default="gemini", help="LLM model (default: gemini)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between LLM calls")
    args = parser.parse_args()

    print(f"update_material.py — {'DRY RUN' if args.dry_run else 'LIVE RUN'}")
    print(f"  skip_audio={args.skip_audio}, limit={args.limit}, candidates={args.candidates}")
    start = time.time()

    db = SessionLocal()
    try:
        sent_a = step_backfill_sentences(db, args.dry_run, args.model, args.delay)

        if not args.skip_audio:
            audio_b = await step_generate_audio(db, args.dry_run, args.limit)
        else:
            audio_b = 0
            print("\n═══ Step B: Skipped (--skip-audio) ═══")

        sent_c = step_pregenerate_candidates(db, args.dry_run, args.candidates, args.model, args.delay)

        elapsed = time.time() - start
        print(f"\n{'─' * 60}")
        print(f"Done in {elapsed:.1f}s")
        print(f"  Step A sentences: {sent_a}")
        print(f"  Step B audio:     {audio_b}")
        print(f"  Step C sentences: {sent_c}")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
