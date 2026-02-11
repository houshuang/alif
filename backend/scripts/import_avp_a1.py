#!/usr/bin/env python3
"""Import AVP A1 dataset (Arabic Vocabulary Profile, A1 level).

Downloads vocabulary from https://lailafamiliar.github.io/A1-AVP-dataset/
and imports into the Alif database as Lemma records (no FSRS cards — just
available vocabulary for the learn-mode word selector to pick from).

Usage:
    python scripts/import_avp_a1.py
    python scripts/import_avp_a1.py --dry-run
"""

import argparse
import json
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, Base, engine
from app.models import Root, Lemma
from app.services.sentence_validator import (
    strip_diacritics as _sv_strip_diacritics,
    normalize_alef,
    build_lemma_lookup,
    resolve_existing_lemma,
)
from app.services.variant_detection import detect_variants, detect_definite_variants, mark_variants

AVP_URL = "https://lailafamiliar.github.io/A1-AVP-dataset/"

# Map AVP categories to our POS tags
CATEGORY_POS_MAP = {
    "NOUNS": "noun",
    "ADJECTIVES": "adj",
    "VERBS": "verb",
    "ADVERBS": "adv",
    "EXPRESSIONS": "expr",
    "PARTICLES & INTERJECTIONS": "particle",
    "DEMONSTRATIVE PRONOUNS": "pron",
    "CONJUNCTIONS": "conj",
    "PRONOUNS": "pron",
    "INTERROGATIVES": "interrog",
    "PREPOSITIONS": "prep",
}


def strip_diacritics(text: str) -> str:
    return _sv_strip_diacritics(text)


def fetch_vocab_data() -> dict[str, list[dict]]:
    """Download the AVP page and extract the vocabData JS object."""
    resp = httpx.get(AVP_URL, timeout=30.0)
    resp.raise_for_status()
    html = resp.text

    # Extract the vocabData object from the script
    match = re.search(r"const\s+vocabData\s*=\s*(\{.+?\});\s*\n", html, re.DOTALL)
    if not match:
        # Try alternative patterns
        match = re.search(r"vocabData\s*=\s*(\{.+?\});\s*\n", html, re.DOTALL)
    if not match:
        raise RuntimeError("Could not find vocabData in the AVP page HTML")

    js_obj = match.group(1)
    # Convert JS object to valid JSON:
    # - Add quotes around unquoted keys
    # - Handle trailing commas
    js_obj = re.sub(r'(\w[\w\s&]*)\s*:', lambda m: f'"{m.group(1).strip()}":' , js_obj)
    # Remove trailing commas before ] or }
    js_obj = re.sub(r",\s*([}\]])", r"\1", js_obj)

    try:
        return json.loads(js_obj)
    except json.JSONDecodeError:
        # Fallback: extract entries with regex
        return _extract_with_regex(html)


def _extract_with_regex(html: str) -> dict[str, list[dict]]:
    """Fallback extraction using regex for each entry."""
    result: dict[str, list[dict]] = {}
    # Find category blocks
    cat_pattern = re.compile(
        r'"([^"]+)"\s*:\s*\[(.*?)\]', re.DOTALL
    )
    entry_pattern = re.compile(
        r'\{\s*"?arabic"?\s*:\s*"([^"]+)"\s*,\s*"?english"?\s*:\s*"([^"]+)"\s*\}'
    )

    for cat_match in cat_pattern.finditer(html):
        category = cat_match.group(1)
        block = cat_match.group(2)
        entries = []
        for entry_match in entry_pattern.finditer(block):
            entries.append({
                "arabic": entry_match.group(1),
                "english": entry_match.group(2),
            })
        if entries:
            result[category] = entries

    if not result:
        raise RuntimeError("Failed to extract any vocabulary from AVP page")
    return result


def run_import(db, dry_run: bool = False) -> dict:
    print("Downloading AVP A1 dataset...")
    vocab_data = fetch_vocab_data()

    total_entries = sum(len(v) for v in vocab_data.values())
    print(f"Found {total_entries} entries across {len(vocab_data)} categories")

    # Build clitic-aware lemma lookup from all existing lemmas
    all_lemmas = db.query(Lemma).all()
    existing_bare = {normalize_alef(lem.lemma_ar_bare) for lem in all_lemmas}
    lemma_lookup = build_lemma_lookup(all_lemmas)

    imported = 0
    skipped_existing = 0
    skipped_multiword = 0
    new_lemmas: list[Lemma] = []

    for category, entries in vocab_data.items():
        pos = CATEGORY_POS_MAP.get(category, "other")

        for entry in entries:
            arabic = entry["arabic"].strip()
            english = entry["english"].strip()

            # Clean up: remove asterisks (transitive markers)
            arabic = arabic.replace("*", "").strip()

            # Sanitize: strip punctuation, reject multi-word
            from app.services.sentence_validator import sanitize_arabic_word
            arabic, san_warnings = sanitize_arabic_word(arabic)
            if not arabic or "multi_word" in san_warnings or "too_short" in san_warnings:
                skipped_multiword += 1
                continue

            bare = strip_diacritics(arabic)
            bare_norm = normalize_alef(bare)

            if bare_norm in existing_bare or resolve_existing_lemma(bare, lemma_lookup):
                skipped_existing += 1
                continue

            if dry_run:
                print(f"  [dry-run] {arabic} ({english}) — {pos}")
                imported += 1
                existing_bare.add(bare_norm)
                continue

            lemma = Lemma(
                lemma_ar=arabic,
                lemma_ar_bare=bare,
                gloss_en=english,
                pos=pos,
                source="avp_a1",
            )
            db.add(lemma)
            new_lemmas.append(lemma)
            existing_bare.add(bare_norm)
            lemma_lookup[bare_norm] = lemma.lemma_id
            imported += 1

    # Detect and mark variants among newly imported lemmas
    variants_marked = 0
    if not dry_run:
        if new_lemmas:
            db.flush()
            new_ids = [l.lemma_id for l in new_lemmas]
            camel_vars = detect_variants(db, lemma_ids=new_ids)
            already = {v[0] for v in camel_vars}
            def_vars = detect_definite_variants(db, lemma_ids=new_ids, already_variant_ids=already)
            all_vars = camel_vars + def_vars
            if all_vars:
                variants_marked = mark_variants(db, all_vars)
                for var_id, canon_id, vtype, _ in all_vars:
                    var = db.get(Lemma,var_id)
                    canon = db.get(Lemma,canon_id)
                    print(f"  Variant: {var.lemma_ar_bare} → {canon.lemma_ar_bare} [{vtype}]")
        db.commit()

    result = {
        "imported": imported,
        "skipped_existing": skipped_existing,
        "skipped_multiword": skipped_multiword,
        "variants_marked": variants_marked,
        "total_in_dataset": total_entries,
    }
    print(f"\nImported {imported} words, skipped {skipped_existing} existing, "
          f"skipped {skipped_multiword} multi-word, marked {variants_marked} variants")
    return result


def main():
    parser = argparse.ArgumentParser(description="Import AVP A1 Arabic vocabulary")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        result = run_import(db, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
