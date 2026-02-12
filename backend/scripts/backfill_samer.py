#!/usr/bin/env python3
"""Backfill cefr_level on lemmas from SAMER Readability Lexicon v2.

SAMER is a 40K-lemma readability lexicon for MSA, manually annotated by
language professionals from Egypt, Syria, and Saudi Arabia.
Levels 1-5 map to CEFR as: L1→A1, L2→A2, L3→B1, L4→B2, L5→C1.

Source: Al Khalil, Habash, Jiang (LREC 2020)
        https://camel.abudhabi.nyu.edu/samer-readability-lexicon/

Usage:
    python scripts/backfill_samer.py /path/to/SAMER-Readability-Lexicon-v2.tsv [--dry-run] [--overwrite]
"""

import argparse
import csv
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("ALIF_SKIP_MIGRATIONS", "1")

from app.database import SessionLocal
from app.models import Lemma
from app.services.activity_log import log_activity

SAMER_TO_CEFR = {1: "A1", 2: "A2", 3: "B1", 4: "B2", 5: "C1"}

DIACRITICS_RE = re.compile(
    r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]'
)


def normalize(text: str) -> str:
    text = DIACRITICS_RE.sub('', text)
    text = text.replace('\u0640', '')
    text = re.sub(r'[أإآٱ]', 'ا', text)
    return text


def load_samer(path: str) -> dict[str, list[dict]]:
    """Load SAMER TSV into {normalized_bare: [entries]}."""
    samer: dict[str, list[dict]] = {}
    with open(path, encoding='utf-8') as f:
        reader = csv.reader(f, delimiter='\t')
        next(reader)  # skip header
        for row in reader:
            if len(row) < 9:
                continue
            lemma_pos = row[3]
            parts = lemma_pos.rsplit('#', 1)
            if len(parts) < 2:
                continue
            lemma_ar, pos = parts
            level = int(row[8])
            occurrences = int(row[0])
            bare = normalize(lemma_ar)
            samer.setdefault(bare, []).append({
                'pos': pos, 'level': level, 'occurrences': occurrences
            })
    return samer


def lookup_samer(samer: dict, bare_norm: str) -> int | None:
    """Find best SAMER level for a normalized bare lemma. Returns 1-5 or None."""
    candidates = samer.get(bare_norm, [])
    if not candidates and bare_norm.startswith('ال'):
        candidates = samer.get(bare_norm[2:], [])
    if not candidates and not bare_norm.startswith('ال'):
        candidates = samer.get('ال' + bare_norm, [])
    if not candidates:
        return None
    # Filter out proper nouns
    real = [c for c in candidates if c['pos'] != 'noun_prop']
    if not real:
        real = candidates
    # Pick highest-occurrence entry
    best = max(real, key=lambda c: c['occurrences'])
    return best['level']


def main():
    parser = argparse.ArgumentParser(description="Backfill CEFR from SAMER readability lexicon")
    parser.add_argument("tsv_path", help="Path to SAMER-Readability-Lexicon-v2.tsv")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing cefr_level values")
    args = parser.parse_args()

    if not Path(args.tsv_path).exists():
        print(f"File not found: {args.tsv_path}")
        sys.exit(1)

    samer = load_samer(args.tsv_path)
    print(f"Loaded SAMER: {sum(len(v) for v in samer.values())} entries, {len(samer)} unique bare forms")

    db = SessionLocal()
    try:
        lemmas = db.query(Lemma).filter(Lemma.canonical_lemma_id.is_(None)).all()
        print(f"Processing {len(lemmas)} canonical lemmas...")

        matched = 0
        updated = 0
        skipped_existing = 0
        level_dist = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

        for lemma in lemmas:
            bare = lemma.lemma_ar_bare
            if not bare:
                continue
            bare_norm = normalize(bare)
            level = lookup_samer(samer, bare_norm)
            if level is None:
                continue

            matched += 1
            level_dist[level] += 1
            cefr = SAMER_TO_CEFR[level]

            if lemma.cefr_level and not args.overwrite:
                skipped_existing += 1
                continue

            if lemma.cefr_level != cefr:
                if not args.dry_run:
                    lemma.cefr_level = cefr
                updated += 1

        if not args.dry_run and updated > 0:
            db.commit()
            log_activity(
                db,
                event_type="frequency_backfill_completed",
                summary=f"SAMER readability: {updated} lemmas got cefr_level",
                detail={
                    "source": "SAMER v2",
                    "matched": matched,
                    "updated": updated,
                    "skipped_existing": skipped_existing,
                    "total": len(lemmas),
                    "level_dist": {f"L{k}": v for k, v in level_dist.items()},
                },
            )

        prefix = "[DRY RUN] " if args.dry_run else ""
        print(f"\n{prefix}Results:")
        print(f"  Matched: {matched}/{len(lemmas)} ({100*matched/len(lemmas):.1f}%)")
        print(f"  Updated: {updated}")
        print(f"  Skipped (had cefr_level): {skipped_existing}")
        print(f"  By SAMER level: " + ", ".join(f"L{k}={v}" for k, v in sorted(level_dist.items())))
    finally:
        db.close()


if __name__ == "__main__":
    main()
