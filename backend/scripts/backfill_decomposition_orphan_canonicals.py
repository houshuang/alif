#!/usr/bin/env python3
"""Phase 2 Step 3: create canonical Lemma rows for 102 orphan compound lemmas.

Reads research/decomposition-classification-2026-04-24.json, filters to
``orphan_compound`` bucket (102 entries), and for each entry:

1. Re-checks via a narrow ``lemma_ar_bare``-only lookup whether the canonical
   already exists in DB. (The audit snapshot may have drifted since it ran
   — the Step 1 patch could have prevented creation, or another import path
   could have created it.) If it exists, record ``already_canonical`` and
   skip. The narrow lookup mirrors the audit's own dedup; using the broader
   ``resolve_existing_lemma`` would over-match via generated verb
   conjugations and al-prefix variants of unrelated lemmas.

2. Sends a batch of ~10 orphans to ``claude_haiku`` (CLI) with a JSON schema
   that returns, per orphan: verdict (``valid`` | ``mle_error``), gloss_en,
   pos, and root. The verdict field is the MLE-noise guard — lemmas like
   ``كِرَاءٌ`` ("rent"), where CAMeL wrongly split ``ك + راء``, are flagged
   ``mle_error`` and skipped.

3. For ``valid`` entries: create ``Lemma(lemma_ar=mle_lex,
   lemma_ar_bare=mle_lex_norm, gloss_en=..., pos=..., source=
   'backfill_decomposition_audit')``, then call ``run_quality_gates`` with
   ``enrich=False`` (gate-3 enrichment is decoupled; gloss/pos/root are
   already LLM-generated, forms/etymology are not required for Step 4).

4. Does NOT link the orphan compound row to the new canonical — that is
   Step 4's job and requires a separate spot-check pass.

The script is resumable. A progress file at
``backend/data/decomposition_backfill_progress.json`` records, per orphan
lemma_id, the outcome: ``created`` (with new canonical_id), ``already_canonical``
(with resolved id), ``mle_error``, or ``llm_failed``. A re-run skips entries
that were ``created`` or ``already_canonical`` and retries ``llm_failed`` ones.

Usage
-----
    python3 scripts/backfill_decomposition_orphan_canonicals.py --dry-run
    python3 scripts/backfill_decomposition_orphan_canonicals.py --limit 5
    python3 scripts/backfill_decomposition_orphan_canonicals.py          # full run

Environment
-----------
    DATABASE_URL     override the DB path (pydantic Settings; default
                     points at ``backend/data/alif.db``). Example:
                     ``DATABASE_URL=sqlite:///$(pwd)/data/alif.prod.db``
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
from app.models import Lemma
from app.services.lemma_quality import run_quality_gates
from app.services.llm import LLMError, generate_completion
from app.services.sentence_validator import normalize_alef


AUDIT_JSON = BACKEND_ROOT.parent / "research" / "decomposition-classification-2026-04-24.json"
PROGRESS_FILE = BACKEND_ROOT / "data" / "decomposition_backfill_progress.json"
BATCH_SIZE = 10
LLM_TIMEOUT_S = 120
LEMMA_SOURCE = "backfill_decomposition_audit"

BATCH_SCHEMA: dict[str, Any] = {
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
                        "enum": ["valid", "mle_error"],
                    },
                    "gloss_en": {"type": "string"},
                    "pos": {
                        "type": "string",
                        "enum": ["noun", "verb", "adj", "adv", "particle", "pron", "expr"],
                    },
                    "root": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["id", "verdict"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}


SYSTEM_PROMPT = """You are an Arabic morphology expert verifying clitic-stripped canonical forms.

You will receive a list of Arabic compound lemmas, each with:
- `compound`: the full compound form as stored in the vocabulary DB (e.g. بِسُرْعة)
- `compound_gloss`: the English gloss stored for the compound (hint only — may differ from canonical meaning)
- `canonical`: the CAMeL-MLE-derived canonical form with clitics stripped (e.g. سُرْعَة)
- `canonical_bare`: the undiacritized form of the canonical (e.g. سرعه)
- `clitic_signals`: the clitics CAMeL claims were stripped

Your job, for each entry:
1. Decide `verdict`:
   - `valid` — the decomposition is real. `canonical` is a legitimate Arabic word and `compound` is that word plus the claimed clitics.
   - `mle_error` — the decomposition is bogus. Classic cases: the compound is a single indivisible lemma and CAMeL hallucinated a clitic (e.g. كِرَاء "rent" misread as كـ + راء), or the canonical isn't a real word in MSA/classical Arabic.
