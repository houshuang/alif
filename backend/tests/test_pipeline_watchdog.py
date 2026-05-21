"""Tests for the pipeline watchdog that catches stuck-target validation loops."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models import ActivityLog, Lemma
from app.services.pipeline_watchdog import (
    DEFAULT_FAILURE_THRESHOLD,
    STRUGGLING_FAILURE_THRESHOLD,
    aggregate_failures_by_lemma,
    check_and_alert,
    find_stuck_lemmas,
    find_struggling_lemmas,
)


def _write_log(tmp_path: Path, entries: list[dict]) -> Path:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    today = datetime.now(timezone.utc).date().isoformat()
    path = log_dir / f"generation_pipeline_{today}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return log_dir


def _now_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_aggregate_counts_failures_and_accepts(tmp_path):
    entries = (
        [{"event": "batch_validation_failed", "lemma_id": 7,
          "issues": ["Target word 'x' not found in sentence"], "ts": _now_ts()}] * 5
        + [{"event": "validation_failed", "lemma_id": 7, "ts": _now_ts()}] * 3
        + [{"event": "sentence_accepted", "lemma_id": 7, "ts": _now_ts()}] * 1
        + [{"event": "batch_validation_failed", "lemma_id": 9, "ts": _now_ts()}] * 2
    )
    log_dir = _write_log(tmp_path, entries)
    agg = aggregate_failures_by_lemma(log_dir)

    assert agg[7]["failures"] == 8
    assert agg[7]["accepts"] == 1
    assert agg[9]["failures"] == 2
    assert agg[9]["accepts"] == 0


def test_find_stuck_lemmas_respects_accept_floor(tmp_path):
    """A lemma at the threshold but with >=1 accept should NOT be flagged."""
    entries = (
        [{"event": "batch_validation_failed", "lemma_id": 7, "ts": _now_ts()}] * 50
        + [{"event": "sentence_accepted", "lemma_id": 7, "ts": _now_ts()}] * 1
        + [{"event": "batch_validation_failed", "lemma_id": 9, "ts": _now_ts()}] * 35
    )
    log_dir = _write_log(tmp_path, entries)
    stuck = find_stuck_lemmas(log_dir, failure_threshold=DEFAULT_FAILURE_THRESHOLD)
    stuck_ids = {s.lemma_id for s in stuck}
    assert 9 in stuck_ids
    assert 7 not in stuck_ids


def test_old_entries_outside_window_are_ignored(tmp_path):
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    entries = [{"event": "batch_validation_failed", "lemma_id": 9, "ts": old_ts}] * 50
    log_dir = _write_log(tmp_path, entries)
    stuck = find_stuck_lemmas(log_dir, window_hours=24, failure_threshold=10)
    assert stuck == []


def test_check_and_alert_emits_activity_log(tmp_path, db_session):
    """When a lemma crosses the threshold, an ActivityLog row appears."""
    lem = Lemma(
        lemma_ar="تَسْت", lemma_ar_bare="test_stuck",
        gloss_en="test stuck lemma",
    )
    db_session.add(lem)
    db_session.commit()
    lemma_id = lem.lemma_id

    entries = [{
        "event": "batch_validation_failed",
        "lemma_id": lemma_id,
        "issues": ["Target word 'test_stuck' not found in sentence"],
        "ts": _now_ts(),
    }] * (DEFAULT_FAILURE_THRESHOLD + 5)
    log_dir = _write_log(tmp_path, entries)

    result = check_and_alert(db_session, log_dir)
    assert any(s.lemma_id == lemma_id for s in result["stuck"])

    alert = (
        db_session.query(ActivityLog)
        .filter(ActivityLog.event_type == "pipeline_target_stuck")
        .order_by(ActivityLog.created_at.desc())
        .first()
    )
    assert alert is not None
    assert lemma_id in (alert.detail_json or {}).get("lemma_ids", [])

    # Second call with the same stuck set should NOT emit another row.
    check_and_alert(db_session, log_dir)
    count_after = db_session.query(ActivityLog).filter(
        ActivityLog.event_type == "pipeline_target_stuck"
    ).count()
    assert count_after == 1


def test_struggling_tier_catches_low_accept_ratio(tmp_path):
    """The #65-shape case: high fails + a few accepts (<15% ratio) should be
    flagged by the struggling tier even though the strict tier ignores it."""
    entries = (
        [{"event": "batch_validation_failed", "lemma_id": 65, "ts": _now_ts()}] * 40
        + [{"event": "sentence_accepted", "lemma_id": 65, "ts": _now_ts()}] * 2
    )
    log_dir = _write_log(tmp_path, entries)
    stuck = find_stuck_lemmas(log_dir)
    struggling = find_struggling_lemmas(log_dir)
    assert 65 not in {s.lemma_id for s in stuck}, "strict tier should skip"
    assert 65 in {s.lemma_id for s in struggling}


def test_struggling_tier_excludes_healthy_lemmas(tmp_path):
    # 15 fails + 5 accepts = 33% ratio, healthy. NOT struggling.
    entries = (
        [{"event": "batch_validation_failed", "lemma_id": 88, "ts": _now_ts()}] * 15
        + [{"event": "sentence_accepted", "lemma_id": 88, "ts": _now_ts()}] * 5
    )
    log_dir = _write_log(tmp_path, entries)
    struggling = find_struggling_lemmas(log_dir)
    assert 88 not in {s.lemma_id for s in struggling}


def test_struggling_tier_excludes_stuck_ids(tmp_path):
    # The exclude_lemma_ids param prevents double-reporting a fully stuck lemma.
    entries = (
        [{"event": "batch_validation_failed", "lemma_id": 5, "ts": _now_ts()}] * 50
    )
    log_dir = _write_log(tmp_path, entries)
    struggling = find_struggling_lemmas(log_dir, exclude_lemma_ids={5})
    assert struggling == []


def test_check_and_alert_emits_both_tier_alerts(tmp_path, db_session):
    strict_lem = Lemma(lemma_ar="ا", lemma_ar_bare="strict_lem", gloss_en="x")
    soft_lem = Lemma(lemma_ar="ب", lemma_ar_bare="soft_lem", gloss_en="y")
    db_session.add_all([strict_lem, soft_lem])
    db_session.commit()

    entries = (
        # Strict: >=30 fails, 0 accepts
        [{"event": "batch_validation_failed", "lemma_id": strict_lem.lemma_id,
          "ts": _now_ts()}] * (DEFAULT_FAILURE_THRESHOLD + 2)
        # Soft: >=15 fails, ~5% accept ratio (well under 15%)
        + [{"event": "batch_validation_failed", "lemma_id": soft_lem.lemma_id,
            "ts": _now_ts()}] * 40
        + [{"event": "sentence_accepted", "lemma_id": soft_lem.lemma_id,
            "ts": _now_ts()}] * 1
    )
    log_dir = _write_log(tmp_path, entries)
    result = check_and_alert(db_session, log_dir)
    assert any(s.lemma_id == strict_lem.lemma_id for s in result["stuck"])
    assert any(s.lemma_id == soft_lem.lemma_id for s in result["struggling"])

    strict_alert = db_session.query(ActivityLog).filter(
        ActivityLog.event_type == "pipeline_target_stuck"
    ).first()
    soft_alert = db_session.query(ActivityLog).filter(
        ActivityLog.event_type == "pipeline_target_struggling"
    ).first()
    assert strict_alert is not None
    assert soft_alert is not None
