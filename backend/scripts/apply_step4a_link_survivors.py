#!/usr/bin/env python3
"""Phase 2 Step 4a-link: wire 11 confirmed_valid orphans to their canonicals.

Follows the ``merge_into`` pattern from ``cleanup_dirty_lemmas_v2.py``:
reassigns SentenceWord/ReviewLog/Sentence.target_lemma_id references from
orphan → canonical, merges ULK, sets ``orphan.canonical_lemma_id = canonical``
(preserves orphan row as variant).

Input: ``research/decomposition-regate-2026-04-24.json`` (tracked verdict
snapshot). Filters to entries with ``outcome == "confirmed_valid"``. 11
entries after Step 4a-prime deletions.

For each survivor:
1. Load orphan Lemma + canonical Lemma.
2. Sanity: orphan.canonical_lemma_id should be NULL (it was by construction
   in the audit). If not NULL, report and skip — we don't silently overwrite
   a prior link.
3. Reassign all downstream refs (SentenceWord, ReviewLog, Sentence.target).
4. ULK merge:
   - Orphan has ULK, canonical has none → reassign orphan_ulk.lemma_id to canonical.
   - Orphan has ULK, canonical has ULK → weighted-state merge (see cleanup_dirty_lemmas_v2.merge_into).
     Sum times_seen/times_correct; whichever side had more encounters provides the FSRS card.
   - Orphan has no ULK → nothing to do.
5. Set orphan.canonical_lemma_id = canonical.lemma_id.
6. Run ``run_quality_gates`` on the canonical (skip_variants=False, enrich=False) —
   enrichment decoupled as per Step 3 convention.

Writes per-orphan outcome to ``backend/data/decomposition_link_progress.json``.

Usage
-----
    python3 scripts/apply_step4a_link_survivors.py --dry-run
    python3 scripts/apply_step4a_link_survivors.py
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
from app.models import Lemma, ReviewLog, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.lemma_quality import run_quality_gates


REGATE_TRACKED = BACKEND_ROOT.parent / "research" / "decomposition-regate-2026-04-24.json"
LINK_PROGRESS = BACKEND_ROOT / "data" / "decomposition_link_progress.json"


def load_survivors() -> list[dict[str, Any]]:
    data = json.loads(REGATE_TRACKED.read_text())
    out = []
    for orphan_id_str, entry in data["entries"].items():
        if entry["outcome"] != "confirmed_valid":
            continue
        out.append({
            "orphan_id": int(orphan_id_str),
            "canonical_id": entry["new_canonical_id"],
            "orphan_ar": entry["orphan_ar"],
            "canonical_ar": entry["proposed_canonical_ar"],
        })
    return sorted(out, key=lambda x: x["orphan_id"])


def load_link_progress() -> dict[str, Any]:
    if not LINK_PROGRESS.exists():
        return {"entries": {}, "started_at": None, "completed_at": None}
    return json.loads(LINK_PROGRESS.read_text())


def save_link_progress(progress: dict[str, Any]) -> None:
    tmp = LINK_PROGRESS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(progress, indent=2, ensure_ascii=False))
    tmp.replace(LINK_PROGRESS)


def merge_orphan_into_canonical(db, orphan: Lemma, canonical: Lemma) -> dict[str, Any]:
    """Reassign refs, merge ULK, link orphan → canonical.

    Mirrors merge_into() in scripts/cleanup_dirty_lemmas_v2.py.
    """
    stats: dict[str, Any] = {
        "sentence_words": 0, "review_logs": 0, "sent_targets": 0,
        "ulk_action": None, "ulk_merged_times_seen": None,
    }

    # 1. SentenceWord.lemma_id — bulk UPDATE is cheaper than per-row for 300+ rows.
    res = db.execute(
        text("UPDATE sentence_words SET lemma_id = :c WHERE lemma_id = :o"),
        {"o": orphan.lemma_id, "c": canonical.lemma_id},
    )
    stats["sentence_words"] = res.rowcount or 0

    # 2. ReviewLog.lemma_id
    res = db.execute(
        text("UPDATE review_log SET lemma_id = :c WHERE lemma_id = :o"),
        {"o": orphan.lemma_id, "c": canonical.lemma_id},
    )
    stats["review_logs"] = res.rowcount or 0

    # 3. Sentence.target_lemma_id
    res = db.execute(
        text("UPDATE sentences SET target_lemma_id = :c WHERE target_lemma_id = :o"),
        {"o": orphan.lemma_id, "c": canonical.lemma_id},
    )
    stats["sent_targets"] = res.rowcount or 0

    # 4. ULK merge.
    orphan_ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == orphan.lemma_id).first()
    canon_ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == canonical.lemma_id).first()

    if orphan_ulk is None:
        stats["ulk_action"] = "no_orphan_ulk_noop"
    elif canon_ulk is None:
        orphan_ulk.lemma_id = canonical.lemma_id
        stats["ulk_action"] = "reassigned_orphan_ulk_to_canonical"
        stats["ulk_merged_times_seen"] = orphan_ulk.times_seen
    else:
        # Both exist — weighted merge. Copied/simplified from merge_into().
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

    # 5. Link orphan → canonical (preserves orphan row as variant).
    orphan.canonical_lemma_id = canonical.lemma_id

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-gates", action="store_true",
                        help="Skip run_quality_gates on canonicals (for fast dry-runs).")
    args = parser.parse_args()

    survivors = load_survivors()
    print(f"Loaded {len(survivors)} confirmed_valid survivors", flush=True)

    progress = load_link_progress()
    if progress.get("started_at") is None:
        progress["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    db = SessionLocal()
    try:
        linked: list[int] = []
        canonicals_to_gate: list[int] = []

        for s in survivors:
            if str(s["orphan_id"]) in progress["entries"] and progress["entries"][str(s["orphan_id"])].get("outcome") == "linked":
                print(f"  skip #{s['orphan_id']} — already linked", flush=True)
                continue

            orphan = db.get(Lemma, s["orphan_id"])
            canonical = db.get(Lemma, s["canonical_id"])
            if orphan is None or canonical is None:
                progress["entries"][str(s["orphan_id"])] = {
                    "outcome": "missing_row",
                    "note": f"orphan={orphan is not None} canonical={canonical is not None}",
                }
                print(f"  ⚠️ MISSING row for orphan #{s['orphan_id']} or canonical #{s['canonical_id']}", flush=True)
                continue

            if orphan.canonical_lemma_id is not None and orphan.canonical_lemma_id != canonical.lemma_id:
                progress["entries"][str(s["orphan_id"])] = {
                    "outcome": "already_linked_elsewhere",
                    "orphan_current_canonical": orphan.canonical_lemma_id,
                    "skipped_target": canonical.lemma_id,
                }
                print(f"  ⚠️ SKIP #{s['orphan_id']} — already linked to canonical #{orphan.canonical_lemma_id}, refusing to overwrite", flush=True)
                continue

            if args.dry_run:
                # Peek at ref counts without mutating.
                sw = db.execute(text("SELECT COUNT(*) FROM sentence_words WHERE lemma_id=:o"), {"o": orphan.lemma_id}).scalar()
                rl = db.execute(text("SELECT COUNT(*) FROM review_log WHERE lemma_id=:o"), {"o": orphan.lemma_id}).scalar()
                st = db.execute(text("SELECT COUNT(*) FROM sentences WHERE target_lemma_id=:o"), {"o": orphan.lemma_id}).scalar()
                ulk = db.execute(text("SELECT COUNT(*) FROM user_lemma_knowledge WHERE lemma_id=:o"), {"o": orphan.lemma_id}).scalar()
                canon_ulk = db.execute(text("SELECT COUNT(*) FROM user_lemma_knowledge WHERE lemma_id=:c"), {"c": canonical.lemma_id}).scalar()
                print(f"  dry-run orphan #{orphan.lemma_id} {orphan.lemma_ar} → canonical #{canonical.lemma_id} {canonical.lemma_ar}: sw={sw} rl={rl} st={st} orphan_ulk={ulk} canon_ulk={canon_ulk}", flush=True)
                progress["entries"][str(s["orphan_id"])] = {
                    "outcome": "dry_run",
                    "canonical_id": canonical.lemma_id,
                    "refs_peek": {"sw": sw, "rl": rl, "st": st, "orphan_ulk": ulk, "canon_ulk": canon_ulk},
                }
                continue

            stats = merge_orphan_into_canonical(db, orphan, canonical)
            progress["entries"][str(s["orphan_id"])] = {
                "outcome": "linked",
                "canonical_id": canonical.lemma_id,
                "orphan_ar": orphan.lemma_ar,
                "canonical_ar": canonical.lemma_ar,
                "stats": stats,
                "linked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            linked.append(orphan.lemma_id)
            canonicals_to_gate.append(canonical.lemma_id)
            print(f"  linked #{orphan.lemma_id} {orphan.lemma_ar} → #{canonical.lemma_id} {canonical.lemma_ar}: {stats}", flush=True)

        if not args.dry_run and linked:
            db.commit()
            print(f"\nCommitted: {len(linked)} orphans linked", flush=True)

            # Re-run quality gates (no enrichment — keep it fast).
            if not args.no_gates and canonicals_to_gate:
                print(f"Running quality gates on {len(canonicals_to_gate)} canonicals (enrich=False)", flush=True)
                run_quality_gates(
                    db,
                    canonicals_to_gate,
                    skip_variants=False,
                    enrich=False,
                    background_enrich=False,
                )
                db.commit()

        progress["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        save_link_progress(progress)

    finally:
        db.close()

    counts: dict[str, int] = {}
    for entry in progress["entries"].values():
        counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
    print("\n=== Summary ===", flush=True)
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}", flush=True)
    print(f"Progress file: {LINK_PROGRESS}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
