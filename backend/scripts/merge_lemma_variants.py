"""Merge lemma variants (masc/fem adjective pairs, singular/plural nouns, possessive forms).

For each merge pair:
- Transfers review_log records from secondary → primary lemma_id
- Transfers sentence_words references from secondary → primary
- Transfers sentences.target_lemma_id from secondary → primary
- Merges FSRS cards: keeps the one with more reviews, sums times_seen/correct
- Stores secondary form in primary's forms_json
- Deletes secondary UserLemmaKnowledge record
- Suspends possessive forms (creates ULK with state=suspended)

Run: python scripts/merge_lemma_variants.py [--dry-run]
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
)


def find_masc_fem_pairs(db):
    """Find adjective pairs where bare forms differ only by ة suffix and gloss overlaps."""
    lemmas = db.query(Lemma).all()
    by_bare = {}
    for l in lemmas:
        bare = l.lemma_ar_bare or ""
        by_bare.setdefault(bare, []).append(l)

    pairs = []
    seen = set()
    for l in lemmas:
        bare = l.lemma_ar_bare or ""
        pos = (l.pos or "").lower()
        if bare.endswith("ة") and pos in ("adj", "adjective", "noun"):
            masc_bare = bare[:-1]
            if masc_bare in by_bare:
                for m in by_bare[masc_bare]:
                    if m.lemma_id == l.lemma_id:
                        continue
                    m_pos = (m.pos or "").lower()
                    # Only pair adjectives with adjectives, or same-POS
                    if m_pos not in ("adj", "adjective") and pos not in ("adj", "adjective"):
                        continue
                    pair_key = (min(m.lemma_id, l.lemma_id), max(m.lemma_id, l.lemma_id))
                    if pair_key in seen:
                        continue
                    # Check gloss overlap (at least one common word)
                    m_words = set((m.gloss_en or "").lower().replace("(", "").replace(")", "").split())
                    f_words = set((l.gloss_en or "").lower().replace("(", "").replace(")", "").split())
                    common = m_words & f_words - {"a", "an", "the", "of", "to", "m", "f", "(m)", "(f)"}
                    if common or _gloss_is_gendered_variant(m.gloss_en, l.gloss_en):
                        seen.add(pair_key)
                        pairs.append((m.lemma_id, l.lemma_id))  # keep masc, merge fem
    return pairs


def _gloss_is_gendered_variant(gloss_m, gloss_f):
    """Check if glosses are gendered variants like 'new (m)' and 'new (f)'."""
    if not gloss_m or not gloss_f:
        return False
    gm = gloss_m.lower().replace("(m)", "").replace("(f)", "").replace("(masc)", "").replace("(fem)", "").strip()
    gf = gloss_f.lower().replace("(m)", "").replace("(f)", "").replace("(masc)", "").replace("(fem)", "").strip()
    return gm == gf


def find_possessive_forms(db):
    """Find lemmas that are possessive forms (my X, your X, his X, etc.)."""
    possessive_prefixes = [
        "my ", "your ", "his ", "her ", "its ", "their ", "our ",
    ]
    lemmas = db.query(Lemma).all()
    possessives = []
    for l in lemmas:
        gloss = (l.gloss_en or "").lower().strip()
        if any(gloss.startswith(p) for p in possessive_prefixes):
            possessives.append(l.lemma_id)
    return possessives


def merge_pair(db, primary_id, secondary_id, form_key, dry_run=False):
    """Merge secondary lemma into primary."""
    primary = db.query(Lemma).filter(Lemma.lemma_id == primary_id).first()
    secondary = db.query(Lemma).filter(Lemma.lemma_id == secondary_id).first()
    if not primary or not secondary:
        print(f"  SKIP: lemma {primary_id} or {secondary_id} not found")
        return

    p_ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == primary_id).first()
    s_ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == secondary_id).first()

    print(f"  Merging {secondary_id} {secondary.lemma_ar_bare} -> {primary_id} {primary.lemma_ar_bare}")

    # 1. Move review logs
    s_reviews = db.query(ReviewLog).filter(ReviewLog.lemma_id == secondary_id).all()
    if s_reviews:
        print(f"    Moving {len(s_reviews)} review logs")
        if not dry_run:
            for r in s_reviews:
                r.lemma_id = primary_id

    # 2. Move sentence_words references
    s_sw = db.query(SentenceWord).filter(SentenceWord.lemma_id == secondary_id).all()
    if s_sw:
        print(f"    Moving {len(s_sw)} sentence_words")
        if not dry_run:
            for sw in s_sw:
                sw.lemma_id = primary_id

    # 3. Update sentences target_lemma_id
    s_sentences = db.query(Sentence).filter(Sentence.target_lemma_id == secondary_id).all()
    if s_sentences:
        print(f"    Moving {len(s_sentences)} sentence targets")
        if not dry_run:
            for s in s_sentences:
                s.target_lemma_id = primary_id

    # 4. Merge FSRS knowledge
    if s_ulk and p_ulk:
        s_seen = s_ulk.times_seen or 0
        p_seen = p_ulk.times_seen or 0
        s_correct = s_ulk.times_correct or 0
        p_correct = p_ulk.times_correct or 0

        p_ulk.times_seen = s_seen + p_seen
        p_ulk.times_correct = s_correct + p_correct

        if s_seen > p_seen and s_ulk.fsrs_card_json:
            print(f"    Using secondary FSRS card (more reviews: {s_seen} vs {p_seen})")
            p_ulk.fsrs_card_json = s_ulk.fsrs_card_json
            p_ulk.knowledge_state = s_ulk.knowledge_state
            if s_ulk.last_reviewed:
                p_ulk.last_reviewed = s_ulk.last_reviewed

        print(f"    Combined: seen={p_ulk.times_seen}, correct={p_ulk.times_correct}")

        if not dry_run:
            db.delete(s_ulk)
    elif s_ulk and not p_ulk:
        print(f"    Moving ULK from secondary to primary")
        if not dry_run:
            s_ulk.lemma_id = primary_id

    # 5. Store secondary form in primary's forms_json
    forms = primary.forms_json or {}
    if isinstance(forms, str):
        forms = json.loads(forms)
    forms = dict(forms)
    if form_key and secondary.lemma_ar and form_key not in forms:
        forms[form_key] = secondary.lemma_ar
        if not dry_run:
            primary.forms_json = forms
        print(f"    Stored {form_key}={secondary.lemma_ar} in forms_json")

    # 6. Delete orphaned secondary Lemma if no remaining references
    remaining_sw = db.query(SentenceWord).filter(SentenceWord.lemma_id == secondary_id).count()
    remaining_st = db.query(Sentence).filter(Sentence.target_lemma_id == secondary_id).count()
    remaining_ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == secondary_id).count()
    if remaining_sw == 0 and remaining_st == 0 and remaining_ulk == 0:
        print(f"    Deleting secondary lemma row")
        if not dry_run:
            db.delete(secondary)


def suspend_possessives(db, possessive_ids, dry_run=False):
    """Suspend possessive lemmas so they don't appear in learn mode."""
    from datetime import datetime, timezone

    for lemma_id in possessive_ids:
        existing = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == lemma_id).first()
        if existing:
            if existing.knowledge_state == "suspended":
                continue
            print(f"  {lemma_id}: already has ULK (state={existing.knowledge_state}), skipping")
            continue

        lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
        if not lemma:
            continue

        print(f"  Suspending {lemma_id} {lemma.lemma_ar_bare} ({lemma.gloss_en})")
        if not dry_run:
            ulk = UserLemmaKnowledge(
                lemma_id=lemma_id,
                knowledge_state="suspended",
                introduced_at=datetime.now(timezone.utc),
                source="study",
            )
            db.add(ulk)


def main():
    dry_run = "--dry-run" in sys.argv

    db = SessionLocal()

    # --- Masc/Fem adjective pairs ---
    print("=== MASC/FEM ADJECTIVE PAIRS ===")
    fem_pairs = find_masc_fem_pairs(db)
    print(f"Found {len(fem_pairs)} masc/fem pairs")
    for primary_id, secondary_id in fem_pairs:
        merge_pair(db, primary_id, secondary_id, "feminine", dry_run)

    # --- Possessive forms ---
    print("\n=== POSSESSIVE FORMS ===")
    possessive_ids = find_possessive_forms(db)
    print(f"Found {len(possessive_ids)} possessive forms")
    suspend_possessives(db, possessive_ids, dry_run)

    if not dry_run:
        db.commit()
        total = db.query(UserLemmaKnowledge).count()
        print(f"\nCommitted. Total study words now: {total}")
    else:
        db.rollback()
        print(f"\nDry run complete. Would merge {len(fem_pairs)} masc/fem pairs "
              f"and suspend {len(possessive_ids)} possessive forms.")

    db.close()


if __name__ == "__main__":
    main()
