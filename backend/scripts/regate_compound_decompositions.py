#!/usr/bin/env python3
"""Phase 2 Step 4c: re-gate the 161 compound_with_canonical entries.

Bucket origin
-------------
Phase-1 audit (research/decomposition-classification-2026-04-24.json) sorted
2,905 lemmas into four buckets. The ``compound_with_canonical`` bucket
(161 entries) holds compounds whose CAMeL-MLE analysis points at a canonical
that ALREADY exists in the DB. Two sub-cases:

- **77 already-linked**  -- ``canonical_lemma_id`` is set in DB to the resolved
  canonical. We are auditing whether that link is correct.
- **84 unlinked**        -- ``canonical_lemma_id`` is NULL in DB but the audit
  resolved a candidate canonical. Step 4c-B may link them after this gate.

Confidence tiers from the audit (re-checked by LLM here regardless):
HIGH=144, MEDIUM=4, LOW=13.

Failure modes carried from Step 4a-prime
----------------------------------------
- **CAMeL ة -> 3ms_poss misread**: feminine nouns ending in ـة decomposed
  as masculine stem + fake 3ms possessive suffix. 22/33 false-positives in
  the orphan re-gate had this pattern (67%).
- **Same-root different-lemma**: gender pairs (إسبانيّ / إسبانيّة),
  singulative/collective (تين / تينة), verbal-noun / noun pairs are
  SEPARATE dictionary lemmas, NOT decompositions.

Verdict enum (4 outcomes)
-------------------------
- ``confirmed_valid_link`` -- compound IS canonical + real clitics. Safe to
  link (4c-B handles unlinked) or leave linked.
- ``bogus_mle_error``      -- compound is NOT a decomposition of canonical.
  Tag with mle_misanalysis.
- ``wrong_canonical_real_compound`` -- compound IS some real decomposition
  but proposed canonical is the wrong dictionary lemma. Tag with
  wrong_canonical (suggested_canonical_bare optional).
- ``uncertain`` -- model could not decide. Manual queue.

Two-pass asymmetric verification
--------------------------------
False-``bogus`` is more harmful than missed-``bogus`` (a false tag biases
future re-mapping passes; a missed tag preserves status quo). Pass 1
classifies all entries; pass 2 re-checks only non-``confirmed_valid_link``
verdicts with a flipped framing biased toward keeping. Disagreements between
the two passes are downgraded to ``uncertain`` and surfaced for manual
review.

Output
------
Per-orphan JSON record at ``backend/data/decomposition_step4c_progress.json``:
    {
      "<orphan_id>": {
        "outcome": <verdict>,
        "reason_pass1": "...",
        "reason_pass2": "..." (if re-checked),
        "agreement": True/False,
        "in_db_link_state": "linked"|"unlinked",
        "orphan_ar": ..., "orphan_gloss": ...,
        "proposed_canonical_id": ..., "proposed_canonical_ar": ...,
        "proposed_canonical_gloss": ..., "proposed_canonical_pos": ...,
        "clitic_signals": {...},
        "confidence_tier": "HIGH"|"MEDIUM"|"LOW",
        "suggested_canonical_bare": "..." (only for wrong_canonical_real_compound),
      },
      ...
    }

Read-only: this script makes ZERO DB writes. Tagging happens in
``apply_step4c_tags.py``; linking happens in ``apply_step4c_link_survivors.py``.

Usage
-----
    python3 scripts/regate_compound_decompositions.py --dry-run           # default
    python3 scripts/regate_compound_decompositions.py --resume            # skip done
    DATABASE_URL=sqlite:///$(pwd)/data/alif.prod.db \\
        python3 scripts/regate_compound_decompositions.py
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


AUDIT_JSON = BACKEND_ROOT.parent / "research" / "decomposition-classification-2026-04-24.json"
PROGRESS_FILE = BACKEND_ROOT / "data" / "decomposition_step4c_progress.json"
BATCH_SIZE = 10
LLM_TIMEOUT_S = 180
MODEL = "claude_sonnet"


PASS1_VERDICT_SCHEMA: dict[str, Any] = {
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
                        "enum": [
                            "confirmed_valid_link",
                            "bogus_mle_error",
                            "wrong_canonical_real_compound",
                            "uncertain",
                        ],
                    },
                    "reason": {"type": "string"},
                    "suggested_canonical_bare": {"type": "string"},
                },
                "required": ["id", "verdict", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}


PASS2_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "agree_with_pass1": {"type": "boolean"},
                    "revised_verdict": {
                        "type": "string",
                        "enum": [
                            "confirmed_valid_link",
                            "bogus_mle_error",
                            "wrong_canonical_real_compound",
                            "uncertain",
                        ],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["id", "agree_with_pass1", "revised_verdict", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}


PASS1_SYSTEM_PROMPT = """You audit Arabic compound-lemma decompositions.

