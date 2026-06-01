#!/usr/bin/env python3
"""Audit + repair Latin homograph mis-mappings in stored sentence words.

LatinCy sometimes assigns a citation lemma that contradicts the morphology it
tagged on the same token (e.g. `pilum` → lemma `pilus`/hair while tagging the
token Gender=Neut, whose lemma is `pilum`/javelin). `app/services/languages/la.py`
now corrects this at the lemmatizer boundary via `lemma_override`, so NEW
mappings are clean — this tool repairs the rows written before the override
existed, and serves as a recurring discovery sweep for the next homograph.

It reports three classes; only the first is auto-fixable here:

  (A) override disagreements — a stored `SentenceWord` whose surface carries a
      homograph override and whose stored lemma differs from what the override
      assigns in sentence context. `apply --apply` re-maps these.
  (B) duplicate lemma rows — the same `lemma_bare` held by ≥2 content lemma_ids
      (e.g. two `condo` entries). Reported for manual merge; NOT auto-fixed.
  (C) glossless mapping targets — lemmas with empty `gloss_en` that sentence
      words point at (violates the no-gloss invariant). Reported; NOT auto-fixed.

Usage:
    python scripts/audit_homograph_mappings.py audit --language la
    python scripts/audit_homograph_mappings.py apply --language la            # dry-run
    python scripts/audit_homograph_mappings.py apply --language la --apply     # commits

Back up the DB before `--apply` (see CLAUDE.md server-ops). The override surface
set lives in la.py (`_LEMMA_OVERRIDES`); extend it there, not here.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import database
from app.models import Lemma, Sentence, SentenceWord
from app.services.activity_log import log_activity
from app.services.languages import get_provider
from app.services.languages.la import lemma_override, override_surface_keys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("audit_homograph_mappings")


def _norm(provider, form: str) -> str:
    return provider.normalize_bare(form)


def _bare_to_lemma_id(db, language_code: str) -> dict[str, int]:
    """bare form -> lemma_id, preferring the canonical row when a bare has both
    a canonical and a variant entry."""
    out: dict[str, int] = {}
    rows = (
        db.query(Lemma.lemma_id, Lemma.lemma_bare, Lemma.canonical_lemma_id)
        .filter(Lemma.language_code == language_code)
        .all()
    )
    for lid, bare, canon in rows:
        if not bare:
            continue
        if bare not in out or canon is None:
            out[bare] = lid
    return out


def _find_disagreements(db, language_code: str) -> list[dict]:
    """Stored SentenceWord rows whose surface carries an override and whose
    stored lemma differs from the override's in-context answer."""
    provider = get_provider(language_code)
    keys = override_surface_keys()
    if not keys:
        return []
    bare_to_id = _bare_to_lemma_id(db, language_code)

    rows = (
        db.query(
            SentenceWord.id,
            SentenceWord.surface_form,
            SentenceWord.lemma_id,
            Sentence.id,
            Sentence.text,
            Sentence.is_active,
        )
        .join(Sentence, Sentence.id == SentenceWord.sentence_id)
        .filter(Sentence.language_code == language_code)
        .filter(SentenceWord.lemma_id.isnot(None))
        .all()
    )

    out: list[dict] = []
    lemma_cache: dict[int, Lemma] = {}
    for sw_id, surface, stored_id, sent_id, text, is_active in rows:
        if not surface or _norm(provider, surface) not in keys:
            continue
        morph = provider.analyze(surface, text)
        correct_form = lemma_override(surface, morph.pos, morph.features)
        if not correct_form:
            continue  # override didn't fire (e.g. genuine masc-acc pilum)
        correct_bare = _norm(provider, correct_form)
        correct_id = bare_to_id.get(correct_bare)
        if correct_id is None or correct_id == stored_id:
            continue
        if stored_id not in lemma_cache:
            lemma_cache[stored_id] = db.query(Lemma).get(stored_id)
        stored = lemma_cache[stored_id]
        out.append({
            "sentence_word_id": sw_id,
            "sentence_id": sent_id,
            "is_active": bool(is_active),
            "surface": surface,
            "context": text,
            "pos": morph.pos,
            "gender": (morph.features or {}).get("Gender"),
            "stored_id": stored_id,
            "stored_form": stored.lemma_form if stored else None,
            "stored_gloss": stored.gloss_en if stored else None,
            "correct_id": correct_id,
            "correct_form": correct_form,
        })
    return out


