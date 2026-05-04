"""Backfill transliteration_ala_lc for lemmas missing transliteration.

Uses deterministic Arabic→ALA-LC romanization from diacritized text.
No LLM needed — pure rule-based character mapping.

Usage:
    cd backend && python3 scripts/backfill_transliteration.py [--dry-run] [--limit=1000] [--rerun-all]

--rerun-all recomputes for every diacritized lemma (use after fixing the
transliteration function itself, to refresh stored values).
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


def backfill(dry_run=False, limit=2000, rerun_all=False):
    db = SessionLocal()

    if rerun_all:
        missing = (
            db.query(Lemma)
            .order_by(Lemma.frequency_rank.asc().nullslast())
            .limit(limit)
            .all()
        )
    else:
        missing = (
            db.query(Lemma)
            .filter(
                (Lemma.transliteration_ala_lc.is_(None)) | (Lemma.transliteration_ala_lc == ""),
                # Include variants — they appear in word lookups and need transliteration
            )
            .order_by(Lemma.frequency_rank.asc().nullslast())
            .limit(limit)
            .all()
        )

    label = "all lemmas" if rerun_all else "lemmas without transliteration"
    print(f"Found {len(missing)} {label} (limit={limit})")
    if not missing:
        db.close()
        return

    total_done = 0
    total_skipped = 0
    total_unchanged = 0

    import re
    diacritic_re = re.compile(r"[\u064B-\u065F\u0670]")

    for lemma in missing:
        source = lemma.lemma_ar or ""
        if not source.strip():
            print(f"  {lemma.lemma_id}: no Arabic text, skipping")
            total_skipped += 1
            continue

        # Skip undiacritized words — they produce garbage transliterations
        if not diacritic_re.search(source):
            total_skipped += 1
            continue

        translit = transliterate_lemma(source)
        if not translit:
            print(f"  {lemma.lemma_id} {lemma.lemma_ar_bare}: empty result, skipping")
            total_skipped += 1
            continue

        if rerun_all and translit == lemma.transliteration_ala_lc:
            total_unchanged += 1
            continue

        prev = lemma.transliteration_ala_lc
        suffix = f" (was {prev!r})" if rerun_all and prev else ""
        print(f"  {lemma.lemma_id} {source} → {translit}{suffix}")
        if not dry_run:
            lemma.transliteration_ala_lc = translit
        total_done += 1

    if not dry_run:
        db.commit()

    unchanged_note = f", {total_unchanged} unchanged" if total_unchanged else ""
    if dry_run:
        db.rollback()
        print(f"\nDry run: would update {total_done} lemmas ({total_skipped} skipped{unchanged_note})")
    else:
        print(f"\nUpdated {total_done} lemmas with transliteration ({total_skipped} skipped{unchanged_note})")
        if total_done > 0:
            log_activity(
                db,
                event_type="transliteration_backfill_completed",
                summary=f"Backfilled ALA-LC transliteration for {total_done} lemmas",
                detail={
                    "lemmas_updated": total_done,
                    "skipped": total_skipped,
                    "unchanged": total_unchanged,
                    "rerun_all": rerun_all,
                },
            )

    db.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    rerun_all = "--rerun-all" in sys.argv
    limit = 2000
    for arg in sys.argv:
        if arg.startswith("--limit="):
            limit = int(arg.split("=")[1])
    backfill(dry_run=dry_run, limit=limit, rerun_all=rerun_all)
