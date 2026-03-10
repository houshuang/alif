"""Backfill expanded forms_json for existing lemmas.

Re-enriches verbs that are missing past_1s (crucial for weak verb recognition)
and nouns/adjectives missing sound_f_plural/sound_m_plural/dual forms.

Uses Claude Haiku CLI (free). Skips lemmas that already have the new keys.
Safe to run multiple times — only fills gaps.

Usage:
    python3 scripts/backfill_expanded_forms.py [--dry-run] [--limit N] [--pos verb|noun]
"""
import argparse
import json
import logging
import sys
import time

sys.path.insert(0, ".")

from app.database import SessionLocal
from app.models import Lemma
from app.services.lemma_enrichment import _generate_forms, FORMS_VALID_KEYS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def find_verbs_needing_enrichment(db) -> list[Lemma]:
    """Find verbs missing past_1s (the key new field for weak verb recognition)."""
    verbs = (
        db.query(Lemma)
        .filter(
            Lemma.pos == "verb",
            Lemma.canonical_lemma_id == None,
            Lemma.forms_json != None,
        )
        .all()
    )
    return [v for v in verbs if not (v.forms_json or {}).get("past_1s")]


def find_nouns_needing_enrichment(db) -> list[Lemma]:
    """Find nouns missing sound_f_plural or sound_m_plural."""
    nouns = (
        db.query(Lemma)
        .filter(
            Lemma.pos == "noun",
            Lemma.canonical_lemma_id == None,
            Lemma.forms_json != None,
        )
        .all()
    )
    return [
        n for n in nouns
        if not (n.forms_json or {}).get("sound_f_plural")
        and not (n.forms_json or {}).get("sound_m_plural")
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--pos", choices=["verb", "noun", "all"], default="all")
    args = parser.parse_args()

    db = SessionLocal()

    candidates = []
    if args.pos in ("verb", "all"):
        verbs = find_verbs_needing_enrichment(db)
        logger.info(f"Found {len(verbs)} verbs needing expanded forms")
        candidates.extend(verbs)
    if args.pos in ("noun", "all"):
        nouns = find_nouns_needing_enrichment(db)
        logger.info(f"Found {len(nouns)} nouns needing expanded forms")
        candidates.extend(nouns)

    if args.limit:
        candidates = candidates[:args.limit]

    if args.dry_run:
        logger.info(f"DRY RUN: would re-enrich {len(candidates)} lemmas")
        for lem in candidates[:10]:
            logger.info(f"  {lem.lemma_ar_bare} ({lem.pos}, {lem.gloss_en})")
        return

    enriched = 0
    failed = 0
    for i, lem in enumerate(candidates):
        try:
            new_forms = _generate_forms(lem)
            if new_forms:
                # Merge: keep existing values, add new ones
                existing = lem.forms_json or {}
                merged = {**existing}
                added_keys = []
                for k, v in new_forms.items():
                    if k not in existing:
                        merged[k] = v
                        added_keys.append(k)
                if added_keys:
                    lem.forms_json = merged
                    db.commit()
                    enriched += 1
                    logger.info(f"[{i+1}/{len(candidates)}] {lem.lemma_ar_bare}: added {', '.join(added_keys)}")
                else:
                    logger.debug(f"[{i+1}/{len(candidates)}] {lem.lemma_ar_bare}: no new keys")
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f"[{i+1}/{len(candidates)}] {lem.lemma_ar_bare}: failed — {e}")
            db.rollback()
            failed += 1

        if (i + 1) % 50 == 0:
            logger.info(f"Progress: {i+1}/{len(candidates)}, enriched={enriched}, failed={failed}")

    logger.info(f"Done: {enriched} enriched, {failed} failed out of {len(candidates)}")
    db.close()


if __name__ == "__main__":
    main()
