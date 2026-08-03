import json
from datetime import datetime, timedelta, timezone

from app.models import ActivityLog, Sentence, Story
from scripts import maintain_short_story_supply as maintainer


def _story(db, *, targets, status="active", active_sentences=4):
    story = Story(
        title_en="Test story",
        body_ar="نَصٌّ",
        source="generated",
        status=status,
        format_type="maintenance_passage",
        metadata_json={
            "experiment_version": maintainer.PASSAGE_EXPERIMENT_VERSION,
            "target_lemma_ids": targets,
        },
    )
    db.add(story)
    db.flush()
    for index in range(4):
        db.add(Sentence(
            arabic_text=f"جُمْلَةٌ {index}",
            source="passage",
            story_id=story.id,
            is_active=index < active_sentences,
        ))
    db.flush()
    return story


def test_supply_snapshot_counts_only_currently_selectable_stories(db_session):
    selectable = _story(db_session, targets=[1, 2, 3])
    _story(db_session, targets=[1, 2, 3], status="failed")
    _story(db_session, targets=[1, 2, 3], active_sentences=2)
    _story(db_session, targets=[1, 2, 4])
    db_session.commit()

    snapshot = maintainer.supply_snapshot(
        db_session,
        due_target_ids={1, 2, 3},
    )

    assert snapshot.active_story_count == 3
    assert snapshot.selectable_story_count == 1
    assert snapshot.selectable_story_ids == [selectable.id]


def test_recent_failed_targets_and_interrupted_runs_are_durable(db_session):
    now = datetime.now(timezone.utc)
    failed = ActivityLog(
        event_type=maintainer.EVENT_TYPE,
        summary="failed",
        detail_json={"status": "failed", "failed_target_lemma_ids": [7, 8, 9]},
        created_at=now - timedelta(hours=2),
    )
    interrupted = ActivityLog(
        event_type=maintainer.EVENT_TYPE,
        summary="started",
        detail_json={
            "status": "started",
            "current_candidate": {"target_lemma_ids": [10, 11, 12]},
        },
        created_at=now - timedelta(hours=1),
    )
    db_session.add_all([failed, interrupted])
    db_session.commit()

    assert maintainer.recent_failed_target_ids(db_session, now=now) == {7, 8, 9}
    assert maintainer.mark_interrupted_runs(db_session, now=now) == 1
    db_session.refresh(interrupted)
    assert interrupted.detail_json["status"] == "interrupted"
    assert interrupted.detail_json["failed_target_lemma_ids"] == [10, 11, 12]


def test_codex_preflight_uses_private_shared_auth_file(tmp_path, monkeypatch):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(json.dumps({
        "tokens": {"access_token": "access", "refresh_token": "refresh"},
    }))
    auth_path.chmod(0o600)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.delenv("ALIF_CODEX_HOME", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_KEY", raising=False)
    monkeypatch.setattr(maintainer.shutil, "which", lambda binary: "/usr/bin/codex")

    result = maintainer.codex_credential_preflight()

    assert result["credential_source"] == "auth_file"
    assert result["codex_home"] == str(tmp_path)


def test_failed_target_extraction_excludes_only_rejected_groups():
    result = {
        "planned_groups": [
            {"target_lemma_ids": [1, 2, 3]},
            {"target_lemma_ids": [4, 5, 6]},
        ],
        "created": [{"target_lemma_ids": [4, 5, 6]}],
    }

    assert maintainer._failed_target_ids(result) == [1, 2, 3]


def test_maintain_supply_records_completed_run_and_forwards_cooldown(
    db_session, monkeypatch
):
    snapshots = iter([
        maintainer.SupplySnapshot(30, 2, 2, [10, 11]),
        maintainer.SupplySnapshot(30, 3, 3, [10, 11, 12]),
    ])
    captured = {}
    lock = object()
    monkeypatch.setattr(maintainer, "_try_acquire_material_update_lock", lambda: lock)
    monkeypatch.setattr(maintainer, "_release_material_update_lock", lambda value: None)
    monkeypatch.setattr(maintainer, "supply_snapshot", lambda db: next(snapshots))
    monkeypatch.setattr(
        maintainer,
        "recent_failed_target_ids",
        lambda db, now: {7, 8, 9},
    )
    monkeypatch.setattr(
        maintainer,
        "codex_credential_preflight",
        lambda: {"credential_source": "auth_file"},
    )

    def fake_seed(
        count,
        attempts_per_story,
        initial_excluded_lemma_ids,
        candidate_event_callback,
    ):
        captured.update({
            "count": count,
            "attempts": attempts_per_story,
            "excluded": initial_excluded_lemma_ids,
        })
        group = {"target_lemma_ids": [1, 2, 3], "scene_hint": "fresh"}
        candidate_event_callback("started", group, {"candidate_index": 1})
        candidate_event_callback("accepted", group, {
            "candidate_index": 1,
            "story": {
                "story_id": 12,
                "title_en": "Fresh",
                "target_lemma_ids": [1, 2, 3],
            },
        })
        return {
            "complete": True,
            "created_count": 1,
            "created": [{
                "story_id": 12,
                "title_en": "Fresh",
                "target_lemma_ids": [1, 2, 3],
            }],
            "planned_groups": [{"target_lemma_ids": [1, 2, 3]}],
            "failures": [],
        }

    monkeypatch.setattr(maintainer, "seed", fake_seed)

    result = maintainer.maintain_supply(minimum_selectable=3, budget=1)

    assert result["status"] == "complete"
    assert captured == {"count": 1, "attempts": 4, "excluded": {7, 8, 9}}
    db_session.expire_all()
    row = db_session.query(ActivityLog).filter_by(
        event_type=maintainer.EVENT_TYPE
    ).one()
    assert row.detail_json["status"] == "complete"
    assert row.detail_json["after"]["selectable_story_count"] == 3


def test_status_only_reports_supply_deficit(monkeypatch, capsys):
    class FakeDb:
        def close(self):
            pass

    monkeypatch.setattr(maintainer, "SessionLocal", FakeDb)
    monkeypatch.setattr(
        maintainer,
        "supply_snapshot",
        lambda db: maintainer.SupplySnapshot(30, 6, 3, [1, 2, 3]),
    )
    monkeypatch.setattr(
        maintainer,
        "recent_failed_target_ids",
        lambda db, now: set(),
    )
    monkeypatch.setattr(
        maintainer,
        "codex_credential_preflight",
        lambda: {"credential_source": "auth_file"},
    )
    monkeypatch.setattr(
        "sys.argv",
        ["maintain_short_story_supply.py", "--status-only"],
    )

    assert maintainer.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "degraded"
    assert report["selectable_deficit"] == 3
