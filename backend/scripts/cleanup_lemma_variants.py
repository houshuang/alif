"""Detect and mark lemma variants using CAMeL Tools morphological analysis.

For each lemma in the DB, runs CAMeL Tools analyzer to detect:
- Possessive forms (enc0 is non-empty): بنتي → base is بنت
- Definite forms (al-prefix where bare form exists): الكتاب → base is كتاب
- Feminine/inflected forms sharing the same lex

Uses DB-aware disambiguation: iterates ALL CAMeL analyses (not just the top one)
and picks the analysis whose lex matches a lemma already in our database.
This eliminates most false positives without needing a large never-merge list.

For detected variants:
- Sets canonical_lemma_id on the variant lemma
- Optionally merges review data into the canonical lemma (--merge)

Run: python scripts/cleanup_lemma_variants.py [--dry-run] [--merge] [--verbose]
  --dry-run:  Show what would be detected, don't change anything
  --merge:    Also merge review data and delete variant lemma rows
  --verbose:  Show which analysis was picked and why for each lemma
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
from app.services.morphology import CAMEL_AVAILABLE
from app.services.variant_detection import (
    detect_variants,
    detect_definite_variants,
    mark_variants,
)


def merge_variant(db, variant_id, canonical_id, form_key, dry_run=False):
    """Full merge: move review data from variant into canonical lemma."""
    primary = db.query(Lemma).filter(Lemma.lemma_id == canonical_id).first()
    secondary = db.query(Lemma).filter(Lemma.lemma_id == variant_id).first()
    if not primary or not secondary:
        return

    p_ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == canonical_id).first()
    s_ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == variant_id).first()

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
    s_sentences = db.query(Sentence).filter(Sentence.target_lemma_id == variant_id).all()
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

    # Store variant form in canonical's forms_json
    forms = primary.forms_json or {}
    if isinstance(forms, str):
        forms = json.loads(forms)
    forms = dict(forms)
    if form_key and secondary.lemma_ar and form_key not in forms:
        forms[form_key] = secondary.lemma_ar
        if not dry_run:
            primary.forms_json = forms
        print(f"    Stored {form_key}={secondary.lemma_ar} in forms_json")


def main():
    dry_run = "--dry-run" in sys.argv
    do_merge = "--merge" in sys.argv
    verbose = "--verbose" in sys.argv

    if not CAMEL_AVAILABLE:
        print("CAMeL Tools not available. Install with: pip install camel-tools")
        print("Then download data: camel_data -i light")
        return

    db = SessionLocal()

    # Step 1: CAMeL Tools analysis (DB-aware disambiguation)
    print("=== CAMeL Tools VARIANT DETECTION (DB-aware) ===")
    camel_variants = detect_variants(db, verbose=verbose)
    print(f"\nFound {len(camel_variants)} variants via CAMeL Tools:")
    for var_id, canon_id, vtype, details in camel_variants:
        var = db.get(Lemma, var_id)
        canon = db.get(Lemma, canon_id)
        print(f"  {var.lemma_ar_bare} ({var.gloss_en}) → {canon.lemma_ar_bare} ({canon.gloss_en}) [{vtype}]")

    # Step 2: Definite form detection (skip already-detected variants)
    already_ids = {v[0] for v in camel_variants}
    print("\n=== DEFINITE FORM DETECTION ===")
    def_variants = detect_definite_variants(db, already_variant_ids=already_ids)
    print(f"Found {len(def_variants)} definite-form variants:")
    for var_id, canon_id, vtype, details in def_variants:
        var = db.get(Lemma, var_id)
        canon = db.get(Lemma, canon_id)
        print(f"  {var.lemma_ar_bare} ({var.gloss_en}) → {canon.lemma_ar_bare} ({canon.gloss_en})")

    all_variants = camel_variants + def_variants

    if not all_variants:
        print("\nNo variants detected.")
        db.close()
        return

    # Step 3: Apply changes
    print(f"\n=== APPLYING CHANGES (dry_run={dry_run}, merge={do_merge}) ===")
    if not dry_run:
        marked = mark_variants(db, all_variants)
        print(f"Marked {marked} variants.")
    else:
        print(f"Would mark {len(all_variants)} variants.")

    for var_id, canon_id, vtype, details in all_variants:
        var = db.get(Lemma, var_id)
        canon = db.get(Lemma, canon_id)
        print(f"\n{var.lemma_ar_bare} ({var.gloss_en}) → {canon.lemma_ar_bare} ({canon.gloss_en}) [{vtype}]")

        if do_merge:
            merge_variant(db, var_id, canon_id, vtype, dry_run)

    if not dry_run:
        db.commit()
        print(f"\nCommitted changes.")

        if all_variants:
            log_activity(
                db,
                event_type="variant_cleanup_completed",
                summary=f"Detected {len(all_variants)} variants, marked {marked} (merge={do_merge})",
                detail={
                    "variants_detected": len(all_variants),
                    "variants_marked": marked,
                    "camel_variants": len(camel_variants),
                    "definite_variants": len(def_variants),
                    "merge": do_merge,
                },
            )
    else:
        db.rollback()
        print(f"\nDry run complete.")

    db.close()


if __name__ == "__main__":
    main()
