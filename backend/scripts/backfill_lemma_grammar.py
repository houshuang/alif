"""Backfill grammar_features_json on existing lemmas using LLM tagging.

Usage:
    cd backend && python scripts/backfill_lemma_grammar.py [--dry-run] [--limit N]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import Lemma
from app.services.activity_log import log_activity
from app.services.grammar_tagger import tag_lemma_grammar


def main():
    parser = argparse.ArgumentParser(description="Backfill lemma grammar features")
    parser.add_argument("--dry-run", action="store_true", help="Print without saving")
    parser.add_argument("--limit", type=int, default=0, help="Max lemmas to process (0=all)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = db.query(Lemma).filter(Lemma.grammar_features_json.is_(None))
        if args.limit:
            query = query.limit(args.limit)
        lemmas = query.all()

        print(f"Found {len(lemmas)} lemmas without grammar features")

        for i, lemma in enumerate(lemmas, 1):
            try:
                features = tag_lemma_grammar(lemma.lemma_ar, lemma.pos, lemma.gloss_en)
                print(f"[{i}/{len(lemmas)}] {lemma.lemma_ar} ({lemma.gloss_en}): {features}")

                if not args.dry_run:
                    lemma.grammar_features_json = features
                    db.commit()

                time.sleep(0.5)
            except Exception as e:
                print(f"  ERROR: {e}")
                continue

        tagged = sum(1 for l in lemmas if l.grammar_features_json is not None)
        if not args.dry_run and tagged > 0:
            log_activity(
                db,
                event_type="grammar_backfill_completed",
                summary=f"Backfilled grammar features for {tagged} lemmas",
                detail={"lemmas_processed": len(lemmas), "lemmas_tagged": tagged},
            )
        print("Done." if not args.dry_run else "Dry run complete, no changes saved.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
