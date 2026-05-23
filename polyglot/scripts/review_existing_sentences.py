"""Review existing active sentences for naturalness and translation quality.

Defaults to LLM-generated rows because that is where bulk generation can create
fluent-looking nonsense. Failed rows are marked inactive so the review picker
cannot surface them.

Usage:

    .venv/bin/python scripts/review_existing_sentences.py --language el --only-unreviewed
    .venv/bin/python scripts/review_existing_sentences.py --language el --dry-run --limit 50
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models import Lemma, Sentence
from app.services.activity_log import log_activity
from app.services.material_generator import review_sentences_quality


BATCH_SIZE = 10
REVIEWER_FAILURE_REASONS = {
    "quality review unavailable",
    "quality review parse error",
    "quality review incomplete",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review active polyglot sentences for quality.")
    parser.add_argument("--language", default="el", help="Language code (el/grc/la). Default: el")
    parser.add_argument("--dry-run", action="store_true", help="Report failures without writing changes")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--source", default="llm", help="Sentence source to review. Default: llm")
    parser.add_argument("--all-sources", action="store_true", help="Ignore --source and review all sources")
    parser.add_argument("--only-unreviewed", action="store_true",
                        help="Skip rows that already have quality_reviewed_at")
    parser.add_argument("--limit", type=int, help="Maximum sentences to review")
    parser.add_argument("--ids", type=int, nargs="*", help="Specific sentence ids to review")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    db = SessionLocal()
    try:
        query = db.query(Sentence).filter(
            Sentence.language_code == args.language,
            Sentence.is_active.is_(True),
        )
        if args.ids:
            query = query.filter(Sentence.id.in_(args.ids))
        if args.source and not args.all_sources:
            query = query.filter(Sentence.source == args.source)
        if args.only_unreviewed:
            query = query.filter(Sentence.quality_reviewed_at.is_(None))
        query = query.order_by(Sentence.id)
        if args.limit:
            query = query.limit(args.limit)

        sentences = query.all()
        source_label = "all sources" if args.all_sources else f"source={args.source}"
        print(
            f"Reviewing {len(sentences)} active {args.language} sentences "
            f"({source_label}) in batches of {args.batch_size}..."
        )

        retired_ids: list[int] = []
        failed_ids: list[int] = []
        reviewed = 0

        for i in range(0, len(sentences), max(1, args.batch_size)):
            batch = sentences[i:i + max(1, args.batch_size)]
            target_ids = {s.target_lemma_id for s in batch if s.target_lemma_id is not None}
            lemmas_by_id = {
                lemma.lemma_id: lemma
                for lemma in db.query(Lemma).filter(Lemma.lemma_id.in_(target_ids)).all()
            } if target_ids else {}

            reviews = review_sentences_quality(
                args.language,
                [
                    {
                        "text": s.text,
                        "english": s.translation_en or "",
                        "target": (
                            lemmas_by_id[s.target_lemma_id].lemma_form
                            if s.target_lemma_id in lemmas_by_id else ""
                        ),
                        "target_gloss": (
                            lemmas_by_id[s.target_lemma_id].gloss_en or ""
                            if s.target_lemma_id in lemmas_by_id else ""
                        ),
                    }
                    for s in batch
                ],
            )

            batch_reasons = {r.reason for r in reviews}
            if batch_reasons and batch_reasons <= REVIEWER_FAILURE_REASONS:
                print(
                    "Reviewer failed for the whole batch; aborting without "
                    f"retiring rows. Reason: {next(iter(batch_reasons))}"
                )
                db.rollback()
                return 2

            now = datetime.now(timezone.utc)
            for s, r in zip(batch, reviews):
                reviewed += 1
                failed = not (r.natural and r.translation_correct)
                if failed:
                    failed_ids.append(s.id)
                    print(f"  FAIL id={s.id}: {r.reason}")
                    print(f"    src: {s.text}")
                    print(f"    eng: {s.translation_en or ''}")

                if not args.dry_run:
                    s.quality_reviewed_at = now
                    s.quality_natural = bool(r.natural)
                    s.quality_translation_correct = bool(r.translation_correct)
                    s.quality_reason = r.reason[:500]
                    if failed:
                        s.is_active = False
                        retired_ids.append(s.id)

            if not args.dry_run:
                db.commit()

            done = min(i + max(1, args.batch_size), len(sentences))
            action_count = len(failed_ids) if args.dry_run else len(retired_ids)
            action_label = "would retire" if args.dry_run else "retired"
            print(f"  [{done}/{len(sentences)}] reviewed, {action_count} {action_label} so far")

        if not args.dry_run and retired_ids:
            log_activity(
                db,
                event_type="sentences_retired",
                language_code=args.language,
                summary=f"Quality review retired {len(retired_ids)} sentences",
                detail={"retired_ids": retired_ids, "total_reviewed": reviewed},
            )

        print(f"\nDone. Reviewed: {reviewed}, Failed: {len(failed_ids)}, Retired: {len(retired_ids)}")
        if args.dry_run and failed_ids:
            print("(dry run: no changes made)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
