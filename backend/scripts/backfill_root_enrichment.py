#!/usr/bin/env python3
"""Backfill root enrichment for roots with 2+ known/acquiring/learning lemmas.

Usage:
    python3 scripts/backfill_root_enrichment.py [--dry-run] [--limit N] [--force]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func
from app.database import SessionLocal
from app.models import Lemma, Root, UserLemmaKnowledge


def main():
    parser = argparse.ArgumentParser(description="Backfill root enrichment")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be enriched")
    parser.add_argument("--limit", type=int, default=50, help="Max roots to enrich (default: 50)")
    parser.add_argument("--force", action="store_true", help="Re-enrich even if enrichment exists")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Find roots with 2+ studied lemmas
        studied_roots = (
            db.query(
                Lemma.root_id,
                func.count(Lemma.lemma_id).label("studied_count"),
            )
            .join(UserLemmaKnowledge)
            .filter(
                Lemma.root_id.isnot(None),
                Lemma.canonical_lemma_id.is_(None),
                UserLemmaKnowledge.knowledge_state.in_(["acquiring", "learning", "known"]),
            )
            .group_by(Lemma.root_id)
            .having(func.count(Lemma.lemma_id) >= 2)
            .order_by(func.count(Lemma.lemma_id).desc())
            .all()
        )

        root_ids = [r.root_id for r in studied_roots]
        roots = db.query(Root).filter(Root.root_id.in_(root_ids)).all()
        root_map = {r.root_id: r for r in roots}

        candidates = []
        for row in studied_roots:
            root = root_map.get(row.root_id)
            if not root:
                continue
            if not args.force and root.enrichment_json:
                continue
            candidates.append((root, row.studied_count))

        print(f"Found {len(candidates)} roots needing enrichment (of {len(studied_roots)} with 2+ studied words)")

        if args.dry_run:
            for root, count in candidates[:args.limit]:
                has = "HAS" if root.enrichment_json else "NO"
                print(f"  {root.root} ({root.core_meaning_en or '?'}) — {count} studied words, {has} enrichment")
            return

        from app.services.root_enrichment import generate_root_enrichment

        enriched = 0
        for root, count in candidates[:args.limit]:
            print(f"  Enriching {root.root} ({root.core_meaning_en or '?'}, {count} studied words)...", end=" ", flush=True)
            if args.force and root.enrichment_json:
                root.enrichment_json = None
                db.commit()
            generate_root_enrichment(root.root_id)
            # Reload to check
            db.refresh(root)
            if root.enrichment_json:
                print("OK")
                enriched += 1
            else:
                print("FAILED")
            time.sleep(3)  # Rate limiting + memory cooldown for Claude CLI

        print(f"\nDone: {enriched}/{min(len(candidates), args.limit)} roots enriched")
    finally:
        db.close()


if __name__ == "__main__":
    main()
