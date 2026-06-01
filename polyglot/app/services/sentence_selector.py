"""Sentence picker + minimal session builder.

Read-side spine of the sentence-review pipeline (PR #3). Given a due lemma,
pick the best Sentence row to put in front of the learner. Given a desired
session size, walk the due list and assemble a list of sentences.

Deliberately scoped down from Alif's 2,864-line ``sentence_selector.py``:

- No intro cards, no reintros, no passages, no grammar-slot weighting.
- No frequency-lane / awzān / clitic / Hindawi-tier machinery.
- No diversity scoring or near-duplicate veto beyond "don't repeat the same
  sentence in one session" and "don't repeat a recently-shown sentence in a
  normal session".
- No generation fallback — when nothing fits, return ``None`` and let PR #4's
  material generator fill the gap.

The three gates the picker DOES honor (Hard Invariants from
``polyglot/CLAUDE.md``):

- **Reviewability**: ``Sentence.is_active`` AND ``mappings_verified_at IS NOT
  NULL``. ``sentence_harvest`` already excludes caps-heading sentences at
  storage, so we don't re-filter here.
- **Canonical at entry**: the caller-supplied ``lemma_id`` is redirected
  through ``resolve_canonical_lemma_id`` before any SentenceWord query.
  Hard Invariant #9.
- **Function-word / proper-name scaffold skip**: comprehensibility counts only
  content lemmas. ``FUNCTION_WORD_SETS[language_code]`` and
  ``Lemma.word_category in ('function_word', 'proper_name')`` both exclude.

Source preference (per 2026-05-22 spec — strengthened from the 2026-05-21
multiplier so quality-approved generated material is *strictly* preferred):

1. **Quality-approved generated sentences strictly outrank textbook sentences.**
   Source is the *primary* sort key, not a score multiplier. Any reviewed-good
   ``llm`` sentence beats any non-``llm`` (textbook page-of-record) sentence regardless of
   comprehensibility. Review wants recall in a novel context — re-showing the
   page the learner just read is zero-friction recognition, not recall. The
   previous (2026-05-21) ``llm × 1.5`` multiplier was not strong enough: a
   fully-comprehensible textbook sentence (score ``1.0``) still beat a
   half-comprehensible llm sentence (``0.65 × 1.5 = 0.975``), so book
   sentences kept surfacing at review time. Unreviewed legacy ``llm`` rows are
   kept as penalized fallback; quality-failed ``llm`` rows are skipped.
2. **Within a tier, comprehensibility + page cooldown order candidates.**
   ``score = (0.3 + 0.7·comprehensibility) · cooldown``. A textbook sentence
   whose page was viewed within ``PAGE_COOLDOWN_DAYS`` (7) days is multiplied
   by ``RECENT_PAGE_PENALTY`` (0.2) so older / unread-page fallbacks rank
   above the page the learner just finished reading.
3. **Never-shown sentences win tie-breakers**. Within a single (tier, score)
   class, prefer sentences with the lowest ``times_shown`` count; among equal
   ``times_shown``, prefer the most recently created (newer LLM material
   beats older).
4. **None**, when no eligible sentence covers the lemma at all. Caller
   defers to generation or skips the lemma.

Book sentences remain a graceful fallback, but session construction caps them:
when no generated sentence covers the lemma yet — the lag between tapping an
unknown word and the warm-cache cron generating — the picker may still return
the textbook sentence, but a normal session only admits a small number of
textbook fallbacks before skipping the rest and waiting for generated material.
Those fallback sentences are translated lazily by
``material_generator.translate_untranslated_sentences`` in the cron so a
fallback never reaches the screen without an English line.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.models import Lemma, Page, Sentence, SentenceWord, UserLemmaKnowledge, Language
from app.services.canonical_resolution import resolve_canonical_lemma_id
from app.services.fsrs_service import parse_json_column
from app.services.lemma_quality import FUNCTION_WORD_SETS, is_noncontent_lemma

logger = logging.getLogger(__name__)


KNOWN_STATES = frozenset({"known", "learning"})
DEFAULT_SESSION_LIMIT = 15

# 2026-05-22: source is the PRIMARY sort key (a tier), not a score multiplier.
# Any quality-approved sentence whose source is in GENERATED_SOURCES strictly
# outranks every other ("book"/page-of-record) candidate — see the module
# docstring for why the old multiplier wasn't strong enough.
GENERATED_SOURCES = frozenset({"llm"})
LLM_UNREVIEWED_QUALITY_MULTIPLIER = 0.55

# Soft penalty applied when a candidate sentence's page was viewed within the
# cooldown window. Multiplicative: a strong (~0.2) penalty drops the page
# sentence below any sane LLM/manual alternative, but keeps it as a fallback
# in case no novel material exists yet (e.g. tapped-unknown words whose
# enrichment cron hasn't run). Set to 1.0 to disable.
PAGE_COOLDOWN_DAYS = 7
RECENT_PAGE_PENALTY = 0.2

# Generated sentences can otherwise repeat when the target word returns quickly
# and the same all-known scaffold still dominates the score. This is only a
# penalty, not a veto, so one-candidate words still have material.
SENTENCE_RECENCY_HOURS = 24
RECENT_SENTENCE_PENALTY = 0.25

# Textbook/page-of-record sentences are useful stopgaps while generated
# material catches up, but they should not dominate a review session. Keep the
# cap deliberately small: a short session may still show one fallback; normal
# sessions show at most two.
#
# **Hard rule (user feedback 2026-05-26):** textbook sentences are NEVER
# served for `acquiring` (Box 1/2/3) lemmas, regardless of this cap. Book
# prose is syntactically and lexically beyond early-learning comprehensibility
# — using it to practice a just-tapped-red word ruins the experience. Acquiring
# lemmas wait for an LLM sentence or get skipped from the session. The cap
# below still gates textbook fallbacks for FSRS-carded lemmas where the word
# is already well-learned.
TEXTBOOK_FALLBACK_SOURCES = frozenset({"textbook"})
TEXTBOOK_FALLBACK_MAX_PER_SESSION = 2

# ── Collateral / scaffold diversity (ported from Alif sentence_selector) ──────
# Polyglot ported only the soft generation-side sampling bias; Alif's
# experiment log credits these *selection-side* mechanisms as what actually
# broke the "same handful of high-frequency known words in every card" pattern.
# Spreading collateral across the known pool is, for polyglot, the same lever as
# confirmation breadth: every distinct assumed-known that surfaces in a shown
# sentence gets a green-confirmation, so wider scaffold reuse = more words
# verified. See research/experiment-log.md (Alif SESSION_SCAFFOLD_DECAY).
#
# Within-session scaffold decay: each reuse of a known scaffold word in an
# already-picked sentence this session multiplies the next candidate's score by
# 0.5**reuse_count (1st use 1.0×, 2nd 0.5×, 3rd 0.25×).
SESSION_SCAFFOLD_DECAY = 0.5
# Scaffold freshness: an over-exposed scaffold word (high times_seen) yields to a
# rarely-/never-seen one. Per-word penalty = min(1, BASELINE/max(times_seen,1)),
# geometric mean across the sentence's content scaffold, floored. A never-seen
# (times_seen=0) word contributes 1.0, so still-unconfirmed knowns are favoured.
SCAFFOLD_FRESHNESS_BASELINE = 5
SCAFFOLD_FRESHNESS_FLOOR = 0.1

# ── Confirmation sweep (Tier 2, polyglot-original) ────────────────────────────
# Share of each session reserved for confirming assumed-known words never yet
# verified by exposure (knowledge_state='known' / no FSRS card / confirmed_at
# IS NULL). Filled by greedy set-cover over existing reviewable sentences,
# maximising distinct never-confirmed assumed-knowns covered. Reading is the
# breadth tool, but the learner studies via sentence review by default — this
# makes review carry real confirmation breadth too. Gap-training keeps the
# majority; the reserve only binds once enough due gaps exist to fill the rest.
CONFIRMATION_SESSION_SHARE = 0.30
# Don't surface a confirmation sentence dense with genuinely-unknown
# (new/acquiring) words just because it covers an assumed-known — the
# assumed-known words are by definition known, so a real exposure should still
# be mostly comprehensible. Same floor Alif uses for its comprehensibility gate.
CONFIRMATION_MIN_COMPREHENSIBILITY = 0.6


def _session_decay_multiplier(reuse_counts: list[int]) -> float:
    """0.5 ** (max reuse so far among this sentence's scaffold words).

    A candidate that re-uses a known word already placed 3× this session is
    scored 0.5**3 = 0.125 — strong enough to let a fresher-scaffold sentence
    win, but a multiplier, not a veto (a one-candidate word still gets shown).
    """
    if not reuse_counts:
        return 1.0
    return SESSION_SCAFFOLD_DECAY ** max(reuse_counts)


def _scaffold_freshness_multiplier(times_seen: list[int]) -> float:
    """Geometric mean of per-scaffold-word freshness penalties.

    penalty(w) = min(1, BASELINE / max(times_seen, 1)); aggregated as a
    geometric mean and floored at SCAFFOLD_FRESHNESS_FLOOR so an all-over-exposed
    sentence still contributes. Empty scaffold → neutral 1.0.
    """
    if not times_seen:
        return 1.0
    product = 1.0
    for ts in times_seen:
        product *= min(1.0, SCAFFOLD_FRESHNESS_BASELINE / max(ts, 1))
    geo = product ** (1.0 / len(times_seen))
    return max(SCAFFOLD_FRESHNESS_FLOOR, geo)


def _confirmation_budget(limit: int) -> int:
    """Slots reserved for the confirmation sweep. 0 for tiny sessions so the
    legacy gap-only behaviour (and the existing tests) is preserved."""
    if limit < 4:
        return 0
    return int(limit * CONFIRMATION_SESSION_SHARE + 0.5)


def _greedy_cover(
    cover_by_id: dict[int, set[int]],
    budget: int,
    rank: dict[int, tuple],
) -> list[int]:
    """Greedy maximum-coverage selection.

    ``cover_by_id`` maps candidate id → the set of target items it covers.
    Repeatedly pick the candidate adding the most *still-uncovered* items
    (ties broken by ``rank[id]``, higher wins), mark them covered, until the
    budget is hit or no candidate adds anything. Pure / DB-free for testability.
    """
    if budget <= 0:
        return []
    covered: set[int] = set()
    remaining = set(cover_by_id)
    chosen: list[int] = []
    while len(chosen) < budget and remaining:
        best_id = None
        best_key: tuple | None = None
        for cid in remaining:
            gain = len(cover_by_id[cid] - covered)
            if gain <= 0:
                continue
            key = (gain,) + tuple(rank.get(cid, ()))
            if best_key is None or key > best_key:
                best_key = key
                best_id = cid
        if best_id is None:
            break
        chosen.append(best_id)
        covered |= cover_by_id[best_id]
        remaining.discard(best_id)
    return chosen


def _quality_multiplier_for_sentence(sent: Sentence) -> float:
    """Prefer quality-reviewed LLM rows, but keep unreviewed rows as fallback."""
    if (sent.source or "") not in GENERATED_SOURCES:
        return 1.0

    natural = getattr(sent, "quality_natural", None)
    translation_correct = getattr(sent, "quality_translation_correct", None)
    if natural is False or translation_correct is False:
        return 0.0
    if natural is True and translation_correct is True:
        return 1.0
    if getattr(sent, "quality_reviewed_at", None) is not None:
        return 1.0
    return LLM_UNREVIEWED_QUALITY_MULTIPLIER


def _is_quality_approved_generated(sent: Sentence) -> bool:
    if (sent.source or "") not in GENERATED_SOURCES:
        return False
    return (
        getattr(sent, "quality_natural", None) is True
        and getattr(sent, "quality_translation_correct", None) is True
    )


@dataclass
class WordRender:
    """Per-word payload returned to the frontend."""
    position: int
    surface_form: str
    lemma_id: Optional[int]
    lemma_form: Optional[str]
    gloss_en: Optional[str]
    is_target: bool
    is_function_word: bool
    is_proper_name: bool
    is_punctuation: bool
    knowledge_state: str


@dataclass
class SentencePayload:
    sentence_id: int
    text: str
    translation_en: Optional[str]
    target_lemma_id: int
    source: Optional[str]
    page_id: Optional[int]
    words: list[WordRender] = field(default_factory=list)
    selection_reason: str = ""
    score: float = 0.0
    candidate_count: int = 0
    llm_candidate_count: int = 0
    selected_times_shown: int = 0
    selected_recently_shown: bool = False


@dataclass
class IntroCardPayload:
    """First-encounter teaching card for a never-shown acquiring lemma, or a
    re-teaching card for a stuck rescue lemma. Polyglot's intro card is
    intentionally lean compared to Alif's (no root, no wazn, no audio, no
    memory hooks) — what we have is the form + gloss + POS + an optional
    Modern↔Ancient cognate pointer (the one Greek-specific affordance).

    The session response carries these alongside ``sentences``; the frontend
    interleaves them before their target sentence and posts
    ``/api/reviews/experiment-intro-ack`` on display to stamp
    ``experiment_intro_shown_at``.
    """
    lemma_id: int
    lemma_form: str
    lemma_bare: str
    gloss_en: Optional[str]
    pos: Optional[str]
    intro_kind: str  # "new" | "rescue"
    times_seen: int
    cognate_lemma_id: Optional[int] = None
    cognate_lemma_form: Optional[str] = None


@dataclass
class SessionBundle:
    """A built session: sentences plus intro cards for content lemmas
    appearing in those sentences that haven't been introduced yet. Mirrors
    Alif's ``SentenceSessionOut`` (items + experiment_intro_cards) — the
    frontend interleaves intro cards before their target sentence.
    """
    sentences: list["SentencePayload"]
    intro_cards: list[IntroCardPayload] = field(default_factory=list)
    skipped_due_lemmas: list["SkippedDueLemma"] = field(default_factory=list)


@dataclass
class SkippedDueLemma:
    lemma_id: int
    queue: str
    reason: str


@dataclass
class _Scored:
    sentence: Sentence
    score: float
    comprehensibility: float
    scaffold_total: int
    scaffold_known: int
    page_first: bool                    # retained for back-compat / introspection
    selection_reason: str
    generated: bool = False             # primary sort tier: approved llm strictly beats book
    times_shown: int = 0                # for tie-break: prefer never-shown
    recently_shown: bool = False


# Intro card constants — ported from Alif. The dynamic-cap heuristic and
# rescue-cooldown values mirror ``sentence_selector._build_intro_cards``.
INTRO_CARDS_BASE = 4
INTRO_CARDS_MAX = 6
INTRO_NEW_CARDS_PER_SESSION = 6
RESCUE_MIN_SEEN = 4
RESCUE_MAX_ACCURACY = 0.50
RESCUE_COOLDOWN_DAYS = 7


def pick_sentence_for_lemma(
    db: Session,
    lemma_id: int,
    language_code: str,
    exclude_sentence_ids: Optional[set[int]] = None,
    exclude_sources: Optional[set[str]] = None,
    avoid_recently_shown: bool = False,
    session_scaffold_counts: Optional[dict[int, int]] = None,
) -> Optional[SentencePayload]:
    """Pick the best sentence covering ``lemma_id`` for the learner.

    Variant ``lemma_id`` is redirected to canonical at function entry.
    Returns ``None`` if no eligible Sentence row exists — caller should
    defer to generation (PR #4) or skip this lemma in the session.
    """
    canonical_id = resolve_canonical_lemma_id(db, lemma_id)
    if canonical_id is None:
        return None

    exclude = set(exclude_sentence_ids or ())
    source_exclude = set(exclude_sources or ())
    function_words = FUNCTION_WORD_SETS.get(language_code, set())

    target_lemma = (
        db.query(Lemma)
        .filter(Lemma.lemma_id == canonical_id, Lemma.language_code == language_code)
        .first()
    )
    if target_lemma is None or is_noncontent_lemma(
        target_lemma,
        language_code=language_code,
        function_words=function_words,
    ):
        return None

    candidate_rows: list[Sentence] = (
        db.query(Sentence)
        .join(SentenceWord, SentenceWord.sentence_id == Sentence.id)
        .filter(
            Sentence.language_code == language_code,
            Sentence.is_active.is_(True),
            Sentence.mappings_verified_at.isnot(None),
            SentenceWord.lemma_id == canonical_id,
        )
        .options(joinedload(Sentence.words))
        .distinct()
        .all()
    )
    candidates = [
        s for s in candidate_rows
        if s.id not in exclude and (s.source or "") not in source_exclude
    ]
    if avoid_recently_shown:
        candidates = [s for s in candidates if not _sentence_recently_shown(s)]
    if not candidates:
        return None
    candidate_count = len(candidates)
    llm_candidate_count = sum(1 for s in candidates if (s.source or "") in GENERATED_SOURCES)

    all_lemma_ids: set[int] = set()
    for sent in candidates:
        for sw in sent.words:
            if sw.lemma_id is not None:
                all_lemma_ids.add(sw.lemma_id)

    lemmas_by_id: dict[int, Lemma] = {}
    ulks_by_lemma: dict[int, UserLemmaKnowledge] = {}
    if all_lemma_ids:
        for lemma in db.query(Lemma).filter(Lemma.lemma_id.in_(all_lemma_ids)).all():
            lemmas_by_id[lemma.lemma_id] = lemma
        for ulk in (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id.in_(all_lemma_ids))
            .all()
        ):
            ulks_by_lemma[ulk.lemma_id] = ulk

    recent_page_view_ids = _load_recent_page_view_ids(db, candidates)

    scored: list[_Scored] = []
    for sent in candidates:
        result = _score_candidate(
            sentence=sent,
            target_canonical_id=canonical_id,
            lemmas_by_id=lemmas_by_id,
            ulks_by_lemma=ulks_by_lemma,
            function_words=function_words,
            recent_page_view_ids=recent_page_view_ids,
            session_scaffold_counts=session_scaffold_counts,
        )
        if result is not None:
            scored.append(result)

    if not scored:
        return None

    # Sort by source tier first (generated strictly beats book), then score
    # (desc), then prefer never-shown sentences (smaller times_shown), then
    # newer-created (larger id ≈ newer in practice for the polyglot DB since
    # ids are autoincrement). Reverse turns "generated first" and "smaller
    # times_shown wins" into the right order by booleans-as-ints / negation.
    scored.sort(
        key=lambda c: (c.generated, c.score, -c.times_shown, c.sentence.id),
        reverse=True,
    )
    best = scored[0]
    return _build_payload(
        best=best,
        target_canonical_id=canonical_id,
        lemmas_by_id=lemmas_by_id,
        ulks_by_lemma=ulks_by_lemma,
        function_words=function_words,
        candidate_count=candidate_count,
        llm_candidate_count=llm_candidate_count,
    )


def _load_recent_page_view_ids(db: Session, candidates: list[Sentence]) -> set[int]:
    """Return the set of ``Page.id`` values whose ``viewed_at`` is within the
    cooldown window. Pages whose ``viewed_at IS NULL`` aren't returned (the
    learner hasn't read them yet, so no cooldown).

    Batched to one query covering every candidate's page_id rather than a
    per-candidate Page lookup. Returns an empty set when no candidates have
    a page_id at all.
    """
    page_ids = {s.page_id for s in candidates if s.page_id is not None}
    if not page_ids:
        return set()
    cutoff = datetime.now(timezone.utc) - timedelta(days=PAGE_COOLDOWN_DAYS)
    rows = (
        db.query(Page.id)
        .filter(Page.id.in_(page_ids), Page.viewed_at.isnot(None))
        .all()
    )
    # SQLite stores datetimes as naive strings — re-fetch viewed_at and
    # compare in Python so the timezone story matches the rest of the codebase
    # (see CLAUDE.md "SQLite naive datetime pitfall"). One extra round-trip
    # but avoids cross-DB datetime semantics.
    if not rows:
        return set()
    detailed = (
        db.query(Page.id, Page.viewed_at)
        .filter(Page.id.in_({r[0] for r in rows}))
        .all()
    )
    recent: set[int] = set()
    for page_id, viewed_at in detailed:
        if viewed_at is None:
            continue
        if viewed_at.tzinfo is None:
            viewed_at = viewed_at.replace(tzinfo=timezone.utc)
        if viewed_at >= cutoff:
            recent.add(page_id)
    return recent


def _score_candidate(
    sentence: Sentence,
    target_canonical_id: int,
    lemmas_by_id: dict[int, Lemma],
    ulks_by_lemma: dict[int, UserLemmaKnowledge],
    function_words: set,
    recent_page_view_ids: Optional[set[int]] = None,
    session_scaffold_counts: Optional[dict[int, int]] = None,
) -> Optional[_Scored]:
    has_target = False
    scaffold_total = 0
    scaffold_known = 0
    # Per-content-scaffold-word signals for the diversity multipliers below.
    scaffold_reuse: list[int] = []     # times each scaffold word already used this session
    scaffold_times_seen: list[int] = []  # lifetime exposures (freshness)

    counts = session_scaffold_counts or {}
    for sw in sentence.words:
        if sw.lemma_id is None:
            continue
        if sw.lemma_id == target_canonical_id:
            has_target = True
            continue
        lemma = lemmas_by_id.get(sw.lemma_id)
        if lemma is None:
            continue
        if is_noncontent_lemma(lemma, function_words=function_words):
            continue

        scaffold_total += 1
        ulk = ulks_by_lemma.get(sw.lemma_id)
        if ulk is not None and ulk.knowledge_state in KNOWN_STATES:
            scaffold_known += 1
        scaffold_reuse.append(counts.get(sw.lemma_id, 0))
        scaffold_times_seen.append((ulk.times_seen or 0) if ulk is not None else 0)

    if not has_target:
        return None

    if scaffold_total == 0:
        comprehensibility = 1.0
        all_known = True
    else:
        comprehensibility = scaffold_known / scaffold_total
        all_known = (scaffold_known == scaffold_total)

    base = 0.3 + 0.7 * comprehensibility
    quality_multiplier = _quality_multiplier_for_sentence(sentence)
    if quality_multiplier <= 0:
        return None

    generated = _is_quality_approved_generated(sentence)

    # Page cooldown — a textbook sentence whose page was viewed within the last
    # PAGE_COOLDOWN_DAYS gets a strong multiplicative penalty so an older /
    # never-read page fallback outranks it. Pages never viewed (viewed_at IS
    # NULL) and pages viewed long ago receive no penalty. Only orders within
    # the book tier now — the generated tier wins outright regardless.
    recent_pages = recent_page_view_ids or set()
    page_recently_viewed = (
        sentence.page_id is not None and sentence.page_id in recent_pages
    )
    cooldown_bonus = RECENT_PAGE_PENALTY if page_recently_viewed else 1.0

    # `page_first` is retained for back-compat (selection_reason + dataclass
    # field) but no longer feeds the score. The ranking is generated-first
    # (the `generated` tier in the sort key), then this within-tier score.
    page_first = sentence.page_id is not None and all_known

    recently_shown = _sentence_recently_shown(sentence)
    sentence_recency_penalty = RECENT_SENTENCE_PENALTY if recently_shown else 1.0

    # Diversity multipliers (ported from Alif): within-session scaffold decay
    # spreads reuse across the session; freshness yields over-exposed scaffold
    # to rarely-seen words. Both are within-tier — the generated-first sort key
    # is unaffected, so material quality still leads.
    session_diversity = _session_decay_multiplier(scaffold_reuse)
    freshness = _scaffold_freshness_multiplier(scaffold_times_seen)

    score = (
        base
        * cooldown_bonus
        * quality_multiplier
        * sentence_recency_penalty
        * session_diversity
        * freshness
    )

    if page_recently_viewed:
        reason = "page_cooldown_fallback"
    elif generated:
        reason = "llm_fresh" if all_known else "llm_with_gaps"
    elif (sentence.source or "") in GENERATED_SOURCES:
        reason = "llm_unreviewed"
    elif page_first:
        reason = "page_first_all_known"
    elif all_known:
        reason = "all_scaffold_known"
    elif comprehensibility >= 0.6:
        reason = "comprehensible"
    else:
        reason = "best_available"
    if recently_shown:
        reason = f"{reason}_recent_repeat"

    return _Scored(
        sentence=sentence,
        score=score,
        comprehensibility=comprehensibility,
        scaffold_total=scaffold_total,
        scaffold_known=scaffold_known,
        page_first=page_first,
        selection_reason=reason,
        generated=generated,
        times_shown=sentence.times_shown or 0,
        recently_shown=recently_shown,
    )


def _sentence_recently_shown(sentence: Sentence) -> bool:
    shown_at = sentence.last_reading_shown_at
    if shown_at is None:
        return False
    if shown_at.tzinfo is None:
        shown_at = shown_at.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=SENTENCE_RECENCY_HOURS)
    return shown_at >= cutoff


def _build_payload(
    best: _Scored,
    target_canonical_id: int,
    lemmas_by_id: dict[int, Lemma],
    ulks_by_lemma: dict[int, UserLemmaKnowledge],
    function_words: set,
    candidate_count: int = 0,
    llm_candidate_count: int = 0,
) -> SentencePayload:
    sent = best.sentence
    words: list[WordRender] = []
    for sw in sorted(sent.words, key=lambda w: w.position):
        lemma = lemmas_by_id.get(sw.lemma_id) if sw.lemma_id else None
        ulk = ulks_by_lemma.get(sw.lemma_id) if sw.lemma_id else None
        is_punctuation = bool(sw.surface_form) and not any(
            c.isalpha() or c.isdigit() for c in sw.surface_form
        )
        is_proper_name = bool(lemma and lemma.word_category == "proper_name")
        is_function_word = bool(
            (lemma and lemma.word_category == "function_word")
            or (lemma and lemma.lemma_bare in function_words)
        )
        words.append(
            WordRender(
                position=sw.position,
                surface_form=sw.surface_form,
                lemma_id=sw.lemma_id,
                lemma_form=lemma.lemma_form if lemma else None,
                gloss_en=lemma.gloss_en if lemma else None,
                is_target=sw.lemma_id == target_canonical_id,
                is_function_word=is_function_word,
                is_proper_name=is_proper_name,
                is_punctuation=is_punctuation,
                knowledge_state=ulk.knowledge_state if ulk else "new",
            )
        )

    return SentencePayload(
        sentence_id=sent.id,
        text=sent.text,
        translation_en=sent.translation_en,
        target_lemma_id=target_canonical_id,
        source=sent.source,
        page_id=sent.page_id,
        words=words,
        selection_reason=best.selection_reason,
        score=best.score,
        candidate_count=candidate_count,
        llm_candidate_count=llm_candidate_count,
        selected_times_shown=best.times_shown,
        selected_recently_shown=best.recently_shown,
    )


def _fsrs_due_lemmas(
    db: Session,
    language_code: str,
    now: datetime,
    limit: int,
) -> list[tuple[UserLemmaKnowledge, Lemma, datetime]]:
    """FSRS-due rows: walk learning/known/lapsed cards and filter by due_dt.

    Mirrors the iteration shape used in the /api/reviews/due endpoint, which
    has to deserialize each fsrs_card_json because the due timestamp lives
    inside the JSON blob (a py-fsrs v6 detail).
    """
    candidates = (
        db.query(UserLemmaKnowledge, Lemma)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            Lemma.language_code == language_code,
            UserLemmaKnowledge.knowledge_state.in_(["learning", "known", "lapsed"]),
            UserLemmaKnowledge.fsrs_card_json.isnot(None),
        )
        .all()
    )
    rows: list[tuple[UserLemmaKnowledge, Lemma, datetime]] = []
    for ulk, lemma in candidates:
        if is_noncontent_lemma(lemma, language_code=language_code):
            continue
        card = parse_json_column(ulk.fsrs_card_json)
        due_str = card.get("due")
        if not due_str:
            continue
        try:
            due_dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if due_dt.tzinfo is None:
            due_dt = due_dt.replace(tzinfo=timezone.utc)
        if due_dt <= now:
            rows.append((ulk, lemma, due_dt))
    rows.sort(key=lambda t: t[2])
    return rows[:limit]


def _acquisition_due_lemmas(
    db: Session,
    language_code: str,
    now: datetime,
    limit: int,
) -> list[tuple[UserLemmaKnowledge, Lemma]]:
    function_words = FUNCTION_WORD_SETS.get(language_code, set())
    rows = (
        db.query(UserLemmaKnowledge, Lemma)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            Lemma.language_code == language_code,
            UserLemmaKnowledge.knowledge_state == "acquiring",
            UserLemmaKnowledge.acquisition_next_due.isnot(None),
            UserLemmaKnowledge.acquisition_next_due <= now,
        )
        .order_by(
            UserLemmaKnowledge.acquisition_box.asc(),
            UserLemmaKnowledge.acquisition_next_due.asc(),
        )
        .all()
    )
    return [
        (ulk, lemma)
        for ulk, lemma in rows
        if not is_noncontent_lemma(
            lemma,
            language_code=language_code,
            function_words=function_words,
        )
    ][:limit]


def _dynamic_intro_cap(db: Session, language_code: str | None = None) -> int:
    """Scale intro-card budget by un-introed acquiring backlog.

    Base 4, +1 per 15 un-introed acquiring lemmas, capped at 6 — Alif's
    2026-04-27 calibration after sessions filled with 10+ intros became
    unreadable. The hard daily-30 cap in ``start_acquisition`` still gates
    absolute net-new volume; this just spreads the in-session reveals.

    Scoped to ``language_code`` (joins Lemma) so a backlog in one Polyglot
    language doesn't inflate the in-session intro reveals of another.
    """
    q = db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.knowledge_state == "acquiring",
        (UserLemmaKnowledge.times_seen == 0) | (UserLemmaKnowledge.times_seen.is_(None)),
        UserLemmaKnowledge.experiment_intro_shown_at.is_(None),
    )
    if language_code is not None:
        q = q.join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id).filter(
            Lemma.language_code == language_code
        )
    unintro_count = q.count()
    return min(INTRO_CARDS_MAX, INTRO_CARDS_BASE + unintro_count // 15)


def _build_intro_cards(
    db: Session,
    sentences: list[SentencePayload],
    language_code: str | None = None,
) -> list[IntroCardPayload]:
    """Build first-encounter + rescue intro cards for lemmas in this session.

    Two categories (mirrors Alif's ``_build_intro_cards``):

    - **New** — ``knowledge_state='acquiring'``, ``times_seen=0``,
      ``experiment_intro_shown_at IS NULL``, ``times_correct=0``. These are
      lemmas the learner has never been reviewed on; first encounter must
      teach them before the gate fires.
    - **Rescue** — ``≥ RESCUE_MIN_SEEN`` reviews, accuracy below
      ``RESCUE_MAX_ACCURACY``, intro either never shown or shown more than
      ``RESCUE_COOLDOWN_DAYS`` ago. Re-teach for stuck words.

    Eligibility is restricted to lemmas appearing in the picked sentences —
    showing an intro card for a word the learner won't see in this session is
    wasted attention. Function words and proper names never get a card.
    """
    eligible_ids: set[int] = set()
    for sent in sentences:
        for w in sent.words:
            if w.lemma_id is None:
                continue
            if w.is_function_word or w.is_proper_name:
                continue
            eligible_ids.add(w.lemma_id)
    if not eligible_ids:
        return []

    now = datetime.now(timezone.utc)
    cooldown_cutoff = now - timedelta(days=RESCUE_COOLDOWN_DAYS)

    ulks = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id.in_(eligible_ids))
        .all()
    )
    lemmas = {
        lemma.lemma_id: lemma
        for lemma in db.query(Lemma).filter(Lemma.lemma_id.in_(eligible_ids)).all()
    }

    new_ids: list[int] = []
    rescue_ids: list[int] = []
    for ulk in ulks:
        if ulk.knowledge_state != "acquiring":
            continue
        lemma = lemmas.get(ulk.lemma_id)
        if lemma is None or is_noncontent_lemma(lemma):
            continue

        times_seen = ulk.times_seen or 0
        times_correct = ulk.times_correct or 0

        if (
            times_seen == 0
            and ulk.experiment_intro_shown_at is None
            and times_correct == 0
        ):
            new_ids.append(ulk.lemma_id)
            continue

        if times_seen >= RESCUE_MIN_SEEN:
            accuracy = times_correct / times_seen
            if accuracy < RESCUE_MAX_ACCURACY:
                if ulk.experiment_intro_shown_at is None:
                    rescue_ids.append(ulk.lemma_id)
                else:
                    shown_at = ulk.experiment_intro_shown_at
                    if shown_at.tzinfo is None:
                        shown_at = shown_at.replace(tzinfo=timezone.utc)
                    if shown_at < cooldown_cutoff:
                        rescue_ids.append(ulk.lemma_id)

    if not new_ids and not rescue_ids:
        return []

    # Order intro cards by the order their target sentence appears in the
    # session so the frontend can splice them in linearly without resorting.
    sentence_order: dict[int, int] = {}
    for idx, sent in enumerate(sentences):
        for w in sent.words:
            if w.lemma_id is not None and w.lemma_id not in sentence_order:
                sentence_order[w.lemma_id] = idx
    new_ids.sort(key=lambda lid: sentence_order.get(lid, 10**9))
    rescue_ids.sort(key=lambda lid: sentence_order.get(lid, 10**9))

    total_budget = INTRO_NEW_CARDS_PER_SESSION
    rescue_budget = _dynamic_intro_cap(db, language_code)

    selected_new = new_ids[:total_budget]
    remaining = max(0, total_budget - len(selected_new))
    selected_rescue = rescue_ids[: min(remaining, rescue_budget)]

    out: list[IntroCardPayload] = []
    for lid, kind in [(i, "new") for i in selected_new] + [(i, "rescue") for i in selected_rescue]:
        lemma = lemmas[lid]
        ulk = next((u for u in ulks if u.lemma_id == lid), None)
        cognate_id: Optional[int] = lemma.cognate_lemma_id
        cognate_form: Optional[str] = None
        if cognate_id is not None:
            cog = db.query(Lemma).filter(Lemma.lemma_id == cognate_id).first()
            if cog is not None:
                cognate_form = cog.lemma_form
        out.append(
            IntroCardPayload(
                lemma_id=lemma.lemma_id,
                lemma_form=lemma.lemma_form,
                lemma_bare=lemma.lemma_bare,
                gloss_en=lemma.gloss_en,
                pos=lemma.pos,
                intro_kind=kind,
                times_seen=(ulk.times_seen or 0) if ulk else 0,
                cognate_lemma_id=cognate_id,
                cognate_lemma_form=cognate_form,
            )
        )
    return out


def _content_scaffold_lemma_ids(payload: SentencePayload) -> list[int]:
    """Content (non-target, non-function, non-proper, non-punctuation) lemma
    ids in a chosen sentence — the words whose within-session reuse the
    scaffold-decay multiplier tracks."""
    out: list[int] = []
    for w in payload.words:
        if w.lemma_id is None or w.is_punctuation:
            continue
        if w.is_function_word or w.is_proper_name:
            continue
        if w.lemma_id == payload.target_lemma_id:
            continue
        out.append(w.lemma_id)
    return out


def _confirmation_sweep(
    db: Session,
    language_code: str,
    now: datetime,
    *,
    budget: int,
    exclude_sentence_ids: set[int],
    session_scaffold_counts: dict[int, int],
) -> list[SentencePayload]:
    """Greedy set-cover sweep that confirms assumed-known words never yet
    verified by exposure (``knowledge_state='known'`` / no FSRS card /
    ``confirmed_at IS NULL``).

    Reading is polyglot's breadth tool, but the learner reviews by default, so
    this lets a session actively confirm a *diverse* set of assumed-knowns:
    pick the reviewable sentence covering the most still-unconfirmed words, mark
    them covered, repeat — each pick deliberately targets different words.
    Textbook sentences ARE allowed here (unlike the acquiring gap path): they're
    authentic exposure and cover the broadest vocabulary, and a comprehensibility
    floor keeps out sentences dense with genuinely-unknown words. DB-only, so the
    ``build_session`` <1s / no-LLM invariant holds.
    """
    if budget <= 0:
        return []
    function_words = FUNCTION_WORD_SETS.get(language_code, set())

    # Whole-language lemma + ULK maps. Loaded by language (not by an IN over the
    # candidate word ids) to stay under SQLite's host-parameter limit and to
    # reuse them for both the unconfirmed set and payload construction.
    lemmas_by_id: dict[int, Lemma] = {
        l.lemma_id: l
        for l in db.query(Lemma).filter(Lemma.language_code == language_code).all()
    }
    ulks_by_lemma: dict[int, UserLemmaKnowledge] = {
        u.lemma_id: u
        for u in db.query(UserLemmaKnowledge)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(Lemma.language_code == language_code)
        .all()
    }

    unconfirmed: set[int] = set()
    for lid, lemma in lemmas_by_id.items():
        if lemma.canonical_lemma_id is not None:
            continue
        u = ulks_by_lemma.get(lid)
        if (
            u is not None
            and u.knowledge_state == "known"
            and u.fsrs_card_json is None
            and u.confirmed_at is None
            and not is_noncontent_lemma(
                lemma, language_code=language_code, function_words=function_words
            )
        ):
            unconfirmed.add(lid)
    if not unconfirmed:
        return []

    candidate_rows = (
        db.query(Sentence)
        .filter(
            Sentence.language_code == language_code,
            Sentence.is_active.is_(True),
            Sentence.mappings_verified_at.isnot(None),
        )
        .options(joinedload(Sentence.words))
        .all()
    )

    cover_by_id: dict[int, set[int]] = {}
    cover_target: dict[int, int] = {}
    sentence_by_id: dict[int, Sentence] = {}
    rank: dict[int, tuple] = {}
    for s in candidate_rows:
        if s.id in exclude_sentence_ids or _sentence_recently_shown(s):
            continue
        covered = {sw.lemma_id for sw in s.words if sw.lemma_id in unconfirmed}
        if not covered:
            continue
        total = known = 0
        for sw in s.words:
            if sw.lemma_id is None:
                continue
            lemma = lemmas_by_id.get(sw.lemma_id)
            if lemma is None or is_noncontent_lemma(lemma, function_words=function_words):
                continue
            total += 1
            u = ulks_by_lemma.get(sw.lemma_id)
            if u is not None and u.knowledge_state in KNOWN_STATES:
                known += 1
        comp = 1.0 if total == 0 else known / total
        if comp < CONFIRMATION_MIN_COMPREHENSIBILITY:
            continue
        cover_by_id[s.id] = covered
        cover_target[s.id] = min(covered)
        sentence_by_id[s.id] = s
        # Tie-break after coverage gain: more comprehensible, then generated
        # (cleaner than book prose), then fresher (fewer prior shows).
        rank[s.id] = (
            round(comp, 3),
            1 if _is_quality_approved_generated(s) else 0,
            -(s.times_shown or 0),
            s.id,
        )

    if not cover_by_id:
        return []

    chosen_ids = _greedy_cover(cover_by_id, budget, rank)

    payloads: list[SentencePayload] = []
    for sid in chosen_ids:
        s = sentence_by_id[sid]
        scored = _Scored(
            sentence=s,
            score=0.0,
            comprehensibility=rank[sid][0],
            scaffold_total=0,
            scaffold_known=0,
            page_first=False,
            selection_reason="confirmation_sweep",
            generated=_is_quality_approved_generated(s),
            times_shown=s.times_shown or 0,
            recently_shown=False,
        )
        payload = _build_payload(
            best=scored,
            target_canonical_id=cover_target[sid],
            lemmas_by_id=lemmas_by_id,
            ulks_by_lemma=ulks_by_lemma,
            function_words=function_words,
            candidate_count=len(cover_by_id),
            llm_candidate_count=0,
        )
        payloads.append(payload)
        for lid in _content_scaffold_lemma_ids(payload):
            session_scaffold_counts[lid] = session_scaffold_counts.get(lid, 0) + 1
    return payloads


def build_session(
    db: Session,
    language_code: str,
    limit: int = DEFAULT_SESSION_LIMIT,
) -> SessionBundle:
    """Assemble a session: due-gap sentences plus a reserved confirmation sweep.

    Gap phase (the majority of the session): pick one sentence per due lemma —
    acquisition-due first (Box 1 → 2 → 3, then by due time), then FSRS-due
    (oldest due first). Within-session scaffold decay (threaded via
    ``session_scaffold_counts``) keeps the same high-frequency known words from
    appearing in every card.

    Confirmation phase (``CONFIRMATION_SESSION_SHARE`` of ``limit``, reserved):
    greedy set-cover over reviewable sentences to confirm a diverse set of
    assumed-known words never verified by exposure. The reserve only binds once
    enough due gaps exist to fill the rest; on a thin-gap day confirmation
    expands into the empty slots, and on a thin-unconfirmed day deferred gaps
    backfill the reserve, so the session is never short when work exists.

    A sentence is never repeated within a session, and sentences shown in the
    last ``SENTENCE_RECENCY_HOURS`` are skipped. Textbook fallbacks are capped
    per session on the gap path (acquiring lemmas get none at all).
    """
    if not db.query(Language).filter(Language.code == language_code).first():
        return SessionBundle(sentences=[])

    now = datetime.now(timezone.utc)
    confirm_reserved = _confirmation_budget(limit)
    gap_cap = limit - confirm_reserved

    used_sentence_ids: set[int] = set()
    skipped_due: list[SkippedDueLemma] = []
    textbook_fallback_limit = _textbook_fallback_limit(limit)
    textbook_fallback_count = 0
    # How often each known scaffold word has already been placed this session —
    # drives the within-session scaffold-decay multiplier in the picker.
    session_scaffold_counts: dict[int, int] = {}

    def _pick_for_session(
        lemma_id: int, *, allow_textbook: bool
    ) -> Optional[SentencePayload]:
        nonlocal textbook_fallback_count
        # Acquiring lemmas never get textbook fallbacks (see TEXTBOOK_FALLBACK_
        # SOURCES docstring). FSRS-carded lemmas can, up to the session cap.
        if not allow_textbook or textbook_fallback_count >= textbook_fallback_limit:
            exclude_sources: Optional[set[str]] = set(TEXTBOOK_FALLBACK_SOURCES)
        else:
            exclude_sources = None
        payload = pick_sentence_for_lemma(
            db,
            lemma_id=lemma_id,
            language_code=language_code,
            exclude_sentence_ids=used_sentence_ids,
            exclude_sources=exclude_sources,
            avoid_recently_shown=True,
            session_scaffold_counts=session_scaffold_counts,
        )
        if payload is not None and (payload.source or "") in TEXTBOOK_FALLBACK_SOURCES:
            textbook_fallback_count += 1
        return payload

    # ── Gap phase ── pick up to the full `limit` in priority order, so leftover
    # gaps can backfill the session if the confirmation sweep can't fill its
    # reserve. Only the first `gap_cap` are committed up front; the rest are
    # overflow consulted after the sweep.
    gap_payloads: list[SentencePayload] = []

    def _commit_gap(payload: SentencePayload) -> None:
        used_sentence_ids.add(payload.sentence_id)
        gap_payloads.append(payload)
        for lid in _content_scaffold_lemma_ids(payload):
            session_scaffold_counts[lid] = session_scaffold_counts.get(lid, 0) + 1

    acquiring = _acquisition_due_lemmas(db, language_code, now, limit)
    for ulk, lemma in acquiring:
        if len(gap_payloads) >= limit:
            break
        payload = _pick_for_session(lemma.lemma_id, allow_textbook=False)
        if payload is None:
            skipped_due.append(SkippedDueLemma(
                lemma_id=lemma.lemma_id,
                queue="acquisition",
                reason="no_eligible_non_recent_sentence",
            ))
            continue
        _commit_gap(payload)

    remaining = max(0, limit - len(gap_payloads))
    fsrs = _fsrs_due_lemmas(db, language_code, now, remaining) if remaining else []
    for ulk, lemma, _due in fsrs:
        if len(gap_payloads) >= limit:
            break
        payload = _pick_for_session(lemma.lemma_id, allow_textbook=True)
        if payload is None:
            skipped_due.append(SkippedDueLemma(
                lemma_id=lemma.lemma_id,
                queue="fsrs",
                reason="no_eligible_non_recent_sentence",
            ))
            continue
        _commit_gap(payload)

    # ── Allocate ── gaps keep the majority (up to gap_cap); the rest is the
    # confirmation reserve, expanded with any slack the gap phase left empty.
    selected: list[SentencePayload] = list(gap_payloads[:gap_cap])
    selected_ids = {p.sentence_id for p in selected}

    confirmation = _confirmation_sweep(
        db,
        language_code,
        now,
        budget=limit - len(selected),
        exclude_sentence_ids={p.sentence_id for p in gap_payloads},
        session_scaffold_counts=session_scaffold_counts,
    )
    for payload in confirmation:
        selected.append(payload)
        selected_ids.add(payload.sentence_id)

    # Backfill leftover confirmation slots with deferred gap sentences, so a thin
    # unconfirmed pool never shrinks the session below the real due backlog.
    for payload in gap_payloads[gap_cap:]:
        if len(selected) >= limit:
            break
        if payload.sentence_id in selected_ids:
            continue
        selected.append(payload)
        selected_ids.add(payload.sentence_id)

    intro_cards = _build_intro_cards(db, selected, language_code)
    return SessionBundle(
        sentences=selected,
        intro_cards=intro_cards,
        skipped_due_lemmas=skipped_due,
    )


def _textbook_fallback_limit(session_limit: int) -> int:
    if session_limit <= 0:
        return 0
    return min(TEXTBOOK_FALLBACK_MAX_PER_SESSION, max(1, session_limit // 5))
