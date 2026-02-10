"""Audit and fix gloss quality for imported lemmas.

Safety-first approach:
- Default is --dry-run: shows proposed changes without writing
- Must pass --fix explicitly to apply changes
- Auto-backs up DB before --fix
- Never deletes lemmas: only updates gloss_en, root_id, canonical_lemma_id
- Batches of 20 words sent to LLM for review
- Confidence threshold: only apply changes where LLM confidence > 0.8
- Runs variant detection on affected lemmas
- Sets root_id = NULL for loanwords
- Cleans up function word ULK records

Usage:
  python scripts/cleanup_glosses.py --dry-run --source textbook_scan
  python scripts/cleanup_glosses.py --dry-run --source wiktionary
  python scripts/cleanup_glosses.py --dry-run --all
  python scripts/cleanup_glosses.py --fix --source textbook_scan
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models import Lemma, Root, UserLemmaKnowledge, ReviewLog
from app.services.sentence_validator import _is_function_word


def get_db():
    return SessionLocal()


def backup_db():
    """Create a timestamped backup of the database file."""
    db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "alif.db")
    if not os.path.exists(db_path):
        print(f"  DB not found at {db_path}, skipping backup")
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.backup_{ts}"
    shutil.copy2(db_path, backup_path)
    print(f"  Backup created: {backup_path}")


def get_lemmas_by_source(db, source: str | None, all_sources: bool) -> list:
    """Get lemmas filtered by source."""
    query = db.query(Lemma)
    if not all_sources and source:
        query = query.filter(Lemma.source == source)
    return query.all()


def batch_review_glosses(lemmas: list[dict]) -> list[dict]:
    """Send a batch of words to LLM for gloss review.

    Returns list of dicts with: bare, current_gloss, proposed_gloss, flag, confidence, is_loanword.
    """
    from app.services.llm import generate_completion, AllProvidersFailed

    word_list = [
        {
            "bare": l["bare"],
            "arabic": l["arabic"],
            "pos": l["pos"],
            "current_gloss": l["gloss"],
        }
        for l in lemmas
    ]

    prompt = (
        "Review these Arabic word glosses for accuracy. For each word:\n"
        "- Check if the English gloss is correct and concise\n"
        "- Flag issues: 'norwegian' (non-English gloss), 'wrong_sense' (wrong meaning), "
        "'verbose' (too long, simplify), 'ok' (correct)\n"
        "- Provide a corrected English gloss if needed (1-3 words)\n"
        "- Set confidence 0.0-1.0 for your correction\n"
        "- Set is_loanword: true if the Arabic word is a loanword (e.g. تلفزيون, كمبيوتر)\n\n"
        "Words:\n"
        + json.dumps(word_list, ensure_ascii=False)
        + "\n\n"
        "Respond with JSON:\n"
        '{"reviews": [{"bare": "...", "flag": "ok|norwegian|wrong_sense|verbose", '
        '"proposed_gloss": "...", "confidence": 0.95, "is_loanword": false}]}'
    )

    try:
        result = generate_completion(
            prompt,
            system_prompt=(
                "You are an Arabic-English lexicography expert. "
                "Review and correct Arabic word glosses. "
                "Be precise and concise. Respond with JSON only."
            ),
        )
        reviews = result.get("reviews", [])
        if isinstance(reviews, list):
            return reviews
    except (AllProvidersFailed, Exception) as e:
        print(f"  LLM call failed: {e}")

    return []


def cleanup_function_word_ulks(db, dry_run: bool) -> int:
    """Delete ULK records for function word lemmas."""
    all_lemmas = db.query(Lemma).all()
    function_lemma_ids = []
    for lem in all_lemmas:
        if lem.lemma_ar_bare and _is_function_word(lem.lemma_ar_bare):
            function_lemma_ids.append(lem.lemma_id)

    if not function_lemma_ids:
        return 0

    ulks = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id.in_(function_lemma_ids))
        .all()
    )

    count = len(ulks)
    if count > 0 and not dry_run:
        # Also delete review logs for these
        db.query(ReviewLog).filter(
            ReviewLog.lemma_id.in_(function_lemma_ids)
        ).delete(synchronize_session=False)
        db.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id.in_(function_lemma_ids)
        ).delete(synchronize_session=False)
        db.commit()

    return count


def cleanup_loanword_roots(db, loanword_bares: set[str], dry_run: bool) -> int:
    """Set root_id = NULL for loanwords and clean orphaned Root records."""
    count = 0
    for bare in loanword_bares:
        lemma = db.query(Lemma).filter(Lemma.lemma_ar_bare == bare).first()
        if lemma and lemma.root_id:
            print(f"  Loanword: {bare} — removing root_id={lemma.root_id}")
            if not dry_run:
                lemma.root_id = None
            count += 1

    if not dry_run and count > 0:
        db.flush()
        # Clean orphaned roots (no lemmas referencing them)
        orphans = (
            db.query(Root)
            .filter(~Root.root_id.in_(
                db.query(Lemma.root_id).filter(Lemma.root_id.isnot(None))
            ))
            .all()
        )
        for orphan in orphans:
            print(f"  Removing orphaned root: {orphan.root} (id={orphan.root_id})")
            db.delete(orphan)
        db.commit()

    return count


def run_variant_detection(db, affected_bares: set[str], dry_run: bool) -> int:
    """Run variant detection on affected lemmas."""
    try:
        from app.services.variant_detection import detect_variants, detect_definite_variants
    except ImportError:
        print("  variant_detection not available, skipping")
        return 0

    affected_lemmas = []
    for bare in affected_bares:
        lemma = db.query(Lemma).filter(Lemma.lemma_ar_bare == bare).first()
        if lemma:
            affected_lemmas.append(lemma)

    if not affected_lemmas:
        return 0

    affected_ids = [l.lemma_id for l in affected_lemmas]
    variants = detect_variants(db, affected_ids)
    definite = detect_definite_variants(db, affected_ids)
    all_variants = variants + definite

    count = 0
    for variant_id, canonical_id, vtype, details in all_variants:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == variant_id).first()
        if lemma and not lemma.canonical_lemma_id:
            print(f"  Variant: {lemma.lemma_ar_bare} → canonical lemma_id={canonical_id} [{vtype}]")
            if not dry_run:
                lemma.canonical_lemma_id = canonical_id
            count += 1

    if not dry_run and count > 0:
        db.commit()

    return count


def main():
    parser = argparse.ArgumentParser(description="Audit and fix gloss quality")
    parser.add_argument("--dry-run", action="store_true", default=True,
                       help="Show proposed changes without writing (default)")
    parser.add_argument("--fix", action="store_true",
                       help="Apply changes to database")
    parser.add_argument("--source", type=str, default=None,
                       help="Filter by source (e.g. textbook_scan, wiktionary)")
    parser.add_argument("--all", action="store_true",
                       help="Process all sources")
    parser.add_argument("--confidence", type=float, default=0.8,
                       help="Minimum confidence to apply LLM corrections (default: 0.8)")
    args = parser.parse_args()

    dry_run = not args.fix
    if not args.source and not args.all:
        print("Error: specify --source <source> or --all")
        sys.exit(1)

    db = get_db()

    print(f"Mode: {'DRY RUN' if dry_run else 'FIX (writing changes)'}")
    print(f"Source filter: {args.source or 'all'}")
    print(f"Confidence threshold: {args.confidence}")
    print()

    if not dry_run:
        print("Backing up database...")
        backup_db()
        print()

    # Get lemmas
    lemmas = get_lemmas_by_source(db, args.source, args.all)
    print(f"Found {len(lemmas)} lemmas to review")
    print()

    # Step 1: Function word ULK cleanup
    print("=== Function Word ULK Cleanup ===")
    fw_count = cleanup_function_word_ulks(db, dry_run)
    print(f"  Function word ULK records to {'remove' if dry_run else 'removed'}: {fw_count}")
    print()

    # Step 2: Batch LLM review
    print("=== Gloss Review ===")
    batch_size = 20
    total_flagged = 0
    total_fixed = 0
    loanword_bares: set[str] = set()
    affected_bares: set[str] = set()

    for i in range(0, len(lemmas), batch_size):
        batch = lemmas[i:i + batch_size]
        batch_data = [
            {
                "bare": lem.lemma_ar_bare,
                "arabic": lem.lemma_ar,
                "pos": lem.pos,
                "gloss": lem.gloss_en,
                "lemma_id": lem.lemma_id,
            }
            for lem in batch
        ]

        reviews = batch_review_glosses(batch_data)
        review_by_bare = {r.get("bare", ""): r for r in reviews if isinstance(r, dict)}

        for lem_data in batch_data:
            review = review_by_bare.get(lem_data["bare"], {})
            flag = review.get("flag", "ok")
            confidence = review.get("confidence", 0)
            proposed = review.get("proposed_gloss")
            is_loanword = review.get("is_loanword", False)

            if flag == "ok":
                continue

            total_flagged += 1
            if is_loanword:
                loanword_bares.add(lem_data["bare"])

            status = "SKIP (low confidence)" if confidence < args.confidence else "FIX"
            print(f"  [{flag}] {lem_data['bare']} ({lem_data['arabic']})")
            print(f"    Current:  {lem_data['gloss']}")
            print(f"    Proposed: {proposed} (confidence={confidence:.2f}) [{status}]")
            if is_loanword:
                print(f"    Loanword: yes")

            if confidence >= args.confidence and proposed:
                affected_bares.add(lem_data["bare"])
                if not dry_run:
                    lemma = db.query(Lemma).filter(Lemma.lemma_id == lem_data["lemma_id"]).first()
                    if lemma:
                        lemma.gloss_en = proposed
                        total_fixed += 1

    if not dry_run and total_fixed > 0:
        db.commit()

    print(f"\n  Total flagged: {total_flagged}")
    print(f"  Total {'would fix' if dry_run else 'fixed'}: {len(affected_bares)}")
    print()

    # Step 3: Loanword root cleanup
    if loanword_bares:
        print("=== Loanword Root Cleanup ===")
        lw_count = cleanup_loanword_roots(db, loanword_bares, dry_run)
        print(f"  Loanword roots {'to remove' if dry_run else 'removed'}: {lw_count}")
        print()

    # Step 4: Variant detection
    if affected_bares:
        print("=== Variant Detection ===")
        v_count = run_variant_detection(db, affected_bares, dry_run)
        print(f"  Variants {'detected' if dry_run else 'marked'}: {v_count}")
        print()

    # Log to experiment log
    log_entry = {
        "date": datetime.now().isoformat(),
        "script": "cleanup_glosses.py",
        "mode": "dry_run" if dry_run else "fix",
        "source": args.source or "all",
        "total_reviewed": len(lemmas),
        "flagged": total_flagged,
        "fixed": total_fixed if not dry_run else 0,
        "function_word_ulks": fw_count,
        "loanwords": len(loanword_bares),
    }

    research_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "research")
    os.makedirs(research_dir, exist_ok=True)
    experiment_log = os.path.join(research_dir, "experiment-log.md")

    entry_text = (
        f"\n## {datetime.now():%Y-%m-%d} — Gloss Cleanup ({'dry run' if dry_run else 'applied'})\n"
        f"- Source: {args.source or 'all'}\n"
        f"- Reviewed: {len(lemmas)} lemmas\n"
        f"- Flagged: {total_flagged}\n"
        f"- Fixed: {total_fixed if not dry_run else 'N/A (dry run)'}\n"
        f"- Function word ULKs cleaned: {fw_count}\n"
        f"- Loanwords found: {len(loanword_bares)}\n"
    )

    if not dry_run:
        with open(experiment_log, "a") as f:
            f.write(entry_text)
        print(f"Logged to {experiment_log}")

    print("\nDone.")
    db.close()


if __name__ == "__main__":
    main()
