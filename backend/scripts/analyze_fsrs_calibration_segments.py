#!/usr/bin/env python3
"""Read-only segmented calibration audit for due sentence-reading FSRS reviews."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from importlib.metadata import version as package_version
from pathlib import Path
from statistics import median

from fsrs import Card, Scheduler

try:
    from analyze_learning_system import (
        analyze_reviews,
        fetch_review_rows,
        file_identity,
        load_lemmas,
        open_read_only,
        parse_cli_datetime,
        parse_datetime,
        parse_json_object,
        rounded,
        stable_json_bytes,
    )
except ModuleNotFoundError:
    from scripts.analyze_learning_system import (
        analyze_reviews,
        fetch_review_rows,
        file_identity,
        load_lemmas,
        open_read_only,
        parse_cli_datetime,
        parse_datetime,
        parse_json_object,
        rounded,
        stable_json_bytes,
    )


DR095_START = parse_cli_datetime("2026-04-13T05:55:46Z")
RECOVERY_START = parse_cli_datetime("2026-07-08T00:00:00Z")


def band(value: float, edges: list[tuple[float, str]]) -> str:
    for upper, label in edges:
        if value < upper:
            return label
    return edges[-1][1]


def wilson(successes: int, count: int, z: float = 1.959963984540054) -> tuple:
    if not count:
        return None, None
    p = successes / count
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    spread = (
        z
        * math.sqrt((p * (1 - p) + z * z / (4 * count)) / count)
        / denominator
    )
    return rounded(center - spread), rounded(center + spread)


def summarize(dimension: str, group: str, items: list[dict]) -> dict:
    count = len(items)
    strict_successes = sum(item["strict_success"] for item in items)
    fsrs_recall_successes = sum(item["fsrs_recall"] for item in items)
    raw_rating2_plus = sum(item["raw_product_rating_ge_2"] for item in items)
    strict_low, strict_high = wilson(strict_successes, count)
    fsrs_low, fsrs_high = wilson(fsrs_recall_successes, count)
    predicted = sum(item["predicted"] for item in items) / count if count else None
    strict_observed = strict_successes / count if count else None
    fsrs_observed = fsrs_recall_successes / count if count else None
    return {
        "dimension": dimension,
        "group": group,
        "reviews": count,
        "strict_successes_rating_ge_3": strict_successes,
        "fsrs_recall_successes_applied_rating_ge_2": fsrs_recall_successes,
        "raw_product_rating_ge_2": raw_rating2_plus,
        "predicted_recall": rounded(predicted),
        "strict_observed_success": rounded(strict_observed),
        "strict_observed_wilson95_low": strict_low,
        "strict_observed_wilson95_high": strict_high,
        "strict_gap_observed_minus_predicted": rounded(
            strict_observed - predicted if count else None
        ),
        "strict_brier_score": rounded(
            sum(
                (item["predicted"] - item["strict_success"]) ** 2
                for item in items
            )
            / count if count else None
        ),
        "fsrs_observed_recall": rounded(fsrs_observed),
        "fsrs_observed_wilson95_low": fsrs_low,
        "fsrs_observed_wilson95_high": fsrs_high,
        "fsrs_calibration_gap_observed_minus_predicted": rounded(
            fsrs_observed - predicted if count else None
        ),
        "fsrs_brier_score": rounded(
            sum(
                (item["predicted"] - item["fsrs_recall"]) ** 2
                for item in items
            )
            / count if count else None
        ),
        "median_lateness_days": rounded(
            median(item["lateness_days"] for item in items) if count else None, 2
        ),
    }


def sql_time(value):
    return value.replace(tzinfo=None).isoformat(" ")


def resolve_applied_rating(
    product_rating: int, metadata: dict
) -> tuple[int, str]:
    """Return the FSRS rating actually applied, with a robust provenance label."""
    value = metadata.get("fsrs_rating_applied")
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 4:
        return value, "stamped"
    return product_rating, (
        "inferred_pre_v2"
        if "fsrs_rating_applied" not in metadata
        else "invalid_stamp_fallback"
    )


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

    scheduler = Scheduler(desired_retention=0.95)
    parameter_hash = hashlib.sha256(
        json.dumps(list(scheduler.parameters), separators=(",", ":")).encode()
    ).hexdigest()
    excluded = Counter()
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)

    connection = open_read_only(args.db)
    try:
        lemmas, canonical_roots = load_lemmas(connection)
        review_rows = fetch_review_rows(connection, args.window_start, args.cutoff)
        _summary, valid_rows, _warnings = analyze_reviews(
            review_rows, lemmas, canonical_roots
        )
        graduated_at = {
            int(row["lemma_id"]): parse_datetime(row["graduated_at"])
            for row in connection.execute(
                "SELECT lemma_id, graduated_at FROM user_lemma_knowledge"
            )
        }
        first_fsrs_after_graduation = {
            int(row["lemma_id"]): parse_datetime(row["first_reviewed_at"])
            for row in connection.execute(
                """
                SELECT r.lemma_id, MIN(r.reviewed_at) AS first_reviewed_at
                FROM review_log r
                JOIN user_lemma_knowledge u ON u.lemma_id = r.lemma_id
                WHERE r.is_acquisition = 0
                  AND u.graduated_at IS NOT NULL
                  AND r.reviewed_at > u.graduated_at
                GROUP BY r.lemma_id
                """
            )
        }
        for row in valid_rows:
            if row["sentence_id"] is None or (row["review_mode"] or "reading") != "reading":
                excluded["not_sentence_reading"] += 1
                continue
            if row["is_acquisition"]:
                excluded["acquisition"] += 1
                continue
            metadata = parse_json_object(row["fsrs_log_json"]) or {}
            pre_card = metadata.get("pre_card")
            if not isinstance(pre_card, dict):
                excluded["missing_pre_card"] += 1
                continue
            try:
                card = Card.from_dict(pre_card)
            except (KeyError, TypeError, ValueError):
                excluded["invalid_pre_card"] += 1
                continue
            reviewed_at = parse_datetime(row["reviewed_at"])
            if (
                reviewed_at is None
                or card.due is None
                or card.stability is None
                or card.last_review is None
            ):
                excluded["incomplete_pre_card"] += 1
                continue
            due_at = card.due
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=reviewed_at.tzinfo)
            if reviewed_at < due_at:
                excluded["not_due"] += 1
                continue
            try:
                predicted = float(
                    scheduler.get_card_retrievability(
                        card, current_datetime=reviewed_at
                    )
                )
            except (TypeError, ValueError):
                excluded["retrievability_error"] += 1
                continue
            product_rating = int(row["rating"])
            applied_rating, applied_rating_source = resolve_applied_rating(
                product_rating, metadata
            )
            lateness = (reviewed_at - due_at).total_seconds() / 86400
            item = {
                "predicted": predicted,
                # Scheduler-policy v2 stores product rating 2 but applies
                # FSRS Again. Use the applied rating whenever it is stamped;
                # pre-v2 unstamped rows used the raw Hard mapping.
                "strict_success": int(product_rating >= 3),
                "fsrs_recall": int(applied_rating >= 2),
                "raw_product_rating_ge_2": int(product_rating >= 2),
                "lateness_days": lateness,
            }
            if reviewed_at < DR095_START:
                epoch = "pre_dr095_proxy"
            elif reviewed_at < RECOVERY_START:
                epoch = "dr095_pre_recovery_proxy"
            else:
                epoch = "recovery_proxy"
            origin = (
                "post_acquisition"
                if graduated_at.get(row["lemma_id"])
                and reviewed_at > graduated_at[row["lemma_id"]]
                else "legacy_or_untracked_origin"
            )
            first_after = (
                "first_fsrs_after_graduation"
                if first_fsrs_after_graduation.get(row["lemma_id"]) == reviewed_at
                else "later_or_legacy_fsrs"
            )
            dimensions = {
                "overall": "all",
                "policy_epoch_proxy": epoch,
                "month": reviewed_at.strftime("%Y-%m"),
                "stability": band(
                    float(card.stability),
                    [(7, "<7d"), (30, "7-30d"), (math.inf, ">=30d")],
                ),
                "lateness": band(
                    lateness,
                    [(1, "0-1d"), (3, "1-3d"), (7, "3-7d"),
                     (14, "7-14d"), (math.inf, ">=14d")],
                ),
                "credit_type_diagnostic": row["credit_type"] or "unknown",
                "scheduler_policy_version": str(
                    metadata.get("fsrs_scheduler_policy_version", "unstamped")
                ),
                "applied_rating_source": applied_rating_source,
                "card_state": str(card.state),
                "origin": origin,
                "first_after_graduation": first_after,
                "predicted_probability": band(
                    predicted,
                    [(0.6, "<0.60"), (0.7, "0.60-0.70"),
                     (0.8, "0.70-0.80"), (0.9, "0.80-0.90"),
                     (math.inf, ">=0.90")],
                ),
            }
            for key, value in dimensions.items():
                groups[(key, value)].append(item)
    finally:
        connection.close()

    if file_identity(args.db) != before:
        parser.error("database changed during FSRS calibration audit")

    rows = [
        summarize(dimension, group, items)
        for (dimension, group), items in sorted(groups.items())
    ]
    overall = next(row for row in rows if row["dimension"] == "overall")
    result = {
        "schema_version": 2,
        "method_version": "fsrs-segmented-calibration-v2-policy-aware",
        "script": file_identity(Path(__file__).resolve()),
        "database": before,
        "baseline_summary": baseline_identity,
        "window": {
            "start": args.window_start.isoformat().replace("+00:00", "Z"),
            "cutoff": cutoff_text,
        },
        "scheduler_used_for_retrievability": {
            "library_version": package_version("fsrs"),
            "desired_retention": scheduler.desired_retention,
            "parameters_sha256": parameter_hash,
            "parameters": list(scheduler.parameters),
        },
        "overall": overall,
        "segments": rows,
        "excluded": dict(sorted(excluded.items())),
        "limitations": [
            "historical rows do not stamp library, parameter, or applied-rating identity",
            "policy epochs are date proxies, not per-review scheduler versions",
            "desired retention changes due dates but not the retrievability formula",
            "scheduler recall uses stamped applied rating>=2; unstamped pre-v2 rows infer the historical raw mapping",
            "strict unaided learning success remains raw product rating>=3",
            "only due reading/sentence FSRS reviews are calibration-eligible",
            "credit type is diagnostic; primary and collateral outcomes are equally valid",
            "observational calibration does not authorize parameter retuning",
        ],
    }
    args.output_dir.mkdir(parents=True)
    (args.output_dir / "fsrs_calibration_segments.json").write_bytes(
        stable_json_bytes(result)
    )
    with (args.output_dir / "segments.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({
        "excluded": result["excluded"],
        "overall": result["overall"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
