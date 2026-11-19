#!/usr/bin/env python3
"""Audit and correct the stored part of speech on lemmas.

The mapping verifier is shown each lemma's ``pos`` and told to flag a wrong
part of speech. A lemma stored with the wrong label (تَسَلَّقَ "to climb" as a
noun, طَقْم "set" as a verb) therefore gets every sentence that uses it
rejected, although the mapping itself is right. On 2026-11-18 this was the
largest single cause of rejected dense sentences.

Two steps, so the proposals can be read before anything is written:

    python scripts/audit_lemma_pos.py --out /tmp/pos_audit.jsonl      # LLM pass, no DB writes
    python scripts/audit_lemma_pos.py --apply /tmp/pos_audit.jsonl    # dry run of the write
    python scripts/audit_lemma_pos.py --apply /tmp/pos_audit.jsonl --write --exclude 2577,4041

The LLM pass resumes: lemma ids already in the output file are skipped.

What ``--apply`` changes:
- spelling variants of a label (``adjective`` -> ``adj``, ``v`` -> ``verb``);
- a disagreement between the open classes verb / noun / adj / adv, or a
  missing label, when the model reports no conflict between form and gloss.
Proper names, function-word classes and rows where the Arabic form and the
English gloss describe different words are listed and left alone.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from app.models import Lemma
from app.services.lemma_pos_check import (
    BATCH_SIZE,
    audit_rows,
    auditable,
    canonical_label,
    plan_changes,
)

def run_audit(out_path: Path, limit: int | None) -> None:
    done: set[int] = set()
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if line.strip():
                done.add(json.loads(line)["lemma_id"])

    db = SessionLocal()
    try:
        rows = [
            {"lemma_id": l.lemma_id, "lemma_ar": l.lemma_ar, "gloss_en": l.gloss_en, "pos": l.pos}
            for l in db.query(Lemma).order_by(Lemma.lemma_id).all()
            if auditable(l) and l.lemma_id not in done
        ]
    finally:
        db.close()
    if limit:
        rows = rows[:limit]
    print(f"{len(rows)} lemmas to audit ({len(done)} already in {out_path})", flush=True)

    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        try:
            proposals = audit_rows(batch)
        except Exception as exc:
            print(f"batch at {start} failed: {exc!r}"[:300], flush=True)
            continue
        with out_path.open("a") as f:
            for p in proposals:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
        print(f"batch at {start}: {len(proposals)}/{len(batch)} entries", flush=True)
    print("DONE", flush=True)


def apply(path: Path, write: bool, exclude: set[int]) -> None:
    proposals = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    changes, left = plan_changes(proposals)
    changes = [c for c in changes if c["lemma_id"] not in exclude]
    print(f"{len(proposals)} audited, {len(changes)} changes, {len(left)} left alone")
    print("changes by kind:", dict(Counter(c["kind"] for c in changes)))
    print("corrected, old -> new:", Counter(
        (canonical_label(c["pos"]), c["new_pos"]) for c in changes if c["kind"] == "corrected"
    ).most_common())
    print("left alone:", dict(Counter(x["why"] for x in left)))
    for c in changes:
        if c["kind"] == "corrected":
            print(f"  #{c['lemma_id']} {c['lemma_ar']} ({c['gloss_en']}): {c['pos']} -> {c['new_pos']}")
    for x in left:
        print(f"  LEFT {x['why']} #{x['lemma_id']} {x['lemma_ar']} ({x['gloss_en']}): "
              f"{x['pos']} vs {x['proposed_pos']} {x['note']}")
    if not write:
        print("Dry run. Pass --write to apply.")
        return

    from app.services.activity_log import log_activity

    db = SessionLocal()
    try:
        applied = 0
        for c in changes:
            lemma = db.get(Lemma, c["lemma_id"])
            # Skip a row edited since the audit read it.
            if lemma is None or lemma.pos != c["pos"] or lemma.gloss_en != c["gloss_en"]:
                continue
            lemma.pos = c["new_pos"]
            applied += 1
        db.commit()
        log_activity(
            db,
            "manual_fix",
            f"Lemma part-of-speech audit: corrected {applied} labels",
            {"by_kind": dict(Counter(c["kind"] for c in changes)), "left_alone": len(left)},
        )
        print(f"Applied {applied} changes")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, help="Write audit proposals to this JSONL file")
    parser.add_argument("--limit", type=int, help="Audit at most this many lemmas")
    parser.add_argument("--apply", type=Path, help="Plan changes from this proposals file")
    parser.add_argument("--write", action="store_true", help="With --apply, write the changes")
    parser.add_argument("--exclude", default="", help="Comma-separated lemma ids to leave unchanged")
    args = parser.parse_args()
    if args.apply:
        apply(args.apply, args.write, {int(x) for x in args.exclude.split(",") if x.strip()})
    elif args.out:
        run_audit(args.out, args.limit)
    else:
        parser.error("pass --out or --apply")


if __name__ == "__main__":
    main()
