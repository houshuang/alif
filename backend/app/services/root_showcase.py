"""Root-showcase sentence orchestration.

Generates "root-saturation" sentences that pack multiple derivations of one
Arabic root into a single sentence (e.g. الكاتب كتب كتبًا في المكتب). Each
surface form earns its own lemma review credit via the foundational
"every word in every sentence" rule — one showcase sentence yields N
review hits instead of 1.

Flow per root:
  1. Build palette from gated canonical lemmas under the root
  2. Build auxiliary vocabulary sample for prompt context
  3. LLM (Claude Sonnet) generates batch of candidate sentences
  4. Route each through the existing validate_multi_target_sentence flow
     (lemma mapping + verifier + corrector — lock-discipline safe)
  5. Persist with kind='root_showcase' and root_focus_id stamped

Reuses the existing multi-target validation + write split (extracted from
the 2026-04-17 lock incident) so generation-time LLM calls never hold the
SQLite write lock.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import Lemma, Root, UserLemmaKnowledge
from app.services.llm import (
    RootShowcaseSentenceResult,
    generate_root_showcase_sentences,
    review_sentences_quality,
)
from app.services.material_generator import (
    validate_multi_target_sentence,
    write_multi_target_sentence,
)
from app.services.sentence_validator import (
    build_comprehensive_lemma_lookup,
    strip_diacritics,
)

logger = logging.getLogger(__name__)


WAZN_TO_FAMILY: dict[str, str] = {
    "form_1": "verb_I", "form_2": "verb_II", "form_3": "verb_III",
    "form_4": "verb_IV", "form_5": "verb_V", "form_6": "verb_VI",
    "form_7": "verb_VII", "form_8": "verb_VIII", "form_9": "verb_IX",
    "form_10": "verb_X",
    "fa'il": "agent", "muf'il": "agent", "mufa''il": "agent",
    "mufa'il": "agent", "mutafa''il": "agent", "mutafa'il": "agent",
    "munfa'il": "agent", "mufta'il": "agent", "mustaf'il": "agent",
    "maf'ul": "patient", "muf'al": "patient", "mufa''al": "patient",
    "mufa'al": "patient", "mufta'al": "patient", "mustaf'al": "patient",
    "fa'l": "masdar_I", "fi'l": "masdar_I", "fu'l": "masdar_I",
    "fa'al": "masdar_I", "fi'al": "masdar_I", "fu'al": "masdar_I",
    "fa'ala": "masdar_I", "fi'ala": "masdar_I", "fu'la": "masdar_I",
    "fa'la": "masdar_I", "fi'la": "masdar_I", "fa'ula": "masdar_I",
    "fa'lan": "masdar_I", "fa'ul": "masdar_I", "fu'ul": "masdar_I",
    "taf'il": "masdar_II",
    "if'al": "masdar_IV",
    "ifti'al": "masdar_VIII",
    "istif'al": "masdar_X",
    "maf'al": "place_or_time", "maf'il": "place_or_time", "maf'ala": "place_or_time",
    "mif'al": "instrument", "mif'ala": "instrument", "mif'aal": "instrument",
    "fa''al": "intensive_profession",
    "fa'iil": "adj_fa'iil",
    "nisba": "nisba_adj",
    "af'al": "elative",
}


@dataclass
class ShowcaseResult:
    root_id: int
    root: str
    palette_size: int
    requested: int
    generated: int
    persisted: int
    sentence_ids: list[int]
    rejected_reasons: list[str]


def build_palette_for_root(db: Session, root_id: int) -> list[dict[str, Any]]:
    """Return the showcase palette for a root.

    Only canonical (no canonical_lemma_id), gated, non-proper-name lemmas
    are included. The returned shape matches what the LLM prompt expects.

    Lemmas with no ULK *or* ULK state in {'new', 'encountered'} are excluded:
    a brand-new lemma should reach the user via a proper intro card first,
    not via a multi-derivation showcase as its first encounter. This rule
    matters in tandem with the Phase 3 gap-fill, which now creates ULK rows
    in 'encountered' state for new lemmas — they remain palette-eligible
    only after they've been introduced and entered acquiring/known/lapsed.
    """
    lemmas = (
        db.query(Lemma)
        .filter(Lemma.root_id == root_id)
        .filter(Lemma.gates_completed_at.isnot(None))
        .filter(Lemma.canonical_lemma_id.is_(None))
        .filter((Lemma.word_category != "proper_name") | (Lemma.word_category.is_(None)))
        .all()
    )
    if not lemmas:
        return []
    lemma_ids = [l.lemma_id for l in lemmas]
    states = {
        u.lemma_id: u.knowledge_state
        for u in db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id.in_(lemma_ids)).all()
    }
    INTRODUCED_STATES = {"acquiring", "learning", "known", "lapsed"}
    palette: list[dict[str, Any]] = []
    for l in lemmas:
        if not l.gloss_en:
            continue
        if states.get(l.lemma_id) not in INTRODUCED_STATES:
            continue
        palette.append({
            "lemma_id": l.lemma_id,
            "arabic": l.lemma_ar,
            "bare": l.lemma_ar_bare,
            "english": l.gloss_en,
            "wazn": l.wazn,
            "family": WAZN_TO_FAMILY.get(l.wazn or "", None),
            "pos": l.pos,
        })
    return palette


def sample_auxiliary_vocabulary(
    db: Session, root_id: int, sample_size: int = 200
) -> list[dict[str, str]]:
    """Pick a sample of known/acquiring lemmas from OTHER roots for prompt glue."""
    ulks = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.knowledge_state.in_(("known", "acquiring", "lapsed")))
        .all()
    )
    other_lemma_ids = [u.lemma_id for u in ulks]
    if not other_lemma_ids:
        return []
    lemmas = (
        db.query(Lemma)
        .filter(Lemma.lemma_id.in_(other_lemma_ids))
        .filter(Lemma.root_id != root_id)
        .filter((Lemma.word_category != "proper_name") | (Lemma.word_category.is_(None)))
        .all()
    )
    if len(lemmas) > sample_size:
        lemmas = random.sample(lemmas, sample_size)
    return [
        {"arabic": l.lemma_ar, "english": l.gloss_en or "", "pos": l.pos or ""}
        for l in lemmas if l.gloss_en
    ]


def generate_and_store_showcases_for_root(
    db: Session,
    root_id: int,
    count: int = 3,
    *,
    model_override: str = "claude_sonnet",
    quality_review: bool = True,
    persist: bool = True,
) -> ShowcaseResult:
    """Generate + persist root-showcase sentences for one root.

    Two-phase to respect SQLite lock discipline:
      Phase A (read + close): load palette, lookup, aux sample. Close session.
      Phase B (LLM, no session): generate, validate (uses a fresh read-only
                                  session inside validate_multi_target_sentence).
      Phase C (write): re-open session, persist accepted sentences.
    """
    root = db.query(Root).filter(Root.root_id == root_id).first()
    if not root:
        raise ValueError(f"Root {root_id} not found")

    palette = build_palette_for_root(db, root_id)
    if len(palette) < 3:
        return ShowcaseResult(
            root_id=root_id, root=root.root, palette_size=len(palette),
            requested=count, generated=0, persisted=0,
            sentence_ids=[], rejected_reasons=["palette_too_small"],
        )

    aux = sample_auxiliary_vocabulary(db, root_id)
    root_str = root.root
    core_meaning = root.core_meaning_en or ""

    # Release the read-side session before any LLM work
    db.commit()

    try:
        generated: list[RootShowcaseSentenceResult] = generate_root_showcase_sentences(
            root=root_str,
            core_meaning_en=core_meaning,
            palette=[
                {
                    "arabic": p["arabic"],
                    "english": p["english"],
                    "wazn": p.get("wazn") or "",
                    "family": p.get("family") or "",
                }
                for p in palette
            ],
            aux_vocabulary=aux,
            count=count,
            model_override=model_override,
        )
    except Exception as e:
        logger.exception(f"Root-showcase generation failed for {root_str}")
        return ShowcaseResult(
            root_id=root_id, root=root_str, palette_size=len(palette),
            requested=count, generated=0, persisted=0,
            sentence_ids=[], rejected_reasons=[f"llm_error: {e}"],
        )

    if not generated:
        return ShowcaseResult(
            root_id=root_id, root=root_str, palette_size=len(palette),
            requested=count, generated=0, persisted=0,
            sentence_ids=[], rejected_reasons=["llm_returned_empty"],
        )

    target_bares: dict[str, int] = {}
    for p in palette:
        bare = p["bare"] or strip_diacritics(p["arabic"])
        if bare:
            target_bares[bare] = p["lemma_id"]

    lemma_lookup = build_comprehensive_lemma_lookup(db)

    # Build a "most-due first" ranking of palette lemmas. We stamp the
    # sentence's target_lemma_id to the most-due lemma actually targeted in
    # the sentence — not an arbitrary one — so the selector picks the showcase
    # up at the moment when a learner most needs review on one of its words.
    palette_due_rank = _build_palette_due_ranking(db, [p["lemma_id"] for p in palette])

    accepted_results: list[tuple[RootShowcaseSentenceResult, list]] = []
    rejected: list[str] = []
    for res in generated:
        # Adapt RootShowcaseSentenceResult to the MultiTargetGeneratedSentence
        # shape that validate_multi_target_sentence expects. Pick the first
        # palette lemma as primary target for storage; selector tier logic
        # later treats them as a group via root_focus_id.
        primary_lemma_id = palette[0]["lemma_id"]
        adapted = _ShowcaseToMultiTargetAdapter(
            arabic=res.arabic,
            english=res.english,
            transliteration=res.transliteration,
            primary_target_lemma_id=primary_lemma_id,
        )
        mappings = validate_multi_target_sentence(
            db, adapted, lemma_lookup, target_bares,
        )
        if mappings is None:
            rejected.append(f"validation_failed: {res.arabic[:40]}")
            continue
        # Require ≥3 distinct palette lemmas actually targeted (per the
        # showcase contract — fewer = not a showcase, just a normal sentence)
        targeted = {m.lemma_id for m in mappings if m.is_target and m.lemma_id}
        if len(targeted) < 3:
            rejected.append(
                f"too_few_palette_lemmas ({len(targeted)}): {res.arabic[:40]}"
            )
            continue
        # Re-pick primary to be the MOST-DUE palette lemma actually in the
        # sentence. Pre-2026-05-28 this used `next(iter(targeted))` which is
        # set-iteration-order — effectively random, so the showcase entered
        # the selector pool when an arbitrary one of its palette lemmas came
        # due. Most-due means the showcase is most likely to win against
        # competing sentences for that lemma's review slot.
        adapted.primary_target_lemma_id = min(
            targeted, key=lambda lid: palette_due_rank.get(lid, _DUE_RANK_DEFAULT)
        )
        accepted_results.append((adapted, mappings))

    if not accepted_results:
        return ShowcaseResult(
            root_id=root_id, root=root_str, palette_size=len(palette),
            requested=count, generated=len(generated), persisted=0,
            sentence_ids=[], rejected_reasons=rejected,
        )

    if quality_review:
        review_inputs = [
            {"arabic": adapted.arabic, "english": adapted.english}
            for adapted, _ in accepted_results
        ]
        reviews = review_sentences_quality(review_inputs)
        # The showcase prompt explicitly invites wordplay — be tolerant of
        # "unnatural" flags on naturalness alone. Reject only on translation
        # incorrectness (a hard quality bar). Stamp the review either way.
        for (adapted, _), review in zip(accepted_results, reviews):
            adapted.quality_reviewed_at = datetime.now(timezone.utc)
            adapted.quality_natural = bool(review.natural)
            adapted.quality_translation_correct = bool(review.translation_correct)
            adapted.quality_reason = (review.reason or "")[:500]
        accepted_results = [
            (a, m) for (a, m) in accepted_results
            if a.quality_translation_correct is not False
        ]
        if not accepted_results:
            return ShowcaseResult(
                root_id=root_id, root=root_str, palette_size=len(palette),
                requested=count, generated=len(generated), persisted=0,
                sentence_ids=[],
                rejected_reasons=rejected + ["quality_translation_incorrect"],
            )

    persisted_ids: list[int] = []
    for adapted, mappings in accepted_results:
        sent = write_multi_target_sentence(db, adapted, mappings)
        sent.root_focus_id = root_id
        sent.kind = "root_showcase"
        db.flush()
        persisted_ids.append(sent.id)
    if persist:
        db.commit()
    else:
        # Dry-run: roll back the writes but keep the persisted_ids list so the
        # caller sees what *would* have been created. IDs become invalid after
        # rollback, so callers must not query them — they're informational only.
        db.rollback()

    return ShowcaseResult(
        root_id=root_id, root=root_str, palette_size=len(palette),
        requested=count, generated=len(generated), persisted=len(persisted_ids),
        sentence_ids=persisted_ids, rejected_reasons=rejected,
    )


# Sentinel: lemmas not in the user's ULK (unstudied) sort *after* known/acquiring
# lemmas — they'd never enter the selector pool anyway, so we'd never need to
# stamp the showcase to them. A large positive float beats any real timestamp.
_DUE_RANK_DEFAULT = 1e15


def _build_palette_due_ranking(db: Session, lemma_ids: list[int]) -> dict[int, float]:
    """Return {lemma_id: due_rank} where lower = more-due / more-overdue.

    Rank is the unix timestamp of when the lemma's next review is due:
      - acquisition_next_due for acquiring lemmas
      - fsrs_card_json.due for FSRS-state lemmas
    Lemmas with no due date (encountered / unstudied) sort to the end via
    _DUE_RANK_DEFAULT — they wouldn't enter the selector via due-ness anyway.
    """
    if not lemma_ids:
        return {}
    import json
    from app.models import UserLemmaKnowledge as _ULK
    ranks: dict[int, float] = {}
    ulks = db.query(_ULK).filter(_ULK.lemma_id.in_(lemma_ids)).all()
    for ulk in ulks:
        due_ts: float | None = None
        if ulk.acquisition_next_due is not None:
            due_ts = ulk.acquisition_next_due.timestamp()
        elif ulk.fsrs_card_json:
            card = ulk.fsrs_card_json
            if isinstance(card, str):
                try:
                    card = json.loads(card)
                except (json.JSONDecodeError, TypeError):
                    card = None
            if isinstance(card, dict) and card.get("due"):
                try:
                    due_ts = datetime.fromisoformat(
                        card["due"].replace("Z", "+00:00")
                    ).timestamp()
                except (ValueError, TypeError, AttributeError):
                    due_ts = None
        if due_ts is not None:
            ranks[ulk.lemma_id] = due_ts
    return ranks


class _ShowcaseToMultiTargetAdapter:
    """Duck-typed adapter so validate_multi_target_sentence + write_multi_target_sentence
    accept a RootShowcaseSentenceResult. We can't subclass MultiTargetGeneratedSentence
    cleanly because the validator/writer read attributes via getattr.
    """
    def __init__(self, arabic: str, english: str, transliteration: str, primary_target_lemma_id: int):
        self.arabic = arabic
        self.english = english
        self.transliteration = transliteration
        self.primary_target_lemma_id = primary_target_lemma_id
        self.quality_reviewed_at = None
        self.quality_natural = None
        self.quality_translation_correct = None
        self.quality_reason = None
