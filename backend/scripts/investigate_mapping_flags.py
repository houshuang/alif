#!/usr/bin/env python3
"""Investigate and fix word_mapping flags.

Lists sentences flagged for poor lemmatization, shows current word-lemma
mappings, and optionally re-evaluates and fixes them.

Usage:
    python3 scripts/investigate_mapping_flags.py              # show pending flags
    python3 scripts/investigate_mapping_flags.py --all        # show all statuses
    python3 scripts/investigate_mapping_flags.py --fix        # re-evaluate and fix pending flags
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import ContentFlag, Lemma, Sentence, SentenceWord


def show_flag(db, flag):
    """Display a single flag with its sentence and word mappings."""
    print(f"\n{'='*60}")
    print(f"Flag #{flag.id}  status={flag.status}  created={flag.created_at}")
    if flag.resolution_note:
        print(f"  Resolution: {flag.resolution_note}")

    sentence = db.query(Sentence).filter(Sentence.id == flag.sentence_id).first()
    if not sentence:
        print("  Sentence not found!")
        return

    print(f"  Arabic: {sentence.arabic_text}")
    print(f"  English: {sentence.english_translation}")
    print(f"  Source: {sentence.source}  Active: {sentence.is_active}")

    words = (
        db.query(SentenceWord)
        .filter(SentenceWord.sentence_id == sentence.id)
        .order_by(SentenceWord.position)
        .all()
    )

    lemma_ids = [w.lemma_id for w in words if w.lemma_id]
    lemmas_by_id = {}
    if lemma_ids:
        for lemma in db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all():
            lemmas_by_id[lemma.lemma_id] = lemma

    print("  Word mappings:")
    for w in words:
        lemma = lemmas_by_id.get(w.lemma_id)
        if lemma:
            marker = " *" if flag.lemma_id and w.lemma_id == flag.lemma_id else ""
            print(f"    [{w.position}] {w.surface_form:>15} → {lemma.lemma_ar_bare} ({lemma.gloss_en}){marker}")
        else:
            print(f"    [{w.position}] {w.surface_form:>15} → (unmapped)")

    if flag.corrected_value:
        print(f"  Corrections applied: {flag.corrected_value}")


def main():
    parser = argparse.ArgumentParser(description="Investigate word_mapping flags")
    parser.add_argument("--all", action="store_true", help="Show all statuses (not just pending)")
    parser.add_argument("--fix", action="store_true", help="Re-evaluate and fix pending flags")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        q = db.query(ContentFlag).filter(ContentFlag.content_type == "word_mapping")
        if not args.all:
            q = q.filter(ContentFlag.status.in_(["pending", "reviewing"]))
        flags = q.order_by(ContentFlag.created_at.desc()).all()

        print(f"Found {len(flags)} word_mapping flag(s)")

        for flag in flags:
            show_flag(db, flag)

        if args.fix:
            pending = [f for f in flags if f.status in ("pending", "reviewing")]
            if not pending:
                print("\nNo pending flags to fix.")
                return

            print(f"\nRe-evaluating {len(pending)} pending flag(s)...")
            from app.services.flag_evaluator import evaluate_flag

            for flag in pending:
                # Reset to pending so evaluator picks it up
                flag.status = "pending"
                db.commit()
                print(f"  Evaluating flag #{flag.id}...")
                evaluate_flag(flag.id)
                # Reload to show result
                db.refresh(flag)
                print(f"    → {flag.status}: {flag.resolution_note}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
