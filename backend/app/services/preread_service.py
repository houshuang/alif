"""Pre-reading: prepare the learner for pages they are about to read.

The learner photographs the next 10–20 pages of a book. This module turns the
OCR'd pages into an automatic plan, with no picking step:

* ``learn``  — unknown words worth learning before reading, ranked by how much
  of these pages they unlock (count here), how much the meaning depends on them
  (one LLM importance rating over the whole text) and general frequency.
  They enter acquisition at once as learner picks (source ``preread``).
* ``shaky``  — words already in FSRS that occur in the pages and are likely
  forgotten (retrievability < ``SHAKY_RETRIEVABILITY``, relearning, or still in
  FSRS learning steps). Their next review is brought forward to now, once.
* ``names``  — proper names in the pages, glossed and transliterated, shown as
  a primer. Name lemmas are inert by design (no cards), so they are not scheduled.

``analyze_pages`` is read-only (plus LLM calls with no dirty session);
writes happen in the router's job runner.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Lemma, UserLemmaKnowledge
from app.services.fsrs_service import card_retrievability, parse_json_column

logger = logging.getLogger(__name__)

PREREAD_SOURCE = "preread"
SHAKY_RETRIEVABILITY = 0.90
# Known words count against discovery's cap too, so it must cover a whole batch.
MAX_CANDIDATES = 4000
LEARN_PER_PAGE = 6
LEARN_MIN = 15
LEARN_MAX = 90
SHAKY_PER_PAGE = 4
SHAKY_MAX = 60
# A word must clear this score to be learned, however many slots remain.
MIN_LEARN_SCORE = 3.0
_TEXT_CHAR_LIMIT = 24000

_NEW_STATES = {None, "new", "encountered"}
_FSRS_STATES = {"learning", "known", "lapsed"}

_IMPORTANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "ratings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "importance": {"type": "integer"},
                },
                "required": ["index", "importance"],
            },
        }
    },
    "required": ["ratings"],
}


def frequency_bonus(rank: int | None) -> float:
    """General-frequency value: common words pay off beyond this book."""
    if rank is None:
        return 0.0
    if rank <= 1000:
        return 3.0
    if rank <= 3000:
        return 2.5
    if rank <= 6000:
        return 2.0
    if rank <= 12000:
        return 1.2
    if rank <= 25000:
        return 0.6
    return 0.0


def learn_score(count: int, rank: int | None, importance: int | None) -> float:
    imp = 1 if importance is None else max(0, min(3, importance))
    return round(1.5 * imp + 1.5 * math.log2(1 + max(count, 1)) + frequency_bonus(rank), 3)


def worth_it(importance: int | None) -> bool:
    """Importance 0 means decorative, guessable, or a wrong-sense match (the
    lookup found a same-spelling word); drop it. Keep all if rating failed."""
    return importance is None or importance > 0


def learn_quota(n_pages: int) -> int:
    return max(LEARN_MIN, min(LEARN_MAX, LEARN_PER_PAGE * max(n_pages, 1)))


def shaky_quota(n_pages: int) -> int:
    return min(SHAKY_MAX, SHAKY_PER_PAGE * max(n_pages, 1))


def _first_page(surfaces: list[str], page_tokens: list[set[str]]) -> int | None:
    forms = set(surfaces)
    for i, toks in enumerate(page_tokens):
        if toks & forms:
            return i + 1
    return None


def _merge_names(names: list[dict]) -> list[dict]:
    """One entry per name: the same name can arrive under two keys (with and
    without the article, or as a stored lemma and an OOV guess)."""
    merged: dict[str, dict] = {}
    for w in names:
        key = (w.get("gloss_en") or "").split("(")[0].strip().lower() or w["lemma_ar_bare"]
        prev = merged.get(key)
        if prev is None:
            merged[key] = dict(w)
            continue
        prev["count_in_text"] = (prev.get("count_in_text") or 0) + (w.get("count_in_text") or 0)
        pages = [p for p in (prev.get("first_page"), w.get("first_page")) if p]
        prev["first_page"] = min(pages) if pages else None
    return sorted(merged.values(), key=lambda w: -(w.get("count_in_text") or 0))


def rate_importance(text: str, items: list[dict]) -> dict[int, int]:
    """One LLM pass: how much does a reader lose without each word?

    ``items``: [{index, lemma_ar, gloss_en}]. Returns {index: 0..3}; empty on
    failure, and callers then fall back to count + frequency only."""
    if not items:
        return {}
    from app.services.llm import generate_completion

    lines = [
        f'{it["index"]}. {it["lemma_ar"]}' + (f' — {it["gloss_en"]}' if it.get("gloss_en") else "")
        for it in items
    ]
    prompt = (
        "An adult learner of Arabic is about to read the pages below. For each "
        "listed word, rate how much a reader who does NOT know it loses when "
        "reading these pages:\n"
        "3 = essential: the passage's meaning breaks without it (topic words, main "
        "actions, recurring key terms)\n"
        "2 = important: a sentence's meaning depends on it\n"
        "1 = helpful detail\n"
        "0 = decorative, or easy to guess from context\n"
        "If the English gloss does not fit how the word is used in these pages "
        "(a different word with the same spelling), rate 0.\n"
        "Rate every listed index exactly once.\n\n"
        f"PAGES:\n{text[:_TEXT_CHAR_LIMIT]}\n\nWORDS:\n" + "\n".join(lines)
    )
    try:
        res = generate_completion(
            prompt=prompt,
            system_prompt="You are an experienced Arabic reading teacher.",
            json_schema=_IMPORTANCE_SCHEMA,
            temperature=0.0,
            model_override="claude_sonnet",
            task_type="preread_importance",
        )
    except Exception as e:
        logger.warning("preread importance rating failed: %s", e)
        return {}
    ratings = res.get("ratings", []) if isinstance(res, dict) else res
    out: dict[int, int] = {}
    for r in ratings if isinstance(ratings, list) else []:
        if isinstance(r, dict) and isinstance(r.get("index"), int):
            try:
                out[r["index"]] = max(0, min(3, int(r.get("importance", 1))))
            except (TypeError, ValueError):
                continue
    return out


def analyze_pages(db: Session, pages: list[str]) -> dict:
    """Build the automatic plan for these pages. Read-only."""
    from app.routers.discover import _ARABIC_TOKEN, discover_words_in_text

    pages = [p for p in pages if (p or "").strip()]
    text = "\n\n".join(pages)
    page_tokens = [set(_ARABIC_TOKEN.findall(p)) for p in pages]
    words = discover_words_in_text(
        db, text, count=MAX_CANDIDATES, include_oov=True, include_learning=True,
        max_words=MAX_CANDIDATES,
    )
    db.rollback()  # end the read transaction before the LLM call

    ids = {w["lemma_id"] for w in words if w.get("lemma_id")}
    lemmas = {l.lemma_id: l for l in db.query(Lemma).filter(Lemma.lemma_id.in_(ids)).all()} if ids else {}
    ulks = (
        {u.lemma_id: u for u in db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id.in_(ids)).all()}
        if ids else {}
    )
    now = datetime.now(timezone.utc)

    new, shaky, names = [], [], []
    stats = {"pages": len(pages), "candidates": len(words), "acquiring": 0,
             "solid": 0, "parked": 0, "no_gloss": 0}
    for w in words:
        lemma = lemmas.get(w.get("lemma_id"))
        w["first_page"] = _first_page(w.get("surface_forms") or [w["surface"]], page_tokens)
        is_name = bool(w.get("is_proper_noun")) or (
            lemma is not None and lemma.word_category == "proper_name"
        )
        if is_name:
            names.append(w)
            continue
        if lemma is not None and lemma.word_category == "onomatopoeia":
            continue
        ulk = ulks.get(w.get("lemma_id"))
        state = ulk.knowledge_state if ulk else None
        if state in _NEW_STATES:
            if not (w.get("gloss_en") or "").strip():
                stats["no_gloss"] += 1
                continue
            new.append(w)
        elif state == "acquiring":
            stats["acquiring"] += 1
        elif state in _FSRS_STATES:
            if ulk.attention_disposition != "maintain":
                stats["parked"] += 1
                continue
            r = card_retrievability(ulk.fsrs_card_json, now)
            if state == "known" and (r is None or r >= SHAKY_RETRIEVABILITY):
                stats["solid"] += 1
                continue
            w["retrievability"] = round(r, 3) if r is not None else None
            shaky.append(w)
        else:
            stats["parked"] += 1

    rated = new + shaky
    importance = rate_importance(
        text,
        [{"index": i, "lemma_ar": w["lemma_ar"], "gloss_en": w.get("gloss_en")}
         for i, w in enumerate(rated)],
    )
    for i, w in enumerate(rated):
        w["importance"] = importance.get(i)
        w["score"] = learn_score(w.get("count_in_text") or 1, w.get("freq_rank"), w["importance"])

    new.sort(key=lambda w: -w["score"])
    quota = learn_quota(len(pages))
    def keep(w):
        return worth_it(w["importance"])

    learn = [w for w in new if w["score"] >= MIN_LEARN_SCORE and keep(w)][:quota]

    shaky = [w for w in shaky if keep(w)]
    shaky.sort(key=lambda w: (-w["score"], w.get("retrievability") or 0))
    shaky = shaky[: shaky_quota(len(pages))]

    names = _merge_names(names)
    for group in (learn, shaky):
        group.sort(key=lambda w: (w.get("first_page") or 999, -w["score"]))

    stats.update(
        new_candidates=len(new), learn=len(learn), shaky=len(shaky), names=len(names),
        importance_rated=len(importance),
    )
    return {"learn": learn, "shaky": shaky, "names": names, "stats": stats}


def pull_forward(db: Session, lemma_ids: list[int]) -> list[int]:
    """Make these FSRS words due now (one early review each). Returns the ids
    whose due date actually moved. Caller commits."""
    now = datetime.now(timezone.utc)
    moved = []
    for ulk in db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id.in_(lemma_ids)).all():
        if ulk.knowledge_state not in _FSRS_STATES:
            continue
        card = parse_json_column(ulk.fsrs_card_json)
        if not isinstance(card, dict) or not card.get("due"):
            continue
        try:
            due = datetime.fromisoformat(str(card["due"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due <= now:
            continue
        card = dict(card)
        card["due"] = now.isoformat()
        ulk.fsrs_card_json = card
        moved.append(ulk.lemma_id)
    return moved
