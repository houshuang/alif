"""Detect and mark lemma variants using CAMeL Tools morphological analysis.

For each lemma in the DB, runs CAMeL Tools analyzer to detect:
- Possessive forms (enc0 is non-empty): بنتي → base is بنت
- Definite forms (al-prefix where bare form exists): الكتاب → base is كتاب
- Feminine/inflected forms sharing the same lex

For detected variants:
- Sets canonical_lemma_id on the variant lemma
- Optionally merges review data into the canonical lemma (--merge)

Run: python scripts/cleanup_lemma_variants.py [--dry-run] [--merge]
  --dry-run: Show what would be detected, don't change anything
  --merge:   Also merge review data and delete variant lemma rows
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
from app.services.morphology import analyze_word_camel, CAMEL_AVAILABLE
from app.services.sentence_validator import strip_diacritics


# Pronominal suffixes that indicate possessives (not taa marbuta or other morphemes)
_REAL_ENCLITICS = {
    "ي", "ك", "ه", "ها", "هم", "هن", "هما", "كم", "كن", "نا",
    "3ms_dobj", "3fs_dobj", "1s_dobj", "2ms_dobj", "2fs_dobj",
}

# Words that should never be merged (semantically distinct despite morphological relation)
_NEVER_MERGE = {
    ("جدا", "جد"),      # "very" is not a variant of "grandfather"
    ("هذه", "هذا"),     # distinct demonstratives
    ("جدتك", "جدا"),    # "your grandmother" ≠ "very"
    ("ابنك", "آب"),     # "your son" ≠ "to return"
    ("ابني", "آب"),     # "my son" ≠ "to return"
    ("غرفة", "غرف"),    # "room" and "rooms" are separate lemmas
    ("جامعة", "جامع"),  # "university" ≠ "comprehensive"
    ("ملكة", "ملك"),    # "queen" ≠ "angel/king" (distinct lemmas)
    ("سمك", "سم"),      # "fish" ≠ "poison"
    ("كلية", "أكل"),    # "college" ≠ "food"
    ("شباك", "شب"),     # "net/window" ≠ "grow up"
    ("قبلة", "قبل"),    # "kiss" ≠ "before"
    ("الآن", "آن"),     # "now" is a distinct word from "time"
    ("اليوم", "يوم"),   # "today" is a distinct word from "day"
    ("الليلة", "ليلة"), # "tonight" is a distinct word from "night"
    ("فلافل", "فلفل"),  # "falafel" ≠ "pepper"
    ("درة", "در"),      # "pearl" ≠ "to stream"
    ("حكمة", "حكم"),    # "wisdom" ≠ "to rule" (related but distinct lemmas)
    ("ترجمة", "ترجم"),  # "translation" ≠ "to translate" (noun vs verb)
    ("صورة", "صور"),    # "photo" ≠ "photos" (separate lemmas)
    ("بيضة", "بيض"),    # "egg" ≠ "eggs" (separate lemmas)
    ("علمنة", "علم"),   # "secularization" ≠ "to know"
    ("عربي", "عرب"),    # "Arab/Arabic" ≠ "to translate into Arabic"
}


def _is_real_enclitic(enc0: str) -> bool:
    """Check if enc0 value represents a real pronominal suffix, not a morpheme artifact."""
    if not enc0 or enc0 == "0" or enc0 == "na":
        return False
    # CAMeL Tools uses various formats; check for known real enclitics
    enc_clean = enc0.strip()
    if enc_clean in _REAL_ENCLITICS:
        return True
    # Also check if it looks like a possessive pronoun (short Arabic suffix)
    if len(enc_clean) <= 3 and any(c in "يكههانم" for c in enc_clean):
        return True
    return False


def _gloss_overlap(gloss_a: str, gloss_b: str) -> bool:
    """Check if two glosses share semantic content (at least one meaningful word)."""
    if not gloss_a or not gloss_b:
        return False
    noise = {"a", "an", "the", "of", "to", "is", "my", "your", "his", "her", "its",
             "their", "our", "(m)", "(f)", "m", "f", "(masc)", "(fem)"}
    words_a = set(gloss_a.lower().replace("(", " ").replace(")", " ").split()) - noise
    words_b = set(gloss_b.lower().replace("(", " ").replace(")", " ").split()) - noise
    return bool(words_a & words_b)


def find_variants_via_camel(db):
    """Analyze all lemmas with CAMeL Tools to find variants."""
    if not CAMEL_AVAILABLE:
        print("ERROR: CAMeL Tools not installed.")
        return []

    lemmas = db.query(Lemma).filter(Lemma.canonical_lemma_id.is_(None)).all()
    bare_to_lemma = {}
    for l in lemmas:
        bare = l.lemma_ar_bare or ""
        bare_to_lemma.setdefault(bare, []).append(l)

    variants = []
    seen_variant_ids = set()

    for lemma in lemmas:
        ar = lemma.lemma_ar or lemma.lemma_ar_bare
        if not ar:
            continue

        analyses = analyze_word_camel(ar)
        if not analyses:
            analyses = analyze_word_camel(lemma.lemma_ar_bare or "")
        if not analyses:
            continue

        top = analyses[0]
        lex = top.get("lex", "")
        enc0 = top.get("enc0", "")
        lex_bare = strip_diacritics(lex)
        lemma_bare = lemma.lemma_ar_bare or ""

        # Skip if lex matches the lemma itself
        if lex_bare == lemma_bare:
            continue

        # Find base lemma candidates in DB
        candidates = bare_to_lemma.get(lex_bare, [])
        base = None
        for c in candidates:
            if c.lemma_id == lemma.lemma_id:
                continue
            # Check gloss overlap to avoid false matches
            if _gloss_overlap(lemma.gloss_en, c.gloss_en):
                base = c
                break

        if not base:
            # No gloss-matching base found, try without gloss check for possessives
            if _is_real_enclitic(enc0):
                for c in candidates:
                    if c.lemma_id != lemma.lemma_id:
                        base = c
                        break
            if not base:
                if _is_real_enclitic(enc0):
                    print(f"  No base found for possessive: {lemma_bare} ({lemma.gloss_en}) (lex={lex_bare})")
                continue

        # Check never-merge list
        pair = (lemma_bare, base.lemma_ar_bare)
        if pair in _NEVER_MERGE or (pair[1], pair[0]) in _NEVER_MERGE:
            continue

        if lemma.lemma_id in seen_variant_ids:
            continue
        seen_variant_ids.add(lemma.lemma_id)

        vtype = "possessive" if _is_real_enclitic(enc0) else "inflected"
        variants.append((lemma.lemma_id, base.lemma_id, vtype, {"enc0": enc0, "lex": lex}))

    return variants


def find_definite_variants(db, already_variant_ids: set):
    """Find lemmas stored as definite (ال-prefixed) where the bare form exists."""
    lemmas = db.query(Lemma).filter(Lemma.canonical_lemma_id.is_(None)).all()
    bare_to_lemma = {}
    for l in lemmas:
        bare = l.lemma_ar_bare or ""
        bare_to_lemma.setdefault(bare, []).append(l)

    variants = []
    for lemma in lemmas:
        if lemma.lemma_id in already_variant_ids:
            continue
        bare = lemma.lemma_ar_bare or ""
        if not bare.startswith("ال"):
            continue
        without_al = bare[2:]
        if without_al in bare_to_lemma:
            for base in bare_to_lemma[without_al]:
                if base.lemma_id == lemma.lemma_id:
                    continue
                if base.lemma_id in already_variant_ids:
                    continue
                pair = (bare, base.lemma_ar_bare or "")
                if pair in _NEVER_MERGE or (pair[1], pair[0]) in _NEVER_MERGE:
                    continue
                variants.append((lemma.lemma_id, base.lemma_id, "definite", {"stripped": without_al}))
                break

    return variants


def mark_canonical(db, variant_id, canonical_id, dry_run=False):
    """Set canonical_lemma_id on a variant lemma."""
    variant = db.query(Lemma).filter(Lemma.lemma_id == variant_id).first()
    if not variant:
        return
    if not dry_run:
        variant.canonical_lemma_id = canonical_id


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

    if not CAMEL_AVAILABLE:
        print("CAMeL Tools not available. Install with: pip install camel-tools")
        print("Then download data: camel_data -i light")
        return

    db = SessionLocal()

    # Step 1: CAMeL Tools analysis
    print("=== CAMeL Tools VARIANT DETECTION ===")
    camel_variants = find_variants_via_camel(db)
    print(f"\nFound {len(camel_variants)} variants via CAMeL Tools:")
    for var_id, canon_id, vtype, details in camel_variants:
        var = db.get(Lemma, var_id)
        canon = db.get(Lemma, canon_id)
        print(f"  {var.lemma_ar_bare} ({var.gloss_en}) → {canon.lemma_ar_bare} ({canon.gloss_en}) [{vtype}]")

    # Step 2: Definite form detection (skip already-detected variants)
    already_ids = {v[0] for v in camel_variants}
    print("\n=== DEFINITE FORM DETECTION ===")
    def_variants = find_definite_variants(db, already_ids)
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
    for var_id, canon_id, vtype, details in all_variants:
        var = db.get(Lemma, var_id)
        canon = db.get(Lemma, canon_id)
        print(f"\n{var.lemma_ar_bare} ({var.gloss_en}) → {canon.lemma_ar_bare} ({canon.gloss_en}) [{vtype}]")

        mark_canonical(db, var_id, canon_id, dry_run)

        if do_merge:
            merge_variant(db, var_id, canon_id, vtype, dry_run)

    if not dry_run:
        db.commit()
        print(f"\nCommitted {len(all_variants)} variant markings.")
    else:
        db.rollback()
        print(f"\nDry run complete. Would mark {len(all_variants)} variants.")

    db.close()


if __name__ == "__main__":
    main()
