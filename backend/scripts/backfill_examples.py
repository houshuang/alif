"""Backfill example_ar and example_en for lemmas that don't have them.

Generates short 3-5 word example sentences via LLM in batches.
Run: python scripts/backfill_examples.py [--dry-run] [--limit=50]
"""

import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Lemma
from app.services.activity_log import log_activity


BATCH_SIZE = 10

SYSTEM_PROMPT = """You are an Arabic language teaching assistant. Generate very short example sentences (3-5 words) for Arabic vocabulary words. Each sentence should:
- Use fully diacritized Arabic (all tashkeel)
- Be simple enough for a beginner
- Clearly demonstrate the meaning of the target word
- Use common, everyday vocabulary

Return JSON array with objects having keys: lemma_id, example_ar, example_en"""


def build_prompt(lemmas):
    words = []
    for l in lemmas:
        words.append(f"- lemma_id={l.lemma_id}, word={l.lemma_ar}, meaning=\"{l.gloss_en}\", pos={l.pos or 'unknown'}")
    word_list = "\n".join(words)
    return f"""Generate a short (3-5 word) example sentence for each of these Arabic words. The sentence should make the meaning of the word clear in context.

{word_list}

Return a JSON array like: [{{"lemma_id": 1, "example_ar": "...", "example_en": "..."}}]"""


def backfill(dry_run=False, limit=50):
    from app.services.llm import generate_completion, AllProvidersFailed

    db = SessionLocal()

    # Find lemmas without examples
    missing = (
        db.query(Lemma)
        .filter(Lemma.example_ar.is_(None))
        .order_by(Lemma.frequency_rank.asc().nullslast())
        .limit(limit)
        .all()
    )

    print(f"Found {len(missing)} lemmas without examples (limit={limit})")
    if not missing:
        db.close()
        return

    total_done = 0
    for i in range(0, len(missing), BATCH_SIZE):
        batch = missing[i:i + BATCH_SIZE]
        print(f"\nBatch {i // BATCH_SIZE + 1}: {len(batch)} words")

        prompt = build_prompt(batch)
        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                json_mode=True,
                temperature=0.5,
            )
        except AllProvidersFailed as e:
            print(f"  LLM failed: {e}")
            continue

        # Parse result - could be {"examples": [...]} or direct [...]
        examples = result if isinstance(result, list) else result.get("examples", result.get("sentences", []))
        if not isinstance(examples, list):
            print(f"  Unexpected response format: {type(result)}")
            continue

        lemma_map = {l.lemma_id: l for l in batch}
        for ex in examples:
            lid = ex.get("lemma_id")
            ar = ex.get("example_ar", "").strip()
            en = ex.get("example_en", "").strip()
            if lid in lemma_map and ar and en:
                lemma = lemma_map[lid]
                print(f"  {lid} {lemma.lemma_ar_bare}: {ar} / {en}")
                if not dry_run:
                    lemma.example_ar = ar
                    lemma.example_en = en
                total_done += 1

        if not dry_run:
            db.commit()

        # Rate limit
        time.sleep(1)

    if dry_run:
        db.rollback()
        print(f"\nDry run: would update {total_done} lemmas")
    else:
        print(f"\nUpdated {total_done} lemmas with examples")
        if total_done > 0:
            log_activity(
                db,
                event_type="examples_backfill_completed",
                summary=f"Backfilled examples for {total_done} lemmas",
                detail={"lemmas_updated": total_done},
            )

    db.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    limit = 50
    for arg in sys.argv:
        if arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
    backfill(dry_run=dry_run, limit=limit)
