"""Verify existing variant→canonical links and unlink false positives.

Queries all lemmas that have canonical_lemma_id set, then:
1. Filters out pairs with gloss overlap (likely correct)
2. Sends suspicious (no gloss overlap) pairs to LLM for verification
3. Unlinks pairs the LLM says are NOT true variants

Usage:
    python3 scripts/verify_variants.py            # dry-run (default)
    python3 scripts/verify_variants.py --fix       # actually unlink false positives
    python3 scripts/verify_variants.py --verbose   # show all pairs including skipped
"""

import argparse
import json
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Lemma
from app.services.activity_log import log_activity
from app.services.variant_detection import _gloss_overlap

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LLM_BATCH_SIZE = 12

SYSTEM_PROMPT = """\
You are an Arabic morphology expert. You will be given pairs of Arabic words \
where one has been marked as a "variant" (same dictionary entry) of the other. \
Your job is to verify whether each link is correct or a false positive.

A pair IS a true variant (same dictionary entry) when:
- Verb conjugation → base verb (يكتبون → كتب)
- Feminine form → masculine (سعيدة → سعيد)
- Broken/sound plural → singular (كتب → كتاب)
- Possessive → base noun (كتابي → كتاب)
- Definite → indefinite (الكتاب → كتاب)

A pair is NOT a true variant (distinct dictionary entries) when:
- Different core meanings despite shared root (جامعة university ≠ جامع mosque)
- Verbal noun vs related noun (كتابة writing ≠ كتاب book)
- Agent noun vs verb/noun (كاتب writer ≠ كتاب book)
- Nisba adjective vs base noun (مصري Egyptian ≠ مصر Egypt)
- Words with different roots
- The key test: would an Arabic learner benefit from tracking these as ONE word? \
If they have different dictionary entries and meanings, they should be separate.

Respond with JSON only."""


def build_batch_prompt(pairs: list[dict]) -> str:
    """Build prompt for a batch of suspicious variant pairs."""
    lines = [
        "For each numbered pair, determine if VARIANT is truly a morphological "
        "variant of CANONICAL (same dictionary entry) or a false positive "
        "(distinct words that were incorrectly linked).\n"
    ]
    for p in pairs:
        lines.append(
            f"{p['idx']}. VARIANT: {p['var_ar']} \"{p['var_gloss']}\" ({p['var_pos']}) "
            f"→ CANONICAL: {p['canon_ar']} \"{p['canon_gloss']}\" ({p['canon_pos']})"
        )

    lines.append(
        '\nRespond with JSON: {"results": [{"id": 1, "is_variant": true/false, '
        '"reason": "brief explanation"}, ...]}'
    )
    return "\n".join(lines)


def call_llm_batch(pairs: list[dict]) -> list[dict]:
    """Send a batch of pairs to LLM and return parsed results."""
    from app.services.llm import generate_completion

    prompt = build_batch_prompt(pairs)

    for model in ["claude_haiku"]:
        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                json_mode=True,
                temperature=0.1,
                model_override=model,
                task_type="variant_verification",
            )
            raw = result.get("results", [])
            if isinstance(raw, list) and len(raw) > 0:
                return raw
        except Exception as e:
            logger.warning(f"LLM call failed with {model}: {e}")
            continue

    return []


