#!/usr/bin/env python3
"""Create missing lemmas from a curated word list and optionally target them.

The list is a TSV of citation form, part of speech and gloss (alternatives
separated by "/"). A word already in the vocabulary with one of those senses
is reused; a word whose spelling exists with a different meaning is listed and
left alone unless its row carries a fourth column `homograph` (a different
word with the same letters, آخَر beside آخِر); anything else is created with the
given source and goes through `run_quality_gates()` without variant detection,
because the list holds reviewed citation forms. With `--program`, every word not yet learned also gets
an open `reading_targets` row under that program. Nothing here creates a card
or bypasses `start_acquisition()`.

Usage:
  cd backend
  PYTHONPATH=. python3 scripts/stage_word_list.py data/word_lists/book_gap_words_2026-11-25.tsv --source book_frequency
  PYTHONPATH=. python3 scripts/stage_word_list.py data/word_lists/alkitaab_part1_remaining.tsv --source textbook_list --program alkitaab_part1 --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("ALIF_SKIP_MIGRATIONS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import Lemma, ReadingTarget, UserLemmaKnowledge  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402
from app.services.canonical_resolution import resolve_canonical_lemma_id  # noqa: E402
from app.services.sentence_validator import normalize_alef, strip_diacritics  # noqa: E402

LEARNED = {"acquiring", "learning", "known", "lapsed"}
POS = {"noun", "verb", "adj", "adv", "prep"}
BATCH = 40


def _key(text: str) -> str:
    return normalize_alef(strip_diacritics(text)).replace("ى", "ي")


def _same_sense(gloss: str, existing_gloss: str | None) -> bool:
    existing = (existing_gloss or "").lower()
    for alt in gloss.split("/"):
        alt = re.sub(r"^to ", "", alt.strip().lower())
        if alt and re.search(rf"\b{re.escape(alt)}", existing):
            return True
    return False


def read_list(path: Path) -> list[tuple[str, str, str, bool]]:
    words = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        homograph = len(parts) == 4 and parts[3].strip() == "homograph"
        if len(parts) not in (3, 4) or parts[1] not in POS or (len(parts) == 4 and not homograph):
            raise SystemExit(f"{path}:{number}: expected form<TAB>pos<TAB>gloss[<TAB>homograph], got {line!r}")
        words.append((parts[0].strip(), parts[1], parts[2].strip(), homograph))
    return words


def _add_target(db, lemma_id: int, program: str) -> None:
    if db.query(ReadingTarget).filter_by(lemma_id=lemma_id, program=program).first() is None:
        db.add(ReadingTarget(lemma_id=lemma_id, program=program, chapter=1, text_count=1))


def _ensure_knowledge(db, lemma_id: int, source: str) -> None:
    if db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma_id).first() is None:
        db.add(UserLemmaKnowledge(lemma_id=lemma_id, knowledge_state="encountered", source=source))
        db.flush()


def stage(words: list[tuple[str, str, str, bool]], source: str, program: str | None, apply: bool) -> dict:
    db = SessionLocal()
    by_key: dict[str, list[Lemma]] = {}
    for lemma in db.query(Lemma).all():
        if lemma.lemma_ar_bare and lemma.word_category not in ("proper_name", "onomatopoeia"):
            by_key.setdefault(_key(lemma.lemma_ar_bare), []).append(lemma)
    states = dict(db.query(UserLemmaKnowledge.lemma_id, UserLemmaKnowledge.knowledge_state).all())

    report: dict[str, list] = {"learned": [], "existing": [], "created": [],
                               "sense_conflict": [], "suspended": []}
    to_create: list[dict] = []
    seen: set[str] = set()
    for lemma_ar, pos, gloss, homograph in words:
        marker = f"{_key(lemma_ar)}|{pos}"
        if marker in seen:
            continue
        seen.add(marker)
        found = by_key.get(_key(lemma_ar), [])
        matches = [l for l in found if _same_sense(gloss, l.gloss_en)]
        if matches:
            canonicals = {resolve_canonical_lemma_id(db, l.lemma_id) for l in matches}
            rows = [l for l in (db.get(Lemma, lid) for lid in canonicals) if l is not None]
            best = max(rows, key=lambda l: states.get(l.lemma_id) in LEARNED)
            state = states.get(best.lemma_id)
            entry = {"word": lemma_ar, "gloss": gloss, "lemma_id": best.lemma_id,
                     "existing": best.lemma_ar, "existing_gloss": best.gloss_en, "state": state}
            if state in LEARNED:
                report["learned"].append(entry)
            elif state == "suspended" or best.gates_completed_at is None:
                report["suspended"].append(entry)
            else:
                if apply and program:
                    _ensure_knowledge(db, best.lemma_id, source)
                    _add_target(db, best.lemma_id, program)
                report["existing"].append(entry)
            continue
        if found and not homograph:
            report["sense_conflict"].append({
                "word": lemma_ar, "gloss": gloss,
                "existing": [(l.lemma_id, l.lemma_ar, l.gloss_en) for l in found],
            })
            continue
        to_create.append({"word": lemma_ar, "gloss": gloss.replace("/", ", "), "pos": pos})
    if apply:
        db.commit()

    report["created"] = to_create
    if apply and to_create:
        from app.services.lemma_quality import run_quality_gates

        for start in range(0, len(to_create), BATCH):
            batch = to_create[start:start + BATCH]
            for entry in batch:
                lemma = Lemma(lemma_ar=entry["word"], lemma_ar_bare=strip_diacritics(entry["word"]),
                              gloss_en=entry["gloss"], pos=entry["pos"], source=source)
                db.add(lemma)
                db.flush()
                entry["lemma_id"] = lemma.lemma_id
            db.commit()
            run_quality_gates(
                db, [e["lemma_id"] for e in batch], skip_variants=True, background_enrich=False)
            db.commit()
            for entry in batch:
                canonical = resolve_canonical_lemma_id(db, entry["lemma_id"])
                lemma = db.get(Lemma, canonical)
                entry["canonical_lemma_id"] = canonical
                entry["gated"] = lemma.gates_completed_at is not None
                state = db.query(UserLemmaKnowledge.knowledge_state).filter_by(lemma_id=canonical).scalar()
                if state in LEARNED:
                    continue
                _ensure_knowledge(db, canonical, source)
                if program:
                    _add_target(db, canonical, program)
            db.commit()
            print(f"  created {min(start + BATCH, len(to_create))}/{len(to_create)}", flush=True)
    if apply:
        log_activity(
            db, "word_list_staged",
            f"Staged word list ({source}{', ' + program if program else ''}): "
            f"{len(report['created'])} created, {len(report['existing'])} existing",
            {"created": [e.get("lemma_id") for e in report["created"]],
             "sense_conflict": report["sense_conflict"], "suspended": report["suspended"]},
        )
        db.commit()
    db.close()
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("list", type=Path)
    ap.add_argument("--source", required=True, help="Lemma.source for created words")
    ap.add_argument("--program", help="reading_targets program for words not yet learned")
    ap.add_argument("--apply", action="store_true", help="Write (default: dry run)")
    args = ap.parse_args()
    report = stage(read_list(args.list), args.source, args.program, args.apply)
    print("APPLIED" if args.apply else "DRY RUN")
    for key in ("created", "existing", "sense_conflict", "suspended"):
        print(f"\n== {key} [{len(report[key])}]")
        for entry in report[key]:
            print("  ", json.dumps(entry, ensure_ascii=False))
    print(f"\n== learned [{len(report['learned'])}]")
    print("  ", "، ".join(f"{e['word']}={e['existing_gloss']}" for e in report["learned"]))


if __name__ == "__main__":
    main()
