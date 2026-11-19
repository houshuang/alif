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
    python scripts/audit_lemma_pos.py --apply /tmp/pos_audit.jsonl --write

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

BATCH_SIZE = 60
OPEN_CLASSES = {"verb", "noun", "adj", "adv"}
POS_VALUES = [
    "verb", "noun", "adj", "adv", "prep", "pron", "conj", "particle",
    "num", "interj", "expr", "noun_prop",
]
LABEL_SPELLINGS = {
    "adjective": "adj",
    "adverb": "adv",
    "preposition": "prep",
    "conjunction": "conj",
    "v": "verb",
    "passive participle": "adj",
}
SKIP_CATEGORIES = {"proper_name", "onomatopoeia", "practiced_name"}
SKIP_POS = {"noun_prop", "proper_noun", "name", "foreign"}

SYSTEM_PROMPT = (
    "You are an Arabic lexicographer checking part-of-speech labels in a "
    "learner's dictionary."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lemma_id": {"type": "integer"},
                    "pos": {"type": "string", "enum": POS_VALUES},
                    "conflict": {"type": "boolean"},
                    "note": {"type": "string"},
                },
                "required": ["lemma_id", "pos", "conflict", "note"],
            },
        }
    },
    "required": ["entries"],
}


def canonical_label(pos: str | None) -> str | None:
    if not pos:
        return None
    p = pos.strip().lower()
    return LABEL_SPELLINGS.get(p, p)


def build_prompt(rows: list[dict]) -> str:
    lines = "\n".join(
        f"- lemma_id={r['lemma_id']}; word={r['lemma_ar']}; gloss={r['gloss_en']}"
        for r in rows
    )
    return f"""For each dictionary headword below, give its part of speech as a dictionary would list that headword.

Rules:
- Judge from the vocalized Arabic form together with the gloss.
- A verbal noun (masdar) is a noun. A person, trade or role (مُدِير, عَامِل, مُتَقَاعِد) is a noun.
- A participle or nisba used mainly to describe (مُزْدَحِم, ثَمِين, نَبَاتِيّ) is an adj.
- A fixed phrase of several words is expr.
- Set conflict=true when the vocalized Arabic form and the English gloss cannot be the same word in the same part of speech, for example the noun form وَصْل with the gloss "to arrive", or a singular noun glossed as a plural or as a verb. In that case still give the part of speech of the Arabic form, and say in the note what disagrees.
- Otherwise conflict=false and note is an empty string.

Return one entry for every lemma_id.

{lines}"""


def run_audit(out_path: Path, limit: int | None) -> None:
    from app.services.llm import generate_completion

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
            if l.gloss_en
            and l.word_category not in SKIP_CATEGORIES
            and (l.pos or "").lower() not in SKIP_POS
            and l.lemma_id not in done
        ]
    finally:
        db.close()
    if limit:
        rows = rows[:limit]
    print(f"{len(rows)} lemmas to audit ({len(done)} already in {out_path})", flush=True)

    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        by_id = {r["lemma_id"]: r for r in batch}
        try:
            result = generate_completion(
                prompt=build_prompt(batch),
                system_prompt=SYSTEM_PROMPT,
                json_schema=SCHEMA,
                timeout=300,
                model_override="claude_haiku",
                task_type="lemma_pos_audit",
            )
        except Exception as exc:
            print(f"batch at {start} failed: {exc!r}"[:300], flush=True)
            continue
        entries = result.get("entries", []) if isinstance(result, dict) else []
        written = 0
        with out_path.open("a") as f:
            for e in entries:
                row = by_id.pop(e.get("lemma_id"), None) if isinstance(e, dict) else None
                if row is None or e.get("pos") not in POS_VALUES:
                    continue
                f.write(json.dumps({
                    **row,
                    "proposed_pos": e["pos"],
                    "conflict": bool(e.get("conflict")),
                    "note": str(e.get("note") or "")[:200],
                }, ensure_ascii=False) + "\n")
                written += 1
        print(f"batch at {start}: {written}/{len(batch)} entries", flush=True)
    print("DONE", flush=True)


def plan_changes(proposals: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (changes, left_alone) from audit proposals."""
    changes: list[dict] = []
    left: list[dict] = []
    for p in proposals:
        current = p.get("pos")
        tidy = canonical_label(current)
        proposed = p["proposed_pos"]
        if tidy == proposed:
            if tidy != current:
                changes.append({**p, "new_pos": tidy, "kind": "label_spelling"})
            continue
        if p.get("conflict"):
            if tidy != current:
                changes.append({**p, "new_pos": tidy, "kind": "label_spelling"})
            left.append({**p, "why": "form_gloss_conflict"})
            continue
        if proposed in OPEN_CLASSES and (tidy is None or tidy in OPEN_CLASSES):
            changes.append({**p, "new_pos": proposed, "kind": "corrected"})
            continue
        if tidy != current:
            changes.append({**p, "new_pos": tidy, "kind": "label_spelling"})
        left.append({**p, "why": "outside_open_classes"})
    return changes, left


def apply(path: Path, write: bool) -> None:
    proposals = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    changes, left = plan_changes(proposals)
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
    args = parser.parse_args()
    if args.apply:
        apply(args.apply, args.write)
    elif args.out:
        run_audit(args.out, args.limit)
    else:
        parser.error("pass --out or --apply")


if __name__ == "__main__":
    main()
