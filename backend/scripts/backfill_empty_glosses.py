#!/usr/bin/env python3
"""Backfill empty gloss_en for lemmas that were imported without translations.

Finds all canonical lemmas with NULL or empty gloss_en and uses LLM batch
translation to populate them. Prioritizes acquiring/known words.

Usage:
    cd backend && python3 scripts/backfill_empty_glosses.py [--dry-run]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import or_

from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge
from app.services.activity_log import log_activity


def backfill_batch(db, lemmas: list[Lemma], dry_run: bool) -> int:
    """Translate a batch of lemmas via LLM and set their gloss_en."""
    from app.services.llm import generate_completion, AllProvidersFailed

    if not lemmas:
        return 0

    words_for_llm = []
    for lem in lemmas:
        words_for_llm.append(
            f"- id={lem.lemma_id}, word={lem.lemma_ar}, pos={lem.pos or 'unknown'}"
        )

    prompt = f"""Translate these Arabic words to English dictionary-form glosses.

Rules:
- Verbs: use infinitive ("to write", "to wake up")
- Nouns: use bare singular ("book", "school")
- Adjectives: use base form ("big", "beautiful")
- Keep glosses concise: 1-3 words

Words:
{chr(10).join(words_for_llm)}

Return JSON array: [{{"id": <lemma_id>, "gloss": "english gloss"}}]
Include ALL words — every word must get a gloss."""

    try:
        result = generate_completion(
            prompt=prompt,
            system_prompt="You translate Arabic words to English. Give concise, dictionary-form glosses (1-3 words). For verbs use infinitive ('to X'). Respond with JSON only.",
            json_mode=True,
            temperature=0.1,
            task_type="backfill_glosses",
        )
    except (AllProvidersFailed, Exception) as e:
        print(f"  LLM failed: {e}")
        return 0

    items = result if isinstance(result, list) else result.get("words", result.get("translations", []))
    if not isinstance(items, list):
        print(f"  Unexpected response type: {type(result)}")
        return 0

    lemma_map = {l.lemma_id: l for l in lemmas}
    filled = 0
    for item in items:
        lid = item.get("id")
        gloss = (item.get("gloss") or item.get("english", "")).strip()
        if not lid or not gloss or lid not in lemma_map:
            continue
        lemma = lemma_map[lid]
        print(f"  {lid} {lemma.lemma_ar} ({lemma.pos}): → \"{gloss}\"")
        if not dry_run:
            lemma.gloss_en = gloss
        filled += 1

    if not dry_run and filled:
        db.commit()

    # Report any that still have no gloss
    missed = [l for l in lemmas if not l.gloss_en and l.lemma_id not in {
        item.get("id") for item in items if (item.get("gloss") or item.get("english", "")).strip()
    }]
    if missed:
        print(f"  WARNING: {len(missed)} words still have no gloss: "
              + ", ".join(f"{l.lemma_id}={l.lemma_ar}" for l in missed[:5]))

    return filled


def main():
    parser = argparse.ArgumentParser(description="Backfill empty gloss_en for lemmas")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"backfill_empty_glosses.py — {'DRY RUN' if args.dry_run else 'LIVE RUN'}")

    db = SessionLocal()
    try:
        # Find all canonical lemmas with empty/null gloss_en
        candidates = (
            db.query(Lemma)
            .filter(
                Lemma.canonical_lemma_id.is_(None),
                or_(Lemma.gloss_en.is_(None), Lemma.gloss_en == ""),
            )
            .all()
        )

        if not candidates:
            print("No lemmas with empty glosses found!")
            return

        # Sort: acquiring/known first (user sees these), then encountered
        ulk_map = {}
        ulk_rows = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id.in_([l.lemma_id for l in candidates]))
            .all()
        )
        for k in ulk_rows:
            ulk_map[k.lemma_id] = k

        state_priority = {"acquiring": 0, "known": 1, "lapsed": 2, "learning": 3, "encountered": 4}
        candidates.sort(
            key=lambda l: state_priority.get(
                (ulk_map.get(l.lemma_id) and ulk_map[l.lemma_id].knowledge_state) or "encountered", 5
            )
        )

        print(f"Found {len(candidates)} lemmas with empty glosses")
        for state in ["acquiring", "known", "lapsed", "learning", "encountered"]:
            count = sum(1 for l in candidates
                       if ulk_map.get(l.lemma_id) and ulk_map[l.lemma_id].knowledge_state == state)
            if count:
                print(f"  {state}: {count}")
        no_ulk = sum(1 for l in candidates if l.lemma_id not in ulk_map)
        if no_ulk:
            print(f"  no ULK: {no_ulk}")

        # Process in batches
        total_filled = 0
        batch_size = 20
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i : i + batch_size]
            print(f"\nBatch {i // batch_size + 1} ({len(batch)} words):")
            filled = backfill_batch(db, batch, args.dry_run)
            total_filled += filled
            if i + batch_size < len(candidates):
                time.sleep(1)

        print(f"\n{'Would fill' if args.dry_run else 'Filled'} {total_filled}/{len(candidates)} glosses")

        if not args.dry_run and total_filled:
            log_activity(
                db,
                event_type="manual_action",
                summary=f"Backfilled {total_filled} empty glosses",
                detail={"total_empty": len(candidates), "filled": total_filled},
            )

    finally:
        db.close()


if __name__ == "__main__":
    main()
