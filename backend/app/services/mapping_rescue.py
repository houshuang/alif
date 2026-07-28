"""Lazy mapping rescue: rehabilitate stale-verified sentences for in-demand lemmas.

Called from ``warm_sentence_cache`` after the gap-detection phase and before LLM
generation. The reviewability gate (``has_current_mapping_verification``) treats
any sentence with ``mappings_verified_at`` older than the active verifier cutoff,
NULL, or equal to the 2000-01-01 corpus sentinel as untrustworthy and hides it
from review selection. That leaves a long tail of structurally fine sentences
stuck in purgatory.

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
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import Session, joinedload

from app.database import SessionLocal
from app.models import FrequencyCoreEntry, Lemma, Sentence, SentenceWord
from app.services.canonical_resolution import resolve_canonical_lemma_id
from app.services.sentence_eligibility import (
    CORPUS_BLOCKED_SENTINEL,
    CORPUS_QUALITY_REJECTED_SENTINEL,
    MAPPING_VERIFICATION_MIN_AT,
    has_no_completed_authentic_quality_failure,
    mapping_verification_retryable_before,
    not_has_unmapped_words,
)
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

# Conservative caps. The hook fires every time warm_sentence_cache runs, so the
# total per-warm-cache LLM budget for rescue should stay small.
MAX_RESCUE_LEMMAS_PER_RUN = 10
MAX_RESCUE_SENTENCES_PER_LEMMA = 5
TOTAL_RESCUE_SENTENCE_CAP = 30
RESCUE_BATCH_SIZE = 15  # sentences per batch_verify_sentences call

# Comprehensive corpus reverification: walk every active reviewable sentence,
# batch-verify against the current verifier and vocabulary, and hide unfixable
# rows by NULLing their bad positions. Designed to run in a single ~15-20
# minute pass (free CLI calls), then
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
    sentences_skipped_changed: int = 0
    targets_repaired: int = 0
    lemmas_now_covered: set[int] = field(default_factory=set)

    def to_dict(self) -> dict:
        out = self.__dict__.copy()
        out["lemmas_now_covered"] = sorted(self.lemmas_now_covered)
        return out


@dataclass(frozen=True)
class _VerificationSnapshot:
    sentence_id: int
    payload: dict
    verification_stamp: datetime | None
    state_signature: tuple


def _sentence_state_signature(
    sentence: Sentence,
    words: Iterable[SentenceWord] | None = None,
) -> tuple:
    """Version signature for read→LLM→write mapping maintenance."""
    word_rows = list(words if words is not None else sentence.words)
    return (
        sentence.arabic_text,
        sentence.english_translation,
        sentence.target_lemma_id,
        sentence.is_active,
        sentence.source,
        sentence.quality_reviewed_at,
        sentence.quality_natural,
        sentence.quality_translation_correct,
        tuple(sorted(
            (
                word.id,
                word.position,
                word.surface_form,
                word.lemma_id,
                bool(word.is_target_word),
            )
            for word in word_rows
        )),
    )


def _mapping_maintenance_candidate_clauses():
    """Active, structurally complete rows outside durable corpus dispositions."""
    return and_(
        Sentence.is_active == True,  # noqa: E712
        not_has_unmapped_words(),
        has_no_completed_authentic_quality_failure(),
        or_(
            Sentence.mappings_verified_at.is_(None),
            Sentence.mappings_verified_at.notin_((
                CORPUS_BLOCKED_SENTINEL,
                CORPUS_QUALITY_REJECTED_SENTINEL,
            )),
        ),
    )


def _acquire_snapshot_for_write(
    db: Session,
    snapshot: _VerificationSnapshot,
) -> tuple[Sentence, list[SentenceWord]] | None:
    """CAS the sentence version, lock its words, and recheck the snapshot."""
    stamp_clause = (
        Sentence.mappings_verified_at.is_(None)
        if snapshot.verification_stamp is None
        else Sentence.mappings_verified_at == snapshot.verification_stamp
    )
    matched = (
        db.query(Sentence)
        .filter(
            Sentence.id == snapshot.sentence_id,
            stamp_clause,
            _mapping_maintenance_candidate_clauses(),
        )
        .update(
            {Sentence.mappings_verified_at: snapshot.verification_stamp},
            synchronize_session=False,
        )
    )
    if matched != 1:
        return None

    db.expire_all()
    sentence = (
        db.query(Sentence)
        .filter(Sentence.id == snapshot.sentence_id)
        .with_for_update()
        .one_or_none()
    )
    if sentence is None:
        return None
    word_rows = (
        db.query(SentenceWord)
        .filter(SentenceWord.sentence_id == snapshot.sentence_id)
        .order_by(SentenceWord.id.asc())
        .with_for_update()
        .all()
    )
    if _sentence_state_signature(sentence, word_rows) != snapshot.state_signature:
        return None
    return sentence, word_rows


def _snapshot_still_matches(
    db: Session,
    snapshot: _VerificationSnapshot,
) -> bool:
    """Read-only precheck before proposal work that may commit independently."""
    stamp_clause = (
        Sentence.mappings_verified_at.is_(None)
        if snapshot.verification_stamp is None
        else Sentence.mappings_verified_at == snapshot.verification_stamp
    )
    sentence = (
        db.query(Sentence)
        .options(joinedload(Sentence.words))
        .filter(
            Sentence.id == snapshot.sentence_id,
            stamp_clause,
            _mapping_maintenance_candidate_clauses(),
        )
        .populate_existing()
        .one_or_none()
    )
    return (
        sentence is not None
        and _sentence_state_signature(sentence) == snapshot.state_signature
    )


def _stale_sentences_for_lemma(
    db: Session, lemma_id: int, cap: int
) -> list[Sentence]:
    """Active sentences containing this lemma whose verification is stale.

    Excludes sentences already covered by the current verification cohort —
    those are picked up by normal selection.
    """
    has_lemma = exists().where(
        SentenceWord.sentence_id == Sentence.id,
        SentenceWord.lemma_id == lemma_id,
    )
    return (
        db.query(Sentence)
        .filter(
            _mapping_maintenance_candidate_clauses(),
            mapping_verification_retryable_before(
                MAPPING_VERIFICATION_MIN_AT
            ),
            has_lemma,
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
    *,
    allow_create: bool = True,
) -> int | None:
    """Resolve or create a lemma for an LLM-proposed correction, gated by frequency.

    Three outcomes:

    1. FCE row found with ``lemma_id`` already set → reuse that existing lemma
       (a different lookup path missed it; the proposal is legitimate).
    2. FCE row found with ``lemma_id IS NULL`` → when ``allow_create`` is
       true, create the Lemma from the proposal, route it through
       ``run_quality_gates`` (enrichment + variant detection + gate stamp),
       and update the FCE row to point at it. Creation is deliberately
       disabled while sentence mutations are pending because the quality
       pipeline commits internally.
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

    if not allow_create:
        _log_proposal_suggestion(
            proposed_ar, proposed_gloss, proposed_pos,
            sentence_id, surface_form, fce_matched=True,
        )
        stats.proposals_logged_only += 1
        return None

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


