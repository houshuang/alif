#!/usr/bin/env python3
"""Read-only milestone analysis for exact-surface randomized episodes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import UserLemmaKnowledge
from app.services.fsrs_service import parse_json_column
from app.services.surface_form_experiment import (
    EXACT_SURFACE_EXPERIMENT_KEY,
    EXACT_SURFACE_EXPIRES_DAYS,
)


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _episode_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _parse_datetime(value)
    except ValueError:
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fisher_two_sided(
    treatment_success: int,
    treatment_total: int,
    control_success: int,
    control_total: int,
) -> float | None:
    """Exact two-sided Fisher p-value for a 2x2 table."""
    if treatment_total == 0 or control_total == 0:
        return None
    total_success = treatment_success + control_success
    total = treatment_total + control_total
    low = max(0, total_success - control_total)
    high = min(treatment_total, total_success)

    def probability(value: int) -> float:
        return (
            math.comb(treatment_total, value)
            * math.comb(control_total, total_success - value)
            / math.comb(total, total_success)
        )

    observed = probability(treatment_success)
    return min(
        1.0,
        sum(
            probability(value)
            for value in range(low, high + 1)
            if probability(value) <= observed + 1e-15
        ),
    )


def _clustered_risk_difference_interval(
    episodes: list[dict[str, Any]],
    simulations: int,
    seed: int,
) -> tuple[float, float] | None:
    by_lemma: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for episode in episodes:
        by_lemma[episode["lemma_id"]].append(episode)
    lemma_ids = sorted(by_lemma)
    if len(lemma_ids) < 2:
        return None
    rng = np.random.default_rng(seed)
    differences: list[float] = []
    for _ in range(simulations):
        sampled = rng.choice(lemma_ids, size=len(lemma_ids), replace=True)
        rows = [
            episode
            for lemma_id in sampled
            for episode in by_lemma[int(lemma_id)]
        ]
        arm_rows = {
            arm: [row for row in rows if row["arm"] == arm]
            for arm in ("control", "treatment")
        }
        if not arm_rows["control"] or not arm_rows["treatment"]:
            continue
        differences.append(
            mean(row["exact_itt_success"] for row in arm_rows["treatment"])
            - mean(row["exact_itt_success"] for row in arm_rows["control"])
        )
    if not differences:
        return None
    return (
        float(np.quantile(differences, 0.025)),
        float(np.quantile(differences, 0.975)),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--cutoff", type=_parse_datetime, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-db-sha256")
    parser.add_argument(
        "--trigger-kind",
        default="successful_first_form_exposure",
        help="Episode trigger_kind to analyze, or 'all'.",
    )
    parser.add_argument("--bootstrap-simulations", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=20260731)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    if args.bootstrap_simulations < 1:
        parser.error("bootstrap-simulations must be positive")

    db_sha = _sha256(args.db)
    if args.expected_db_sha256 and db_sha != args.expected_db_sha256:
        parser.error("database SHA-256 does not match expected snapshot")
    engine = create_engine(
        f"sqlite:///file:{args.db.resolve()}?mode=ro&immutable=1&uri=true",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _query_only(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA query_only=ON")

    db = sessionmaker(bind=engine)()
    episodes: list[dict[str, Any]] = []
    try:
        for knowledge in db.query(UserLemmaKnowledge).all():
            stats = parse_json_column(knowledge.variant_stats_json)
            container = stats.get(EXACT_SURFACE_EXPERIMENT_KEY)
            stored = (
                container.get("episodes")
                if isinstance(container, dict)
                else None
            )
            if not isinstance(stored, list):
                continue
            for episode in stored:
                if not isinstance(episode, dict):
                    continue
                trigger_kind = episode.get("trigger_kind") or "legacy_yellow"
                if (
                    args.trigger_kind != "all"
                    and trigger_kind != args.trigger_kind
                ):
                    continue
                triggered_at = _episode_datetime(episode.get("triggered_at"))
                if triggered_at is None or triggered_at > args.cutoff:
                    continue
                arm = episode.get("arm")
                if arm not in {"control", "treatment"}:
                    continue
                exact_rating = episode.get("exact_all_word_outcome_rating")
                if exact_rating is None:
                    exact_rating = episode.get("outcome_rating")
                all_word_rating = episode.get("all_word_outcome_rating")
                if all_word_rating is None:
                    all_word_rating = episode.get(
                        "any_form_outcome_rating"
                    )
                mature = triggered_at <= (
                    args.cutoff - timedelta(days=EXACT_SURFACE_EXPIRES_DAYS)
                )
                episodes.append({
                    "lemma_id": knowledge.lemma_id,
                    "arm": arm,
                    "trigger_kind": trigger_kind,
                    "triggered_at": triggered_at,
                    "mature": mature,
                    "exact_itt_success": bool(
                        mature
                        and isinstance(exact_rating, int)
                        and exact_rating >= 3
                    ),
                    "exact_delivered": exact_rating is not None,
                    "all_word_observed": all_word_rating is not None,
                    "all_word_success": (
                        isinstance(all_word_rating, int)
                        and all_word_rating >= 3
                    ),
                    "all_word_credit_type": episode.get(
                        "all_word_credit_type"
                    ),
                    "morph_category": episode.get("morph_category"),
                })
    finally:
        db.close()
        engine.dispose()

    mature = [episode for episode in episodes if episode["mature"]]
    by_arm = {
        arm: [episode for episode in mature if episode["arm"] == arm]
        for arm in ("control", "treatment")
    }
    arm_summary = {}
    for arm, rows in by_arm.items():
        successes = sum(row["exact_itt_success"] for row in rows)
        observed = [row for row in rows if row["all_word_observed"]]
        arm_summary[arm] = {
            "assigned_mature": len(rows),
            "successful_exact_retrievals": successes,
            "successful_exact_retrieval_rate": (
                round(successes / len(rows), 4) if rows else None
            ),
            "all_word_outcomes": len(observed),
            "all_word_outcome_yield": (
                round(len(observed) / len(rows), 4) if rows else None
            ),
            "all_word_success_rate_among_observed": (
                round(
                    mean(row["all_word_success"] for row in observed),
                    4,
                )
                if observed
                else None
            ),
        }

    control_n = len(by_arm["control"])
    treatment_n = len(by_arm["treatment"])
    control_success = sum(
        row["exact_itt_success"] for row in by_arm["control"]
    )
    treatment_success = sum(
        row["exact_itt_success"] for row in by_arm["treatment"]
    )
    control_rate = control_success / control_n if control_n else None
    treatment_rate = treatment_success / treatment_n if treatment_n else None
    risk_difference = (
        treatment_rate - control_rate
        if treatment_rate is not None and control_rate is not None
        else None
    )
    interval = _clustered_risk_difference_interval(
        mature,
        args.bootstrap_simulations,
        args.seed,
    )
    assigned_arm_counts = Counter(
        episode["arm"] for episode in episodes
    )
    output = {
        "schema_version": 1,
        "method": "exact-surface-episode-itt-v1",
        "database": {"filename": args.db.name, "sha256": db_sha},
        "cutoff": args.cutoff.isoformat(),
        "trigger_kind": args.trigger_kind,
        "assigned": {
            "episodes": len(episodes),
            "arm_counts": dict(sorted(assigned_arm_counts.items())),
            "treatment_fraction": (
                round(
                    assigned_arm_counts["treatment"] / len(episodes),
                    4,
                )
                if episodes
                else None
            ),
            "morphology_categories": dict(sorted(Counter(
                episode["morph_category"] or "unknown"
                for episode in episodes
            ).items())),
        },
        "primary_exact_retrieval_itt": {
            "definition": (
                "successful exact-form all-word review in a different "
                "sentence within 14 days; non-delivery is failure"
            ),
            "mature_episodes": len(mature),
            "arms": arm_summary,
            "risk_difference_treatment_minus_control": (
                round(risk_difference, 4)
                if risk_difference is not None
                else None
            ),
            "lemma_clustered_bootstrap_95pct": (
                [round(value, 4) for value in interval]
                if interval is not None
                else None
            ),
            "fisher_exact_two_sided_p": (
                round(
                    _fisher_two_sided(
                        treatment_success,
                        treatment_n,
                        control_success,
                        control_n,
                    ),
                    6,
                )
                if treatment_n and control_n
                else None
            ),
        },
        "milestones": {
            "balance_check_40_reached": len(episodes) >= 40,
            "descriptive_80_reached": len(mature) >= 80,
            "efficacy_200_reached": len(mature) >= 200,
            "assignment_balance_within_40_60": (
                0.4
                <= assigned_arm_counts["treatment"] / len(episodes)
                <= 0.6
                if len(episodes) >= 40
                else None
            ),
        },
        "notes": [
            "Only episodes at least 14 days old enter the primary ITT analysis.",
            "All-word success among observed outcomes is secondary and may be censored.",
            "The clustered bootstrap resamples canonical lemmas, not review rows.",
        ],
    }
    if _sha256(args.db) != db_sha:
        parser.error("database changed during analysis")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "assigned": output["assigned"],
        "primary": output["primary_exact_retrieval_itt"],
        "milestones": output["milestones"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
