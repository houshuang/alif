#!/usr/bin/env python3
"""Unified periodic update: backfill sentences, generate audio, pre-generate for upcoming words.

Designed to run as a cron job every 6 hours inside the Docker container.

Steps:
  A) Backfill sentences for introduced words (< 2 sentences each)
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
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.word_selector import select_next_words, get_sentence_difficulty_params
from app.services.llm import AllProvidersFailed, generate_sentences_batch
from app.services.sentence_generator import (
    get_content_word_counts,
    get_avoid_words,
    group_words_for_multi_target,
    generate_validated_sentences_multi_target,
    sample_known_words_weighted,
    KNOWN_SAMPLE_SIZE,
)
from app.services.sentence_validator import (
    build_lemma_lookup,
    map_tokens_to_lemmas,
    strip_diacritics,
    tokenize_display,
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

MIN_SENTENCES = 2  # per-word target for backfill generation
MIN_SENTENCES_CAP_ENFORCEMENT = 1  # per-word floor during cap enforcement (JIT handles gaps)
TARGET_PIPELINE_SENTENCES = 300  # hard cap — JIT generation fills gaps with current vocabulary
CAP_HEADROOM = 30  # retire this many below cap to leave room for multi-target backfill


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


def get_words_by_due_date(db: Session) -> list[tuple[int, str]]:
    """Return lemma_ids sorted by FSRS due date (most urgent first).

    Returns list of (lemma_id, due_iso_string) for all non-suspended words.
    """
    from datetime import datetime, timezone

    knowledges = (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.knowledge_state.notin_(["suspended", "encountered"]),
        )
        .all()
    )

    items: list[tuple[int, datetime]] = []
    for k in knowledges:
        # Acquiring words use acquisition_next_due
        if k.knowledge_state == "acquiring":
            if k.acquisition_next_due:
                due_dt = k.acquisition_next_due
                if due_dt.tzinfo is None:
                    due_dt = due_dt.replace(tzinfo=timezone.utc)
                items.append((k.lemma_id, due_dt))
            continue

        if not k.fsrs_card_json:
            continue
        try:
            card = k.fsrs_card_json if isinstance(k.fsrs_card_json, dict) else __import__("json").loads(k.fsrs_card_json)
        except (TypeError, ValueError):
            continue
        due_str = card.get("due", "")
        if due_str:
            try:
                due_dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
                if due_dt.tzinfo is None:
                    due_dt = due_dt.replace(tzinfo=timezone.utc)
                items.append((k.lemma_id, due_dt))
            except (ValueError, TypeError):
                pass

    items.sort(key=lambda x: x[1])
    return [(lid, dt.isoformat()) for lid, dt in items]


def get_known_words_and_lookup(db: Session) -> tuple[list[dict[str, str]], dict[str, int]]:
    all_lemmas = (
        db.query(Lemma)
        .join(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.fsrs_card_json.isnot(None))
        .all()
    )
    known_words = [
        {"arabic": lem.lemma_ar, "english": lem.gloss_en or "", "pos": lem.pos or ""}
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
    difficulty_hint: str | None = None,
    max_words: int | None = None,
) -> int:
    target_bare = strip_diacritics(lemma.lemma_ar)
    all_bare = set(lemma_lookup.keys())
    stored = 0
    rejected_words: list[str] = []

    # Use dynamic difficulty based on word familiarity if not explicitly provided
    if difficulty_hint is None or max_words is None:
        diff_params = get_sentence_difficulty_params(db, lemma.lemma_id)
        if difficulty_hint is None:
            difficulty_hint = diff_params["difficulty_hint"]
        if max_words is None:
            max_words = diff_params["max_words"]

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
                count=min(needed - stored + 2, 4),
                difficulty_hint=difficulty_hint,
                model_override=model,
                rejected_words=rejected_words if rejected_words else None,
                avoid_words=avoid_words,
                max_words=max_words,
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
                created_at=datetime.now(timezone.utc),
            )
            db.add(sent)
            db.flush()

            tokens = tokenize_display(res.arabic)
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


# ── Step 0: Enforce sentence cap by retiring excess ──────────────────

def step_enforce_cap(db: Session, dry_run: bool, max_sentences: int = TARGET_PIPELINE_SENTENCES) -> int:
    """Retire excess sentences when over the pipeline cap.

    Retirement priority:
      1. Never-shown sentences (times_shown=0) — stale first (no acquiring scaffold)
      2. Shown stale sentences (no acquiring/learning scaffold words)
      3. Oldest by last_reading_shown_at as final tiebreaker
    Always keeps at least MIN_SENTENCES active per target word.
    """
    import json

    existing_counts = get_existing_counts(db)
    total_active = sum(existing_counts.values())

    # Also count sentences with no target_lemma_id
    orphan_count = (
        db.query(func.count(Sentence.id))
        .filter(Sentence.is_active == True, Sentence.target_lemma_id.is_(None))
        .scalar() or 0
    )
    total_active += orphan_count

    print(f"\n═══ Step 0: Enforce sentence cap ═══")
    print(f"  Active sentences: {total_active} (cap: {max_sentences})")

    retire_target = max_sentences - CAP_HEADROOM
    if total_active <= retire_target:
        print(f"  Under retire target ({retire_target}), nothing to retire.")
        return 0

    excess = total_active - retire_target
    print(f"  Over cap by {excess} — identifying sentences to retire")

    # Load all active sentences with their diversity scores
    sentences = db.query(Sentence).filter(Sentence.is_active == True).all()
    all_sw = db.query(SentenceWord).filter(
        SentenceWord.sentence_id.in_([s.id for s in sentences])
    ).all()
    all_ulk = db.query(UserLemmaKnowledge).all()

    knowledge_map = {k.lemma_id: k for k in all_ulk}
    sw_by_sentence: dict[int, list] = {}
    for sw in all_sw:
        sw_by_sentence.setdefault(sw.sentence_id, []).append(sw)

    # Score and sort for retirement
    candidates: list[tuple[Sentence, int]] = []  # (sentence, priority)
    for sent in sentences:
        sws = sw_by_sentence.get(sent.id, [])
        scaffold_lemmas: set[int] = set()
        acquiring_count = 0
        for sw in sws:
            if not sw.lemma_id or sw.is_target_word:
                continue
            if sw.lemma_id in scaffold_lemmas:
                continue
            scaffold_lemmas.add(sw.lemma_id)
            ulk = knowledge_map.get(sw.lemma_id)
            if ulk and ulk.knowledge_state in ("acquiring", "learning", "lapsed"):
                acquiring_count += 1

        never_shown = (sent.times_shown or 0) == 0
        is_stale = acquiring_count == 0 and len(scaffold_lemmas) >= 2

        # Priority: lower = retire first
        # 0 = never-shown + stale, 1 = never-shown, 2 = shown + stale, 3 = shown
        if never_shown and is_stale:
            priority = 0
        elif never_shown:
            priority = 1
        elif is_stale:
            priority = 2
        else:
            priority = 3

        candidates.append((sent, priority))

    # Sort by priority (lowest first), then oldest
    candidates.sort(key=lambda x: (x[1], x[0].last_reading_shown_at or datetime.min))

    # Enforce min-active per target
    retire_count_per_target: dict[int | None, int] = {}
    retired = 0
    for sent, _ in candidates:
        if retired >= excess:
            break
        target_id = sent.target_lemma_id
        already_retiring = retire_count_per_target.get(target_id, 0)
        active = existing_counts.get(target_id, 0) if target_id else orphan_count
        if active - already_retiring <= MIN_SENTENCES_CAP_ENFORCEMENT:
            continue

        if not dry_run:
            sent.is_active = False
        retired += 1
        retire_count_per_target[target_id] = already_retiring + 1

    if not dry_run and retired > 0:
        db.commit()
        log_activity(
            db,
            event_type="sentences_retired",
            summary=f"Cap enforcement: retired {retired} sentences (cap={max_sentences})",
            detail={"retired": retired, "was_active": total_active, "cap": max_sentences},
        )

    print(f"  → Retired {retired} sentences (target was {excess})")
    return retired


# ── Step A: Backfill sentences for words, prioritized by due date ────

def step_backfill_sentences(
    db: Session, dry_run: bool, model: str, delay: float,
    max_sentences: int = TARGET_PIPELINE_SENTENCES,
) -> int:
    print("\n═══ Step A: Backfill sentences (due-date priority) ═══")

    # Get words sorted by due date (most urgent first)
    due_order = get_words_by_due_date(db)
    if not due_order:
        print("  No introduced words found.")
        return 0

    existing_counts = get_existing_counts(db)
    total_active = sum(existing_counts.values())
    print(f"  Total active sentences: {total_active}")
    print(f"  Pipeline target: {max_sentences}")

    if total_active >= max_sentences:
        print(f"  Pipeline full ({total_active} >= {max_sentences}), skipping.")
        return 0

    budget = max_sentences - total_active
    print(f"  Budget: {budget} sentences to generate")
    print(f"  Words ordered by due date: {len(due_order)} total")

    known_words, lemma_lookup = get_known_words_and_lookup(db)
    content_word_counts = get_content_word_counts(db)
    avoid_words = get_avoid_words(content_word_counts, known_words)

    # Collect words needing sentences
    words_needing: list[dict] = []
    for lemma_id, due_str in due_order:
        existing = existing_counts.get(lemma_id, 0)
        needed = MIN_SENTENCES - existing
        if needed <= 0:
            continue
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        if not lemma:
            continue
        words_needing.append({
            "lemma_id": lemma_id,
            "lemma_ar": lemma.lemma_ar,
            "gloss_en": lemma.gloss_en or "",
            "root_id": lemma.root_id,
            "due_str": due_str,
            "existing": existing,
            "needed": min(needed, budget),
        })

    total = 0
    words_processed = 0
    covered_by_multi: set[int] = set()

    # Phase 1: Multi-target generation for groups of 2-4 words
    if not dry_run and len(words_needing) >= 2:
        from app.services.material_generator import store_multi_target_sentence
        groups = group_words_for_multi_target(words_needing)
        for group in groups:
            if total >= budget:
                break
            print(f"  Multi-target group: {', '.join(w['lemma_ar'] for w in group)}")
            try:
                multi_results = generate_validated_sentences_multi_target(
                    target_words=group,
                    known_words=known_words,
                    existing_sentence_counts=existing_counts,
                    count=len(group),
                    difficulty_hint="beginner",
                    content_word_counts=content_word_counts,
                    avoid_words=avoid_words,
                    lemma_lookup=lemma_lookup,
                )
            except Exception as e:
                print(f"    Multi-target failed: {e}")
                continue

            target_bares = {strip_diacritics(tw["lemma_ar"]): tw["lemma_id"] for tw in group}
            for mres in multi_results:
                if total >= budget:
                    break
                sent = store_multi_target_sentence(db, mres, lemma_lookup, target_bares)
                if sent:
                    total += 1
                    words_processed += 1
                    for lid in mres.target_lemma_ids:
                        covered_by_multi.add(lid)
                        existing_counts[lid] = existing_counts.get(lid, 0) + 1
                    print(f"    ✓ Multi-target sentence covering {len(mres.target_lemma_ids)} words")

            if delay > 0:
                time.sleep(delay)

        db.commit()

    # Phase 2: Single-target for remaining words
    for w in words_needing:
        if total >= budget:
            break

        lemma_id = w["lemma_id"]
        existing = existing_counts.get(lemma_id, 0)
        needed = MIN_SENTENCES - existing
        if needed <= 0:
            continue

        needed = min(needed, budget - total)
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        if not lemma:
            continue

        word_sample = sample_known_words_weighted(
            known_words, content_word_counts, KNOWN_SAMPLE_SIZE,
            target_lemma_id=lemma.lemma_id,
        )

        words_processed += 1
        print(f"  {lemma.lemma_ar} ({lemma.gloss_en}) — have {existing}, need {needed}, due {w['due_str'][:10]}")
        if dry_run:
            total += needed
        else:
            stored = generate_sentences_for_word(
                db, lemma, word_sample, lemma_lookup,
                needed=needed, model=model, delay=delay,
                avoid_words=avoid_words,
            )
            total += stored
            if stored:
                print(f"    Generated {stored} sentences")

    print(f"  → Generated {total} sentences for {words_processed} words")
    return total


# ── Step B: Generate audio for review-eligible sentences ─────────────

def get_audio_eligible_sentences(db: Session) -> list[Sentence]:
    """A sentence is audio-eligible when it's approaching listening readiness.

    To be conservative with TTS costs, we only generate audio for sentences
    where every word has stability >= 3 days and times_seen >= 3 — meaning
    the user knows the words well enough that listening practice is near.
    """
    import json as _json

    sentences = (
        db.query(Sentence)
        .filter(Sentence.audio_url.is_(None), Sentence.is_active == True)  # noqa: E712
        .all()
    )

    eligible = []
    for sent in sentences:
        words = db.query(SentenceWord).filter(SentenceWord.sentence_id == sent.id).all()
        if not words:
            continue

        ready = True
        for sw in words:
            if sw.lemma_id is None:
                continue
            ulk = (
                db.query(UserLemmaKnowledge)
                .filter(UserLemmaKnowledge.lemma_id == sw.lemma_id)
                .first()
            )
            if not ulk or (ulk.times_seen or 0) < 3:
                ready = False
                break
            # Check FSRS stability >= 3 days
            if ulk.fsrs_card_json:
                card = ulk.fsrs_card_json
                if isinstance(card, str):
                    card = _json.loads(card)
                if card.get("stability", 0) < 3.0:
                    ready = False
                    break
            else:
                ready = False
                break

        if ready:
            eligible.append(sent)

    return eligible


MIN_AUDIO_BACKLOG = 30


async def step_generate_audio(db: Session, dry_run: bool, limit: int) -> int:
    print("\n═══ Step B: Generate audio for review-eligible sentences ═══")

    # Check existing audio backlog — only generate if below minimum
    existing_audio = (
        db.query(Sentence)
        .filter(Sentence.audio_url.isnot(None), Sentence.is_active == True)  # noqa: E712
        .count()
    )
    print(f"  Current audio backlog: {existing_audio} sentences")
    if existing_audio >= MIN_AUDIO_BACKLOG:
        print(f"  Backlog sufficient (>= {MIN_AUDIO_BACKLOG}), skipping audio generation.")
        return 0

    needed = MIN_AUDIO_BACKLOG - existing_audio
    print(f"  Need {needed} more sentences with audio")

    eligible = get_audio_eligible_sentences(db)
    if not eligible:
        print("  No audio-eligible sentences found.")
        return 0

    print(f"  Found {len(eligible)} eligible sentences without audio")
    # Cap at what we actually need to reach the backlog minimum
    eligible = eligible[:needed]
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

    # Check pipeline capacity first
    existing_counts = get_existing_counts(db)
    total_active = sum(existing_counts.values())
    if total_active >= TARGET_PIPELINE_SENTENCES:
        print(f"  Pipeline full ({total_active} >= {TARGET_PIPELINE_SENTENCES}), skipping.")
        return 0

    candidates = select_next_words(db, count=count)
    if not candidates:
        print("  No candidates available.")
        return 0

    print(f"  Found {len(candidates)} upcoming candidates")

    known_words, lemma_lookup = get_known_words_and_lookup(db)
    budget = TARGET_PIPELINE_SENTENCES - total_active

    total = 0
    for i, cand in enumerate(candidates):
        if total >= budget:
            break
        lid = cand["lemma_id"]
        existing = existing_counts.get(lid, 0)
        needed = MIN_SENTENCES - existing
        if needed <= 0:
            continue

        needed = min(needed, budget - total)
        print(f"  [{i+1}/{len(candidates)}] {cand['lemma_ar']} ({cand['gloss_en']}) — "
              f"have {existing}, need {needed}")
        if dry_run:
            total += needed
        else:
            lemma = db.query(Lemma).filter(Lemma.lemma_id == lid).first()
            if lemma:
                stored = generate_sentences_for_word(
                    db, lemma, known_words, lemma_lookup,
                    needed=needed, model=model, delay=delay,
                )
                total += stored
                if stored:
                    print(f"    Generated {stored} sentences")

    print(f"  → Total sentences: {total}")
    return total


# ── Main ─────────────────────────────────────────────────────────────

def step_backfill_samer(db: Session, dry_run: bool) -> int:
    """Fill cefr_level from SAMER lexicon for any lemmas missing it."""
    samer_path = Path(__file__).resolve().parent.parent / "data" / "samer.tsv"
    if not samer_path.exists():
        return 0

    missing = db.query(Lemma).filter(
        Lemma.cefr_level.is_(None),
        Lemma.canonical_lemma_id.is_(None),
    ).all()
    if not missing:
        return 0

    print(f"\n═══ Step D: SAMER readability backfill ═══")
    from scripts.backfill_samer import load_samer, lookup_samer, SAMER_TO_CEFR
    samer = load_samer(str(samer_path))

    import re
    diac_re = re.compile(r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]')
    def normalize(t):
        t = diac_re.sub('', t).replace('\u0640', '')
        return re.sub(r'[أإآٱ]', 'ا', t)

    updated = 0
    for lemma in missing:
        bare = lemma.lemma_ar_bare
        if not bare:
            continue
        level = lookup_samer(samer, normalize(bare))
        if level is not None:
            if not dry_run:
                lemma.cefr_level = SAMER_TO_CEFR[level]
            updated += 1

    if not dry_run and updated > 0:
        db.commit()
    print(f"  Filled cefr_level for {updated}/{len(missing)} lemmas")
    return updated


async def main():
    parser = argparse.ArgumentParser(description="Unified material update workflow")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    parser.add_argument("--skip-audio", action="store_true", help="Skip TTS audio generation")
    parser.add_argument("--limit", type=int, default=0, help="Max audio generations (0=unlimited)")
    parser.add_argument("--candidates", type=int, default=10, help="Number of upcoming candidates (default: 10)")
    parser.add_argument("--max-sentences", type=int, default=TARGET_PIPELINE_SENTENCES,
                        help=f"Max total active sentences in pipeline (default: {TARGET_PIPELINE_SENTENCES})")
    parser.add_argument("--model", default="gemini", help="LLM model (default: gemini)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between LLM calls")
    args = parser.parse_args()

    print(f"update_material.py — {'DRY RUN' if args.dry_run else 'LIVE RUN'}")
    print(f"  skip_audio={args.skip_audio}, limit={args.limit}, candidates={args.candidates}")
    start = time.time()

    db = SessionLocal()
    try:
        retired_0 = step_enforce_cap(db, args.dry_run, args.max_sentences)

        sent_a = step_backfill_sentences(db, args.dry_run, args.model, args.delay, args.max_sentences)

        if not args.skip_audio:
            audio_b = await step_generate_audio(db, args.dry_run, args.limit)
        else:
            audio_b = 0
            print("\n═══ Step B: Skipped (--skip-audio) ═══")

        sent_c = step_pregenerate_candidates(db, args.dry_run, args.candidates, args.model, args.delay)

        samer_d = step_backfill_samer(db, args.dry_run)

        # Step E: Enrich ALL lemmas missing forms/etymology/transliteration
        enrich_e = 0
        print("\n═══ Step E: Enrich unenriched lemmas ═══")
        unenriched = (
            db.query(Lemma.lemma_id)
            .filter(
                Lemma.canonical_lemma_id.is_(None),
                (Lemma.forms_json.is_(None) | Lemma.etymology_json.is_(None)),
            )
            .all()
        )
        unenriched_ids = [r[0] for r in unenriched]
        if unenriched_ids:
            print(f"  Found {len(unenriched_ids)} lemmas to enrich")
            if not args.dry_run:
                from app.services.lemma_enrichment import enrich_lemmas_batch
                result = enrich_lemmas_batch(unenriched_ids)
                enrich_e = result.get("forms", 0) + result.get("etymology", 0)
        else:
            print("  All lemmas enriched")

        elapsed = time.time() - start
        print(f"\n{'─' * 60}")
        print(f"Done in {elapsed:.1f}s")
        print(f"  Step 0 retired:   {retired_0}")
        print(f"  Step A sentences: {sent_a}")
        print(f"  Step B audio:     {audio_b}")
        print(f"  Step C sentences: {sent_c}")
        print(f"  Step D SAMER:     {samer_d}")
        print(f"  Step E enriched:  {enrich_e}")

        if not args.dry_run and (retired_0 + sent_a + audio_b + sent_c + enrich_e > 0):
            log_activity(
                db,
                event_type="material_updated",
                summary=f"Retired {retired_0}, generated {sent_a}+{sent_c} sentences, {audio_b} audio, enriched {enrich_e} in {elapsed:.0f}s",
                detail={
                    "step_0_retired": retired_0,
                    "step_a_sentences": sent_a,
                    "step_b_audio": audio_b,
                    "step_c_sentences": sent_c,
                    "step_d_samer": samer_d,
                    "step_e_enriched": enrich_e,
                    "elapsed_seconds": round(elapsed, 1),
                },
            )
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
