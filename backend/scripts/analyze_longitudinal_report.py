#!/usr/bin/env python3
"""Build the self-contained longitudinal Alif learning report.

The input must be a pinned SQLite online backup. The database is opened with
``mode=ro&immutable=1`` and never mutated. The report intentionally emphasizes
only effects that are substantively interesting and survive basic robustness
checks.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression


SCRIPT_PATH = Path(__file__).resolve()
BACKEND_DIR = SCRIPT_PATH.parents[1]
REPO_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.sentence_validator import is_function_word_lemma  # noqa: E402


BREAK_PRE_END = pd.Timestamp("2026-06-21T00:00:00Z")
BREAK_RETURN = pd.Timestamp("2026-07-05T00:00:00Z")
CUTOFF = pd.Timestamp("2026-07-30T13:58:00Z")
INERT_CATEGORIES = {"proper_name", "onomatopoeia"}


def open_immutable(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if not total:
        return (math.nan, math.nan)
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return center - half, center + half


def pct(value: float | None, digits: int = 1) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(100 * float(value), digits)


def rounded(value: float | None, digits: int = 3) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def canonical_map(lemmas: pd.DataFrame) -> dict[int, int]:
    parent = dict(zip(lemmas["lemma_id"], lemmas["canonical_lemma_id"]))
    result: dict[int, int] = {}
    for raw in parent:
        current = int(raw)
        visited: set[int] = set()
        while current not in visited and pd.notna(parent.get(current)):
            visited.add(current)
            current = int(parent[current])
        result[int(raw)] = current
    return result


def valid_lemma_ids(lemmas: pd.DataFrame) -> set[int]:
    valid: set[int] = set()
    for row in lemmas.itertuples():
        override = (
            None if pd.isna(row.function_word_override)
            else bool(row.function_word_override)
        )
        if row.word_category in INERT_CATEGORIES:
            continue
        if is_function_word_lemma(row.lemma_ar_bare, override):
            continue
        valid.add(int(row.lemma_id))
    return valid


def load_data(connection: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    lemmas = pd.read_sql_query(
        """
        SELECT lemma_id, lemma_ar, lemma_ar_bare, gloss_en, canonical_lemma_id,
               word_category, function_word_override, pos, root_id, wazn,
               frequency_rank, forms_json
        FROM lemmas
        """,
        connection,
    )
    reviews = pd.read_sql_query(
        """
        SELECT id, lemma_id, rating, reviewed_at, response_ms, session_id,
               fsrs_log_json, review_mode, sentence_id, credit_type,
               is_acquisition, was_confused, client_review_id
        FROM review_log
        ORDER BY reviewed_at, id
        """,
        connection,
    )
    sentence_words = pd.read_sql_query(
        """
        SELECT sentence_id, lemma_id, surface_form, position
        FROM sentence_words
        WHERE lemma_id IS NOT NULL
        """,
        connection,
    )
    sentences = pd.read_sql_query(
        """
        SELECT id AS sentence_id, source, arabic_text
        FROM sentences
        """,
        connection,
    )
    knowledge = pd.read_sql_query(
        """
        SELECT lemma_id, introduced_at, acquisition_started_at,
               entered_acquiring_at, graduated_at, leech_suspended_at,
               knowledge_state, source, acquisition_box
        FROM user_lemma_knowledge
        """,
        connection,
    )
    evidence = pd.read_sql_query(
        """
        SELECT w.*, r.credit_type, r.is_acquisition, r.was_confused
        FROM word_review_evidence w
        LEFT JOIN review_log r ON r.id = w.review_log_id
        """,
        connection,
    )
    return {
        "lemmas": lemmas,
        "reviews": reviews,
        "sentence_words": sentence_words,
        "sentences": sentences,
        "knowledge": knowledge,
        "evidence": evidence,
    }


def add_presentation_metadata(reviews: pd.DataFrame) -> pd.DataFrame:
    """Recover the sentence-presentation unit shared by its word outcomes.

    Modern client IDs are ``<presentation UUID>:<lemma_id>``. The first 426
    sentence-word rows predate those IDs, but their rows were inserted within
    milliseconds of one another; they are grouped when session and sentence
    match and the inter-row gap is at most one second.
    """
    frame = reviews.sort_values(["reviewed_at", "id"]).copy()
    modern = frame["client_review_id"].notna()
    frame["presentation_id"] = pd.Series(index=frame.index, dtype="object")
    frame.loc[modern, "presentation_id"] = (
        frame.loc[modern, "client_review_id"].astype(str).str.rsplit(":", n=1).str[0]
    )
    previous_key: tuple[str, int] | None = None
    previous_time = pd.NaT
    legacy_group = 0
    for row in frame.loc[~modern].itertuples():
        key = (
            str(row.session_id),
            int(row.sentence_id) if pd.notna(row.sentence_id) else -1,
        )
        separated = (
            previous_key != key
            or pd.isna(previous_time)
            or (row.reviewed_at - previous_time).total_seconds() > 1
        )
        if separated:
            legacy_group += 1
        frame.at[row.Index, "presentation_id"] = f"legacy:{legacy_group}"
        previous_key = key
        previous_time = row.reviewed_at

    sentence_rows = frame[
        (frame["review_mode"] == "reading") & frame["sentence_id"].notna()
    ]
    presentations = (
        sentence_rows.groupby("presentation_id")
        .agg(
            presentation_at=("reviewed_at", "min"),
            presentation_response_ms=("response_ms", "max"),
            presentation_word_count=("id", "size"),
            presentation_session=("session_id", "first"),
        )
        .reset_index()
        .sort_values(["presentation_session", "presentation_at", "presentation_id"])
    )
    presentations["presentation_position"] = (
        presentations.groupby("presentation_session").cumcount() + 1
    )
    presentations["session_presentation_n"] = presentations.groupby(
        "presentation_session"
    )["presentation_id"].transform("size")
    return frame.merge(presentations, on="presentation_id", how="left")


def add_review_history(
    reviews: pd.DataFrame,
    sentence_surface_map: dict[tuple[int, int], tuple[str, ...]],
) -> pd.DataFrame:
    rows: list[tuple] = []
    for lemma_id, group in reviews.groupby("lemma_id", sort=False):
        days: set = set()
        sessions: set = set()
        contexts: set = set()
        forms: set[str] = set()
        successes = 0
        prior_n = 0
        previous_any = pd.NaT
        for row in group.itertuples():
            is_sentence_reading = (
                row.review_mode == "reading" and pd.notna(row.sentence_id)
            )
            surfaces = (
                set(
                    sentence_surface_map.get(
                        (int(row.sentence_id), int(lemma_id)), ()
                    )
                )
                if is_sentence_reading
                else set()
            )
            rows.append(
                (
                    row.id,
                    prior_n,
                    len(days),
                    len(sessions),
                    len(contexts),
                    len(forms),
                    successes,
                    previous_any,
                    bool(
                        is_sentence_reading
                        and int(row.sentence_id) in contexts
                    ),
                    bool(surfaces) and surfaces.issubset(forms),
                    bool(surfaces),
                )
            )
            if not is_sentence_reading:
                continue
            prior_n += 1
            days.add(row.day)
            if pd.notna(row.session_id):
                sessions.add(row.session_id)
            if pd.notna(row.sentence_id):
                contexts.add(int(row.sentence_id))
            forms.update(surfaces)
            successes += int(row.clean)
            previous_any = row.reviewed_at
    history = pd.DataFrame(
        rows,
        columns=[
            "id",
            "prior_n",
            "prior_days",
            "prior_sessions",
            "prior_contexts",
            "prior_forms",
            "prior_successes",
            "prev_any",
            "same_sentence",
            "seen_surface",
            "has_surface",
        ],
    )
    result = reviews.merge(history, on="id", how="left")
    result["gap_any_d"] = (
        result["reviewed_at"] - result["prev_any"]
    ).dt.total_seconds() / 86400
    result["prior_acc"] = (
        result["prior_successes"] / result["prior_n"].replace(0, np.nan)
    )
    result["day_density"] = (
        result["prior_days"] / result["prior_n"].replace(0, np.nan)
    )
    result["context_density"] = (
        result["prior_contexts"] / result["prior_n"].replace(0, np.nan)
    )
    result["novel_form"] = (~result["seen_surface"]) & result["has_surface"]
    return result


def summarize_gap_curve(outcomes: pd.DataFrame) -> list[dict]:
    bins = [-1, 1 / 24, 1, 3, 7, 14, 30, 10_000]
    labels = ["<1 hour", "1h–1d", "1–3d", "3–7d", "7–14d", "14–30d", "30d+"]
    frame = outcomes.dropna(subset=["gap_any_d"]).copy()
    frame["band"] = pd.cut(
        frame["gap_any_d"], bins=bins, labels=labels, include_lowest=True
    )
    result: list[dict] = []
    for band, group in frame.groupby("band", observed=True):
        successes = int(group["clean"].sum())
        total = len(group)
        low, high = wilson(successes, total)
        result.append(
            {
                "band": str(band),
                "n": total,
                "recall": pct(successes / total),
                "low": pct(low),
                "high": pct(high),
                "median_gap": rounded(group["gap_any_d"].median(), 2),
            }
        )
    return result


def adjusted_panel_model(
    tests: pd.DataFrame,
    bootstrap_iterations: int = 200,
) -> tuple[list[dict], pd.DataFrame, dict]:
    frame = tests.copy()
    frame["pos2"] = (
        frame["pos"].fillna("other").replace(
            {"verb_pseudo": "verb", "adjective": "adj"}
        )
    )
    frame.loc[~frame["pos2"].isin(["noun", "verb", "adj"]), "pos2"] = "other"
    frame["month"] = frame["reviewed_at"].dt.strftime("%Y-%m")
    frame["log_gap"] = np.log1p(frame["gap_any_d"])
    frame["log_n"] = np.log1p(frame["prior_n"])
    median_frequency = frame["frequency_rank"].median()
    frame["log_freq"] = np.log1p(
        frame["frequency_rank"].fillna(median_frequency)
    )
    frame["freq_missing"] = frame["frequency_rank"].isna().astype(int)
    frame["is_primary"] = (frame["credit_type"] == "primary").astype(int)
    frame["scheduled_acquisition"] = frame["is_acquisition"].astype(int)
    frame["log_sentence_words"] = np.log1p(frame["presentation_word_count"])
    numeric = [
        "log_gap",
        "log_n",
        "prior_acc",
        "day_density",
        "context_density",
        "novel_form",
        "same_sentence",
        "log_freq",
        "freq_missing",
        "is_primary",
        "scheduled_acquisition",
        "log_sentence_words",
    ]
    design = pd.concat(
        [
            frame[numeric].astype(float),
            pd.get_dummies(frame[["pos2", "month"]], dtype=float),
        ],
        axis=1,
    )
    standardize = [
        "log_gap",
        "log_n",
        "prior_acc",
        "day_density",
        "context_density",
        "log_freq",
        "log_sentence_words",
    ]
    means = design.mean()
    standard_deviations = design.std().replace(0, 1)
    for column in standardize:
        design[column] = (
            design[column] - means[column]
        ) / standard_deviations[column]
    outcome = frame["clean"].astype(int).to_numpy()
    model = LogisticRegression(C=2, max_iter=3000)
    model.fit(design, outcome)

    def counterfactual(
        fitted: LogisticRegression,
        *,
        numeric_change: tuple[str, float] | None = None,
        binary_change: tuple[str, int] | None = None,
        pos_change: str | None = None,
    ) -> float:
        altered = design.copy()
        if numeric_change:
            column, value = numeric_change
            altered[column] = (
                value - means[column]
            ) / standard_deviations[column]
        if binary_change:
            column, value = binary_change
            altered[column] = value
        if pos_change:
            for column in [item for item in altered if item.startswith("pos2_")]:
                altered[column] = 0
            altered[f"pos2_{pos_change}"] = 1
        return float(fitted.predict_proba(altered)[:, 1].mean())

    day_quartiles = frame["day_density"].quantile([0.25, 0.75])
    context_quartiles = frame["context_density"].quantile([0.25, 0.75])
    point_effects = {
        "distributed_days": (
            counterfactual(
                model,
                numeric_change=("day_density", float(day_quartiles.iloc[1])),
            )
            - counterfactual(
                model,
                numeric_change=("day_density", float(day_quartiles.iloc[0])),
            )
        ),
        "context_diversity": (
            counterfactual(
                model,
                numeric_change=(
                    "context_density",
                    float(context_quartiles.iloc[1]),
                ),
            )
            - counterfactual(
                model,
                numeric_change=(
                    "context_density",
                    float(context_quartiles.iloc[0]),
                ),
            )
        ),
        "novel_form": (
            counterfactual(model, binary_change=("novel_form", 1))
            - counterfactual(model, binary_change=("novel_form", 0))
        ),
        "verb_vs_noun": (
            counterfactual(model, pos_change="verb")
            - counterfactual(model, pos_change="noun")
        ),
    }

    rng = np.random.default_rng(42)
    lemma_ids = frame["lemma_id"].unique()
    bootstraps: list[dict[str, float]] = []
    for _ in range(bootstrap_iterations):
        sampled = rng.choice(lemma_ids, len(lemma_ids), replace=True)
        counts = pd.Series(sampled).value_counts()
        weights = frame["lemma_id"].map(counts).fillna(0).to_numpy()
        fitted = LogisticRegression(C=2, max_iter=2000)
        fitted.fit(design, outcome, sample_weight=weights)
        bootstraps.append(
            {
                "distributed_days": (
                    counterfactual(
                        fitted,
                        numeric_change=(
                            "day_density",
                            float(day_quartiles.iloc[1]),
                        ),
                    )
                    - counterfactual(
                        fitted,
                        numeric_change=(
                            "day_density",
                            float(day_quartiles.iloc[0]),
                        ),
                    )
                ),
                "context_diversity": (
                    counterfactual(
                        fitted,
                        numeric_change=(
                            "context_density",
                            float(context_quartiles.iloc[1]),
                        ),
                    )
                    - counterfactual(
                        fitted,
                        numeric_change=(
                            "context_density",
                            float(context_quartiles.iloc[0]),
                        ),
                    )
                ),
                "novel_form": (
                    counterfactual(
                        fitted, binary_change=("novel_form", 1)
                    )
                    - counterfactual(
                        fitted, binary_change=("novel_form", 0)
                    )
                ),
                "verb_vs_noun": (
                    counterfactual(fitted, pos_change="verb")
                    - counterfactual(fitted, pos_change="noun")
                ),
            }
        )
    bootstrap_frame = pd.DataFrame(bootstraps)
    session_rng = np.random.default_rng(314)
    session_keys = frame["session_id"].fillna(
        frame["id"].map(lambda value: f"missing-session:{value}")
    )
    unique_sessions = session_keys.unique()
    session_bootstraps: list[dict[str, float]] = []
    session_iterations = min(80, bootstrap_iterations)
    for _ in range(session_iterations):
        sampled_sessions = session_rng.choice(
            unique_sessions,
            len(unique_sessions),
            replace=True,
        )
        session_counts = pd.Series(sampled_sessions).value_counts()
        weights = session_keys.map(session_counts).fillna(0).to_numpy()
        fitted = LogisticRegression(C=2, max_iter=2000)
        fitted.fit(design, outcome, sample_weight=weights)
        session_bootstraps.append(
            {
                "distributed_days": (
                    counterfactual(
                        fitted,
                        numeric_change=(
                            "day_density",
                            float(day_quartiles.iloc[1]),
                        ),
                    )
                    - counterfactual(
                        fitted,
                        numeric_change=(
                            "day_density",
                            float(day_quartiles.iloc[0]),
                        ),
                    )
                ),
                "context_diversity": (
                    counterfactual(
                        fitted,
                        numeric_change=(
                            "context_density",
                            float(context_quartiles.iloc[1]),
                        ),
                    )
                    - counterfactual(
                        fitted,
                        numeric_change=(
                            "context_density",
                            float(context_quartiles.iloc[0]),
                        ),
                    )
                ),
                "novel_form": (
                    counterfactual(
                        fitted, binary_change=("novel_form", 1)
                    )
                    - counterfactual(
                        fitted, binary_change=("novel_form", 0)
                    )
                ),
                "verb_vs_noun": (
                    counterfactual(fitted, pos_change="verb")
                    - counterfactual(fitted, pos_change="noun")
                ),
            }
        )
    session_bootstrap_frame = pd.DataFrame(session_bootstraps)
    labels = {
        "distributed_days": "More distributed across days",
        "context_diversity": "More distinct sentence contexts",
        "novel_form": "New surface form at test",
        "verb_vs_noun": "Verb rather than noun",
    }
    effects: list[dict] = []
    for key in point_effects:
        effects.append(
            {
                "key": key,
                "label": labels[key],
                "effect": pct(point_effects[key]),
                "low": pct(bootstrap_frame[key].quantile(0.025)),
                "high": pct(bootstrap_frame[key].quantile(0.975)),
                "session_low": pct(
                    session_bootstrap_frame[key].quantile(0.025)
                ),
                "session_high": pct(
                    session_bootstrap_frame[key].quantile(0.975)
                ),
            }
        )
    metadata = {
        "tests": len(frame),
        "words": int(frame["lemma_id"].nunique()),
        "day_density_q25": rounded(day_quartiles.iloc[0]),
        "day_density_q75": rounded(day_quartiles.iloc[1]),
        "context_density_q25": rounded(context_quartiles.iloc[0]),
        "context_density_q75": rounded(context_quartiles.iloc[1]),
        "bootstrap_iterations": bootstrap_iterations,
        "session_bootstrap_iterations": session_iterations,
    }
    word_dummies = pd.get_dummies(
        frame["lemma_id"], prefix="word", dtype=float
    )
    fixed_design = pd.concat([design, word_dummies], axis=1)
    sensitivity: dict[str, list[float]] = defaultdict(list)
    for regularization in [0.2, 1, 2, 10]:
        fixed_model = LogisticRegression(
            C=regularization,
            max_iter=4000,
        )
        fixed_model.fit(fixed_design, outcome)

        def fixed_prediction(column: str, value: float) -> float:
            altered = fixed_design.copy()
            altered[column] = (
                (value - means[column]) / standard_deviations[column]
                if column in standardize
                else value
            )
            return float(fixed_model.predict_proba(altered)[:, 1].mean())

        sensitivity["distributed_days"].append(
            fixed_prediction("day_density", float(day_quartiles.iloc[1]))
            - fixed_prediction("day_density", float(day_quartiles.iloc[0]))
        )
        sensitivity["context_diversity"].append(
            fixed_prediction(
                "context_density", float(context_quartiles.iloc[1])
            )
            - fixed_prediction(
                "context_density", float(context_quartiles.iloc[0])
            )
        )
        sensitivity["novel_form"].append(
            fixed_prediction("novel_form", 1)
            - fixed_prediction("novel_form", 0)
        )
    metadata["word_intercept_sensitivity"] = {
        key: {
            "low": pct(min(values)),
            "high": pct(max(values)),
        }
        for key, values in sensitivity.items()
    }
    return effects, frame, metadata


def spacing_strata(tests: pd.DataFrame) -> list[dict]:
    ranges = [
        (3, 5, "3–5 prior exposures"),
        (6, 8, "6–8 prior exposures"),
        (9, 12, "9–12 prior exposures"),
        (13, 20, "13–20 prior exposures"),
    ]
    output: list[dict] = []
    for lower, upper, label in ranges:
        group = tests[tests["prior_n"].between(lower, upper)].copy()
        if group.empty:
            continue
        group["spread"] = pd.qcut(
            group["day_density"],
            3,
            labels=["less distributed", "middle", "more distributed"],
            duplicates="drop",
        )
        for spread, cells in group.groupby("spread", observed=True):
            output.append(
                {
                    "exposure": label,
                    "spread": str(spread),
                    "n": len(cells),
                    "recall": pct(cells["clean"].mean()),
                    "median_days": rounded(cells["prior_days"].median(), 1),
                    "median_gap": rounded(cells["gap_any_d"].median(), 1),
                }
            )
    return output


def context_transfer(tests: pd.DataFrame) -> list[dict]:
    frame = tests.copy()
    frame["context_type"] = np.select(
        [
            frame["same_sentence"] & frame["seen_surface"],
            (~frame["same_sentence"]) & frame["seen_surface"],
            frame["same_sentence"] & (~frame["seen_surface"]),
        ],
        [
            "Same sentence, seen form",
            "New sentence, seen form",
            "Same sentence, new form",
        ],
        default="New sentence, new form",
    )
    order = [
        "Same sentence, seen form",
        "New sentence, seen form",
        "New sentence, new form",
        "Same sentence, new form",
    ]
    output: list[dict] = []
    for label in order:
        group = frame[frame["context_type"] == label]
        if len(group) < 10:
            continue
        output.append(
            {
                "type": label,
                "n": len(group),
                "recall": pct(group["clean"].mean()),
                "median_gap": rounded(group["gap_any_d"].median(), 1),
            }
        )
    return output


def break_model(
    reviews: pd.DataFrame,
    lemmas: pd.DataFrame,
    bootstrap_iterations: int = 200,
) -> tuple[dict, list[dict]]:
    frame = reviews[
        (reviews["review_mode"] == "reading")
        & reviews["sentence_id"].notna()
        & reviews["gap_any_d"].between(14, 120)
        & (reviews["prior_n"] >= 3)
    ].copy()
    frame["break_candidate"] = (
        (frame["reviewed_at"] >= BREAK_RETURN)
        & (frame["prev_any"] < BREAK_PRE_END)
    )
    first_break_ids = (
        frame[frame["break_candidate"]]
        .sort_values("reviewed_at")
        .groupby("lemma_id")
        .head(1)["id"]
    )
    frame["break_flag"] = frame["id"].isin(first_break_ids)
    frame = frame[frame["break_flag"] | (~frame["break_candidate"])].copy()
    frame = frame.merge(
        lemmas[["lemma_id", "pos", "frequency_rank"]],
        on="lemma_id",
        how="left",
    )
    frame["pos2"] = (
        frame["pos"].fillna("other").replace(
            {"verb_pseudo": "verb", "adjective": "adj"}
        )
    )
    frame.loc[~frame["pos2"].isin(["noun", "verb", "adj"]), "pos2"] = "other"
    frame["log_gap"] = np.log1p(frame["gap_any_d"])
    frame["log_n"] = np.log1p(frame["prior_n"])
    median_frequency = frame["frequency_rank"].median()
    frame["log_freq"] = np.log1p(
        frame["frequency_rank"].fillna(median_frequency)
    )
    frame["is_primary"] = (frame["credit_type"] == "primary").astype(int)
    frame["scheduled_acquisition"] = frame["is_acquisition"].astype(int)
    frame["log_sentence_words"] = np.log1p(frame["presentation_word_count"])
    numeric = [
        "log_gap",
        "log_n",
        "prior_acc",
        "day_density",
        "log_freq",
        "break_flag",
        "is_primary",
        "scheduled_acquisition",
        "log_sentence_words",
    ]
    design = pd.concat(
        [
            frame[numeric].astype(float),
            pd.get_dummies(frame["pos2"], prefix="pos", dtype=float),
        ],
        axis=1,
    )
    standardize = [
        "log_gap",
        "log_n",
        "prior_acc",
        "day_density",
        "log_freq",
        "log_sentence_words",
    ]
    means = design.mean()
    standard_deviations = design.std().replace(0, 1)
    for column in standardize:
        design[column] = (
            design[column] - means[column]
        ) / standard_deviations[column]
    outcome = frame["clean"].astype(int).to_numpy()
    model = LogisticRegression(C=2, max_iter=2500)
    model.fit(design, outcome)
    no_break = design.copy()
    break_condition = design.copy()
    no_break["break_flag"] = 0
    break_condition["break_flag"] = 1
    point = (
        model.predict_proba(break_condition)[:, 1].mean()
        - model.predict_proba(no_break)[:, 1].mean()
    )
    rng = np.random.default_rng(7)
    lemma_ids = frame["lemma_id"].unique()
    values: list[float] = []
    for _ in range(bootstrap_iterations):
        sampled = rng.choice(lemma_ids, len(lemma_ids), replace=True)
        counts = pd.Series(sampled).value_counts()
        weights = frame["lemma_id"].map(counts).fillna(0).to_numpy()
        fitted = LogisticRegression(C=2, max_iter=1500)
        fitted.fit(design, outcome, sample_weight=weights)
        values.append(
            float(
                fitted.predict_proba(break_condition)[:, 1].mean()
                - fitted.predict_proba(no_break)[:, 1].mean()
            )
        )
    effect = {
        "label": "First retrieval spanning the vacation",
        "effect": pct(point),
        "low": pct(np.quantile(values, 0.025)),
        "high": pct(np.quantile(values, 0.975)),
        "break_n": int(frame["break_flag"].sum()),
        "control_n": int((~frame["break_flag"]).sum()),
        "raw_break": pct(frame.loc[frame["break_flag"], "clean"].mean()),
        "raw_control": pct(frame.loc[~frame["break_flag"], "clean"].mean()),
    }
    frame["band"] = pd.cut(
        frame["gap_any_d"],
        [14, 21, 30, 60, 120],
        labels=["14–21d", "21–30d", "30–60d", "60–120d"],
    )
    cells: list[dict] = []
    for (break_flag, band), group in frame.groupby(
        ["break_flag", "band"], observed=True
    ):
        cells.append(
            {
                "series": "Vacation-spanning" if break_flag else "Ordinary gap",
                "band": str(band),
                "n": len(group),
                "recall": pct(group["clean"].mean()),
                "median_gap": rounded(group["gap_any_d"].median(), 1),
            }
        )
    return effect, cells


def break_resilience(reviews: pd.DataFrame) -> list[dict]:
    frame = reviews[
        (reviews["review_mode"] == "reading")
        & reviews["sentence_id"].notna()
    ].copy()
    rows: list[dict] = []
    for lemma_id, group in frame.groupby("lemma_id"):
        before = group[group["reviewed_at"] < BREAK_PRE_END]
        after = group[group["reviewed_at"] >= BREAK_RETURN]
        if before.empty or after.empty:
            continue
        outcome = after.iloc[0]
        gap = (
            outcome["reviewed_at"] - before["reviewed_at"].max()
        ).total_seconds() / 86400
        if gap < 14:
            continue
        rows.append(
            {
                "lemma_id": int(lemma_id),
                "clean": bool(outcome["clean"]),
                "gap": gap,
                "prior_n": len(before),
                "prior_days": int(before["day"].nunique()),
                "prior_acc": float(before["clean"].mean()),
            }
        )
    cohort = pd.DataFrame(rows)
    cohort["exposure_band"] = pd.qcut(
        cohort["prior_n"],
        3,
        labels=["fewer", "middle", "more"],
        duplicates="drop",
    )
    output: list[dict] = []
    for band, group in cohort.groupby("exposure_band", observed=True):
        output.append(
            {
                "band": str(band),
                "n": len(group),
                "recall": pct(group["clean"].mean()),
                "median_exposures": rounded(group["prior_n"].median(), 1),
                "median_days": rounded(group["prior_days"].median(), 1),
                "median_gap": rounded(group["gap"].median(), 1),
            }
        )
    return output


def session_dynamics(outcomes: pd.DataFrame) -> list[dict]:
    frame = outcomes[
        outcomes["session_id"].notna()
        & (outcomes["session_presentation_n"] >= 15)
    ].copy()
    session_count = int(frame["session_id"].nunique())
    frame["band"] = pd.cut(
        frame["presentation_position"],
        [0, 3, 7, 12, 20, 10_000],
        labels=["1–3", "4–7", "8–12", "13–20", "21+"],
    )
    output: list[dict] = []
    for band, group in frame.groupby("band", observed=True):
        presentations = group.drop_duplicates("presentation_id")
        active_time = presentations.loc[
            presentations["presentation_response_ms"].between(1000, 180000),
            "presentation_response_ms",
        ]
        output.append(
            {
                "band": str(band),
                "n": len(group),
                "presentations": int(group["presentation_id"].nunique()),
                "recall": pct(group["clean"].mean()),
                "median_seconds": rounded(active_time.median() / 1000, 1),
                "sessions": session_count,
            }
        )
    return output


def learning_rate_summary(outcomes: pd.DataFrame) -> dict:
    """Outcome-based throughput benchmarks without using current state labels.

    A word is counted at a gap threshold only when it has actually received a
    reading-sentence judgment after at least that much time since its preceding
    reading-sentence appearance.  The latest qualifying judgment is used so an
    early success cannot permanently classify a word as retained.
    """

    frame = outcomes.sort_values("reviewed_at").copy()
    presentations = frame.drop_duplicates("presentation_id").copy()
    timed = presentations["presentation_response_ms"].between(1000, 180000)
    timed_ms = presentations.loc[timed, "presentation_response_ms"]
    median_ms = float(timed_ms.median())
    measured_hours = float(timed_ms.sum() / 3_600_000)
    imputed_hours = float(
        (
            timed_ms.sum()
            + (~timed).sum() * median_ms
        )
        / 3_600_000
    )
    word_n = int(frame["lemma_id"].nunique())
    active_days = int(frame["day"].nunique())
    calendar_days = int(
        (
            frame["reviewed_at"].max().normalize()
            - frame["reviewed_at"].min().normalize()
        ).days
        + 1
    )
    first_seen = frame.groupby("lemma_id")["reviewed_at"].min()

    thresholds: list[dict] = []
    attainment: list[dict] = []
    for gap_days in [1, 3, 7, 14, 30]:
        qualifying = frame[frame["gap_any_d"] >= gap_days].copy()
        latest = (
            qualifying.sort_values("reviewed_at")
            .groupby("lemma_id")
            .tail(1)
        )
        latest_clean = int(latest["clean"].sum())
        tested_words = int(latest["lemma_id"].nunique())
        thresholds.append(
            {
                "gap_days": gap_days,
                "tested_words": tested_words,
                "latest_clean_words": latest_clean,
                "latest_clean_pct": pct(latest["clean"].mean()),
                "lower_bound_all_seen_pct": pct(latest_clean / word_n),
                "words_per_active_day": rounded(
                    latest_clean / active_days, 2
                ),
                "words_per_timed_minute": rounded(
                    latest_clean / (measured_hours * 60), 3
                ),
                "words_per_imputed_minute": rounded(
                    latest_clean / (imputed_hours * 60), 3
                ),
            }
        )

        first_clean = (
            qualifying[qualifying["clean"]]
            .sort_values("reviewed_at")
            .groupby("lemma_id")
            .head(1)
        )
        elapsed = (
            first_clean["reviewed_at"]
            - first_clean["lemma_id"].map(first_seen)
        ).dt.total_seconds() / 86400
        attainment.append(
            {
                "gap_days": gap_days,
                "words": len(first_clean),
                "median_prior_exposures": rounded(
                    first_clean["prior_n"].median(), 1
                ),
                "median_elapsed_days": rounded(elapsed.median(), 1),
                "p25_elapsed_days": rounded(elapsed.quantile(0.25), 1),
                "p75_elapsed_days": rounded(elapsed.quantile(0.75), 1),
            }
        )

    return {
        "definition": (
            "Latest clean sentence-word judgment after the stated actual gap; "
            "not a current knowledge-state label or productive-vocabulary test."
        ),
        "words_seen": word_n,
        "active_days": active_days,
        "calendar_days": calendar_days,
        "new_words_seen_per_active_day": rounded(word_n / active_days, 2),
        "new_words_seen_per_calendar_day": rounded(
            word_n / calendar_days, 2
        ),
        "presentations": len(presentations),
        "timed_presentations": int(timed.sum()),
        "timing_coverage_pct": pct(timed.mean()),
        "median_presentation_seconds": rounded(median_ms / 1000, 2),
        "measured_presentation_hours": rounded(measured_hours, 2),
        "median_imputed_presentation_hours": rounded(imputed_hours, 2),
        "thresholds": thresholds,
        "first_attainment": attainment,
    }


def cohort_analysis(
    knowledge: pd.DataFrame,
    reviews: pd.DataFrame,
) -> tuple[list[dict], list[dict]]:
    frame = knowledge.copy()
    for column in [
        "introduced_at",
        "acquisition_started_at",
        "entered_acquiring_at",
        "graduated_at",
        "leech_suspended_at",
    ]:
        frame[column] = pd.to_datetime(frame[column], utc=True)
    frame["start"] = (
        frame["entered_acquiring_at"]
        .fillna(frame["acquisition_started_at"])
        .fillna(frame["introduced_at"])
    )
    frame = frame.dropna(subset=["start"]).copy()
    frame["day"] = frame["start"].dt.date
    review_times: dict[int, list[pd.Timestamp]] = {
        int(lemma_id): group["reviewed_at"].sort_values().tolist()
        for lemma_id, group in reviews.groupby("lemma_id")
    }
    first_reviews: list[pd.Timestamp] = []
    for row in frame.itertuples():
        times = review_times.get(int(row.lemma_id), [])
        if not times:
            first_reviews.append(pd.NaT)
            continue
        eligible = [review_time for review_time in times if review_time >= row.start]
        first_reviews.append(eligible[0] if eligible else pd.NaT)
    frame["first_review"] = first_reviews
    frame["first_review_hours"] = (
        frame["first_review"] - frame["start"]
    ).dt.total_seconds() / 3600
    frame["durable"] = frame["knowledge_state"].isin(
        ["known", "learning", "lapsed"]
    )
    frame["suspended"] = frame["knowledge_state"] == "suspended"
    cohorts = (
        frame.groupby("day")
        .agg(
            n=("lemma_id", "size"),
            median_first_hours=("first_review_hours", "median"),
            p90_first_hours=("first_review_hours", lambda values: values.quantile(0.9)),
            reviewed=("first_review", "count"),
            durable=("durable", "mean"),
            suspended=("suspended", "mean"),
        )
        .reset_index()
    )
    cohorts["age_days"] = (
        CUTOFF.tz_convert(None).date() - cohorts["day"]
    ).apply(lambda value: value.days)
    chart_rows: list[dict] = []
    for row in cohorts[cohorts["n"] >= 10].itertuples():
        chart_rows.append(
            {
                "date": str(row.day),
                "n": int(row.n),
                "median_first_hours": rounded(row.median_first_hours, 1),
                "p90_first_hours": rounded(row.p90_first_hours, 1),
                "reviewed": int(row.reviewed),
                "durable": pct(row.durable),
                "suspended": pct(row.suspended),
                "age_days": int(row.age_days),
            }
        )
    highlighted_dates = {
        "2026-02-28",
        "2026-06-03",
        "2026-07-15",
        "2026-07-21",
    }
    highlighted = [
        row for row in chart_rows if row["date"] in highlighted_dates
    ]
    return chart_rows, highlighted


def daily_timeline(reviews: pd.DataFrame) -> list[dict]:
    daily = (
        reviews.groupby(reviews["reviewed_at"].dt.date)
        .agg(
            reviews=("id", "size"),
            clean=("clean", "mean"),
            sessions=("session_id", "nunique"),
        )
        .reset_index()
    )
    sentence_outcomes = reviews[
        (reviews["review_mode"] == "reading")
        & reviews["sentence_id"].notna()
    ].copy()
    sentence_daily = (
        sentence_outcomes.groupby(sentence_outcomes["reviewed_at"].dt.date)["clean"]
        .agg(["count", "mean"])
        .rename(columns={"count": "word_n", "mean": "word_clean"})
    )
    daily = daily.merge(
        sentence_daily,
        left_on="reviewed_at",
        right_index=True,
        how="left",
    )
    return [
        {
            "date": str(row.reviewed_at),
            "reviews": int(row.reviews),
            "clean": pct(row.clean),
            "sessions": int(row.sessions),
            "word_n": int(row.word_n) if pd.notna(row.word_n) else 0,
            "word_clean": (
                pct(row.word_clean) if pd.notna(row.word_clean) else None
            ),
        }
        for row in daily.itertuples()
    ]


def evidence_summary(evidence: pd.DataFrame) -> dict:
    if evidence.empty:
        return {}
    frame = evidence.copy()
    frame["clean"] = (
        (frame["rating"] >= 3)
        & (~frame["was_confused"].fillna(0).astype(bool))
    )
    hidden = frame[~frame["front_initial_tashkeel_visible"].astype(bool)]
    visible = frame[frame["front_initial_tashkeel_visible"].astype(bool)]
    causes: Counter[str] = Counter()
    for raw in frame["failure_causes_json"].dropna():
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, list):
            causes.update(value)
    return {
        "rows": len(frame),
        "reviews": int(frame["client_review_id"].nunique()),
        "words": int(frame["canonical_lemma_id"].nunique()),
        "first": str(frame["created_at"].min()),
        "last": str(frame["created_at"].max()),
        "hidden_n": len(hidden),
        "hidden_clean": pct(hidden["clean"].mean()),
        "hidden_reveal": pct(
            hidden["front_ever_tashkeel_visible"].astype(bool).mean()
        ),
        "visible_n": len(visible),
        "visible_clean": pct(visible["clean"].mean()),
        "causes": dict(causes),
    }


def word_trajectories(
    reviews: pd.DataFrame,
    lemmas: pd.DataFrame,
) -> list[dict]:
    reading = reviews[
        (reviews["review_mode"] == "reading")
        & reviews["sentence_id"].notna()
    ].copy()
    summary = (
        reading.groupby("lemma_id")
        .agg(
            n=("clean", "size"),
            clean=("clean", "mean"),
            failures=("clean", lambda values: int((~values).sum())),
        )
        .reset_index()
        .merge(
            lemmas[["lemma_id", "lemma_ar", "gloss_en", "pos"]],
            on="lemma_id",
            how="left",
        )
    )
    hard = summary[summary["n"] >= 15].sort_values(["clean", "n"]).head(4)
    easy = summary[summary["n"] >= 30].sort_values(
        ["clean", "n"], ascending=[False, False]
    ).head(2)
    selected = pd.concat([hard, easy]).drop_duplicates("lemma_id")
    output: list[dict] = []
    start = reviews["reviewed_at"].min()
    for word in selected.itertuples():
        events = reading[reading["lemma_id"] == word.lemma_id]
        output.append(
            {
                "lemma_id": int(word.lemma_id),
                "arabic": "" if pd.isna(word.lemma_ar) else str(word.lemma_ar),
                "gloss": "" if pd.isna(word.gloss_en) else str(word.gloss_en),
                "pos": "" if pd.isna(word.pos) else str(word.pos),
                "n": int(word.n),
                "clean": pct(word.clean),
                "events": [
                    {
                        "day": rounded(
                            (row.reviewed_at - start).total_seconds() / 86400,
                            2,
                        ),
                        "rating": int(row.rating),
                        "clean": bool(row.clean),
                        "acquisition": bool(row.is_acquisition),
                    }
                    for row in events.itertuples()
                ],
            }
        )
    return output


def make_report_html(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    effects = {row["key"]: row for row in data["effects"]}
    spacing_effect = effects["distributed_days"]
    context_effect = effects["context_diversity"]
    form_effect = effects["novel_form"]
    verb_effect = effects["verb_vs_noun"]
    break_effect = data["break_effect"]
    evidence = data["evidence"]
    highlighted_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['date'])}</td>
          <td>{row['n']}</td>
          <td>{row['median_first_hours']:.1f} h</td>
          <td>{row['durable']:.1f}%</td>
          <td>{row['suspended']:.1f}%</td>
          <td>{row['age_days']} d</td>
        </tr>
        """
        for row in data["highlighted_cohorts"]
    )
    gap_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['band'])}</td>
          <td>{row['median_gap']:.2f} d</td>
          <td>{row['n']:,}</td>
          <td>{row['recall']:.1f}%</td>
          <td>{row['low']:.1f}–{row['high']:.1f}%</td>
        </tr>
        """
        for row in data["gap_curve"]
    )
    learning_rate = data["learning_rate"]
    learning_rate_rows = "".join(
        f"""
        <tr>
          <td>≥{row['gap_days']} day{'s' if row['gap_days'] != 1 else ''}</td>
          <td>{row['tested_words']:,}</td>
          <td>{row['latest_clean_words']:,}</td>
          <td>{row['latest_clean_pct']:.1f}%</td>
          <td>{row['lower_bound_all_seen_pct']:.1f}%</td>
          <td>{row['words_per_active_day']:.2f}</td>
          <td>{row['words_per_imputed_minute']:.3f}–{row['words_per_timed_minute']:.3f}</td>
        </tr>
        """
        for row in learning_rate["thresholds"]
    )
    attainment_rows = "".join(
        f"""
        <tr>
          <td>First clean test after ≥{row['gap_days']} day{'s' if row['gap_days'] != 1 else ''}</td>
          <td>{row['words']:,}</td>
          <td>{row['median_prior_exposures']:.1f}</td>
          <td>{row['median_elapsed_days']:.1f} d</td>
          <td>{row['p25_elapsed_days']:.1f}–{row['p75_elapsed_days']:.1f} d</td>
        </tr>
        """
        for row in learning_rate["first_attainment"]
        if row["gap_days"] >= 7
    )
    spacing_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['exposure'])}</td>
          <td>{html.escape(row['spread'])}</td>
          <td>{row['median_days']:.0f}</td>
          <td>{row['median_gap']:.1f} d</td>
          <td>{row['n']:,}</td>
          <td>{row['recall']:.1f}%</td>
        </tr>
        """
        for row in data["spacing_strata"]
    )
    effect_interpretations = {
        "distributed_days": "Upper versus lower quartile of prior exposures spread across distinct days.",
        "context_diversity": "Upper versus lower quartile of distinct-sentence density, holding spacing and count fixed.",
        "novel_form": "The exact surface form at test had never appeared previously for that canonical word.",
        "verb_vs_noun": "Average adjusted difference for verbs relative to nouns; not a comparison of matched meanings.",
    }
    effect_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['label'])}</td>
          <td>{row['effect']:+.1f} pp</td>
          <td>{row['low']:+.1f} to {row['high']:+.1f}</td>
          <td>{row['session_low']:+.1f} to {row['session_high']:+.1f}</td>
          <td>{html.escape(effect_interpretations[row['key']])}</td>
        </tr>
        """
        for row in data["effects"]
    )
    form_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['type'])}</td>
          <td>{row['n']:,}</td>
          <td>{row['median_gap']:.1f} d</td>
          <td>{row['recall']:.1f}%</td>
        </tr>
        """
        for row in data["context_transfer"]
    )
    break_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['band'])}</td>
          <td>{html.escape(row['series'])}</td>
          <td>{row['median_gap']:.1f} d</td>
          <td>{row['n']:,}</td>
          <td>{row['recall']:.1f}%</td>
        </tr>
        """
        for row in data["break_cells"]
    )
    session_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['band'])}</td>
          <td>{row['n']:,}</td>
          <td>{row['presentations']:,}</td>
          <td>{row['recall']:.1f}%</td>
          <td>{row['median_seconds']:.1f} s</td>
        </tr>
        """
        for row in data["session_dynamics"]
    )
    fsrs_by_group: dict[str, dict] = defaultdict(dict)
    for row in data["fsrs"]:
        fsrs_by_group[row["group"]][row["series"]] = row
    fsrs_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(group)}</td>
          <td>{values['Predicted']['n']:,}</td>
          <td>{values['Predicted']['value']:.1f}%</td>
          <td>{values['Observed']['value']:.1f}%</td>
          <td>{values['Observed']['value'] - values['Predicted']['value']:+.1f} pp</td>
          <td>{values['Observed']['median_lateness_days']:.2f} d</td>
        </tr>
        """
        for group, values in fsrs_by_group.items()
    )
    trajectory_rows = "".join(
        f"""
        <tr>
          <td class="arabic">{html.escape(word['arabic'])}</td>
          <td>{html.escape(word['gloss'])}</td>
          <td>{html.escape(word['pos'])}</td>
          <td>{word['n']:,}</td>
          <td>{sum(not event['clean'] for event in word['events']):,}</td>
          <td>{word['clean']:.1f}%</td>
        </tr>
        """
        for word in data["trajectories"]
    )
    advanced = data["advanced"]
    intro = advanced["intro_trial"]
    intro_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['label'])}</td>
          <td>{row['groups']['intro_ab_card']['recall']:.1f}% (n={row['groups']['intro_ab_card']['n']})</td>
          <td>{row['groups']['intro_ab_sentence']['recall']:.1f}% (n={row['groups']['intro_ab_sentence']['n']})</td>
          <td>{row['effect']:+.1f} pp</td>
          <td>{row['low']:+.1f} to {row['high']:+.1f}</td>
          <td>{row['holm_p']:.4f}</td>
          <td>{row['groups']['intro_ab_card']['median_intervening_exposures']:.1f} / {row['groups']['intro_ab_sentence']['median_intervening_exposures']:.1f}</td>
        </tr>
        """
        for row in intro["outcomes"]
    )
    prediction_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['subset'])}</td>
          <td>{html.escape(row['model'])}</td>
          <td>{row['n']:,}</td>
          <td>{row['brier']:.4f}</td>
          <td>{row['log_loss']:.4f}</td>
          <td>{row['auc']:.4f}</td>
          <td>{row['mean_predicted']:.1f}% / {row['observed']:.1f}%</td>
        </tr>
        """
        for row in advanced["predictive_bakeoff"]["metrics"]
    )
    common_fsrs_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['model'])}</td>
          <td>{row['brier']:.4f}</td>
          <td>{row['log_loss']:.4f}</td>
          <td>{row['auc']:.4f}</td>
          <td>{row['mean_predicted']:.1f}% / {row['observed']:.1f}%</td>
        </tr>
        """
        for row in advanced["predictive_bakeoff"]["common_fsrs_metrics"]
    )
    recovery_next_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['band'])}</td>
          <td>{row['n']:,}</td>
          <td>{row['median_delay_days']:.2f} d</td>
          <td>{row['next_clean']:.1f}%</td>
        </tr>
        """
        for row in advanced["failure_recovery"]["next_outcomes"]
    )
    recovery_stable_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['timing'])}</td>
          <td>{row['episodes']:,}</td>
          <td>{row['median_attempts']:.1f}</td>
          <td>{row['median_days_to_clean']:.2f} d</td>
          <td>{row['stable_n']:,}</td>
          <td>{'—' if row['stable_clean'] is None else f"{row['stable_clean']:.1f}%"}</td>
        </tr>
        """
        for row in advanced["failure_recovery"]["recovery_timing"]
    )
    morphology_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['pos'])}</td>
          <td>{html.escape(row['form'])}</td>
          <td>{row['n']:,}</td>
          <td>{row['recall']:.1f}%</td>
        </tr>
        """
        for row in advanced["morphology"]["form_transfer"]
    )
    adjusted_session_rows = "".join(
        f"""
        <tr>
          <td>{html.escape(row['band'])}</td>
          <td>{row['n']:,}</td>
          <td>{row['observed']:.1f}%</td>
          <td>{row['expected']:.1f}%</td>
          <td>{row['residual_vs_first_pp']:+.1f} pp</td>
          <td>{row['median_seconds']:.1f} s</td>
        </tr>
        """
        for row in advanced["session_adjusted"]
    )
    change_rows = "".join(
        f"""
        <tr>
          <td>{row['segment']}</td>
          <td>{html.escape(row['start'])} to {html.escape(row['end'])}</td>
          <td>{row['active_days']}</td>
          <td>{row['outcomes_per_day']:.1f}</td>
          <td>{row['median_gap']:.1f} d</td>
          <td>{row['sentence_words']:.1f}</td>
          <td>{row['clean']:.1f}%</td>
        </tr>
        """
        for row in advanced["change_points"]["segments"]
    )
    prediction_metrics = advanced["predictive_bakeoff"]["metrics"]
    all_prediction = {
        row["model"]: row
        for row in prediction_metrics
        if row["subset"] == "All ≥1-hour tests"
    }
    cold_prediction = {
        row["model"]: row
        for row in prediction_metrics
        if row["subset"] == "Cold tests ≥3d"
    }
    intro_first = intro["outcomes"][0]
    intro_first_time = intro["first_time_sensitivity"]
    aipw = advanced["spacing_aipw_sensitivity"]
    recovery = advanced["failure_recovery"]
    morphology = advanced["morphology"]
    morphology_form_lookup = {
        (row["pos"], row["form"]): row["recall"]
        for row in morphology["form_transfer"]
    }
    verb_form_difference = (
        morphology_form_lookup[("verb", "seen")]
        - morphology_form_lookup[("verb", "new")]
    )
    selector_coverage = advanced["selector_log_coverage"]
    changes = advanced["change_points"]
    rate_by_gap = {
        row["gap_days"]: row for row in learning_rate["thresholds"]
    }
    rate_7d = rate_by_gap[7]
    rate_14d = rate_by_gap[14]
    rate_30d = rate_by_gap[30]
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>What 61,498 sentence-word judgments reveal</title>
<style>
:root {{
  color-scheme: light dark;
  --bg: #f6f3ed;
  --paper: #fffdf8;
  --ink: #17201d;
  --muted: #66706c;
  --line: #d8d3c8;
  --green: #176b55;
  --green-soft: #dcece5;
  --coral: #c4503f;
  --coral-soft: #f2ddd7;
  --blue: #2c5d8a;
  --gold: #a67922;
  --violet: #70558b;
  --shadow: 0 16px 50px rgba(45,35,20,.08);
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #121715; --paper: #19201d; --ink: #eef2ee; --muted: #a9b1ac;
    --line: #36413b; --green: #69c9a6; --green-soft: #203a31;
    --coral: #f08a78; --coral-soft: #442b27; --blue: #81b3e0;
    --gold: #e1b85c; --violet: #b99bd1; --shadow: none;
  }}
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  line-height: 1.55;
}}
main {{ max-width: 1160px; margin: 0 auto; padding: 64px 28px 100px; }}
header {{ max-width: 900px; margin-bottom: 48px; }}
.eyebrow {{ color: var(--green); font-weight: 700; letter-spacing: .08em; text-transform: uppercase; font-size: 12px; }}
h1 {{ font-family: Georgia, "Times New Roman", serif; font-size: clamp(42px, 7vw, 78px); line-height: .98; letter-spacing: -.04em; margin: 14px 0 24px; max-width: 850px; }}
.dek {{ font-family: Georgia, "Times New Roman", serif; font-size: clamp(19px, 2.5vw, 27px); line-height: 1.35; color: var(--muted); max-width: 820px; }}
.provenance {{ margin-top: 22px; color: var(--muted); font-size: 13px; }}
.metric-strip {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 1px; background: var(--line); border: 1px solid var(--line); margin: 42px 0 68px; }}
.metric {{ background: var(--paper); padding: 22px; }}
.metric strong {{ display: block; font-family: Georgia, serif; font-size: 34px; line-height: 1; margin-bottom: 8px; }}
.metric span {{ color: var(--muted); font-size: 13px; }}
.toc {{ display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: baseline; margin: -34px 0 54px; font-size: 13px; }}
.toc strong {{ color: var(--muted); }}
.toc a {{ text-decoration: none; border-bottom: 1px solid var(--line); }}
section {{ margin: 76px 0; }}
.section-number {{ color: var(--green); font-weight: 700; font-size: 12px; letter-spacing: .08em; }}
h2 {{ font-family: Georgia, serif; font-size: clamp(30px,4vw,48px); line-height: 1.05; letter-spacing: -.025em; margin: 10px 0 18px; max-width: 840px; }}
h3 {{ font-family: Georgia, serif; font-size: 25px; margin: 0 0 10px; }}
p {{ max-width: 760px; }}
.lead {{ font-size: 19px; color: var(--muted); }}
.finding {{ display: grid; grid-template-columns: minmax(0,1.2fr) minmax(300px,.8fr); gap: 42px; align-items: start; }}
.finding.reverse {{ grid-template-columns: minmax(300px,.8fr) minmax(0,1.2fr); }}
.finding.reverse .copy {{ order: 2; }}
.chart {{ min-height: 280px; width: 100%; }}
.chart svg {{ width: 100%; height: auto; overflow: visible; }}
.chart-title {{ font-size: 13px; color: var(--muted); margin-bottom: 10px; }}
.figure-caption {{ color: var(--muted); font-size: 13px; max-width: 900px; margin: 12px 0 24px; }}
.note {{ border-left: 3px solid var(--gold); padding: 2px 0 2px 18px; color: var(--muted); font-size: 14px; margin-top: 24px; }}
.callout {{ background: var(--paper); box-shadow: var(--shadow); padding: 28px; }}
.callout strong.big {{ font-family: Georgia, serif; font-size: 43px; display: block; color: var(--green); line-height: 1; margin-bottom: 8px; }}
.callout.loss strong.big {{ color: var(--coral); }}
.legend {{ display: flex; gap: 18px; flex-wrap: wrap; color: var(--muted); font-size: 12px; margin-top: 10px; }}
.legend i {{ display: inline-block; width: 10px; height: 10px; margin-right: 6px; border-radius: 50%; }}
.table-wrap {{ width: 100%; max-width: 100%; overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
th,td {{ padding: 11px 12px; text-align: left; border-bottom: 1px solid var(--line); }}
th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }}
.data-table {{ margin: 24px 0; }}
.data-table caption {{ text-align: left; font-family: Georgia, serif; font-size: 21px; font-weight: 700; margin-bottom: 8px; }}
.explainer {{ display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 1px; background: var(--line); border: 1px solid var(--line); margin: 28px 0; }}
.explainer > div {{ background: var(--paper); padding: 20px; min-width: 0; }}
.explainer h3 {{ font-family: inherit; color: var(--green); font-size: 12px; text-transform: uppercase; letter-spacing: .07em; margin-bottom: 8px; }}
.explainer p {{ margin: 0; font-size: 14px; color: var(--muted); }}
.primer {{ background: var(--green-soft); padding: 32px; margin-top: 42px; }}
.primer h2 {{ font-size: 34px; }}
.definitions {{ display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 18px 30px; margin: 28px 0 0; }}
.definitions div {{ min-width: 0; }}
.definitions dt {{ font-weight: 700; margin-bottom: 4px; }}
.definitions dd {{ color: var(--muted); margin: 0; font-size: 14px; }}
.subfinding {{ max-width: 900px; margin: 24px 0; }}
.subfinding h3 {{ font-size: 25px; margin-top: 26px; }}
.equation {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--paper); padding: 13px 15px; font-size: 13px; display: inline-block; }}
.methods {{ background: var(--paper); padding: 32px; box-shadow: var(--shadow); }}
.methods-grid {{ display: grid; grid-template-columns: repeat(2,1fr); gap: 28px; }}
.methods p, .methods li {{ font-size: 14px; color: var(--muted); }}
.verdict {{ font-size: 16px; line-height: 1.55; max-width: 900px; padding: 28px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); }}
.verdict > strong {{ display: block; font-family: Georgia, serif; font-size: 25px; line-height: 1.35; margin-bottom: 10px; }}
.arabic {{ font-family: "Geeza Pro", "Noto Naskh Arabic", serif; direction: rtl; font-size: 20px; }}
.small {{ font-size: 12px; color: var(--muted); }}
code {{ overflow-wrap: anywhere; word-break: break-word; }}
a {{ color: var(--green); }}
footer {{ margin-top: 80px; padding-top: 24px; border-top: 1px solid var(--line); color: var(--muted); font-size: 13px; }}
@media (max-width: 760px) {{
  main {{ padding: 40px 18px 70px; }}
  .metric-strip {{ grid-template-columns: repeat(2,1fr); }}
  .finding,.finding.reverse {{ grid-template-columns: 1fr; }}
  .finding.reverse .copy {{ order: 0; }}
  .methods-grid,.explainer,.definitions {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<main>
<header>
  <div class="eyebrow">A longitudinal N-of-1 study · February 8–July 30, 2026</div>
  <h1>What {data['overview']['sentence_outcomes']:,} sentence-word judgments reveal</h1>
  <div class="dek">Spacing across days matters. Full all-word histories predict future recognition far better than recency alone. A historical randomized experiment shows that an intro card sharply improved the first sentence encounter, but did not establish durable retention; new inflections, verbs, and the vacation remain the clearest longer-term risks.</div>
  <div class="provenance">Read-only production snapshot · SHA-256 <code>{data['provenance']['database_sha256'][:16]}…</code> · generated from the final stored history, never from current-state labels as historical predictors</div>
</header>

<div class="metric-strip" aria-label="Dataset overview">
  <div class="metric"><strong>{data['overview']['sentence_outcomes']:,}</strong><span>sentence-word outcomes</span></div>
  <div class="metric"><strong>{data['overview']['presentations']:,}</strong><span>displayed sentences</span></div>
  <div class="metric"><strong>{data['overview']['words']:,}</strong><span>distinct canonical words</span></div>
  <div class="metric"><strong>{data['overview']['days']}</strong><span>active review days</span></div>
</div>

<nav class="toc" aria-label="Report sections">
  <strong>Jump to</strong>
  <a href="#literature">literature & learning rate</a>
  <a href="#gap">retention curve</a>
  <a href="#spacing">spacing</a>
  <a href="#contexts">contexts</a>
  <a href="#forms">forms & verbs</a>
  <a href="#vacation">vacation</a>
  <a href="#sessions">sessions</a>
  <a href="#cohorts">intake</a>
  <a href="#calibration">FSRS</a>
  <a href="#trajectories">word trajectories</a>
  <a href="#intro-trial">randomized intro trial</a>
  <a href="#prediction">predictive bakeoff</a>
  <a href="#recovery">failure recovery</a>
  <a href="#morphology">morphology</a>
  <a href="#regimes">regimes & logging</a>
  <a href="#tashkil">tashkīl</a>
  <a href="#decisions">decisions</a>
  <a href="#methods">methods</a>
</nav>

<section class="primer" id="definitions">
  <div class="section-number">How to read the report</div>
  <h2>Every word in a displayed sentence is evidence</h2>
  <p>The analytical unit is a sentence-word judgment: one canonical content word encountered in one displayed sentence, with that word’s own rating/confusion outcome. A displayed sentence therefore contributes several outcomes. The presentation UUID is recovered from the portion of <code>client_review_id</code> before <code>:lemma_id</code>; 426 legacy rows are grouped by session, sentence, and sub-second insertion proximity.</p>
  <p>Primary status is <strong>not</strong> an inclusion rule or evidence weight. It records why the scheduler chose the sentence. Once the sentence appeared, all {data['scope']['sentence_outcomes']:,} word outcomes count equally: {data['scope']['primary_outcomes']:,} were labelled primary and {data['scope']['collateral_outcomes']:,} collateral. Among the delayed tests used in the adjusted model, {data['scope']['delayed_collateral']:,} of {data['scope']['delayed_outcomes']:,} outcomes ({100*data['scope']['delayed_collateral']/data['scope']['delayed_outcomes']:.1f}%) are collateral.</p>
  <p>A primary, non-acquisition restriction would retain only {data['scope']['delayed_primary_nonacquisition']:,} of those delayed outcomes ({100*data['scope']['delayed_primary_nonacquisition']/data['scope']['delayed_outcomes']:.1f}%). This report does not make that restriction.</p>
  <p>This is not merely multiplying one sentence-level judgment across its words. In {data['scope']['mixed_clean_presentations']:,} displayed sentences, some content words were clean and others were not. Those mixed outcomes show that the word rows carry distinct comprehension information. Primary and acquisition flags remain in the adjusted model only as nuisance variables for historical scheduling differences.</p>
  <dl class="definitions">
    <div><dt>Clean recognition</dt><dd>Rating 3 or 4 with no explicit confusion/yellow mark. Rating 2 is not counted as clean even where older FSRS code treated it as recall.</dd></div>
    <div><dt>Canonical word</dt><dd>Inflected or duplicate lemma aliases are collapsed into one scheduling identity before exposure history is reconstructed.</dd></div>
    <div><dt>Primary versus collateral</dt><dd>Scheduling provenance only. Primary means the word caused sentence selection; collateral means it was another word in that sentence. Both are equal evidence after display.</dd></div>
    <div><dt>Demonstrated gap</dt><dd>The actual elapsed time since that word last appeared in any reading sentence—not the interval the scheduler intended.</dd></div>
    <div><dt>Raw percentage</dt><dd>A direct average of observed outcomes. It is descriptive and can reflect which words the algorithm selected.</dd></div>
    <div><dt>Adjusted difference</dt><dd>A model-based comparison holding measured history approximately constant. It reduces obvious confounding but does not create random assignment.</dd></div>
  </dl>
</section>

<section id="literature">
  <div class="section-number">Benchmark · What the literature changes</div>
  <h2>The memory result is credible; the scheduler target is not being met</h2>
  <p class="lead">Alif has accumulated evidence consistent with a high-throughput sentence-based retrieval system, not evidence of an implausibly fast new kind of learning. Its approximately one-week recognition level matches the classic repeated-retrieval result almost exactly. Its own FSRS probabilities, however, overpredict the stricter product outcome by 9–16 percentage points.</p>

  <h3>What “learning rate” can honestly mean in this record</h3>
  <p>There was no frozen pre-study prediction for vocabulary growth, so the historical data cannot pass or fail a preregistered words-per-day target. The defensible retrospective rate is outcome-based. A word counts only if it was actually tested in a reading sentence after a specified gap, and its <em>latest</em> qualifying judgment was clean. This is stricter than counting a word as permanently learned after one success and more informative than the current database state, but it is still receptive, contextual, and self-graded—not a productive vocabulary examination.</p>
  <p>Across {learning_rate['active_days']} active days, {learning_rate['words_seen']:,} canonical content words appeared: {learning_rate['new_words_seen_per_active_day']:.2f} newly encountered words per active day. At the ≥7-day standard, {rate_7d['latest_clean_words']:,} of {rate_7d['tested_words']:,} tested words had a clean latest qualifying outcome ({rate_7d['latest_clean_pct']:.1f}%). That is {rate_7d['words_per_active_day']:.2f} demonstrated words per active day and a conservative lower bound of {rate_7d['lower_bound_all_seen_pct']:.1f}% of every word seen, including late words that had not yet received a seven-day test.</p>
  <p>Recorded sentence-response time covers {learning_rate['timing_coverage_pct']:.1f}% of {learning_rate['presentations']:,} presentations. Dividing the seven-day total by recorded time yields {rate_7d['words_per_timed_minute']:.3f} demonstrated words per timed minute; imputing every missing presentation at the observed median of {learning_rate['median_presentation_seconds']:.2f} seconds lowers it to {rate_7d['words_per_imputed_minute']:.3f}. The useful estimate is therefore <strong>{rate_7d['words_per_imputed_minute']:.2f}–{rate_7d['words_per_timed_minute']:.2f} words per presentation-minute</strong>, not “total human learning time”: it excludes unlogged reading, lookups outside the timed presentation, content preparation, and previous Arabic study.</p>

  <div class="table-wrap data-table">
    <table>
      <caption>Outcome-based learning-rate estimates</caption>
      <thead><tr><th>Minimum actual gap</th><th>Words tested</th><th>Latest test clean</th><th>Clean among tested</th><th>Lower bound among all seen</th><th>Clean words / active day</th><th>Clean words / timed minute</th></tr></thead>
      <tbody>{learning_rate_rows}</tbody>
    </table>
  </div>
  <p class="note">The “all seen” column is deliberately a lower bound. It counts every late-entering word in the denominator even when the observation window ended before that word could receive the stated test; this right-censoring is especially severe at 30 days. “Among tested” is the interpretable retention estimate, while the lower bound prevents untested words from being silently treated as successes.</p>

  <p>The exposure cost is not tiny. The median word that first demonstrated a clean ≥7-day test had already appeared {learning_rate['first_attainment'][2]['median_prior_exposures']:.0f} times and took {learning_rate['first_attainment'][2]['median_elapsed_days']:.1f} calendar days from first sentence exposure. Reaching a ≥30-day clean test took a median {learning_rate['first_attainment'][4]['median_prior_exposures']:.0f} earlier exposures and {learning_rate['first_attainment'][4]['median_elapsed_days']:.1f} days. Alif’s apparent speed comes substantially from testing several independently scored words in one sentence, not from each word requiring only one or two encounters.</p>
  <div class="table-wrap data-table">
    <table>
      <caption>Time and exposure count to first demonstrated long-gap success</caption>
      <thead><tr><th>Milestone</th><th>Words attaining it</th><th>Median prior sentence exposures</th><th>Median elapsed time</th><th>Middle 50% elapsed</th></tr></thead>
      <tbody>{attainment_rows}</tbody>
    </table>
  </div>

  <h3>The closest quantitative comparisons</h3>
  <div class="table-wrap data-table">
    <table>
      <caption>Alif versus published vocabulary-learning results</caption>
      <thead><tr><th>Comparison</th><th>Published result</th><th>Alif result</th><th>Interpretation</th></tr></thead>
      <tbody>
        <tr>
          <td><a href="https://www.science.org/doi/10.1126/science.1152408">Karpicke &amp; Roediger (2008)</a>: repeated retrieval of 40 Swahili–English pairs</td>
          <td>About 80% cued recall after one week with repeated testing; 33–36% when successfully recalled items were dropped from further testing.</td>
          <td>{rate_7d['latest_clean_pct']:.1f}% clean on the latest ≥7-day sentence test among {rate_7d['tested_words']:,} tested words.</td>
          <td>Remarkably similar numeric retention and strong support for continuing retrieval after the first success. Alif’s contextual recognition judgment is easier than productive cued recall, so equality of percentages does not mean equal memory strength.</td>
        </tr>
        <tr>
          <td><a href="https://aclanthology.org/2024.bea-1.29/">Paddags, Hershcovich &amp; Savage (2024)</a>: the closest system, independently scheduling every word in multiword sentences</td>
          <td>Mean 0.14 remembered words/minute for a single-word SRS baseline, 0.54 for hybrid generated sentences, and 0.60 for retrieved multiword sentences in a randomized 10-day, 26-person Danish study.</td>
          <td>{rate_7d['words_per_imputed_minute']:.3f}–{rate_7d['words_per_timed_minute']:.3f} latest-clean ≥7-day words per timed presentation-minute.</td>
          <td>Alif is about 2.3–2.6× the paper’s single-word baseline, close to its overall median 0.38, and 40–47% below its multiword mean. Alif uses a longer, stricter endpoint and morphologically richer Arabic; its timing omits some activity. This supports plausibility and the architecture, not a head-to-head ranking.</td>
        </tr>
        <tr>
          <td><a href="https://www.cambridge.org/core/journals/language-teaching/article/how-effective-is-second-language-incidental-vocabulary-learning-a-metaanalysis/E38E3468FD2090B1FA3051051DE8E70C">Webb, Uchihara &amp; Yanagisawa (2023)</a>: incidental learning from meaning-focused input</td>
          <td>Average delayed target-word gain around 15% across reading studies.</td>
          <td>{rate_7d['latest_clean_pct']:.1f}% latest-clean among words actually tested after ≥7 days.</td>
          <td>Alif is far above incidental reading, as an intentional retrieval system should be. The denominators differ—gain on experimentally unknown targets versus recognition among scheduled vocabulary—so this is validation of the need for deliberate practice, not an eight-to-one effect estimate.</td>
        </tr>
        <tr>
          <td><a href="https://onlinelibrary.wiley.com/doi/10.1111/lang.12479">Kim &amp; Webb (2022)</a>: 48 spacing experiments</td>
          <td>Longer versus shorter spacing averaged about <em>g</em>=0.40 on delayed tests; spaced versus massed L2 vocabulary comparisons were larger (reported around <em>g</em>=0.76).</td>
          <td>{aipw['effect']:+.1f} pp ({aipw['low']:+.1f} to {aipw['high']:+.1f}) in the doubly robust high-versus-low day-density sensitivity; {spacing_effect['effect']:+.1f} pp in the main adjusted panel.</td>
          <td>Same direction but a smaller-looking magnitude: a 4.6-point binary difference is roughly <em>d</em>≈0.12 near Alif’s base rate. Both Alif groups were already distributed across days, unlike a massed-versus-spaced experiment, and the historical contrast is not randomized.</td>
        </tr>
      </tbody>
    </table>
  </div>

  <h3>What is actually counter to a simple reading of the literature?</h3>
  <p><strong>Context diversity is the clearest apparent discrepancy.</strong> Controlled studies often report transfer benefits from varied contexts. A recent L2 experiment found high contextual diversity advantages around <em>d</em>=0.23–0.26 on delayed meaning recognition, approximately an 8–9 point difference near an 80–85% base rate. Alif’s adjusted contrast is only {context_effect['effect']:+.1f} point, with intervals from {context_effect['low']:+.1f} to {context_effect['high']:+.1f}. That is materially smaller, not merely nonsignificant.</p>
  <p>It is not a clean contradiction. The experimental studies compare deliberately low versus high diversity for newly taught words. Alif asks for the marginal value of still more distinct sentence IDs after natural context diversity is already very high. The newer literature is itself mixed: <a href="https://pmc.ncbi.nlm.nih.gov/articles/PMC10728247/">Mak et al. (2023)</a> found that coherent repeated narrative context improved early meaning learning, while <a href="https://www.cambridge.org/core/journals/studies-in-second-language-acquisition/article/effects-of-contextual-diversity-on-incidental-vocabulary-learning/F83A1EB55404E2E07FDC1695B0BC2BAE">Oikawa &amp; Uchihara (2026)</a> found only a small delayed high-diversity benefit. The warranted conclusion is therefore a saturation result: Alif has no evidence that maximizing sentence-ID diversity beyond its existing level is worth selector capacity.</p>
  <p><strong>The sentence-load result challenges the traditional “one fact per card” intuition, but agrees with the closest system experiment.</strong> Moving from four to eight content words had an adjusted effect of {advanced['sentence_load']['adjusted_contrasts']['content_words_4_vs_8']:+.1f} points, and adding two other novel words cost only {advanced['sentence_load']['adjusted_contrasts']['other_novel_0_vs_2']:+.1f} points. Paddags and colleagues causally found that independently scoring several words in one sentence increased throughput without reducing the remembered fraction. Together, these are substantive support for Alif’s multiword presentation and for recording every word separately.</p>

  <h3>Design audit: what the evidence validates—and what it does not</h3>
  <div class="table-wrap data-table">
    <table>
      <caption>Literature-grounded audit of Alif’s design</caption>
      <thead><tr><th>Design choice</th><th>Alif evidence</th><th>Literature comparison</th><th>Verdict</th></tr></thead>
      <tbody>
        <tr><td>Independently score and remember every content word in a sentence</td><td>{data['scope']['mixed_clean_presentations']:,} sentences contain mixed per-word outcomes; full all-word histories reach AUC {all_prediction['Full history logistic']['auc']:.3f} versus {all_prediction['Half-life regression']['auc']:.3f} for HLR.</td><td>The closest randomized multiword-sentence system reported fourfold higher efficiency than single-word review.</td><td><strong>Supported.</strong> This validates all-word evidence and independent state. It does not imply that every collateral encounter should advance the schedule identically.</td></tr>
        <tr><td>Use sentences containing several due words</td><td>No adjusted penalty from four to eight content words; only a small penalty from additional novel/fragile neighbors.</td><td>Directly aligned with Paddags et al.’s multi-target sentence intervention.</td><td><strong>Supported, with load guardrails.</strong> Sentence correctness and the number of genuinely novel neighbors matter more than raw length.</td></tr>
        <tr><td>Continue retrieval after a word first appears learned</td><td>Median first ≥7-day success followed nine earlier sentence exposures; distributed-day history predicts later success.</td><td>Karpicke &amp; Roediger found approximately 80% versus 33–36% one-week recall depending on whether repeated retrieval continued.</td><td><strong>Strongly supported.</strong> Graduation must not mean disappearance from retrieval.</td></tr>
        <tr><td>Use a card to introduce a word before sentences</td><td>Randomized immediate benefit {intro_first['effect']:+.1f} pp; no established advantage at 1–90 days.</td><td><a href="https://doi.org/10.1177/1362168806072463">Webb (2007)</a> found no overall significant difference between word pairs and single glossed sentences across several vocabulary dimensions.</td><td><strong>Card as scaffold, not retention engine.</strong> It purchases first-encounter comprehension; ongoing retrieval can remain sentence-based.</td></tr>
        <tr><td>Maximize distinct sentence contexts</td><td>{context_effect['effect']:+.1f} pp adjusted, interval crosses zero.</td><td>Controlled findings range from small delayed diversity benefits to an early advantage for coherent repeated context.</td><td><strong>Not validated as an optimization objective.</strong> Preserve natural variety but do not spend scarce capacity maximizing sentence IDs.</td></tr>
        <tr><td>Schedule a lemma while tracking exact forms</td><td>New exact forms cost {abs(form_effect['effect']):.1f} points adjusted; new verb forms show an {verb_form_difference:.1f}-point raw deficit.</td><td>Arabic’s root-and-pattern morphology and the broader word-family literature both warn that knowing one family member does not guarantee access to every inflection.</td><td><strong>Supported.</strong> Keep a canonical memory object, but require evidence on useful surface forms—especially verbs.</td></tr>
        <tr><td>Fade tashkīl as competence grows</td><td>Historical fade status is mostly unlogged, so no causal estimate is possible.</td><td><a href="https://doi.org/10.1111/modl.12642">Al-Midwah &amp; Alhawary (2020)</a> found vowelized-material groups consistently outperformed unvowelized groups across three L2 proficiency levels.</td><td><strong>Unvalidated and potentially risky if early.</strong> The fade policy needs the planned randomized, word-level test.</td></tr>
        <tr><td>Trust the stored FSRS probability as the product’s recall probability</td><td>Predicted 89–93%; observed strict success 77–80%, worsening to a -15.6-point gap for ≥30-day stability.</td><td>Spaced-repetition models predict the outcome and rating semantics on which they were trained; Alif asks a stricter all-word question.</td><td><strong>Not validated.</strong> Version-stamped recalibration and workload/lateness control are required.</td></tr>
        <tr><td>Claim that the current vocabulary produces independent Arabic reading proficiency</td><td>No held-out passage comprehension, productive test, listening test, or corpus-coverage endpoint was collected.</td><td>Reading research commonly finds that lexical coverage—not a raw learned-word count—must approach roughly 95% for minimal and 98% for comfortable comprehension; Arabic lemma/form accounting complicates transfer.</td><td><strong>Not yet validated.</strong> Alif’s memory layer looks effective, but the ultimate reading goal needs held-out natural-text coverage and comprehension tests.</td></tr>
      </tbody>
    </table>
  </div>

  <div class="verdict">
    <strong>Bottom line</strong>
    Alif is learning at a plausible-to-good rate for sentence-based spaced retrieval: roughly {rate_7d['words_per_imputed_minute']:.2f}–{rate_7d['words_per_timed_minute']:.2f} demonstrated ≥7-day words per timed minute and {rate_7d['latest_clean_pct']:.1f}% latest clean recognition among tested words. The architecture’s unusual feature—putting several independently scheduled words into one sentence and scoring every word—has both internal predictive support and unusually close external experimental support. What is failing is calibration and delivery: the stored scheduler probabilities are too optimistic, mature items are late, exact-form transfer is weaker than lemma-level state implies, and no evidence yet connects the vocabulary count to unassisted Arabic passage comprehension.
  </div>
</section>

<section id="gap">
  <div class="section-number">01 · The clearest curve</div>
  <div class="finding">
    <div class="copy">
      <h2>Immediate success is almost a different outcome</h2>
      <p class="lead">Across all sentence-word outcomes, clean recognition was {data['gap_curve'][0]['recall']:.1f}% when the word had appeared less than an hour earlier. It declined steadily to {data['gap_curve'][-2]['recall']:.1f}% at 14–30 days and {data['gap_curve'][-1]['recall']:.1f}% beyond 30 days.</p>
      <p>Every bar asks: “When this canonical word appeared again in any sentence, what fraction of its word-level outcomes were clean?” The clock starts at the preceding appearance of that word, whether it had been the scheduling target or a collateral word. Acquisition flags are not excluded.</p>
      <p>With the full evidence restored, the curve is both much larger and more coherent: {data['gap_curve'][0]['n']:,} sub-hour outcomes, {data['gap_curve'][3]['n']:,} at 3–7 days, and {data['gap_curve'][-1]['n']:,} beyond 30 days. Recognition falls from {data['gap_curve'][1]['recall']:.1f}% inside one day to {data['gap_curve'][2]['recall']:.1f}% at 1–3 days, {data['gap_curve'][3]['recall']:.1f}% at 3–7 days, and {data['gap_curve'][4]['recall']:.1f}% at 7–14 days.</p>
      <p>The curve is still descriptive: easier words are allowed to survive to long gaps, and the scheduling system chooses sentences. Yet the approximately {data['gap_curve'][0]['recall']-data['gap_curve'][-1]['recall']:.1f}-point immediate-to-30-day difference makes the practical distinction unavoidable. Same-hour evidence is real learning evidence, but it is not equivalent to durable retrieval evidence.</p>
    </div>
    <div>
      <div class="chart-title">Clean word recognition by gap since any sentence exposure</div>
      <div class="chart" id="gap-chart"></div>
      <p class="figure-caption">Bar height is observed clean recognition. Whiskers are Wilson 95% binomial intervals. The n beneath each bar counts all eligible sentence-word outcomes, with no primary/collateral distinction.</p>
    </div>
  </div>
  <div class="explainer">
    <div><h3>What varies</h3><p>Elapsed time since the word’s previous appearance in any reading sentence.</p></div>
    <div><h3>What is included</h3><p>Primary, collateral, acquisition-labelled, and ordinary scheduled appearances all contribute equal word-level evidence.</p></div>
    <div><h3>Decision implication</h3><p>Do not count a sub-hour success as equivalent evidence to a success after several days.</p></div>
  </div>
  <div class="table-wrap data-table">
    <table>
      <caption>Exact values behind the gap curve</caption>
      <thead><tr><th>Gap band</th><th>Median actual gap</th><th>Tests</th><th>Clean</th><th>95% interval</th></tr></thead>
      <tbody>{gap_rows}</tbody>
    </table>
  </div>
</section>

<section id="spacing">
  <div class="section-number">02 · The strongest adjusted finding</div>
  <h2>Five reviews are not five reviews</h2>
  <p class="lead">Across all delayed sentence-word outcomes, moving from the lower to upper quartile of distribution across study days predicted a <strong>{spacing_effect['effect']:+.1f}-point</strong> recall difference (word-cluster 95% interval {spacing_effect['low']:+.1f} to {spacing_effect['high']:+.1f}; session-block interval {spacing_effect['session_low']:+.1f} to {spacing_effect['session_high']:+.1f}).</p>
  <div class="subfinding">
    <h3>What “distributed” means here</h3>
    <p>For every delayed test, the program reconstructed all earlier reading outcomes for that canonical word. Day density is the number of distinct calendar days represented in that history divided by the number of prior exposures. Five exposures on two days have density 0.40; five exposures on five days have density 1.00.</p>
    <p class="equation">day density = distinct prior study days ÷ prior reading exposures</p>
    <p>The comparison is not literally the same words randomized into two schedules. It compares test histories at day-density {data['model_metadata']['day_density_q25']:.2f} and {data['model_metadata']['day_density_q75']:.2f}, while adjusting for gap, exposure count, prior accuracy, context density, form novelty, same-sentence status, frequency, POS, month, sentence length, and the historical primary/acquisition scheduling flags. Outcomes enter only after at least three days without <em>any</em> reading exposure to that word.</p>
  </div>
  <div class="finding">
    <div>
      <div class="chart-title">Raw delayed recall within exposure-count strata</div>
      <div class="chart" id="spacing-chart"></div>
      <p class="figure-caption">Within each prior-exposure band, blue histories are relatively compressed and green histories relatively distributed. These are raw tertiles calculated separately inside each exposure band; they illustrate the data but are not the adjusted +{spacing_effect['effect']:.1f}-point estimate.</p>
    </div>
    <div class="callout">
      <strong class="big">{spacing_effect['effect']:+.1f} pp</strong>
      <h3>The spacing signal survives adjustment</h3>
      <p>The raw bars are imperfect: the 3–5 exposure group is non-monotonic, and more-distributed groups often reached a longer test gap. The regression explicitly adjusts for those differences rather than reading a causal effect directly off the bars.</p>
      <p>Uncertainty was checked two ways: resampling complete words preserves repeated outcomes for the same vocabulary item, while resampling complete sessions preserves the shared conditions and multiple word judgments inside sentence presentations. A separate regularized word-intercept model produced a +{data['model_metadata']['word_intercept_sensitivity']['distributed_days']['low']:.1f} to +{data['model_metadata']['word_intercept_sensitivity']['distributed_days']['high']:.1f}-point range.</p>
      <p class="small">{data['model_metadata']['tests']:,} delayed outcomes across {data['model_metadata']['words']:,} words; {data['model_metadata']['bootstrap_iterations']} word-cluster and {data['model_metadata']['session_bootstrap_iterations']} session-block bootstrap fits.</p>
    </div>
  </div>
  <div class="explainer">
    <div><h3>Most defensible reading</h3><p>Independent study days carry information that repeated encounters compressed into one or two days do not.</p></div>
    <div><h3>Remaining alternative</h3><p>The selector may still assign spacing using unrecorded properties of words or learner state. This is a strong association, not a randomized treatment effect.</p></div>
    <div><h3>Algorithm test</h3><p>When workload permits, favor a new day of evidence over another same-day repetition, then prospectively compare delayed outcomes.</p></div>
  </div>
  <div class="table-wrap data-table">
    <table>
      <caption>Raw spacing cells used in the descriptive graph</caption>
      <thead><tr><th>Prior exposure band</th><th>Relative distribution</th><th>Median study days</th><th>Median test gap</th><th>Tests</th><th>Clean</th></tr></thead>
      <tbody>{spacing_rows}</tbody>
    </table>
  </div>
</section>

<section id="contexts">
  <div class="section-number">03 · A hypothesis that did not survive</div>
  <div class="finding reverse">
    <div class="copy">
      <h2>Sentence variety is not the independent lever</h2>
      <p class="lead">Raw novel-context recall rises with accumulated context. But after accounting for exposure count and distribution across days, the independent context-diversity effect is only <strong>{context_effect['effect']:+.1f} points</strong> (word-cluster interval {context_effect['low']:+.1f} to {context_effect['high']:+.1f}; session-block interval {context_effect['session_low']:+.1f} to {context_effect['session_high']:+.1f}).</p>
      <p>Context density is distinct prior sentence IDs divided by prior exposures. The model compares {data['model_metadata']['context_density_q25']:.2f} with {data['model_metadata']['context_density_q75']:.2f}. In practical terms, even the lower quartile already saw roughly three distinct sentences per four exposures, while the upper quartile saw almost a new sentence every time. This is therefore a test of <em>more variety versus already-substantial variety</em>, not one memorized sentence versus rich contextual practice.</p>
      <p>The raw correlation is easy to misread because a word cannot accumulate many contexts without also accumulating exposures, study days, and opportunities to demonstrate success. Once those are included, the remaining estimate is small and uncertain. The word-intercept sensitivity models put it between {data['model_metadata']['word_intercept_sensitivity']['context_diversity']['low']:+.1f} and {data['model_metadata']['word_intercept_sensitivity']['context_diversity']['high']:+.1f} points.</p>
      <p>The practical conclusion is narrow: do not spend selection or generation capacity maximizing sentence-ID diversity after natural variety is already high. It does not show that repetitive flashcards would work equally well, nor does it measure comprehension, syntactic learning, or the acquisition of new meanings.</p>
    </div>
    <div>
      <div class="chart-title">Adjusted recall differences, percentage points</div>
      <div class="chart" id="effects-chart"></div>
      <p class="figure-caption">Each dot is an average model-based difference; each horizontal line is the word-cluster bootstrap 95% interval. The exact table also reports session-block intervals, which preserve shared sentence/session conditions. The vertical zero line means “no adjusted difference.”</p>
    </div>
  </div>
  <div class="explainer">
    <div><h3>What the graph says</h3><p>Spacing, novel form, and word class have intervals separated from zero; extra context density does not.</p></div>
    <div><h3>What it cannot say</h3><p>It cannot compare rich sentence reading with decontextualized cards because the observed histories already contain substantial sentence variety.</p></div>
    <div><h3>Design implication</h3><p>Preserve natural contextual variation, but rank form coverage and across-day spacing above maximizing distinct sentence IDs.</p></div>
  </div>
  <div class="table-wrap data-table">
    <table>
      <caption>Adjusted effects shown in the forest plot</caption>
      <thead><tr><th>Comparison</th><th>Estimate</th><th>Word-cluster 95% interval</th><th>Session-block 95% interval</th><th>Operational definition</th></tr></thead>
      <tbody>{effect_rows}</tbody>
    </table>
  </div>
</section>

<section id="forms">
  <div class="section-number">04 · The Arabic-specific bottleneck</div>
  <h2>The word can be known while the form is not</h2>
  <div class="finding">
    <div class="copy">
      <p class="lead">On delayed outcomes, a surface form never previously seen for that canonical word carried an adjusted <strong>{abs(form_effect['effect']):.1f}-point</strong> penalty (session-block interval {form_effect['session_low']:+.1f} to {form_effect['session_high']:+.1f}). Verbs carried another <strong>{abs(verb_effect['effect']):.1f}-point</strong> penalty relative to nouns (session-block interval {verb_effect['session_low']:+.1f} to {verb_effect['session_high']:+.1f}).</p>
      <p>The purple bars separate two kinds of novelty. Moving from the same sentence to a new sentence while keeping a previously seen surface form changes raw recall only from {data['context_transfer'][0]['recall']:.1f}% to {data['context_transfer'][1]['recall']:.1f}%. When both the sentence and the surface form are new, recall falls to {data['context_transfer'][2]['recall']:.1f}%. That pattern points more strongly to morphological or orthographic transfer than to sentence memorization.</p>
      <p>“Surface form” means the exact token string linked to the canonical lemma in the sentence mapping. For a verb, that may distinguish citation form, conjugated forms, participles, or attached pronouns; for a noun, singular/plural or suffixed forms. The adjusted penalty compares an unseen form with a seen form while controlling exposure count, gap, prior success, study-day distribution, context density, same-sentence status, frequency, POS, month, sentence length, and scheduling flags.</p>
      <p>The verb estimate is not proof that verbal morphology alone causes the difficulty. Verbs may also differ in polysemy, frequency measurement, sentence role, and how the selector chooses them. It is nevertheless a useful risk marker because the penalty remains after the recorded controls.</p>
      <p>The right product response is not independent SRS cards for every inflection, which would fragment the canonical memory and explode workload. It is to occasionally choose a pedagogically useful unseen form when the canonical word is already due, record the exact token shown, and evaluate whether later recognition transfers.</p>
    </div>
    <div>
      <div class="chart-title">Observed delayed recall by current context/form status</div>
      <div class="chart" id="form-chart"></div>
      <p class="figure-caption">These bars are raw outcomes, not the adjusted −{abs(form_effect['effect']):.1f}-point estimate. Their median gaps differ: {data['context_transfer'][0]['median_gap']:.1f}, {data['context_transfer'][1]['median_gap']:.1f}, and {data['context_transfer'][2]['median_gap']:.1f} days. The regression accounts for gap and other history.</p>
    </div>
  </div>
  <div class="explainer">
    <div><h3>Signal</h3><p>New sentence alone is nearly neutral when the form has been seen; a new form is where the visible drop occurs.</p></div>
    <div><h3>Measurement limit</h3><p>Before July 27, form history is reconstructed from the current verified sentence mapping rather than immutable token snapshots.</p></div>
    <div><h3>Prospective test</h3><p>Randomize seen versus useful-unseen forms within a safe due-word band and measure exact-form as well as canonical recognition.</p></div>
  </div>
  <div class="table-wrap data-table">
    <table>
      <caption>Raw context/form cells</caption>
      <thead><tr><th>Test status</th><th>Tests</th><th>Median gap since any reading</th><th>Clean</th></tr></thead>
      <tbody>{form_rows}</tbody>
    </table>
  </div>
  <div class="note">The rare “same sentence, new form” combination had fewer than ten observations and is omitted. A sentence normally contains a stable token, so changing the form usually also means changing the sentence.</div>
</section>

<section id="vacation">
  <div class="section-number">05 · The vacation natural experiment</div>
  <div class="finding reverse">
    <div class="copy">
      <h2>The break cost more than its calendar gap</h2>
      <p class="lead">First retrievals spanning the vacation were {break_effect['raw_break']:.1f}% clean versus {break_effect['raw_control']:.1f}% for ordinary 14–120 day tests. After adjusting for gap, prior exposure, prior accuracy, spacing, frequency, and word class, the vacation association was <strong>{break_effect['effect']:+.1f} points</strong> (95% interval {break_effect['low']:+.1f} to {break_effect['high']:+.1f}).</p>
      <p>A vacation-spanning event is the first qualifying word outcome on or after July 5 whose preceding appearance in <em>any</em> sentence was before June 21. The comparison pool contains all other sentence-word outcomes with actual exposure-to-exposure gaps of 14–120 days. Primary status plays no role in either definition.</p>
      <p>The graph makes the raw comparison within broad gap bands. Vacation-spanning cells now contain {next(row['n'] for row in data['break_cells'] if row['series']=='Vacation-spanning' and row['band']=='14–21d'):,}, {next(row['n'] for row in data['break_cells'] if row['series']=='Vacation-spanning' and row['band']=='21–30d'):,}, {next(row['n'] for row in data['break_cells'] if row['series']=='Vacation-spanning' and row['band']=='30–60d'):,}, and {next(row['n'] for row in data['break_cells'] if row['series']=='Vacation-spanning' and row['band']=='60–120d'):,} outcomes, so the deficit is not driven by a handful of targeted cards.</p>
      <p>The adjusted result is consistent with more than ordinary forgetting: disrupted routine, a synchronized backlog, and changed retrieval context are plausible mechanisms. They cannot be separated in this history. Sparse reviews of other words occurred during the nominal break; the candidate definition only asserts that the particular word had no recorded sentence appearance between its pre-June-21 exposure and its post-July-5 return.</p>
      <p>The full evidence also changes the resilience story. Words in the lower, middle, and upper prior-exposure thirds had medians of {data['break_resilience'][0]['median_exposures']:.0f}, {data['break_resilience'][1]['median_exposures']:.0f}, and {data['break_resilience'][2]['median_exposures']:.0f} exposures across {data['break_resilience'][0]['median_days']:.0f}, {data['break_resilience'][1]['median_days']:.0f}, and {data['break_resilience'][2]['median_days']:.0f} days; post-break recall rose from {data['break_resilience'][0]['recall']:.1f}% to {data['break_resilience'][1]['recall']:.1f}% and {data['break_resilience'][2]['recall']:.1f}%. Count, distributed days, word difficulty, and gap are entangled, so this is not a dose-response estimate—but mature, broadly practised words were visibly more resilient.</p>
    </div>
    <div>
      <div class="chart-title">Vacation-spanning versus ordinary long gaps</div>
      <div class="chart" id="break-chart"></div>
      <p class="figure-caption">Blue bars are ordinary exposure-to-exposure gaps; gold bars are each word’s first qualifying appearance after the disruption. Every content word counts. The −{abs(break_effect['effect']):.1f}-point headline comes from the adjusted model across {break_effect['break_n']:,} vacation-spanning and {break_effect['control_n']:,} control outcomes.</p>
    </div>
  </div>
  <div class="explainer">
    <div><h3>Why this is interesting</h3><p>Elapsed gap alone does not explain the post-break deficit in the measured model.</p></div>
    <div><h3>Why uncertainty remains</h3><p>There was one synchronized vacation. The {break_effect['break_n']:,} word outcomes share that single event and are not {break_effect['break_n']:,} independent vacations.</p></div>
    <div><h3>Operational response</h3><p>After a long interruption, prioritize low-history words during controlled recovery and avoid simultaneous new intake; mature words appear more resilient.</p></div>
  </div>
  <div class="table-wrap data-table">
    <table>
      <caption>Exact vacation and ordinary-gap cells</caption>
      <thead><tr><th>Gap band</th><th>Series</th><th>Median actual gap</th><th>Tests</th><th>Clean</th></tr></thead>
      <tbody>{break_rows}</tbody>
    </table>
  </div>
</section>

<section id="sessions">
  <div class="section-number">06 · Sessions</div>
  <div class="finding">
    <div class="copy">
      <h2>No evidence of a late-session collapse</h2>
      <p class="lead">Across {data['session_dynamics'][0]['sessions']} sessions containing at least 15 displayed sentences, word-level clean recognition stayed in a narrow band from {min(row['recall'] for row in data['session_dynamics']):.1f}% to {max(row['recall'] for row in data['session_dynamics']):.1f}%, while median sentence response time declined from {data['session_dynamics'][0]['median_seconds']:.1f}s to {data['session_dynamics'][-1]['median_seconds']:.1f}s.</p>
      <p>Position is counted at the sentence-presentation level. Every content-word outcome attached to sentence positions 1–3 enters the first recall cell, and so on. The first three positions contain {data['session_dynamics'][0]['presentations']:,} displayed sentences and {data['session_dynamics'][0]['n']:,} word outcomes; positions 21+ contain {data['session_dynamics'][-1]['presentations']:,} sentences and {data['session_dynamics'][-1]['n']:,} word outcomes. Response time is taken once per presentation from the row that carries it, then values below one second or above three minutes are excluded.</p>
      <p>The small dip around positions 8–12 and later recovery should not be read as a cognitive curve. The selector chooses sentence order, sentence lengths vary, and a learner who reaches position 21 is a selected session. Position is entangled with sentence difficulty, queue policy, and completion.</p>
      <p>The defensible statement is negative: there is no visible late-session collapse through the observed 21+ range. The full word evidence is much stronger for this claim than the earlier primary-only subset, but it still does not prove that arbitrarily long sessions are harmless.</p>
    </div>
    <div>
      <div class="chart-title">Word recognition and response time by sentence position</div>
      <div class="chart" id="session-chart"></div>
      <p class="figure-caption">Green uses the left axis and shows clean recognition. Blue uses the right axis and shows median seconds. Two different scales share the graph; the crossing or slope sizes should not be compared numerically.</p>
    </div>
  </div>
  <div class="explainer">
    <div><h3>Observed pattern</h3><p>Word recognition remains broadly flat while sentence response time becomes faster.</p></div>
    <div><h3>Main confound</h3><p>Sentence position was selected by the algorithm, not randomized. Early and late sentences differ.</p></div>
    <div><h3>Next clean test</h3><p>Randomize ordering within narrow priority bands, or compare the same difficulty strata by position.</p></div>
  </div>
  <div class="table-wrap data-table">
    <table>
      <caption>Session-position cells</caption>
      <thead><tr><th>Sentence position</th><th>Word outcomes</th><th>Sentence presentations</th><th>Clean</th><th>Median response</th></tr></thead>
      <tbody>{session_rows}</tbody>
    </table>
  </div>
</section>

<section id="cohorts">
  <div class="section-number">07 · Intake cohorts</div>
  <h2>Batch size is not the whole story</h2>
  <p class="lead">The 205-word July 15 cohort waited a median {next(row['median_first_hours'] for row in data['highlighted_cohorts'] if row['date']=='2026-07-15'):.1f} hours for first review. But the smaller, harder July 21 classical cohort produced a higher suspension share despite much faster first contact. Source difficulty and queue state matter alongside batch size.</p>
  <p>An intake cohort is defined by the date a word’s current acquisition episode began, using the best available acquisition, acquiring, or introduction timestamp. For each day with at least ten words, the scatter plots cohort size on the horizontal axis and the median delay until the first recorded review on the vertical axis. A high point means half the cohort waited longer than that many hours before first contact.</p>
  <p>If batch size alone governed service, points would form a clear upward curve. They do not. Large cohorts sometimes received quick contact and smaller cohorts sometimes waited. That is expected because the admission queue competes with due retention work, acquisition boxes, source-specific difficulty, and changing selector policies.</p>
  <div class="chart-title">Admission cohort size versus median time to first review; recent cohorts annotated</div>
  <div class="chart" id="cohort-chart"></div>
  <p class="figure-caption">Each point is one admission day with at least ten words. Green points are before July; coral points are July cohorts. Labels identify four cohorts discussed in the table. The graph measures service latency, not eventual learning.</p>
  <div class="table-wrap">
    <table>
      <caption>Selected cohorts and their current—not age-standardized—states</caption>
      <thead><tr><th>Start date</th><th>Words</th><th>Median first review</th><th>Durable now</th><th>Suspended now</th><th>Follow-up</th></tr></thead>
      <tbody>{highlighted_rows}</tbody>
    </table>
  </div>
  <p>The state columns require special caution. The February cohort has {next(row['age_days'] for row in data['highlighted_cohorts'] if row['date']=='2026-02-28')} days of follow-up; July 21 has only {next(row['age_days'] for row in data['highlighted_cohorts'] if row['date']=='2026-07-21')}. “Durable now” means currently known, learning, or lapsed, while “suspended now” is the current suspended state. The older cohort has had far more opportunities both to graduate and to recover, so its current percentage is not a fair outcome benchmark for the recent cohort.</p>
  <div class="explainer">
    <div><h3>Comparable endpoint</h3><p>First-review latency is observed on the same clock for almost every cohort and can be compared directly.</p></div>
    <div><h3>Non-comparable endpoint</h3><p>Current durable and suspended shares have radically unequal follow-up and changing policies.</p></div>
    <div><h3>Admission rule</h3><p>Preview queue service and source difficulty together; do not infer one universal safe batch size from historical cohort totals.</p></div>
  </div>
  <div class="note">A stronger future cohort analysis should report fixed-age outcomes—such as first contact by 24/72 hours and clean sentence-word recall by day 7 or 14—then compare cohorts only once each reaches that age.</div>
</section>

<section id="calibration">
  <div class="section-number">08 · Calibration</div>
  <div class="finding reverse">
    <div class="copy">
      <h2>The scheduler is overconfident where delivery is late</h2>
      <p class="lead">Across due FSRS tests, observed strict success was 77–80% while predicted recall was 89–93%. The discrepancy grows with stability because mature cards were also delivered later.</p>
      <p>This audit begins from stored pre-review card state and includes only sentence-reading reviews that were actually due. Acquisition reviews, not-due encounters, and rows without a complete pre-card state are excluded. Stability is the model’s pre-review estimate of memory strength: under seven days, 7–30 days, or at least 30 days.</p>
      <p>“Predicted” is the retrievability calculated from the stored state with the currently installed FSRS implementation. “Observed” is the report’s stricter outcome—rating 3 or 4 and no confusion. That matters because older rating semantics sometimes treated rating 2 as recall. The graph is deliberately evaluating whether the model predicts the product outcome we now care about, not merely whether it reproduces the old binary convention.</p>
      <p>The calibration gap widens from {next(row['value'] for row in data['fsrs'] if row['group']=='<7d' and row['series']=='Observed') - next(row['value'] for row in data['fsrs'] if row['group']=='<7d' and row['series']=='Predicted'):+.1f} points in the lowest-stability band to {next(row['value'] for row in data['fsrs'] if row['group']=='>=30d' and row['series']=='Observed') - next(row['value'] for row in data['fsrs'] if row['group']=='>=30d' and row['series']=='Predicted'):+.1f} points for ≥30-day stability. Median lateness simultaneously rises from {next(row['median_lateness_days'] for row in data['fsrs'] if row['group']=='<7d'):.2f} to {next(row['median_lateness_days'] for row in data['fsrs'] if row['group']=='>=30d'):.2f} days. This co-movement is consistent with delivery pressure contributing to poor outcomes, but does not identify how much is lateness versus model/version mismatch or difficult-card selection.</p>
      <p>This does not authorize a global FSRS retune. The 166-day history crosses scheduler and application changes, while the audit applies one current retrievability implementation retrospectively. The safer response is version-stamped prospective calibration, explicit lateness reporting, and workload control before changing parameters globally.</p>
    </div>
    <div>
      <div class="chart-title">FSRS predicted versus observed strict success</div>
      <div class="chart" id="fsrs-chart"></div>
      <p class="figure-caption">Blue is mean predicted retrievability and gold is observed clean recognition. Each pair uses the same due reviews. Higher bars are better; the vertical difference within each pair is the calibration error for the report’s strict outcome.</p>
    </div>
  </div>
  <div class="explainer">
    <div><h3>Observed mismatch</h3><p>The model is optimistic in all three stability bands, especially among mature cards.</p></div>
    <div><h3>Why not retune immediately</h3><p>Historical versions, rating semantics, late delivery, and card selection are mixed in the same record.</p></div>
    <div><h3>Required instrumentation</h3><p>Persist scheduler/model version and prediction at review time; report calibration jointly by version, lateness, and strict outcome.</p></div>
  </div>
  <div class="table-wrap data-table">
    <table>
      <caption>Calibration cells</caption>
      <thead><tr><th>Pre-review stability</th><th>Due tests</th><th>Predicted</th><th>Observed strict</th><th>Observed − predicted</th><th>Median late</th></tr></thead>
      <tbody>{fsrs_rows}</tbody>
    </table>
  </div>
</section>

<section id="trajectories">
  <div class="section-number">09 · Individual trajectories</div>
  <h2>The average conceals several learning systems</h2>
  <p class="lead">Some words become effectively invulnerable; others continue alternating between recognition and failure despite repeated exposure. The hard cases are disproportionately verbs and interference-prone forms.</p>
  <p>The plot deliberately selects extremes rather than a representative sample. The four “hard” rows are the lowest-clean-rate words with at least 15 sentence outcomes; the two “easy” rows are the highest-clean-rate words with at least 30. Primary status is ignored in both selection and display.</p>
  <p>For example, <span class="arabic">{html.escape(data['trajectories'][0]['arabic'])}</span> ({html.escape(data['trajectories'][0]['gloss'])}) is clean on only {data['trajectories'][0]['clean']:.1f}% of {data['trajectories'][0]['n']} sentence appearances. Successes do not form a stable endpoint: later failures recur after apparent recovery. By contrast, <span class="arabic">{html.escape(data['trajectories'][-2]['arabic'])}</span> ({html.escape(data['trajectories'][-2]['gloss'])}) is clean on all {data['trajectories'][-2]['n']} recorded appearances. That is evidence of a ceiling item repeatedly understood in ordinary reading, regardless of why each sentence was scheduled.</p>
  <p>The key lesson is heterogeneity. A single “number of repetitions to learn a word” is not a useful description: some words remain fragile after repeated successful episodes, while others are ceiling items almost immediately. Difficulty-aware policies should respond to the shape and recency of failures, exact forms, and confusions—not just cumulative count.</p>
  <div class="chart-title">Every dot is one equally weighted sentence-word outcome</div>
  <div class="chart" id="trajectory-chart"></div>
  <div class="legend" aria-label="Trajectory legend">
    <span><i style="background:var(--green)"></i>clean rating 3/4</span>
    <span><i style="background:var(--gold)"></i>rating 2 / partial</span>
    <span><i style="background:var(--coral)"></i>rating 1 or confused failure</span>
    <span>dot position = date of the sentence appearance; scheduling role does not change its size or weight</span>
  </div>
  <p class="figure-caption">Horizontal position is days since the first review in the snapshot, not days since that word was introduced. Dense clusters can contain several words credited from the same sentence session.</p>
  <div class="explainer">
    <div><h3>Hard trajectory</h3><p>Repeated alternation between success and failure suggests form/interference sensitivity or an unstable memory, not simply insufficient total count.</p></div>
    <div><h3>Easy trajectory</h3><p>Large numbers of successful sentence appearances show that some common words remain at ceiling across contexts and time.</p></div>
    <div><h3>Selector implication</h3><p>Use failures from every displayed sentence, plus form-specific evidence, to allocate future diagnostic exposure.</p></div>
  </div>
  <div class="table-wrap data-table">
    <table>
      <caption>Words selected for the trajectory plot</caption>
      <thead><tr><th>Arabic</th><th>Gloss</th><th>POS</th><th>Sentence outcomes</th><th>Failures</th><th>Clean</th></tr></thead>
      <tbody>{trajectory_rows}</tbody>
    </table>
  </div>
</section>

<section id="intro-trial">
  <div class="section-number">10 · A historical randomized experiment</div>
  <h2>An intro card bought immediate comprehension—not demonstrated long-term memory</h2>
  <p class="lead">Among {intro['n']} analyzable words randomized while the March experiment was active, the card-first arm was clean on {intro_first['groups']['intro_ab_card']['recall']:.1f}% of first acquisition sentence judgments, versus {intro_first['groups']['intro_ab_sentence']['recall']:.1f}% for sentence-first: a {intro_first['effect']:+.1f}-point intention-to-treat difference.</p>
  <p>This is the report’s strongest causal comparison because <code>start_acquisition()</code> assigned each word with a 50/50 random draw. The reconstructed cohort uses <code>acquisition_started_at</code> between the introducing and terminating commits, then takes the first acquisition-labelled judgment in a displayed reading sentence. It includes primary and collateral words equally: assignment concerned the teaching policy, while the outcome is the word judgment actually made.</p>
  <p>The first-judgment 95% interval is {intro_first['low']:+.1f} to {intro_first['high']:+.1f} points; the permutation p-value is {intro_first['randomization_p']:.4f}, or {intro_first['holm_p']:.4f} after Holm correction across the eight reported horizons. The assignment split—{intro['card_n']} card-first and {intro['sentence_n']} sentence-first—does not differ detectably from 50/50 (exact binomial p={intro['allocation_binomial_p']:.4f}).</p>
  <p>{intro['prior_sentence_history_n']} assigned words had an earlier sentence history before this acquisition episode. Excluding them strengthens rather than removes the pattern: among first-time acquisitions, the effect is {intro_first_time['effect']:+.1f} points ({intro_first_time['low']:+.1f} to {intro_first_time['high']:+.1f}; {intro_first_time['groups']['intro_ab_card']['n']} versus {intro_first_time['groups']['intro_ab_sentence']['n']} words). This sensitivity is not needed to make the randomized result work, but it better matches the “cold encounter” hypothesis.</p>
  <div class="finding">
    <div>
      <div class="chart-title">Card-first minus sentence-first clean recognition</div>
      <div class="chart" id="intro-chart"></div>
      <p class="figure-caption">Points are arm differences at the first qualifying judgment at or after each horizon; whiskers are Newcombe 95% intervals. Later points do not compare untouched memories: both policies generate subsequent exposures, and some sentence-first words later received cards.</p>
    </div>
    <div class="copy">
      <h3>Why the later line is not a durable win</h3>
      <p>At ≥1, 3, 7, 14, 30, and 90 days, every interval crosses zero. The isolated +{intro['outcomes'][6]['effect']:.1f}-point estimate at 60 days is one of seven exploratory delayed comparisons and does not survive multiplicity correction (Holm p={intro['outcomes'][6]['holm_p']:.4f}). The median number of intervening sentence exposures also rises from roughly two by day 1 to about sixteen by day 90, so later outcomes estimate the total downstream policy path, not the memory trace created by one card.</p>
      <p>Protocol adherence was imperfect: only {intro['card_received_before_first_n']} of {intro['card_n']} card-assigned words have an acknowledgement timestamp before the first judgment, and {intro['sentence_later_card_crossover_n']} sentence-first words later have a card timestamp. The intention-to-treat estimate is therefore the defensible one; conditioning the analysis on actually receiving a card would break randomization.</p>
      <p><strong>Decision:</strong> keep intro cards if reducing first-encounter failure and friction is valuable. Do not count the current experiment as evidence that the card improves retention after ordinary sentence practice catches up. A new trial would need a predeclared delayed outcome before crossover or differential rescue.</p>
    </div>
  </div>
  <div class="table-wrap data-table">
    <table>
      <caption>Randomized arm outcomes by follow-up horizon</caption>
      <thead><tr><th>Outcome</th><th>Card first</th><th>Sentence first</th><th>Difference</th><th>95% interval</th><th>Holm p</th><th>Median intervening exposures, card / sentence</th></tr></thead>
      <tbody>{intro_rows}</tbody>
    </table>
  </div>
</section>

<section id="prediction">
  <div class="section-number">11 · Prospective prediction</div>
  <h2>The trajectory matters far more than the last gap</h2>
  <p class="lead">On {all_prediction['Full history logistic']['n']:,} genuinely future outcomes, a full all-word history model reduced Brier error from {all_prediction['Recency-only']['brier']:.4f} for recency alone to {all_prediction['Full history logistic']['brier']:.4f}, while AUC rose from {all_prediction['Recency-only']['auc']:.3f} to {all_prediction['Full history logistic']['auc']:.3f}.</p>
  <p>This is a forward-chained bakeoff, not a random train/test split. For each April, May, June, and July fold, models train only on preceding outcomes and predict the next calendar block. Every feature exists before the judgment: actual gap, prior sentence count, prior clean rate, prior days and contexts, successful and failed recency-weighted activation, exact-form novelty, frequency, POS, acquisition/primary scheduling provenance, and sentence load. No future state or current outcome is allowed into the feature history.</p>
  <p>The middle steps explain what the extra history contributes. Half-life regression improves on recency alone ({all_prediction['Half-life regression']['brier']:.4f}); a compact activation-history model improves further ({all_prediction['Activation history']['brier']:.4f}); context, form, lexical, and delivery variables bring the full model to {all_prediction['Full history logistic']['brier']:.4f}. On the colder ≥3-day subset, the same ordering remains: Brier {cold_prediction['Recency-only']['brier']:.4f}, {cold_prediction['Half-life regression']['brier']:.4f}, {cold_prediction['Activation history']['brier']:.4f}, and {cold_prediction['Full history logistic']['brier']:.4f}.</p>
  <div class="finding reverse">
    <div>
      <div class="chart-title">Forward-chained Brier error; lower is better</div>
      <div class="chart" id="prediction-chart"></div>
      <p class="figure-caption">Brier error is the mean squared distance between predicted probability and the 0/1 clean outcome. Unlike AUC, it penalizes badly calibrated probabilities as well as poor ranking. The ≥3-day bars are harder because the immediate-memory cases are removed.</p>
    </div>
    <div class="copy">
      <h3>What this changes operationally</h3>
      <p>A scheduler that compresses the past into only “days since last seen” throws away a large amount of signal. At minimum, Alif should retain count, success/failure recency, exposure distribution, exact-form history, and uncertainty for every word seen in every sentence. This supports risk ranking even if the scheduling rule itself keeps changing.</p>
      <p>On the {advanced['predictive_bakeoff']['common_fsrs_n']:,}-outcome subset with a recoverable pre-event FSRS card, FSRS retrievability has AUC {next(row['auc'] for row in advanced['predictive_bakeoff']['common_fsrs_metrics'] if row['model']=='FSRS retrievability'):.3f}, but predicts {next(row['mean_predicted'] for row in advanced['predictive_bakeoff']['common_fsrs_metrics'] if row['model']=='FSRS retrievability'):.1f}% against {next(row['observed'] for row in advanced['predictive_bakeoff']['common_fsrs_metrics'] if row['model']=='FSRS retrievability'):.1f}% observed strict recognition. This is not a verdict that FSRS is intrinsically worse: its card state and rating semantics were designed around scheduled targets, while this report asks whether <em>every</em> sentence word was clean. It is evidence that the current FSRS number is not calibrated to that broader outcome.</p>
      <p>All four learned models are somewhat optimistic in later months, so discrimination and calibration must be monitored separately. The right next step is prospective logging and recalibration, not deploying the full-history coefficients as if they were timeless.</p>
    </div>
  </div>
  <div class="table-wrap data-table">
    <table>
      <caption>Temporal validation metrics</caption>
      <thead><tr><th>Test set</th><th>Model</th><th>n</th><th>Brier ↓</th><th>Log loss ↓</th><th>AUC ↑</th><th>Predicted / observed</th></tr></thead>
      <tbody>{prediction_rows}</tbody>
    </table>
  </div>
  <div class="table-wrap data-table">
    <table>
      <caption>Common subset with historical FSRS pre-card state</caption>
      <thead><tr><th>Model</th><th>Brier ↓</th><th>Log loss ↓</th><th>AUC ↑</th><th>Predicted / observed</th></tr></thead>
      <tbody>{common_fsrs_rows}</tbody>
    </table>
  </div>
  <div class="note"><strong>Independent spacing sensitivity.</strong> Restricting delayed tests to the lowest and highest quartiles of across-day density, a doubly robust outcome/propensity analysis estimates {aipw['effect']:+.1f} points for the more distributed group (word-cluster bootstrap {aipw['low']:+.1f} to {aipw['high']:+.1f}; n={aipw['n']:,}, {aipw['words']:,} words). This agrees with the main panel’s +{spacing_effect['effect']:.1f}-point result, but it is still a bounded observational sensitivity—not a full causal model of the evolving treatment history.</div>
</section>

<section id="recovery">
  <div class="section-number">12 · After a failure</div>
  <h2>Immediate repair wins the next attempt; a little delay may win the later test</h2>
  <p class="lead">After {recovery['failure_events_with_next']:,} failures with a later observation, the very next judgment was clean {recovery['next_outcomes'][0]['next_clean']:.1f}% of the time when it came within ten minutes, versus roughly {recovery['next_outcomes'][4]['next_clean']:.1f}% after 3–7 days.</p>
  <p>The first graph answers a short-horizon operational question: if a word just failed, how likely is its next recorded sentence judgment to be clean at each actual delay? The steep decline is expected because a near-immediate retry still benefits from working memory and often from corrective feedback. It confirms that rapid re-exposure is effective at producing an immediate successful retrieval; it does not prove that this repair is durable.</p>
  <div class="finding">
    <div>
      <div class="chart-title">Clean recognition on the next judgment after a failure</div>
      <div class="chart" id="recovery-chart"></div>
      <p class="figure-caption">Each failure contributes its next observed word judgment. Delay was not randomized: hard words, session mechanics, and rescue policy all influence when the next attempt occurs.</p>
    </div>
    <div class="copy">
      <h3>The delayed check reverses part of the story</h3>
      <p>For each run of failures, the analysis finds the first subsequent clean outcome, then asks whether the next available test after a ≥3-day gap is also clean. Recoveries achieved within an hour pass that later check {recovery['recovery_timing'][0]['stable_clean']:.1f}% of the time. Recoveries achieved after 1 hour–1 day pass {recovery['recovery_timing'][1]['stable_clean']:.1f}% of the time, and those after 1–3 days pass {recovery['recovery_timing'][2]['stable_clean']:.1f}%.</p>
      <p>This pattern is compatible with a useful distinction between <em>repair</em> and <em>confirmation</em>: show feedback and obtain an immediate clean response when needed, but do not let that same-hour success close the episode. Require a later diagnostic retrieval. The data do not identify the optimal delay, because timing was selected and easier episodes may naturally survive to particular schedules.</p>
      <p><strong>Decision:</strong> keep rapid rescue as a learner-experience safeguard, but record it as assisted/short-horizon evidence and schedule a separate delayed confirmation. Randomize that confirmation among safe delays to learn the actual optimum.</p>
    </div>
  </div>
  <div class="table-wrap data-table">
    <table>
      <caption>Next outcome after every failure</caption>
      <thead><tr><th>Actual delay</th><th>Failure events</th><th>Median delay</th><th>Next clean</th></tr></thead>
      <tbody>{recovery_next_rows}</tbody>
    </table>
  </div>
  <div class="table-wrap data-table">
    <table>
      <caption>Failure-run recovery and the next ≥3-day confirmation</caption>
      <thead><tr><th>Time to first clean</th><th>Episodes</th><th>Median attempts</th><th>Median time</th><th>Later tests</th><th>Later clean</th></tr></thead>
      <tbody>{recovery_stable_rows}</tbody>
    </table>
  </div>
</section>

<section id="morphology">
  <div class="section-number">13 · Arabic-specific transfer</div>
  <h2>Novel forms hurt every major POS, but verbs pay the largest toll</h2>
  <p class="lead">At delayed tests with at least three prior sentence exposures, recognition fell from {morphology['form_transfer'][2]['recall']:.1f}% on a previously seen verb surface to {morphology['form_transfer'][3]['recall']:.1f}% on a new one—a raw {morphology['form_transfer'][2]['recall']-morphology['form_transfer'][3]['recall']:.1f}-point transfer penalty.</p>
  <p>The same comparison is {morphology['form_transfer'][0]['recall']-morphology['form_transfer'][1]['recall']:.1f} points for nouns and {morphology['form_transfer'][4]['recall']-morphology['form_transfer'][5]['recall']:.1f} for adjectives. These cells use the current verified sentence surface mapping to reconstruct whether the exact token form had appeared earlier. They are descriptive by POS; the main adjusted panel separately estimates a {form_effect['effect']:+.1f}-point average novelty effect after controlling for measured history, month, frequency, sentence length, and scheduling provenance.</p>
  <div class="finding reverse">
    <div>
      <div class="chart-title">Delayed clean recognition: seen versus new exact surface form</div>
      <div class="chart" id="morphology-chart"></div>
      <p class="figure-caption">The canonical word remains the scheduling identity. “New” means the exact current surface form has not appeared in any earlier displayed reading sentence for that word.</p>
    </div>
    <div class="copy">
      <h3>Why this is more than “verbs are hard”</h3>
      <p>The verb penalty appears both between POS categories and within verbs when only exact-form novelty changes. That makes form transfer a more actionable target than simply increasing generic verb repetition. A single canonical schedule can remain intact while the selector tracks which useful inflections have actually been demonstrated.</p>
      <p>Weak-root verbs are also lower in the raw data than sound-root verbs, but those groups differ in form mix and lexical difficulty; this report treats that as a stratification lead, not an independent causal finding. Likewise, prior exposure to other members of a root family correlates with somewhat better early recognition, but root-family exposure was not assigned.</p>
      <p>Only {morphology['resolved_confusion_edges']} captured confusions can be resolved to a different lemma. {morphology['same_root_confusion_share']:.1f}% share a root, but the sample is far too small—and every top directed pair occurs only once—to support a confusion network. The useful conclusion is to continue capturing exact alternatives, not to automate root-based remediation yet.</p>
    </div>
  </div>
  <div class="table-wrap data-table">
    <table>
      <caption>Exact-form transfer by part of speech</caption>
      <thead><tr><th>POS</th><th>Current form</th><th>Delayed outcomes</th><th>Clean</th></tr></thead>
      <tbody>{morphology_rows}</tbody>
    </table>
  </div>
</section>

<section id="regimes">
  <div class="section-number">14 · Regime changes and identifiability</div>
  <h2>The data detect the vacation regime—but cannot reconstruct the selector’s counterfactuals</h2>
  <p class="lead">A change-point algorithm given no vacation date splits the delivery history on June 22: exactly when review volume fell, sentences became longer, and demonstrated gaps lengthened.</p>
  <p>The segmentation standardizes daily outcome volume, acquisition share, primary share, median gap, mean sentence length, and session count, then chooses one to six contiguous regimes by BIC. Clean recognition is summarized afterward but is deliberately excluded from selecting the boundary. The two-regime solution therefore detects a change in what was delivered, not a breakpoint chosen to maximize an accuracy story.</p>
  <p>Before June 22, the learner produced {changes['segments'][0]['outcomes_per_day']:.1f} sentence-word outcomes per active day with a {changes['segments'][0]['median_gap']:.1f}-day median gap and {changes['segments'][0]['sentence_words']:.1f} content words per sentence. From June 22 onward, those values were {changes['segments'][1]['outcomes_per_day']:.1f}, {changes['segments'][1]['median_gap']:.1f} days, and {changes['segments'][1]['sentence_words']:.1f}. This independently supports treating the vacation/return as a regime shift, and explains why a month coefficient alone cannot tell the whole story.</p>
  <div class="table-wrap data-table">
    <table>
      <caption>BIC-selected delivery regimes</caption>
      <thead><tr><th>Regime</th><th>Dates</th><th>Active days</th><th>Outcomes/day</th><th>Median gap</th><th>Words/sentence</th><th>Clean, descriptive</th></tr></thead>
      <tbody>{change_rows}</tbody>
    </table>
  </div>
  <h3>Why historical off-policy evaluation stops here</h3>
  <p>The interaction logs contain {selector_coverage['selected_events']:,} <code>sentence_selected</code> events across {selector_coverage['selected_sessions']:,} sessions, but only {selector_coverage['joined_selected_pairs']:,} selected session/sentence pairs join the stored outcome history—{selector_coverage['observed_coverage_pct']:.1f}% of observed presentation pairs. More importantly, the logs do not contain the full eligible candidate set or the probability with which each action could have been selected.</p>
  <p>Without those denominators, inverse-propensity or doubly robust policy evaluation cannot answer “what would have happened under selector B?” The AIPW spacing sensitivity above models a coarse historical contrast; it is not an evaluation of an alternative sequential policy. The honest boundary is: this history can compare predictions, describe delivered regimes, exploit the real randomized intro experiment, and generate bounded observational estimates. It cannot recover unlogged alternatives.</p>
  <div class="note">The companion methods specification turns this limitation into an instrumentation plan: immutable decision IDs, complete candidate snapshots or reproducible candidate-set hashes, score components, model/policy versions, eligibility reasons, randomized exploration probabilities, actual presentation state, and delayed outcome linkage.</div>
  <h3>Session length and sentence load: useful negative results</h3>
  <p>Forward-chained expected recall removes most apparent session-position differences. Relative to positions 1–3, residual recognition is {advanced['session_adjusted'][2]['residual_vs_first_pp']:+.1f} points at positions 8–12, {advanced['session_adjusted'][3]['residual_vs_first_pp']:+.1f} at 13–20, and {advanced['session_adjusted'][4]['residual_vs_first_pp']:+.1f} at 21+, while median response time falls from {advanced['session_adjusted'][0]['median_seconds']:.1f} to {advanced['session_adjusted'][-1]['median_seconds']:.1f} seconds. This suggests a small middle-session dip, not accumulating collapse.</p>
  <p>A separate nonlinear load model finds essentially no adjusted change when sentence length moves from four to eight content words ({advanced['sentence_load']['adjusted_contrasts']['content_words_4_vs_8']:+.1f} points) or when zero versus two other fragile words co-occur ({advanced['sentence_load']['adjusted_contrasts']['other_fragile_0_vs_2']:+.1f}); two other novel forms are associated with {advanced['sentence_load']['adjusted_contrasts']['other_novel_0_vs_2']:+.1f} points. These are exploratory partial-dependence contrasts without randomized load, but they argue against imposing a hard short-sentence or short-session rule from this history.</p>
  <div class="table-wrap data-table">
    <table>
      <caption>Session position against forward-chained expected difficulty</caption>
      <thead><tr><th>Position</th><th>Outcomes</th><th>Observed</th><th>Expected</th><th>Residual vs positions 1–3</th><th>Median response</th></tr></thead>
      <tbody>{adjusted_session_rows}</tbody>
    </table>
  </div>
</section>

<section id="tashkil">
  <div class="section-number">15 · A useful warning from the new tashkīl evidence</div>
  <div class="finding">
    <div class="copy">
      <h2>The obvious tashkīl comparison points backward</h2>
      <p class="lead">In the first {evidence.get('rows', 0):,} token snapshots, initially unvocalized tokens were {evidence.get('hidden_clean', 0):.1f}% clean versus {evidence.get('visible_clean', 0):.1f}% when vocalized.</p>
      <p>The unit here is a token snapshot, not an independent review. The {evidence.get('rows', 0):,} token rows come from only {evidence.get('reviews', 0):,} review sentences and cover {evidence.get('words', 0):,} canonical words; every token in a sentence inherits the same review-level rating. Standard binomial reasoning over 1,523 independent outcomes would therefore be wrong.</p>
      <p>More importantly, visibility was assigned from prior strength. Strong words were eligible for faded tashkīl and weak or new words retained vowels. The hidden group starts with an easier case mix. The +{evidence.get('hidden_clean', 0)-evidence.get('visible_clean', 0):.1f}-point raw difference is exactly what that policy should produce even if hiding vowels has no benefit—or is mildly harmful.</p>
      <p>Only {evidence.get('hidden_reveal', 0):.1f}% of initially hidden tokens had vowels revealed during interaction. That low reveal rate suggests the fading UI was usually tolerated for the already-strong group, but it still does not identify the effect on recognition because reveal behavior and strength are selected together.</p>
      <p>The useful result is methodological: exact presentation instrumentation now exists. A credible trial would randomize visible versus hidden tashkīl only among words inside the same pre-specified safe strength band, cluster analysis by review and word, and examine clean recognition, reveal use, response time, and later retention. Four days of assigned-by-strength observations cannot answer the causal question.</p>
    </div>
    <div class="callout loss">
      <strong class="big">{evidence.get('hidden_clean', 0)-evidence.get('visible_clean', 0):+.1f} pp</strong>
      <h3>A confounded “benefit”</h3>
      <p>{evidence.get('hidden_n', 0):,} initially unvocalized tokens at {evidence.get('hidden_clean', 0):.1f}% clean.</p>
      <p>{evidence.get('visible_n', 0):,} initially vocalized tokens at {evidence.get('visible_clean', 0):.1f}% clean.</p>
      <p class="small">Coverage: {html.escape(evidence.get('first', ''))[:10]} through {html.escape(evidence.get('last', ''))[:10]}. Review-level outcomes are repeated across tokens.</p>
    </div>
  </div>
  <div class="explainer">
    <div><h3>Naïve conclusion</h3><p>“Hiding vowels improves recall by {evidence.get('hidden_clean', 0)-evidence.get('visible_clean', 0):.1f} points.” The data do not support this.</p></div>
    <div><h3>Actual conclusion</h3><p>The fading policy successfully selects stronger words; presentation and strength are inseparable observationally.</p></div>
    <div><h3>Next experiment</h3><p>Randomize within a narrow safe-strength band and retain exact token-level display/reveal metadata.</p></div>
  </div>
</section>

<section id="confidence">
  <div class="section-number">16 · Evidence strength</div>
  <h2>How much confidence each finding deserves</h2>
  <p>Thousands of outcomes make estimates precise, but they do not automatically make them causal. Repetition occurs within words, and the algorithm assigned exposure, timing, form, and order. The appropriate confidence depends on the comparison design, not only the row count.</p>
  <div class="table-wrap data-table">
    <table>
      <thead><tr><th>Finding</th><th>Evidence type</th><th>Strongest warranted claim</th><th>Main unresolved threat</th></tr></thead>
      <tbody>
        <tr><td>Intro card</td><td>Historical word-level randomized assignment; intention-to-treat; multiplicity-corrected horizons</td><td>Large improvement in the first acquisition sentence judgment; no established durable advantage</td><td>Protocol non-adherence, later crossover, modest cohort</td></tr>
        <tr><td>History-based prediction</td><td>Four forward-chained calendar folds</td><td>Full all-word trajectories materially outperform gap-only prediction on future outcomes</td><td>One learner and continuing policy/concept drift</td></tr>
        <tr><td>Across-day spacing</td><td>Adjusted all-outcome panel + word/session resampling + word-intercept sensitivity</td><td>Strongest actionable historical association; suitable for a bounded prospective policy test</td><td>Unrecorded selector reasons and learner state</td></tr>
        <tr><td>Failure recovery</td><td>Episode reconstruction with later ≥3-day checks</td><td>Immediate repair and delayed confirmation should be treated as distinct outcomes</td><td>Recovery delay was selected, not randomized</td></tr>
        <tr><td>Extra context density</td><td>Same adjusted panel</td><td>No measurable incremental retention benefit within an already high-variety regime</td><td>No low-context comparison; other language outcomes unmeasured</td></tr>
        <tr><td>New form and verbs</td><td>Raw transfer cells + adjusted panel</td><td>Robust risk markers for delayed recognition and a strong experimental target</td><td>Older form history reconstructed; residual linguistic difficulty</td></tr>
        <tr><td>Vacation penalty</td><td>One quasi-experiment with gap-matched controls</td><td>The disruption is associated with worse first post-break retrieval beyond measured gap/history</td><td>One shared event; routine, backlog, and context cannot be separated</td></tr>
        <tr><td>Session position</td><td>Descriptive sentence-sequence analysis</td><td>No observed late decline through position 21+ in sessions with at least 15 displayed sentences</td><td>Algorithmic ordering and completion selection</td></tr>
        <tr><td>Intake cohorts</td><td>Descriptive cohort history</td><td>Batch size alone does not explain first-service latency</td><td>Source, queue state, policy, and unequal follow-up</td></tr>
        <tr><td>FSRS calibration</td><td>Retrospective due-review audit</td><td>Current retrospective predictions are optimistic for the strict outcome</td><td>Mixed versions, semantics, and late delivery</td></tr>
        <tr><td>Tashkīl fading</td><td>Four-day selected observational sample</td><td>The instrumentation exposes severe selection bias and enables a future trial</td><td>Visibility is assigned by strength; token outcomes are clustered</td></tr>
      </tbody>
    </table>
  </div>
</section>

<section id="decisions">
  <div class="section-number">17 · What should change</div>
  <h2>Five decisions, in order</h2>
  <div class="verdict">
    <strong>1. Keep intro cards for first-encounter comprehension, not as a retention substitute.</strong>
    <p>The randomized experiment shows a large first-judgment benefit and no established delayed benefit. Intro cards can reduce avoidable cold-start failures, but a card acknowledgement should not count as a retrieval or allow subsequent sentence practice to be skipped.</p>
    <p class="small">Guardrail: distinguish “card shown,” “first sentence clean,” and delayed unassisted retrieval in both state and analytics.</p>
  </div>
  <div class="verdict">
    <strong>2. Optimize distribution across days before increasing repetitions.</strong>
    <p>The +{spacing_effect['effect']:.1f}-point adjusted association is the strongest positive lever and remains stable with word-specific intercepts. Same-session repair remains necessary after a failure; the change is to stop treating another clean repetition on the same day as equivalent to a new day of retrieval evidence. After a word is answered cleanly, prefer its next diagnostic confirmation on a later day unless acquisition safety requires otherwise.</p>
    <p class="small">Guardrail: measure workload, acquisition completion, and delayed clean recall together. A spacing policy that strands weak words in Box 1 is not an improvement.</p>
  </div>
  <div class="verdict">
    <strong>3. Separate immediate failure repair from delayed confirmation.</strong>
    <p>Rapid re-exposure reliably produces a clean next response, but same-hour recovery is weaker on the next available ≥3-day test than recovery after a modest delay. Keep assisted repair for usability, then require a later diagnostic check before treating the lapse as resolved.</p>
    <p class="small">Guardrail: timing is observational here; randomize the delayed confirmation window before optimizing it globally.</p>
  </div>
  <div class="verdict">
    <strong>4. Target form transfer, especially for verbs.</strong>
    <p>When a canonical word is already due, occasionally select a sentence containing a useful form not yet demonstrated. Keep one canonical schedule; attach form coverage and exact-token evidence to it. The objective is diagnostic transfer, not creating separate cards for every conjugation and plural.</p>
    <p class="small">Guardrail: first test prospectively in a safe band. Older reconstructed form history is adequate for hypothesis generation, not for silently changing every review.</p>
  </div>
  <div class="verdict">
    <strong>5. Log decisions well enough to evaluate them; do not optimize context count for its own sake.</strong>
    <p>Natural sentence variety should remain because it supports reading and form exposure. But once most encounters already use distinct sentences, maximizing the sentence-ID count is not an evidenced retention objective. Generation and selector capacity should prioritize grammatical quality, appropriate difficulty, spacing, and missing form coverage.</p>
    <p class="small">Guardrail: persist candidate sets, propensities, policy versions, and actual presentation state. This result does not support replacing sentences with isolated word cards; that comparison does not exist in the data.</p>
  </div>
  <h3>Changes this report does not justify</h3>
  <p>It does not justify a global FSRS retune, a universal intake batch limit, a shorter hard session cap, or more aggressive tashkīl fading. Those observations are confounded by delivery, follow-up, ordering, or strength-based assignment and need prospective or version-stratified evidence.</p>
</section>

<section class="methods" id="methods">
  <div class="section-number">Methods and limits</div>
  <h2>What the report did—and did not—claim</h2>
  <div class="methods-grid">
    <div>
      <h3>Snapshot and population</h3>
      <p>The source is an immutable SQLite online-backup snapshot covering February 8 through July 30, 2026. Function words, proper names, and onomatopoeia are excluded with the deployed lemma-aware classifier. Alias lemmas are collapsed before histories are reconstructed. The learner is one person using one evolving application; repeated rows improve within-person resolution but do not estimate population generalizability.</p>
      <h3>Unit of analysis</h3>
      <p>A displayed sentence is the presentation and each content-word judgment inside it is an outcome. All {data['scope']['sentence_outcomes']:,} outcomes count equally, whether the scheduler labelled that word primary or collateral and whether the presentation was acquisition-labelled. Presentations and sessions are retained as clusters because their word outcomes share conditions.</p>
      <h3>Outcome</h3>
      <p>“Clean recognition” means rating 3 or 4 and no explicit confusion/yellow mark. Rating 2 is kept as a non-clean partial outcome because its semantics changed historically. The FSRS audit explicitly compares predictions with this strict outcome rather than silently mixing conventions.</p>
      <h3>History reconstruction</h3>
      <p>Before each outcome, the script reconstructs prior exposures, successful outcomes, distinct days, sessions, sentence IDs, surface forms, and elapsed gaps using only earlier rows. No current knowledge state is allowed to leak backward as a predictor. Cohort tables are the exception: they clearly label current state as a descriptive endpoint.</p>
      <h3>Panel model</h3>
      <p>{data['model_metadata']['tests']:,} sentence-word outcomes after ≥3 days without any earlier appearance of that word in a displayed sentence. There is no primary, collateral, acquisition, or ordinary-scheduling exclusion. Predictors were gap, prior exposure count and accuracy, exposure distribution across days, context density, form novelty, same-sentence status, frequency, POS, month, and sentence length. Primary and acquisition flags enter only as nuisance controls for historical scheduling differences.</p>
      <p>In a sensitivity analysis adding a regularized intercept for every word, spacing remained {data['model_metadata']['word_intercept_sensitivity']['distributed_days']['low']:+.1f} to {data['model_metadata']['word_intercept_sensitivity']['distributed_days']['high']:+.1f} points, context density {data['model_metadata']['word_intercept_sensitivity']['context_diversity']['low']:+.1f} to {data['model_metadata']['word_intercept_sensitivity']['context_diversity']['high']:+.1f}, and form novelty {data['model_metadata']['word_intercept_sensitivity']['novel_form']['low']:+.1f} to {data['model_metadata']['word_intercept_sensitivity']['novel_form']['high']:+.1f} across four regularization strengths.</p>
      <h3>Randomized intro experiment</h3>
      <p>The analysis reconstructs assignment eligibility from the exact Git interval in which <code>random.choice(["intro_ab_card", "intro_ab_sentence"])</code> was active and <code>acquisition_started_at</code> fell inside that interval. The primary analysis is intention-to-treat, uses the first post-start acquisition judgment in a reading sentence, reports Newcombe intervals and permutation p-values, and applies Holm correction across eight follow-up horizons. It does not condition on acknowledged card receipt.</p>
    </div>
    <div>
      <h3>Selection</h3>
      <p>The algorithm chose what appeared, when it appeared, which form was used, and its position. That affects causal interpretation, but it does not erase a word judgment after the sentence was shown. Primary status is therefore scheduling provenance and a nuisance covariate—not an inclusion rule. Adjusted historical effects are within-learner associations, not randomized causal estimates. Month indicators absorb broad temporal changes but cannot reconstruct every historical algorithm rule. The vacation is one quasi-experiment.</p>
      <h3>Temporal predictive validation</h3>
      <p>Four calendar folds predict April through July using only preceding events. The comparison reports Brier score, log loss, AUC, calibration error, and predicted versus observed prevalence for all ≥1-hour tests and the colder ≥3-day subset. The purpose is prospective risk estimation, not causal coefficient interpretation. FSRS is compared only where a stored pre-event card can be reconstructed, and its outcome mismatch is stated explicitly.</p>
      <h3>Recovery and regimes</h3>
      <p>Failure episodes begin at the first failure in a consecutive run and end at the next clean outcome; durability uses the next available test after a ≥3-day gap. A separate BIC segmentation uses delivery variables but not accuracy to find descriptive regimes. Neither analysis randomizes timing or proves a causal mechanism.</p>
      <h3>Presentation history</h3>
      <p>Exact token/tashkīl evidence begins July 27. Older form reconstruction uses the current verified sentence mapping and is lower-grade than immutable token snapshots. Unknown historical fading can make some older “clean” or “failure” outcomes easier or harder than the model knows.</p>
      <h3>Uncertainty</h3>
      <p>Wilson intervals describe raw binomial cells. Adjusted-effect uncertainty is shown under two dependence assumptions: {data['model_metadata']['bootstrap_iterations']} bootstrap samples of complete canonical words preserve each word's repeated history, while {data['model_metadata']['session_bootstrap_iterations']} samples of complete sessions preserve shared sentence conditions and within-session outcomes. Neither can manufacture randomized assignment. The vacation interval clusters by word but cannot cluster over vacations because only one occurred. Token-level tashkīl percentages receive no causal interval because their assignment and clustering invalidate a naïve comparison.</p>
      <h3>Exploratory status</h3>
      <p>This is a structured exploratory analysis, not a preregistered experiment. Many plausible views were inspected; only findings with useful magnitude or methodological value were retained. Exact estimates should be treated as hypotheses for prospective validation, with the strongest confidence placed on patterns that survive adjustment and sensitivity checks.</p>
    </div>
  </div>
  <p class="small">Snapshot: {data['provenance']['database_bytes']:,} bytes, SHA-256 <code>{data['provenance']['database_sha256']}</code>. Valid rows exclude function words, proper names, and onomatopoeia using the deployed lemma-aware classifier. The report is about this learner under Alif; it does not estimate a population effect.</p>
  <h3>Methods literature used for the second-phase design</h3>
  <p class="small"><a href="https://onlinelibrary.wiley.com/doi/abs/10.1207/s15516709cog0000_14">Pavlik & Anderson (2005), activation and vocabulary spacing</a> · <a href="https://pubmed.ncbi.nlm.nih.gov/18590367/">Pavlik & Anderson (2008), optimizing practice schedules</a> · <a href="https://aclanthology.org/P16-1174/">Settles & Meeder (2016), half-life regression</a> · <a href="https://pmc.ncbi.nlm.nih.gov/articles/PMC4832051/">Hernán & Robins (2016), target-trial emulation</a> · <a href="https://pubmed.ncbi.nlm.nih.gov/10955408/">Robins, Hernán & Brumback (2000), marginal structural models</a> · <a href="https://pubmed.ncbi.nlm.nih.gov/26651463/">Klasnja et al. (2015), micro-randomized trials</a> · <a href="https://www.microsoft.com/en-us/research/publication/doubly-robust-policy-evaluation-and-learning-2/">Dudík, Langford & Li (2011), doubly robust policy evaluation</a> · <a href="https://eric.ed.gov/?id=EJ686919">Abu-Rabia & Awwad (2004), Arabic morphological word recognition</a>.</p>
</section>

<footer>
  Generated by <code>backend/scripts/analyze_longitudinal_report.py</code> and <code>backend/scripts/analyze_longitudinal_advanced.py</code>. Companions: <a href="longitudinal-learning-research-plan-2026-07-30.md">research plan</a> · <a href="longitudinal-learning-phase2-methods-2026-07-30.md">decision logging and prospective experiment specification</a>.
</footer>
</main>

<script>
const DATA = {payload};
const COLORS = {{
  green: getComputedStyle(document.documentElement).getPropertyValue('--green').trim(),
  coral: getComputedStyle(document.documentElement).getPropertyValue('--coral').trim(),
  blue: getComputedStyle(document.documentElement).getPropertyValue('--blue').trim(),
  gold: getComputedStyle(document.documentElement).getPropertyValue('--gold').trim(),
  violet: getComputedStyle(document.documentElement).getPropertyValue('--violet').trim(),
  ink: getComputedStyle(document.documentElement).getPropertyValue('--ink').trim(),
  muted: getComputedStyle(document.documentElement).getPropertyValue('--muted').trim(),
  line: getComputedStyle(document.documentElement).getPropertyValue('--line').trim(),
  paper: getComputedStyle(document.documentElement).getPropertyValue('--paper').trim()
}};
const NS='http://www.w3.org/2000/svg';
function el(name, attrs={{}}, text='') {{
  const node=document.createElementNS(NS,name);
  for (const [k,v] of Object.entries(attrs)) node.setAttribute(k,String(v));
  if (text!=='') node.textContent=text;
  return node;
}}
function baseSvg(target, width=640, height=320) {{
  const host=document.getElementById(target);
  const svg=el('svg',{{viewBox:`0 0 ${{width}} ${{height}}`,role:'img','aria-label':host.previousElementSibling?.textContent||'Chart'}});
  host.replaceChildren(svg); return svg;
}}
function text(svg,x,y,value,anchor='start',size=12,color=COLORS.muted,weight=400) {{
  svg.appendChild(el('text',{{x,y,'text-anchor':anchor,'font-size':size,fill:color,'font-weight':weight}},value));
}}
function barChart(target, rows, key, value, options={{}}) {{
  const w=640,h=320,m={{t:20,r:18,b:70,l:48}}, svg=baseSvg(target,w,h);
  const max=options.max||100, innerW=w-m.l-m.r, innerH=h-m.t-m.b;
  [0,25,50,75,100].forEach(v=>{{
    const y=m.t+innerH-(v/max)*innerH;
    svg.appendChild(el('line',{{x1:m.l,x2:w-m.r,y1:y,y2:y,stroke:COLORS.line}}));
    text(svg,m.l-8,y+4,`${{v}}%`,'end',11);
  }});
  const step=innerW/rows.length, bw=Math.min(54,step*.62);
  rows.forEach((d,i)=>{{
    const x=m.l+i*step+(step-bw)/2, y=m.t+innerH-(d[value]/max)*innerH;
    svg.appendChild(el('rect',{{x,y,width:bw,height:m.t+innerH-y,rx:3,fill:options.color||COLORS.green}}));
    if(d.low!=null&&d.high!=null){{
      const yl=m.t+innerH-(d.low/max)*innerH, yh=m.t+innerH-(d.high/max)*innerH, cx=x+bw/2;
      svg.appendChild(el('line',{{x1:cx,x2:cx,y1:yl,y2:yh,stroke:COLORS.ink,'stroke-width':1.5}}));
      svg.appendChild(el('line',{{x1:cx-5,x2:cx+5,y1:yl,y2:yl,stroke:COLORS.ink}}));
      svg.appendChild(el('line',{{x1:cx-5,x2:cx+5,y1:yh,y2:yh,stroke:COLORS.ink}}));
    }}
    text(svg,x+bw/2,y-8,`${{d[value].toFixed(1)}}%`,'middle',12,COLORS.ink,500);
    const label=String(d[key]);
    const parts=label.length>14?label.split(/\\s+/):[label];
    parts.slice(0,3).forEach((p,j)=>text(svg,x+bw/2,h-m.b+20+j*14,p,'middle',10));
    if(d.n) text(svg,x+bw/2,h-8,`n=${{d.n}}`,'middle',10);
  }});
}}
function groupedBars(target, rows, groupKey, seriesKey, valueKey, seriesOrder) {{
  const groups=[...new Set(rows.map(d=>d[groupKey]))], w=660,h=330,m={{t:24,r:20,b:75,l:48}}, svg=baseSvg(target,w,h);
  const innerW=w-m.l-m.r,innerH=h-m.t-m.b,step=innerW/groups.length,cluster=step*.75,bw=cluster/seriesOrder.length;
  [0,25,50,75,100].forEach(v=>{{
    const y=m.t+innerH-v/100*innerH; svg.appendChild(el('line',{{x1:m.l,x2:w-m.r,y1:y,y2:y,stroke:COLORS.line}})); text(svg,m.l-7,y+4,`${{v}}%`,'end',11);
  }});
  const colors=[COLORS.blue,COLORS.gold,COLORS.green,COLORS.coral];
  groups.forEach((g,gi)=>{{
    const subset=rows.filter(d=>d[groupKey]===g);
    seriesOrder.forEach((s,si)=>{{
      const d=subset.find(x=>x[seriesKey]===s); if(!d)return;
      const x=m.l+gi*step+(step-cluster)/2+si*bw,y=m.t+innerH-d[valueKey]/100*innerH;
      svg.appendChild(el('rect',{{x,y,width:bw-2,height:m.t+innerH-y,fill:colors[si],rx:2}}));
      text(svg,x+(bw-2)/2,y-5,`${{d[valueKey].toFixed(1)}}%`,'middle',9,COLORS.ink,500);
    }});
    text(svg,m.l+gi*step+step/2,h-m.b+19,g,'middle',10);
  }});
  seriesOrder.forEach((s,i)=>{{ const x=m.l+i*150; svg.appendChild(el('rect',{{x,y:h-20,width:9,height:9,fill:colors[i]}})); text(svg,x+14,h-11,s,'start',10); }});
}}
function forest(target, rows) {{
  const w=650,h=290,m={{t:20,r:55,b:35,l:245}},svg=baseSvg(target,w,h),min=-14,max=12,innerW=w-m.l-m.r,step=(h-m.t-m.b)/rows.length;
  const sx=v=>m.l+(v-min)/(max-min)*innerW;
  [-10,-5,0,5,10].forEach(v=>{{const x=sx(v);svg.appendChild(el('line',{{x1:x,x2:x,y1:m.t,y2:h-m.b,stroke:v===0?COLORS.ink:COLORS.line,'stroke-width':v===0?1.4:1}}));text(svg,x,h-10,`${{v>0?'+':''}}${{v}}`,'middle',11);}});
  rows.forEach((d,i)=>{{const y=m.t+step*(i+.5);text(svg,m.l-12,y+4,d.label,'end',12,COLORS.ink);svg.appendChild(el('line',{{x1:sx(d.low),x2:sx(d.high),y1:y,y2:y,stroke:d.effect>=0?COLORS.green:COLORS.coral,'stroke-width':3}}));svg.appendChild(el('circle',{{cx:sx(d.effect),cy:y,r:5,fill:d.effect>=0?COLORS.green:COLORS.coral}}));text(svg,w-2,y+4,`${{d.effect>0?'+':''}}${{d.effect.toFixed(1)}} pp`,'end',11,COLORS.ink,500);}});
}}
function sessionChart(target, rows) {{
  const w=640,h=320,m={{t:25,r:55,b:55,l:50}},svg=baseSvg(target,w,h),innerW=w-m.l-m.r,innerH=h-m.t-m.b,step=innerW/(rows.length-1);
  const sy=v=>m.t+innerH-(v-70)/22*innerH, ty=v=>m.t+innerH-(v-15)/25*innerH;
  [70,75,80,85,90].forEach(v=>{{const y=sy(v);svg.appendChild(el('line',{{x1:m.l,x2:w-m.r,y1:y,y2:y,stroke:COLORS.line}}));text(svg,m.l-8,y+4,`${{v}}%`,'end',10);}});
  const p1=rows.map((d,i)=>`${{m.l+i*step}},${{sy(d.recall)}}`).join(' '),p2=rows.map((d,i)=>`${{m.l+i*step}},${{ty(d.median_seconds)}}`).join(' ');
  svg.appendChild(el('polyline',{{points:p1,fill:'none',stroke:COLORS.green,'stroke-width':3}}));svg.appendChild(el('polyline',{{points:p2,fill:'none',stroke:COLORS.blue,'stroke-width':3}}));
  rows.forEach((d,i)=>{{const x=m.l+i*step;svg.appendChild(el('circle',{{cx:x,cy:sy(d.recall),r:4,fill:COLORS.green}}));svg.appendChild(el('circle',{{cx:x,cy:ty(d.median_seconds),r:4,fill:COLORS.blue}}));text(svg,x,h-24,d.band,'middle',11);}});
  text(svg,w-4,m.t+3,'seconds','end',10,COLORS.blue);[20,30,40].forEach(v=>text(svg,w-4,ty(v)+4,String(v),'end',10,COLORS.blue));
}}
function scatter(target, rows) {{
  const data=rows.filter(d=>d.median_first_hours!=null&&d.median_first_hours>=0),w=700,h=360,m={{t:22,r:30,b:58,l:58}},svg=baseSvg(target,w,h),maxX=Math.max(...data.map(d=>d.n))*1.08,maxY=Math.min(180,Math.max(...data.map(d=>d.median_first_hours))*1.05);
  const sx=v=>m.l+v/maxX*(w-m.l-m.r),sy=v=>m.t+(h-m.t-m.b)-Math.min(v,maxY)/maxY*(h-m.t-m.b);
  [0,50,100,150,200].filter(v=>v<=maxX).forEach(v=>{{const x=sx(v);text(svg,x,h-25,String(v),'middle',10);}});[0,24,72,120,168].filter(v=>v<=maxY).forEach(v=>{{const y=sy(v);svg.appendChild(el('line',{{x1:m.l,x2:w-m.r,y1:y,y2:y,stroke:COLORS.line}}));text(svg,m.l-8,y+4,`${{v}}h`,'end',10);}});
  data.forEach(d=>{{const recent=d.date>='2026-07-01';svg.appendChild(el('circle',{{cx:sx(d.n),cy:sy(d.median_first_hours),r:recent?6:3.5,fill:recent?COLORS.coral:COLORS.green,opacity:recent?1:.48}}));if(['2026-07-15','2026-07-21','2026-06-03','2026-02-28'].includes(d.date))text(svg,sx(d.n)+7,sy(d.median_first_hours)-7,d.date.slice(5),'start',10,COLORS.ink,500);}});
  text(svg,(m.l+w-m.r)/2,h-4,'words admitted that day','middle',11);text(svg,2,14,'median first-review delay','start',11);
}}
function trajectory(target, words) {{
  const w=900,rowH=50,h=words.length*rowH+55,m={{l:150,r:20,t:20,b:35}},svg=baseSvg(target,w,h),maxDay=Math.max(...words.flatMap(w=>w.events.map(e=>e.day))),sx=d=>m.l+d/maxDay*(w-m.l-m.r);
  [0,30,60,90,120,150].filter(v=>v<=maxDay).forEach(v=>{{const x=sx(v);svg.appendChild(el('line',{{x1:x,x2:x,y1:m.t,y2:h-m.b,stroke:COLORS.line}}));text(svg,x,h-12,`day ${{v}}`,'middle',10);}});
  words.forEach((word,i)=>{{const y=m.t+i*rowH+18;text(svg,m.l-12,y+3,word.arabic,'end',18,COLORS.ink,500);text(svg,m.l-12,y+17,word.gloss,'end',9);svg.appendChild(el('line',{{x1:m.l,x2:w-m.r,y1:y,y2:y,stroke:COLORS.line}}));word.events.forEach(e=>{{const color=e.clean?COLORS.green:(e.rating===2?COLORS.gold:COLORS.coral);svg.appendChild(el('circle',{{cx:sx(e.day),cy:y,r:3.4,fill:color,opacity:.88}}));}});}});
}}
function horizonEffects(target, rows) {{
  const w=760,h=390,m={{t:24,r:70,b:58,l:58}},svg=baseSvg(target,w,h),min=-30,max=50,innerW=w-m.l-m.r,innerH=h-m.t-m.b,step=innerW/(rows.length-1);
  const sy=v=>m.t+(max-v)/(max-min)*innerH;
  [-20,0,20,40].forEach(v=>{{const y=sy(v);svg.appendChild(el('line',{{x1:m.l,x2:w-m.r,y1:y,y2:y,stroke:v===0?COLORS.ink:COLORS.line,'stroke-width':v===0?1.4:1}}));text(svg,m.l-8,y+4,`${{v>0?'+':''}}${{v}} pp`,'end',10);}});
  rows.forEach((d,i)=>{{const x=m.l+i*step,y=sy(d.effect),yl=sy(d.low),yh=sy(d.high),color=d.horizon_days===0?COLORS.green:COLORS.blue;svg.appendChild(el('line',{{x1:x,x2:x,y1:yl,y2:yh,stroke:color,'stroke-width':2}}));svg.appendChild(el('line',{{x1:x-5,x2:x+5,y1:yl,y2:yl,stroke:color}}));svg.appendChild(el('line',{{x1:x-5,x2:x+5,y1:yh,y2:yh,stroke:color}}));svg.appendChild(el('circle',{{cx:x,cy:y,r:d.horizon_days===0?6:4.5,fill:color}}));text(svg,x,h-25,d.horizon_days===0?'first':`${{d.horizon_days}}d`,'middle',10);text(svg,x,y-10,`${{d.effect>0?'+':''}}${{d.effect.toFixed(1)}}`,'middle',10,COLORS.ink,500);}});
  text(svg,(m.l+w-m.r)/2,h-5,'first qualifying judgment at or after horizon','middle',11);
}}
function predictionBars(target, rows) {{
  const subsetOrder=['All ≥1-hour tests','Cold tests ≥3d'],modelOrder=['Recency-only','Half-life regression','Activation history','Full history logistic'];
  const w=700,h=360,m={{t:30,r:20,b:95,l:60}},svg=baseSvg(target,w,h),max=.16,innerW=w-m.l-m.r,innerH=h-m.t-m.b,step=innerW/subsetOrder.length,cluster=step*.72,bw=cluster/modelOrder.length,colors=[COLORS.coral,COLORS.gold,COLORS.blue,COLORS.green];
  [0,.04,.08,.12,.16].forEach(v=>{{const y=m.t+innerH-v/max*innerH;svg.appendChild(el('line',{{x1:m.l,x2:w-m.r,y1:y,y2:y,stroke:COLORS.line}}));text(svg,m.l-8,y+4,v.toFixed(2),'end',10);}});
  subsetOrder.forEach((subset,si)=>{{modelOrder.forEach((model,mi)=>{{const d=rows.find(x=>x.subset===subset&&x.model===model);if(!d)return;const x=m.l+si*step+(step-cluster)/2+mi*bw,y=m.t+innerH-d.brier/max*innerH;svg.appendChild(el('rect',{{x,y,width:bw-3,height:m.t+innerH-y,fill:colors[mi],rx:2}}));text(svg,x+(bw-3)/2,y-6,d.brier.toFixed(3),'middle',9,COLORS.ink,500);}});text(svg,m.l+si*step+step/2,h-m.b+22,subset,'middle',11);}});
  modelOrder.forEach((model,i)=>{{const x=m.l+(i%2)*270,y=h-42+Math.floor(i/2)*18;svg.appendChild(el('rect',{{x,y:y-9,width:9,height:9,fill:colors[i]}}));text(svg,x+14,y,model,'start',10);}});
}}
barChart('gap-chart',DATA.gap_curve,'band','recall');
groupedBars('spacing-chart',DATA.spacing_strata,'exposure','spread','recall',['less distributed','middle','more distributed']);
forest('effects-chart',DATA.effects);
barChart('form-chart',DATA.context_transfer,'type','recall',{{color:COLORS.violet}});
groupedBars('break-chart',DATA.break_cells,'band','series','recall',['Ordinary gap','Vacation-spanning']);
sessionChart('session-chart',DATA.session_dynamics);
scatter('cohort-chart',DATA.cohorts);
groupedBars('fsrs-chart',DATA.fsrs,'group','series','value',['Predicted','Observed']);
trajectory('trajectory-chart',DATA.trajectories);
horizonEffects('intro-chart',DATA.advanced.intro_trial.outcomes);
predictionBars('prediction-chart',DATA.advanced.predictive_bakeoff.metrics);
barChart('recovery-chart',DATA.advanced.failure_recovery.next_outcomes,'band','next_clean',{{color:COLORS.coral}});
groupedBars('morphology-chart',DATA.advanced.morphology.form_transfer,'pos','form','recall',['seen','new']);
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--baseline-summary", required=True, type=Path)
    parser.add_argument("--advanced-json", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-html", required=True, type=Path)
    parser.add_argument("--bootstrap-iterations", type=int, default=200)
    args = parser.parse_args()

    database_hash_before = sha256_file(args.db)
    connection = open_immutable(args.db)
    loaded = load_data(connection)
    connection.close()

    lemmas = loaded["lemmas"]
    valid_ids = valid_lemma_ids(lemmas)
    canonicals = canonical_map(lemmas)
    valid_canonical_ids = {canonicals[lemma_id] for lemma_id in valid_ids}
    sentence_words = loaded["sentence_words"].copy()
    sentence_words["canonical_id"] = sentence_words["lemma_id"].map(canonicals)
    sentence_surface_map = (
        sentence_words.groupby(["sentence_id", "canonical_id"])["surface_form"]
        .apply(lambda values: tuple(sorted(set(values))))
        .to_dict()
    )

    reviews = loaded["reviews"]
    reviews = reviews[reviews["lemma_id"].isin(valid_ids)].copy()
    reviews["source_lemma_id"] = reviews["lemma_id"]
    reviews["lemma_id"] = reviews["lemma_id"].map(canonicals).astype(int)
    reviews["reviewed_at"] = pd.to_datetime(reviews["reviewed_at"], utc=True)
    reviews["day"] = reviews["reviewed_at"].dt.floor("D")
    reviews["clean"] = (
        (reviews["rating"] >= 3)
        & (~reviews["was_confused"].astype(bool))
    )
    reviews = add_presentation_metadata(reviews)
    reviews = add_review_history(reviews, sentence_surface_map)

    lemma_features = lemmas[
        ["lemma_id", "pos", "frequency_rank", "lemma_ar", "gloss_en"]
    ]
    sentence_outcomes = reviews[
        (reviews["review_mode"] == "reading")
        & reviews["sentence_id"].notna()
    ].copy()
    tests = sentence_outcomes[
        (sentence_outcomes["gap_any_d"] >= 3)
        & (sentence_outcomes["prior_n"] >= 3)
        & sentence_outcomes["has_surface"]
    ].merge(lemma_features, on="lemma_id", how="left")

    effects, model_frame, model_metadata = adjusted_panel_model(
        tests,
        bootstrap_iterations=args.bootstrap_iterations,
    )
    break_effect, break_cells = break_model(
        reviews,
        lemmas,
        bootstrap_iterations=args.bootstrap_iterations,
    )
    cohorts, highlighted_cohorts = cohort_analysis(
        loaded["knowledge"][
            loaded["knowledge"]["lemma_id"].isin(valid_canonical_ids)
        ].copy(),
        reviews,
    )
    baseline = json.loads(args.baseline_summary.read_text())
    advanced = json.loads(args.advanced_json.read_text())
    if (
        advanced["provenance"]["database_sha256"]
        != database_hash_before
    ):
        raise RuntimeError(
            "advanced analysis was generated from a different database"
        )
    fsrs_rows: list[dict] = []
    for group in ["<7d", "7-30d", ">=30d"]:
        item = baseline["fsrs_calibration"]["by_stability"][group]
        fsrs_rows.extend(
            [
                {
                    "group": group,
                    "series": "Predicted",
                    "value": pct(item["predicted_recall"]),
                    "n": item["reviews"],
                    "median_lateness_days": item["median_lateness_days"],
                    "brier_score": item["brier_score"],
                },
                {
                    "group": group,
                    "series": "Observed",
                    "value": pct(item["observed_recall"]),
                    "n": item["reviews"],
                    "median_lateness_days": item["median_lateness_days"],
                    "brier_score": item["brier_score"],
                },
            ]
        )

    data = {
        "provenance": {
            "database_sha256": database_hash_before,
            "database_bytes": args.db.stat().st_size,
            "cutoff": CUTOFF.isoformat(),
            "script_sha256": sha256_file(SCRIPT_PATH),
        },
        "overview": {
            "valid_reviews": len(reviews),
            "words": int(reviews["lemma_id"].nunique()),
            "days": int(reviews["reviewed_at"].dt.date.nunique()),
            "sessions": int(reviews["session_id"].nunique()),
            "sentence_outcomes": len(sentence_outcomes),
            "presentations": int(
                sentence_outcomes["presentation_id"].nunique()
            ),
        },
        "scope": {
            "sentence_outcomes": len(sentence_outcomes),
            "primary_outcomes": int(
                (sentence_outcomes["credit_type"] == "primary").sum()
            ),
            "collateral_outcomes": int(
                (sentence_outcomes["credit_type"] == "collateral").sum()
            ),
            "delayed_outcomes": len(tests),
            "delayed_primary": int((tests["credit_type"] == "primary").sum()),
            "delayed_primary_nonacquisition": int(
                (
                    (tests["credit_type"] == "primary")
                    & ~tests["is_acquisition"].astype(bool)
                ).sum()
            ),
            "delayed_collateral": int(
                (tests["credit_type"] == "collateral").sum()
            ),
            "presentations": int(
                sentence_outcomes["presentation_id"].nunique()
            ),
            "mixed_clean_presentations": int(
                (
                    sentence_outcomes.groupby("presentation_id")["clean"].nunique()
                    > 1
                ).sum()
            ),
        },
        "daily": daily_timeline(reviews),
        "gap_curve": summarize_gap_curve(sentence_outcomes),
        "learning_rate": learning_rate_summary(sentence_outcomes),
        "effects": effects,
        "model_metadata": model_metadata,
        "spacing_strata": spacing_strata(model_frame),
        "context_transfer": context_transfer(model_frame),
        "break_effect": break_effect,
        "break_cells": break_cells,
        "break_resilience": break_resilience(reviews),
        "session_dynamics": session_dynamics(sentence_outcomes),
        "cohorts": cohorts,
        "highlighted_cohorts": highlighted_cohorts,
        "fsrs": fsrs_rows,
        "evidence": evidence_summary(loaded["evidence"]),
        "trajectories": word_trajectories(reviews, lemmas),
        "advanced": advanced,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_html.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    args.output_html.write_text(make_report_html(data))
    database_hash_after = sha256_file(args.db)
    if database_hash_after != database_hash_before:
        raise RuntimeError("database bytes changed during read-only analysis")
    print(
        f"Wrote {args.output_json} and {args.output_html}; "
        f"{len(sentence_outcomes):,} sentence-word outcomes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