Each entry has:
- `orphan_ar`         : lemma stored in vocabulary (CAMeL-MLE thinks it is canonical + clitics)
- `orphan_gloss`      : English gloss for the orphan
- `proposed_canonical_ar`    : CAMeL's proposed canonical form
- `proposed_canonical_gloss` : DB gloss for the canonical
- `proposed_canonical_pos`   : POS of the canonical in our DB
- `orphan_db_pos`     : POS of the orphan in our DB (if known)
- `clitic_signals`    : clitics CAMeL claims were stripped to get from orphan to canonical

You must return ONE of four verdicts per entry:

1. `confirmed_valid_link`
   The orphan IS the canonical with real Arabic clitics attached. Examples:
     - bi-سُرْعة "with speed" -> canonical سُرْعَة + prefix بِ. valid.
     - li-أحمد "for Ahmed" -> canonical أحمد + prefix لِ. valid.
     - wa-تَرَكَهُم "and left them" -> canonical تَرَك + wa- + 3mp suffix هم. valid.
     - عِنْدي "I have" -> canonical عِنْد + 1s pronoun ي. valid.

2. `bogus_mle_error`
   The decomposition is wrong; the orphan is its own dictionary lemma.

   CRITICAL FAILURE MODE: feminine ة (tā marbūṭa, U+0629) misread as 3ms-poss ـه.
   Pattern: feminine noun X-ة gets split into masculine stem X + fake 3ms_poss enc0.
   The "canonical" is then a DIFFERENT dictionary lemma.

   Bogus examples:
     - سِتَارَة "curtain" -> proposed سَتّار "concealer". WRONG: ة is feminine ending.
     - سَجَّادَة "carpet" -> proposed سَجّاد. WRONG: same failure.
     - نَامُوسِيَّة "mosquito net" -> proposed نامُوس "law". WRONG: different lemma.
     - تِينَة "one fig" -> proposed تِين "figs (collective)". WRONG: singulative != collective.
     - حِكَّة "an itch (n)" -> proposed حَكّ "scratching (vn)". WRONG: separate lemmas.
     - إسبانِيّة (fem. nisba) -> proposed إسبانِيّ (masc. nisba). WRONG: separate lemmas.

   Also bogus when the proposed canonical is a DIFFERENT root, or when POS
   conflict makes the decomposition implausible (e.g. orphan is `prep` but
   "canonical" is a `verb` and the orphan is not a participial form).

3. `wrong_canonical_real_compound`
   The orphan IS a genuine compound but the proposed canonical is wrong.
   Example: orphan `بَجانِبِ` "next to (prep)" with proposed canonical جانَب (verb,
   "to be alongside"). The orphan is بِ + جانِب (noun, "side"), not the verb.
   You MAY include `suggested_canonical_bare` if the right one is obvious.

4. `uncertain`
   You cannot decide between confirmed_valid_link and bogus.

Decision rules (apply in order):
- If orphan ends in ـة AND only clitic is `enc0: 3ms_poss` -> default to bogus_mle_error
  unless you can name a specific reason it really IS a 3ms-poss form.
- Gender pairs of nisba/adjective and singulative/collective pairs -> bogus_mle_error.
- POS mismatch where the canonical's POS makes the decomposition implausible
  -> usually wrong_canonical_real_compound (if the compound is a clear prep+noun
  or similar) or bogus_mle_error.
- Be strict. False `confirmed_valid_link` corrupts the canonical-variant graph.

Return JSON matching the schema. Every input id must appear exactly once."""


PASS2_SYSTEM_PROMPT = """You are double-checking decomposition verdicts that flagged something
as bogus or wrong-canonical. The cost of a false-bogus tag is HIGHER than the
cost of a missed tag, so this pass is biased toward KEEPING the link.

