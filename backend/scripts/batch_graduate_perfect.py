"""One-time script: graduate all acquiring words with perfect accuracy and 3+ reviews.

Production data (2026-03-03) shows 41 words stuck in acquisition with 100% accuracy.
Fast graduates (≤6 reviews) have 0% lapse rate — these words clearly don't need more drilling.

Usage:
    python3 scripts/batch_graduate_perfect.py [--dry-run]
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge
from app.services.acquisition_service import _graduate


def main():
    parser = argparse.ArgumentParser(description="Batch graduate perfect-accuracy acquiring words")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without making changes")
    parser.add_argument("--min-reviews", type=int, default=3, help="Minimum reviews required (default: 3)")
    parser.add_argument("--min-accuracy", type=float, default=1.0, help="Minimum accuracy (default: 1.0 = perfect)")
    args = parser.parse_args()

    db = SessionLocal()
    now = datetime.now(timezone.utc)

    try:
        acquiring = (
            db.query(UserLemmaKnowledge)
            .filter(
                UserLemmaKnowledge.knowledge_state == "acquiring",
                UserLemmaKnowledge.times_seen >= args.min_reviews,
            )
            .all()
        )

        candidates = []
        for ulk in acquiring:
            if ulk.times_seen == 0:
                continue
            accuracy = ulk.times_correct / ulk.times_seen
            if accuracy >= args.min_accuracy:
                lemma = db.query(Lemma).filter(Lemma.lemma_id == ulk.lemma_id).first()
                candidates.append((ulk, lemma, accuracy))

        print(f"Found {len(candidates)} acquiring words with ≥{args.min_accuracy*100:.0f}% accuracy and ≥{args.min_reviews} reviews")
        print()

        for ulk, lemma, accuracy in candidates:
            ar = lemma.lemma_ar if lemma else "?"
            en = lemma.gloss_en if lemma else "?"
            box = ulk.acquisition_box or "?"
            print(f"  {ar:>15}  ({en:<30})  box={box}  seen={ulk.times_seen}  correct={ulk.times_correct}  acc={accuracy:.0%}")

        if args.dry_run:
            print(f"\n[DRY RUN] Would graduate {len(candidates)} words. Run without --dry-run to apply.")
            return

        graduated_count = 0
        for ulk, lemma, accuracy in candidates:
            _graduate(ulk, now, db=db)
            graduated_count += 1

        db.commit()
        print(f"\nGraduated {graduated_count} words to FSRS.")

        # Log to ActivityLog
        try:
            from app.services.activity_log import log_activity
            log_activity(
                db,
                event_type="manual_action",
                summary=f"Batch graduated {graduated_count} perfect-accuracy words from acquisition",
                detail={
                    "graduated_count": graduated_count,
                    "min_reviews": args.min_reviews,
                    "min_accuracy": args.min_accuracy,
                    "lemma_ids": [ulk.lemma_id for ulk, _, _ in candidates],
                },
            )
            db.commit()
        except Exception as e:
            print(f"Warning: failed to log activity: {e}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
