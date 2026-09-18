#!/usr/bin/env python3
"""Give a CAMeL frequency rank to every lemma stored without one.

From 2026-03-31 to 2026-09-18 `lemma_quality._CAMEL_CACHE` pointed at
backend/app/data instead of backend/data, so run_quality_gates() and the
discover API assigned no rank. Those lemmas read as unranked (rare) to
generation, session priority and leech reintroduction. This fills only NULL
ranks through the same `assign_frequency_rank()` the quality gates use; it
never changes an existing rank. Lemmas whose form is past the rank-map cap
stay NULL, which consumers already treat as rare.

Usage:
    cd backend && python3 scripts/backfill_missing_frequency_ranks.py --dry-run
    cd backend && python3 scripts/backfill_missing_frequency_ranks.py
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session

from app.models import Lemma, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.lemma_quality import _load_rank_map, assign_frequency_rank

ACTIVE_STATES = ("known", "learning", "lapsed", "acquiring")


def _band(rank: int | None) -> str:
    if rank is None:
        return "still_unranked"
    for limit, label in ((1000, "<=1k"), (2000, "1-2k"), (5000, "2-5k"), (20000, "5-20k")):
        if rank <= limit:
            return label
    return ">20k"


def backfill_missing_frequency_ranks(db: Session, dry_run: bool) -> dict:
    if not _load_rank_map():
        raise SystemExit("CAMeL frequency file missing; nothing to assign from.")

    active = {
        lemma_id
        for (lemma_id,) in db.query(UserLemmaKnowledge.lemma_id)
        .filter(UserLemmaKnowledge.knowledge_state.in_(ACTIVE_STATES))
        .all()
    }
    missing = db.query(Lemma).filter(Lemma.frequency_rank.is_(None)).all()
    bands: Counter = Counter()
    active_bands: Counter = Counter()
    for lemma in missing:
        rank = lemma.frequency_rank if assign_frequency_rank(lemma) else None
        bands[_band(rank)] += 1
        if lemma.lemma_id in active:
            active_bands[_band(rank)] += 1

    assigned = sum(n for band, n in bands.items() if band != "still_unranked")
    summary = {
        "lemmas_without_rank": len(missing),
        "assigned": assigned,
        "bands": dict(bands),
        "active_bands": dict(active_bands),
        "dry_run": dry_run,
    }
    if dry_run:
        db.rollback()
        return summary

    db.commit()
    log_activity(
        db,
        "frequency_rank_backfill",
        f"Assigned CAMeL frequency ranks to {assigned} of {len(missing)} unranked lemmas",
        summary,
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        summary = backfill_missing_frequency_ranks(db, args.dry_run)
    finally:
        db.close()
    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"{prefix}{summary['assigned']} of {summary['lemmas_without_rank']} unranked lemmas get a rank")
    print(f"  all:    {summary['bands']}")
    print(f"  active: {summary['active_bands']}")


if __name__ == "__main__":
    main()
