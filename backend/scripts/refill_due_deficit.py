#!/usr/bin/env python3
"""Refill reviewable sentences for due words that no other generation path covers.

Closes the recurring "due-coverage deficit" (R4, 2026-06-16). Two production
generators exist and neither covers this case:

  * ``warm_sentence_cache`` (live, post-session) only generates for the focus
    cohort + acquiring-rescue + intro candidates + recency-exhausted cohort.
  * the ``material_jobs`` cron queue was retired 2026-06-16 (it never drained;
    a rescue-word flood starved everything else).

So a known/learning/lapsed word that is FSRS-**due** but has fallen out of the
focus cohort with **zero reviewable sentences** is targeted by nothing — it
silently drops from sessions. This step finds those words and generates through
the single verified pipeline (``batch_generate_material``: disambiguation → LLM
verification → correction → ``mappings_verified_at``).

Inert lemmas (proper_name / onomatopoeia / function words) and lemmas already in
generation backoff are skipped. Likely lemma artifacts (verb conjugations stored
as standalone lemmas, e.g. نَدْرُسُ "we study"; leading-shadda display forms) are
attempted but logged as decomposition-audit candidates — retire/merge decisions
belong to that audit, not here.

Runs as a cron phase after ``update_material.py`` maintenance. Holds the shared
material-update file lock so it can't race ``warm_sentence_cache``. The DB session
is closed before the LLM loop (write-lock discipline); ``batch_generate_material``
opens its own short-lived sessions for the read/validate/write split.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.material_generator import (
    _release_material_update_lock,
    _try_acquire_material_update_lock,
    batch_generate_material,
    lemmas_on_backoff,
    record_generation_result,
)
from app.services.sentence_eligibility import reviewable_coverage_counts
from app.services.sentence_validator import _is_function_word

DUE_STATES = ("known", "learning", "lapsed")
DEFAULT_BUDGET = 30          # max words generated per run (×count sentences each)
DEFAULT_COUNT_PER_WORD = 2
CHUNK_SIZE = 12              # words per batch_generate_material call
SHADDA = "ّ"
_ARTIFACT_GLOSS_PREFIXES = ("we ", "they ", "she ", "he ", "you ", "i ")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _is_inert(lemma: Lemma) -> bool:
    if (lemma.word_category or "") in {"proper_name", "onomatopoeia"}:
        return True
    return _is_function_word(lemma.lemma_ar_bare or "")


def _has_word_initial_shadda(ar: str) -> bool:
    """A shadda before the 2nd base (non-diacritic) letter is impossible word-initial
    gemination — a display artifact (the 2026-06-13 headword bug residue)."""
    base_letters = 0
    for ch in ar:
        if ch == SHADDA:
            return base_letters <= 1
        # Arabic combining marks: harakat (U+064B–U+0652) + dagger alef (U+0670)
        if "ً" <= ch <= "ْ" or ch == "ٰ":
            continue
        base_letters += 1
        if base_letters >= 2:
            break
    return False


def _looks_like_artifact(lemma: Lemma) -> bool:
    """Heuristic: conjugated verb stored as a lemma, or leading-shadda display form.

    Informational only — these are still attempted; the flag routes them to the
    lemma-decomposition audit for proper retire/merge handling.
    """
    gloss = (lemma.gloss_en or "").strip().lower()
    if gloss.startswith(_ARTIFACT_GLOSS_PREFIXES):
        return True
    return _has_word_initial_shadda(lemma.lemma_ar or "")


def compute_due_deficit(db, states: tuple[str, ...] = DUE_STATES) -> list[int]:
    """Return lemma_ids that are FSRS-due in `states` with zero reviewable sentences."""
    now = datetime.now(timezone.utc)
    candidates: list[int] = []
    ulks = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.knowledge_state.in_(states))
        .all()
    )
    for u in ulks:
        card = u.fsrs_card_json or {}
        due_raw = card.get("due")
        if not due_raw:
            continue
        try:
            due = datetime.fromisoformat(due_raw)
        except (ValueError, TypeError):
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due <= now:
            candidates.append(u.lemma_id)
    if not candidates:
        return []
    coverage = reviewable_coverage_counts(db, lemma_ids=set(candidates))
    return [lid for lid in candidates if coverage.get(lid, 0) == 0]


def classify(db, deficit_ids: list[int]) -> dict:
    """Split deficit lemmas into inert / on-backoff / artifact / generatable."""
    lemmas = {
        l.lemma_id: l
        for l in db.query(Lemma).filter(Lemma.lemma_id.in_(deficit_ids)).all()
    }
    on_backoff = lemmas_on_backoff(db, deficit_ids)
    inert: list[int] = []
    backed_off: list[int] = []
    artifacts: list[int] = []
    generatable: list[int] = []
    for lid in deficit_ids:
        lemma = lemmas.get(lid)
        if lemma is None:
            continue
        if lid in on_backoff:
            backed_off.append(lid)
            continue
        if _is_inert(lemma):
            inert.append(lid)
            continue
        if _looks_like_artifact(lemma):
            artifacts.append(lid)
        generatable.append(lid)
    return {
        "lemmas": lemmas,
        "inert": inert,
        "backed_off": backed_off,
        "artifacts": artifacts,
        "generatable": generatable,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--budget", type=int, default=_env_int("ALIF_DEFICIT_REFILL_BUDGET", DEFAULT_BUDGET),
                   help="Max words to generate for this run")
    p.add_argument("--count", type=int, default=_env_int("ALIF_DEFICIT_REFILL_COUNT", DEFAULT_COUNT_PER_WORD),
                   help="Sentences to generate per word")
    p.add_argument("--dry-run", action="store_true", help="Report the deficit without generating")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    db = SessionLocal()
    lock_handle = None
    try:
        deficit = compute_due_deficit(db)
        print(f"Due-deficit words (FSRS-due, zero reviewable sentence): {len(deficit)}")
        if not deficit:
            return 0

        buckets = classify(db, deficit)
        lemmas = buckets["lemmas"]
        generatable = buckets["generatable"]
        print(
            f"  inert skipped: {len(buckets['inert'])}  "
            f"backoff skipped: {len(buckets['backed_off'])}  "
            f"artifact candidates: {len(buckets['artifacts'])}  "
            f"generatable: {len(generatable)}"
        )
        if buckets["artifacts"]:
            sample = ", ".join(
                f"{lid}:{(lemmas[lid].lemma_ar if lid in lemmas else '?')}"
                for lid in buckets["artifacts"][:12]
            )
            print(f"  decomposition-audit candidates: {sample}")

        target = generatable[: max(0, args.budget)]
        if args.dry_run:
            print(f"  [dry-run] would generate for {len(target)} words (count={args.count})")
            for lid in target:
                lem = lemmas.get(lid)
                print(f"    {lid} {lem.lemma_ar if lem else '?'} = {lem.gloss_en if lem else ''}")
            return 0
        if not target:
            print("  Nothing generatable this run.")
            return 0

        # Acquire the shared material-update lock so we don't race warm_sentence_cache.
        lock_handle = _try_acquire_material_update_lock()
        if lock_handle is None:
            print("  Material update/backfill already running; skipping deficit refill.")
            return 0

        # Release the read session before slow LLM work (write-lock discipline);
        # batch_generate_material manages its own sessions.
        db.close()

        total_generated = 0
        covered: list[int] = []
        failed: list[int] = []
        for i in range(0, len(target), CHUNK_SIZE):
            chunk = target[i : i + CHUNK_SIZE]
            result = batch_generate_material(chunk, count_per_word=args.count)
            total_generated += int(result.get("generated", 0))
            chunk_failed = set(result.get("words_failed", []))
            chunk_covered = [lid for lid in chunk if lid not in chunk_failed]
            covered.extend(chunk_covered)
            failed.extend(chunk_failed)
            # Record per-word outcome so chronic failures back off (own session).
            rdb = SessionLocal()
            try:
                for lid in chunk:
                    record_generation_result(rdb, lid, 0 if lid in chunk_failed else 1)
            finally:
                rdb.close()
            print(
                f"  chunk {i // CHUNK_SIZE + 1}: generated={result.get('generated', 0)} "
                f"covered={len(chunk_covered)}/{len(chunk)}"
            )

        adb = SessionLocal()
        try:
            log_activity(
                adb,
                "deficit_refill",
                f"Refilled {len(covered)}/{len(target)} due-deficit words "
                f"({total_generated} sentences)",
                detail={
                    "deficit_total": len(deficit),
                    "generatable": len(generatable),
                    "targeted": len(target),
                    "covered": len(covered),
                    "failed": failed,
                    "inert_skipped": buckets["inert"],
                    "backoff_skipped": buckets["backed_off"],
                    "artifact_candidates": buckets["artifacts"],
                },
            )
        finally:
            adb.close()

        print(
            f"Done: {total_generated} sentences, covered {len(covered)}/{len(target)}, "
            f"failed {len(failed)}."
        )
        return 0
    finally:
        if lock_handle is not None:
            _release_material_update_lock(lock_handle)
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