For each entry you receive:
- The same compound + proposed canonical context as the original gate.
- The pass-1 verdict and its reason.

You must:
1. Re-evaluate from scratch -- do NOT just confirm the prior reason.
2. Set `agree_with_pass1 = true` only if you independently reach the same verdict.
3. If you disagree, set `agree_with_pass1 = false` and provide
   `revised_verdict` with the verdict YOU think is correct.
4. If you cannot decide, return `revised_verdict = "uncertain"`.

The same KNOWN FAILURE MODES from pass 1 apply (ة -> 3ms_poss misread, etc.),
but you should also actively look for reasons the pass-1 verdict might be
overcautious:

- Genuinely-existing 3ms_poss forms (e.g. ابنه "his son") DO occur even when
  the orphan ends in ه/ة. If the gloss makes the possessive reading natural,
  it may be valid.
- Some prefix particles (بِ, لِ, وَ, فَ, كَ, سَ) DO attach to nouns/verbs
  legitimately. A bi+noun like بِجانب "next to" is a real compound even if the
  CANONICAL POS resolution went to a verb -- in that case you would say
  `wrong_canonical_real_compound`, not `bogus_mle_error`.

Return JSON matching the schema."""


def load_entries() -> list[dict[str, Any]]:
    """Return all 161 compound_with_canonical entries with regate context."""
    audit = json.loads(AUDIT_JSON.read_text())
    cwc = audit["buckets"]["compound_with_canonical"]
    return sorted(cwc, key=lambda x: x["lemma_id"])


def fetch_canonical_meta(db, canonical_id: int) -> dict[str, Any]:
    """Pull canonical's current ar/gloss/pos from DB (audit JSON only has resolved id)."""
    row = db.execute(
        text("SELECT lemma_ar, gloss_en, pos FROM lemmas WHERE lemma_id = :id"),
        {"id": canonical_id},
    ).fetchone()
    if row is None:
        return {"ar": "", "gloss": "", "pos": ""}
    return {"ar": row[0] or "", "gloss": row[1] or "", "pos": row[2] or ""}


def build_pass1_payload(batch: list[dict[str, Any]], canonical_meta: dict[int, dict[str, Any]]) -> str:
    payload = []
    for local_id, e in enumerate(batch, start=1):
        cm = canonical_meta.get(e["canonical_lemma_id_resolved"], {})
        payload.append({
            "id": local_id,
            "orphan_ar": e["lemma_ar"],
            "orphan_gloss": e.get("gloss_en") or "",
            "orphan_db_pos": e.get("db_pos") or "",
            "proposed_canonical_ar": cm.get("ar", ""),
            "proposed_canonical_gloss": cm.get("gloss", ""),
            "proposed_canonical_pos": cm.get("pos", ""),
            "clitic_signals": e.get("clitic_signals") or {},
        })
    return "Audit the following entries:\n\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def build_pass2_payload(batch: list[dict[str, Any]], pass1_results: dict[int, dict[str, Any]],
                        canonical_meta: dict[int, dict[str, Any]]) -> str:
    payload = []
    for local_id, e in enumerate(batch, start=1):
        cm = canonical_meta.get(e["canonical_lemma_id_resolved"], {})
        prev = pass1_results.get(local_id, {})
        payload.append({
            "id": local_id,
            "orphan_ar": e["lemma_ar"],
            "orphan_gloss": e.get("gloss_en") or "",
            "orphan_db_pos": e.get("db_pos") or "",
            "proposed_canonical_ar": cm.get("ar", ""),
            "proposed_canonical_gloss": cm.get("gloss", ""),
            "proposed_canonical_pos": cm.get("pos", ""),
            "clitic_signals": e.get("clitic_signals") or {},
            "pass1_verdict": prev.get("verdict"),
            "pass1_reason": prev.get("reason"),
        })
    return "Re-check these flagged verdicts:\n\n" + json.dumps(payload, ensure_ascii=False, indent=2)


def llm_call(prompt: str, system_prompt: str, schema: dict[str, Any]) -> dict[int, dict[str, Any]] | None:
    try:
        resp = generate_completion(
            prompt=prompt,
            system_prompt=system_prompt,
            json_schema=schema,
            temperature=0.1,
            timeout=LLM_TIMEOUT_S,
            model_override=MODEL,
            task_type="decomposition_step4c",
            cli_only=False,
        )
    except LLMError as e:
        print(f"  LLM error: {e}", flush=True)
        return None

    by_id: dict[int, dict[str, Any]] = {}
    for r in resp.get("results", []):
        lid = r.get("id")
        if isinstance(lid, int):
            by_id[lid] = r
    return by_id


