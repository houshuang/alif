#!/usr/bin/env python3
"""Read-only feasibility replay for a contextual-variable-retrieval pilot.

The proposed experiment randomizes an acquiring lemma to either reuse one
anchor sentence (constant cue) or rotate through semantically distinct
sentences (variable cues).  It never changes review timing or card count.  A
later, previously unseen sentence is reserved as a common transfer assessment.

Historical review order supplies acquisition volume and follow-up timing.  The
sentence pool at the pinned cutoff supplies prospective context capacity; it is
not assumed that every sentence existed at the historical review time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

from sqlalchemy import create_engine, event, or_
from sqlalchemy.orm import sessionmaker

from app.models import Lemma, ReviewLog, Sentence, SentenceWord
from app.services.canonical_resolution import resolve_canonical_via_map
from app.services.confusion_service import normalize_surface_form
from app.services.sentence_eligibility import reviewable_sentence_clauses
from app.services.sentence_validator import is_function_word_lemma


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


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def context_jaccard(left: set[int], right: set[int]) -> float:
    """Content-lemma overlap, with two empty contexts treated as identical."""
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)


def _normal_total_sample(
    baseline: float,
    uplift: float,
    power: float = 0.80,
) -> int:
    """Approximate equal-arm total N for a two-sided proportions test."""
    treatment = min(0.999, baseline + uplift)
    pooled = (baseline + treatment) / 2
    z_alpha = 1.959963984540054
    z_beta = 0.8416212335729143 if power == 0.80 else 0.8416212335729143
    numerator = (
        z_alpha * math.sqrt(2 * pooled * (1 - pooled))
        + z_beta
        * math.sqrt(
            baseline * (1 - baseline)
            + treatment * (1 - treatment)
        )
    ) ** 2
    return 2 * math.ceil(numerator / (uplift ** 2))


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--cutoff", required=True, type=_parse_datetime)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--expected-db-sha256")
    parser.add_argument("--max-context-jaccard", type=float, default=0.50)
    parser.add_argument("--assessment-min-days", type=int, default=3)
    parser.add_argument("--assessment-max-days", type=int, default=30)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    if not 0 <= args.max_context_jaccard <= 1:
        parser.error("max-context-jaccard must be between 0 and 1")
    if args.assessment_min_days < 1:
        parser.error("assessment-min-days must be positive")
    if args.assessment_max_days <= args.assessment_min_days:
        parser.error("assessment-max-days must exceed assessment-min-days")

    before_hash = _sha256(args.db)
    if args.expected_db_sha256 and before_hash != args.expected_db_sha256:
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
        parent_by_id = {
            lemma.lemma_id: lemma.canonical_lemma_id for lemma in lemmas
        }
        canonical_by_id = {
            lemma_id: resolve_canonical_via_map(lemma_id, parent_by_id)
            for lemma_id in parent_by_id
        }
        inert_ids = {
            lemma.lemma_id
            for lemma in lemmas
            if (
                lemma.word_category == "proper_name"
                or is_function_word_lemma(
                    lemma.lemma_ar_bare,
                    lemma.function_word_override,
                )
            )
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
        eligible_ids = {sentence.id for sentence in eligible_sentences}
        sentence_word_count: Counter[int] = Counter()
        forms_by_sentence_lemma: dict[tuple[int, int], set[str]] = defaultdict(set)
        content_by_sentence: dict[int, set[int]] = defaultdict(set)
        for sentence_id, lemma_id, surface in db.query(
            SentenceWord.sentence_id,
            SentenceWord.lemma_id,
            SentenceWord.surface_form,
        ).filter(SentenceWord.lemma_id.is_not(None)).all():
            if sentence_id not in eligible_ids:
                continue
            sentence_word_count[sentence_id] += 1
            canonical_id = canonical_by_id.get(lemma_id, lemma_id)
            forms_by_sentence_lemma[(sentence_id, canonical_id)].add(
                normalize_surface_form(surface)
            )
            if lemma_id not in inert_ids and canonical_id not in inert_ids:
                content_by_sentence[sentence_id].add(canonical_id)

        contexts_by_lemma_form: dict[tuple[int, str], set[int]] = defaultdict(set)
        for (sentence_id, lemma_id), forms in forms_by_sentence_lemma.items():
            if len(forms) == 1:
                contexts_by_lemma_form[(lemma_id, next(iter(forms)))].add(
                    sentence_id
                )

        reviews = (
            db.query(ReviewLog)
            .filter(
                ReviewLog.reviewed_at <= args.cutoff.replace(tzinfo=None),
                ReviewLog.review_mode == "reading",
                ReviewLog.sentence_id.is_not(None),
            )
            .order_by(ReviewLog.reviewed_at, ReviewLog.id)
            .all()
        )
        by_lemma: dict[int, list[ReviewLog]] = defaultdict(list)
        active_dates: set[str] = set()
        for row in reviews:
            canonical_id = canonical_by_id.get(row.lemma_id, row.lemma_id)
            by_lemma[canonical_id].append(row)
            at = _aware(row.reviewed_at)
            if at is not None:
                active_dates.add(at.date().isoformat())

        episodes: list[dict[str, Any]] = []
        all_completed_acquisition_episodes: list[dict[str, int]] = []
        distributed_confirmation_pairs: list[dict[str, bool]] = []
        for lemma_id, rows in by_lemma.items():
            current: list[ReviewLog] = []
            for index, row in enumerate(rows):
                if not row.is_acquisition:
                    continue
                current.append(row)
                metadata = _json_dict(row.fsrs_log_json)
                if metadata.get("graduated") is not True:
                    continue

                acquisition_rows = current
                current = []
                all_completed_acquisition_episodes.append({
                    "reviews": len(acquisition_rows),
                    "distinct_sentences": len({
                        acquisition_row.sentence_id
                        for acquisition_row in acquisition_rows
                    }),
                })
                successful_rows = [
                    acquisition_row
                    for acquisition_row in acquisition_rows
                    if acquisition_row.rating >= 3
                    and _aware(acquisition_row.reviewed_at) is not None
                ]
                for success_index, earlier_success in enumerate(
                    successful_rows[:-1]
                ):
                    earlier_at = _aware(earlier_success.reviewed_at)
                    later_success = next((
                        candidate
                        for candidate in successful_rows[success_index + 1:]
                        if _aware(candidate.reviewed_at).date()
                        != earlier_at.date()
                    ), None)
                    if later_success is None:
                        continue
                    earlier_forms = forms_by_sentence_lemma.get(
                        (earlier_success.sentence_id, lemma_id), set()
                    )
                    later_forms = forms_by_sentence_lemma.get(
                        (later_success.sentence_id, lemma_id), set()
                    )
                    distributed_confirmation_pairs.append({
                        "different_sentence": (
                            earlier_success.sentence_id
                            != later_success.sentence_id
                        ),
                        "same_surface": (
                            len(earlier_forms) == 1
                            and earlier_forms == later_forms
                        ),
                    })
                    break
                anchor = acquisition_rows[0]
                anchor_at = _aware(anchor.reviewed_at)
                graduated_at = _aware(row.reviewed_at)
                if anchor_at is None or graduated_at is None:
                    continue
                anchor_forms = forms_by_sentence_lemma.get(
                    (anchor.sentence_id, lemma_id), set()
                )
                if len(anchor_forms) != 1:
                    continue
                surface = next(iter(anchor_forms))
                all_contexts = contexts_by_lemma_form.get((lemma_id, surface), set())
                anchor_content = content_by_sentence.get(anchor.sentence_id, set()) - {
                    lemma_id
                }
                distinct_alternatives = [
                    sentence_id
                    for sentence_id in all_contexts
                    if (
                        sentence_id != anchor.sentence_id
                        and context_jaccard(
                            anchor_content,
                            content_by_sentence.get(sentence_id, set()) - {lemma_id},
                        ) <= args.max_context_jaccard
                        and abs(
                            sentence_word_count[sentence_id]
                            - sentence_word_count[anchor.sentence_id]
                        ) <= 3
                    )
                ]
                if len(distinct_alternatives) < 2:
                    continue

                lower = graduated_at + timedelta(days=args.assessment_min_days)
                upper = graduated_at + timedelta(days=args.assessment_max_days)
                outcome = None
                for later in rows[index + 1:]:
                    later_at = _aware(later.reviewed_at)
                    if later_at is None or later_at < lower:
                        continue
                    if later_at > upper:
                        break
                    later_forms = forms_by_sentence_lemma.get(
                        (later.sentence_id, lemma_id), set()
                    )
                    if (
                        not later.is_acquisition
                        and len(later_forms) == 1
                        and next(iter(later_forms)) == surface
                        and later.sentence_id != anchor.sentence_id
                    ):
                        outcome = later
                        break

                outcome_at = _aware(outcome.reviewed_at) if outcome else None
                episodes.append({
                    "lemma_id": lemma_id,
                    "anchor_at": anchor_at,
                    "graduated_at": graduated_at,
                    "acquisition_reviews": len(acquisition_rows),
                    "observed_acquisition_contexts": len({
                        acquisition_row.sentence_id
                        for acquisition_row in acquisition_rows
                    }),
                    "eligible_contexts": len(all_contexts),
                    "semantically_distinct_alternatives": len(
                        distinct_alternatives
                    ),
                    "outcome_delivered": outcome is not None,
                    "outcome_success": bool(outcome and outcome.rating >= 3),
                    "outcome_delay_days": (
                        (outcome_at - graduated_at).total_seconds() / 86400
                        if outcome_at is not None else None
                    ),
                })

    finally:
        db.close()
        engine.dispose()

    delivered = [episode for episode in episodes if episode["outcome_delivered"]]
    successful = [episode for episode in delivered if episode["outcome_success"]]
    cohort_dates = {
        episode["anchor_at"].date().isoformat() for episode in episodes
    }
    first_date = min(cohort_dates, default=None)
    active_cohort_dates = {
        date for date in active_dates if first_date is not None and date >= first_date
    }
    rate = (
        len(episodes) / len(active_cohort_dates)
        if active_cohort_dates else 0.0
    )
    baseline_itt = len(successful) / len(episodes) if episodes else 0.0
    baseline_delivered = (
        len(successful) / len(delivered) if delivered else 0.0
    )
    power_rows = []
    for uplift in (0.03, 0.05, 0.10, 0.15):
        total = _normal_total_sample(baseline_itt, uplift)
        power_rows.append({
            "absolute_uplift": uplift,
            "total_mature_episodes_for_80pct_power": total,
            "active_learning_days_at_observed_rate": (
                math.ceil(total / rate) if rate > 0 else None
            ),
            "elapsed_days_including_assessment_window": (
                math.ceil(total / rate) + args.assessment_max_days
                if rate > 0 else None
            ),
        })

    # Faster micro-randomized design: at an ordinary non-acquisition review,
    # choose between the most recent successful same-form sentence (constant)
    # and one not previously seen for the lemma (variable).  Do not open a new
    # episode for that lemma until the next 1-14 day word review has supplied
    # the causal retention endpoint or the window has expired.
    micro_episodes: list[dict[str, Any]] = []
    for lemma_id, rows in by_lemma.items():
        seen_sentence_ids: set[int] = set()
        last_success_by_surface: dict[str, int] = {}
        index = 0
        while index < len(rows):
            row = rows[index]
            row_at = _aware(row.reviewed_at)
            forms = forms_by_sentence_lemma.get((row.sentence_id, lemma_id), set())
            surface = next(iter(forms)) if len(forms) == 1 else None
            anchor_id = last_success_by_surface.get(surface) if surface else None
            novel_options: list[int] = []
            if (
                row_at is not None
                and not row.is_acquisition
                and surface is not None
                and anchor_id is not None
                and anchor_id in eligible_ids
            ):
                anchor_content = content_by_sentence.get(anchor_id, set()) - {
                    lemma_id
                }
                novel_options = [
                    sentence_id
                    for sentence_id in contexts_by_lemma_form.get(
                        (lemma_id, surface), set()
                    )
                    if (
                        sentence_id not in seen_sentence_ids
                        and sentence_id != anchor_id
                        and context_jaccard(
                            anchor_content,
                            content_by_sentence.get(sentence_id, set()) - {
                                lemma_id
                            },
                        ) <= args.max_context_jaccard
                        and abs(
                            sentence_word_count[sentence_id]
                            - sentence_word_count[anchor_id]
                        ) <= 3
                    )
                ]

            if not novel_options:
                if surface is not None:
                    seen_sentence_ids.add(row.sentence_id)
                    if row.rating >= 3:
                        last_success_by_surface[surface] = row.sentence_id
                index += 1
                continue

            outcome = None
            outcome_index = None
            lower = row_at + timedelta(days=1)
            upper = row_at + timedelta(days=14)
            for later_index in range(index + 1, len(rows)):
                later = rows[later_index]
                later_at = _aware(later.reviewed_at)
                if later_at is None or later_at < lower:
                    continue
                if later_at > upper:
                    break
                if not later.is_acquisition:
                    outcome = later
                    outcome_index = later_index
                    break
            micro_episodes.append({
                "lemma_id": lemma_id,
                "assigned_at": row_at,
                "surface": surface,
                "novel_option_count": len(novel_options),
                "outcome_delivered": outcome is not None,
                "outcome_success": bool(outcome and outcome.rating >= 3),
                "outcome_delay_days": (
                    (_aware(outcome.reviewed_at) - row_at).total_seconds() / 86400
                    if outcome is not None else None
                ),
            })

            # Update the actually observed decision row before moving forward;
            # it remains part of the historical seen-context trajectory.
            seen_sentence_ids.add(row.sentence_id)
            if row.rating >= 3:
                last_success_by_surface[surface] = row.sentence_id
            if outcome_index is None:
                index += 1
            else:
                for skipped in rows[index + 1:outcome_index]:
                    skipped_forms = forms_by_sentence_lemma.get(
                        (skipped.sentence_id, lemma_id), set()
                    )
                    if len(skipped_forms) != 1:
                        continue
                    skipped_surface = next(iter(skipped_forms))
                    seen_sentence_ids.add(skipped.sentence_id)
                    if skipped.rating >= 3:
                        last_success_by_surface[skipped_surface] = (
                            skipped.sentence_id
                        )
                # The outcome closes the episode. Process it normally on the
                # next loop so it can become the anchor for a future episode.
                index = outcome_index

    micro_delivered = [
        episode for episode in micro_episodes if episode["outcome_delivered"]
    ]
    micro_successful = [
        episode for episode in micro_delivered if episode["outcome_success"]
    ]
    micro_dates = {
        episode["assigned_at"].date().isoformat()
        for episode in micro_episodes
    }
    micro_first = min(micro_dates, default=None)
    micro_active_dates = {
        date for date in active_dates
        if micro_first is not None and date >= micro_first
    }
    micro_rate = (
        len(micro_episodes) / len(micro_active_dates)
        if micro_active_dates else 0.0
    )
    micro_itt = (
        len(micro_successful) / len(micro_episodes)
        if micro_episodes else 0.0
    )
    micro_power = []
    for uplift in (0.02, 0.03, 0.05, 0.10):
        total = _normal_total_sample(micro_itt, uplift)
        micro_power.append({
            "absolute_uplift": uplift,
            "total_mature_episodes_for_80pct_power": total,
            "active_learning_days_at_observed_rate": (
                math.ceil(total / micro_rate) if micro_rate > 0 else None
            ),
            "elapsed_days_including_outcome_window": (
                math.ceil(total / micro_rate) + 14
                if micro_rate > 0 else None
            ),
        })

    context_events: list[dict[str, Any]] = []
    distinct_counts: list[int] = []
    for lemma_rows in by_lemma.values():
        seen: set[int] = set()
        previous_sentence_id = None
        previous_rating = None
        for row in lemma_rows:
            row_at = _aware(row.reviewed_at)
            if row_at is None:
                continue
            # Acquisition sentences are part of the learner's context history
            # even though only later, non-acquisition reviews are summarized.
            if not row.is_acquisition and seen:
                context_events.append({
                    "reviewed_at": row_at,
                    "new_for_lemma": row.sentence_id not in seen,
                    "immediate_repeat": row.sentence_id == previous_sentence_id,
                    "previous_success": previous_rating is not None
                    and previous_rating >= 3,
                    "success": row.rating >= 3,
                })
            seen.add(row.sentence_id)
            previous_sentence_id = row.sentence_id
            previous_rating = row.rating
        if seen:
            distinct_counts.append(len(seen))

    recent_cutoff = args.cutoff - timedelta(days=30)
    recent_context_events = [
        row for row in context_events if row["reviewed_at"] >= recent_cutoff
    ]

    def _context_policy_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        after_success = [row for row in rows if row["previous_success"]]
        after_failure = [row for row in rows if not row["previous_success"]]
        new_context = [row for row in rows if row["new_for_lemma"]]
        familiar_context = [row for row in rows if not row["new_for_lemma"]]
        return {
            "eligible_following_reviews": len(rows),
            "new_sentence_for_lemma_fraction": round(
                sum(row["new_for_lemma"] for row in rows) / len(rows), 4
            ) if rows else None,
            "immediate_same_sentence_repeat_fraction": round(
                sum(row["immediate_repeat"] for row in rows) / len(rows), 4
            ) if rows else None,
            "repeat_after_previous_success_fraction": round(
                sum(row["immediate_repeat"] for row in after_success)
                / len(after_success),
                4,
            ) if after_success else None,
            "repeat_after_previous_failure_fraction": round(
                sum(row["immediate_repeat"] for row in after_failure)
                / len(after_failure),
                4,
            ) if after_failure else None,
            "new_context_success": {
                "n": len(new_context),
                "rate": round(
                    sum(row["success"] for row in new_context)
                    / len(new_context),
                    4,
                ) if new_context else None,
            },
            "familiar_context_success": {
                "n": len(familiar_context),
                "rate": round(
                    sum(row["success"] for row in familiar_context)
                    / len(familiar_context),
                    4,
                ) if familiar_context else None,
            },
        }

    context_counts = [
        float(episode["semantically_distinct_alternatives"])
        for episode in episodes
    ]
    acquisition_counts = [
        float(episode["acquisition_reviews"]) for episode in episodes
    ]
    delays = [
        float(episode["outcome_delay_days"])
        for episode in delivered
    ]
    repeated_acquisition_episodes = [
        episode for episode in all_completed_acquisition_episodes
        if episode["reviews"] >= 2
    ]
    output = {
        "schema_version": 1,
        "method": "context-diversity-acquisition-feasibility-v1",
        "database": {"filename": args.db.name, "sha256": before_hash},
        "cutoff": args.cutoff.isoformat(),
        "proposed_estimand": (
            "variable minus constant acquisition cue policy on successful "
            "same-form retrieval in a previously unseen assessment sentence"
        ),
        "eligibility": {
            "same_normalized_surface_across_contexts": True,
            "minimum_semantically_distinct_alternatives": 2,
            "maximum_content_lemma_jaccard": args.max_context_jaccard,
            "maximum_word_count_difference": 3,
            "non_passage_reviewable_sentences_only": True,
        },
        "prospective_capacity": {
            "qualifying_completed_acquisition_episodes": len(episodes),
            "active_days_in_cohort_window": len(active_cohort_dates),
            "episodes_per_active_learning_day": round(rate, 3),
            "median_acquisition_reviews": round(median(acquisition_counts), 2)
            if acquisition_counts else None,
            "p90_acquisition_reviews": round(
                _percentile(acquisition_counts, 0.9), 2
            ) if acquisition_counts else None,
            "median_distinct_alternatives": round(median(context_counts), 2)
            if context_counts else None,
            "p10_distinct_alternatives": round(
                _percentile(context_counts, 0.1), 2
            ) if context_counts else None,
        },
        "historical_neutral_assessment_proxy": {
            "window_days_after_graduation": [
                args.assessment_min_days,
                args.assessment_max_days,
            ],
            "delivered": len(delivered),
            "delivery_fraction": round(
                len(delivered) / len(episodes), 4
            ) if episodes else None,
            "successful": len(successful),
            "successful_itt_rate": round(baseline_itt, 4)
            if episodes else None,
            "success_rate_among_delivered": round(baseline_delivered, 4)
            if delivered else None,
            "median_delay_days": round(median(delays), 2) if delays else None,
        },
        "sample_size_projection": power_rows,
        "micro_randomized_alternative": {
            "estimand": (
                "variable versus familiar same-form context on success at the "
                "next 1-14 day non-acquisition word review"
            ),
            "qualifying_nonoverlapping_episodes": len(micro_episodes),
            "active_days_in_cohort_window": len(micro_active_dates),
            "episodes_per_active_learning_day": round(micro_rate, 3),
            "outcome_delivered": len(micro_delivered),
            "outcome_delivery_fraction": round(
                len(micro_delivered) / len(micro_episodes), 4
            ) if micro_episodes else None,
            "successful_outcomes": len(micro_successful),
            "successful_itt_rate": round(micro_itt, 4)
            if micro_episodes else None,
            "median_novel_options": round(median(
                episode["novel_option_count"] for episode in micro_episodes
            ), 2) if micro_episodes else None,
            "sample_size_projection": micro_power,
            "interpretation": (
                "The randomized review is the intervention; its immediate "
                "rating measures context dependence, while the next review "
                "is the retention endpoint."
            ),
        },
        "observed_context_use": {
            "all_completed_acquisition_episodes": len(
                all_completed_acquisition_episodes
            ),
            "all_acquisition_mean_reviews": round(mean(
                episode["reviews"]
                for episode in all_completed_acquisition_episodes
            ), 2) if all_completed_acquisition_episodes else None,
            "all_acquisition_mean_distinct_sentences": round(mean(
                episode["distinct_sentences"]
                for episode in all_completed_acquisition_episodes
            ), 2) if all_completed_acquisition_episodes else None,
            "all_acquisition_single_sentence_fraction": round(
                sum(
                    episode["distinct_sentences"] == 1
                    for episode in all_completed_acquisition_episodes
                ) / len(all_completed_acquisition_episodes),
                4,
            ) if all_completed_acquisition_episodes else None,
            "repeated_acquisition_episodes": len(
                repeated_acquisition_episodes
            ),
            "repeated_acquisition_single_sentence_fraction": round(
                sum(
                    episode["distinct_sentences"] == 1
                    for episode in repeated_acquisition_episodes
                ) / len(repeated_acquisition_episodes),
                4,
            ) if repeated_acquisition_episodes else None,
            "repeated_acquisition_distinct_sentence_ratio": round(mean(
                episode["distinct_sentences"] / episode["reviews"]
                for episode in repeated_acquisition_episodes
            ), 4) if repeated_acquisition_episodes else None,
            "historical_distributed_confirmation_pairs": len(
                distributed_confirmation_pairs
            ),
            "distributed_confirmation_different_sentence_fraction": round(
                sum(
                    row["different_sentence"]
                    for row in distributed_confirmation_pairs
                ) / len(distributed_confirmation_pairs),
                4,
            ) if distributed_confirmation_pairs else None,
            "distributed_confirmation_different_sentence_same_surface_fraction": round(
                sum(
                    row["different_sentence"] and row["same_surface"]
                    for row in distributed_confirmation_pairs
                ) / len(distributed_confirmation_pairs),
                4,
            ) if distributed_confirmation_pairs else None,
            "mean_distinct_acquisition_sentences": round(mean(
                episode["observed_acquisition_contexts"] for episode in episodes
            ), 2) if episodes else None,
            "single_sentence_acquisition_fraction": round(
                sum(
                    episode["observed_acquisition_contexts"] == 1
                    for episode in episodes
                ) / len(episodes),
                4,
            ) if episodes else None,
        },
        "observed_mature_context_policy": {
            "all_history": _context_policy_summary(context_events),
            "last_30_days": _context_policy_summary(recent_context_events),
            "median_distinct_sentences_per_reviewed_lemma": round(
                median(distinct_counts), 2
            ) if distinct_counts else None,
            "p90_distinct_sentences_per_reviewed_lemma": round(
                _percentile([float(value) for value in distinct_counts], 0.9),
                2,
            ) if distinct_counts else None,
        },
        "design_recommendation": {
            "arms": {
                "constant": "reuse one anchor sentence during acquisition",
                "variable": "prefer a not-yet-used semantically distinct sentence",
            },
            "workload_matched": True,
            "review_timing_unchanged": True,
            "assessment": (
                "reserve one previously unseen same-form sentence when the "
                "graduated lemma next becomes due"
            ),
            "activate_before_capacity_gate": False,
        },
        "limitations": [
            "The cutoff sentence pool is used for historical serviceability.",
            "Historical follow-up was not experimentally reserved and therefore underestimates prospective assessment delivery.",
            "The replay estimates capacity and power, not the treatment effect.",
            "Context distinctness uses content-lemma overlap, not a semantic embedding.",
        ],
    }
    if _sha256(args.db) != before_hash:
        parser.error("database changed during replay")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "capacity": output["prospective_capacity"],
        "assessment": output["historical_neutral_assessment_proxy"],
        "power": output["sample_size_projection"],
        "micro": output["micro_randomized_alternative"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
