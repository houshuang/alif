#!/usr/bin/env python3
"""Read-only audit of prospective token-level form/tashkeel evidence.

This script describes protocol-v1/v2 capture and outcomes. It deliberately does
not recommend a scheduling change: tashkeel fading is assigned to stronger
words, while reveal toggles and cause chips are learner-selected.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable
from urllib.parse import quote


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _percent(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(100 * numerator / denominator, 2)


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _pre_stability(value: Any) -> float | None:
    if not value:
        return None
    try:
        payload = value if isinstance(value, dict) else json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    pre_card = payload.get("pre_card") if isinstance(payload, dict) else None
    stability = pre_card.get("stability") if isinstance(pre_card, dict) else None
    return float(stability) if isinstance(stability, (int, float)) else None


def summarize_group(rows: Iterable[sqlite3.Row]) -> dict[str, Any]:
    materialized = list(rows)
    ratings = Counter(int(row["rating"]) for row in materialized)
    stabilities = [
        stability
        for row in materialized
        if (stability := _pre_stability(row["fsrs_log_json"])) is not None
    ]
    return {
        "token_rows": len(materialized),
        "distinct_reviews": len({
            row["client_review_id"] for row in materialized
        }),
        "distinct_canonical_lemmas": len({
            row["canonical_lemma_id"] for row in materialized
        }),
        "rating_counts": {
            str(rating): ratings.get(rating, 0) for rating in (1, 2, 3)
        },
        "strict_unaided_success_rating3_pct": _percent(
            ratings.get(3, 0),
            len(materialized),
        ),
        "assisted_recognition_rating2_pct": _percent(
            ratings.get(2, 0),
            len(materialized),
        ),
        "failure_rating1_pct": _percent(
            ratings.get(1, 0),
            len(materialized),
        ),
        "pre_review_stability_available": len(stabilities),
        "median_pre_review_stability_days": (
            round(median(stabilities), 3) if stabilities else None
        ),
    }


def summarize_rows(
    rows: list[sqlite3.Row],
    interaction_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    vocalized = [
        row for row in rows if bool(row["front_initial_tashkeel_visible"])
    ]
    unvocalized = [
        row for row in rows if not bool(row["front_initial_tashkeel_visible"])
    ]
    front_reveal = [
        row
        for row in unvocalized
        if bool(row["front_ever_tashkeel_visible"])
    ]
    causes = Counter()
    combinations = Counter()
    for row in rows:
        selected = sorted(set(_json_list(row["failure_causes_json"])))
        causes.update(selected)
        if selected:
            combinations["+".join(selected)] += 1

    created = [
        parsed
        for row in rows
        if (parsed := _parse_datetime(row["created_at"])) is not None
    ]
    protocol_counts = Counter(
        int(row["protocol_version"])
        for row in rows
        if "protocol_version" in row.keys()
    )
    schedulable = [
        row for row in rows
        if "is_schedulable_content" not in row.keys()
        or bool(row["is_schedulable_content"])
    ]
    inert = [
        row for row in rows
        if "is_schedulable_content" in row.keys()
        and not bool(row["is_schedulable_content"])
    ]
    result = {
        "protocol_versions": dict(sorted(protocol_counts.items())),
        "capture_window": {
            "first_created_at": min(created).isoformat() if created else None,
            "last_created_at": max(created).isoformat() if created else None,
        },
        "all_tokens": summarize_group(rows),
        "token_role": {
            "schedulable_content": summarize_group(schedulable),
            "exposure_only_function_or_name": summarize_group(inert),
        },
        "initial_render": {
            "vocalized": summarize_group(vocalized),
            "unvocalized": summarize_group(unvocalized),
        },
        "front_vowel_reveal_after_initially_unvocalized": summarize_group(
            front_reveal
        ),
        "cause_counts": dict(sorted(causes.items())),
        "cause_combination_counts": dict(sorted(combinations.items())),
        "integrity": {
            "rows_without_linked_review_log": sum(
                row["review_log_id"] is None for row in rows
            ),
            "duplicate_client_token_keys": len(rows) - len({
                (row["client_review_id"], row["sentence_word_id"])
                for row in rows
            }),
        },
        "submission_telemetry": interaction_summary,
        "interpretation_limits": [
            "Initial vocalized/unvocalized outcome differences are observational: fading is assigned to stronger words.",
            "Front vowel reveals are learner-selected after difficulty and are not a randomized treatment.",
            "Optional cause chips are incomplete self-report; missing cause means unknown, not no cause.",
            "Primary and collateral labels do not affect validity and are intentionally absent from token eligibility.",
            "Do not change FSRS, acquisition, workload, or form routing from this report without version-segmented matched analysis and bounded replay.",
        ],
    }
    return result


def _iter_interaction_events(log_dir: Path) -> Iterable[dict[str, Any]]:
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


def summarize_interactions(
    events: Iterable[dict[str, Any]],
    *,
    since: datetime | None,
    cutoff: datetime | None,
) -> dict[str, Any]:
    matching = []
    for event in events:
        if (
            event.get("event") != "sentence_review"
            or event.get("word_evidence_protocol_version") not in (1, 2)
        ):
            continue
        timestamp = _parse_datetime(event.get("ts"))
        if since and (timestamp is None or timestamp < since):
            continue
        if cutoff and (timestamp is None or timestamp > cutoff):
            continue
        matching.append(event)

    submitted = sum(
        value
        for event in matching
        if isinstance((value := event.get("word_evidence_count")), int)
    )
    saved = sum(
        value
        for event in matching
        if isinstance((value := event.get("word_evidence_saved")), int)
    )
    return {
        "review_events": len(matching),
        "submitted_token_rows": submitted,
        "saved_token_rows": saved,
        "dropped_token_rows": max(0, submitted - saved),
        "saved_pct_of_submitted": _percent(saved, submitted),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def analyze(
    db_path: Path,
    *,
    since: datetime | None = None,
    cutoff: datetime | None = None,
    interaction_log_dir: Path | None = None,
) -> dict[str, Any]:
    before_hash = _sha256(db_path)
    uri = f"file:{quote(str(db_path.resolve()))}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        exists = connection.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'word_review_evidence'
            """
        ).fetchone()
        if not exists:
            raise SystemExit(
                "word_review_evidence table is absent; deploy migration first"
            )

        clauses = ["e.protocol_version IN (1, 2)"]
        parameters: list[str] = []
        if since:
            clauses.append("e.created_at >= ?")
            parameters.append(since.replace(tzinfo=None).isoformat(sep=" "))
        if cutoff:
            clauses.append("e.created_at <= ?")
            parameters.append(cutoff.replace(tzinfo=None).isoformat(sep=" "))
        rows = connection.execute(
            f"""
            SELECT e.*, r.fsrs_log_json
            FROM word_review_evidence AS e
            LEFT JOIN review_log AS r ON r.id = e.review_log_id
            WHERE {' AND '.join(clauses)}
            ORDER BY e.created_at, e.id
            """,
            parameters,
        ).fetchall()
    finally:
        connection.close()

    interaction_summary = None
    if interaction_log_dir:
        interaction_summary = summarize_interactions(
            _iter_interaction_events(interaction_log_dir),
            since=since,
            cutoff=cutoff,
        )
    result = summarize_rows(rows, interaction_summary)
    result["database"] = {
        "path": str(db_path.resolve()),
        "sha256": before_hash,
        "unchanged_after_analysis": _sha256(db_path) == before_hash,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--since", help="Inclusive ISO-8601 timestamp")
    parser.add_argument("--cutoff", help="Inclusive ISO-8601 timestamp")
    parser.add_argument("--interaction-log-dir", type=Path)
    parser.add_argument("--output", type=Path, help="Optional JSON output path")
    args = parser.parse_args()

    result = analyze(
        args.db,
        since=_parse_datetime(args.since),
        cutoff=_parse_datetime(args.cutoff),
        interaction_log_dir=args.interaction_log_dir,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