def _fold_proposal_stats(target, source: RescueStats) -> None:
    """Fold proposal counters after the transaction that produced them commits."""
    target.proposals_reused_existing += source.proposals_reused_existing
    target.proposals_created_lemma += source.proposals_created_lemma
    target.proposals_logged_only += source.proposals_logged_only


def _prepare_unlinked_frequency_proposals(
    snapshots: Iterable[_VerificationSnapshot],
    issues_by_sentence_id: dict[int, list[dict]],
    stats: RescueStats,
) -> None:
    """Create FCE-backed missing lemmas before any sentence write transaction.

    ``run_quality_gates`` commits internally. Keeping this pre-pass in its own
    session means those commits can never publish a half-applied
    ``Sentence``/``SentenceWord`` correction. The later sentence transaction
    still rechecks the complete read snapshot with a CAS before using a newly
    created lemma.
    """
    snapshot_by_id = {snapshot.sentence_id: snapshot for snapshot in snapshots}
    db = SessionLocal()
    try:
        lemma_lookup = build_comprehensive_lemma_lookup(db)
        for sentence_id, issues in issues_by_sentence_id.items():
            snapshot = snapshot_by_id.get(sentence_id)
            if (
                snapshot is None
                or not issues
                or not _snapshot_still_matches(db, snapshot)
            ):
                continue
            word_by_pos = {
                mapping.position: mapping
                for mapping in snapshot.payload["mappings"]
            }
            for issue in issues:
                position = issue.get("position")
                word = word_by_pos.get(position)
                if word is None:
                    continue
                proposed_ar = str(issue.get("correct_lemma_ar", "") or "")
                proposed_gloss = str(issue.get("correct_gloss", "") or "")
                proposed_pos = str(issue.get("correct_pos", "") or "")
                if not proposed_ar:
                    continue

                from app.services.sentence_validator import correct_mapping

                if correct_mapping(
                    db,
                    proposed_ar,
                    proposed_gloss,
                    proposed_pos,
                    current_lemma_id=None,
                    lemma_lookup=lemma_lookup,
                ) is not None:
                    continue
                fce = _frequency_core_lookup(db, proposed_ar)
                if fce is None or fce.lemma_id is not None:
                    continue

                _try_frequency_gated_proposal(
                    db,
                    proposed_ar,
                    proposed_gloss,
                    proposed_pos,
                    sentence_id,
                    word.surface_form or "",
                    stats,
                    allow_create=True,
                )
                # The creation path may normalize the stored form and commits
                # internally; rebuild before evaluating a later proposal.
                lemma_lookup = build_comprehensive_lemma_lookup(db)
        db.commit()
    except Exception:
        logger.exception("mapping rescue proposal pre-pass failed")
        db.rollback()
    finally:
        db.close()


