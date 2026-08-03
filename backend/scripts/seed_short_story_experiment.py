"""Generate a bounded batch for the embedded short-story experiment.

This command deliberately uses the same production generator and fail-closed
quality gates as warm-cache generation. It is suitable for a one-off scheduled
seed after an LLM quota reset; it never inserts a rejected draft.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Callable

from app.services.passage_generator import (
    PASSAGE_EXPERIMENT_VERSION,
    generate_and_store_maintenance_passage,
    plan_due_maintenance_target_groups,
)


def seed(
    count: int,
    attempts_per_story: int,
    initial_excluded_lemma_ids: set[int] | None = None,
    candidate_event_callback: (
        Callable[[str, dict[str, Any], dict[str, Any]], None] | None
    ) = None,
) -> dict[str, Any]:
    created: list[dict[str, Any]] = []
    failures: list[str] = []
    excluded_lemma_ids = set(initial_excluded_lemma_ids or set())
    candidate_budget = max(count, min(12, count * 2))
    try:
        planned_groups = plan_due_maintenance_target_groups(
            count,
            excluded_lemma_ids=excluded_lemma_ids,
        )
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

    candidate_index = 0
    while candidate_index < len(planned_groups) and len(created) < count:
        group = planned_groups[candidate_index]
        candidate_index += 1
        group_ids = {int(lemma_id) for lemma_id in group["target_lemma_ids"]}
        print(
            f"Trying story candidate {candidate_index}/{candidate_budget}: "
            f"targets={sorted(group_ids)} scene={group.get('scene_hint') or '(none)'}",
            flush=True,
        )
        if candidate_event_callback:
            candidate_event_callback("started", group, {
                "candidate_index": candidate_index,
                "candidate_budget": candidate_budget,
            })
        try:
            story = generate_and_store_maintenance_passage(
                target_lemma_ids=group["target_lemma_ids"],
                scene_hint=group.get("scene_hint"),
                sentence_count=4,
                max_generation_attempts=attempts_per_story,
            )
        except Exception as exc:  # Keep the bounded batch moving after rejection.
            failures.append(f"{type(exc).__name__}: {exc}")
            if candidate_event_callback:
                candidate_event_callback("failed", group, {
                    "candidate_index": candidate_index,
                    "error": f"{type(exc).__name__}: {exc}",
                })
        else:
            metadata = story.metadata_json if isinstance(story.metadata_json, dict) else {}
            created_item = {
                "story_id": story.id,
                "title_en": story.title_en,
                "narrative_mode": metadata.get("narrative_mode"),
                "target_lemma_ids": metadata.get("target_lemma_ids") or [],
            }
            created.append(created_item)
            if candidate_event_callback:
                candidate_event_callback("accepted", group, {
                    "candidate_index": candidate_index,
                    "story": created_item,
                })
            print(
                f"Accepted story {story.id}: {story.title_en}",
                flush=True,
            )
        excluded_lemma_ids.update(group_ids)

        if (
            candidate_index == len(planned_groups)
            and len(created) < count
            and len(planned_groups) < candidate_budget
        ):
            replacement_count = min(
                count - len(created),
                candidate_budget - len(planned_groups),
            )
            try:
                replacements = plan_due_maintenance_target_groups(
                    replacement_count,
                    excluded_lemma_ids=excluded_lemma_ids,
                )
            except Exception as exc:
                failures.append(f"replacement target planner: {type(exc).__name__}: {exc}")
                break
            planned_groups.extend(replacements)

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
    parser.add_argument(
        "--exclude-lemma-id",
        type=int,
        action="append",
        default=[],
        help="Skip a target rejected by an earlier bounded run (repeatable).",
    )
    args = parser.parse_args()
    count = max(1, min(args.count, 12))
    attempts = max(1, min(args.attempts_per_story, 6))
    result = seed(count, attempts, set(args.exclude_lemma_id))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