def _report_duplicate_lemmas(db, language_code: str) -> list[dict]:
    by_bare: dict[str, list[Lemma]] = defaultdict(list)
    for lem in (
        db.query(Lemma)
        .filter(Lemma.language_code == language_code)
        .filter(Lemma.word_category.is_(None))
        .all()
    ):
        if lem.lemma_bare:
            by_bare[lem.lemma_bare].append(lem)
    return [
        {"bare": bare, "lemma_ids": [l.lemma_id for l in lems],
         "forms": [l.lemma_form for l in lems], "glosses": [l.gloss_en for l in lems]}
        for bare, lems in sorted(by_bare.items())
        if len(lems) > 1
    ]


def _report_glossless_targets(db, language_code: str) -> list[dict]:
    rows = (
        db.query(Lemma.lemma_id, Lemma.lemma_form)
        .join(SentenceWord, SentenceWord.lemma_id == Lemma.lemma_id)
        .join(Sentence, Sentence.id == SentenceWord.sentence_id)
        .filter(Sentence.language_code == language_code)
        .filter((Lemma.gloss_en.is_(None)) | (Lemma.gloss_en == ""))
        # Non-content lemmas legitimately carry no gloss (proper names use the
        # lemma_form as the gloss; function/not-word are inert), so they are not
        # gloss-invariant violations — exclude to avoid permanent false positives.
        .filter(
            (Lemma.word_category.is_(None))
            | (Lemma.word_category.notin_(("proper_name", "function_word", "not_word")))
        )
        .distinct()
        .all()
    )
    return [{"lemma_id": lid, "lemma_form": form} for lid, form in rows]


def cmd_audit(args) -> None:
    db = database.SessionLocal()
    try:
        dis = _find_disagreements(db, args.language)
        dups = _report_duplicate_lemmas(db, args.language)
        glossless = _report_glossless_targets(db, args.language)
    finally:
        db.close()

    print(f"\n(A) OVERRIDE DISAGREEMENTS — {len(dis)} sentence_words remappable")
    by_pair: dict[tuple, int] = defaultdict(int)
    for d in dis:
        by_pair[(d["surface"], d["stored_form"], d["correct_form"])] += 1
    for (surf, frm, to), n in sorted(by_pair.items(), key=lambda kv: -kv[1]):
        print(f"    x{n:>3}  {surf!r}: {frm} -> {to}")

    print(f"\n(B) DUPLICATE LEMMA ROWS — {len(dups)} bare forms (manual merge)")
    for d in dups:
        print(f"    {d['bare']}: ids={d['lemma_ids']} glosses={d['glosses']}")

    print(f"\n(C) GLOSSLESS MAPPING TARGETS — {len(glossless)} lemmas (manual)")
    for g in glossless:
        print(f"    #{g['lemma_id']} {g['lemma_form']}")


def cmd_apply(args) -> None:
    db = database.SessionLocal()
    try:
        dis = _find_disagreements(db, args.language)
        if not dis:
            print("No override disagreements found — nothing to apply.")
            return
        active = [d for d in dis if d["is_active"]]
        print(f"Found {len(dis)} remappable sentence_words "
              f"({len(active)} on active sentences).")
        for d in dis:
            print(f"  sw#{d['sentence_word_id']} s#{d['sentence_id']} "
                  f"{d['surface']!r} {d['stored_form']}(#{d['stored_id']}) -> "
                  f"{d['correct_form']}(#{d['correct_id']})  «{d['context'][:60]}»")
        if not args.apply:
            print("\nDry-run. Re-run with --apply to commit.")
            return

        for d in dis:
            sw = db.query(SentenceWord).get(d["sentence_word_id"])
            if sw is not None:
                sw.lemma_id = d["correct_id"]
        db.commit()
        log_activity(
            db,
            event_type="manual_action",
            summary=f"Remapped {len(dis)} Latin homograph sentence_words "
                    f"(override disagreement fix)",
            detail={"language": args.language,
                    "remapped": [(d["surface"], d["stored_id"], d["correct_id"])
                                 for d in dis]},
            language_code=args.language,
        )
        print(f"\nApplied: remapped {len(dis)} sentence_words.")
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("audit", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--language", default="la")
        if name == "apply":
            p.add_argument("--apply", action="store_true",
                           help="commit the remap (default is dry-run)")
    args = ap.parse_args()
    (cmd_audit if args.cmd == "audit" else cmd_apply)(args)


if __name__ == "__main__":
    main()
