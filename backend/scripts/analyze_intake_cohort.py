#!/usr/bin/env python3
"""Read-only trajectory audit for one acquisition intake cohort.

All stored word-review rows count regardless of primary/collateral role. The
report describes observed exposure, workload, graduation, and suspension; it
does not treat success-conditioned graduation timing as an unconditional
forecast and does not simulate staged intake.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

try:
    from analyze_learning_system import (
        file_identity,
        open_read_only,
        parse_cli_datetime,
        parse_datetime,
        rounded,
        stable_json_bytes,
    )
except ModuleNotFoundError:
    from scripts.analyze_learning_system import (
        file_identity,
        open_read_only,
        parse_cli_datetime,
        parse_datetime,
        rounded,
        stable_json_bytes,
    )


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def iso_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sql_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(tzinfo=None).isoformat(" ")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--cutoff", type=parse_cli_datetime, required=True)
    parser.add_argument("--start", type=parse_cli_datetime, required=True)
    parser.add_argument("--end", type=parse_cli_datetime, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--current-baseline-summary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("output-dir already exists")
    if not args.start < args.end <= args.cutoff:
        parser.error("require start < end <= cutoff")

    db_before = file_identity(args.db)
    baseline_identity = file_identity(args.current_baseline_summary)
    baseline = json.loads(args.current_baseline_summary.read_bytes())
    if baseline["provenance"]["database"]["sha256"] != db_before["sha256"]:
        parser.error("baseline summary and database snapshot do not match")
    cutoff_text = iso_z(args.cutoff)
    if baseline["window"]["cutoff"] != cutoff_text:
        parser.error("baseline summary and cohort cutoff do not match")

    connection = open_read_only(args.db)
    try:
        cohort_rows = list(connection.execute(
            """
            SELECT lemma_id, knowledge_state, acquisition_box,
                   acquisition_started_at, entered_acquiring_at, graduated_at,
                   leech_suspended_at
            FROM user_lemma_knowledge
            WHERE source = ?
              AND acquisition_started_at >= ?
              AND acquisition_started_at < ?
            ORDER BY lemma_id
            """,
            (args.source, sql_time(args.start), sql_time(args.end)),
        ))
        cohort_ids = [int(row["lemma_id"]) for row in cohort_rows]
        if not cohort_ids:
            parser.error("cohort is empty")
        placeholders = ",".join("?" for _ in cohort_ids)
        review_rows = list(connection.execute(
            f"""
            SELECT lemma_id, reviewed_at, rating, session_id, review_mode,
                   credit_type, is_acquisition, sentence_id
            FROM review_log
            WHERE lemma_id IN ({placeholders})
              AND reviewed_at < ?
            ORDER BY reviewed_at, id
            """,
            (*cohort_ids, sql_time(args.cutoff)),
        ))
        session_rows = list(connection.execute(
            f"""
            SELECT session_id, MIN(reviewed_at) AS first_at
            FROM review_log
            WHERE session_id IS NOT NULL
              AND reviewed_at >= ?
              AND reviewed_at < ?
            GROUP BY session_id
            ORDER BY first_at, session_id
            """,
            (sql_time(args.start), sql_time(args.cutoff)),
        ))
    finally:
        connection.close()
    if file_identity(args.db) != db_before:
        parser.error("database changed during cohort audit")

    sessions = {
        row["session_id"]: index + 1 for index, row in enumerate(session_rows)
    }
    by_lemma: dict[int, list[Any]] = defaultdict(list)
    for row in review_rows:
        by_lemma[int(row["lemma_id"])].append(row)

    states = Counter()
    credit_types = Counter()
    review_modes = Counter()
    first_review_hours: list[float] = []
    first_review_session_ordinals: list[int] = []
    graduation_days: list[float] = []
    lemma_outputs = []
    total_successes = 0
    total_acquisition_reviews = 0
    reviewed_lemmas = 0
    for cohort_row in cohort_rows:
        lemma_id = int(cohort_row["lemma_id"])
        states[cohort_row["knowledge_state"]] += 1
        started_at = parse_datetime(cohort_row["acquisition_started_at"])
        graduated_at = parse_datetime(cohort_row["graduated_at"])
        suspended_at = parse_datetime(cohort_row["leech_suspended_at"])
        rows = by_lemma.get(lemma_id, [])
        acquisition_rows = [row for row in rows if row["is_acquisition"]]
        if rows:
            reviewed_lemmas += 1
        first_at = parse_datetime(rows[0]["reviewed_at"]) if rows else None
        first_session = rows[0]["session_id"] if rows else None
        if first_at and started_at:
            first_review_hours.append(
                (first_at - started_at).total_seconds() / 3600
            )
        if first_session in sessions:
            first_review_session_ordinals.append(sessions[first_session])
        for row in rows:
            credit_types[row["credit_type"] or "unknown"] += 1
            review_modes[row["review_mode"] or "unknown"] += 1
        successes = sum(row["rating"] >= 3 for row in acquisition_rows)
        total_successes += successes
        total_acquisition_reviews += len(acquisition_rows)
        if graduated_at and started_at:
            graduation_days.append(
                (graduated_at - started_at).total_seconds() / 86400
            )
        lemma_outputs.append({
            "lemma_id": lemma_id,
            "state_at_cutoff": cohort_row["knowledge_state"],
            "box_at_cutoff": cohort_row["acquisition_box"],
            "started_at": iso_z(started_at),
            "first_review_at": iso_z(first_at),
            "first_review_hours": rounded(
                (first_at - started_at).total_seconds() / 3600
                if first_at and started_at else None,
                3,
            ),
            "first_review_session_ordinal": (
                sessions.get(first_session) if first_session else None
            ),
            "graduated_at": iso_z(graduated_at),
            "graduation_days": rounded(
                (graduated_at - started_at).total_seconds() / 86400
                if graduated_at and started_at else None,
                3,
            ),
            "suspended_at": iso_z(suspended_at),
            "all_review_rows": len(rows),
            "acquisition_review_rows": len(acquisition_rows),
            "acquisition_successes": successes,
            "distinct_review_sessions": len({
                row["session_id"] for row in rows if row["session_id"]
            }),
        })

    earliest_start = min(
        parse_datetime(row["acquisition_started_at"]) for row in cohort_rows
    )
    followup_days = (args.cutoff - earliest_start).total_seconds() / 86400
    horizons = [1, 3, 5, 7, 10, 14, 21, 28]
    horizon_rows = []
    for days in horizons:
        if followup_days < days:
            continue
        threshold_by_lemma = {
            int(row["lemma_id"]): parse_datetime(row["acquisition_started_at"])
            + timedelta(days=days)
            for row in cohort_rows
        }
        graduated = sum(
            output["graduated_at"] is not None
            and parse_datetime(output["graduated_at"])
            <= threshold_by_lemma[output["lemma_id"]]
            for output in lemma_outputs
        )
        reviewed = sum(
            output["first_review_at"] is not None
            and parse_datetime(output["first_review_at"])
            <= threshold_by_lemma[output["lemma_id"]]
            for output in lemma_outputs
        )
        horizon_rows.append({
            "days": days,
            "first_reviewed": reviewed,
            "first_reviewed_fraction": rounded(reviewed / len(cohort_ids)),
            "graduated": graduated,
            "graduated_fraction": rounded(graduated / len(cohort_ids)),
        })

    calendar_reviews: dict[str, Counter] = defaultdict(Counter)
    for row in review_rows:
        day = parse_datetime(row["reviewed_at"]).date().isoformat()
        calendar_reviews[day]["all_word_reviews"] += 1
        if row["is_acquisition"]:
            calendar_reviews[day]["acquisition_reviews"] += 1
            if row["rating"] >= 3:
                calendar_reviews[day]["acquisition_successes"] += 1
    daily_rows = [
        {"day": day, **dict(counts)}
        for day, counts in sorted(calendar_reviews.items())
    ]

    result = {
        "schema_version": 1,
        "method_version": "intake-cohort-observed-trajectory-v1",
        "script": file_identity(Path(__file__).resolve()),
        "database": db_before,
        "baseline_summary": baseline_identity,
        "cutoff": cutoff_text,
        "cohort_definition": {
            "source": args.source,
            "start": iso_z(args.start),
            "end": iso_z(args.end),
            "semantics": "source match and acquisition_started_at in [start,end)",
        },
        "summary": {
            "cohort_size": len(cohort_ids),
            "states_at_cutoff": dict(sorted(states.items())),
            "reviewed_lemmas": reviewed_lemmas,
            "never_reviewed_lemmas": len(cohort_ids) - reviewed_lemmas,
            "ever_graduated": sum(output["graduated_at"] is not None for output in lemma_outputs),
            "currently_suspended": states["suspended"],
            "ever_suspended_with_timestamp": sum(
                output["suspended_at"] is not None for output in lemma_outputs
            ),
            "all_review_rows": len(review_rows),
            "acquisition_review_rows": total_acquisition_reviews,
            "acquisition_accuracy": rounded(
                total_successes / total_acquisition_reviews
                if total_acquisition_reviews else None
            ),
            "review_credit_types": dict(sorted(credit_types.items())),
            "review_modes": dict(sorted(review_modes.items())),
            "first_review_hours_median": rounded(median(first_review_hours), 2),
            "first_review_hours_p90": rounded(percentile(first_review_hours, 0.9), 2),
            "first_review_session_ordinal_median": rounded(
                median(first_review_session_ordinals)
                if first_review_session_ordinals else None,
                2,
            ),
            "first_review_session_ordinal_p90": rounded(
                percentile(first_review_session_ordinals, 0.9),
                2,
            ),
            "graduation_days_median_success_conditioned": rounded(
                median(graduation_days) if graduation_days else None,
                2,
            ),
            "graduation_days_p90_success_conditioned": rounded(
                percentile(graduation_days, 0.9),
                2,
            ),
            "followup_days": rounded(followup_days, 2),
        },
        "fixed_horizons": horizon_rows,
        "daily_reviews": daily_rows,
        "lemmas": lemma_outputs,
        "limitations": [
            "observed cohort audit, not a staged-intake counterfactual",
            "graduation-time quantiles are conditioned on successful graduation",
            "session ordinal counts all review sessions after cohort-window start",
            "current suspended state is not a historical competing-risk curve",
            "all primary and collateral word reviews count equally",
        ],
    }

    args.output_dir.mkdir(parents=True)
    (args.output_dir / "cohort.json").write_bytes(stable_json_bytes(result))
    with (args.output_dir / "lemmas.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(lemma_outputs[0]))
        writer.writeheader()
        writer.writerows(lemma_outputs)
    with (args.output_dir / "daily_reviews.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["day", "all_word_reviews", "acquisition_reviews", "acquisition_successes"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in daily_rows:
            writer.writerow({field: row.get(field, 0) for field in fields})
    with (args.output_dir / "fixed_horizons.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(horizon_rows[0]) if horizon_rows else ["days"])
        writer.writeheader()
        writer.writerows(horizon_rows)
    print(json.dumps(result["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
