"""Backfill forms_json on lemmas from Wiktionary JSONL data.

Extracts key morphological forms per POS:
  - Nouns: plural, gender
  - Verbs: present (non-past), masdar (noun-from-verb), active participle, verb form
  - Adjectives: feminine, elative (comparative), plural

Usage:
    python scripts/backfill_forms.py [--wiktionary PATH] [--dry-run]
"""

import argparse
import gzip
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# Allow running from backend/ or project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import Lemma


DIACRITIC_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")


def strip_diacritics(text: str) -> str:
    return DIACRITIC_RE.sub("", text)


def load_wiktionary_index(path: str) -> dict[str, list[dict]]:
    """Load Wiktionary JSONL and index by bare word form."""
    index: dict[str, list[dict]] = defaultdict(list)
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            word = entry.get("word", "")
            bare = strip_diacritics(word)
            if bare:
                index[bare].append(entry)
    return index


def _first_form_with_tag(forms: list[dict], tag: str) -> str | None:
    """Find the first form that has exactly the given tag (not in declension)."""
    for f in forms:
        tags = set(f.get("tags", []))
        source = f.get("source", "")
        if source in ("declension", "conjugation"):
            continue
        if tag in tags:
            return f.get("form")
    return None


def _first_form_with_tags(forms: list[dict], required_tags: set[str]) -> str | None:
    """Find first form matching all required tags (not from declension tables)."""
    for f in forms:
        tags = set(f.get("tags", []))
        source = f.get("source", "")
        if source in ("declension", "conjugation"):
            continue
        if required_tags.issubset(tags):
            return f.get("form")
    return None


def _get_verb_form(forms: list[dict]) -> str | None:
    """Extract verb form number (I, II, ..., X) from canonical tag."""
    form_map = {
        "form-i": "I", "form-ii": "II", "form-iii": "III",
        "form-iv": "IV", "form-v": "V", "form-vi": "VI",
        "form-vii": "VII", "form-viii": "VIII", "form-ix": "IX",
        "form-x": "X",
    }
    for f in forms:
        tags = set(f.get("tags", []))
        if "canonical" in tags:
            for tag, label in form_map.items():
                if tag in tags:
                    return label
    return None


def _get_gender(forms: list[dict]) -> str | None:
    """Extract gender from canonical form tags."""
    for f in forms:
        tags = set(f.get("tags", []))
        if "canonical" in tags:
            if "feminine" in tags:
                return "f"
            if "masculine" in tags:
                return "m"
    return None


def extract_noun_forms(entry: dict) -> dict:
    forms = entry.get("forms") or []
    result = {}

    gender = _get_gender(forms)
    if gender:
        result["gender"] = gender

    plural = _first_form_with_tag(forms, "plural")
    if plural:
        result["plural"] = plural

    return result


def extract_verb_forms(entry: dict) -> dict:
    forms = entry.get("forms") or []
    result = {}

    verb_form = _get_verb_form(forms)
    if verb_form:
        result["verb_form"] = verb_form

    present = _first_form_with_tag(forms, "non-past")
    if present:
        result["present"] = present

    masdar = _first_form_with_tag(forms, "noun-from-verb")
    if masdar:
        result["masdar"] = masdar

    active_part = _first_form_with_tags(forms, {"active", "participle"})
    if active_part:
        result["active_participle"] = active_part

    return result


def extract_adj_forms(entry: dict) -> dict:
    forms = entry.get("forms") or []
    result = {}

    feminine = _first_form_with_tag(forms, "feminine")
    if feminine:
        result["feminine"] = feminine

    elative = _first_form_with_tag(forms, "elative")
    if elative:
        result["elative"] = elative

    plural = _first_form_with_tag(forms, "plural")
    if plural:
        result["plural"] = plural

    return result


def find_best_entry(
    entries: list[dict], lemma_pos: str | None, lemma_ar: str
) -> dict | None:
    """Pick the best Wiktionary entry for a lemma, matching POS and diacritized form."""
    pos_map = {
        "noun": "noun",
        "verb": "verb",
        "adj": "adj",
        "adjective": "adj",
        "adv": "adv",
        "adverb": "adv",
        "prep": "prep",
        "preposition": "prep",
        "pron": "pron",
        "pronoun": "pron",
        "particle": "particle",
        "conj": "conj",
        "conjunction": "conj",
    }
    target_pos = pos_map.get((lemma_pos or "").lower(), (lemma_pos or "").lower())
    lemma_bare = strip_diacritics(lemma_ar)

    # Filter to matching POS
    pos_matches = [e for e in entries if e.get("pos") == target_pos]
    if not pos_matches:
        pos_matches = entries

    if len(pos_matches) == 1:
        return pos_matches[0]

    # Try to match by diacritized canonical form
    for entry in pos_matches:
        for f in (entry.get("forms") or []):
            if "canonical" in f.get("tags", []):
                if strip_diacritics(f.get("form", "")) == lemma_bare:
                    return entry

    # Try matching the entry word itself
    for entry in pos_matches:
        if strip_diacritics(entry.get("word", "")) == lemma_bare:
            return entry

    return pos_matches[0] if pos_matches else None


def backfill(wiktionary_path: str, dry_run: bool = False) -> None:
    print(f"Loading Wiktionary data from {wiktionary_path}...")
    wikt_index = load_wiktionary_index(wiktionary_path)
    print(f"  Indexed {len(wikt_index)} unique bare forms")

    db = SessionLocal()
    try:
        lemmas = db.query(Lemma).all()
        print(f"  Found {len(lemmas)} lemmas in DB")

        updated = 0
        matched = 0
        for lemma in lemmas:
            bare = lemma.lemma_ar_bare
            if not bare:
                continue

            # Strip leading ال for lookup
            lookup_bare = bare
            if lookup_bare.startswith("ال"):
                lookup_bare = lookup_bare[2:]

            entries = wikt_index.get(lookup_bare, [])
            if not entries:
                continue

            matched += 1
            entry = find_best_entry(entries, lemma.pos, lemma.lemma_ar)
            if not entry:
                continue

            pos = (lemma.pos or "").lower()
            if pos in ("noun",):
                forms = extract_noun_forms(entry)
            elif pos in ("verb",):
                forms = extract_verb_forms(entry)
            elif pos in ("adj", "adjective"):
                forms = extract_adj_forms(entry)
            else:
                forms = extract_noun_forms(entry)

            if not forms:
                continue

            if dry_run:
                print(f"  {lemma.lemma_ar} ({lemma.pos}): {json.dumps(forms, ensure_ascii=False)}")
            else:
                lemma.forms_json = forms

            updated += 1

        if not dry_run:
            db.commit()
            print(f"Updated {updated}/{len(lemmas)} lemmas ({matched} matched in Wiktionary)")
        else:
            print(f"Would update {updated}/{len(lemmas)} lemmas ({matched} matched in Wiktionary)")

    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill forms_json from Wiktionary")
    parser.add_argument(
        "--wiktionary",
        default="/app/data/wiktionary_arabic.jsonl.gz",
        help="Path to wiktionary_arabic.jsonl.gz",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    args = parser.parse_args()
    backfill(args.wiktionary, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
