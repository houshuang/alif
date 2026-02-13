"""Backfill etymology_json for lemmas that don't have etymology data.

Generates structured etymology information (root meaning, morphological pattern,
derivation explanation, semantic field, related loanwords, cultural notes) via LLM.

Usage:
    cd backend && python scripts/backfill_etymology.py [--dry-run] [--batch-size=10] [--limit=500]
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
from app.models import Lemma, Root
from app.services.activity_log import log_activity


SYSTEM_PROMPT = """You are an Arabic etymology and morphology expert. For each word, generate structured etymology data that helps a language learner understand word origins.

There are TWO types of words:

1. NATIVE ARABIC WORDS (have a consonantal root):
- root_meaning: the core semantic field of the consonantal root (2-5 words)
- pattern: the morphological pattern (wazan) in Arabic transliteration (e.g. "maf'al", "fa'ala", "taf'īl", "maf'ūl", "fi'āla", "fu'ūl"). Use standard pattern notation with f-'-l representing the root consonants.
- pattern_meaning: what this pattern generally produces (e.g. "place of doing X", "one who does X", "the act of doing X")
- derivation: a short formula showing how root + pattern = meaning (e.g. "maktab = place of writing = office/desk")
- semantic_field: 2-4 related concepts (e.g. "literacy, education, correspondence")
- related_loanwords: English or other European words borrowed from this Arabic root, if any. Return empty array [] if none.
- cultural_note: brief cultural context if relevant, otherwise null

2. LOANWORDS and FOREIGN-ORIGIN WORDS (pizza, chocolate, cinema, tea, computer, etc.):
- root_meaning: null
- pattern: null
- pattern_meaning: null
- derivation: "From [source language] '[original word]' ([meaning])" — trace the borrowing path if it went through intermediate languages (e.g. "From Chinese 茶 (chá) via Persian into Arabic")
- semantic_field: 2-4 related concepts
- related_loanwords: cognates in other languages borrowed from the same source. Return [] if none.
- cultural_note: when/how the word entered Arabic, or interesting cultural context. null if nothing notable.

ONLY return null for the whole entry for closed-class function words: pronouns (هو، هي، أنا، نحن، هم، أنت، أنتم), demonstratives (هذا، هذه، ذلك), prepositions (في، من، على، إلى، عن، مع، بـ، لـ), conjunctions (و، أو، ثم، لأن، ولكن), particles (لا، نعم، هل، يا، ما), and pure proper nouns of countries/cities.

Return JSON array: [{"lemma_id": 1, "etymology": {...}}]"""

EXPECTED_KEYS = {"root_meaning", "pattern", "pattern_meaning", "derivation", "semantic_field", "related_loanwords", "cultural_note"}


def build_prompt(lemmas_with_roots):
    lines = []
    for lemma, root in lemmas_with_roots:
        pos_hint = f", pos={lemma.pos}" if lemma.pos else ""
        gloss = f", meaning=\"{lemma.gloss_en}\"" if lemma.gloss_en else ""
        root_info = f", root={root.root}" if root else ""
        root_meaning = f", root_meaning=\"{root.core_meaning_en}\"" if root and root.core_meaning_en else ""
        lines.append(
            f"- lemma_id={lemma.lemma_id}, word={lemma.lemma_ar_bare}{pos_hint}{gloss}{root_info}{root_meaning}"
        )
    word_list = "\n".join(lines)
    return f"""Generate etymology data for each Arabic word:

{word_list}

Return JSON array: [{{"lemma_id": 1, "etymology": {{"root_meaning": "...", "pattern": "...", "pattern_meaning": "...", "derivation": "...", "semantic_field": "...", "related_loanwords": [...], "cultural_note": null}}}}]

Use null for etymology if the word has no meaningful root derivation (particles, pronouns, etc.)."""


def validate_etymology(etym):
    """Check that the etymology dict has the expected structure."""
    if not isinstance(etym, dict):
        return False
    # Must have derivation at minimum (both native words and loanwords)
    if not etym.get("derivation"):
        return False
    return True


def backfill(dry_run=False, batch_size=10, limit=500):
    from app.services.llm import generate_completion, AllProvidersFailed

    db = SessionLocal()

    missing = (
        db.query(Lemma)
        .filter(
            Lemma.etymology_json.is_(None),
            Lemma.canonical_lemma_id.is_(None),
        )
        .order_by(Lemma.frequency_rank.asc().nullslast())
        .limit(limit)
        .all()
    )

    print(f"Found {len(missing)} lemmas without etymology (limit={limit})")
    if not missing:
        db.close()
        return

    root_ids = {l.root_id for l in missing if l.root_id}
    roots_by_id = {}
    if root_ids:
        for root in db.query(Root).filter(Root.root_id.in_(root_ids)).all():
            roots_by_id[root.root_id] = root

    total_done = 0
    total_skipped = 0
    total_null = 0

    for i in range(0, len(missing), batch_size):
        batch = missing[i : i + batch_size]
        batch_num = i // batch_size + 1
        print(f"\nBatch {batch_num}: {len(batch)} words")

        lemmas_with_roots = [
            (lemma, roots_by_id.get(lemma.root_id)) for lemma in batch
        ]
        prompt = build_prompt(lemmas_with_roots)

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

        items = (
            result
            if isinstance(result, list)
            else result.get("words", result.get("etymologies", []))
        )
        if not isinstance(items, list):
            print(f"  Unexpected response format: {type(result)}")
            continue

        lemma_map = {l.lemma_id: l for l in batch}
        for item in items:
            lid = item.get("lemma_id")
            etym = item.get("etymology")

            if lid not in lemma_map:
                continue

            lemma = lemma_map[lid]

            if etym is None:
                print(f"  {lid} {lemma.lemma_ar_bare}: no etymology (function word)")
                total_null += 1
                continue

            if not validate_etymology(etym):
                print(f"  {lid} {lemma.lemma_ar_bare}: invalid etymology structure, skipping")
                total_skipped += 1
                continue

            preview = etym.get("derivation", "")[:60]
            print(f"  {lid} {lemma.lemma_ar_bare}: {preview}")
            if not dry_run:
                lemma.etymology_json = etym
            total_done += 1

        if not dry_run:
            db.commit()

        time.sleep(1)

    if dry_run:
        db.rollback()
        print(f"\nDry run: would update {total_done} lemmas ({total_skipped} invalid, {total_null} no-root)")
    else:
        print(f"\nUpdated {total_done} lemmas with etymology ({total_skipped} invalid, {total_null} no-root)")
        if total_done > 0:
            log_activity(
                db,
                event_type="etymology_backfill_completed",
                summary=f"Backfilled etymology for {total_done} lemmas",
                detail={"lemmas_updated": total_done, "skipped": total_skipped, "null_entries": total_null},
            )

    db.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    batch_size = 10
    limit = 500
    for arg in sys.argv:
        if arg.startswith("--batch-size="):
            batch_size = int(arg.split("=")[1])
        elif arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
    backfill(dry_run=dry_run, batch_size=batch_size, limit=limit)
