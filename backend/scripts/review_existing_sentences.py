"""Review all active sentences through the shared Haiku-tier quality gate.

Retires sentences flagged as unnatural or incorrectly translated. Provider or
parse failures leave rows untouched for a later retry.
Run: python3 scripts/review_existing_sentences.py [--dry-run] [--batch-size 10]
"""
import argparse
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Sentence
from app.services.llm import (
    review_sentences_quality,
    sentence_quality_review_input,
)
from app.services.activity_log import log_activity

BATCH_SIZE = 10


def _review_snapshot(sentence: Sentence) -> dict:
    """Capture every field that a quality verdict depends on or overwrites."""
    return {
        "arabic_text": sentence.arabic_text,
        "english_translation": sentence.english_translation,
        "source": sentence.source,
        "kind": sentence.kind,
        "is_active": bool(sentence.is_active),
        "quality_reviewed_at": sentence.quality_reviewed_at,
        "quality_natural": sentence.quality_natural,
        "quality_translation_correct": (
            sentence.quality_translation_correct
        ),
        "quality_reason": sentence.quality_reason,
    }


def _unchanged_snapshot_query(db, sentence_id: int, snapshot: dict):
    """Return a query matching exactly the row reviewed by the provider."""
    return db.query(Sentence).filter(
        Sentence.id == sentence_id,
        Sentence.arabic_text == snapshot["arabic_text"],
        Sentence.english_translation
        == snapshot["english_translation"],
        Sentence.source == snapshot["source"],
        Sentence.kind == snapshot["kind"],
        Sentence.is_active == snapshot["is_active"],
        Sentence.quality_reviewed_at
        == snapshot["quality_reviewed_at"],
        Sentence.quality_natural == snapshot["quality_natural"],
        Sentence.quality_translation_correct
        == snapshot["quality_translation_correct"],
        Sentence.quality_reason == snapshot["quality_reason"],
    )


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
        retry_ids = []
        reviewed = 0

        for i in range(0, len(sentences), args.batch_size):
            batch = sentences[i : i + args.batch_size]
            review_rows = []
            for sentence in batch:
                snapshot = _review_snapshot(sentence)
                if not snapshot["is_active"]:
                    retry_ids.append(sentence.id)
                    print(
                        f"  RETRY id={sentence.id}: sentence became inactive "
                        "before quality review"
                    )
                    continue
                review_rows.append((sentence.id, snapshot))

            to_review = [
                sentence_quality_review_input(
                    arabic=snapshot["arabic_text"],
                    english=snapshot["english_translation"] or "",
                    source=snapshot["source"],
                    kind=snapshot["kind"],
                )
                for _sentence_id, snapshot in review_rows
            ]

            # Release the read transaction before the external call. The
            # conditional write below then observes any concurrent mutation.
            db.commit()
            reviews = review_sentences_quality(to_review)

            for (sentence_id, snapshot), r in zip(review_rows, reviews):
                if not getattr(r, "review_completed", True):
                    retry_ids.append(sentence_id)
                    print(f"  RETRY id={sentence_id}: {r.reason}")
                    continue

                rejected = not r.natural or not r.translation_correct
                unchanged_query = _unchanged_snapshot_query(
                    db,
                    sentence_id,
                    snapshot,
                )
                if args.dry_run:
                    unchanged = unchanged_query.first() is not None
                else:
                    values = {
                        Sentence.quality_reviewed_at: datetime.now(
                            timezone.utc
                        ),
                        Sentence.quality_natural: bool(r.natural),
                        Sentence.quality_translation_correct: bool(
                            r.translation_correct
                        ),
                        Sentence.quality_reason: r.reason[:500],
                    }
                    if rejected:
                        values[Sentence.is_active] = False
                    unchanged = bool(
                        unchanged_query.update(
                            values,
                            synchronize_session=False,
                        )
                    )

                if not unchanged:
                    retry_ids.append(sentence_id)
                    print(
                        f"  RETRY id={sentence_id}: sentence changed during "
                        "quality review"
                    )
                    continue

                reviewed += 1
                if rejected:
                    failed_ids.append(sentence_id)
                    print(f"  FAIL id={sentence_id}: {r.reason}")
                    print(f"    ar: {snapshot['arabic_text']}")
                    print(f"    en: {snapshot['english_translation']}")
                    if not args.dry_run:
                        retired_ids.append(sentence_id)

            if not args.dry_run:
                db.commit()
            else:
                # Release any read snapshot opened by the unchanged checks.
                db.rollback()

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

        print(
            f"\nDone. Reviewed: {reviewed}, Failed: {len(failed_ids)}, "
            f"Retired: {len(retired_ids)}, Retry: {len(retry_ids)}"
        )
        if args.dry_run and failed_ids:
            print("(dry run — no changes made)")

    finally:
        db.close()


if __name__ == "__main__":
    main()
