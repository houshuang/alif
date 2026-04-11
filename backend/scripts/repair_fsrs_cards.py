#!/usr/bin/env python3
"""Repair FSRS cards by replaying actual review history.

Fixes two issues found in 2026-04-11 analysis:
1. Ghost-corrupted cards from April 1 quality gate deployment (93 words)
2. Stuck difficulty from FSRS lapse penalty asymmetry (189 words)

For each affected word, replays all non-acquisition FSRS reviews through
a fresh Scheduler to recompute the correct card state. This preserves the
full review history while fixing the card parameters.

Usage:
    python3 scripts/repair_fsrs_cards.py --dry-run    # preview changes
    python3 scripts/repair_fsrs_cards.py              # apply fixes
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fsrs import Scheduler, Card, Rating, State
from sqlalchemy import text

from app.database import SessionLocal

STATE_MAP = {
    State.Learning: "learning",
    State.Review: "known",
    State.Relearning: "lapsed",
}

RATING_MAP = {
    1: Rating.Again,
    2: Rating.Hard,
    3: Rating.Good,
    4: Rating.Easy,
}


def find_affected_words(db):
    """Find words that need FSRS card repair."""
    affected = {}

    # Category 1: Ghost-corrupted (card last_review doesn't match any review_log)
    rows = db.execute(text("""
        SELECT ulk.lemma_id, ulk.fsrs_card_json, ulk.knowledge_state,
               ulk.times_seen, ulk.times_correct,
               l.lemma_ar, l.gloss_en
        FROM user_lemma_knowledge ulk
        JOIN lemmas l ON ulk.lemma_id = l.lemma_id
        WHERE ulk.fsrs_card_json IS NOT NULL
        AND ulk.knowledge_state IN ('known', 'learning', 'lapsed')
    """)).fetchall()

    for r in rows:
        card_data = json.loads(r.fsrs_card_json) if isinstance(r.fsrs_card_json, str) else r.fsrs_card_json
        if not card_data:
            continue

        difficulty = card_data.get("difficulty") or 0
        stability = card_data.get("stability") or 0
        accuracy = r.times_correct / r.times_seen if r.times_seen and r.times_seen > 0 else 0

        # Count FSRS reviews (non-acquisition)
        fsrs_reviews = db.execute(text("""
            SELECT COUNT(*) as cnt FROM review_log
            WHERE lemma_id = :lid AND is_acquisition = 0
        """), {"lid": r.lemma_id}).fetchone().cnt

        # Criterion: difficulty > 7 AND accuracy > 75% AND 5+ FSRS reviews
        # This catches both ghost-corrupted and stuck-difficulty words
        if difficulty > 7.0 and accuracy > 0.75 and fsrs_reviews >= 5:
            affected[r.lemma_id] = {
                "arabic": r.lemma_ar,
                "gloss": r.gloss_en,
                "state": r.knowledge_state,
                "old_difficulty": difficulty,
                "old_stability": stability,
                "accuracy": accuracy,
                "fsrs_reviews": fsrs_reviews,
            }

    # Category 2: Null FSRS cards for lapsed words
    null_cards = db.execute(text("""
        SELECT ulk.lemma_id, l.lemma_ar, l.gloss_en, ulk.knowledge_state
        FROM user_lemma_knowledge ulk
        JOIN lemmas l ON ulk.lemma_id = l.lemma_id
        WHERE ulk.fsrs_card_json IS NULL
        AND ulk.knowledge_state IN ('lapsed', 'learning', 'known')
    """)).fetchall()

    for r in null_cards:
        fsrs_reviews = db.execute(text("""
            SELECT COUNT(*) as cnt FROM review_log
            WHERE lemma_id = :lid AND is_acquisition = 0
        """), {"lid": r.lemma_id}).fetchone().cnt

        if fsrs_reviews >= 1:
            affected[r.lemma_id] = {
                "arabic": r.lemma_ar,
                "gloss": r.gloss_en,
                "state": r.knowledge_state,
                "old_difficulty": None,
                "old_stability": None,
                "accuracy": 0,
                "fsrs_reviews": fsrs_reviews,
                "null_card": True,
            }

    return affected


def replay_reviews(db, lemma_id):
    """Replay all FSRS reviews for a lemma to recompute the correct card."""
    reviews = db.execute(text("""
        SELECT rating, reviewed_at FROM review_log
        WHERE lemma_id = :lid AND is_acquisition = 0
        ORDER BY reviewed_at ASC
    """), {"lid": lemma_id}).fetchall()

    if not reviews:
        return None, None

    scheduler = Scheduler()
    card = Card()

    for r in reviews:
        rating = RATING_MAP.get(r.rating, Rating.Good)
        reviewed_at = r.reviewed_at
        if isinstance(reviewed_at, str):
            for fmt in ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"]:
                try:
                    reviewed_at = datetime.strptime(reviewed_at.replace("+00:00", ""), fmt)
                    break
                except ValueError:
                    continue
        if not isinstance(reviewed_at, datetime):
            continue
        reviewed_at = reviewed_at.replace(tzinfo=timezone.utc)
        card, _ = scheduler.review_card(card, rating, reviewed_at)

    new_state = STATE_MAP.get(card.state, "learning")
    card_dict = card.to_dict()
    if new_state == "known" and card_dict.get("stability", 0) < 1.0:
        new_state = "lapsed"

    return card_dict, new_state


def main():
    parser = argparse.ArgumentParser(description="Repair FSRS cards by replaying review history")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        affected = find_affected_words(db)
        print(f"Found {len(affected)} words needing repair")

        repaired = 0
        skipped = 0
        changes = []

        for lemma_id, info in sorted(affected.items(), key=lambda x: x[1].get("old_difficulty") or 0, reverse=True):
            new_card, new_state = replay_reviews(db, lemma_id)
            if new_card is None:
                skipped += 1
                continue

            new_diff = new_card.get("difficulty", 0)
            new_stab = new_card.get("stability", 0)
            old_diff = info["old_difficulty"]
            old_stab = info["old_stability"]

            # Only apply if there's a meaningful improvement
            diff_improved = old_diff is None or (old_diff - new_diff > 0.5)
            is_null_card = info.get("null_card", False)

            if not diff_improved and not is_null_card:
                skipped += 1
                continue

            changes.append({
                "lemma_id": lemma_id,
                "arabic": info["arabic"],
                "gloss": info["gloss"],
                "old_diff": old_diff,
                "new_diff": new_diff,
                "old_stab": old_stab,
                "new_stab": new_stab,
                "old_state": info["state"],
                "new_state": new_state,
            })

            if not args.dry_run:
                db.execute(text("""
                    UPDATE user_lemma_knowledge
                    SET fsrs_card_json = :card, knowledge_state = :state
                    WHERE lemma_id = :lid
                """), {"card": json.dumps(new_card), "state": new_state, "lid": lemma_id})

            repaired += 1

        # Print summary
        print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Repair summary:")
        print(f"  Repaired: {repaired}")
        print(f"  Skipped (no improvement): {skipped}")

        if changes:
            print(f"\n  {'Arabic':15s} {'Gloss':25s} {'Diff':>12s} {'Stab':>12s} {'State':>15s}")
            print(f"  {'-'*15} {'-'*25} {'-'*12} {'-'*12} {'-'*15}")
            for c in changes[:40]:
                old_d = f"{c['old_diff']:.1f}" if c['old_diff'] is not None else "NULL"
                new_d = f"{c['new_diff']:.1f}"
                old_s = f"{c['old_stab']:.1f}" if c['old_stab'] is not None else "NULL"
                new_s = f"{c['new_stab']:.1f}"
                print(f"  {c['arabic']:15s} {(c['gloss'] or '?')[:25]:25s} "
                      f"{old_d:>5s}→{new_d:<5s} "
                      f"{old_s:>5s}→{new_s:<5s} "
                      f"{c['old_state']:>7s}→{c['new_state']}")
            if len(changes) > 40:
                print(f"  ... and {len(changes) - 40} more")

        if not args.dry_run and repaired > 0:
            db.commit()
            print(f"\nCommitted {repaired} repairs to database.")

            # Log the activity
            db.execute(text("""
                INSERT INTO activity_log (event_type, summary, detail_json, created_at)
                VALUES ('manual_action', :summary, :detail, :now)
            """), {
                "summary": f"Repaired {repaired} FSRS cards by replaying review history (ghost corruption + stuck difficulty)",
                "detail": json.dumps({
                    "repaired": repaired,
                    "skipped": skipped,
                    "sample": [c["lemma_id"] for c in changes[:20]],
                }),
                "now": datetime.now(timezone.utc).isoformat(),
            })
            db.commit()
        elif args.dry_run:
            print("\n[DRY RUN] No changes applied. Run without --dry-run to apply.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
