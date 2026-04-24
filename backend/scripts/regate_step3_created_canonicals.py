#!/usr/bin/env python3
"""Phase 2 Step 4a-prime: re-gate Step 3's 33 "created" canonicals.

Step 3 created 33 new canonical lemmas (source='backfill_decomposition_audit',
ids #3139–#3171) for orphan compounds whose canonical bare forms were not in
the DB. Post-hoc inspection revealed a systematic CAMeL MLE failure that the
original LLM verdict gate missed:

  feminine ة (tā marbūṭa) misread as 3ms possessive pronoun ـه

Pattern: a feminine noun ending in ـَة gets decomposed by CAMeL into its
masculine stem + fake `enc0: 3ms_poss`. The resulting "canonical" is a
DIFFERENT Arabic lemma — often same root with related meaning — which the
original LLM gate rationalized as valid by drafting a bridging gloss.

21 of the 33 created entries match this failure pattern exactly
(lemma_ar_bare ends in ه/ة AND clitic_signals == {"enc0": "3ms_poss"}). Manual
spot-check of those 21 shows majority are bogus.

This script re-gates all 33 with a STRICTER prompt that:
1. Explicitly names the ة→3ms_poss failure mode with worked examples.
2. Shows the original orphan + proposed canonical side-by-side.
3. Instructs the LLM to lean `bogus_mle_error` when in doubt.

For each Step 3 "created" entry the re-gate writes to
``backend/data/decomposition_regate_progress.json`` one of:
- ``confirmed_valid`` — orphan IS a real compound of proposed canonical + clitics
- ``bogus_mle_error`` — orphan is NOT a compound of proposed canonical

This script does NOT mutate the DB. Apply decisions via
``apply_regate_decisions.py`` (separate step) which:
- For ``bogus_mle_error``: DELETE the new canonical lemma row (zero downstream
  refs verified at time of regate run), tag orphan with decomposition_note
  mle_misanalysis (see Step 4b schema).
- For ``confirmed_valid``: leave canonical in place; orphan linking happens
  in Step 4a-link script.

Usage
-----
    python3 scripts/regate_step3_created_canonicals.py --dry-run
    python3 scripts/regate_step3_created_canonicals.py
    python3 scripts/regate_step3_created_canonicals.py --resume

Environment
-----------
    DATABASE_URL   override the DB path. Default is backend/data/alif.db.
                   For a local dry-run:
                   DATABASE_URL=sqlite:///$(pwd)/data/alif.prod.db
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text

from app.database import SessionLocal
from app.services.llm import LLMError, generate_completion


STEP3_PROGRESS = BACKEND_ROOT.parent / "research" / "decomposition-backfill-progress-2026-04-24.json"
AUDIT_JSON = BACKEND_ROOT.parent / "research" / "decomposition-classification-2026-04-24.json"
REGATE_PROGRESS = BACKEND_ROOT / "data" / "decomposition_regate_progress.json"
BATCH_SIZE = 10
LLM_TIMEOUT_S = 120


REGATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "verdict": {
                        "type": "string",
                        "enum": ["confirmed_valid", "bogus_mle_error"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["id", "verdict", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}


SYSTEM_PROMPT = """You are re-auditing canonical-form assignments for Arabic lemmas.

