"""Audit variant-canonical links for semantically distinct words incorrectly merged.

Computes Jaccard gloss similarity between each variant and its canonical lemma.
Zero-overlap pairs are sent to LLM for verification. Confirmed false positives
can be unmerged with --fix.

Usage:
    python3 scripts/audit_variants.py                # dry-run, report only
    python3 scripts/audit_variants.py --fix          # unmerge confirmed false positives
    python3 scripts/audit_variants.py --threshold 0.1  # flag pairs below 0.1 Jaccard
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Lemma
from app.services.activity_log import log_activity
from app.services.variant_detection import compute_jaccard_similarity

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LLM_BATCH_SIZE = 12

SYSTEM_PROMPT = """\
You are an Arabic morphology expert. You will be given pairs of Arabic words \
where one has been marked as a "variant" (same dictionary entry) of the other. \
Your job is to verify whether each link is correct or a false positive.

A pair IS a true variant (same dictionary entry) when:
- Verb conjugation -> base verb
- Feminine form -> masculine (same concept)
- Broken/sound plural -> singular
- Possessive -> base noun
- Definite -> indefinite

A pair is NOT a true variant (distinct dictionary entries) when:
- Different core meanings despite shared root (e.g. dish vs hunter)
- Verbal noun vs related noun (writing vs book)
- Agent noun vs verb/noun (writer vs book)
- Nisba adjective vs base noun (Egyptian vs Egypt)
- Words with different roots
- The key test: would an Arabic learner benefit from tracking these as ONE word?

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
            f"-> CANONICAL: {p['canon_ar']} \"{p['canon_gloss']}\" ({p['canon_pos']})"
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
                task_type="variant_audit",
            )
            raw = result.get("results", [])
            if isinstance(raw, list) and len(raw) > 0:
                return raw
        except Exception as e:
            logger.warning(f"LLM call failed with {model}: {e}")
            continue

    return []


