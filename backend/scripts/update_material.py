#!/usr/bin/env python3
"""Unified periodic update: backfill sentences, generate audio, pre-generate for upcoming words.

Designed to run as a cron job every 6 hours inside the Docker container.

Steps:
  A) Backfill sentences for introduced words (< 2 sentences each)
  B) Generate audio for review-eligible sentences (all words reviewed ≥1 time)
  C) Pre-generate sentences for top upcoming word candidates (no audio)
  F) Reintroduce leeches past their cooldown period
  G3) FSRS difficulty reconciliation (replay reviews for stuck-difficulty words)

Usage:
    python scripts/update_material.py                  # full run
    python scripts/update_material.py --dry-run        # preview only
    python scripts/update_material.py --skip-audio     # skip TTS generation
    python scripts/update_material.py --limit 20       # max 20 audio generations
"""

import argparse
import asyncio
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.word_selector import select_next_words
from app.services.material_generator import generate_material_for_word
from app.services.sentence_generator import (
    get_content_word_counts,
    get_avoid_words,
    group_words_for_multi_target,
    generate_validated_sentences_multi_target,
)
from app.services.sentence_validator import (
    build_lemma_lookup,
    strip_diacritics,
)
from app.services.tts import (
    DEFAULT_VOICE_ID,
    TTSError,
    TTSKeyMissing,
    cache_key_for,
    generate_and_cache,
    get_cached_path,
)

TARGET_PIPELINE_SENTENCES = 2000  # safety valve only — tier-based lifecycle manages pool size
CAP_HEADROOM = 50  # retire this many below cap to leave room for multi-target backfill
PREGEN_SENTENCES_PER_CANDIDATE = 3  # for step C pre-generation of not-yet-introduced words


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


# ── Step A2: Enrich unverified corpus sentences ──────────────────────
#
# Corpus sentences are imported with raw text (possibly undiacritized),
# no translation, and rule-based lemma mappings that may be wrong.
# This step enriches them one at a time via Claude CLI:
#   1. Diacritize (add tashkeel if missing)
#   2. Translate to English
#   3. Verify/correct word-lemma mappings using the existing pipeline
#
# Only processes sentences containing words the learner is actively studying.

MAX_ENRICH_PER_RUN = 50


