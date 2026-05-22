#!/usr/bin/env python3
"""Audit and clean up lemmas whose etymology_json describes a different word.

Background
----------
Etymology is generated per-word by Claude Haiku (``lemma_enrichment._generate_
etymology_batch``). The model is free to frame a word as a native Arabic root
derivation or a foreign loanword. For تَوْب "repentance" (root ت.و.ب) it
hallucinated a complete "From English 'laptop'..." etymology off the bare-string
coincidence (توب ≈ the "-top" tail of لابتوب), ignoring the gloss and root it
was given. Nothing validated that the etymology actually relates to the word's
meaning, so the wrong content shipped verbatim. The user has seen this
"translation and etymology don't match" pattern on other words too.

This is the one-time backstop sweep. Going forward the bug is prevented by:
  - a hardened etymology prompt (anchor on gloss + root),
  - an inline coherence gate in enrich_lemmas_batch, and
  - the D6 dimension of chimera_audit (recurring detection).

What it does
------------
High-signal scope: loanword-mode etymologies (no root_meaning / pattern,
"From <lang>..." derivation) on canonical lemmas that HAVE an Arabic root —
the same signature تَوْب matches. Each is confirmed with the shared
``verify_etymology_coherence_batch`` (gloss "jacket" ↔ "From English 'jacket'"
passes; gloss "repentance" ↔ a laptop etymology fails). Confirmed-incoherent
etymologies are set to NULL so the next enrichment pass regenerates them with
the hardened prompt. ``--regenerate`` re-enriches the cleared lemmas inline.

Modes
-----
  --dry-run        Report only; no DB writes (default behaviour is to apply).
  --regenerate     After NULLing, re-run enrichment on the cleared lemmas.
  --limit N        Cap the candidate set (testing).
  --verbose        Print per-lemma decisions.

Output
------
Writes a JSON report to backend/data/etymology_mismatch_audit.json. Logs a
``manual_action`` ActivityLog entry on apply runs.

Usage
-----
    python3 scripts/cleanup_etymology_mismatches.py --dry-run
    python3 scripts/cleanup_etymology_mismatches.py
    python3 scripts/cleanup_etymology_mismatches.py --regenerate
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models import Lemma, Root  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402
from app.services.lemma_enrichment import (  # noqa: E402
    verify_etymology_coherence_batch,
)

REPORT_FILE = BACKEND_ROOT / "data" / "etymology_mismatch_audit.json"
BATCH_SIZE = 10


def _is_loanword_mode(etym: dict) -> bool:
    return (
        isinstance(etym, dict)
        and not etym.get("root_meaning")
        and not etym.get("pattern")
        and (etym.get("derivation") or "").strip().lower().startswith("from ")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report only; no DB writes.")
    parser.add_argument("--regenerate", action="store_true",
                        help="After NULLing, re-enrich the cleared lemmas inline.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap candidate set (testing).")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # High-signal candidate set: loanword-mode etymology on a rooted canonical.
        rows = (
            db.query(Lemma)
            .filter(
                Lemma.canonical_lemma_id.is_(None),
                Lemma.root_id.isnot(None),
                Lemma.etymology_json.isnot(None),
            )
            .order_by(Lemma.lemma_id)
            .all()
        )
        candidates = [l for l in rows if _is_loanword_mode(l.etymology_json or {})]
        if args.limit:
            candidates = candidates[: args.limit]

        print(f"Checking {len(candidates)} loanword-mode etymologies on rooted lemmas...")
        if not candidates:
            print("Nothing to check.")
            return

        roots_by_id: dict = {}
        root_ids = {l.root_id for l in candidates if l.root_id}
        if root_ids:
            for r in db.query(Root).filter(Root.root_id.in_(root_ids)).all():
                roots_by_id[r.root_id] = r

        by_id = {l.lemma_id: l for l in candidates}
        incoherent_ids: list[int] = []
        verify_failed = 0
        start = time.time()

        for i in range(0, len(candidates), BATCH_SIZE):
            chunk_lemmas = candidates[i:i + BATCH_SIZE]
            chunk = [(l, l.etymology_json) for l in chunk_lemmas]
            result = verify_etymology_coherence_batch(chunk, roots_by_id)
            if result is None:
                verify_failed += len(chunk_lemmas)
                print(f"  batch {i // BATCH_SIZE}: verify FAILED (kept as-is)")
            else:
                for lid in sorted(result):
                    incoherent_ids.append(lid)
                    if args.verbose:
                        lem = by_id[lid]
                        deriv = (lem.etymology_json or {}).get("derivation", "")
                        print(f"  INCOHERENT #{lid} {lem.lemma_ar_bare} "
                              f'"{lem.gloss_en}" ← {deriv[:70]}')
            time.sleep(1)

        elapsed = time.time() - start
        print(f"\nChecked {len(candidates)} in {elapsed:.0f}s: "
              f"{len(incoherent_ids)} incoherent, {verify_failed} verify-failed.")

        report = {
            "generated_at": datetime.utcnow().isoformat(),
            "checked": len(candidates),
            "incoherent": len(incoherent_ids),
            "verify_failed": verify_failed,
            "dry_run": args.dry_run,
            "incoherent_lemmas": [
                {
                    "lemma_id": lid,
                    "lemma_ar_bare": by_id[lid].lemma_ar_bare,
                    "gloss_en": by_id[lid].gloss_en,
                    "derivation": (by_id[lid].etymology_json or {}).get("derivation"),
                }
                for lid in incoherent_ids
            ],
        }
        REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"Report → {REPORT_FILE}")

        if args.dry_run:
            print("\nDry run — no DB writes.")
            return

        if not incoherent_ids:
            print("Nothing to clear.")
            return

        # Apply: NULL the incoherent etymologies (regenerate cleanly later).
        for lid in incoherent_ids:
            by_id[lid].etymology_json = None
        db.commit()
        print(f"Cleared etymology_json on {len(incoherent_ids)} lemmas.")

        log_activity(
            db,
            event_type="manual_action",
            summary=(f"Cleared {len(incoherent_ids)} incoherent etymologies "
                     "(etymology↔gloss mismatch sweep)"),
            detail={"lemma_ids": incoherent_ids, "checked": len(candidates)},
        )

        if args.regenerate:
            from app.services.lemma_enrichment import enrich_lemmas_batch
            print(f"Regenerating etymology for {len(incoherent_ids)} lemmas...")
            summary = enrich_lemmas_batch(incoherent_ids)
            print(f"Re-enrichment: {summary}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
