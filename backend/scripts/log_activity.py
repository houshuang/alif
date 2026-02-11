#!/usr/bin/env python3
"""CLI tool to log an activity entry from the command line.

Usage:
    python scripts/log_activity.py "backfill_completed" "Backfilled frequency for 200 lemmas"
    python scripts/log_activity.py "manual_fix" "Corrected glosses for 5 words" --detail '{"lemma_ids": [1,2,3,4,5]}'
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from app.services.activity_log import log_activity


def main():
    parser = argparse.ArgumentParser(description="Log an activity entry")
    parser.add_argument("event_type", help="Event type (e.g., backfill_completed, manual_fix)")
    parser.add_argument("summary", help="Human-readable summary")
    parser.add_argument("--detail", help="JSON detail dict (optional)", default=None)
    args = parser.parse_args()

    detail = json.loads(args.detail) if args.detail else None

    db = SessionLocal()
    try:
        entry = log_activity(db, args.event_type, args.summary, detail)
        print(f"Logged: [{entry.event_type}] {entry.summary}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
