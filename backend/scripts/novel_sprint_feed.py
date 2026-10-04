#!/usr/bin/env python3
"""Stage a real text's vocabulary gaps as reading targets (novel sprint).

The sprint goal is to finish one real Arabic novel. This script turns a chapter
(or the whole book) into `reading_targets` rows so that ordinary intake — the
session builder's intro cards and the cron's new-word selection — introduces the
book's own gap words first, biggest unlocks first, within the active daily budget.
It never creates cards, never bypasses `start_acquisition()`, and never changes
an existing word's provenance or attention disposition directly: the attention
refresh keeps targeted words `maintain` because the target row exists.

Pipeline (same hardened text→lemma path as `reading_readiness.py`):

  1. analyze(): token-weighted coverage + gap words ranked by in-text frequency
  2. in-vocabulary gaps  -> ReadingTarget (+ an `encountered` knowledge row if none)
  3. out-of-vocabulary gaps -> need a gloss. Glosses come from a reviewed JSON file
     (`--glosses`, {bare_or_vocalized: {"lemma_ar":..., "gloss_en":..., "pos":...}}).
     Proper names and anything without a gloss are skipped and listed, never
     created (hard invariant: no words without an English gloss).
     New lemmas get `source="novel_sprint"`, go through `run_quality_gates()`,
     and only then get their target row.

Usage:
  cd backend
  PYTHONPATH=. python3 scripts/novel_sprint_feed.py --text ch1.txt --program rijal_fi_al_shams \
      --chapter 1 [--limit 60] [--glosses glosses.json] [--min-count 1] [--dry-run]
  PYTHONPATH=. python3 scripts/novel_sprint_feed.py --status --program rijal_fi_al_shams

`--dry-run` prints what would be staged, including the OOV words that still need
a gloss, so the gloss file can be written and reviewed before anything is created.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ALIF_SKIP_MIGRATIONS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import Lemma, ReadingTarget, UserLemmaKnowledge  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402
from app.services.canonical_resolution import resolve_canonical_lemma_id  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reading_readiness import analyze, load_text  # noqa: E402

SOURCE = "novel_sprint"
_DIACRITICS = re.compile(r"[ً-ْٰ]")


def bare(s: str) -> str:
    s = _DIACRITICS.sub("", s)
    return s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")


def _load_glosses(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for key, val in raw.items():
        if not isinstance(val, dict) or not (val.get("gloss_en") or "").strip():
            continue
        out[bare(key)] = val
        if val.get("lemma_ar"):
            out[bare(val["lemma_ar"])] = val
    return out


def _ensure_knowledge(db, lemma_id: int, lemma_source: str | None) -> UserLemmaKnowledge:
    ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma_id).first()
    if ulk is None:
        ulk = UserLemmaKnowledge(
            lemma_id=lemma_id,
            knowledge_state="encountered",
            source=lemma_source or SOURCE,
        )
        db.add(ulk)
        db.flush()
    return ulk


def _add_target(db, lemma_id: int, program: str, chapter: int | None, count: int) -> bool:
    existing = (
        db.query(ReadingTarget)
        .filter(ReadingTarget.lemma_id == lemma_id, ReadingTarget.program == program)
        .first()
    )
    if existing is not None:
        changed = False
        if existing.retired_at is not None:
            existing.retired_at = None
            changed = True
        if (existing.text_count or 0) < count:
            existing.text_count = count
            changed = True
        if existing.chapter is None and chapter is not None:
            existing.chapter = chapter
            changed = True
        return changed
    db.add(ReadingTarget(lemma_id=lemma_id, program=program, chapter=chapter, text_count=count))
    return True


def stage(args) -> dict:
    db = SessionLocal()
    text = load_text(args.text)
    result = analyze(db, text, top=max(args.limit, 40))
    # Encountered words are "in progress" for coverage but still need introducing,
    # so they are staged alongside the true gaps (biggest unlocks first).
    gaps = [g for g in result["gaps_full"] + result.get("encountered_gaps", [])
            if (g.get("count") or 0) >= args.min_count]
    gaps.sort(key=lambda g: -(g.get("count") or 0))
    gaps = gaps[: args.limit]
    glosses = _load_glosses(args.glosses)

    report = {
        "program": args.program,
        "chapter": args.chapter,
        "coverage": result["coverage"],
        "tokens_total": result["tokens_total"],
        "targeted_existing": [],
        "created": [],
        "skipped_no_gloss": [],
        "skipped_proper_name": [],
        "already_learned": [],
    }

    # Phase 1: in-vocabulary gaps (pure DB writes, fast).
    for g in gaps:
        if g["kind"] not in {"new_in_vocab", "encountered"} or g.get("lemma_id") is None:
            continue
        lid = resolve_canonical_lemma_id(db, g["lemma_id"])
        lemma = db.get(Lemma, lid)
        if lemma is None or lemma.word_category in {"proper_name", "onomatopoeia"}:
            report["skipped_proper_name"].append(g["display"])
            continue
        ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lid).first()
        if ulk is not None and ulk.knowledge_state in {"acquiring", "learning", "known"}:
            report["already_learned"].append(lemma.lemma_ar)
            continue
        if not args.dry_run:
            _ensure_knowledge(db, lid, lemma.source)
            _add_target(db, lid, args.program, args.chapter, g["count"])
        report["targeted_existing"].append({"lemma_id": lid, "lemma_ar": lemma.lemma_ar,
                                            "gloss_en": lemma.gloss_en, "count": g["count"]})
    if not args.dry_run:
        db.commit()

    # Phase 2: out-of-vocabulary gaps. Creation is gated on a reviewed gloss.
    new_ids: list[int] = []
    for g in gaps:
        if g["kind"] != "unmapped":
            continue
        surface = g["display"]
        spec = glosses.get(bare(surface))
        if spec is None:
            report["skipped_no_gloss"].append({"surface": surface, "count": g["count"]})
            continue
        if (spec.get("pos") or "").lower() in {"noun_prop", "proper_name", "name"}:
            report["skipped_proper_name"].append(surface)
            continue
        citation = (spec.get("lemma_ar") or surface).strip()
        citation_bare = bare(citation)
        existing = (
            db.query(Lemma)
            .filter(Lemma.lemma_ar_bare == citation_bare, Lemma.canonical_lemma_id.is_(None))
            .all()
        )
        if len(existing) == 1:
            lid = existing[0].lemma_id
            ulk = db.query(UserLemmaKnowledge).filter_by(lemma_id=lid).first()
            if ulk is not None and ulk.knowledge_state in {"acquiring", "learning", "known"}:
                report["already_learned"].append(existing[0].lemma_ar)
                continue
            if not args.dry_run:
                _ensure_knowledge(db, lid, existing[0].source)
                _add_target(db, lid, args.program, args.chapter, g["count"])
            report["targeted_existing"].append({"lemma_id": lid, "lemma_ar": existing[0].lemma_ar,
                                                "gloss_en": existing[0].gloss_en, "count": g["count"]})
            continue
        if len(existing) > 1:
            # Ambiguous bare identity: the mapping layer could not resolve it either.
            report["skipped_no_gloss"].append({"surface": surface, "count": g["count"],
                                               "reason": "ambiguous_bare_identity"})
            continue
        entry = {"surface": surface, "lemma_ar": citation, "gloss_en": spec["gloss_en"].strip(),
                 "pos": spec.get("pos"), "count": g["count"]}
        if not args.dry_run:
            lemma = Lemma(
                lemma_ar=citation,
                lemma_ar_bare=citation_bare,
                gloss_en=entry["gloss_en"],
                pos=spec.get("pos"),
                transliteration_ala_lc=spec.get("transliteration"),
                register=spec.get("register"),
                source=SOURCE,
            )
            db.add(lemma)
            db.flush()
            entry["lemma_id"] = lemma.lemma_id
            new_ids.append(lemma.lemma_id)
        report["created"].append(entry)
    if not args.dry_run:
        db.commit()

    # Phase 3: quality gates for the new rows (LLM work; the session is clean),
    # then target rows for whatever the gates kept canonical.
    if new_ids and not args.dry_run:
        from app.services.lemma_quality import run_quality_gates
        run_quality_gates(db, new_ids, background_enrich=True)
        db.commit()
        for entry in report["created"]:
            lid = entry.get("lemma_id")
            if lid is None:
                continue
            canonical = resolve_canonical_lemma_id(db, lid)
            lemma = db.get(Lemma, canonical)
            _ensure_knowledge(db, canonical, lemma.source)
            _add_target(db, canonical, args.program, args.chapter, entry["count"])
            entry["canonical_lemma_id"] = canonical
            entry["gated"] = lemma.gates_completed_at is not None
        db.commit()
        log_activity(db, "novel_sprint_stage",
                     f"Staged reading targets for {args.program} ch{args.chapter}: "
                     f"{len(report['targeted_existing'])} existing, {len(report['created'])} created",
                     {k: v for k, v in report.items() if k != "coverage"})
        db.commit()
    db.close()
    return report


def status(args) -> dict:
    db = SessionLocal()
    rows = (
        db.query(ReadingTarget, Lemma, UserLemmaKnowledge)
        .join(Lemma, Lemma.lemma_id == ReadingTarget.lemma_id)
        .outerjoin(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == ReadingTarget.lemma_id)
        .filter(ReadingTarget.program == args.program, ReadingTarget.retired_at.is_(None))
        .order_by(ReadingTarget.chapter, ReadingTarget.text_count.desc())
        .all()
    )
    by_state: dict[str, int] = {}
    items = []
    for target, lemma, ulk in rows:
        state = ulk.knowledge_state if ulk else "none"
        by_state[state] = by_state.get(state, 0) + 1
        items.append({"lemma_id": lemma.lemma_id, "lemma_ar": lemma.lemma_ar, "chapter": target.chapter,
                      "count": target.text_count, "state": state,
                      "disposition": ulk.attention_disposition if ulk else None,
                      "gated": lemma.gates_completed_at is not None})
    db.close()
    return {"program": args.program, "open_targets": len(items), "by_state": by_state, "items": items}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--program", required=True, help="Program key, e.g. rijal_fi_al_shams")
    ap.add_argument("--text", type=Path, help="Chapter/book text (txt/html/epub)")
    ap.add_argument("--chapter", type=int, default=None)
    ap.add_argument("--limit", type=int, default=60, help="Max gap words to stage this run")
    ap.add_argument("--min-count", type=int, default=1, help="Minimum in-text occurrences")
    ap.add_argument("--glosses", type=Path, default=None, help="Reviewed gloss JSON for OOV words")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true", help="Print open targets for the program")
    ap.add_argument("--json", type=Path, default=None, help="Write the report here")
    args = ap.parse_args()

    if args.status:
        out = status(args)
    else:
        if args.text is None:
            ap.error("--text is required unless --status")
        out = stage(args)
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.json:
        args.json.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
