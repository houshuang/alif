"""Backfill memory_hooks_json for words currently being learned.

Generates memory hooks (mnemonics, cognates, collocations, usage context, fun facts)
for words in acquiring/learning/known state that don't have hooks yet.

Usage:
    cd backend && python3 scripts/backfill_memory_hooks.py [--dry-run] [--batch-size=10] [--limit=100] [--force] [--box1-only]

Options:
    --force       Clear existing memory_hooks_json before regenerating (allows re-generation)
    --box1-only   Only regenerate hooks for words currently in acquisition box 1
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
from app.models import Lemma, Root, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.memory_hooks import PREMIUM_SYSTEM_PROMPT, prepare_hooks_for_storage


def build_prompt(lemmas_with_roots):
    lines = []
    for lemma, root in lemmas_with_roots:
        pos_hint = f", pos={lemma.pos}" if lemma.pos else ""
        gloss = f", meaning=\"{lemma.gloss_en}\"" if lemma.gloss_en else ""
        translit = f", transliteration={lemma.transliteration_ala_lc}" if lemma.transliteration_ala_lc else ""
        root_info = f", root={root.root}" if root else ""
        root_meaning = f", root_meaning=\"{root.core_meaning_en}\"" if root and root.core_meaning_en else ""
        etymology_hint = ""
        if lemma.etymology_json and isinstance(lemma.etymology_json, dict):
            deriv = lemma.etymology_json.get("derivation", "")
            if deriv:
                etymology_hint = f", etymology=\"{deriv}\""
        lines.append(
            f"- lemma_id={lemma.lemma_id}, word={lemma.lemma_ar}, bare={lemma.lemma_ar_bare}{translit}{pos_hint}{gloss}{root_info}{root_meaning}{etymology_hint}"
        )
    word_list = "\n".join(lines)
    return f"""Generate memory hooks for each Arabic word using the overgenerate-and-rank method.

For EACH word:
1. Generate 3 candidate mnemonics with different keywords
2. Self-evaluate each on sound match, interaction, and meaning extraction (1-5)
3. Pick the best candidate
4. If no candidate deserves >=4/5 on sound_match, interaction, and meaning extraction, set hooks to null

{word_list}

Return JSON array: [{{"lemma_id": 1, "hooks": {{"candidates": [{{"keyword": "...", "mnemonic": "...", "sound_match": 5, "interaction": 5, "extraction": 5}}], "best_index": 0, "mnemonic": "THE WINNING MNEMONIC", "cognates": [...], "collocations": [...], "usage_context": "...", "fun_fact": "..."}}}}]
best_index is zero-based.

Include candidates and scores in "hooks"; the backend will store only the winning public fields if quality passes.
Use null for hooks if the word is a particle/pronoun/function word."""


def backfill(dry_run=False, batch_size=10, limit=100, force=False, box1_only=False):
    from app.services.llm import generate_completion, AllProvidersFailed

    db = SessionLocal()

    base_q = (
        db.query(Lemma)
        .join(UserLemmaKnowledge)
        .filter(
            Lemma.canonical_lemma_id.is_(None),
            UserLemmaKnowledge.knowledge_state.in_(["acquiring", "learning", "known", "lapsed"]),
        )
    )
    if box1_only:
        base_q = base_q.filter(UserLemmaKnowledge.acquisition_box == 1)
    if not force:
        base_q = base_q.filter(Lemma.memory_hooks_json.is_(None))

    missing = (
        base_q
        .order_by(Lemma.frequency_rank.asc().nullslast())
        .limit(limit)
        .all()
    )

    mode_desc = "box-1 words" if box1_only else "learning words"
    hook_desc = "(re-generating existing)" if force else "without memory hooks"
    print(f"Found {len(missing)} {mode_desc} {hook_desc} (limit={limit})")
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
                system_prompt=PREMIUM_SYSTEM_PROMPT,
                json_mode=True,
                temperature=0.8,
                model_override="claude_sonnet",
                task_type="memory_hooks",
            )
        except AllProvidersFailed as e:
            print(f"  LLM failed: {e}")
            continue

        items = (
            result
            if isinstance(result, list)
            else result.get("words", result.get("hooks", []))
        )
        if not isinstance(items, list):
            print(f"  Unexpected response format: {type(result)}")
            continue

        lemma_map = {l.lemma_id: l for l in batch}
        for item in items:
            if item is None:
                continue
            lid = item.get("lemma_id")
            hooks = item.get("hooks")

            if lid not in lemma_map:
                continue

            lemma = lemma_map[lid]

            if hooks is None:
                print(f"  {lid} {lemma.lemma_ar_bare}: no hooks (function word)")
                total_null += 1
                continue

            storage_hooks, reason = prepare_hooks_for_storage(hooks)
            if storage_hooks is None:
                print(f"  {lid} {lemma.lemma_ar_bare}: discarded hooks ({reason})")
                total_skipped += 1
                continue

            mnemonic_preview = storage_hooks.get("mnemonic", "")[:60]
            cognate_count = len(storage_hooks.get("cognates", []) or [])
            action = "regenerated" if force and lemma.memory_hooks_json else "generated"
            print(f"  {lid} {lemma.lemma_ar_bare}: [{action}] {mnemonic_preview}... ({cognate_count} cognates, quality={reason})")
            if not dry_run:
                lemma.memory_hooks_json = storage_hooks
            total_done += 1

        if not dry_run:
            db.commit()

        time.sleep(1)

    if dry_run:
        db.rollback()
        print(f"\nDry run: would update {total_done} lemmas ({total_skipped} invalid, {total_null} function words)")
    else:
        print(f"\nUpdated {total_done} lemmas with memory hooks ({total_skipped} invalid, {total_null} function words)")
        if total_done > 0:
            log_activity(
                db,
                event_type="memory_hooks_backfill_completed",
                summary=f"Backfilled memory hooks for {total_done} lemmas",
                detail={"lemmas_updated": total_done, "skipped": total_skipped, "null_entries": total_null},
            )

    db.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    box1_only = "--box1-only" in sys.argv
    batch_size = 10
    limit = 100
    for arg in sys.argv:
        if arg.startswith("--batch-size="):
            batch_size = int(arg.split("=")[1])
        elif arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
    backfill(dry_run=dry_run, batch_size=batch_size, limit=limit, force=force, box1_only=box1_only)
