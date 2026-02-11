"""Clean up garbage roots created by OCR imports.

Finds roots with invalid formats (#, Latin letters, wrong length) and
uses LLM to reassign their lemmas to correct roots.

Usage:
    cd backend && python scripts/cleanup_bad_roots.py [--dry-run] [--merge]
"""

import json
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Lemma, Root, UserLemmaKnowledge, ReviewLog, SentenceWord, StoryWord
from app.services.llm import generate_completion
from app.services.morphology import is_valid_root


def find_bad_roots(db):
    bad = []
    for r in db.query(Root).all():
        if not is_valid_root(r.root):
            count = db.query(Lemma).filter(Lemma.root_id == r.root_id).count()
            bad.append((r, count))
    return bad


def classify_lemmas_llm(lemmas: list[dict]) -> list[dict]:
    """Use LLM to get correct root and POS for a batch of lemmas."""
    items = []
    for l in lemmas:
        items.append(f'  {{"id": {l["id"]}, "arabic": "{l["arabic"]}", "english": "{l["english"]}", "current_pos": "{l["pos"]}"}}')

    prompt = f"""For each Arabic word below, provide:
1. The correct Arabic trilateral/quadrilateral root in dot-separated format (e.g. "ك.ت.ب")
2. The correct POS: noun, verb, adj, adv, prep, pron, conj, part, or noun_prop (only for actual proper nouns like names/places)
3. The base dictionary form (lemma) without clitics/prefixes — e.g. "مُرِيحَة" → "مُرِيح", "وَالْمَسْجِد" → "مَسْجِد"

Words:
[
{chr(10).join(items)}
]

Return a JSON array with objects: {{"id": ..., "root": "X.Y.Z", "pos": "...", "base_lemma": "..."}}
Only use noun_prop for actual proper names (people, countries). Words like "comfortable" or "many" should be adj/noun."""

    result = generate_completion(
        prompt=prompt,
        system_prompt="You are an Arabic morphology expert. Return valid JSON only.",
        json_mode=True,
        temperature=0.0,
    )

    if isinstance(result, list):
        return result
    if isinstance(result, dict) and "words" in result:
        return result["words"]
    if isinstance(result, dict) and "results" in result:
        return result["results"]
    return []


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Clean up garbage roots")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--merge", action="store_true", help="Merge review data into canonical lemmas")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        bad_roots = find_bad_roots(db)
        print(f"Found {len(bad_roots)} bad roots")

        # Collect all affected lemmas
        all_lemmas = []
        for root, count in bad_roots:
            if count == 0:
                print(f"  Deleting empty root: id={root.root_id} \"{root.root}\"")
                if not args.dry_run:
                    db.delete(root)
                continue
            lemmas = db.query(Lemma).filter(Lemma.root_id == root.root_id).all()
            for l in lemmas:
                all_lemmas.append({
                    "id": l.lemma_id,
                    "arabic": l.lemma_ar,
                    "english": l.gloss_en or "",
                    "pos": l.pos or "",
                    "root_id": root.root_id,
                    "root_str": root.root,
                })

        if not all_lemmas:
            print("No lemmas to fix")
            if not args.dry_run:
                db.commit()
            return

        print(f"\n{len(all_lemmas)} lemmas need fixing. Classifying via LLM...")

        # Process in batches of 20
        batch_size = 20
        all_results = []
        for i in range(0, len(all_lemmas), batch_size):
            batch = all_lemmas[i:i + batch_size]
            print(f"  Batch {i // batch_size + 1}: {len(batch)} lemmas...")
            try:
                results = classify_lemmas_llm(batch)
                all_results.extend(results)
                time.sleep(0.5)
            except Exception as e:
                print(f"  LLM error: {e}")
                continue

        # Build lookup by ID
        result_map = {r["id"]: r for r in all_results if isinstance(r, dict) and "id" in r}
        print(f"\nGot LLM results for {len(result_map)}/{len(all_lemmas)} lemmas")

        # Apply fixes
        fixed = 0
        variants_found = 0
        for lemma_data in all_lemmas:
            lid = lemma_data["id"]
            llm = result_map.get(lid)
            if not llm:
                print(f"  SKIP {lemma_data['arabic']}: no LLM result")
                continue

            new_root_str = llm.get("root", "")
            new_pos = llm.get("pos", "")
            base_lemma_ar = llm.get("base_lemma", "")

            if not is_valid_root(new_root_str):
                print(f"  SKIP {lemma_data['arabic']}: LLM root \"{new_root_str}\" also invalid")
                continue

            lemma = db.query(Lemma).filter(Lemma.lemma_id == lid).first()
            if not lemma:
                continue

            # Check if base_lemma matches an existing lemma
            canonical = None
            if base_lemma_ar and base_lemma_ar != lemma.lemma_ar:
                from app.services.sentence_validator import strip_diacritics
                base_bare = strip_diacritics(base_lemma_ar)
                candidates = db.query(Lemma).filter(
                    Lemma.lemma_ar_bare == base_bare,
                    Lemma.lemma_id != lid,
                    Lemma.canonical_lemma_id.is_(None),
                ).all()
                if candidates:
                    canonical = candidates[0]

            # Find or create correct root
            existing_root = db.query(Root).filter(Root.root == new_root_str).first()

            action_parts = []
            if canonical:
                action_parts.append(f"variant of {canonical.lemma_ar} (id={canonical.lemma_id})")
                variants_found += 1
            if new_pos and new_pos != lemma.pos:
                action_parts.append(f"pos: {lemma.pos} → {new_pos}")
            action_parts.append(f"root: \"{lemma_data['root_str']}\" → \"{new_root_str}\"")

            print(f"  FIX {lemma.lemma_ar} ({lemma.gloss_en}): {', '.join(action_parts)}")

            if not args.dry_run:
                # Assign correct root
                if existing_root:
                    lemma.root_id = existing_root.root_id
                else:
                    new_root = Root(root=new_root_str, core_meaning_en="")
                    db.add(new_root)
                    db.flush()
                    lemma.root_id = new_root.root_id

                # Fix POS
                if new_pos:
                    lemma.pos = new_pos

                # Mark as variant if found
                if canonical:
                    lemma.canonical_lemma_id = canonical.lemma_id

            fixed += 1

        # Delete now-orphaned bad roots
        if not args.dry_run:
            db.flush()
            orphaned = 0
            for root, _ in bad_roots:
                remaining = db.query(Lemma).filter(Lemma.root_id == root.root_id).count()
                if remaining == 0:
                    db.delete(root)
                    orphaned += 1
            db.commit()
            print(f"\nDeleted {orphaned} orphaned bad roots")

        print(f"\nDone. Fixed: {fixed}, Variants found: {variants_found}")
        if args.dry_run:
            print("(dry run — no changes applied)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
