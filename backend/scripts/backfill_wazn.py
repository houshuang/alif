"""Backfill wazn (morphological pattern) for lemmas.

Phase 1: Extract from existing etymology_json.pattern data (no LLM needed).
Phase 2: Use LLM to classify remaining lemmas that have roots but no wazn.

Usage:
    cd backend && python3 scripts/backfill_wazn.py [--dry-run] [--batch-size=10] [--limit=500] [--phase=1|2|both]
"""

import json
import sys
import os
import re
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from app.models import Lemma, Root
from app.services.activity_log import log_activity

# Normalized wazn values — map common variants to canonical forms
WAZN_NORMALIZE = {
    # Active participle
    "fa'il": "fa'il",
    "faa'il": "fa'il",
    "fā'il": "fa'il",
    # Passive participle
    "maf'ul": "maf'ul",
    "maf'ūl": "maf'ul",
    "maf'uul": "maf'ul",
    # Place/time noun
    "maf'al": "maf'al",
    "maf'ala": "maf'ala",
    # Instrument noun
    "mif'al": "mif'al",
    "mif'ala": "mif'ala",
    "mif'āl": "mif'al",
    # Verbal noun patterns
    "fi'ala": "fi'ala",
    "fi'āla": "fi'ala",
    "fu'ul": "fu'ul",
    "fu'ūl": "fu'ul",
    "fa'l": "fa'l",
    "fi'l": "fi'l",
    "fu'l": "fu'l",
    "fa'al": "fa'al",
    "fa'ala": "fa'ala",
    "taf'il": "taf'il",
    "taf'īl": "taf'il",
    # Intensive/profession
    "fa''al": "fa''al",
    "fa'il": "fa'il",
    "fa'iil": "fa'iil",
    "fa'īl": "fa'iil",
    # Broken plurals
    "af'al": "af'al",
    "af'ila": "af'ila",
    "fu'ala": "fu'ala",
    "fu'alaa'": "fu'ala'",
    "fi'al": "fi'al",
    # Verb forms
    "fa'ala": "form_1",
    "if'al": "form_1",
    "fa''ala": "form_2",
    "taf'il": "taf'il",
    "faa'ala": "form_3",
    "fa'ala (form III)": "form_3",
    "mufaa'ala": "mufaa'ala",
    "af'ala": "form_4",
    "if'aal": "if'aal",
    "tafa''ala": "form_5",
    "tafa'ala": "form_5",
    "tafaa'ala": "form_6",
    "infa'ala": "form_7",
    "ifta'ala": "form_8",
    "if'alla": "form_9",
    "istaf'ala": "form_10",
    # Nisba/relational
    "fa'li": "nisba",
    "fa'liyy": "nisba",
    # Diminutive
    "fu'ayl": "fu'ayl",
    # Elative
    "af'al (elative)": "af'al",
}

# Wazn meanings for common patterns
WAZN_MEANINGS = {
    "fa'il": "doer/agent (active participle)",
    "maf'ul": "object/patient (passive participle)",
    "maf'al": "place/time of action",
    "maf'ala": "place of action",
    "mif'al": "instrument/tool",
    "mif'ala": "instrument/tool",
    "fa'iil": "intensive adjective",
    "fu'ul": "verbal noun (plural-like)",
    "fi'ala": "verbal noun (profession/craft)",
    "fa'l": "verbal noun (action)",
    "fi'l": "verbal noun",
    "fu'l": "verbal noun",
    "fa'al": "verbal noun",
    "taf'il": "verbal noun of form II (causative/intensive)",
    "fa''al": "intensive/habitual doer",
    "af'al": "elative/comparative",
    "fu'ala'": "broken plural",
    "fu'ayl": "diminutive",
    "nisba": "relational adjective",
    "form_1": "basic verb (form I)",
    "form_2": "causative/intensive verb (form II)",
    "form_3": "reciprocal verb (form III)",
    "form_4": "causative verb (form IV)",
    "form_5": "reflexive of form II (form V)",
    "form_6": "reciprocal reflexive (form VI)",
    "form_7": "passive/reflexive (form VII)",
    "form_8": "reflexive (form VIII)",
    "form_9": "colors/defects (form IX)",
    "form_10": "requestive/estimative (form X)",
    "if'aal": "verbal noun of form IV",
    "mufaa'ala": "verbal noun of form III",
}


