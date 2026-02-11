"""Backfill root_id for lemmas that don't have roots assigned.

Uses LLM to extract the 3/4-consonant Arabic root for each lemma.
Creates Root records as needed and links lemmas.

Usage:
    cd backend && python scripts/backfill_roots.py [--dry-run] [--limit=500]
"""

import json
import sys
import os
import time
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Lemma, Root
from app.services.morphology import is_valid_root


BATCH_SIZE = 20

SYSTEM_PROMPT = """You are an Arabic morphology expert. For each Arabic word, extract its consonantal root (جذر).

Rules:
- Return the root in dotted Arabic notation (e.g. ك.ت.ب for كتاب)
- Most roots are 3 consonants (trilateral), some are 4 (quadrilateral)
- For particles, pronouns, and words without a clear root, return null
- Use the standard root consonants (not surface letters affected by morphological patterns)

Return a JSON array: [{"lemma_id": 1, "root": "ك.ت.ب"}]
Use null for root if the word has no meaningful root."""


def build_prompt(lemmas):
    lines = []
    for l in lemmas:
        pos_hint = f", pos={l.pos}" if l.pos else ""
        gloss = f", meaning=\"{l.gloss_en}\"" if l.gloss_en else ""
        lines.append(f"- lemma_id={l.lemma_id}, word={l.lemma_ar}{pos_hint}{gloss}")
    word_list = "\n".join(lines)
    return f"""Extract the Arabic consonantal root for each word:

{word_list}

Return JSON array: [{{"lemma_id": 1, "root": "ك.ت.ب"}}] (use null for root if no root)"""


def normalize_root(root_str):
    """Normalize root format to dotted notation."""
    if not root_str:
        return None
    # Remove any non-Arabic chars except dots
    cleaned = re.sub(r'[^\u0600-\u06FF.]', '', root_str)
    if not cleaned:
        return None
    # If already dotted, validate
    if '.' in cleaned:
        parts = cleaned.split('.')
        if len(parts) < 3 or len(parts) > 4:
            return None
        return cleaned
    # If space-separated
    chars = [c for c in cleaned if c.strip()]
    if len(chars) < 3 or len(chars) > 4:
        return None
    return '.'.join(chars)


def backfill(dry_run=False, limit=500):
    from app.services.llm import generate_completion, AllProvidersFailed

    db = SessionLocal()

    # Find lemmas without roots (skip variants and function words)
    missing = (
        db.query(Lemma)
        .filter(
            Lemma.root_id.is_(None),
            Lemma.canonical_lemma_id.is_(None),
        )
        .order_by(Lemma.frequency_rank.asc().nullslast())
        .limit(limit)
        .all()
    )

    print(f"Found {len(missing)} lemmas without roots (limit={limit})")
    if not missing:
        db.close()
        return

    # Pre-load existing roots
    existing_roots = {}
    for root in db.query(Root).all():
        existing_roots[root.root] = root

    total_done = 0
    roots_created = 0

    for i in range(0, len(missing), BATCH_SIZE):
        batch = missing[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
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

        # Parse
        items = result if isinstance(result, list) else result.get("roots", result.get("words", []))
        if not isinstance(items, list):
            print(f"  Unexpected response format: {type(result)}")
            continue

        lemma_map = {l.lemma_id: l for l in batch}
        for item in items:
            lid = item.get("lemma_id")
            raw_root = item.get("root")
            if lid not in lemma_map:
                continue

            lemma = lemma_map[lid]

            if raw_root is None:
                print(f"  {lid} {lemma.lemma_ar_bare}: no root (particle/pronoun)")
                continue

            root_str = normalize_root(raw_root)
            if not root_str or not is_valid_root(root_str):
                print(f"  {lid} {lemma.lemma_ar_bare}: invalid root '{raw_root}'")
                continue

            # Find or create root
            if root_str in existing_roots:
                root_obj = existing_roots[root_str]
            else:
                if dry_run:
                    print(f"  {lid} {lemma.lemma_ar_bare}: {root_str} (NEW ROOT)")
                    total_done += 1
                    roots_created += 1
                    continue
                root_obj = Root(root=root_str)
                db.add(root_obj)
                db.flush()
                existing_roots[root_str] = root_obj
                roots_created += 1

            print(f"  {lid} {lemma.lemma_ar_bare}: {root_str}")
            if not dry_run:
                lemma.root_id = root_obj.root_id
            total_done += 1

        if not dry_run:
            db.commit()

        time.sleep(1)

    if dry_run:
        db.rollback()
        print(f"\nDry run: would update {total_done} lemmas, create {roots_created} new roots")
    else:
        print(f"\nUpdated {total_done} lemmas with roots, created {roots_created} new roots")

    db.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    limit = 500
    for arg in sys.argv:
        if arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
    backfill(dry_run=dry_run, limit=limit)
