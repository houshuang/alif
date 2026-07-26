#!/usr/bin/env python3
"""Right-censored read-only analysis of failed-word follow-up and recovery.

Every word outcome from a reviewed sentence is treated identically. The
primary/collateral label is retained only for diagnostics and never changes
eligibility, follow-up, or recovery counting.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable
from urllib.parse import quote

try:
    from analyze_learning_system import file_identity, stable_json_bytes
except ModuleNotFoundError:  # imported as backend.scripts.analyze_lapse_recovery
    from scripts.analyze_learning_system import file_identity, stable_json_bytes


HORIZONS = {
    "10_minutes": timedelta(minutes=10),
    "2_hours": timedelta(hours=2),
    "24_hours": timedelta(hours=24),
    "7_days": timedelta(days=7),
}
PERIODS = {
    "all": None,
    "last_90_days": timedelta(days=90),
    "last_30_days": timedelta(days=30),
    "last_7_days": timedelta(days=7),
}


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@dataclass(frozen=True)
class LapseEvent:
    lemma_id: int
    reviewed_at: datetime
    pre_stability_days: float
    credit_type: str | None
    next_reviewed_at: datetime | None
    next_rating: int | None


def _percent(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(100 * numerator / denominator, 2)


def summarize_events(
    events: Iterable[LapseEvent],
    cutoff: datetime,
    period_start: datetime,
    horizon: timedelta,
) -> dict[str, Any]:
    """Summarize one cohort with explicit right-censoring."""
    eligible = [
        event
        for event in events
        if period_start <= event.reviewed_at <= cutoff - horizon
    ]
    followed = [
        event
        for event in eligible
        if (
            event.next_reviewed_at is not None
            and event.next_reviewed_at <= event.reviewed_at + horizon
        )
    ]
    delays_minutes = [
        (event.next_reviewed_at - event.reviewed_at).total_seconds() / 60
        for event in followed
        if event.next_reviewed_at is not None
    ]
    ratings = {
        rating: sum(event.next_rating == rating for event in followed)
        for rating in (1, 2, 3, 4)
    }
    spontaneous = ratings[3] + ratings[4]
    return {
        "eligible_lapses": len(eligible),
        "distinct_lemmas": len({event.lemma_id for event in eligible}),
        "followed_up": len(followed),
        "followup_rate_pct": _percent(len(followed), len(eligible)),
        "median_followup_minutes": (
            round(median(delays_minutes), 2) if delays_minutes else None
        ),
        "first_followup_rating_counts": {
            str(rating): count for rating, count in ratings.items()
        },
        "assisted_recognition_rating2_pct_of_followups": _percent(
            ratings[2], len(followed)
        ),
        "spontaneous_retrieval_rating3_or_4_pct_of_followups": _percent(
            spontaneous, len(followed)
        ),
        "repeat_failure_rating1_pct_of_followups": _percent(
            ratings[1], len(followed)
        ),
    }


def load_events(
    connection: sqlite3.Connection,
    window_start: datetime,
    cutoff: datetime,
) -> list[LapseEvent]:
    rows = connection.execute(
        """
        SELECT id, lemma_id, rating, reviewed_at, fsrs_log_json, credit_type,
               COALESCE(is_acquisition, 0) AS is_acquisition
        FROM review_log
        WHERE reviewed_at >= ?
          AND reviewed_at <= ?
        ORDER BY lemma_id, reviewed_at, id
        """,
        (
            window_start.replace(tzinfo=None).isoformat(sep=" "),
            cutoff.replace(tzinfo=None).isoformat(sep=" "),
        ),
    ).fetchall()
    by_lemma: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        by_lemma.setdefault(int(row["lemma_id"]), []).append(row)

    events: list[LapseEvent] = []
    for lemma_id, lemma_rows in by_lemma.items():
        for index, row in enumerate(lemma_rows):
            if int(row["is_acquisition"]) or int(row["rating"]) != 1:
                continue
            payload = parse_json(row["fsrs_log_json"])
            pre_card = payload.get("pre_card")
            if not isinstance(pre_card, dict):
                continue
            stability = pre_card.get("stability")
            if not isinstance(stability, (int, float)):
                continue
            next_row = (
                lemma_rows[index + 1]
                if index + 1 < len(lemma_rows)
                else None
            )
            events.append(LapseEvent(
                lemma_id=lemma_id,
                reviewed_at=parse_datetime(row["reviewed_at"]),
                pre_stability_days=float(stability),
                credit_type=row["credit_type"],
                next_reviewed_at=(
                    parse_datetime(next_row["reviewed_at"])
                    if next_row is not None
                    else None
                ),
                next_rating=(
                    int(next_row["rating"]) if next_row is not None else None
                ),
            ))
    return events


def load_current_lapsed_stock(
    connection: sqlite3.Connection,
    cutoff: datetime,
) -> dict[str, int]:
    """Describe the snapshot's current Relearning stock and due subset."""
    rows = connection.execute(
        """
        SELECT lemma_id, fsrs_card_json
        FROM user_lemma_knowledge
        WHERE knowledge_state = 'lapsed'
        """
    ).fetchall()
    due_ids: set[int] = set()
    all_ids: set[int] = set()
    for row in rows:
        lemma_id = int(row["lemma_id"])
        all_ids.add(lemma_id)
        due_text = parse_json(row["fsrs_card_json"]).get("due")
        if not isinstance(due_text, str):
            continue
        try:
            due_at = parse_datetime(due_text)
        except ValueError:
            continue
        if due_at <= cutoff:
            due_ids.add(lemma_id)

    latest_pre_stability_by_lemma: dict[int, float] = {}
    if all_ids:
        placeholders = ",".join("?" for _ in all_ids)
        lapse_rows = connection.execute(
            f"""
            SELECT id, lemma_id, fsrs_log_json
            FROM review_log
            WHERE lemma_id IN ({placeholders})
              AND rating = 1
              AND COALESCE(is_acquisition, 0) = 0
              AND reviewed_at <= ?
            ORDER BY lemma_id, reviewed_at DESC, id DESC
            """,
            (
                *sorted(all_ids),
                cutoff.replace(tzinfo=None).isoformat(sep=" "),
            ),
        ).fetchall()
        seen: set[int] = set()
        for row in lapse_rows:
            lemma_id = int(row["lemma_id"])
            if lemma_id in seen:
                continue
            # The latest lapse is authoritative even if its evidence is
            # malformed or below threshold; never scan back to an older lapse.
            seen.add(lemma_id)
            payload = parse_json(row["fsrs_log_json"])
            pre_card = payload.get("pre_card")
            if not isinstance(pre_card, dict):
                continue
            stability = pre_card.get("stability")
            if isinstance(stability, (int, float)):
                latest_pre_stability_by_lemma[lemma_id] = float(stability)

    established_ids = {
        lemma_id
        for lemma_id in all_ids
        if latest_pre_stability_by_lemma.get(lemma_id, 0.0) >= 7
    }
    older_ids = {
        lemma_id
        for lemma_id in established_ids
        if latest_pre_stability_by_lemma[lemma_id] >= 30
    }
    return {
        "lapsed_words": len(all_ids),
        "lapsed_due_now": len(all_ids & due_ids),
        "established_pre_stability_ge_7d": len(established_ids),
        "established_due_now": len(established_ids & due_ids),
        "older_pre_stability_ge_30d": len(older_ids),
        "older_due_now": len(older_ids & due_ids),
    }