2. If `valid`, provide:
   - `gloss_en`: concise English gloss of the CANONICAL form (not the compound). Multiple senses separated by `; `.
   - `pos`: one of `noun`, `verb`, `adj`, `adv`, `particle`, `pron`, `expr`.
   - `root`: the triliteral/quadriliteral root as dot-separated letters (e.g. `س.ر.ع` for سُرْعَة). Use `?` if no clear root (frozen particles, loanwords).
3. If `mle_error`, set `note` to a short reason (one sentence).

Return a single JSON object matching the provided schema. Every input id must appear in results exactly once."""


def load_orphans() -> list[dict[str, Any]]:
    with open(AUDIT_JSON) as f:
        data = json.load(f)
    return data["buckets"]["orphan_compound"]


def load_progress() -> dict[str, Any]:
    if not PROGRESS_FILE.exists():
        return {"entries": {}, "started_at": None, "completed_at": None}
    with open(PROGRESS_FILE) as f:
        return json.load(f)


def save_progress(progress: dict[str, Any]) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_FILE.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(progress, f, indent=2, ensure_ascii=False)
    tmp.replace(PROGRESS_FILE)


def build_batch_prompt(batch: list[dict[str, Any]]) -> str:
    payload = []
    for local_id, entry in enumerate(batch, start=1):
        payload.append({
            "id": local_id,
            "compound": entry["lemma_ar"],
            "compound_gloss": entry.get("gloss_en") or "",
            "canonical": entry["mle_lex"],
            "canonical_bare": entry["mle_lex_norm"],
            "clitic_signals": entry.get("clitic_signals") or {},
        })
    return "Process each of the following entries:\n\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )


def llm_enrich_batch(batch: list[dict[str, Any]]) -> dict[int, dict[str, Any]] | None:
    """Call Claude Haiku with the batch. Returns dict keyed by local 1-based id,
    or None if the LLM call failed."""
    prompt = build_batch_prompt(batch)
    try:
        resp = generate_completion(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            json_schema=BATCH_SCHEMA,
            temperature=0.2,
            timeout=LLM_TIMEOUT_S,
            model_override="claude_haiku",
            task_type="decomposition_backfill",
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


def build_primary_bare_lookup(db) -> dict[str, int]:
    """Narrow lookup of normalized ``lemma_ar_bare`` → ``lemma_id``.

    Mirrors the audit classifier's own dedup: only the direct ``lemma_ar_bare``
    column, no derived ``forms_json`` entries, no generated verb conjugations,
    no al-prefix variants. That's the right check for "does this canonical
    already exist as a primary lemma row" — which is what Step 3 cares about.

    ``resolve_existing_lemma`` / ``build_lemma_lookup`` would be too broad here:
    they match generated verb conjugations (e.g. a 3p-fem-sg form of سَرَّع
    equal to سرعه) against the canonical we want to create, incorrectly
    claiming it's already in DB.
    """
    rows = db.execute(text("SELECT lemma_id, lemma_ar_bare FROM lemmas")).fetchall()
    lookup: dict[str, int] = {}
    for lid, bare in rows:
        if bare:
            norm = normalize_alef(bare)
            if norm and norm not in lookup:
                lookup[norm] = lid
    return lookup


def process_batch(
    db,
    batch: list[dict[str, Any]],
    lemma_lookup: dict[str, int],
    progress: dict[str, Any],
    *,
    dry_run: bool,
) -> None:
    batch_ids = [e["lemma_id"] for e in batch]
    print(f"  batch {batch_ids[0]}..{batch_ids[-1]} ({len(batch)} orphans)", flush=True)

    # Re-check dedup first — may shrink the batch before LLM call.
    to_enrich: list[dict[str, Any]] = []
    for entry in batch:
        bare = normalize_alef(entry["mle_lex_norm"])
        existing = lemma_lookup.get(bare)
        if existing is not None:
            progress["entries"][str(entry["lemma_id"])] = {
                "outcome": "already_canonical",
                "canonical_lemma_id": existing,
                "canonical_bare": bare,
            }
            print(f"    skip #{entry['lemma_id']} {entry['lemma_ar']} → canonical #{existing} already exists", flush=True)
        else:
            to_enrich.append(entry)

    if not to_enrich:
        save_progress(progress)
        return

    t_start = time.time()
    results = llm_enrich_batch(to_enrich)
    llm_elapsed = time.time() - t_start
    print(f"    LLM returned in {llm_elapsed:.1f}s", flush=True)

    if results is None:
        for entry in to_enrich:
            progress["entries"][str(entry["lemma_id"])] = {
                "outcome": "llm_failed",
                "canonical_bare": entry["mle_lex_norm"],
            }
        save_progress(progress)
        return

    new_lemma_ids: list[int] = []
    for local_id, entry in enumerate(to_enrich, start=1):
        r = results.get(local_id)
        if r is None:
            progress["entries"][str(entry["lemma_id"])] = {
                "outcome": "llm_failed",
                "reason": "missing from batch response",
            }
            continue

        if r.get("verdict") != "valid":
            progress["entries"][str(entry["lemma_id"])] = {
                "outcome": "mle_error",
                "reason": r.get("note", ""),
                "canonical_bare": entry["mle_lex_norm"],
            }
            print(f"    mle_error #{entry['lemma_id']} {entry['lemma_ar']} — {r.get('note', '')[:80]}", flush=True)
            continue

        gloss = (r.get("gloss_en") or "").strip()
        pos = (r.get("pos") or "").strip()
        root = (r.get("root") or "").strip()
        if not gloss or not pos:
            progress["entries"][str(entry["lemma_id"])] = {
                "outcome": "llm_failed",
                "reason": "empty gloss or pos in verdict=valid response",
            }
            continue

        if dry_run:
            progress["entries"][str(entry["lemma_id"])] = {
                "outcome": "dry_run",
                "would_create": {
                    "lemma_ar": entry["mle_lex"],
                    "lemma_ar_bare": entry["mle_lex_norm"],
                    "gloss_en": gloss,
                    "pos": pos,
                    "root": root,
                },
            }
            print(f"    dry-run #{entry['lemma_id']} → would create {entry['mle_lex']} ({gloss}, {pos}, {root})", flush=True)
            continue

        lemma = Lemma(
            lemma_ar=entry["mle_lex"],
            lemma_ar_bare=entry["mle_lex_norm"],
            gloss_en=gloss,
            pos=pos,
            source=LEMMA_SOURCE,
        )
        db.add(lemma)
        db.flush()
        new_lemma_ids.append(lemma.lemma_id)
        lemma_lookup[normalize_alef(entry["mle_lex_norm"])] = lemma.lemma_id

        progress["entries"][str(entry["lemma_id"])] = {
            "outcome": "created",
            "new_canonical_id": lemma.lemma_id,
            "canonical_ar": entry["mle_lex"],
            "canonical_bare": entry["mle_lex_norm"],
            "gloss_en": gloss,
            "pos": pos,
            "root_str": root,
        }
        print(f"    created #{lemma.lemma_id} {entry['mle_lex']} ({gloss}, {pos}) ← orphan #{entry['lemma_id']}", flush=True)

    # Commit the batch's inserts first — releases the write lock before the
    # quality gates do their own work (they commit internally). This avoids
    # holding the single-writer lock across any slow step in run_quality_gates.
    if new_lemma_ids and not dry_run:
        db.commit()
        run_quality_gates(
            db,
            new_lemma_ids,
            skip_variants=False,
            enrich=False,
            background_enrich=False,
        )

    save_progress(progress)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Run LLM calls but do not insert into DB.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process only the first N orphans (for testing).")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--retry-failed", action="store_true",
                        help="Re-process entries previously marked llm_failed.")
    args = parser.parse_args()

    orphans = load_orphans()
    print(f"Loaded {len(orphans)} orphans from {AUDIT_JSON.name}", flush=True)

    progress = load_progress()
    if progress.get("started_at") is None:
        progress["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    done_outcomes = {"created", "already_canonical", "mle_error", "dry_run"}
    if args.retry_failed:
        done_outcomes.discard("dry_run")  # redo dry_run on retry

    to_process = []
    for o in orphans:
        entry_progress = progress["entries"].get(str(o["lemma_id"]))
        if entry_progress and entry_progress["outcome"] in done_outcomes:
            continue
        to_process.append(o)

    if args.limit is not None:
        to_process = to_process[:args.limit]

    print(f"Will process {len(to_process)} orphans "
          f"(already done: {len(orphans) - len(to_process)})", flush=True)

    if not to_process:
        print("Nothing to do.", flush=True)
        return 0

    db = SessionLocal()
    try:
        lemma_lookup = build_primary_bare_lookup(db)
        print(f"Loaded primary-bare lookup: {len(lemma_lookup)} keys", flush=True)

        for i in range(0, len(to_process), args.batch_size):
            batch = to_process[i:i + args.batch_size]
            print(f"\n[{i + 1}..{i + len(batch)} of {len(to_process)}]", flush=True)
            process_batch(db, batch, lemma_lookup, progress, dry_run=args.dry_run)

        progress["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        save_progress(progress)
    finally:
        db.close()

    # Summary
    counts: dict[str, int] = {}
    for entry in progress["entries"].values():
        counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
    print("\n=== Summary ===", flush=True)
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}", flush=True)
    print(f"Progress file: {PROGRESS_FILE}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
