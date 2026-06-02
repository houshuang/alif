"""Repair Quran lemmas mis-created from the dagger-alef (U+0670) normalization bug.

Found by scripts/audit_quran_dagger_alef.py. The import dropped the long ā (a
dagger alef) before converting it, collapsing words onto the wrong skeleton —
e.g. خَٰلِدُونَ ("abiding forever") → خلدون = the proper name Khaldūn.

This script fixes the *no-collision* cases only: re-headword the lemma to its
dictionary form, clear the corrupted enrichment, and re-run run_quality_gates()
so forms/etymology/transliteration regenerate from the correct headword. It also
fixes the lemma's FrequencyCoreEntry display_form/gloss where present.

It deliberately does NOT touch the cases that need canonical merges (the
dictionary form already exists as another lemma) or clitic decomposition — those
are handled separately. See the audit output and the 2026-06-02 experiment-log
entry.

    python3 scripts/fix_quran_dagger_alef_lemmas.py            # dry run
    python3 scripts/fix_quran_dagger_alef_lemmas.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models import FrequencyCoreEntry, Lemma
from app.services.lemma_quality import run_quality_gates
from app.services.sentence_validator import normalize_alef

# lemma_id -> corrected dictionary headword. Only no-collision re-headwords here.
FIXES: dict[int, dict] = {
    2887: {
        "lemma_ar": "خَالِد",
        "lemma_ar_bare": "خالد",
        "pos": "adj",
        "gloss_en": "eternal; everlasting; one who abides forever",
    },
    2897: {
        "lemma_ar": "مَيِّت",
        "lemma_ar_bare": "ميت",
        "pos": "adj",
        "gloss_en": "dead; deceased",
    },
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        fixed_ids: list[int] = []
        for lemma_id, fix in FIXES.items():
            lem = db.get(Lemma, lemma_id)
            if not lem:
                print(f"#{lemma_id}: NOT FOUND, skipping")
                continue
            # Guard: refuse if the dictionary form now collides with another lemma.
            nb = normalize_alef(fix["lemma_ar_bare"])
            clash = (
                db.query(Lemma)
                .filter(Lemma.lemma_ar_bare == nb, Lemma.lemma_id != lemma_id)
                .first()
            )
            if clash:
                print(f"#{lemma_id}: target bare {nb!r} collides with #{clash.lemma_id} "
                      f"{clash.lemma_ar} — needs a MERGE, skipping")
                continue

            print(f"#{lemma_id}: {lem.lemma_ar!r}/{lem.lemma_ar_bare!r} -> "
                  f"{fix['lemma_ar']!r}/{fix['lemma_ar_bare']!r}  gloss={fix['gloss_en']!r}")
            if not args.apply:
                continue

            lem.lemma_ar = fix["lemma_ar"]
            lem.lemma_ar_bare = fix["lemma_ar_bare"]
            lem.pos = fix["pos"]
            lem.gloss_en = fix["gloss_en"]
            # Clear corrupted enrichment so run_quality_gates regenerates it.
            lem.forms_json = None
            lem.forms_translit_json = None
            lem.etymology_json = None
            lem.memory_hooks_json = None
            lem.wazn = None
            lem.wazn_meaning = None
            lem.transliteration_ala_lc = None
            lem.gates_completed_at = None

            # FrequencyCoreEntry display_form/gloss, if this lemma is in the core.
            for fce in db.query(FrequencyCoreEntry).filter(FrequencyCoreEntry.lemma_id == lemma_id).all():
                fce.display_form = fix["lemma_ar"]
                fce.gloss_en = fix["gloss_en"]
                fce.pos = fix["pos"]

            fixed_ids.append(lemma_id)

        if args.apply and fixed_ids:
            db.commit()
            print(f"\nRe-running quality gates (enrichment inline) for {fixed_ids} ...")
            result = run_quality_gates(db, fixed_ids, enrich=True, background_enrich=False)
            db.commit()
            print("quality gates:", result)

            from app.services.activity_log import log_activity
            log_activity(
                db,
                event_type="manual_action",
                summary=f"Repaired {len(fixed_ids)} Quran dagger-alef lemmas (re-headword + re-enrich)",
                detail={"lemma_ids": fixed_ids, "fixes": {str(k): FIXES[k] for k in fixed_ids}},
            )
            db.commit()
            # Show the result
            for lemma_id in fixed_ids:
                lem = db.get(Lemma, lemma_id)
                print(f"  #{lemma_id} now: {lem.lemma_ar} bare={lem.lemma_ar_bare} "
                      f"wazn={lem.wazn} translit={lem.transliteration_ala_lc} "
                      f"forms={lem.forms_json}")
        elif not args.apply:
            print("\n(dry run — re-run with --apply to write)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
