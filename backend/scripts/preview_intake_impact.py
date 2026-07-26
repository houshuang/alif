#!/usr/bin/env python3
"""Read-only WP8 intake-impact preview.

The preview classifies proposed lemma IDs against a pinned learner snapshot and
expresses their acquisition cost in recent observed capacity units. It has no apply
mode and never imports, creates, or starts a lemma.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

from analyze_learning_system import (
    file_identity,
    invalid_lemma_reason,
    iso_z,
    load_lemmas,
    open_read_only,
    parse_cli_datetime,
    parse_datetime,
    rounded,
    stable_json_bytes,
    table_columns,
)


LEARNED = {"known", "learning"}
IN_TRAINING = {"acquiring", "lapsed"}
PENDING = {"encountered", "new"}


def load_candidates(path: Path | None, anonymous_count: int) -> tuple[list[int], int]:
    lemma_ids: list[int] = []
    unresolved = anonymous_count
    if path:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise ValueError("candidate-file must contain a JSON list")
        for item in value:
            if isinstance(item, int):
                lemma_ids.append(item)
            elif isinstance(item, dict) and isinstance(item.get("lemma_id"), int):
                lemma_ids.append(item["lemma_id"])
            else:
                unresolved += 1
    return lemma_ids, unresolved


def date_range(start: datetime, cutoff: datetime) -> list[str]:
    days = []
    current = start.date()
    while current < cutoff.date() or (current == cutoff.date() and not days):
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def recent_capacity(connection, start: datetime, cutoff: datetime) -> dict[str, Any]:
    start_sql = start.replace(tzinfo=None).isoformat(" ")
    cutoff_sql = cutoff.replace(tzinfo=None).isoformat(" ")
    daily: dict[str, Counter] = defaultdict(Counter)
    for row in connection.execute(
        """
        SELECT date(reviewed_at) AS day, is_acquisition, credit_type, review_mode,
               rating, COUNT(*) AS n
        FROM review_log
        WHERE reviewed_at >= ? AND reviewed_at < ?
        GROUP BY day, is_acquisition, credit_type, review_mode, rating
        """,
        (start_sql, cutoff_sql),
    ):
        day = row["day"]
        daily[day]["word_reviews"] += row["n"]
        if row["is_acquisition"]:
            daily[day]["acquisition_reviews"] += row["n"]
            if row["rating"] >= 3:
                daily[day]["acquisition_successes"] += row["n"]
        if row["credit_type"] == "primary" and row["review_mode"] == "reading":
            daily[day]["primary_reading_cards"] += row["n"]

    for row in connection.execute(
        """
        SELECT date(graduated_at) AS day, COUNT(*) AS n
        FROM user_lemma_knowledge
        WHERE graduated_at >= ? AND graduated_at < ?
        GROUP BY day
        """,
        (start_sql, cutoff_sql),
    ):
        daily[row["day"]]["graduations"] += row["n"]

    calendar_days = date_range(start, cutoff)
    acq_reviews = [daily[day]["acquisition_reviews"] for day in calendar_days]
    primary_cards = [daily[day]["primary_reading_cards"] for day in calendar_days]
    graduations = [daily[day]["graduations"] for day in calendar_days]
    total_acq = sum(acq_reviews)
    total_acq_success = sum(daily[day]["acquisition_successes"] for day in calendar_days)

    episode_reviews = []
    for row in connection.execute(
        """
        SELECT u.lemma_id,
               SUM(CASE WHEN r.id IS NOT NULL THEN 1 ELSE 0 END) AS reviews
        FROM user_lemma_knowledge u
        LEFT JOIN review_log r
          ON r.lemma_id = u.lemma_id
         AND r.is_acquisition = 1
         AND r.reviewed_at >= COALESCE(u.entered_acquiring_at, u.acquisition_started_at)
         AND r.reviewed_at <= u.graduated_at
        WHERE u.graduated_at >= ? AND u.graduated_at < ?
        GROUP BY u.lemma_id
        """,
        (start_sql, cutoff_sql),
    ):
        if row["reviews"]:
            episode_reviews.append(int(row["reviews"]))

    return {
        "window_start": iso_z(start),
        "days": len(calendar_days),
        "median_acquisition_reviews_per_calendar_day": rounded(median(acq_reviews), 2),
        "mean_acquisition_reviews_per_calendar_day": rounded(total_acq / len(calendar_days), 2),
        "median_primary_reading_cards_per_calendar_day": rounded(median(primary_cards), 2),
        "median_graduations_per_calendar_day": rounded(median(graduations), 2),
        "mean_graduations_per_calendar_day": rounded(sum(graduations) / len(calendar_days), 2),
        "acquisition_accuracy": rounded(
            total_acq_success / total_acq if total_acq else None
        ),
        "median_episode_reviews_to_graduation": rounded(
            median(episode_reviews) if episode_reviews else None, 2
        ),
        "graduation_episodes_observed": len(episode_reviews),
    }


def classify_candidates(connection, lemma_ids: list[int], unresolved: int) -> dict[str, Any]:
    lemmas, roots = load_lemmas(connection)
    ulks = {
        int(row["lemma_id"]): row["knowledge_state"]
        for row in connection.execute(
            "SELECT lemma_id, knowledge_state FROM user_lemma_knowledge"
        )
    }
    categories = Counter()
    missing_ids = []
    canonical_ids = []
    for lemma_id in lemma_ids:
        if lemma_id not in lemmas:
            missing_ids.append(lemma_id)
            unresolved += 1
            continue
        canonical_ids.append(roots.get(lemma_id, lemma_id))
    unique = sorted(set(canonical_ids))
    for lemma_id in unique:
        if invalid_lemma_reason(lemmas.get(lemma_id)):
            categories["inert_not_eligible"] += 1
            continue
        state = ulks.get(lemma_id)
        if state in LEARNED:
            categories["already_learned"] += 1
        elif state in IN_TRAINING:
            categories["already_in_training"] += 1
        elif state in PENDING:
            categories["existing_pending"] += 1
        elif state == "suspended":
            categories["suspended_requires_decision"] += 1
        else:
            categories["existing_untracked"] += 1
    categories["unresolved_or_not_yet_created"] = unresolved
    additions = (
        categories["existing_pending"]
        + categories["existing_untracked"]
        + categories["unresolved_or_not_yet_created"]
    )
    return {
        "input_entries": len(lemma_ids) + unresolved - len(missing_ids),
        "resolved_entries": len(canonical_ids),
        "unique_canonical_lemmas": len(unique),
        "canonical_or_duplicate_collapses": len(canonical_ids) - len(unique),
        "missing_lemma_ids": sorted(missing_ids),
        "categories": dict(sorted(categories.items())),
        "projected_box1_additions": additions,
        "maximum_new_fsrs_arrivals": additions,
    }


def scenarios(additions: int, capacity: dict[str, Any]) -> list[dict[str, Any]]:
    reviews_per_word = capacity["median_episode_reviews_to_graduation"]
    daily_capacity = capacity["mean_acquisition_reviews_per_calendar_day"]
    reference_reviews = additions * reviews_per_word if reviews_per_word is not None else None
    capacity_days = (
        reference_reviews / daily_capacity
        if reference_reviews is not None and daily_capacity
        else None
    )
    output = []
    for label, rate in (("immediate", None), ("stage_8_per_day", 8), ("stage_30_per_day", 30)):
        admission_days = 1 if additions else 0
        if rate and additions:
            admission_days = math.ceil(additions / rate)
        output.append(
            {
                "scenario": label,
                "admission_days": admission_days,
                "peak_daily_additions": additions if rate is None else min(rate, additions),
                "success_conditioned_review_reference": rounded(reference_reviews, 1),
                "success_conditioned_capacity_days": rounded(capacity_days, 1),
            }
        )
    return output


def render_markdown(result: dict[str, Any]) -> str:
    c = result["classification"]
    cap = result["recent_capacity"]
    recovery = result["current_recovery"]
    lines = [
        "# Intake-impact preview",
        "",
        f"- Snapshot cutoff: `{result['cutoff']}`",
        f"- Database SHA-256: `{result['database']['sha256']}`",
        f"- Projected Box-1 additions: **{c['projected_box1_additions']}**",
        f"- Maximum eventual FSRS arrivals: **{c['maximum_new_fsrs_arrivals']}**",
        "",
        "## Classification",
        "",
        "| Category | Lemmas |",
        "|---|---:|",
    ]
    for key, value in c["categories"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
            "## Capacity basis",
            "",
            f"- Recent acquisition accuracy: "
            f"{cap['acquisition_accuracy']:.1%}" if cap["acquisition_accuracy"] is not None
            else "- Recent acquisition accuracy: unavailable",
            f"- Mean acquisition reviews/calendar day: "
            f"{cap['mean_acquisition_reviews_per_calendar_day']}",
            f"- Median observed episode reviews to graduation: "
            f"{cap['median_episode_reviews_to_graduation']} "
            f"(n={cap['graduation_episodes_observed']})",
            "",
            "## Scenarios",
            "",
            "| Scenario | Admission days | Peak admissions/day | Success-conditioned review reference | Capacity-days reference |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in result["scenarios"]:
        lines.append(
            f"| {row['scenario']} | {row['admission_days']} | "
            f"{row['peak_daily_additions']} | "
            f"{row['success_conditioned_review_reference']} | "
            f"{row['success_conditioned_capacity_days']} |"
        )
    lines.extend(
        [
            "",
            "## Current recovery context",
            "",
            f"- Recovery active: **{'yes' if recovery['active'] else 'no'}**",
            f"- Actionable Box 1: {recovery['box1_actionable']}",
            f"- Due Box 2: {recovery['box2_due']}",
            f"- Strict main FSRS due: {recovery['strict_main_fsrs_due']}",
            "",
        ]
    )
    reference = result.get("empirical_reference_cohort")
    if reference:
        summary = reference["summary"]
        lines.extend(
            [
                "## Empirical intake reference",
                "",
                f"- Cohort size: {summary['cohort_size']}",
                f"- Median admission→first review: "
                f"{summary['first_review_hours_median']} hours",
                f"- P90 admission→first review: "
                f"{summary['first_review_hours_p90']} hours",
                f"- Median first-review session ordinal: "
                f"{summary['first_review_session_ordinal_median']}",
                f"- Ever graduated at {summary['followup_days']} days follow-up: "
                f"{summary['ever_graduated']}/{summary['cohort_size']}",
                f"- Still acquiring: "
                f"{summary['states_at_cutoff'].get('acquiring', 0)}",
                "",
            ]
        )
    lines.extend(
        [
            "## Interpretation limits",
            "",
            "This is a capacity preview, not a memory simulation. The reviews-to-graduation "
            "estimate is conditioned on observed successful graduations, so it understates "
            "suspended and not-yet-finished demand. The preview does not apply intake, predict "
            "individual graduation dates, or claim staged intake improves retention.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--cutoff", type=parse_cli_datetime, required=True)
    parser.add_argument(
        "--capacity-cutoff",
        type=parse_cli_datetime,
        help="Optional earlier cutoff for backtesting capacity before a historical intake",
    )
    parser.add_argument("--candidate-file", type=Path)
    parser.add_argument("--anonymous-candidate-count", type=int, default=0)
    parser.add_argument("--history-days", type=int, default=30)
    parser.add_argument("--current-baseline-summary", type=Path, required=True)
    parser.add_argument(
        "--reference-cohort-json",
        type=Path,
        help="Optional observed cohort trajectory from analyze_intake_cohort.py",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.anonymous_candidate_count < 0 or args.history_days < 1:
        parser.error("candidate count must be non-negative and history-days must be positive")
    if args.output_dir.exists():
        parser.error("output-dir already exists")

    before = file_identity(args.db)
    candidate_before = file_identity(args.candidate_file) if args.candidate_file else None
    baseline_before = file_identity(args.current_baseline_summary)
    baseline_bytes = args.current_baseline_summary.read_bytes()
    reference_before = (
        file_identity(args.reference_cohort_json)
        if args.reference_cohort_json else None
    )
    reference_bytes = (
        args.reference_cohort_json.read_bytes()
        if args.reference_cohort_json else None
    )
    lemma_ids, unresolved = load_candidates(args.candidate_file, args.anonymous_candidate_count)
    capacity_cutoff = args.capacity_cutoff or args.cutoff
    if capacity_cutoff > args.cutoff:
        parser.error("capacity-cutoff cannot be after snapshot cutoff")
    connection = open_read_only(args.db)
    try:
        capacity = recent_capacity(
            connection,
            capacity_cutoff - timedelta(days=args.history_days),
            capacity_cutoff,
        )
        classification = classify_candidates(connection, lemma_ids, unresolved)
    finally:
        connection.close()
    after = file_identity(args.db)
    if before != after:
        parser.error("database changed during preview")
    candidate_after = file_identity(args.candidate_file) if args.candidate_file else None
    if candidate_before != candidate_after:
        parser.error("candidate file changed during preview")
    baseline_after = file_identity(args.current_baseline_summary)
    if baseline_before != baseline_after:
        parser.error("baseline summary changed during preview")
    reference_after = (
        file_identity(args.reference_cohort_json)
        if args.reference_cohort_json else None
    )
    if reference_before != reference_after:
        parser.error("reference cohort changed during preview")

    baseline = json.loads(baseline_bytes)
    baseline_database = baseline.get("provenance", {}).get("database", {})
    baseline_cutoff = baseline.get("window", {}).get("cutoff")
    if baseline_database.get("sha256") != before["sha256"]:
        parser.error("baseline summary and database snapshot do not match")
    if baseline_cutoff != iso_z(args.cutoff):
        parser.error("baseline summary and preview cutoff do not match")
    reference = json.loads(reference_bytes) if reference_bytes else None
    if (
        reference is not None
        and reference.get("database", {}).get("sha256") != before["sha256"]
    ):
        parser.error("reference cohort and database snapshot do not match")
    recovery_values = baseline["current_state"]["recovery"]["values"]
    result = {
        "schema_version": 1,
        "method_version": "wp8-intake-preview-v1",
        "cutoff": iso_z(args.cutoff),
        "capacity_cutoff": iso_z(capacity_cutoff),
        "script": file_identity(Path(__file__).resolve()),
        "database": before,
        "candidate_file": candidate_before,
        "current_baseline_summary": baseline_before,
        "reference_cohort_file": reference_before,
        "classification": classification,
        "recent_capacity": capacity,
        "current_recovery": {
            "active": baseline["current_state"]["recovery"]["active"],
            **recovery_values,
        },
        "scenarios": scenarios(classification["projected_box1_additions"], capacity),
        "empirical_reference_cohort": (
            {
                "method_version": reference["method_version"],
                "cohort_definition": reference["cohort_definition"],
                "summary": reference["summary"],
                "fixed_horizons": reference["fixed_horizons"],
            }
            if reference is not None else None
        ),
        "limitations": [
            "capacity projection, not causal simulation",
            "unresolved entries require quality-gated lemma creation before intake",
            "reviews-to-graduation is success-conditioned and understates suspended or censored demand",
            "maximum FSRS arrivals ignores suspension and censoring",
            "does not apply or stage intake",
        ],
    }
    args.output_dir.mkdir(parents=True)
    (args.output_dir / "preview.json").write_bytes(stable_json_bytes(result))
    (args.output_dir / "preview.md").write_text(render_markdown(result), encoding="utf-8")
    print(f"Wrote read-only intake preview to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