def _apply_with_proposal_fallback(
    db: Session,
    issues: list[dict],
    word_rows: list[SentenceWord],
    sentence_id: int,
    arabic_text: str,
    lemma_lookup,
    stats: "RescueStats",
    *,
    sentence: Sentence | None = None,
    allow_lemma_creation: bool = False,
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
       reverify: NULL the bad position and clear the stamp).

    Without this discrimination the reverify sweep had a 70% false-positive
    deactivation rate on conjugated verbs and inflected nouns — the verifier
    routinely flags those even when the prompt says not to.
    """
    from app.services.sentence_validator import correct_mapping

    before_lemma_ids = {word.id: word.lemma_id for word in word_rows}
    target_before = (
        resolve_canonical_lemma_id(db, sentence.target_lemma_id)
        if sentence is not None and sentence.target_lemma_id is not None
        else None
    )
    target_present_before = (
        target_before is not None
        and any(
            word.lemma_id is not None
            and resolve_canonical_lemma_id(db, word.lemma_id) == target_before
            for word in word_rows
        )
    )

    failed = apply_corrections(
        issues, word_rows, db, lemma_lookup=lemma_lookup,
        arabic_text=arabic_text,
    )

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
            allow_create=allow_lemma_creation,
        )
        if new_lid and new_lid != word.lemma_id:
            logger.info(
                "Rescue proposal pos %d '%s': #%s → #%d",
                pos, word.surface_form, word.lemma_id, new_lid,
            )
            word.lemma_id = new_lid
        else:
            still_failed.append(pos)

    if sentence is not None:
        changed_words = [
            word
            for word in word_rows
            if before_lemma_ids.get(word.id) != word.lemma_id
        ]
        target_after_present = (
            target_before is not None
            and any(
                word.lemma_id is not None
                and resolve_canonical_lemma_id(db, word.lemma_id)
                == target_before
                for word in word_rows
            )
        )
        if target_present_before and not target_after_present:
            changed_primary_words = [
                word
                for word in changed_words
                if before_lemma_ids.get(word.id) is not None
                and resolve_canonical_lemma_id(
                    db, before_lemma_ids[word.id]
                )
                == target_before
            ]
            repaired_targets = {
                resolve_canonical_lemma_id(db, word.lemma_id)
                for word in changed_primary_words
                if word.lemma_id is not None
            }
            if len(repaired_targets) == 1:
                repaired_target = repaired_targets.pop()
                if sentence.target_lemma_id != repaired_target:
                    sentence.target_lemma_id = repaired_target
                    stats.targets_repaired += 1
            else:
                # We cannot infer a unique replacement for the lost primary.
                # Fail those positions closed; callers either keep the stale
                # stamp or NULL them so the row is invisible.
                still_failed.extend(
                    [word.position for word in changed_primary_words]
                    or [word.position for word in changed_words]
                )
    return sorted(set(still_failed))


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
    sentences_flagged: int = 0
    sentences_skipped_changed: int = 0
    targets_repaired: int = 0

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

    The sweep hides sentences when even the frequency-gated proposal cannot
    repair the mapping. That's usually because the correct lemma simply isn't
    in the vocabulary yet — the sentence is structurally fine but references
    a word we haven't imported. A slower offline pass can decide case-by-case
    whether to add the lemma or retire the sentence permanently.
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
    """Walk the full active-reviewable corpus and fail unfixable rows closed.

    Designed to run as a one-shot maintenance pass when the user wants
    confidence that *every* sentence currently visible in sessions has been
    checked by the current verifier against the current vocabulary. Subsequent
    runs are idempotent: passing sentences get re-stamped (cheap), while bad
    positions are NULLed and hidden by the reviewability gate.

    Write-lock discipline (CLAUDE.md Rule #10): each batch reads its data and
    closes the DB before the LLM call, then reopens for a quick write.

    Args:
        batch_size: sentences per LLM call. Higher → fewer round trips but
            longer single-call latency and bigger prompt. 15 is a good
            balance and matches the existing rescue batch size.
        sentence_ids: optional restrict to specific IDs (for spot-check or
            resume-after-failure). Defaults to all reviewable sentences.
        dry_run: if True, run the verifier with no database, proposal,
            quality-pipeline, triage-log, or activity-log writes.
        progress_every: print a one-line progress update every N batches.
    """
    stats = ReverifyStats()
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    db = SessionLocal()
    try:
        if sentence_ids is None:
            ids = _all_active_reviewable_sentence_ids(db)
        else:
            requested_ids = sorted(set(sentence_ids))
            ids = [
                row[0]
                for row in db.query(Sentence.id)
                .filter(
                    Sentence.id.in_(requested_ids),
                    _mapping_maintenance_candidate_clauses(),
                )
                .order_by(Sentence.id.asc())
                .all()
            ]
    finally:
        db.close()

    if not ids:
        return stats

    total = len(ids)
    logger.info("reverify_all_active_sentences: %d sentences to check", total)
    batch_count = (total + batch_size - 1) // batch_size

    for batch_idx in range(batch_count):
        chunk_ids = ids[batch_idx * batch_size:(batch_idx + 1) * batch_size]

        # ── Read phase ───────────────────────────────────────────────────
        db = SessionLocal()
        snapshots: list[_VerificationSnapshot] = []
        try:
            sentences = (
                db.query(Sentence)
                .options(joinedload(Sentence.words))
                .filter(
                    Sentence.id.in_(chunk_ids),
                    _mapping_maintenance_candidate_clauses(),
                )
                .order_by(Sentence.id.asc())
                .all()
            )
            referenced_lemma_ids = {
                word.lemma_id
                for sentence in sentences
                for word in sentence.words
                if word.lemma_id
            }
            referenced_lemmas = (
                db.query(Lemma)
                .filter(Lemma.lemma_id.in_(list(referenced_lemma_ids)))
                .all()
            )
            lemma_map = {
                lemma.lemma_id: lemma for lemma in referenced_lemmas
            }

            for sentence in sentences:
                mappings = _to_token_mappings(sentence.words)
                if not mappings:
                    continue
                snapshots.append(
                    _VerificationSnapshot(
                        sentence_id=sentence.id,
                        payload={
                            "arabic": sentence.arabic_text,
                            "english": sentence.english_translation or "",
                            "mappings": mappings,
                            "has_ambiguous": False,
                        },
                        verification_stamp=sentence.mappings_verified_at,
                        state_signature=_sentence_state_signature(sentence),
                    )
                )
        except Exception:
            logger.exception("reverify: read phase failed for batch %d", batch_idx)
            continue
        finally:
            db.close()

        if not snapshots:
            continue

        # ── LLM verify (no DB session held) ──────────────────────────────
        inputs = [snapshot.payload for snapshot in snapshots]
        try:
            results = batch_verify_sentences(inputs, lemma_map)
        except Exception:
            logger.exception("reverify: batch_verify raised, batch %d", batch_idx)
            results = None
        if results is None or len(results) != len(snapshots):
            stats.llm_failures += len(snapshots)
            if (
                (batch_idx + 1) % progress_every == 0
                or batch_idx + 1 == batch_count
            ):
                logger.info(
                    "reverify: batch %d/%d — LLM failed, skipping %d sentences",
                    batch_idx + 1, batch_count, len(snapshots),
                )
            continue

        stats.batches_run += 1
        stats.sentences_attempted += len(snapshots)
        issues_by_sentence_id = {
            snapshot.sentence_id: list(result.get("issues") or [])
            for snapshot, result in zip(snapshots, results)
        }

        # A dry-run is strictly observational. In particular it must not call
        # proposal/logging helpers or the lemma quality pipeline, all of which
        # can write or commit.
        if dry_run:
            for issues in issues_by_sentence_id.values():
                if issues:
                    stats.sentences_flagged += 1
                else:
                    stats.sentences_passed += 1
            continue

        proposal_stats = RescueStats()
        _prepare_unlinked_frequency_proposals(
            snapshots, issues_by_sentence_id, proposal_stats
        )
        _fold_proposal_stats(stats, proposal_stats)

        # ── Write phase ──────────────────────────────────────────────────
        for snapshot in snapshots:
            issues = issues_by_sentence_id[snapshot.sentence_id]
            db = SessionLocal()
            sentence_stats = RescueStats()
            triage: tuple[list[int], list[TokenMapping]] | None = None
            try:
                acquired = _acquire_snapshot_for_write(db, snapshot)
                if acquired is None:
                    db.rollback()
                    stats.sentences_skipped_changed += 1
                    continue
                sentence, word_rows = acquired

                if not issues:
                    sentence.mappings_verified_at = datetime.now(timezone.utc)
                    db.commit()
                    stats.sentences_passed += 1
                    continue

                lemma_lookup = build_comprehensive_lemma_lookup(db)
                still_failed = _apply_with_proposal_fallback(
                    db,
                    issues,
                    word_rows,
                    snapshot.sentence_id,
                    sentence.arabic_text or "",
                    lemma_lookup,
                    sentence_stats,
                    sentence=sentence,
                    allow_lemma_creation=False,
                )
                if not still_failed:
                    sentence.mappings_verified_at = datetime.now(timezone.utc)
                    db.commit()
                    stats.sentences_corrected += 1
                    stats.targets_repaired += sentence_stats.targets_repaired
                    _fold_proposal_stats(stats, sentence_stats)
                    continue

                # Can't repair — NULL the offending positions and clear the
                # verification stamp. Both conditions fail the central
                # reviewability gate even if a reported position is missing.
                word_by_pos = {word.position: word for word in word_rows}
                nulled = 0
                for position in still_failed:
                    word = word_by_pos.get(position)
                    if word is not None and word.lemma_id is not None:
                        word.lemma_id = None
                        nulled += 1
                sentence.mappings_verified_at = None
                triage = (
                    still_failed,
                    list(snapshot.payload["mappings"]),
                )
                db.commit()
                stats.sentences_unfixable += 1
                stats.positions_nulled += nulled
                stats.targets_repaired += sentence_stats.targets_repaired
                _fold_proposal_stats(stats, sentence_stats)
            except Exception:
                logger.exception(
                    "reverify: write failed for sentence %d",
                    snapshot.sentence_id,
                )
                db.rollback()
            finally:
                db.close()

            if triage is not None:
                failed_positions, original_words = triage
                _log_reverify_triage(
                    snapshot.sentence_id,
                    snapshot.payload["arabic"] or "",
                    snapshot.payload["english"] or "",
                    failed_positions,
                    original_words,
                    issues,
                )

        if (
            (batch_idx + 1) % progress_every == 0
            or batch_idx + 1 == batch_count
        ):
            logger.info(
                "reverify: batch %d/%d — passed=%d corrected=%d "
                "unfixable=%d skipped_changed=%d (nulled %d positions)",
                batch_idx + 1,
                batch_count,
                stats.sentences_passed,
                stats.sentences_corrected,
                stats.sentences_unfixable,
                stats.sentences_skipped_changed,
                stats.positions_nulled,
            )

    return stats


def reverify_oldest_active_sentences(
    *,
    max_sentences: int = 30,
    min_age_days: int = 1,
) -> ReverifyStats:
    """Per-warm-cache pre-verification of the oldest-stamped active sentences.

    Picks up where the one-shot ``reverify_all_active_sentences`` sweep leaves
    off: each warm cache pass re-checks the ``max_sentences`` sentences whose
    ``mappings_verified_at`` is oldest (and at least ``min_age_days`` old, so
    we don't re-verify what we just stamped). At ~30 sentences/warm pass with
    typical usage, the full active corpus rolls over every ~12 days.

    Same calibrated logic as the full sweep: false-positive ``same_lemma``
    flags are dropped, real failures NULL the offending position(s) and log
    to ``mapping_reverify_failures_<date>.jsonl``.
    """
    stats = ReverifyStats()
    cutoff = datetime.now(timezone.utc) - timedelta(days=min_age_days)

    db = SessionLocal()
    try:
        from app.services.sentence_eligibility import reviewable_sentence_clauses
        ids = [
            r[0] for r in db.query(Sentence.id)
            .filter(
                reviewable_sentence_clauses(),
                Sentence.mappings_verified_at < cutoff.replace(tzinfo=None),
            )
            .order_by(Sentence.mappings_verified_at.asc())
            .limit(max_sentences)
            .all()
        ]
    finally:
        db.close()

    if not ids:
        return stats

    # Delegate to the existing sweep machinery with the explicit ID list.
    return reverify_all_active_sentences(
        batch_size=REVERIFY_BATCH_SIZE,
        sentence_ids=ids,
    )


def reverify_sentences_before(
    sentence_ids: list[int],
    *,
    cutoff: datetime,
    batch_size: int = REVERIFY_BATCH_SIZE,
) -> ReverifyStats:
    """Reverify explicit reviewable sentence IDs with stamps older than cutoff.

    This is the just-in-time counterpart to the global sweeps: callers can pass
    the concrete sentences about to be shown, and only rows whose
    ``mappings_verified_at`` predates a hardening timestamp pay the LLM cost.
    If verification cannot stamp a row fresh, the caller should treat it as
    unsafe for the current response.
    """
    stats = ReverifyStats()
    if not sentence_ids:
        return stats

    cutoff_naive = (
        cutoff.astimezone(timezone.utc).replace(tzinfo=None)
        if cutoff.tzinfo is not None
        else cutoff
    )

    db = SessionLocal()
    try:
        ids = [
            r[0] for r in db.query(Sentence.id)
            .filter(
                Sentence.id.in_(list(set(sentence_ids))),
                _mapping_maintenance_candidate_clauses(),
                mapping_verification_retryable_before(cutoff_naive),
            )
            .order_by(Sentence.mappings_verified_at.asc())
            .all()
        ]
    finally:
        db.close()

    if not ids:
        return stats

    return reverify_all_active_sentences(
        batch_size=batch_size,
        sentence_ids=ids,
    )


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
        snapshots: list[_VerificationSnapshot] = []
        for sentence in all_sentences:
            mappings = _to_token_mappings(sentence.words)
            if not mappings:
                continue
            snapshots.append(
                _VerificationSnapshot(
                    sentence_id=sentence.id,
                    payload={
                        "arabic": sentence.arabic_text,
                        "english": sentence.english_translation or "",
                        "mappings": mappings,
                        "has_ambiguous": False,
                    },
                    verification_stamp=sentence.mappings_verified_at,
                    state_signature=_sentence_state_signature(sentence),
                )
            )
        stats.sentences_attempted = len(snapshots)
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
        inputs = [snapshot.payload for snapshot in chunk]
        try:
            results = batch_verify_sentences(inputs, lemma_map)
        except Exception:
            logger.exception("mapping_rescue: batch_verify_sentences raised")
            results = None
        if results is None or len(results) != len(chunk):
            # LLM failure for this chunk — skip rescuing these sentences this run
            continue
        for snapshot, result in zip(chunk, results):
            issues_by_sentence_id[snapshot.sentence_id] = list(
                result.get("issues") or []
            )

    if not issues_by_sentence_id:
        return stats

    # Create any approved missing FCE lemmas in a transaction that cannot
    # contain pending sentence mutations. The sentence CAS below still decides
    # whether the proposal may be applied.
    _prepare_unlinked_frequency_proposals(
        snapshots, issues_by_sentence_id, stats
    )

    # ── Phase 3: write — apply corrections, stamp survivors ───────────────
    snapshot_by_id = {
        snapshot.sentence_id: snapshot for snapshot in snapshots
    }
    for sentence_id, issues in issues_by_sentence_id.items():
        snapshot = snapshot_by_id[sentence_id]
        db = SessionLocal()
        sentence_stats = RescueStats()
        try:
            acquired = _acquire_snapshot_for_write(db, snapshot)
            if acquired is None:
                db.rollback()
                stats.sentences_skipped_changed += 1
                continue
            sentence, word_rows = acquired

            if not issues:
                sentence.mappings_verified_at = datetime.now(timezone.utc)
                db.commit()
                stats.sentences_rescued += 1
                continue

            lemma_lookup = build_comprehensive_lemma_lookup(db)
            still_failed = _apply_with_proposal_fallback(
                db,
                issues,
                word_rows,
                sentence_id,
                sentence.arabic_text or "",
                lemma_lookup,
                sentence_stats,
                sentence=sentence,
                allow_lemma_creation=False,
            )
            if still_failed:
                # Persist any independent valid corrections but leave the
                # verification stamp stale so the row remains unreviewable.
                db.commit()
                stats.sentences_unfixable += 1
                stats.targets_repaired += sentence_stats.targets_repaired
                _fold_proposal_stats(stats, sentence_stats)
                continue

            sentence.mappings_verified_at = datetime.now(timezone.utc)
            db.commit()
            stats.sentences_rescued += 1
            stats.sentences_corrected += 1
            stats.targets_repaired += sentence_stats.targets_repaired
            _fold_proposal_stats(stats, sentence_stats)
        except Exception:
            logger.exception(
                "mapping_rescue: write failed for sentence %d", sentence_id
            )
            db.rollback()
        finally:
            db.close()

    # Recompute coverage to tell the caller which lemmas no longer need fresh
    # generation.
    db = SessionLocal()
    try:
        for canonical_id in per_lemma:
            if _coverage_after_rescue(db, canonical_id) >= coverage_target:
                stats.lemmas_now_covered.add(canonical_id)
    finally:
        db.close()

    return stats
