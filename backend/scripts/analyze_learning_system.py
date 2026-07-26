#!/usr/bin/env python3
"""Deterministic, read-only Alif learning-system baseline analysis.

This is the lightweight WP0/WP1 command described in
research/learning-metrics-spec.md. It does not replay historical state or mutate
learning data.

Example:
    .venv/bin/python scripts/analyze_learning_system.py \
      --db /tmp/alif-baseline.db \
      --interaction-log-dir /tmp/alif-interactions \
      --window-start 2026-03-27T00:00:00Z \
      --session-window-start 2026-07-05T00:00:00Z \
      --cutoff 2026-07-25T20:00:00Z \
      --output-dir ../research/baselines/learning-system-2026-07-25 \
      --strict-read-only
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable
from urllib.parse import quote


SCRIPT_PATH = Path(__file__).resolve()
BACKEND_DIR = SCRIPT_PATH.parents[1]
REPO_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.sentence_validator import _is_function_word  # noqa: E402


SCHEMA_VERSION = 1
ARTIFACT_SOURCES = {"textbook_scan", "book", "story_import", "scaffold", "book_ocr"}
INERT_CATEGORIES = {"proper_name", "onomatopoeia"}
FSRS_STATES = {"learning", "known", "lapsed"}
PLANNED_CARD_TYPES = {"sentence", "passage", "intro", "experiment_intro", "verse"}
MAIN_LANE_MAX_RANK = 5000
RECOVERY_LIMITS = {"box1_actionable": 5, "box2_due": 30, "strict_main_fsrs_due": 750}
INTERACTION_FILE_RE = re.compile(r"^interactions_(\d{4}-\d{2}-\d{2})\.jsonl$")


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def rounded(value: float | None, digits: int = 4) -> float | None:
    if value is None or math.isnan(value):
        return None
    return round(value, digits)


def percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_identity(path: Path) -> dict[str, Any]:
    return {
        "filename": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def stable_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def open_read_only(path: Path) -> sqlite3.Connection:
    # `immutable=1` is safe only because this command requires a pinned snapshot.
    # It also prevents SQLite from creating -wal/-shm files merely because the
    # copied database header records WAL journal mode.
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}


def validate_schema(connection: sqlite3.Connection) -> None:
    required = {
        "review_log": {
            "id", "lemma_id", "rating", "reviewed_at", "review_mode",
            "sentence_id", "credit_type", "is_acquisition", "fsrs_log_json",
        },
        "user_lemma_knowledge": {
            "lemma_id", "knowledge_state", "fsrs_card_json", "times_seen",
            "times_correct", "acquisition_box", "acquisition_next_due",
            "acquisition_started_at", "entered_acquiring_at", "graduated_at",
            "source",
        },
        "lemmas": {
            "lemma_id", "lemma_ar_bare", "canonical_lemma_id", "word_category",
            "frequency_rank", "source",
        },
        "frequency_core_entries": {
            "lemma_id", "core_rank", "excluded_reason",
        },
    }
    missing: list[str] = []
    for table, columns in required.items():
        present = table_columns(connection, table)
        absent = sorted(columns - present)
        if absent:
            missing.append(f"{table}: {', '.join(absent)}")
    if missing:
        raise RuntimeError("Database snapshot lacks required columns: " + "; ".join(missing))


def resolve_canonical(lemma_id: int, canonical_by_id: dict[int, int | None]) -> int:
    current = lemma_id
    visited: set[int] = set()
    while current not in visited:
        visited.add(current)
        parent = canonical_by_id.get(current)
        if parent is None or parent == current:
            return current
        current = parent
    return min(visited)


def load_lemmas(connection: sqlite3.Connection) -> tuple[dict[int, dict[str, Any]], dict[int, int]]:
    lemmas: dict[int, dict[str, Any]] = {}
    canonical_by_id: dict[int, int | None] = {}
    for row in connection.execute(
        """
        SELECT lemma_id, lemma_ar_bare, canonical_lemma_id, word_category,
               frequency_rank, source
        FROM lemmas
        """
    ):
        item = dict(row)
        lemma_id = int(item["lemma_id"])
        lemmas[lemma_id] = item
        canonical_by_id[lemma_id] = item["canonical_lemma_id"]
    canonical_roots = {
        lemma_id: resolve_canonical(lemma_id, canonical_by_id)
        for lemma_id in canonical_by_id
    }
    return lemmas, canonical_roots


def invalid_lemma_reason(lemma: dict[str, Any] | None) -> str | None:
    if lemma is None:
        return "missing_lemma"
    if lemma.get("word_category") in INERT_CATEGORIES:
        return "inert_category"
    bare = lemma.get("lemma_ar_bare") or ""
    if bare and _is_function_word(bare):
        return "function_word"
    return None


def fetch_review_rows(
    connection: sqlite3.Connection,
    window_start: datetime,
    cutoff: datetime,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, lemma_id, rating, reviewed_at, review_mode, sentence_id,
               credit_type, is_acquisition, fsrs_log_json, client_review_id,
               session_id
        FROM review_log
        WHERE reviewed_at >= ? AND reviewed_at < ?
        ORDER BY reviewed_at, id
        """,
        (window_start.replace(tzinfo=None).isoformat(" "), cutoff.replace(tzinfo=None).isoformat(" ")),
    )
    return [dict(row) for row in rows]