def enrich_corpus_sentences(db: Session) -> int:
    """Enrich unverified corpus sentences: diacritize, translate, verify mappings.

    Concurrency: uses a simple row-level guard — sets mappings_verified_at to
    a sentinel before processing, so overlapping cron runs skip it.
    """
    from app.services.llm import generate_completion, AllProvidersFailed
    from app.services.sentence_validator import (
        verify_and_correct_mappings_llm,
        correct_mapping as _correct_mapping,
        _log_mapping_correction,
        build_comprehensive_lemma_lookup,
        detect_proper_names,
        map_tokens_to_lemmas,
        normalize_alef,
        strip_punctuation,
        strip_tatweel,
        tokenize_display,
    )
    from app.services.transliteration import transliterate_arabic

    # Find lemma_ids for acquiring + FSRS words
    active_ids = {
        r[0] for r in db.query(UserLemmaKnowledge.lemma_id).filter(
            UserLemmaKnowledge.knowledge_state.in_(
                ["acquiring", "known", "learning", "lapsed"]
            )
        ).all()
    }
    if not active_ids:
        print("  No active words")
        return 0

    # Find unverified corpus sentences containing active words.
    # These are inactive until enriched — step activates them after verification.
    unverified = (
        db.query(Sentence)
        .filter(
            Sentence.mappings_verified_at.is_(None),
            Sentence.source.in_(["corpus", "book"]),
        )
        .join(SentenceWord, SentenceWord.sentence_id == Sentence.id)
        .filter(SentenceWord.lemma_id.in_(active_ids))
        .distinct()
        .limit(MAX_ENRICH_PER_RUN)
        .all()
    )

    if not unverified:
        print("  No unverified corpus sentences for active words")
        return 0

    print(f"  Found {len(unverified)} unverified corpus sentences to enrich")

    # Build lemma lookup and map for verification
    lemma_lookup = build_comprehensive_lemma_lookup(db)
    all_lemma_ids = set()
    for sent in unverified:
        for sw in sent.words:
            if sw.lemma_id:
                all_lemma_ids.add(sw.lemma_id)
    lemma_map = {
        l.lemma_id: l
        for l in db.query(Lemma).filter(Lemma.lemma_id.in_(all_lemma_ids)).all()
    } if all_lemma_ids else {}

    # Build proper names set from unmapped words across all candidate sentences
    unmapped_freq: dict[str, int] = {}
    for sent in unverified:
        for sw in sent.words:
            if sw.lemma_id is None:
                bare = normalize_alef(strip_diacritics(strip_punctuation(
                    strip_tatweel(sw.surface_form)
                )))
                if bare and len(bare) > 1:
                    unmapped_freq[bare] = unmapped_freq.get(bare, 0) + 1
    proper_names = detect_proper_names(unmapped_freq, lemma_lookup, min_frequency=2)
    if proper_names:
        print(f"  Detected {len(proper_names)} proper names: {sorted(proper_names)[:10]}...")

    # Concurrency guard: claim sentences by setting a sentinel timestamp
    sentinel = datetime(2000, 1, 1)
    claimed_ids = [s.id for s in unverified]
    db.query(Sentence).filter(Sentence.id.in_(claimed_ids)).update(
        {Sentence.mappings_verified_at: sentinel}, synchronize_session="fetch"
    )
    db.commit()

    enriched = 0
    now = datetime.now(timezone.utc)

    for sent in unverified:
        arabic = sent.arabic_diacritized or sent.arabic_text

        # Step 1: Diacritize + translate in one call
        if not sent.english_translation:
            prompt = (
                "You are an Arabic language expert. For the following Arabic sentence:\n\n"
                f"{arabic}\n\n"
                "1. Add full tashkeel (diacritics/vowelization) to the Arabic text. "
                "Keep the exact same words, just add harakat.\n"
                "2. Translate it to natural English.\n\n"
                "Return JSON: {\"diacritized\": \"...\", \"translation\": \"...\"}"
            )
            try:
                result = generate_completion(
                    prompt=prompt,
                    system_prompt="Add diacritics and translate Arabic. Return JSON only.",
                    json_mode=True,
                    temperature=0.0,
                    model_override="claude_haiku",
                    task_type="corpus_enrichment",
                    cli_only=True,
                )
                diacritized = result.get("diacritized", "")
                translation = result.get("translation", "")
                if diacritized:
                    sent.arabic_diacritized = diacritized
                    sent.arabic_text = strip_diacritics(diacritized)
                    sent.transliteration = transliterate_arabic(diacritized) or ""
                if translation:
                    sent.english_translation = translation
            except (AllProvidersFailed, Exception) as e:
                print(f"  Sentence {sent.id}: diacritize/translate failed: {e}")
                sent.mappings_verified_at = None  # release claim for retry
                db.commit()
                continue

        # Step 2: Re-map tokens with diacritized text + proper names
        tokens = tokenize_display(sent.arabic_diacritized or sent.arabic_text)
        mappings = map_tokens_to_lemmas(
            tokens=tokens,
            lemma_lookup=lemma_lookup,
            target_lemma_id=0,
            target_bare="",
            proper_names=proper_names,
        )

        # Refresh lemma_map for any new lemma_ids
        new_ids = {m.lemma_id for m in mappings if m.lemma_id and m.lemma_id not in lemma_map}
        if new_ids:
            for l in db.query(Lemma).filter(Lemma.lemma_id.in_(new_ids)).all():
                lemma_map[l.lemma_id] = l

        # Step 3: Verify mappings via LLM (same pipeline as LLM-generated sentences)
        corrections = verify_and_correct_mappings_llm(
            sent.arabic_diacritized or sent.arabic_text,
            sent.english_translation or "",
            mappings,
            lemma_map,
        )

        if corrections is None:
            print(f"  Sentence {sent.id}: verification unavailable, skipping")
            continue

        # Apply corrections (same pattern as material_generator.py)
        if corrections:
            correction_failed = False
            for corr in corrections:
                pos_idx = corr["position"]
                m = next((m for m in mappings if m.position == pos_idx), None)
                if not m:
                    continue
                new_lid = _correct_mapping(
                    db,
                    str(corr.get("correct_lemma_ar", "") or ""),
                    str(corr.get("correct_gloss", "") or ""),
                    str(corr.get("correct_pos", "") or ""),
                    current_lemma_id=m.lemma_id,
                )
                if new_lid and new_lid != m.lemma_id:
                    m.lemma_id = new_lid
                elif not new_lid:
                    correction_failed = True
            _log_mapping_correction(corrections, not correction_failed, sent.arabic_text)
            if correction_failed:
                sent.is_active = False
                sent.mappings_verified_at = now  # don't retry
                print(f"  Sentence {sent.id}: correction failed, deactivated")
                db.commit()
                continue

        # Check for unmapped content words — reject if any remain
        from app.services.sentence_validator import _is_function_word, strip_punctuation, strip_tatweel
        has_unmapped = False
        for m in mappings:
            if m.is_function_word or getattr(m, 'is_proper_name', False):
                continue
            lid = m.lemma_id if m.lemma_id and m.lemma_id != 0 else None
            bare = strip_diacritics(strip_punctuation(strip_tatweel(m.surface_form)))
            if not bare or len(bare) <= 1:
                continue
            if lid is None:
                has_unmapped = True
                print(f"  Sentence {sent.id}: unmapped word '{m.surface_form}' — deactivating")
                break

        if has_unmapped:
            sent.is_active = False
            sent.mappings_verified_at = now  # don't retry
            db.commit()
            continue

        # Update SentenceWord records with re-mapped tokens
        # Delete old and recreate (simpler than diffing)
        db.query(SentenceWord).filter(SentenceWord.sentence_id == sent.id).delete()
        for m in mappings:
            lid = m.lemma_id if m.lemma_id and m.lemma_id != 0 else None
            db.add(SentenceWord(
                sentence_id=sent.id,
                position=m.position,
                surface_form=m.surface_form,
                lemma_id=lid,
                is_target_word=False,
            ))

        sent.mappings_verified_at = now
        sent.is_active = True  # activate after successful enrichment
        db.commit()
        enriched += 1
        if enriched % 10 == 0:
            print(f"  ...enriched {enriched}/{len(unverified)}")

    print(f"  Enriched {enriched}/{len(unverified)} corpus sentences")
    return enriched


