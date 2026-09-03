#!/usr/bin/env python3
"""Read-only checkpoint report for the low-energy maintenance experiment.

The report deliberately separates workload, scheduled retention tests, and
exposure-only appearances.  Raw accuracy is not comparable with the legacy
policy because the experiment removes its easiest high-retrievability
collateral judgments from the scheduled-review denominator.

Example:
    .venv/bin/python scripts/analyze_low_energy_maintenance_experiment.py \
      --db /tmp/alif-checkpoint.db \
      --interaction-log-dir /tmp/alif-interactions \
      --start 2026-09-03T10:00:00Z \
      --output ../research/baselines/low-energy-maintenance-day-3.json
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from analyze_learning_system import (  # noqa: E402
    analyze_current_state,
    load_lemmas,
    open_read_only,
    parse_datetime,
    percentile,
    sha256_file,
    table_columns,
)


POLICY_VERSION = "low_energy_maintenance_v1"
BASELINE = {
    "captured_at": "2026-09-03T09:31:44Z",
    "strict_main_fsrs_due": 744,
    "raw_fsrs_due": 827,
    "box1_actionable": 41,
    "acquiring_total": 56,
    "scheduled_clean_30d_pct": 89.0,
    "old_gap_7d_clean_pct": 85.8,
    "old_gap_14d_clean_pct": 81.5,
    "old_gap_30d_clean_pct": 73.6,
}


def pct(numerator: int, denominator: int) -> float | None:
    return round(100 * numerator / denominator, 1) if denominator else None


def sqlite_timestamp(value: datetime) -> str:
    """Match SQLAlchemy's naive UTC SQLite datetime serialization."""
    return value.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