def main():
    parser = argparse.ArgumentParser(description="Verify existing variant→canonical links")
    parser.add_argument("--fix", action="store_true", help="Actually unlink false positives (default is dry-run)")
    parser.add_argument("--verbose", action="store_true", help="Show all pairs including confirmed ones")
    args = parser.parse_args()

    dry_run = not args.fix

    db = SessionLocal()

    # Step 1: Query all variant→canonical pairs
    variants = (
        db.query(Lemma)
        .filter(Lemma.canonical_lemma_id.isnot(None))
        .all()
    )

    print(f"Found {len(variants)} variant→canonical links in DB\n")

    if not variants:
        db.close()
        return

    # Step 2: Separate into gloss-overlap (likely OK) vs suspicious (no overlap)
    confirmed_by_gloss = []
    suspicious = []

    for var in variants:
        canon = db.query(Lemma).filter(Lemma.lemma_id == var.canonical_lemma_id).first()
        if not canon:
            logger.warning(f"Variant #{var.lemma_id} points to missing canonical #{var.canonical_lemma_id}")
            suspicious.append((var, None))
            continue

        if _gloss_overlap(var.gloss_en, canon.gloss_en):
            confirmed_by_gloss.append((var, canon))
        else:
            suspicious.append((var, canon))

    print(f"  {len(confirmed_by_gloss)} pairs have gloss overlap (skipping)")
    print(f"  {len(suspicious)} pairs have NO gloss overlap (sending to LLM)\n")

    if args.verbose and confirmed_by_gloss:
        print("--- Confirmed by gloss overlap ---")
        for var, canon in confirmed_by_gloss:
            print(f"  OK  {var.lemma_ar_bare} \"{var.gloss_en}\" → {canon.lemma_ar_bare} \"{canon.gloss_en}\"")
        print()

    # Filter out pairs with missing canonical (broken links)
    broken_links = [(var, canon) for var, canon in suspicious if canon is None]
    suspicious = [(var, canon) for var, canon in suspicious if canon is not None]

    if broken_links:
        print(f"--- {len(broken_links)} broken links (canonical missing) ---")
        for var, _ in broken_links:
            print(f"  BROKEN  #{var.lemma_id} {var.lemma_ar_bare} → canonical #{var.canonical_lemma_id} missing")
        print()

    if not suspicious:
        print("No suspicious pairs to verify with LLM.")
        db.close()
        return

    # Step 3: Batch LLM verification
    print("--- LLM verification ---")
    to_unlink = []
    llm_confirmed = []

    for batch_start in range(0, len(suspicious), LLM_BATCH_SIZE):
        batch = suspicious[batch_start:batch_start + LLM_BATCH_SIZE]
        batch_num = batch_start // LLM_BATCH_SIZE + 1
        total_batches = (len(suspicious) + LLM_BATCH_SIZE - 1) // LLM_BATCH_SIZE
        print(f"  Batch {batch_num}/{total_batches} ({len(batch)} pairs)...")

        llm_input = []
        for i, (var, canon) in enumerate(batch):
            llm_input.append({
                "idx": i + 1,
                "var_ar": var.lemma_ar_bare or "",
                "var_gloss": var.gloss_en or "",
                "var_pos": var.pos or "",
                "canon_ar": canon.lemma_ar_bare or "",
                "canon_gloss": canon.gloss_en or "",
                "canon_pos": canon.pos or "",
            })

        results = call_llm_batch(llm_input)

        if not results:
            print(f"    LLM returned no results for this batch, skipping")
            continue

        result_by_id = {}
        for r in results:
            if isinstance(r, dict) and "id" in r:
                result_by_id[r["id"]] = r

        for i, (var, canon) in enumerate(batch):
            r = result_by_id.get(i + 1)
            if not r:
                print(f"    ? {var.lemma_ar_bare} \"{var.gloss_en}\" → {canon.lemma_ar_bare} \"{canon.gloss_en}\" — no LLM response")
                continue

            is_variant = bool(r.get("is_variant", False))
            reason = r.get("reason", "")

            if is_variant:
                llm_confirmed.append((var, canon, reason))
                if args.verbose:
                    print(f"    OK  {var.lemma_ar_bare} \"{var.gloss_en}\" → {canon.lemma_ar_bare} \"{canon.gloss_en}\" — {reason}")
            else:
                to_unlink.append((var, canon, reason))
                print(f"    UNLINK  {var.lemma_ar_bare} \"{var.gloss_en}\" → {canon.lemma_ar_bare} \"{canon.gloss_en}\" — {reason}")

    # Step 4: Summary and apply fixes
    print(f"\n=== SUMMARY ===")
    print(f"  Total variant links:         {len(variants)}")
    print(f"  Confirmed by gloss overlap:  {len(confirmed_by_gloss)}")
    print(f"  Confirmed by LLM:            {len(llm_confirmed)}")
    print(f"  Broken links:                {len(broken_links)}")
    print(f"  FALSE POSITIVES to unlink:   {len(to_unlink)}")

    if to_unlink:
        print(f"\nFalse positives:")
        for var, canon, reason in to_unlink:
            print(f"  {var.lemma_ar_bare} \"{var.gloss_en}\" → {canon.lemma_ar_bare} \"{canon.gloss_en}\"")
            print(f"    Reason: {reason}")

    if not to_unlink and not broken_links:
        print("\nAll variant links verified. Nothing to fix.")
        db.close()
        return

    if dry_run:
        print(f"\nDry run — no changes made. Use --fix to unlink false positives.")
        db.close()
        return

    # Apply fixes
    unlinked = 0
    for var, canon, reason in to_unlink:
        var.canonical_lemma_id = None
        unlinked += 1

    for var, _ in broken_links:
        var.canonical_lemma_id = None
        unlinked += 1

    db.commit()
    print(f"\nUnlinked {unlinked} false positive variant links.")

    log_activity(
        db,
        event_type="variant_cleanup_completed",
        summary=f"Verified {len(variants)} variant links: unlinked {unlinked} false positives",
        detail={
            "total_links": len(variants),
            "confirmed_by_gloss": len(confirmed_by_gloss),
            "confirmed_by_llm": len(llm_confirmed),
            "broken_links": len(broken_links),
            "unlinked": len(to_unlink),
            "unlinked_pairs": [
                {
                    "variant_id": var.lemma_id,
                    "variant": var.lemma_ar_bare,
                    "canonical_id": canon.lemma_id,
                    "canonical": canon.lemma_ar_bare,
                    "reason": reason,
                }
                for var, canon, reason in to_unlink
            ],
        },
        commit=True,
    )

    db.close()


if __name__ == "__main__":
    main()
