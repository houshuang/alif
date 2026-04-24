#!/usr/bin/env python3
"""Phase 2 Step 4b: tag 89 orphan lemmas with mle_misanalysis metadata.

Two source buckets:

1. ``research/decomposition-regate-2026-04-24.json`` — 22 entries with
   ``outcome == "bogus_mle_error"``. Their proposed canonicals (new_canonical_id)
   were deleted in Step 4a-prime; the orphan keys (Lemma.lemma_id) remain in
   the DB with canonical_lemma_id = NULL. We tag the orphan.

2. ``research/decomposition-backfill-progress-2026-04-24.json`` — 67 entries
   with ``outcome == "mle_error"``. Step 3 refused to create a canonical for
   these; the orphan lemma rows (keys) are still in the DB.

For each orphan we stamp Lemma.decomposition_note = {
    "mle_misanalysis": true,
    "reason": <LLM note copied verbatim from the source artifact>,
    "source_artifact": <filename>,
    "tagged_at": <UTC ISO8601>,
    "phase": "step4b",
}

Safety:
- Dry-run by default; --apply to write.
- Skips any lemma whose decomposition_note is already set (refuses to overwrite).
- Writes per-orphan outcome to backend/data/step4b_tag_progress.json.
- Emits one ActivityLog entry per run (on --apply) summarising counts.

Usage:
    python3 scripts/tag_mle_misanalysis_orphans.py                # dry-run
    python3 scripts/tag_mle_misanalysis_orphans.py --apply        # commit
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import SessionLocal
from app.models import Lemma
from app.services.activity_log import log_activity


REGATE_ARTIFACT = BACKEND_ROOT.parent / "research" / "decomposition-regate-2026-04-24.json"
BACKFILL_ARTIFACT = BACKEND_ROOT.parent / "research" / "decomposition-backfill-progress-2026-04-24.json"
PROGRESS_FILE = BACKEND_ROOT / "data" / "step4b_tag_progress.json"


def collect_targets() -> list[dict[str, Any]]:
    """Load orphan lemma_ids + reasons from both source JSONs."""
    targets: list[dict[str, Any]] = []

    regate = json.loads(REGATE_ARTIFACT.read_text())
    for orphan_id_str, entry in regate["entries"].items():
        if entry.get("outcome") != "bogus_mle_error":
            continue
        targets.append({
            "orphan_id": int(orphan_id_str),
            "reason": entry["reason"],
            "source_artifact": REGATE_ARTIFACT.name,
            "bucket": "bogus_canonical_deleted",
        })

    backfill = json.loads(BACKFILL_ARTIFACT.read_text())
    for orphan_id_str, entry in backfill["entries"].items():
        if entry.get("outcome") != "mle_error":
            continue
        targets.append({
            "orphan_id": int(orphan_id_str),
            "reason": entry["reason"],
            "source_artifact": BACKFILL_ARTIFACT.name,
            "bucket": "step3_refused_creation",
        })

    return sorted(targets, key=lambda x: x["orphan_id"])


def load_progress() -> dict[str, Any]:
    if not PROGRESS_FILE.exists():
        return {"entries": {}, "started_at": None, "completed_at": None}
    return json.loads(PROGRESS_FILE.read_text())


def save_progress(progress: dict[str, Any]) -> None:
    tmp = PROGRESS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(progress, indent=2, ensure_ascii=False))
    tmp.replace(PROGRESS_FILE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit changes. Default is dry-run.")
    args = parser.parse_args()

    targets = collect_targets()
    print(f"Loaded {len(targets)} orphan lemmas to tag "
          f"(regate bogus={sum(1 for t in targets if t['bucket']=='bogus_canonical_deleted')}, "
          f"backfill mle_error={sum(1 for t in targets if t['bucket']=='step3_refused_creation')})",
          flush=True)

    progress = load_progress()
    if progress.get("started_at") is None:
        progress["started_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    tagged: list[int] = []
    skipped_missing: list[int] = []
    skipped_has_note: list[int] = []

    db = SessionLocal()
    try:
        for t in targets:
            orphan_id = t["orphan_id"]
            lemma = db.get(Lemma, orphan_id)

            if lemma is None:
                progress["entries"][str(orphan_id)] = {
                    "outcome": "missing_lemma",
                    "bucket": t["bucket"],
                }
                skipped_missing.append(orphan_id)
                print(f"  ⚠️ MISS #{orphan_id} — lemma row not found", flush=True)
                continue

            if lemma.decomposition_note is not None:
                progress["entries"][str(orphan_id)] = {
                    "outcome": "already_tagged",
                    "bucket": t["bucket"],
                    "existing_note": lemma.decomposition_note,
                }
                skipped_has_note.append(orphan_id)
                print(f"  skip #{orphan_id} {lemma.lemma_ar} — already has decomposition_note: {lemma.decomposition_note}", flush=True)
                continue

            note = {
                "mle_misanalysis": True,
                "reason": t["reason"],
                "source_artifact": t["source_artifact"],
                "tagged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "phase": "step4b",
            }

            if args.apply:
                lemma.decomposition_note = note
                progress["entries"][str(orphan_id)] = {
                    "outcome": "tagged",
                    "bucket": t["bucket"],
                    "lemma_ar": lemma.lemma_ar,
                    "note": note,
                }
                tagged.append(orphan_id)
                print(f"  tag #{orphan_id} {lemma.lemma_ar} ({t['bucket']})", flush=True)
            else:
                progress["entries"][str(orphan_id)] = {
                    "outcome": "dry_run",
                    "bucket": t["bucket"],
                    "lemma_ar": lemma.lemma_ar,
                    "would_write": note,
                }
                print(f"  dry-run #{orphan_id} {lemma.lemma_ar} ({t['bucket']})", flush=True)

        if args.apply:
            db.commit()
            print(f"\nCommitted: tagged {len(tagged)} orphans", flush=True)
            log_activity(
                db,
                "manual_action",
                f"Step 4b: tagged {len(tagged)} orphan lemmas with mle_misanalysis",
                detail={
                    "tagged_count": len(tagged),
                    "skipped_missing": len(skipped_missing),
                    "skipped_already_tagged": len(skipped_has_note),
                    "buckets": {
                        "bogus_canonical_deleted": sum(
                            1 for e in progress["entries"].values()
                            if e.get("outcome") == "tagged" and e.get("bucket") == "bogus_canonical_deleted"
                        ),
                        "step3_refused_creation": sum(
                            1 for e in progress["entries"].values()
                            if e.get("outcome") == "tagged" and e.get("bucket") == "step3_refused_creation"
                        ),
                    },
                    "progress_file": str(PROGRESS_FILE.relative_to(BACKEND_ROOT.parent)),
                },
            )

        progress["completed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save_progress(progress)

    finally:
        db.close()

    print("\n=== Summary ===", flush=True)
    counts: dict[str, int] = {}
    for entry in progress["entries"].values():
        counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}", flush=True)
    print(f"Progress file: {PROGRESS_FILE}", flush=True)
    if not args.apply:
        print("\n[DRY RUN] Re-run with --apply to commit.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
