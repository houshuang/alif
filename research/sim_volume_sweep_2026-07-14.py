#!/usr/bin/env python3
"""Volume-sweep simulation: what does 30/50/100/120/150 cards/day buy over 90 days?

Drives the real service stack (app/simulation/) from a fresh prod backup, one
simulated day at a time, with two supply-side emulations that the LLM-mocked
sim otherwise lacks (both exist in production via cron + warm_sentence_cache):

  1. Frequency-core intake — creates Lemma rows from unlinked
     FrequencyCoreEntry rows in core_rank order (mirrors the cron's
     ALIF_FREQ_CORE_INTAKE_* path), gates stamped, FCE linked.
  2. Synthetic sentence supply — for intro-frontier lemmas and
     acquiring/lapsed/learning/due lemmas with too few reviewable sentences,
     creates minimal verified sentences (target + known scaffolds) that pass
     reviewable_sentence_clauses() and the comprehensibility gate.

Synthetic sentences are NOT natural Arabic — this run measures *scheduling
dynamics* (intake caps, recovery gates, graduation, leech flow, review load),
not sentence quality.

Usage (one process per variant; each copies the backup to its own temp DB):
  cd backend
  python3 ../research/sim_volume_sweep_2026-07-14.py --volume 100 --days 90 \
      --db ~/alif-backups/alif_20260714_090005.db \
      --out ../research/simdata_volume_2026-07-14/v100.json
  # variants: --schedule weekdays (5d/wk), --break-start 31 --break-end 44
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))
os.environ["ALIF_SKIP_MIGRATIONS"] = "1"
os.environ["TESTING"] = "1"

from sqlalchemy import func  # noqa: E402
from sqlalchemy.orm import aliased  # noqa: E402

from app.models import (  # noqa: E402
    FrequencyCoreEntry,
    Lemma,
    ReviewLog,
    Sentence,
    SentenceWord,
    UserLemmaKnowledge,
)
from app.services.sentence_eligibility import reviewable_sentence_clauses  # noqa: E402
from app.services.sentence_validator import strip_diacritics  # noqa: E402
from app.simulation.db_setup import create_simulation_db  # noqa: E402

# Root/pattern enrichment spawn threads that use the app's global session (not
# the sim DB) and are LLM paths besides — no-op them, as the 2026-06-03 throttle
# sim did. Late-bound imports in acquisition_service pick these up.
import app.services.lemma_enrichment as _lemma_enrichment  # noqa: E402
import app.services.memory_hooks as _memory_hooks  # noqa: E402
import app.services.pattern_enrichment as _pattern_enrichment  # noqa: E402
import app.services.root_enrichment as _root_enrichment  # noqa: E402

_root_enrichment.maybe_enrich_root = lambda *a, **k: None
_pattern_enrichment.maybe_enrich_pattern = lambda *a, **k: None
_lemma_enrichment.enrich_lemmas_batch = lambda *a, **k: None
_memory_hooks.generate_memory_hooks = lambda *a, **k: None
from app.simulation.runner import DaySnapshot, _fill_state_counts, run_simulation  # noqa: E402
from app.simulation.student import StudentProfile  # noqa: E402


class VolumeProfile(StudentProfile):
    """Fixed daily sentence-card volume, calibrated accuracy (real-user params)."""

    def __init__(self, daily_cards: int):
        super().__init__(
            name=f"v{daily_cards}",
            base_comprehension=0.80,
            miss_probability=0.20,
            session_size_range=(8, 12),
            sessions_per_day_range=(1, 1),  # unused; sessions_today() overridden
            study_days_per_week=7.0,  # always-study; driver decides skip days
            no_idea_threshold=0.15,
        )
        self.daily_cards = daily_cards
        self._plan: list[int] = []

    def sessions_today(self) -> int:
        sizes: list[int] = []
        remaining = self.daily_cards
        while remaining > 0:
            s = min(remaining, random.randint(*self.session_size_range))
            sizes.append(s)
            remaining -= s
        self._plan = sizes
        return len(sizes)

    def session_size(self) -> int:
        return self._plan.pop(0) if self._plan else 8


def fce_intake(db, now: datetime, per_day: int, max_rank: int) -> int:
    """Create Lemma rows for the next unlinked frequency-core entries (rank order)."""
    entries = (
        db.query(FrequencyCoreEntry)
        .filter(
            FrequencyCoreEntry.lemma_id.is_(None),
            FrequencyCoreEntry.excluded_reason.is_(None),
            FrequencyCoreEntry.core_rank <= max_rank,
        )
        .order_by(FrequencyCoreEntry.core_rank)
        .limit(per_day)
        .all()
    )
    created = 0
    for e in entries:
        bare = strip_diacritics(e.display_form)
        # Skip if an identical bare lemma already exists (linker would handle IRL)
        existing = db.query(Lemma).filter(Lemma.lemma_ar_bare == bare).first()
        if existing is not None:
            e.lemma_id = existing.lemma_id
            continue
        lemma = Lemma(
            lemma_ar=e.display_form,
            lemma_ar_bare=bare,
            pos=e.pos,
            gloss_en=e.gloss_en or "(sim gloss)",
            frequency_rank=e.core_rank,
            source="sim_freq_core",
            gates_completed_at=now,
        )
        db.add(lemma)
        db.flush()
        e.lemma_id = lemma.lemma_id
        created += 1
    db.commit()
    return created


def _known_scaffold_pool(db, size: int = 300) -> list[Lemma]:
    rows = (
        db.query(Lemma)
        .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        .filter(
            UserLemmaKnowledge.knowledge_state == "known",
            Lemma.word_category.is_(None),
            Lemma.canonical_lemma_id.is_(None),
        )
        .order_by(func.random())
        .limit(size)
        .all()
    )
    return rows


def _eligible_sentence_counts(db, lemma_ids: list[int]) -> dict[int, int]:
    """One grouped query: lemma_id -> count of reviewable sentences containing it."""
    if not lemma_ids:
        return {}
    sw = aliased(SentenceWord)
    rows = (
        db.query(sw.lemma_id, func.count(func.distinct(Sentence.id)))
        .join(Sentence, sw.sentence_id == Sentence.id)
        .filter(
            sw.lemma_id.in_(lemma_ids),
            Sentence.is_active.is_(True),
            *reviewable_sentence_clauses(),
        )
        .group_by(sw.lemma_id)
        .all()
    )
    return dict(rows)


def _supply_targets(db, frontier_n: int) -> list[int]:
    """Lemma ids needing sentence supply: intake frontier + in-flight lemmas."""
    targets: list[int] = []
    # In-flight: acquiring / lapsed / learning (rescue + due coverage)
    inflight = (
        db.query(UserLemmaKnowledge.lemma_id)
        .filter(UserLemmaKnowledge.knowledge_state.in_(["acquiring", "lapsed", "learning"]))
        .all()
    )
    targets.extend(r[0] for r in inflight)
    # Intro frontier: gated standard lemmas with no ULK, by frequency rank
    frontier = (
        db.query(Lemma.lemma_id)
        .outerjoin(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        .filter(
            UserLemmaKnowledge.id.is_(None),
            Lemma.gates_completed_at.isnot(None),
            Lemma.word_category.is_(None),
            Lemma.canonical_lemma_id.is_(None),
        )
        .order_by(Lemma.frequency_rank.isnot(None).desc(), Lemma.frequency_rank)
        .limit(frontier_n)
        .all()
    )
    targets.extend(r[0] for r in frontier)
    return targets


def synthetic_sentence_supply(
    db,
    now: datetime,
    frontier_n: int = 60,
    per_target: int = 2,
    max_new: int = 150,
) -> int:
    """Ensure frontier/in-flight lemmas each have >= per_target reviewable sentences."""
    scaffolds = _known_scaffold_pool(db)
    if len(scaffolds) < 10:
        return 0
    created = 0
    targets = _supply_targets(db, frontier_n)
    have_counts = _eligible_sentence_counts(db, targets)
    for lemma_id in targets:
        if created >= max_new:
            break
        have = have_counts.get(lemma_id, 0)
        if have >= per_target:
            continue
        target = db.get(Lemma, lemma_id)
        if target is None:
            continue
        for _ in range(per_target - have):
            picks = random.sample(scaffolds, 3)
            words = picks[:2] + [target] + picks[2:]
            sent = Sentence(
                arabic_text=" ".join(w.lemma_ar for w in words),
                english_translation=f"(sim sentence for {target.gloss_en})",
                source="llm",
                target_lemma_id=target.lemma_id,
                difficulty_score=2.0,
                is_active=True,
                created_at=now,
                mappings_verified_at=now,
                max_word_count=len(words),
            )
            db.add(sent)
            db.flush()
            for pos, w in enumerate(words):
                db.add(
                    SentenceWord(
                        sentence_id=sent.id,
                        position=pos,
                        surface_form=w.lemma_ar_bare,
                        lemma_id=w.lemma_id,
                        is_target_word=(w.lemma_id == target.lemma_id),
                    )
                )
            created += 1
    db.commit()
    return created


def state_checkpoint(db, day: int) -> dict:
    rows = (
        db.query(UserLemmaKnowledge.lemma_id, UserLemmaKnowledge.knowledge_state,
                 Lemma.source, Lemma.lemma_ar_bare)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .all()
    )
    by_state: dict[str, list[int]] = {}
    new_bares: dict[str, list[str]] = {}  # sim-created lemmas: stable key = bare form
    for lemma_id, state, source, bare in rows:
        if source == "sim_freq_core":
            new_bares.setdefault(state, []).append(bare)
        else:
            by_state.setdefault(state, []).append(lemma_id)
    return {
        "day": day,
        "counts": {
            s: len(by_state.get(s, [])) + len(new_bares.get(s, []))
            for s in set(by_state) | set(new_bares)
        },
        "known_ids": sorted(by_state.get("known", [])),
        "learning_ids": sorted(by_state.get("learning", [])),
        "acquiring_ids": sorted(by_state.get("acquiring", [])),
        "known_new_bares": sorted(new_bares.get("known", [])),
        "learning_new_bares": sorted(new_bares.get("learning", [])),
        "acquiring_new_bares": sorted(new_bares.get("acquiring", [])),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--volume", type=int, required=True, help="sentence cards per study day")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--db", type=str, required=True)
    ap.add_argument("--out", type=str, required=True)
    ap.add_argument("--schedule", choices=["all", "weekdays"], default="all")
    ap.add_argument("--break-start", type=int, default=None, help="skip days N..M (1-based)")
    ap.add_argument("--break-end", type=int, default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fce-per-day", type=int, default=20)
    ap.add_argument("--fce-max-rank", type=int, default=5000)
    args = ap.parse_args()

    engine, SessionFactory, tmp_path = create_simulation_db(args.db)
    print(f"sim db: {tmp_path}", flush=True)

    # db_setup's engine has no PRAGMA tuning (the app-level listener is bound to
    # app.database.engine only) and the prod schema lacks an index on
    # sentence_words.lemma_id — both dominate sim runtime at 90-day scale.
    from sqlalchemy import event as sa_event, text as sa_text

    @sa_event.listens_for(engine, "before_cursor_execute")
    def _q_start(conn, cursor, statement, parameters, context, executemany):
        context._sim_q_start = time.time()

    @sa_event.listens_for(engine, "after_cursor_execute")
    def _q_end(conn, cursor, statement, parameters, context, executemany):
        took = time.time() - getattr(context, "_sim_q_start", time.time())
        if took > 2.0:
            print(f"SLOWQ {took:.1f}s: {' '.join(statement.split())[:300]}", flush=True)

    @sa_event.listens_for(engine, "connect")
    def _sim_pragmas(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        # Throwaway DB copy: durability is irrelevant, and macOS F_FULLFSYNC
        # serializes parallel sims on the disk barrier (23% CPU, fsync-bound).
        cur.execute("PRAGMA synchronous=OFF")
        cur.execute("PRAGMA wal_autocheckpoint=20000")
        # Must exceed DB size (~117MB) — a smaller cache turns the selector's
        # scan-heavy queries into permanent thrash (observed 10x slowdown at 64MB).
        cur.execute("PRAGMA cache_size=-262144")
        cur.execute("PRAGMA temp_store=MEMORY")
        cur.execute("PRAGMA mmap_size=268435456")
        cur.close()

    with engine.connect() as conn:
        conn.execute(sa_text(
            "CREATE INDEX IF NOT EXISTS ix_sim_sw_lemma ON sentence_words(lemma_id)"
        ))
        conn.commit()

    db = SessionFactory()

    latest = db.query(func.max(ReviewLog.reviewed_at)).scalar()
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    start = (latest + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    print(f"sim start: {start.date()}", flush=True)

    profile = VolumeProfile(args.volume)
    snapshots: list[DaySnapshot] = []
    checkpoints: list[dict] = [state_checkpoint(db, 0)]

    for day in range(1, args.days + 1):
        day_start = start + timedelta(days=day - 1)
        skipped = (args.schedule == "weekdays" and day_start.weekday() >= 5) or (
            args.break_start is not None
            and args.break_start <= day <= (args.break_end or args.break_start)
        )
        if skipped:
            snap = DaySnapshot(day=day, date=day_start.strftime("%Y-%m-%d"), skipped=True)
            _fill_state_counts(db, snap)
            snapshots.append(snap)
        else:
            t0 = time.time()
            supply_now = day_start.replace(hour=5)
            n_lemmas = fce_intake(db, supply_now, args.fce_per_day, args.fce_max_rank)
            n_sents = synthetic_sentence_supply(db, supply_now)
            t1 = time.time()
            day_snaps = run_simulation(
                db, 1, profile, start_date=day_start, seed=args.seed * 1000 + day
            )
            t2 = time.time()
            snap = day_snaps[0]
            snap.day = day
            snapshots.append(snap)
            print(
                f"  d{day}: supply {t1-t0:.1f}s (+{n_lemmas}L/+{n_sents}S) "
                f"sim {t2-t1:.1f}s reviews={snap.reviews_submitted}",
                flush=True,
            )

        if day % 15 == 0 or day == args.days:
            checkpoints.append(state_checkpoint(db, day))
            s = snapshots[-1]
            print(
                f"day {day}: known={s.known} acquiring={s.acquiring} "
                f"(box1={s.box_1}) reviews={s.reviews_submitted} "
                f"fill={s.items_received}/{s.session_limit}",
                flush=True,
            )

    out = {
        "variant": {
            "volume": args.volume,
            "days": args.days,
            "schedule": args.schedule,
            "break": [args.break_start, args.break_end],
            "seed": args.seed,
            "source_db": str(args.db),
            "sim_start": start.isoformat(),
        },
        "days": [vars(s) for s in snapshots],
        "checkpoints": checkpoints,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out))
    print(f"wrote {out_path}", flush=True)

    db.close()
    engine.dispose()


if __name__ == "__main__":
    main()
