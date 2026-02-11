#!/usr/bin/env python3
"""Production cleanup: form-aware dedup + forms_json enrichment.

Three passes:
1. Re-run variant detection with hamza-aware code
2. Form-aware dedup: for each non-variant lemma, run lookup_lemma() against
   all others. If it matches a different lemma, merge it.
3. Enrich forms_json: for lemmas with known variant records, ensure the
   variant's bare form is in the canonical's forms_json.

Usage:
    python scripts/normalize_and_dedup.py --dry-run   # preview changes
    python scripts/normalize_and_dedup.py --merge      # apply merges
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import (
    Lemma,
    UserLemmaKnowledge,
    ReviewLog,
    SentenceWord,
    Sentence,
    StoryWord,
)
from app.services.activity_log import log_activity
from app.services.sentence_validator import (
    normalize_alef,
    strip_diacritics,
    build_lemma_lookup,
    resolve_existing_lemma,
)
from app.services.variant_detection import (
    detect_variants_llm,
    detect_definite_variants,
    mark_variants,
)
from app.services.morphology import CAMEL_AVAILABLE


def merge_into(db, variant_id, canonical_id, dry_run=False):
    """Merge variant into canonical: move reviews, sentence_words, etc."""
    primary = db.query(Lemma).filter(Lemma.lemma_id == canonical_id).first()
    secondary = db.query(Lemma).filter(Lemma.lemma_id == variant_id).first()
    if not primary or not secondary:
        return

    p_ulk = db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == canonical_id
    ).first()
    s_ulk = db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == variant_id
    ).first()

    # Move review logs
    s_reviews = db.query(ReviewLog).filter(ReviewLog.lemma_id == variant_id).all()
    if s_reviews:
        print(f"    Moving {len(s_reviews)} review logs")
        if not dry_run:
            for r in s_reviews:
                r.lemma_id = canonical_id

    # Move sentence_words
    s_sw = db.query(SentenceWord).filter(SentenceWord.lemma_id == variant_id).all()
    if s_sw:
        print(f"    Moving {len(s_sw)} sentence_words")
        if not dry_run:
            for sw in s_sw:
                sw.lemma_id = canonical_id

    # Move story_words
    s_stw = db.query(StoryWord).filter(StoryWord.lemma_id == variant_id).all()
    if s_stw:
        print(f"    Moving {len(s_stw)} story_words")
        if not dry_run:
            for stw in s_stw:
                stw.lemma_id = canonical_id

    # Move sentence targets
    s_sentences = db.query(Sentence).filter(
        Sentence.target_lemma_id == variant_id
    ).all()
    if s_sentences:
        print(f"    Moving {len(s_sentences)} sentence targets")
        if not dry_run:
            for s in s_sentences:
                s.target_lemma_id = canonical_id

    # Merge FSRS knowledge
    if s_ulk and p_ulk:
        s_seen = s_ulk.times_seen or 0
        p_seen = p_ulk.times_seen or 0
        p_ulk.times_seen = s_seen + p_seen
        p_ulk.times_correct = (s_ulk.times_correct or 0) + (p_ulk.times_correct or 0)
        if s_seen > p_seen and s_ulk.fsrs_card_json:
            print(f"    Using variant FSRS card (more reviews: {s_seen} vs {p_seen})")
            p_ulk.fsrs_card_json = s_ulk.fsrs_card_json
            p_ulk.knowledge_state = s_ulk.knowledge_state
            if s_ulk.last_reviewed:
                p_ulk.last_reviewed = s_ulk.last_reviewed
        if not dry_run:
            db.delete(s_ulk)
    elif s_ulk and not p_ulk:
        print(f"    Moving ULK from variant to canonical")
        if not dry_run:
            s_ulk.lemma_id = canonical_id

    # Mark as variant
    if not dry_run:
        secondary.canonical_lemma_id = canonical_id


def enrich_forms_json(db, canonical_id, variant_bare, form_key=None, dry_run=False):
    """Add variant bare form to canonical's forms_json if not already there."""
    primary = db.query(Lemma).filter(Lemma.lemma_id == canonical_id).first()
    if not primary:
        return

    forms = primary.forms_json or {}
    if isinstance(forms, str):
        forms = json.loads(forms)
    forms = dict(forms)

    # Auto-determine form_key if not provided
    if not form_key:
        form_key = f"variant_{variant_bare}"

    # Check if already stored
    for k, v in forms.items():
        v_bare = normalize_alef(strip_diacritics(v)) if isinstance(v, str) else ""
        if v_bare == normalize_alef(variant_bare):
            return  # already stored

    forms[form_key] = variant_bare
    if not dry_run:
        primary.forms_json = forms
    print(f"    Enriched forms_json: {form_key}={variant_bare}")


