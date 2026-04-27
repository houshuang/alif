#!/usr/bin/env python3
"""Phase 2 Step 4c-A: tag bogus + wrong_canonical compounds.

Reads ``backend/data/decomposition_step4c_progress.json`` (output of
``regate_compound_decompositions.py``). For each entry whose verdict is
``bogus_mle_error`` or ``wrong_canonical_real_compound``, stamp
``Lemma.decomposition_note`` with structured metadata.

Tag-only: this script never unlinks, repoints, or deletes lemmas. Per the
Step 4c safety note, even a wrong existing canonical_lemma_id is left in
place — corpus mappings (sentence_words, review_log) depend on it. The
note marks the row for a future targeted re-mapping pass.

Note schema
-----------
For ``bogus_mle_error``::

    {
      "mle_misanalysis": True,
      "reason": <pass1 LLM reason>,
      "agreement": True,
      "source_artifact": "decomposition_step4c_progress.json",
      "phase": "step4c",
      "tagged_at": <UTC ISO8601>,
      "in_db_link_state_at_tag": "linked"|"unlinked",
      "wrong_canonical_existing": True   # only if was already linked
    }

For ``wrong_canonical_real_compound``::

    {
      "mle_misanalysis": False,
      "wrong_canonical": True,
      "reason": <pass1 LLM reason>,
      "suggested_canonical_bare": "..."  # if model provided
      "source_artifact": "decomposition_step4c_progress.json",
      "phase": "step4c",
      "tagged_at": <UTC ISO8601>,
      "in_db_link_state_at_tag": "linked"|"unlinked"
    }

Safety
------
- Dry-run by default; --apply to commit.
- Refuses to overwrite existing decomposition_note (4b tags survive).
- Backup recommended before --apply (script does NOT auto-backup; use
  the standard sqlite .backup pattern from CLAUDE.md before running on
  prod).
- ActivityLog entry on --apply summarising counts.

Usage
-----
    python3 scripts/apply_step4c_tags.py                # dry-run
    python3 scripts/apply_step4c_tags.py --apply        # commit
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


PROGRESS_FILE = BACKEND_ROOT / "data" / "decomposition_step4c_progress.json"
APPLY_LOG = BACKEND_ROOT / "data" / "step4c_tag_apply.json"
TAGGABLE_VERDICTS = {"bogus_mle_error", "wrong_canonical_real_compound"}


def build_note(entry: dict[str, Any]) -> dict[str, Any]:
    verdict = entry["outcome"]
    note: dict[str, Any] = {
        "reason": entry.get("reason_pass1", ""),
        "agreement": entry.get("agreement", False),
        "source_artifact": PROGRESS_FILE.name,
        "phase": "step4c",
        "tagged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "in_db_link_state_at_tag": entry.get("in_db_link_state", ""),
    }
    if verdict == "bogus_mle_error":
        note["mle_misanalysis"] = True
        if entry.get("in_db_link_state") == "linked":
            note["wrong_canonical_existing"] = True
    elif verdict == "wrong_canonical_real_compound":
        note["mle_misanalysis"] = False
        note["wrong_canonical"] = True
        if entry.get("suggested_canonical_bare"):
            note["suggested_canonical_bare"] = entry["suggested_canonical_bare"]
    return note


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit changes. Default is dry-run.")
    args = parser.parse_args()

    if not PROGRESS_FILE.exists():
        print(f"ERROR: {PROGRESS_FILE} not found. Run regate_compound_decompositions.py first.", file=sys.stderr)
        return 1

    progress = json.loads(PROGRESS_FILE.read_text())
    candidates = [(int(orphan_id), e) for orphan_id, e in progress["entries"].items()
                  if e.get("outcome") in TAGGABLE_VERDICTS]
    print(f"Loaded {len(progress['entries'])} verdicts; {len(candidates)} taggable "
          f"(bogus_mle_error or wrong_canonical_real_compound)", flush=True)

    by_verdict: dict[str, int] = {}
    for _, e in candidates:
        v = e["outcome"]
        by_verdict[v] = by_verdict.get(v, 0) + 1
    for k, v in sorted(by_verdict.items()):
        print(f"  {k}: {v}", flush=True)

    apply_log: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entries": {},
        "completed_at": None,
        "applied": args.apply,
    }
    tagged_ids: list[int] = []
    skipped_missing: list[int] = []
    skipped_has_note: list[int] = []

    db = SessionLocal()
    try:
        for orphan_id, entry in sorted(candidates):
            lemma = db.get(Lemma, orphan_id)
            if lemma is None:
                apply_log["entries"][str(orphan_id)] = {"outcome": "missing_lemma"}
                skipped_missing.append(orphan_id)
                print(f"  ⚠️ MISS #{orphan_id} — lemma row not found", flush=True)
                continue

            if lemma.decomposition_note is not None:
                apply_log["entries"][str(orphan_id)] = {
                    "outcome": "already_tagged",
                    "existing_note": lemma.decomposition_note,
                }
                skipped_has_note.append(orphan_id)
                print(f"  skip #{orphan_id} {lemma.lemma_ar} — already tagged: {lemma.decomposition_note}", flush=True)
                continue

            note = build_note(entry)

            if args.apply:
                lemma.decomposition_note = note
                apply_log["entries"][str(orphan_id)] = {
                    "outcome": "tagged",
                    "verdict": entry["outcome"],
                    "lemma_ar": lemma.lemma_ar,
                    "note": note,
                }
                tagged_ids.append(orphan_id)
                print(f"  tag #{orphan_id} {lemma.lemma_ar} ({entry['outcome']})", flush=True)
            else:
                apply_log["entries"][str(orphan_id)] = {
                    "outcome": "dry_run",
                    "verdict": entry["outcome"],
                    "lemma_ar": lemma.lemma_ar,
                    "would_write": note,
                }
                print(f"  dry-run #{orphan_id} {lemma.lemma_ar} ({entry['outcome']})", flush=True)

        if args.apply:
            db.commit()
            print(f"\nCommitted: tagged {len(tagged_ids)} lemmas", flush=True)
            log_activity(
                db,
                "manual_action",
                f"Step 4c-A: tagged {len(tagged_ids)} compounds with decomposition_note",
                detail={
                    "tagged_count": len(tagged_ids),
                    "skipped_missing": len(skipped_missing),
                    "skipped_already_tagged": len(skipped_has_note),
                    "by_verdict": {
                        v: sum(
                            1 for e in apply_log["entries"].values()
                            if e.get("outcome") == "tagged" and e.get("verdict") == v
                        )
                        for v in TAGGABLE_VERDICTS
                    },
                    "progress_file": str(PROGRESS_FILE.relative_to(BACKEND_ROOT.parent)),
                },
            )

        apply_log["completed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        APPLY_LOG.parent.mkdir(parents=True, exist_ok=True)
        tmp = APPLY_LOG.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(apply_log, indent=2, ensure_ascii=False))
        tmp.replace(APPLY_LOG)

    finally:
        db.close()

    print("\n=== Summary ===", flush=True)
    counts: dict[str, int] = {}
    for entry in apply_log["entries"].values():
        counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}", flush=True)
    print(f"Apply log: {APPLY_LOG}", flush=True)
    if not args.apply:
        print("\n[DRY RUN] Re-run with --apply to commit.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
