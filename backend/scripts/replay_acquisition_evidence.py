#!/usr/bin/env python3
"""Replay logged acquisition evidence and compare spacing-aware graduation gates.

This is an event-decision replay, not a full selector/workload simulation. It
never mutates the database and does not infer that a post-graduation review
would have been delivered on the same schedule had graduation been delayed.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import timedelta, timezone
from pathlib import Path

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


POLICIES = {
    "logged_current": {
        "description": "reproduce every stored graduation decision",
        "minimum_gap": None,
        "different_session": False,
        "different_utc_day": False,
    },
    "prior_success_10m_other_session": {
        "description": (
            "perfect/high-accuracy graduation needs a prior successful "
            "acquisition review at least 10m earlier in another known session"
        ),
        "minimum_gap": timedelta(minutes=10),
        "different_session": True,
        "different_utc_day": False,
    },
    "prior_success_12h": {
        "description": (
            "perfect/high-accuracy graduation needs a prior successful "
            "acquisition review at least 12h earlier"
        ),
        "minimum_gap": timedelta(hours=12),
        "different_session": False,
        "different_utc_day": False,
    },
    "prior_success_prior_utc_day": {
        "description": (
            "perfect/high-accuracy graduation needs a prior successful "
            "acquisition review on an earlier UTC date"
        ),
        "minimum_gap": None,
        "different_session": False,
        "different_utc_day": True,
    },
}
GATED_REASONS = {"perfect_accuracy", "high_accuracy"}


def sql_time(value):
    return value.replace(tzinfo=None).isoformat(" ")


def as_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def successful_evidence(event: dict) -> bool:
    return event["rating"] >= 3 and not event["retest_credit_blocked"]


def prior_success_qualifies(
    prior: dict,
    graduation: dict,
    policy: dict,
) -> bool:
    if not successful_evidence(prior):
        return False
    gap = graduation["reviewed_at_dt"] - prior["reviewed_at_dt"]
    minimum_gap = policy["minimum_gap"]
    if minimum_gap is not None and gap < minimum_gap:
        return False
    if policy["different_session"]:
        if not prior["session_id"] or not graduation["session_id"]:
            return False
        if prior["session_id"] == graduation["session_id"]:
            return False
    if (
        policy["different_utc_day"]
        and prior["reviewed_at_dt"].date() >= graduation["reviewed_at_dt"].date()
    ):
        return False
    return True


def policy_qualifies(event: dict, episode_events: list[dict], policy: dict) -> bool:
    if event["reason"] not in GATED_REASONS:
        return True
    return any(
        prior_success_qualifies(prior, event, policy)
        for prior in episode_events
        if prior["reviewed_at_dt"] < event["reviewed_at_dt"]
    )


def first_sentence_outcome(connection, lemma_id: int, start, cutoff):
    row = connection.execute(
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
        (lemma_id, sql_time(start), sql_time(cutoff)),
    ).fetchone()
    return dict(row) if row is not None else None


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
        rows = list(connection.execute(
            """
            SELECT id, lemma_id, rating, reviewed_at, session_id, review_mode,
                   credit_type, sentence_id, fsrs_log_json
            FROM review_log
            WHERE is_acquisition = 1
              AND reviewed_at < ?
              AND json_extract(fsrs_log_json, '$.pre_times_seen') IS NOT NULL
              AND json_extract(fsrs_log_json, '$.pre_times_correct') IS NOT NULL
            ORDER BY lemma_id, reviewed_at, id
            """,
            (sql_time(args.cutoff),),
        ))

        events_by_lemma: dict[int, list[dict]] = defaultdict(list)
        for row in rows:
            metadata = as_dict(row["fsrs_log_json"])
            reviewed_at = parse_datetime(row["reviewed_at"])
            event = {
                "id": int(row["id"]),
                "lemma_id": int(row["lemma_id"]),
                "rating": int(row["rating"]),
                "reviewed_at": row["reviewed_at"],
                "reviewed_at_dt": reviewed_at,
                "session_id": row["session_id"],
                "review_mode": row["review_mode"],
                "credit_type": row["credit_type"] or "unknown",
                "sentence_id": row["sentence_id"],
                "pre_times_seen": int(metadata["pre_times_seen"]),
                "pre_times_correct": int(metadata["pre_times_correct"]),
                "box_before": metadata.get("acquisition_box_before"),
                "box_after": metadata.get("acquisition_box_after"),
                "graduated": bool(metadata.get("graduated")),
                "reason": metadata.get("graduation_reason") or "legacy/null",
                "intro_working_memory_blocked": bool(
                    metadata.get("intro_working_memory_blocked")
                ),
                "retest_credit_blocked": bool(
                    metadata.get("retest_credit_blocked")
                ),
            }
            event["post_times_seen"] = event["pre_times_seen"] + (
                0 if event["retest_credit_blocked"] else 1
            )
            event["post_times_correct"] = event["pre_times_correct"] + (
                1 if successful_evidence(event) else 0
            )
            events_by_lemma[event["lemma_id"]].append(event)

        counter_checks = 0
        counter_mismatches = []
        episode_by_event: dict[int, list[dict]] = {}
        episode_boundary_by_event: dict[int, str] = {}
        for lemma_id, events in events_by_lemma.items():
            episode: list[dict] = []
            prior = None
            boundary = "first_logged_event"
            for event in events:
                if prior is not None and not prior["graduated"]:
                    counter_checks += 1
                    if (
                        event["pre_times_seen"] != prior["post_times_seen"]
                        or event["pre_times_correct"] != prior["post_times_correct"]
                    ):
                        if (
                            prior["retest_credit_blocked"]
                            and event["pre_times_seen"]
                            == prior["pre_times_seen"] + 1
                            and event["pre_times_correct"]
                            == prior["pre_times_correct"]
                            + int(prior["rating"] >= 3)
                        ):
                            discontinuity_kind = "pre_v2_retest_counter_semantics"
                        else:
                            intervening_non_acquisition = connection.execute(
                                """
                                SELECT COUNT(*)
                                FROM review_log
                                WHERE lemma_id = ?
                                  AND is_acquisition = 0
                                  AND reviewed_at > ?
                                  AND reviewed_at < ?
                                """,
                                (
                                    lemma_id,
                                    prior["reviewed_at"],
                                    event["reviewed_at"],
                                ),
                            ).fetchone()[0]
                            discontinuity_kind = (
                                "intervening_non_acquisition_episode"
                                if intervening_non_acquisition
                                else "unexplained_or_legacy_state_change"
                            )
                        counter_mismatches.append({
                            "lemma_id": lemma_id,
                            "prior_review_id": prior["id"],
                            "review_id": event["id"],
                            "expected_pre_times_seen": prior["post_times_seen"],
                            "actual_pre_times_seen": event["pre_times_seen"],
                            "expected_pre_times_correct": prior["post_times_correct"],
                            "actual_pre_times_correct": event["pre_times_correct"],
                            "reviewed_at": event["reviewed_at"],
                            "kind": discontinuity_kind,
                        })
                        # An external reset/reintroduction boundary cannot be
                        # reconstructed safely from counters alone.
                        episode = []
                        boundary = f"counter_discontinuity/{discontinuity_kind}"
                elif prior is not None and prior["graduated"]:
                    boundary = "prior_graduation"
                episode.append(event)
                episode_by_event[event["id"]] = list(episode)
                episode_boundary_by_event[event["id"]] = boundary
                prior = event
                if event["graduated"]:
                    episode = []
                    boundary = "prior_graduation"

        graduation_events = [
            event
            for events in events_by_lemma.values()
            for event in events
            if event["graduated"]
            and args.window_start <= event["reviewed_at_dt"] < args.cutoff
        ]
        graduation_events.sort(key=lambda item: (item["reviewed_at_dt"], item["id"]))

        decision_rows = []
        for event in graduation_events:
            event_with_followup = dict(event)
            event_with_followup.pop("reviewed_at_dt")
            eligible_3d = event["reviewed_at_dt"] + timedelta(days=3) < args.cutoff
            followup = (
                first_sentence_outcome(
                    connection,
                    event["lemma_id"],
                    event["reviewed_at_dt"] + timedelta(days=3),
                    args.cutoff,
                )
                if eligible_3d
                else None
            )
            row = {
                **event_with_followup,
                "eligible_3d": eligible_3d,
                "delivered_3d": followup is not None,
                "success_3d": (
                    bool(followup["rating"] >= 3) if followup is not None else None
                ),
                "episode_boundary": episode_boundary_by_event[event["id"]],
            }
            episode = episode_by_event[event["id"]]
            for name, policy in POLICIES.items():
                row[name] = policy_qualifies(event, episode, policy)
            decision_rows.append(row)
    finally:
        connection.close()

    if file_identity(args.db) != before:
        parser.error("database changed during acquisition evidence replay")

    policy_rows = []
    for name, policy in POLICIES.items():
        kept = [row for row in decision_rows if row[name]]
        deferred = [row for row in decision_rows if not row[name]]
        delivered = [row for row in kept if row["delivered_3d"]]
        deferred_delivered = [row for row in deferred if row["delivered_3d"]]
        gated = [row for row in decision_rows if row["reason"] in GATED_REASONS]
        qualifying_gated = [row for row in gated if row[name]]
        qualifying_gated_delivered = [
            row for row in qualifying_gated if row["delivered_3d"]
        ]
        policy_rows.append({
            "policy": name,
            "description": policy["description"],
            "logged_graduations": len(decision_rows),
            "would_qualify_at_logged_decision": len(kept),
            "would_defer_at_logged_decision": len(deferred),
            "deferred_perfect_accuracy": sum(
                row["reason"] == "perfect_accuracy" for row in deferred
            ),
            "deferred_high_accuracy": sum(
                row["reason"] == "high_accuracy" for row in deferred
            ),
            "gated_reason_graduations": len(gated),
            "qualifying_gated_reason_graduations": len(qualifying_gated),
            "qualifying_gated_delivered_3d": len(qualifying_gated_delivered),
            "qualifying_gated_success_3d": sum(
                row["success_3d"] for row in qualifying_gated_delivered
            ),
            "qualifying_gated_recall_among_delivered_3d": rounded(
                sum(row["success_3d"] for row in qualifying_gated_delivered)
                / len(qualifying_gated_delivered)
                if qualifying_gated_delivered else None
            ),
            "qualifying_delivered_3d": len(delivered),
            "qualifying_success_3d": sum(row["success_3d"] for row in delivered),
            "qualifying_recall_among_delivered_3d": rounded(
                sum(row["success_3d"] for row in delivered) / len(delivered)
                if delivered else None
            ),
            "deferred_delivered_3d": len(deferred_delivered),
            "deferred_success_3d": sum(
                row["success_3d"] for row in deferred_delivered
            ),
            "deferred_recall_among_delivered_3d": rounded(
                sum(row["success_3d"] for row in deferred_delivered)
                / len(deferred_delivered)
                if deferred_delivered else None
            ),
        })

    serializable_events = []
    for event in decision_rows:
        serializable_events.append(event)
    result = {
        "schema_version": 1,
        "method_version": "acquisition-evidence-decision-replay-v1",
        "script": file_identity(Path(__file__).resolve()),
        "database": before,
        "baseline_summary": baseline_identity,
        "window": {
            "start": args.window_start.isoformat().replace("+00:00", "Z"),
            "cutoff": cutoff_text,
        },
        "integrity": {
            "events_with_logged_pre_counters": sum(
                len(events) for events in events_by_lemma.values()
            ),
            "contiguous_counter_transitions_checked": counter_checks,
            "counter_mismatches": len(counter_mismatches),
            "counter_mismatches_in_window": sum(
                parse_datetime(item["reviewed_at"]) >= args.window_start
                for item in counter_mismatches
            ),
            "graduations_after_counter_discontinuity": sum(
                row["episode_boundary"].startswith("counter_discontinuity/")
                for row in decision_rows
            ),
            "counter_mismatch_kinds": dict(sorted(Counter(
                item["kind"] for item in counter_mismatches
            ).items())),
            "counter_mismatch_kinds_in_window": dict(sorted(Counter(
                item["kind"]
                for item in counter_mismatches
                if parse_datetime(item["reviewed_at"]) >= args.window_start
            ).items())),
            "counter_mismatch_examples": (
                [
                    item for item in counter_mismatches
                    if parse_datetime(item["reviewed_at"]) >= args.window_start
                ]
                + [
                    item for item in counter_mismatches
                    if parse_datetime(item["reviewed_at"]) < args.window_start
                ]
            )[:30],
        },
        "summary": {
            "logged_graduations": len(decision_rows),
            "graduation_reasons": dict(sorted(Counter(
                row["reason"] for row in decision_rows
            ).items())),
            "graduation_credit_types": dict(sorted(Counter(
                row["credit_type"] for row in decision_rows
            ).items())),
        },
        "policies": {
            name: {
                **{
                    "description": policy["description"],
                    "different_session": policy["different_session"],
                    "different_utc_day": policy["different_utc_day"],
                },
                "minimum_gap_seconds": (
                    policy["minimum_gap"].total_seconds()
                    if policy["minimum_gap"] is not None else None
                ),
                "applies_only_to_reasons": sorted(GATED_REASONS),
            }
            for name, policy in POLICIES.items()
        },
        "policy_results": policy_rows,
        "graduation_decisions": serializable_events,
        "limitations": [
            "event-decision replay, not a selector or queue simulation",
            "does not predict when a deferred word would next be delivered",
            "does not treat post-graduation FSRS delivery as counterfactual acquisition delivery",
            "counter discontinuities mark episode boundaries rather than being repaired",
            "reason groups and observed follow-up delivery remain confounded",
            "all primary and collateral word outcomes count equally",
        ],
    }
    args.output_dir.mkdir(parents=True)
    (args.output_dir / "acquisition_evidence_replay.json").write_bytes(
        stable_json_bytes(result)
    )
    with (args.output_dir / "by_policy.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(policy_rows[0]))
        writer.writeheader()
        writer.writerows(policy_rows)
    print(json.dumps({
        "integrity": result["integrity"],
        "policy_results": policy_rows,
        "summary": result["summary"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
