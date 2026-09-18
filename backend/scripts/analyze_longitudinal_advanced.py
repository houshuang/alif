#!/usr/bin/env python3
"""Advanced, read-only analyses for the longitudinal Alif learning report.

This script deliberately imports the canonical event reconstruction from
``analyze_longitudinal_report.py`` so the original and advanced reports share
the same all-word sentence evidence, canonicalization, exclusions, and clock.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import binomtest
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    brier_score_loss,
    log_loss,
    roc_auc_score,
)
from sklearn.preprocessing import SplineTransformer

import analyze_longitudinal_report as core

try:
    from fsrs import Card, Scheduler
except ImportError:  # pragma: no cover - workspace dependency is expected
    Card = None
    Scheduler = None


TRIAL_START = pd.Timestamp("2026-03-03T22:17:14Z")
TRIAL_END = pd.Timestamp("2026-03-21T19:19:19Z")
RNG_SEED = 20260730


def stable_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode()


def load_extra(connection: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    knowledge = pd.read_sql_query(
        """
        SELECT lemma_id, experiment_group, experiment_intro_shown_at,
               introduced_at, acquisition_started_at, entered_acquiring_at,
               graduated_at, knowledge_state, source
        FROM user_lemma_knowledge
        """,
        connection,
    )
    sentences = pd.read_sql_query(
        """
        SELECT id AS sentence_id, source AS sentence_source, arabic_text,
               difficulty_score
        FROM sentences
        """,
        connection,
    )
    grammar = pd.read_sql_query(
        """
        SELECT sentence_id, COUNT(*) AS grammar_feature_count
        FROM sentence_grammar_features
        GROUP BY sentence_id
        """,
        connection,
    )
    roots = pd.read_sql_query(
        "SELECT root_id, root, productivity_score FROM roots",
        connection,
    )
    confusions = pd.read_sql_query(
        """
        SELECT id, failed_lemma_id, confused_with_lemma_id,
               resolved_lemma_id, confused_with_text, rating,
               captured_at, capture_method, resolution_confidence
        FROM confusion_captures
        """,
        connection,
    )
    return {
        "knowledge": knowledge,
        "sentences": sentences,
        "grammar": grammar,
        "roots": roots,
        "confusions": confusions,
    }


def add_activation_history(frame: pd.DataFrame) -> pd.DataFrame:
    """Add ACT-R-inspired recency-weighted successful/failure activation."""
    result = frame.copy()
    success_activation = pd.Series(0.0, index=result.index)
    failure_activation = pd.Series(0.0, index=result.index)
    prior_other_root = pd.Series(0, index=result.index, dtype=int)
    for lemma_id, group in result.groupby("lemma_id", sort=False):
        prior_success: list[pd.Timestamp] = []
        prior_failure: list[pd.Timestamp] = []
        for row in group.sort_values(["reviewed_at", "id"]).itertuples():
            now = row.reviewed_at
            if prior_success:
                hours = np.array(
                    [
                        max((now - value).total_seconds() / 3600, 1 / 60)
                        for value in prior_success
                    ]
                )
                success_activation.at[row.Index] = float(
                    np.power(hours + 1, -0.5).sum()
                )
            if prior_failure:
                hours = np.array(
                    [
                        max((now - value).total_seconds() / 3600, 1 / 60)
                        for value in prior_failure
                    ]
                )
                failure_activation.at[row.Index] = float(
                    np.power(hours + 1, -0.5).sum()
                )
            if row.review_mode != "reading" or pd.isna(row.sentence_id):
                continue
            if bool(row.clean):
                prior_success.append(now)
            else:
                prior_failure.append(now)
    root_counts: Counter = Counter()
    lemma_counts: Counter = Counter()
    for row in result.sort_values(["reviewed_at", "id"]).itertuples():
        root_id = row.root_id if pd.notna(row.root_id) else None
        if root_id is not None:
            prior_other_root.at[row.Index] = (
                root_counts[int(root_id)] - lemma_counts[int(row.lemma_id)]
            )
        if row.review_mode != "reading" or pd.isna(row.sentence_id):
            continue
        if root_id is not None:
            root_counts[int(root_id)] += 1
            lemma_counts[int(row.lemma_id)] += 1
    result["success_activation"] = success_activation
    result["failure_activation"] = failure_activation
    result["prior_other_root_exposures"] = prior_other_root
    return result


def build_event_data(
    database: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict, dict]:
    connection = core.open_immutable(database)
    loaded = core.load_data(connection)
    extra = load_extra(connection)
    connection.close()

    lemmas = loaded["lemmas"].copy()
    valid_ids = core.valid_lemma_ids(lemmas)
    canonicals = core.canonical_map(lemmas)
    sentence_words = loaded["sentence_words"].copy()
    sentence_words["canonical_id"] = sentence_words["lemma_id"].map(canonicals)
    sentence_surface_map = (
        sentence_words.groupby(["sentence_id", "canonical_id"])["surface_form"]
        .apply(lambda values: tuple(sorted(set(values))))
        .to_dict()
    )

    reviews = loaded["reviews"]
    reviews = reviews[reviews["lemma_id"].isin(valid_ids)].copy()
    reviews["source_lemma_id"] = reviews["lemma_id"].astype(int)
    reviews["lemma_id"] = reviews["lemma_id"].map(canonicals).astype(int)
    reviews["reviewed_at"] = pd.to_datetime(reviews["reviewed_at"], utc=True)
    reviews["day"] = reviews["reviewed_at"].dt.floor("D")
    reviews["clean"] = (
        (reviews["rating"] >= 3)
        & (~reviews["was_confused"].astype(bool))
    )
    canonical_features = lemmas[
        [
            "lemma_id",
            "lemma_ar",
            "lemma_ar_bare",
            "gloss_en",
            "pos",
            "root_id",
            "wazn",
            "frequency_rank",
            "forms_json",
        ]
    ].copy()
    reviews = reviews.merge(
        canonical_features,
        on="lemma_id",
        how="left",
        validate="many_to_one",
    )
    group_map = extra["knowledge"].set_index("lemma_id")
    reviews["experiment_group"] = reviews["source_lemma_id"].map(
        group_map["experiment_group"]
    )
    reviews["experiment_intro_shown_at"] = reviews["source_lemma_id"].map(
        group_map["experiment_intro_shown_at"]
    )
    reviews = core.add_presentation_metadata(reviews)
    reviews = core.add_review_history(reviews, sentence_surface_map)
    reviews = add_activation_history(reviews)

    sentence_outcomes = reviews[
        (reviews["review_mode"] == "reading")
        & reviews["sentence_id"].notna()
    ].copy()
    sentence_meta = extra["sentences"].merge(
        extra["grammar"], on="sentence_id", how="left"
    )
    sentence_meta["grammar_feature_count"] = (
        sentence_meta["grammar_feature_count"].fillna(0).astype(int)
    )
    sentence_meta["arabic_characters"] = sentence_meta["arabic_text"].str.len()
    sentence_outcomes = sentence_outcomes.merge(
        sentence_meta[
            [
                "sentence_id",
                "sentence_source",
                "difficulty_score",
                "grammar_feature_count",
                "arabic_characters",
            ]
        ],
        on="sentence_id",
        how="left",
        validate="many_to_one",
    )
    fragile = (
        (sentence_outcomes["prior_n"] < 3)
        | (sentence_outcomes["prior_acc"].fillna(0) < 0.65)
    ).astype(int)
    novel = sentence_outcomes["novel_form"].astype(int)
    sentence_outcomes["other_fragile_words"] = (
        fragile.groupby(sentence_outcomes["presentation_id"]).transform("sum")
        - fragile
    )
    sentence_outcomes["other_novel_forms"] = (
        novel.groupby(sentence_outcomes["presentation_id"]).transform("sum")
        - novel
    )
    sentence_outcomes["sentence_content_words"] = (
        sentence_outcomes.groupby("presentation_id")["id"].transform("size")
    )

    roots = extra["roots"].set_index("root_id")
    sentence_outcomes["root"] = sentence_outcomes["root_id"].map(roots["root"])
    return reviews, sentence_outcomes, lemmas, loaded, extra


def newcombe_difference(
    success_a: int, n_a: int, success_b: int, n_b: int
) -> tuple[float, float]:
    low_a, high_a = core.wilson(success_a, n_a)
    low_b, high_b = core.wilson(success_b, n_b)
    return 100 * (low_a - high_b), 100 * (high_a - low_b)


def permutation_pvalue(
    outcomes: np.ndarray,
    group: np.ndarray,
    iterations: int = 20000,
) -> float:
    rng = np.random.default_rng(RNG_SEED)
    observed = outcomes[group == 1].mean() - outcomes[group == 0].mean()
    values = np.empty(iterations)
    for index in range(iterations):
        shuffled = rng.permutation(group)
        values[index] = (
            outcomes[shuffled == 1].mean()
            - outcomes[shuffled == 0].mean()
        )
    return float((1 + (np.abs(values) >= abs(observed)).sum()) / (iterations + 1))


def intro_trial_analysis(
    reviews: pd.DataFrame,
    sentence_outcomes: pd.DataFrame,
    knowledge: pd.DataFrame,
) -> dict:
    assignments = knowledge[
        knowledge["experiment_group"].isin(
            ["intro_ab_card", "intro_ab_sentence"]
        )
    ].copy()
    assignments["acquisition_started_at"] = pd.to_datetime(
        assignments["acquisition_started_at"], utc=True, errors="coerce"
    )
    assignments["experiment_intro_shown_at"] = pd.to_datetime(
        assignments["experiment_intro_shown_at"], utc=True, errors="coerce"
    )
    assignments = assignments[
        assignments["acquisition_started_at"].between(
            TRIAL_START, TRIAL_END, inclusive="left"
        )
    ]
    eligible = reviews[
        reviews["is_acquisition"].astype(bool)
        & (reviews["review_mode"] == "reading")
        & reviews["sentence_id"].notna()
    ].merge(
        assignments[
            [
                "lemma_id",
                "experiment_group",
                "acquisition_started_at",
                "experiment_intro_shown_at",
            ]
        ],
        left_on="source_lemma_id",
        right_on="lemma_id",
        how="inner",
        suffixes=("", "_assignment"),
        validate="many_to_one",
    )
    eligible = eligible[
        eligible["reviewed_at"] >= eligible["acquisition_started_at"]
    ]
    first = (
        eligible.sort_values(["reviewed_at", "id"])
        .groupby("source_lemma_id", as_index=False)
        .first()
    )
    first = first[
        [
            "source_lemma_id",
            "experiment_group",
            "reviewed_at",
            "clean",
            "acquisition_started_at",
            "experiment_intro_shown_at",
            "pos",
            "frequency_rank",
            "lemma_ar_bare",
        ]
    ].rename(columns={"reviewed_at": "t0", "clean": "first_clean"})
    first["experiment_intro_shown_at"] = pd.to_datetime(
        first["experiment_intro_shown_at"], utc=True, errors="coerce"
    )
    first["card_before_first"] = (
        first["experiment_intro_shown_at"].notna()
        & (first["experiment_intro_shown_at"] <= first["t0"])
    )
    first["arabic_length"] = first["lemma_ar_bare"].fillna("").str.len()
    prior_source_ids = set()
    for item in first.itertuples():
        had_prior = (
            (sentence_outcomes["source_lemma_id"] == item.source_lemma_id)
            & (
                sentence_outcomes["reviewed_at"]
                < item.acquisition_started_at
            )
        ).any()
        if had_prior:
            prior_source_ids.add(item.source_lemma_id)
    first["prior_sentence_history"] = first["source_lemma_id"].isin(
        prior_source_ids
    )

    rows: list[dict] = []
    for horizon in [0, 1, 3, 7, 14, 30, 60, 90]:
        observations: list[dict] = []
        for item in first.itertuples():
            if horizon == 0:
                observations.append(
                    {
                        "group": item.experiment_group,
                        "clean": bool(item.first_clean),
                        "prior_intervening": 0,
                    }
                )
                continue
            candidates = sentence_outcomes[
                (sentence_outcomes["source_lemma_id"] == item.source_lemma_id)
                & (
                    sentence_outcomes["reviewed_at"]
                    >= item.t0 + pd.Timedelta(days=horizon)
                )
            ].sort_values(["reviewed_at", "id"])
            if candidates.empty:
                continue
            event = candidates.iloc[0]
            prior_intervening = sentence_outcomes[
                (sentence_outcomes["source_lemma_id"] == item.source_lemma_id)
                & (sentence_outcomes["reviewed_at"] > item.t0)
                & (sentence_outcomes["reviewed_at"] < event.reviewed_at)
            ].shape[0]
            observations.append(
                {
                    "group": item.experiment_group,
                    "clean": bool(event.clean),
                    "prior_intervening": prior_intervening,
                }
            )
        data = pd.DataFrame(observations)
        if data.empty:
            continue
        summaries = {}
        for group in ["intro_ab_card", "intro_ab_sentence"]:
            values = data[data["group"] == group]
            successes = int(values["clean"].sum())
            low, high = core.wilson(successes, len(values))
            summaries[group] = {
                "n": len(values),
                "successes": successes,
                "recall": core.pct(values["clean"].mean()),
                "low": core.pct(low),
                "high": core.pct(high),
                "median_intervening_exposures": core.rounded(
                    values["prior_intervening"].median(), 1
                ),
            }
        card = summaries["intro_ab_card"]
        sentence = summaries["intro_ab_sentence"]
        low, high = newcombe_difference(
            card["successes"],
            card["n"],
            sentence["successes"],
            sentence["n"],
        )
        group_binary = (
            data["group"] == "intro_ab_card"
        ).astype(int).to_numpy()
        rows.append(
            {
                "horizon_days": horizon,
                "label": "First acquisition judgment"
                if horizon == 0
                else f"First judgment after ≥{horizon}d",
                "groups": summaries,
                "effect": round(card["recall"] - sentence["recall"], 1),
                "low": round(low, 1),
                "high": round(high, 1),
                "randomization_p": round(
                    permutation_pvalue(
                        data["clean"].astype(int).to_numpy(),
                        group_binary,
                    ),
                    4,
                ),
            }
        )
    ordered = sorted(
        enumerate(rows), key=lambda item: item[1]["randomization_p"]
    )
    running = 0.0
    for rank, (index, row) in enumerate(ordered):
        adjusted = min(1.0, row["randomization_p"] * (len(rows) - rank))
        running = max(running, adjusted)
        rows[index]["holm_p"] = round(running, 4)

    balance: list[dict] = []
    for column, label in [
        ("frequency_rank", "Frequency rank"),
        ("arabic_length", "Arabic character length"),
    ]:
        values = first[["experiment_group", column]].dropna()
        card = values[values["experiment_group"] == "intro_ab_card"][column]
        sentence = values[
            values["experiment_group"] == "intro_ab_sentence"
        ][column]
        pooled = math.sqrt((card.var(ddof=1) + sentence.var(ddof=1)) / 2)
        balance.append(
            {
                "feature": label,
                "card_mean": core.rounded(card.mean(), 2),
                "sentence_mean": core.rounded(sentence.mean(), 2),
                "standardized_difference": core.rounded(
                    (card.mean() - sentence.mean()) / pooled if pooled else 0,
                    3,
                ),
            }
        )
    pos_table = pd.crosstab(first["experiment_group"], first["pos"].fillna(""))
    pos_balance = []
    for pos in sorted(pos_table.columns):
        pos_balance.append(
            {
                "pos": pos or "unknown",
                "card_pct": core.pct(
                    pos_table.loc["intro_ab_card", pos]
                    / pos_table.loc["intro_ab_card"].sum()
                ),
                "sentence_pct": core.pct(
                    pos_table.loc["intro_ab_sentence", pos]
                    / pos_table.loc["intro_ab_sentence"].sum()
                ),
            }
        )
    group_counts = first["experiment_group"].value_counts()
    total = int(len(first))
    first_time = first[~first["prior_sentence_history"]]
    first_time_groups = {}
    for group in ["intro_ab_card", "intro_ab_sentence"]:
        values = first_time[first_time["experiment_group"] == group]
        successes = int(values["first_clean"].sum())
        low, high = core.wilson(successes, len(values))
        first_time_groups[group] = {
            "n": len(values),
            "successes": successes,
            "recall": core.pct(values["first_clean"].mean()),
            "low": core.pct(low),
            "high": core.pct(high),
        }
    first_time_card = first_time_groups["intro_ab_card"]
    first_time_sentence = first_time_groups["intro_ab_sentence"]
    first_time_low, first_time_high = newcombe_difference(
        first_time_card["successes"],
        first_time_card["n"],
        first_time_sentence["successes"],
        first_time_sentence["n"],
    )
    return {
        "start": TRIAL_START.isoformat(),
        "end": TRIAL_END.isoformat(),
        "cohort_definition": (
            "Acquisition started while the random.choice assignment code was "
            "active; outcome is each assigned word's first acquisition "
            "judgment in a displayed reading sentence."
        ),
        "assigned_n": int(len(assignments)),
        "assigned_without_outcome_n": int(len(assignments) - len(first)),
        "n": total,
        "card_n": int(group_counts.get("intro_ab_card", 0)),
        "sentence_n": int(group_counts.get("intro_ab_sentence", 0)),
        "allocation_binomial_p": round(
            float(
                binomtest(
                    int(group_counts.get("intro_ab_card", 0)),
                    total,
                    0.5,
                ).pvalue
            ),
            4,
        ),
        "card_received_before_first_n": int(
            first.loc[
                first["experiment_group"] == "intro_ab_card",
                "card_before_first",
            ].sum()
        ),
        "sentence_later_card_crossover_n": int(
            first.loc[
                first["experiment_group"] == "intro_ab_sentence",
                "experiment_intro_shown_at",
            ].notna().sum()
        ),
        "prior_sentence_history_n": int(first["prior_sentence_history"].sum()),
        "first_time_sensitivity": {
            "definition": (
                "Assigned words with no earlier displayed reading-sentence "
                "outcome before this acquisition start."
            ),
            "groups": first_time_groups,
            "effect": round(
                first_time_card["recall"]
                - first_time_sentence["recall"],
                1,
            ),
            "low": round(first_time_low, 1),
            "high": round(first_time_high, 1),
            "randomization_p": round(
                permutation_pvalue(
                    first_time["first_clean"].astype(int).to_numpy(),
                    (
                        first_time["experiment_group"] == "intro_ab_card"
                    ).astype(int).to_numpy(),
                ),
                4,
            ),
        },
        "outcomes": rows,
        "balance": balance,
        "pos_balance": pos_balance,
        "limitations": [
            "The randomization draw itself was not timestamped; acquisition_started_at defines cohort entry.",
            "The current experiment_group field persists across later re-entry.",
            "There was protocol non-adherence: some card-assigned words were judged before a card was acknowledged, and some sentence-first words later crossed over.",
            "Later-horizon effects are total policy effects and include intervening exposures.",
        ],
    }


def safe_log_loss(y: np.ndarray, p: np.ndarray) -> float:
    return float(log_loss(y, np.clip(p, 1e-5, 1 - 1e-5), labels=[0, 1]))


def calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    frame = pd.DataFrame({"y": y, "p": p})
    try:
        frame["bin"] = pd.qcut(frame["p"], bins, duplicates="drop")
    except ValueError:
        return math.nan
    grouped = frame.groupby("bin", observed=True).agg(
        observed=("y", "mean"),
        predicted=("p", "mean"),
        n=("y", "size"),
    )
    return float(
        (
            (grouped["observed"] - grouped["predicted"]).abs()
            * grouped["n"]
        ).sum()
        / grouped["n"].sum()
    )


def metric_row(model: str, y: np.ndarray, p: np.ndarray) -> dict:
    return {
        "model": model,
        "n": len(y),
        "brier": core.rounded(brier_score_loss(y, p), 4),
        "log_loss": core.rounded(safe_log_loss(y, p), 4),
        "auc": core.rounded(
            roc_auc_score(y, p) if len(np.unique(y)) > 1 else math.nan,
            4,
        ),
        "calibration_error": core.rounded(calibration_error(y, p), 4),
        "mean_predicted": core.pct(np.mean(p)),
        "observed": core.pct(np.mean(y)),
    }


def basic_feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame(index=frame.index)
    result["log_gap"] = np.log1p(frame["gap_any_d"].clip(lower=0))
    result["log_prior_n"] = np.log1p(frame["prior_n"])
    result["log_prior_days"] = np.log1p(frame["prior_days"])
    result["prior_acc"] = frame["prior_acc"].fillna(0.5)
    result["day_density"] = frame["day_density"].fillna(0)
    result["context_density"] = frame["context_density"].fillna(0)
    result["novel_form"] = frame["novel_form"].astype(int)
    result["same_sentence"] = frame["same_sentence"].astype(int)
    result["log_frequency"] = np.log1p(
        frame["frequency_rank"].fillna(
            frame["frequency_rank"].median()
        )
    )
    result["success_activation"] = np.log1p(frame["success_activation"])
    result["failure_activation"] = np.log1p(frame["failure_activation"])
    result["is_acquisition"] = frame["is_acquisition"].astype(int)
    result["is_primary"] = (frame["credit_type"] == "primary").astype(int)
    result["sentence_words"] = np.log1p(frame["sentence_content_words"])
    result = pd.concat(
        [
            result,
            pd.get_dummies(
                frame["pos"].fillna("unknown"),
                prefix="pos",
                dtype=float,
            ),
        ],
        axis=1,
    )
    return result.astype(float)


def fit_logistic(
    train: pd.DataFrame,
    test: pd.DataFrame,
    columns: list[str],
    c_value: float = 0.3,
) -> np.ndarray:
    all_columns = sorted(set(train.columns) | set(test.columns))
    x_train = train.reindex(columns=all_columns, fill_value=0)[columns].to_numpy()
    x_test = test.reindex(columns=all_columns, fill_value=0)[columns].to_numpy()
    means = x_train.mean(axis=0)
    scales = x_train.std(axis=0)
    scales[scales == 0] = 1
    model = LogisticRegression(
        C=c_value,
        max_iter=1000,
        solver="lbfgs",
        random_state=RNG_SEED,
    )
    model.fit((x_train - means) / scales, train.attrs["y"])
    return model.predict_proba((x_test - means) / scales)[:, 1]


def fit_hlr(
    train_frame: pd.DataFrame,
    test_frame: pd.DataFrame,
) -> np.ndarray:
    features = ["log_prior_n", "prior_acc", "log_prior_days", "day_density"]
    x_train = train_frame[features].to_numpy()
    x_test = test_frame[features].to_numpy()
    mean = x_train.mean(axis=0)
    scale = x_train.std(axis=0)
    scale[scale == 0] = 1
    x_train = np.column_stack(
        [np.ones(len(x_train)), (x_train - mean) / scale]
    )
    x_test = np.column_stack(
        [np.ones(len(x_test)), (x_test - mean) / scale]
    )
    gap_train = np.expm1(train_frame["log_gap"].to_numpy())
    gap_test = np.expm1(test_frame["log_gap"].to_numpy())
    y = train_frame.attrs["y"]

    def predict(weights: np.ndarray, x: np.ndarray, gap: np.ndarray) -> np.ndarray:
        log_half_life = np.clip(x @ weights, -4, 9)
        half_life = np.exp(log_half_life)
        return np.clip(np.power(2.0, -gap / half_life), 1e-5, 1 - 1e-5)

    def objective(weights: np.ndarray) -> float:
        probability = predict(weights, x_train, gap_train)
        likelihood = -np.mean(
            y * np.log(probability) + (1 - y) * np.log(1 - probability)
        )
        return float(likelihood + 0.003 * np.square(weights[1:]).sum())

    fitted = minimize(
        objective,
        np.zeros(x_train.shape[1]),
        method="L-BFGS-B",
        options={"maxiter": 500},
    )
    return predict(fitted.x, x_test, gap_test)


def fsrs_predictions(frame: pd.DataFrame) -> pd.Series:
    if Scheduler is None or Card is None:
        return pd.Series(np.nan, index=frame.index)
    scheduler = Scheduler(desired_retention=0.95)
    result = pd.Series(np.nan, index=frame.index)
    for row in frame.itertuples():
        if bool(row.is_acquisition) or not row.fsrs_log_json:
            continue
        try:
            metadata = (
                json.loads(row.fsrs_log_json)
                if isinstance(row.fsrs_log_json, str)
                else row.fsrs_log_json
            )
            if not isinstance(metadata, dict):
                continue
            pre_card = metadata.get("pre_card")
            if not isinstance(pre_card, dict):
                continue
            card = Card.from_dict(pre_card)
            probability = scheduler.get_card_retrievability(
                card, current_datetime=row.reviewed_at.to_pydatetime()
            )
            result.at[row.Index] = float(probability)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return result


def predictive_bakeoff(sentence_outcomes: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    eligible = sentence_outcomes[
        (sentence_outcomes["prior_n"] >= 1)
        & sentence_outcomes["gap_any_d"].notna()
        & (sentence_outcomes["gap_any_d"] >= 1 / 24)
    ].copy()
    eligible["fsrs_prediction"] = fsrs_predictions(eligible)
    features = basic_feature_frame(eligible)
    features.attrs["y"] = eligible["clean"].astype(int).to_numpy()
    prediction_rows: list[pd.DataFrame] = []
    fold_summary: list[dict] = []
    fold_starts = [
        pd.Timestamp("2026-04-01T00:00:00Z"),
        pd.Timestamp("2026-05-01T00:00:00Z"),
        pd.Timestamp("2026-06-01T00:00:00Z"),
        pd.Timestamp("2026-07-01T00:00:00Z"),
    ]
    fold_ends = fold_starts[1:] + [core.CUTOFF]
    for start, end in zip(fold_starts, fold_ends):
        train_mask = eligible["reviewed_at"] < start
        test_mask = eligible["reviewed_at"].between(start, end, inclusive="left")
        if train_mask.sum() < 500 or test_mask.sum() < 100:
            continue
        train_features = features.loc[train_mask].copy()
        test_features = features.loc[test_mask].copy()
        train_features.attrs["y"] = (
            eligible.loc[train_mask, "clean"].astype(int).to_numpy()
        )
        test_features.attrs["y"] = (
            eligible.loc[test_mask, "clean"].astype(int).to_numpy()
        )
        recency_columns = ["log_gap"]
        activation_columns = [
            "log_gap",
            "log_prior_n",
            "prior_acc",
            "success_activation",
            "failure_activation",
        ]
        history_columns = list(train_features.columns)
        predictions = {
            "Recency-only": fit_logistic(
                train_features, test_features, recency_columns, 1.0
            ),
            "Half-life regression": fit_hlr(
                train_features, test_features
            ),
            "Activation history": fit_logistic(
                train_features, test_features, activation_columns, 0.5
            ),
            "Full history logistic": fit_logistic(
                train_features, test_features, history_columns, 0.3
            ),
        }
        fold = eligible.loc[test_mask].copy()
        for name, values in predictions.items():
            fold[name] = values
        fold["fold"] = start.strftime("%Y-%m")
        prediction_rows.append(fold)
        fold_summary.append(
            {
                "fold": start.strftime("%Y-%m"),
                "train_n": int(train_mask.sum()),
                "test_n": int(test_mask.sum()),
            }
        )
    predicted = pd.concat(prediction_rows).sort_values(["reviewed_at", "id"])
    model_names = [
        "Recency-only",
        "Half-life regression",
        "Activation history",
        "Full history logistic",
    ]
    metrics = []
    for subset_name, mask in [
        ("All ≥1-hour tests", np.ones(len(predicted), dtype=bool)),
        ("Cold tests ≥3d", (predicted["gap_any_d"] >= 3).to_numpy()),
    ]:
        y = predicted.loc[mask, "clean"].astype(int).to_numpy()
        for name in model_names:
            row = metric_row(name, y, predicted.loc[mask, name].to_numpy())
            row["subset"] = subset_name
            metrics.append(row)
    common = predicted["fsrs_prediction"].notna()
    common_metrics = []
    if common.sum():
        y = predicted.loc[common, "clean"].astype(int).to_numpy()
        for name in model_names:
            common_metrics.append(
                metric_row(name, y, predicted.loc[common, name].to_numpy())
            )
        common_metrics.append(
            metric_row(
                "FSRS retrievability",
                y,
                predicted.loc[common, "fsrs_prediction"].to_numpy(),
            )
        )
    calibration = []
    for name in model_names:
        frame = predicted[["clean", name]].copy()
        frame["bin"] = pd.qcut(frame[name], 10, duplicates="drop")
        for interval, group in frame.groupby("bin", observed=True):
            calibration.append(
                {
                    "model": name,
                    "predicted": core.pct(group[name].mean()),
                    "observed": core.pct(group["clean"].mean()),
                    "n": len(group),
                }
            )
    return (
        {
            "folds": fold_summary,
            "metrics": metrics,
            "common_fsrs_n": int(common.sum()),
            "common_fsrs_metrics": common_metrics,
            "calibration": calibration,
            "eligibility": (
                "Reading sentence-word outcomes with prior displayed-sentence "
                "history and at least one hour since the preceding exposure."
            ),
        },
        predicted,
    )


def fit_surface_model(frame: pd.DataFrame) -> tuple:
    numeric = frame[
        [
            "gap_any_d",
            "prior_n",
            "day_density",
            "prior_acc",
            "context_density",
            "frequency_rank",
        ]
    ].copy()
    numeric["gap_any_d"] = np.log1p(numeric["gap_any_d"])
    numeric["prior_n"] = np.log1p(numeric["prior_n"])
    numeric["frequency_rank"] = np.log1p(
        numeric["frequency_rank"].fillna(
            numeric["frequency_rank"].median()
        )
    )
    numeric = numeric.fillna(numeric.median())
    spline = SplineTransformer(
        n_knots=5, degree=2, include_bias=False, extrapolation="linear"
    )
    curved = spline.fit_transform(numeric[["gap_any_d", "prior_n", "day_density"]])
    linear = numeric[
        ["prior_acc", "context_density", "frequency_rank"]
    ].to_numpy()
    interaction = np.column_stack(
        [
            numeric["gap_any_d"] * numeric["day_density"],
            numeric["prior_n"] * numeric["day_density"],
            numeric["gap_any_d"] * numeric["prior_n"],
        ]
    )
    nuisance = pd.get_dummies(
        frame[["pos"]].fillna("unknown"), dtype=float
    ).to_numpy()
    x = np.column_stack([curved, linear, interaction, nuisance])
    model = LogisticRegression(
        C=0.3,
        max_iter=1500,
        solver="lbfgs",
        random_state=RNG_SEED,
    )
    model.fit(x, frame["clean"].astype(int))
    return model, spline, numeric.columns, nuisance.shape[1]


def surface_predict(
    model,
    spline,
    template: pd.DataFrame,
    nuisance_columns: list[str],
) -> np.ndarray:
    numeric = template[
        [
            "gap_any_d",
            "prior_n",
            "day_density",
            "prior_acc",
            "context_density",
            "frequency_rank",
        ]
    ].copy()
    numeric["gap_any_d"] = np.log1p(numeric["gap_any_d"])
    numeric["prior_n"] = np.log1p(numeric["prior_n"])
    numeric["frequency_rank"] = np.log1p(numeric["frequency_rank"])
    curved = spline.transform(numeric[["gap_any_d", "prior_n", "day_density"]])
    linear = numeric[
        ["prior_acc", "context_density", "frequency_rank"]
    ].to_numpy()
    interaction = np.column_stack(
        [
            numeric["gap_any_d"] * numeric["day_density"],
            numeric["prior_n"] * numeric["day_density"],
            numeric["gap_any_d"] * numeric["prior_n"],
        ]
    )
    nuisance = pd.get_dummies(
        template[["pos"]].fillna("unknown"), dtype=float
    ).reindex(columns=nuisance_columns, fill_value=0)
    x = np.column_stack([curved, linear, interaction, nuisance])
    return model.predict_proba(x)[:, 1]


def spacing_surface(sentence_outcomes: pd.DataFrame) -> dict:
    frame = sentence_outcomes[
        (sentence_outcomes["gap_any_d"] >= 1)
        & (sentence_outcomes["prior_n"] >= 2)
    ].copy()
    nuisance_columns = list(
        pd.get_dummies(
            frame[["pos"]].fillna("unknown"), dtype=float
        ).columns
    )
    model, spline, _, _ = fit_surface_model(frame)
    grid = pd.DataFrame(
        [
            {
                "gap_any_d": gap,
                "prior_n": exposures,
                "day_density": density,
                "prior_acc": frame["prior_acc"].median(),
                "context_density": frame["context_density"].median(),
                "frequency_rank": frame["frequency_rank"].median(),
                "pos": "noun",
            }
            for exposures in [3, 5, 10, 20, 40]
            for gap in [1, 3, 7, 14, 30, 60]
            for density in [0.4, 0.6, 0.8, 1.0]
        ]
    )
    grid["predicted"] = surface_predict(
        model, spline, grid, nuisance_columns
    )
    rows = [
        {
            "prior_exposures": int(row.prior_n),
            "gap_days": int(row.gap_any_d),
            "day_density": row.day_density,
            "predicted_recall": core.pct(row.predicted),
        }
        for row in grid.itertuples()
    ]
    contrasts = []
    for (exposures, gap), group in grid.groupby(["prior_n", "gap_any_d"]):
        low = group.loc[group["day_density"] == 0.4, "predicted"].iloc[0]
        high = group.loc[group["day_density"] == 0.8, "predicted"].iloc[0]
        contrasts.append(
            {
                "prior_exposures": int(exposures),
                "gap_days": int(gap),
                "effect_08_vs_04": round(100 * (high - low), 1),
            }
        )
    return {
        "n": len(frame),
        "grid": rows,
        "contrasts": contrasts,
        "note": (
            "Penalized logistic spline surface; predictions hold prior "
            "accuracy, context density, frequency, and POS at representative values."
        ),
    }


def aipw_estimate(
    frame: pd.DataFrame,
    iterations: int = 200,
) -> dict:
    q25 = frame["day_density"].quantile(0.25)
    q75 = frame["day_density"].quantile(0.75)
    sample = frame[
        (frame["day_density"] <= q25) | (frame["day_density"] >= q75)
    ].copy()
    sample["treatment"] = (sample["day_density"] >= q75).astype(int)
    features = basic_feature_frame(sample)
    features = features.drop(columns=["day_density"])
    x = features.to_numpy()
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale == 0] = 1
    x = (x - mean) / scale
    treatment = sample["treatment"].to_numpy()
    outcome = sample["clean"].astype(int).to_numpy()

    def one_estimate(indices: np.ndarray) -> tuple[float, float, float, float]:
        xb = x[indices]
        ab = treatment[indices]
        yb = outcome[indices]
        propensity_model = LogisticRegression(
            C=0.2, max_iter=1000, random_state=RNG_SEED
        ).fit(xb, ab)
        propensity = np.clip(
            propensity_model.predict_proba(xb)[:, 1], 0.05, 0.95
        )
        model1 = LogisticRegression(
            C=0.3, max_iter=1000, random_state=RNG_SEED
        ).fit(xb[ab == 1], yb[ab == 1])
        model0 = LogisticRegression(
            C=0.3, max_iter=1000, random_state=RNG_SEED
        ).fit(xb[ab == 0], yb[ab == 0])
        mu1 = model1.predict_proba(xb)[:, 1]
        mu0 = model0.predict_proba(xb)[:, 1]
        psi1 = np.mean(mu1 + ab * (yb - mu1) / propensity)
        psi0 = np.mean(mu0 + (1 - ab) * (yb - mu0) / (1 - propensity))
        weights = ab / propensity + (1 - ab) / (1 - propensity)
        ess = np.square(weights.sum()) / np.square(weights).sum()
        return float(psi1 - psi0), float(ess), float(propensity.min()), float(propensity.max())

    point, ess, pmin, pmax = one_estimate(np.arange(len(sample)))
    rng = np.random.default_rng(RNG_SEED)
    words = sample["lemma_id"].unique()
    estimates = []
    for _ in range(iterations):
        sampled_words = rng.choice(words, size=len(words), replace=True)
        counts = Counter(int(value) for value in sampled_words)
        indices = np.concatenate(
            [
                np.tile(
                    np.flatnonzero(sample["lemma_id"].to_numpy() == word),
                    count,
                )
                for word, count in counts.items()
            ]
        )
        try:
            estimate, *_ = one_estimate(indices)
            estimates.append(estimate)
        except ValueError:
            continue
    return {
        "n": len(sample),
        "words": int(sample["lemma_id"].nunique()),
        "low_density_max": core.rounded(q25),
        "high_density_min": core.rounded(q75),
        "effect": round(100 * point, 1),
        "low": round(100 * np.quantile(estimates, 0.025), 1),
        "high": round(100 * np.quantile(estimates, 0.975), 1),
        "effective_sample_size": round(ess, 1),
        "propensity_min_after_clipping": round(pmin, 3),
        "propensity_max_after_clipping": round(pmax, 3),
        "bootstrap_iterations": len(estimates),
        "interpretation": (
            "Cross-sectional doubly robust sensitivity at delayed tests, "
            "not a full marginal structural model of the complete treatment history."
        ),
    }


def recovery_analysis(sentence_outcomes: pd.DataFrame) -> dict:
    episodes = []
    next_events = []
    for lemma_id, group in sentence_outcomes.groupby("lemma_id", sort=False):
        rows = group.sort_values(["reviewed_at", "id"]).reset_index(drop=True)
        for index in range(len(rows) - 1):
            current = rows.iloc[index]
            if bool(current.clean):
                continue
            following = rows.iloc[index + 1]
            delta = (
                following.reviewed_at - current.reviewed_at
            ).total_seconds() / 86400
            next_events.append(
                {
                    "delay_days": delta,
                    "clean": bool(following.clean),
                }
            )
            if index > 0 and not bool(rows.iloc[index - 1].clean):
                continue
            recovery_index = None
            for candidate in range(index + 1, len(rows)):
                if bool(rows.iloc[candidate].clean):
                    recovery_index = candidate
                    break
            if recovery_index is None:
                continue
            recovery = rows.iloc[recovery_index]
            delta_recovery = (
                recovery.reviewed_at - current.reviewed_at
            ).total_seconds() / 86400
            stable = None
            stable_gap = None
            for candidate in range(recovery_index + 1, len(rows)):
                test = rows.iloc[candidate]
                if test.gap_any_d >= 3:
                    stable = bool(test.clean)
                    stable_gap = float(test.gap_any_d)
                    break
            episodes.append(
                {
                    "lemma_id": int(lemma_id),
                    "attempts": recovery_index - index,
                    "days_to_clean": delta_recovery,
                    "stable": stable,
                    "stable_gap": stable_gap,
                    "recovery_timing": (
                        "<1 hour"
                        if delta_recovery < 1 / 24
                        else "1h–1d"
                        if delta_recovery < 1
                        else "1–3d"
                        if delta_recovery < 3
                        else "3d+"
                    ),
                }
            )
    episode_frame = pd.DataFrame(episodes)
    next_frame = pd.DataFrame(next_events)
    bands = [
        (-math.inf, 10 / 1440, "<10m"),
        (10 / 1440, 1 / 24, "10m–1h"),
        (1 / 24, 1, "1h–1d"),
        (1, 3, "1–3d"),
        (3, 7, "3–7d"),
        (7, math.inf, "7d+"),
    ]
    next_rows = []
    for low, high, label in bands:
        subset = next_frame[
            (next_frame["delay_days"] >= low)
            & (next_frame["delay_days"] < high)
        ]
        if len(subset):
            next_rows.append(
                {
                    "band": label,
                    "n": len(subset),
                    "next_clean": core.pct(subset["clean"].mean()),
                    "median_delay_days": core.rounded(
                        subset["delay_days"].median(), 2
                    ),
                }
            )
    recovery_rows = []
    for timing in ["<1 hour", "1h–1d", "1–3d", "3d+"]:
        subset = episode_frame[episode_frame["recovery_timing"] == timing]
        stable = subset["stable"].dropna()
        recovery_rows.append(
            {
                "timing": timing,
                "episodes": len(subset),
                "median_attempts": core.rounded(
                    subset["attempts"].median(), 1
                ),
                "median_days_to_clean": core.rounded(
                    subset["days_to_clean"].median(), 2
                ),
                "stable_n": len(stable),
                "stable_clean": core.pct(stable.mean()) if len(stable) else None,
            }
        )
    return {
        "failure_events_with_next": len(next_frame),
        "recovery_episodes": len(episode_frame),
        "next_outcomes": next_rows,
        "recovery_timing": recovery_rows,
        "definition": (
            "A recovery episode begins at the first failure in a consecutive "
            "failure run and ends at the next clean sentence-word outcome."
        ),
    }


def fit_history_probability(frame: pd.DataFrame) -> np.ndarray:
    features = basic_feature_frame(frame)
    x = features.to_numpy()
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale == 0] = 1
    model = LogisticRegression(
        C=0.3,
        max_iter=1200,
        solver="lbfgs",
        random_state=RNG_SEED,
    )
    model.fit((x - mean) / scale, frame["clean"].astype(int))
    return model.predict_proba((x - mean) / scale)[:, 1]


def session_adjusted_analysis(predicted: pd.DataFrame) -> list[dict]:
    """Compare session positions against strictly forward-chained predictions."""
    eligible = predicted.copy()
    eligible["expected"] = eligible["Full history logistic"]
    eligible = eligible[eligible["session_presentation_n"] >= 15].copy()
    eligible["band"] = pd.cut(
        eligible["presentation_position"],
        bins=[0, 3, 7, 12, 20, math.inf],
        labels=["1–3", "4–7", "8–12", "13–20", "21+"],
    )
    rows = []
    for band, group in eligible.groupby("band", observed=True):
        presentations = group.drop_duplicates("presentation_id")
        response = presentations["presentation_response_ms"]
        response = response[(response >= 1000) & (response <= 180000)]
        rows.append(
            {
                "band": str(band),
                "n": len(group),
                "presentations": int(group["presentation_id"].nunique()),
                "sessions": int(group["presentation_session"].nunique()),
                "observed": core.pct(group["clean"].mean()),
                "expected": core.pct(group["expected"].mean()),
                "residual_pp": round(
                    100 * (group["clean"].mean() - group["expected"].mean()),
                    1,
                ),
                "median_seconds": core.rounded(response.median() / 1000, 1),
            }
        )
    if rows:
        baseline = rows[0]["residual_pp"]
        for row in rows:
            row["residual_vs_first_pp"] = round(
                row["residual_pp"] - baseline, 1
            )
    return rows


def sentence_load_analysis(sentence_outcomes: pd.DataFrame) -> dict:
    frame = sentence_outcomes[
        (sentence_outcomes["prior_n"] >= 1)
        & sentence_outcomes["gap_any_d"].notna()
    ].copy()
    frame["word_band"] = pd.cut(
        frame["sentence_content_words"],
        bins=[0, 3, 5, 7, math.inf],
        labels=["1–3", "4–5", "6–7", "8+"],
    )
    frame["fragile_band"] = pd.cut(
        frame["other_fragile_words"],
        bins=[-1, 0, 1, 2, math.inf],
        labels=["0", "1", "2", "3+"],
    )
    cells = []
    for (word_band, fragile_band), group in frame.groupby(
        ["word_band", "fragile_band"], observed=True
    ):
        if len(group) < 50:
            continue
        cells.append(
            {
                "word_band": str(word_band),
                "other_fragile": str(fragile_band),
                "n": len(group),
                "recall": core.pct(group["clean"].mean()),
                "median_gap": core.rounded(group["gap_any_d"].median(), 1),
            }
        )
    feature_columns = [
        "gap_any_d",
        "prior_n",
        "prior_acc",
        "day_density",
        "context_density",
        "frequency_rank",
        "other_fragile_words",
        "other_novel_forms",
        "sentence_content_words",
        "grammar_feature_count",
        "arabic_characters",
    ]
    x = frame[feature_columns].copy()
    x["gap_any_d"] = np.log1p(x["gap_any_d"])
    x["prior_n"] = np.log1p(x["prior_n"])
    x["frequency_rank"] = np.log1p(
        x["frequency_rank"].fillna(x["frequency_rank"].median())
    )
    x = x.fillna(x.median())
    model = HistGradientBoostingClassifier(
        max_iter=250,
        max_leaf_nodes=15,
        min_samples_leaf=80,
        learning_rate=0.06,
        l2_regularization=4,
        random_state=RNG_SEED,
    ).fit(x, frame["clean"].astype(int))
    base = x.copy()
    contrasts = {}
    scenarios = {
        "other_fragile_0_vs_2": ("other_fragile_words", 0, 2),
        "other_novel_0_vs_2": ("other_novel_forms", 0, 2),
        "content_words_4_vs_8": ("sentence_content_words", 4, 8),
    }
    for key, (column, low, high) in scenarios.items():
        low_frame = base.copy()
        high_frame = base.copy()
        low_frame[column] = low
        high_frame[column] = high
        effect = (
            model.predict_proba(high_frame)[:, 1]
            - model.predict_proba(low_frame)[:, 1]
        ).mean()
        contrasts[key] = round(100 * effect, 1)
    return {
        "n": len(frame),
        "cells": cells,
        "adjusted_contrasts": contrasts,
        "fragile_definition": (
            "Another content word with fewer than three prior displayed-sentence "
            "exposures or prior clean rate below 65%."
        ),
    }


def morphology_analysis(
    sentence_outcomes: pd.DataFrame,
    confusions: pd.DataFrame,
    lemmas: pd.DataFrame,
    canonicals: dict[int, int],
) -> dict:
    tests = sentence_outcomes[
        (sentence_outcomes["gap_any_d"] >= 3)
        & (sentence_outcomes["prior_n"] >= 3)
        & sentence_outcomes["has_surface"]
    ].copy()
    transfer = []
    for pos in ["noun", "verb", "adj"]:
        subset = tests[tests["pos"] == pos]
        if len(subset) < 100:
            continue
        for novel, group in subset.groupby("novel_form"):
            transfer.append(
                {
                    "pos": pos,
                    "form": "new" if novel else "seen",
                    "n": len(group),
                    "recall": core.pct(group["clean"].mean()),
                }
            )
    tests["weak_root"] = tests["root"].fillna("").str.contains(
        r"[وي]"
    )
    weak_verbs = []
    verbs = tests[(tests["pos"] == "verb") & tests["root"].notna()]
    for value, group in verbs.groupby("weak_root"):
        weak_verbs.append(
            {
                "type": "weak-root" if value else "sound-root",
                "n": len(group),
                "words": int(group["lemma_id"].nunique()),
                "recall": core.pct(group["clean"].mean()),
                "novel_form_share": core.pct(group["novel_form"].mean()),
                "median_gap": core.rounded(group["gap_any_d"].median(), 1),
            }
        )
    early = sentence_outcomes[
        (sentence_outcomes["prior_n"].between(1, 5))
        & (sentence_outcomes["gap_any_d"] >= 1)
    ].copy()
    early["root_band"] = pd.cut(
        early["prior_other_root_exposures"],
        bins=[-1, 0, 5, math.inf],
        labels=["0", "1–5", "6+"],
    )
    root_transfer = []
    for band, group in early.groupby("root_band", observed=True):
        root_transfer.append(
            {
                "prior_other_root_exposures": str(band),
                "n": len(group),
                "words": int(group["lemma_id"].nunique()),
                "recall": core.pct(group["clean"].mean()),
                "median_gap": core.rounded(group["gap_any_d"].median(), 1),
            }
        )

    lemma_lookup = lemmas.set_index("lemma_id")
    edges: Counter = Counter()
    same_root = 0
    resolved = 0
    for row in confusions.itertuples():
        target = (
            row.resolved_lemma_id
            if pd.notna(row.resolved_lemma_id)
            else row.confused_with_lemma_id
        )
        if pd.isna(target):
            continue
        failed = canonicals.get(int(row.failed_lemma_id), int(row.failed_lemma_id))
        target = canonicals.get(int(target), int(target))
        if failed == target:
            continue
        edges[(failed, target)] += 1
        resolved += 1
        failed_root = (
            lemma_lookup.at[failed, "root_id"]
            if failed in lemma_lookup.index
            else None
        )
        target_root = (
            lemma_lookup.at[target, "root_id"]
            if target in lemma_lookup.index
            else None
        )
        if (
            pd.notna(failed_root)
            and pd.notna(target_root)
            and int(failed_root) == int(target_root)
        ):
            same_root += 1
    top_edges = []
    for (failed, target), count in edges.most_common(12):
        top_edges.append(
            {
                "failed_id": failed,
                "failed": (
                    lemma_lookup.at[failed, "lemma_ar"]
                    if failed in lemma_lookup.index
                    else str(failed)
                ),
                "confused_with_id": target,
                "confused_with": (
                    lemma_lookup.at[target, "lemma_ar"]
                    if target in lemma_lookup.index
                    else str(target)
                ),
                "count": count,
            }
        )
    return {
        "form_transfer": transfer,
        "weak_verbs": weak_verbs,
        "root_transfer": root_transfer,
        "resolved_confusion_edges": resolved,
        "same_root_confusion_share": core.pct(
            same_root / resolved if resolved else None
        ),
        "top_confusions": top_edges,
    }


def segment_cost(prefix: np.ndarray, prefix_sq: np.ndarray, i: int, j: int) -> float:
    count = j - i
    total = prefix[j] - prefix[i]
    total_sq = prefix_sq[j] - prefix_sq[i]
    return float((total_sq - np.square(total).sum() / count).sum())


def change_point_analysis(sentence_outcomes: pd.DataFrame) -> dict:
    daily = (
        sentence_outcomes.groupby("day")
        .agg(
            outcomes=("id", "size"),
            clean=("clean", "mean"),
            acquisition=("is_acquisition", "mean"),
            primary=("credit_type", lambda values: (values == "primary").mean()),
            median_gap=("gap_any_d", "median"),
            sentence_words=("sentence_content_words", "mean"),
            sessions=("presentation_session", "nunique"),
        )
        .reset_index()
        .sort_values("day")
    )
    columns = [
        "outcomes",
        "acquisition",
        "primary",
        "median_gap",
        "sentence_words",
        "sessions",
    ]
    values = daily[columns].copy()
    values["outcomes"] = np.log1p(values["outcomes"])
    values["sessions"] = np.log1p(values["sessions"])
    values = values.interpolate().bfill().ffill()
    x = (values - values.mean()) / values.std().replace(0, 1)
    matrix = x.to_numpy()
    n, p = matrix.shape
    prefix = np.vstack([np.zeros(p), np.cumsum(matrix, axis=0)])
    prefix_sq = np.vstack([np.zeros(p), np.cumsum(np.square(matrix), axis=0)])
    min_size = 10
    candidates = []
    for segments in range(1, 7):
        dp = np.full((segments + 1, n + 1), np.inf)
        back = np.full((segments + 1, n + 1), -1, dtype=int)
        dp[0, 0] = 0
        for k in range(1, segments + 1):
            for end in range(k * min_size, n + 1):
                starts = range((k - 1) * min_size, end - min_size + 1)
                for start in starts:
                    value = dp[k - 1, start] + segment_cost(
                        prefix, prefix_sq, start, end
                    )
                    if value < dp[k, end]:
                        dp[k, end] = value
                        back[k, end] = start
        sse = dp[segments, n]
        bic = n * p * math.log(max(sse / (n * p), 1e-9)) + (
            segments * (p + 1) * math.log(n)
        )
        bounds = [n]
        end = n
        for k in range(segments, 0, -1):
            end = int(back[k, end])
            bounds.append(end)
        bounds = sorted(bounds)
        candidates.append((bic, segments, bounds))
    _, segments, bounds = min(candidates)
    rows = []
    for index in range(len(bounds) - 1):
        start, end = bounds[index], bounds[index + 1]
        group = daily.iloc[start:end]
        rows.append(
            {
                "segment": index + 1,
                "start": group["day"].min().date().isoformat(),
                "end": group["day"].max().date().isoformat(),
                "active_days": len(group),
                "outcomes_per_day": round(group["outcomes"].mean(), 1),
                "clean": core.pct(group["clean"].mean()),
                "acquisition_share": core.pct(group["acquisition"].mean()),
                "primary_share": core.pct(group["primary"].mean()),
                "median_gap": core.rounded(group["median_gap"].median(), 1),
                "sentence_words": core.rounded(
                    group["sentence_words"].mean(), 1
                ),
            }
        )
    return {
        "active_days": n,
        "selected_segments": segments,
        "segments": rows,
        "method": (
            "BIC-selected multivariate least-squares segmentation of standardized "
            "daily delivery variables; descriptive epoch detection, not causal attribution."
        ),
    }


def selector_log_coverage(
    logs_dir: Path,
    sentence_outcomes: pd.DataFrame,
) -> dict:
    selected = []
    starts = []
    log_paths = sorted(
        Path(path)
        for path in glob.glob(str(logs_dir / "interactions_*.jsonl"))
    )
    manifest = hashlib.sha256()
    for path in log_paths:
        manifest.update(path.name.encode())
        manifest.update(core.sha256_file(path).encode())
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                timestamp = pd.to_datetime(
                    item.get("ts"), utc=True, errors="coerce"
                )
                if pd.isna(timestamp) or timestamp > core.CUTOFF:
                    continue
                if item.get("event") == "sentence_selected":
                    selected.append(item)
                elif item.get("event") == "session_start":
                    starts.append(item)
    selected_keys = {
        (item.get("session_id"), item.get("sentence_id"))
        for item in selected
    }
    observed_keys = set(
        zip(
            sentence_outcomes["session_id"],
            sentence_outcomes["sentence_id"].astype(int),
        )
    )
    joined = selected_keys & observed_keys
    return {
        "selected_events": len(selected),
        "selected_sessions": len(
            {item.get("session_id") for item in selected}
        ),
        "session_starts": len(starts),
        "joined_selected_pairs": len(joined),
        "observed_presentation_pairs": len(observed_keys),
        "observed_coverage_pct": round(
            100 * len(joined) / len(observed_keys), 1
        ),
        "has_candidate_sets": False,
        "has_selection_probabilities": False,
        "input_jsonl_files": len(log_paths),
        "input_manifest_sha256": manifest.hexdigest(),
        "cutoff": core.CUTOFF.isoformat(),
        "implication": (
            "Useful for a partial historical selector-coverage audit, "
            "insufficient for historical off-policy evaluation."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--logs-dir", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--bootstrap-iterations", type=int, default=200)
    args = parser.parse_args()

    before = core.sha256_file(args.db)
    reviews, sentence_outcomes, lemmas, loaded, extra = build_event_data(args.db)
    canonicals = core.canonical_map(lemmas)
    delayed = sentence_outcomes[
        (sentence_outcomes["gap_any_d"] >= 3)
        & (sentence_outcomes["prior_n"] >= 3)
        & sentence_outcomes["has_surface"]
    ].copy()

    intro = intro_trial_analysis(
        reviews, sentence_outcomes, extra["knowledge"]
    )
    prediction, predicted_frame = predictive_bakeoff(sentence_outcomes)
    surface = spacing_surface(sentence_outcomes)
    aipw = aipw_estimate(
        delayed, iterations=args.bootstrap_iterations
    )
    recovery = recovery_analysis(sentence_outcomes)
    session = session_adjusted_analysis(predicted_frame)
    load = sentence_load_analysis(sentence_outcomes)
    morphology = morphology_analysis(
        sentence_outcomes,
        extra["confusions"],
        lemmas,
        canonicals,
    )
    changes = change_point_analysis(sentence_outcomes)
    selector = selector_log_coverage(args.logs_dir, sentence_outcomes)

    result = {
        "provenance": {
            "database_sha256": before,
            "script_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
            "cutoff": core.CUTOFF.isoformat(),
        },
        "event_scope": {
            "sentence_word_outcomes": len(sentence_outcomes),
            "words": int(sentence_outcomes["lemma_id"].nunique()),
            "presentations": int(
                sentence_outcomes["presentation_id"].nunique()
            ),
            "history_definition": (
                "Only earlier displayed reading-sentence word outcomes advance "
                "exposure count, context, form, success, or gap clocks."
            ),
        },
        "intro_trial": intro,
        "predictive_bakeoff": prediction,
        "spacing_surface": surface,
        "spacing_aipw_sensitivity": aipw,
        "failure_recovery": recovery,
        "session_adjusted": session,
        "sentence_load": load,
        "morphology": morphology,
        "change_points": changes,
        "selector_log_coverage": selector,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_bytes(stable_json_bytes(result))
    after = core.sha256_file(args.db)
    if after != before:
        raise RuntimeError("database bytes changed during read-only analysis")
    print(
        json.dumps(
            {
                "output": str(args.output_json),
                "sentence_word_outcomes": len(sentence_outcomes),
                "intro_trial_n": intro["n"],
                "prediction_folds": len(prediction["folds"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