def main():
    dry_run = "--dry-run" in sys.argv
    do_merge = "--merge" in sys.argv

    db = SessionLocal()
    total_changes = 0

    # === PASS 1: LLM-confirmed variant detection ===
    print("=" * 60)
    print("PASS 1: VARIANT DETECTION (CAMeL + LLM confirmation)")
    print("=" * 60)

    if CAMEL_AVAILABLE:
        llm_variants = detect_variants_llm(db, verbose=True)
        already_ids = {v[0] for v in llm_variants}
        def_variants = detect_definite_variants(db, already_variant_ids=already_ids)
        all_variants = llm_variants + def_variants

        new_variants = []
        for var_id, canon_id, vtype, details in all_variants:
            var = db.get(Lemma, var_id)
            if var and var.canonical_lemma_id is None:
                new_variants.append((var_id, canon_id, vtype, details))

        print(f"\nFound {len(new_variants)} LLM-confirmed new variants")
        for var_id, canon_id, vtype, details in new_variants:
            var = db.get(Lemma, var_id)
            canon = db.get(Lemma, canon_id)
            reason = details.get("llm_reason", "")
            print(f"  {var.lemma_ar_bare} ({var.gloss_en}) → {canon.lemma_ar_bare} ({canon.gloss_en}) [{vtype}] {reason}")

        if new_variants and do_merge:
            print(f"\nApplying {len(new_variants)} variant merges...")
            for var_id, canon_id, vtype, details in new_variants:
                var = db.get(Lemma, var_id)
                canon = db.get(Lemma, canon_id)
                print(f"  Merging {var.lemma_ar_bare} → {canon.lemma_ar_bare}")
                merge_into(db, var_id, canon_id, dry_run)
            total_changes += len(new_variants)
        elif new_variants and not do_merge and not dry_run:
            variants_marked = mark_variants(db, new_variants)
            total_changes += variants_marked
            print(f"Marked {variants_marked} variants (use --merge to also move reviews/sentences)")
    else:
        print("CAMeL Tools not available, skipping morphological variant detection")

    # === PASS 2: Definite article dedup (conservative) ===
    print()
    print("=" * 60)
    print("PASS 2: AL-PREFIX DEDUP")
    print("=" * 60)

    # Only merge exact al-prefix duplicates (الكتاب→كتاب)
    # NOT hamza variants — those can be different words (سأل≠سال, أب≠آب)
    canonical_lemmas = (
        db.query(Lemma)
        .filter(Lemma.canonical_lemma_id.is_(None))
        .all()
    )
    bare_to_ids: dict[str, list[int]] = {}
    for lem in canonical_lemmas:
        bare = lem.lemma_ar_bare or ""
        bare_to_ids.setdefault(bare, []).append(lem.lemma_id)

    dedup_pairs = []
    for lemma in canonical_lemmas:
        bare = lemma.lemma_ar_bare or ""
        # Only check al-prefix: الكتاب should merge into كتاب
        if bare.startswith("ال") and len(bare) > 2:
            without_al = bare[2:]
            if without_al in bare_to_ids:
                for target_id in bare_to_ids[without_al]:
                    if target_id != lemma.lemma_id:
                        dedup_pairs.append((lemma.lemma_id, target_id))
                        break

    # Deduplicate: only merge once per pair, prefer keeping the one with more reviews
    seen = set()
    for var_id, canon_id in dedup_pairs:
        pair = tuple(sorted([var_id, canon_id]))
        if pair in seen:
            continue
        seen.add(pair)

        var = db.get(Lemma, var_id)
        canon = db.get(Lemma, canon_id)
        v_ulk = db.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == var_id
        ).first()
        c_ulk = db.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == canon_id
        ).first()
        v_seen = (v_ulk.times_seen or 0) if v_ulk else 0
        c_seen = (c_ulk.times_seen or 0) if c_ulk else 0

        # Keep the one with more reviews as canonical
        if v_seen > c_seen:
            var_id, canon_id = canon_id, var_id
            var, canon = canon, var

        print(f"  DEDUP: {var.lemma_ar_bare} ({var.gloss_en}) → {canon.lemma_ar_bare} ({canon.gloss_en})")
        if do_merge:
            merge_into(db, var_id, canon_id, dry_run)
        elif not dry_run:
            var = db.get(Lemma, var_id)
            var.canonical_lemma_id = canon_id
        total_changes += 1

    # === PASS 3: Enrich forms_json ===
    print()
    print("=" * 60)
    print("PASS 3: ENRICH forms_json")
    print("=" * 60)

    variants_with_canonical = (
        db.query(Lemma)
        .filter(Lemma.canonical_lemma_id.isnot(None))
        .all()
    )
    enriched = 0
    for var in variants_with_canonical:
        if var.lemma_ar_bare and var.canonical_lemma_id:
            canon = db.get(Lemma, var.canonical_lemma_id)
            if canon:
                forms = canon.forms_json or {}
                if isinstance(forms, str):
                    forms = json.loads(forms)
                # Check if variant form is already stored
                already = False
                var_norm = normalize_alef(var.lemma_ar_bare)
                for v in forms.values():
                    if isinstance(v, str) and normalize_alef(strip_diacritics(v)) == var_norm:
                        already = True
                        break
                # Also skip if variant bare == canonical bare (al-prefix dups)
                if normalize_alef(canon.lemma_ar_bare) == var_norm:
                    already = True
                if not already:
                    enrich_forms_json(db, var.canonical_lemma_id, var.lemma_ar_bare, dry_run=dry_run)
                    enriched += 1

    print(f"Enriched forms_json on {enriched} lemmas")

    # === SUMMARY ===
    print()
    print("=" * 60)
    mode = "DRY RUN" if dry_run else ("MERGE" if do_merge else "MARK ONLY")
    print(f"DONE ({mode}): {total_changes} variant changes, {enriched} forms enriched")
    print("=" * 60)

    if not dry_run and (total_changes > 0 or enriched > 0):
        db.commit()
        log_activity(
            db,
            event_type="normalize_dedup_completed",
            summary=f"Dedup: {total_changes} variants, {enriched} forms enriched (merge={do_merge})",
            detail={
                "variants": total_changes,
                "forms_enriched": enriched,
                "merge": do_merge,
            },
        )
        db.commit()
        print("Committed.")
    else:
        db.rollback()
        if dry_run:
            print("Dry run — no changes made.")

    db.close()


if __name__ == "__main__":
    main()
