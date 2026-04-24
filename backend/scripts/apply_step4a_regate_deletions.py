#!/usr/bin/env python3
"""Phase 2 Step 4a, Phase A: delete the 22 bogus canonicals flagged by re-gate.

Reads ``backend/data/decomposition_regate_progress.json``, finds entries with
outcome ``bogus_mle_error``, re-verifies each target lemma has zero downstream
references (paranoid double-check — they shouldn't have any, but 5–30 minutes
may have elapsed since regate ran), then deletes the lemma row. Also deletes
any Root row that was created solely to hold this lemma (orphan root).

Does NOT modify the orphan compounds themselves — they stay in the DB as
orphans with ``canonical_lemma_id = NULL``. Tagging them with
``decomposition_note = {"mle_misanalysis": true, ...}`` is Step 4b's job
(after the schema migration lands).

Usage
-----
    python3 scripts/apply_step4a_regate_deletions.py --dry-run
    python3 scripts/apply_step4a_regate_deletions.py

Environment
-----------
    DATABASE_URL    override DB path (default backend/data/alif.db).
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


# Read from tracked research/ copy (checked into git) so prod runs use the same
# frozen regate decisions as local dry-runs. The backend/data/ path is the
# intermediate in-flight file used while regate was still running.
REGATE_PROGRESS_TRACKED = BACKEND_ROOT.parent / "research" / "decomposition-regate-2026-04-24.json"
REGATE_PROGRESS_LOCAL = BACKEND_ROOT / "data" / "decomposition_regate_progress.json"
APPLY_PROGRESS = BACKEND_ROOT / "data" / "decomposition_regate_apply_progress.json"


def load_regate() -> dict[str, Any]:
    path = REGATE_PROGRESS_TRACKED if REGATE_PROGRESS_TRACKED.exists() else REGATE_PROGRESS_LOCAL
    print(f"Reading regate verdicts from {path}", flush=True)
    return json.loads(path.read_text())


def verify_no_refs(db, lemma_id: int) -> dict[str, int]:
    rows = db.execute(text("""
        SELECT
          (SELECT COUNT(*) FROM sentence_words WHERE lemma_id = :id)          AS sw,
          (SELECT COUNT(*) FROM review_log    WHERE lemma_id = :id)           AS rl,
          (SELECT COUNT(*) FROM sentences     WHERE target_lemma_id = :id)    AS st,
          (SELECT COUNT(*) FROM user_lemma_knowledge WHERE lemma_id = :id)    AS ulk,
          (SELECT COUNT(*) FROM lemmas WHERE canonical_lemma_id = :id)        AS vars
    """), {"id": lemma_id}).fetchone()
    return {"sentence_words": rows[0], "review_log": rows[1], "sent_targets": rows[2], "ulk": rows[3], "variants": rows[4]}


def root_is_orphan_after_delete(db, root_id: int, about_to_delete_lemma_id: int) -> bool:
    """True iff this root would be referenced by zero OTHER lemmas after deletion."""
    if root_id is None:
        return False
    remaining = db.execute(
        text("SELECT COUNT(*) FROM lemmas WHERE root_id = :r AND lemma_id != :l"),
        {"r": root_id, "l": about_to_delete_lemma_id},
    ).scalar()
    return remaining == 0


def load_apply_progress() -> dict[str, Any]:
    if not APPLY_PROGRESS.exists():
        return {"entries": {}, "started_at": None, "completed_at": None}
    return json.loads(APPLY_PROGRESS.read_text())


def save_apply_progress(progress: dict[str, Any]) -> None:
    tmp = APPLY_PROGRESS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(progress, indent=2, ensure_ascii=False))
    tmp.replace(APPLY_PROGRESS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be deleted without committing.")
    args = parser.parse_args()

    regate = load_regate()
    bogus_entries = [
        (int(k), v) for k, v in regate["entries"].items()
        if v["outcome"] == "bogus_mle_error"
    ]
    print(f"Loaded {len(bogus_entries)} bogus_mle_error entries from regate", flush=True)

    apply_prog = load_apply_progress()
    if apply_prog.get("started_at") is None:
        apply_prog["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    db = SessionLocal()
    try:
        deleted_lemmas: list[int] = []
        deleted_roots: list[int] = []
        blocked: list[dict[str, Any]] = []

        for orphan_id, entry in sorted(bogus_entries, key=lambda x: x[1]["new_canonical_id"]):
            new_id = entry["new_canonical_id"]

            if str(orphan_id) in apply_prog["entries"] and apply_prog["entries"][str(orphan_id)].get("outcome") == "deleted":
                print(f"  skip #{orphan_id} — already applied (canonical #{new_id})", flush=True)
                continue

            lemma = db.get(Lemma, new_id)
            if lemma is None:
                apply_prog["entries"][str(orphan_id)] = {
                    "outcome": "missing",
                    "new_canonical_id": new_id,
                    "note": "Lemma row not found — possibly already deleted in a prior run.",
                }
                print(f"  miss #{new_id} (orphan {orphan_id}) — lemma row gone", flush=True)
                continue

            refs = verify_no_refs(db, new_id)
            total_refs = sum(refs.values())
            if total_refs > 0:
                blocked.append({
                    "orphan_id": orphan_id,
                    "new_canonical_id": new_id,
                    "canonical_ar": lemma.lemma_ar,
                    "refs": refs,
                })
                apply_prog["entries"][str(orphan_id)] = {
                    "outcome": "blocked_has_refs",
                    "new_canonical_id": new_id,
                    "canonical_ar": lemma.lemma_ar,
                    "refs": refs,
                }
                print(f"  ⚠️ BLOCKED #{new_id} {lemma.lemma_ar} (orphan {orphan_id}) — refs: {refs}", flush=True)
                continue

            root_id = lemma.root_id
            will_orphan_root = root_is_orphan_after_delete(db, root_id, new_id) if root_id else False

            if args.dry_run:
                apply_prog["entries"][str(orphan_id)] = {
                    "outcome": "dry_run",
                    "new_canonical_id": new_id,
                    "canonical_ar": lemma.lemma_ar,
                    "would_delete_root_id": root_id if will_orphan_root else None,
                }
                print(f"  dry-run would delete lemma #{new_id} {lemma.lemma_ar}" + (f" + root #{root_id}" if will_orphan_root else ""), flush=True)
                continue

            # Real delete.
            db.delete(lemma)
            deleted_lemmas.append(new_id)
            if will_orphan_root:
                db.execute(text("DELETE FROM roots WHERE root_id = :r"), {"r": root_id})
                deleted_roots.append(root_id)

            apply_prog["entries"][str(orphan_id)] = {
                "outcome": "deleted",
                "new_canonical_id": new_id,
                "canonical_ar": lemma.lemma_ar,
                "deleted_root_id": root_id if will_orphan_root else None,
                "deleted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            print(f"  deleted #{new_id} {lemma.lemma_ar}" + (f" + root #{root_id}" if will_orphan_root else ""), flush=True)

        if args.dry_run:
            print("\n[DRY RUN] No commits issued.", flush=True)
        else:
            db.commit()
            print(f"\nCommitted: deleted {len(deleted_lemmas)} lemmas, {len(deleted_roots)} orphan roots", flush=True)

        apply_prog["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        save_apply_progress(apply_prog)

        if blocked:
            print(f"\n⚠️ {len(blocked)} deletions blocked by downstream refs — inspect progress file.")
            for b in blocked:
                print(f"    #{b['new_canonical_id']} {b['canonical_ar']}: {b['refs']}")
            return 2
    finally:
        db.close()

    print(f"\nApply progress: {APPLY_PROGRESS}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
