"""Exact-surface retrieval pilot for morphology-related yellow marks.

The canonical lemma remains the scheduling unit. This experiment only chooses
which already-due sentence represents that lemma: treatment episodes prefer the
same non-trivial surface form in a different sentence and make it the primary
retrieval target. It never creates a card, changes a due date, or changes the
Hard rating supplied by the learner.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, aliased

from app.models import Lemma, ReviewLog, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.canonical_resolution import resolve_canonical_via_map
from app.services.confusion_service import (
    classify_surface_morphology,
    normalize_surface_form,
)
from app.services.fsrs_service import parse_json_column
from app.services.interaction_logger import log_interaction
from app.services.sentence_eligibility import reviewable_sentence_clauses


EXACT_SURFACE_EXPERIMENT_KEY = "__exact_surface_v1"
EXACT_SURFACE_EXPERIMENT_VERSION = "exact_surface_v1"
EXACT_SURFACE_EXPIRES_DAYS = 14
EXACT_SURFACE_MAX_SLOTS_PER_SESSION = 1

_TREATMENT = "treatment"
_CONTROL = "control"
_PURE_PREFIXES = ("وال", "بال", "فال", "كال", "لل", "ال", "و", "ف", "ب", "ل", "ك")
_ELIGIBLE_CATEGORIES = {
    "verb_present",
    "verb_other",
    "derived_form",
    "enclitic",
    "inflection",
}


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def eligible_surface_morphology(surface: str, lemma: Lemma | None) -> dict | None:
    """Return non-trivial morphology metadata or None for citation/clitic noise."""
    if lemma is None:
        return None
    key = normalize_surface_form(surface)
    lemma_key = normalize_surface_form(lemma.lemma_ar_bare or lemma.lemma_ar or "")
    if not key or not lemma_key or key == lemma_key:
        return None
    if key in {prefix + lemma_key for prefix in _PURE_PREFIXES}:
        return None
    morphology = classify_surface_morphology(key, lemma)
    if not morphology or morphology.get("category") not in _ELIGIBLE_CATEGORIES:
        return None
    return morphology


def deterministic_arm(
    review_identity: str,
    lemma_id: int,
    surface_key: str,
) -> str:
    """Stable 50/50 arm assignment, including across offline retries."""
    payload = (
        f"{EXACT_SURFACE_EXPERIMENT_VERSION}:{review_identity}:"
        f"{lemma_id}:{surface_key}"
    ).encode("utf-8")
    return _TREATMENT if hashlib.sha256(payload).digest()[0] & 1 else _CONTROL


def _canonical_member_ids(db: Session, canonical_id: int) -> set[int]:
    canonical_by_id = {
        lemma_id: parent_id
        for lemma_id, parent_id in db.query(
            Lemma.lemma_id,
            Lemma.canonical_lemma_id,
        ).all()
    }
    return {
        lemma_id
        for lemma_id in canonical_by_id
        if resolve_canonical_via_map(lemma_id, canonical_by_id) == canonical_id
    } | {canonical_id}


def exact_surface_candidate_sentence_ids(
    db: Session,
    lemma_id: int,
    surface_key: str,
    exclude_sentence_ids: set[int] | None = None,
) -> set[int]:
    """Return existing, reviewable, non-passage sentences for an exact form."""
    member_ids = _canonical_member_ids(db, lemma_id)
    # Keep the outer mapping row distinct from the SentenceWord referenced by
    # reviewable_sentence_clauses()'s NOT EXISTS subquery. Reusing the model in
    # both places lets SQLAlchemy auto-correlate away the outer FROM clause.
    sw = aliased(SentenceWord)
    rows = (
        db.query(Sentence.id, sw.surface_form)
        .join(sw, sw.sentence_id == Sentence.id)
        .filter(
            sw.lemma_id.in_(member_ids),
            reviewable_sentence_clauses(),
            or_(Sentence.source.is_(None), Sentence.source != "passage"),
            # Match the selector's fail-closed behavior for explicitly rejected
            # LLM material. Unreviewed LLM rows remain valid fallbacks there and
            # here; rows known to be unnatural or mistranslated do not make an
            # otherwise-undeliverable episode look eligible.
            or_(
                Sentence.source.is_(None),
                Sentence.source != "llm",
                and_(
                    Sentence.quality_natural.is_not(False),
                    Sentence.quality_translation_correct.is_not(False),
                ),
            ),
        )
        .all()
    )
    forms_by_sentence: dict[int, set[str]] = {}
    for sentence_id, surface in rows:
        forms_by_sentence.setdefault(sentence_id, set()).add(
            normalize_surface_form(surface)
        )
    excluded = exclude_sentence_ids or set()
    return {
        sentence_id
        for sentence_id, form_keys in forms_by_sentence.items()
        if sentence_id not in excluded
        and form_keys == {surface_key}
    }


def _experiment_container(knowledge: UserLemmaKnowledge) -> tuple[dict, dict, list[dict]]:
    # SQLAlchemy's plain JSON column does not observe nested in-place changes.
    # Work on a deep copy so the final assignment is detectably different and
    # outcome/undo updates survive commit.
    stats = deepcopy(parse_json_column(knowledge.variant_stats_json))
    if not isinstance(stats, dict):
        stats = {}
    container = stats.get(EXACT_SURFACE_EXPERIMENT_KEY)
    if not isinstance(container, dict):
        container = {"version": EXACT_SURFACE_EXPERIMENT_VERSION, "episodes": []}
    episodes = container.get("episodes")
    if not isinstance(episodes, list):
        episodes = []
    container["version"] = EXACT_SURFACE_EXPERIMENT_VERSION
    container["episodes"] = episodes
    stats[EXACT_SURFACE_EXPERIMENT_KEY] = container
    return stats, container, episodes


def process_surface_experiment_review(
    db: Session,
    knowledge: UserLemmaKnowledge,
    lemma: Lemma | None,
    surfaces: list[str],
    review_log: ReviewLog | None,
    credit_type: str,
    sentence_ids: list[int],
    now: datetime,
) -> None:
    """Resolve an old episode, then optionally assign a new yellow episode."""
    if (
        lemma is None
        or review_log is None
        or review_log.id is None
        or review_log.review_mode != "reading"
    ):
        return
    normalized = {normalize_surface_form(surface) for surface in surfaces}
    normalized.discard("")
    if not normalized:
        return
    key = next(iter(normalized)) if len(normalized) == 1 else None

    stats, _container, episodes = _experiment_container(knowledge)
    changed = False

    # Intention-to-treat safety endpoint: the first later primary reading test,
    # regardless of which form the ordinary scheduler presents. Exact-form
    # delivery differs by design between arms, so comparing ratings only among
    # exact outcomes would condition on a post-randomization event.
    for episode in episodes:
        if (
            episode.get("any_form_outcome_rating") is not None
            or review_log.id <= (episode.get("trigger_review_id") or 0)
            or credit_type != "primary"
            or review_log.is_acquisition
        ):
            continue
        expires_at = _parse_dt(episode.get("expires_at"))
        if expires_at is not None and now > expires_at:
            continue
        episode["any_form_review_id"] = review_log.id
        episode["any_form_reviewed_at"] = now.isoformat()
        episode["any_form_surface_keys"] = sorted(normalized)
        episode["any_form_was_exact"] = normalized == {episode.get("surface_key")}
        episode["any_form_outcome_rating"] = review_log.rating
        episode["any_form_outcome_was_confused"] = bool(review_log.was_confused)
        episode["any_form_outcome_sentence_id"] = review_log.sentence_id
        changed = True
        log_interaction(
            event="exact_surface_experiment_any_form_outcome",
            episode_id=episode.get("id"),
            lemma_id=knowledge.lemma_id,
            arm=episode.get("arm"),
            rating=review_log.rating,
            was_confused=bool(review_log.was_confused),
            was_exact=episode["any_form_was_exact"],
        )

    # Outcomes require a later primary retrieval of the exact same form. This
    # makes a failed collateral form become a genuine test rather than receiving
    # another collateral success automatically.
    for episode in episodes:
        if (
            key is None
            or episode.get("surface_key") != key
            or episode.get("outcome_rating") is not None
            or review_log.id <= (episode.get("trigger_review_id") or 0)
            or credit_type != "primary"
            or review_log.is_acquisition
            or set(sentence_ids) & set(episode.get("trigger_sentence_ids") or [])
        ):
            continue
        expires_at = _parse_dt(episode.get("expires_at"))
        if expires_at is not None and now > expires_at:
            continue
        episode["delivered_at"] = now.isoformat()
        episode["outcome_review_id"] = review_log.id
        episode["outcome_rating"] = review_log.rating
        episode["outcome_was_confused"] = bool(review_log.was_confused)
        episode["outcome_credit_type"] = credit_type
        episode["outcome_sentence_id"] = review_log.sentence_id
        changed = True
        log_interaction(
            event="exact_surface_experiment_outcome",
            episode_id=episode.get("id"),
            lemma_id=knowledge.lemma_id,
            arm=episode.get("arm"),
            rating=review_log.rating,
            was_confused=bool(review_log.was_confused),
        )
        break

    # The pilot is specifically about yellow FSRS events. Acquisition evidence
    # and red misses retain their existing paths and cannot assign an episode.
    if key is not None and review_log.was_confused and not review_log.is_acquisition:
        morphology = eligible_surface_morphology(key, lemma)
        already_assigned = any(
            episode.get("surface_key") == key for episode in episodes
        )
        if morphology and not already_assigned:
            candidate_ids = exact_surface_candidate_sentence_ids(
                db,
                knowledge.lemma_id,
                key,
                exclude_sentence_ids=set(sentence_ids),
            )
            if candidate_ids:
                identity = review_log.client_review_id or str(review_log.id)
                arm = deterministic_arm(identity, knowledge.lemma_id, key)
                digest = hashlib.sha256(
                    f"{identity}:{knowledge.lemma_id}:{key}".encode("utf-8")
                ).hexdigest()[:16]
                episode = {
                    "id": digest,
                    "surface_key": key,
                    "surface_display": surfaces[0],
                    "morph_category": morphology.get("category"),
                    "form_key": morphology.get("form_key"),
                    "trigger_review_id": review_log.id,
                    "trigger_sentence_ids": list(sentence_ids),
                    "triggered_at": now.isoformat(),
                    "arm": arm,
                    "candidate_count_at_trigger": len(candidate_ids),
                    "expires_at": (now + timedelta(days=EXACT_SURFACE_EXPIRES_DAYS)).isoformat(),
                    "delivered_at": None,
                    "outcome_review_id": None,
                    "outcome_rating": None,
                    "outcome_was_confused": None,
                    "outcome_credit_type": None,
                    "outcome_sentence_id": None,
                    "any_form_review_id": None,
                    "any_form_reviewed_at": None,
                    "any_form_surface_keys": None,
                    "any_form_was_exact": None,
                    "any_form_outcome_rating": None,
                    "any_form_outcome_was_confused": None,
                    "any_form_outcome_sentence_id": None,
                }
                episodes.append(episode)
                changed = True
                log_interaction(
                    event="exact_surface_experiment_assigned",
                    episode_id=digest,
                    lemma_id=knowledge.lemma_id,
                    arm=arm,
                    surface_key=key,
                    morph_category=morphology.get("category"),
                    candidate_count=len(candidate_ids),
                )
            else:
                log_interaction(
                    event="exact_surface_experiment_ineligible",
                    lemma_id=knowledge.lemma_id,
                    reason="no_different_reviewable_sentence",
                    surface_key=key,
                )

    if changed:
        knowledge.variant_stats_json = stats


def undo_surface_experiment_reviews(
    knowledge: UserLemmaKnowledge,
    review_ids: set[int],
) -> bool:
    """Remove trigger effects or reopen outcomes for deleted ReviewLogs."""
    if not review_ids:
        return False
    stats = deepcopy(parse_json_column(knowledge.variant_stats_json))
    if not isinstance(stats, dict):
        return False
    container = stats.get(EXACT_SURFACE_EXPERIMENT_KEY)
    episodes = container.get("episodes") if isinstance(container, dict) else None
    if not isinstance(episodes, list):
        return False

    changed = False
    retained: list[dict] = []
    for episode in episodes:
        if not isinstance(episode, dict):
            retained.append(episode)
            continue
        if episode.get("trigger_review_id") in review_ids:
            changed = True
            continue
        if episode.get("outcome_review_id") in review_ids:
            for key in (
                "delivered_at",
                "outcome_review_id",
                "outcome_rating",
                "outcome_was_confused",
                "outcome_credit_type",
                "outcome_sentence_id",
            ):
                episode[key] = None
            changed = True
        if episode.get("any_form_review_id") in review_ids:
            for key in (
                "any_form_review_id",
                "any_form_reviewed_at",
                "any_form_surface_keys",
                "any_form_was_exact",
                "any_form_outcome_rating",
                "any_form_outcome_was_confused",
                "any_form_outcome_sentence_id",
            ):
                episode[key] = None
            changed = True
        retained.append(episode)

    if changed:
        container = dict(container)
        container["episodes"] = retained
        stats[EXACT_SURFACE_EXPERIMENT_KEY] = container
        knowledge.variant_stats_json = stats
    return changed


def active_treatment_episodes(
    knowledge_by_id: dict[int, UserLemmaKnowledge],
    now: datetime,
) -> dict[int, dict]:
    """Return oldest active treatment episode per canonical lemma."""
    active: dict[int, dict] = {}
    for lemma_id, knowledge in knowledge_by_id.items():
        if knowledge.knowledge_state == "acquiring":
            continue
        stats = parse_json_column(knowledge.variant_stats_json)
        container = stats.get(EXACT_SURFACE_EXPERIMENT_KEY)
        episodes = container.get("episodes") if isinstance(container, dict) else None
        if not isinstance(episodes, list):
            continue
        for episode in episodes:
            if (
                not isinstance(episode, dict)
                or episode.get("arm") != _TREATMENT
                or episode.get("outcome_rating") is not None
            ):
                continue
            expires_at = _parse_dt(episode.get("expires_at"))
            if expires_at is None or now > expires_at:
                continue
            current = active.get(lemma_id)
            if current is None or str(episode.get("triggered_at")) < str(current.get("triggered_at")):
                active[lemma_id] = episode
    return active
