"""Merge al- prefixed lemma duplicates into their bare forms.

For each pair like الكلب (the dog) / كلب (dog):
- Transfers review logs from al- lemma to bare lemma
- Transfers FSRS card data (keeps whichever has more reviews)
- Updates sentence_words references
- Deletes the al- UserLemmaKnowledge record
- Keeps the al- Lemma row (useful for dictionary) but removes it from study set

Run: python scripts/merge_al_lemmas.py [--dry-run]
"""

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

MERGE_PAIRS = [
    (1, 77),    # الكلب -> كلب
    (3, 106),   # الغرفة -> غرفة
    (80, 185),  # الجار -> جار
    (81, 85),   # المحامي -> محامي
    (82, 130),  # الأستاذ -> أستاذ
    (83, 110),  # المدينة -> مدينة
    (84, 107),  # الجامعة -> جامعة
    (87, 124),  # المعلمة -> معلمة
    (88, 176),  # الباب -> باب
    (89, 121),  # المعلم -> معلم
    (90, 181),  # البيت -> بيت
    (91, 166),  # المترجم -> مترجم
    (92, 180),  # الكراج -> كراج
    (93, 113),  # المهندسة -> مهندسة
]


def merge(dry_run: bool = False):
    db = SessionLocal()

    for al_id, bare_id in MERGE_PAIRS:
        al_lemma = db.query(Lemma).filter(Lemma.lemma_id == al_id).first()
        bare_lemma = db.query(Lemma).filter(Lemma.lemma_id == bare_id).first()
        if not al_lemma or not bare_lemma:
            print(f"  SKIP: lemma {al_id} or {bare_id} not found")
            continue

        al_ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == al_id).first()
        bare_ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == bare_id).first()

        print(f"Merging {al_id} {al_lemma.lemma_ar_bare} -> {bare_id} {bare_lemma.lemma_ar_bare}")

        # 1. Move review logs
        al_reviews = db.query(ReviewLog).filter(ReviewLog.lemma_id == al_id).all()
        if al_reviews:
            print(f"  Moving {len(al_reviews)} review logs")
            if not dry_run:
                for r in al_reviews:
                    r.lemma_id = bare_id

        # 2. Move sentence_words references
        al_sw = db.query(SentenceWord).filter(SentenceWord.lemma_id == al_id).all()
        if al_sw:
            print(f"  Moving {len(al_sw)} sentence_words")
            if not dry_run:
                for sw in al_sw:
                    sw.lemma_id = bare_id

        # 3. Update sentences where target_lemma_id = al_id
        al_sentences = db.query(Sentence).filter(Sentence.target_lemma_id == al_id).all()
        if al_sentences:
            print(f"  Moving {len(al_sentences)} sentence targets")
            if not dry_run:
                for s in al_sentences:
                    s.target_lemma_id = bare_id

        # 4. Merge FSRS knowledge: keep the one with more reviews, combine counts
        if al_ulk and bare_ulk:
            al_seen = al_ulk.times_seen or 0
            bare_seen = bare_ulk.times_seen or 0
            al_correct = al_ulk.times_correct or 0
            bare_correct = bare_ulk.times_correct or 0

            # Combine counts
            bare_ulk.times_seen = al_seen + bare_seen
            bare_ulk.times_correct = al_correct + bare_correct

            # Keep whichever FSRS card has more progress
            if al_seen > bare_seen and al_ulk.fsrs_card_json:
                print(f"  Using al- FSRS card (more reviews: {al_seen} vs {bare_seen})")
                bare_ulk.fsrs_card_json = al_ulk.fsrs_card_json
                bare_ulk.knowledge_state = al_ulk.knowledge_state
                if al_ulk.last_reviewed:
                    bare_ulk.last_reviewed = al_ulk.last_reviewed

            print(f"  Combined: seen={bare_ulk.times_seen}, correct={bare_ulk.times_correct}")

            # Delete al- ULK
            if not dry_run:
                db.delete(al_ulk)
        elif al_ulk and not bare_ulk:
            # Just reassign the ULK
            print(f"  Moving ULK from al- to bare")
            if not dry_run:
                al_ulk.lemma_id = bare_id

        # 5. Delete the orphaned al- Lemma row
        al_sw_remaining = db.query(SentenceWord).filter(SentenceWord.lemma_id == al_id).count()
        al_st_remaining = db.query(Sentence).filter(Sentence.target_lemma_id == al_id).count()
        if al_sw_remaining == 0 and al_st_remaining == 0:
            print(f"  Deleting al- lemma row")
            if not dry_run:
                db.delete(al_lemma)
        else:
            print(f"  Keeping al- lemma (still has {al_sw_remaining} sentence_words, {al_st_remaining} targets)")

        print(f"  Done")

    if not dry_run:
        db.commit()
        print(f"\nCommitted. Merged {len(MERGE_PAIRS)} pairs.")

        # Verify new count
        total = db.query(UserLemmaKnowledge).count()
        print(f"Total study words now: {total}")
    else:
        print(f"\nDry run complete. Would merge {len(MERGE_PAIRS)} pairs.")

    db.close()


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    merge(dry_run=dry_run)
