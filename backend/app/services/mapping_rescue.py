"""Lazy mapping rescue: rehabilitate stale-verified sentences for in-demand lemmas.

Called from ``warm_sentence_cache`` after the gap-detection phase and before LLM
generation. The reviewability gate (``has_current_mapping_verification``) treats
any sentence with ``mappings_verified_at`` < 2026-04-16, NULL, or equal to the
2000-01-01 corpus sentinel as untrustworthy and hides it from review selection.
That leaves a long tail of structurally fine sentences stuck in purgatory.

Rather than draining the backlog globally on a schedule (expensive, blind),
this module rescues *only* the stale sentences attached to lemmas the warm
cache has just identified as gap candidates. The verification work happens on
exactly the cohort with active demand and stops the moment the gap is closed.

The frequency-core gate
-----------------------
When the verifier flags a position and proposes a correct lemma that doesn't
exist in the vocabulary yet, this module looks the proposal up in
``frequency_core_entries`` (by bare form). If the proposal matches an entry
that already points at a lemma, we reuse that lemma. If it matches an entry
with ``lemma_id IS NULL`` (a known-frequency lemma we just haven't imported
yet), we create the lemma from the LLM proposal, route it through
``run_quality_gates``, and re-link the FCE row. Proposals with no FCE match
are logged as import suggestions and the sentence stays stale.

Write-lock discipline
---------------------
All LLM work happens outside any DB session. The flow is read → close → LLM →
reopen → write, in line with CLAUDE.md Rule #10.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import exists, or_
from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal
from app.models import FrequencyCoreEntry, Lemma, Sentence, SentenceWord
from app.services.canonical_resolution import resolve_canonical_lemma_id
from app.services.sentence_eligibility import MAPPING_VERIFICATION_MIN_AT
from app.services.sentence_validator import (
    TokenMapping,
    apply_corrections,
    batch_verify_sentences,
    build_comprehensive_lemma_lookup,
    normalize_arabic,
    strip_diacritics,
    strip_tanwin_alif,
)

logger = logging.getLogger(__name__)

CORPUS_SENTINEL = datetime(2000, 1, 1)

# Conservative caps. The hook fires every time warm_sentence_cache runs, so the
# total per-warm-cache LLM budget for rescue should stay small.
MAX_RESCUE_LEMMAS_PER_RUN = 10
MAX_RESCUE_SENTENCES_PER_LEMMA = 5
TOTAL_RESCUE_SENTENCE_CAP = 30
RESCUE_BATCH_SIZE = 15  # sentences per batch_verify_sentences call

# Comprehensive corpus reverification: walk every active reviewable sentence,
# batch-verify against the current verifier and vocabulary, deactivate unfixable
# ones. Designed to run in a single ~15-20 minute pass (free CLI calls), then
# leave the corpus in a "every active sentence is currently verified" state.
REVERIFY_BATCH_SIZE = 15


@dataclass
class RescueStats:
    lemmas_attempted: int = 0
    sentences_attempted: int = 0
    sentences_rescued: int = 0
    sentences_corrected: int = 0
    sentences_unfixable: int = 0
    proposals_reused_existing: int = 0
    proposals_created_lemma: int = 0
    proposals_logged_only: int = 0
    lemmas_now_covered: set[int] = field(default_factory=set)

    def to_dict(self) -> dict:
        out = self.__dict__.copy()
        out["lemmas_now_covered"] = sorted(self.lemmas_now_covered)
        return out


def _stale_sentences_for_lemma(
    db: Session, lemma_id: int, cap: int
) -> list[Sentence]:
    """Active sentences containing this lemma whose verification is stale.

    Excludes sentences already covered by the current verification cohort —
    those are picked up by normal selection.
    """
    is_stale = or_(
        Sentence.mappings_verified_at.is_(None),
        Sentence.mappings_verified_at < MAPPING_VERIFICATION_MIN_AT,
        Sentence.mappings_verified_at == CORPUS_SENTINEL,
    )
    return (
        db.query(Sentence)
        .join(SentenceWord, SentenceWord.sentence_id == Sentence.id)
        .filter(
            Sentence.is_active == True,  # noqa: E712
            is_stale,
            SentenceWord.lemma_id == lemma_id,
        )
        .options(joinedload(Sentence.words))
        .order_by(Sentence.id.asc())
        .limit(cap)
        .all()
    )


def _to_token_mappings(words: Iterable[SentenceWord]) -> list[TokenMapping]:
    """Adapt persisted SentenceWord rows into TokenMapping shape for the verifier.

    TokenMapping carries transient generation-time fields (``via_clitic``,
    ``alternative_lemma_ids``) that don't exist on the persisted row, so we
    synthesize neutral values. For stale-verified sentences we have already
    resolved every position, so no ambiguity is signalled to the verifier.
    """
    out: list[TokenMapping] = []
    for w in words:
        if w.lemma_id is None:
            # Stale-verified sentences may still have NULL lemma_id positions
            # for surface forms whose lemma hasn't been imported yet. Skip them
            # in the verifier prompt; the storage→reviewability healing path
            # elsewhere handles NULL → lemma_id transitions when new lemmas land.
            continue
        out.append(
            TokenMapping(
                position=w.position,
                surface_form=w.surface_form,
                lemma_id=w.lemma_id,
                is_target=bool(w.is_target_word),
                is_function_word=False,
                alternative_lemma_ids=[],
                via_clitic=False,
            )
        )
    return out


def _frequency_core_lookup(
    db: Session, proposed_ar: str
) -> FrequencyCoreEntry | None:
    """Find a FrequencyCoreEntry whose lemma_key matches the proposed bare form.

    Tries the verbatim normalized form, then the tanwin-stripped form, then
    strips a leading ``ال`` if present. This mirrors ``correct_mapping``'s
    fallback strategy so the gate aligns with what apply_corrections already
    accepts.
    """
    if not proposed_ar:
        return None
    bare = normalize_arabic(proposed_ar)
    if not bare:
        return None

    keys = {bare}
    stripped = strip_tanwin_alif(bare)
    if stripped:
        keys.add(stripped)
    if bare.startswith("ال"):
        keys.add(bare[2:])

    return (
        db.query(FrequencyCoreEntry)
        .filter(FrequencyCoreEntry.lemma_key.in_(list(keys)))
        .order_by(FrequencyCoreEntry.core_rank.asc())
        .first()
    )


def _log_proposal_suggestion(
    proposed_ar: str,
    proposed_gloss: str,
    proposed_pos: str,
    sentence_id: int,
    surface_form: str,
    fce_matched: bool,
) -> None:
    """Append a structured proposal log for downstream import scripts.

    Even when we don't auto-create (no FCE match), we keep the proposal so
    ``scripts/missing_lemma_candidates.py`` can rank surface forms that keep
    being flagged across multiple sentences.
    """
    from app.config import settings
    import json
    from datetime import datetime as _dt

    log_dir = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"rescue_proposals_{_dt.now():%Y-%m-%d}.jsonl"
    entry = {
        "ts": _dt.now().isoformat(),
        "event": "rescue_lemma_proposal",
        "proposed_ar": proposed_ar,
        "proposed_gloss": proposed_gloss,
        "proposed_pos": proposed_pos,
        "sentence_id": sentence_id,
        "surface_form": surface_form,
        "frequency_core_match": fce_matched,
    }
    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("Failed to write rescue proposal log", exc_info=True)


def _try_frequency_gated_proposal(
    db: Session,
    proposed_ar: str,
    proposed_gloss: str,
    proposed_pos: str,
    sentence_id: int,
    surface_form: str,
    stats: "RescueStats",
) -> int | None:
    """Resolve or create a lemma for an LLM-proposed correction, gated by frequency.

    Three outcomes:

    1. FCE row found with ``lemma_id`` already set → reuse that existing lemma
       (a different lookup path missed it; the proposal is legitimate).
    2. FCE row found with ``lemma_id IS NULL`` → create the Lemma from the
       proposal, route through ``run_quality_gates`` (enrichment + variant
       detection + gate stamp), and update the FCE row to point at it.
    3. No FCE match → log the proposal for offline review, return None.
    """
    fce = _frequency_core_lookup(db, proposed_ar)
    if fce is None:
        _log_proposal_suggestion(
            proposed_ar, proposed_gloss, proposed_pos,
            sentence_id, surface_form, fce_matched=False,
        )
        stats.proposals_logged_only += 1
        return None

    if fce.lemma_id is not None:
        _log_proposal_suggestion(
            proposed_ar, proposed_gloss, proposed_pos,
            sentence_id, surface_form, fce_matched=True,
        )
        stats.proposals_reused_existing += 1
        return fce.lemma_id

    bare = normalize_arabic(proposed_ar)
    new_lemma = Lemma(
        lemma_ar=proposed_ar,
        lemma_ar_bare=bare,
        pos=(proposed_pos or fce.pos or None) or None,
        gloss_en=proposed_gloss or fce.gloss_en or None,
        source="rescue_proposal",
        frequency_rank=fce.core_rank,
    )
    db.add(new_lemma)
    db.flush()
    new_id = new_lemma.lemma_id
    fce.lemma_id = new_id

    from app.services.lemma_quality import run_quality_gates
    try:
        run_quality_gates(db, [new_id], background_enrich=True)
    except Exception:
        logger.exception(
            "run_quality_gates failed for rescue-created lemma %d", new_id
        )

    _log_proposal_suggestion(
        proposed_ar, proposed_gloss, proposed_pos,
        sentence_id, surface_form, fce_matched=True,
    )
    stats.proposals_created_lemma += 1
    return new_id


def _apply_with_proposal_fallback(
    db: Session,
    issues: list[dict],
    word_rows: list[SentenceWord],
    sentence_id: int,
    arabic_text: str,
    lemma_lookup,
    stats: "RescueStats",
) -> list[int]:
    """apply_corrections + frequency-gated proposal fallback for remaining failures.

    Mutates ``word_rows`` in place when a fix is found. Returns the list of
    positions that still don't have a valid lemma after both passes — caller
    decides whether to stamp the sentence as verified or leave it stale.

    Discriminates between two kinds of `apply_corrections` failures:

    1. **`same_lemma`** — the verifier proposed a lemma that resolves to the
       *current* mapping (e.g. flagged `أُعَلِّمُ → عَلَّمَ` when `#722` is
       already `عَلَّمَ`). This is a pedantic verifier overcalling a
       conjugation/inflection that's already correctly mapped to the base
       lemma. Treat as not-a-failure: the mapping stands.
    2. **`not_found`** — the verifier proposed a lemma not in the DB at all.
       This is the legitimate vocab-gap case; try the frequency-gated
       proposal path. If even that can't create a lemma, return the position
       as a real failure so the caller can decide (rescue: leave stale;
       reverify: deactivate).

    Without this discrimination the reverify sweep had a 70% false-positive
    deactivation rate on conjugated verbs and inflected nouns — the verifier
    routinely flags those even when the prompt says not to.
    """
    from app.services.sentence_validator import correct_mapping

    failed = apply_corrections(
        issues, word_rows, db, lemma_lookup=lemma_lookup,
        arabic_text=arabic_text,
    )
    if not failed:
        return []

    issue_by_pos = {i["position"]: i for i in issues if "position" in i}
    word_by_pos = {w.position: w for w in word_rows}

    still_failed: list[int] = []
    for pos in failed:
        issue = issue_by_pos.get(pos)
        word = word_by_pos.get(pos)
        if not issue or not word:
            still_failed.append(pos)
            continue

        proposed_ar = str(issue.get("correct_lemma_ar", "") or "")
        proposed_gloss = str(issue.get("correct_gloss", "") or "")
        proposed_pos = str(issue.get("correct_pos", "") or "")

        # Does the verifier's proposal resolve to any lemma in the DB?
        # `current_lemma_id=None` so we don't prefer a different lemma — we
        # just want to know whether the proposed lemma exists at all.
        resolved = correct_mapping(
            db, proposed_ar, proposed_gloss, proposed_pos,
            current_lemma_id=None, lemma_lookup=lemma_lookup,
        )
        if resolved is not None:
            # Proposed lemma exists in DB. `apply_corrections` already would
            # have remapped to it if it were a different lemma; the failure
            # here must be the same_lemma path — verifier flagged a correct
            # conjugation/inflection mapping. Mapping stands, no fix needed.
            logger.debug(
                "Reverify pos %d '%s': verifier overcalled (proposed %r "
                "resolves to existing lemma); keeping current mapping",
                pos, word.surface_form, proposed_ar,
            )
            continue

        # Proposed lemma not in DB at all — legitimate gap. Try FCE proposal.
        new_lid = _try_frequency_gated_proposal(
            db, proposed_ar, proposed_gloss, proposed_pos,
            sentence_id, word.surface_form or "", stats,
        )
        if new_lid and new_lid != word.lemma_id:
            logger.info(
                "Rescue proposal pos %d '%s': #%s → #%d",
                pos, word.surface_form, word.lemma_id, new_lid,
            )
            word.lemma_id = new_lid
        else:
            still_failed.append(pos)
    return still_failed


def _coverage_after_rescue(db: Session, lemma_id: int) -> int:
    """Count of currently-reviewable sentences for a lemma (post-stamp).

    Uses ``exists`` for the lemma filter instead of a JOIN so it doesn't
    collide with the ``exists`` subquery inside ``not_has_unmapped_words``
    (both would correlate the same SentenceWord alias and yield a SELECT with
    no FROM).
    """
    from app.services.sentence_eligibility import reviewable_sentence_clauses

    has_lemma = exists().where(
        SentenceWord.sentence_id == Sentence.id,
        SentenceWord.lemma_id == lemma_id,
    )
    return (
        db.query(Sentence)
        .filter(has_lemma, reviewable_sentence_clauses())
        .count()
    )


@dataclass
class ReverifyStats:
    """Stats for the full-corpus reverification sweep."""
    batches_run: int = 0
    sentences_attempted: int = 0
    sentences_passed: int = 0
    sentences_corrected: int = 0
    # Sentences with at least one position the verifier flagged AND we couldn't
    # repair. We NULL the offending position(s) rather than deactivating the
    # whole sentence — the reviewability gate hides anything with a NULL
    # lemma_id, and the cron's step 0b healer can auto-create proper-name
    # lemmas on the next pass, restoring the sentence naturally. The triage
    # log captures the original lemma_id + verifier proposal for offline
    # investigation.
    sentences_unfixable: int = 0
    positions_nulled: int = 0
    proposals_reused_existing: int = 0
    proposals_created_lemma: int = 0
    proposals_logged_only: int = 0
    llm_failures: int = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _log_reverify_triage(
    sentence_id: int,
    arabic_text: str,
    english_text: str,
    failed_positions: list[int],
    word_rows: list[SentenceWord],
    issues: list[dict],
) -> None:
    """Append to a triage log for deeper later investigation.

    The sweep deactivates sentences when even the frequency-gated proposal
    can't repair the mapping. That's usually because the correct lemma simply
    isn't in the vocabulary yet — the sentence is structurally fine but
    references a word we haven't imported. A slower offline pass can decide
    case-by-case whether to add the lemma or retire the sentence permanently.
    """
    from app.config import settings
    import json
    from datetime import datetime as _dt

    log_dir = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"mapping_reverify_failures_{_dt.now():%Y-%m-%d}.jsonl"

    failure_detail = []
    word_by_pos = {w.position: w for w in word_rows}
    issue_by_pos = {i.get("position"): i for i in issues if "position" in i}
    for pos in failed_positions:
        w = word_by_pos.get(pos)
        i = issue_by_pos.get(pos, {})
        failure_detail.append({
            "position": pos,
            "surface_form": w.surface_form if w else None,
            "current_lemma_id": w.lemma_id if w else None,
            "proposed_lemma_ar": i.get("correct_lemma_ar"),
            "proposed_gloss": i.get("correct_gloss"),
            "explanation": i.get("explanation"),
        })

    entry = {
        "ts": _dt.now().isoformat(),
        "event": "reverify_deactivated",
        "sentence_id": sentence_id,
        "arabic": arabic_text,
        "english": english_text,
        "failed_positions": failure_detail,
    }
    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("Failed to write reverify triage log", exc_info=True)


def _all_active_reviewable_sentence_ids(db: Session) -> list[int]:
    """All sentence IDs the user could currently see — the population to sweep.

    Uses the reviewability gate (active + no NULL lemma + fresh verification
    cohort). Sentences blocked by the gate are already invisible; the existing
    `rescue_sentences_for_lemmas` covers those lazily.
    """
    from app.services.sentence_eligibility import reviewable_sentence_clauses

    return [
        r[0] for r in db.query(Sentence.id)
        .filter(reviewable_sentence_clauses())
        .order_by(Sentence.id.asc())
        .all()
    ]


def reverify_all_active_sentences(
    *,
    batch_size: int = REVERIFY_BATCH_SIZE,
    sentence_ids: list[int] | None = None,
    dry_run: bool = False,
    progress_every: int = 10,
) -> ReverifyStats:
    """Walk the full active-reviewable corpus, re-verify, deactivate unfixable.

    Designed to run as a one-shot maintenance pass when the user wants
    confidence that *every* sentence currently visible in sessions has been
    checked by the current verifier against the current vocabulary. Subsequent
    runs are idempotent: passing sentences get re-stamped (cheap), already-bad
    sentences would've been deactivated on a previous pass.

    Write-lock discipline (CLAUDE.md Rule #10): each batch reads its data and
    closes the DB before the LLM call, then reopens for a quick write.

    Args:
        batch_size: sentences per LLM call. Higher → fewer round trips but
            longer single-call latency and bigger prompt. 15 is a good
            balance and matches the existing rescue batch size.
        sentence_ids: optional restrict to specific IDs (for spot-check or
            resume-after-failure). Defaults to all reviewable sentences.
        dry_run: if True, run the verifier but don't deactivate or stamp.
        progress_every: print a one-line progress update every N batches.
    """
    stats = ReverifyStats()

    db = SessionLocal()
    try:
        if sentence_ids is None:
            ids = _all_active_reviewable_sentence_ids(db)
        else:
            ids = list(sentence_ids)
    finally:
        db.close()

    if not ids:
        return stats

    total = len(ids)
    logger.info("reverify_all_active_sentences: %d sentences to check", total)

    now = datetime.now(timezone.utc)
    batch_count = (total + batch_size - 1) // batch_size

    for batch_idx in range(batch_count):
        chunk_ids = ids[batch_idx * batch_size:(batch_idx + 1) * batch_size]

        # ── Read phase ───────────────────────────────────────────────────
        db = SessionLocal()
        snapshots: list[tuple[int, dict]] = []
        try:
            sentences = (
                db.query(Sentence)
                .options(joinedload(Sentence.words))
                .filter(Sentence.id.in_(chunk_ids))
                .all()
            )
            referenced_lemma_ids = {
                w.lemma_id for s in sentences for w in s.words if w.lemma_id
            }
            referenced_lemmas = (
                db.query(Lemma)
                .filter(Lemma.lemma_id.in_(list(referenced_lemma_ids)))
                .all()
            )
            lemma_map = {l.lemma_id: l for l in referenced_lemmas}

            for s in sentences:
                mappings = _to_token_mappings(s.words)
                if not mappings:
                    continue
                snapshots.append((
                    s.id,
                    {
                        "arabic": s.arabic_text,
                        "english": s.english_translation or "",
                        "mappings": mappings,
                        "has_ambiguous": False,
                    },
                ))
        except Exception:
            logger.exception("reverify: read phase failed for batch %d", batch_idx)
            db.close()
            continue
        finally:
            db.close()

        if not snapshots:
            continue

        # ── LLM verify (no DB session held) ──────────────────────────────
        inputs = [snap[1] for snap in snapshots]
        try:
            results = batch_verify_sentences(inputs, lemma_map)
        except Exception:
            logger.exception("reverify: batch_verify raised, batch %d", batch_idx)
            results = None
        if results is None:
            stats.llm_failures += len(snapshots)
            if (batch_idx + 1) % progress_every == 0 or batch_idx + 1 == batch_count:
                logger.info(
                    "reverify: batch %d/%d — LLM failed, skipping %d sentences",
                    batch_idx + 1, batch_count, len(snapshots),
                )
            continue

        stats.batches_run += 1
        stats.sentences_attempted += len(snapshots)

        # ── Write phase ──────────────────────────────────────────────────
        db = SessionLocal()
        try:
            lemma_lookup = build_comprehensive_lemma_lookup(db)
            rescue_stats_shim = RescueStats()  # tracks proposal sub-counters
            for (sid, _), res in zip(snapshots, results):
                issues = list(res.get("issues") or [])
                sentence = (
                    db.query(Sentence)
                    .options(joinedload(Sentence.words))
                    .filter(Sentence.id == sid)
                    .one_or_none()
                )
                if sentence is None:
                    continue
                word_rows = list(sentence.words)

                if not issues:
                    if not dry_run:
                        sentence.mappings_verified_at = now
                    stats.sentences_passed += 1
                    continue

                still_failed = _apply_with_proposal_fallback(
                    db, issues, word_rows, sid,
                    sentence.arabic_text or "", lemma_lookup, rescue_stats_shim,
                )
                if not still_failed:
                    if not dry_run:
                        sentence.mappings_verified_at = now
                    stats.sentences_corrected += 1
                    continue

                # Can't repair — NULL the offending positions and log for triage.
                # The reviewability gate (`not_has_unmapped_words`) will hide
                # the sentence from selection. update_material.py step 0b
                # (`fix_null_lemma_ids.remap_unmapped_sentence_words`) will
                # retry these on each cron pass and auto-create proper-name
                # lemmas when applicable.
                _log_reverify_triage(
                    sid,
                    sentence.arabic_text or "",
                    sentence.english_translation or "",
                    still_failed,
                    word_rows,
                    issues,
                )
                if not dry_run:
                    word_by_pos = {w.position: w for w in word_rows}
                    for pos in still_failed:
                        w = word_by_pos.get(pos)
                        if w is not None:
                            w.lemma_id = None
                            stats.positions_nulled += 1
                stats.sentences_unfixable += 1

            if not dry_run:
                db.commit()
            # Fold proposal stats from the shim into our top-level stats.
            stats.proposals_reused_existing += rescue_stats_shim.proposals_reused_existing
            stats.proposals_created_lemma += rescue_stats_shim.proposals_created_lemma
            stats.proposals_logged_only += rescue_stats_shim.proposals_logged_only
        except Exception:
            logger.exception("reverify: write phase failed for batch %d", batch_idx)
            db.rollback()
        finally:
            db.close()

        if (batch_idx + 1) % progress_every == 0 or batch_idx + 1 == batch_count:
            logger.info(
                "reverify: batch %d/%d — passed=%d corrected=%d unfixable=%d (nulled %d positions)",
                batch_idx + 1, batch_count,
                stats.sentences_passed,
                stats.sentences_corrected,
                stats.sentences_unfixable,
                stats.positions_nulled,
            )

    return stats


def rescue_sentences_for_lemmas(
    gap_lemma_ids: list[int],
    *,
    coverage_target: int = 3,
) -> RescueStats:
    """Lazy rescue pass for the warm-cache gap list.

    For each gap lemma (capped), pull its stale-verified sentences, batch-verify
    them, apply confident corrections, then either stamp them as fresh-verified
    (if all positions are valid) or leave them alone.

    Returns ``RescueStats`` for the caller to fold into its own stats dict.
    """
    stats = RescueStats()
    if not gap_lemma_ids:
        return stats

    cohort = gap_lemma_ids[:MAX_RESCUE_LEMMAS_PER_RUN]

    # ── Phase 1: read ─────────────────────────────────────────────────────
    # Pull stale sentences + their words + relevant lemmas, then close DB.
    db = SessionLocal()
    try:
        # Resolve canonicals so a stale row attached to a variant still attaches
        # to its canonical lemma's gap.
        canonical_targets: dict[int, int] = {}
        for lid in cohort:
            try:
                canonical_targets[lid] = resolve_canonical_lemma_id(db, lid)
            except Exception:
                canonical_targets[lid] = lid

        per_lemma: dict[int, list[Sentence]] = {}
        seen_sentence_ids: set[int] = set()
        total_pulled = 0
        for canonical_id in canonical_targets.values():
            if total_pulled >= TOTAL_RESCUE_SENTENCE_CAP:
                break
            remaining = TOTAL_RESCUE_SENTENCE_CAP - total_pulled
            cap = min(MAX_RESCUE_SENTENCES_PER_LEMMA, remaining)
            candidates = _stale_sentences_for_lemma(db, canonical_id, cap)
            fresh = [s for s in candidates if s.id not in seen_sentence_ids]
            for s in fresh:
                seen_sentence_ids.add(s.id)
            if fresh:
                per_lemma[canonical_id] = fresh
                total_pulled += len(fresh)

        if not per_lemma:
            return stats

        all_sentences = [s for ss in per_lemma.values() for s in ss]
        stats.lemmas_attempted = len(per_lemma)
        stats.sentences_attempted = len(all_sentences)

        # Build lemma_map for the verifier (all lemma_ids referenced anywhere)
        referenced_lemma_ids = {
            w.lemma_id for s in all_sentences for w in s.words if w.lemma_id
        }
        referenced_lemmas = (
            db.query(Lemma)
            .filter(Lemma.lemma_id.in_(list(referenced_lemma_ids)))
            .all()
        )
        lemma_map: dict[int, Lemma] = {l.lemma_id: l for l in referenced_lemmas}

        # Snapshot the data we need outside the session
        snapshots: list[tuple[int, dict, list[TokenMapping]]] = []
        for s in all_sentences:
            mappings = _to_token_mappings(s.words)
            if not mappings:
                continue
            snapshots.append(
                (
                    s.id,
                    {
                        "arabic": s.arabic_text,
                        "english": s.english_translation or "",
                        "mappings": mappings,
                        "has_ambiguous": False,
                    },
                    mappings,
                )
            )
    except Exception:
        logger.exception("mapping_rescue: read phase failed")
        return stats
    finally:
        db.close()

    if not snapshots:
        return stats

    # ── Phase 2: LLM verify (no DB session held) ──────────────────────────
    issues_by_sentence_id: dict[int, list[dict]] = {}
    for chunk_start in range(0, len(snapshots), RESCUE_BATCH_SIZE):
        chunk = snapshots[chunk_start:chunk_start + RESCUE_BATCH_SIZE]
        inputs = [snap[1] for snap in chunk]
        try:
            results = batch_verify_sentences(inputs, lemma_map)
        except Exception:
            logger.exception("mapping_rescue: batch_verify_sentences raised")
            results = None
        if results is None:
            # LLM failure for this chunk — skip rescuing these sentences this run
            continue
        for (sid, _, _), res in zip(chunk, results):
            issues_by_sentence_id[sid] = list(res.get("issues") or [])

    if not issues_by_sentence_id:
        return stats

    # ── Phase 3: write — apply corrections, stamp survivors ───────────────
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        lemma_lookup = build_comprehensive_lemma_lookup(db)
        for sentence_id, issues in issues_by_sentence_id.items():
            sentence = (
                db.query(Sentence)
                .options(joinedload(Sentence.words))
                .filter(Sentence.id == sentence_id)
                .one_or_none()
            )
            if sentence is None:
                continue
            word_rows = list(sentence.words)

            if not issues:
                # Clean. Stamp as freshly verified.
                sentence.mappings_verified_at = now
                stats.sentences_rescued += 1
                continue

            still_failed = _apply_with_proposal_fallback(
                db, issues, word_rows, sentence_id,
                sentence.arabic_text or "", lemma_lookup, stats,
            )
            if still_failed:
                stats.sentences_unfixable += 1
                # Leave the sentence stale-verified — it stays in purgatory.
                continue
            sentence.mappings_verified_at = now
            stats.sentences_rescued += 1
            stats.sentences_corrected += 1
        db.commit()

        # Recompute coverage to tell the caller which lemmas no longer need
        # fresh generation.
        for canonical_id in per_lemma:
            if _coverage_after_rescue(db, canonical_id) >= coverage_target:
                stats.lemmas_now_covered.add(canonical_id)
    except Exception:
        logger.exception("mapping_rescue: write phase failed")
        db.rollback()
    finally:
        db.close()

    return stats
