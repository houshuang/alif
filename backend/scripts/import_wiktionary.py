#!/usr/bin/env python3
"""Import high-frequency Arabic words from kaikki.org Wiktionary extract.

Downloads the Arabic JSONL (~385MB), filters to nouns/verbs/adjectives with
English glosses, and imports the top N most useful entries not already in DB.

Usage:
    python scripts/import_wiktionary.py                     # import top 1000
    python scripts/import_wiktionary.py --limit 2000        # import top 2000
    python scripts/import_wiktionary.py --dry-run            # preview without writing
    python scripts/import_wiktionary.py --skip-download      # reuse cached file
"""

import argparse
import gzip
import json
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, Base, engine
from app.models import Root, Lemma
from app.services.ocr_service import validate_gloss
from app.services.sentence_validator import (
    strip_diacritics as _sv_strip_diacritics,
    normalize_alef,
    build_lemma_lookup,
    resolve_existing_lemma,
)
from app.services.variant_detection import detect_variants_llm, detect_definite_variants, mark_variants

WIKTIONARY_URL = "https://kaikki.org/dictionary/Arabic/kaikki.org-dictionary-Arabic.jsonl"
CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "wiktionary_arabic.jsonl.gz"

WANTED_POS = {"noun", "verb", "adj"}

# Map kaikki POS to our POS tags
POS_MAP = {
    "noun": "noun",
    "verb": "verb",
    "adj": "adj",
    "adv": "adv",
    "name": "name",
    "pron": "pron",
    "prep": "prep",
    "conj": "conj",
    "particle": "particle",
    "num": "num",
    "intj": "particle",
}


def strip_diacritics(text: str) -> str:
    return _sv_strip_diacritics(text)


def download_wiktionary(cache_path: Path) -> None:
    """Stream-download the JSONL and compress to cache."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {WIKTIONARY_URL}...")
    print("(This is ~385MB, may take a few minutes)")

    with httpx.stream("GET", WIKTIONARY_URL, timeout=600.0, follow_redirects=True) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0

        with gzip.open(cache_path, "wt", encoding="utf-8") as f:
            for chunk in resp.iter_text(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk.encode())
                if total > 0:
                    pct = downloaded * 100 // total
                    print(f"\r  {downloaded // (1024*1024)}MB / {total // (1024*1024)}MB ({pct}%)", end="", flush=True)

    print(f"\n  Saved to {cache_path}")


def extract_root(entry: dict) -> str | None:
    """Try to extract Arabic root from etymology_templates."""
    for tmpl in entry.get("etymology_templates", []):
        if tmpl.get("name") in ("ar-root", "ar-rootbox"):
            args = tmpl.get("args", {})
            # Root radicals are often in positional args
            radicals = []
            for i in range(1, 6):
                r = args.get(str(i), "")
                if r and r != "ar":
                    radicals.append(r)
            if len(radicals) >= 3:
                return ".".join(radicals)
    return None


def extract_gloss(entry: dict) -> str | None:
    """Get the first English gloss from senses, validated for conciseness."""
    for sense in entry.get("senses", []):
        glosses = sense.get("glosses", [])
        for g in glosses:
            if g and not g.startswith("(") and len(g) < 200:
                cleaned = validate_gloss(g)
                if cleaned:
                    return cleaned
                # Fall through to next gloss if this one was verbose
    return None


def parse_entries(cache_path: Path) -> list[dict]:
    """Parse the JSONL and extract candidate words."""
    candidates = []
    seen_bare = set()

    opener = gzip.open if str(cache_path).endswith(".gz") else open
    with opener(cache_path, "rt", encoding="utf-8") as f:
        for line_num, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            word = entry.get("word", "")
            pos = entry.get("pos", "").lower()

            # Only nouns, verbs, adjectives
            if pos not in WANTED_POS:
                continue

            # Sanitize: strip punctuation, reject multi-word
            from app.services.sentence_validator import sanitize_arabic_word
            word, san_warnings = sanitize_arabic_word(word)
            if not word or "multi_word" in san_warnings or "too_short" in san_warnings:
                continue

            bare = strip_diacritics(word)

            # Deduplicate by bare form
            if bare in seen_bare:
                continue

            gloss = extract_gloss(entry)
            if not gloss:
                continue

            root_str = extract_root(entry)

            seen_bare.add(bare)
            candidates.append({
                "arabic": word,
                "bare": bare,
                "gloss": gloss,
                "pos": POS_MAP.get(pos, pos),
                "root": root_str,
            })

            if line_num % 10000 == 0 and line_num > 0:
                print(f"\r  Parsed {line_num} lines, {len(candidates)} candidates...", end="", flush=True)

    print(f"\n  Total candidates: {len(candidates)}")
    return candidates


def run_import(db, candidates: list[dict], limit: int, dry_run: bool = False) -> dict:
    # Build clitic-aware lemma lookup from all existing lemmas
    all_lemmas = db.query(Lemma).all()
    existing_bare = {normalize_alef(lem.lemma_ar_bare) for lem in all_lemmas}
    lemma_lookup = build_lemma_lookup(all_lemmas)
    existing_roots = {r.root: r for r in db.query(Root).all()}

    # Filter out already-existing words (exact bare match + clitic-aware lookup)
    new_candidates = [
        c for c in candidates
        if normalize_alef(c["bare"]) not in existing_bare
        and not resolve_existing_lemma(c["bare"], lemma_lookup)
    ]
    print(f"After filtering existing: {len(new_candidates)} new candidates (from {len(candidates)})")

    # Take top N (the JSONL is roughly ordered by frequency/importance)
    to_import = new_candidates[:limit]

    imported = 0
    roots_created = 0
    new_lemmas: list[Lemma] = []

    for c in to_import:
        if dry_run:
            print(f"  [dry-run] {c['arabic']} ({c['gloss']}) — {c['pos']}")
            imported += 1
            continue

        # Handle root
        root_id = None
        if c["root"]:
            if c["root"] in existing_roots:
                root_id = existing_roots[c["root"]].root_id
            else:
                root_obj = Root(root=c["root"])
                db.add(root_obj)
                db.flush()
                existing_roots[c["root"]] = root_obj
                root_id = root_obj.root_id
                roots_created += 1

        lemma = Lemma(
            lemma_ar=c["arabic"],
            lemma_ar_bare=c["bare"],
            gloss_en=c["gloss"],
            pos=c["pos"],
            root_id=root_id,
            source="wiktionary",
        )
        db.add(lemma)
        new_lemmas.append(lemma)
        imported += 1

    # Detect and mark variants among newly imported lemmas
    variants_marked = 0
    if not dry_run:
        if new_lemmas:
            db.flush()
            new_ids = [l.lemma_id for l in new_lemmas]
            camel_vars = detect_variants_llm(db, lemma_ids=new_ids)
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
        "skipped_existing": len(candidates) - len(new_candidates),
        "roots_created": roots_created,
        "variants_marked": variants_marked,
        "limit": limit,
    }
    print(f"\nImported {imported} words, created {roots_created} roots, marked {variants_marked} variants")
    return result


def main():
    parser = argparse.ArgumentParser(description="Import Arabic words from Wiktionary")
    parser.add_argument("--limit", type=int, default=1000, help="Max words to import (default: 1000)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-download", action="store_true", help="Use cached file")
    args = parser.parse_args()

    if not args.skip_download or not CACHE_FILE.exists():
        download_wiktionary(CACHE_FILE)
    else:
        print(f"Using cached file: {CACHE_FILE}")

    print("Parsing entries...")
    candidates = parse_entries(CACHE_FILE)

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        result = run_import(db, candidates, limit=args.limit, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
