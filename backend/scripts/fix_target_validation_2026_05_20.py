"""One-shot data repair for the 2026-05-20 target-validation stuck-lemma audit.

After the pipeline-watchdog audit surfaced three lemmas burning 461 validation
failures across the week with zero acceptances:

  - id=3277 طَاغٌ (tyrant): bare="طاغي" was correct; Phase 2 of warm_sentence_cache
    was passing strip_diacritics(lemma_ar)="طاغ" to the validator instead of the
    stored bare. Fixed in code at material_generator.py / sentence_generator.py.
    No DB change needed; just clear backoff so the next cron retries.
  - id=575 ذَرَا (to scatter): final-weak verb, LLM produces ذَرَى (alef-maksura
    variant). Fixed by adding word-final ا ↔ ى swap to validator target_form_map.
    Mirrors build_lemma_lookup Pass 1b. No DB change needed; just clear backoff.
  - id=895 جَانٍ (guilty, perpetrator): defective active participle. Bare was
    stored as "جان" without the explicit ya, but every surface form the LLM
    produces carries the ya (الجاني, جانياً, جانية). The "implicit ya" stored
    form is the dictionary convention, not what appears in real Arabic prose.
    Repair: rewrite lemma_ar_bare="جان" → "جاني" and lemma_ar="جَانٍ" → "جَانِي"
    so the validator's existing ال-prefix + tanwin-alif handling matches.

Dry-run by default; pass --apply to commit.

    python3 scripts/fix_target_validation_2026_05_20.py            # dry-run
    python3 scripts/fix_target_validation_2026_05_20.py --apply    # commit
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge
from app.services.activity_log import log_activity


# (lemma_id, expected_old_bare, new_lemma_ar, new_lemma_ar_bare, note)
JAN_REPAIR = (895, "جان", "جَانِي", "جاني",
               "Defective active participle — bare stored without explicit ya")

# Lemmas whose backoff state we want to clear so the next cron retries.
LEMMA_IDS_TO_UNBLOCK = [895, 575, 3277]


def repair_jaani(db, apply: bool) -> bool:
    """Repair the جان → جاني bare-form mismatch.

    Returns True if a change was applied (or would be applied in dry-run).
    """
    lid, expected_old_bare, new_ar, new_bare, _note = JAN_REPAIR
    lem = db.query(Lemma).filter(Lemma.lemma_id == lid).first()
    if not lem:
        print(f"  [{lid}] not found — skipping")
        return False
    if lem.lemma_ar_bare == new_bare:
        print(f"  [{lid}] already repaired (bare={new_bare!r}) — skipping")
        return False
    if lem.lemma_ar_bare != expected_old_bare:
        print(
            f"  [{lid}] expected bare={expected_old_bare!r}, "
            f"got {lem.lemma_ar_bare!r} — NOT applying (manual review needed)"
        )
        return False
    # Check for collision before rewriting
    collision = (
        db.query(Lemma)
        .filter(
            Lemma.lemma_ar_bare == new_bare,
            Lemma.lemma_id != lid,
            Lemma.canonical_lemma_id.is_(None),
        )
        .first()
    )
    if collision:
        print(
            f"  [{lid}] collision: lemma {collision.lemma_id} already holds "
            f"bare={new_bare!r} — NOT applying (consider merging)"
        )
        return False

    old_ar = lem.lemma_ar
    old_bare = lem.lemma_ar_bare
    print(f"  [{lid}] {old_ar!r}/{old_bare!r} → {new_ar!r}/{new_bare!r}")
    if apply:
        lem.lemma_ar = new_ar
        lem.lemma_ar_bare = new_bare
    return True


def clear_generation_backoff(db, lemma_ids: list[int], apply: bool) -> int:
    """Reset generation_failed_count and generation_backoff_until.

    The stale backoff from before the fixes would otherwise keep these lemmas
    out of generation for up to 7 days.
    """
    rows = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id.in_(lemma_ids))
        .all()
    )
    cleared = 0
    for ulk in rows:
        if (ulk.generation_failed_count or 0) == 0 and ulk.generation_backoff_until is None:
            continue
        print(
            f"  ulk[{ulk.lemma_id}] gen_failed={ulk.generation_failed_count} "
            f"backoff_until={ulk.generation_backoff_until} → cleared"
        )
        if apply:
            ulk.generation_failed_count = 0
            ulk.generation_backoff_until = None
        cleared += 1
    return cleared


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Commit changes")
    args = ap.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== fix_target_validation_2026_05_20 ({mode}) ===\n")

    db = SessionLocal()
    try:
        print("[1] جان bare-form repair (id=895)")
        changed = repair_jaani(db, args.apply)

        print(f"\n[2] Clear generation backoff for {LEMMA_IDS_TO_UNBLOCK}")
        cleared = clear_generation_backoff(db, LEMMA_IDS_TO_UNBLOCK, args.apply)
        print(f"  cleared {cleared} ULK rows")

        if args.apply and (changed or cleared):
            db.commit()
            log_activity(
                db,
                event_type="manual_action",
                summary=(
                    "Target-validation repair: جان bare→جاني; "
                    f"cleared backoff on {LEMMA_IDS_TO_UNBLOCK}"
                ),
                detail={
                    "jan_repaired": changed,
                    "backoff_cleared": cleared,
                    "lemma_ids_unblocked": LEMMA_IDS_TO_UNBLOCK,
                },
            )
            print("\nCommitted + logged to ActivityLog.")
        elif not args.apply:
            print("\n(dry-run — pass --apply to commit)")
        else:
            print("\nNo changes to commit.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
