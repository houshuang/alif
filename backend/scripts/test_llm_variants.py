#!/usr/bin/env python3
"""Prototype: test LLM variant detection against known cases from the spec.

Tests against the known false positives and true positives documented in
research/variant-detection-spec.md section 3.

Usage:
    python scripts/test_llm_variants.py              # test against spec examples
    python scripts/test_llm_variants.py --production  # run against production DB
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.variant_detection import evaluate_variants_llm


# Ground truth from the spec
# False positives (CAMeL says variant, but they're distinct lemmas)
FALSE_POSITIVES = [
    # Taa marbuta nouns with DIFFERENT meaning from base (section 3a)
    {"word_ar": "جامعة", "word_gloss": "university", "word_pos": "noun",
     "base_ar": "جامع", "base_gloss": "mosque", "base_pos": "noun"},
    {"word_ar": "شاشة", "word_gloss": "screen", "word_pos": "noun",
     "base_ar": "شاش", "base_gloss": "muslin", "base_pos": "noun"},
    {"word_ar": "سنة", "word_gloss": "year", "word_pos": "noun",
     "base_ar": "سن", "base_gloss": "age; tooth", "base_pos": "noun"},
    {"word_ar": "كلمة", "word_gloss": "word", "word_pos": "noun",
     "base_ar": "كلم", "base_gloss": "to speak", "base_pos": "verb"},
    # Masdar / verbal noun vs concrete noun
    {"word_ar": "كتابة", "word_gloss": "writing", "word_pos": "noun",
     "base_ar": "كتاب", "base_gloss": "book", "base_pos": "noun"},
    # Place noun vs related noun
    {"word_ar": "مكتبة", "word_gloss": "library", "word_pos": "noun",
     "base_ar": "مكتب", "base_gloss": "office", "base_pos": "noun"},
    # Short-stem false positives (section 3b)
    {"word_ar": "سمك", "word_gloss": "fish", "word_pos": "noun",
     "base_ar": "سم", "base_gloss": "poison", "base_pos": "noun"},
    {"word_ar": "بنك", "word_gloss": "bank", "word_pos": "noun",
     "base_ar": "بن", "base_gloss": "son", "base_pos": "noun"},
    # Nisba adjectives (section 3c)
    {"word_ar": "عربي", "word_gloss": "Arabic; Arab", "word_pos": "adj",
     "base_ar": "عرب", "base_gloss": "to translate", "base_pos": "verb"},
    {"word_ar": "مصري", "word_gloss": "Egyptian", "word_pos": "adj",
     "base_ar": "مصر", "base_gloss": "Egypt", "base_pos": "noun"},
]

# True positives — these ARE variants (same dictionary entry)
TRUE_POSITIVES = [
    # Singular / broken plural
    {"word_ar": "غرفة", "word_gloss": "room", "word_pos": "noun",
     "base_ar": "غرف", "base_gloss": "rooms", "base_pos": "noun"},
    # Feminine counterpart with SAME meaning
    {"word_ar": "ملكة", "word_gloss": "queen", "word_pos": "noun",
     "base_ar": "ملك", "base_gloss": "king", "base_pos": "noun"},
    # Verb conjugations (section 3d)
    {"word_ar": "تحبون", "word_gloss": "you all love", "word_pos": "verb",
     "base_ar": "أحب", "base_gloss": "to love", "base_pos": "verb"},
    {"word_ar": "يحبون", "word_gloss": "they love", "word_pos": "verb",
     "base_ar": "أحب", "base_gloss": "to love", "base_pos": "verb"},
    # Feminine adjectives
    {"word_ar": "سعيدة", "word_gloss": "happy (f.)", "word_pos": "adj",
     "base_ar": "سعيد", "base_gloss": "happy", "base_pos": "adj"},
    {"word_ar": "مريحة", "word_gloss": "comfortable (f.)", "word_pos": "adj",
     "base_ar": "مريح", "base_gloss": "comfortable", "base_pos": "adj"},
    {"word_ar": "صديقة", "word_gloss": "friend (f.)", "word_pos": "noun",
     "base_ar": "صديق", "base_gloss": "friend", "base_pos": "noun"},
    {"word_ar": "ممرضة", "word_gloss": "nurse (f.)", "word_pos": "noun",
     "base_ar": "ممرض", "base_gloss": "nurse", "base_pos": "noun"},
    # Possessives
    {"word_ar": "مدرستي", "word_gloss": "my teacher", "word_pos": "noun",
     "base_ar": "مدرس", "base_gloss": "teacher", "base_pos": "noun"},
    {"word_ar": "اصدقائي", "word_gloss": "my friends", "word_pos": "noun",
     "base_ar": "صديق", "base_gloss": "friend", "base_pos": "noun"},
    # Clitic prefixes
    {"word_ar": "والمسجد", "word_gloss": "and the mosque", "word_pos": "noun",
     "base_ar": "مسجد", "base_gloss": "mosque", "base_pos": "noun"},
]


def run_spec_test():
    """Test LLM against known ground truth from the spec."""
    print("=" * 70)
    print("LLM VARIANT DETECTION — SPEC TEST")
    print("=" * 70)

    # Combine all candidates with IDs
    all_candidates = []
    for i, fp in enumerate(FALSE_POSITIVES):
        all_candidates.append({"id": i, **fp})
    offset = len(FALSE_POSITIVES)
    for i, tp in enumerate(TRUE_POSITIVES):
        all_candidates.append({"id": offset + i, **tp})

    print(f"\nSending {len(all_candidates)} candidates to LLM...")
    print(f"  Expected: {len(FALSE_POSITIVES)} NOT variants, {len(TRUE_POSITIVES)} ARE variants\n")

    results = evaluate_variants_llm(all_candidates)
    result_by_id = {r["id"]: r for r in results}

    # Check false positives (should all be is_variant=False)
    print("FALSE POSITIVES (should be rejected):")
    print("-" * 50)
    fp_correct = 0
    for i, fp in enumerate(FALSE_POSITIVES):
        r = result_by_id.get(i)
        if not r:
            status = "NO RESPONSE"
            correct = False
        elif r["is_variant"]:
            status = "WRONG — marked as variant"
            correct = False
        else:
            status = "correct"
            correct = True

        if correct:
            fp_correct += 1
        reason = r["reason"] if r else ""
        mark = "✓" if correct else "✗"
        print(f"  {mark} {fp['word_ar']} → {fp['base_ar']}: {status}")
        if reason:
            print(f"    Reason: {reason}")

    # Check true positives (should all be is_variant=True)
    print(f"\nTRUE POSITIVES (should be confirmed):")
    print("-" * 50)
    tp_correct = 0
    for i, tp in enumerate(TRUE_POSITIVES):
        r = result_by_id.get(offset + i)
        if not r:
            status = "NO RESPONSE"
            correct = False
        elif not r["is_variant"]:
            status = "WRONG — rejected as variant"
            correct = False
        else:
            status = "correct"
            correct = True

        if correct:
            tp_correct += 1
        reason = r["reason"] if r else ""
        mark = "✓" if correct else "✗"
        print(f"  {mark} {tp['word_ar']} → {tp['base_ar']}: {status}")
        if reason:
            print(f"    Reason: {reason}")

    total = len(FALSE_POSITIVES) + len(TRUE_POSITIVES)
    total_correct = fp_correct + tp_correct
    print(f"\n{'=' * 50}")
    print(f"RESULTS: {total_correct}/{total} correct ({total_correct/total*100:.0f}%)")
    print(f"  False positive rejection: {fp_correct}/{len(FALSE_POSITIVES)}")
    print(f"  True positive confirmation: {tp_correct}/{len(TRUE_POSITIVES)}")
    print(f"{'=' * 50}")


def run_production_test():
    """Run LLM variant detection against the production DB."""
    from app.database import SessionLocal
    from app.models import Lemma as LemmaModel
    from app.services.variant_detection import detect_variants_llm

    print("=" * 70)
    print("LLM VARIANT DETECTION — PRODUCTION DB")
    print("=" * 70)

    db = SessionLocal()
    try:
        confirmed = detect_variants_llm(db, verbose=True)
        print(f"\nConfirmed variants: {len(confirmed)}")
        for var_id, canon_id, vtype, details in confirmed:
            var = db.query(LemmaModel).filter(LemmaModel.lemma_id == var_id).first()
            canon = db.query(LemmaModel).filter(LemmaModel.lemma_id == canon_id).first()
            print(f"  {var.lemma_ar_bare} ({var.gloss_en}) → "
                  f"{canon.lemma_ar_bare} ({canon.gloss_en}) [{vtype}]")
            print(f"    Reason: {details.get('llm_reason', '')}")
    finally:
        db.close()


if __name__ == "__main__":
    if "--production" in sys.argv:
        run_production_test()
    else:
        run_spec_test()
