"""Fast readout for the embedded short-story maintenance experiment.

Measures the outcomes that can move within days: supply diversity, target
coverage/repetition, morphology delivery, cards shown, clean reading speed,
immediate comprehension, and selected-target ratings. Delayed retention remains
an explicitly later endpoint.

Read-only. Intended to run on production:

    python scripts/analyze_short_story_experiment.py --days 3
"""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DB = Path("/opt/alif/backend/data/alif.db")
DEFAULT_LOG_DIR = Path("/opt/alif/backend/data/logs")
EXPERIMENT_VERSION = "clustered_short_stories_v2"
IDLE_CUTOFF_MS = 20 * 60 * 1000


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iter_events(log_dir: Path, cutoff: datetime) -> Iterable[dict[str, Any]]:
    for path in sorted(log_dir.glob("interactions_*.jsonl*")):
        opener = gzip.open if path.suffix == ".gz" else open
        try:
            handle = opener(path, "rt", encoding="utf-8")
        except OSError:
            continue
        with handle:
            for raw_line in handle:
                try:
                    event = json.loads(raw_line)
                except (TypeError, json.JSONDecodeError):
                    continue
                timestamp = _parse_ts(event.get("ts"))
                if timestamp and timestamp >= cutoff:
                    yield event


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def analyze(db_path: Path, log_dir: Path, days: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")

    story_rows = conn.execute(
        """
        SELECT id, created_at, metadata_json
        FROM stories
        WHERE format_type = 'maintenance_passage'
        ORDER BY created_at
        """
    ).fetchall()
    experiment_stories: dict[int, dict[str, Any]] = {}
    first_sentence_to_story: dict[int, int] = {}
    all_sentence_to_story: dict[int, int] = {}
    story_word_counts: dict[int, int] = {}
    for row in story_rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        if metadata.get("experiment_version") != EXPERIMENT_VERSION:
            continue
        created_at = _parse_ts(row["created_at"])
        sentence_rows = conn.execute(
            "SELECT id FROM sentences WHERE story_id=? ORDER BY id",
            (row["id"],),
        ).fetchall()
        sentence_ids = [int(item[0]) for item in sentence_rows]
        if not sentence_ids:
            continue
        metadata["created_at"] = created_at
        metadata["sentence_ids"] = sentence_ids
        experiment_stories[int(row["id"])] = metadata
        first_sentence_to_story[sentence_ids[0]] = int(row["id"])
        all_sentence_to_story.update({sentence_id: int(row["id"]) for sentence_id in sentence_ids})
        placeholders = ",".join("?" for _ in sentence_ids)
        story_word_counts[int(row["id"])] = int(conn.execute(
            f"SELECT COUNT(*) FROM sentence_words WHERE sentence_id IN ({placeholders})",
            sentence_ids,
        ).fetchone()[0])

    recent_stories = {
        story_id: metadata
        for story_id, metadata in experiment_stories.items()
        if metadata.get("created_at") and metadata["created_at"] >= cutoff
    }
    modes = Counter()
    target_counts = Counter()
    target_slots = 0
    repeated_target_slots = 0
    varied_surface_slots = 0
    morphology_stories = 0
    morphology_delivered = 0
    for metadata in recent_stories.values():
        modes[str(metadata.get("narrative_mode") or "<missing>")] += 1
        if metadata.get("morphology_focus"):
            morphology_stories += 1
        occurrence_counts = metadata.get("target_occurrence_counts") or {}
        surface_counts = metadata.get("target_surface_form_counts") or {}
        for raw_id in metadata.get("target_lemma_ids") or []:
            lemma_id = int(raw_id)
            target_counts[lemma_id] += 1
            target_slots += 1
            if int(occurrence_counts.get(str(lemma_id), 0)) >= 2:
                repeated_target_slots += 1
            if int(surface_counts.get(str(lemma_id), 0)) >= 2:
                varied_surface_slots += 1
        morphology_target = metadata.get("morphology_target_lemma_id")
        if morphology_target is not None and int(surface_counts.get(str(morphology_target), 0)) >= 2:
            morphology_delivered += 1

    events = list(_iter_events(log_dir, cutoff))
    shown_story_ids: list[int] = []
    for event in events:
        if event.get("event") != "card_shown" or event.get("card_type") != "passage":
            continue
        detail = event.get("detail") or {}
        story_id = detail.get("story_id")
        if story_id is None:
            sentence_id = event.get("sentence_id")
            story_id = first_sentence_to_story.get(sentence_id)
        if story_id in experiment_stories:
            shown_story_ids.append(int(story_id))

    outcome_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    target_rating_total = 0
    target_rating_success = 0
    for event in events:
        if event.get("event") != "sentence_review":
            continue
        sentence_id = event.get("sentence_id")
        story_id = first_sentence_to_story.get(sentence_id)
        if story_id in experiment_stories:
            group = "v2_story"
            word_count = story_word_counts.get(story_id, 0)
        elif event.get("parent_card_type") == "passage":
            group = "legacy_story"
            sentence_ids = event.get("sentence_ids") or [sentence_id]
            valid_ids = [int(item) for item in sentence_ids if isinstance(item, int)]
            if valid_ids:
                placeholders = ",".join("?" for _ in valid_ids)
                word_count = int(conn.execute(
                    f"SELECT COUNT(*) FROM sentence_words WHERE sentence_id IN ({placeholders})",
                    valid_ids,
                ).fetchone()[0])
            else:
                word_count = 0
        else:
            group = "sentence"
            word_count = int(conn.execute(
                "SELECT COUNT(*) FROM sentence_words WHERE sentence_id=?",
                (sentence_id,),
            ).fetchone()[0]) if isinstance(sentence_id, int) else 0

        response_ms = event.get("response_ms")
        ms_per_word = None
        if (
            isinstance(response_ms, (int, float))
            and 0 < response_ms < IDLE_CUTOFF_MS
            and word_count > 0
        ):
            ms_per_word = float(response_ms) / word_count
        outcome_rows[group].append({
            "understood": event.get("comprehension_signal") == "understood",
            "ms_per_word": ms_per_word,
        })

        if group == "v2_story" and story_id is not None:
            targets = {
                int(item)
                for item in experiment_stories[story_id].get("target_lemma_ids") or []
            }
            for raw_id, raw_rating in (event.get("word_ratings") or {}).items():
                try:
                    lemma_id = int(raw_id)
                    rating = int(raw_rating)
                except (TypeError, ValueError):
                    continue
                if lemma_id in targets:
                    target_rating_total += 1
                    target_rating_success += rating >= 3

    outcomes: dict[str, Any] = {}
    for group, rows in outcome_rows.items():
        speeds = [row["ms_per_word"] for row in rows if row["ms_per_word"] is not None]
        outcomes[group] = {
            "reviews": len(rows),
            "clean_speed_samples": len(speeds),
            "median_ms_per_word": _median(speeds),
            "understood_pct": round(100 * sum(row["understood"] for row in rows) / len(rows), 1),
        }

    conn.close()
    return {
        "experiment_version": EXPERIMENT_VERSION,
        "window_days": days,
        "cutoff": cutoff.isoformat(),
        "supply": {
            "stories_created": len(recent_stories),
            "narrative_modes": dict(sorted(modes.items())),
            "unique_target_lemmas": len(target_counts),
            "target_slots": target_slots,
            "target_slots_repeated_pct": round(100 * repeated_target_slots / target_slots, 1) if target_slots else None,
            "target_slots_varied_surface_pct": round(100 * varied_surface_slots / target_slots, 1) if target_slots else None,
            "morphology_stories": morphology_stories,
            "morphology_stories_delivering_varied_forms": morphology_delivered,
            "top_target_share_pct": round(
                100 * sum(count for _, count in target_counts.most_common(10)) / target_slots,
                1,
            ) if target_slots else None,
        },
        "delivery": {
            "cards_shown": len(shown_story_ids),
            "unique_stories_shown": len(set(shown_story_ids)),
            "outcomes": outcomes,
            "selected_target_success_pct": round(
                100 * target_rating_success / target_rating_total,
                1,
            ) if target_rating_total else None,
            "selected_target_ratings": target_rating_total,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = analyze(args.db, args.log_dir, max(1, args.days))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    supply = result["supply"]
    delivery = result["delivery"]
    print(f"Embedded short-story experiment: last {result['window_days']} days")
    print(
        "Supply: "
        f"{supply['stories_created']} stories, "
        f"{len(supply['narrative_modes'])} modes, "
        f"{supply['unique_target_lemmas']} unique targets / {supply['target_slots']} slots"
    )
    print(
        "Practice design: "
        f"{supply['target_slots_repeated_pct']}% target slots repeated, "
        f"{supply['target_slots_varied_surface_pct']}% with varied surfaces, "
        f"{supply['morphology_stories_delivering_varied_forms']}/"
        f"{supply['morphology_stories']} morphology stories delivered the contrast"
    )
    print(
        "Delivery: "
        f"{delivery['cards_shown']} cards shown / "
        f"{delivery['unique_stories_shown']} unique stories; "
        f"target success={delivery['selected_target_success_pct']}% "
        f"(n={delivery['selected_target_ratings']})"
    )
    for group, metrics in sorted(delivery["outcomes"].items()):
        print(
            f"  {group}: n={metrics['reviews']}, "
            f"understood={metrics['understood_pct']}%, "
            f"median clean ms/word={metrics['median_ms_per_word']}"
        )


if __name__ == "__main__":
    main()