# ── Step 0: Enforce sentence cap by retiring excess ──────────────────

def step_enforce_cap(
    db: Session,
    dry_run: bool,
    max_sentences: int = TARGET_PIPELINE_SENTENCES,
    tier_lookup: dict | None = None,
) -> int:
    """Retire excess sentences when over the pipeline cap.

    Retirement priority:
      1. Never-shown sentences (times_shown=0) — stale first (no acquiring scaffold)
      2. Shown stale sentences (no acquiring/learning scaffold words)
      3. Oldest by last_reading_shown_at as final tiebreaker
    Floor per word is due-date-tier-aware: tier 1 (due <12h) keeps 2,
    tier 2 (12-36h) keeps 1, tier 3+ keeps 0.
    """
    import json

    if tier_lookup is None:
        from app.services.pipeline_tiers import compute_word_tiers, build_tier_lookup
        word_tiers = compute_word_tiers(db)
        tier_lookup = build_tier_lookup(word_tiers)

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
        # Never-shown sentences should be PROTECTED — they haven't had a chance
        # to be used yet. Stale shown sentences are the best retirement candidates.
        # 0 = shown + stale, 1 = shown (not stale), 2 = never-shown + stale, 3 = never-shown
        if not never_shown and is_stale:
            priority = 0
        elif not never_shown:
            priority = 1
        elif never_shown and is_stale:
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
        wt = tier_lookup.get(target_id) if target_id else None
        floor = wt.cap_floor if wt else 0
        if active - already_retiring <= floor:
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
    tier_lookup: dict | None = None,
) -> int:
    print("\n═══ Step A: Backfill sentences (due-date priority) ═══")

    # Compute tiers if not provided
    if tier_lookup is None:
        from app.services.pipeline_tiers import compute_word_tiers, build_tier_lookup
        word_tiers = compute_word_tiers(db)
        tier_lookup = build_tier_lookup(word_tiers)
    else:
        word_tiers = sorted(
            tier_lookup.values(),
            key=lambda w: (w.due_dt or datetime.max.replace(tzinfo=timezone.utc)),
        )

    from app.services.pipeline_tiers import tier_summary
    ts = tier_summary(word_tiers)
    print(f"  Tier distribution: T1={ts.get(1, 0)} T2={ts.get(2, 0)} T3={ts.get(3, 0)} T4={ts.get(4, 0)}")

    existing_counts = get_existing_counts(db)
    total_active = sum(existing_counts.values())
    print(f"  Total active sentences: {total_active}")
    print(f"  Pipeline target: {max_sentences}")

    if total_active >= max_sentences:
        print(f"  Pipeline full ({total_active} >= {max_sentences}), skipping.")
        return 0

    budget = max_sentences - total_active
    print(f"  Budget: {budget} sentences to generate")

    known_words, lemma_lookup = get_known_words_and_lookup(db)
    content_word_counts = get_content_word_counts(db)
    avoid_words = get_avoid_words(content_word_counts, known_words)

    # Collect words needing sentences — tier-based targets
    words_needing: list[dict] = []
    for wt in word_tiers:
        if wt.backfill_target <= 0:
            continue  # tier 4: skip, JIT fills when needed
        existing = existing_counts.get(wt.lemma_id, 0)
        needed = wt.backfill_target - existing
        if needed <= 0:
            continue
        lemma = db.query(Lemma).filter(Lemma.lemma_id == wt.lemma_id).first()
        if not lemma:
            continue
        words_needing.append({
            "lemma_id": wt.lemma_id,
            "lemma_ar": lemma.lemma_ar,
            "gloss_en": lemma.gloss_en or "",
            "root_id": lemma.root_id,
            "due_str": wt.due_dt.isoformat() if wt.due_dt else "none",
            "existing": existing,
            "needed": min(needed, budget),
            "tier": wt.tier,
            "backfill_target": wt.backfill_target,
        })

    print(f"  Words needing sentences: {len(words_needing)} (of {len(word_tiers)} total)")

    total = 0
    words_processed = 0
    covered_by_multi: set[int] = set()

    # Phase 1: Multi-target generation for groups of 2-4 words
    # Generate-then-write: LLM calls happen first, DB writes batched after
    if not dry_run and len(words_needing) >= 2:
        from app.services.material_generator import store_multi_target_sentence
        groups = group_words_for_multi_target(words_needing)

        # Generate all multi-target sentences (no DB writes during LLM calls)
        all_multi_results: list[tuple[list, dict[str, int]]] = []
        for group in groups:
            if total + sum(len(r) for r, _ in all_multi_results) >= budget:
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
                    model_override=model,
                )
                target_bares = {strip_diacritics(tw["lemma_ar"]): tw["lemma_id"] for tw in group}
                all_multi_results.append((multi_results, target_bares))
            except Exception as e:
                print(f"    Multi-target failed: {e}")
                continue

            if delay > 0:
                time.sleep(delay)

        # Write all multi-target results to DB (fast)
        for multi_results, target_bares in all_multi_results:
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

        if all_multi_results:
            db.commit()

    # Phase 2: Batch single-target for remaining words (≥3 → batch, else single)
    remaining = [
        w for w in words_needing
        if existing_counts.get(w["lemma_id"], 0) < w["backfill_target"]
    ]
    remaining_ids = [w["lemma_id"] for w in remaining]

    if not dry_run and len(remaining_ids) >= 3 and total < budget:
        from app.services.material_generator import batch_generate_material, BATCH_WORD_SIZE
        covered_by_batch: set[int] = set()
        for i in range(0, len(remaining_ids), BATCH_WORD_SIZE):
            if total >= budget:
                break
            chunk = remaining_ids[i:i + BATCH_WORD_SIZE]
            print(f"  Batch generating for {len(chunk)} words...")
            result = batch_generate_material(chunk, model_override=model)
            batch_stored = result.get("generated", 0)
            total += batch_stored
            words_processed += result.get("words_covered", 0)
            if batch_stored:
                print(f"    ✓ Batch: {batch_stored} sentences for {result['words_covered']} words")
            for lid in chunk:
                if lid not in result.get("words_failed", []):
                    covered_by_batch.add(lid)
        # Single-word fallback for batch failures
        for lid in remaining_ids:
            if lid in covered_by_batch or total >= budget:
                continue
            lemma = db.query(Lemma).filter(Lemma.lemma_id == lid).first()
            if not lemma:
                continue
            w = next((w for w in remaining if w["lemma_id"] == lid), None)
            if not w:
                continue
            needed = min(w["backfill_target"] - existing_counts.get(lid, 0), budget - total)
            if needed <= 0:
                continue
            words_processed += 1
            print(f"  {lemma.lemma_ar} ({lemma.gloss_en}) — fallback single-word, need {needed}")
            stored = generate_material_for_word(lemma.lemma_id, needed=needed, model_override=model)
            total += stored
            if stored:
                print(f"    Generated {stored} sentences")
    else:
        # Small set or dry run: single-word path
        for w in remaining:
            if total >= budget:
                break
            lemma_id = w["lemma_id"]
            existing = existing_counts.get(lemma_id, 0)
            needed = w["backfill_target"] - existing
            if needed <= 0:
                continue
            needed = min(needed, budget - total)
            lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
            if not lemma:
                continue
            words_processed += 1
            print(f"  {lemma.lemma_ar} ({lemma.gloss_en}) — have {existing}, need {needed}, due {w['due_str'][:10]}")
            if dry_run:
                total += needed
            else:
                stored = generate_material_for_word(
                    lemma.lemma_id, needed=needed, model_override=model,
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
                if (card.get("stability") or 0) < 3.0:
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

    budget = TARGET_PIPELINE_SENTENCES - total_active

    total = 0
    for i, cand in enumerate(candidates):
        if total >= budget:
            break
        lid = cand["lemma_id"]
        existing = existing_counts.get(lid, 0)
        needed = PREGEN_SENTENCES_PER_CANDIDATE - existing
        if needed <= 0:
            continue

        needed = min(needed, budget - total)
        print(f"  [{i+1}/{len(candidates)}] {cand['lemma_ar']} ({cand['gloss_en']}) — "
              f"have {existing}, need {needed}")
        if dry_run:
            total += needed
        else:
            stored = generate_material_for_word(
                lid, needed=needed, model_override=model,
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
    parser.add_argument("--model", default="claude_sonnet", help="LLM model for sentence gen (default: claude_sonnet)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between LLM calls")
    args = parser.parse_args()

    print(f"update_material.py — {'DRY RUN' if args.dry_run else 'LIVE RUN'}")
    print(f"  skip_audio={args.skip_audio}, limit={args.limit}, candidates={args.candidates}")
    start = time.time()

    db = SessionLocal()
    try:
        from app.services.pipeline_tiers import compute_word_tiers, build_tier_lookup, tier_summary
        word_tiers = compute_word_tiers(db)
        tier_lk = build_tier_lookup(word_tiers)
        ts = tier_summary(word_tiers)
        print(f"\n  Word tiers: T1={ts.get(1, 0)} T2={ts.get(2, 0)} T3={ts.get(3, 0)} T4={ts.get(4, 0)}")

        retired_0 = step_enforce_cap(db, args.dry_run, args.max_sentences, tier_lookup=tier_lk)

        sent_a = step_backfill_sentences(db, args.dry_run, args.model, args.delay, args.max_sentences, tier_lookup=tier_lk)

        # ── Step A2: Enrich corpus sentences (diacritize + translate + verify) ──
        trans_a2 = 0
        print("\n═══ Step A2: Enrich corpus sentences for due/acquiring words ═══")
        if not args.dry_run:
            trans_a2 = enrich_corpus_sentences(db)
        else:
            print("  Skipped (dry run)")

        if not args.skip_audio:
            audio_b = await step_generate_audio(db, args.dry_run, args.limit)
        else:
            audio_b = 0
            print("\n═══ Step B: Skipped (--skip-audio) ═══")

        sent_c = step_pregenerate_candidates(db, args.dry_run, args.candidates, args.model, args.delay)

        samer_d = step_backfill_samer(db, args.dry_run)

        # Step E: Enrich ALL lemmas missing forms/etymology/grammar/examples/roots
        enrich_e = 0
        print("\n═══ Step E: Enrich unenriched lemmas ═══")
        unenriched = (
            db.query(Lemma.lemma_id)
            .filter(
                Lemma.canonical_lemma_id.is_(None),
                (
                    Lemma.forms_json.is_(None)
                    | Lemma.etymology_json.is_(None)
                    | Lemma.memory_hooks_json.is_(None)
                    | (Lemma.grammar_features_json.is_(None) & Lemma.pos.in_(["noun", "verb", "adjective", "adj"]))
                    | (Lemma.example_ar.is_(None) & Lemma.pos.in_(["noun", "verb", "adjective", "adj"]))
                    | (Lemma.root_id.is_(None) & Lemma.pos.in_(["noun", "verb", "adjective", "adj"]))
                ),
            )
            .all()
        )
        unenriched_ids = [r[0] for r in unenriched]
        if unenriched_ids:
            print(f"  Found {len(unenriched_ids)} lemmas to enrich")
            if not args.dry_run:
                from app.services.lemma_enrichment import enrich_lemmas_batch
                result = enrich_lemmas_batch(unenriched_ids)
                enrich_e = (result.get("forms", 0) + result.get("etymology", 0)
                            + result.get("roots", 0) + result.get("grammar", 0) + result.get("examples", 0))
        else:
            print("  All lemmas enriched")

        # Step F: Reintroduce leeches past cooldown
        leech_f = 0
        print("\n═══ Step F: Leech reintroductions ═══")
        if not args.dry_run:
            from app.services.leech_service import check_leech_reintroductions
            reintroduced = check_leech_reintroductions(db)
            leech_f = len(reintroduced)
            if leech_f:
                print(f"  Reintroduced {leech_f} leeches: {reintroduced}")
            else:
                print("  No leeches ready for reintroduction")
        else:
            print("  Skipped (dry run)")

        # Step G: Ensure all active book words have ULK records
        book_ulk_g = 0
        print("\n═══ Step G: Book ULK consistency ═══")
        if not args.dry_run:
            from app.models import Story, StoryWord, UserLemmaKnowledge as ULK
            active_books = db.query(Story).filter(
                Story.source == "book_ocr", Story.status == "active"
            ).all()
            for book in active_books:
                book_lids = {
                    sw.lemma_id for sw in book.words
                    if sw.lemma_id and not sw.is_function_word
                }
                if not book_lids:
                    continue
                existing = {
                    r[0] for r in db.query(ULK.lemma_id)
                    .filter(ULK.lemma_id.in_(book_lids)).all()
                }
                missing = book_lids - existing
                for lid in missing:
                    db.add(ULK(
                        lemma_id=lid,
                        knowledge_state="encountered",
                        source="book",
                        total_encounters=1,
                    ))
                    book_ulk_g += 1
                if missing:
                    db.commit()
            if book_ulk_g:
                print(f"  Created {book_ulk_g} missing ULK records for book words")
            else:
                print("  All book words have ULK records")
        else:
            print("  Skipped (dry run)")

        # ── Step G2: Catch ungated lemmas ────────────────────────────
        ungated_g2 = 0
        print("\n═══ Step G2: Catch ungated lemmas ═══")
        if not args.dry_run:
            ungated = (
                db.query(Lemma.lemma_id)
                .filter(Lemma.gates_completed_at.is_(None))
                .all()
            )
            ungated_ids = [r[0] for r in ungated]
            if ungated_ids:
                from app.services.lemma_quality import run_quality_gates
                print(f"  Found {len(ungated_ids)} ungated lemmas — running quality gates")
                result = run_quality_gates(
                    db, ungated_ids,
                    background_enrich=False,
                )
                ungated_g2 = result.get("stamped", 0)
                print(f"  Stamped {ungated_g2} lemmas")
            else:
                print("  All lemmas gated")
        else:
            print("  Skipped (dry run)")

        # ── Step G3: FSRS difficulty reconciliation ────────────────────
        diff_g3 = 0
        print("\n═══ Step G3: FSRS difficulty reconciliation ═══")
        if not args.dry_run:
            from scripts.repair_fsrs_cards import find_affected_words, replay_reviews
            affected = find_affected_words(db)
            for lemma_id, info in affected.items():
                new_card, new_state = replay_reviews(db, lemma_id)
                if new_card is None:
                    continue
                old_diff = info.get("old_difficulty") or 0
                new_diff = new_card.get("difficulty", 0)
                if old_diff - new_diff > 0.5 or info.get("null_card"):
                    db.execute(text("""
                        UPDATE user_lemma_knowledge
                        SET fsrs_card_json = :card, knowledge_state = :state
                        WHERE lemma_id = :lid
                    """), {"card": json.dumps(new_card), "state": new_state, "lid": lemma_id})
                    diff_g3 += 1
            if diff_g3:
                db.commit()
                print(f"  Repaired {diff_g3} FSRS cards (difficulty reconciliation)")
            else:
                print("  No cards need repair")
        else:
            print("  Skipped (dry run)")

        # ── Step H: auto-generate stories ────────────────────────────
        stories_h = 0
        STORY_TARGET = 3  # keep at least 3 non-archived active stories
        STORY_FORMATS = ["standard", "standard", "long", "breakdown", "arabic_explanation"]
        print(f"\n[H] Auto-generate stories (target ≥ {STORY_TARGET} active non-archived)")
        if not args.dry_run:
            from app.models import Story as StoryModel
            from app.services.story_service import generate_story as gen_story
            active_stories = db.query(StoryModel).filter(
                StoryModel.status == "active",
                StoryModel.archived_at.is_(None),
                StoryModel.source != "book_ocr",
            ).count()
            deficit = STORY_TARGET - active_stories
            print(f"  Active non-archived stories: {active_stories}, need {max(0, deficit)} more")
            for i in range(deficit):
                fmt = STORY_FORMATS[i % len(STORY_FORMATS)]
                length = random.choice(["short", "medium", "long"])
                try:
                    print(f"  Generating story {i+1}/{deficit} (format={fmt}, length={length})...")
                    story_obj, new_ids = gen_story(
                        db, difficulty="beginner", length=length,
                        format_type=fmt,
                    )
                    stories_h += 1
                    print(f"  Generated: '{story_obj.title_en}' ({story_obj.total_words} words)")
                except Exception as e:
                    print(f"  Story generation failed: {e}")
                    logger.exception("Step H story generation failed")
        else:
            print("  Skipped (dry run)")

        # ── Step I: auto-generate podcasts ─────────────────────────
        podcasts_i = 0
        PODCAST_TARGET = 4  # keep at least 4 unheard podcasts
        MAX_PODCAST_PER_RUN = 2  # limit TTS cost per cron run
        print(f"\n[I] Auto-generate podcasts (target ≥ {PODCAST_TARGET} unheard)")
        if not args.dry_run:
            from app.services.podcast_service import unheard_count
            from scripts.generate_story_podcasts import (
                generate_ci_podcast,
                generate_single_podcast,
                get_high_stability_words,
                pick_unused_ci_topic,
                pick_unused_theme,
            )
            from scripts.generate_repetition_podcasts import generate_single_repetition_podcast
            from scripts.generate_podcast_images import generate_image, ART_STYLE, THEME_PROMPTS

            current_unheard = unheard_count()
            deficit = min(PODCAST_TARGET - current_unheard, MAX_PODCAST_PER_RUN)
            print(f"  Unheard podcasts: {current_unheard}, need {max(0, deficit)} more")
            if deficit > 0:
                words = get_high_stability_words(db, min_stability_days=14.0)
                has_words = len(words) >= 30
                generated_paths: list[Path] = []
                # 3-way rotation: story → CI → repetition
                for i in range(deficit):
                    try:
                        fmt_idx = (current_unheard + podcasts_i) % 3
                        if fmt_idx == 1 and has_words:
                            ci = pick_unused_ci_topic()
                            if ci:
                                print(f"  Generating CI podcast {i+1}/{deficit}: {ci['topic'][:50]}...")
                                path = await generate_ci_podcast(db, words, ci["topic"], ci["target"])
                                if path:
                                    podcasts_i += 1
                                    generated_paths.append(path)
                                    print(f"  Generated: {path.name}")
                                continue
                        if fmt_idx == 2:
                            print(f"  Generating repetition podcast {i+1}/{deficit}...")
                            path = await generate_single_repetition_podcast(db)
                            if path:
                                podcasts_i += 1
                                generated_paths.append(path)
                                print(f"  Generated: {path.name}")
                            continue
                        if has_words:
                            theme = pick_unused_theme()
                            print(f"  Generating story podcast {i+1}/{deficit}: {theme['title']}...")
                            path = await generate_single_podcast(db, words, theme)
                            if path:
                                podcasts_i += 1
                                generated_paths.append(path)
                                print(f"  Generated: {path.name}")
                    except Exception as e:
                        print(f"  Podcast generation failed: {e}")
                        logger.exception("Step I podcast generation failed")

                # Auto-generate cover images for new podcasts
                if generated_paths:
                    api_key = os.environ.get("GEMINI_KEY")
                    if api_key:
                        for path in generated_paths:
                            try:
                                stem = path.stem
                                image_path = path.parent / f"{stem}.png"
                                if image_path.exists():
                                    continue
                                meta_path = path.parent / f"{stem}.json"
                                if not meta_path.exists():
                                    continue
                                meta = json.loads(meta_path.read_text())
                                theme_id = meta.get("theme_id", "")
                                if theme_id in THEME_PROMPTS:
                                    prompt = THEME_PROMPTS[theme_id]
                                else:
                                    summary = meta.get("summary", "An Arabic language learning podcast")
                                    prompt = f"Illustration for a story: {summary}. {ART_STYLE}"
                                image_bytes = generate_image(prompt, api_key)
                                if image_bytes:
                                    image_path.write_bytes(image_bytes)
                                    meta["image_filename"] = image_path.name
                                    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
                                    print(f"  Generated image: {image_path.name}")
                            except Exception as e:
                                print(f"  Image generation failed for {path.name}: {e}")
                if not has_words and podcasts_i == 0:
                    print(f"  Not enough high-stability words ({len(words)})")
        else:
            print("  Skipped (dry run)")

        elapsed = time.time() - start
        print(f"\n{'─' * 60}")
        print(f"Done in {elapsed:.1f}s")
        print(f"  Step 0 retired:   {retired_0}")
        print(f"  Step A sentences: {sent_a}")
        print(f"  Step A2 translate: {trans_a2}")
        print(f"  Step B audio:     {audio_b}")
        print(f"  Step C sentences: {sent_c}")
        print(f"  Step D SAMER:     {samer_d}")
        print(f"  Step E enriched:  {enrich_e}")
        print(f"  Step F leeches:   {leech_f}")
        print(f"  Step G book ULK:  {book_ulk_g}")
        print(f"  Step G2 ungated:  {ungated_g2}")
        print(f"  Step G3 diff fix: {diff_g3}")
        print(f"  Step H stories:   {stories_h}")
        print(f"  Step I podcasts:  {podcasts_i}")

        if not args.dry_run and (retired_0 + sent_a + trans_a2 + audio_b + sent_c + enrich_e + leech_f + book_ulk_g + ungated_g2 + diff_g3 + stories_h + podcasts_i > 0):
            log_activity(
                db,
                event_type="material_updated",
                summary=f"Retired {retired_0}, generated {sent_a}+{sent_c} sentences, {audio_b} audio, enriched {enrich_e}, reintro {leech_f} leeches, {book_ulk_g} book ULK, {ungated_g2} ungated, {diff_g3} diff fix, {stories_h} stories, {podcasts_i} podcasts in {elapsed:.0f}s",
                detail={
                    "step_0_retired": retired_0,
                    "step_a_sentences": sent_a,
                    "step_b_audio": audio_b,
                    "step_c_sentences": sent_c,
                    "step_d_samer": samer_d,
                    "step_e_enriched": enrich_e,
                    "step_f_leeches": leech_f,
                    "step_g_book_ulk": book_ulk_g,
                    "step_g2_ungated": ungated_g2,
                    "step_g3_diff_fix": diff_g3,
                    "step_h_stories": stories_h,
                    "step_i_podcasts": podcasts_i,
                    "elapsed_seconds": round(elapsed, 1),
                },
            )
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
