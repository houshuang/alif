"""Focus cohort — limits active review pool for intensive treatment.

Caps the number of words in active review to MAX_COHORT_SIZE.
Priority: acquiring words first, then FSRS due words by lowest stability.
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import UserLemmaKnowledge
from app.services.fsrs_service import parse_json_column

MAX_COHORT_SIZE = 100


def get_focus_cohort(db: Session) -> set[int]:
    """Get the set of lemma_ids in the active focus cohort.

    Always includes all acquiring words. Fills remaining slots with
    FSRS due words sorted by lowest stability (most fragile first).
    """
    now = datetime.now(timezone.utc)

    all_active = (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.knowledge_state.notin_(["suspended", "encountered"]),
        )
        .all()
    )

    cohort: set[int] = set()
    fsrs_candidates: list[tuple[int, float]] = []

    for k in all_active:
        if k.knowledge_state == "acquiring":
            cohort.add(k.lemma_id)
        elif k.fsrs_card_json:
            card_data = parse_json_column(k.fsrs_card_json)
            due_str = card_data.get("due")
            if due_str:
                due_dt = datetime.fromisoformat(due_str)
                if due_dt.tzinfo is None:
                    due_dt = due_dt.replace(tzinfo=timezone.utc)
                if due_dt <= now:
                    stability = card_data.get("stability") or 0.0
                    fsrs_candidates.append((k.lemma_id, stability))

    # Fill remaining slots with lowest-stability FSRS words
    remaining = MAX_COHORT_SIZE - len(cohort)
    if remaining > 0:
        fsrs_candidates.sort(key=lambda x: x[1])
        for lid, _ in fsrs_candidates[:remaining]:
            cohort.add(lid)

    return cohort


def get_cohort_stats(db: Session) -> dict:
    """Get breakdown of the focus cohort composition."""
    now = datetime.now(timezone.utc)

    all_active = (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.knowledge_state.notin_(["suspended", "encountered"]),
        )
        .all()
    )

    acquiring = 0
    fsrs_due = 0
    fsrs_not_due = 0

    for k in all_active:
        if k.knowledge_state == "acquiring":
            acquiring += 1
        elif k.fsrs_card_json:
            card_data = parse_json_column(k.fsrs_card_json)
            due_str = card_data.get("due")
            if due_str:
                due_dt = datetime.fromisoformat(due_str)
                if due_dt.tzinfo is None:
                    due_dt = due_dt.replace(tzinfo=timezone.utc)
                if due_dt <= now:
                    fsrs_due += 1
                else:
                    fsrs_not_due += 1

    cohort = get_focus_cohort(db)

    return {
        "cohort_size": len(cohort),
        "max_cohort_size": MAX_COHORT_SIZE,
        "acquiring": acquiring,
        "fsrs_due": fsrs_due,
        "fsrs_not_due": fsrs_not_due,
        "outside_cohort": max(0, fsrs_due - (MAX_COHORT_SIZE - acquiring)),
    }
