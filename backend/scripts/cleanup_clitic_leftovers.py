#!/usr/bin/env python3
"""Clean up clitic-leftover lemmas from pre-2026-04-24 imports.

Background
----------
Before the 2026-04-24 clitic-aware dedup work, several import paths
created lemma rows where the bare form still carried an unstripped
proclitic (و, ب, ل, ال, ...) or enclitic (ـي, ـك, ـه, ـها, ـهم, ...).
The 2026-04-27 lemma-decomposition audit (Phase 2 step 4c) tagged 91
of these and linked 17 to canonicals, but a residual cohort survived
because the clitic-shape audit was scoped to compound forms with
existing canonicals.

A broader audit on 2026-05-06 surfaced 95 such lemmas:
  * 75 ALREADY_LINKED — canonical_lemma_id set, but some still carry
    stale sentence_words / review_log / target_lemma_id / ULK refs.
  * 13 ORPHAN_NO_CANON — canonical_lemma_id NULL.
  *  7 FALSE_POS_VERB — ل-initial verbs, false positives, skipped.

This script handles the first two groups in three phases:

  Phase A (75 lemmas)  — repoint stale refs on already-linked compounds.
  Phase B  (7 lemmas)  — link orphans to existing in-DB canonicals.
  Phase C  (6 lemmas)  — create new canonicals + link orphans.

Usage
-----
    python3 scripts/cleanup_clitic_leftovers.py --dry-run
    python3 scripts/cleanup_clitic_leftovers.py
    python3 scripts/cleanup_clitic_leftovers.py --skip-create  # phases A+B only
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

from sqlalchemy import text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Lemma, UserLemmaKnowledge  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402
from app.services.lemma_quality import run_quality_gates  # noqa: E402


REPORT_FILE = BACKEND_ROOT / "data" / "clitic_leftovers_cleanup.json"


# ---------- Phase B: orphans whose stripped form already exists in DB.
# Mapping: orphan_id -> canonical_id (verified to exist in prod 2026-05-06).
PHASE_B_LINKS: list[tuple[int, int, str]] = [
    # (orphan_id, canonical_id, note)
    (42,   45,   "جدتي → جدة (grandmother)"),
    (1732, 1736, "وناكل → ناكل (we eat, inflected canonical row)"),
    (2902, 2907, "للملئكة → الملئكة (the angels)"),
    (2850, 1444, "شيطينهم → شيطان (devil/demon)"),
    (2866, 663,  "ءاذانهم → أذن (ear)"),
    (2880, 1033, "وادعوا → دعا (to call)"),
    (2355, 477,  "بالشوكلاته → شوكولاتة (chocolate)"),
]


# ---------- Phase C: orphans whose canonical does not yet exist; create stub
# + run quality gates (enrichment will fill diacritized full form, root, etc.).
# 'full' is a best-guess vocalized form; enrichment may overwrite it.
PHASE_C_CREATE: list[dict[str, Any]] = [
    {"orphan_id": 2894, "bare": "ميثاق", "full": "مِيثَاق",
     "gloss_en": "covenant, treaty", "pos": "noun", "source": "audit_2026-05-06"},
    {"orphan_id": 2854, "bare": "طغيان", "full": "طُغْيَان",
     "gloss_en": "transgression, excess", "pos": "noun", "source": "audit_2026-05-06"},
    {"orphan_id": 2858, "bare": "تجارة", "full": "تِجَارَة",
     "gloss_en": "trade, commerce", "pos": "noun", "source": "audit_2026-05-06"},
    {"orphan_id": 2882, "bare": "اتقى", "full": "اِتَّقَى",
     "gloss_en": "to fear, beware",  "pos": "verb", "source": "audit_2026-05-06"},
    {"orphan_id": 2895, "bare": "افسد", "full": "أَفْسَدَ",
     "gloss_en": "to corrupt, ruin", "pos": "verb", "source": "audit_2026-05-06"},
    {"orphan_id": 2652, "bare": "مجال", "full": "مَجَال",
     "gloss_en": "field, domain, scope", "pos": "noun", "source": "audit_2026-05-06"},
]


# Phase A is computed at runtime — every lemma we surface in the broadened
# audit that already has canonical_lemma_id, but has at least one stale ref.

# Mirror the audit query so the script is self-contained.
PROCLITICS = ["وال", "بال", "فال", "كال", "لل", "ال", "و", "ف", "ب", "ل", "ك"]
ENCLITICS = ["هما", "هم", "هن", "ها", "كم", "كن", "نا", "ني", "ه", "ك", "ي"]
ENCLITIC_GLOSS = {
    "ي":   ("my ",), "نا": ("our ", "us "), "ك": ("your ", "you "),
    "كم":  ("your ",), "كن": ("your ",), "ه": ("his ", "him ", "its "),
    "ها":  ("her ", "its "), "هم": ("their ", "them "),
    "هن":  ("their ", "them "), "هما": ("their ", "them "), "ني": ("me ",),
}
PROCLITIC_GLOSS = {
    "و": ("and ",), "ف": ("and ", "so ", "then "),
    "ب": ("with ", "by ", "in "), "ل": ("to ", "for "),
    "ك": ("like ", "as "), "ال": ("the ",),
    "وال": ("and the ",), "بال": ("with the ", "by the ", "in the "),
    "فال": ("and the ", "so the "), "كال": ("like the ", "as the "),
    "لل": ("to the ", "for the "),
}


def find_phase_a(db) -> list[dict[str, Any]]:
    """Return lemmas matching the broadened clitic audit AND already linked."""
    out: list[dict[str, Any]] = []
    rows = db.execute(text(
        "SELECT lemma_id, lemma_ar_bare, gloss_en, pos, canonical_lemma_id "
        "FROM lemmas WHERE lemma_ar_bare IS NOT NULL "
        "AND gloss_en IS NOT NULL AND gloss_en != ''"
    )).fetchall()
    for r in rows:
        bare = (r.lemma_ar_bare or "").strip()
        gloss = (r.gloss_en or "").strip().lower()
        if not bare or not gloss or r.canonical_lemma_id is None:
            continue
        match = None
        for enc in sorted(ENCLITICS, key=len, reverse=True):
            if bare.endswith(enc) and len(bare) > len(enc) + 1:
                if any(gloss.startswith(p) for p in ENCLITIC_GLOSS.get(enc, ())):
                    match = ("enclitic", enc); break
        if match is None:
            for pro in PROCLITICS:
                if bare.startswith(pro) and len(bare) > len(pro) + 1:
                    if any(gloss.startswith(p) for p in PROCLITIC_GLOSS.get(pro, ())):
                        match = ("proclitic", pro); break
        if match is None:
            continue
        # Skip ل-initial verbs (English "to V" infinitive false positives).
        if match == ("proclitic", "ل") and r.pos == "verb":
            continue
        out.append({"orphan_id": r.lemma_id, "canonical_id": r.canonical_lemma_id})
    return sorted(out, key=lambda h: h["orphan_id"])


def reassign_refs(db, orphan_id: int, canonical_id: int) -> dict[str, int]:
    """Repoint sentence_words, review_log, sentence.target_lemma_id from
    orphan -> canonical. Idempotent."""
    sw = db.execute(text("UPDATE sentence_words SET lemma_id=:c WHERE lemma_id=:o"),
                    {"o": orphan_id, "c": canonical_id}).rowcount or 0
    rl = db.execute(text("UPDATE review_log SET lemma_id=:c WHERE lemma_id=:o"),
                    {"o": orphan_id, "c": canonical_id}).rowcount or 0
    st = db.execute(text("UPDATE sentences SET target_lemma_id=:c WHERE target_lemma_id=:o"),
                    {"o": orphan_id, "c": canonical_id}).rowcount or 0
    return {"sentence_words": sw, "review_logs": rl, "sent_targets": st}


def merge_or_drop_orphan_ulk(db, orphan_id: int, canonical_id: int) -> dict[str, Any]:
    """If both ULKs exist, weighted-merge into canonical and delete orphan ULK.
    If only orphan ULK, reassign to canonical. If only canonical or neither,
    no-op. Same semantics as merge_orphan_into_canonical()."""
    orphan_ulk = db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == orphan_id).first()
    canon_ulk = db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == canonical_id).first()
    if orphan_ulk is None:
        return {"action": "no_orphan_ulk_noop"}
    if canon_ulk is None:
        orphan_ulk.lemma_id = canonical_id
        return {"action": "reassigned_orphan_ulk_to_canonical",
                "merged_times_seen": orphan_ulk.times_seen}
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
    return {"action": "merged_orphan_into_canonical_ulk",
            "merged_times_seen": canon_ulk.times_seen}


def phase_a_cleanup(db, dry_run: bool) -> tuple[list[dict], list[int]]:
    """Repoint stale refs on already-linked compounds. Skip ones with nothing
    to do."""
    items = find_phase_a(db)
    print(f"\n=== Phase A: scanning {len(items)} ALREADY_LINKED lemmas ===", flush=True)
    log: list[dict] = []
    canonicals_touched: set[int] = set()
    for item in items:
        oid, cid = item["orphan_id"], item["canonical_id"]
        sw = db.execute(text("SELECT COUNT(*) FROM sentence_words WHERE lemma_id=:o"),
                        {"o": oid}).scalar()
        rl = db.execute(text("SELECT COUNT(*) FROM review_log WHERE lemma_id=:o"),
                        {"o": oid}).scalar()
        st = db.execute(text("SELECT COUNT(*) FROM sentences WHERE target_lemma_id=:o"),
                        {"o": oid}).scalar()
        ulk = db.execute(text("SELECT COUNT(*) FROM user_lemma_knowledge WHERE lemma_id=:o"),
                         {"o": oid}).scalar()
        if sw == 0 and rl == 0 and st == 0 and ulk == 0:
            continue
        if dry_run:
            log.append({"phase": "A", "orphan_id": oid, "canonical_id": cid,
                        "outcome": "dry_run",
                        "stale": {"sw": sw, "rl": rl, "st": st, "ulk": ulk}})
            print(f"  dry-run #{oid} -> #{cid}: sw={sw} rl={rl} st={st} ulk={ulk}",
                  flush=True)
            continue
        refs = reassign_refs(db, oid, cid)
        ulk_action = merge_or_drop_orphan_ulk(db, oid, cid)
        canonicals_touched.add(cid)
        log.append({"phase": "A", "orphan_id": oid, "canonical_id": cid,
                    "outcome": "cleaned", "refs": refs, "ulk": ulk_action,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        print(f"  cleaned #{oid} -> #{cid}: refs={refs} ulk={ulk_action}", flush=True)
    return log, sorted(canonicals_touched)


def phase_b_link(db, dry_run: bool) -> tuple[list[dict], list[int]]:
    """Link orphans to existing in-DB canonicals (full merge_orphan)."""
    print(f"\n=== Phase B: linking {len(PHASE_B_LINKS)} orphans to existing canonicals ===",
          flush=True)
    log: list[dict] = []
    canonicals_touched: set[int] = set()
    for oid, cid, note in PHASE_B_LINKS:
        orphan = db.get(Lemma, oid)
        canon = db.get(Lemma, cid)
        if orphan is None or canon is None:
            log.append({"phase": "B", "orphan_id": oid, "canonical_id": cid,
                        "outcome": "missing_row",
                        "exists": {"orphan": orphan is not None,
                                   "canonical": canon is not None}})
            print(f"  ⚠️ missing rows for #{oid} or #{cid}", flush=True)
            continue
        if orphan.canonical_lemma_id is not None:
            log.append({"phase": "B", "orphan_id": oid, "canonical_id": cid,
                        "outcome": "already_linked",
                        "current_canonical": orphan.canonical_lemma_id})
            print(f"  skip #{oid} — already linked to #{orphan.canonical_lemma_id}",
                  flush=True)
            continue
        if dry_run:
            sw = db.execute(text("SELECT COUNT(*) FROM sentence_words WHERE lemma_id=:o"),
                            {"o": oid}).scalar()
            ulk = db.execute(text("SELECT COUNT(*) FROM user_lemma_knowledge WHERE lemma_id=:o"),
                             {"o": oid}).scalar()
            log.append({"phase": "B", "orphan_id": oid, "canonical_id": cid,
                        "outcome": "dry_run", "note": note,
                        "stale": {"sw": sw, "ulk": ulk}})
            print(f"  dry-run #{oid} -> #{cid} ({note}): sw={sw} ulk={ulk}", flush=True)
            continue
        refs = reassign_refs(db, oid, cid)
        ulk_action = merge_or_drop_orphan_ulk(db, oid, cid)
        orphan.canonical_lemma_id = cid
        canonicals_touched.add(cid)
        log.append({"phase": "B", "orphan_id": oid, "canonical_id": cid,
                    "outcome": "linked", "note": note, "refs": refs,
                    "ulk": ulk_action,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        print(f"  linked #{oid} -> #{cid} ({note}): refs={refs} ulk={ulk_action}",
              flush=True)
    return log, sorted(canonicals_touched)


def phase_c_create_and_link(db, dry_run: bool, skip_create: bool
                            ) -> tuple[list[dict], list[int]]:
    """Create new canonicals + link orphans. Quality gates run with
    enrich=True synchronously, so each new lemma gets full LLM enrichment."""
    if skip_create:
        print(f"\n=== Phase C: skipped (--skip-create) ===", flush=True)
        return [], []
    print(f"\n=== Phase C: creating {len(PHASE_C_CREATE)} canonicals + linking ===",
          flush=True)
    log: list[dict] = []
    new_canonical_ids: list[int] = []
    pairs: list[tuple[int, int]] = []  # (orphan_id, new_canonical_id)
    for spec in PHASE_C_CREATE:
        oid = spec["orphan_id"]
        orphan = db.get(Lemma, oid)
        if orphan is None:
            log.append({"phase": "C", "orphan_id": oid, "outcome": "missing_orphan"})
            continue
        if orphan.canonical_lemma_id is not None:
            log.append({"phase": "C", "orphan_id": oid, "outcome": "already_linked",
                        "current_canonical": orphan.canonical_lemma_id})
            print(f"  skip #{oid} — already linked", flush=True)
            continue
        existing = db.execute(text(
            "SELECT lemma_id FROM lemmas WHERE lemma_ar_bare=:b"),
            {"b": spec["bare"]}).fetchone()
        if existing is not None:
            log.append({"phase": "C", "orphan_id": oid,
                        "outcome": "canonical_now_exists_use_phase_b",
                        "found_canonical": existing[0]})
            print(f"  ⚠️ #{oid}: canonical {spec['bare']!r} already exists as "
                  f"#{existing[0]} — re-run after moving to PHASE_B_LINKS", flush=True)
            continue
        if dry_run:
            log.append({"phase": "C", "orphan_id": oid, "outcome": "dry_run_create",
                        "spec": spec})
            print(f"  dry-run create #{oid} → new canonical {spec['bare']!r} "
                  f"({spec['gloss_en']})", flush=True)
            continue
        new_lemma = Lemma(
            lemma_ar=spec["full"], lemma_ar_bare=spec["bare"],
            gloss_en=spec["gloss_en"], pos=spec["pos"], source=spec["source"],
        )
        db.add(new_lemma)
        db.flush()
        new_id = new_lemma.lemma_id
        new_canonical_ids.append(new_id)
        pairs.append((oid, new_id))
        log.append({"phase": "C", "orphan_id": oid, "new_canonical_id": new_id,
                    "outcome": "created",
                    "spec": spec, "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        print(f"  created #{new_id} {spec['bare']} for orphan #{oid}", flush=True)

    if not dry_run and new_canonical_ids:
        db.commit()
        print(f"  committed {len(new_canonical_ids)} stub canonicals", flush=True)
        print(f"  running quality gates (enrich=True) on new canonicals...",
              flush=True)
        gates = run_quality_gates(db, new_canonical_ids, enrich=True,
                                  background_enrich=False)
        db.commit()
        print(f"  gates: finalized={gates.get('finalize')} "
              f"variants={gates.get('variants')} stamped={gates.get('stamped')}",
              flush=True)

        # Now link orphans to their freshly-created canonicals.
        print(f"  linking {len(pairs)} orphans to new canonicals...", flush=True)
        for oid, nid in pairs:
            orphan = db.get(Lemma, oid)
            refs = reassign_refs(db, oid, nid)
            ulk_action = merge_or_drop_orphan_ulk(db, oid, nid)
            orphan.canonical_lemma_id = nid
            for entry in log:
                if entry.get("phase") == "C" and entry.get("orphan_id") == oid \
                        and entry.get("outcome") == "created":
                    entry["refs"] = refs
                    entry["ulk"] = ulk_action
                    break
            print(f"    linked #{oid} -> #{nid}: refs={refs} ulk={ulk_action}",
                  flush=True)
        db.commit()
    return log, new_canonical_ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-create", action="store_true",
                        help="Skip Phase C (create new canonicals).")
    parser.add_argument("--skip-gate-rerun", action="store_true",
                        help="Skip the post-link run_quality_gates(enrich=False) "
                             "on canonicals touched in phases A/B.")
    args = parser.parse_args()

    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    db = SessionLocal()
    full_log: dict[str, Any] = {"started_at": started, "applied": not args.dry_run,
                                "entries": []}
    try:
        a_log, a_canon = phase_a_cleanup(db, args.dry_run)
        b_log, b_canon = phase_b_link(db, args.dry_run)
        full_log["entries"].extend(a_log)
        full_log["entries"].extend(b_log)
        if not args.dry_run:
            db.commit()
            touched = sorted(set(a_canon) | set(b_canon))
            if touched and not args.skip_gate_rerun:
                print(f"\nRe-running quality gates (enrich=False) on {len(touched)} "
                      f"canonicals touched in phases A/B...", flush=True)
                run_quality_gates(db, touched, enrich=False,
                                  background_enrich=False)
                db.commit()

        c_log, c_new = phase_c_create_and_link(db, args.dry_run, args.skip_create)
        full_log["entries"].extend(c_log)

        full_log["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = REPORT_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(full_log, indent=2, ensure_ascii=False))
        tmp.replace(REPORT_FILE)

        if not args.dry_run:
            counts: dict[str, int] = {}
            for e in full_log["entries"]:
                k = f"{e.get('phase')}/{e.get('outcome')}"
                counts[k] = counts.get(k, 0) + 1
            log_activity(
                db, "manual_action",
                f"Clitic-leftover cleanup: {sum(counts.values())} actions across phases A/B/C",
                detail={"counts": counts, "report": str(REPORT_FILE.relative_to(BACKEND_ROOT.parent))},
            )
            db.commit()
    finally:
        db.close()

    print("\n=== Summary ===", flush=True)
    counts: dict[str, int] = {}
    for e in full_log["entries"]:
        k = f"{e.get('phase')}/{e.get('outcome')}"
        counts[k] = counts.get(k, 0) + 1
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}", flush=True)
    print(f"Report → {REPORT_FILE}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
