#!/usr/bin/env python3
"""Unified periodic update: backfill sentences, generate audio, pre-generate for upcoming words.

Designed to run as a cron job every 6 hours inside the Docker container.

Steps:
  A) Backfill sentences for introduced words (< 2 sentences each)
  B) Generate audio for review-eligible sentences (all words reviewed ≥1 time)
  C) Pre-generate sentences for top upcoming word candidates (no audio)
  F) Reintroduce leeches past their cooldown period
  G3) FSRS difficulty reconciliation (replay reviews for stuck-difficulty words)

Usage:
    python scripts/update_material.py                  # full run
    python scripts/update_material.py --dry-run        # preview only
    python scripts/update_material.py --skip-audio     # skip TTS generation
    python scripts/update_material.py --limit 20       # max 20 audio generations
    python scripts/update_material.py --only-corpus-enrichment \
        --kind momo_book --corpus-limit 20
"""

import argparse
import asyncio
import fcntl
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services import corpus_enrichment as corpus_enrichment_service
from app.services.corpus_enrichment import (
    DEFAULT_ENRICH_LIMIT,
    MAX_ACTIVATE_LIMIT,
    MAX_ENRICH_LIMIT,
    CorpusActivationPlan,
    CorpusEnrichmentResult,
    enrich_corpus_sentences,
    generate_corpus_enrichment_batch,
    has_arabic_diacritics,
    plan_corpus_activation,
    plan_corpus_enrichment_report,
    outside_corpus_governor_clause,
)
from app.services.word_selector import select_next_words
from app.services.material_generator import (
    acquiring_material_gaps,
    generate_material_for_word,
    lemmas_on_backoff,
    record_generation_result,
)
from app.services.sentence_generator import (
    get_content_word_counts,
    get_avoid_words,
    group_words_for_multi_target,
    generate_validated_sentences_multi_target,
)
from app.services.sentence_validator import (
    _is_function_word,
    build_lemma_lookup,
    strip_diacritics,
)
from app.services.sentence_eligibility import (
    CORPUS_CLAIM_SENTINEL,
    CORPUS_DURABLE_DISPOSITION_SENTINELS,
)
from app.services.tts import (
    DEFAULT_VOICE_ID,
    TTSError,
    TTSKeyMissing,
    audio_generation_enabled,
    cache_key_for,
    generate_and_cache,
    get_cached_path,
)

TARGET_PIPELINE_SENTENCES = 2000  # safety valve only — tier-based lifecycle manages pool size
DEFAULT_STEP_A_SENTENCE_BUDGET = 40  # bounded per cron; 2000 is a cap, not a fill target
CAP_HEADROOM = 50  # retire this many below cap to leave room for multi-target backfill
PREGEN_SENTENCES_PER_CANDIDATE = 3  # for step C pre-generation of not-yet-introduced words
MAX_DUE_DENSE_SALVAGE_PER_RUN = 25
CORPUS_NON_ACTIVATABLE_SENTINELS = (
    CORPUS_CLAIM_SENTINEL,
    *CORPUS_DURABLE_DISPOSITION_SENTINELS,
)
CORPUS_ENRICH_BATCH_SIZE = max(
    1, int(os.environ.get("ALIF_CORPUS_ENRICH_BATCH_SIZE", "10"))
)
CORPUS_VERIFY_BATCH_SIZE = max(
    1, int(os.environ.get("ALIF_CORPUS_VERIFY_BATCH_SIZE", "10"))
)
LOCK_PATH = Path(
    os.environ.get("ALIF_UPDATE_MATERIAL_LOCK", "/tmp/alif-update-material.lock")
)

# Backward-compatible aliases for focused batching tests and any one-off imports
# that used the old script-local helpers.
_has_diacritics = has_arabic_diacritics
_generate_corpus_enrichment_batch = generate_corpus_enrichment_batch
_CORPUS_ENRICH_SCHEMA = corpus_enrichment_service._CORPUS_ENRICH_SCHEMA


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _run_lemma_enrichment(force: bool = False) -> bool:
    return force or _env_bool("ALIF_RUN_CRON_LEMMA_ENRICHMENT", False)


def _run_corpus_enrichment(force: bool = False) -> bool:
    return force or _env_bool("ALIF_RUN_CRON_CORPUS_ENRICHMENT", False)


def _run_pregeneration(force: bool = False) -> bool:
    return force or _env_bool("ALIF_RUN_CRON_PREGENERATION", False)


def _is_generation_inert_lemma(lemma: Lemma) -> bool:
    if lemma.word_category in {"proper_name", "onomatopoeia"}:
        return True
    return _is_function_word(lemma.lemma_ar_bare or "")


def _augment_groups_with_recovery(
    groups: list[list[dict]],
    recovery_words: list[dict],
    max_group_size: int = 4,
) -> list[list[dict]]:
    """Top up multi-target groups with at most 1 backed-off lemma each.

    Recovery lemmas only fill empty slots — never displace a healthy lemma —
    so groups remain majority-healthy. Skips a recovery lemma whose root_id
    collides with one already in the group.
    """
    if not recovery_words:
        return groups
    pool = list(recovery_words)
    random.shuffle(pool)
    out: list[list[dict]] = []
    for group in groups:
        if len(group) >= max_group_size or not pool:
            out.append(group)
            continue
        existing_roots = {w.get("root_id") for w in group if w.get("root_id") is not None}
        for i, rw in enumerate(pool):
            rid = rw.get("root_id")
            if rid is not None and rid in existing_roots:
                continue
            group = group + [rw]
            pool.pop(i)
            break
        out.append(group)
    return out


def get_existing_counts(db: Session) -> dict[int, int]:
    rows = (
        db.query(Sentence.target_lemma_id, func.count(Sentence.id))
        .filter(
            Sentence.target_lemma_id.isnot(None),
            Sentence.is_active == True,  # noqa: E712
        )
        .group_by(Sentence.target_lemma_id)
        .all()
    )
    return {lid: cnt for lid, cnt in rows}


def get_words_by_due_date(db: Session) -> list[tuple[int, str]]:
    """Return lemma_ids sorted by FSRS due date (most urgent first).

    Returns list of (lemma_id, due_iso_string) for all non-suspended words.
    """
    from datetime import datetime, timezone

    knowledges = (
        db.query(UserLemmaKnowledge)
        .filter(
            UserLemmaKnowledge.knowledge_state.notin_(["suspended", "encountered"]),
        )
        .all()
    )

    items: list[tuple[int, datetime]] = []
    for k in knowledges:
        # Acquiring words use acquisition_next_due
        if k.knowledge_state == "acquiring":
            if k.acquisition_next_due:
                due_dt = k.acquisition_next_due
                if due_dt.tzinfo is None:
                    due_dt = due_dt.replace(tzinfo=timezone.utc)
                items.append((k.lemma_id, due_dt))
            continue

        if not k.fsrs_card_json:
            continue
        try:
            card = k.fsrs_card_json if isinstance(k.fsrs_card_json, dict) else __import__("json").loads(k.fsrs_card_json)
        except (TypeError, ValueError):
            continue
        due_str = card.get("due", "")
        if due_str:
            try:
                due_dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
                if due_dt.tzinfo is None:
                    due_dt = due_dt.replace(tzinfo=timezone.utc)
                items.append((k.lemma_id, due_dt))
            except (ValueError, TypeError):
                pass

    items.sort(key=lambda x: x[1])
    return [(lid, dt.isoformat()) for lid, dt in items]


def get_known_words_and_lookup(db: Session) -> tuple[list[dict[str, str]], dict[str, int]]:
    all_lemmas = (
        db.query(Lemma)
        .join(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.fsrs_card_json.isnot(None))
        .all()
    )
    known_words = [
        {"arabic": lem.lemma_ar, "english": lem.gloss_en or "", "pos": lem.pos or ""}
        for lem in all_lemmas
    ]
    lemma_lookup = build_lemma_lookup(all_lemmas)
    return known_words, lemma_lookup


