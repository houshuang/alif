#!/usr/bin/env python3
"""Clean up the review pool after algorithm redesign deployment.

Actions:
  3a. Move under-learned words back to acquiring (times_correct < 3)
  3b. Suspend junk words via LLM quality check
  3c. Retire incomprehensible sentences (< 50% content words known)
  3d. Log regeneration candidates (words with < 2 active sentences)

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
from app.services.sentence_validator import FUNCTION_WORDS, strip_diacritics


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


def suspend_junk_words(db, dry_run: bool) -> dict:
    """Suspend junk words via LLM quality check."""
    from app.services.llm import generate_completion, AllProvidersFailed

    active_ulks = (
        db.query(UserLemmaKnowledge, Lemma)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            UserLemmaKnowledge.knowledge_state.notin_(["suspended", "encountered"]),
        )
        .all()
    )

    if not active_ulks:
        return {"count": 0, "words": []}

    lemma_list = [
        {"id": lemma.lemma_id, "arabic": lemma.lemma_ar_bare, "english": lemma.gloss_en or ""}
        for _, lemma in active_ulks
    ]

    # Batch through LLM in groups of 50
    all_junk_ids: set[int] = set()
    batch_size = 50
    for i in range(0, len(lemma_list), batch_size):
        batch = lemma_list[i:i + batch_size]
        word_list = "\n".join(
            f"  {w['id']}: {w['arabic']} ({w['english']})" for w in batch
        )

        prompt = f"""Given these Arabic lemmas, identify which are NOT useful standalone words for an early MSA learner.

Flag these types:
- Transliterations of English/foreign words (e.g. سي = "c", واي = "wi")
- Abbreviations or letter names
- Partial words or fragments
- Proper nouns (except countries, major cities, or important cultural terms)

Words:
{word_list}

Return JSON: {{"junk_ids": [list of id numbers that should be removed]}}
Only flag words you are confident are junk. When in doubt, keep the word."""

        try:
            result = generate_completion(prompt, json_mode=True, temperature=0.1)
            junk_ids = result.get("junk_ids", [])
            all_junk_ids.update(int(x) for x in junk_ids)
        except (AllProvidersFailed, Exception) as e:
            print(f"  LLM batch {i//batch_size + 1} failed: {e}")
            continue

    suspended_words = []
    for ulk, lemma in active_ulks:
        if lemma.lemma_id in all_junk_ids:
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


def main():
    parser = argparse.ArgumentParser(description="Clean up review pool after algorithm redesign")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, don't modify")
    args = parser.parse_args()

    db = SessionLocal()
    prefix = "DRY RUN — " if args.dry_run else ""

    try:
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

        # 3b: Suspend junk words
        print(f"\n{prefix}Step 3b: Suspend junk words (LLM quality check)")
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
                    f"{junk_result['count']} junk suspended, "
                    f"{retire_result['count']} sentences retired, "
                    f"{regen_result['count']} words need regeneration"
                ),
                detail={
                    "reset_count": reset_result["count"],
                    "junk_count": junk_result["count"],
                    "retired_sentences": retire_result["count"],
                    "regen_needed": regen_result["count"],
                },
            )
            print(f"\nChanges applied and logged to ActivityLog.")
        else:
            print(f"\nDry run complete. Use without --dry-run to apply.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
