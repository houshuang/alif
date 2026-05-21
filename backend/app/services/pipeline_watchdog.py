"""Watch the generation pipeline for individual target lemmas that burn
high validation-failure rates with zero acceptances.

Background: on 2026-05-20 three lemmas (جان id=895, ذرا id=575, طاغ id=3277)
accumulated 461 ``Target word ... not found`` failures across a week with
zero accepted sentences. None of the existing alerts surfaced this because
the per-lemma ``generation_failed_count`` only ticks on the legacy single-
word path, and the JSONL ``batch_validation_failed`` events are aggregated
by issue text (not by lemma_id) in ``pipeline_stats.py``.

This watchdog scans the most recent JSONL pipeline log, aggregates failures
vs acceptances by lemma_id, and emits an ActivityLog row whenever a single
lemma crosses ``failure_threshold`` failures with no acceptances inside the
window. ActivityLog rows are surfaced in the More tab — same visibility as
the cron's ``material_updated`` events — so a stuck lemma is caught in <24h
instead of accumulating losses for a week.
"""
from __future__ import annotations

import gzip
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, NamedTuple

from sqlalchemy.orm import Session

from app.models import ActivityLog, Lemma
from app.services.activity_log import log_activity

logger = logging.getLogger(__name__)

# Events that signal a sentence was rejected before being written.
_FAILURE_EVENTS = {"batch_validation_failed", "validation_failed"}
# Events that signal a sentence was accepted (per-lemma success).
_ACCEPT_EVENTS = {"sentence_accepted", "multi_target_accepted"}

# Default threshold: any single lemma accumulating >=30 failures in a day
# with zero acceptances is almost certainly stuck (a target_bare mismatch,
# a bad data row, or a lemma the LLM physically cannot use comprehensibly).
# Chosen so normal hard lemmas — which often see 5-15 failures before a
# successful generation — don't trigger false alarms.
DEFAULT_FAILURE_THRESHOLD = 30

# Softer tier — catches lemmas escaping the strict 0-accept gate. Triggered
# by the 2026-05-20 audit: #65 had 43 failures + 4 accepts in 24h (ratio
# 9.3%) and went undetected at the strict threshold for a week. The soft
# tier catches that exact shape within ~1 day. Reported as a separate event
# (``pipeline_target_struggling``) so the strict alert keeps its zero-FP
# meaning.
STRUGGLING_FAILURE_THRESHOLD = 15
STRUGGLING_ACCEPT_RATIO = 0.15


class StuckLemma(NamedTuple):
    lemma_id: int
    failure_count: int
    accept_count: int
    sample_issues: list[str]


