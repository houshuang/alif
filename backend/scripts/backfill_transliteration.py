"""Backfill transliteration_ala_lc for lemmas missing transliteration.

Uses deterministic Arabic→ALA-LC romanization from diacritized text.
No LLM needed — pure rule-based character mapping.

Usage:
    cd backend && python3 scripts/backfill_transliteration.py [--dry-run] [--limit=1000]
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from app.models import Lemma
from app.services.activity_log import log_activity
from app.services.transliteration import transliterate_lemma


def backfill(dry_run=False, limit=2000):
    db = SessionLocal()

    missing = (
        db.query(Lemma)
        .filter(
            (Lemma.transliteration_ala_lc.is_(None)) | (Lemma.transliteration_ala_lc == ""),
            Lemma.canonical_lemma_id.is_(None),
        )
        .order_by(Lemma.frequency_rank.asc().nullslast())
        .limit(limit)
        .all()
    )

    print(f"Found {len(missing)} lemmas without transliteration (limit={limit})")
    if not missing:
        db.close()
        return

    total_done = 0
    total_skipped = 0

    for lemma in missing:
        source = lemma.lemma_ar or ""
        if not source.strip():
            print(f"  {lemma.lemma_id}: no Arabic text, skipping")
            total_skipped += 1
            continue

        translit = transliterate_lemma(source)
        if not translit:
            print(f"  {lemma.lemma_id} {lemma.lemma_ar_bare}: empty result, skipping")
            total_skipped += 1
            continue

        print(f"  {lemma.lemma_id} {source} → {translit}")
        if not dry_run:
            lemma.transliteration_ala_lc = translit
        total_done += 1

    if not dry_run:
        db.commit()

    if dry_run:
        db.rollback()
        print(f"\nDry run: would update {total_done} lemmas ({total_skipped} skipped)")
    else:
        print(f"\nUpdated {total_done} lemmas with transliteration ({total_skipped} skipped)")
        if total_done > 0:
            log_activity(
                db,
                event_type="transliteration_backfill_completed",
                summary=f"Backfilled ALA-LC transliteration for {total_done} lemmas",
                detail={"lemmas_updated": total_done, "skipped": total_skipped},
            )

    db.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    limit = 2000
    for arg in sys.argv:
        if arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
    backfill(dry_run=dry_run, limit=limit)
