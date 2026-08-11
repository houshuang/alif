"""Token-isolated morphology/tashkeel recovery.

This ledger is deliberately subordinate to canonical scheduling.  It never
creates a card or due date; it records which running form needs representation
inside sentences that are already eligible for scheduled work.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.services.confusion_service import (
    classify_surface_morphology,
    normalize_surface_form,
)
from app.services.fsrs_service import parse_json_column
from app.services.sentence_validator import strip_diacritics

if TYPE_CHECKING:
    from app.models import Lemma, ReviewLog, UserLemmaKnowledge


FORM_RECOVERY_KEY = "__form_recovery_v1"
FORM_RECOVERY_VERSION = "form_recovery_v1"
FORM_RECOVERY_REQUIRED_SUCCESSES = 2
FORM_RECOVERY_CAUSES = {"unfamiliar_form", "missing_tashkeel"}


def form_family(surface: str, lemma: "Lemma | None") -> dict[str, Any] | None:
    """Return a stable, diacritic-aware family for a non-citation surface."""
    morphology = classify_surface_morphology(surface, lemma)
    if not morphology:
        return None
    category = morphology.get("category")
    form_key = morphology.get("form_key")
    return {
        "category": category,
        "form_key": form_key,
        "family_key": f"{category}:{form_key or '-'}",
    }


def is_meaningful_form_failure(surface: str, lemma: "Lemma | None") -> bool:
    """Exclude citation identity and article-only variation from protection."""
    return form_family(surface, lemma) is not None


def _state(knowledge: "UserLemmaKnowledge") -> tuple[dict, dict, list[dict]]:
    # JSON columns do not notice nested in-place mutation. Work on a deep copy
    # so assigning the finished ledger always produces a detectable old/new
    # value, including citation-form tashkeel episodes with no variant stats.
    stats = deepcopy(parse_json_column(knowledge.variant_stats_json))
    if not isinstance(stats, dict):
        stats = {}
    state = dict(stats.get(FORM_RECOVERY_KEY) or {})
    state.setdefault("version", FORM_RECOVERY_VERSION)
    episodes = [dict(ep) for ep in state.get("episodes") or []]
    state["episodes"] = episodes
    return stats, state, episodes


def _episode_key(row: dict, family: dict | None) -> str:
    causes = "+".join(sorted(row["causes"]))
    normalized = normalize_surface_form(row["surface_form"])
    family_key = family["family_key"] if family else "tashkeel"
    return f"{normalized}|{family_key}|{causes}"


def _matches_episode(row: dict, episode: dict, lemma: "Lemma | None") -> bool:
    normalized = normalize_surface_form(row["surface_form"])
    if normalized == episode.get("normalized_surface"):
        matches_form = True
    else:
        family = form_family(row["surface_form"], lemma)
        matches_form = bool(
            family
            and family.get("family_key") == episode.get("family_key")
        )
    if not matches_form:
        return False
    if "missing_tashkeel" in set(episode.get("causes") or []):
        # A success only demonstrates recovery from missing marks when those
        # marks were actually hidden on the judged front.
        return bool(
            strip_diacritics(row["surface_form"]) != row["surface_form"]
            and not row["front_initial_tashkeel_visible"]
        )
    return True


def process_form_recovery_review(
    *,
    knowledge: "UserLemmaKnowledge",
    lemma: "Lemma | None",
    rows: list[dict],
    protected: bool,
    review_log: "ReviewLog | None",
    client_review_id: str | None,
    now: datetime,
) -> None:
    """Open protected failures and credit later token-level green outcomes."""
    if not rows or review_log is None:
        return
    stats, state, episodes = _state(knowledge)
    changed = False

    if protected:
        for row in rows:
            if row["rating"] >= 3 or not set(row["causes"]) <= FORM_RECOVERY_CAUSES:
                continue
            family = form_family(row["surface_form"], lemma)
            key = _episode_key(row, family)
            episode = next(
                (
                    ep for ep in reversed(episodes)
                    if ep.get("episode_key") == key and ep.get("status") == "open"
                ),
                None,
            )
            trigger = {
                "review_log_id": review_log.id,
                "client_review_id": client_review_id,
                "sentence_word_id": row["sentence_word_id"],
                "sentence_id": row["sentence_id"],
                "at": now.isoformat(),
                "rating": row["rating"],
            }
            if episode is None:
                episode = {
                    "episode_key": key,
                    "status": "open",
                    "surface_form": row["surface_form"],
                    "normalized_surface": normalize_surface_form(row["surface_form"]),
                    "family_key": family["family_key"] if family else None,
                    "category": family["category"] if family else None,
                    "form_key": family["form_key"] if family else None,
                    "causes": sorted(row["causes"]),
                    "required_successes": FORM_RECOVERY_REQUIRED_SUCCESSES,
                    "triggers": [],
                    "successes": [],
                    "opened_at": now.isoformat(),
                }
                episodes.append(episode)
            if not any(
                existing.get("sentence_word_id") == row["sentence_word_id"]
                and existing.get("review_log_id") == review_log.id
                for existing in episode["triggers"]
            ):
                episode["triggers"].append(trigger)
                episode["last_trigger_at"] = now.isoformat()
                changed = True

    for episode in episodes:
        if episode.get("status") != "open":
            continue
        trigger_review_ids = {
            item.get("review_log_id") for item in episode.get("triggers") or []
        }
        if review_log.id in trigger_review_ids:
            continue
        for row in rows:
            if row["rating"] != 3 or not _matches_episode(row, episode, lemma):
                continue
            sentence_id = row["sentence_id"]
            successes = episode.setdefault("successes", [])
            # Repeated occurrences inside one passage/sentence are one proof.
            if any(success.get("sentence_id") == sentence_id for success in successes):
                continue
            successes.append({
                "review_log_id": review_log.id,
                "client_review_id": client_review_id,
                "sentence_word_id": row["sentence_word_id"],
                "sentence_id": sentence_id,
                "surface_form": row["surface_form"],
                "at": now.isoformat(),
            })
            changed = True
            if len(successes) >= int(
                episode.get("required_successes")
                or FORM_RECOVERY_REQUIRED_SUCCESSES
            ):
                episode["status"] = "resolved"
                episode["resolved_at"] = now.isoformat()
            break

    if changed:
        state["episodes"] = episodes
        stats[FORM_RECOVERY_KEY] = state
        knowledge.variant_stats_json = stats


def undo_form_recovery_reviews(
    knowledge: "UserLemmaKnowledge",
    deleted_review_ids: set[int],
) -> None:
    """Remove trigger/outcome evidence written by an undone review."""
    if not deleted_review_ids:
        return
    stats, state, episodes = _state(knowledge)
    if FORM_RECOVERY_KEY not in stats:
        return
    changed = False
    kept: list[dict] = []
    for episode in episodes:
        triggers = [
            item for item in episode.get("triggers") or []
            if item.get("review_log_id") not in deleted_review_ids
        ]
        successes = [
            item for item in episode.get("successes") or []
            if item.get("review_log_id") not in deleted_review_ids
        ]
        if len(triggers) != len(episode.get("triggers") or []) or len(successes) != len(
            episode.get("successes") or []
        ):
            changed = True
        if not triggers:
            continue
        episode["triggers"] = triggers
        episode["successes"] = successes
        required = int(
            episode.get("required_successes") or FORM_RECOVERY_REQUIRED_SUCCESSES
        )
        if len(successes) < required:
            episode["status"] = "open"
            episode.pop("resolved_at", None)
        kept.append(episode)
    if changed:
        state["episodes"] = kept
        stats[FORM_RECOVERY_KEY] = state
        knowledge.variant_stats_json = stats


def open_form_recovery_episodes(knowledge: "UserLemmaKnowledge") -> list[dict]:
    """Read active episodes without mutating the JSON column."""
    stats = parse_json_column(knowledge.variant_stats_json)
    state = stats.get(FORM_RECOVERY_KEY) if isinstance(stats, dict) else None
    if not isinstance(state, dict) or state.get("version") != FORM_RECOVERY_VERSION:
        return []
    return [
        dict(episode)
        for episode in state.get("episodes") or []
        if episode.get("status") == "open"
    ]


def is_form_recovery_protected_log(review_log: "ReviewLog") -> bool:
    """Whether a product failure was intentionally not a canonical lapse."""
    metadata = parse_json_column(review_log.fsrs_log_json)
    if not isinstance(metadata, dict):
        return False
    return bool(
        metadata.get("form_recovery_policy_version") == FORM_RECOVERY_VERSION
        and metadata.get("form_recovery_protected") is True
    )
