#!/usr/bin/env python3
"""Reset word knowledge to a clean learning baseline.

Keeps FSRS cards only for words with genuine learning signal:
  times_seen >= 5 AND accuracy >= 60%

Everything else (acquiring, low-signal learning/known/lapsed/new) is
reset to "encountered" state with no FSRS card or acquisition data.
Review history is preserved for analysis.

Usage:
    python scripts/reset_to_learning_baseline.py --dry-run
    python scripts/reset_to_learning_baseline.py
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from datetime import datetime, timezone

from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.fsrs_service import parse_json_column

MIN_REVIEWS = 5
MIN_ACCURACY = 0.60


def _is_genuinely_known(ulk: UserLemmaKnowledge) -> bool:
    ts = ulk.times_seen or 0
    tc = ulk.times_correct or 0
    if ts < MIN_REVIEWS:
        return False
    acc = tc / ts
    return acc >= MIN_ACCURACY


def main():
    parser = argparse.ArgumentParser(description="Reset to learning baseline")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    args = parser.parse_args()

    db = SessionLocal()

    try:
        all_ulks = (
            db.query(UserLemmaKnowledge, Lemma)
            .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
            .all()
        )

        stats = {
            "total": len(all_ulks),
            "kept_fsrs": 0,
            "reset_to_encountered": 0,
            "already_encountered": 0,
            "suspended": 0,
        }

        kept: list[tuple] = []
        reset: list[tuple] = []

        for ulk, lemma in all_ulks:
            if ulk.knowledge_state == "encountered":
                stats["already_encountered"] += 1
                continue
            if ulk.knowledge_state == "suspended":
                stats["suspended"] += 1
                continue

            if _is_genuinely_known(ulk):
                kept.append((ulk, lemma))
                stats["kept_fsrs"] += 1
            else:
                reset.append((ulk, lemma))
                stats["reset_to_encountered"] += 1

                if not args.dry_run:
                    ulk.knowledge_state = "encountered"
                    ulk.fsrs_card_json = None
                    ulk.acquisition_box = None
                    ulk.acquisition_next_due = None
                    ulk.acquisition_started_at = None
                    ulk.introduced_at = None
                    ulk.graduated_at = None
                    # Keep times_seen/times_correct/total_encounters for history

        if not args.dry_run:
            db.commit()

        prefix = "DRY RUN — " if args.dry_run else ""
        print(f"\n{prefix}Learning Baseline Reset Report")
        print("=" * 60)
        print(f"Total ULK records:               {stats['total']}")
        print(f"Already encountered:             {stats['already_encountered']}")
        print(f"Suspended (unchanged):           {stats['suspended']}")
        print(f"Kept as FSRS (genuinely known):  {stats['kept_fsrs']}")
        print(f"Reset to encountered:            {stats['reset_to_encountered']}")

        if kept:
            print(f"\nKept ({len(kept)}):")
            for ulk, lemma in kept[:50]:
                ts = ulk.times_seen or 0
                tc = ulk.times_correct or 0
                acc = tc / ts if ts > 0 else 0
                stab = ""
                if ulk.fsrs_card_json:
                    card = parse_json_column(ulk.fsrs_card_json)
                    if card:
                        stab = f"stab={card.get('stability', 0):.1f}d"
                print(
                    f"  {lemma.lemma_ar_bare:<15} {(lemma.gloss_en or '')[:25]:<25} "
                    f"ts={ts} acc={acc:.0%} {stab} [{ulk.knowledge_state}]"
                )
            if len(kept) > 50:
                print(f"  ... and {len(kept) - 50} more")

        if reset:
            print(f"\nReset ({len(reset)}):")
            for ulk, lemma in reset[:50]:
                ts = ulk.times_seen or 0
                tc = ulk.times_correct or 0
                acc = tc / ts if ts > 0 else 0
                print(
                    f"  {lemma.lemma_ar_bare:<15} {(lemma.gloss_en or '')[:25]:<25} "
                    f"ts={ts} acc={acc:.0%} was={ulk.knowledge_state}"
                )
            if len(reset) > 50:
                print(f"  ... and {len(reset) - 50} more")

        if not args.dry_run and reset:
            log_activity(
                db,
                event_type="learning_baseline_reset",
                summary=f"Reset {stats['reset_to_encountered']} words to encountered, kept {stats['kept_fsrs']} FSRS words",
                detail=stats,
            )
            print(f"\nChanges applied and logged to ActivityLog.")
        elif args.dry_run:
            print(f"\nDry run complete. Use without --dry-run to apply.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
