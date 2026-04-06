#!/usr/bin/env python3
"""Fix dirty lemma_ar_bare fields using LLM classification.

Finds lemmas with ال-prefix in bare form, asks the LLM which ones should
be cleaned (strips ال, normalizes ه→ة), and applies the fixes.

The LLM correctly distinguishes:
- Dirty OCR: المطحونه → مطحونة (strip + fix ة)
- Legitimate: الله, الذي, التقى, الرازي (don't touch)

Run: python scripts/cleanup_dirty_bare_forms.py [--apply]
"""

import argparse
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge
from app.services.sentence_validator import strip_diacritics

logger = logging.getLogger(__name__)


def find_al_prefixed_lemmas(db):
    """Find all lemmas with ال in bare form or diacritized form."""
    from sqlalchemy import or_
    all_lemmas = db.query(Lemma).filter(
        or_(Lemma.word_category != "junk", Lemma.word_category.is_(None))
    ).all()

    candidates = []
    for lem in all_lemmas:
        bare = lem.lemma_ar_bare or ""
        ar_stripped = strip_diacritics(lem.lemma_ar or "")
        if bare.startswith('ال') or ar_stripped.startswith('ال'):
            candidates.append(lem)
    return candidates


def classify_with_llm(candidates):
    """Ask the LLM which bare forms need cleaning."""
    from app.services.llm import generate_completion

    word_list = "\n".join(
        f"  {lem.lemma_id}: {lem.lemma_ar_bare} ({lem.gloss_en})"
        for lem in candidates
    )

    prompt = f"""Review these Arabic lemma bare forms from a vocabulary database.
Some have ال-prefix baked in from OCR errors and need cleaning. Others have ال as an integral part.

For each word, decide:
- If the ال should be stripped (it's just the definite article baked into a bare form), return "clean" with the corrected form
- Also fix final ه that should be ة (OCR artifact): المطحونه → مطحونة
- If the ال is integral to the word, return "keep"

Rules for "keep" (do NOT clean):
- الله (God) — special word
- Relative pronouns: الذي, التي, الذين, اللذان, اللتان, اللواتي
- Form VIII/X verbs where ال is part of root: التقى, التحق, التهاب, استقبل
- الآن (now), اليوم (today) — fixed temporal expressions
- Proper nouns where ال is part of the name: الرازي, الحاوي
- الف (one thousand) — not ال + ف
- Words where stripping would leave <2 chars: الم → م (too short)

Rules for "clean" (DO clean):
- Common nouns/adjectives with OCR-baked ال: المكتب→مكتب, الشقة→شقة, المدينة→مدينة
- Country names where ال is article: السودان→سودان, الجزائر→جزائر, المغرب→مغرب
- Words with ه→ة OCR error: المطحونه→مطحونة, الجراحه→جراحة

Words:
{word_list}

Return JSON: {{"results": [{{"id": 123, "action": "clean", "fixed": "مطحونة"}}, {{"id": 456, "action": "keep"}}, ...]}}
Include every word ID."""

    result = generate_completion(prompt, json_mode=True, temperature=0.1, task_type="cleanup")
    return result.get("results", [])


def cleanup(dry_run=True):
    db = SessionLocal()

    candidates = find_al_prefixed_lemmas(db)
    if not candidates:
        print("No ال-prefixed lemmas found.")
        db.close()
        return

    print(f"Found {len(candidates)} lemmas with ال-prefix. Asking LLM to classify...\n")

    llm_results = classify_with_llm(candidates)
    id_to_result = {r["id"]: r for r in llm_results}

    to_clean = []
    to_keep = []
    for lem in candidates:
        r = id_to_result.get(lem.lemma_id, {})
        action = r.get("action", "keep")
        if action == "clean" and r.get("fixed"):
            to_clean.append((lem, r["fixed"]))
        else:
            to_keep.append(lem)

    print(f"LLM verdict: {len(to_clean)} to clean, {len(to_keep)} to keep\n")

    if to_clean:
        print("Will clean:")
        for lem, fixed in to_clean:
            ulk = db.query(UserLemmaKnowledge).filter(
                UserLemmaKnowledge.lemma_id == lem.lemma_id
            ).first()
            state = ulk.knowledge_state if ulk else "no_ulk"
            print(f"  [{lem.lemma_id}] {lem.lemma_ar_bare} → {fixed} ({lem.gloss_en}) [{state}]")

    if to_keep:
        print("\nKeeping as-is:")
        for lem in to_keep:
            print(f"  [{lem.lemma_id}] {lem.lemma_ar_bare} ({lem.gloss_en})")

    if dry_run:
        print(f"\n[DRY RUN] Use --apply to fix {len(to_clean)} lemmas.")
        db.close()
        return

    # Check for post-clean duplicates
    fixed = 0
    variants_marked = 0
    for lem, new_bare in to_clean:
        # Check if cleaned form collides with existing lemma
        existing = db.query(Lemma).filter(
            Lemma.lemma_ar_bare == new_bare,
            Lemma.lemma_id != lem.lemma_id,
            Lemma.canonical_lemma_id.is_(None),
        ).first()

        lem.lemma_ar_bare = new_bare
        # Also clean the diacritized form if it starts with ال
        if lem.lemma_ar and strip_diacritics(lem.lemma_ar).startswith('ال'):
            lem.lemma_ar = new_bare  # Use the cleaned bare as fallback
        fixed += 1

        if existing:
            lem.canonical_lemma_id = existing.lemma_id
            variants_marked += 1
            print(f"  [{lem.lemma_id}] → variant of [{existing.lemma_id}] {existing.lemma_ar_bare}")

    db.commit()
    print(f"\nApplied: {fixed} cleaned, {variants_marked} marked as variants.")
    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix dirty lemma bare forms (LLM-powered)")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry run)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    dry_run = not args.apply
    if dry_run:
        print("=== DRY RUN (use --apply to commit) ===\n")
    else:
        print("=== APPLYING CHANGES ===\n")

    cleanup(dry_run=dry_run)
