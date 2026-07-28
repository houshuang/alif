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
``finally``-equivalent failure path, and legacy claims are recovered only
inside an exact caller-supplied kind/ID scope.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from math import ceil
from typing import Iterable, Sequence

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, aliased, selectinload

from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.canonical_resolution import resolve_canonical_via_map
from app.services.pipeline_tiers import WordTier, compute_word_tiers
from app.services.proper_name_lemmas import get_or_create_proper_name_lemma
from app.services.sentence_eligibility import (
    MAPPING_VERIFICATION_MIN_AT,
    reviewable_sentence_clauses,
)
from app.services.sentence_validator import is_function_word_lemma


CORPUS_CLAIM_SENTINEL = datetime(2000, 1, 1)
DEFAULT_ENRICH_LIMIT = 20
MAX_ENRICH_LIMIT = 50
MAX_ACTIVATE_LIMIT = 20
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

    def detail(self) -> dict:
        return {
            "sentence_id": self.sentence_id,
            "kind": self.kind,
            "legacy_claim": self.legacy_claim,
            **self.demand.detail(),
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

    def detail(self) -> dict:
        return asdict(self)


@dataclass
class CorpusEnrichmentResult:
    scope: dict
    selected_ids: list[int] = field(default_factory=list)
    recovered_legacy_claim_ids: list[int] = field(default_factory=list)
    translated_ids: list[int] = field(default_factory=list)
    prepared_ids: list[int] = field(default_factory=list)
    activated_ids: list[int] = field(default_factory=list)
    retry_ids: list[int] = field(default_factory=list)
    mapping_rejected_ids: list[int] = field(default_factory=list)
    quality_rejected_ids: list[int] = field(default_factory=list)
    target_rejected_ids: list[int] = field(default_factory=list)
    activation_blocked_acquiring_ids: list[int] = field(default_factory=list)
    activation_no_demand_ids: list[int] = field(default_factory=list)
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


def _load_learning_context(
    db: Session,
    *,
    now: datetime | None = None,
) -> _LearningContext:
    now = _aware(now) or datetime.now(timezone.utc)
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
        for lemma_id in lemmas
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
    for target_id, count in (
        db.query(Sentence.target_lemma_id, func.count(Sentence.id))
        .filter(
            Sentence.target_lemma_id.isnot(None),
            reviewable_sentence_clauses(),
        )
        .group_by(Sentence.target_lemma_id)
        .all()
    ):
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
        candidate.sentence_id,
    )


def plan_corpus_enrichment(
    db: Session,
    *,
    kind: str | None = None,
    sentence_ids: Sequence[int] | None = None,
    limit: int = DEFAULT_ENRICH_LIMIT,
    include_legacy_claims: bool = True,
    now: datetime | None = None,
) -> list[CorpusCandidate]:
    """Return a deterministic, read-only enrichment plan."""
    scope = CorpusScope.build(kind=kind, sentence_ids=sentence_ids)
    if limit < 0 or limit > MAX_ENRICH_LIMIT:
        raise ValueError(
            f"corpus enrichment limit must be between 0 and {MAX_ENRICH_LIMIT}"
        )
    if limit == 0:
        return []

    verification_clause = Sentence.mappings_verified_at.is_(None)
    if include_legacy_claims:
        verification_clause = or_(
            verification_clause,
            Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
        )
    query = (
        db.query(Sentence)
        .options(selectinload(Sentence.words))
        .filter(
            Sentence.is_active.is_(False),
            verification_clause,
        )
    )
    rows = _scope_query(query, scope).order_by(Sentence.id).all()
    context = _load_learning_context(db, now=now)

    candidates: list[CorpusCandidate] = []
    for sentence in rows:
        content_ids = _canonical_content_ids(sentence.words, context)
        demand = _demand_for_content_ids(content_ids, context)
        if not demand.introduced_lemma_ids:
            continue
        candidates.append(
            CorpusCandidate(
                sentence_id=sentence.id,
                kind=sentence.kind,
                legacy_claim=sentence.mappings_verified_at
                == CORPUS_CLAIM_SENTINEL,
                demand=demand,
            )
        )
    candidates.sort(key=_candidate_sort_key)
    return candidates[:limit]


