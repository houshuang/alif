#!/usr/bin/env python3
"""Read-only acquisition-graduation evidence and cold-recall audit."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import median

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


def sql_time(value):
    return value.replace(tzinfo=None).isoformat(" ")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--cutoff", type=parse_cli_datetime, required=True)
    parser.add_argument("--window-start", type=parse_cli_datetime, required=True)
    parser.add_argument("--current-baseline-summary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("output-dir already exists")

    before = file_identity(args.db)
    baseline_identity = file_identity(args.current_baseline_summary)
    baseline = json.loads(args.current_baseline_summary.read_bytes())
    cutoff_text = args.cutoff.isoformat().replace("+00:00", "Z")
    if baseline["provenance"]["database"]["sha256"] != before["sha256"]:
        parser.error("baseline summary and database snapshot do not match")
    if baseline["window"]["cutoff"] != cutoff_text:
        parser.error("baseline summary and cutoff do not match")

    connection = open_read_only(args.db)
    try:
        graduates = list(connection.execute(
            """
            SELECT r.id, r.lemma_id, r.reviewed_at AS graduated_at, r.rating,
                   r.credit_type, r.session_id,
                   json_extract(r.fsrs_log_json, '$.graduation_reason') AS reason,
                   u.acquisition_started_at
            FROM review_log r
            JOIN user_lemma_knowledge u ON u.lemma_id = r.lemma_id
            WHERE r.is_acquisition = 1
              AND json_extract(r.fsrs_log_json, '$.graduated') = 1
              AND r.reviewed_at >= ?
              AND r.reviewed_at < ?
            ORDER BY r.reviewed_at, r.id
            """,
            (sql_time(args.window_start), sql_time(args.cutoff)),
        ))
        all_non_success = list(connection.execute(
            """
            SELECT lemma_id, reviewed_at, rating, credit_type,
                   json_extract(fsrs_log_json, '$.graduation_reason') AS reason
            FROM review_log
            WHERE is_acquisition = 1
              AND json_extract(fsrs_log_json, '$.graduated') = 1
              AND rating < 3
            ORDER BY reviewed_at, id
            """
        ))
        output_rows = []
        for graduate in graduates:
            grad_at = parse_datetime(graduate["graduated_at"])
            episode_start = (
                parse_datetime(graduate["acquisition_started_at"])
                or args.window_start
            )
            evidence = list(connection.execute(
                """
                SELECT reviewed_at, rating, session_id
                FROM review_log
                WHERE lemma_id = ?
                  AND is_acquisition = 1
                  AND reviewed_at >= ?
                  AND reviewed_at <= ?
                ORDER BY reviewed_at, id
                """,
                (
                    graduate["lemma_id"],
                    sql_time(episode_start),
                    sql_time(grad_at),
                ),
            ))
            sessions = {
                row["session_id"] for row in evidence if row["session_id"]
            }
            row = {
                "lemma_id": int(graduate["lemma_id"]),
                "graduated_at": graduate["graduated_at"],
                "reason": graduate["reason"] or "legacy/null",
                "graduating_rating": int(graduate["rating"]),
                "graduating_credit_type": graduate["credit_type"] or "unknown",
                "episode_review_rows": len(evidence),
                "episode_distinct_sessions": len(sessions),
                "episode_hours": rounded(
                    (grad_at - episode_start).total_seconds() / 3600,
                    2,
                ),
            }
            for days in (1, 3, 7):
                eligible = grad_at + timedelta(days=days) < args.cutoff
                follow = None
                if eligible:
                    follow = connection.execute(
                        """
                        SELECT rating, reviewed_at, credit_type
                        FROM review_log
                        WHERE lemma_id = ?
                          AND reviewed_at >= ?
                          AND reviewed_at < ?
                          AND review_mode = 'reading'
                          AND sentence_id IS NOT NULL
                        ORDER BY reviewed_at, id
                        LIMIT 1
                        """,
                        (
                            graduate["lemma_id"],
                            sql_time(grad_at + timedelta(days=days)),
                            sql_time(args.cutoff),
                        ),
                    ).fetchone()
                row[f"eligible_{days}d"] = eligible
                row[f"delivered_{days}d"] = follow is not None
                row[f"success_{days}d"] = (
                    int(follow["rating"] >= 3) if follow is not None else None
                )
            output_rows.append(row)
    finally:
        connection.close()
    if file_identity(args.db) != before:
        parser.error("database changed during graduation audit")

    by_reason = defaultdict(list)
    for row in output_rows:
        by_reason[row["reason"]].append(row)
    reason_rows = []
    for reason, rows in sorted(by_reason.items()):
        result = {
            "reason": reason,
            "graduations": len(rows),
            "non_success_graduations": sum(
                row["graduating_rating"] < 3 for row in rows
            ),
            "episode_reviews_median": rounded(
                median(row["episode_review_rows"] for row in rows), 2
            ),
            "episode_sessions_median": rounded(
                median(row["episode_distinct_sessions"] for row in rows), 2
            ),
            "one_session_graduations": sum(
                row["episode_distinct_sessions"] <= 1 for row in rows
            ),
            "episode_hours_median": rounded(
                median(row["episode_hours"] for row in rows), 2
            ),
        }
        for days in (1, 3, 7):
            eligible = [row for row in rows if row[f"eligible_{days}d"]]
            delivered = [row for row in eligible if row[f"delivered_{days}d"]]
            result[f"eligible_{days}d"] = len(eligible)
            result[f"delivered_{days}d"] = len(delivered)
            result[f"delivery_fraction_{days}d"] = rounded(
                len(delivered) / len(eligible) if eligible else None
            )
            result[f"successes_{days}d"] = sum(
                row[f"success_{days}d"] for row in delivered
            )
            result[f"recall_among_delivered_{days}d"] = rounded(
                sum(row[f"success_{days}d"] for row in delivered) / len(delivered)
                if delivered else None
            )
        reason_rows.append(result)

    result = {
        "schema_version": 1,
        "method_version": "graduation-retention-observed-v1",
        "script": file_identity(Path(__file__).resolve()),
        "database": before,
        "baseline_summary": baseline_identity,
        "window": {
            "start": args.window_start.isoformat().replace("+00:00", "Z"),
            "cutoff": cutoff_text,
        },
        "summary": {
            "graduations": len(output_rows),
            "graduation_reasons": dict(sorted(Counter(
                row["reason"] for row in output_rows
            ).items())),
            "non_success_graduations_all_time": len(all_non_success),
            "non_success_graduation_rows": [
                dict(row) for row in all_non_success
            ],
        },
        "by_reason": reason_rows,
        "graduations": output_rows,
        "limitations": [
            "observational and unadjusted for cohort, difficulty, or censoring",
            "recall denominator is delivered follow-up, not every eligible graduation",
            "episode start uses current ULK acquisition_started_at and may blur old re-episodes",
            "small reason cells are descriptive, not decision-grade",
            "primary and collateral graduation/follow-up rows are equally valid",
        ],
    }
    args.output_dir.mkdir(parents=True)
    (args.output_dir / "graduation_retention.json").write_bytes(
        stable_json_bytes(result)
    )
    with (args.output_dir / "by_reason.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        fields = (
            list(reason_rows[0])
            if reason_rows
            else [
                "reason",
                "graduations",
                "non_success_graduations",
                "episode_reviews_median",
                "episode_sessions_median",
                "one_session_graduations",
                "episode_hours_median",
            ]
        )
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(reason_rows)
    print(json.dumps(result["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