def normalize_wazn(raw_pattern: str) -> str | None:
    """Normalize a raw pattern string to a canonical wazn value."""
    if not raw_pattern:
        return None

    cleaned = raw_pattern.strip().lower()

    # Direct lookup
    if cleaned in WAZN_NORMALIZE:
        return WAZN_NORMALIZE[cleaned]

    # Check for form_N pattern
    form_match = re.match(r"form[_ ](\d+)", cleaned, re.IGNORECASE)
    if form_match:
        n = int(form_match.group(1))
        if 1 <= n <= 10:
            return f"form_{n}"

    # If already looks like a normalized value, accept it
    if cleaned in WAZN_MEANINGS:
        return cleaned

    return None


def phase1_extract_from_etymology(dry_run=False):
    """Extract wazn from existing etymology_json.pattern data."""
    db = SessionLocal()

    lemmas = (
        db.query(Lemma)
        .filter(
            Lemma.wazn.is_(None),
            Lemma.etymology_json.isnot(None),
            Lemma.canonical_lemma_id.is_(None),
        )
        .all()
    )

    print(f"Phase 1: Found {len(lemmas)} lemmas with etymology but no wazn")

    updated = 0
    skipped = 0

    for lemma in lemmas:
        etym = lemma.etymology_json
        if not isinstance(etym, dict):
            continue

        raw_pattern = etym.get("pattern")
        raw_meaning = etym.get("pattern_meaning")

        if not raw_pattern:
            continue

        normalized = normalize_wazn(raw_pattern)
        if not normalized:
            print(f"  {lemma.lemma_id} {lemma.lemma_ar_bare}: unknown pattern '{raw_pattern}', skipping")
            skipped += 1
            continue

        meaning = raw_meaning or WAZN_MEANINGS.get(normalized)

        print(f"  {lemma.lemma_id} {lemma.lemma_ar_bare}: {raw_pattern} -> {normalized}")
        if not dry_run:
            lemma.wazn = normalized
            if meaning:
                lemma.wazn_meaning = meaning

        updated += 1

    if not dry_run:
        db.commit()
        if updated > 0:
            log_activity(
                db,
                event_type="wazn_backfill_completed",
                summary=f"Phase 1: Extracted wazn from etymology for {updated} lemmas",
                detail={"phase": 1, "updated": updated, "skipped": skipped},
            )
    else:
        db.rollback()

    print(f"Phase 1 {'(dry run)' if dry_run else ''}: {updated} updated, {skipped} unknown patterns")
    db.close()
    return updated


LLM_SYSTEM_PROMPT = """You are an Arabic morphology expert. For each word, identify its morphological pattern (wazn/وزن).

Use these EXACT normalized pattern names:
- Verb forms: form_1, form_2, form_3, form_4, form_5, form_6, form_7, form_8, form_9, form_10
- Active participle: fa'il
- Passive participle: maf'ul
- Place/time noun: maf'al or maf'ala
- Instrument noun: mif'al or mif'ala
- Verbal nouns: fa'l, fi'l, fu'l, fa'al, fi'ala, fu'ul, taf'il, if'aal, mufaa'ala
- Intensive/profession: fa''al
- Intensive adjective: fa'iil
- Elative/comparative: af'al
- Relational adjective: nisba
- Diminutive: fu'ayl

Return null for pattern if:
- The word is a function word (pronoun, preposition, conjunction, particle)
- The word is a loanword with no Arabic morphological pattern
- The word has an irregular/unpatterned form

Return JSON array: [{"lemma_id": 1, "wazn": "fa'il", "wazn_meaning": "doer/agent (active participle)"}]
Use null for wazn and wazn_meaning if not applicable."""


