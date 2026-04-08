"""Roll back the April 1, 2026 glitch session and fix lemma 441 (ال).

Session 214d75f5-b406-45cb-90ca-b7e4256bdf9a had 102 bogus "Again" reviews
from an offline sync glitch (sub-200ms response times, impossible for real use).
This corrupted FSRS state for 11 words that are still stuck as "lapsed".

Also: deactivates sentence 20403 (separated ال) and suspends lemma 441.
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone
from app.database import SessionLocal
from app.models import ReviewLog, SentenceReviewLog, UserLemmaKnowledge, Sentence, Lemma

GLITCH_SESSION = "214d75f5-b406-45cb-90ca-b7e4256bdf9a"


def rollback_session(db, dry_run=True):
    # Find all bad reviews (rating=1) in the glitch session
    bad_reviews = (
        db.query(ReviewLog)
        .filter(
            ReviewLog.session_id == GLITCH_SESSION,
            ReviewLog.rating == 1,
        )
        .all()
    )
    print(f"Found {len(bad_reviews)} bad reviews in glitch session")

    if not bad_reviews:
        print("No bad reviews found — already rolled back?")
        return

    # Group by lemma_id, find the FIRST bad review per lemma (has the pre-state)
    first_bad_by_lemma: dict[int, ReviewLog] = {}
    all_bad_ids = []
    for r in bad_reviews:
        all_bad_ids.append(r.id)
        if r.lemma_id not in first_bad_by_lemma:
            first_bad_by_lemma[r.lemma_id] = r

    affected_lemma_ids = set(first_bad_by_lemma.keys())
    print(f"Affected lemmas: {len(affected_lemma_ids)}")

    # For each affected lemma, check if it needs state restoration
    restored = 0
    for lemma_id, first_review in first_bad_by_lemma.items():
        ulk = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id == lemma_id)
            .first()
        )
        if not ulk:
            print(f"  WARNING: lemma {lemma_id} has no UserLemmaKnowledge")
            continue

        log_data = first_review.fsrs_log_json
        if not log_data:
            print(f"  WARNING: review {first_review.id} has no fsrs_log_json")
            continue

        if isinstance(log_data, str):
            log_data = json.loads(log_data)

        pre_card = log_data.get("pre_card")
        pre_state = log_data.get("pre_knowledge_state")
        pre_seen = log_data.get("pre_times_seen", 0)
        pre_correct = log_data.get("pre_times_correct", 0)

        # Check if a LATER legitimate review exists
        later_good_review = (
            db.query(ReviewLog)
            .filter(
                ReviewLog.lemma_id == lemma_id,
                ReviewLog.session_id != GLITCH_SESSION,
                ReviewLog.reviewed_at > first_review.reviewed_at,
            )
            .order_by(ReviewLog.reviewed_at.desc())
            .first()
        )

        if later_good_review:
            # Word was reviewed legitimately after the glitch.
            # Just fix the times_seen/times_correct counters by removing the
            # bad review count.
            bad_count = sum(1 for r in bad_reviews if r.lemma_id == lemma_id)
            print(
                f"  Lemma {lemma_id} ({ulk.knowledge_state}): "
                f"has later review — decrementing times_seen by {bad_count}"
            )
            if not dry_run:
                ulk.times_seen = max(0, (ulk.times_seen or 0) - bad_count)
        else:
            # No later review — restore to pre-glitch state
            pre_stab = pre_card.get("stability", "?") if pre_card else "?"
            cur_stab = "?"
            if ulk.fsrs_card_json:
                card_data = ulk.fsrs_card_json
                if isinstance(card_data, str):
                    card_data = json.loads(card_data)
                cur_stab = card_data.get("stability", "?")

            print(
                f"  Lemma {lemma_id}: RESTORING {ulk.knowledge_state}(stab={cur_stab}) "
                f"-> {pre_state}(stab={pre_stab})"
            )
            restored += 1

            if not dry_run:
                ulk.knowledge_state = pre_state
                ulk.fsrs_card_json = pre_card
                ulk.times_seen = pre_seen
                ulk.times_correct = pre_correct

                # Find the most recent review BEFORE the glitch to set last_reviewed
                prev_review = (
                    db.query(ReviewLog)
                    .filter(
                        ReviewLog.lemma_id == lemma_id,
                        ReviewLog.reviewed_at < first_review.reviewed_at,
                    )
                    .order_by(ReviewLog.reviewed_at.desc())
                    .first()
                )
                if prev_review:
                    ulk.last_reviewed = prev_review.reviewed_at

    # Delete bad review_log entries
    print(f"\nDeleting {len(all_bad_ids)} bad review_log entries...")
    if not dry_run:
        db.query(ReviewLog).filter(ReviewLog.id.in_(all_bad_ids)).delete(
            synchronize_session=False
        )

    # Delete sentence_review_log entries for the glitch session
    bad_srl = (
        db.query(SentenceReviewLog)
        .filter(SentenceReviewLog.session_id == GLITCH_SESSION)
        .all()
    )
    bad_srl_ids = [s.id for s in bad_srl]
    print(f"Deleting {len(bad_srl_ids)} sentence_review_log entries...")
    if not dry_run:
        db.query(SentenceReviewLog).filter(
            SentenceReviewLog.id.in_(bad_srl_ids)
        ).delete(synchronize_session=False)

    print(f"\nSummary: {restored} words fully restored, {len(affected_lemma_ids) - restored} counter-adjusted")
    return restored


def fix_al_lemma(db, dry_run=True):
    """Deactivate sentence 20403 and suspend lemma 441."""
    # Deactivate sentence with separated ال
    sent = db.query(Sentence).filter(Sentence.id == 20403).first()
    if sent:
        print(f"\nSentence 20403: is_active={sent.is_active}, text={sent.arabic_text[:50]}")
        if not dry_run:
            sent.is_active = False
            print("  -> Deactivated")
    else:
        print("\nSentence 20403 not found (may not exist in local DB)")

    # Suspend lemma 441 (ال as standalone particle)
    lemma = db.query(Lemma).filter(Lemma.lemma_id == 441).first()
    if lemma:
        print(f"Lemma 441: {lemma.lemma_ar} ({lemma.pos}), gloss={lemma.gloss_en}")
        ulk = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id == 441)
            .first()
        )
        if ulk:
            print(f"  Knowledge: state={ulk.knowledge_state}, box={ulk.acquisition_box}")
            if not dry_run:
                ulk.knowledge_state = "suspended"
                ulk.leech_suspended_at = datetime.now(timezone.utc)
                print("  -> Suspended")
    else:
        print("Lemma 441 not found (may not exist in local DB)")


def main():
    dry_run = "--execute" not in sys.argv
    if dry_run:
        print("=== DRY RUN === (pass --execute to apply changes)\n")
    else:
        print("=== EXECUTING === Changes will be committed\n")

    db = SessionLocal()
    try:
        rollback_session(db, dry_run=dry_run)
        fix_al_lemma(db, dry_run=dry_run)

        if not dry_run:
            db.commit()
            print("\nAll changes committed.")
        else:
            db.rollback()
            print("\nDry run complete — no changes made.")
    except Exception as e:
        db.rollback()
        print(f"\nERROR: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
