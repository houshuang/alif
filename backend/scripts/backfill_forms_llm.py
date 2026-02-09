"""Backfill forms_json on lemmas using LLM.

Generates morphological forms (plural, present tense, masdar, etc.)
for lemmas that don't have forms_json set.

Usage:
    cd backend && python scripts/backfill_forms_llm.py [--dry-run] [--limit N] [--batch N]
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import Lemma
from app.services.llm import generate_completion

FORMS_SYSTEM_PROMPT = """\
You are an Arabic morphology expert. Given an Arabic word with its POS and meaning, \
return its key morphological forms as JSON.

For verbs, return:
- "present": the present/imperfect 3rd person masculine singular (e.g. يَكْتُبُ)
- "masdar": the verbal noun (e.g. كِتَابَة)
- "active_participle": the active participle (e.g. كَاتِب)
- "verb_form": the verb form number as Roman numeral (I, II, III, IV, V, VI, VII, VIII, IX, X)

For nouns, return:
- "plural": the most common plural form with full diacritics
- "gender": "m" or "f"

For adjectives, return:
- "feminine": the feminine form (e.g. كَبِيرَة)
- "plural": the most common plural form
- "elative": the comparative/superlative form if it exists (e.g. أَكْبَر)

Always include full diacritics on Arabic text. Only include fields you are confident about. \
Return empty object {} if the word doesn't have meaningful forms (particles, pronouns, etc.)."""


def backfill_forms(lemma_ar: str, pos: str | None, gloss_en: str | None) -> dict:
    parts = [f"Arabic: {lemma_ar}"]
    if pos:
        parts.append(f"POS: {pos}")
    if gloss_en:
        parts.append(f"English: {gloss_en}")

    prompt = "Return the morphological forms for this Arabic word:\n\n" + "\n".join(parts)

    result = generate_completion(
        prompt=prompt,
        system_prompt=FORMS_SYSTEM_PROMPT,
        json_mode=True,
        temperature=0.1,
    )

    # Validate: only keep known keys with string values
    valid_keys = {"gender", "plural", "present", "masdar", "active_participle",
                  "verb_form", "feminine", "elative"}
    cleaned = {}
    for k, v in result.items():
        if k in valid_keys and isinstance(v, str) and v.strip():
            cleaned[k] = v.strip()
    return cleaned


def main():
    parser = argparse.ArgumentParser(description="Backfill forms_json using LLM")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Max lemmas (0=all)")
    parser.add_argument("--batch", type=int, default=50, help="Commit every N lemmas")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = (
            db.query(Lemma)
            .filter(Lemma.forms_json.is_(None))
            .filter(Lemma.pos.in_(["noun", "verb", "adj", "adjective"]))
        )
        if args.limit:
            query = query.limit(args.limit)
        lemmas = query.all()

        print(f"Found {len(lemmas)} lemmas without forms_json")
        updated = 0
        errors = 0

        for i, lemma in enumerate(lemmas, 1):
            try:
                forms = backfill_forms(lemma.lemma_ar, lemma.pos, lemma.gloss_en)
                if forms:
                    print(f"[{i}/{len(lemmas)}] {lemma.lemma_ar} ({lemma.pos}, {lemma.gloss_en}): {json.dumps(forms, ensure_ascii=False)}")
                    if not args.dry_run:
                        lemma.forms_json = forms
                    updated += 1
                else:
                    print(f"[{i}/{len(lemmas)}] {lemma.lemma_ar}: no forms")

                if not args.dry_run and i % args.batch == 0:
                    db.commit()
                    print(f"  Committed batch ({updated} updated so far)")

                time.sleep(0.3)
            except Exception as e:
                print(f"[{i}/{len(lemmas)}] {lemma.lemma_ar}: ERROR {e}")
                errors += 1
                continue

        if not args.dry_run:
            db.commit()

        print(f"\nDone. Updated: {updated}, Errors: {errors}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