def _iter_pipeline_entries(log_dir: Path, cutoff: datetime) -> Iterable[dict]:
    """Yield pipeline log entries with ts >= cutoff.

    Reads today's JSONL plus yesterday's gz/jsonl so a 24h window always
    sees both files. Older logs are ignored.
    """
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    for d in (yesterday, today):
        for suffix in ("", ".gz"):
            path = log_dir / f"generation_pipeline_{d.isoformat()}.jsonl{suffix}"
            if not path.exists():
                continue
            opener = gzip.open if suffix == ".gz" else open
            with opener(path, "rt", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = entry.get("ts")
                    if ts:
                        try:
                            entry_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if entry_dt.tzinfo is None:
                                entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                            if entry_dt < cutoff:
                                continue
                        except ValueError:
                            pass
                    yield entry
            break


def aggregate_failures_by_lemma(
    log_dir: Path,
    window_hours: int = 24,
) -> dict[int, dict]:
    """Return ``{lemma_id: {"failures": int, "accepts": int, "issues": list}}``.

    Issues are deduplicated and capped at 3 samples per lemma so the
    downstream ActivityLog detail stays compact.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    failures: Counter[int] = Counter()
    accepts: Counter[int] = Counter()
    issues: defaultdict[int, list[str]] = defaultdict(list)

    for entry in _iter_pipeline_entries(log_dir, cutoff):
        ev = entry.get("event")
        lid = entry.get("lemma_id")
        # Multi-target events carry the primary target as `target_lemma_id`
        # and accepted-sentence events use various aliases. Normalize.
        if lid is None:
            lid = entry.get("target_lemma_id") or entry.get("primary_target_lemma_id")
        if lid is None:
            continue
        if ev in _FAILURE_EVENTS:
            failures[lid] += 1
            for iss in (entry.get("issues") or [])[:1]:
                if iss and iss not in issues[lid] and len(issues[lid]) < 3:
                    issues[lid].append(iss)
        elif ev in _ACCEPT_EVENTS:
            accepts[lid] += 1

    return {
        lid: {
            "failures": failures[lid],
            "accepts": accepts.get(lid, 0),
            "issues": issues.get(lid, []),
        }
        for lid in failures
    }


def find_stuck_lemmas(
    log_dir: Path,
    window_hours: int = 24,
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
) -> list[StuckLemma]:
    """Return lemmas with >=failure_threshold failures and 0 accepts."""
    agg = aggregate_failures_by_lemma(log_dir, window_hours=window_hours)
    stuck = []
    for lid, stats in agg.items():
        if stats["failures"] >= failure_threshold and stats["accepts"] == 0:
            stuck.append(StuckLemma(
                lemma_id=lid,
                failure_count=stats["failures"],
                accept_count=stats["accepts"],
                sample_issues=stats["issues"],
            ))
    stuck.sort(key=lambda s: -s.failure_count)
    return stuck


def emit_stuck_lemma_alert(
    db: Session,
    stuck: list[StuckLemma],
    window_hours: int = 24,
) -> ActivityLog | None:
    """Emit an ActivityLog row for stuck lemmas, idempotent within the window.

    Skips if an identical alert (same set of lemma_ids) was emitted in the
    window already — keeps the More-tab feed readable when the same lemmas
    stay stuck for multiple cron passes.
    """
    if not stuck:
        return None

    lemma_ids = sorted(s.lemma_id for s in stuck)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    existing = (
        db.query(ActivityLog)
        .filter(
            ActivityLog.event_type == "pipeline_target_stuck",
            ActivityLog.created_at >= cutoff,
        )
        .order_by(ActivityLog.created_at.desc())
        .first()
    )
    if existing:
        prev_ids = sorted((existing.detail_json or {}).get("lemma_ids") or [])
        if prev_ids == lemma_ids:
            logger.info("Stuck-lemma alert already emitted for %s", lemma_ids)
            return None

    lemma_rows = db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all()
    lemma_by_id = {l.lemma_id: l for l in lemma_rows}
    summaries = []
    detail_items = []
    for s in stuck:
        lem = lemma_by_id.get(s.lemma_id)
        ar = lem.lemma_ar if lem else "?"
        gloss = (lem.gloss_en if lem else "") or "?"
        summaries.append(f"{ar} ({gloss}): {s.failure_count} fails / 0 accepts")
        detail_items.append({
            "lemma_id": s.lemma_id,
            "lemma_ar": ar,
            "gloss_en": gloss,
            "failure_count": s.failure_count,
            "accept_count": s.accept_count,
            "sample_issues": s.sample_issues,
        })

    summary = (
        f"{len(stuck)} lemma(s) stuck in generation "
        f"({window_hours}h window): "
        + "; ".join(summaries[:3])
        + ("…" if len(summaries) > 3 else "")
    )
    return log_activity(
        db,
        event_type="pipeline_target_stuck",
        summary=summary,
        detail={
            "lemma_ids": lemma_ids,
            "window_hours": window_hours,
            "items": detail_items,
        },
    )


def find_struggling_lemmas(
    log_dir: Path,
    window_hours: int = 24,
    failure_threshold: int = STRUGGLING_FAILURE_THRESHOLD,
    accept_ratio: float = STRUGGLING_ACCEPT_RATIO,
    exclude_lemma_ids: set[int] | None = None,
) -> list[StuckLemma]:
    """Return lemmas with >=failure_threshold failures AND accept-rate below
    ``accept_ratio``. Distinct from ``find_stuck_lemmas`` — these lemmas
    occasionally produce sentences but are net-losing on every cron pass.

    Pass ``exclude_lemma_ids`` to skip lemmas already reported as fully
    stuck, so the same row doesn't show up in both event types.
    """
    exclude = exclude_lemma_ids or set()
    agg = aggregate_failures_by_lemma(log_dir, window_hours=window_hours)
    struggling = []
    for lid, stats in agg.items():
        if lid in exclude:
            continue
        fails = stats["failures"]
        accepts = stats["accepts"]
        if fails < failure_threshold:
            continue
        if accepts >= fails * accept_ratio:
            continue  # producing enough sentences to be considered healthy
        struggling.append(StuckLemma(
            lemma_id=lid,
            failure_count=fails,
            accept_count=accepts,
            sample_issues=stats["issues"],
        ))
    struggling.sort(key=lambda s: -s.failure_count)
    return struggling


def emit_struggling_lemma_alert(
    db: Session,
    struggling: list[StuckLemma],
    window_hours: int = 24,
) -> ActivityLog | None:
    """Emit a ``pipeline_target_struggling`` ActivityLog row.

    Idempotent against the previous identical alert in the same window, same
    as the strict-tier alert.
    """
    if not struggling:
        return None
    lemma_ids = sorted(s.lemma_id for s in struggling)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    existing = (
        db.query(ActivityLog)
        .filter(
            ActivityLog.event_type == "pipeline_target_struggling",
            ActivityLog.created_at >= cutoff,
        )
        .order_by(ActivityLog.created_at.desc())
        .first()
    )
    if existing:
        prev_ids = sorted((existing.detail_json or {}).get("lemma_ids") or [])
        if prev_ids == lemma_ids:
            return None

    lemma_rows = db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all()
    lemma_by_id = {l.lemma_id: l for l in lemma_rows}
    summaries = []
    detail_items = []
    for s in struggling:
        lem = lemma_by_id.get(s.lemma_id)
        ar = lem.lemma_ar if lem else "?"
        gloss = (lem.gloss_en if lem else "") or "?"
        ratio = (s.accept_count / s.failure_count * 100) if s.failure_count else 0.0
        summaries.append(
            f"{ar} ({gloss}): {s.failure_count} fails / {s.accept_count} accepts ({ratio:.0f}%)"
        )
        detail_items.append({
            "lemma_id": s.lemma_id,
            "lemma_ar": ar,
            "gloss_en": gloss,
            "failure_count": s.failure_count,
            "accept_count": s.accept_count,
            "sample_issues": s.sample_issues,
        })
    summary = (
        f"{len(struggling)} lemma(s) struggling in generation "
        f"({window_hours}h, <{STRUGGLING_ACCEPT_RATIO*100:.0f}% accept): "
        + "; ".join(summaries[:3])
        + ("…" if len(summaries) > 3 else "")
    )
    return log_activity(
        db,
        event_type="pipeline_target_struggling",
        summary=summary,
        detail={
            "lemma_ids": lemma_ids,
            "window_hours": window_hours,
            "items": detail_items,
        },
    )


def check_and_alert(
    db: Session,
    log_dir: Path,
    window_hours: int = 24,
    failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
) -> dict:
    """Convenience wrapper: run both tiers, decide, emit. Returns
    ``{"stuck": [...], "struggling": [...]}``.
    """
    stuck = find_stuck_lemmas(
        log_dir,
        window_hours=window_hours,
        failure_threshold=failure_threshold,
    )
    if stuck:
        emit_stuck_lemma_alert(db, stuck, window_hours=window_hours)

    struggling = find_struggling_lemmas(
        log_dir,
        window_hours=window_hours,
        exclude_lemma_ids={s.lemma_id for s in stuck},
    )
    if struggling:
        emit_struggling_lemma_alert(db, struggling, window_hours=window_hours)

    return {"stuck": stuck, "struggling": struggling}
