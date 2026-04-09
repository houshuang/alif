#!/usr/bin/env python3
"""Import common Arabic words that the LLM uses as scaffold in sentences.

These words were identified by mining correction_failed logs — the LLM
keeps generating natural sentences using them, but the mapping pipeline
rejects the sentences because the correct lemma isn't in the DB.

Importing them as source="scaffold" lets them serve as valid mapping
targets without forcing them into the learner's review queue.

Usage:
    python scripts/import_scaffold_lemmas.py              # import all
    python scripts/import_scaffold_lemmas.py --dry-run    # preview
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, Base, engine
from app.models import Lemma
from app.services.lemma_quality import run_quality_gates
from app.services.sentence_validator import (
    strip_diacritics,
    normalize_alef,
    resolve_existing_lemma,
    build_comprehensive_lemma_lookup,
)

# Words mined from correction_failed pipeline logs (2026-03 through 2026-04).
# Each: (diacritized_form, gloss_en, pos)
SCAFFOLD_WORDS = [
    ("أَخِيرًا", "finally, at last", "adv"),
    ("فَضَّلَ", "to prefer", "verb"),
    ("اِحْتَاجَ", "to need, to require", "verb"),
    ("سُرْعَة", "speed, quickness", "noun"),
    ("مُنَاسِب", "suitable, appropriate", "adj"),
    ("صِدْق", "honesty, truthfulness", "noun"),
    ("ضَحِكَ", "to laugh", "verb"),
    ("إِنْسَان", "human being, person", "noun"),
    ("مَاهِر", "skilled, expert", "adj"),
    ("رَأَى", "to see", "verb"),
    ("وُصُول", "arrival", "noun"),
    ("تَرَكَ", "to leave, to abandon", "verb"),
    ("اِكْتِشَاف", "discovery", "noun"),
    ("لَمَّا", "when", "particle"),
    ("مُقْبِل", "next, upcoming", "adj"),
    ("دُخُول", "entry, entering", "noun"),
    ("قَطَعَ", "to cut", "verb"),
    ("مَوْجُود", "present, existing", "adj"),
    ("مَزْرَعَة", "farm", "noun"),
    ("مُغْلَق", "closed", "adj"),
    ("حَلّ", "solution", "noun"),
    ("تَأْكِيد", "confirmation, certainty", "noun"),
    ("ذَهَاب", "going, departure", "noun"),
    ("مُرَاجَعَة", "review, revision", "noun"),
    ("يَمِين", "right (direction)", "noun"),
    ("مَعْرِفَة", "knowledge", "noun"),
    ("خُرُوج", "exit, going out", "noun"),
    ("كَسَرَ", "to break", "verb"),
    ("صَارِم", "strict, firm", "adj"),
    ("سَرِقَة", "theft, robbery", "noun"),
    ("رَحْمَة", "mercy, compassion", "noun"),
    ("نَزَلَ", "to descend, to come down", "verb"),
    ("حُبّ", "love", "noun"),
    ("مَصْنَع", "factory", "noun"),
    ("لَمَسَ", "to touch", "verb"),
    ("مُرْتَاح", "relaxed, comfortable", "adj"),
    ("تَنْظِيف", "cleaning", "noun"),
    ("أَدْرَكَ", "to realize, to perceive", "verb"),
    ("زِيَادَة", "increase", "noun"),
]


def main():
    parser = argparse.ArgumentParser(description="Import scaffold lemmas")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        lookup = build_comprehensive_lemma_lookup(db)
        existing_bare = set(
            r[0] for r in db.execute(
                Lemma.__table__.select().with_only_columns(Lemma.lemma_ar_bare)
            ).fetchall()
        )

        imported = 0
        skipped = 0
        new_ids: list[int] = []

        for arabic, gloss, pos in SCAFFOLD_WORDS:
            bare = strip_diacritics(arabic)

            # Skip if already exists
            if normalize_alef(bare) in existing_bare or bare in existing_bare:
                print(f"  [skip] {arabic} ({gloss}) — already in DB")
                skipped += 1
                continue

            existing = resolve_existing_lemma(bare, lookup)
            if existing:
                print(f"  [skip] {arabic} ({gloss}) — resolves to existing #{existing}")
                skipped += 1
                continue

            if args.dry_run:
                print(f"  [dry-run] {arabic} (bare: {bare}) — {gloss} [{pos}]")
                imported += 1
                continue

            lemma = Lemma(
                lemma_ar=arabic,
                lemma_ar_bare=bare,
                gloss_en=gloss,
                pos=pos,
                source="scaffold",
            )
            db.add(lemma)
            db.flush()
            new_ids.append(lemma.lemma_id)
            imported += 1
            print(f"  [import] #{lemma.lemma_id} {arabic} — {gloss} [{pos}]")

        if new_ids and not args.dry_run:
            db.commit()
            print(f"\nRunning quality gates on {len(new_ids)} lemmas...")
            gates = run_quality_gates(
                db, new_ids,
                background_enrich=False,
            )
            db.commit()
            print(f"  Finalized: {gates.get('finalize', {})}")
            print(f"  Variants marked: {gates.get('variants', 0)}")
            print(f"  Stamped: {gates.get('stamped', 0)}")

        print(f"\nDone: {imported} imported, {skipped} skipped")

    finally:
        db.close()


if __name__ == "__main__":
    main()
