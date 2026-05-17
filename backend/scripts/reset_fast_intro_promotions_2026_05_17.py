"""Reset acquisition cards promoted on intro-card working memory.

One-shot cleanup for the 2026-05-13..17 intro overload audit. The script keeps
review history intact and only moves current acquiring words back to Box 1 due
now when their first correct acquisition review happened inside the intro-card
working-memory gap.

Default is dry-run. Use --apply to commit.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models import ActivityLog, Lemma, ReviewLog, UserLemmaKnowledge
from app.services.acquisition_service import FAST_GRAD_INTRO_GAP


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _gap_minutes(first_review: ReviewLog, ulk: UserLemmaKnowledge) -> float | None:
    intro_shown = _aware(ulk.experiment_intro_shown_at)
    reviewed_at = _aware(first_review.reviewed_at)
    if intro_shown is None or reviewed_at is None:
        return None
    gap = (reviewed_at - intro_shown).total_seconds() / 60
    if gap < 0:
        return None
    return gap


def _candidate_reason(
    logs: list[ReviewLog],
    ulk: UserLemmaKnowledge,
    gap_limit: timedelta,
) -> str | None:
    if not logs:
        return None
    first = logs[0]
    gap = _gap_minutes(first, ulk)
    if gap is None or gap >= gap_limit.total_seconds() / 60:
        return None

    times_seen = ulk.times_seen or 0
    times_correct = ulk.times_correct or 0
    accuracy = times_correct / times_seen if times_seen else 0.0

    if (
        ulk.acquisition_box == 2
        and len(logs) == 1
        and first.rating >= 3
        and times_seen == 1
        and times_correct == 1
    ):
        return "box2_one_fast_correct"

    if (
        ulk.acquisition_box >= 2
        and first.rating >= 3
        and any(log.rating < 3 for log in logs[1:])
        and accuracy < 0.80
    ):
        return "fast_correct_then_fail_low_acc"

    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="commit the reset")
    parser.add_argument(
        "--gap-minutes",
        type=float,
        default=FAST_GRAD_INTRO_GAP.total_seconds() / 60,
        help="intro-to-first-review window treated as working memory",
    )
    args = parser.parse_args()

    db = SessionLocal()
    now = datetime.now(timezone.utc)
    gap_limit = timedelta(minutes=args.gap_minutes)

    candidates: list[dict[str, Any]] = []
    rows = (
        db.query(UserLemmaKnowledge, Lemma)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            UserLemmaKnowledge.knowledge_state == "acquiring",
            UserLemmaKnowledge.acquisition_box.isnot(None),
        )
        .order_by(UserLemmaKnowledge.lemma_id)
        .all()
    )

    for ulk, lemma in rows:
        logs = (
            db.query(ReviewLog)
            .filter(
                ReviewLog.lemma_id == ulk.lemma_id,
                ReviewLog.is_acquisition == True,  # noqa: E712
            )
            .order_by(ReviewLog.reviewed_at.asc(), ReviewLog.id.asc())
            .all()
        )
        reason = _candidate_reason(logs, ulk, gap_limit)
        if not reason:
            continue
        first = logs[0]
        gap = _gap_minutes(first, ulk)
        candidates.append(
            {
                "lemma_id": ulk.lemma_id,
                "lemma_ar": lemma.lemma_ar,
                "gloss_en": lemma.gloss_en,
                "source": ulk.source,
                "old_box": ulk.acquisition_box,
                "times_seen": ulk.times_seen or 0,
                "times_correct": ulk.times_correct or 0,
                "ratings": "".join(str(log.rating) for log in logs),
                "gap_minutes": round(gap or 0.0, 2),
                "reason": reason,
            }
        )

    summary = {
        "apply": args.apply,
        "candidate_count": len(candidates),
        "by_reason": dict(Counter(c["reason"] for c in candidates)),
        "by_old_box": dict(Counter(c["old_box"] for c in candidates)),
        "by_source": dict(Counter(c["source"] for c in candidates)),
        "candidates": candidates,
    }

    if args.apply and candidates:
        ids = [c["lemma_id"] for c in candidates]
        for ulk in (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id.in_(ids))
            .all()
        ):
            ulk.acquisition_box = 1
            ulk.acquisition_next_due = now
        db.add(
            ActivityLog(
                event_type="fast_intro_promotions_reset",
                summary=(
                    f"Reset {len(candidates)} acquisition words to Box 1 after "
                    "fast intro-card promotion audit"
                ),
                detail_json={
                    "gap_minutes": args.gap_minutes,
                    "candidate_count": len(candidates),
                    "by_reason": summary["by_reason"],
                    "by_old_box": summary["by_old_box"],
                    "by_source": summary["by_source"],
                    "lemma_ids": ids,
                },
            )
        )
        db.commit()
        summary["committed"] = True
    else:
        db.rollback()
        summary["committed"] = False

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
