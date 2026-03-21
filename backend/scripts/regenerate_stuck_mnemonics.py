"""Regenerate memory hooks for words failing despite having mnemonics.

Finds words where the existing mnemonic didn't help: they have hooks but
still show <50% recent accuracy with ≥4 reviews. Regenerates with a prompt
that explicitly mentions the previous failed keyword, asking for a different
approach.

Usage:
    cd backend && python3 scripts/regenerate_stuck_mnemonics.py [--dry-run] [--limit=10]

Options:
    --dry-run    Preview stuck words without regenerating
    --limit=N    Max words to regenerate (default: 10)
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from app.models import Lemma, ReviewLog, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.memory_hooks import (
    PREMIUM_SYSTEM_PROMPT,
    _build_word_info,
    validate_hooks,
    find_stuck_hook_words,
)


def regenerate_stuck(dry_run: bool = False, limit: int = 10):
    from app.services.llm import generate_completion, AllProvidersFailed

    db = SessionLocal()
    try:
        stuck_words = find_stuck_hook_words(db, limit=limit)
        print(f"Found {len(stuck_words)} words with failing mnemonics")

        if not stuck_words:
            return

        regenerated = []
        failed = []

        for lemma, ulk, recent_acc in stuck_words:
            old_hooks = lemma.memory_hooks_json or {}
            old_mnemonic = old_hooks.get("mnemonic", "")
            regen_count = old_hooks.get("regeneration_count", 0)

            print(
                f"\n  {lemma.lemma_id} {lemma.lemma_ar_bare} "
                f"({lemma.gloss_en or '?'}) — "
                f"accuracy={recent_acc:.0%}, "
                f"reviews={ulk.times_seen}, "
                f"regen_count={regen_count}"
            )
            if old_mnemonic:
                print(f"    Old mnemonic: {old_mnemonic[:80]}...")

            if dry_run:
                continue

            word_info = _build_word_info(lemma)
            failed_note = ""
            if old_mnemonic:
                failed_note = (
                    f"\n\nThe previous mnemonic FAILED — the learner reviewed this word "
                    f"{ulk.times_seen} times with only {recent_acc:.0%} accuracy. "
                    f"Do NOT reuse the same keyword or approach:\n"
                    f'  "{old_mnemonic}"'
                )

            prompt = f"""Generate memory hooks for this HARD Arabic word that the learner keeps forgetting:

{word_info}{failed_note}

Generate 3 candidate mnemonics with COMPLETELY DIFFERENT keywords from the failed one.
Self-evaluate each on sound match, interaction, and meaning extraction (1-5).
Pick the candidate with the highest total score.

Return JSON with keys: candidates, best_index, mnemonic, cognates, collocations, usage_context, fun_fact.
Return null if the word is a particle/pronoun/function word."""

            try:
                result = generate_completion(
                    prompt=prompt,
                    system_prompt=PREMIUM_SYSTEM_PROMPT,
                    json_mode=True,
                    temperature=0.8,
                    model_override="claude_haiku",
                    task_type="memory_hooks_regeneration",
                )
            except AllProvidersFailed as e:
                print(f"    LLM failed: {e}")
                failed.append(lemma.lemma_id)
                continue

            if result is None or not isinstance(result, dict) or not result:
                print(f"    No result from LLM")
                failed.append(lemma.lemma_id)
                continue

            hooks = result.get("hooks", result) if "hooks" in result else result
            hooks.pop("candidates", None)
            hooks.pop("best_index", None)

            if not validate_hooks(hooks):
                print(f"    Invalid hooks structure")
                failed.append(lemma.lemma_id)
                continue

            # Preserve old hooks as backup and track regeneration count
            hooks["previous_hooks"] = {
                k: v for k, v in old_hooks.items()
                if k not in ("previous_hooks", "regeneration_count")
            }
            hooks["regeneration_count"] = regen_count + 1
            hooks["regenerated_at"] = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat()

            lemma.memory_hooks_json = hooks
            new_mnemonic = hooks.get("mnemonic", "")[:80]
            print(f"    New mnemonic: {new_mnemonic}...")
            regenerated.append(lemma.lemma_id)

            time.sleep(1)

        if not dry_run and regenerated:
            db.commit()
            log_activity(
                db,
                event_type="mnemonic_regeneration",
                summary=f"Regenerated mnemonics for {len(regenerated)} stuck words",
                detail={
                    "lemma_ids": regenerated,
                    "failed": failed,
                },
            )

        status = "Would regenerate" if dry_run else "Regenerated"
        print(f"\n{status}: {len(regenerated)} words")
        if failed:
            print(f"Failed: {len(failed)} words")

    finally:
        db.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    limit = 10
    for arg in sys.argv:
        if arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
    regenerate_stuck(dry_run=dry_run, limit=limit)
