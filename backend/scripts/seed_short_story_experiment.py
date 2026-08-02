"""Generate a bounded batch for the embedded short-story experiment.

This command deliberately uses the same production generator and fail-closed
quality gates as warm-cache generation. It is suitable for a one-off scheduled
seed after an LLM quota reset; it never inserts a rejected draft.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from app.services.passage_generator import (
    PASSAGE_EXPERIMENT_VERSION,
    generate_and_store_maintenance_passage,
    plan_due_maintenance_target_groups,
)


def seed(count: int, attempts_per_story: int) -> dict[str, Any]:
    created: list[dict[str, Any]] = []
    failures: list[str] = []
    try:
        planned_groups = plan_due_maintenance_target_groups(count)
    except Exception as exc:
        return {
            "experiment_version": PASSAGE_EXPERIMENT_VERSION,
            "requested": count,
            "planned_groups": [],
            "created": [],
            "created_count": 0,
            "failed_count": count,
            "complete": False,
            "failures": [f"target planner: {type(exc).__name__}: {exc}"],
        }

    for group in planned_groups:
        try:
            story = generate_and_store_maintenance_passage(
                target_lemma_ids=group["target_lemma_ids"],
                scene_hint=group.get("scene_hint"),
                sentence_count=4,
                max_generation_attempts=attempts_per_story,
            )
        except Exception as exc:  # Keep the bounded batch moving after rejection.
            failures.append(f"{type(exc).__name__}: {exc}")
            continue
        metadata = story.metadata_json if isinstance(story.metadata_json, dict) else {}
        created.append({
            "story_id": story.id,
            "title_en": story.title_en,
            "narrative_mode": metadata.get("narrative_mode"),
            "target_lemma_ids": metadata.get("target_lemma_ids") or [],
        })

    return {
        "experiment_version": PASSAGE_EXPERIMENT_VERSION,
        "requested": count,
        "planned_groups": planned_groups,
        "created": created,
        "created_count": len(created),
        "failed_count": len(failures),
        "complete": len(created) == count,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--attempts-per-story", type=int, default=4)
    args = parser.parse_args()
    count = max(1, min(args.count, 12))
    attempts = max(1, min(args.attempts_per_story, 6))
    result = seed(count, attempts)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
