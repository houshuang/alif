"""DB-wide chimera audit: catch lemmas with shape/root/forms inconsistencies.

Same logic that runs every cron pass via warm_sentence_cache phase 7. This
script is the human-readable front-end:

    python3 scripts/chimera_audit.py                  # print only (D1..D5)
    python3 scripts/chimera_audit.py --etymology      # also run D6 etymology check
    python3 scripts/chimera_audit.py --etymology --no-llm   # D6 pre-filter only (cheap)
    python3 scripts/chimera_audit.py --emit-alert     # also write ActivityLog

The six categories are documented in ``app/services/chimera_audit.py``.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.services.chimera_audit import (
    find_chimera_candidates,
    find_etymology_incoherence_candidates,
    emit_chimera_alert,
)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--emit-alert", action="store_true",
        help="Write a chimera_audit_findings ActivityLog row (idempotent)",
    )
    ap.add_argument(
        "--etymology", action="store_true",
        help="Also run the D6 etymology↔gloss coherence check (LLM)",
    )
    ap.add_argument(
        "--no-llm", action="store_true",
        help="With --etymology: print pre-filter suspects only, skip the LLM confirm",
    )
    ap.add_argument(
        "--limit", type=int, default=200,
        help="Max D6 candidates to inspect (default 200)",
    )
    args = ap.parse_args()

    db = SessionLocal()
    try:
        candidates = find_chimera_candidates(db)
        if args.etymology:
            candidates += find_etymology_incoherence_candidates(
                db, limit=args.limit, llm_verify=not args.no_llm,
            )
        print(f"Total candidates: {len(candidates)}\n")
        by_cat: dict[str, list] = defaultdict(list)
        for c in candidates:
            by_cat[c.category].append(c)
        for cat in sorted(by_cat):
            print(f"=== {cat} — {len(by_cat[cat])} lemmas ===")
            for c in by_cat[cat]:
                print(
                    f"  #{c.lemma_id:>5} {c.lemma_ar:18} bare={c.lemma_ar_bare!r:14} "
                    f"gloss={c.gloss_en[:50]!r}"
                )
                print(f"        {c.note}")
            print()

        if args.emit_alert and candidates:
            row = emit_chimera_alert(db, candidates)
            if row is None:
                print("(Alert suppressed — same candidate set as the most recent run.)")
            else:
                print(f"Emitted ActivityLog id={row.id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