def recover_scoped_legacy_claims(
    db: Session,
    scope: CorpusScope,
) -> list[int]:
    """Reset historical claim sentinels only inside the exact requested scope."""
    query = db.query(Sentence.id).filter(
        Sentence.is_active.is_(False),
        Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
    )
    query = _scope_query(query, scope)
    recovered_ids = [row[0] for row in query.order_by(Sentence.id).all()]
    if not recovered_ids:
        return []
    db.query(Sentence).filter(
        Sentence.id.in_(recovered_ids),
        Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
    ).update(
        {Sentence.mappings_verified_at: None},
        synchronize_session=False,
    )
    db.commit()
    return recovered_ids


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
    lines = [f"- id={sent.id}: {sent.arabic_text}" for sent in sentences]
    prompt = (
        "For each Arabic sentence below:\n"
        "1. Add full tashkeel (diacritics/vowelization) to the Arabic text. "
        "Keep the exact same words and letters; only add harakat.\n"
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
            model_override="claude_haiku",
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
    from app.services.sentence_validator import strip_diacritics, strip_tatweel

    return strip_diacritics(strip_tatweel(original)).strip() == strip_diacritics(
        strip_tatweel(diacritized)
    ).strip()


def _claim_candidates(
    db: Session,
    candidate_ids: Sequence[int],
) -> list[int]:
    if not candidate_ids:
        return []
    claimed_ids: list[int] = []
    for sentence_id in candidate_ids:
        updated = (
            db.query(Sentence)
            .filter(
                Sentence.id == sentence_id,
                Sentence.is_active.is_(False),
                Sentence.mappings_verified_at.is_(None),
            )
            .update(
                {Sentence.mappings_verified_at: CORPUS_CLAIM_SENTINEL},
                synchronize_session=False,
            )
        )
        if updated:
            claimed_ids.append(sentence_id)
    db.commit()
    return claimed_ids


def _release_claims(
    db: Session,
    pending_claim_ids: set[int],
) -> list[int]:
    """Release only claims still carrying this pipeline's sentinel."""
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
    if releasable:
        db.query(Sentence).filter(
            Sentence.id.in_(releasable),
            Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
        ).update(
            {Sentence.mappings_verified_at: None},
            synchronize_session=False,
        )
        db.commit()
    pending_claim_ids.difference_update(releasable)
    return releasable


def _mark_retry(
    db: Session,
    sentence_ids: Iterable[int],
    pending_claim_ids: set[int],
    result: CorpusEnrichmentResult,
    reason: str,
) -> None:
    ids = sorted(set(sentence_ids))
    if not ids:
        return
    db.query(Sentence).filter(
        Sentence.id.in_(ids),
        Sentence.mappings_verified_at == CORPUS_CLAIM_SENTINEL,
    ).update(
        {
            Sentence.mappings_verified_at: None,
            Sentence.is_active: False,
        },
        synchronize_session=False,
    )
    db.commit()
    pending_claim_ids.difference_update(ids)
    result.retry_ids.extend(sid for sid in ids if sid not in result.retry_ids)
    result.add_failure(reason, len(ids))


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


def _mapping_is_complete(
    mappings: list,
    context: _LearningContext,
) -> tuple[bool, str | None]:
    for mapping in mappings:
        lemma_id = mapping.lemma_id if mapping.lemma_id not in (None, 0) else None
        if lemma_id is None:
            if getattr(mapping, "is_proper_name", False):
                continue
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
        if lemma_id is None and getattr(mapping, "is_proper_name", False):
            lemma_id = get_or_create_proper_name_lemma(
                db,
                mapping.surface_form,
                source="corpus",
            )
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


def _terminal_mapping_reject(
    db: Session,
    sentence_id: int,
    *,
    now: datetime,
    pending_claim_ids: set[int],
    result: CorpusEnrichmentResult,
    reason: str,
) -> None:
    sentence = db.get(Sentence, sentence_id)
    if sentence is None:
        pending_claim_ids.discard(sentence_id)
        return
    sentence.is_active = False
    sentence.mappings_verified_at = now
    # A terminal mapping/target rejection must also stay fail-closed if an
    # unrelated maintenance path later toggles is_active. These fields act as
    # the durable authentic-row eligibility verdict; the reason preserves the
    # actual (non-linguistic) failure cause.
    sentence.quality_reviewed_at = now
    sentence.quality_natural = False
    sentence.quality_translation_correct = False
    sentence.quality_reason = f"corpus enrichment rejected: {reason}"[:500]
    db.commit()
    pending_claim_ids.discard(sentence_id)
    result.mapping_rejected_ids.append(sentence_id)
    result.add_failure(reason)


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

    # The read-only planner may have populated the identity map with learner
    # state that changed before this write pass. Expire it so both the
    # prepared rows and the activation context are reloaded from the database.
    db.expire_all()
    rows = {
        sentence.id: sentence
        for sentence in _prepared_query(db, scope)
        .filter(Sentence.id.in_(plan.selected_ids))
        .all()
    }
    context = _load_learning_context(db, now=now)
    activated_ids: list[int] = []
    try:
        for sentence_id in plan.selected_ids:
            sentence = rows.get(sentence_id)
            if sentence is None:
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
            for word in sentence.words:
                word.is_target_word = False
            target_word = min(
                (
                    word
                    for word in sentence.words
                    if word.position == target_position
                    and word.lemma_id is not None
                    and context.canonical_id(word.lemma_id) == target_id
                ),
                key=lambda word: word.id,
                default=None,
            )
            if target_word is None:
                continue
            target_word.is_target_word = True
            sentence.target_lemma_id = target_id
            sentence.is_active = True
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
    if limit > 0 and activate_limit > 0:
        raise ValueError(
            "corpus preparation and activation require separate invocations"
        )
    enrichment_batch_size = max(1, enrichment_batch_size)
    verification_batch_size = max(1, verification_batch_size)
    now = _aware(now) or datetime.now(timezone.utc)
    result = CorpusEnrichmentResult(scope=scope.detail())
    pending_claim_ids: set[int] = set()

    try:
        # Activation-only runs must not mutate the preparation backlog. Legacy
        # claim recovery belongs to a nonzero preparation tranche, not merely
        # to sharing the same scoped entry point.
        if limit > 0:
            result.recovered_legacy_claim_ids = recover_scoped_legacy_claims(
                db,
                scope,
            )
        candidates = plan_corpus_enrichment(
            db,
            kind=scope.kind,
            sentence_ids=scope.sentence_ids,
            limit=limit,
            include_legacy_claims=False,
            now=now,
        )
        claimed_ids = _claim_candidates(
            db,
            [candidate.sentence_id for candidate in candidates],
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
            lemma_lookup = build_comprehensive_lemma_lookup(db)
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
            try:
                enriched = generate_corpus_enrichment_batch(batch)
            except Exception:
                enriched = {}
            retry_ids: list[int] = []
            for sentence in batch:
                item = enriched.get(sentence.id)
                needs_diacritics = not has_arabic_diacritics(sentence.arabic_text)
                needs_translation = not (
                    sentence.english_translation or ""
                ).strip()
                if item is None:
                    retry_ids.append(sentence.id)
                    continue
                diacritized = item.get("diacritized", "")
                translation = item.get("translation", "")
                if (
                    (
                        needs_diacritics
                        and not has_arabic_diacritics(diacritized)
                    )
                    or (needs_translation and not translation)
                    or (diacritized and not _same_letters(sentence.arabic_text, diacritized))
                ):
                    retry_ids.append(sentence.id)
                    continue
                if diacritized:
                    sentence.arabic_text = diacritized
                    sentence.transliteration = transliterate_arabic(diacritized) or ""
                if translation:
                    sentence.english_translation = translation
                ready_ids.add(sentence.id)
                result.translated_ids.append(sentence.id)
            db.commit()
            _mark_retry(
                db,
                retry_ids,
                pending_claim_ids,
                result,
                "enrichment_unavailable_or_invalid",
            )

        # Build final token mappings after diacritization.  All data needed by
        # the verifier is primitive/in-memory; commit before every slow call.
        unmapped_frequency: dict[str, int] = {}
        for sentence_id in ready_ids:
            sentence = sentences[sentence_id]
            for word in sentence.words:
                if word.lemma_id is not None:
                    continue
                bare = normalize_alef(
                    strip_diacritics(
                        strip_punctuation(strip_tatweel(word.surface_form))
                    )
                )
                if bare and len(bare) > 1:
                    unmapped_frequency[bare] = unmapped_frequency.get(bare, 0) + 1
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
        for sentence_id in sorted(ready_ids):
            sentence = sentences[sentence_id]
            mappings = map_tokens_to_lemmas(
                tokens=tokenize_display(sentence.arabic_text),
                lemma_lookup=lemma_lookup,
                target_lemma_id=0,
                target_bare="",
                proper_names=proper_names,
            )
            for mapping in mappings:
                if mapping.lemma_id:
                    mapping_lemma_ids.add(mapping.lemma_id)
                mapping_lemma_ids.update(mapping.alternative_lemma_ids or [])
            verification_candidates.append(
                {
                    "sentence": sentence,
                    "arabic": sentence.arabic_text,
                    "english": sentence.english_translation or "",
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

        verified_results: dict[int, tuple[Sentence, list]] = {}
        for start in range(
            0,
            len(verification_candidates),
            verification_batch_size,
        ):
            batch = verification_candidates[
                start : start + verification_batch_size
            ]
            try:
                batch_results = batch_verify_sentences(batch, lemma_map)
            except Exception:
                batch_results = None
            batch_ids = [candidate["sentence"].id for candidate in batch]
            if batch_results is None or len(batch_results) != len(batch):
                _mark_retry(
                    db,
                    batch_ids,
                    pending_claim_ids,
                    result,
                    "mapping_verification_unavailable_or_incomplete",
                )
                continue

            for candidate, verification in zip(batch, batch_results):
                sentence = candidate["sentence"]
                mappings = candidate["mappings"]
                by_position = {mapping.position: mapping for mapping in mappings}
                for choice in verification.get("disambiguation", []):
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
                    verification.get("issues", []),
                    mappings,
                    db,
                    lemma_lookup=lemma_lookup,
                    arabic_text=sentence.arabic_text,
                )
                if failed_positions:
                    _terminal_mapping_reject(
                        db,
                        sentence.id,
                        now=now,
                        pending_claim_ids=pending_claim_ids,
                        result=result,
                        reason="mapping_correction_failed",
                    )
                    continue
                verified_results[sentence.id] = (sentence, mappings)
            # Correction application may create or update lemmas. Close that
            # short write transaction before the next external verifier call.
            db.commit()

        # Phase 2: determine target candidates from the final mappings, then
        # run quality review while the ORM session is clean.
        context = _load_learning_context(db, now=now)
        quality_ready: list[tuple[Sentence, list, int, int]] = []
        for sentence_id in sorted(verified_results):
            sentence, mappings = verified_results[sentence_id]
            complete, incomplete_reason = _mapping_is_complete(mappings, context)
            if not complete:
                _terminal_mapping_reject(
                    db,
                    sentence_id,
                    now=now,
                    pending_claim_ids=pending_claim_ids,
                    result=result,
                    reason=incomplete_reason or "incomplete_mapping",
                )
                continue
            target_id, target_position = _target_choice(
                mappings,
                context,
                sentence.target_lemma_id,
            )
            if target_id is None or target_position is None:
                _terminal_mapping_reject(
                    db,
                    sentence_id,
                    now=now,
                    pending_claim_ids=pending_claim_ids,
                    result=result,
                    reason="no_valid_content_target",
                )
                result.target_rejected_ids.append(sentence_id)
                continue
            quality_ready.append(
                (sentence, mappings, target_id, target_position)
            )
        db.commit()

        quality_inputs = [
            {
                "arabic": sentence.arabic_text,
                "english": sentence.english_translation or "",
            }
            for sentence, _, _, _ in quality_ready
        ]
        try:
            quality_reviews = review_sentences_quality(quality_inputs)
        except Exception:
            quality_reviews = []
        if len(quality_reviews) != len(quality_ready):
            quality_reviews = []

        if not quality_reviews and quality_ready:
            _mark_retry(
                db,
                [sentence.id for sentence, _, _, _ in quality_ready],
                pending_claim_ids,
                result,
                "quality_review_unavailable_or_incomplete",
            )
        else:
            # Phase 3: short final writes only; no external calls below.
            for (
                sentence,
                mappings,
                target_id,
                target_position,
            ), review in zip(quality_ready, quality_reviews):
                if not getattr(review, "review_completed", True):
                    _mark_retry(
                        db,
                        [sentence.id],
                        pending_claim_ids,
                        result,
                        "quality_review_unavailable_or_incomplete",
                    )
                    continue
                if not _write_final_mappings(
                    db,
                    sentence,
                    mappings,
                    target_lemma_id=target_id,
                    target_position=target_position,
                    context=context,
                ):
                    db.rollback()
                    _terminal_mapping_reject(
                        db,
                        sentence.id,
                        now=now,
                        pending_claim_ids=pending_claim_ids,
                        result=result,
                        reason="target_write_failed",
                    )
                    result.target_rejected_ids.append(sentence.id)
                    continue

                sentence.mappings_verified_at = now
                sentence.quality_reviewed_at = now
                sentence.quality_natural = bool(review.natural)
                sentence.quality_translation_correct = bool(
                    review.translation_correct
                )
                sentence.quality_reason = (review.reason or "")[:500]
                sentence.is_active = False
                db.commit()
                pending_claim_ids.discard(sentence.id)
                if review.natural and review.translation_correct:
                    result.prepared_ids.append(sentence.id)
                else:
                    result.quality_rejected_ids.append(sentence.id)
                    result.add_failure("quality_rejected")

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

    except Exception as exc:
        released = _release_claims(db, pending_claim_ids)
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
        released = _release_claims(db, pending_claim_ids)
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
                f"retry {len(result.retry_ids)}, rejected "
                f"{len(result.mapping_rejected_ids) + len(result.quality_rejected_ids)}"
            ),
            detail=result.detail(),
        )
    return result
