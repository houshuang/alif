"""Keep a small, selectable supply of embedded maintenance stories.

This is the durable recovery path for the opportunistic post-session warm-cache
generator.  It is intentionally bounded to one accepted story per cron pass by
default, records every generation run in ``activity_log``, cools down target
triples that recently failed, and uses the same Codex-only planner/editor as the
live application.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func

from app.database import SessionLocal
from app.models import ActivityLog, Sentence, Story
from app.services.material_generator import (
    _release_material_update_lock,
    _try_acquire_material_update_lock,
)
from app.services.passage_generator import (
    PASSAGE_EXPERIMENT_VERSION,
    _due_maintenance_targets,
)
from scripts.seed_short_story_experiment import seed


EVENT_TYPE = "short_story_supply_run"
ALERT_EVENT_TYPE = "short_story_supply_alert"
DEFAULT_MIN_SELECTABLE = 6
DEFAULT_BUDGET = 1
DEFAULT_ATTEMPTS_PER_STORY = 4
FAILED_TARGET_COOLDOWN = timedelta(hours=48)
STALE_RUN_AFTER = timedelta(minutes=45)
ALERT_WINDOW = timedelta(hours=24)
ALERT_THRESHOLD = 3
ALERT_DEDUPE_WINDOW = timedelta(hours=6)


@dataclass(frozen=True)
class SupplySnapshot:
    due_target_count: int
    active_story_count: int
    selectable_story_count: int
    selectable_story_ids: list[int]


def _metadata(story: Story) -> dict[str, Any]:
    value = story.metadata_json
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def due_maintenance_target_ids(db) -> set[int]:
    return {
        int(item["lemma_id"])
        for item in _due_maintenance_targets(db, limit=10_000)
    }


def supply_snapshot(
    db,
    *,
    due_target_ids: set[int] | None = None,
) -> SupplySnapshot:
    """Count stories that can still deliver their three planned due targets."""
    due_ids = (
        set(due_target_ids)
        if due_target_ids is not None
        else due_maintenance_target_ids(db)
    )
    sentence_counts = dict(
        db.query(Sentence.story_id, func.count(Sentence.id))
        .filter(
            Sentence.story_id.isnot(None),
            Sentence.source == "passage",
            Sentence.is_active.is_(True),
        )
        .group_by(Sentence.story_id)
        .all()
    )
    active_stories = (
        db.query(Story)
        .filter(
            Story.format_type == "maintenance_passage",
            Story.status == "active",
        )
        .all()
    )
    selectable: list[int] = []
    experiment_active = 0
    for story in active_stories:
        metadata = _metadata(story)
        if metadata.get("experiment_version") != PASSAGE_EXPERIMENT_VERSION:
            continue
        experiment_active += 1
        try:
            target_ids = {
                int(lemma_id)
                for lemma_id in (metadata.get("target_lemma_ids") or [])
            }
        except (TypeError, ValueError):
            continue
        if (
            len(target_ids) == 3
            and target_ids.issubset(due_ids)
            and int(sentence_counts.get(story.id, 0)) >= 3
        ):
            selectable.append(int(story.id))
    return SupplySnapshot(
        due_target_count=len(due_ids),
        active_story_count=experiment_active,
        selectable_story_count=len(selectable),
        selectable_story_ids=sorted(selectable),
    )


def codex_credential_preflight() -> dict[str, Any]:
    """Fail before planning if the shared Codex runtime is visibly unusable."""
    binary = shutil.which("codex")
    if not binary:
        raise RuntimeError("codex CLI is not on PATH")
    codex_home = Path(
        os.environ.get("ALIF_CODEX_HOME")
        or os.environ.get("CODEX_HOME")
        or str(Path.home() / ".codex")
    )
    if os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_KEY"):
        return {
            "binary": binary,
            "credential_source": "environment",
            "codex_home": str(codex_home),
        }
    auth_path = codex_home / "auth.json"
    if not auth_path.is_file():
        raise RuntimeError(f"Codex auth file is missing: {auth_path}")
    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Codex auth file is unreadable: {auth_path}") from exc
    tokens = payload.get("tokens") if isinstance(payload, dict) else None
    has_login = isinstance(tokens, dict) and bool(
        tokens.get("access_token") and tokens.get("refresh_token")
    )
    has_key = isinstance(payload, dict) and bool(payload.get("OPENAI_API_KEY"))
    if not (has_login or has_key):
        raise RuntimeError(f"Codex auth file contains no usable credentials: {auth_path}")
    if auth_path.stat().st_mode & 0o077:
        raise RuntimeError(f"Codex auth file permissions are too broad: {auth_path}")
    return {
        "binary": binary,
        "credential_source": "auth_file",
        "codex_home": str(codex_home),
        "auth_mtime": datetime.fromtimestamp(
            auth_path.stat().st_mtime, tz=timezone.utc
        ).isoformat(),
    }


def _detail(row: ActivityLog) -> dict[str, Any]:
    value = row.detail_json
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    # Return a fresh mapping so assigning it back to a SQLAlchemy JSON column
    # is always detected as a change (in-place mutation is not tracked here).
    return dict(value) if isinstance(value, dict) else {}


def mark_interrupted_runs(db, *, now: datetime) -> int:
    cutoff = now - STALE_RUN_AFTER
    rows = (
        db.query(ActivityLog)
        .filter(
            ActivityLog.event_type == EVENT_TYPE,
            ActivityLog.created_at < cutoff,
        )
        .order_by(ActivityLog.id.desc())
        .limit(30)
        .all()
    )
    changed = 0
    for row in rows:
        detail = _detail(row)
        if detail.get("status") != "started":
            continue
        detail["status"] = "interrupted"
        detail["interrupted_at"] = now.isoformat()
        row.detail_json = detail
        row.summary = "Embedded short-story generation was interrupted; cron will retry"
        changed += 1
    if changed:
        db.commit()
    return changed


def recent_failed_target_ids(db, *, now: datetime) -> set[int]:
    cutoff = now - FAILED_TARGET_COOLDOWN
    rows = (
        db.query(ActivityLog)
        .filter(
            ActivityLog.event_type == EVENT_TYPE,
            ActivityLog.created_at >= cutoff,
        )
        .all()
    )
    excluded: set[int] = set()
    for row in rows:
        detail = _detail(row)
        for raw_id in detail.get("failed_target_lemma_ids") or []:
            try:
                excluded.add(int(raw_id))
            except (TypeError, ValueError):
                continue
    return excluded


def _failed_target_ids(result: dict[str, Any]) -> list[int]:
    created_sets = {
        frozenset(int(lemma_id) for lemma_id in item.get("target_lemma_ids") or [])
        for item in result.get("created") or []
    }
    failed: set[int] = set()
    for group in result.get("planned_groups") or []:
        group_ids = frozenset(
            int(lemma_id) for lemma_id in group.get("target_lemma_ids") or []
        )
        if group_ids and group_ids not in created_sets:
            failed.update(group_ids)
    return sorted(failed)


def _maybe_emit_alert(db, *, now: datetime, error: str | None = None) -> None:
    cutoff = now - ALERT_WINDOW
    recent_runs = (
        db.query(ActivityLog)
        .filter(
            ActivityLog.event_type == EVENT_TYPE,
            ActivityLog.created_at >= cutoff,
        )
        .all()
    )
    failed_runs = [
        row
        for row in recent_runs
        if _detail(row).get("status") in {"failed", "interrupted"}
    ]
    if len(failed_runs) < ALERT_THRESHOLD and error is None:
        return
    existing = (
        db.query(ActivityLog)
        .filter(
            ActivityLog.event_type == ALERT_EVENT_TYPE,
            ActivityLog.created_at >= now - ALERT_DEDUPE_WINDOW,
        )
        .first()
    )
    if existing:
        return
    summary = (
        f"Embedded short-story supply needs attention: {error}"
        if error
        else f"Embedded short-story generation failed {len(failed_runs)} times in 24h"
    )
    db.add(ActivityLog(
        event_type=ALERT_EVENT_TYPE,
        summary=summary[:500],
        detail_json={
            "failed_runs_24h": len(failed_runs),
            "error": error,
        },
    ))
    db.commit()


def maintain_supply(
    *,
    minimum_selectable: int = DEFAULT_MIN_SELECTABLE,
    budget: int = DEFAULT_BUDGET,
    attempts_per_story: int = DEFAULT_ATTEMPTS_PER_STORY,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    lock_handle = _try_acquire_material_update_lock()
    if lock_handle is None:
        return {"status": "skipped", "reason": "material_update_active"}
    run_id: int | None = None
    try:
        db = SessionLocal()
        try:
            mark_interrupted_runs(db, now=now)
            before = supply_snapshot(db)
            deficit = max(0, minimum_selectable - before.selectable_story_count)
            if deficit == 0:
                return {"status": "healthy", "before": asdict(before)}
            excluded = recent_failed_target_ids(db, now=now)
            try:
                preflight = codex_credential_preflight()
            except Exception as exc:
                _maybe_emit_alert(db, now=now, error=str(exc))
                return {
                    "status": "failed",
                    "reason": "codex_preflight",
                    "error": str(exc),
                    "before": asdict(before),
                }
            requested = min(max(1, budget), deficit)
            row = ActivityLog(
                event_type=EVENT_TYPE,
                summary=f"Generating up to {requested} embedded short stories",
                detail_json={
                    "status": "started",
                    "started_at": now.isoformat(),
                    "minimum_selectable": minimum_selectable,
                    "requested": requested,
                    "before": asdict(before),
                    "excluded_target_lemma_ids": sorted(excluded),
                    "codex_preflight": preflight,
                },
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            run_id = int(row.id)
        finally:
            db.close()

        result = seed(
            count=requested,
            attempts_per_story=max(1, attempts_per_story),
            initial_excluded_lemma_ids=excluded,
        )
        db = SessionLocal()
        try:
            after = supply_snapshot(db)
            row = db.get(ActivityLog, run_id)
            status = (
                "complete"
                if result.get("complete")
                else "partial"
                if result.get("created_count")
                else "failed"
            )
            failed_ids = _failed_target_ids(result)
            detail = _detail(row)
            detail.update({
                "status": status,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "after": asdict(after),
                "created": result.get("created") or [],
                "failed_target_lemma_ids": failed_ids,
                "failures": result.get("failures") or [],
            })
            row.detail_json = detail
            row.summary = (
                f"Embedded short-story supply: created {result.get('created_count', 0)}/"
                f"{requested}; {after.selectable_story_count} selectable"
            )
            db.commit()
            if status == "failed":
                _maybe_emit_alert(db, now=datetime.now(timezone.utc))
            return {
                "status": status,
                "run_id": run_id,
                "before": asdict(before),
                "after": asdict(after),
                "created": result.get("created") or [],
                "failed_target_lemma_ids": failed_ids,
                "failures": result.get("failures") or [],
            }
        finally:
            db.close()
    except Exception as exc:
        db = SessionLocal()
        try:
            if run_id is not None:
                row = db.get(ActivityLog, run_id)
                if row is not None:
                    detail = _detail(row)
                    detail.update({
                        "status": "failed",
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    row.detail_json = detail
                    row.summary = f"Embedded short-story generation failed: {exc}"[:500]
                    db.commit()
            _maybe_emit_alert(db, now=datetime.now(timezone.utc))
        finally:
            db.close()
        raise
    finally:
        _release_material_update_lock(lock_handle)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Check selectable supply and Codex credentials without writing or generating.",
    )
    parser.add_argument(
        "--minimum-selectable",
        type=int,
        default=int(os.environ.get("ALIF_SHORT_STORY_MIN_SELECTABLE", DEFAULT_MIN_SELECTABLE)),
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=int(os.environ.get("ALIF_SHORT_STORY_CRON_BUDGET", DEFAULT_BUDGET)),
    )
    parser.add_argument(
        "--attempts-per-story",
        type=int,
        default=DEFAULT_ATTEMPTS_PER_STORY,
    )
    args = parser.parse_args()
    if args.status_only:
        db = SessionLocal()
        try:
            snapshot = supply_snapshot(db)
            minimum = max(1, min(args.minimum_selectable, 12))
            deficit = max(0, minimum - snapshot.selectable_story_count)
            report = {
                "status": "healthy" if deficit == 0 else "degraded",
                "minimum_selectable": minimum,
                "selectable_deficit": deficit,
                "supply": asdict(snapshot),
                "failed_target_lemma_ids_on_cooldown": sorted(
                    recent_failed_target_ids(db, now=datetime.now(timezone.utc))
                ),
                "codex_preflight": codex_credential_preflight(),
            }
        except Exception as exc:
            report = {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        finally:
            db.close()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "healthy" else 1
    result = maintain_supply(
        minimum_selectable=max(1, min(args.minimum_selectable, 12)),
        budget=max(1, min(args.budget, 3)),
        attempts_per_story=max(1, min(args.attempts_per_story, 6)),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("status") in {"failed", "partial"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
