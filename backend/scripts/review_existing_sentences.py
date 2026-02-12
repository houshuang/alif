"""Review all active sentences for quality using Gemini Flash.

Retires sentences flagged as unnatural or incorrectly translated.
Run: python3 scripts/review_existing_sentences.py [--dry-run] [--batch-size 10]
"""
import argparse
import sys
import os
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
    args = parser.parse_args()

    db = SessionLocal()
    try:
        sentences = (
            db.query(Sentence)
            .filter(Sentence.is_active == True)  # noqa: E712
            .order_by(Sentence.id)
            .all()
        )
        print(f"Reviewing {len(sentences)} active sentences in batches of {args.batch_size}...")

        retired_ids = []
        reviewed = 0

        for i in range(0, len(sentences), args.batch_size):
            batch = sentences[i : i + args.batch_size]
            to_review = [
                {"arabic": s.arabic_diacritized or s.arabic_text, "english": s.english_translation or ""}
                for s in batch
            ]

            reviews = review_sentences_quality(to_review)

            for s, r in zip(batch, reviews):
                reviewed += 1
                if not r.natural or not r.translation_correct:
                    print(f"  FAIL id={s.id}: {r.reason}")
                    print(f"    ar: {s.arabic_diacritized}")
                    print(f"    en: {s.english_translation}")
                    if not args.dry_run:
                        s.is_active = False
                        retired_ids.append(s.id)

            if not args.dry_run and retired_ids:
                db.commit()

            done = min(i + args.batch_size, len(sentences))
            print(f"  [{done}/{len(sentences)}] reviewed, {len(retired_ids)} retired so far")

        if not args.dry_run and retired_ids:
            db.commit()
            log_activity(
                db,
                event_type="sentences_retired",
                summary=f"Quality review retired {len(retired_ids)} sentences",
                detail={"retired_ids": retired_ids, "total_reviewed": reviewed},
            )

        print(f"\nDone. Reviewed: {reviewed}, Retired: {len(retired_ids)}")
        if args.dry_run and retired_ids:
            print("(dry run — no changes made)")

    finally:
        db.close()


if __name__ == "__main__":
    main()