def salvage_due_dense_inactive_sentences(
    db: Session,
    target_lemma_ids: set[int],
    known_lemma_ids: set[int],
    budget: int,
    dry_run: bool,
    deficit_lemma_ids: set[int] | None = None,
) -> int:
    """Reactivate verified inactive sentences that cover multiple target words.

    This is deliberately conservative: only already-verified sentences whose
    non-function content is in the active known/acquiring set are considered,
    and non-dry runs still pass the Haiku quality gate before reactivation.

    `deficit_lemma_ids` are target words with *zero* reviewable sentences. For
    those, a single-coverage sentence is salvaged too (the usual >=2 threshold
    would leave a churned known word stranded — its retired sentence covers only
    1 due word, so it never qualifies; see experiment-log 2026-05-29).
    """
    deficit_lemma_ids = deficit_lemma_ids or set()
    if budget <= 0 or (len(target_lemma_ids) < 2 and not deficit_lemma_ids):
        return 0
    from app.services.llm import (
        review_sentences_quality,
        sentence_quality_review_input,
    )
    from app.services.sentence_validator import _is_function_word, strip_diacritics

    rows = (
        db.query(Sentence)
        .join(SentenceWord, SentenceWord.sentence_id == Sentence.id)
        .filter(
            Sentence.is_active == False,  # noqa: E712
            Sentence.mappings_verified_at.isnot(None),
            Sentence.mappings_verified_at.notin_(
                CORPUS_NON_ACTIVATABLE_SENTINELS
            ),
            outside_corpus_governor_clause(),
            SentenceWord.lemma_id.in_(target_lemma_ids),
        )
        .distinct()
        .limit(400)
        .all()
    )
    candidates: list[tuple[Sentence, set[int]]] = []
    for sent in rows:
        due_hits: set[int] = set()
        safe = True
        for sw in sent.words:
            if not sw.lemma_id:
                continue
            if _is_function_word(strip_diacritics(sw.surface_form or "")):
                continue
            if sw.lemma_id in target_lemma_ids:
                due_hits.add(sw.lemma_id)
            elif sw.lemma_id not in known_lemma_ids:
                safe = False
                break
        if safe and (len(due_hits) >= 2 or (due_hits & deficit_lemma_ids)):
            candidates.append((sent, due_hits))

    # Deficit words first (they have zero reviewable coverage), then most-dense.
    candidates.sort(
        key=lambda item: (
            0 if (item[1] & deficit_lemma_ids) else 1,
            -len(item[1]),
            item[0].times_shown or 0,
            item[0].id,
        )
    )
    candidates = candidates[: min(MAX_DUE_DENSE_SALVAGE_PER_RUN, budget)]
    if not candidates:
        return 0
    if dry_run:
        print(f"  Due-dense inactive salvage candidates: {len(candidates)}")
        return min(len(candidates), budget)

    # The provider call runs without a write lock. Snapshot every parent field
    # that makes its verdict applicable so a concurrent edit cannot be exposed
    # by a stale approval. The final write below is an exact compare-and-set.
    snapshots = [
        {
            "sentence_id": sent.id,
            "hits": hits,
            "arabic_text": sent.arabic_text,
            "english_translation": sent.english_translation,
            "transliteration": sent.transliteration,
            "source": sent.source,
            "kind": sent.kind,
            "mappings_verified_at": sent.mappings_verified_at,
            "quality_reviewed_at": sent.quality_reviewed_at,
            "quality_natural": sent.quality_natural,
            "quality_translation_correct": (
                sent.quality_translation_correct
            ),
            "quality_reason": sent.quality_reason,
        }
        for sent, hits in candidates
    ]
    # Close the read transaction before the slow call so a concurrent writer
    # can commit and the later CAS observes its new state.
    db.commit()
    reviews = review_sentences_quality([
        sentence_quality_review_input(
            arabic=snapshot["arabic_text"],
            english=snapshot["english_translation"] or "",
            source=snapshot["source"],
            kind=snapshot["kind"],
        )
        for snapshot in snapshots
    ])
    reactivated = 0
    for snapshot, review in zip(snapshots, reviews):
        if (
            not getattr(review, "review_completed", True)
            or not getattr(review, "natural", False)
            or not getattr(review, "translation_correct", False)
        ):
            continue
        updated = (
            db.query(Sentence)
            .filter(
                Sentence.id == snapshot["sentence_id"],
                Sentence.is_active.is_(False),
                Sentence.arabic_text == snapshot["arabic_text"],
                Sentence.english_translation
                == snapshot["english_translation"],
                Sentence.transliteration == snapshot["transliteration"],
                Sentence.source == snapshot["source"],
                Sentence.kind == snapshot["kind"],
                Sentence.mappings_verified_at
                == snapshot["mappings_verified_at"],
                Sentence.quality_reviewed_at
                == snapshot["quality_reviewed_at"],
                Sentence.quality_natural == snapshot["quality_natural"],
                Sentence.quality_translation_correct
                == snapshot["quality_translation_correct"],
                Sentence.quality_reason == snapshot["quality_reason"],
            )
            .update(
                {Sentence.is_active: True},
                synchronize_session=False,
            )
        )
        if not updated:
            continue
        reactivated += 1
        print(
            "    ✓ Salvaged inactive sentence "
            f"{snapshot['sentence_id']} covering "
            f"{len(snapshot['hits'])} target words"
        )
        if reactivated >= budget:
            break
    if reactivated:
        db.commit()
        log_activity(
            db,
            event_type="sentences_salvaged",
            summary=f"Reactivated {reactivated} due-dense inactive sentences",
            detail={"reactivated": reactivated},
        )
    else:
        # Even a zero-row UPDATE starts a SQLite write transaction. Release it
        # after all compare-and-set attempts miss so later LLM work cannot hold
        # the database writer lock.
        db.rollback()
    return reactivated


# ── Step A2: Scoped corpus enrichment and activation ─────────────────


def _validate_corpus_cli_args(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    *,
    corpus_requested: bool,
) -> None:
    """Fail closed before any maintenance when corpus work is unscoped."""
    args.corpus_kind = (args.corpus_kind or "").strip() or None
    args.corpus_sentence_id = sorted(set(args.corpus_sentence_id or []))
    args.corpus_retry_blocked = bool(
        getattr(args, "corpus_retry_blocked", False)
    )
    if not corpus_requested:
        return
    if args.corpus_kind is None and not args.corpus_sentence_id:
        parser.error(
            "corpus enrichment requires --kind/--corpus-kind and/or "
            "--corpus-sentence-id"
        )
    if any(sentence_id <= 0 for sentence_id in args.corpus_sentence_id):
        parser.error("--corpus-sentence-id values must be positive")
    if not 0 <= args.corpus_limit <= MAX_ENRICH_LIMIT:
        parser.error(
            f"--corpus-limit must be between 0 and {MAX_ENRICH_LIMIT}"
        )
    if not 0 <= args.corpus_activate_limit <= MAX_ACTIVATE_LIMIT:
        parser.error(
            "--corpus-activate-limit must be between 0 and "
            f"{MAX_ACTIVATE_LIMIT}"
        )
    if args.corpus_limit > 0 and args.corpus_activate_limit > 0:
        parser.error(
            "corpus preparation and activation require separate invocations; "
            "set --corpus-activate-limit 0 while enriching, or "
            "--corpus-limit 0 while activating"
        )
    if args.corpus_retry_blocked and (
        not args.corpus_sentence_id
        or args.corpus_limit <= 0
        or args.corpus_activate_limit != 0
    ):
        parser.error(
            "--corpus-retry-blocked requires one or more explicit "
            "--corpus-sentence-id values, nonzero --corpus-limit, and "
            "--corpus-activate-limit 0"
        )
    if args.corpus_active_ceiling < 0:
        parser.error("--corpus-active-ceiling must be non-negative")


def _corpus_run_kwargs(args: argparse.Namespace) -> dict:
    return {
        "kind": args.corpus_kind,
        "sentence_ids": args.corpus_sentence_id,
        "limit": args.corpus_limit,
        "activate_limit": args.corpus_activate_limit,
        "active_ceiling": args.corpus_active_ceiling,
        "retry_blocked": args.corpus_retry_blocked,
    }


def _corpus_rejected_count(result: CorpusEnrichmentResult | None) -> int:
    if result is None:
        return 0
    return len(
        set(result.mapping_blocked_ids)
        | set(result.quality_rejected_ids)
        | set(result.target_rejected_ids)
    )


