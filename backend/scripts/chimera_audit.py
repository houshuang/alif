"""DB-wide chimera audit: catch lemmas with shape/root/forms inconsistencies.

Same logic that runs every cron pass via warm_sentence_cache phase 7. This
script is the human-readable front-end:

    python3 scripts/chimera_audit.py                  # print only
    python3 scripts/chimera_audit.py --emit-alert     # also write ActivityLog

The five categories are documented in ``app/services/chimera_audit.py``.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.services.chimera_audit import find_chimera_candidates, emit_chimera_alert


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--emit-alert", action="store_true",
        help="Write a chimera_audit_findings ActivityLog row (idempotent)",
    )
    args = ap.parse_args()

    db = SessionLocal()
    try:
        candidates = find_chimera_candidates(db)
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
