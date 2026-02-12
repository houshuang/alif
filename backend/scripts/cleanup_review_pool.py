#!/usr/bin/env python3
"""Clean up the review pool — comprehensive data quality pass.

Actions:
  3a. Move under-learned words back to acquiring (times_correct < 3)
  3b. Suspend variant ULK records (merge stats into canonical)
  3b2. Suspend known junk words (hardcoded transliterations)
  3c. Retire incomprehensible sentences (< 50% content words known)
  3d. Log regeneration candidates (words with < 2 active sentences)
  3e. Run variant detection on uncovered words (textbook_scan, story_import)

Usage:
    python scripts/cleanup_review_pool.py --dry-run     # preview changes
    python scripts/cleanup_review_pool.py               # apply changes
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from datetime import datetime, timedelta, timezone

from sqlalchemy import func as sa_func

from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.fsrs_service import parse_json_column
from app.services.sentence_validator import FUNCTION_WORDS, strip_diacritics

# Known transliteration junk — not real Arabic vocabulary
JUNK_BARE_FORMS = {"سي", "واي", "رود", "توب"}


def reset_under_learned(db, dry_run: bool) -> dict:
    """Move words with < 3 correct reviews back to acquiring."""
    now = datetime.now(timezone.utc)

    candidates = (
        db.query(UserLemmaKnowledge, Lemma)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            UserLemmaKnowledge.knowledge_state.in_(["learning", "known", "lapsed", "new"]),
            UserLemmaKnowledge.fsrs_card_json.isnot(None),
            UserLemmaKnowledge.times_correct < 3,
        )
        .all()
    )

    reset_words = []
    for ulk, lemma in candidates:
        # Skip variants — they'll be handled by suspend_variant_ulks
        if lemma.canonical_lemma_id is not None:
            continue
        reset_words.append({
            "lemma_id": lemma.lemma_id,
            "arabic": lemma.lemma_ar_bare,
            "english": lemma.gloss_en,
            "old_state": ulk.knowledge_state,
            "times_seen": ulk.times_seen,
            "times_correct": ulk.times_correct,
        })

        if not dry_run:
            ulk.knowledge_state = "acquiring"
            ulk.acquisition_box = 1
            ulk.acquisition_next_due = now + timedelta(hours=4)
            ulk.fsrs_card_json = None

    if not dry_run and reset_words:
        db.flush()

    return {"count": len(reset_words), "words": reset_words}


def suspend_variant_ulks(db, dry_run: bool) -> dict:
    """Suspend ULK records for variant lemmas, merging stats into canonical."""
    variant_lemmas = (
        db.query(Lemma)
        .filter(Lemma.canonical_lemma_id.isnot(None))
        .all()
    )

    if not variant_lemmas:
        return {"count": 0, "words": [], "canonicals_created": 0}

    suspended = []
    canonicals_created = 0

    for vlem in variant_lemmas:
        variant_ulk = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id == vlem.lemma_id)
            .first()
        )
        if not variant_ulk:
            continue
        if variant_ulk.knowledge_state in ("suspended", "encountered"):
            continue

        # Get or create canonical ULK
        canonical_ulk = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id == vlem.canonical_lemma_id)
            .first()
        )

        canonical_lemma = (
            db.query(Lemma)
            .filter(Lemma.lemma_id == vlem.canonical_lemma_id)
            .first()
        )

        if not canonical_ulk:
            if not dry_run:
                canonical_ulk = UserLemmaKnowledge(
                    lemma_id=vlem.canonical_lemma_id,
                    knowledge_state="encountered",
                    source=variant_ulk.source or "variant_merge",
                    times_seen=0,
                    times_correct=0,
                    total_encounters=0,
                )
                db.add(canonical_ulk)
                db.flush()
            canonicals_created += 1

        suspended.append({
            "lemma_id": vlem.lemma_id,
            "arabic": vlem.lemma_ar_bare,
            "english": vlem.gloss_en,
            "old_state": variant_ulk.knowledge_state,
            "canonical_id": vlem.canonical_lemma_id,
            "canonical_arabic": canonical_lemma.lemma_ar_bare if canonical_lemma else "?",
            "times_seen": variant_ulk.times_seen,
            "times_correct": variant_ulk.times_correct,
        })

        if not dry_run and canonical_ulk:
            # Merge review stats into canonical
            canonical_ulk.times_seen = (canonical_ulk.times_seen or 0) + (variant_ulk.times_seen or 0)
            canonical_ulk.times_correct = (canonical_ulk.times_correct or 0) + (variant_ulk.times_correct or 0)
            canonical_ulk.total_encounters = (canonical_ulk.total_encounters or 0) + (variant_ulk.total_encounters or 0)

            # Merge variant_stats_json: add the variant form as a tracked variant
            vstats = parse_json_column(canonical_ulk.variant_stats_json)
            vstats = dict(vstats)
            variant_bare = vlem.lemma_ar_bare or ""
            if variant_bare:
                existing = vstats.get(variant_bare, {"seen": 0, "missed": 0, "confused": 0})
                existing["seen"] = existing.get("seen", 0) + (variant_ulk.times_seen or 0)
                missed = (variant_ulk.times_seen or 0) - (variant_ulk.times_correct or 0)
                existing["missed"] = existing.get("missed", 0) + max(0, missed)
                vstats[variant_bare] = existing
                canonical_ulk.variant_stats_json = vstats

            # Suspend the variant ULK
            variant_ulk.knowledge_state = "suspended"
            variant_ulk.fsrs_card_json = None

    if not dry_run and suspended:
        db.flush()

    return {"count": len(suspended), "words": suspended, "canonicals_created": canonicals_created}


def suspend_junk_words(db, dry_run: bool) -> dict:
    """Suspend known junk words (transliterations, abbreviations)."""
    suspended_words = []

    for bare_form in JUNK_BARE_FORMS:
        lemma = (
            db.query(Lemma)
            .filter(Lemma.lemma_ar_bare == bare_form)
            .first()
        )
        if not lemma:
            continue

        ulk = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id == lemma.lemma_id)
            .first()
        )
        if not ulk or ulk.knowledge_state == "suspended":
            continue

        suspended_words.append({
            "lemma_id": lemma.lemma_id,
            "arabic": lemma.lemma_ar_bare,
            "english": lemma.gloss_en,
            "old_state": ulk.knowledge_state,
        })

        if not dry_run:
            ulk.knowledge_state = "suspended"

    if not dry_run and suspended_words:
        db.flush()

    return {"count": len(suspended_words), "words": suspended_words}


def retire_incomprehensible_sentences(db, dry_run: bool) -> dict:
    """Retire sentences where < 50% of content words are known."""
    active_sentences = (
        db.query(Sentence)
        .filter(Sentence.is_active == True)  # noqa: E712
        .all()
    )

    # Build knowledge state lookup
    all_ulk = db.query(UserLemmaKnowledge).all()
    known_states = {"known", "learning", "lapsed", "acquiring"}
    known_lemma_ids = {u.lemma_id for u in all_ulk if u.knowledge_state in known_states}

    retired = []
    for sent in active_sentences:
        words = (
            db.query(SentenceWord)
            .filter(SentenceWord.sentence_id == sent.id)
            .all()
        )

        content_words = []
        for sw in words:
            if not sw.lemma_id:
                continue
            bare = strip_diacritics(sw.surface_form)
            if bare in FUNCTION_WORDS:
                continue
            content_words.append(sw)

        if not content_words:
            continue

        known_count = sum(1 for sw in content_words if sw.lemma_id in known_lemma_ids)
        comprehension_pct = known_count / len(content_words)

        if comprehension_pct < 0.50:
            retired.append({
                "sentence_id": sent.id,
                "arabic": sent.arabic_text[:60],
                "target_lemma_id": sent.target_lemma_id,
                "comprehension_pct": round(comprehension_pct * 100),
                "content_words": len(content_words),
                "known": known_count,
            })
            if not dry_run:
                sent.is_active = False

    if not dry_run and retired:
        db.flush()

    return {"count": len(retired), "sentences": retired}


def find_regeneration_candidates(db) -> dict:
    """Find words that need sentence regeneration (< 2 active sentences)."""
    active_states = {"acquiring", "learning", "known", "lapsed"}
    active_ulks = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.knowledge_state.in_(active_states))
        .all()
    )

    candidates = []
    for ulk in active_ulks:
        active_sentence_count = (
            db.query(sa_func.count(Sentence.id))
            .filter(
                Sentence.target_lemma_id == ulk.lemma_id,
                Sentence.is_active == True,  # noqa: E712
            )
            .scalar()
        )

        if active_sentence_count < 2:
            lemma = db.query(Lemma).filter(Lemma.lemma_id == ulk.lemma_id).first()
            candidates.append({
                "lemma_id": ulk.lemma_id,
                "arabic": lemma.lemma_ar_bare if lemma else "?",
                "active_sentences": active_sentence_count,
                "state": ulk.knowledge_state,
            })

    return {"count": len(candidates), "words": candidates}


def run_variant_detection_on_uncovered(db, dry_run: bool) -> dict:
    """Run variant detection on words that missed it (textbook_scan, story_import)."""
    uncovered = (
        db.query(Lemma)
        .filter(
            Lemma.canonical_lemma_id.is_(None),
            Lemma.source.in_(["textbook_scan", "story_import", "story"]),
        )
        .all()
    )

    lemma_ids = [l.lemma_id for l in uncovered]
    if not lemma_ids:
        return {"count": 0, "variants_found": 0}

    if dry_run:
        return {"count": len(lemma_ids), "variants_found": 0, "note": "would run detection on these"}

    from app.services.variant_detection import (
        detect_variants_llm,
        detect_definite_variants,
        mark_variants,
    )

    camel_vars = detect_variants_llm(db, lemma_ids=lemma_ids)
    already = {v[0] for v in camel_vars}
    def_vars = detect_definite_variants(db, lemma_ids=lemma_ids, already_variant_ids=already)
    all_vars = camel_vars + def_vars
    variants_marked = 0
    if all_vars:
        variants_marked = mark_variants(db, all_vars)
    db.flush()

    return {"count": len(lemma_ids), "variants_found": variants_marked}


def main():
    parser = argparse.ArgumentParser(description="Clean up review pool — comprehensive data quality pass")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, don't modify")
    args = parser.parse_args()

    db = SessionLocal()
    prefix = "DRY RUN — " if args.dry_run else ""

    try:
        # 3e: Run variant detection on uncovered words (BEFORE variant ULK cleanup)
        print(f"\n{prefix}Step 3e: Run variant detection on uncovered words")
        print("=" * 60)
        detect_result = run_variant_detection_on_uncovered(db, args.dry_run)
        print(f"Words checked: {detect_result['count']}, new variants found: {detect_result['variants_found']}")

        # 3a: Reset under-learned words
        print(f"\n{prefix}Step 3a: Reset under-learned words to acquiring")
        print("=" * 60)
        reset_result = reset_under_learned(db, args.dry_run)
        print(f"Words to reset: {reset_result['count']}")
        for w in reset_result["words"][:30]:
            print(f"  {w['arabic']:<15} {(w['english'] or '')[:25]:<25} "
                  f"state={w['old_state']} seen={w['times_seen']} correct={w['times_correct']}")
        if len(reset_result["words"]) > 30:
            print(f"  ... and {len(reset_result['words']) - 30} more")

        # 3b: Suspend variant ULK records (merge stats into canonical)
        print(f"\n{prefix}Step 3b: Suspend variant ULK records")
        print("=" * 60)
        variant_result = suspend_variant_ulks(db, args.dry_run)
        print(f"Variant ULKs to suspend: {variant_result['count']} "
              f"(canonicals created: {variant_result['canonicals_created']})")
        for w in variant_result["words"][:30]:
            print(f"  {w['arabic']:<15} → {w['canonical_arabic']:<15} "
                  f"state={w['old_state']} seen={w['times_seen']} correct={w['times_correct']}")
        if len(variant_result["words"]) > 30:
            print(f"  ... and {len(variant_result['words']) - 30} more")

        # 3b2: Suspend known junk words
        print(f"\n{prefix}Step 3b2: Suspend junk words (hardcoded)")
        print("=" * 60)
        junk_result = suspend_junk_words(db, args.dry_run)
        print(f"Junk words to suspend: {junk_result['count']}")
        for w in junk_result["words"]:
            print(f"  {w['arabic']:<15} {(w['english'] or '')[:25]:<25} state={w['old_state']}")

        # 3c: Retire incomprehensible sentences
        print(f"\n{prefix}Step 3c: Retire incomprehensible sentences")
        print("=" * 60)
        retire_result = retire_incomprehensible_sentences(db, args.dry_run)
        print(f"Sentences to retire: {retire_result['count']}")
        for s in retire_result["sentences"][:20]:
            print(f"  id={s['sentence_id']:<5} {s['comprehension_pct']}% "
                  f"({s['known']}/{s['content_words']}) {s['arabic'][:50]}")
        if len(retire_result["sentences"]) > 20:
            print(f"  ... and {len(retire_result['sentences']) - 20} more")

        # 3d: Find regeneration candidates
        print(f"\n{prefix}Step 3d: Words needing sentence regeneration")
        print("=" * 60)
        regen_result = find_regeneration_candidates(db)
        print(f"Words needing regeneration: {regen_result['count']}")
        for w in regen_result["words"][:20]:
            print(f"  {w['arabic']:<15} state={w['state']} active_sentences={w['active_sentences']}")
        if len(regen_result["words"]) > 20:
            print(f"  ... and {len(regen_result['words']) - 20} more")

        # Commit all changes
        if not args.dry_run:
            db.commit()

            log_activity(
                db,
                event_type="manual_action",
                summary=(
                    f"Review pool cleanup: {reset_result['count']} words→acquiring, "
                    f"{variant_result['count']} variant ULKs suspended, "
                    f"{junk_result['count']} junk suspended, "
                    f"{retire_result['count']} sentences retired, "
                    f"{regen_result['count']} words need regeneration"
                ),
                detail={
                    "reset_count": reset_result["count"],
                    "variant_suspended": variant_result["count"],
                    "canonicals_created": variant_result["canonicals_created"],
                    "junk_count": junk_result["count"],
                    "retired_sentences": retire_result["count"],
                    "regen_needed": regen_result["count"],
                    "variant_detection_checked": detect_result["count"],
                    "variant_detection_found": detect_result["variants_found"],
                },
            )
            print(f"\nChanges applied and logged to ActivityLog.")
        else:
            print(f"\nDry run complete. Use without --dry-run to apply.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
