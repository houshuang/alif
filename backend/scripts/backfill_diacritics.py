"""Backfill diacritics (tashkīl) on lemma_ar for lemmas stored without diacritics.

After diacritization, also runs the deterministic transliterator to fill
transliteration_ala_lc.

Usage:
    cd backend && python3 scripts/backfill_diacritics.py [--dry-run] [--batch-size=30] [--limit=2000]
"""

import sys
import os
import time
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from app.models import Lemma
from app.services.activity_log import log_activity
from app.services.transliteration import transliterate_lemma

DIACRITIC_RE = re.compile(r"[\u064B-\u065F\u0670]")

SYSTEM_PROMPT = """You are an Arabic diacritization expert. Add full tashkīl (diacritics) to each Arabic word.

Rules:
- Add fatḥa, kasra, ḍamma, sukūn, shadda, and tanwīn where appropriate
- Use the dictionary/citation form (lemma form):
  - Nouns: indefinite with tanwīn (e.g. كِتَابٌ, مَدْرَسَةٌ)
  - Verbs: past tense 3rd person masculine singular (e.g. كَتَبَ, ذَهَبَ)
  - Adjectives: masculine singular indefinite (e.g. كَبِيرٌ, جَمِيلٌ)
  - Particles/prepositions: as commonly written (e.g. فِي, مِنْ, عَلَى)
- The POS and English gloss are provided to help disambiguate
- Preserve the exact consonant skeleton — only ADD diacritics, never change letters

Return a JSON array: [{"lemma_id": 1, "diacritized": "كِتَابٌ"}, ...]"""


def has_diacritics(text: str) -> bool:
    return bool(DIACRITIC_RE.search(text))


def strip_diacritics(text: str) -> str:
    return DIACRITIC_RE.sub("", text)


def build_prompt(lemmas):
    lines = []
    for lemma in lemmas:
        pos_hint = f", pos={lemma.pos}" if lemma.pos else ""
        gloss = f', en="{lemma.gloss_en}"' if lemma.gloss_en else ""
        lines.append(
            f"- lemma_id={lemma.lemma_id}, word={lemma.lemma_ar}{pos_hint}{gloss}"
        )
    word_list = "\n".join(lines)
    return f"""Add full diacritics (tashkīl) to each Arabic word in dictionary/citation form:

{word_list}

Return JSON array: [{{"lemma_id": 1, "diacritized": "..."}}]"""


def backfill(dry_run=False, batch_size=30, limit=2000):
    from app.services.llm import generate_completion, AllProvidersFailed

    db = SessionLocal()

    # Find lemmas where lemma_ar has no diacritics
    all_lemmas = (
        db.query(Lemma)
        .filter(Lemma.canonical_lemma_id.is_(None))
        .order_by(Lemma.frequency_rank.asc().nullslast())
        .all()
    )

    missing = [l for l in all_lemmas if not has_diacritics(l.lemma_ar or "")]
    missing = missing[:limit]

    print(f"Found {len(missing)} lemmas without diacritics (limit={limit})")
    if not missing:
        db.close()
        return

    total_done = 0
    total_skipped = 0
    total_translit = 0

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
            else result.get("words", result.get("results", []))
        )
        if not isinstance(items, list):
            print(f"  Unexpected response format: {type(result)}")
            continue

        lemma_map = {l.lemma_id: l for l in batch}
        for item in items:
            lid = item.get("lemma_id")
            diacritized = item.get("diacritized", "")

            if lid not in lemma_map:
                continue

            lemma = lemma_map[lid]

            if not diacritized or not isinstance(diacritized, str):
                print(f"  {lid} {lemma.lemma_ar}: empty result, skipping")
                total_skipped += 1
                continue

            diacritized = diacritized.strip()

            # Verify consonant skeleton matches
            if strip_diacritics(diacritized) != strip_diacritics(lemma.lemma_ar):
                print(f"  {lid} {lemma.lemma_ar}: skeleton mismatch '{diacritized}', skipping")
                total_skipped += 1
                continue

            if not has_diacritics(diacritized):
                print(f"  {lid} {lemma.lemma_ar}: no diacritics added, skipping")
                total_skipped += 1
                continue

            # Also generate transliteration
            translit = transliterate_lemma(diacritized)

            print(f"  {lid} {lemma.lemma_ar} → {diacritized}  ({translit})")
            if not dry_run:
                lemma.lemma_ar = diacritized
                if translit:
                    lemma.transliteration_ala_lc = translit
                    total_translit += 1
            total_done += 1

        if not dry_run:
            db.commit()

        time.sleep(0.5)

    if dry_run:
        db.rollback()
        print(f"\nDry run: would diacritize {total_done} lemmas ({total_skipped} skipped, {total_translit} transliterated)")
    else:
        print(f"\nDiacritized {total_done} lemmas ({total_skipped} skipped, {total_translit} transliterated)")
        if total_done > 0:
            log_activity(
                db,
                event_type="diacritics_backfill_completed",
                summary=f"Backfilled diacritics for {total_done} lemmas",
                detail={"lemmas_updated": total_done, "skipped": total_skipped, "transliterated": total_translit},
            )

    db.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    batch_size = 30
    limit = 2000
    for arg in sys.argv:
        if arg.startswith("--batch-size="):
            batch_size = int(arg.split("=")[1])
        elif arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
    backfill(dry_run=dry_run, batch_size=batch_size, limit=limit)
