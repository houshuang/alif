#!/usr/bin/env python3
"""Backfill pattern enrichment for patterns with 2+ known/acquiring/learning words.

Usage:
    python3 scripts/backfill_pattern_enrichment.py [--dry-run] [--limit N] [--force]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func
from app.database import SessionLocal
from app.models import Lemma, PatternInfo, UserLemmaKnowledge


def main():
    parser = argparse.ArgumentParser(description="Backfill pattern enrichment")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be enriched")
    parser.add_argument("--limit", type=int, default=30, help="Max patterns to enrich (default: 30)")
    parser.add_argument("--force", action="store_true", help="Re-enrich even if enrichment exists")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Find patterns with 2+ studied words
        studied_patterns = (
            db.query(
                Lemma.wazn,
                Lemma.wazn_meaning,
                func.count(Lemma.lemma_id).label("studied_count"),
            )
            .join(UserLemmaKnowledge)
            .filter(
                Lemma.wazn.isnot(None),
                Lemma.canonical_lemma_id.is_(None),
                UserLemmaKnowledge.knowledge_state.in_(["acquiring", "learning", "known"]),
            )
            .group_by(Lemma.wazn)
            .having(func.count(Lemma.lemma_id) >= 2)
            .order_by(func.count(Lemma.lemma_id).desc())
            .all()
        )

        # Check which already have enrichment
        existing_wazns = set()
        if not args.force:
            enriched_rows = (
                db.query(PatternInfo.wazn)
                .filter(PatternInfo.enrichment_json.isnot(None))
                .all()
            )
            existing_wazns = {r.wazn for r in enriched_rows}

        candidates = []
        for row in studied_patterns:
            if not args.force and row.wazn in existing_wazns:
                continue
            candidates.append((row.wazn, row.wazn_meaning, row.studied_count))

        print(f"Found {len(candidates)} patterns needing enrichment (of {len(studied_patterns)} with 2+ studied words)")

        if args.dry_run:
            for wazn, meaning, count in candidates[:args.limit]:
                has = "HAS" if wazn in existing_wazns else "NO"
                print(f"  {wazn} ({meaning or '?'}) — {count} studied words, {has} enrichment")
            return

        from app.services.pattern_enrichment import generate_pattern_enrichment

        enriched = 0
        for wazn, meaning, count in candidates[:args.limit]:
            print(f"  Enriching {wazn} ({meaning or '?'}, {count} studied words)...", end=" ", flush=True)
            if args.force:
                existing = db.query(PatternInfo).filter(PatternInfo.wazn == wazn).first()
                if existing:
                    existing.enrichment_json = None
                    db.commit()
            generate_pattern_enrichment(wazn)
            # Check result
            pi = db.query(PatternInfo).filter(PatternInfo.wazn == wazn).first()
            if pi and pi.enrichment_json:
                print("OK")
                enriched += 1
            else:
                print("FAILED")
            time.sleep(1)  # Rate limiting

        print(f"\nDone: {enriched}/{min(len(candidates), args.limit)} patterns enriched")
    finally:
        db.close()


if __name__ == "__main__":
    main()