def _run_scoped_corpus_step(
    db: Session,
    args: argparse.Namespace,
) -> CorpusEnrichmentResult | None:
    """Print a read-only plan or execute the scoped enrichment service."""
    kwargs = _corpus_run_kwargs(args)
    if args.dry_run:
        enrichment_plan = plan_corpus_enrichment_report(
            db,
            kind=kwargs["kind"],
            sentence_ids=kwargs["sentence_ids"],
            limit=kwargs["limit"],
            include_legacy_claims=(
                bool(kwargs["sentence_ids"])
                and not kwargs["retry_blocked"]
            ),
            include_blocked=kwargs["retry_blocked"],
            only_blocked=kwargs["retry_blocked"],
        )
        if kwargs["activate_limit"]:
            activation_plan = plan_corpus_activation(
                db,
                kind=kwargs["kind"],
                sentence_ids=kwargs["sentence_ids"],
                activate_limit=kwargs["activate_limit"],
                active_ceiling=kwargs["active_ceiling"],
            )
        else:
            active_before = int(
                db.query(func.count(Sentence.id))
                .filter(Sentence.is_active.is_(True))
                .scalar()
                or 0
            )
            activation_plan = CorpusActivationPlan(
                active_before=active_before,
                active_ceiling=kwargs["active_ceiling"],
                capacity=0,
            )
        print(
            json.dumps(
                {
                    "mode": "dry_run",
                    "scope": {
                        "kind": kwargs["kind"],
                        "sentence_ids": kwargs["sentence_ids"],
                    },
                    "enrichment_plan": enrichment_plan.detail(),
                    "activation_plan": activation_plan.detail(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return None

    result = enrich_corpus_sentences(
        db,
        **kwargs,
        enrichment_batch_size=CORPUS_ENRICH_BATCH_SIZE,
        verification_batch_size=CORPUS_VERIFY_BATCH_SIZE,
    )
    print(json.dumps(result.detail(), ensure_ascii=False, indent=2))
    return result


# ── Step 0: Enforce sentence cap by retiring excess ──────────────────

def step_enforce_cap(
    db: Session,
    dry_run: bool,
    max_sentences: int = TARGET_PIPELINE_SENTENCES,
    tier_lookup: dict | None = None,
) -> int:
    """Retire excess sentences when over the pipeline cap.

    Retirement priority:
      1. Never-shown sentences (times_shown=0) — stale first (no acquiring scaffold)
      2. Shown stale sentences (no acquiring/learning scaffold words)
      3. Oldest by last_reading_shown_at as final tiebreaker
    Floor per word is due-date-tier-aware: tier 1 (due <12h) keeps 2,
    tier 2 (12-36h) keeps 1, tier 3+ keeps 0.
    """
    import json

    if tier_lookup is None:
        from app.services.pipeline_tiers import compute_word_tiers, build_tier_lookup
        word_tiers = compute_word_tiers(db)
        tier_lookup = build_tier_lookup(word_tiers)

    existing_counts = get_existing_counts(db)
    total_active = sum(existing_counts.values())

    # Also count sentences with no target_lemma_id
    orphan_count = (
        db.query(func.count(Sentence.id))
        .filter(Sentence.is_active == True, Sentence.target_lemma_id.is_(None))
        .scalar() or 0
    )
    total_active += orphan_count

    print(f"\n═══ Step 0: Enforce sentence cap ═══")
    print(f"  Active sentences: {total_active} (cap: {max_sentences})")

    retire_target = max_sentences - CAP_HEADROOM
    if total_active <= retire_target:
        print(f"  Under retire target ({retire_target}), nothing to retire.")
        return 0

    excess = total_active - retire_target
    print(f"  Over cap by {excess} — identifying sentences to retire")

    # Load all active sentences with their diversity scores
    sentences = db.query(Sentence).filter(Sentence.is_active == True).all()
    all_sw = db.query(SentenceWord).filter(
        SentenceWord.sentence_id.in_([s.id for s in sentences])
    ).all()
    all_ulk = db.query(UserLemmaKnowledge).all()

    knowledge_map = {k.lemma_id: k for k in all_ulk}
    sw_by_sentence: dict[int, list] = {}
    for sw in all_sw:
        sw_by_sentence.setdefault(sw.sentence_id, []).append(sw)

    # Last-sentence guard: never retire a sentence if it is the only reviewable
    # one covering an FSRS word (known/learning/lapsed) — target OR collateral.
    # The per-target floor below protects only the target, so collateral-only
    # words used to drop to zero coverage and fall into the due deficit.
    from app.services.sentence_eligibility import reviewable_coverage_counts

    protected_ids = {
        k.lemma_id for k in all_ulk
        if k.knowledge_state in ("known", "learning", "lapsed")
    }
    coverage = reviewable_coverage_counts(db, lemma_ids=protected_ids)

    def _covered_protected(sent) -> set[int]:
        return {
            sw.lemma_id for sw in sw_by_sentence.get(sent.id, [])
            if sw.lemma_id in protected_ids
        }

    # Score and sort for retirement — protect unshown book sentences (managed by reactivation step)
    candidates: list[tuple[Sentence, int]] = []  # (sentence, priority)
    for sent in sentences:
        if sent.source == "book" and (sent.times_shown or 0) == 0:
            continue
        sws = sw_by_sentence.get(sent.id, [])
        scaffold_lemmas: set[int] = set()
        acquiring_count = 0
        for sw in sws:
            if not sw.lemma_id or sw.is_target_word:
                continue
            if sw.lemma_id in scaffold_lemmas:
                continue
            scaffold_lemmas.add(sw.lemma_id)
            ulk = knowledge_map.get(sw.lemma_id)
            if ulk and ulk.knowledge_state in ("acquiring", "learning", "lapsed"):
                acquiring_count += 1

        never_shown = (sent.times_shown or 0) == 0
        is_stale = acquiring_count == 0 and len(scaffold_lemmas) >= 2

        # Priority: lower = retire first
        # Never-shown sentences should be PROTECTED — they haven't had a chance
        # to be used yet. Stale shown sentences are the best retirement candidates.
        # 0 = shown + stale, 1 = shown (not stale), 2 = never-shown + stale, 3 = never-shown
        if not never_shown and is_stale:
            priority = 0
        elif not never_shown:
            priority = 1
        elif never_shown and is_stale:
            priority = 2
        else:
            priority = 3

        candidates.append((sent, priority))

    # Sort by priority (lowest first), then oldest
    candidates.sort(key=lambda x: (x[1], x[0].last_reading_shown_at or datetime.min))

    # Enforce min-active per target
    retire_count_per_target: dict[int | None, int] = {}
    retired = 0
    for sent, _ in candidates:
        if retired >= excess:
            break
        target_id = sent.target_lemma_id
        already_retiring = retire_count_per_target.get(target_id, 0)
        active = existing_counts.get(target_id, 0) if target_id else orphan_count
        wt = tier_lookup.get(target_id) if target_id else None
        floor = wt.cap_floor if wt else 0
        if active - already_retiring <= floor:
            continue

        # Last-reviewable-sentence guard for covered FSRS words.
        covered = _covered_protected(sent)
        if any(coverage.get(lid, 0) <= 1 for lid in covered):
            continue

        if not dry_run:
            sent.is_active = False
        for lid in covered:
            coverage[lid] = coverage.get(lid, 0) - 1
        retired += 1
        retire_count_per_target[target_id] = already_retiring + 1

    if not dry_run and retired > 0:
        db.commit()
        log_activity(
            db,
            event_type="sentences_retired",
            summary=f"Cap enforcement: retired {retired} sentences (cap={max_sentences})",
            detail={"retired": retired, "was_active": total_active, "cap": max_sentences},
        )

    print(f"  → Retired {retired} sentences (target was {excess})")
    return retired


# ── Step A: Backfill sentences for words, prioritized by due date ────

def step_backfill_sentences(
    db: Session, dry_run: bool, model: str, delay: float,
    max_sentences: int = TARGET_PIPELINE_SENTENCES,
    max_step_a_sentences: int | None = None,
    allow_single_word_fallback: bool = False,
    tier_lookup: dict | None = None,
) -> int:
    print("\n═══ Step A: Backfill sentences (due-date priority) ═══")

    # Compute tiers if not provided
    if tier_lookup is None:
        from app.services.pipeline_tiers import compute_word_tiers, build_tier_lookup
        word_tiers = compute_word_tiers(db)
        tier_lookup = build_tier_lookup(word_tiers)
    else:
        word_tiers = sorted(
            tier_lookup.values(),
            key=lambda w: (w.due_dt or datetime.max.replace(tzinfo=timezone.utc)),
        )

    from app.services.pipeline_tiers import tier_summary
    ts = tier_summary(word_tiers)
    print(f"  Tier distribution: T1={ts.get(1, 0)} T2={ts.get(2, 0)} T3={ts.get(3, 0)} T4={ts.get(4, 0)}")

    existing_counts = get_existing_counts(db)
    total_active = sum(existing_counts.values())
    print(f"  Total active sentences: {total_active}")
    print(f"  Pipeline cap: {max_sentences}")

    if total_active >= max_sentences:
        print(f"  Pipeline full ({total_active} >= {max_sentences}), skipping.")
        return 0

    if max_step_a_sentences is None:
        max_step_a_sentences = _env_int("ALIF_STEP_A_SENTENCE_BUDGET", DEFAULT_STEP_A_SENTENCE_BUDGET)
    if max_step_a_sentences <= 0:
        print(f"  Step A sentence budget disabled ({max_step_a_sentences}); skipping.")
        return 0

    capacity = max_sentences - total_active
    budget = min(capacity, max_step_a_sentences)
    print(f"  Step A budget: {budget} sentences (capacity to cap: {capacity})")

    known_words, lemma_lookup = get_known_words_and_lookup(db)
    content_word_counts = get_content_word_counts(db)
    avoid_words = get_avoid_words(content_word_counts, known_words)

    acquiring_rescue_words = acquiring_material_gaps(
        db,
        limit=max(0, int(os.environ.get("ALIF_ACQUIRING_RESCUE_LIMIT", "80"))),
    )

    # Skip lemmas currently in generation backoff (3+ consecutive failed runs).
    # They are re-admitted when the backoff_until timestamp expires; any later
    # success clears the counter. Acquiring rescue is stricter: already-active
    # study words with missing material are attempted even while on backoff.
    rescue_ids = {w["lemma_id"] for w in acquiring_rescue_words}
    candidate_ids = list({
        wt.lemma_id for wt in word_tiers if wt.backfill_target > 0
    } | rescue_ids)
    backoff_ids = lemmas_on_backoff(db, candidate_ids)
    rescue_backoff_ids = backoff_ids & rescue_ids
    ordinary_backoff_ids = backoff_ids - rescue_backoff_ids
    if ordinary_backoff_ids:
        print(f"  Skipping {len(ordinary_backoff_ids)} non-rescue words in generation backoff")
    if rescue_backoff_ids:
        print(f"  Overriding backoff for {len(rescue_backoff_ids)} acquiring material rescue gaps")

    # Collect words needing sentences — tier-based targets
    words_needing: list[dict] = []
    seen_needing: set[int] = set()
    rescue_ready = 0
    for w in acquiring_rescue_words:
        lid = w["lemma_id"]
        words_needing.append({
            **w,
            "needed": min(w["needed"], budget),
        })
        seen_needing.add(lid)
        rescue_ready += 1
    if acquiring_rescue_words:
        print(
            f"  Acquiring material rescue gaps: {rescue_ready} "
            f"(of {len(acquiring_rescue_words)} found)"
        )

    for wt in word_tiers:
        if wt.backfill_target <= 0:
            continue  # tier 4: skip, JIT fills when needed
        if wt.lemma_id in seen_needing:
            continue
        if wt.lemma_id in ordinary_backoff_ids:
            continue
        existing = existing_counts.get(wt.lemma_id, 0)
        needed = wt.backfill_target - existing
        if needed <= 0:
            continue
        lemma = db.query(Lemma).filter(Lemma.lemma_id == wt.lemma_id).first()
        if not lemma:
            continue
        if _is_generation_inert_lemma(lemma):
            continue
        words_needing.append({
            "lemma_id": wt.lemma_id,
            "lemma_ar": lemma.lemma_ar,
            "lemma_ar_bare": lemma.lemma_ar_bare,
            "gloss_en": lemma.gloss_en or "",
            "pos": lemma.pos or "",
            "root_id": lemma.root_id,
            "due_str": wt.due_dt.isoformat() if wt.due_dt else "none",
            "existing": existing,
            "needed": min(needed, budget),
            "tier": wt.tier,
            "backfill_target": wt.backfill_target,
        })

    print(f"  Words needing sentences: {len(words_needing)} (of {len(word_tiers)} total)")

    salvaged = 0
    if words_needing and budget > 0:
        active_known_ids = {
            row[0] for row in db.query(UserLemmaKnowledge.lemma_id)
            .filter(UserLemmaKnowledge.knowledge_state.in_(["acquiring", "known", "learning", "lapsed"]))
            .all()
        }
        from app.services.sentence_eligibility import reviewable_coverage_counts

        needing_ids = {w["lemma_id"] for w in words_needing}
        coverage = reviewable_coverage_counts(db, lemma_ids=needing_ids)
        deficit_ids = {lid for lid in needing_ids if coverage.get(lid, 0) == 0}
        salvaged = salvage_due_dense_inactive_sentences(
            db=db,
            target_lemma_ids=needing_ids,
            known_lemma_ids=active_known_ids,
            budget=budget,
            dry_run=dry_run,
            deficit_lemma_ids=deficit_ids,
        )
        if salvaged:
            budget -= salvaged
            existing_counts = get_existing_counts(db)
            words_needing = [
                w for w in words_needing
                if existing_counts.get(w["lemma_id"], 0) < w["backfill_target"]
            ]
            print(f"  Salvaged {salvaged}; remaining words needing generation: {len(words_needing)}")

    # Backoff-aware multi-target: backed-off lemmas are excluded from the
    # self-correct paths (where they keep failing) but get free recovery
    # attempts as collateral in multi-target groups, capped at 1 per group of
    # ≤4 healthy lemmas. The original concern from #37 was chronic failures
    # crowding out viable lemmas; the cap keeps groups majority-healthy while
    # giving backoff lemmas a path back. A successful generation auto-resets
    # the counter via record_generation_outcome().
    backoff_recovery_words: list[dict] = []
    if ordinary_backoff_ids and len(words_needing) >= 2:
        sample_n = min(len(ordinary_backoff_ids), max(1, len(words_needing) // 3))
        for lid in random.sample(list(ordinary_backoff_ids), sample_n):
            lemma = db.query(Lemma).filter(Lemma.lemma_id == lid).first()
            if not lemma or not (lemma.gloss_en or "").strip():
                continue
            if _is_generation_inert_lemma(lemma):
                continue
            backoff_recovery_words.append({
                "lemma_id": lid,
                "lemma_ar": lemma.lemma_ar,
                "gloss_en": lemma.gloss_en or "",
                "pos": lemma.pos or "",
                "root_id": lemma.root_id,
                "due_str": "backoff",
                "existing": existing_counts.get(lid, 0),
                "needed": 1,
                "tier": 4,
                "backfill_target": 1,
            })
        if backoff_recovery_words:
            print(f"  Including {len(backoff_recovery_words)} backed-off lemmas as multi-target recovery (≤1/group)")

    total = salvaged
    words_processed = 0
    covered_by_multi: set[int] = set()
    covered_by_batch: set[int] = set()
    covered_single: set[int] = set()
    attempted_lemma_ids: set[int] = set()

    # Phase 1: Multi-target generation for groups of 2-4 words.
    # Split into generate (LLM) → validate (LLM, read-only DB) → write (DB only),
    # with db.commit() between phases so the write lock is never held during
    # an LLM call.
    if not dry_run and len(words_needing) >= 2:
        from app.services.material_generator import (
            validate_multi_target_sentences_batch,
            write_multi_target_sentence,
        )
        groups = group_words_for_multi_target(words_needing)
        if backoff_recovery_words:
            groups = _augment_groups_with_recovery(groups, backoff_recovery_words)

        # Phase 1a: generate all multi-target sentences (LLM only, no DB writes)
        all_multi_results: list[tuple[list, dict[str, int]]] = []
        for group in groups:
            if total + sum(len(r) for r, _ in all_multi_results) >= budget:
                break
            print(f"  Multi-target group: {', '.join(w['lemma_ar'] for w in group)}")
            attempted_lemma_ids.update(w["lemma_id"] for w in group)
            try:
                multi_results = generate_validated_sentences_multi_target(
                    target_words=group,
                    known_words=known_words,
                    existing_sentence_counts=existing_counts,
                    count=len(group),
                    difficulty_hint="beginner",
                    content_word_counts=content_word_counts,
                    avoid_words=avoid_words,
                    lemma_lookup=lemma_lookup,
                    model_override=model,
                )
                target_bares = {strip_diacritics(tw["lemma_ar"]): tw["lemma_id"] for tw in group}
                all_multi_results.append((multi_results, target_bares))
            except Exception as e:
                print(f"    Multi-target failed: {e}")
                continue

            if delay > 0:
                time.sleep(delay)

        # Ensure session is clean before the validation LLM calls —
        # prior steps in this cron run may have left uncommitted writes that
        # would otherwise be autoflushed during a Lemma lookup, acquiring the
        # write lock for the duration of the 15-25s verify_and_correct calls.
        db.commit()

        # Phase 1b: validate generated sentences (batched LLM disambig + verify).
        # DB reads only; no writes. Collect the validated mappings to hand
        # off to the write phase.
        candidates: list[tuple[object, dict[str, int]]] = []
        for multi_results, target_bares in all_multi_results:
            for mres in multi_results:
                if total + len(candidates) >= budget:
                    break
                candidates.append((mres, target_bares))
        validated = validate_multi_target_sentences_batch(
            db, candidates, lemma_lookup,
        )

        # Phase 1c: write validated sentences (pure DB, milliseconds).
        for mres, mappings in validated:
            if total >= budget:
                break
            sent = write_multi_target_sentence(db, mres, mappings)
            total += 1
            words_processed += 1
            for lid in mres.target_lemma_ids:
                covered_by_multi.add(lid)
                existing_counts[lid] = existing_counts.get(lid, 0) + 1
            print(f"    ✓ Multi-target sentence covering {len(mres.target_lemma_ids)} words")

        if validated:
            db.commit()

    # Phase 2: Batch single-target for remaining words.
    # Do not fan failed batches out into many one-lemma Claude sessions during
    # cron. One- and two-word needs still run as a single bounded batch.
    remaining = [
        w for w in words_needing
        if existing_counts.get(w["lemma_id"], 0) < w["backfill_target"]
    ]
    remaining_ids = [w["lemma_id"] for w in remaining]

    if not dry_run and remaining_ids and total < budget:
        from app.services.material_generator import batch_generate_material, BATCH_WORD_SIZE
        for i in range(0, len(remaining_ids), BATCH_WORD_SIZE):
            if total >= budget:
                break
            chunk = remaining_ids[i:i + BATCH_WORD_SIZE]
            attempted_lemma_ids.update(chunk)
            print(f"  Batch generating for {len(chunk)} words...")
            result = batch_generate_material(chunk, model_override=model)
            batch_stored = result.get("generated", 0)
            total += batch_stored
            words_processed += result.get("words_covered", 0)
            if batch_stored:
                print(f"    ✓ Batch: {batch_stored} sentences for {result['words_covered']} words")
            for lid in chunk:
                if lid not in result.get("words_failed", []):
                    covered_by_batch.add(lid)
        # Optional manual escape hatch for rescue operations. Cron keeps this
        # off so a bad batch cannot turn into dozens of single-target sessions.
        failed_after_batch = [lid for lid in remaining_ids if lid not in covered_by_batch]
        if failed_after_batch and not allow_single_word_fallback:
            print(f"  Skipping single-word fallback for {len(failed_after_batch)} batch misses")
        if failed_after_batch and allow_single_word_fallback:
            print(f"  Single-word fallback enabled for {len(failed_after_batch)} batch misses")
        for lid in remaining_ids:
            if not allow_single_word_fallback or lid in covered_by_batch or total >= budget:
                continue
            lemma = db.query(Lemma).filter(Lemma.lemma_id == lid).first()
            if not lemma:
                continue
            w = next((w for w in remaining if w["lemma_id"] == lid), None)
            if not w:
                continue
            needed = min(w["backfill_target"] - existing_counts.get(lid, 0), budget - total)
            if needed <= 0:
                continue
            words_processed += 1
            print(f"  {lemma.lemma_ar} ({lemma.gloss_en}) — fallback single-word, need {needed}")
            stored = generate_material_for_word(lemma.lemma_id, needed=needed, model_override=model)
            total += stored
            if stored:
                print(f"    Generated {stored} sentences")
                covered_single.add(lid)
    else:
        # Dry run: simulate the work the live run would attempt.
        for w in remaining:
            if total >= budget:
                break
            lemma_id = w["lemma_id"]
            existing = existing_counts.get(lemma_id, 0)
            needed = w["backfill_target"] - existing
            if needed <= 0:
                continue
            needed = min(needed, budget - total)
            lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
            if not lemma:
                continue
            words_processed += 1
            print(f"  {lemma.lemma_ar} ({lemma.gloss_en}) — have {existing}, need {needed}, due {w['due_str'][:10]}")
            if dry_run:
                total += needed

    # Record generation outcome per attempted lemma so chronically-failing words
    # are excluded from future runs via the generation_backoff_until timestamp.
    if not dry_run and words_needing:
        covered = covered_by_multi | covered_by_batch | covered_single
        for w in words_needing:
            lid = w["lemma_id"]
            if lid not in attempted_lemma_ids:
                continue
            record_generation_result(db, lid, 1 if lid in covered else 0)
        # Backoff-recovery lemmas: only record successes (which clear the counter).
        # Don't penalise failures — they're already on backoff and not in
        # words_needing; recording another 0-result here would just push their
        # backoff_until further out without giving them another chance until
        # they age out. A success via multi-target collateral resets normally.
        for w in backoff_recovery_words:
            lid = w["lemma_id"]
            if lid in covered:
                record_generation_result(db, lid, 1)

    print(f"  → Generated {total} sentences for {words_processed} words")
    return total


# ── Step B: Generate audio for review-eligible sentences ─────────────

def get_audio_eligible_sentences(db: Session) -> list[Sentence]:
    """A sentence is audio-eligible when it's approaching listening readiness.

    To be conservative with TTS costs, we only generate audio for sentences
    where every word has stability >= 3 days and times_seen >= 3 — meaning
    the user knows the words well enough that listening practice is near.
    """
    import json as _json

    sentences = (
        db.query(Sentence)
        .filter(Sentence.audio_url.is_(None), Sentence.is_active == True)  # noqa: E712
        .all()
    )

    eligible = []
    for sent in sentences:
        words = db.query(SentenceWord).filter(SentenceWord.sentence_id == sent.id).all()
        if not words:
            continue

        ready = True
        for sw in words:
            if sw.lemma_id is None:
                continue
            ulk = (
                db.query(UserLemmaKnowledge)
                .filter(UserLemmaKnowledge.lemma_id == sw.lemma_id)
                .first()
            )
            if not ulk or (ulk.times_seen or 0) < 3:
                ready = False
                break
            # Check FSRS stability >= 3 days
            if ulk.fsrs_card_json:
                card = ulk.fsrs_card_json
                if isinstance(card, str):
                    card = _json.loads(card)
                if (card.get("stability") or 0) < 3.0:
                    ready = False
                    break
            else:
                ready = False
                break

        if ready:
            eligible.append(sent)

    return eligible


MIN_AUDIO_BACKLOG = 30


async def step_generate_audio(db: Session, dry_run: bool, limit: int) -> int:
    print("\n═══ Step B: Generate audio for review-eligible sentences ═══")
    if not audio_generation_enabled():
        print("  Audio generation disabled (ALIF_AUDIO_ENABLED is not set); skipping.")
        return 0

    # Check existing audio backlog — only generate if below minimum
    existing_audio = (
        db.query(Sentence)
        .filter(Sentence.audio_url.isnot(None), Sentence.is_active == True)  # noqa: E712
        .count()
    )
    print(f"  Current audio backlog: {existing_audio} sentences")
    if existing_audio >= MIN_AUDIO_BACKLOG:
        print(f"  Backlog sufficient (>= {MIN_AUDIO_BACKLOG}), skipping audio generation.")
        return 0

    needed = MIN_AUDIO_BACKLOG - existing_audio
    print(f"  Need {needed} more sentences with audio")

    eligible = get_audio_eligible_sentences(db)
    if not eligible:
        print("  No audio-eligible sentences found.")
        return 0

    print(f"  Found {len(eligible)} eligible sentences without audio")
    # Cap at what we actually need to reach the backlog minimum
    eligible = eligible[:needed]
    if limit > 0:
        eligible = eligible[:limit]
        print(f"  Limited to {limit}")

    if dry_run:
        print(f"  [dry-run] Would generate {len(eligible)} audio files")
        return len(eligible)

    generated = 0
    for sent in eligible:
        key = cache_key_for(sent.arabic_text, DEFAULT_VOICE_ID)
        if get_cached_path(key):
            sent.audio_url = f"/api/tts/audio/{key}.mp3"
            generated += 1
            continue
        try:
            path = await generate_and_cache(
                sent.arabic_text, DEFAULT_VOICE_ID, cache_key=key, slow_mode=True,
            )
            sent.audio_url = f"/api/tts/audio/{path.name}"
            generated += 1
            print(f"    ✓ Sentence {sent.id}: {sent.arabic_text[:40]}...")
            await asyncio.sleep(0.5)
        except (TTSError, TTSKeyMissing) as e:
            print(f"    ✗ Sentence {sent.id}: {e}")
            continue

    db.commit()
    print(f"  → Total audio generated: {generated}")
    return generated


# ── Step C: Pre-generate for upcoming candidates ─────────────────────

def step_pregenerate_candidates(db: Session, dry_run: bool, count: int, model: str, delay: float) -> int:
    print("\n═══ Step C: Pre-generate sentences for upcoming candidates ═══")

    # Check pipeline capacity first
    existing_counts = get_existing_counts(db)
    total_active = sum(existing_counts.values())
    if total_active >= TARGET_PIPELINE_SENTENCES:
        print(f"  Pipeline full ({total_active} >= {TARGET_PIPELINE_SENTENCES}), skipping.")
        return 0

    try:
        from app.services.frequency_core_intake import (
            DEFAULT_LIMIT as FREQ_CORE_INTAKE_LIMIT,
            DEFAULT_MAX_RANK as FREQ_CORE_INTAKE_MAX_RANK,
            DEFAULT_RETRY_COOLDOWN_HOURS as FREQ_CORE_INTAKE_RETRY_COOLDOWN_HOURS,
            DEFAULT_RETRY_LIMIT as FREQ_CORE_INTAKE_RETRY_LIMIT,
            intake_frequency_core_gaps,
        )
        intake_limit = max(
            0,
            int(os.environ.get("ALIF_FREQ_CORE_INTAKE_LIMIT", str(FREQ_CORE_INTAKE_LIMIT))),
        )
        intake_max_rank = max(
            0,
            int(os.environ.get("ALIF_FREQ_CORE_INTAKE_MAX_RANK", str(FREQ_CORE_INTAKE_MAX_RANK))),
        )
        intake_retry_limit = max(
            0,
            int(os.environ.get("ALIF_FREQ_CORE_INTAKE_RETRY_LIMIT", str(FREQ_CORE_INTAKE_RETRY_LIMIT))),
        )
        intake_retry_cooldown_hours = max(
            0,
            int(os.environ.get(
                "ALIF_FREQ_CORE_INTAKE_RETRY_COOLDOWN_HOURS",
                str(FREQ_CORE_INTAKE_RETRY_COOLDOWN_HOURS),
            )),
        )
        intake = intake_frequency_core_gaps(
            db,
            limit=intake_limit,
            max_rank=intake_max_rank,
            retry_limit=intake_retry_limit,
            retry_cooldown_hours=intake_retry_cooldown_hours,
            dry_run=dry_run,
        )
        if any(intake.get(k) for k in ("resolved_existing", "created", "rejected", "errors")):
            print(
                "  Frequency-core intake: "
                f"resolved={intake.get('resolved_existing', 0)}, "
                f"created={intake.get('created', 0)}, "
                f"rejected={intake.get('rejected', 0)}, "
                f"errors={intake.get('errors', 0)}"
            )
    except Exception as exc:
        print(f"  Frequency-core intake failed: {exc}")

    # Safety net: heal any frequency-core entries that drifted onto a variant
    # lemma (e.g. an inflected form whose canonical was linked after mapping).
    # Idempotent; covers paths that set canonical_lemma_id directly.
    try:
        from app.services.frequency_core_intake import remap_variant_frequency_core_entries
        if not dry_run:
            remap = remap_variant_frequency_core_entries(db)
            if remap.get("remapped") or remap.get("excluded"):
                db.commit()
                print(
                    "  Frequency-core variant remap: "
                    f"remapped={remap['remapped']}, excluded_dupes={remap['excluded']}"
                )
    except Exception as exc:
        print(f"  Frequency-core variant remap failed: {exc}")

    candidates = select_next_words(db, count=count)
    if not candidates:
        print("  No candidates available.")
        return 0

    print(f"  Found {len(candidates)} upcoming candidates")

    budget = TARGET_PIPELINE_SENTENCES - total_active

    planned_total = 0
    candidate_needs: list[tuple[int, int]] = []
    for i, cand in enumerate(candidates):
        if planned_total >= budget:
            break
        lid = cand["lemma_id"]
        existing = existing_counts.get(lid, 0)
        needed = PREGEN_SENTENCES_PER_CANDIDATE - existing
        if needed <= 0:
            continue

        needed = min(needed, budget - planned_total)
        print(f"  [{i+1}/{len(candidates)}] {cand['lemma_ar']} ({cand['gloss_en']}) — "
              f"have {existing}, need {needed}")
        candidate_needs.append((lid, needed))
        planned_total += needed

    if dry_run:
        print(f"  → Total sentences: {planned_total}")
        return planned_total

    total = 0
    if candidate_needs:
        from collections import defaultdict
        from app.services.material_generator import batch_generate_material, BATCH_WORD_SIZE

        ids_by_needed: dict[int, list[int]] = defaultdict(list)
        for lid, needed in candidate_needs:
            ids_by_needed[needed].append(lid)

        for needed, lemma_ids in sorted(ids_by_needed.items(), reverse=True):
            for i in range(0, len(lemma_ids), BATCH_WORD_SIZE):
                chunk = lemma_ids[i:i + BATCH_WORD_SIZE]
                result = batch_generate_material(
                    chunk,
                    count_per_word=needed,
                    model_override=model,
                )
                stored = result.get("generated", 0)
                total += stored
                if stored:
                    print(
                        f"    Batch: {stored} sentences for "
                        f"{result.get('words_covered', 0)} words"
                    )

    print(f"  → Total sentences: {total}")
    return total


# ── Step G1b: Reactivate book sentences on comprehensible pages ──────

def step_reactivate_book_sentences(db: Session) -> int:
    """Activate book sentences whose pages are fully comprehensible.

    A page is comprehensible when every non-function-word lemma is
    known, learning, acquiring, or lapsed (i.e., no encountered or
    unknown-state words remain).  Sentences on such pages are activated
    so they enter the review pool — these are real passages the learner
    is working through, not disposable LLM scaffolding.
    """
    from collections import defaultdict
    from app.models import Story, StoryWord

    active_books = db.query(Story).filter(
        Story.source == "book_ocr", Story.status == "active",
    ).all()
    if not active_books:
        print("  No active books")
        return 0

    total_reactivated = 0
    for book in active_books:
        # Build page → lemma readiness map
        pages: dict[int, dict] = defaultdict(lambda: {"ready": True, "enc": 0})
        sw_rows = (
            db.query(StoryWord.page_number, StoryWord.lemma_id, StoryWord.is_function_word,
                     UserLemmaKnowledge.knowledge_state)
            .outerjoin(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == StoryWord.lemma_id)
            .filter(StoryWord.story_id == book.id, StoryWord.lemma_id.isnot(None))
            .all()
        )
        for page, lid, is_func, ks in sw_rows:
            if page is None:
                continue
            _ = pages[page]  # ensure page exists in dict even if all words are ready
            if is_func:
                continue
            if ks not in ("known", "learning", "acquiring", "lapsed"):
                pages[page]["ready"] = False
                pages[page]["enc"] += 1

        ready_pages = {p for p, info in pages.items() if info["ready"]}
        if not ready_pages:
            print(f"  {book.title_en}: no fully comprehensible pages yet")
            continue

        # Reactivate inactive sentences on ready pages
        inactive_on_ready = (
            db.query(Sentence)
            .filter(
                Sentence.story_id == book.id,
                Sentence.is_active == False,  # noqa: E712
                Sentence.page_number.in_(ready_pages),
                or_(
                    Sentence.mappings_verified_at.is_(None),
                    Sentence.mappings_verified_at.notin_(
                        CORPUS_NON_ACTIVATABLE_SENTINELS
                    ),
                ),
                outside_corpus_governor_clause(),
            )
            .all()
        )
        for sent in inactive_on_ready:
            sent.is_active = True
        if inactive_on_ready:
            db.commit()
            total_reactivated += len(inactive_on_ready)
            print(f"  {book.title_en}: reactivated {len(inactive_on_ready)} sentences on {len(ready_pages)} green pages")
        else:
            print(f"  {book.title_en}: {len(ready_pages)} green pages, all sentences already active")

    return total_reactivated


# ── Main ─────────────────────────────────────────────────────────────

def step_backfill_samer(db: Session, dry_run: bool) -> int:
    """Fill cefr_level from SAMER lexicon for any lemmas missing it."""
    samer_path = Path(__file__).resolve().parent.parent / "data" / "samer.tsv"
    if not samer_path.exists():
        return 0

    missing = db.query(Lemma).filter(
        Lemma.cefr_level.is_(None),
        Lemma.canonical_lemma_id.is_(None),
    ).all()
    if not missing:
        return 0

    print(f"\n═══ Step D: SAMER readability backfill ═══")
    from scripts.backfill_samer import load_samer, lookup_samer, SAMER_TO_CEFR
    samer = load_samer(str(samer_path))

    import re
    diac_re = re.compile(r'[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]')
    def normalize(t):
        t = diac_re.sub('', t).replace('\u0640', '')
        return re.sub(r'[أإآٱ]', 'ا', t)

    updated = 0
    for lemma in missing:
        bare = lemma.lemma_ar_bare
        if not bare:
            continue
        level = lookup_samer(samer, normalize(bare))
        if level is not None:
            if not dry_run:
                lemma.cefr_level = SAMER_TO_CEFR[level]
            updated += 1

    if not dry_run and updated > 0:
        db.commit()
    print(f"  Filled cefr_level for {updated}/{len(missing)} lemmas")
    return updated


async def main() -> int:
    parser = argparse.ArgumentParser(description="Unified material update workflow")
    parser.add_argument("--dry-run", action="store_true", help="Preview without changes")
    parser.add_argument("--skip-audio", action="store_true", help="Skip TTS audio generation")
    parser.add_argument("--limit", type=int, default=0, help="Max audio generations (0=unlimited)")
    parser.add_argument("--candidates", type=int, default=10, help="Number of upcoming candidates (default: 10)")
    parser.add_argument("--max-sentences", type=int, default=TARGET_PIPELINE_SENTENCES,
                        help=f"Max total active sentences safety cap (default: {TARGET_PIPELINE_SENTENCES})")
    parser.add_argument(
        "--max-step-a-sentences",
        type=int,
        default=_env_int("ALIF_STEP_A_SENTENCE_BUDGET", DEFAULT_STEP_A_SENTENCE_BUDGET),
        help=(
            "Max Step A sentences to generate this run "
            f"(default: {DEFAULT_STEP_A_SENTENCE_BUDGET}; ALIF_STEP_A_SENTENCE_BUDGET)"
        ),
    )
    parser.add_argument(
        "--allow-single-word-fallback",
        action="store_true",
        help="Allow per-lemma fallback after batch misses (manual rescue only; off for cron)",
    )
    parser.add_argument(
        "--run-lemma-enrichment",
        action="store_true",
        help="Run expensive lemma enrichment in this invocation (off by default for cron)",
    )
    parser.add_argument(
        "--run-corpus-enrichment",
        action="store_true",
        help=(
            "Run scoped corpus sentence enrichment during the full workflow "
            "(requires --kind and/or --corpus-sentence-id)"
        ),
    )
    parser.add_argument(
        "--only-corpus-enrichment",
        action="store_true",
        help="Run only scoped corpus enrichment/activation and no other maintenance",
    )
    parser.add_argument(
        "--kind",
        "--corpus-kind",
        dest="corpus_kind",
        default=None,
        help="Restrict corpus work to this exact Sentence.kind",
    )
    parser.add_argument(
        "--corpus-sentence-id",
        type=int,
        action="append",
        default=None,
        help="Restrict corpus work to an explicit sentence ID (repeatable)",
    )
    parser.add_argument(
        "--corpus-limit",
        type=int,
        default=DEFAULT_ENRICH_LIMIT,
        help=(
            "Maximum corpus rows to enrich "
            f"(default: {DEFAULT_ENRICH_LIMIT}, max: {MAX_ENRICH_LIMIT})"
        ),
    )
    parser.add_argument(
        "--corpus-activate-limit",
        type=int,
        default=0,
        help=(
            "Maximum prepared corpus rows to activate in an activation-only "
            "invocation (--corpus-limit 0) "
            f"(default: 0, max: {MAX_ACTIVATE_LIMIT})"
        ),
    )
    parser.add_argument(
        "--corpus-active-ceiling",
        type=int,
        default=TARGET_PIPELINE_SENTENCES - CAP_HEADROOM,
        help=(
            "Refuse corpus activation at or above this active-sentence count "
            f"(default: {TARGET_PIPELINE_SENTENCES - CAP_HEADROOM})"
        ),
    )
    parser.add_argument(
        "--corpus-retry-blocked",
        action="store_true",
        help=(
            "Retry only explicitly listed durable corpus blockers after "
            "reviewed inventory curation; requires --corpus-sentence-id, "
            "nonzero preparation, and zero activation"
        ),
    )
    parser.add_argument(
        "--run-pregeneration",
        action="store_true",
        help="Run speculative upcoming-word sentence pre-generation (off by default for cron)",
    )
    parser.add_argument("--model", default="claude_sonnet", help="LLM model for sentence gen (default: claude_sonnet)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between LLM calls")
    args = parser.parse_args()
    corpus_requested = args.only_corpus_enrichment or _run_corpus_enrichment(
        args.run_corpus_enrichment
    )
    _validate_corpus_cli_args(
        parser,
        args,
        corpus_requested=corpus_requested,
    )

    print(f"update_material.py — {'DRY RUN' if args.dry_run else 'LIVE RUN'}")
    print(
        f"  skip_audio={args.skip_audio}, limit={args.limit}, candidates={args.candidates}, "
        f"max_step_a_sentences={args.max_step_a_sentences}, "
        f"pregeneration={_run_pregeneration(args.run_pregeneration)}, "
        f"corpus_requested={corpus_requested}, corpus_kind={args.corpus_kind}, "
        f"corpus_ids={args.corpus_sentence_id}, corpus_limit={args.corpus_limit}, "
        f"corpus_activate_limit={args.corpus_activate_limit}, "
        f"corpus_active_ceiling={args.corpus_active_ceiling}, "
        f"corpus_retry_blocked={args.corpus_retry_blocked}"
    )
    start = time.time()

    lock_handle = None
    if not args.dry_run:
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        lock_handle = LOCK_PATH.open("a+")
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"  Another update_material.py run is active ({LOCK_PATH}); skipping.")
            lock_handle.close()
            return 75 if args.only_corpus_enrichment else 0
        lock_handle.seek(0)
        lock_handle.truncate(0)
        lock_handle.write(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n")
        lock_handle.flush()

    db = SessionLocal()
    try:
        if args.only_corpus_enrichment:
            print("\n═══ Scoped corpus enrichment only ═══")
            corpus_result = _run_scoped_corpus_step(db, args)
            elapsed = time.time() - start
            if corpus_result is None:
                print(f"\nCorpus dry run completed in {elapsed:.1f}s")
            else:
                rejected = _corpus_rejected_count(corpus_result)
                print(f"\nCorpus run completed in {elapsed:.1f}s")
                print(f"  Prepared:  {corpus_result.prepared}")
                print(f"  Activated: {corpus_result.activated}")
                print(f"  Retry:     {len(corpus_result.retry_ids)}")
                print(f"  Rejected:  {rejected}")
                print(
                    "  Claims recovered: "
                    f"{len(corpus_result.recovered_legacy_claim_ids)}"
                )
            return 0

        from app.services.pipeline_tiers import compute_word_tiers, build_tier_lookup, tier_summary
        word_tiers = compute_word_tiers(db)
        tier_lk = build_tier_lookup(word_tiers)
        ts = tier_summary(word_tiers)
        print(f"\n  Word tiers: T1={ts.get(1, 0)} T2={ts.get(2, 0)} T3={ts.get(3, 0)} T4={ts.get(4, 0)}")

        retired_0 = step_enforce_cap(db, args.dry_run, args.max_sentences, tier_lookup=tier_lk)

        # Re-attempt mapping for any active SentenceWord rows still NULL —
        # newer lemmas / clitic-stripping fixes can resolve old gaps. Auto-
        # creates proper-name lemmas (the documented exception). Sentences
        # with unresolvable common-word gaps remain is_active=True but stay
        # filtered by sentence_eligibility.not_has_unmapped_words at review
        # time until the missing lemma is added.
        print("\n═══ Step 0b: Re-map unmapped sentence_words ═══")
        if args.dry_run:
            print("  Skipped (dry run)")
        else:
            from scripts.fix_null_lemma_ids import remap_unmapped_sentence_words
            try:
                remap_stats = remap_unmapped_sentence_words(db)
                if remap_stats["fixed_by_lookup"] or remap_stats["fixed_by_proper_name"]:
                    log_activity(
                        db,
                        event_type="sentence_words_remapped",
                        summary=(
                            f"Remapped {remap_stats['fixed_by_lookup']} via lookup, "
                            f"{remap_stats['fixed_by_proper_name']} via proper-name auto-create"
                        ),
                        detail=remap_stats,
                    )
            except Exception as exc:
                print(f"  Remap step failed: {exc}")

        sent_a = step_backfill_sentences(
            db,
            args.dry_run,
            args.model,
            args.delay,
            args.max_sentences,
            max_step_a_sentences=args.max_step_a_sentences,
            allow_single_word_fallback=args.allow_single_word_fallback,
            tier_lookup=tier_lk,
        )

        # ── Step A2: Scoped corpus enrichment + bounded activation ───────
        corpus_a2: CorpusEnrichmentResult | None = None
        print("\n═══ Step A2: Scoped corpus enrichment and activation ═══")
        if not corpus_requested:
            print(
                "  Skipped (use --run-corpus-enrichment with --kind and/or "
                "--corpus-sentence-id)"
            )
        else:
            corpus_a2 = _run_scoped_corpus_step(db, args)

        if not args.skip_audio:
            audio_b = await step_generate_audio(db, args.dry_run, args.limit)
        else:
            audio_b = 0
            print("\n═══ Step B: Skipped (--skip-audio) ═══")

        sent_c = 0
        if not _run_pregeneration(args.run_pregeneration):
            print("\n═══ Step C: Pre-generate sentences for upcoming candidates ═══")
            print(
                "  Skipped (speculative; use --run-pregeneration or "
                "ALIF_RUN_CRON_PREGENERATION=1)"
            )
        else:
            sent_c = step_pregenerate_candidates(db, args.dry_run, args.candidates, args.model, args.delay)

        samer_d = step_backfill_samer(db, args.dry_run)

        # Step E: Enrich ALL lemmas missing forms/etymology/grammar/examples/roots
        enrich_e = 0
        print("\n═══ Step E: Enrich unenriched lemmas ═══")
        if not _run_lemma_enrichment(args.run_lemma_enrichment):
            print(
                "  Skipped (expensive; use --run-lemma-enrichment or "
                "ALIF_RUN_CRON_LEMMA_ENRICHMENT=1)"
            )
        else:
            # NOTE: memory_hooks_json is deliberately absent from this filter —
            # hooks are lazy-generated on first failed review (see the Step 4
            # note in lemma_enrichment.py), so "missing hooks" is not backlog.
            # Including it inflated the work list ~7x (1,789 phantom rows on
            # 2026-07-15) and starved real enrichment out of the cron timeout.
            unenriched = (
                db.query(Lemma.lemma_id)
                .filter(
                    Lemma.canonical_lemma_id.is_(None),
                    (
                        Lemma.forms_json.is_(None)
                        | Lemma.etymology_json.is_(None)
                        | (Lemma.grammar_features_json.is_(None) & Lemma.pos.in_(["noun", "verb", "adjective", "adj"]))
                        | (Lemma.example_ar.is_(None) & Lemma.pos.in_(["noun", "verb", "adjective", "adj"]))
                        | (Lemma.root_id.is_(None) & Lemma.pos.in_(["noun", "verb", "adjective", "adj"]))
                    ),
                )
                .order_by(Lemma.lemma_id.desc())
                .all()
            )
            unenriched_ids = [r[0] for r in unenriched]
            # Bound the pass so it finishes inside the cron wrapper's
            # MAINTENANCE_TIMEOUT_SECONDS (900s) — an unbounded pass was being
            # killed at the timeout with all progress lost. Newest lemmas first:
            # they're the ones the user just imported and is about to review.
            enrich_cap = int(os.environ.get("ALIF_CRON_ENRICH_LIMIT", "80"))
            if len(unenriched_ids) > enrich_cap:
                print(
                    f"  Found {len(unenriched_ids)} lemmas to enrich; processing the "
                    f"newest {enrich_cap} this pass, deferring "
                    f"{len(unenriched_ids) - enrich_cap} (ALIF_CRON_ENRICH_LIMIT)"
                )
                unenriched_ids = unenriched_ids[:enrich_cap]
            if unenriched_ids:
                print(f"  Enriching {len(unenriched_ids)} lemmas")
                if not args.dry_run:
                    from app.services.lemma_enrichment import enrich_lemmas_batch
                    result = enrich_lemmas_batch(unenriched_ids)
                    enrich_e = (result.get("forms", 0) + result.get("etymology", 0)
                                + result.get("roots", 0) + result.get("grammar", 0) + result.get("examples", 0))
            else:
                print("  All lemmas enriched")

        # Step F: Reintroduce leeches past cooldown
        leech_f = 0
        print("\n═══ Step F: Leech reintroductions ═══")
        if not args.dry_run:
            from app.services.leech_service import check_leech_reintroductions
            reintroduced = check_leech_reintroductions(db)
            leech_f = len(reintroduced)
            if leech_f:
                print(f"  Reintroduced {leech_f} leeches: {reintroduced}")
            else:
                print("  No leeches ready for reintroduction")
        else:
            print("  Skipped (dry run)")

        # Step G: Ensure all active book words have ULK records
        book_ulk_g = 0
        print("\n═══ Step G: Book ULK consistency ═══")
        if not args.dry_run:
            from app.models import Story, StoryWord, UserLemmaKnowledge as ULK
            active_books = db.query(Story).filter(
                Story.source == "book_ocr", Story.status == "active"
            ).all()
            for book in active_books:
                book_lids = {
                    sw.lemma_id for sw in book.words
                    if sw.lemma_id and not sw.is_function_word
                }
                if not book_lids:
                    continue
                existing = {
                    r[0] for r in db.query(ULK.lemma_id)
                    .filter(ULK.lemma_id.in_(book_lids)).all()
                }
                missing = book_lids - existing
                for lid in missing:
                    db.add(ULK(
                        lemma_id=lid,
                        knowledge_state="encountered",
                        source="book",
                        total_encounters=1,
                    ))
                    book_ulk_g += 1
                if missing:
                    db.commit()
            if book_ulk_g:
                print(f"  Created {book_ulk_g} missing ULK records for book words")
            else:
                print("  All book words have ULK records")
        else:
            print("  Skipped (dry run)")

        # ── Step G1b: Reactivate book sentences on comprehensible pages ──
        book_reactivated = 0
        print("\n═══ Step G1b: Book sentence reactivation ═══")
        if not args.dry_run:
            book_reactivated = step_reactivate_book_sentences(db)
        else:
            print("  Skipped (dry run)")

        # ── Step G2: Catch ungated lemmas ────────────────────────────
        ungated_g2 = 0
        print("\n═══ Step G2: Catch ungated lemmas ═══")
        if not args.dry_run:
            ungated = (
                db.query(Lemma.lemma_id)
                .filter(Lemma.gates_completed_at.is_(None))
                .all()
            )
            ungated_ids = [r[0] for r in ungated]
            if ungated_ids:
                from app.services.lemma_quality import run_quality_gates
                print(f"  Found {len(ungated_ids)} ungated lemmas — running quality gates")
                result = run_quality_gates(
                    db, ungated_ids,
                    background_enrich=False,
                )
                ungated_g2 = result.get("stamped", 0)
                print(f"  Stamped {ungated_g2} lemmas")
            else:
                print("  All lemmas gated")
        else:
            print("  Skipped (dry run)")

        # ── Step G2b: Root-showcase refresh ────────────────────────────
        # For roots that have fewer than MIN_ACTIVE active showcase sentences
        # AND a palette of ≥4 introduced lemmas, regenerate. Capped per run so
        # one cron pass can't burn unbounded LLM spend. Skip silently if there
        # are no eligible roots — no error, no log noise.
        showcase_g2b = 0
        print("\n═══ Step G2b: Root-showcase refresh ═══")
        if not args.dry_run:
            from app.services.root_showcase import (
                build_palette_for_root,
                generate_and_store_showcases_for_root,
            )
            from app.models import Root as _Root

            MIN_ACTIVE_SHOWCASES = 2
            MIN_PALETTE_FOR_REFRESH = 4
            MAX_ROOTS_PER_CRON_RUN = 3
            COUNT_PER_REFRESH = 3

            active_per_root = dict(
                db.query(Sentence.root_focus_id, func.count(Sentence.id))
                .filter(Sentence.kind == "root_showcase")
                .filter(Sentence.is_active.is_(True))
                .filter(Sentence.root_focus_id.isnot(None))
                .group_by(Sentence.root_focus_id)
                .all()
            )
            # Also consider roots with ZERO showcases (not in the count above)
            # — pull all roots that have any focused sentence history OR a
            # large palette. Cheap to enumerate; main loop short-circuits.
            roots_with_showcases = set(active_per_root.keys())
            # Don't enumerate every root in the DB — only those with at least
            # one historical showcase OR explicitly seeded. The bootstrap run
            # (generate_root_showcases.py) seeds the initial set; cron only
            # refreshes those, never introduces brand-new roots on its own.
            depleted_root_ids = [
                rid for rid in roots_with_showcases
                if active_per_root.get(rid, 0) < MIN_ACTIVE_SHOWCASES
            ]

            refreshed = 0
            for rid in depleted_root_ids[:MAX_ROOTS_PER_CRON_RUN]:
                palette = build_palette_for_root(db, rid)
                if len(palette) < MIN_PALETTE_FOR_REFRESH:
                    continue
                root_row = db.query(_Root).filter(_Root.root_id == rid).first()
                root_str = root_row.root if root_row else f"#{rid}"
                print(f"  Refreshing {root_str}: active={active_per_root.get(rid, 0)}, palette={len(palette)}")
                try:
                    result = generate_and_store_showcases_for_root(
                        db, rid, count=COUNT_PER_REFRESH,
                    )
                    showcase_g2b += result.persisted
                    refreshed += 1
                    print(f"    persisted={result.persisted}, generated={result.generated}")
                except Exception as e:
                    logger.exception(f"Showcase refresh failed for root {rid}")
                    print(f"    failed: {e}")
                    # Clean dirty state so the next iteration / next step
                    # doesn't inherit a poisoned session
                    db.rollback()
            if refreshed == 0:
                print(f"  No roots eligible for refresh ({len(depleted_root_ids)} depleted, "
                      f"none met palette≥{MIN_PALETTE_FOR_REFRESH} threshold)")
        else:
            print("  Skipped (dry run)")

        # ── Step G3: FSRS difficulty reconciliation ────────────────────
        diff_g3 = 0
        print("\n═══ Step G3: FSRS difficulty reconciliation ═══")
        if not args.dry_run:
            from scripts.repair_fsrs_cards import find_affected_words, replay_reviews
            affected = find_affected_words(db)
            for lemma_id, info in affected.items():
                new_card, new_state = replay_reviews(db, lemma_id)
                if new_card is None:
                    continue
                old_diff = info.get("old_difficulty") or 0
                new_diff = new_card.get("difficulty", 0)
                if old_diff - new_diff > 0.5 or info.get("null_card"):
                    db.execute(text("""
                        UPDATE user_lemma_knowledge
                        SET fsrs_card_json = :card, knowledge_state = :state
                        WHERE lemma_id = :lid
                    """), {"card": json.dumps(new_card), "state": new_state, "lid": lemma_id})
                    diff_g3 += 1
            if diff_g3:
                db.commit()
                print(f"  Repaired {diff_g3} FSRS cards (difficulty reconciliation)")
            else:
                print("  No cards need repair")
        else:
            print("  Skipped (dry run)")

        # ── Step H: auto-generate stories ────────────────────────────
        stories_h = 0
        STORY_TARGET = 3  # keep at least 3 non-archived active stories
        STORY_FORMATS = ["standard", "standard", "long", "breakdown", "arabic_explanation"]
        print(f"\n[H] Auto-generate stories (target ≥ {STORY_TARGET} active non-archived)")
        if not args.dry_run:
            from app.models import Story as StoryModel
            from app.services.story_service import generate_story as gen_story
            active_stories = db.query(StoryModel).filter(
                StoryModel.status == "active",
                StoryModel.archived_at.is_(None),
                StoryModel.source != "book_ocr",
            ).count()
            deficit = STORY_TARGET - active_stories
            print(f"  Active non-archived stories: {active_stories}, need {max(0, deficit)} more")
            for i in range(deficit):
                fmt = STORY_FORMATS[i % len(STORY_FORMATS)]
                length = random.choice(["short", "medium", "long"])
                try:
                    print(f"  Generating story {i+1}/{deficit} (format={fmt}, length={length})...")
                    story_obj, new_ids = gen_story(
                        db, difficulty="beginner", length=length,
                        format_type=fmt,
                    )
                    stories_h += 1
                    print(f"  Generated: '{story_obj.title_en}' ({story_obj.total_words} words)")
                except Exception as e:
                    print(f"  Story generation failed: {e}")
                    logger.exception("Step H story generation failed")
        else:
            print("  Skipped (dry run)")

        # ── Step I: auto-generate podcasts ─────────────────────────
        podcasts_i = 0
        PODCAST_TARGET = 4  # keep at least 4 unheard podcasts
        MAX_PODCAST_PER_RUN = 2  # limit TTS cost per cron run
        print(f"\n[I] Auto-generate podcasts (target ≥ {PODCAST_TARGET} unheard)")
        if not audio_generation_enabled():
            print("  Audio generation disabled (ALIF_AUDIO_ENABLED is not set); skipping podcasts.")
        elif not args.dry_run:
            from app.services.podcast_service import unheard_count
            from scripts.generate_story_podcasts import (
                generate_ci_podcast,
                generate_single_podcast,
                get_high_stability_words,
                pick_unused_ci_topic,
                pick_unused_theme,
            )
            from scripts.generate_repetition_podcasts import generate_single_repetition_podcast
            from scripts.generate_podcast_images import generate_image, ART_STYLE, THEME_PROMPTS

            current_unheard = unheard_count()
            deficit = min(PODCAST_TARGET - current_unheard, MAX_PODCAST_PER_RUN)
            print(f"  Unheard podcasts: {current_unheard}, need {max(0, deficit)} more")
            if deficit > 0:
                words = get_high_stability_words(db, min_stability_days=14.0)
                has_words = len(words) >= 30
                generated_paths: list[Path] = []
                # 3-way rotation: story → CI → repetition
                for i in range(deficit):
                    try:
                        fmt_idx = (current_unheard + podcasts_i) % 3
                        if fmt_idx == 1 and has_words:
                            ci = pick_unused_ci_topic()
                            if ci:
                                print(f"  Generating CI podcast {i+1}/{deficit}: {ci['topic'][:50]}...")
                                path = await generate_ci_podcast(db, words, ci["topic"], ci["target"])
                                if path:
                                    podcasts_i += 1
                                    generated_paths.append(path)
                                    print(f"  Generated: {path.name}")
                                continue
                        if fmt_idx == 2:
                            print(f"  Generating repetition podcast {i+1}/{deficit}...")
                            path = await generate_single_repetition_podcast(db)
                            if path:
                                podcasts_i += 1
                                generated_paths.append(path)
                                print(f"  Generated: {path.name}")
                            continue
                        if has_words:
                            theme = pick_unused_theme()
                            print(f"  Generating story podcast {i+1}/{deficit}: {theme['title']}...")
                            path = await generate_single_podcast(db, words, theme)
                            if path:
                                podcasts_i += 1
                                generated_paths.append(path)
                                print(f"  Generated: {path.name}")
                    except Exception as e:
                        print(f"  Podcast generation failed: {e}")
                        logger.exception("Step I podcast generation failed")

                # Auto-generate cover images for new podcasts
                if generated_paths:
                    api_key = os.environ.get("GEMINI_KEY")
                    if api_key:
                        for path in generated_paths:
                            try:
                                stem = path.stem
                                image_path = path.parent / f"{stem}.png"
                                if image_path.exists():
                                    continue
                                meta_path = path.parent / f"{stem}.json"
                                if not meta_path.exists():
                                    continue
                                meta = json.loads(meta_path.read_text())
                                theme_id = meta.get("theme_id", "")
                                if theme_id in THEME_PROMPTS:
                                    prompt = THEME_PROMPTS[theme_id]
                                else:
                                    summary = meta.get("summary", "An Arabic language learning podcast")
                                    prompt = f"Illustration for a story: {summary}. {ART_STYLE}"
                                image_bytes = generate_image(prompt, api_key)
                                if image_bytes:
                                    image_path.write_bytes(image_bytes)
                                    meta["image_filename"] = image_path.name
                                    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
                                    print(f"  Generated image: {image_path.name}")
                            except Exception as e:
                                print(f"  Image generation failed for {path.name}: {e}")
                if not has_words and podcasts_i == 0:
                    print(f"  Not enough high-stability words ({len(words)})")
        else:
            print("  Skipped (dry run)")

        elapsed = time.time() - start
        corpus_prepared_a2 = corpus_a2.prepared if corpus_a2 else 0
        corpus_activated_a2 = corpus_a2.activated if corpus_a2 else 0
        corpus_retry_a2 = len(corpus_a2.retry_ids) if corpus_a2 else 0
        corpus_rejected_a2 = _corpus_rejected_count(corpus_a2)
        corpus_touched_a2 = (
            len(
                set(corpus_a2.selected_ids)
                | set(corpus_a2.recovered_legacy_claim_ids)
                | set(corpus_a2.activated_ids)
            )
            if corpus_a2
            else 0
        )
        print(f"\n{'─' * 60}")
        print(f"Done in {elapsed:.1f}s")
        print(f"  Step 0 retired:   {retired_0}")
        print(f"  Step A sentences: {sent_a}")
        print(f"  Step A2 prepared: {corpus_prepared_a2}")
        print(f"  Step A2 activated: {corpus_activated_a2}")
        print(f"  Step A2 retry:    {corpus_retry_a2}")
        print(f"  Step A2 rejected: {corpus_rejected_a2}")
        print(f"  Step B audio:     {audio_b}")
        print(f"  Step C sentences: {sent_c}")
        print(f"  Step D SAMER:     {samer_d}")
        print(f"  Step E enriched:  {enrich_e}")
        print(f"  Step F leeches:   {leech_f}")
        print(f"  Step G book ULK:  {book_ulk_g}")
        print(f"  Step G1b book reactivated: {book_reactivated}")
        print(f"  Step G2 ungated:  {ungated_g2}")
        print(f"  Step G3 diff fix: {diff_g3}")
        print(f"  Step H stories:   {stories_h}")
        print(f"  Step I podcasts:  {podcasts_i}")

        material_change_count = (
            retired_0
            + sent_a
            + corpus_touched_a2
            + audio_b
            + sent_c
            + enrich_e
            + leech_f
            + book_ulk_g
            + book_reactivated
            + ungated_g2
            + diff_g3
            + stories_h
            + podcasts_i
        )
        if not args.dry_run and material_change_count > 0:
            log_activity(
                db,
                event_type="material_updated",
                summary=(
                    f"Retired {retired_0}, generated {sent_a}+{sent_c} sentences, "
                    f"corpus {corpus_prepared_a2} prepared/"
                    f"{corpus_activated_a2} activated, {audio_b} audio, "
                    f"enriched {enrich_e}, reintro {leech_f} leeches, "
                    f"{book_ulk_g} book ULK, {book_reactivated} book reactivated, "
                    f"{ungated_g2} ungated, {diff_g3} diff fix, "
                    f"{stories_h} stories, {podcasts_i} podcasts in {elapsed:.0f}s"
                ),
                detail={
                    "step_0_retired": retired_0,
                    "step_a_sentences": sent_a,
                    "step_a2_corpus": corpus_a2.detail() if corpus_a2 else None,
                    "step_a2_prepared": corpus_prepared_a2,
                    "step_a2_activated": corpus_activated_a2,
                    "step_a2_retry": corpus_retry_a2,
                    "step_a2_rejected": corpus_rejected_a2,
                    "step_b_audio": audio_b,
                    "step_c_sentences": sent_c,
                    "step_d_samer": samer_d,
                    "step_e_enriched": enrich_e,
                    "step_f_leeches": leech_f,
                    "step_g_book_ulk": book_ulk_g,
                    "step_g1b_book_reactivated": book_reactivated,
                    "step_g2_ungated": ungated_g2,
                    "step_g3_diff_fix": diff_g3,
                    "step_h_stories": stories_h,
                    "step_i_podcasts": podcasts_i,
                    "elapsed_seconds": round(elapsed, 1),
                },
            )
        return 0
    finally:
        db.close()
        if lock_handle is not None:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)
            lock_handle.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
