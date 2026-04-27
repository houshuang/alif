#!/usr/bin/env python3
"""Phase 2 Step 4c-B: link confirmed_valid_link unlinked compounds.

Reads ``backend/data/decomposition_step4c_progress.json`` (output of
``regate_compound_decompositions.py``). Filters to entries where:
  - outcome == "confirmed_valid_link"
  - in_db_link_state == "unlinked"

For each survivor, follows the merge_into pattern from
``apply_step4a_link_survivors.py`` (which itself mirrors merge_into in
``cleanup_dirty_lemmas_v2.py``):

1. Reassign SentenceWord/ReviewLog/Sentence.target_lemma_id refs orphan -> canonical.
2. Merge ULK (no orphan ULK -> noop; one-side -> reassign; both -> weighted merge).
3. Set orphan.canonical_lemma_id = canonical_id (preserves orphan as variant).
4. Run quality gates (no enrichment) on touched canonicals.

Already-linked compounds are NOT touched even if their verdict is
confirmed_valid_link — their link is already in place.

Usage
-----
    python3 scripts/apply_step4c_link_survivors.py --dry-run
    python3 scripts/apply_step4c_link_survivors.py
    python3 scripts/apply_step4c_link_survivors.py --no-gates  # skip quality gates
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
from app.models import Lemma, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.lemma_quality import run_quality_gates


PROGRESS_FILE = BACKEND_ROOT / "data" / "decomposition_step4c_progress.json"
LINK_LOG = BACKEND_ROOT / "data" / "step4c_link_apply.json"


def load_survivors() -> list[dict[str, Any]]:
    data = json.loads(PROGRESS_FILE.read_text())
    out: list[dict[str, Any]] = []
    for orphan_id_str, entry in data["entries"].items():
        if entry.get("outcome") != "confirmed_valid_link":
            continue
        if entry.get("in_db_link_state") != "unlinked":
            continue
        out.append({
            "orphan_id": int(orphan_id_str),
            "canonical_id": entry["proposed_canonical_id"],
            "orphan_ar": entry.get("orphan_ar", ""),
            "canonical_ar": entry.get("proposed_canonical_ar", ""),
        })
    return sorted(out, key=lambda x: x["orphan_id"])


def merge_orphan_into_canonical(db, orphan: Lemma, canonical: Lemma) -> dict[str, Any]:
    """Reassign refs, merge ULK, link orphan -> canonical.

    Mirrors merge_into() in scripts/cleanup_dirty_lemmas_v2.py and the
    function in apply_step4a_link_survivors.py.
    """
    stats: dict[str, Any] = {
        "sentence_words": 0, "review_logs": 0, "sent_targets": 0,
        "ulk_action": None, "ulk_merged_times_seen": None,
    }

    res = db.execute(
        text("UPDATE sentence_words SET lemma_id = :c WHERE lemma_id = :o"),
        {"o": orphan.lemma_id, "c": canonical.lemma_id},
    )
    stats["sentence_words"] = res.rowcount or 0

    res = db.execute(
        text("UPDATE review_log SET lemma_id = :c WHERE lemma_id = :o"),
        {"o": orphan.lemma_id, "c": canonical.lemma_id},
    )
    stats["review_logs"] = res.rowcount or 0

    res = db.execute(
        text("UPDATE sentences SET target_lemma_id = :c WHERE target_lemma_id = :o"),
        {"o": orphan.lemma_id, "c": canonical.lemma_id},
    )
    stats["sent_targets"] = res.rowcount or 0

    orphan_ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == orphan.lemma_id).first()
    canon_ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == canonical.lemma_id).first()

    if orphan_ulk is None:
        stats["ulk_action"] = "no_orphan_ulk_noop"
    elif canon_ulk is None:
        orphan_ulk.lemma_id = canonical.lemma_id
        stats["ulk_action"] = "reassigned_orphan_ulk_to_canonical"
        stats["ulk_merged_times_seen"] = orphan_ulk.times_seen
    else:
        o_seen = orphan_ulk.times_seen or 0
        c_seen = canon_ulk.times_seen or 0
        canon_ulk.times_seen = o_seen + c_seen
        canon_ulk.times_correct = (canon_ulk.times_correct or 0) + (orphan_ulk.times_correct or 0)
        if o_seen > c_seen and orphan_ulk.fsrs_card_json:
            canon_ulk.fsrs_card_json = orphan_ulk.fsrs_card_json
            canon_ulk.knowledge_state = orphan_ulk.knowledge_state
            if orphan_ulk.last_reviewed:
                canon_ulk.last_reviewed = orphan_ulk.last_reviewed
        db.delete(orphan_ulk)
        stats["ulk_action"] = "merged_orphan_into_canonical_ulk"
        stats["ulk_merged_times_seen"] = canon_ulk.times_seen

    orphan.canonical_lemma_id = canonical.lemma_id
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-gates", action="store_true",
                        help="Skip run_quality_gates on canonicals (for fast dry-runs).")
    args = parser.parse_args()

    if not PROGRESS_FILE.exists():
        print(f"ERROR: {PROGRESS_FILE} not found. Run regate_compound_decompositions.py first.", file=sys.stderr)
        return 1

    survivors = load_survivors()
    print(f"Loaded {len(survivors)} confirmed_valid_link unlinked survivors", flush=True)

    apply_log: dict[str, Any] = {"entries": {}, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "completed_at": None,
                                 "applied": not args.dry_run}

    db = SessionLocal()
    try:
        linked_ids: list[int] = []
        canonicals_to_gate: list[int] = []

        for s in survivors:
            orphan = db.get(Lemma, s["orphan_id"])
            canonical = db.get(Lemma, s["canonical_id"])
            if orphan is None or canonical is None:
                apply_log["entries"][str(s["orphan_id"])] = {
                    "outcome": "missing_row",
                    "note": f"orphan={orphan is not None} canonical={canonical is not None}",
                }
                print(f"  ⚠️ MISSING row for orphan #{s['orphan_id']} or canonical #{s['canonical_id']}", flush=True)
                continue

            if orphan.canonical_lemma_id is not None:
                # Drift since verdict pass — orphan got linked elsewhere. Skip safely.
                apply_log["entries"][str(s["orphan_id"])] = {
                    "outcome": "already_linked",
                    "current_canonical": orphan.canonical_lemma_id,
                    "skipped_target": canonical.lemma_id,
                }
                print(f"  skip #{s['orphan_id']} {orphan.lemma_ar} — already linked to #{orphan.canonical_lemma_id}", flush=True)
                continue

            if args.dry_run:
                sw = db.execute(text("SELECT COUNT(*) FROM sentence_words WHERE lemma_id=:o"), {"o": orphan.lemma_id}).scalar()
                rl = db.execute(text("SELECT COUNT(*) FROM review_log WHERE lemma_id=:o"), {"o": orphan.lemma_id}).scalar()
                st = db.execute(text("SELECT COUNT(*) FROM sentences WHERE target_lemma_id=:o"), {"o": orphan.lemma_id}).scalar()
                ulk = db.execute(text("SELECT COUNT(*) FROM user_lemma_knowledge WHERE lemma_id=:o"), {"o": orphan.lemma_id}).scalar()
                canon_ulk = db.execute(text("SELECT COUNT(*) FROM user_lemma_knowledge WHERE lemma_id=:c"), {"c": canonical.lemma_id}).scalar()
                print(f"  dry-run #{orphan.lemma_id} {orphan.lemma_ar} -> #{canonical.lemma_id} {canonical.lemma_ar}: "
                      f"sw={sw} rl={rl} st={st} orphan_ulk={ulk} canon_ulk={canon_ulk}", flush=True)
                apply_log["entries"][str(s["orphan_id"])] = {
                    "outcome": "dry_run",
                    "canonical_id": canonical.lemma_id,
                    "refs_peek": {"sw": sw, "rl": rl, "st": st, "orphan_ulk": ulk, "canon_ulk": canon_ulk},
                }
                continue

            stats = merge_orphan_into_canonical(db, orphan, canonical)
            apply_log["entries"][str(s["orphan_id"])] = {
                "outcome": "linked",
                "canonical_id": canonical.lemma_id,
                "orphan_ar": orphan.lemma_ar,
                "canonical_ar": canonical.lemma_ar,
                "stats": stats,
                "linked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            linked_ids.append(orphan.lemma_id)
            canonicals_to_gate.append(canonical.lemma_id)
            print(f"  linked #{orphan.lemma_id} {orphan.lemma_ar} -> #{canonical.lemma_id} {canonical.lemma_ar}: {stats}", flush=True)

        if not args.dry_run and linked_ids:
            db.commit()
            print(f"\nCommitted: {len(linked_ids)} compounds linked", flush=True)

            if not args.no_gates and canonicals_to_gate:
                # Dedupe canonicals (multiple orphans may point at the same canonical)
                unique_canonicals = sorted(set(canonicals_to_gate))
                print(f"Running quality gates on {len(unique_canonicals)} canonicals (enrich=False)", flush=True)
                run_quality_gates(
                    db,
                    unique_canonicals,
                    skip_variants=False,
                    enrich=False,
                    background_enrich=False,
                )
                db.commit()

            log_activity(
                db,
                "manual_action",
                f"Step 4c-B: linked {len(linked_ids)} compounds to existing canonicals",
                detail={
                    "linked_count": len(linked_ids),
                    "canonicals_gated": len(set(canonicals_to_gate)),
                    "progress_file": str(PROGRESS_FILE.relative_to(BACKEND_ROOT.parent)),
                },
            )

        apply_log["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        LINK_LOG.parent.mkdir(parents=True, exist_ok=True)
        tmp = LINK_LOG.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(apply_log, indent=2, ensure_ascii=False))
        tmp.replace(LINK_LOG)

    finally:
        db.close()

    counts: dict[str, int] = {}
    for entry in apply_log["entries"].values():
        counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
    print("\n=== Summary ===", flush=True)
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}", flush=True)
    print(f"Apply log: {LINK_LOG}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
