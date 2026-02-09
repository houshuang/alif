"""Backfill core_meaning_en (root etymology) for roots that don't have one.

Generates short root etymologies via LLM in batches.
Run: python scripts/backfill_root_meanings.py [--dry-run] [--limit=500] [--overwrite]
"""

import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Root, Lemma


BATCH_SIZE = 20

SYSTEM_PROMPT = """You are an Arabic linguistics expert. For each Arabic consonantal root (given in dotted notation like ك.ت.ب), write a brief etymology describing the semantic field of the root.

Rules:
- 5-15 words, informal, helpful for a language learner
- Describe the semantic field, not just one word
- Use the pattern: "related to [concept], [concept], [concept]"
- Don't start with "The root..." — just start with "related to..."

Examples:
- ك.ت.ب → "related to writing, books, correspondence, scribes"
- د.ر.س → "related to studying, learning, lessons, schools"
- ع.ل.م → "related to knowledge, science, knowing, teaching"
- أ.ك.ل → "related to eating, food, meals, consuming"

Return a JSON array: [{"root_id": 1, "meaning": "related to ..."}]"""


def build_prompt(roots_with_lemmas):
    lines = []
    for root, lemmas in roots_with_lemmas:
        lemma_strs = ", ".join(
            f"{l.lemma_ar} ({l.gloss_en})" for l in lemmas[:5] if l.gloss_en
        )
        ctx = f", known words: {lemma_strs}" if lemma_strs else ""
        lines.append(f"- root_id={root.root_id}, root={root.root}{ctx}")

    root_list = "\n".join(lines)
    return f"""Give the core meaning/etymology for these Arabic roots:

{root_list}

Return JSON array: [{{"root_id": 1, "meaning": "related to ..."}}]"""


def backfill(dry_run=False, limit=500, overwrite=False):
    from app.services.llm import generate_completion, AllProvidersFailed

    db = SessionLocal()

    query = db.query(Root)
    if not overwrite:
        query = query.filter(Root.core_meaning_en.is_(None))
    roots = query.order_by(Root.root_id).limit(limit).all()

    print(f"Found {len(roots)} roots to process (limit={limit}, overwrite={overwrite})")
    if not roots:
        db.close()
        return

    # Pre-fetch lemmas for all roots
    root_ids = [r.root_id for r in roots]
    lemmas_by_root = {}
    all_lemmas = (
        db.query(Lemma)
        .filter(Lemma.root_id.in_(root_ids), Lemma.canonical_lemma_id.is_(None))
        .all()
    )
    for l in all_lemmas:
        lemmas_by_root.setdefault(l.root_id, []).append(l)

    total_done = 0
    for i in range(0, len(roots), BATCH_SIZE):
        batch = roots[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        print(f"\nBatch {batch_num}: {len(batch)} roots")

        roots_with_lemmas = [
            (root, lemmas_by_root.get(root.root_id, [])) for root in batch
        ]
        prompt = build_prompt(roots_with_lemmas)

        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                json_mode=True,
                temperature=0.3,
            )
        except AllProvidersFailed as e:
            print(f"  LLM failed: {e}")
            continue

        # Parse — could be [..] or {"roots": [..]} or {"meanings": [..]}
        meanings = (
            result
            if isinstance(result, list)
            else result.get("roots", result.get("meanings", []))
        )
        if not isinstance(meanings, list):
            print(f"  Unexpected response format: {type(result)}")
            continue

        root_map = {r.root_id: r for r in batch}
        for item in meanings:
            rid = item.get("root_id")
            meaning = item.get("meaning", "").strip()
            if rid in root_map and meaning:
                root = root_map[rid]
                print(f"  {root.root}: {meaning}")
                if not dry_run:
                    root.core_meaning_en = meaning
                total_done += 1

        if not dry_run:
            db.commit()

        time.sleep(1)

    if dry_run:
        db.rollback()
        print(f"\nDry run: would update {total_done} roots")
    else:
        print(f"\nUpdated {total_done} roots with etymologies")

    db.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    overwrite = "--overwrite" in sys.argv
    limit = 500
    for arg in sys.argv:
        if arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
    backfill(dry_run=dry_run, limit=limit, overwrite=overwrite)
