#!/usr/bin/env python3
"""Clean up lemma entries with dirty Arabic text.

Fixes:
- Trailing/leading punctuation (النَّرْوِيج؟ → النَّرْوِيج)
- Slash-separated alternatives (الصَّفُّ/السَّنَةُ → الصَّفُّ)
- Multi-word phrases: delete and optionally recreate first word as new lemma

Run: python scripts/cleanup_lemma_text.py [--dry-run] [--verbose]
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import (
    Lemma,
    UserLemmaKnowledge,
    ReviewLog,
    Sentence,
    SentenceWord,
    StoryWord,
)
from app.services.sentence_validator import (
    FUNCTION_WORDS,
    compute_bare_form,
    normalize_arabic,
    sanitize_arabic_word,
)


def _delete_lemma(db, lemma, dry_run=False):
    """Delete a lemma and all associated records."""
    lid = lemma.lemma_id

    reviews = db.query(ReviewLog).filter(ReviewLog.lemma_id == lid).all()
    if reviews and not dry_run:
        for r in reviews:
            db.delete(r)

    ulk = db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == lid
    ).first()
    if ulk and not dry_run:
        db.delete(ulk)

    swords = db.query(SentenceWord).filter(SentenceWord.lemma_id == lid).all()
    if swords and not dry_run:
        for sw in swords:
            sw.lemma_id = None

    sentences = db.query(Sentence).filter(Sentence.target_lemma_id == lid).all()
    if sentences and not dry_run:
        for s in sentences:
            s.target_lemma_id = None
            s.is_active = False

    stwords = db.query(StoryWord).filter(StoryWord.lemma_id == lid).all()
    if stwords and not dry_run:
        for sw in stwords:
            sw.lemma_id = None

    if not dry_run:
        db.delete(lemma)


def _merge_into(db, source, target, dry_run=False):
    """Merge source lemma into target (transfer references, delete source)."""
    sid = source.lemma_id
    tid = target.lemma_id

    reviews = db.query(ReviewLog).filter(ReviewLog.lemma_id == sid).all()
    if reviews:
        if not dry_run:
            for r in reviews:
                r.lemma_id = tid

    s_ulk = db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == sid
    ).first()
    t_ulk = db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == tid
    ).first()

    if s_ulk and t_ulk:
        t_ulk.times_seen = (t_ulk.times_seen or 0) + (s_ulk.times_seen or 0)
        t_ulk.times_correct = (t_ulk.times_correct or 0) + (s_ulk.times_correct or 0)
        if not dry_run:
            db.delete(s_ulk)
    elif s_ulk and not t_ulk:
        if not dry_run:
            s_ulk.lemma_id = tid

    swords = db.query(SentenceWord).filter(SentenceWord.lemma_id == sid).all()
    if swords and not dry_run:
        for sw in swords:
            sw.lemma_id = tid

    sentences = db.query(Sentence).filter(Sentence.target_lemma_id == sid).all()
    if sentences and not dry_run:
        for s in sentences:
            s.target_lemma_id = tid

    stwords = db.query(StoryWord).filter(StoryWord.lemma_id == sid).all()
    if stwords and not dry_run:
        for sw in stwords:
            sw.lemma_id = tid

    if not dry_run:
        db.delete(source)


def cleanup(dry_run=True, verbose=False):
    db = SessionLocal()

    all_lemmas = db.query(Lemma).all()

    # Build bare→lemma map for dedup detection
    bare_to_lemmas: dict[str, list[Lemma]] = {}
    for lem in all_lemmas:
        bare_to_lemmas.setdefault(lem.lemma_ar_bare, []).append(lem)

    fixed_punct = 0
    fixed_slash = 0
    deleted_multiword = 0
    merged_dupes = 0
    total = len(all_lemmas)

    # Collect changes for LLM verification
    changes: list[dict] = []

    for lemma in all_lemmas:
        cleaned, warnings = sanitize_arabic_word(lemma.lemma_ar)
        if not warnings:
            continue

        action: dict = {
            "lemma_id": lemma.lemma_id,
            "original": lemma.lemma_ar,
            "cleaned": cleaned,
            "gloss": lemma.gloss_en,
            "warnings": warnings,
        }

        if "empty" in warnings or "empty_after_clean" in warnings:
            action["action"] = "delete_empty"
            if verbose:
                print(f"  DELETE empty: {lemma.lemma_ar!r} (id={lemma.lemma_id})")
            _delete_lemma(db, lemma, dry_run)
            deleted_multiword += 1
            changes.append(action)
            continue

        if "multi_word" in warnings:
            # Check if first word is a function word
            first_bare = normalize_arabic(cleaned)
            if first_bare in FUNCTION_WORDS:
                action["action"] = "delete_multiword_function"
                if verbose:
                    print(
                        f"  DELETE multi-word (first word is function): "
                        f"{lemma.lemma_ar!r} → {cleaned!r} (id={lemma.lemma_id})"
                    )
                _delete_lemma(db, lemma, dry_run)
                deleted_multiword += 1
                changes.append(action)
                continue

            # Check if first word already exists
            existing = bare_to_lemmas.get(first_bare, [])
            existing = [l for l in existing if l.lemma_id != lemma.lemma_id]
            if existing:
                action["action"] = "delete_multiword_exists"
                action["merged_into"] = existing[0].lemma_ar
                if verbose:
                    print(
                        f"  DELETE multi-word (first word exists as {existing[0].lemma_ar}): "
                        f"{lemma.lemma_ar!r} (id={lemma.lemma_id})"
                    )
                _merge_into(db, lemma, existing[0], dry_run)
                merged_dupes += 1
            else:
                action["action"] = "delete_multiword_new"
                if verbose:
                    print(
                        f"  DELETE multi-word: "
                        f"{lemma.lemma_ar!r} (id={lemma.lemma_id})"
                    )
                _delete_lemma(db, lemma, dry_run)
                deleted_multiword += 1

            changes.append(action)
            continue

        # Punctuation or slash fix — update in place
        new_bare = compute_bare_form(cleaned)

        # Check for dedup collision
        existing = [
            l for l in bare_to_lemmas.get(new_bare, [])
            if l.lemma_id != lemma.lemma_id
        ]
        if existing:
            action["action"] = "merge_dedup"
            action["merged_into"] = existing[0].lemma_ar
            if verbose:
                print(
                    f"  MERGE dedup: {lemma.lemma_ar!r} → {existing[0].lemma_ar!r} "
                    f"(id={lemma.lemma_id} → {existing[0].lemma_id})"
                )
            _merge_into(db, lemma, existing[0], dry_run)
            merged_dupes += 1
        else:
            if "slash_split" in warnings:
                action["action"] = "fix_slash"
                fixed_slash += 1
            else:
                action["action"] = "fix_punct"
                fixed_punct += 1

            if verbose:
                print(f"  FIX: {lemma.lemma_ar!r} → {cleaned!r} (id={lemma.lemma_id})")

            if not dry_run:
                lemma.lemma_ar = cleaned
                lemma.lemma_ar_bare = new_bare
                # Update the bare_to_lemmas map
                bare_to_lemmas.setdefault(new_bare, []).append(lemma)

        changes.append(action)

    if not dry_run:
        db.commit()

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n{prefix}Results ({total} lemmas scanned):")
    print(f"  Fixed punctuation: {fixed_punct}")
    print(f"  Fixed slash-separated: {fixed_slash}")
    print(f"  Deleted (multi-word/empty): {deleted_multiword}")
    print(f"  Merged duplicates: {merged_dupes}")
    print(f"  Total changes: {fixed_punct + fixed_slash + deleted_multiword + merged_dupes}")

    db.close()
    return changes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean up dirty lemma text")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Show what would change (default)")
    parser.add_argument("--apply", action="store_true",
                        help="Actually apply changes")
    parser.add_argument("--verbose", action="store_true",
                        help="Show per-lemma details")
    args = parser.parse_args()

    dry_run = not args.apply
    if not dry_run:
        print("=== APPLYING CHANGES ===\n")
    else:
        print("=== DRY RUN (use --apply to commit) ===\n")

    changes = cleanup(dry_run=dry_run, verbose=args.verbose or dry_run)