def _iter_events(log_dir: Path) -> Iterable[dict[str, Any]]:
    for path in sorted(log_dir.glob("interactions_*.jsonl*")):
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            with opener(path, "rt", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        yield event
        except (OSError, EOFError):
            continue


def _in_window(event: dict[str, Any], start: datetime, cutoff: datetime) -> bool:
    timestamp = parse_datetime(event.get("ts"))
    return bool(timestamp and start <= timestamp <= cutoff)


def summarize_interactions(
    events: Iterable[dict[str, Any]],
    *,
    start: datetime,
    cutoff: datetime,
) -> dict[str, Any]:
    sessions: list[tuple[datetime, dict[str, Any]]] = []
    exposures: list[dict[str, Any]] = []
    confusion_rescues: list[dict[str, Any]] = []
    for event in events:
        if not _in_window(event, start, cutoff):
            continue
        timestamp = parse_datetime(event.get("ts"))
        if event.get("event") == "session_start":
            diagnostics = event.get("selection_diagnostics") or {}
            if diagnostics.get("learning_policy_version") == POLICY_VERSION:
                sessions.append((timestamp, event))
        elif (
            event.get("event") == "mature_collateral_exposure"
            and event.get("policy_version") == POLICY_VERSION
        ):
            exposures.append(event)
        elif event.get("event") == "confusion_context_rescue_selected":
            confusion_rescues.append(event)

    daily_peak_due: dict[str, int] = {}
    daily_cards: dict[str, int] = defaultdict(int)
    reason_counts: Counter[str] = Counter()
    density_breaches = 0
    passage_cards = 0
    for timestamp, event in sessions:
        day = timestamp.date().isoformat()
        due = event.get("total_due_words")
        if isinstance(due, int):
            daily_peak_due[day] = max(due, daily_peak_due.get(day, 0))
        cards = event.get("card_count")
        if isinstance(cards, int):
            daily_cards[day] += cards
        passage_cards += int(event.get("passage_count") or 0)
        diagnostics = event.get("selection_diagnostics") or {}
        density_breaches += int(diagnostics.get("cards_over_due_density_cap") or 0)
        reason_counts.update(diagnostics.get("selection_reason_counts") or {})

    exposure_breaches: list[dict[str, Any]] = []
    for event in exposures:
        retrievability = event.get("retrievability")
        valid = (
            event.get("credit_type") == "collateral"
            and event.get("knowledge_state") == "known"
            and event.get("review_mode") == "reading"
            and event.get("was_due") is False
            and isinstance(event.get("rating"), int)
            and event["rating"] >= 3
            and isinstance(retrievability, (int, float))
            and retrievability >= 0.97
        )
        if not valid:
            exposure_breaches.append(event)

    return {
        "policy_session_count": len(sessions),
        "daily_peak_selector_due": dict(sorted(daily_peak_due.items())),
        "daily_sentence_cards": dict(sorted(daily_cards.items())),
        "selection_reason_counts": dict(sorted(reason_counts.items())),
        "automatic_passage_cards": passage_cards,
        "density_cap_breaches": density_breaches,
        "mature_collateral_exposures": len(exposures),
        "distinct_exposure_lemmas": len({e.get("lemma_id") for e in exposures}),
        "exposure_invariant_breaches": len(exposure_breaches),
        "exposure_breach_samples": exposure_breaches[:5],
        "confusion_context_rescues": len(confusion_rescues),
        "distinct_confusion_captures_served": len({
            event.get("capture_id")
            for event in confusion_rescues
            if event.get("capture_id") is not None
        }),
    }


def summarize_workload(connection, start: datetime, cutoff: datetime) -> dict[str, Any]:
    rows = list(connection.execute(
        """
        SELECT reviewed_at, response_ms, comprehension, client_review_id
        FROM sentence_review_log
        WHERE reviewed_at >= ? AND reviewed_at <= ?
          AND COALESCE(review_mode, 'reading') = 'reading'
          AND (client_review_id IS NULL OR client_review_id NOT LIKE '%:s%')
        ORDER BY reviewed_at
        """,
        (sqlite_timestamp(start), sqlite_timestamp(cutoff)),
    ))
    daily_cards: Counter[str] = Counter()
    for row in rows:
        reviewed_at = parse_datetime(row["reviewed_at"])
        if reviewed_at:
            daily_cards[reviewed_at.date().isoformat()] += 1
    response_seconds = [
        row["response_ms"] / 1000
        for row in rows
        if isinstance(row["response_ms"], (int, float)) and row["response_ms"] >= 0
    ]
    comprehension = Counter(row["comprehension"] for row in rows)

    intake_rows = list(connection.execute(
        """
        SELECT acquisition_started_at
        FROM user_lemma_knowledge
        WHERE acquisition_started_at >= ? AND acquisition_started_at <= ?
          AND COALESCE(acquisition_episode_kind, 'new') != 'leech_reintro'
        """,
        (sqlite_timestamp(start), sqlite_timestamp(cutoff)),
    ))
    daily_intake: Counter[str] = Counter()
    for row in intake_rows:
        introduced = parse_datetime(row["acquisition_started_at"])
        if introduced:
            daily_intake[introduced.date().isoformat()] += 1

    return {
        "reading_sentence_cards": len(rows),
        "active_days": len(daily_cards),
        "daily_cards": dict(sorted(daily_cards.items())),
        "median_cards_per_active_day": (
            round(median(daily_cards.values()), 1) if daily_cards else None
        ),
        "median_response_seconds": (
            round(median(response_seconds), 1) if response_seconds else None
        ),
        "p90_response_seconds": (
            round(percentile(response_seconds, 0.9), 1) if response_seconds else None
        ),
        "comprehension_counts": dict(sorted(comprehension.items())),
        "true_new_intake": len(intake_rows),
        "daily_true_new_intake": dict(sorted(daily_intake.items())),
        "max_true_new_intake_on_day": max(daily_intake.values(), default=0),
    }


def _evidence_rows(connection) -> list[dict[str, Any]]:
    if not table_columns(connection, "word_review_evidence"):
        return []
    return [dict(row) for row in connection.execute(
        """
        SELECT e.client_review_id,
               e.canonical_lemma_id,
               MIN(e.rating) AS rating,
               MIN(e.created_at) AS created_at,
               MAX(CASE WHEN e.review_log_id IS NOT NULL THEN 1 ELSE 0 END)
                   AS scheduling_credit,
               COALESCE(u.introduced_at, u.acquisition_started_at,
                        u.entered_acquiring_at, u.graduated_at) AS first_learning_at
        FROM word_review_evidence e
        LEFT JOIN user_lemma_knowledge u
          ON u.lemma_id = e.canonical_lemma_id
        WHERE e.is_schedulable_content = 1
          AND e.review_mode = 'reading'
        GROUP BY e.client_review_id, e.canonical_lemma_id
        ORDER BY e.canonical_lemma_id, MIN(e.created_at)
        """
    )]


def _rating_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    clean = sum(1 for row in rows if int(row["rating"]) >= 3)
    return {
        "judgments": len(rows),
        "clean": clean,
        "clean_pct": pct(clean, len(rows)),
        "rating_counts": dict(sorted(Counter(str(row["rating"]) for row in rows).items())),
    }


def summarize_retention(connection, start: datetime, cutoff: datetime) -> dict[str, Any]:
    rows = _evidence_rows(connection)
    last_scheduled: dict[int, datetime] = {}
    scheduled_window: list[dict[str, Any]] = []
    exposure_window: list[dict[str, Any]] = []
    old_cutoff = start - timedelta(days=90)

    for row in rows:
        timestamp = parse_datetime(row["created_at"])
        if not timestamp or timestamp > cutoff:
            continue
        lemma_id = int(row["canonical_lemma_id"])
        if bool(row["scheduling_credit"]):
            previous = last_scheduled.get(lemma_id)
            row["scheduled_gap_days"] = (
                (timestamp - previous).total_seconds() / 86400 if previous else None
            )
            first_learning = parse_datetime(row.get("first_learning_at"))
            row["old_at_start"] = bool(first_learning and first_learning <= old_cutoff)
            if timestamp >= start:
                scheduled_window.append(row)
            last_scheduled[lemma_id] = timestamp
        elif timestamp >= start:
            exposure_window.append(row)

    old_scheduled = [row for row in scheduled_window if row["old_at_start"]]
    gaps = {}
    for days in (7, 14, 30):
        eligible = [
            row for row in old_scheduled
            if row["scheduled_gap_days"] is not None
            and row["scheduled_gap_days"] >= days
        ]
        gaps[f">={days}d"] = _rating_summary(eligible)

    by_client: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in scheduled_window:
        by_client[str(row["client_review_id"])].append(row)
    density_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for client_rows in by_client.values():
        density = len(client_rows)
        bucket = "1-2" if density <= 2 else "3-4" if density <= 4 else "5+"
        density_rows[bucket].extend(client_rows)

    return {
        "scheduled": _rating_summary(scheduled_window),
        "exposure_only_evidence_rows": len(exposure_window),
        "distinct_scheduled_lemmas": len({
            row["canonical_lemma_id"] for row in scheduled_window
        }),
        "old_cohort_definition": "first learning timestamp at least 90 days before experiment start",
        "old_scheduled": _rating_summary(old_scheduled),
        "old_scheduled_by_prior_gap": gaps,
        "scheduled_by_card_density": {
            bucket: _rating_summary(density_rows.get(bucket, []))
            for bucket in ("1-2", "3-4", "5+")
        },
        "cards_with_5plus_scheduled_judgments": sum(
            1 for client_rows in by_client.values() if len(client_rows) >= 5
        ),
    }


def summarize_confusions(connection, start: datetime, cutoff: datetime) -> dict[str, Any]:
    rows = list(connection.execute(
        """
        SELECT id, captured_at
        FROM confusion_captures
        WHERE captured_at >= ? AND captured_at <= ?
          AND COALESCE(confused_with_lemma_id, resolved_lemma_id) IS NOT NULL
        ORDER BY captured_at
        """,
        (sqlite_timestamp(start), sqlite_timestamp(cutoff)),
    ))
    mature = [
        row for row in rows
        if (captured := parse_datetime(row["captured_at"]))
        and captured <= cutoff - timedelta(days=14)
    ]
    return {
        "named_captures": len(rows),
        "captures_with_full_14d_service_window": len(mature),
        "note": "Service events are counted from interaction telemetry; a 14-day rate is only interpretable once captures mature.",
    }


def _signal(level: str, code: str, triggered: bool, detail: str) -> dict[str, Any]:
    return {"level": level, "code": code, "triggered": triggered, "detail": detail}


def evaluate_signals(
    *,
    elapsed_days: float,
    current: dict[str, Any],
    workload: dict[str, Any],
    retention: dict[str, Any],
    interactions: dict[str, Any],
) -> list[dict[str, Any]]:
    recovery = current["recovery"]["values"]
    main_due = recovery["strict_main_fsrs_due"]
    gap7 = retention["old_scheduled_by_prior_gap"][">=7d"]
    gap14 = retention["old_scheduled_by_prior_gap"][">=14d"]
    signals = [
        _signal(
            "stop",
            "density_cap_breach",
            interactions["density_cap_breaches"] > 0,
            "No automatic sentence card may contain five or more actionable scheduled words.",
        ),
        _signal(
            "stop",
            "exposure_invariant_breach",
            interactions["exposure_invariant_breaches"] > 0,
            "Exposure-only must remain clean, known, collateral, reading, not-due, and R>=0.97.",
        ),
        _signal(
            "stop",
            "daily_intake_cap_breach",
            workload["max_true_new_intake_on_day"] > 2,
            "Automatic true-new intake must never exceed two words in a UTC day.",
        ),
        _signal(
            "stop",
            "automatic_passage_delivery",
            interactions["automatic_passage_cards"] > 0,
            "Maintenance passages are suspended from the automatic review queue.",
        ),
        _signal(
            "stop",
            "severe_debt_growth",
            elapsed_days >= 7 and main_due >= 1000,
            f"Strict main FSRS due is {main_due}; stop threshold is 1000 after day 7.",
        ),
        _signal(
            "warn",
            "debt_not_burning_down",
            elapsed_days >= 7
            and (main_due >= BASELINE["strict_main_fsrs_due"] + 100
                 or main_due >= math.ceil(BASELINE["strict_main_fsrs_due"] * 1.15)),
            f"Strict main FSRS due is {main_due} vs {BASELINE['strict_main_fsrs_due']} at baseline.",
        ),
        _signal(
            "warn",
            "old_7d_retention_low",
            elapsed_days >= 7 and gap7["judgments"] >= 20
            and gap7["clean_pct"] is not None and gap7["clean_pct"] < 80,
            f"Old >=7d clean rate is {gap7['clean_pct']}% across {gap7['judgments']} scheduled tests.",
        ),
        _signal(
            "warn",
            "old_14d_retention_low",
            elapsed_days >= 14 and gap14["judgments"] >= 20
            and gap14["clean_pct"] is not None and gap14["clean_pct"] < 78,
            f"Old >=14d clean rate is {gap14['clean_pct']}% across {gap14['judgments']} scheduled tests.",
        ),
        _signal(
            "warn",
            "daily_volume_below_maintenance_target",
            elapsed_days >= 7 and workload["active_days"] >= 4
            and workload["median_cards_per_active_day"] is not None
            and workload["median_cards_per_active_day"] < 24,
            f"Median volume is {workload['median_cards_per_active_day']} cards per active day; intended floor is about 30.",
        ),
    ]
    return signals


def checkpoint_name(elapsed_days: float) -> str:
    if elapsed_days < 3:
        return "pre-day-3: instrumentation only"
    if elapsed_days < 7:
        return "day-3 operational check"
    if elapsed_days < 14:
        return "week-1 direction check"
    if elapsed_days < 30:
        return "week-2 retention check"
    if elapsed_days < 60:
        return "day-30 efficacy check"
    return "day-60 decision check"


def analyze(db_path: Path, log_dir: Path, start: datetime, cutoff: datetime) -> dict[str, Any]:
    before_hash = sha256_file(db_path)
    connection = open_read_only(db_path)
    try:
        lemmas, canonical_roots = load_lemmas(connection)
        current, _acquisition_rows, warnings = analyze_current_state(
            connection,
            cutoff,
            lemmas,
            canonical_roots,
        )
        workload = summarize_workload(connection, start, cutoff)
        retention = summarize_retention(connection, start, cutoff)
        confusions = summarize_confusions(connection, start, cutoff)
    finally:
        connection.close()
    after_hash = sha256_file(db_path)
    if before_hash != after_hash:
        raise RuntimeError("Read-only invariant failed: database hash changed")

    interactions = summarize_interactions(
        _iter_events(log_dir),
        start=start,
        cutoff=cutoff,
    )
    elapsed_days = max(0.0, (cutoff - start).total_seconds() / 86400)
    signals = evaluate_signals(
        elapsed_days=elapsed_days,
        current=current,
        workload=workload,
        retention=retention,
        interactions=interactions,
    )
    return {
        "schema_version": 1,
        "policy_version": POLICY_VERSION,
        "window": {
            "start": start.isoformat(),
            "cutoff": cutoff.isoformat(),
            "elapsed_days": round(elapsed_days, 2),
            "checkpoint": checkpoint_name(elapsed_days),
        },
        "baseline": BASELINE,
        "current_state": current,
        "workload": workload,
        "scheduled_retention": retention,
        "confusions": {**confusions, **{
            "rescues_selected": interactions["confusion_context_rescues"],
            "distinct_captures_served": interactions["distinct_confusion_captures_served"],
        }},
        "interaction_telemetry": interactions,
        "danger_signals": signals,
        "triggered_signals": [signal for signal in signals if signal["triggered"]],
        "warnings": warnings,
        "integrity": {
            "database_sha256_before": before_hash,
            "database_sha256_after": after_hash,
            "database_unchanged": before_hash == after_hash,
        },
        "interpretation_limits": [
            "This is a bundled policy experiment; it tests the package, not the isolated causal effect of each component.",
            "Scheduled accuracy will usually look harder than legacy accuracy because trivial high-R collateral judgments become exposure-only.",
            "Day 3 checks operation and safety; it cannot establish retention efficacy.",
            "A confusion service rate needs captures with a full 14-day opportunity window.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    window = report["window"]
    current = report["current_state"]
    workload = report["workload"]
    retention = report["scheduled_retention"]
    triggered = report["triggered_signals"]
    recovery = current["recovery"]["values"]
    gap7 = retention["old_scheduled_by_prior_gap"][">=7d"]
    gap14 = retention["old_scheduled_by_prior_gap"][">=14d"]
    median_cards = workload["median_cards_per_active_day"]
    median_response = workload["median_response_seconds"]
    p90_response = workload["p90_response_seconds"]
    lines = [
        f"# Low-energy maintenance — {window['checkpoint']}",
        "",
        f"Window: {window['start']} to {window['cutoff']} ({window['elapsed_days']} days)",
        "",
        f"- Sentence cards: {workload['reading_sentence_cards']} across {workload['active_days']} active days; median {median_cards if median_cards is not None else 'n/a'} per active day.",
        f"- Response time: median {f'{median_response}s' if median_response is not None else 'n/a'}; p90 {f'{p90_response}s' if p90_response is not None else 'n/a'}.",
        f"- New intake: {workload['true_new_intake']} total; maximum {workload['max_true_new_intake_on_day']} in one day.",
        f"- Debt: strict main FSRS {recovery['strict_main_fsrs_due']} (baseline {BASELINE['strict_main_fsrs_due']}), Box 1 actionable {recovery['box1_actionable']}, Box 2 due {recovery['box2_due']}.",
        f"- Scheduled judgments: {retention['scheduled']['judgments']}; clean {retention['scheduled']['clean_pct']}%.",
        f"- Old words after >=7d: {gap7['clean_pct']}% clean (n={gap7['judgments']}); after >=14d: {gap14['clean_pct']}% (n={gap14['judgments']}).",
        f"- Exposure-only mature collateral: {report['interaction_telemetry']['mature_collateral_exposures']} events.",
        f"- Named-confusion rescues: {report['confusions']['rescues_selected']} selections.",
        "",
        "Triggered danger signals: " + (
            ", ".join(signal["code"] for signal in triggered) if triggered else "none"
        ),
        "",
        "Do not compare scheduled clean rate directly with the legacy headline; the denominator is intentionally harder.",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Pinned SQLite snapshot")
    parser.add_argument("--interaction-log-dir", type=Path, required=True)
    parser.add_argument("--start", required=True, help="Experiment start, ISO-8601")
    parser.add_argument(
        "--cutoff",
        default=datetime.now(timezone.utc).isoformat(),
        help="Checkpoint cutoff, ISO-8601 (default: now)",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON output path")
    args = parser.parse_args()

    start = parse_datetime(args.start)
    cutoff = parse_datetime(args.cutoff)
    if not start or not cutoff or cutoff < start:
        parser.error("--start and --cutoff must be valid ISO timestamps with cutoff >= start")
    report = analyze(args.db, args.interaction_log_dir, start, cutoff)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(render_markdown(report))
    return 1 if any(
        signal["triggered"] and signal["level"] == "stop"
        for signal in report["danger_signals"]
    ) else 0


if __name__ == "__main__":
    raise SystemExit(main())
