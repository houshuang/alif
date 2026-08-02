from types import SimpleNamespace

from scripts import seed_short_story_experiment as seeder


def _story(story_id: int):
    return SimpleNamespace(
        id=story_id,
        title_en=f"Story {story_id}",
        metadata_json={
            "narrative_mode": "tiny_mystery",
            "target_lemma_ids": [1, 2, 3],
        },
    )


def test_seed_replans_fresh_targets_after_a_failed_candidate(monkeypatch):
    outcomes = iter([_story(10), RuntimeError("rejected"), _story(11), _story(12)])
    generated_calls = []
    planner_calls = []

    def fake_generate(**kwargs):
        generated_calls.append(kwargs)
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(seeder, "generate_and_store_maintenance_passage", fake_generate)
    def fake_plan(count, excluded_lemma_ids=None):
        planner_calls.append((count, set(excluded_lemma_ids or set())))
        start = 1 if len(planner_calls) == 1 else 10
        return [
            {
                "target_lemma_ids": [start + i * 3, start + i * 3 + 1, start + i * 3 + 2],
                "scene_hint": "scene",
            }
            for i in range(count)
        ]

    monkeypatch.setattr(seeder, "plan_due_maintenance_target_groups", fake_plan)

    result = seeder.seed(count=3, attempts_per_story=2)

    assert result["created_count"] == 3
    assert result["failed_count"] == 1
    assert result["complete"] is True
    assert [item["story_id"] for item in result["created"]] == [10, 11, 12]
    assert [call["scene_hint"] for call in generated_calls] == ["scene"] * 4
    assert planner_calls == [(3, set()), (1, set(range(1, 10)))]


def test_seed_marks_full_batch_complete(monkeypatch):
    outcomes = iter([_story(20), _story(21)])
    monkeypatch.setattr(
        seeder,
        "generate_and_store_maintenance_passage",
        lambda **kwargs: next(outcomes),
    )
    monkeypatch.setattr(
        seeder,
        "plan_due_maintenance_target_groups",
        lambda count, excluded_lemma_ids=None: [
            {"target_lemma_ids": [i * 3 + 1, i * 3 + 2, i * 3 + 3], "scene_hint": "scene"}
            for i in range(count)
        ],
    )

    result = seeder.seed(count=2, attempts_per_story=1)

    assert result["complete"] is True
    assert result["failed_count"] == 0


def test_seed_stops_after_bounded_fresh_candidate_budget(monkeypatch):
    monkeypatch.setattr(
        seeder,
        "generate_and_store_maintenance_passage",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("rejected")),
    )
    next_id = iter(range(1, 20))

    def fake_plan(count, excluded_lemma_ids=None):
        return [
            {
                "target_lemma_ids": [next(next_id), next(next_id), next(next_id)],
                "scene_hint": "fresh scene",
            }
            for _ in range(count)
        ]

    monkeypatch.setattr(seeder, "plan_due_maintenance_target_groups", fake_plan)

    result = seeder.seed(count=2, attempts_per_story=1)

    assert result["complete"] is False
    assert result["created_count"] == 0
    assert result["failed_count"] == 4
    assert len(result["planned_groups"]) == 4


def test_seed_fails_closed_when_target_planning_fails(monkeypatch):
    monkeypatch.setattr(
        seeder,
        "plan_due_maintenance_target_groups",
        lambda count: (_ for _ in ()).throw(RuntimeError("planner unavailable")),
    )

    result = seeder.seed(count=3, attempts_per_story=1)

    assert result["created_count"] == 0
    assert result["failed_count"] == 3
    assert result["complete"] is False
    assert "target planner" in result["failures"][0]
