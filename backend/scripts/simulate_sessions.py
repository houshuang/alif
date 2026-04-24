#!/usr/bin/env python3
"""Simulate a student's learning journey over multiple days.

Drives the real Alif service stack against a copy of the production database.

Usage:
    python3 scripts/simulate_sessions.py --days 30 --profile beginner
    python3 scripts/simulate_sessions.py --days 60 --profile strong --db ~/alif-backups/alif_20260210.db
    python3 scripts/simulate_sessions.py --days 30 --profile casual --csv /tmp/claude/sim.csv
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ALIF_SKIP_MIGRATIONS"] = "1"
os.environ["TESTING"] = "1"

from datetime import datetime, timezone

from app.simulation.db_setup import create_simulation_db, find_latest_backup
from app.simulation.reporter import print_console_report, write_csv_report
from app.simulation.runner import run_simulation
from app.simulation.student import PROFILES


def main():
    parser = argparse.ArgumentParser(
        description="Simulate a student's learning journey over multiple days"
    )
    parser.add_argument("--days", type=int, required=True, help="Number of days to simulate")
    parser.add_argument(
        "--profile",
        choices=list(PROFILES.keys()),
        required=True,
        help="Student behavior profile",
    )
    parser.add_argument("--db", type=str, help="Path to DB backup (default: latest from ~/alif-backups/)")
    parser.add_argument("--csv", type=str, help="Write CSV report to this path")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument(
        "--start-date",
        type=str,
        help="Simulation start date YYYY-MM-DD (default: 2026-03-01)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    db_path = args.db or find_latest_backup()
    print(f"Source DB: {db_path}")

    engine, SessionFactory, tmp_path = create_simulation_db(db_path)
    print(f"Simulation DB: {tmp_path}")

    profile = PROFILES[args.profile]
    start_date = None
    if args.start_date:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )

    print(f"Profile: {profile.name}, Days: {args.days}, Seed: {args.seed}")
    print()

    db = SessionFactory()
    try:
        snapshots = run_simulation(
            db, args.days, profile, start_date=start_date, seed=args.seed
        )
        print_console_report(snapshots, profile_name=profile.name)
        if args.csv:
            write_csv_report(snapshots, args.csv)
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    main()
