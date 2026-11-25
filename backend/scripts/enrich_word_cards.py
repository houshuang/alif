#!/usr/bin/env python3
"""Fill ``Lemma.word_card_json`` lazily, most useful words first.

Derivation chain, form glosses and set phrases for the review word panel; see
``app/services/word_card.py``. Run by the material cron with a small limit, so
the backlog fills over days and the panel falls back to its plain content until
a word has a card.

Usage:
  cd backend
  PYTHONPATH=/opt/limbic python3 scripts/enrich_word_cards.py --limit 40 [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from app.services.activity_log import log_activity
from app.services.word_card import enrich_word_cards


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--dry-run", action="store_true", help="Print cards instead of writing them")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        stats = enrich_word_cards(db, args.limit, dry_run=args.dry_run)
    finally:
        db.close()
    print(stats)
    if not args.dry_run and stats["written"]:
        adb = SessionLocal()
        try:
            log_activity(adb, "word_card_enrichment",
                         f"Word cards for {stats['written']} words "
                         f"({stats['chains']} chains, {stats['phrases']} phrases)",
                         detail=stats)
        finally:
            adb.close()


if __name__ == "__main__":
    main()
