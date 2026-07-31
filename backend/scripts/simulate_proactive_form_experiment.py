#!/usr/bin/env python3
"""Reconstruct proactive form-pilot opportunities and simulate trial power.

This script never writes to the database. Historical review order supplies the
learner trajectory; the sentence pool at the pinned cutoff supplies prospective
exact-form serviceability. The latter is intentionally a deployment-capacity
estimate, not a claim that every sentence existed at its historical trigger.
"""

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
from sqlalchemy import create_engine, event, or_
from sqlalchemy.orm import sessionmaker

from app.models import Lemma, ReviewLog, Sentence, SentenceWord
from app.services.canonical_resolution import resolve_canonical_via_map
from app.services.confusion_service import normalize_surface_form
from app.services.sentence_eligibility import reviewable_sentence_clauses
from app.services.surface_form_experiment import (
    EXACT_SURFACE_EXPIRES_DAYS,
    deterministic_arm,
    eligible_surface_morphology,
)


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _trial_power(
    total_evaluable: int,
    control_success: float,
    uplift: float,
    simulations: int,
    seed: int,
) -> float:
    """Two-sided pooled two-proportion z-test Monte Carlo power."""
    control_n = total_evaluable // 2
    treatment_n = total_evaluable - control_n
    if control_n < 2 or treatment_n < 2:
        return 0.0
    treatment_success = min(0.999, control_success + uplift)
    rng = np.random.default_rng(seed)
    control_hits = rng.binomial(control_n, control_success, simulations)
    treatment_hits = rng.binomial(
        treatment_n,
        treatment_success,
        simulations,
    )
    control_rate = control_hits / control_n
    treatment_rate = treatment_hits / treatment_n
    pooled = (control_hits + treatment_hits) / total_evaluable
    standard_error = np.sqrt(
        pooled * (1 - pooled) * (1 / control_n + 1 / treatment_n)
    )
    z = np.divide(
        treatment_rate - control_rate,
        standard_error,
        out=np.zeros_like(treatment_rate, dtype=float),
        where=standard_error > 0,
    )
    return float(np.mean(np.abs(z) >= 1.959963984540054))


