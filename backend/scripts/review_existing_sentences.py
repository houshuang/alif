"""Review all active sentences for quality using Gemini Flash.

Retires sentences flagged as unnatural or incorrectly translated.
Run: python3 scripts/review_existing_sentences.py [--dry-run] [--batch-size 10]
"""
import argparse
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Sentence
from app.services.llm import review_sentences_quality
from app.services.activity_log import log_activity

BATCH_SIZE = 10


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Don't retire, just report")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--source", help="Only review sentences from this source, e.g. llm")
    parser.add_argument("--only-unreviewed", action="store_true", help="Skip rows with quality_reviewed_at")
    parser.add_argument("--limit", type=int, help="Maximum sentences to review")
    parser.add_argument("--ids", type=int, nargs="*", help="Specific sentence ids to review")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = db.query(Sentence).filter(Sentence.is_active == True)  # noqa: E712
        if args.ids:
            query = query.filter(Sentence.id.in_(args.ids))
        if args.source:
            query = query.filter(Sentence.source == args.source)
        if args.only_unreviewed:
            query = query.filter(Sentence.quality_reviewed_at.is_(None))
        query = query.order_by(Sentence.id)
        if args.limit:
            query = query.limit(args.limit)
        sentences = query.all()
        print(f"Reviewing {len(sentences)} active sentences in batches of {args.batch_size}...")

        retired_ids = []
        failed_ids = []
        reviewed = 0

        for i in range(0, len(sentences), args.batch_size):
            batch = sentences[i : i + args.batch_size]
            to_review = [
                {"arabic": s.arabic_text, "english": s.english_translation or ""}
                for s in batch
            ]

            reviews = review_sentences_quality(to_review)

            for s, r in zip(batch, reviews):
                reviewed += 1
                if not r.natural or not r.translation_correct:
                    failed_ids.append(s.id)
                    print(f"  FAIL id={s.id}: {r.reason}")
                    print(f"    ar: {s.arabic_text}")
                    print(f"    en: {s.english_translation}")
                if not args.dry_run:
                    s.quality_reviewed_at = datetime.now(timezone.utc)
                    s.quality_natural = bool(r.natural)
                    s.quality_translation_correct = bool(r.translation_correct)
                    s.quality_reason = r.reason[:500]
                    if not r.natural or not r.translation_correct:
                        s.is_active = False
                        retired_ids.append(s.id)

            if not args.dry_run:
                db.commit()

            done = min(i + args.batch_size, len(sentences))
            action_count = len(failed_ids) if args.dry_run else len(retired_ids)
            action_label = "would retire" if args.dry_run else "retired"
            print(f"  [{done}/{len(sentences)}] reviewed, {action_count} {action_label} so far")

        if not args.dry_run and retired_ids:
            db.commit()
            log_activity(
                db,
                event_type="sentences_retired",
                summary=f"Quality review retired {len(retired_ids)} sentences",
                detail={"retired_ids": retired_ids, "total_reviewed": reviewed},
            )

        print(f"\nDone. Reviewed: {reviewed}, Failed: {len(failed_ids)}, Retired: {len(retired_ids)}")
        if args.dry_run and failed_ids:
            print("(dry run — no changes made)")

    finally:
        db.close()


if __name__ == "__main__":
    main()
