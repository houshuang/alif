"""Identify leech words: high review count, low accuracy.

Leeches consume FSRS review time without progressing. Shows them sorted
by wasted reviews (times_seen - times_correct).

Usage:
    python scripts/identify_leeches.py
    python scripts/identify_leeches.py --threshold 0.2 --min-reviews 8
    python scripts/identify_leeches.py --source textbook_scan
    python scripts/identify_leeches.py --suspend --dry-run
    python scripts/identify_leeches.py --suspend --threshold 0.2 --min-reviews 8
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge


def main():
    parser = argparse.ArgumentParser(description="Identify leech words")
    parser.add_argument("--suspend", action="store_true", help="Suspend identified leeches")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be suspended")
    parser.add_argument("--threshold", type=float, default=0.3, help="Accuracy threshold (default 0.3)")
    parser.add_argument("--min-reviews", type=int, default=5, help="Minimum reviews to consider (default 5)")
    parser.add_argument("--source", type=str, default=None, help="Filter by source (e.g. textbook_scan)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = (
            db.query(UserLemmaKnowledge, Lemma)
            .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
            .filter(UserLemmaKnowledge.knowledge_state != "suspended")
            .filter(UserLemmaKnowledge.times_seen >= args.min_reviews)
        )

        if args.source:
            query = query.filter(UserLemmaKnowledge.source == args.source)

        leeches = []
        for ulk, lemma in query.all():
            seen = ulk.times_seen or 0
            correct = ulk.times_correct or 0
            if seen == 0:
                continue
            accuracy = correct / seen
            if accuracy < args.threshold:
                wasted = seen - correct
                leeches.append((ulk, lemma, accuracy, wasted))

        leeches.sort(key=lambda x: -x[3])

        print(
            f"=== LEECH WORDS (accuracy < {args.threshold * 100:.0f}%, "
            f"min {args.min_reviews} reviews) ===\n"
        )
        print(
            f"{'Arabic':<15} {'English':<20} {'Seen':>5} {'Correct':>8} "
            f"{'Acc%':>5} {'Wasted':>7} {'Source':<15} {'State':<10} {'Freq':>6}"
        )
        print("-" * 110)

        for ulk, lemma, accuracy, wasted in leeches:
            freq = lemma.frequency_rank or 0
            print(
                f"{lemma.lemma_ar_bare:<15} {(lemma.gloss_en or '')[:20]:<20} "
                f"{ulk.times_seen:>5} {ulk.times_correct:>8} "
                f"{accuracy * 100:>4.0f}% {wasted:>7} "
                f"{(ulk.source or ''):<15} {ulk.knowledge_state:<10} "
                f"{freq:>6}"
            )

        print(f"\nTotal leeches: {len(leeches)}")
        total_wasted = sum(x[3] for x in leeches)
        print(f"Total wasted reviews: {total_wasted}")

        by_source: dict[str, list[int]] = {}
        for ulk, lemma, accuracy, wasted in leeches:
            src = ulk.source or "unknown"
            by_source.setdefault(src, []).append(wasted)
        if by_source:
            print("\nBy source:")
            for src, wasted_list in sorted(by_source.items(), key=lambda x: -sum(x[1])):
                print(f"  {src}: {len(wasted_list)} words, {sum(wasted_list)} wasted reviews")

        # Variant breakdown
        variant_count = sum(1 for _, l, _, _ in leeches if l.canonical_lemma_id is not None)
        if variant_count:
            print(f"\nVariants (canonical_lemma_id set): {variant_count}/{len(leeches)}")

        if (args.suspend or args.dry_run) and leeches:
            action = "Would suspend" if args.dry_run else "Suspending"
            print(f"\n{action} {len(leeches)} leeches...")
            if not args.dry_run:
                for ulk, lemma, _, _ in leeches:
                    ulk.knowledge_state = "suspended"
                db.commit()
                print("Done.")
            else:
                print("(dry run, no changes)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
