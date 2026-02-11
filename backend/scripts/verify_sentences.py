#!/usr/bin/env python3
"""Verify Arabic naturalness of all LLM-generated sentences.

Sends sentences in batches to GPT-5.2 for evaluation. Retires (soft-deletes)
sentences flagged as unnatural. Run update_material.py afterwards to
regenerate replacements.

Usage:
    python scripts/verify_sentences.py              # check all, retire bad ones
    python scripts/verify_sentences.py --dry-run    # report only
    python scripts/verify_sentences.py --workers 3  # limit parallelism
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from app.models import Sentence
from app.services.activity_log import log_activity
from app.services.llm import generate_completion

BATCH_SIZE = 20
MAX_WORKERS = 5

VERIFY_SYSTEM_PROMPT = """\
You are an expert in Modern Standard Arabic (MSA / fusha). Your task is to evaluate \
Arabic sentences for naturalness and correctness.

Flag ONLY real errors:
- Wrong collocations (words that don't go together semantically)
- Semantic nonsense (sentence meaning doesn't make sense)
- Unnatural phrasing that no native speaker would use
- Incorrect grammar (wrong case endings, agreement errors)

Do NOT flag:
- Minor style preferences
- Slightly formal or textbook-like phrasing
- Correct but uncommon word choices
- Missing or incorrect diacritics (these are cosmetic)

Respond with JSON: {"results": [{"id": 1, "ok": true}, {"id": 2, "ok": false, "issue": "brief explanation"}]}"""


def verify_batch(
    sentences: list[tuple[int, str, str]],
    model: str,
) -> list[dict]:
    """Verify a batch of sentences. Returns list of {id, ok, issue?}."""
    lines = []
    for idx, (sent_id, arabic, english) in enumerate(sentences, 1):
        lines.append(f"{idx}. Arabic: {arabic}")
        lines.append(f"   English: {english}")

    prompt = (
        f"Evaluate these {len(sentences)} Arabic sentences for naturalness:\n\n"
        + "\n".join(lines)
        + "\n\nRespond with JSON."
    )

    try:
        result = generate_completion(
            prompt=prompt,
            system_prompt=VERIFY_SYSTEM_PROMPT,
            json_mode=True,
            temperature=0.0,
            model_override=model,
        )
    except Exception as e:
        print(f"  LLM error: {e}")
        return [{"id": i + 1, "ok": True} for i in range(len(sentences))]

    raw_results = result.get("results", [])
    if not isinstance(raw_results, list):
        return [{"id": i + 1, "ok": True} for i in range(len(sentences))]

    # Map 1-based batch index back to sentence IDs
    mapped = []
    for r in raw_results:
        if not isinstance(r, dict):
            continue
        idx = r.get("id", 0)
        if 1 <= idx <= len(sentences):
            sent_id = sentences[idx - 1][0]
            mapped.append({
                "sentence_id": sent_id,
                "ok": r.get("ok", True),
                "issue": r.get("issue", ""),
            })
    return mapped


def main():
    parser = argparse.ArgumentParser(description="Verify Arabic sentence naturalness")
    parser.add_argument("--dry-run", action="store_true", help="Report only, don't retire")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help=f"Parallel workers (default: {MAX_WORKERS})")
    parser.add_argument("--model", default="openai", help="LLM model for verification (default: openai)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        sentences = (
            db.query(Sentence)
            .filter(
                Sentence.source == "llm",
                Sentence.is_active == True,  # noqa: E712
            )
            .order_by(Sentence.id)
            .all()
        )

        print(f"Found {len(sentences)} active LLM-generated sentences")
        if not sentences:
            return

        # Build batches of (sentence_id, arabic, english)
        items = [
            (s.id, s.arabic_text, s.english_translation or "")
            for s in sentences
        ]
        batches = [
            items[i : i + BATCH_SIZE]
            for i in range(0, len(items), BATCH_SIZE)
        ]
        print(f"Split into {len(batches)} batches of up to {BATCH_SIZE}")
        print(f"Using model: {args.model}, workers: {args.workers}")
        print()

        flagged: list[dict] = []
        checked = 0

        start = time.time()
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(verify_batch, batch, args.model): i
                for i, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                batch_idx = futures[future]
                try:
                    results = future.result()
                except Exception as e:
                    print(f"  Batch {batch_idx} error: {e}")
                    continue

                batch_flagged = [r for r in results if not r.get("ok", True)]
                flagged.extend(batch_flagged)
                checked += len(batches[batch_idx])

                if batch_flagged:
                    for r in batch_flagged:
                        print(f"  FLAGGED #{r['sentence_id']}: {r.get('issue', '?')}")
                else:
                    print(f"  Batch {batch_idx + 1}/{len(batches)}: all OK")

        elapsed = time.time() - start
        print(f"\nChecked {checked} sentences in {elapsed:.1f}s")
        print(f"Flagged: {len(flagged)} unnatural sentences")

        if not flagged:
            print("All sentences passed!")
            return

        # Show flagged sentences
        flagged_ids = {r["sentence_id"] for r in flagged}
        print(f"\n--- Flagged sentences ({len(flagged_ids)}) ---")
        for r in flagged:
            sid = r["sentence_id"]
            sent = db.query(Sentence).get(sid)
            if sent:
                print(f"  #{sid}: {sent.arabic_text}")
                print(f"         {sent.english_translation}")
                print(f"         Issue: {r.get('issue', '?')}")
                print()

        if args.dry_run:
            print("DRY RUN — no changes made")
            return

        # Retire flagged sentences
        retired = 0
        for sid in flagged_ids:
            sent = db.query(Sentence).get(sid)
            if sent and sent.is_active:
                sent.is_active = False
                retired += 1

        db.commit()
        print(f"Retired {retired} sentences")

        log_activity(
            db,
            "sentences_retired",
            f"Quality check: retired {retired} unnatural sentences (GPT-5.2 verification)",
            {
                "total_checked": checked,
                "total_flagged": len(flagged),
                "retired": retired,
                "model": args.model,
                "issues": [
                    {"id": r["sentence_id"], "issue": r.get("issue", "")}
                    for r in flagged
                ],
            },
        )
        db.commit()

        print(f"\nRun update_material.py to regenerate replacements.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
