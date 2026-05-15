#!/usr/bin/env python3
"""OCR textbook_scan cleanup: al-display fixes, chimera suspends, manual fixes.

Pre-existing OCR-imported lemmas that have one or more of:
  Phase A — `lemma_ar` retains the textbook surface with an integrated al-
            prefix, while `lemma_ar_bare` correctly omits it. Display-only
            bug: the user sees "الْمَاشِي" instead of "ماشِي" on intro
            cards. Fix by re-analyzing the stored surface with CAMeL, and
            if `prc0='Al_det'`, replacing `lemma_ar` with the CAMeL lex.

  Phase B — Cross-root chimera lemmas where `lemma_ar`'s root differs from
            `lemma_ar_bare`'s root. Three confirmed cases:
              - #2307 lemma_ar='آنِسَة' bare='نسي' gloss='Miss'  → bare belongs
                to "to forget" but the gloss/display says "Miss". 129 SW +
                27 RL + 38 sentence target refs all wrongly attribute "Miss"
                to "to forget" surfaces.
              - #3450 'فارسٌ' / 'عشب' / 'horseman'
              - #3452 'اشتدَّ' / 'طعام' / 'to intensify'
            Action: suspend ULK and NULL out downstream refs so the cron
            re-resolves them via the comprehensive lemma lookup.

  Phase C — #1527 إلزامي with corrupted bare 'زامي' (CAMeL mis-stripped
            integral إل as a clitic). Set bare to 'الزامي' (matching how
            the integral-al lemmas like الذي/التي are stored).

Each phase is independently runnable with --phase A|B|C.

Usage:
    python3 scripts/cleanup_ocr_lemma_corruption_2026_05_15.py --dry-run
    python3 scripts/cleanup_ocr_lemma_corruption_2026_05_15.py --phase A
    python3 scripts/cleanup_ocr_lemma_corruption_2026_05_15.py
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    Lemma, ReviewLog, Sentence, SentenceWord, StoryWord, UserLemmaKnowledge,
)
from app.services.activity_log import log_activity  # noqa: E402
from app.services.morphology import (  # noqa: E402
    CAMEL_AVAILABLE, analyze_word_camel, get_best_lemma_mle,
)
from app.services.sentence_validator import (  # noqa: E402
    normalize_alef, strip_diacritics,
)


CHIMERA_IDS = [2307, 3450, 3452]


def phase_a(db, dry_run: bool) -> dict:
    """Fix lemmas where stored lemma_ar has an al- prefix the bare omits.

    Re-analyzes each candidate with CAMeL. If the top analysis says
    prc0='Al_det', replaces lemma_ar with the CAMeL lex (canonical vocalized
    form without al-). Skips cases where CAMeL doesn't see Al_det (could be
    integral-al lemma like الذي, or analysis is ambiguous).
    """
    print("\n=== Phase A: al-display fixes ===")
    candidates = []
    for l in db.query(Lemma).filter(Lemma.canonical_lemma_id.is_(None)).all():
        if not l.lemma_ar or not l.lemma_ar_bare:
            continue
        ar_stripped = normalize_alef(strip_diacritics(l.lemma_ar))
        bare_norm = normalize_alef(l.lemma_ar_bare)
        if ar_stripped.startswith("ال") and not bare_norm.startswith("ال") and ar_stripped != bare_norm:
            candidates.append(l)

    counts = {"updated": 0, "skipped_integral_al": 0, "skipped_no_camel": 0,
              "skipped_mismatch": 0}
    print(f"  Found {len(candidates)} candidates")

    for l in candidates:
        analyses = analyze_word_camel(l.lemma_ar)
        if not analyses:
            counts["skipped_no_camel"] += 1
            continue
        # Find the top analysis whose stripped lex matches our bare (so we
        # don't accidentally re-write the lemma into a different sense)
        chosen = None
        for a in analyses[:5]:
            lex = a.get("lex") or ""
            if not lex:
                continue
            if normalize_alef(strip_diacritics(lex)) == normalize_alef(l.lemma_ar_bare):
                chosen = a
                break
        if chosen is None:
            counts["skipped_mismatch"] += 1
            continue
        prc0 = chosen.get("prc0") or ""
        if "Al_det" not in prc0:
            # CAMeL doesn't see the al- as the definite article — could be
            # integral. Be conservative: leave alone.
            counts["skipped_integral_al"] += 1
            continue
        new_ar = chosen["lex"]
        print(f"  #{l.lemma_id} {l.lemma_ar!r} → {new_ar!r}  (bare={l.lemma_ar_bare!r} gloss={l.gloss_en!r})")
        if not dry_run:
            l.lemma_ar = new_ar
        counts["updated"] += 1

    if not dry_run:
        db.commit()
    print(f"  Result: {counts}")
    return counts


def phase_b(db, dry_run: bool) -> dict:
    """Suspend chimera lemmas and NULL out downstream refs."""
    print("\n=== Phase B: chimera suspend + downstream NULL ===")
    counts = {"suspended": 0, "sw_nulled": 0, "stw_nulled": 0,
              "sentence_target_nulled": 0}

    for lid in CHIMERA_IDS:
        l = db.get(Lemma, lid)
        if not l:
            print(f"  SKIP #{lid}: missing")
            continue
        ulk = db.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == lid
        ).first()

        # Count what we're about to NULL
        sw_count = db.query(SentenceWord).filter(SentenceWord.lemma_id == lid).count()
        stw_count = db.query(StoryWord).filter(StoryWord.lemma_id == lid).count()
        st_count = db.query(Sentence).filter(Sentence.target_lemma_id == lid).count()

        print(f"  #{lid} {l.lemma_ar!r}/{l.lemma_ar_bare!r} gloss={l.gloss_en!r}")
        print(f"    suspending ULK; NULLing {sw_count} SW + {stw_count} StoryWord "
              f"+ {st_count} sentence targets")

        if dry_run:
            continue

        if ulk and ulk.knowledge_state != "suspended":
            ulk.knowledge_state = "suspended"
            ulk.experiment_group = None
            ulk.experiment_intro_shown_at = None
            counts["suspended"] += 1

        # NULL refs — the comprehensive lookup will retry on next cron pass
        if sw_count:
            db.query(SentenceWord).filter(SentenceWord.lemma_id == lid).update(
                {"lemma_id": None}, synchronize_session=False
            )
            counts["sw_nulled"] += sw_count
        if stw_count:
            db.query(StoryWord).filter(StoryWord.lemma_id == lid).update(
                {"lemma_id": None}, synchronize_session=False
            )
            counts["stw_nulled"] += stw_count
        if st_count:
            db.query(Sentence).filter(Sentence.target_lemma_id == lid).update(
                {"target_lemma_id": None}, synchronize_session=False
            )
            counts["sentence_target_nulled"] += st_count

        # Tag the lemma so it's obvious in future audits
        existing_note = l.decomposition_note or {}
        if isinstance(existing_note, str):
            import json
            try:
                existing_note = json.loads(existing_note)
            except Exception:
                existing_note = {}
        existing_note["chimera"] = True
        existing_note["chimera_reason"] = (
            f"lemma_ar={l.lemma_ar!r} / bare={l.lemma_ar_bare!r} — root mismatch; "
            f"suspended 2026-05-15"
        )
        l.decomposition_note = existing_note

        db.commit()

    print(f"  Result: {counts}")
    return counts


def phase_c(db, dry_run: bool) -> dict:
    """Fix #1527 إِلْزامِيّ — corrupted bare 'زامي' should be 'الزامي'.

    CAMeL mis-stripped the integral إل as if it were al- + ل clitic. The
    correct unvocalized bare is 'الزامي' (matching how الذي/التي/الآن are
    stored — al- integral, kept in the bare).
    """
    print("\n=== Phase C: #1527 إلزامي bare correction ===")
    counts = {"updated": 0}
    l = db.get(Lemma, 1527)
    if not l:
        print("  SKIP: #1527 missing")
        return counts
    print(f"  #1527 lemma_ar={l.lemma_ar!r} current bare={l.lemma_ar_bare!r}")
    new_bare = "الزامي"
    if l.lemma_ar_bare == new_bare:
        print("  Already correct, skipping")
        return counts
    print(f"  Setting bare → {new_bare!r}")
    if not dry_run:
        l.lemma_ar_bare = new_bare
        db.commit()
    counts["updated"] = 1
    print(f"  Result: {counts}")
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--phase", choices=["A", "B", "C", "all"], default="all",
        help="Run only one phase (default: all)",
    )
    args = parser.parse_args()

    if not CAMEL_AVAILABLE:
        print("CAMeL Tools not available", file=sys.stderr)
        sys.exit(1)

    db = SessionLocal()
    summary = {}
    try:
        if args.phase in ("A", "all"):
            summary["phase_a"] = phase_a(db, args.dry_run)
        if args.phase in ("B", "all"):
            summary["phase_b"] = phase_b(db, args.dry_run)
        if args.phase in ("C", "all"):
            summary["phase_c"] = phase_c(db, args.dry_run)

        if args.dry_run:
            print("\nDry run — no DB writes.")
        else:
            log_activity(
                db,
                event_type="manual_action",
                summary=(
                    "OCR lemma corruption cleanup: "
                    f"phase_a={summary.get('phase_a', {}).get('updated', 0)} updated, "
                    f"phase_b={summary.get('phase_b', {}).get('suspended', 0)} chimeras suspended, "
                    f"phase_c={summary.get('phase_c', {}).get('updated', 0)} bare-fixed"
                ),
                detail={
                    "script": "cleanup_ocr_lemma_corruption_2026_05_15",
                    **summary,
                    "chimera_ids": CHIMERA_IDS,
                },
            )
            db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