def render_report(result: dict[str, Any]) -> str:
    lines = [
        "# Failed-word follow-up and established-lapse recovery",
        "",
        f"- Cutoff: `{result['cutoff']}`",
        f"- Window start: `{result['window_start']}`",
        f"- Non-acquisition rating-1 events with pre-card stability: "
        f"**{result['population']['lapse_events']}**",
        f"- Distinct failed lemmas: "
        f"**{result['population']['distinct_lapsed_lemmas']}**",
        f"- Current Relearning stock: "
        f"**{result['current_lapsed_stock']['lapsed_words']}** "
        f"({result['current_lapsed_stock']['lapsed_due_now']} due at cutoff)",
        "",
        "All sentence words count equally. Rating 2 means assisted recognition "
        "after the answer was revealed; the spontaneous-retrieval endpoint is "
        "therefore rating 3 or 4.",
        "",
        "## Right-censored follow-up",
        "",
        "| Period | Cohort | Horizon | Eligible | Followed | Follow-up | "
        "Median delay | Spontaneous at first follow-up |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for period_name, period in result["periods"].items():
        for cohort_name, cohort in period["cohorts"].items():
            for horizon_name, metrics in cohort.items():
                lines.append(
                    f"| {period_name} | {cohort_name} | {horizon_name} | "
                    f"{metrics['eligible_lapses']} | {metrics['followed_up']} | "
                    f"{metrics['followup_rate_pct']}% | "
                    f"{metrics['median_followup_minutes']} min | "
                    f"{metrics['spontaneous_retrieval_rating3_or_4_pct_of_followups']}% |"
                )
    lines.extend([
        "",
        "## Interpretation limits",
        "",
        "- This is an observational delivery analysis, not a counterfactual "
        "retention estimate.",
        "- Each horizon excludes lapses too close to the cutoff to have the full "
        "follow-up opportunity.",
        "- First-follow-up ratings describe the next recorded word outcome; they "
        "do not prove that a particular selector or retry mechanism caused it.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--current-baseline-summary", type=Path, required=True)
    parser.add_argument("--window-start", type=parse_datetime, required=True)
    parser.add_argument("--cutoff", type=parse_datetime, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("output-dir already exists")
    if args.window_start >= args.cutoff:
        parser.error("window-start must be earlier than cutoff")

    database_before = file_identity(args.db)
    baseline_identity = file_identity(args.current_baseline_summary)
    baseline = json.loads(args.current_baseline_summary.read_bytes())
    if baseline["provenance"]["database"]["sha256"] != database_before["sha256"]:
        parser.error("baseline summary and database snapshot do not match")
    cutoff_text = args.cutoff.isoformat().replace("+00:00", "Z")
    if baseline["window"]["cutoff"] != cutoff_text:
        parser.error("baseline summary and analysis cutoff do not match")

    uri = (
        f"file:{quote(str(args.db.resolve()), safe='/')}"
        "?mode=ro&immutable=1"
    )
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        events = load_events(connection, args.window_start, args.cutoff)
        current_lapsed_stock = load_current_lapsed_stock(
            connection,
            args.cutoff,
        )
    finally:
        connection.close()
    if file_identity(args.db) != database_before:
        parser.error("database changed during analysis")

    cohorts = {
        "all_lapses": events,
        "fragile_pre_stability_lt_7d": [
            event for event in events if event.pre_stability_days < 7
        ],
        "established_pre_stability_ge_7d": [
            event for event in events if event.pre_stability_days >= 7
        ],
        "older_pre_stability_ge_30d": [
            event for event in events if event.pre_stability_days >= 30
        ],
    }
    periods: dict[str, Any] = {}
    for period_name, duration in PERIODS.items():
        period_start = max(
            args.window_start,
            args.cutoff - duration if duration is not None else args.window_start,
        )
        periods[period_name] = {
            "start": period_start.isoformat().replace("+00:00", "Z"),
            "cohorts": {
                cohort_name: {
                    horizon_name: summarize_events(
                        cohort_events,
                        args.cutoff,
                        period_start,
                        horizon,
                    )
                    for horizon_name, horizon in HORIZONS.items()
                }
                for cohort_name, cohort_events in cohorts.items()
            },
        }

    result = {
        "schema_version": 2,
        "method_version": "lapse-followup-right-censored-v2",
        "script": file_identity(Path(__file__).resolve()),
        # The repository is public. Validate exact hashes above, but do not
        # publish a stable fingerprint of the learner's production database.
        "database": {"filename": database_before["filename"]},
        "baseline_summary": {"filename": baseline_identity["filename"]},
        "window_start": args.window_start.isoformat().replace("+00:00", "Z"),
        "cutoff": cutoff_text,
        "scope": {
            "lapse_initiators_non_acquisition_only": True,
            "first_followup_includes_acquisition_reviews": True,
            "all_sentence_words_count_equally": True,
            "credit_type_affects_metrics": False,
            "rating2_interpretation": (
                "not recognized before flip; recognized after reveal"
            ),
            "spontaneous_retrieval_endpoint": "rating >= 3",
            "right_censored": True,
        },
        "population": {
            "lapse_events": len(events),
            "distinct_lapsed_lemmas": len({event.lemma_id for event in events}),
            "fragile_pre_stability_lt_7d": len(cohorts[
                "fragile_pre_stability_lt_7d"
            ]),
            "established_pre_stability_ge_7d": len(cohorts[
                "established_pre_stability_ge_7d"
            ]),
            "older_pre_stability_ge_30d": len(cohorts[
                "older_pre_stability_ge_30d"
            ]),
            "credit_type_counts_diagnostic_only": {
                credit_type: sum(
                    (event.credit_type or "unknown") == credit_type
                    for event in events
                )
                for credit_type in sorted({
                    event.credit_type or "unknown" for event in events
                })
            },
        },
        "current_lapsed_stock": current_lapsed_stock,
        "periods": periods,
        "limitations": [
            "observational delivery analysis, not a causal estimate",
            "first subsequent word outcome may come from any sentence",
            "does not reconstruct unlogged opportunities to review",
        ],
    }
    args.output_dir.mkdir(parents=True)
    (args.output_dir / "lapse_recovery.json").write_bytes(
        stable_json_bytes(result)
    )
    (args.output_dir / "report.md").write_text(
        render_report(result),
        encoding="utf-8",
    )
    checksum_lines = []
    for name in ("lapse_recovery.json", "report.md"):
        digest = hashlib.sha256((args.output_dir / name).read_bytes()).hexdigest()
        checksum_lines.append(f"{digest}  {name}")
    (args.output_dir / "SHA256SUMS").write_text(
        "\n".join(checksum_lines) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["population"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