def main():
    parser = argparse.ArgumentParser(
        description="Audit variant->canonical links for false merges"
    )
    parser.add_argument(
        "--fix", action="store_true",
        help="Unmerge LLM-confirmed false positives (default: dry-run)"
    )
    parser.add_argument(
        "--threshold", type=float, default=0.0,
        help="Jaccard similarity threshold below which to flag (default: 0.0 = zero overlap only)"
    )
    parser.add_argument(
        "--skip-llm", action="store_true",
        help="Skip LLM verification, just report gloss similarity"
    )
    args = parser.parse_args()

    dry_run = not args.fix
    db = SessionLocal()

    # Step 1: Load all variant->canonical pairs
    variants = (
        db.query(Lemma)
        .filter(Lemma.canonical_lemma_id.isnot(None))
        .all()
    )

    print(f"Found {len(variants)} variant->canonical links\n")

    if not variants:
        db.close()
        return

    # Step 2: Compute Jaccard similarity for each pair
    all_pairs = []
    suspicious = []
    confirmed_by_gloss = []

    for var in variants:
        canon = db.query(Lemma).filter(Lemma.lemma_id == var.canonical_lemma_id).first()
        if not canon:
            logger.warning(
                f"Variant #{var.lemma_id} points to missing canonical #{var.canonical_lemma_id}"
            )
            continue

        jaccard = compute_jaccard_similarity(var.gloss_en or "", canon.gloss_en or "")
        pair_info = {
            "variant_id": var.lemma_id,
            "variant_ar": var.lemma_ar_bare or "",
            "variant_gloss": var.gloss_en or "",
            "variant_pos": var.pos or "",
            "canonical_id": canon.lemma_id,
            "canonical_ar": canon.lemma_ar_bare or "",
            "canonical_gloss": canon.gloss_en or "",
            "canonical_pos": canon.pos or "",
            "jaccard": jaccard,
        }
        all_pairs.append(pair_info)

        if jaccard <= args.threshold:
            suspicious.append(pair_info)
        else:
            confirmed_by_gloss.append(pair_info)

    print(f"  {len(confirmed_by_gloss)} pairs above Jaccard threshold (OK)")
    print(f"  {len(suspicious)} pairs at/below threshold {args.threshold} (suspicious)\n")

    if not suspicious:
        print("No suspicious pairs found.")
        db.close()
        return

    # Step 3: Show suspicious pairs table
    print(f"{'ID':>5}  {'Variant':>15}  {'Variant Gloss':<25}  {'Canon ID':>8}  "
          f"{'Canonical':>15}  {'Canon Gloss':<25}  {'Jaccard':>7}")
    print("-" * 110)
    for p in suspicious:
        print(f"{p['variant_id']:>5}  {p['variant_ar']:>15}  {p['variant_gloss']:<25.25}  "
              f"{p['canonical_id']:>8}  {p['canonical_ar']:>15}  "
              f"{p['canonical_gloss']:<25.25}  {p['jaccard']:>7.3f}")
    print()

    if args.skip_llm:
        print("Skipping LLM verification (--skip-llm).")
        db.close()
        return

    # Step 4: LLM verification of suspicious pairs
    print("--- LLM verification ---")
    to_unlink = []
    llm_confirmed = []

    for batch_start in range(0, len(suspicious), LLM_BATCH_SIZE):
        batch = suspicious[batch_start:batch_start + LLM_BATCH_SIZE]
        batch_num = batch_start // LLM_BATCH_SIZE + 1
        total_batches = (len(suspicious) + LLM_BATCH_SIZE - 1) // LLM_BATCH_SIZE
        print(f"  Batch {batch_num}/{total_batches} ({len(batch)} pairs)...")

        llm_input = []
        for i, p in enumerate(batch):
            llm_input.append({
                "idx": i + 1,
                "var_ar": p["variant_ar"],
                "var_gloss": p["variant_gloss"],
                "var_pos": p["variant_pos"],
                "canon_ar": p["canonical_ar"],
                "canon_gloss": p["canonical_gloss"],
                "canon_pos": p["canonical_pos"],
            })

        results = call_llm_batch(llm_input)

        if not results:
            print("    LLM returned no results for this batch, skipping")
            continue

        result_by_id = {}
        for r in results:
            if isinstance(r, dict) and "id" in r:
                result_by_id[r["id"]] = r

        for i, p in enumerate(batch):
            r = result_by_id.get(i + 1)
            if not r:
                p["llm_verdict"] = "no_response"
                continue

            is_variant = bool(r.get("is_variant", False))
            reason = r.get("reason", "")

            if is_variant:
                p["llm_verdict"] = f"YES: {reason}"
                llm_confirmed.append(p)
            else:
                p["llm_verdict"] = f"NO: {reason}"
                to_unlink.append(p)
                print(f"    UNLINK  {p['variant_ar']} \"{p['variant_gloss']}\" -> "
                      f"{p['canonical_ar']} \"{p['canonical_gloss']}\" -- {reason}")

    # Step 5: Results table
    print(f"\n{'='*110}")
    print("RESULTS TABLE")
    print(f"{'='*110}")
    print(f"{'ID':>5}  {'Variant':>15}  {'Variant Gloss':<25}  {'Canon ID':>8}  "
          f"{'Canonical':>15}  {'Canon Gloss':<25}  {'Jaccard':>7}  {'LLM Verdict'}")
    print("-" * 130)
    for p in suspicious:
        verdict = p.get("llm_verdict", "pending")
        print(f"{p['variant_id']:>5}  {p['variant_ar']:>15}  {p['variant_gloss']:<25.25}  "
              f"{p['canonical_id']:>8}  {p['canonical_ar']:>15}  "
              f"{p['canonical_gloss']:<25.25}  {p['jaccard']:>7.3f}  {verdict}")

    # Step 6: Summary
    print(f"\n=== SUMMARY ===")
    print(f"  Total variant links:         {len(variants)}")
    print(f"  Confirmed by gloss overlap:  {len(confirmed_by_gloss)}")
    print(f"  Confirmed by LLM:            {len(llm_confirmed)}")
    print(f"  FALSE POSITIVES to unlink:   {len(to_unlink)}")

    if not to_unlink:
        print("\nAll suspicious variant links verified by LLM. Nothing to fix.")
        db.close()
        return

    if dry_run:
        print(f"\nDry run -- no changes made. Use --fix to unmerge false positives.")
        db.close()
        return

    # Step 7: Apply fixes
    unlinked = 0
    for p in to_unlink:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == p["variant_id"]).first()
        if lemma:
            lemma.canonical_lemma_id = None
            unlinked += 1

    db.commit()
    print(f"\nUnmerged {unlinked} false positive variant links.")

    log_activity(
        db,
        event_type="variant_cleanup_completed",
        summary=f"Variant audit: unmerged {unlinked} false positives out of {len(variants)} links",
        detail={
            "total_links": len(variants),
            "confirmed_by_gloss": len(confirmed_by_gloss),
            "confirmed_by_llm": len(llm_confirmed),
            "unlinked": len(to_unlink),
            "unlinked_pairs": [
                {
                    "variant_id": p["variant_id"],
                    "variant": p["variant_ar"],
                    "variant_gloss": p["variant_gloss"],
                    "canonical_id": p["canonical_id"],
                    "canonical": p["canonical_ar"],
                    "canonical_gloss": p["canonical_gloss"],
                    "jaccard": p["jaccard"],
                    "llm_verdict": p.get("llm_verdict", ""),
                }
                for p in to_unlink
            ],
        },
        commit=True,
    )

    db.close()


if __name__ == "__main__":
    main()
