"""Backfill word_category for existing lemmas.

Phase 1: Cross-reference StoryWord.name_type → set word_category on linked Lemmas.
Phase 2: Run classify_lemmas() on remaining NULL-category lemmas in batches.

Usage:
    python3 scripts/backfill_word_categories.py [--dry-run]
"""
import argparse
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import SessionLocal
from app.models import Lemma, StoryWord
from app.services.import_quality import classify_lemmas

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Backfill word_category on lemmas")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()

    db = SessionLocal()

    # Phase 1: StoryWord.name_type → Lemma.word_category
    name_sws = db.query(StoryWord).filter(StoryWord.name_type.isnot(None), StoryWord.lemma_id.isnot(None)).all()
    name_lemma_ids = set()
    for sw in name_sws:
        if sw.name_type in ("personal", "place"):
            name_lemma_ids.add(sw.lemma_id)

    phase1_count = 0
    for lid in name_lemma_ids:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == lid).first()
        if lemma and not lemma.word_category:
            logger.info(f"  Phase 1: {lemma.lemma_ar_bare} ({lemma.gloss_en}) → proper_name (from StoryWord)")
            if not args.dry_run:
                lemma.word_category = "proper_name"
                if lemma.gloss_en and not lemma.gloss_en.startswith("(name)"):
                    lemma.gloss_en = f"(name) {lemma.gloss_en}"
            phase1_count += 1

    logger.info(f"Phase 1: {phase1_count} lemmas marked as proper_name from StoryWord data")

    # Phase 2: LLM classification for remaining NULL-category lemmas
    uncategorized = db.query(Lemma).filter(Lemma.word_category.is_(None)).all()
    logger.info(f"Phase 2: {len(uncategorized)} lemmas need classification")

    batch_size = 50
    phase2_counts = {"proper_name": 0, "onomatopoeia": 0, "standard": 0}

    for i in range(0, len(uncategorized), batch_size):
        batch = uncategorized[i:i + batch_size]
        lemma_dicts = [
            {"arabic": lem.lemma_ar_bare, "english": lem.gloss_en or ""}
            for lem in batch
        ]

        classified, rejected = classify_lemmas(lemma_dicts, batch_size=batch_size)

        # Build lookup by arabic
        cat_by_arabic = {}
        for c in classified:
            cat = c.get("word_category", "standard")
            cat_by_arabic[c["arabic"]] = cat

        for lem in batch:
            cat = cat_by_arabic.get(lem.lemma_ar_bare, "standard")
            if cat in ("proper_name", "onomatopoeia"):
                logger.info(f"  Phase 2: {lem.lemma_ar_bare} ({lem.gloss_en}) → {cat}")
                if not args.dry_run:
                    lem.word_category = cat
                    if cat == "proper_name" and lem.gloss_en and not lem.gloss_en.startswith("(name)"):
                        lem.gloss_en = f"(name) {lem.gloss_en}"
            phase2_counts[cat] = phase2_counts.get(cat, 0) + 1

    logger.info(f"Phase 2 results: {phase2_counts}")

    if not args.dry_run:
        db.commit()
        logger.info("Committed all changes")

        # Log to ActivityLog
        try:
            from app.services.activity_log import log_activity
            log_activity(
                db,
                event_type="manual_action",
                summary=f"Backfilled word_category: {phase1_count} from StoryWord, "
                        f"{phase2_counts.get('proper_name', 0)} names + "
                        f"{phase2_counts.get('onomatopoeia', 0)} sounds from LLM",
                detail_json={
                    "phase1_count": phase1_count,
                    "phase2_counts": phase2_counts,
                },
            )
        except Exception as e:
            logger.warning(f"Could not log activity: {e}")
    else:
        logger.info("DRY RUN — no changes written")

    db.close()


if __name__ == "__main__":
    main()
