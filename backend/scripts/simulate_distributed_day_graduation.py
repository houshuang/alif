#!/usr/bin/env python3
"""Read-only workload replay for the distributed-day graduation policy.

The replay identifies acquisition reviews that actually graduated under an
early tier despite having evidence from only one UTC calendar day. It then
uses the first later successful word review on another day as a conservative
proxy for when a one-day deferred confirmation could have completed.

This estimates intervention volume and backlog, not the causal retention
benefit. The latter requires prospective versioned outcomes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import ReviewLog


EARLY_REASONS = {"first_correct", "perfect_accuracy", "high_accuracy"}


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _peak_pending(events: list[tuple[datetime, int]]) -> int:
    pending = 0
    peak = 0
    # Completions precede additions at the same timestamp.
    for _at, delta in sorted(events, key=lambda item: (item[0], item[1])):
        pending += delta
        peak = max(peak, pending)
    return peak


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--cutoff", required=True, type=_parse_datetime)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-db-sha256")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")

    before_hash = _sha256(args.db)
    if args.expected_db_sha256 and before_hash != args.expected_db_sha256:
        parser.error("database SHA-256 does not match expected snapshot")
    engine = create_engine(
        f"sqlite:///file:{args.db.resolve()}?mode=ro&immutable=1&uri=true",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _query_only(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA query_only=ON")

    db = sessionmaker(bind=engine)()
    try:
        rows = (
            db.query(ReviewLog)
            .filter(ReviewLog.reviewed_at <= args.cutoff.replace(tzinfo=None))
            .order_by(ReviewLog.reviewed_at, ReviewLog.id)
            .all()
        )
        by_lemma: dict[int, list[ReviewLog]] = defaultdict(list)
        active_dates: set[str] = set()
        for row in rows:
            reviewed_at = _aware(row.reviewed_at)
            if reviewed_at is None:
                continue
            active_dates.add(reviewed_at.date().isoformat())
            by_lemma[row.lemma_id].append(row)

        candidates: list[dict[str, Any]] = []
        policy_start: datetime | None = None
        for lemma_rows in by_lemma.values():
            acquisition_dates: set[str] = set()
            for index, row in enumerate(lemma_rows):
                reviewed_at = _aware(row.reviewed_at)
                if reviewed_at is None:
                    continue
                if row.is_acquisition:
                    acquisition_dates.add(reviewed_at.date().isoformat())
                metadata = _json_dict(row.fsrs_log_json)
                if metadata.get("graduation_policy_version") == 2:
                    policy_start = min(policy_start or reviewed_at, reviewed_at)
                reason = metadata.get("graduation_reason")
                if (
                    metadata.get("graduated") is not True
                    or reason not in EARLY_REASONS
                    or len(acquisition_dates) >= 2
                ):
                    continue
                confirmation = None
                for later in lemma_rows[index + 1:]:
                    later_at = _aware(later.reviewed_at)
                    if later_at is None or later.rating < 3:
                        continue
                    if later_at.date() == reviewed_at.date():
                        continue
                    confirmation = later_at
                    break
                candidates.append({
                    "lemma_id": row.lemma_id,
                    "reason": reason,
                    "blocked_at": reviewed_at,
                    "confirmation_at": confirmation,
                })
    finally:
        db.close()
        engine.dispose()

    cohort_start = min(
        (row["blocked_at"] for row in candidates),
        default=policy_start or args.cutoff,
    )
    if policy_start is None:
        policy_start = cohort_start
    active_policy_dates = {
        date for date in active_dates
        if date >= cohort_start.date().isoformat()
    }
    resolved = [row for row in candidates if row["confirmation_at"] is not None]
    delays = [
        (row["confirmation_at"] - row["blocked_at"]).total_seconds() / 86400
        for row in resolved
    ]
    events: list[tuple[datetime, int]] = []
    for row in candidates:
        events.append((row["blocked_at"], 1))
        if row["confirmation_at"] is not None:
            events.append((row["confirmation_at"], -1))
    daily = Counter(row["blocked_at"].date().isoformat() for row in candidates)
    output = {
        "schema_version": 1,
        "method": "distributed-day-graduation-workload-replay-v1",
        "database": {"filename": args.db.name, "sha256": before_hash},
        "cutoff": args.cutoff.isoformat(),
        "observed_policy_start": policy_start.isoformat(),
        "candidate_cohort_start": cohort_start.isoformat(),
        "counterfactual_candidates": {
            "same_day_early_graduations": len(candidates),
            "by_reason": dict(sorted(Counter(
                row["reason"] for row in candidates
            ).items())),
            "active_days_in_observed_policy_window": len(active_policy_dates),
            "mean_deferred_confirmations_per_active_day": round(
                len(candidates) / len(active_policy_dates), 3
            ) if active_policy_dates else None,
            "maximum_on_one_day": max(daily.values(), default=0),
            "peak_pending_using_observed_followup": _peak_pending(events),
        },
        "observed_later_success_proxy": {
            "resolved_on_later_day": len(resolved),
            "unresolved_by_cutoff": len(candidates) - len(resolved),
            "resolved_fraction": round(
                len(resolved) / len(candidates), 4
            ) if candidates else None,
            "median_calendar_days": round(median(delays), 3) if delays else None,
            "mean_calendar_days": round(mean(delays), 3) if delays else None,
            "within_2_days_fraction": round(
                sum(delay <= 2 for delay in delays) / len(candidates), 4
            ) if candidates else None,
            "within_7_days_fraction": round(
                sum(delay <= 7 for delay in delays) / len(candidates), 4
            ) if candidates else None,
        },
        "proposed_policy": {
            "first_day_success": "remain acquiring; Box 2 due next day",
            "second_day_success": "graduate immediately as distributed_confirmation",
            "maximum_intended_added_successes_per_word": 1,
            "feature_flag": "ALIF_DISTRIBUTED_DAY_GRADUATION",
        },
        "limitations": [
            "The replay covers only reviews with explicit graduation-reason telemetry.",
            "Observed later reviews occurred under the old FSRS policy; an acquiring confirmation should usually be delivered sooner.",
            "This estimates workload and deliverability, not the causal retention effect.",
        ],
    }
    if _sha256(args.db) != before_hash:
        parser.error("database changed during replay")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "candidates": output["counterfactual_candidates"],
        "followup": output["observed_later_success_proxy"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
