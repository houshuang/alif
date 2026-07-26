#!/usr/bin/env python3
"""Deterministic, read-only S0/S1 selector replay over one pinned snapshot.

This is deliberately a bounded snapshot replay, not a historical state
reconstruction. Paired requests use identical inputs. Successive rounds exclude
the union of sentences returned by both arms to stress quota filling as the
serviceable cache is depleted. No reviews are submitted and intro mutations are
disabled.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

try:
    from analyze_learning_system import file_identity, stable_json_bytes
except ModuleNotFoundError:  # imported as backend.scripts.replay_selector_s1
    from scripts.analyze_learning_system import file_identity, stable_json_bytes
from app.services import sentence_eligibility
from app.services.sentence_selector import (
    SELECTOR_POLICY_S0,
    SELECTOR_POLICY_S1,
    SELECTOR_POLICY_S1B,
    SELECTOR_POLICY_RECOVERY,
    build_session,
)


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def summarize(
    result: dict[str, Any],
    limit: int,
    opening_quota: int = 5,
) -> dict[str, Any]:
    items = result.get("items") or []
    due_ids: set[int] = set()
    all_word_ids: set[int] = set()
    base_due_ids: set[int] = set()
    base_all_word_ids: set[int] = set()
    sentence_ids: set[int] = set()
    reasons: dict[str, int] = {}
    quality_values: list[float] = []
    for item in items:
        sentence_ids.update(item.get("sentence_ids") or [item.get("sentence_id")])
        info = item.get("selection_info") or {}
        reason = info.get("reason") or "unknown"
        reasons[reason] = reasons.get(reason, 0) + 1
        item_due_ids = set(info.get("due_lemma_ids") or [])
        due_ids.update(item_due_ids)
        if reason != "acquisition_repeat":
            base_due_ids.update(item_due_ids)
        quality = (info.get("components") or {}).get("quality_multiplier")
        if isinstance(quality, (int, float)):
            quality_values.append(float(quality))
        for word in item.get("words") or []:
            if word.get("is_function_word") or word.get("is_proper_name"):
                continue
            lemma_id = word.get("canonical_lemma_id") or word.get("lemma_id")
            if isinstance(lemma_id, int):
                all_word_ids.add(lemma_id)
                if reason != "acquisition_repeat":
                    base_all_word_ids.add(lemma_id)
        for passage_sentence in item.get("passage_sentences") or []:
            for word in passage_sentence.get("words") or []:
                if word.get("is_function_word") or word.get("is_proper_name"):
                    continue
                lemma_id = (
                    word.get("canonical_lemma_id") or word.get("lemma_id")
                )
                if isinstance(lemma_id, int):
                    all_word_ids.add(lemma_id)
                    if reason != "acquisition_repeat":
                        base_all_word_ids.add(lemma_id)
    opening = sum(
        count for reason, count in reasons.items()
        if reason in {
            "frequency_due_first",
            "frequency_due_first_s1",
            "frequency_due_first_s1b",
        }
    )
    recovery_ids = sorted({
        int(primary_lemma_id)
        for item in items
        if (
            (item.get("selection_info") or {}).get("reason")
            == "established_lapse_recovery_v1"
        )
        for primary_lemma_id in [item.get("primary_lemma_id")]
        if isinstance(primary_lemma_id, int)
    })
    return {
        "requested_base_limit": limit,
        "returned_cards_including_repetitions": len(items),
        "base_cards_before_acquisition_repetitions": (
            len(items) - reasons.get("acquisition_repeat", 0)
        ),
        "distinct_due_words_covered": len(due_ids),
        "distinct_all_words_presented": len(all_word_ids),
        "base_distinct_due_words_covered": len(base_due_ids),
        "base_distinct_all_words_presented": len(base_all_word_ids),
        "opening_cards": opening,
        "opening_quota": min(opening_quota, limit),
        "opening_quota_filled": opening >= min(opening_quota, limit),
        "established_lapse_recovery_cards": reasons.get(
            "established_lapse_recovery_v1",
            0,
        ),
        "established_lapse_recovery_lemma_ids": recovery_ids,
        "mean_quality_multiplier": round(mean(quality_values), 4) if quality_values else None,
        "selection_reasons": dict(sorted(reasons.items())),
        "sentence_ids": sorted(sid for sid in sentence_ids if sid),
        "due_lemma_ids": sorted(due_ids),
        "total_due_words_reported": result.get("total_due_words"),
    }


def compare(s0: dict[str, Any], s1: dict[str, Any]) -> dict[str, Any]:
    ids0 = set(s0["sentence_ids"])
    ids1 = set(s1["sentence_ids"])
    union = ids0 | ids1
    return {
        "due_coverage_delta": (
            s1["distinct_due_words_covered"] - s0["distinct_due_words_covered"]
        ),
        "all_word_breadth_delta": (
            s1["distinct_all_words_presented"] - s0["distinct_all_words_presented"]
        ),
        "base_due_coverage_delta": (
            s1["base_distinct_due_words_covered"]
            - s0["base_distinct_due_words_covered"]
        ),
        "base_all_word_breadth_delta": (
            s1["base_distinct_all_words_presented"]
            - s0["base_distinct_all_words_presented"]
        ),
        "returned_cards_delta": (
            s1["returned_cards_including_repetitions"]
            - s0["returned_cards_including_repetitions"]
        ),
        "opening_cards_delta": s1["opening_cards"] - s0["opening_cards"],
        "sentence_set_jaccard": round(len(ids0 & ids1) / len(union), 4) if union else 1.0,
    }


def public_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Remove stable learner/content identifiers from committed replay output."""
    private_keys = {
        "sentence_ids",
        "due_lemma_ids",
        "established_lapse_recovery_lemma_ids",
    }
    return {
        key: value
        for key, value in summary.items()
        if key not in private_keys
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--cutoff", type=parse_datetime, required=True)
    parser.add_argument("--current-baseline-summary", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limits", default="5,10,20")
    parser.add_argument("--depletion-rounds", type=int, default=4)
    parser.add_argument(
        "--candidate-policy",
        choices=[
            SELECTOR_POLICY_S1,
            SELECTOR_POLICY_S1B,
            SELECTOR_POLICY_RECOVERY,
        ],
        default=SELECTOR_POLICY_S1,
    )
    parser.add_argument(
        "--mapping-verification-cutoff",
        type=parse_datetime,
        help=(
            "Pin historical reviewability semantics. Defaults to the current "
            "code constant."
        ),
    )
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("output-dir already exists")
    limits = [int(value) for value in args.limits.split(",")]
    if not limits or any(value < 1 for value in limits) or args.depletion_rounds < 1:
        parser.error("limits and depletion-rounds must be positive")
    mapping_cutoff = args.mapping_verification_cutoff
    if mapping_cutoff is not None:
        sentence_eligibility.MAPPING_VERIFICATION_MIN_AT = (
            mapping_cutoff.replace(tzinfo=None)
        )

    db_before = file_identity(args.db)
    baseline_identity = file_identity(args.current_baseline_summary)
    baseline = json.loads(args.current_baseline_summary.read_bytes())
    if baseline["provenance"]["database"]["sha256"] != db_before["sha256"]:
        parser.error("baseline summary and database snapshot do not match")
    cutoff_text = args.cutoff.isoformat().replace("+00:00", "Z")
    if baseline["window"]["cutoff"] != cutoff_text:
        parser.error("baseline summary and replay cutoff do not match")

    url = f"sqlite:///file:{args.db.resolve()}?mode=ro&immutable=1&uri=true"
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _query_only(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA query_only=ON")

    session = sessionmaker(bind=engine)()
    candidate_policy = args.candidate_policy
    rows: list[dict[str, Any]] = []
    try:
        for limit in limits:
            excluded: set[int] = set()
            for round_number in range(args.depletion_rounds):
                arms = {}
                for policy in (SELECTOR_POLICY_S0, candidate_policy):
                    raw = build_session(
                        session,
                        limit=limit,
                        mode="reading",
                        log_events=False,
                        exclude_sentence_ids=set(excluded),
                        allow_intro_mutations=False,
                        selector_policy=policy,
                        at=args.cutoff,
                    )
                    arms[policy] = summarize(
                        raw,
                        limit,
                        opening_quota=(
                            3
                            if policy == SELECTOR_POLICY_S1B
                            else 5
                        ),
                    )
                comparison = compare(arms[SELECTOR_POLICY_S0], arms[candidate_policy])
                rows.append({
                    "limit": limit,
                    "depletion_round": round_number,
                    "excluded_sentence_count": len(excluded),
                    "s0": arms[SELECTOR_POLICY_S0],
                    "candidate": arms[candidate_policy],
                    "comparison": comparison,
                })
                excluded.update(arms[SELECTOR_POLICY_S0]["sentence_ids"])
                excluded.update(arms[candidate_policy]["sentence_ids"])
    finally:
        session.close()
        engine.dispose()

    if file_identity(args.db) != db_before:
        parser.error("database changed during replay")
    regressions = [
        row for row in rows
        if row["comparison"]["due_coverage_delta"] < 0
        or row["comparison"]["base_due_coverage_delta"] < 0
        or row["comparison"]["all_word_breadth_delta"] < 0
        or row["comparison"]["base_all_word_breadth_delta"] < 0
        or row["comparison"]["returned_cards_delta"] > 0
        or (
            row["s0"]["mean_quality_multiplier"] is not None
            and row["candidate"]["mean_quality_multiplier"] is not None
            and row["candidate"]["mean_quality_multiplier"]
            < row["s0"]["mean_quality_multiplier"]
        )
    ]
    result = {
        "schema_version": 3,
        "method_version": "selector-candidate-bounded-snapshot-v3",
        "candidate_policy": candidate_policy,
        "script": file_identity(Path(__file__).resolve()),
        "dependencies": {
            "sentence_selector": file_identity(
                Path(__file__).resolve().parents[1]
                / "app" / "services" / "sentence_selector.py"
            ),
            "sentence_eligibility": file_identity(
                Path(sentence_eligibility.__file__).resolve()
            ),
        },
        "database": {"filename": db_before["filename"]},
        "baseline_summary": {"filename": baseline_identity["filename"]},
        "cutoff": cutoff_text,
        "scope": {
            "historical_state_reconstruction": False,
            "review_submission": False,
            "intro_mutations": False,
            "all_sentence_words_count_equally": True,
            "depletion_rule": "exclude union of prior S0 and S1 sentence IDs",
            "mapping_verification_cutoff": (
                mapping_cutoff.isoformat().replace("+00:00", "Z")
                if mapping_cutoff is not None
                else sentence_eligibility.MAPPING_VERIFICATION_MIN_AT.isoformat()
            ),
        },
        "rows": [
            {
                **{
                    key: value
                    for key, value in row.items()
                    if key not in {"s0", "candidate"}
                },
                "s0": public_summary(row["s0"]),
                "candidate": public_summary(row["candidate"]),
            }
            for row in rows
        ],
        "aggregate": {
            "paired_requests": len(rows),
            "requests_with_due_coverage_regression": sum(
                row["comparison"]["due_coverage_delta"] < 0 for row in rows
            ),
            "requests_with_due_coverage_gain": sum(
                row["comparison"]["due_coverage_delta"] > 0 for row in rows
            ),
            "requests_with_base_due_coverage_regression": sum(
                row["comparison"]["base_due_coverage_delta"] < 0 for row in rows
            ),
            "requests_with_returned_card_increase": sum(
                row["comparison"]["returned_cards_delta"] > 0 for row in rows
            ),
            "requests_with_all_word_breadth_regression": sum(
                row["comparison"]["all_word_breadth_delta"] < 0 for row in rows
            ),
            "requests_with_base_all_word_breadth_regression": sum(
                row["comparison"]["base_all_word_breadth_delta"] < 0 for row in rows
            ),
            "requests_serving_established_lapse_recovery": sum(
                row["candidate"]["established_lapse_recovery_cards"] > 0
                for row in rows
            ),
            "distinct_established_lapse_recovery_lemmas": len({
                lemma_id
                for row in rows
                for lemma_id in row["candidate"][
                    "established_lapse_recovery_lemma_ids"
                ]
            }),
            "mean_due_coverage_delta": round(
                mean(row["comparison"]["due_coverage_delta"] for row in rows), 4
            ),
            "mean_base_due_coverage_delta": round(
                mean(row["comparison"]["base_due_coverage_delta"] for row in rows), 4
            ),
            "mean_returned_cards_delta": round(
                mean(row["comparison"]["returned_cards_delta"] for row in rows), 4
            ),
            "mean_all_word_breadth_delta": round(
                mean(row["comparison"]["all_word_breadth_delta"] for row in rows), 4
            ),
            "mean_base_all_word_breadth_delta": round(
                mean(row["comparison"]["base_all_word_breadth_delta"] for row in rows), 4
            ),
            "s0_opening_quotas_filled": sum(row["s0"]["opening_quota_filled"] for row in rows),
            "candidate_opening_quotas_filled": sum(
                row["candidate"]["opening_quota_filled"] for row in rows
            ),
        },
        "verdict": (
            "do_not_shadow"
            if regressions
            else "eligible_for_historical_reconstruction_then_shadow"
        ),
        "limitations": [
            "one pinned end-state snapshot, not historical session-state reconstruction",
            "does not estimate retention or causal learning effects",
            "does not model review answers or state transitions between requests",
            "depletion rounds are serviceability stress tests, not learner sessions",
        ],
    }

    args.output_dir.mkdir(parents=True)
    with (args.output_dir / "replay.json.gz").open("wb") as raw_handle:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw_handle,
            mtime=0,
        ) as gzip_handle:
            gzip_handle.write(stable_json_bytes(result))
    with (args.output_dir / "paired_requests.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "limit", "depletion_round", "excluded_sentence_count",
                "s0_due", "candidate_due", "due_delta",
                "s0_all_words", "candidate_all_words",
                "all_words_delta", "base_due_delta", "returned_cards_delta",
                "base_all_words_delta",
                "s0_opening", "candidate_opening", "jaccard",
            ],
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "limit": row["limit"],
                "depletion_round": row["depletion_round"],
                "excluded_sentence_count": row["excluded_sentence_count"],
                "s0_due": row["s0"]["distinct_due_words_covered"],
                "candidate_due": row["candidate"]["distinct_due_words_covered"],
                "due_delta": row["comparison"]["due_coverage_delta"],
                "s0_all_words": row["s0"]["distinct_all_words_presented"],
                "candidate_all_words": row["candidate"]["distinct_all_words_presented"],
                "all_words_delta": row["comparison"]["all_word_breadth_delta"],
                "base_due_delta": row["comparison"]["base_due_coverage_delta"],
                "returned_cards_delta": row["comparison"]["returned_cards_delta"],
                "base_all_words_delta": row["comparison"]["base_all_word_breadth_delta"],
                "s0_opening": row["s0"]["opening_cards"],
                "candidate_opening": row["candidate"]["opening_cards"],
                "jaccard": row["comparison"]["sentence_set_jaccard"],
            })
    checksum_lines = []
    for name in ("paired_requests.csv", "replay.json.gz"):
        digest = hashlib.sha256((args.output_dir / name).read_bytes()).hexdigest()
        checksum_lines.append(f"{digest}  {name}")
    (args.output_dir / "SHA256SUMS").write_text(
        "\n".join(checksum_lines) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["aggregate"], sort_keys=True))
    print(f"Verdict: {result['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