def load_progress() -> dict[str, Any]:
    if not PROGRESS_FILE.exists():
        return {"entries": {}, "started_at": None, "completed_at": None}
    return json.loads(PROGRESS_FILE.read_text())


def save_progress(progress: dict[str, Any]) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(progress, indent=2, ensure_ascii=False))
    tmp.replace(PROGRESS_FILE)


def reconcile(pass1: dict[str, Any] | None, pass2: dict[str, Any] | None) -> tuple[str, bool]:
    """Combine the two passes into a final verdict.

    Returns (final_verdict, agreement).
    Rules:
      - If pass1 is confirmed_valid_link -> keep, no pass2 needed (asymmetric).
      - If pass2 agrees -> use pass1 verdict.
      - If pass2 disagrees -> downgrade to "uncertain" (manual queue).
      - If pass2 is missing for a non-confirmed verdict -> "uncertain".
    """
    if not pass1:
        return "uncertain", False
    v1 = pass1.get("verdict") or "uncertain"
    if v1 == "confirmed_valid_link":
        return v1, True
    if not pass2:
        return "uncertain", False
    if pass2.get("agree_with_pass1"):
        return v1, True
    revised = pass2.get("revised_verdict") or "uncertain"
    # Disagreement: only trust if pass2 also resolves to a definite verdict matching v1.
    # Otherwise downgrade.
    if revised == v1:
        return v1, True
    return "uncertain", False


