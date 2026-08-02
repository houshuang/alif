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


def test_seed_continues_after_failure_and_marks_partial_batch_incomplete(monkeypatch):
    outcomes = iter([_story(10), RuntimeError("rejected"), _story(11)])

    def fake_generate(**kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(seeder, "generate_and_store_maintenance_passage", fake_generate)
    monkeypatch.setattr(
        seeder,
        "plan_due_maintenance_target_groups",
        lambda count: [
            {"target_lemma_ids": [i * 3 + 1, i * 3 + 2, i * 3 + 3], "scene_hint": "scene"}
            for i in range(count)
        ],
    )

    result = seeder.seed(count=3, attempts_per_story=2)

    assert result["created_count"] == 2
    assert result["failed_count"] == 1
    assert result["complete"] is False
    assert [item["story_id"] for item in result["created"]] == [10, 11]


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
        lambda count: [
            {"target_lemma_ids": [i * 3 + 1, i * 3 + 2, i * 3 + 3], "scene_hint": "scene"}
            for i in range(count)
        ],
    )

    result = seeder.seed(count=2, attempts_per_story=1)

    assert result["complete"] is True
    assert result["failed_count"] == 0


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
