#!/usr/bin/env python3
"""Fix dirty lemma_ar_bare fields from OCR/book imports.

Finds and fixes two patterns:
1. Definite article ال baked into bare form (المطحونة → مطحونة)
2. Ta marbuta written as ha when ال was present (المطحونه → مطحونة)

Also checks lemma_ar (diacritized) for the same issues.

After cleaning, checks for duplicates that the fix may have created
(e.g. المطحونة and مطحونة now both → مطحونة).

Run: python scripts/cleanup_dirty_bare_forms.py [--apply] [--verbose]
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge
from app.services.lemma_quality import clean_bare_form, normalize_ta_marbuta
from app.services.sentence_validator import strip_diacritics


def find_dirty_lemmas(db):
    """Find lemmas whose bare forms need cleaning."""
    from sqlalchemy import or_
    all_lemmas = db.query(Lemma).filter(
        or_(Lemma.word_category != "junk", Lemma.word_category.is_(None))
    ).all()
    dirty = []

    for lem in all_lemmas:
        if not lem.lemma_ar_bare:
            continue

        old_bare = lem.lemma_ar_bare
        had_al = old_bare.startswith('ال')
        new_bare = clean_bare_form(old_bare)
        new_bare = normalize_ta_marbuta(new_bare, had_al_prefix=had_al)

        # Also check diacritized form
        old_ar = lem.lemma_ar or ""
        old_ar_stripped = strip_diacritics(old_ar)
        had_al_ar = old_ar_stripped.startswith('ال')
        new_ar = clean_bare_form(old_ar)
        new_ar = normalize_ta_marbuta(new_ar, had_al_prefix=had_al_ar)

        if new_bare != old_bare or new_ar != old_ar:
            dirty.append({
                "lemma": lem,
                "old_bare": old_bare,
                "new_bare": new_bare,
                "old_ar": old_ar,
                "new_ar": new_ar,
                "bare_changed": new_bare != old_bare,
                "ar_changed": new_ar != old_ar,
            })

    return dirty


def find_post_clean_duplicates(db, dirty_items):
    """After cleaning, check if any lemmas now collide with existing ones."""
    dupes = []
    for item in dirty_items:
        new_bare = item["new_bare"]
        lem = item["lemma"]
        from sqlalchemy import or_
        existing = db.query(Lemma).filter(
            Lemma.lemma_ar_bare == new_bare,
            Lemma.lemma_id != lem.lemma_id,
            Lemma.canonical_lemma_id.is_(None),
            or_(Lemma.word_category != "junk", Lemma.word_category.is_(None)),
        ).first()
        if existing:
            dupes.append((lem, existing))
    return dupes


def cleanup(dry_run=True, verbose=False):
    db = SessionLocal()

    dirty = find_dirty_lemmas(db)
    if not dirty:
        print("No dirty bare forms found.")
        db.close()
        return

    print(f"Found {len(dirty)} lemmas with dirty bare forms:\n")
    for item in dirty:
        lem = item["lemma"]
        ulk = db.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == lem.lemma_id
        ).first()
        state = ulk.knowledge_state if ulk else "no_ulk"
        changes = []
        if item["bare_changed"]:
            changes.append(f"bare: {item['old_bare']!r} → {item['new_bare']!r}")
        if item["ar_changed"]:
            changes.append(f"ar: {item['old_ar']!r} → {item['new_ar']!r}")
        print(f"  [{lem.lemma_id}] {', '.join(changes)} ({lem.gloss_en}) [{state}]")

    # Check for duplicates post-clean
    dupes = find_post_clean_duplicates(db, dirty)
    if dupes:
        print(f"\n⚠ {len(dupes)} post-clean duplicates detected:")
        for dirty_lem, existing_lem in dupes:
            print(
                f"  [{dirty_lem.lemma_id}] {dirty_lem.lemma_ar_bare} → "
                f"collides with [{existing_lem.lemma_id}] {existing_lem.lemma_ar_bare} "
                f"({existing_lem.gloss_en})"
            )
        print("  These will be marked as variants of the existing lemma.")

    if dry_run:
        print(f"\n[DRY RUN] Use --apply to fix these.")
        db.close()
        return

    # Apply fixes
    fixed = 0
    for item in dirty:
        lem = item["lemma"]
        if item["bare_changed"]:
            lem.lemma_ar_bare = item["new_bare"]
        if item["ar_changed"]:
            lem.lemma_ar = item["new_ar"]
        fixed += 1

    # Mark post-clean duplicates as variants
    variants_marked = 0
    for dirty_lem, existing_lem in dupes:
        dirty_lem.canonical_lemma_id = existing_lem.lemma_id
        variants_marked += 1
        print(f"  Marked [{dirty_lem.lemma_id}] as variant of [{existing_lem.lemma_id}]")

    db.commit()
    print(f"\nApplied: {fixed} bare forms cleaned, {variants_marked} variants marked.")
    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix dirty lemma bare forms (ال prefix, ه→ة)")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry run)")
    parser.add_argument("--verbose", action="store_true", help="Extra detail")
    args = parser.parse_args()

    dry_run = not args.apply
    if dry_run:
        print("=== DRY RUN (use --apply to commit) ===\n")
    else:
        print("=== APPLYING CHANGES ===\n")

    cleanup(dry_run=dry_run, verbose=args.verbose)