def analyze_reviews(
    rows: list[dict[str, Any]],
    lemmas: dict[int, dict[str, Any]],
    canonical_roots: dict[int, int],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    invalid = Counter()
    valid_rows: list[dict[str, Any]] = []
    canonical_ids: set[int] = set()
    ratings = Counter()
    modes = Counter()
    acquisition = Counter()
    sentence_credit = Counter()
    sentence_credit_success = Counter()
    client_ids = Counter()

    for row in rows:
        if row["rating"] not in (1, 2, 3, 4):
            invalid["invalid_rating"] += 1
            continue
        reason = invalid_lemma_reason(lemmas.get(row["lemma_id"]))
        if reason:
            invalid[reason] += 1
            continue
        valid_rows.append(row)
        canonical_ids.add(canonical_roots.get(row["lemma_id"], row["lemma_id"]))
        ratings[str(row["rating"])] += 1
        modes[row["review_mode"] or "unknown"] += 1
        acquisition["acquisition" if row["is_acquisition"] else "fsrs"] += 1
        if row["client_review_id"]:
            client_ids[row["client_review_id"]] += 1
        if row["sentence_id"] is not None:
            credit = row["credit_type"] or "null"
            sentence_credit[credit] += 1
            if row["rating"] >= 3:
                sentence_credit_success[credit] += 1

    duplicate_client_ids = sorted(key for key, count in client_ids.items() if count > 1)
    warnings = []
    if duplicate_client_ids:
        warnings.append(
            f"{len(duplicate_client_ids)} duplicate non-null client_review_id values found"
        )

    sentence_rows = [row for row in valid_rows if row["sentence_id"] is not None]
    sentence_reading = [
        row for row in sentence_rows if (row["review_mode"] or "reading") == "reading"
    ]
    credit_accuracy = {}
    for credit in sorted(sentence_credit):
        total = sentence_credit[credit]
        credit_accuracy[credit] = {
            "reviews": total,
            "successes": sentence_credit_success[credit],
            "accuracy": rounded(sentence_credit_success[credit] / total if total else None),
        }

    summary = {
        "valid_word_reviews": len(valid_rows),
        "invalid_rows": dict(sorted(invalid.items())),
        "distinct_canonical_words": len(canonical_ids),
        "sentence_word_reviews": len(sentence_rows),
        "sentence_reading_word_reviews": len(sentence_reading),
        "ratings": dict(sorted(ratings.items())),
        "review_modes": dict(sorted(modes.items())),
        "phase": dict(sorted(acquisition.items())),
        "sentence_credit": dict(sorted(sentence_credit.items())),
        "sentence_credit_accuracy_diagnostic_only": credit_accuracy,
        "duplicate_client_review_ids": len(duplicate_client_ids),
    }
    return summary, valid_rows, warnings


def load_core_ranks(connection: sqlite3.Connection) -> dict[int, int]:
    return {
        int(row["lemma_id"]): int(row["rank"])
        for row in connection.execute(
            """
            SELECT lemma_id, MIN(core_rank) AS rank
            FROM frequency_core_entries
            WHERE lemma_id IS NOT NULL AND excluded_reason IS NULL
            GROUP BY lemma_id
            """
        )
    }


def parse_json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def analyze_current_state(
    connection: sqlite3.Connection,
    cutoff: datetime,
    lemmas: dict[int, dict[str, Any]],
    canonical_roots: dict[int, int],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    columns = table_columns(connection, "user_lemma_knowledge")
    backoff_expr = (
        "generation_backoff_until" if "generation_backoff_until" in columns else "NULL"
    )
    rows = [
        dict(row)
        for row in connection.execute(
            f"""
            SELECT lemma_id, knowledge_state, fsrs_card_json, times_seen,
                   times_correct, acquisition_box, acquisition_next_due,
                   acquisition_started_at, entered_acquiring_at, graduated_at,
                   source, {backoff_expr} AS generation_backoff_until
            FROM user_lemma_knowledge
            ORDER BY lemma_id
            """
        )
    ]
    ulk_by_id = {int(row["lemma_id"]): row for row in rows}
    core_ranks = load_core_ranks(connection)
    state_counts = Counter((row["knowledge_state"] or "null") for row in rows)
    box_rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
    box1_actionable = 0
    box2_due = 0

    for row in rows:
        if row["knowledge_state"] != "acquiring":
            continue
        box = int(row["acquisition_box"] or 1)
        lemma = lemmas.get(row["lemma_id"])
        reason = invalid_lemma_reason(lemma)
        due_at = parse_datetime(row["acquisition_next_due"])
        is_due = bool(due_at and due_at < cutoff)
        entered = parse_datetime(row["entered_acquiring_at"]) or parse_datetime(
            row["acquisition_started_at"]
        )
        age_days = (cutoff - entered).total_seconds() / 86400 if entered else None
        item = {
            "lemma_id": row["lemma_id"],
            "box": box,
            "due": is_due,
            "zero_correct": not bool(row["times_correct"]),
            "age_days": age_days,
            "invalid_reason": reason,
        }
        box_rows[box].append(item)
        if reason:
            continue
        if box == 1:
            backoff = parse_datetime(row["generation_backoff_until"])
            actionable = (
                not row["times_seen"]
                or is_due
            ) and not (backoff and backoff > cutoff)
            if actionable:
                box1_actionable += 1
        elif box == 2 and is_due:
            box2_due += 1

    acquisition_csv: list[dict[str, Any]] = []
    acquisition_summary: dict[str, Any] = {}
    for box in sorted(box_rows):
        items = box_rows[box]
        ages = [item["age_days"] for item in items if item["age_days"] is not None]
        row = {
            "box": box,
            "total": len(items),
            "due": sum(1 for item in items if item["due"]),
            "zero_correct": sum(1 for item in items if item["zero_correct"]),
            "age_median_days": rounded(median(ages) if ages else None, 2),
            "age_p90_days": rounded(percentile(ages, 0.90), 2),
            "age_max_days": rounded(max(ages) if ages else None, 2),
        }
        acquisition_csv.append(row)
        acquisition_summary[str(box)] = row

    fsrs_due: list[dict[str, Any]] = []
    invalid_fsrs_json = 0
    for row in rows:
        if row["knowledge_state"] not in FSRS_STATES:
            continue
        lemma_id = int(row["lemma_id"])
        lemma = lemmas.get(lemma_id)
        reason = invalid_lemma_reason(lemma)
        if reason:
            continue
        card = parse_json_object(row["fsrs_card_json"])
        if not card:
            invalid_fsrs_json += 1
            continue
        due_at = parse_datetime(card.get("due"))
        if not due_at or due_at >= cutoff:
            continue
        root = canonical_roots.get(lemma_id, lemma_id)
        shadowed = (
            root != lemma_id
            and ulk_by_id.get(root, {}).get("knowledge_state") in {"known", "learning"}
        )
        rank = core_ranks.get(lemma_id)
        lemma_rank = lemma.get("frequency_rank") if lemma else None
        artifact = (
            row["source"] in ARTIFACT_SOURCES
            or (lemma and lemma.get("source") in ARTIFACT_SOURCES)
        )
        main_lane = bool(
            (rank is not None and rank <= MAIN_LANE_MAX_RANK)
            or (lemma_rank is not None and lemma_rank <= MAIN_LANE_MAX_RANK)
            or not artifact
        )
        stability = card.get("stability")
        try:
            stability_value = float(stability) if stability is not None else None
        except (TypeError, ValueError):
            stability_value = None
        fsrs_due.append(
            {
                "lemma_id": lemma_id,
                "stability": stability_value,
                "main_lane": main_lane,
                "shadowed": shadowed,
            }
        )

    stability_bands = {"<7d": 0, "7-30d": 0, ">=30d": 0, "unknown": 0}
    for item in fsrs_due:
        stability = item["stability"]
        if stability is None:
            stability_bands["unknown"] += 1
        elif stability < 7:
            stability_bands["<7d"] += 1
        elif stability < 30:
            stability_bands["7-30d"] += 1
        else:
            stability_bands[">=30d"] += 1

    strict_main = sum(
        1 for item in fsrs_due if item["main_lane"] and not item["shadowed"]
    )
    recovery_values = {
        "box1_actionable": box1_actionable,
        "box2_due": box2_due,
        "strict_main_fsrs_due": strict_main,
    }
    tripped = [
        name for name, value in recovery_values.items()
        if value >= RECOVERY_LIMITS[name]
    ]
    warnings = []
    if invalid_fsrs_json:
        warnings.append(f"{invalid_fsrs_json} active FSRS rows had missing/invalid card JSON")

    summary = {
        "state_counts": dict(sorted(state_counts.items())),
        "acquisition": acquisition_summary,
        "acquisition_total": sum(len(items) for items in box_rows.values()),
        "recovery": {
            "values": recovery_values,
            "limits": RECOVERY_LIMITS,
            "active": bool(tripped),
            "tripped": tripped,
        },
        "fsrs": {
            "raw_actionable_due": len(fsrs_due),
            "strict_main_due": strict_main,
            "stability_bands": stability_bands,
            "shadowed_due_variants": sum(1 for item in fsrs_due if item["shadowed"]),
        },
    }
    return summary, acquisition_csv, warnings


def analyze_graduations(
    connection: sqlite3.Connection,
    window_start: datetime,
    cutoff: datetime,
) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT lemma_id, entered_acquiring_at, acquisition_started_at, graduated_at
        FROM user_lemma_knowledge
        WHERE graduated_at >= ? AND graduated_at < ?
        ORDER BY graduated_at, lemma_id
        """,
        (window_start.replace(tzinfo=None).isoformat(" "), cutoff.replace(tzinfo=None).isoformat(" ")),
    )
    durations: list[float] = []
    count = 0
    missing_start = 0
    for row in rows:
        count += 1
        graduated = parse_datetime(row["graduated_at"])
        started = parse_datetime(row["entered_acquiring_at"]) or parse_datetime(
            row["acquisition_started_at"]
        )
        if not graduated or not started or graduated < started:
            missing_start += 1
            continue
        durations.append((graduated - started).total_seconds() / 86400)
    return {
        "graduations": count,
        "duration_observed": len(durations),
        "duration_missing_or_invalid": missing_start,
        "duration_median_days": rounded(median(durations) if durations else None, 2),
        "duration_p90_days": rounded(percentile(durations, 0.90), 2),
    }


def calibration_band(stability: float) -> str:
    if stability < 7:
        return "<7d"
    if stability < 30:
        return "7-30d"
    return ">=30d"


def summarize_calibration(group: str, values: list[dict[str, float]]) -> dict[str, Any]:
    count = len(values)
    return {
        "group": group,
        "reviews": count,
        "predicted_recall": rounded(
            sum(item["predicted"] for item in values) / count if count else None
        ),
        "observed_recall": rounded(
            sum(item["observed"] for item in values) / count if count else None
        ),
        "brier_score": rounded(
            sum((item["predicted"] - item["observed"]) ** 2 for item in values) / count
            if count else None
        ),
        "median_lateness_days": rounded(
            median(item["lateness_days"] for item in values) if count else None, 2
        ),
    }


def analyze_fsrs_calibration(
    valid_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    try:
        from fsrs import Card, Scheduler
    except ImportError as exc:
        raise RuntimeError("The fsrs package is required for calibration analysis") from exc

    scheduler = Scheduler(desired_retention=0.95)
    by_band: dict[str, list[dict[str, float]]] = defaultdict(list)
    by_month: dict[str, list[dict[str, float]]] = defaultdict(list)
    excluded = Counter()

    for row in valid_rows:
        if row["sentence_id"] is None or (row["review_mode"] or "reading") != "reading":
            excluded["not_sentence_reading"] += 1
            continue
        if row["is_acquisition"]:
            excluded["acquisition"] += 1
            continue
        log_data = parse_json_object(row["fsrs_log_json"])
        pre_card = log_data.get("pre_card") if log_data else None
        if not isinstance(pre_card, dict):
            excluded["missing_pre_card"] += 1
            continue
        reviewed_at = parse_datetime(row["reviewed_at"])
        try:
            card = Card.from_dict(pre_card)
        except (KeyError, TypeError, ValueError):
            excluded["invalid_pre_card"] += 1
            continue
        if reviewed_at is None or card.due is None or card.stability is None:
            excluded["incomplete_pre_card"] += 1
            continue
        due_at = card.due
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        else:
            due_at = due_at.astimezone(timezone.utc)
        if reviewed_at < due_at:
            excluded["not_due"] += 1
            continue
        try:
            predicted = scheduler.get_card_retrievability(card, current_datetime=reviewed_at)
        except (TypeError, ValueError):
            excluded["retrievability_error"] += 1
            continue
        item = {
            "predicted": float(predicted),
            "observed": 1.0 if row["rating"] >= 3 else 0.0,
            "lateness_days": (reviewed_at - due_at).total_seconds() / 86400,
        }
        by_band[calibration_band(float(card.stability))].append(item)
        by_month[reviewed_at.strftime("%Y-%m")].append(item)

    band_order = ["<7d", "7-30d", ">=30d"]
    csv_rows = [
        {"dimension": "stability", **summarize_calibration(band, by_band.get(band, []))}
        for band in band_order
    ]
    csv_rows.extend(
        {"dimension": "month", **summarize_calibration(month, by_month[month])}
        for month in sorted(by_month)
    )
    summary = {
        "by_stability": {
            row["group"]: {key: value for key, value in row.items() if key not in {"dimension", "group"}}
            for row in csv_rows if row["dimension"] == "stability"
        },
        "by_month": {
            row["group"]: {key: value for key, value in row.items() if key not in {"dimension", "group"}}
            for row in csv_rows if row["dimension"] == "month"
        },
        "excluded": dict(sorted(excluded.items())),
        "version_caveat": (
            "Current installed FSRS retrievability is applied to historical stored pre-card "
            "states; segment by application/scheduler version before retuning."
        ),
    }
    warnings = []
    eligible = sum(len(values) for values in by_band.values())
    if eligible == 0:
        warnings.append("No eligible due sentence-reading FSRS rows for calibration")
    return summary, csv_rows, warnings


def selected_interaction_files(
    log_dir: Path | None,
    window_start: datetime,
    cutoff: datetime,
) -> list[Path]:
    if log_dir is None:
        return []
    selected = []
    for path in sorted(log_dir.glob("interactions_*.jsonl")):
        match = INTERACTION_FILE_RE.match(path.name)
        if not match:
            continue
        file_date = date.fromisoformat(match.group(1))
        if window_start.date() <= file_date <= cutoff.date():
            selected.append(path)
    return selected


def iter_interactions(
    paths: list[Path],
    window_start: datetime,
    cutoff: datetime,
) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    bad_lines = 0
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue
                timestamp = parse_datetime(event.get("ts"))
                if timestamp is None:
                    bad_lines += 1
                    continue
                if window_start <= timestamp < cutoff:
                    events.append(event)
    events.sort(key=lambda item: (item.get("ts", ""), item.get("event", ""), str(item.get("session_id", ""))))
    return events, bad_lines


def analyze_sessions(
    events: list[dict[str, Any]],
    bad_lines: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    sessions: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "planned_total": 0,
            "last_planned_ts": None,
            "last_review_ts": None,
            "answered_sentences": 0,
            "rating1": set(),
            "rating2": set(),
        }
    )
    auto_wrap_sizes: list[int] = []
    v2_timestamps: list[datetime] = []

    for event in events:
        session_id = event.get("session_id")
        detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
        if detail.get("v") == 2:
            timestamp = parse_datetime(event.get("ts"))
            if timestamp:
                v2_timestamps.append(timestamp)
        if event.get("event") == "card_shown":
            if (
                event.get("card_type") == "wrapup"
                and detail.get("variant") == "wrapup_auto"
                and event.get("card_index") == 0
                and isinstance(event.get("total_cards"), int)
            ):
                auto_wrap_sizes.append(event["total_cards"])
            if not session_id or event.get("card_type") not in PLANNED_CARD_TYPES:
                continue
            card_index = event.get("card_index")
            total_cards = event.get("total_cards")
            if not isinstance(card_index, int) or not isinstance(total_cards, int):
                continue
            state = sessions[str(session_id)]
            state["planned_total"] = max(state["planned_total"], total_cards)
            if card_index == total_cards - 1:
                timestamp = parse_datetime(event.get("ts"))
                if timestamp and (
                    state["last_planned_ts"] is None or timestamp > state["last_planned_ts"]
                ):
                    state["last_planned_ts"] = timestamp
        elif event.get("event") == "sentence_review" and session_id:
            state = sessions[str(session_id)]
            state["answered_sentences"] += 1
            timestamp = parse_datetime(event.get("ts"))
            if timestamp and (
                state["last_review_ts"] is None or timestamp > state["last_review_ts"]
            ):
                state["last_review_ts"] = timestamp
            ratings = event.get("word_ratings")
            if isinstance(ratings, dict):
                for lemma_id, rating in ratings.items():
                    try:
                        numeric_id = int(lemma_id)
                        numeric_rating = int(rating)
                    except (TypeError, ValueError):
                        continue
                    if numeric_rating == 1:
                        state["rating1"].add(numeric_id)
                    elif numeric_rating == 2:
                        state["rating2"].add(numeric_id)

    session_rows: list[dict[str, Any]] = []
    for session_id in sorted(sessions):
        state = sessions[session_id]
        if not state["planned_total"]:
            continue
        complete = bool(
            state["last_planned_ts"]
            and state["last_review_ts"]
            and state["last_review_ts"] >= state["last_planned_ts"]
        )
        session_rows.append(
            {
                "session_id": session_id,
                "complete": complete,
                "planned_cards": state["planned_total"],
                "answered_sentences": state["answered_sentences"],
                "distinct_rating1": len(state["rating1"]),
                "distinct_rating2": len(state["rating2"]),
            }
        )

    complete_rows = [row for row in session_rows if row["complete"]]
    abandoned_rows = [row for row in session_rows if not row["complete"]]
    summary = {
        "analyzable_sessions": len(session_rows),
        "approximately_complete": len(complete_rows),
        "completion_rate": rounded(
            len(complete_rows) / len(session_rows) if session_rows else None
        ),
        "complete_distinct_rating1_median": rounded(
            median(row["distinct_rating1"] for row in complete_rows)
            if complete_rows else None,
            2,
        ),
        "complete_distinct_rating1_min": (
            min(row["distinct_rating1"] for row in complete_rows) if complete_rows else None
        ),
        "complete_distinct_rating1_max": (
            max(row["distinct_rating1"] for row in complete_rows) if complete_rows else None
        ),
        "abandoned_answered_sentences_median": rounded(
            median(row["answered_sentences"] for row in abandoned_rows)
            if abandoned_rows else None,
            2,
        ),
        "abandoned_distinct_rating1_median": rounded(
            median(row["distinct_rating1"] for row in abandoned_rows)
            if abandoned_rows else None,
            2,
        ),
        "auto_wrap_count": len(auto_wrap_sizes),
        "auto_wrap_card_sizes": sorted(auto_wrap_sizes),
        "auto_wrap_cards_median": rounded(
            median(auto_wrap_sizes) if auto_wrap_sizes else None, 2
        ),
        "protocol_v2_first_telemetry": (
            iso_z(min(v2_timestamps)) if v2_timestamps else None
        ),
        "bad_or_timestamp_missing_log_lines": bad_lines,
    }
    warnings = []
    if bad_lines:
        warnings.append(f"{bad_lines} interaction lines were invalid or lacked a timestamp")
    return summary, session_rows, warnings


def analyze_daily_flows(
    valid_rows: list[dict[str, Any]],
    connection: sqlite3.Connection,
    window_start: datetime,
    cutoff: datetime,
) -> list[dict[str, Any]]:
    days: dict[str, Counter] = defaultdict(Counter)
    for row in valid_rows:
        timestamp = parse_datetime(row["reviewed_at"])
        if not timestamp:
            continue
        day = timestamp.date().isoformat()
        days[day]["word_reviews"] += 1
        if row["sentence_id"] is not None:
            days[day]["sentence_word_reviews"] += 1
        if row["rating"] >= 3:
            days[day]["successes"] += 1
        if row["is_acquisition"]:
            days[day]["acquisition_reviews"] += 1

    for row in connection.execute(
        """
        SELECT graduated_at
        FROM user_lemma_knowledge
        WHERE graduated_at >= ? AND graduated_at < ?
        """,
        (window_start.replace(tzinfo=None).isoformat(" "), cutoff.replace(tzinfo=None).isoformat(" ")),
    ):
        timestamp = parse_datetime(row["graduated_at"])
        if timestamp:
            days[timestamp.date().isoformat()]["graduations"] += 1

    return [
        {
            "date": day,
            "word_reviews": values["word_reviews"],
            "sentence_word_reviews": values["sentence_word_reviews"],
            "successes": values["successes"],
            "accuracy": rounded(
                values["successes"] / values["word_reviews"]
                if values["word_reviews"] else None
            ),
            "acquisition_reviews": values["acquisition_reviews"],
            "graduations": values["graduations"],
        }
        for day, values in sorted(days.items())
    ]


def git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_DIR,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def csv_bytes(rows: list[dict[str, Any]], fieldnames: list[str]) -> bytes:
    import io

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field) for field in fieldnames})
    return output.getvalue().encode("utf-8")


def report_markdown(summary: dict[str, Any]) -> str:
    reviews = summary["reviews"]
    state = summary["current_state"]
    recovery = state["recovery"]
    calibration = summary["fsrs_calibration"]["by_stability"]
    sessions = summary["sessions"]
    graduations = summary["graduations"]
    warnings = summary["warnings"]

    lines = [
        "# Learning-system baseline",
        "",
        f"- Window: `{summary['window']['start']}` to `{summary['window']['cutoff']}`",
        f"- Session window: `{summary['window']['session_start']}` to `{summary['window']['cutoff']}`",
        f"- Git commit: `{summary['provenance']['git_commit']}`",
        f"- Database SHA-256: `{summary['provenance']['database']['sha256']}`",
        f"- FSRS library: `{summary['provenance']['fsrs_version']}`",
        "",
        "## Review activity",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Valid word reviews | {reviews['valid_word_reviews']:,} |",
        f"| Distinct canonical words | {reviews['distinct_canonical_words']:,} |",
        f"| Sentence-word reviews | {reviews['sentence_word_reviews']:,} |",
        f"| Sentence reading reviews | {reviews['sentence_reading_word_reviews']:,} |",
        f"| Primary sentence rows | {reviews['sentence_credit'].get('primary', 0):,} |",
        f"| Collateral sentence rows | {reviews['sentence_credit'].get('collateral', 0):,} |",
        f"| Graduations | {graduations['graduations']:,} |",
        "",
        "Primary/collateral is a diagnostic split only; both are equally valid word outcomes.",
        "",
        "## Current recovery pressure",
        "",
        "| Gate | Current | Trigger | Tripped |",
        "|---|---:|---:|:---:|",
    ]
    for gate in ("box1_actionable", "box2_due", "strict_main_fsrs_due"):
        lines.append(
            f"| {gate} | {recovery['values'][gate]:,} | {recovery['limits'][gate]:,} | "
            f"{'yes' if gate in recovery['tripped'] else 'no'} |"
        )
    lines.extend(
        [
            "",
            f"Recovery active: **{'yes' if recovery['active'] else 'no'}**.",
            "",
            "## Matched FSRS calibration",
            "",
            "| Stability | Reviews | Predicted | Observed | Brier | Median late |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for band in ("<7d", "7-30d", ">=30d"):
        row = calibration[band]
        predicted = "—" if row["predicted_recall"] is None else f"{row['predicted_recall']:.1%}"
        observed = "—" if row["observed_recall"] is None else f"{row['observed_recall']:.1%}"
        brier = "—" if row["brier_score"] is None else f"{row['brier_score']:.4f}"
        late = "—" if row["median_lateness_days"] is None else f"{row['median_lateness_days']:.1f} d"
        lines.append(
            f"| {band} | {row['reviews']:,} | {predicted} | {observed} | {brier} | {late} |"
        )
    lines.extend(
        [
            "",
            "Predictions use the installed FSRS library at each actual review time; historical "
            "scheduler-version mixing remains a caveat.",
            "",
            "## Session behavior",
            "",
            "| Metric | Value |",
            "|---|---:|",
            f"| Analyzable sessions | {sessions['analyzable_sessions']:,} |",
            f"| Approximately complete | {sessions['approximately_complete']:,} |",
            f"| Completion rate | {sessions['completion_rate']:.1%} |"
            if sessions["completion_rate"] is not None else "| Completion rate | — |",
            f"| Complete-session median distinct rating-1 | "
            f"{sessions['complete_distinct_rating1_median'] if sessions['complete_distinct_rating1_median'] is not None else '—'} |",
            f"| Auto-wrap sizes | {', '.join(str(value) for value in sessions['auto_wrap_card_sizes']) or '—'} |",
            f"| Protocol-v2 first telemetry | {sessions['protocol_v2_first_telemetry'] or 'not present'} |",
            "",
            "Completion is an interaction-log approximation; see `research/learning-metrics-spec.md`.",
            "",
            "## Warnings and limitations",
            "",
        ]
    )
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- No input-integrity warnings.")
    lines.extend(
        [
            "- Current-library calibration does not authorize FSRS retuning.",
            "- This report does not reconstruct historical daily state or causal retry effects.",
            "",
        ]
    )
    return "\n".join(lines)


def build_artifacts(
    db_path: Path,
    log_dir: Path | None,
    window_start: datetime,
    session_window_start: datetime,
    cutoff: datetime,
    strict_read_only: bool,
) -> dict[str, bytes]:
    if not db_path.is_file():
        raise FileNotFoundError(f"Database does not exist: {db_path}")
    if log_dir is not None and not log_dir.is_dir():
        raise FileNotFoundError(f"Interaction log directory does not exist: {log_dir}")
    if not window_start < cutoff:
        raise ValueError("window-start must be before cutoff")
    if not session_window_start < cutoff:
        raise ValueError("session-window-start must be before cutoff")

    sidecars = [
        Path(str(db_path) + suffix)
        for suffix in ("-wal", "-shm", "-journal")
        if Path(str(db_path) + suffix).exists()
    ]
    if strict_read_only and sidecars:
        raise RuntimeError(
            "Strict mode requires a stable SQLite snapshot without sidecars: "
            + ", ".join(path.name for path in sidecars)
        )

    selected_logs = selected_interaction_files(log_dir, session_window_start, cutoff)
    db_before = file_identity(db_path)
    logs_before = [file_identity(path) for path in selected_logs]
    warnings: list[str] = []
    if log_dir is None:
        warnings.append("No interaction-log directory supplied; session metrics are empty")
    elif not selected_logs:
        warnings.append("No interaction files selected for the session window")

    connection = open_read_only(db_path)
    try:
        validate_schema(connection)
        lemmas, canonical_roots = load_lemmas(connection)
        review_rows = fetch_review_rows(connection, window_start, cutoff)
        review_summary, valid_rows, review_warnings = analyze_reviews(
            review_rows, lemmas, canonical_roots
        )
        current_state, acquisition_rows, state_warnings = analyze_current_state(
            connection, cutoff, lemmas, canonical_roots
        )
        graduations = analyze_graduations(connection, window_start, cutoff)
        calibration, calibration_rows, calibration_warnings = analyze_fsrs_calibration(
            valid_rows
        )
        daily_rows = analyze_daily_flows(valid_rows, connection, window_start, cutoff)
    finally:
        connection.close()

    events, bad_lines = iter_interactions(selected_logs, session_window_start, cutoff)
    session_summary, session_rows, session_warnings = analyze_sessions(events, bad_lines)
    warnings.extend(review_warnings + state_warnings + calibration_warnings + session_warnings)

    db_after = file_identity(db_path)
    logs_after = [file_identity(path) for path in selected_logs]
    if strict_read_only and (db_before != db_after or logs_before != logs_after):
        raise RuntimeError("An input changed while strict read-only analysis was running")

    metric_spec = REPO_DIR / "research" / "learning-metrics-spec.md"
    provenance = {
        "git_commit": git_commit(),
        "script": file_identity(SCRIPT_PATH),
        "metric_spec": file_identity(metric_spec) if metric_spec.is_file() else None,
        "database": db_before,
        "interaction_logs": logs_before,
        "python_version": sys.version.split()[0],
        "sqlite_version": sqlite3.sqlite_version,
        "fsrs_version": package_version("fsrs"),
        "strict_read_only": strict_read_only,
    }
    summary = {
        "schema_version": SCHEMA_VERSION,
        "window": {
            "start": iso_z(window_start),
            "session_start": iso_z(session_window_start),
            "cutoff": iso_z(cutoff),
            "semantics": "half-open [start, cutoff)",
        },
        "provenance": provenance,
        "reviews": review_summary,
        "current_state": current_state,
        "graduations": graduations,
        "fsrs_calibration": calibration,
        "sessions": session_summary,
        "warnings": sorted(set(warnings)),
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "window": summary["window"],
        "inputs": provenance,
        "definitions": {
            "metric_spec": "research/learning-metrics-spec.md",
            "primary_collateral": "equal word-memory validity; primary-only recovery effort gate",
            "calibration": "stored pre-card at actual review timestamp",
            "session_completion": "final planned display followed by sentence review",
        },
    }

    artifacts = {
        "manifest.json": stable_json_bytes(manifest),
        "summary.json": stable_json_bytes(summary),
        "acquisition_stock.csv": csv_bytes(
            acquisition_rows,
            [
                "box", "total", "due", "zero_correct", "age_median_days",
                "age_p90_days", "age_max_days",
            ],
        ),
        "fsrs_calibration.csv": csv_bytes(
            calibration_rows,
            [
                "dimension", "group", "reviews", "predicted_recall",
                "observed_recall", "brier_score", "median_lateness_days",
            ],
        ),
        "daily_flows.csv": csv_bytes(
            daily_rows,
            [
                "date", "word_reviews", "sentence_word_reviews", "successes",
                "accuracy", "acquisition_reviews", "graduations",
            ],
        ),
        "session_completion.csv": csv_bytes(
            session_rows,
            [
                "session_id", "complete", "planned_cards", "answered_sentences",
                "distinct_rating1", "distinct_rating2",
            ],
        ),
        "report.md": report_markdown(summary).encode("utf-8"),
    }
    checksum_lines = [
        f"{hashlib.sha256(content).hexdigest()}  {name}"
        for name, content in sorted(artifacts.items())
    ]
    artifacts["SHA256SUMS"] = ("\n".join(checksum_lines) + "\n").encode("utf-8")
    return artifacts


def write_artifacts(output_dir: Path, artifacts: dict[str, bytes]) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    for name, content in sorted(artifacts.items()):
        (output_dir / name).write_bytes(content)


def parse_cli_datetime(value: str) -> datetime:
    parsed = parse_datetime(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"Invalid ISO datetime: {value}")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Pinned SQLite snapshot")
    parser.add_argument("--interaction-log-dir", type=Path)
    parser.add_argument("--window-start", type=parse_cli_datetime, required=True)
    parser.add_argument("--session-window-start", type=parse_cli_datetime)
    parser.add_argument("--cutoff", type=parse_cli_datetime, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--strict-read-only",
        action="store_true",
        help="Reject SQLite sidecars and verify all input hashes before/after analysis",
    )
    args = parser.parse_args()
    session_start = args.session_window_start or args.window_start
    try:
        artifacts = build_artifacts(
            db_path=args.db,
            log_dir=args.interaction_log_dir,
            window_start=args.window_start,
            session_window_start=session_start,
            cutoff=args.cutoff,
            strict_read_only=args.strict_read_only,
        )
        write_artifacts(args.output_dir, artifacts)
    except (FileExistsError, FileNotFoundError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Wrote {len(artifacts)} deterministic artifacts to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
