"""Audit: Quran lemmas damaged by the dagger-alef (U+0670) normalization bug.

Before the fix, the Quran import stripped the dagger alef (a long ā written as a
combining mark in Uthmani orthography) *before* converting it to a full alef, so
خَٰلِدُونَ collapsed to the bare skeleton خلدون — which is the proper name Khaldūn
(ابن خلدون), not the participle خالِد. See quran_service._quran_bare /
normalize_quranic_to_msa and the 2026-06-02 experiment-log entry.

This audit re-normalizes the ORIGINAL Uthmani surface forms (preserved in
QuranicVerseWord.surface_form) the correct way and reports lemmas whose stored
bare matches the *collapsed* (mater-dropped) skeleton — i.e. created from the
damaged form. Source-grounded, no fuzzy heuristics.

    python3 scripts/audit_quran_dagger_alef.py            # report only
    python3 scripts/audit_quran_dagger_alef.py --json     # machine-readable
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models import Lemma, QuranicVerseWord
from app.services.quran_service import _quran_bare
from app.services.sentence_validator import (
    normalize_alef,
    strip_diacritics,
    strip_tatweel,
)

DAGGER = "ٰ"


def _old_bare(surface: str) -> str:
    """The buggy normalization: strip diacritics (deletes the dagger) first."""
    return normalize_alef(strip_tatweel(strip_diacritics(surface)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        # Surfaces that actually carry a dagger alef.
        rows = (
            db.query(QuranicVerseWord)
            .filter(QuranicVerseWord.surface_form.like(f"%{DAGGER}%"))
            .all()
        )

        # lemma_id -> set of (surface, old_bare, new_bare) where normalization differs
        by_lemma: dict[int, set] = defaultdict(set)
        for vw in rows:
            if not vw.lemma_id:
                continue
            old = _old_bare(vw.surface_form)
            new = _quran_bare(vw.surface_form)
            if old != new:
                by_lemma[vw.lemma_id].add((vw.surface_form, old, new))

        damaged = []
        for lemma_id, surfaces in by_lemma.items():
            lem = db.get(Lemma, lemma_id)
            if not lem:
                continue
            stored = normalize_alef(lem.lemma_ar_bare or "")
            # The lemma is suspect when its stored bare equals the COLLAPSED
            # skeleton of a surface that should have kept a mater.
            collapsed_hits = sorted({(s, o, n) for (s, o, n) in surfaces if normalize_alef(o) == stored})
            if not collapsed_hits:
                continue
            # Does the corrected skeleton already exist as another lemma?
            corrected = normalize_alef(collapsed_hits[0][2])
            other = (
                db.query(Lemma)
                .filter(Lemma.lemma_ar_bare == corrected, Lemma.lemma_id != lemma_id)
                .first()
            )
            damaged.append({
                "lemma_id": lemma_id,
                "lemma_ar": lem.lemma_ar,
                "stored_bare": lem.lemma_ar_bare,
                "corrected_bare": corrected,
                "gloss_en": lem.gloss_en,
                "pos": lem.pos,
                "word_category": lem.word_category,
                "source": lem.source,
                "surfaces": [s for (s, o, n) in collapsed_hits],
                "existing_target_lemma_id": other.lemma_id if other else None,
                "existing_target_lemma_ar": other.lemma_ar if other else None,
            })

        damaged.sort(key=lambda d: d["lemma_id"])
        if args.json:
            print(json.dumps(damaged, ensure_ascii=False, indent=2))
        else:
            print(f"Dagger-alef surfaces scanned: {len(rows)}")
            print(f"Damaged lemmas: {len(damaged)}\n")
            for d in damaged:
                tgt = (f"  -> existing #{d['existing_target_lemma_id']} "
                       f"{d['existing_target_lemma_ar']}") if d["existing_target_lemma_id"] else "  -> (no existing target)"
                print(f"#{d['lemma_id']:>5} {d['lemma_ar']:16} bare={d['stored_bare']!r:12} "
                      f"=> {d['corrected_bare']!r:12} cat={d['word_category']} gloss={d['gloss_en']!r}")
                print(f"       surfaces={d['surfaces']}{tgt}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
