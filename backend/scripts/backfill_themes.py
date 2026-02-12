"""Backfill thematic_domain for lemmas that don't have one.

Tags each lemma with a thematic domain (school, food, family, etc.) via LLM.

Usage:
    cd backend && python scripts/backfill_themes.py [--dry-run] [--batch-size=30] [--limit=500]
"""

import json
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from app.models import Lemma
from app.services.activity_log import log_activity


DOMAINS = [
    "school", "food", "family", "work", "travel", "home", "nature", "body",
    "time", "religion", "commerce", "media", "politics", "emotions", "social",
    "daily_routine", "language", "science", "military", "law",
]

SYSTEM_PROMPT = f"""You are an Arabic vocabulary specialist. Classify each word into exactly one thematic domain.

Available domains: {", ".join(DOMAINS)}

Rules:
- Choose the single BEST domain for each word
- If a word could fit multiple domains, pick the most primary/common usage
- For very general words (e.g. "big", "go"), pick the domain where they're most commonly taught
- Words about greetings, politeness, conversation → social
- Words about reading, writing, letters → language
- Words about weather, animals, plants → nature
- Words about feelings, mental states → emotions
- Words about buying, selling, money, prices → commerce
- Words about routine activities (sleep, wake, wash) → daily_routine

Return a JSON array: [{{"lemma_id": 1, "domain": "school"}}]"""


def build_prompt(lemmas):
    lines = []
    for l in lemmas:
        pos_hint = f", pos={l.pos}" if l.pos else ""
        gloss = f", meaning=\"{l.gloss_en}\"" if l.gloss_en else ""
        lines.append(f"- lemma_id={l.lemma_id}, word={l.lemma_ar_bare}{pos_hint}{gloss}")
    word_list = "\n".join(lines)
    return f"""Classify each Arabic word into a thematic domain:

{word_list}

Return JSON array: [{{"lemma_id": 1, "domain": "school"}}]"""


def backfill(dry_run=False, batch_size=30, limit=500):
    from app.services.llm import generate_completion, AllProvidersFailed

    db = SessionLocal()

    missing = (
        db.query(Lemma)
        .filter(
            Lemma.thematic_domain.is_(None),
            Lemma.canonical_lemma_id.is_(None),
        )
        .order_by(Lemma.frequency_rank.asc().nullslast())
        .limit(limit)
        .all()
    )

    print(f"Found {len(missing)} lemmas without thematic domain (limit={limit})")
    if not missing:
        db.close()
        return

    total_done = 0
    total_skipped = 0

    for i in range(0, len(missing), batch_size):
        batch = missing[i : i + batch_size]
        batch_num = i // batch_size + 1
        print(f"\nBatch {batch_num}: {len(batch)} words")

        prompt = build_prompt(batch)
        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                json_mode=True,
                temperature=0.1,
            )
        except AllProvidersFailed as e:
            print(f"  LLM failed: {e}")
            continue

        items = (
            result
            if isinstance(result, list)
            else result.get("words", result.get("classifications", []))
        )
        if not isinstance(items, list):
            print(f"  Unexpected response format: {type(result)}")
            continue

        lemma_map = {l.lemma_id: l for l in batch}
        for item in items:
            lid = item.get("lemma_id")
            domain = item.get("domain", "").strip().lower()

            if lid not in lemma_map:
                continue

            lemma = lemma_map[lid]

            if domain not in DOMAINS:
                print(f"  {lid} {lemma.lemma_ar_bare}: unknown domain '{domain}', skipping")
                total_skipped += 1
                continue

            print(f"  {lid} {lemma.lemma_ar_bare} ({lemma.gloss_en}): {domain}")
            if not dry_run:
                lemma.thematic_domain = domain
            total_done += 1

        if not dry_run:
            db.commit()

        time.sleep(1)

    if dry_run:
        db.rollback()
        print(f"\nDry run: would tag {total_done} lemmas ({total_skipped} skipped)")
    else:
        print(f"\nTagged {total_done} lemmas with thematic domains ({total_skipped} skipped)")
        if total_done > 0:
            log_activity(
                db,
                event_type="themes_backfill_completed",
                summary=f"Backfilled thematic domains for {total_done} lemmas",
                detail={"lemmas_updated": total_done, "skipped": total_skipped},
            )

    db.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    batch_size = 30
    limit = 500
    for arg in sys.argv:
        if arg.startswith("--batch-size="):
            batch_size = int(arg.split("=")[1])
        elif arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
    backfill(dry_run=dry_run, batch_size=batch_size, limit=limit)
