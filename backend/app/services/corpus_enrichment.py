"""Scoped enrichment and activation for authentic corpus sentences.

Imported corpus rows are deliberately invisible until they have passed three
separate gates:

1. diacritization + English translation,
2. contextual word-to-lemma mapping verification, and
3. Arabic-naturalness + translation-correctness review.

Enrichment and activation are intentionally separate.  A successful enrichment
repairs target bookkeeping and leaves the row inactive.  Activation is an
explicit, bounded, demand-aware pass over already prepared rows.

The caller must hold the shared material-update flock.  Rows are also claimed
with the historical ``2000-01-01`` sentinel because a few manual sentence
mutators do not take that advisory lock.  Claims are always released in a
``finally``-equivalent failure path, and legacy claims are recovered only from
a bounded, explicitly named sentence-ID scope.
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from math import ceil
from typing import Iterable, Sequence

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.orm import Session, aliased, selectinload

from app.models import (
    ActivityLog,
    Lemma,
    Sentence,
    SentenceWord,
    UserLemmaKnowledge,
)
from app.services.activity_log import log_activity
from app.services.canonical_resolution import resolve_canonical_via_map
from app.services.pipeline_tiers import (
    WordTier,
    compute_knowledge_tier,
    compute_word_tiers,
)
from app.services.sentence_eligibility import (
    CORPUS_BLOCKED_SENTINEL,
    CORPUS_CLAIM_SENTINEL,
    CORPUS_QUALITY_REJECTED_SENTINEL,
    MAPPING_VERIFICATION_MIN_AT,
    reviewable_sentence_clauses,
)
from app.services.sentence_validator import is_function_word_lemma


DEFAULT_ENRICH_LIMIT = 20
MAX_ENRICH_LIMIT = 50
MAX_ACTIVATE_LIMIT = 20
PREFLIGHT_OVERFETCH_FACTOR = 4
MAX_PREFLIGHT_ROWS = MAX_ENRICH_LIMIT * PREFLIGHT_OVERFETCH_FACTOR
INTRODUCED_STATES = {"acquiring", "known", "learning", "lapsed"}
FSRS_STATES = {"known", "learning", "lapsed"}
INERT_CATEGORIES = {"proper_name", "onomatopoeia"}


@dataclass(frozen=True)
class CorpusScope:
    """Exact corpus scope.

    At least one of ``kind`` or ``sentence_ids`` is required.  When both are
    present they are intersected.
    """

    kind: str | None = None
    sentence_ids: tuple[int, ...] = ()

    @classmethod
    def build(
        cls,
        *,
        kind: str | None = None,
        sentence_ids: Sequence[int] | None = None,
    ) -> "CorpusScope":
        clean_kind = (kind or "").strip() or None
        clean_ids = tuple(sorted({int(sid) for sid in (sentence_ids or ())}))
        if any(sid <= 0 for sid in clean_ids):
            raise ValueError("corpus sentence IDs must be positive")
        if clean_kind is None and not clean_ids:
            raise ValueError(
                "corpus enrichment requires --kind and/or explicit sentence IDs"
            )
        return cls(kind=clean_kind, sentence_ids=clean_ids)

    def detail(self) -> dict:
        return {
            "kind": self.kind,
            "sentence_ids": list(self.sentence_ids),
        }


@dataclass(frozen=True)
class CorpusDemand:
    """Demand snapshot for one sentence after canonical/inert filtering."""

    content_lemma_ids: frozenset[int] = frozenset()
    introduced_lemma_ids: frozenset[int] = frozenset()
    acquiring_lemma_ids: frozenset[int] = frozenset()
    fsrs_demand_lemma_ids: frozenset[int] = frozenset()
    fsrs_due_lemma_ids: frozenset[int] = frozenset()
    shortage_lemma_ids: frozenset[int] = frozenset()
    best_tier: int = 99
    earliest_due_at: datetime | None = None

    @property
    def has_acquiring(self) -> bool:
        return bool(self.acquiring_lemma_ids)

    def detail(self) -> dict:
        return {
            "content_lemma_ids": sorted(self.content_lemma_ids),
            "introduced_lemma_ids": sorted(self.introduced_lemma_ids),
            "acquiring_lemma_ids": sorted(self.acquiring_lemma_ids),
            "fsrs_demand_lemma_ids": sorted(self.fsrs_demand_lemma_ids),
            "fsrs_due_lemma_ids": sorted(self.fsrs_due_lemma_ids),
            "shortage_lemma_ids": sorted(self.shortage_lemma_ids),
            "best_tier": None if self.best_tier == 99 else self.best_tier,
            "earliest_due_at": (
                self.earliest_due_at.isoformat() if self.earliest_due_at else None
            ),
        }


@dataclass(frozen=True)
class CorpusCandidate:
    sentence_id: int
    kind: str | None
    legacy_claim: bool
    demand: CorpusDemand
    mapping_risk: "CorpusMappingRisk" = field(
        default_factory=lambda: CorpusMappingRisk()
    )

    def detail(self) -> dict:
        return {
            "sentence_id": self.sentence_id,
            "kind": self.kind,
            "legacy_claim": self.legacy_claim,
            **self.demand.detail(),
            "mapping_risk": self.mapping_risk.detail(),
        }


@dataclass(frozen=True)
class CorpusMappingRisk:
    """Read-only deterministic mapping risks found before any external call."""

    unmapped_positions: tuple[int, ...] = ()
    unmapped_tokens: tuple[str, ...] = ()
    proper_name_positions: tuple[int, ...] = ()
    ambiguous_positions: tuple[int, ...] = ()
    via_clitic_positions: tuple[int, ...] = ()
    changed_positions: tuple[int, ...] = ()
    inventory_reason: str | None = None
    inventory_positions: tuple[int, ...] = ()
    token_count: int = 0

    @property
    def guaranteed_incomplete(self) -> bool:
        return self.inventory_reason is not None

    def detail(self) -> dict:
        return {
            "unmapped_positions": list(self.unmapped_positions),
            "unmapped_tokens": list(self.unmapped_tokens),
            "proper_name_positions": list(self.proper_name_positions),
            "ambiguous_positions": list(self.ambiguous_positions),
            "via_clitic_positions": list(self.via_clitic_positions),
            "changed_positions": list(self.changed_positions),
            "inventory_reason": self.inventory_reason,
            "inventory_positions": list(self.inventory_positions),
            "token_count": self.token_count,
        }


@dataclass
class CorpusEnrichmentPlan:
    """Bounded prospective plan plus the risks that were not selected."""

    candidates: list[CorpusCandidate] = field(default_factory=list)
    rows_available: int = 0
    rows_preflighted: int = 0
    preflight_cap: int = 0
    cursor_key: str | None = None
    cursor_start_after_id: int | None = None
    cursor_end_id: int | None = None
    cursor_wrapped: bool = False
    skipped_unmapped_ids: list[int] = field(default_factory=list)
    skipped_inventory_ids: list[int] = field(default_factory=list)
    skipped_no_demand_ids: list[int] = field(default_factory=list)
    risk_by_sentence: dict[int, dict] = field(default_factory=dict)

    def detail(self) -> dict:
        ambiguous = sum(
            bool(risk.get("ambiguous_positions"))
            for risk in self.risk_by_sentence.values()
        )
        changed = sum(
            bool(risk.get("changed_positions"))
            for risk in self.risk_by_sentence.values()
        )
        clitic = sum(
            bool(risk.get("via_clitic_positions"))
            for risk in self.risk_by_sentence.values()
        )
        return {
            "rows_available": self.rows_available,
            "rows_preflighted": self.rows_preflighted,
            "preflight_cap": self.preflight_cap,
            "cursor_key": self.cursor_key,
            "cursor_start_after_id": self.cursor_start_after_id,
            "cursor_end_id": self.cursor_end_id,
            "cursor_wrapped": self.cursor_wrapped,
            "selected_count": len(self.candidates),
            "skipped_unmapped_ids": self.skipped_unmapped_ids,
            "skipped_inventory_ids": self.skipped_inventory_ids,
            "skipped_no_demand_ids": self.skipped_no_demand_ids,
            "risk_metrics": {
                "ambiguous_rows": ambiguous,
                "via_clitic_rows": clitic,
                "stored_mapping_changed_rows": changed,
                "guaranteed_incomplete_rows": len(
                    self.skipped_inventory_ids
                ),
            },
            "risk_by_sentence": {
                str(sentence_id): risk
                for sentence_id, risk in sorted(self.risk_by_sentence.items())
            },
            "candidates": [candidate.detail() for candidate in self.candidates],
        }


@dataclass
class CorpusActivationPlan:
    eligible_ids: list[int] = field(default_factory=list)
    selected_ids: list[int] = field(default_factory=list)
    blocked_acquiring_ids: list[int] = field(default_factory=list)
    no_fsrs_demand_ids: list[int] = field(default_factory=list)
    active_before: int = 0
    active_ceiling: int = 0
    capacity: int = 0
    _planned_state_by_id: dict[int, tuple[dict, tuple]] = field(
        default_factory=dict,
        repr=False,
    )

    def detail(self) -> dict:
        detail = asdict(self)
        detail.pop("_planned_state_by_id", None)
        return detail


@dataclass
class CorpusEnrichmentResult:
    scope: dict
    selected_ids: list[int] = field(default_factory=list)
    recovered_legacy_claim_ids: list[int] = field(default_factory=list)
    recovered_blocked_ids: list[int] = field(default_factory=list)
    preflight_skipped_ids: list[int] = field(default_factory=list)
    mapping_blocked_ids: list[int] = field(default_factory=list)
    translated_ids: list[int] = field(default_factory=list)
    prepared_ids: list[int] = field(default_factory=list)
    activated_ids: list[int] = field(default_factory=list)
    retry_ids: list[int] = field(default_factory=list)
    quality_rejected_ids: list[int] = field(default_factory=list)
    target_rejected_ids: list[int] = field(default_factory=list)
    activation_blocked_acquiring_ids: list[int] = field(default_factory=list)
    activation_no_demand_ids: list[int] = field(default_factory=list)
    diagnostics: list[dict] = field(default_factory=list)
    preflight: dict = field(default_factory=dict)
    failure_reasons: dict[str, int] = field(default_factory=dict)
    active_before: int = 0
    active_ceiling: int = 0
    activation_capacity: int = 0

    @property
    def prepared(self) -> int:
        return len(self.prepared_ids)

    @property
    def activated(self) -> int:
        return len(self.activated_ids)

    def add_failure(self, reason: str, count: int = 1) -> None:
        self.failure_reasons[reason] = self.failure_reasons.get(reason, 0) + count

    def detail(self) -> dict:
        return {
            **asdict(self),
            "prepared": self.prepared,
            "activated": self.activated,
        }


@dataclass
class _LearningContext:
    lemmas: dict[int, Lemma]
    canonical_by_id: dict[int, int | None]
    knowledge_by_id: dict[int, UserLemmaKnowledge]
    tiers_by_id: dict[int, WordTier]
    coverage_by_id: dict[int, int]
    active_target_count_by_id: dict[int, int]
    now: datetime

    def canonical_id(self, lemma_id: int) -> int:
        return resolve_canonical_via_map(lemma_id, self.canonical_by_id)

    def canonical_lemma(self, lemma_id: int) -> Lemma | None:
        return self.lemmas.get(self.canonical_id(lemma_id))

    def is_content(self, lemma_id: int) -> bool:
        lemma = self.canonical_lemma(lemma_id)
        if lemma is None or lemma.word_category in INERT_CATEGORIES:
            return False
        return not is_function_word_lemma(
            lemma.lemma_ar_bare,
            lemma.function_word_override,
        )


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _scope_query(query, scope: CorpusScope):
    query = query.filter(Sentence.source.in_(("corpus", "book")))
    if scope.kind is not None:
        query = query.filter(Sentence.kind == scope.kind)
    if scope.sentence_ids:
        query = query.filter(Sentence.id.in_(scope.sentence_ids))
    return query


def outside_corpus_governor_clause():
    """Exclude authentic rows that have entered the governed QA lifecycle.

    ``NOT (source IN (...) AND quality_reviewed_at IS NOT NULL)`` is not safe
    for legacy rows whose source is NULL: SQL's three-valued logic would also
    exclude those rows.  Spell out the complement so legacy/LLM maintenance
    keeps its historical behavior while reviewed book/corpus rows can only be
    activated by this module's bounded governor.
    """
    return or_(
        Sentence.source.is_(None),
        Sentence.source.notin_(("corpus", "book")),
        Sentence.quality_reviewed_at.is_(None),
    )


def _preflight_cursor_key(
    scope: CorpusScope,
    *,
    include_legacy_claims: bool,
    include_blocked: bool,
    only_blocked: bool,
) -> str:
    """Return an opaque key for one exact candidate universe."""
    payload = repr(
        (
            "corpus-preflight-v1",
            scope.kind,
            scope.sentence_ids,
            include_legacy_claims,
            include_blocked,
            only_blocked,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _latest_preflight_cursor(
    db: Session,
    *,
    cursor_key: str,
) -> int | None:
    """Read the last completed cursor for this exact scope and mode."""
    row = (
        db.query(ActivityLog.detail_json)
        .filter(
            ActivityLog.event_type == "corpus_enrichment_scoped",
            ActivityLog.detail_json.is_not(None),
            ActivityLog.detail_json["preflight"]["cursor_key"].as_string()
            == cursor_key,
        )
        .order_by(ActivityLog.id.desc())
        .first()
    )
    if row is None:
        return None
    detail = row[0] or {}
    cursor = (detail.get("preflight") or {}).get("cursor_end_id")
    try:
        cursor = int(cursor)
    except (TypeError, ValueError):
        return None
    return cursor if cursor > 0 else None


def _load_learning_context(
    db: Session,
    *,
    now: datetime | None = None,
    relevant_raw_lemma_ids: set[int] | None = None,
) -> _LearningContext:
    now = _aware(now) or datetime.now(timezone.utc)
    if relevant_raw_lemma_ids is None:
        lemmas = {lemma.lemma_id: lemma for lemma in db.query(Lemma).all()}
        canonical_by_id = {
            lemma_id: lemma.canonical_lemma_id
            for lemma_id, lemma in lemmas.items()
        }
        knowledge_by_id = {
            row.lemma_id: row for row in db.query(UserLemmaKnowledge).all()
        }
        tiers_by_id = {
            tier.lemma_id: tier for tier in compute_word_tiers(db, now=now)
        }
        relevant_canonical_ids: set[int] | None = None
        coverage_raw_universe = set(lemmas)
    else:
        # Activation needs authoritative state only for lemmas present in the
        # <=20 selected rows. Load the complete canonical ID graph as compact
        # scalar pairs, then fetch ORM state for that small closure. This keeps
        # BEGIN IMMEDIATE short instead of scanning corpus-wide coverage and
        # learner state while all app writes are blocked.
        canonical_by_id = dict(
            db.query(Lemma.lemma_id, Lemma.canonical_lemma_id).all()
        )
        selected_raw_ids = {
            lemma_id
            for lemma_id in relevant_raw_lemma_ids
            if lemma_id in canonical_by_id
        }
        relevant_canonical_ids = {
            resolve_canonical_via_map(lemma_id, canonical_by_id)
            for lemma_id in selected_raw_ids
        }
        coverage_raw_universe = {
            lemma_id
            for lemma_id in canonical_by_id
            if resolve_canonical_via_map(lemma_id, canonical_by_id)
            in relevant_canonical_ids
        }
        lemmas = {
            lemma.lemma_id: lemma
            for lemma in db.query(Lemma)
            .filter(Lemma.lemma_id.in_(coverage_raw_universe))
            .all()
        }
        knowledge_rows = (
            db.query(UserLemmaKnowledge)
            .filter(
                UserLemmaKnowledge.lemma_id.in_(relevant_canonical_ids)
            )
            .all()
            if relevant_canonical_ids
            else []
        )
        knowledge_by_id = {row.lemma_id: row for row in knowledge_rows}
        tiers_by_id = {
            row.lemma_id: compute_knowledge_tier(row, now=now)
            for row in knowledge_rows
            if row.knowledge_state not in {"suspended", "encountered"}
        }
    introduced_ids = {
        lemma_id
        for lemma_id, row in knowledge_by_id.items()
        if row.knowledge_state in INTRODUCED_STATES
    }
    # SentenceWord rows store the surface lemma, while demand and learner state
    # are canonical. Count distinct sentence coverage after canonicalization;
    # summing the existing per-raw-lemma helper would double-count a sentence
    # that contains two variants of the same canonical lemma.
    raw_introduced_ids = {
        lemma_id
        for lemma_id in coverage_raw_universe
        if resolve_canonical_via_map(lemma_id, canonical_by_id) in introduced_ids
    }
    coverage_sentence_ids: dict[int, set[int]] = {}
    if raw_introduced_ids:
        coverage_word = aliased(SentenceWord)
        coverage_rows = (
            db.query(coverage_word.lemma_id, coverage_word.sentence_id)
            .join(Sentence, Sentence.id == coverage_word.sentence_id)
            .filter(
                coverage_word.lemma_id.in_(raw_introduced_ids),
                reviewable_sentence_clauses(),
            )
            .distinct()
            .all()
        )
        for raw_lemma_id, sentence_id in coverage_rows:
            canonical_id = resolve_canonical_via_map(
                raw_lemma_id,
                canonical_by_id,
            )
            coverage_sentence_ids.setdefault(canonical_id, set()).add(
                sentence_id
            )
    coverage_by_id = {
        lemma_id: len(sentence_ids)
        for lemma_id, sentence_ids in coverage_sentence_ids.items()
    }

    active_target_count_by_id: dict[int, int] = {}
    target_query = (
        db.query(Sentence.target_lemma_id, func.count(Sentence.id))
        .filter(
            Sentence.target_lemma_id.isnot(None),
            reviewable_sentence_clauses(),
        )
        .group_by(Sentence.target_lemma_id)
    )
    if relevant_canonical_ids is not None:
        target_query = target_query.filter(
            Sentence.target_lemma_id.in_(coverage_raw_universe)
        )
    for target_id, count in target_query.all():
        canonical_id = resolve_canonical_via_map(target_id, canonical_by_id)
        active_target_count_by_id[canonical_id] = (
            active_target_count_by_id.get(canonical_id, 0) + count
        )

    return _LearningContext(
        lemmas=lemmas,
        canonical_by_id=canonical_by_id,
        knowledge_by_id=knowledge_by_id,
        tiers_by_id=tiers_by_id,
        coverage_by_id=coverage_by_id,
        active_target_count_by_id=active_target_count_by_id,
        now=now,
    )


def _canonical_content_ids(
    words: Iterable[SentenceWord],
    context: _LearningContext,
) -> set[int]:
    content_ids: set[int] = set()
    for word in words:
        if word.lemma_id is None or not context.is_content(word.lemma_id):
            continue
        content_ids.add(context.canonical_id(word.lemma_id))
    return content_ids


def _demand_for_content_ids(
    content_ids: set[int],
    context: _LearningContext,
    *,
    projected_coverage: dict[int, int] | None = None,
) -> CorpusDemand:
    coverage = projected_coverage or context.coverage_by_id
    introduced: set[int] = set()
    acquiring: set[int] = set()
    fsrs_demand: set[int] = set()
    fsrs_due: set[int] = set()
    shortage: set[int] = set()
    due_dates: list[datetime] = []
    tiers: list[int] = []

    for lemma_id in content_ids:
        knowledge = context.knowledge_by_id.get(lemma_id)
        if knowledge is None:
            continue
        state = knowledge.knowledge_state
        if state in INTRODUCED_STATES:
            introduced.add(lemma_id)
        if state == "acquiring":
            acquiring.add(lemma_id)
            continue
        if state not in FSRS_STATES:
            continue

        tier = context.tiers_by_id.get(lemma_id)
        if tier is None or tier.backfill_target <= 0:
            continue
        fsrs_demand.add(lemma_id)
        tiers.append(tier.tier)
        due_at = _aware(tier.due_dt)
        if due_at is not None:
            due_dates.append(due_at)
            if due_at <= context.now:
                fsrs_due.add(lemma_id)
        if coverage.get(lemma_id, 0) < tier.backfill_target:
            shortage.add(lemma_id)

    return CorpusDemand(
        content_lemma_ids=frozenset(content_ids),
        introduced_lemma_ids=frozenset(introduced),
        acquiring_lemma_ids=frozenset(acquiring),
        fsrs_demand_lemma_ids=frozenset(fsrs_demand),
        fsrs_due_lemma_ids=frozenset(fsrs_due),
        shortage_lemma_ids=frozenset(shortage),
        best_tier=min(tiers) if tiers else 99,
        earliest_due_at=min(due_dates) if due_dates else None,
    )


def _candidate_sort_key(candidate: CorpusCandidate) -> tuple:
    demand = candidate.demand
    earliest = demand.earliest_due_at or datetime.max.replace(tzinfo=timezone.utc)
    return (
        1 if demand.has_acquiring else 0,
        0 if demand.shortage_lemma_ids else 1,
        demand.best_tier,
        earliest,
        -len(demand.shortage_lemma_ids),
        -len(demand.fsrs_due_lemma_ids),
        -len(demand.introduced_lemma_ids),
        len(candidate.mapping_risk.ambiguous_positions),
        len(candidate.mapping_risk.via_clitic_positions),
        len(candidate.mapping_risk.changed_positions),
        candidate.mapping_risk.token_count,
        candidate.sentence_id,
    )


def _prospective_mapping_risk(
    sentence: Sentence,
    mappings: list,
    context: _LearningContext,
) -> CorpusMappingRisk:
    stored_by_position = {
        word.position: word.lemma_id for word in sentence.words
    }
    unmapped = [
        mapping
        for mapping in mappings
        if mapping.lemma_id in (None, 0)
        and not getattr(mapping, "is_proper_name", False)
    ]
    proper_names = [
        mapping
        for mapping in mappings
        if mapping.lemma_id in (None, 0)
        and getattr(mapping, "is_proper_name", False)
    ]
    missing_lemma_positions = [
        mapping.position
        for mapping in mappings
        if mapping.lemma_id not in (None, 0)
        and mapping.lemma_id not in context.lemmas
    ]
    glossless_positions = [
        mapping.position
        for mapping in mappings
        if mapping.lemma_id not in (None, 0)
        and mapping.lemma_id in context.lemmas
        and context.is_content(mapping.lemma_id)
        and not (context.lemmas[mapping.lemma_id].gloss_en or "").strip()
    ]
    inventory_reason = None
    inventory_positions: tuple[int, ...] = ()
    if unmapped:
        inventory_reason = "unmapped_token"
        inventory_positions = tuple(mapping.position for mapping in unmapped)
    elif proper_names:
        # Authentic-corpus preparation never creates vocabulary entries.
        inventory_reason = "unresolved_proper_name"
        inventory_positions = tuple(
            mapping.position for mapping in proper_names
        )
    elif missing_lemma_positions:
        inventory_reason = "missing_lemma"
        inventory_positions = tuple(missing_lemma_positions)
    elif glossless_positions:
        inventory_reason = "glossless_lemma"
        inventory_positions = tuple(glossless_positions)

    return CorpusMappingRisk(
        unmapped_positions=tuple(mapping.position for mapping in unmapped),
        unmapped_tokens=tuple(mapping.surface_form for mapping in unmapped),
        proper_name_positions=tuple(
            mapping.position for mapping in proper_names
        ),
        ambiguous_positions=tuple(
            mapping.position
            for mapping in mappings
            if mapping.alternative_lemma_ids
        ),
        via_clitic_positions=tuple(
            mapping.position
            for mapping in mappings
            if getattr(mapping, "via_clitic", False)
        ),
        changed_positions=tuple(
            mapping.position
            for mapping in mappings
            if stored_by_position.get(mapping.position) != mapping.lemma_id
        ),
        inventory_reason=inventory_reason,
        inventory_positions=inventory_positions,
        token_count=len(mappings),
    )


def plan_corpus_enrichment_report(
    db: Session,
    *,
    kind: str | None = None,
    sentence_ids: Sequence[int] | None = None,
    limit: int = DEFAULT_ENRICH_LIMIT,
    include_legacy_claims: bool = True,
    include_blocked: bool = False,
    only_blocked: bool = False,
    now: datetime | None = None,
    cursor_after_id: int | None = None,
) -> CorpusEnrichmentPlan:
    """Return a bounded, cursor-progressive plan without external calls."""
    from app.services.sentence_validator import (
        build_comprehensive_lemma_lookup,
        detect_proper_names,
        map_tokens_to_lemmas,
        normalize_alef,
        strip_diacritics,
        strip_punctuation,
        strip_tatweel,
        tokenize_display,
    )

    scope = CorpusScope.build(kind=kind, sentence_ids=sentence_ids)
    if limit < 0 or limit > MAX_ENRICH_LIMIT:
        raise ValueError(
            f"corpus enrichment limit must be between 0 and {MAX_ENRICH_LIMIT}"
        )
    if cursor_after_id is not None and cursor_after_id <= 0:
        raise ValueError("corpus preflight cursor must be a positive sentence ID")
    report = CorpusEnrichmentPlan()
    if limit == 0:
        return report

    if only_blocked:
        verification_clause = (
            Sentence.mappings_verified_at == CORPUS_BLOCKED_SENTINEL
        )
    else:
        verification_clause = Sentence.mappings_verified_at.is_(None)
        if include_legacy_claims:
            verification_clause = or_(
                verification_clause,
                Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
            )
        if include_blocked:
            verification_clause = or_(
                verification_clause,
                Sentence.mappings_verified_at == CORPUS_BLOCKED_SENTINEL,
            )
    query = (
        db.query(Sentence)
        .options(selectinload(Sentence.words))
        .filter(
            Sentence.is_active.is_(False),
            verification_clause,
        )
    )
    query = _scope_query(query, scope)
    report.rows_available = int(
        _scope_query(
            db.query(func.count(Sentence.id)).filter(
                Sentence.is_active.is_(False),
                verification_clause,
            ),
            scope,
        ).scalar()
        or 0
    )
    report.preflight_cap = min(
        MAX_PREFLIGHT_ROWS,
        max(limit, limit * PREFLIGHT_OVERFETCH_FACTOR),
    )
    report.cursor_key = _preflight_cursor_key(
        scope,
        include_legacy_claims=include_legacy_claims,
        include_blocked=include_blocked,
        only_blocked=only_blocked,
    )
    if cursor_after_id is None:
        cursor_after_id = _latest_preflight_cursor(
            db,
            cursor_key=report.cursor_key,
        )
    report.cursor_start_after_id = cursor_after_id

    if cursor_after_id is None:
        scan_rows = (
            query.order_by(Sentence.id).limit(report.preflight_cap).all()
        )
    else:
        scan_rows = (
            query.filter(Sentence.id > cursor_after_id)
            .order_by(Sentence.id)
            .limit(report.preflight_cap)
            .all()
        )
        remaining = report.preflight_cap - len(scan_rows)
        if remaining > 0:
            wrapped_rows = (
                query.filter(Sentence.id <= cursor_after_id)
                .order_by(Sentence.id)
                .limit(remaining)
                .all()
            )
            if wrapped_rows:
                report.cursor_wrapped = True
                scan_rows.extend(wrapped_rows)
    report.rows_preflighted = len(scan_rows)
    if scan_rows:
        # Record progress in scan order, before the cohort's risk/demand sort.
        # A completed live run persists this through its ActivityLog detail.
        report.cursor_end_id = scan_rows[-1].id
    if not scan_rows:
        return report

    context = _load_learning_context(db, now=now)

    # Stored mappings are only a cheap ordering hint for the bounded cohort.
    # Prospective mappings below are authoritative for both demand and safety.
    stored_order: list[tuple[tuple, Sentence]] = []
    for sentence in scan_rows:
        content_ids = _canonical_content_ids(sentence.words, context)
        demand = _demand_for_content_ids(content_ids, context)
        hint = CorpusCandidate(
            sentence_id=sentence.id,
            kind=sentence.kind,
            legacy_claim=sentence.mappings_verified_at
            == CORPUS_CLAIM_SENTINEL,
            demand=demand,
        )
        stored_order.append(
            (
                (
                    0 if demand.introduced_lemma_ids else 1,
                    *_candidate_sort_key(hint),
                ),
                sentence,
            )
        )
    stored_order.sort(key=lambda item: item[0])
    preflight_rows = [sentence for _, sentence in stored_order]

    lemma_lookup = build_comprehensive_lemma_lookup(db, require_gated=True)
    first_pass_by_id: dict[int, list] = {}
    unmapped_frequency: dict[str, int] = {}
    for sentence in preflight_rows:
        mappings = map_tokens_to_lemmas(
            tokens=tokenize_display(sentence.arabic_text),
            lemma_lookup=lemma_lookup,
            target_lemma_id=0,
            target_bare="",
            proper_names=set(),
        )
        first_pass_by_id[sentence.id] = mappings
        for mapping in mappings:
            if mapping.lemma_id not in (None, 0):
                continue
            bare = normalize_alef(
                strip_diacritics(
                    strip_punctuation(strip_tatweel(mapping.surface_form))
                )
            )
            if bare and len(bare) > 1:
                unmapped_frequency[bare] = unmapped_frequency.get(bare, 0) + 1
    proper_names = detect_proper_names(
        unmapped_frequency,
        lemma_lookup,
        min_frequency=2,
    )

    candidates: list[CorpusCandidate] = []
    for sentence in preflight_rows:
        mappings = (
            map_tokens_to_lemmas(
                tokens=tokenize_display(sentence.arabic_text),
                lemma_lookup=lemma_lookup,
                target_lemma_id=0,
                target_bare="",
                proper_names=proper_names,
            )
            if proper_names
            else first_pass_by_id[sentence.id]
        )
        risk = _prospective_mapping_risk(sentence, mappings, context)
        report.risk_by_sentence[sentence.id] = risk.detail()
        if risk.guaranteed_incomplete:
            report.skipped_inventory_ids.append(sentence.id)
            if risk.unmapped_positions:
                report.skipped_unmapped_ids.append(sentence.id)
            continue

        content_ids = _canonical_content_ids(mappings, context)
        demand = _demand_for_content_ids(content_ids, context)
        if not demand.introduced_lemma_ids:
            report.skipped_no_demand_ids.append(sentence.id)
            continue
        candidates.append(
            CorpusCandidate(
                sentence_id=sentence.id,
                kind=sentence.kind,
                legacy_claim=sentence.mappings_verified_at
                == CORPUS_CLAIM_SENTINEL,
                demand=demand,
                mapping_risk=risk,
            )
        )
    candidates.sort(key=_candidate_sort_key)
    report.candidates = candidates[:limit]
    report.skipped_unmapped_ids.sort()
    report.skipped_inventory_ids.sort()
    report.skipped_no_demand_ids.sort()
    return report


def plan_corpus_enrichment(
    db: Session,
    *,
    kind: str | None = None,
    sentence_ids: Sequence[int] | None = None,
    limit: int = DEFAULT_ENRICH_LIMIT,
    include_legacy_claims: bool = True,
    include_blocked: bool = False,
    only_blocked: bool = False,
    now: datetime | None = None,
    cursor_after_id: int | None = None,
) -> list[CorpusCandidate]:
    """Backward-compatible candidate-only view of the prospective plan."""
    return plan_corpus_enrichment_report(
        db,
        kind=kind,
        sentence_ids=sentence_ids,
        limit=limit,
        include_legacy_claims=include_legacy_claims,
        include_blocked=include_blocked,
        only_blocked=only_blocked,
        now=now,
        cursor_after_id=cursor_after_id,
    ).candidates


def recover_scoped_legacy_claims(
    db: Session,
    scope: CorpusScope,
    *,
    limit: int,
) -> list[int]:
    """Reset a bounded set of explicitly named historical claim sentinels."""
    if not scope.sentence_ids:
        raise ValueError(
            "recovering corpus claims requires explicit sentence IDs"
        )
    if limit < 0 or limit > MAX_ENRICH_LIMIT:
        raise ValueError(
            f"claim recovery limit must be between 0 and {MAX_ENRICH_LIMIT}"
        )
    if limit == 0:
        return []
    query = db.query(Sentence.id).filter(
        Sentence.is_active.is_(False),
        Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
    )
    query = _scope_query(query, scope)
    recovered_ids = [
        row[0]
        for row in query.order_by(Sentence.id).limit(limit).all()
    ]
    if not recovered_ids:
        return []
    db.query(Sentence).filter(
        Sentence.id.in_(recovered_ids),
        Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
    ).update(
        {
            Sentence.mappings_verified_at: None,
            Sentence.is_active: False,
        },
        synchronize_session=False,
    )
    db.commit()
    return recovered_ids


def retry_exact_blocked_sentences(
    db: Session,
    scope: CorpusScope,
) -> list[int]:
    """Identify explicitly named blocked rows eligible for a curated retry.

    Passing a kind alone is intentionally insufficient: inventory curation is
    reviewed against exact sentence IDs, and a broad reset would silently
    re-open the whole durable backlog.  This lookup is deliberately read-only:
    the caller atomically transitions each selected row from the durable
    blocker to the transient claim, so there is no externally visible NULL
    interval in which another maintenance path could mistake it for ordinary
    work.
    """
    if not scope.sentence_ids:
        raise ValueError(
            "retrying blocked corpus rows requires explicit sentence IDs"
        )
    query = db.query(Sentence).filter(
        Sentence.id.in_(scope.sentence_ids),
        Sentence.is_active.is_(False),
        Sentence.mappings_verified_at == CORPUS_BLOCKED_SENTINEL,
    )
    query = _scope_query(query, scope)
    return [
        sentence.id
        for sentence in query.order_by(Sentence.id).all()
    ]


_CORPUS_ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "diacritized": {"type": "string"},
                    "translation": {"type": "string"},
                },
                "required": ["id", "diacritized", "translation"],
            },
        },
    },
    "required": ["sentences"],
}

_PROJECTABLE_HARAKAT = frozenset(
    chr(codepoint) for codepoint in range(0x064B, 0x0653)
)
_VOWEL_OR_SUKUN_HARAKAT = frozenset(
    {
        "\u064b",  # fathatan
        "\u064c",  # dammatan
        "\u064d",  # kasratan
        "\u064e",  # fatha
        "\u064f",  # damma
        "\u0650",  # kasra
        "\u0652",  # sukun
    }
)
_SHADDA = "\u0651"
_TATWEEL = "\u0640"
_CORPUS_ENRICH_PROVIDER_OVERRIDES = frozenset({"openai", "anthropic"})


def _is_layout_separator(char: str) -> bool:
    """Whether a provider may reformat this character without changing text.

    The provider's layout is never stored. Punctuation and whitespace merely
    delimit immutable content tokens, while tatweel is ignored for alignment
    and retained exactly from the source during reconstruction.
    """
    return (
        char.isspace()
        or char == _TATWEEL
        or unicodedata.category(char).startswith("P")
    )


def _content_tokens(text: str) -> tuple[str, ...]:
    """Return exact NFC content tokens with ordinary harakat removed.

    Token boundaries remain part of identity: ``كل ما`` must not align with
    ``كلما``. Only provider punctuation/spacing/tatweel layout is flexible.
    Identity-bearing marks such as combining hamza, maddah, dagger alef, and
    Quranic annotations remain in the tokens and therefore cannot be added or
    removed through the harakat path.
    """
    tokens: list[str] = []
    current: list[str] = []
    for char in text:
        if char in _PROJECTABLE_HARAKAT:
            continue
        if char == _TATWEEL:
            continue
        if _is_layout_separator(char):
            if current:
                tokens.append(unicodedata.normalize("NFC", "".join(current)))
                current = []
            continue
        current.append(char)
    if current:
        tokens.append(unicodedata.normalize("NFC", "".join(current)))
    return tuple(tokens)


def _numeric_separator_signature(
    text: str,
) -> tuple[tuple[int, str], ...]:
    """Protect punctuation whose identity changes a written number."""
    signature: list[tuple[int, str]] = []
    digits_seen = 0
    for index, char in enumerate(text):
        if char.isdigit():
            digits_seen += 1
        if not unicodedata.category(char).startswith("P"):
            continue
        previous = index - 1
        while previous >= 0 and (
            text[previous] in _PROJECTABLE_HARAKAT
            or text[previous] == _TATWEEL
        ):
            previous -= 1
        following = index + 1
        while following < len(text) and (
            text[following] in _PROJECTABLE_HARAKAT
            or text[following] == _TATWEEL
        ):
            following += 1
        if (
            previous >= 0
            and following < len(text)
            and text[previous].isdigit()
            and text[following].isdigit()
        ):
            # Bind the separator to the exact preceding digit ordinal. Merely
            # comparing separator characters would accept regrouping such as
            # ``١،٢ ٣،٤`` -> ``١،٢،٣ ٤``.
            signature.append((digits_seen, char))
    return tuple(signature)


def _is_arabic_base_letter(char: str) -> bool:
    if char == _TATWEEL or not unicodedata.category(char).startswith("L"):
        return False
    return unicodedata.name(char, "").startswith("ARABIC LETTER")


def _arabic_harakat_clusters(
    text: str,
) -> list[tuple[str, tuple[str, ...]]] | None:
    """Collect ordinary harakat attached to each Arabic base letter.

    Non-projectable combining marks stay in the content identity comparison.
    An ordinary haraka anywhere except the combining cluster immediately after
    an Arabic base is ambiguous and rejects the proposal.
    """
    clusters: list[tuple[str, tuple[str, ...]]] = []
    index = 0
    while index < len(text):
        char = text[index]
        if _is_arabic_base_letter(char):
            marks: list[str] = []
            cursor = index + 1
            while (
                cursor < len(text)
                and unicodedata.category(text[cursor]).startswith("M")
            ):
                if text[cursor] in _PROJECTABLE_HARAKAT:
                    marks.append(text[cursor])
                cursor += 1
            clusters.append((char, tuple(marks)))
            index = cursor
            continue
        if char in _PROJECTABLE_HARAKAT:
            return None
        index += 1
    return clusters


def _valid_harakat_cluster(marks: tuple[str, ...]) -> bool:
    if any(mark not in _PROJECTABLE_HARAKAT for mark in marks):
        return False
    if len(set(marks)) != len(marks):
        return False
    vowel_or_sukun = set(marks) & _VOWEL_OR_SUKUN_HARAKAT
    if len(vowel_or_sukun) > 1:
        return False
    if _SHADDA in marks and "\u0652" in marks:
        return False
    return True


def _project_diacritics_onto_source(
    source: str,
    proposed: str,
) -> str | None:
    """Project validated ordinary harakat onto the immutable source layout.

    The provider may change punctuation, quote style, whitespace, or tatweel,
    because none of those characters are copied from its response. Exact NFC
    content tokens, word boundaries, digits, and Arabic letter identities must
    still match. Only U+064B–U+0652 may be transferred.
    """
    if not source or not proposed:
        return None
    if _content_tokens(source) != _content_tokens(proposed):
        return None
    if _numeric_separator_signature(source) != _numeric_separator_signature(
        proposed
    ):
        return None

    source_clusters = _arabic_harakat_clusters(source)
    proposed_clusters = _arabic_harakat_clusters(proposed)
    if (
        source_clusters is None
        or proposed_clusters is None
        or len(source_clusters) != len(proposed_clusters)
    ):
        return None

    merged_marks: list[tuple[str, ...]] = []
    for (_, existing), (_, supplied) in zip(
        source_clusters,
        proposed_clusters,
        strict=True,
    ):
        if not _valid_harakat_cluster(existing):
            return None
        if not _valid_harakat_cluster(supplied):
            return None
        merged = list(supplied)
        merged.extend(mark for mark in existing if mark not in merged)
        merged_tuple = tuple(merged)
        if not _valid_harakat_cluster(merged_tuple):
            return None
        merged_marks.append(merged_tuple)

    output: list[str] = []
    letter_index = 0
    index = 0
    while index < len(source):
        char = source[index]
        if _is_arabic_base_letter(char):
            output.append(char)
            cursor = index + 1
            while (
                cursor < len(source)
                and unicodedata.category(source[cursor]).startswith("M")
            ):
                if source[cursor] not in _PROJECTABLE_HARAKAT:
                    output.append(source[cursor])
                cursor += 1
            output.extend(merged_marks[letter_index])
            letter_index += 1
            index = cursor
            continue
        if char in _PROJECTABLE_HARAKAT:
            return None
        output.append(char)
        index += 1
    return "".join(output)


def _corpus_enrichment_provider() -> str:
    provider = (os.environ.get("ALIF_CORPUS_ENRICH_PROVIDER") or "").strip()
    if not provider:
        return "claude_haiku"
    if provider not in _CORPUS_ENRICH_PROVIDER_OVERRIDES:
        raise ValueError(
            "ALIF_CORPUS_ENRICH_PROVIDER must be one of "
            + ", ".join(sorted(_CORPUS_ENRICH_PROVIDER_OVERRIDES))
        )
    return provider


def has_arabic_diacritics(arabic_text: str | None) -> bool:
    """Return whether Arabic text has substantial lexical vowel coverage.

    One stray mark is not enough for the corpus contract of fully vowelized
    learner-facing text. Long-vowel letters are legitimately unmarked, so use
    a conservative 40% marked-letter floor rather than demanding a mark on
    every Arabic base letter.
    """
    text = arabic_text or ""
    arabic_letter_indexes = [
        index
        for index, char in enumerate(text)
        if (
            0x0621 <= ord(char) <= 0x063A
            or 0x0641 <= ord(char) <= 0x064A
            or ord(char) == 0x0671
        )
    ]
    if not arabic_letter_indexes:
        return False

    marked_letters = 0
    for index in arabic_letter_indexes:
        cursor = index + 1
        while cursor < len(text):
            codepoint = ord(text[cursor])
            if 0x064B <= codepoint <= 0x0652 or codepoint == 0x0670:
                marked_letters += 1
                break
            # Other combining marks may precede the lexical vowel.
            if not 0x0610 <= codepoint <= 0x061A and not 0x0653 <= codepoint <= 0x065F:
                break
            cursor += 1

    required = max(
        1,
        2 if len(arabic_letter_indexes) >= 4 else 1,
        ceil(len(arabic_letter_indexes) * 0.40),
    )
    return marked_letters >= required


def generate_corpus_enrichment_batch(
    sentences: list[Sentence],
) -> dict[int, dict[str, str]]:
    """Diacritize and translate corpus sentences in one structured LLM call."""
    from app.services.llm import AllProvidersFailed, generate_completion

    if not sentences:
        return {}
    lines = [
        (
            f"- id={sent.id}: {sent.arabic_text}\n"
            "  exact non-diacritic content tokens: "
            + json.dumps(
                list(_content_tokens(sent.arabic_text)),
                ensure_ascii=False,
            )
        )
        for sent in sentences
    ]
    prompt = (
        "For each Arabic sentence below:\n"
        "1. Add full tashkeel (diacritics/vowelization) to the Arabic text. "
        "Keep the exact same Unicode words and letters; only add harakat. "
        "Never normalize spelling or substitute distinct letters such as "
        "ى/ي or ة/ه. Verify that the output has the exact content-token list "
        "shown for its id after harakat are removed.\n"
        "2. Translate it faithfully into natural English.\n\n"
        + "\n".join(lines)
        + "\n\nReturn JSON exactly as "
        '{"sentences": [{"id": 1, "diacritized": "...", "translation": "..."}]}.'
    )
    try:
        result = generate_completion(
            prompt=prompt,
            system_prompt=(
                "Add diacritics and faithfully translate Arabic sentences. "
                "Return JSON only."
            ),
            json_schema=_CORPUS_ENRICH_SCHEMA,
            temperature=0.0,
            model_override=_corpus_enrichment_provider(),
            task_type="corpus_enrichment",
        )
    except AllProvidersFailed:
        return {}

    if not isinstance(result, dict) or not isinstance(result.get("sentences"), list):
        return {}
    requested_ids = {sent.id for sent in sentences}
    output: dict[int, dict[str, str]] = {}
    seen_ids: set[int] = set()
    for item in result["sentences"]:
        if not isinstance(item, dict):
            continue
        sentence_id = item.get("id")
        if (
            not isinstance(sentence_id, int)
            or isinstance(sentence_id, bool)
            or sentence_id not in requested_ids
        ):
            continue
        if sentence_id in seen_ids:
            # A duplicate ID makes the provider response ambiguous. Remove it
            # so the caller releases this row for retry.
            output.pop(sentence_id, None)
            continue
        seen_ids.add(sentence_id)
        diacritized = (item.get("diacritized") or "").strip()
        translation = (item.get("translation") or "").strip()
        if diacritized or translation:
            output[sentence_id] = {
                "diacritized": diacritized,
                "translation": translation,
            }
    return output


def _same_letters(original: str, diacritized: str) -> bool:
    """Backward-compatible identity predicate for one-off diagnostics."""
    return _project_diacritics_onto_source(original, diacritized) is not None


def _claim_candidates(
    db: Session,
    candidate_ids: Sequence[int],
    *,
    blocked_retry_ids: set[int] | None = None,
) -> list[int]:
    if not candidate_ids:
        return []
    blocked_retry_ids = blocked_retry_ids or set()
    claimed_ids: list[int] = []
    claimed_blocked_ids: list[int] = []
    for sentence_id in candidate_ids:
        expected_disposition = (
            CORPUS_BLOCKED_SENTINEL
            if sentence_id in blocked_retry_ids
            else None
        )
        updated = (
            db.query(Sentence)
            .filter(
                Sentence.id == sentence_id,
                Sentence.is_active.is_(False),
                (
                    Sentence.mappings_verified_at
                    == expected_disposition
                    if expected_disposition is not None
                    else Sentence.mappings_verified_at.is_(None)
                ),
            )
            .update(
                {Sentence.mappings_verified_at: CORPUS_CLAIM_SENTINEL},
                synchronize_session=False,
            )
        )
        if updated:
            claimed_ids.append(sentence_id)
            if expected_disposition == CORPUS_BLOCKED_SENTINEL:
                claimed_blocked_ids.append(sentence_id)

    # A completed passing review remains useful evidence because an exact
    # blocked retry changes inventory/mappings, not sentence text.  Clear every
    # other stale or legacy pseudo-verdict only after the Jan-2 -> Jan-1 claim
    # succeeds, so a concurrently claimed row is never mutated.
    if claimed_blocked_ids:
        blocked_rows = (
            db.query(Sentence)
            .filter(
                Sentence.id.in_(claimed_blocked_ids),
                Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
            )
            .all()
        )
        for sentence in blocked_rows:
            if not (
                sentence.quality_reviewed_at is not None
                and sentence.quality_natural is True
                and sentence.quality_translation_correct is True
            ):
                sentence.quality_reviewed_at = None
                sentence.quality_natural = None
                sentence.quality_translation_correct = None
                sentence.quality_reason = None
    db.commit()
    return claimed_ids


def _release_claims(
    db: Session,
    pending_claim_ids: set[int],
    blocked_retry_ids: set[int],
) -> list[int]:
    """Release claims to their correct ordinary or durable disposition."""
    # Always discard any partial work before a failure logger can commit it.
    # This matters after enrichment claims are resolved but activation has
    # dirtied several rows and then raises before its final commit.
    db.rollback()
    if not pending_claim_ids:
        return []
    releasable = [
        row[0]
        for row in db.query(Sentence.id)
        .filter(
            Sentence.id.in_(pending_claim_ids),
            Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
        )
        .all()
    ]
    blocked_releasable = sorted(set(releasable) & blocked_retry_ids)
    ordinary_releasable = sorted(set(releasable) - blocked_retry_ids)
    if blocked_releasable:
        db.query(Sentence).filter(
            Sentence.id.in_(blocked_releasable),
            Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
        ).update(
            {
                Sentence.mappings_verified_at: CORPUS_BLOCKED_SENTINEL,
                Sentence.is_active: False,
            },
            synchronize_session=False,
        )
    if ordinary_releasable:
        db.query(Sentence).filter(
            Sentence.id.in_(ordinary_releasable),
            Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
        ).update(
            {
                Sentence.mappings_verified_at: None,
                Sentence.is_active: False,
            },
            synchronize_session=False,
        )
    if releasable:
        db.commit()
    pending_claim_ids.difference_update(releasable)
    return releasable


def _mark_retry(
    db: Session,
    sentence_ids: Iterable[int],
    pending_claim_ids: set[int],
    blocked_retry_ids: set[int],
    result: CorpusEnrichmentResult,
    reason: str,
    *,
    expected_content_by_id: dict[int, dict[str, str | None]] | None = None,
    content_change_reason: str = "content_changed_during_processing",
) -> None:
    ids = sorted(set(sentence_ids))
    if not ids:
        return
    released_ids: list[int] = []
    lost_ids: list[int] = []
    content_guard_failed_ids: list[int] = []
    for sentence_id in ids:
        disposition = (
            CORPUS_BLOCKED_SENTINEL
            if sentence_id in blocked_retry_ids
            else None
        )
        update_query = db.query(Sentence).filter(
            Sentence.id == sentence_id,
            Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
        )
        expected_content = (
            expected_content_by_id.get(sentence_id)
            if expected_content_by_id is not None
            else None
        )
        if expected_content is not None:
            update_query = update_query.filter(
                Sentence.is_active.is_(False),
                Sentence.arabic_text == expected_content["arabic"],
                Sentence.english_translation == expected_content["english"],
            )
        updated = update_query.update(
            {
                Sentence.mappings_verified_at: disposition,
                Sentence.is_active: False,
            },
            synchronize_session=False,
        )
        if updated:
            released_ids.append(sentence_id)
        elif expected_content is not None:
            content_guard_failed_ids.append(sentence_id)
        else:
            lost_ids.append(sentence_id)
    db.commit()
    pending_claim_ids.difference_update(released_ids)
    result.retry_ids.extend(
        sid for sid in released_ids if sid not in result.retry_ids
    )
    if released_ids:
        result.add_failure(reason, len(released_ids))
    for sentence_id in content_guard_failed_ids:
        _release_content_changed_for_retry(
            db,
            sentence_id,
            pending_claim_ids=pending_claim_ids,
            blocked_retry_ids=blocked_retry_ids,
            result=result,
            reason=content_change_reason,
            expected_content=expected_content_by_id[sentence_id],
        )
    for sentence_id in lost_ids:
        _record_claim_lost(
            result,
            pending_claim_ids,
            sentence_id=sentence_id,
            phase=f"retry_{reason}",
        )


def _release_content_changed_for_retry(
    db: Session,
    sentence_id: int,
    *,
    pending_claim_ids: set[int],
    blocked_retry_ids: set[int],
    result: CorpusEnrichmentResult,
    reason: str,
    expected_content: dict[str, str | None],
    claim_lost_phase: str | None = None,
) -> None:
    """Release an owned claim after its reviewed text changed concurrently.

    The row must remain invisible, and any QA verdict for the old text must be
    cleared. Rows that entered through an explicit blocked retry return to that
    durable disposition instead of being silently promoted to an ordinary
    unreviewed row.
    """
    db.rollback()
    disposition = (
        CORPUS_BLOCKED_SENTINEL
        if sentence_id in blocked_retry_ids
        else None
    )
    matched = (
        db.query(Sentence)
        .filter(
            Sentence.id == sentence_id,
            Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
        )
        .update(
            {Sentence.mappings_verified_at: CORPUS_CLAIM_SENTINEL},
            synchronize_session=False,
        )
    )
    if not matched:
        db.rollback()
        _record_claim_lost(
            result,
            pending_claim_ids,
            sentence_id=sentence_id,
            phase=claim_lost_phase or reason,
        )
        return

    db.expire_all()
    sentence = (
        db.query(Sentence)
        .filter(Sentence.id == sentence_id)
        .with_for_update()
        .one_or_none()
    )
    if sentence is None:
        db.rollback()
        _record_claim_lost(
            result,
            pending_claim_ids,
            sentence_id=sentence_id,
            phase=claim_lost_phase or reason,
        )
        return

    values = {
        Sentence.mappings_verified_at: disposition,
        Sentence.is_active: False,
        Sentence.quality_reviewed_at: None,
        Sentence.quality_natural: None,
        Sentence.quality_translation_correct: None,
        Sentence.quality_reason: None,
    }
    if sentence.arabic_text != expected_content["arabic"]:
        from app.services.transliteration import transliterate_arabic

        values[Sentence.transliteration] = (
            transliterate_arabic(sentence.arabic_text) or ""
        )
    updated = (
        db.query(Sentence)
        .filter(
            Sentence.id == sentence_id,
            Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
        )
        .update(values, synchronize_session=False)
    )
    if not updated:
        db.rollback()
        _record_claim_lost(
            result,
            pending_claim_ids,
            sentence_id=sentence_id,
            phase=claim_lost_phase or reason,
        )
        return
    db.commit()
    pending_claim_ids.discard(sentence_id)
    if sentence_id not in result.retry_ids:
        result.retry_ids.append(sentence_id)
    result.add_failure(reason)
    _record_diagnostic(
        result,
        sentence_id=sentence_id,
        disposition="retry",
        reason=reason,
    )


def _target_choice(
    mappings: list,
    context: _LearningContext,
    original_target_id: int | None,
) -> tuple[int | None, int | None]:
    """Return ``(canonical target lemma ID, target token position)``."""
    choices: list[tuple[tuple, int, int]] = []
    seen_positions: set[int] = set()
    for mapping in mappings:
        lemma_id = mapping.lemma_id if mapping.lemma_id not in (None, 0) else None
        if lemma_id is None or mapping.position in seen_positions:
            continue
        seen_positions.add(mapping.position)
        if not context.is_content(lemma_id):
            continue
        canonical_id = context.canonical_id(lemma_id)
        knowledge = context.knowledge_by_id.get(canonical_id)
        state = knowledge.knowledge_state if knowledge else None
        tier = context.tiers_by_id.get(canonical_id)
        due_at = _aware(tier.due_dt) if tier else None
        coverage = context.coverage_by_id.get(canonical_id, 0)
        target_count = context.active_target_count_by_id.get(canonical_id, 0)

        if state in FSRS_STATES and tier and tier.backfill_target > 0:
            shortage = max(0, tier.backfill_target - coverage)
            lane = 0 if shortage else 1
            tier_rank = tier.tier
        elif state in FSRS_STATES:
            shortage = 0
            lane = 2
            tier_rank = 99
        elif state == "acquiring":
            shortage = 0
            lane = 3
            tier_rank = 99
        elif state == "encountered":
            shortage = 0
            lane = 4
            tier_rank = 99
        elif state == "suspended":
            continue
        else:
            shortage = 0
            lane = 5
            tier_rank = 99

        original_canonical = (
            context.canonical_id(original_target_id)
            if original_target_id in context.lemmas
            else None
        )
        key = (
            lane,
            -shortage,
            tier_rank,
            due_at or datetime.max.replace(tzinfo=timezone.utc),
            coverage,
            target_count,
            0 if canonical_id == original_canonical else 1,
            canonical_id,
            mapping.position,
        )
        choices.append((key, canonical_id, mapping.position))
    if not choices:
        return None, None
    choices.sort(key=lambda item: item[0])
    _, target_id, position = choices[0]
    return target_id, position


def _no_target_reason(
    mappings: list,
    context: _LearningContext,
) -> str:
    """Explain why a complete mapping has no currently valid target."""
    content_states: list[str | None] = []
    for mapping in mappings:
        lemma_id = mapping.lemma_id if mapping.lemma_id not in (None, 0) else None
        if lemma_id is None or not context.is_content(lemma_id):
            continue
        canonical_id = context.canonical_id(lemma_id)
        knowledge = context.knowledge_by_id.get(canonical_id)
        content_states.append(knowledge.knowledge_state if knowledge else None)
    if content_states and all(state == "suspended" for state in content_states):
        return "target_content_suspended"
    return "no_valid_content_target"


def _mapping_is_complete(
    mappings: list,
    context: _LearningContext,
) -> tuple[bool, str | None]:
    for mapping in mappings:
        lemma_id = mapping.lemma_id if mapping.lemma_id not in (None, 0) else None
        if lemma_id is None:
            if getattr(mapping, "is_proper_name", False):
                return False, "unresolved_proper_name"
            return False, "unmapped_token"
        lemma = context.lemmas.get(lemma_id)
        if lemma is None:
            return False, "missing_lemma"
        if (
            context.is_content(lemma_id)
            and not (lemma.gloss_en or "").strip()
        ):
            return False, "glossless_lemma"
    return True, None


def _write_final_mappings(
    db: Session,
    sentence: Sentence,
    mappings: list,
    *,
    target_lemma_id: int,
    target_position: int,
    context: _LearningContext,
) -> bool:
    """Replace token mappings and repair exactly one canonical target flag."""
    resolved: list[tuple[object, int]] = []
    for mapping in mappings:
        lemma_id = mapping.lemma_id if mapping.lemma_id not in (None, 0) else None
        if lemma_id is None:
            return False
        resolved.append((mapping, lemma_id))

    db.query(SentenceWord).filter(
        SentenceWord.sentence_id == sentence.id
    ).delete(synchronize_session="fetch")
    # Make the replacement boundary explicit. This also prevents SQLite test
    # databases from reusing a just-deleted row ID while the old identity is
    # still pending in the ORM session.
    db.flush()
    target_written = False
    for mapping, lemma_id in resolved:
        canonical_id = (
            context.canonical_id(lemma_id)
            if lemma_id in context.canonical_by_id
            else lemma_id
        )
        is_target = (
            not target_written
            and mapping.position == target_position
            and canonical_id == target_lemma_id
        )
        db.add(
            SentenceWord(
                sentence_id=sentence.id,
                position=mapping.position,
                surface_form=mapping.surface_form,
                lemma_id=lemma_id,
                is_target_word=is_target,
            )
        )
        target_written = target_written or is_target
    if not target_written:
        return False
    sentence.target_lemma_id = target_lemma_id
    return True


def _mapping_state_signature(
    sentence: Sentence,
    words: Iterable[SentenceWord] | None = None,
) -> tuple:
    """Compact version signature for mappings observed before an LLM call."""
    word_rows = list(words if words is not None else sentence.words)
    return (
        sentence.target_lemma_id,
        tuple(
            sorted(
                (
                    word.id,
                    word.position,
                    word.surface_form,
                    word.lemma_id,
                    bool(word.is_target_word),
                )
                for word in word_rows
            )
        ),
    )


def _activation_parent_state(sentence: Sentence) -> dict:
    """Return parent fields whose derived mapping/QA state depends on them."""
    return {
        "arabic": sentence.arabic_text,
        "english": sentence.english_translation,
        "source": sentence.source,
        "kind": sentence.kind,
        "target": sentence.target_lemma_id,
        "mapping_stamp": sentence.mappings_verified_at,
        "quality_stamp": sentence.quality_reviewed_at,
        "quality_natural": sentence.quality_natural,
        "quality_translation": sentence.quality_translation_correct,
        "quality_reason": sentence.quality_reason,
    }


def _invalidate_content_drift(
    sentence: Sentence,
    expected_parent: dict,
) -> bool:
    """Invalidate unchanged derived artifacts after parent content drift."""
    arabic_changed = sentence.arabic_text != expected_parent.get("arabic")
    english_changed = (
        sentence.english_translation != expected_parent.get("english")
    )
    if not (arabic_changed or english_changed):
        return False

    sentence.is_active = False
    if sentence.mappings_verified_at == expected_parent.get("mapping_stamp"):
        sentence.mappings_verified_at = None
    if sentence.quality_reviewed_at == expected_parent.get("quality_stamp"):
        sentence.quality_reviewed_at = None
        sentence.quality_natural = None
        sentence.quality_translation_correct = None
        sentence.quality_reason = None
    if arabic_changed:
        from app.services.transliteration import transliterate_arabic

        sentence.transliteration = (
            transliterate_arabic(sentence.arabic_text) or ""
        )
    return True


def _position_diagnostics(
    mappings: Iterable,
    positions: Iterable[int],
    issues: Iterable[dict] | None = None,
) -> list[dict]:
    by_position = {mapping.position: mapping for mapping in mappings}
    issue_by_position = {
        issue.get("position"): issue
        for issue in (issues or [])
        if isinstance(issue, dict)
        and isinstance(issue.get("position"), int)
        and not isinstance(issue.get("position"), bool)
    }
    detail: list[dict] = []
    for position in sorted(set(positions)):
        mapping = by_position.get(position)
        issue = issue_by_position.get(position, {})
        row = {
            "position": position,
            "surface_form": (
                str(getattr(mapping, "surface_form", ""))[:80]
                if mapping is not None
                else ""
            ),
            "current_lemma_id": (
                getattr(mapping, "lemma_id", None)
                if mapping is not None
                else None
            ),
        }
        if issue:
            row["proposed_lemma"] = str(
                issue.get("correct_lemma_ar", "")
            )[:80]
            row["proposed_gloss"] = str(
                issue.get("correct_gloss", "")
            )[:120]
            row["proposed_pos"] = str(issue.get("correct_pos", ""))[:40]
        detail.append(row)
    return detail


def _record_diagnostic(
    result: CorpusEnrichmentResult,
    *,
    sentence_id: int,
    disposition: str,
    reason: str,
    positions: list[dict] | None = None,
) -> dict:
    diagnostic = {
        "sentence_id": sentence_id,
        "disposition": disposition,
        "reason": reason,
        "positions": positions or [],
    }
    result.diagnostics.append(diagnostic)
    return diagnostic


def _record_claim_lost(
    result: CorpusEnrichmentResult,
    pending_claim_ids: set[int],
    *,
    sentence_id: int,
    phase: str,
) -> None:
    """Stop touching a row whose transient claim changed concurrently."""
    pending_claim_ids.discard(sentence_id)
    reason = f"claim_lost_{phase}"
    _record_diagnostic(
        result,
        sentence_id=sentence_id,
        disposition="skipped",
        reason=reason,
    )
    result.add_failure(reason)


def _mark_mapping_blocked(
    db: Session,
    sentence_id: int,
    *,
    pending_claim_ids: set[int],
    blocked_retry_ids: set[int],
    result: CorpusEnrichmentResult,
    reason: str,
    expected_content: dict[str, str | None],
    expected_mapping_state: tuple,
    positions: list[dict] | None = None,
) -> bool:
    # Acquire the parent write boundary before inspecting child mappings. This
    # prevents a stale terminal verifier result from stranding a mapping that
    # a non-flock writer repaired while the external call was in flight.
    matched = (
        db.query(Sentence)
        .filter(
            Sentence.id == sentence_id,
            Sentence.is_active.is_(False),
            Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
            Sentence.arabic_text == expected_content["arabic"],
            Sentence.english_translation == expected_content["english"],
        )
        .update(
            {Sentence.mappings_verified_at: CORPUS_CLAIM_SENTINEL},
            synchronize_session=False,
        )
    )
    if not matched:
        db.rollback()
        _release_content_changed_for_retry(
            db,
            sentence_id=sentence_id,
            pending_claim_ids=pending_claim_ids,
            blocked_retry_ids=blocked_retry_ids,
            result=result,
            reason="content_changed_during_mapping",
            expected_content=expected_content,
            claim_lost_phase="mapping_block",
        )
        return False

    db.expire_all()
    sentence = (
        db.query(Sentence)
        .filter(Sentence.id == sentence_id)
        .with_for_update()
        .one_or_none()
    )
    words = (
        db.query(SentenceWord)
        .filter(SentenceWord.sentence_id == sentence_id)
        .order_by(SentenceWord.id.asc())
        .with_for_update()
        .all()
    )
    if (
        sentence is None
        or _mapping_state_signature(sentence, words)
        != expected_mapping_state
    ):
        db.rollback()
        _mark_retry(
            db,
            [sentence_id],
            pending_claim_ids,
            blocked_retry_ids,
            result,
            "mapping_state_changed_during_verification",
            expected_content_by_id={sentence_id: expected_content},
            content_change_reason="content_changed_during_mapping",
        )
        return False

    values = {
        Sentence.is_active: False,
        Sentence.mappings_verified_at: CORPUS_BLOCKED_SENTINEL,
    }
    # Inventory/mapping failure is not a linguistic QA verdict. Preserve a
    # completed early pass, but never manufacture QA=false or a review stamp.
    if not (
        sentence.quality_reviewed_at is not None
        and sentence.quality_natural is True
        and sentence.quality_translation_correct is True
    ):
        values.update(
            {
                Sentence.quality_reviewed_at: None,
                Sentence.quality_natural: None,
                Sentence.quality_translation_correct: None,
                Sentence.quality_reason: None,
            }
        )
    updated = (
        db.query(Sentence)
        .filter(
            Sentence.id == sentence_id,
            Sentence.is_active.is_(False),
            Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
            Sentence.arabic_text == expected_content["arabic"],
            Sentence.english_translation == expected_content["english"],
        )
        .update(values, synchronize_session=False)
    )
    if not updated:
        db.rollback()
        _release_content_changed_for_retry(
            db,
            sentence_id=sentence_id,
            pending_claim_ids=pending_claim_ids,
            blocked_retry_ids=blocked_retry_ids,
            result=result,
            reason="content_changed_during_mapping",
            expected_content=expected_content,
            claim_lost_phase="mapping_block",
        )
        return False
    db.commit()
    pending_claim_ids.discard(sentence_id)
    if sentence_id not in result.mapping_blocked_ids:
        result.mapping_blocked_ids.append(sentence_id)
    _record_diagnostic(
        result,
        sentence_id=sentence_id,
        disposition="blocked",
        reason=reason,
        positions=positions,
    )
    result.add_failure(reason)
    return True


def _mark_quality_rejected(
    db: Session,
    sentence_id: int,
    *,
    now: datetime,
    pending_claim_ids: set[int],
    blocked_retry_ids: set[int],
    result: CorpusEnrichmentResult,
    natural: bool,
    translation_correct: bool,
    reason: str,
    expected_content: dict[str, str | None],
) -> None:
    updated = (
        db.query(Sentence)
        .filter(
            Sentence.id == sentence_id,
            Sentence.is_active.is_(False),
            Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
            Sentence.arabic_text == expected_content["arabic"],
            Sentence.english_translation == expected_content["english"],
        )
        .update(
            {
                Sentence.is_active: False,
                Sentence.mappings_verified_at: (
                    CORPUS_QUALITY_REJECTED_SENTINEL
                ),
                Sentence.quality_reviewed_at: now,
                Sentence.quality_natural: natural,
                Sentence.quality_translation_correct: translation_correct,
                Sentence.quality_reason: reason[:500],
            },
            synchronize_session=False,
        )
    )
    if not updated:
        db.rollback()
        _release_content_changed_for_retry(
            db,
            sentence_id=sentence_id,
            pending_claim_ids=pending_claim_ids,
            blocked_retry_ids=blocked_retry_ids,
            result=result,
            reason="content_changed_during_quality_review",
            expected_content=expected_content,
            claim_lost_phase="quality_rejection",
        )
        return
    db.commit()
    pending_claim_ids.discard(sentence_id)
    result.quality_rejected_ids.append(sentence_id)
    result.add_failure("quality_rejected")


def _prepared_query(db: Session, scope: CorpusScope):
    from app.services.sentence_eligibility import not_has_unmapped_words

    query = db.query(Sentence).options(selectinload(Sentence.words)).filter(
        Sentence.is_active.is_(False),
        Sentence.mappings_verified_at.isnot(None),
        Sentence.mappings_verified_at >= MAPPING_VERIFICATION_MIN_AT,
        Sentence.mappings_verified_at != CORPUS_CLAIM_SENTINEL,
        Sentence.quality_reviewed_at.isnot(None),
        Sentence.quality_natural.is_(True),
        Sentence.quality_translation_correct.is_(True),
        Sentence.target_lemma_id.isnot(None),
        not_has_unmapped_words(),
    )
    return _scope_query(query, scope)


def _begin_sqlite_write_boundary(db: Session) -> None:
    """Serialize the short activation tranche before reloading live state."""
    if db.get_bind().dialect.name == "sqlite":
        db.execute(text("BEGIN IMMEDIATE"))


def plan_corpus_activation(
    db: Session,
    *,
    kind: str | None = None,
    sentence_ids: Sequence[int] | None = None,
    activate_limit: int = 0,
    active_ceiling: int,
    now: datetime | None = None,
) -> CorpusActivationPlan:
    """Build a deterministic greedy activation plan without writing."""
    scope = CorpusScope.build(kind=kind, sentence_ids=sentence_ids)
    if activate_limit < 0 or activate_limit > MAX_ACTIVATE_LIMIT:
        raise ValueError(
            f"corpus activation limit must be between 0 and {MAX_ACTIVATE_LIMIT}"
        )
    if active_ceiling < 0:
        raise ValueError("corpus active ceiling must be non-negative")
    active_before = db.query(func.count(Sentence.id)).filter(
        Sentence.is_active.is_(True)
    ).scalar() or 0
    capacity = max(0, active_ceiling - active_before)
    plan = CorpusActivationPlan(
        active_before=active_before,
        active_ceiling=active_ceiling,
        capacity=min(activate_limit, capacity),
    )
    rows = _prepared_query(db, scope).order_by(Sentence.id).all()
    context = _load_learning_context(db, now=now)
    demand_by_id: dict[int, CorpusDemand] = {}
    for sentence in rows:
        content_ids = _canonical_content_ids(sentence.words, context)
        demand = _demand_for_content_ids(content_ids, context)
        if demand.has_acquiring:
            plan.blocked_acquiring_ids.append(sentence.id)
            continue
        if not demand.fsrs_demand_lemma_ids:
            plan.no_fsrs_demand_ids.append(sentence.id)
            continue
        demand_by_id[sentence.id] = demand
    plan.eligible_ids = sorted(demand_by_id)

    projected_coverage = dict(context.coverage_by_id)
    remaining = set(plan.eligible_ids)
    while remaining and len(plan.selected_ids) < min(activate_limit, capacity):
        scored: list[tuple[tuple, int, CorpusDemand]] = []
        for sentence_id in remaining:
            content_ids = set(demand_by_id[sentence_id].content_lemma_ids)
            demand = _demand_for_content_ids(
                content_ids,
                context,
                projected_coverage=projected_coverage,
            )
            zero_due_gains = sum(
                projected_coverage.get(lemma_id, 0) == 0
                for lemma_id in demand.fsrs_due_lemma_ids
            )
            earliest = demand.earliest_due_at or datetime.max.replace(
                tzinfo=timezone.utc
            )
            score = (
                -zero_due_gains,
                -len(demand.shortage_lemma_ids),
                -len(demand.fsrs_due_lemma_ids),
                demand.best_tier,
                earliest,
                sentence_id,
            )
            scored.append((score, sentence_id, demand))
        scored.sort(key=lambda item: item[0])
        _, chosen_id, chosen_demand = scored[0]
        plan.selected_ids.append(chosen_id)
        remaining.remove(chosen_id)
        for lemma_id in chosen_demand.fsrs_demand_lemma_ids:
            projected_coverage[lemma_id] = projected_coverage.get(lemma_id, 0) + 1
    selected = set(plan.selected_ids)
    plan._planned_state_by_id = {
        sentence.id: (
            _activation_parent_state(sentence),
            _mapping_state_signature(sentence),
        )
        for sentence in rows
        if sentence.id in selected
    }
    return plan


def activate_prepared_corpus_sentences(
    db: Session,
    *,
    scope: CorpusScope,
    activate_limit: int,
    active_ceiling: int,
    now: datetime | None = None,
) -> CorpusActivationPlan:
    """Activate an explicit, capacity-clamped demand-aware tranche."""
    plan = plan_corpus_activation(
        db,
        kind=scope.kind,
        sentence_ids=scope.sentence_ids,
        activate_limit=activate_limit,
        active_ceiling=active_ceiling,
        now=now,
    )
    if not plan.selected_ids:
        return plan

    # The read-only planner may have populated the identity map with state that
    # changed before this write pass. End that snapshot, then acquire SQLite's
    # writer boundary before reloading the active count, rows, and learner
    # context once. This keeps the state authoritative for the whole bounded
    # tranche without paying a full-context reload under the lock per row.
    db.commit()
    db.expire_all()
    try:
        _begin_sqlite_write_boundary(db)
        live_active = int(
            db.query(func.count(Sentence.id))
            .filter(Sentence.is_active.is_(True))
            .scalar()
            or 0
        )
        plan.active_before = live_active
        plan.capacity = min(
            activate_limit,
            max(0, active_ceiling - live_active),
        )
        if plan.capacity == 0:
            plan.selected_ids = []
            db.commit()
            return plan

        rows = {
            sentence.id: sentence
            for sentence in _prepared_query(db, scope)
            .filter(Sentence.id.in_(plan.selected_ids))
            .all()
        }
        activation_raw_lemma_ids = {
            word.lemma_id
            for sentence in rows.values()
            for word in sentence.words
            if word.lemma_id is not None
        }
        context = _load_learning_context(
            db,
            now=now,
            relevant_raw_lemma_ids=activation_raw_lemma_ids,
        )
        active_sentence = aliased(Sentence)
        active_count = (
            select(func.count(active_sentence.id))
            .where(active_sentence.is_active.is_(True))
            .scalar_subquery()
        )
        activated_ids: list[int] = []
        for sentence_id in plan.selected_ids:
            if len(activated_ids) >= plan.capacity:
                break
            sentence = rows.get(sentence_id)
            if sentence is None:
                continue
            parent_snapshot = _activation_parent_state(sentence)
            mapping_snapshot = _mapping_state_signature(sentence)
            planned_snapshot = plan._planned_state_by_id.get(sentence_id)
            if planned_snapshot != (parent_snapshot, mapping_snapshot):
                planned_parent = (
                    planned_snapshot[0] if planned_snapshot is not None else {}
                )
                planned_mapping = (
                    planned_snapshot[1] if planned_snapshot is not None else None
                )
                if sentence.is_active is False and _invalidate_content_drift(
                    sentence,
                    planned_parent,
                ):
                    # Mapping verification and translation QA are independent:
                    # refreshing one cannot authenticate the other.
                    pass
                elif (
                    sentence.is_active is False
                    and sentence.mappings_verified_at
                    == planned_parent.get("mapping_stamp")
                    and mapping_snapshot != planned_mapping
                ):
                    # Preserve the external child edit, but its old verifier
                    # stamp no longer authenticates the new mapping state.
                    sentence.mappings_verified_at = None
                    sentence.is_active = False
                continue
            # Re-evaluate canonical demand from the reloaded row and learner
            # context. Planning is advisory: a word may have entered
            # acquisition or lost actionable FSRS demand since selection.
            content_ids = _canonical_content_ids(sentence.words, context)
            demand = _demand_for_content_ids(content_ids, context)
            if demand.has_acquiring:
                if sentence_id not in plan.blocked_acquiring_ids:
                    plan.blocked_acquiring_ids.append(sentence_id)
                continue
            if not demand.fsrs_demand_lemma_ids:
                if sentence_id not in plan.no_fsrs_demand_ids:
                    plan.no_fsrs_demand_ids.append(sentence_id)
                continue
            # Due demand can change between enrichment and activation. Repair
            # the primary target immediately before making the row visible.
            pseudo_mappings = [
                _StoredMapping(
                    position=word.position,
                    surface_form=word.surface_form,
                    lemma_id=word.lemma_id,
                )
                for word in sentence.words
            ]
            target_id, target_position = _target_choice(
                pseudo_mappings,
                context,
                sentence.target_lemma_id,
            )
            if target_id is None or target_position is None:
                continue
            # Claim a visibility slot with a final parent-state and ceiling
            # guard. The tranche already holds SQLite's writer boundary, and
            # later candidates in this transaction see earlier slot claims.
            slot_claim = db.execute(
                update(Sentence)
                .where(
                    Sentence.id == sentence_id,
                    Sentence.is_active.is_(False),
                    Sentence.arabic_text == parent_snapshot["arabic"],
                    Sentence.english_translation
                    == parent_snapshot["english"],
                    Sentence.source == parent_snapshot["source"],
                    Sentence.kind == parent_snapshot["kind"],
                    Sentence.target_lemma_id == parent_snapshot["target"],
                    Sentence.mappings_verified_at
                    == parent_snapshot["mapping_stamp"],
                    Sentence.quality_reviewed_at
                    == parent_snapshot["quality_stamp"],
                    Sentence.quality_natural
                    == parent_snapshot["quality_natural"],
                    Sentence.quality_translation_correct
                    == parent_snapshot["quality_translation"],
                    Sentence.quality_reason
                    == parent_snapshot["quality_reason"],
                    active_count < active_ceiling,
                )
                .values(is_active=True)
                .execution_options(synchronize_session=False)
            )
            if slot_claim.rowcount != 1:
                # If content changed without a new QA/mapping stamp, invalidate
                # the stale derived state so a later activation pass cannot
                # make the edited text visible.
                db.expire_all()
                live_sentence = (
                    db.query(Sentence)
                    .filter(Sentence.id == sentence_id)
                    .with_for_update()
                    .one_or_none()
                )
                if (
                    live_sentence is not None
                    and live_sentence.is_active is False
                ):
                    _invalidate_content_drift(
                        live_sentence,
                        parent_snapshot,
                    )
                continue

            db.expire_all()
            locked_sentence = (
                db.query(Sentence)
                .filter(Sentence.id == sentence_id)
                .with_for_update()
                .one_or_none()
            )
            locked_words = (
                db.query(SentenceWord)
                .filter(SentenceWord.sentence_id == sentence_id)
                .order_by(SentenceWord.id.asc())
                .with_for_update()
                .all()
            )
            if (
                locked_sentence is None
                or _mapping_state_signature(locked_sentence, locked_words)
                != mapping_snapshot
            ):
                db.query(Sentence).filter(
                    Sentence.id == sentence_id,
                    Sentence.is_active.is_(True),
                    Sentence.mappings_verified_at
                    == parent_snapshot["mapping_stamp"],
                ).update(
                    {
                        Sentence.is_active: False,
                        Sentence.mappings_verified_at: None,
                    },
                    synchronize_session=False,
                )
                continue
            target_word = min(
                (
                    word
                    for word in locked_words
                    if word.position == target_position
                    and word.lemma_id is not None
                    and context.canonical_id(word.lemma_id) == target_id
                ),
                key=lambda word: word.id,
                default=None,
            )
            if target_word is None:
                db.query(Sentence).filter(
                    Sentence.id == sentence_id,
                    Sentence.is_active.is_(True),
                ).update(
                    {
                        Sentence.is_active: False,
                        Sentence.mappings_verified_at: None,
                    },
                    synchronize_session=False,
                )
                continue
            for word in locked_words:
                word.is_target_word = False
            target_word.is_target_word = True
            locked_sentence.target_lemma_id = target_id
            activated_ids.append(sentence_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    plan.selected_ids = activated_ids
    return plan


@dataclass
class _StoredMapping:
    position: int
    surface_form: str
    lemma_id: int | None
    is_proper_name: bool = False


def enrich_corpus_sentences(
    db: Session,
    *,
    kind: str | None = None,
    sentence_ids: Sequence[int] | None = None,
    limit: int = DEFAULT_ENRICH_LIMIT,
    activate_limit: int = 0,
    active_ceiling: int,
    enrichment_batch_size: int = 10,
    verification_batch_size: int = 10,
    now: datetime | None = None,
    write_activity: bool = True,
    retry_blocked: bool = False,
) -> CorpusEnrichmentResult:
    """Run one exact-scope corpus phase: preparation or activation, never both."""
    from app.services.llm import review_sentences_quality
    from app.services.sentence_validator import (
        apply_corrections,
        batch_verify_sentences,
        build_comprehensive_lemma_lookup,
        detect_proper_names,
        map_tokens_to_lemmas,
        normalize_alef,
        strip_diacritics,
        strip_punctuation,
        strip_tatweel,
        tokenize_display,
    )
    from app.services.transliteration import transliterate_arabic

    scope = CorpusScope.build(kind=kind, sentence_ids=sentence_ids)
    if limit < 0 or limit > MAX_ENRICH_LIMIT:
        raise ValueError(
            f"corpus enrichment limit must be between 0 and {MAX_ENRICH_LIMIT}"
        )
    if activate_limit < 0 or activate_limit > MAX_ACTIVATE_LIMIT:
        raise ValueError(
            f"corpus activation limit must be between 0 and {MAX_ACTIVATE_LIMIT}"
        )
    if active_ceiling < 0:
        raise ValueError("corpus active ceiling must be non-negative")
    if limit > 0 and activate_limit > 0:
        raise ValueError(
            "corpus preparation and activation require separate invocations"
        )
    if retry_blocked and (
        not scope.sentence_ids or limit <= 0 or activate_limit != 0
    ):
        raise ValueError(
            "blocked-row retry requires explicit sentence IDs, nonzero "
            "preparation, and zero activation"
        )
    enrichment_batch_size = max(1, enrichment_batch_size)
    verification_batch_size = max(1, verification_batch_size)
    now = _aware(now) or datetime.now(timezone.utc)
    result = CorpusEnrichmentResult(scope=scope.detail())
    pending_claim_ids: set[int] = set()
    blocked_retry_ids: set[int] = set()

    try:
        include_legacy_claims = bool(scope.sentence_ids) and not retry_blocked
        enrichment_plan = plan_corpus_enrichment_report(
            db,
            kind=scope.kind,
            sentence_ids=scope.sentence_ids,
            limit=limit,
            include_legacy_claims=include_legacy_claims,
            include_blocked=retry_blocked,
            only_blocked=retry_blocked,
            now=now,
        )
        if include_legacy_claims:
            legacy_candidate_ids = [
                candidate.sentence_id
                for candidate in enrichment_plan.candidates
                if candidate.legacy_claim
            ]
            if legacy_candidate_ids:
                result.recovered_legacy_claim_ids = (
                    recover_scoped_legacy_claims(
                        db,
                        CorpusScope.build(
                            kind=scope.kind,
                            sentence_ids=legacy_candidate_ids,
                        ),
                        limit=limit,
                    )
                )
            recovered_claim_ids = set(result.recovered_legacy_claim_ids)
            enrichment_plan.candidates = [
                candidate
                for candidate in enrichment_plan.candidates
                if not candidate.legacy_claim
                or candidate.sentence_id in recovered_claim_ids
            ]
        if retry_blocked:
            retry_candidate_ids = [
                candidate.sentence_id
                for candidate in enrichment_plan.candidates
            ]
            if retry_candidate_ids:
                blocked_retry_ids.update(
                    retry_exact_blocked_sentences(
                        db,
                        CorpusScope.build(
                            kind=scope.kind,
                            sentence_ids=retry_candidate_ids,
                        ),
                    )
                )
            enrichment_plan.candidates = [
                candidate
                for candidate in enrichment_plan.candidates
                if candidate.sentence_id in blocked_retry_ids
            ]
        result.preflight = enrichment_plan.detail()
        result.preflight_skipped_ids = list(
            enrichment_plan.skipped_inventory_ids
        )

        claimed_ids = _claim_candidates(
            db,
            [
                candidate.sentence_id
                for candidate in enrichment_plan.candidates
            ],
            blocked_retry_ids=blocked_retry_ids,
        )
        result.recovered_blocked_ids = sorted(
            set(claimed_ids) & blocked_retry_ids
        )
        result.selected_ids = claimed_ids
        pending_claim_ids.update(claimed_ids)

        sentences = {
            sentence.id: sentence
            for sentence in db.query(Sentence)
            .options(selectinload(Sentence.words))
            .filter(Sentence.id.in_(claimed_ids))
            .all()
        }
        if claimed_ids:
            lemma_lookup = build_comprehensive_lemma_lookup(
                db,
                require_gated=True,
            )
            db.commit()
        else:
            lemma_lookup = None

        # Phase 1: slow enrichment calls, with no open write transaction.
        needs_enrichment = [
            sentences[sentence_id]
            for sentence_id in claimed_ids
            if (
                not has_arabic_diacritics(sentences[sentence_id].arabic_text)
                or not (sentences[sentence_id].english_translation or "").strip()
            )
        ]
        ready_ids = set(claimed_ids) - {sent.id for sent in needs_enrichment}
        for start in range(0, len(needs_enrichment), enrichment_batch_size):
            batch = needs_enrichment[start : start + enrichment_batch_size]
            enrichment_inputs = {
                sentence.id: {
                    "arabic": sentence.arabic_text,
                    "english": sentence.english_translation,
                    "needs_diacritics": not has_arabic_diacritics(
                        sentence.arabic_text
                    ),
                    "needs_translation": not (
                        sentence.english_translation or ""
                    ).strip(),
                }
                for sentence in batch
            }
            try:
                enriched = generate_corpus_enrichment_batch(batch)
            except Exception:
                enriched = {}
            retry_ids: list[int] = []
            content_changed_ids: list[int] = []
            for sentence in batch:
                enrichment_input = enrichment_inputs[sentence.id]
                item = enriched.get(sentence.id)
                needs_diacritics = enrichment_input["needs_diacritics"]
                needs_translation = enrichment_input["needs_translation"]
                if item is None:
                    retry_ids.append(sentence.id)
                    continue
                diacritized = item.get("diacritized", "")
                translation = item.get("translation", "")
                projected_diacritized = (
                    _project_diacritics_onto_source(
                        enrichment_input["arabic"],
                        diacritized,
                    )
                    if needs_diacritics
                    else None
                )
                invalid_diacritics = (
                    needs_diacritics
                    and (
                        projected_diacritized is None
                        or not has_arabic_diacritics(
                            projected_diacritized
                        )
                    )
                )
                invalid_translation = needs_translation and not translation
                if invalid_diacritics or invalid_translation:
                    retry_ids.append(sentence.id)
                    continue
                values = {}
                if needs_diacritics:
                    values[Sentence.arabic_text] = projected_diacritized
                    values[Sentence.transliteration] = (
                        transliterate_arabic(projected_diacritized) or ""
                    )
                if needs_translation:
                    values[Sentence.english_translation] = translation
                update_query = db.query(Sentence).filter(
                    Sentence.id == sentence.id,
                    Sentence.is_active.is_(False),
                    Sentence.mappings_verified_at
                    == CORPUS_CLAIM_SENTINEL,
                )
                # The external call ran without a DB lock. Preserve the
                # field-specific "fill missing only" contract with an exact
                # compare-and-set against each original value we replace.
                # Translation also depends on the exact Arabic sent to the
                # provider, so guard that input even when Arabic itself was
                # already vocalized. A manual/concurrent edit that deliberately
                # leaves our claim stamp in place must still win.
                if needs_diacritics or needs_translation:
                    update_query = update_query.filter(
                        Sentence.arabic_text
                        == enrichment_input["arabic"]
                    )
                if needs_translation:
                    update_query = update_query.filter(
                        Sentence.english_translation
                        == enrichment_input["english"]
                    )
                updated = update_query.update(
                    values,
                    synchronize_session=False,
                )
                if not updated:
                    content_changed_ids.append(sentence.id)
                    continue
                ready_ids.add(sentence.id)
                result.translated_ids.append(sentence.id)
            db.commit()
            for sentence_id in content_changed_ids:
                _release_content_changed_for_retry(
                    db,
                    sentence_id,
                    pending_claim_ids=pending_claim_ids,
                    blocked_retry_ids=blocked_retry_ids,
                    result=result,
                    reason="content_changed_during_enrichment",
                    expected_content=enrichment_inputs[sentence_id],
                    claim_lost_phase="phase1_enrichment",
                )
            _mark_retry(
                db,
                retry_ids,
                pending_claim_ids,
                blocked_retry_ids,
                result,
                "enrichment_unavailable_or_invalid",
            )

        # Phase 2: linguistic QA happens immediately after text enrichment and
        # before any mapping-verifier call. A completed rejection is terminal;
        # an unavailable review releases the claim for a transient retry.
        quality_rows = [
            sentences[sentence_id] for sentence_id in sorted(ready_ids)
        ]
        quality_snapshots = {
            sentence.id: {
                "arabic": sentence.arabic_text,
                "english": sentence.english_translation,
            }
            for sentence in quality_rows
        }
        quality_inputs = [
            {
                "arabic": quality_snapshots[sentence.id]["arabic"],
                "english": quality_snapshots[sentence.id]["english"] or "",
            }
            for sentence in quality_rows
        ]
        db.commit()
        if quality_rows:
            try:
                quality_reviews = review_sentences_quality(quality_inputs)
            except Exception:
                quality_reviews = []
        else:
            quality_reviews = []

        quality_pass_ids: set[int] = set()
        if quality_rows and len(quality_reviews) != len(quality_rows):
            _mark_retry(
                db,
                [sentence.id for sentence in quality_rows],
                pending_claim_ids,
                blocked_retry_ids,
                result,
                "quality_review_unavailable_or_incomplete",
                expected_content_by_id=quality_snapshots,
                content_change_reason="content_changed_during_quality_review",
            )
        else:
            incomplete_quality_ids: list[int] = []
            for sentence, review in zip(quality_rows, quality_reviews):
                if not getattr(review, "review_completed", True):
                    incomplete_quality_ids.append(sentence.id)
                    continue
                natural = bool(review.natural)
                translation_correct = bool(review.translation_correct)
                reason = str(review.reason or "")
                if not (natural and translation_correct):
                    _mark_quality_rejected(
                        db,
                        sentence.id,
                        now=now,
                        pending_claim_ids=pending_claim_ids,
                        blocked_retry_ids=blocked_retry_ids,
                        result=result,
                        natural=natural,
                        translation_correct=translation_correct,
                        reason=reason,
                        expected_content=quality_snapshots[sentence.id],
                    )
                    continue
                expected_content = quality_snapshots[sentence.id]
                updated = (
                    db.query(Sentence)
                    .filter(
                        Sentence.id == sentence.id,
                        Sentence.is_active.is_(False),
                        Sentence.mappings_verified_at
                        == CORPUS_CLAIM_SENTINEL,
                        Sentence.arabic_text == expected_content["arabic"],
                        Sentence.english_translation
                        == expected_content["english"],
                    )
                    .update(
                        {
                            Sentence.quality_reviewed_at: now,
                            Sentence.quality_natural: True,
                            Sentence.quality_translation_correct: True,
                            Sentence.quality_reason: reason[:500],
                            Sentence.is_active: False,
                        },
                        synchronize_session=False,
                    )
                )
                if not updated:
                    db.rollback()
                    _release_content_changed_for_retry(
                        db,
                        sentence.id,
                        pending_claim_ids=pending_claim_ids,
                        blocked_retry_ids=blocked_retry_ids,
                        result=result,
                        reason="content_changed_during_quality_review",
                        expected_content=expected_content,
                        claim_lost_phase="quality_pass",
                    )
                    continue
                db.commit()
                quality_pass_ids.add(sentence.id)
            db.commit()
            _mark_retry(
                db,
                incomplete_quality_ids,
                pending_claim_ids,
                blocked_retry_ids,
                result,
                "quality_review_unavailable_or_incomplete",
                expected_content_by_id=quality_snapshots,
                content_change_reason="content_changed_during_quality_review",
            )

        # Phase 3: rebuild prospective mappings from the enriched text. This
        # repeats the cheap preflight intentionally; text may have just gained
        # hamza/diacritic information. No vocabulary rows are created.
        first_pass_mappings: dict[int, list] = {}
        unmapped_frequency: dict[str, int] = {}
        for sentence_id in sorted(quality_pass_ids):
            expected_content = quality_snapshots[sentence_id]
            mappings = map_tokens_to_lemmas(
                tokens=tokenize_display(expected_content["arabic"]),
                lemma_lookup=lemma_lookup,
                target_lemma_id=0,
                target_bare="",
                proper_names=set(),
            )
            first_pass_mappings[sentence_id] = mappings
            for mapping in mappings:
                if mapping.lemma_id not in (None, 0):
                    continue
                bare = normalize_alef(
                    strip_diacritics(
                        strip_punctuation(
                            strip_tatweel(mapping.surface_form)
                        )
                    )
                )
                if bare and len(bare) > 1:
                    unmapped_frequency[bare] = (
                        unmapped_frequency.get(bare, 0) + 1
                    )
        proper_names = (
            detect_proper_names(
                unmapped_frequency,
                lemma_lookup,
                min_frequency=2,
            )
            if lemma_lookup is not None
            else set()
        )

        verification_candidates: list[dict] = []
        mapping_lemma_ids: set[int] = set()
        context = _load_learning_context(db, now=now)
        for sentence_id in sorted(quality_pass_ids):
            sentence = sentences[sentence_id]
            expected_content = quality_snapshots[sentence_id]
            expected_mapping_state = _mapping_state_signature(sentence)
            mappings = (
                map_tokens_to_lemmas(
                    tokens=tokenize_display(expected_content["arabic"]),
                    lemma_lookup=lemma_lookup,
                    target_lemma_id=0,
                    target_bare="",
                    proper_names=proper_names,
                )
                if proper_names
                else first_pass_mappings[sentence_id]
            )
            complete, incomplete_reason = _mapping_is_complete(
                mappings,
                context,
            )
            if not complete:
                reason = incomplete_reason or "incomplete_mapping"
                if reason == "unmapped_token":
                    positions = [
                        mapping.position
                        for mapping in mappings
                        if mapping.lemma_id in (None, 0)
                        and not getattr(mapping, "is_proper_name", False)
                    ]
                elif reason == "unresolved_proper_name":
                    positions = [
                        mapping.position
                        for mapping in mappings
                        if mapping.lemma_id in (None, 0)
                        and getattr(mapping, "is_proper_name", False)
                    ]
                elif reason == "missing_lemma":
                    positions = [
                        mapping.position
                        for mapping in mappings
                        if mapping.lemma_id not in (None, 0)
                        and mapping.lemma_id not in context.lemmas
                    ]
                else:
                    positions = [
                        mapping.position
                        for mapping in mappings
                        if mapping.lemma_id not in (None, 0)
                        and mapping.lemma_id in context.lemmas
                        and context.is_content(mapping.lemma_id)
                        and not (
                            context.lemmas[mapping.lemma_id].gloss_en or ""
                        ).strip()
                    ]
                _mark_mapping_blocked(
                    db,
                    sentence_id,
                    pending_claim_ids=pending_claim_ids,
                    blocked_retry_ids=blocked_retry_ids,
                    result=result,
                    reason=reason,
                    expected_content=expected_content,
                    expected_mapping_state=expected_mapping_state,
                    positions=_position_diagnostics(mappings, positions),
                )
                continue
            for mapping in mappings:
                if mapping.lemma_id:
                    mapping_lemma_ids.add(mapping.lemma_id)
                mapping_lemma_ids.update(mapping.alternative_lemma_ids or [])
            verification_candidates.append(
                {
                    "sentence": sentence,
                    "arabic": expected_content["arabic"],
                    "english": expected_content["english"] or "",
                    "expected_content": expected_content,
                    "mapping_state_signature": expected_mapping_state,
                    "mappings": mappings,
                    "has_ambiguous": any(
                        mapping.alternative_lemma_ids for mapping in mappings
                    ),
                }
            )

        lemma_map = {
            lemma.lemma_id: lemma
            for lemma in db.query(Lemma)
            .filter(Lemma.lemma_id.in_(mapping_lemma_ids))
            .all()
        } if mapping_lemma_ids else {}
        db.commit()

        verified_results: dict[int, tuple[dict, list]] = {}
        for start in range(
            0,
            len(verification_candidates),
            verification_batch_size,
        ):
            batch = verification_candidates[
                start : start + verification_batch_size
            ]
            try:
                batch_results = batch_verify_sentences(
                    batch,
                    lemma_map,
                    return_invalid_rows=True,
                )
            except Exception:
                batch_results = None
            batch_ids = [candidate["sentence"].id for candidate in batch]
            batch_content = {
                candidate["sentence"].id: candidate["expected_content"]
                for candidate in batch
            }
            if batch_results is None or len(batch_results) != len(batch):
                _mark_retry(
                    db,
                    batch_ids,
                    pending_claim_ids,
                    blocked_retry_ids,
                    result,
                    "mapping_verification_unavailable_or_incomplete",
                    expected_content_by_id=batch_content,
                    content_change_reason=(
                        "content_changed_during_mapping_verification"
                    ),
                )
                continue

            for candidate, verification in zip(batch, batch_results):
                sentence = candidate["sentence"]
                mappings = candidate["mappings"]
                invalid_reason = verification.get("invalid_reason")
                invalid_positions = verification.get("invalid_positions", [])
                if invalid_reason:
                    _mark_retry(
                        db,
                        [sentence.id],
                        pending_claim_ids,
                        blocked_retry_ids,
                        result,
                        f"mapping_verifier_{invalid_reason}",
                        expected_content_by_id={
                            sentence.id: candidate["expected_content"]
                        },
                        content_change_reason=(
                            "content_changed_during_mapping_verification"
                        ),
                    )
                    _record_diagnostic(
                        result,
                        sentence_id=sentence.id,
                        disposition="retry",
                        reason=f"mapping_verifier_{invalid_reason}",
                        positions=_position_diagnostics(
                            mappings,
                            invalid_positions,
                        ),
                    )
                    continue

                disambiguation = verification.get("disambiguation", [])
                issues = verification.get("issues", [])
                contradictory_positions = {
                    choice.get("position")
                    for choice in disambiguation
                    if isinstance(choice, dict)
                } & {
                    issue.get("position")
                    for issue in issues
                    if isinstance(issue, dict)
                }
                contradictory_positions.discard(None)
                if contradictory_positions:
                    _mark_retry(
                        db,
                        [sentence.id],
                        pending_claim_ids,
                        blocked_retry_ids,
                        result,
                        "mapping_verifier_contradictory_verdict",
                        expected_content_by_id={
                            sentence.id: candidate["expected_content"]
                        },
                        content_change_reason=(
                            "content_changed_during_mapping_verification"
                        ),
                    )
                    _record_diagnostic(
                        result,
                        sentence_id=sentence.id,
                        disposition="retry",
                        reason="mapping_verifier_contradictory_verdict",
                        positions=_position_diagnostics(
                            mappings,
                            contradictory_positions,
                            issues,
                        ),
                    )
                    continue

                by_position = {mapping.position: mapping for mapping in mappings}
                for choice in disambiguation:
                    mapping = by_position.get(choice.get("position"))
                    chosen_id = choice.get("lemma_id")
                    if mapping is None or not chosen_id:
                        continue
                    valid_ids = {
                        mapping.lemma_id,
                        *(mapping.alternative_lemma_ids or []),
                    }
                    if chosen_id in valid_ids:
                        mapping.lemma_id = chosen_id

                failed_positions = apply_corrections(
                    issues,
                    mappings,
                    db,
                    lemma_lookup=lemma_lookup,
                    arabic_text=candidate["expected_content"]["arabic"],
                    require_gated_lemmas=True,
                )
                if failed_positions:
                    _mark_mapping_blocked(
                        db,
                        sentence.id,
                        pending_claim_ids=pending_claim_ids,
                        blocked_retry_ids=blocked_retry_ids,
                        result=result,
                        reason="mapping_correction_failed",
                        expected_content=candidate["expected_content"],
                        expected_mapping_state=(
                            candidate["mapping_state_signature"]
                        ),
                        positions=_position_diagnostics(
                            mappings,
                            failed_positions,
                            issues,
                        ),
                    )
                    continue
                verified_results[sentence.id] = (candidate, mappings)
            # Close the read/correction transaction before the next external
            # verifier call. The correction path never creates lemmas.
            db.commit()

        # Phase 4: target choice and final mapping writes. QA is already a
        # completed pass and is preserved for any mapping/inventory blocker.
        context = _load_learning_context(db, now=now)
        for sentence_id in sorted(verified_results):
            candidate, mappings = verified_results[sentence_id]
            sentence = candidate["sentence"]
            expected_content = candidate["expected_content"]
            complete, incomplete_reason = _mapping_is_complete(mappings, context)
            if not complete:
                _mark_mapping_blocked(
                    db,
                    sentence_id,
                    pending_claim_ids=pending_claim_ids,
                    blocked_retry_ids=blocked_retry_ids,
                    result=result,
                    reason=incomplete_reason or "incomplete_mapping",
                    expected_content=expected_content,
                    expected_mapping_state=(
                        candidate["mapping_state_signature"]
                    ),
                )
                continue
            target_id, target_position = _target_choice(
                mappings,
                context,
                candidate["mapping_state_signature"][0],
            )
            if target_id is None or target_position is None:
                target_reason = _no_target_reason(mappings, context)
                if target_reason == "target_content_suspended":
                    _mark_retry(
                        db,
                        [sentence_id],
                        pending_claim_ids,
                        blocked_retry_ids,
                        result,
                        target_reason,
                        expected_content_by_id={
                            sentence_id: expected_content
                        },
                        content_change_reason=(
                            "content_changed_during_mapping_verification"
                        ),
                    )
                    _record_diagnostic(
                        result,
                        sentence_id=sentence_id,
                        disposition="retry",
                        reason=target_reason,
                    )
                    continue
                target_blocked = _mark_mapping_blocked(
                    db,
                    sentence_id,
                    pending_claim_ids=pending_claim_ids,
                    blocked_retry_ids=blocked_retry_ids,
                    result=result,
                    reason="no_valid_content_target",
                    expected_content=expected_content,
                    expected_mapping_state=(
                        candidate["mapping_state_signature"]
                    ),
                )
                if target_blocked:
                    result.target_rejected_ids.append(sentence_id)
                continue
            # Consume the transient claim in the same transaction as the word
            # replacement. If a non-flock manual mutator changed the row during
            # the external calls, the compare-and-set fails and this pipeline
            # leaves both its new disposition and its mappings untouched.
            claimed_for_write = (
                db.query(Sentence)
                .filter(
                    Sentence.id == sentence_id,
                    Sentence.is_active.is_(False),
                    Sentence.mappings_verified_at
                    == CORPUS_CLAIM_SENTINEL,
                    Sentence.arabic_text == expected_content["arabic"],
                    Sentence.english_translation
                    == expected_content["english"],
                )
                .update(
                    {Sentence.mappings_verified_at: now},
                    synchronize_session="fetch",
                )
            )
            if not claimed_for_write:
                db.rollback()
                _release_content_changed_for_retry(
                    db,
                    sentence_id,
                    pending_claim_ids=pending_claim_ids,
                    blocked_retry_ids=blocked_retry_ids,
                    result=result,
                    reason="content_changed_during_mapping_verification",
                    expected_content=expected_content,
                    claim_lost_phase="final_mapping_write",
                )
                continue

            # The parent CAS above is the write boundary. Reload and lock the
            # child mappings, then reject any non-flock mapping edit made while
            # contextual verification was in flight.
            db.expire_all()
            locked_sentence = (
                db.query(Sentence)
                .filter(Sentence.id == sentence_id)
                .with_for_update()
                .one_or_none()
            )
            locked_words = (
                db.query(SentenceWord)
                .filter(SentenceWord.sentence_id == sentence_id)
                .order_by(SentenceWord.id.asc())
                .with_for_update()
                .all()
            )
            if (
                locked_sentence is None
                or _mapping_state_signature(locked_sentence, locked_words)
                != candidate["mapping_state_signature"]
            ):
                db.rollback()
                _mark_retry(
                    db,
                    [sentence_id],
                    pending_claim_ids,
                    blocked_retry_ids,
                    result,
                    "mapping_state_changed_during_verification",
                    expected_content_by_id={
                        sentence_id: expected_content
                    },
                    content_change_reason=(
                        "content_changed_during_mapping_verification"
                    ),
                )
                continue
            if not _write_final_mappings(
                db,
                locked_sentence,
                mappings,
                target_lemma_id=target_id,
                target_position=target_position,
                context=context,
            ):
                db.rollback()
                target_blocked = _mark_mapping_blocked(
                    db,
                    sentence_id,
                    pending_claim_ids=pending_claim_ids,
                    blocked_retry_ids=blocked_retry_ids,
                    result=result,
                    reason="target_write_failed",
                    expected_content=expected_content,
                    expected_mapping_state=(
                        candidate["mapping_state_signature"]
                    ),
                )
                if target_blocked:
                    result.target_rejected_ids.append(sentence_id)
                continue

            locked_sentence.is_active = False
            db.commit()
            pending_claim_ids.discard(sentence_id)
            result.prepared_ids.append(sentence_id)

        if activate_limit:
            activation = activate_prepared_corpus_sentences(
                db,
                scope=scope,
                activate_limit=activate_limit,
                active_ceiling=active_ceiling,
                now=now,
            )
            result.activated_ids = activation.selected_ids
            result.activation_blocked_acquiring_ids = (
                activation.blocked_acquiring_ids
            )
            result.activation_no_demand_ids = activation.no_fsrs_demand_ids
            result.active_before = activation.active_before
            result.active_ceiling = activation.active_ceiling
            result.activation_capacity = activation.capacity
        else:
            # Preparation can never activate a row. Keep the useful pool-size
            # telemetry with one COUNT, without loading every prepared row or
            # rebuilding the full learning context in the activation planner.
            result.active_before = int(
                db.query(func.count(Sentence.id))
                .filter(Sentence.is_active.is_(True))
                .scalar()
                or 0
            )
            result.active_ceiling = active_ceiling
            result.activation_capacity = 0

    except Exception as exc:
        released = _release_claims(
            db,
            pending_claim_ids,
            blocked_retry_ids,
        )
        result.retry_ids.extend(
            sentence_id
            for sentence_id in released
            if sentence_id not in result.retry_ids
        )
        result.add_failure(f"unexpected:{type(exc).__name__}")
        if write_activity:
            log_activity(
                db,
                event_type="corpus_enrichment_failed",
                summary=(
                    "Scoped corpus enrichment failed; "
                    f"released {len(released)} claims"
                ),
                detail=result.detail(),
            )
        raise
    finally:
        released = _release_claims(
            db,
            pending_claim_ids,
            blocked_retry_ids,
        )
        result.retry_ids.extend(
            sentence_id
            for sentence_id in released
            if sentence_id not in result.retry_ids
        )

    if write_activity:
        log_activity(
            db,
            event_type="corpus_enrichment_scoped",
            summary=(
                f"Prepared {result.prepared}, activated {result.activated}, "
                f"retry {len(result.retry_ids)}, blocked "
                f"{len(result.mapping_blocked_ids)}, rejected "
                f"{len(result.quality_rejected_ids)}"
            ),
            detail=result.detail(),
        )
    return result
