#!/usr/bin/env python3
"""Fix inflated FSRS cards from OCR textbook scanning.

Textbook scans auto-create ULK records with FSRS cards and "understood"
reviews for every word on the page. This inflates stability for words the
user may never have actually studied. This script resets those cards based
on how many real (non-textbook_scan) reviews exist.

Actions per word:
  0 real reviews         → full reset to encountered (delete all ReviewLog)
  1-2 real, <50% acc     → full reset to encountered (delete all ReviewLog)
  3+ real reviews        → delete textbook_scan reviews, replay real ones through FSRS

Usage:
    python scripts/reset_ocr_cards.py --dry-run     # preview changes
    python scripts/reset_ocr_cards.py               # apply changes
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from datetime import datetime, timezone

from fsrs import Card, Scheduler, Rating, State
from sqlalchemy import func

from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge, ReviewLog
from app.services.activity_log import log_activity
from app.services.fsrs_service import RATING_MAP, STATE_MAP


def main():
    parser = argparse.ArgumentParser(description="Reset inflated FSRS cards from OCR textbook scanning")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, don't modify the database")
    args = parser.parse_args()

    db = SessionLocal()
    scheduler = Scheduler()

    try:
        # Find all ULK records from textbook scanning
        ocr_ulks = (
            db.query(UserLemmaKnowledge, Lemma)
            .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
            .filter(UserLemmaKnowledge.source == "textbook_scan")
            .all()
        )

        if not ocr_ulks:
            print("No textbook_scan ULK records found.")
            return

        print(f"Found {len(ocr_ulks)} words with source=textbook_scan\n")

        stats = {
            "total": len(ocr_ulks),
            "full_reset_no_reviews": 0,
            "full_reset_low_accuracy": 0,
            "replayed": 0,
            "textbook_reviews_deleted": 0,
            "all_reviews_deleted": 0,
        }

        full_resets: list[tuple[Lemma, str]] = []
        replays: list[tuple[Lemma, int, int]] = []  # (lemma, real_count, textbook_count)

        for ulk, lemma in ocr_ulks:
            # Count reviews by type
            all_reviews = (
                db.query(ReviewLog)
                .filter(ReviewLog.lemma_id == lemma.lemma_id)
                .order_by(ReviewLog.reviewed_at.asc())
                .all()
            )

            textbook_reviews = [r for r in all_reviews if r.review_mode == "textbook_scan"]
            real_reviews = [r for r in all_reviews if r.review_mode != "textbook_scan"]

            real_count = len(real_reviews)
            textbook_count = len(textbook_reviews)

            if real_count == 0:
                # No real reviews — full reset
                full_resets.append((lemma, "no real reviews"))
                stats["full_reset_no_reviews"] += 1
                stats["all_reviews_deleted"] += len(all_reviews)

                if not args.dry_run:
                    for r in all_reviews:
                        db.delete(r)
                    ulk.knowledge_state = "encountered"
                    ulk.fsrs_card_json = None
                    ulk.times_seen = 0
                    ulk.times_correct = 0
                    ulk.introduced_at = None
                    ulk.last_reviewed = None

            elif real_count <= 2:
                # Check accuracy of real reviews
                real_correct = sum(1 for r in real_reviews if r.rating >= 3)
                accuracy = real_correct / real_count if real_count > 0 else 0

                if accuracy < 0.5:
                    full_resets.append((lemma, f"{real_count} reviews, {accuracy:.0%} accuracy"))
                    stats["full_reset_low_accuracy"] += 1
                    stats["all_reviews_deleted"] += len(all_reviews)

                    if not args.dry_run:
                        for r in all_reviews:
                            db.delete(r)
                        ulk.knowledge_state = "encountered"
                        ulk.fsrs_card_json = None
                        ulk.times_seen = 0
                        ulk.times_correct = 0
                        ulk.introduced_at = None
                        ulk.last_reviewed = None
                else:
                    # Low review count but decent accuracy — replay
                    replays.append((lemma, real_count, textbook_count))
                    stats["replayed"] += 1
                    stats["textbook_reviews_deleted"] += textbook_count

                    if not args.dry_run:
                        _replay_reviews(db, scheduler, ulk, all_reviews, real_reviews)

            else:
                # 3+ real reviews — replay
                replays.append((lemma, real_count, textbook_count))
                stats["replayed"] += 1
                stats["textbook_reviews_deleted"] += textbook_count

                if not args.dry_run:
                    _replay_reviews(db, scheduler, ulk, all_reviews, real_reviews)

        if not args.dry_run:
            db.commit()

        # Print report
        prefix = "DRY RUN — " if args.dry_run else ""
        print(f"\n{prefix}OCR Card Reset Report")
        print("=" * 60)
        print(f"Total textbook_scan words:       {stats['total']}")
        print(f"Full reset (no real reviews):     {stats['full_reset_no_reviews']}")
        print(f"Full reset (low accuracy):        {stats['full_reset_low_accuracy']}")
        print(f"Replayed with real reviews:       {stats['replayed']}")
        print(f"Total reviews deleted:            {stats['all_reviews_deleted'] + stats['textbook_reviews_deleted']}")
        print(f"  - All reviews (full reset):     {stats['all_reviews_deleted']}")
        print(f"  - Textbook-only (replay):       {stats['textbook_reviews_deleted']}")

        if full_resets:
            print(f"\nFull resets ({len(full_resets)}):")
            for lemma, reason in full_resets[:30]:
                print(f"  {lemma.lemma_ar_bare:<15} {(lemma.gloss_en or '')[:25]:<25} ({reason})")
            if len(full_resets) > 30:
                print(f"  ... and {len(full_resets) - 30} more")

        if replays:
            print(f"\nReplayed ({len(replays)}):")
            for lemma, real_count, textbook_count in replays[:30]:
                print(
                    f"  {lemma.lemma_ar_bare:<15} {(lemma.gloss_en or '')[:25]:<25} "
                    f"({real_count} real, {textbook_count} textbook deleted)"
                )
            if len(replays) > 30:
                print(f"  ... and {len(replays) - 30} more")

        if not args.dry_run and (full_resets or replays):
            log_activity(
                db,
                event_type="ocr_cards_reset",
                summary=(
                    f"Reset {stats['full_reset_no_reviews'] + stats['full_reset_low_accuracy']} OCR cards, "
                    f"replayed {stats['replayed']} with real reviews"
                ),
                detail=stats,
            )
            print(f"\nChanges applied and logged to ActivityLog.")
        elif args.dry_run:
            print(f"\nDry run complete. Use without --dry-run to apply.")

    finally:
        db.close()


def _replay_reviews(
    db,
    scheduler: Scheduler,
    ulk: UserLemmaKnowledge,
    all_reviews: list[ReviewLog],
    real_reviews: list[ReviewLog],
):
    """Delete all reviews, reset ULK, replay real reviews through FSRS."""
    # Delete all review log entries
    for r in all_reviews:
        db.delete(r)

    # Reset ULK to fresh state
    ulk.fsrs_card_json = None
    ulk.times_seen = 0
    ulk.times_correct = 0
    ulk.knowledge_state = "encountered"
    ulk.introduced_at = None
    ulk.last_reviewed = None

    # Replay real reviews through FSRS
    card = Card()
    times_seen = 0
    times_correct = 0
    last_reviewed = None

    for review in real_reviews:
        rating = RATING_MAP[review.rating]
        reviewed_at = review.reviewed_at
        if reviewed_at and reviewed_at.tzinfo is None:
            from datetime import timezone
            reviewed_at = reviewed_at.replace(tzinfo=timezone.utc)
        card, _ = scheduler.review_card(card, rating, reviewed_at)
        times_seen += 1
        if review.rating >= 3:
            times_correct += 1
        last_reviewed = review.reviewed_at

    # Determine state from final card
    new_state = STATE_MAP.get(card.state, "learning")
    card_dict = card.to_dict()
    stability = card_dict.get("stability", 0)
    if new_state == "known" and stability < 1.0:
        new_state = "lapsed"

    ulk.fsrs_card_json = card_dict
    ulk.knowledge_state = new_state
    ulk.times_seen = times_seen
    ulk.times_correct = times_correct
    ulk.last_reviewed = last_reviewed
    ulk.introduced_at = real_reviews[0].reviewed_at if real_reviews else None

    # Re-create ReviewLog entries for real reviews
    for review in real_reviews:
        new_log = ReviewLog(
            lemma_id=ulk.lemma_id,
            rating=review.rating,
            reviewed_at=review.reviewed_at,
            response_ms=review.response_ms,
            context=review.context,
            session_id=review.session_id,
            review_mode=review.review_mode,
            comprehension_signal=review.comprehension_signal,
            sentence_id=review.sentence_id,
            credit_type=review.credit_type,
            is_acquisition=review.is_acquisition,
        )
        db.add(new_log)


if __name__ == "__main__":
    main()