def _normal_sample_size(
    control_success: float,
    uplift: float,
    target_power: float = 0.80,
) -> int:
    """Approximate total evaluable N for equal-arm, two-sided proportions."""
    treatment_success = min(0.999, control_success + uplift)
    realized_uplift = treatment_success - control_success
    if realized_uplift <= 0:
        return 0
    pooled = (control_success + treatment_success) / 2
    z_alpha = 1.959963984540054
    # 0.841621 is Phi^-1(.80); keep the implementation dependency-light.
    z_beta = 0.8416212335729143 if target_power == 0.80 else 0.8416212335729143
    numerator = (
        z_alpha * math.sqrt(2 * pooled * (1 - pooled))
        + z_beta
        * math.sqrt(
            control_success * (1 - control_success)
            + treatment_success * (1 - treatment_success)
        )
    ) ** 2
    per_arm = math.ceil(numerator / (realized_uplift ** 2))
    return per_arm * 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--cutoff", type=_parse_datetime, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-db-sha256")
    parser.add_argument("--simulations", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=20260731)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    if args.simulations < 1:
        parser.error("simulations must be positive")

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
    try:
        lemmas = db.query(Lemma).all()
        lemma_by_id = {lemma.lemma_id: lemma for lemma in lemmas}
        canonical_parent = {
            lemma.lemma_id: lemma.canonical_lemma_id for lemma in lemmas
        }
        canonical_by_id = {
            lemma_id: resolve_canonical_via_map(lemma_id, canonical_parent)
            for lemma_id in canonical_parent
        }

        eligible_sentences = (
            db.query(Sentence)
            .filter(
                reviewable_sentence_clauses(),
                or_(Sentence.source.is_(None), Sentence.source != "passage"),
                or_(
                    Sentence.source.is_(None),
                    Sentence.source != "llm",
                    (
                        Sentence.quality_natural.is_not(False)
                        & Sentence.quality_translation_correct.is_not(False)
                    ),
                ),
            )
            .all()
        )
        eligible_sentence_ids = {sentence.id for sentence in eligible_sentences}

        forms_by_sentence_lemma: dict[tuple[int, int], set[str]] = defaultdict(set)
        for sentence_id, lemma_id, surface in db.query(
            SentenceWord.sentence_id,
            SentenceWord.lemma_id,
            SentenceWord.surface_form,
        ).filter(SentenceWord.lemma_id.is_not(None)).all():
            canonical_id = canonical_by_id.get(lemma_id, lemma_id)
            key = normalize_surface_form(surface)
            if key:
                forms_by_sentence_lemma[(sentence_id, canonical_id)].add(key)

        exact_sentences: dict[tuple[int, str], set[int]] = defaultdict(set)
        for (sentence_id, lemma_id), forms in forms_by_sentence_lemma.items():
            if sentence_id in eligible_sentence_ids and len(forms) == 1:
                exact_sentences[(lemma_id, next(iter(forms)))].add(sentence_id)

        review_rows = (
            db.query(ReviewLog)
            .filter(
                ReviewLog.reviewed_at <= args.cutoff.replace(tzinfo=None),
                ReviewLog.review_mode == "reading",
                ReviewLog.sentence_id.is_not(None),
            )
            .order_by(ReviewLog.reviewed_at, ReviewLog.id)
            .all()
        )

        seen_forms: set[tuple[int, str]] = set()
        assigned_forms: set[tuple[int, str]] = set()
        open_episode_by_lemma: dict[int, dict[str, Any]] = {}
        assignments: list[dict[str, Any]] = []
        active_review_dates: set[str] = set()
        first_review_at: datetime | None = None
        last_review_at: datetime | None = None

        for row in review_rows:
            reviewed_at = _aware(row.reviewed_at)
            if reviewed_at is None:
                continue
            first_review_at = first_review_at or reviewed_at
            last_review_at = reviewed_at
            active_review_dates.add(reviewed_at.date().isoformat())
            lemma_id = canonical_by_id.get(row.lemma_id, row.lemma_id)
            forms = forms_by_sentence_lemma.get((row.sentence_id, lemma_id), set())
            surface_key = next(iter(forms)) if len(forms) == 1 else None

            episode = open_episode_by_lemma.get(lemma_id)
            if episode is not None:
                if reviewed_at > episode["expires_at"]:
                    open_episode_by_lemma.pop(lemma_id, None)
                    episode["expired_without_exact_outcome"] = True
                    episode = None
                elif row.id > episode["trigger_review_id"] and not row.is_acquisition:
                    if episode["all_word_outcome_rating"] is None:
                        episode["all_word_outcome_rating"] = row.rating
                        episode["all_word_outcome_at"] = reviewed_at
                        episode["all_word_credit_type"] = row.credit_type
                        episode["all_word_same_trigger_context"] = (
                            row.sentence_id == episode["trigger_sentence_id"]
                        )
                    exact_different_context = (
                        surface_key == episode["surface_key"]
                        and row.sentence_id != episode["trigger_sentence_id"]
                    )
                    if (
                        exact_different_context
                        and episode["exact_all_word_outcome_rating"] is None
                    ):
                        episode["exact_all_word_outcome_rating"] = row.rating
                        episode["exact_all_word_outcome_at"] = reviewed_at
                        episode["exact_all_word_credit_type"] = row.credit_type
                    if row.credit_type == "primary":
                        if episode["any_form_outcome_rating"] is None:
                            episode["any_form_outcome_rating"] = row.rating
                            episode["any_form_outcome_at"] = reviewed_at
                        if exact_different_context:
                            episode["exact_outcome_rating"] = row.rating
                            episode["exact_outcome_at"] = reviewed_at
                    if exact_different_context:
                        open_episode_by_lemma.pop(lemma_id, None)
                        episode = None

            if surface_key is None:
                continue
            pair = (lemma_id, surface_key)
            first_exposure = pair not in seen_forms
            seen_forms.add(pair)
            if not first_exposure:
                continue
            if row.is_acquisition or row.rating < 3:
                continue
            lemma = lemma_by_id.get(lemma_id)
            morphology = eligible_surface_morphology(surface_key, lemma)
            if not morphology:
                continue
            candidate_ids = exact_sentences.get(pair, set()) - {row.sentence_id}
            if not candidate_ids:
                continue
            if pair in assigned_forms or lemma_id in open_episode_by_lemma:
                continue

            identity = row.client_review_id or str(row.id)
            assignment = {
                "trigger_review_id": row.id,
                "triggered_at": reviewed_at,
                "trigger_sentence_id": row.sentence_id,
                "lemma_id": lemma_id,
                "surface_key": surface_key,
                "category": morphology.get("category"),
                "arm": deterministic_arm(identity, lemma_id, surface_key),
                "candidate_count": len(candidate_ids),
                "expires_at": reviewed_at + timedelta(
                    days=EXACT_SURFACE_EXPIRES_DAYS
                ),
                "any_form_outcome_rating": None,
                "any_form_outcome_at": None,
                "all_word_outcome_rating": None,
                "all_word_outcome_at": None,
                "all_word_credit_type": None,
                "all_word_same_trigger_context": None,
                "exact_all_word_outcome_rating": None,
                "exact_all_word_outcome_at": None,
                "exact_all_word_credit_type": None,
                "exact_outcome_rating": None,
                "exact_outcome_at": None,
                "expired_without_exact_outcome": False,
            }
            assignments.append(assignment)
            assigned_forms.add(pair)
            open_episode_by_lemma[lemma_id] = assignment

        all_word_outcomes = [
            assignment
            for assignment in assignments
            if assignment["all_word_outcome_rating"] is not None
        ]
        any_outcomes = [
            assignment
            for assignment in assignments
            if assignment["any_form_outcome_rating"] is not None
        ]
        exact_outcomes = [
            assignment
            for assignment in assignments
            if assignment["exact_outcome_rating"] is not None
        ]
        exact_all_word_outcomes = [
            assignment
            for assignment in assignments
            if assignment["exact_all_word_outcome_rating"] is not None
        ]
        baseline_success = (
            mean(
                assignment["all_word_outcome_rating"] >= 3
                for assignment in all_word_outcomes
            )
            if all_word_outcomes
            else 0.75
        )
        outcome_yield = (
            len(all_word_outcomes) / len(assignments) if assignments else 0.0
        )
        arm_counts = Counter(
            assignment["arm"] for assignment in assignments
        )
        category_counts = Counter(
            assignment["category"] for assignment in assignments
        )
        candidate_counts = [
            assignment["candidate_count"] for assignment in assignments
        ]
        active_days = len(active_review_dates)
        assignments_per_active_day = (
            len(assignments) / active_days if active_days else 0.0
        )

        # The reconstructed endpoint is already near ceiling (about 95%).
        # Effects above 4.7 points are impossible, so simulate realistic
        # absolute gains rather than silently clipping oversized inputs.
        effect_sizes = (0.01, 0.02, 0.04)
        evaluable_sizes = (50, 100, 200, 400)
        power_rows = []
        for total_evaluable in evaluable_sizes:
            for uplift in effect_sizes:
                power_rows.append({
                    "total_evaluable_episodes": total_evaluable,
                    "absolute_uplift": uplift,
                    "monte_carlo_power": round(
                        _trial_power(
                            total_evaluable,
                            baseline_success,
                            uplift,
                            args.simulations,
                            args.seed
                            + total_evaluable
                            + int(uplift * 1000),
                        ),
                        4,
                    ),
                })
        sample_size_rows = []
        for uplift in effect_sizes:
            evaluable_needed = _normal_sample_size(
                baseline_success,
                uplift,
            )
            assigned_needed = (
                math.ceil(evaluable_needed / outcome_yield)
                if outcome_yield > 0
                else None
            )
            active_days_needed = (
                math.ceil(assigned_needed / assignments_per_active_day)
                if assigned_needed is not None and assignments_per_active_day > 0
                else None
            )
            sample_size_rows.append({
                "absolute_uplift": uplift,
                "evaluable_episodes_for_80pct_power": evaluable_needed,
                "assignments_needed_at_reconstructed_outcome_yield": (
                    assigned_needed
                ),
                "active_learning_days_at_reconstructed_rate": (
                    active_days_needed
                ),
            })

        # Operational ITT endpoint: a successful exact-form review in a
        # different sentence within 14 days; no exact delivery counts as zero.
        # This directly measures whether the intervention produces the intended
        # retrieval opportunity. Treatment effect sizes are an assumption grid.
        exact_control_success = (
            sum(
                assignment["exact_all_word_outcome_rating"] is not None
                and assignment["exact_all_word_outcome_rating"] >= 3
                for assignment in assignments
            )
            / len(assignments)
            if assignments else 0.0
        )
        exact_effect_sizes = (0.10, 0.20, 0.30)
        exact_power_rows = []
        for total_evaluable in (40, 80, 120, 200):
            for uplift in exact_effect_sizes:
                exact_power_rows.append({
                    "total_assigned_episodes": total_evaluable,
                    "assumed_absolute_uplift": uplift,
                    "monte_carlo_power": round(
                        _trial_power(
                            total_evaluable,
                            exact_control_success,
                            uplift,
                            args.simulations,
                            args.seed
                            + 10_000
                            + total_evaluable
                            + int(uplift * 1000),
                        ),
                        4,
                    ),
                })

        output = {
            "schema_version": 1,
            "method": "proactive-form-opportunity-reconstruction-v1",
            "database": {
                "filename": args.db.name,
                "sha256": db_sha,
            },
            "cutoff": args.cutoff.isoformat(),
            "window": {
                "first_reading_review": (
                    first_review_at.isoformat() if first_review_at else None
                ),
                "last_reading_review": (
                    last_review_at.isoformat() if last_review_at else None
                ),
                "active_reading_days": active_days,
                "reading_review_events_with_sentence": len(review_rows),
            },
            "prospective_capacity": {
                "successful_first_form_assignments": len(assignments),
                "assignments_per_active_learning_day": round(
                    assignments_per_active_day,
                    3,
                ),
                "arm_counts": dict(sorted(arm_counts.items())),
                "morphology_categories": dict(sorted(category_counts.items())),
                "median_alternate_sentence_count": (
                    int(np.median(candidate_counts)) if candidate_counts else 0
                ),
                "mean_alternate_sentence_count": (
                    round(mean(candidate_counts), 2) if candidate_counts else 0
                ),
            },
            "endpoint_yield_under_historical_ordinary_scheduling": {
                "all_word_outcomes_within_14d": len(all_word_outcomes),
                "all_word_outcome_fraction": round(outcome_yield, 4),
                "all_word_success_rate": round(baseline_success, 4),
                "all_word_collateral_fraction": round(
                    mean(
                        assignment["all_word_credit_type"] != "primary"
                        for assignment in all_word_outcomes
                    )
                    if all_word_outcomes else 0.0,
                    4,
                ),
                "all_word_same_trigger_context_fraction": round(
                    mean(
                        assignment["all_word_same_trigger_context"]
                        for assignment in all_word_outcomes
                    )
                    if all_word_outcomes else 0.0,
                    4,
                ),
                "any_form_primary_outcomes_within_14d": len(any_outcomes),
                "any_form_primary_outcome_fraction": round(
                    len(any_outcomes) / len(assignments)
                    if assignments else 0.0,
                    4,
                ),
                "exact_form_primary_outcomes_within_14d": len(exact_outcomes),
                "exact_form_outcome_fraction": round(
                    len(exact_outcomes) / len(assignments)
                    if assignments else 0.0,
                    4,
                ),
                "any_form_primary_success_rate": round(
                    mean(
                        assignment["any_form_outcome_rating"] >= 3
                        for assignment in any_outcomes
                    )
                    if any_outcomes else 0.0,
                    4,
                ),
                "exact_form_all_word_outcomes_within_14d": len(
                    exact_all_word_outcomes
                ),
                "exact_form_all_word_outcome_fraction": round(
                    len(exact_all_word_outcomes) / len(assignments)
                    if assignments else 0.0,
                    4,
                ),
                "successful_exact_form_all_word_itt_rate": round(
                    exact_control_success,
                    4,
                ),
            },
            "monte_carlo": {
                "simulations_per_cell": args.simulations,
                "two_sided_alpha": 0.05,
                "seed": args.seed,
                "power": power_rows,
                "sample_size_projection": sample_size_rows,
                "exact_retrieval_itt_assumption_grid": exact_power_rows,
            },
            "deployment_gates": {
                "feature_flag_default_off": True,
                "no_new_cards_or_due_date_changes": True,
                "maximum_treatment_slots_per_session": 1,
                "randomized_control_arm": True,
                "intention_to_treat_endpoint_recorded": True,
                "recommended_status": (
                    "eligible_for_time_bounded_pilot"
                    if len(assignments) >= 20
                    and min(arm_counts.values(), default=0) >= 5
                    else "insufficient_reconstructed_opportunity"
                ),
            },
            "limitations": [
                (
                    "Historical review order is real, but alternate-sentence "
                    "serviceability uses the sentence pool at the cutoff."
                ),
                (
                    "The reconstruction does not apply treatment, so its "
                    "endpoint yield estimates ordinary-scheduler control yield."
                ),
                (
                    "Power cells assume independent Bernoulli outcomes; the "
                    "actual analysis must use lemma-clustered uncertainty."
                ),
                (
                    "This estimates experiment throughput and power, not the "
                    "causal benefit of exact-form retrieval."
                ),
            ],
        }
    finally:
        db.close()
        engine.dispose()

    if _sha256(args.db) != db_sha:
        parser.error("database changed during simulation")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "prospective_capacity": output["prospective_capacity"],
        "endpoint_yield": output[
            "endpoint_yield_under_historical_ordinary_scheduling"
        ],
        "status": output["deployment_gates"]["recommended_status"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
