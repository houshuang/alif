from datetime import datetime, timezone

from scripts.analyze_low_energy_maintenance_experiment import (
    BASELINE,
    evaluate_signals,
    sqlite_timestamp,
    summarize_interactions,
)


START = datetime(2026, 9, 3, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 9, 10, tzinfo=timezone.utc)


def test_sqlite_timestamp_matches_sqlalchemy_naive_utc_storage():
    assert sqlite_timestamp(START) == "2026-09-03 00:00:00"


def test_interaction_summary_audits_policy_invariants():
    events = [
        {
            "ts": "2026-09-04T08:00:00Z",
            "event": "session_start",
            "total_due_words": 100,
            "card_count": 30,
            "passage_count": 0,
            "selection_diagnostics": {
                "learning_policy_version": "low_energy_maintenance_v1",
                "cards_over_due_density_cap": 0,
                "selection_reason_counts": {"greedy_cover": 30},
            },
        },
        {
            "ts": "2026-09-04T08:01:00Z",
            "event": "mature_collateral_exposure",
            "policy_version": "low_energy_maintenance_v1",
            "lemma_id": 10,
            "credit_type": "collateral",
            "knowledge_state": "known",
            "review_mode": "reading",
            "was_due": False,
            "rating": 3,
            "retrievability": 0.98,
        },
        {
            "ts": "2026-09-04T08:02:00Z",
            "event": "mature_collateral_exposure",
            "policy_version": "low_energy_maintenance_v1",
            "lemma_id": 11,
            "credit_type": "primary",
            "knowledge_state": "known",
            "review_mode": "reading",
            "was_due": False,
            "rating": 3,
            "retrievability": 0.99,
        },
    ]

    result = summarize_interactions(events, start=START, cutoff=CUTOFF)

    assert result["policy_session_count"] == 1
    assert result["daily_sentence_cards"] == {"2026-09-04": 30}
    assert result["mature_collateral_exposures"] == 2
    assert result["exposure_invariant_breaches"] == 1


def test_evaluation_triggers_hard_and_directional_signals():
    current = {
        "recovery": {
            "values": {
                "strict_main_fsrs_due": 1000,
                "box1_actionable": 30,
                "box2_due": 20,
            }
        }
    }
    workload = {
        "max_true_new_intake_on_day": 3,
        "active_days": 7,
        "median_cards_per_active_day": 20,
    }
    retention = {
        "cards_with_5plus_scheduled_judgments": 1,
        "old_scheduled_by_prior_gap": {
            ">=7d": {"judgments": 25, "clean_pct": 76.0},
            ">=14d": {"judgments": 20, "clean_pct": 75.0},
        },
    }
    interactions = {
        "density_cap_breaches": 0,
        "exposure_invariant_breaches": 0,
        "automatic_passage_cards": 0,
    }

    signals = evaluate_signals(
        elapsed_days=14,
        current=current,
        workload=workload,
        retention=retention,
        interactions=interactions,
    )
    triggered = {signal["code"] for signal in signals if signal["triggered"]}

    assert {
        "density_cap_breach",
        "daily_intake_cap_breach",
        "severe_debt_growth",
        "debt_not_burning_down",
        "old_7d_retention_low",
        "old_14d_retention_low",
        "daily_volume_below_maintenance_target",
    } <= triggered
    assert BASELINE["strict_main_fsrs_due"] == 749
