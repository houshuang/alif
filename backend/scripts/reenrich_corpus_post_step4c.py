#!/usr/bin/env python3
"""Phase 2 Step 6: requeue inactive corpus sentences for re-verification.

Steps 4a-link, 4b, 4c-A, and 4c-B together changed which lemmas are reachable
from the canonical-variant graph. Inactive corpus sentences whose mappings
were previously verified-and-failed (mappings_verified_at IS NOT NULL,
is_active = 0) may now resolve cleanly.

This script clears mappings_verified_at on those sentences so the cron
verification step (A2) re-evaluates them. No verification is performed here —
the cron handles it asynchronously.

Strategy
--------
Default mode targets ALL inactive+verified corpus sentences (the broad sweep
the next-session-prompt described). An optional --touched-only mode targets
only sentences whose lemma_id touches a recently-modified lemma; useful for
incremental re-runs.

Usage
-----
    python3 scripts/reenrich_corpus_post_step4c.py --dry-run
    python3 scripts/reenrich_corpus_post_step4c.py --apply
    python3 scripts/reenrich_corpus_post_step4c.py --apply --touched-only
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text

from app.database import SessionLocal
from app.services.activity_log import log_activity


STEP4C_PROGRESS = BACKEND_ROOT / "data" / "decomposition_step4c_progress.json"
STEP4A_LINK_PROGRESS = BACKEND_ROOT / "data" / "decomposition_link_progress.json"
STEP4B_PROGRESS = BACKEND_ROOT / "data" / "step4b_tag_progress.json"


def collect_touched_lemma_ids() -> set[int]:
    """Lemma ids touched by Steps 4a-link / 4b / 4c-A / 4c-B."""
    ids: set[int] = set()

    if STEP4A_LINK_PROGRESS.exists():
        d = json.loads(STEP4A_LINK_PROGRESS.read_text())
        for k, e in d.get("entries", {}).items():
            if e.get("outcome") in {"linked", "tagged"}:
                ids.add(int(k))
                if c := e.get("canonical_id"):
                    ids.add(c)

    if STEP4B_PROGRESS.exists():
        d = json.loads(STEP4B_PROGRESS.read_text())
        for k, e in d.get("entries", {}).items():
            if e.get("outcome") == "tagged":
                ids.add(int(k))

    if STEP4C_PROGRESS.exists():
        d = json.loads(STEP4C_PROGRESS.read_text())
        for k, e in d.get("entries", {}).items():
            if e.get("outcome") in {"bogus_mle_error", "wrong_canonical_real_compound", "confirmed_valid_link"}:
                ids.add(int(k))
                if c := e.get("proposed_canonical_id"):
                    ids.add(c)

    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the change.")
    parser.add_argument("--touched-only", action="store_true",
                        help="Only requeue sentences whose lemma_id touches a Step 4 lemma.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        before = db.execute(text(
            "SELECT COUNT(*) FROM sentences "
            "WHERE source='corpus' AND is_active=0 AND mappings_verified_at IS NOT NULL"
        )).scalar()
        total_corpus = db.execute(text("SELECT COUNT(*) FROM sentences WHERE source='corpus'")).scalar()
        print(f"Corpus total: {total_corpus}", flush=True)
        print(f"Inactive+verified (candidates for re-verification): {before}", flush=True)

        if args.touched_only:
            touched = collect_touched_lemma_ids()
            print(f"Touched lemma ids from Steps 4a-link/4b/4c: {len(touched)}", flush=True)
            if not touched:
                print("No touched lemmas — nothing to do.", flush=True)
                return 0
            ids_csv = ",".join(str(i) for i in sorted(touched))
            target_count = db.execute(text(f"""
                SELECT COUNT(DISTINCT s.id) FROM sentences s
                JOIN sentence_words sw ON sw.sentence_id = s.id
                WHERE s.source='corpus' AND s.is_active=0 AND s.mappings_verified_at IS NOT NULL
                  AND sw.lemma_id IN ({ids_csv})
            """)).scalar()
            print(f"Inactive+verified sentences touching those lemmas: {target_count}", flush=True)
        else:
            target_count = before

        if not args.apply:
            print(f"\n[DRY RUN] Would clear mappings_verified_at on {target_count} sentences.", flush=True)
            print("Re-run with --apply to commit.", flush=True)
            return 0

        if args.touched_only:
            ids_csv = ",".join(str(i) for i in sorted(collect_touched_lemma_ids()))
            res = db.execute(text(f"""
                UPDATE sentences SET mappings_verified_at = NULL
                WHERE source='corpus' AND is_active=0 AND mappings_verified_at IS NOT NULL
                  AND id IN (
                    SELECT DISTINCT s.id FROM sentences s
                    JOIN sentence_words sw ON sw.sentence_id = s.id
                    WHERE s.source='corpus' AND s.is_active=0 AND s.mappings_verified_at IS NOT NULL
                      AND sw.lemma_id IN ({ids_csv})
                  )
            """))
        else:
            res = db.execute(text(
                "UPDATE sentences SET mappings_verified_at = NULL "
                "WHERE source='corpus' AND is_active=0 AND mappings_verified_at IS NOT NULL"
            ))
        cleared = res.rowcount or 0
        db.commit()

        log_activity(
            db,
            "manual_action",
            f"Step 6: cleared mappings_verified_at on {cleared} inactive corpus sentences",
            detail={
                "cleared_count": cleared,
                "mode": "touched_only" if args.touched_only else "all_inactive_verified",
                "before_inactive_verified": before,
                "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
        print(f"\nCommitted: cleared {cleared} sentences. Cron step A2 will re-verify on next run.", flush=True)

    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