def process_batch(db, batch: list[dict[str, Any]], progress: dict[str, Any]) -> None:
    batch_ids = [e["lemma_id"] for e in batch]
    print(f"  batch lemmas {batch_ids[0]}..{batch_ids[-1]} ({len(batch)})", flush=True)

    canonical_ids = {e["canonical_lemma_id_resolved"] for e in batch}
    canonical_meta = {cid: fetch_canonical_meta(db, cid) for cid in canonical_ids}

    # Pass 1
    t0 = time.time()
    pass1_prompt = build_pass1_payload(batch, canonical_meta)
    pass1_results = llm_call(pass1_prompt, PASS1_SYSTEM_PROMPT, PASS1_VERDICT_SCHEMA)
    print(f"    pass1: {time.time() - t0:.1f}s", flush=True)

    if pass1_results is None:
        for entry in batch:
            progress["entries"][str(entry["lemma_id"])] = {
                "outcome": "llm_failed_pass1",
                "orphan_ar": entry["lemma_ar"],
            }
        save_progress(progress)
        return

    # Identify entries that need pass 2 (any non-confirmed_valid_link verdict)
    needs_pass2: list[tuple[int, dict[str, Any]]] = []
    for local_id, entry in enumerate(batch, start=1):
        r = pass1_results.get(local_id)
        if r is None:
            continue
        if r.get("verdict") != "confirmed_valid_link":
            needs_pass2.append((local_id, entry))

    pass2_results: dict[int, dict[str, Any]] = {}
    if needs_pass2:
        # Build a pass2 batch that preserves local ids (so the LLM mapping stays consistent).
        # We send only the flagged ones but renumber to 1..N, then map back.
        pass2_batch = [e for _, e in needs_pass2]
        pass2_subset = {local_id: pass1_results[local_id] for local_id, _ in needs_pass2}

        # Renumbering: pass2 LLM sees 1..N; we map back via (renumbered_id -> original local_id)
        renumber_map = {i + 1: original_local_id for i, (original_local_id, _) in enumerate(needs_pass2)}
        renumbered_pass1 = {i + 1: pass2_subset[orig_local] for i, (orig_local, _) in enumerate(needs_pass2)}

        t0 = time.time()
        pass2_prompt = build_pass2_payload(pass2_batch, renumbered_pass1, canonical_meta)
        pass2_raw = llm_call(pass2_prompt, PASS2_SYSTEM_PROMPT, PASS2_VERDICT_SCHEMA)
        print(f"    pass2: {time.time() - t0:.1f}s ({len(needs_pass2)} re-checked)", flush=True)

        if pass2_raw is not None:
            for renumber_id, p2 in pass2_raw.items():
                orig = renumber_map.get(renumber_id)
                if orig is not None:
                    pass2_results[orig] = p2

    # Reconcile + write
    for local_id, entry in enumerate(batch, start=1):
        p1 = pass1_results.get(local_id)
        p2 = pass2_results.get(local_id)
        final_verdict, agreement = reconcile(p1, p2)

        cm = canonical_meta.get(entry["canonical_lemma_id_resolved"], {})
        rec: dict[str, Any] = {
            "outcome": final_verdict,
            "agreement": agreement,
            "reason_pass1": (p1 or {}).get("reason", ""),
            "in_db_link_state": "linked" if entry.get("canonical_lemma_id") else "unlinked",
            "orphan_ar": entry["lemma_ar"],
            "orphan_gloss": entry.get("gloss_en") or "",
            "orphan_db_pos": entry.get("db_pos") or "",
            "proposed_canonical_id": entry["canonical_lemma_id_resolved"],
            "proposed_canonical_ar": cm.get("ar", ""),
            "proposed_canonical_gloss": cm.get("gloss", ""),
            "proposed_canonical_pos": cm.get("pos", ""),
            "clitic_signals": entry.get("clitic_signals") or {},
            "confidence_tier": entry.get("confidence", ""),
        }
        if p2 is not None:
            rec["reason_pass2"] = p2.get("reason", "")
            rec["pass2_revised_verdict"] = p2.get("revised_verdict")
        if p1 and p1.get("suggested_canonical_bare"):
            rec["suggested_canonical_bare"] = p1["suggested_canonical_bare"]

        progress["entries"][str(entry["lemma_id"])] = rec

        glyph = {
            "confirmed_valid_link": "✓ valid",
            "bogus_mle_error": "✗ BOGUS",
            "wrong_canonical_real_compound": "→ wrong_canonical",
            "uncertain": "? UNCERTAIN",
            "llm_failed_pass1": "! llm_fail",
        }.get(final_verdict, final_verdict)
        link_marker = "L" if entry.get("canonical_lemma_id") else "U"
        print(f"    [{link_marker}] {glyph} #{entry['lemma_id']} {entry['lemma_ar']} -> "
              f"#{entry['canonical_lemma_id_resolved']} {cm.get('ar', '')}", flush=True)

    save_progress(progress)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--resume", action="store_true",
                        help="Skip entries with finalized verdict. Retry llm_failed.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N entries (for smoke testing).")
    args = parser.parse_args()

    entries = load_entries()
    print(f"Loaded {len(entries)} compound_with_canonical entries", flush=True)

    progress = load_progress()
    if progress.get("started_at") is None:
        progress["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    done_outcomes = {"confirmed_valid_link", "bogus_mle_error",
                     "wrong_canonical_real_compound", "uncertain"}
    to_process = []
    for e in entries:
        existing = progress["entries"].get(str(e["lemma_id"]))
        if args.resume and existing and existing.get("outcome") in done_outcomes:
            continue
        to_process.append(e)
    if args.limit is not None:
        to_process = to_process[:args.limit]
    print(f"Will re-gate {len(to_process)} (already done: {len(entries) - len(to_process)})", flush=True)
    if not to_process:
        return 0

    db = SessionLocal()
    try:
        for i in range(0, len(to_process), args.batch_size):
            batch = to_process[i:i + args.batch_size]
            print(f"\n[{i + 1}..{i + len(batch)} of {len(to_process)}]", flush=True)
            process_batch(db, batch, progress)

        progress["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        save_progress(progress)
    finally:
        db.close()

    counts: dict[str, int] = {}
    by_link_state: dict[tuple[str, str], int] = {}
    for entry in progress["entries"].values():
        outcome = entry.get("outcome", "unknown")
        counts[outcome] = counts.get(outcome, 0) + 1
        link_state = entry.get("in_db_link_state", "?")
        by_link_state[(outcome, link_state)] = by_link_state.get((outcome, link_state), 0) + 1
    print("\n=== Summary ===", flush=True)
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}", flush=True)
    print("\n=== By link state ===", flush=True)
    for (outcome, link), v in sorted(by_link_state.items()):
        print(f"  {outcome:35s} {link:10s} {v}", flush=True)
    print(f"\nProgress file: {PROGRESS_FILE}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
