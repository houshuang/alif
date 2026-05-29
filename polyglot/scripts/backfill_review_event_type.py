"""Backfill ReviewLog.event_type and graduation_tier for historical rows.

Both columns are new (2026-05-29). Going forward they are written at review time;
this script reconstructs them for rows logged before the change so analytics see a
complete history. Idempotent: only touches rows where event_type IS NULL.

event_type:
  - is_acquisition=1                                  -> "acquisition_review"
  - fsrs_log_json.scaffold_confirmation is True       -> "scaffold_confirmation"
  - otherwise                                         -> "fsrs_review"

graduation_tier (acquisition rows that graduated only; NULL otherwise):
  reconstructed from the pre-state stored in fsrs_log_json using the same tier
  precedence as acquisition_service.submit_acquisition_review. Tier 0 (first-correct)
  is exact; the box check that distinguishes Tier 2 from Tier 3 is approximated from
  pre-state, so a small number of pre-change graduations may be off by one tier.
  Going forward the value is exact (captured at the graduating branch).

Usage:
    python3 scripts/backfill_review_event_type.py [--apply]
Without --apply it prints a dry-run summary.
"""
import argparse

from app.database import SessionLocal, ensure_schema
from app.models import ReviewLog


def classify_event_type(row: ReviewLog) -> str:
    if row.is_acquisition:
        return "acquisition_review"
    j = row.fsrs_log_json or {}
    if isinstance(j, dict) and j.get("scaffold_confirmation"):
        return "scaffold_confirmation"
    return "fsrs_review"


def reconstruct_tier(row: ReviewLog) -> int | None:
    j = row.fsrs_log_json or {}
    if not isinstance(j, dict) or not j.get("graduated"):
        return None
    pre_seen = j.get("pre_times_seen") or 0
    pre_correct = j.get("pre_times_correct") or 0
    if pre_seen == 0:
        return 0  # Tier 0: first-correct instant graduation (exact)
    new_seen = pre_seen + 1
    new_correct = pre_correct + 1  # graduation requires a correct (rating>=3) review
    accuracy = new_correct / new_seen if new_seen else 0.0
    box_after = j.get("acquisition_box_after")
    box_before = j.get("acquisition_box_before") or 1
    box = box_after if box_after is not None else box_before
    if accuracy >= 1.0 and new_seen >= 3:
        return 1
    if accuracy >= 0.80 and new_seen >= 4 and (box or 1) >= 2:
        return 2
    return 3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (else dry run)")
    args = ap.parse_args()

    ensure_schema()
    db = SessionLocal()
    try:
        rows = db.query(ReviewLog).filter(ReviewLog.event_type.is_(None)).all()
        counts: dict[str, int] = {}
        tiers: dict[int, int] = {}
        for row in rows:
            et = classify_event_type(row)
            counts[et] = counts.get(et, 0) + 1
            tier = reconstruct_tier(row)
            if tier is not None:
                tiers[tier] = tiers.get(tier, 0) + 1
            if args.apply:
                row.event_type = et
                row.graduation_tier = tier
        if args.apply:
            db.commit()
        print(f"{'APPLIED' if args.apply else 'DRY RUN'}: {len(rows)} rows with NULL event_type")
        for et, n in sorted(counts.items()):
            print(f"  event_type={et}: {n}")
        if tiers:
            print("  graduation_tier reconstructed:")
            for t in sorted(tiers):
                print(f"    tier {t}: {tiers[t]}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