def phase2_llm_classify(dry_run=False, batch_size=10, limit=500):
    """Use LLM to classify remaining lemmas without wazn."""
    from app.services.llm import generate_completion, AllProvidersFailed

    db = SessionLocal()

    missing = (
        db.query(Lemma)
        .filter(
            Lemma.wazn.is_(None),
            Lemma.root_id.isnot(None),
            Lemma.canonical_lemma_id.is_(None),
        )
        .order_by(Lemma.frequency_rank.asc().nullslast())
        .limit(limit)
        .all()
    )

    print(f"\nPhase 2: Found {len(missing)} lemmas with root but no wazn (limit={limit})")
    if not missing:
        db.close()
        return 0

    root_ids = {l.root_id for l in missing if l.root_id}
    roots_by_id = {}
    if root_ids:
        for root in db.query(Root).filter(Root.root_id.in_(root_ids)).all():
            roots_by_id[root.root_id] = root

    total_done = 0
    total_skipped = 0
    total_null = 0

    for i in range(0, len(missing), batch_size):
        batch = missing[i : i + batch_size]
        batch_num = i // batch_size + 1
        print(f"\nBatch {batch_num}: {len(batch)} words")

        lines = []
        for lemma in batch:
            root = roots_by_id.get(lemma.root_id)
            pos_hint = f", pos={lemma.pos}" if lemma.pos else ""
            gloss = f', meaning="{lemma.gloss_en}"' if lemma.gloss_en else ""
            root_info = f", root={root.root}" if root else ""
            lines.append(f"- lemma_id={lemma.lemma_id}, word={lemma.lemma_ar_bare}{pos_hint}{gloss}{root_info}")

        prompt = f"Classify the morphological pattern (wazn) for each word:\n\n" + "\n".join(lines)

        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt=LLM_SYSTEM_PROMPT,
                json_mode=True,
                temperature=0.2,
            )
        except AllProvidersFailed as e:
            print(f"  LLM failed: {e}")
            continue

        items = result if isinstance(result, list) else result.get("words", [])
        if not isinstance(items, list):
            print(f"  Unexpected response format: {type(result)}")
            continue

        lemma_map = {l.lemma_id: l for l in batch}
        for item in items:
            lid = item.get("lemma_id")
            wazn = item.get("wazn")
            wazn_meaning = item.get("wazn_meaning")

            if lid not in lemma_map:
                continue

            lemma = lemma_map[lid]

            if wazn is None:
                print(f"  {lid} {lemma.lemma_ar_bare}: no pattern (function/loanword)")
                total_null += 1
                continue

            # Normalize
            normalized = normalize_wazn(wazn)
            if not normalized:
                normalized = wazn  # Accept LLM's value if not in our map

            meaning = wazn_meaning or WAZN_MEANINGS.get(normalized)

            print(f"  {lid} {lemma.lemma_ar_bare}: {normalized}")
            if not dry_run:
                lemma.wazn = normalized
                if meaning:
                    lemma.wazn_meaning = meaning
            total_done += 1

        if not dry_run:
            db.commit()

        time.sleep(1)

    if dry_run:
        db.rollback()
        print(f"\nPhase 2 (dry run): would update {total_done} ({total_skipped} invalid, {total_null} no-pattern)")
    else:
        print(f"\nPhase 2: updated {total_done} ({total_skipped} invalid, {total_null} no-pattern)")
        if total_done > 0:
            log_activity(
                db,
                event_type="wazn_backfill_completed",
                summary=f"Phase 2: LLM classified wazn for {total_done} lemmas",
                detail={"phase": 2, "updated": total_done, "skipped": total_skipped, "null_entries": total_null},
            )

    db.close()
    return total_done


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    phase = "both"
    batch_size = 10
    limit = 500

    for arg in sys.argv:
        if arg.startswith("--phase="):
            phase = arg.split("=")[1]
        elif arg.startswith("--batch-size="):
            batch_size = int(arg.split("=")[1])
        elif arg.startswith("--limit="):
            limit = int(arg.split("=")[1])

    if phase in ("1", "both"):
        phase1_extract_from_etymology(dry_run=dry_run)

    if phase in ("2", "both"):
        phase2_llm_classify(dry_run=dry_run, batch_size=batch_size, limit=limit)