Each entry contains:
- `orphan_ar`: Arabic lemma as stored in vocabulary (was thought to be a compound = canonical + stripped clitics)
- `orphan_gloss`: English gloss stored for the orphan
- `proposed_canonical_ar`: CAMeL-MLE's proposed canonical form after clitic stripping
- `proposed_canonical_gloss`: LLM-generated gloss for the proposed canonical (from Step 3's original gate)
- `clitic_signals`: clitics CAMeL claims were stripped

CRITICAL KNOWN FAILURE MODE you MUST check for:

CAMeL MLE frequently misreads the feminine-noun ending ة (tā marbūṭa, U+0629) as a 3ms possessive pronoun suffix ـه. This produces a false decomposition: a feminine noun X-ة gets split into a masculine stem X + fake `enc0: 3ms_poss`. The resulting "canonical" is then a DIFFERENT Arabic lemma — often same root with a related-but-distinct meaning. The previous verdict gate rationalized these by writing bridging glosses. You must NOT do that.

Concrete examples of this failure (verdict: bogus_mle_error):
- orphan سِتَارَة (curtain) → proposed سَتّار (one who conceals). WRONG: سِتَارَة is itself a canonical feminine noun; the ة is the feminine ending, not a clitic.
- orphan سَجَّادَة (carpet) → proposed سَجّاد (prostrator/carpet-seller). WRONG: same failure.
- orphan نَامُوسِيَّة (mosquito net) → proposed نامُوس (law). WRONG: different lemma, different meaning entirely.
- orphan شَارِحَة (colon mark ":") → proposed شارِح (explainer). WRONG: different word that happens to share a root.
- orphan تِينَة (one fig) → proposed تِين (figs, collective). WRONG: singulative/collective are separate dictionary lemmas.
- orphan حِكَّة (an itch, noun) → proposed حَكّ (itching/scratching, verbal noun). WRONG: different forms.

Legitimate decompositions (verdict: confirmed_valid) look like:
- orphan بِسُرْعة (quickly, "with speed") → proposed سُرْعَة (speed). Prefix clitic بِ (bi- "with") stripped. Real compound.
- orphan لِأَحْمَد ((name) for Ahmed) → proposed أَحْمَد (Ahmed). Prefix clitic لِ (li- "for"). Real compound.
- orphan وَأَذْكَى (and smarter) → proposed أَذْكَى (smarter). Prefix clitic وَ (wa- "and"). Real compound.
- orphan أَصَبِعَهُم (their fingers) → proposed إِصْبَع (finger). Genuine 3mp possessive suffix هُم.
- orphan وَتَرَكَهُم (and left them) → proposed تَرَك (left). Prefix وَ + verb object suffix هُم.

Decision rules:
1. If the orphan ends in ـَة AND the only clitic is `enc0: 3ms_poss`, treat as HIGH SUSPICION for the ة-misread failure. The orphan is almost certainly a feminine noun that is itself the canonical; verdict should default to `bogus_mle_error` unless you are genuinely confident it's a real 3ms possessive form (e.g. something like "ابنه" his son — but even then, the DB would usually store the canonical form ابن, not the inflected one).
2. If the proposed canonical is a different dictionary lemma from the orphan (different meaning, even if same root), verdict = `bogus_mle_error`. Gender pairs of adjectives (masc/fem nisba like إسبانِيّ / إسبانِيّة) are SEPARATE lemmas in this system — verdict = `bogus_mle_error`.
3. Singulative/collective pairs (تِين / تِينَة, نَعام / نَعامَة) are separate dictionary lemmas — verdict = `bogus_mle_error`.
4. Only verdict = `confirmed_valid` when the orphan is unambiguously the canonical with a real inflectional clitic (prefix preposition/conjunction, or real pronoun suffix attached to an already-canonical base form).

Be strict. A false `confirmed_valid` corrupts the canonical-variant graph and silently mangles ULK merges; a false `bogus_mle_error` just means we delete a recently-created unreferenced lemma row and no real data is lost.

For each entry return `verdict` and a short `reason` (1-2 sentences explaining the verdict).

Return JSON matching the provided schema. Every input id must appear in results exactly once."""


def load_step3_created() -> list[dict[str, Any]]:
    """Return the 33 Step 3 'created' entries with orphan + canonical context."""
    progress = json.loads(STEP3_PROGRESS.read_text())
    audit = json.loads(AUDIT_JSON.read_text())
    orphan_by_id = {o["lemma_id"]: o for o in audit["buckets"]["orphan_compound"]}

    out = []
    for orphan_id_str, entry in progress["entries"].items():
        if entry["outcome"] != "created":
            continue
        orphan_id = int(orphan_id_str)
        orphan = orphan_by_id.get(orphan_id, {})
        out.append({
            "orphan_id": orphan_id,
            "orphan_ar": orphan.get("lemma_ar", ""),
            "orphan_gloss": orphan.get("gloss_en", "") or "",
            "orphan_bare": orphan.get("lemma_ar_bare", ""),
            "clitic_signals": orphan.get("clitic_signals", {}),
            "new_canonical_id": entry["new_canonical_id"],
            "canonical_ar": entry["canonical_ar"],
            "canonical_gloss": entry["gloss_en"],
            "canonical_pos": entry["pos"],
            "canonical_root": entry.get("root_str", ""),
        })
    return sorted(out, key=lambda x: x["orphan_id"])


def load_progress() -> dict[str, Any]:
    if not REGATE_PROGRESS.exists():
        return {"entries": {}, "started_at": None, "completed_at": None}
    return json.loads(REGATE_PROGRESS.read_text())


def save_progress(progress: dict[str, Any]) -> None:
    REGATE_PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGATE_PROGRESS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(progress, indent=2, ensure_ascii=False))
    tmp.replace(REGATE_PROGRESS)


def build_batch_prompt(batch: list[dict[str, Any]]) -> str:
    payload = []
    for local_id, entry in enumerate(batch, start=1):
        payload.append({
            "id": local_id,
            "orphan_ar": entry["orphan_ar"],
            "orphan_gloss": entry["orphan_gloss"],
            "orphan_bare": entry["orphan_bare"],
            "proposed_canonical_ar": entry["canonical_ar"],
            "proposed_canonical_gloss": entry["canonical_gloss"],
            "proposed_canonical_pos": entry["canonical_pos"],
            "clitic_signals": entry["clitic_signals"],
        })
    return "Re-gate the following entries:\n\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def llm_regate_batch(batch: list[dict[str, Any]]) -> dict[int, dict[str, Any]] | None:
    prompt = build_batch_prompt(batch)
    try:
        resp = generate_completion(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            json_schema=REGATE_SCHEMA,
            temperature=0.1,
            timeout=LLM_TIMEOUT_S,
            model_override="claude_haiku",
            task_type="decomposition_regate",
            cli_only=False,
        )
    except LLMError as e:
        print(f"  LLM error: {e}", flush=True)
        return None

    results = resp.get("results", [])
    by_id: dict[int, dict[str, Any]] = {}
    for r in results:
        lid = r.get("id")
        if isinstance(lid, int):
            by_id[lid] = r
    return by_id


def verify_no_refs(db, lemma_id: int) -> dict[str, int]:
    """Check downstream references for a new canonical. All should be 0."""
    rows = db.execute(text("""
        SELECT
          (SELECT COUNT(*) FROM sentence_words WHERE lemma_id = :id) AS sw,
          (SELECT COUNT(*) FROM review_log    WHERE lemma_id = :id) AS rl,
          (SELECT COUNT(*) FROM sentences     WHERE target_lemma_id = :id) AS st,
          (SELECT COUNT(*) FROM user_lemma_knowledge WHERE lemma_id = :id) AS ulk,
          (SELECT COUNT(*) FROM lemmas WHERE canonical_lemma_id = :id) AS vars
    """), {"id": lemma_id}).fetchone()
    return {"sentence_words": rows[0], "review_log": rows[1], "sent_targets": rows[2], "ulk": rows[3], "variants": rows[4]}


def process_batch(db, batch: list[dict[str, Any]], progress: dict[str, Any], *, dry_run: bool) -> None:
    batch_ids = [e["orphan_id"] for e in batch]
    print(f"  batch orphans {batch_ids[0]}..{batch_ids[-1]} ({len(batch)})", flush=True)
    t_start = time.time()
    results = llm_regate_batch(batch)
    llm_elapsed = time.time() - t_start
    print(f"    LLM returned in {llm_elapsed:.1f}s", flush=True)

    if results is None:
        for entry in batch:
            progress["entries"][str(entry["orphan_id"])] = {
                "outcome": "llm_failed",
                "orphan_ar": entry["orphan_ar"],
                "new_canonical_id": entry["new_canonical_id"],
            }
        save_progress(progress)
        return

    for local_id, entry in enumerate(batch, start=1):
        r = results.get(local_id)
        if r is None:
            progress["entries"][str(entry["orphan_id"])] = {
                "outcome": "llm_failed",
                "reason": "missing from batch response",
                "orphan_ar": entry["orphan_ar"],
                "new_canonical_id": entry["new_canonical_id"],
            }
            continue

        verdict = r.get("verdict")
        reason = r.get("reason", "")
        refs = verify_no_refs(db, entry["new_canonical_id"])

        progress["entries"][str(entry["orphan_id"])] = {
            "outcome": verdict,
            "reason": reason,
            "orphan_ar": entry["orphan_ar"],
            "orphan_gloss": entry["orphan_gloss"],
            "proposed_canonical_ar": entry["canonical_ar"],
            "proposed_canonical_gloss": entry["canonical_gloss"],
            "new_canonical_id": entry["new_canonical_id"],
            "clitic_signals": entry["clitic_signals"],
            "canonical_refs": refs,
        }
        mark = "⚠️ BOGUS" if verdict == "bogus_mle_error" else "✓ valid"
        print(f"    {mark} orphan #{entry['orphan_id']} {entry['orphan_ar']} → canonical #{entry['new_canonical_id']} {entry['canonical_ar']} — {reason[:100]}", flush=True)

    save_progress(progress)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--resume", action="store_true",
                        help="Skip entries already re-gated. Retry llm_failed.")
    args = parser.parse_args()

    created = load_step3_created()
    print(f"Loaded {len(created)} Step 3 created entries", flush=True)

    progress = load_progress()
    if progress.get("started_at") is None:
        progress["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    done_outcomes = {"confirmed_valid", "bogus_mle_error"}
    to_process = []
    for e in created:
        existing = progress["entries"].get(str(e["orphan_id"]))
        if args.resume and existing and existing["outcome"] in done_outcomes:
            continue
        to_process.append(e)
    print(f"Will re-gate {len(to_process)} (already done: {len(created) - len(to_process)})", flush=True)
    if not to_process:
        return 0

    db = SessionLocal()
    try:
        for i in range(0, len(to_process), args.batch_size):
            batch = to_process[i:i + args.batch_size]
            print(f"\n[{i + 1}..{i + len(batch)} of {len(to_process)}]", flush=True)
            process_batch(db, batch, progress, dry_run=args.dry_run)

        progress["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        save_progress(progress)
    finally:
        db.close()

    counts: dict[str, int] = {}
    for entry in progress["entries"].values():
        counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
    print("\n=== Summary ===", flush=True)
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}", flush=True)
    print(f"Progress file: {REGATE_PROGRESS}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
