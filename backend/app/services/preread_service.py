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
* Sense check — words matched to an existing lemma are checked against how
  the pages use them. A stored gloss that lacks the page's sense is widened
  (``regloss``); a page word that is a different lexeme with the same spelling
  (``عَمَدَ`` "to set about" vs ``عَمَّدَ`` "to baptize") is re-routed through the
  discover sense gate as that lexeme, and a shaky "match" of that kind becomes
  a word to learn instead of a refresh.
* ``names``  — proper names in the pages, glossed and transliterated, shown as
  a primer. Name lemmas are inert by design (no cards), so they are not scheduled.

``analyze_pages`` is read-only (plus LLM calls with no dirty session);
writes happen in the router's job runner.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import Lemma, UserLemmaKnowledge
from app.services.fsrs_service import card_retrievability, parse_json_column

logger = logging.getLogger(__name__)

PREREAD_SOURCE = "preread"
SHAKY_RETRIEVABILITY = 0.90
# Days after a batch during which its words are scheduled first.
PRIORITY_DAYS = 4
# Known words count against discovery's cap too, so it must cover a whole batch.
MAX_CANDIDATES = 4000
LEARN_PER_PAGE = 6
LEARN_MIN = 15
LEARN_MAX = 90
SHAKY_PER_PAGE = 4
SHAKY_MAX = 60
# Names the learner meets in the pages are practised like vocabulary: they slow
# reading as much as unknown words do (learner decision 2026-11-15).
NAMES_MAX = 30
# A practised name leaves the inert ``proper_name`` category so every gate that
# skips names (selection, credit, scaffold counts) treats it as a normal word.
PRACTICED_NAME_CATEGORY = "practiced_name"
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


def _dedupe(words: list[dict]) -> list[dict]:
    """Two surface keys can resolve to one stored lemma; keep one entry and add
    up the counts."""
    out: list[dict] = []
    by_id: dict[int, dict] = {}
    for w in words:
        lid = w.get("lemma_id")
        if lid is None:
            out.append(w)
            continue
        prev = by_id.get(lid)
        if prev is None:
            by_id[lid] = w
            out.append(w)
            continue
        prev["count_in_text"] = (prev.get("count_in_text") or 0) + (w.get("count_in_text") or 0)
        prev["surface_forms"] = list(prev.get("surface_forms") or []) + [
            f for f in (w.get("surface_forms") or []) if f not in (prev.get("surface_forms") or [])
        ]
    return out


def active_priority_ids(db: Session, now: datetime | None = None) -> set[int]:
    """Lemma ids from pre-read batches finished in the last
    ``PRIORITY_DAYS``: the words and names entered and the shaky words refreshed. The
    session builder schedules and boosts these while the pages are being read."""
    from app.models import PrereadSet

    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=PRIORITY_DAYS)).replace(tzinfo=None)
    rows = (
        db.query(PrereadSet.result_json)
        .filter(PrereadSet.status == "done", PrereadSet.finished_at >= since)
        .all()
    )
    ids: set[int] = set()
    for (result,) in rows:
        result = parse_json_column(result)
        if not isinstance(result, dict):
            continue
        for w in (result.get("learn") or []) + (result.get("names") or []):
            if isinstance(w, dict) and w.get("added") and w.get("lemma_id"):
                ids.add(int(w["lemma_id"]))
        for w in result.get("shaky") or []:
            if isinstance(w, dict) and w.get("lemma_id"):
                ids.add(int(w["lemma_id"]))
    return ids


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
        "(a different word with the same spelling), rate 0. If the word is used "
        "here as (part of) a person's or place's name, rate 0.\n"
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


_SENSE_SCHEMA = {
    "type": "object",
    "properties": {
        "mismatches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["broader", "different"]},
                    "lemma_ar": {"type": "string"},
                    "gloss_en": {"type": "string"},
                    "pos": {"type": "string"},
                },
                "required": ["index", "verdict", "gloss_en"],
            },
        }
    },
    "required": ["mismatches"],
}
# A widened gloss stays a dictionary gloss, not a definition.
_MAX_GLOSS_CHARS = 90


def _vocalized_key(ar: str) -> str:
    """Citation form for identity comparison: keep letters and short vowels,
    drop tanwin, final case vowels and tatweel, so قَرْنٌ == قَرْن."""
    import unicodedata

    s = unicodedata.normalize("NFC", ar.strip()).replace("\u0640", "")
    s = "".join(ch for ch in s if ch not in "\u064b\u064c\u064d")
    while s and s[-1] in "\u064e\u064f\u0650\u0652":
        s = s[:-1]
    return s


def _gloss_words(g: str) -> set[str]:
    import re

    return {w for w in re.findall(r"[a-z]+", g.casefold()) if len(w) > 2 and w not in {"the", "and", "for"}}


def _is_widening(old: str, new: str) -> bool:
    """A widened gloss is plain English that keeps some of the stored meaning
    and actually changes something."""
    import re

    if new.strip().casefold() == old.strip().casefold():
        return False
    if "=" in new or re.search(r"[\u0600-\u06ff]", new):
        return False
    old_words = _gloss_words(old)
    return not old_words or bool(old_words & _gloss_words(new))


def check_senses(text: str, items: list[dict]) -> dict[int, dict]:
    """One LLM pass: does each stored gloss cover how the pages use the word?

    ``items``: [{index, lemma_ar, gloss_en, pos, forms}]. Returns {index: {verdict,
    gloss_en, lemma_ar?, pos?}} for mismatches only; empty on failure, and the
    plan then keeps the stored glosses."""
    if not items:
        return {}
    from app.services.llm import generate_completion

    lines = [
        f'{it["index"]}. {it["lemma_ar"]} ({it.get("pos") or "?"}) — {it.get("gloss_en") or ""}'
        + (f' [in the pages: {", ".join(it["forms"][:5])}]' if it.get("forms") else "")
        for it in items
    ]
    prompt = (
        "An adult learner of Arabic is about to read the pages below. Each listed "
        "word was matched to an entry in their vocabulary, shown with its stored "
        "English gloss and the spellings found in the pages. Check every entry "
        "against how those spellings are actually used, and report only the entries "
        "where a reader relying on the stored gloss would misunderstand the passage:\n"
        '- "broader": the same word (same vocalized citation form and part of '
        "speech), but the stored gloss lacks the meaning used here (قَرْن stored as "
        '"horn" where the pages mean "century"). Give gloss_en: a short dictionary '
        "gloss that puts the meaning used here first and keeps the stored meanings, "
        'e.g. "century; horn". It must stay generally usable, not tied to this book.\n'
        '- "different": the pages use a different word that merely shares the '
        "spelling: another verb form, another vocalization, another part of speech "
        "or an unrelated homograph (pages have عَمَدَ إلى \"to set about\" but the entry "
        'is عَمَّدَ "to baptize"; pages have the plural أعراق of عِرْق "race" but the '
        'entry is the verb عَرَقَ). Give lemma_ar: the fully vocalized citation form '
        "of the word used here (singular noun, past-tense 3ms verb, no ال), pos, "
        "and gloss_en: a short general dictionary gloss that includes the meaning "
        "used here.\n"
        "Do not report entries that fit, rewordings or reorderings of a gloss that "
        "already covers the use, or words used as part of a name. Keep glosses "
        "under 80 characters.\n\n"
        f"PAGES:\n{text[:_TEXT_CHAR_LIMIT]}\n\nENTRIES:\n" + "\n".join(lines)
    )
    try:
        res = generate_completion(
            prompt=prompt,
            system_prompt="You are an experienced Arabic lexicographer and reading teacher.",
            json_schema=_SENSE_SCHEMA,
            temperature=0.0,
            model_override="claude_sonnet",
            task_type="preread_sense_check",
        )
    except Exception as e:
        logger.warning("preread sense check failed: %s", e)
        return {}
    rows = res.get("mismatches", []) if isinstance(res, dict) else []
    by_index = {it["index"]: it for it in items}
    out: dict[int, dict] = {}
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict) or r.get("index") not in by_index:
            continue
        item = by_index[r["index"]]
        gloss = (r.get("gloss_en") or "").strip()
        verdict = r.get("verdict")
        if not gloss or len(gloss) > _MAX_GLOSS_CHARS or verdict not in ("broader", "different"):
            continue
        if verdict == "broader" and not _is_widening(item.get("gloss_en") or "", gloss):
            continue
        if verdict == "different":
            new_ar = (r.get("lemma_ar") or "").strip()
            # The same vocalized citation form is the same lexeme, not a homograph.
            if not new_ar or _vocalized_key(new_ar) == _vocalized_key(item.get("lemma_ar") or ""):
                continue
        out[r["index"]] = {
            "verdict": verdict,
            "gloss_en": gloss,
            "lemma_ar": (r.get("lemma_ar") or "").strip() or None,
            "pos": (r.get("pos") or "").strip() or None,
        }
    return out


def _apply_senses(text: str, learn: list[dict], shaky: list[dict]) -> tuple[list[dict], list[dict], int]:
    """Annotate matched words with the sense check. A widened gloss is stored
    on the word as ``regloss`` (written by the router); a different lexeme
    replaces the word's identity so the router's add re-routes it, and leaves
    the shaky list for the learn list. Returns (learn, shaky, n_checked)."""
    checked = [w for w in learn + shaky if w.get("lemma_id")]
    verdicts = check_senses(
        text,
        [{"index": i, "lemma_ar": w["lemma_ar"], "gloss_en": w.get("gloss_en"), "pos": w.get("pos"),
          "forms": w.get("surface_forms") or [w.get("surface") or w["lemma_ar"]]}
         for i, w in enumerate(checked)],
    )
    for i, w in enumerate(checked):
        v = verdicts.get(i)
        if not v:
            continue
        if v["verdict"] == "broader":
            w["regloss"] = {"from": w.get("gloss_en"), "to": v["gloss_en"]}
            w["gloss_en"] = v["gloss_en"]
        else:
            w["sense_from"] = {"lemma_id": w["lemma_id"], "lemma_ar": w["lemma_ar"],
                               "gloss_en": w.get("gloss_en")}
            w.update(lemma_id=None, lemma_ar=v["lemma_ar"], gloss_en=v["gloss_en"],
                     knowledge_state=None)
            if v["pos"]:
                w["pos"] = v["pos"]
    moved = [w for w in shaky if w.get("sense_from")]
    return learn + moved, [w for w in shaky if not w.get("sense_from")], len(checked)


def regloss(db: Session, words: list[dict]) -> list[int]:
    """Write the widened glosses from the sense check. Compare-and-set on the
    old gloss so a concurrent edit wins. Memory hooks built on the old sense
    are cleared so they can be regenerated. Caller commits."""
    changed = []
    for w in words:
        rg = w.get("regloss")
        if not rg or not w.get("lemma_id"):
            continue
        lemma = db.get(Lemma, w["lemma_id"])
        if lemma is None or (lemma.gloss_en or "") != (rg["from"] or ""):
            continue
        lemma.gloss_en = rg["to"]
        if lemma.memory_hooks_json:
            lemma.memory_hooks_json = None
            changed.append(lemma.lemma_id)
        w["reglossed"] = True
    return changed


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

    words = _dedupe(words)
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

    learn, shaky, n_checked = _apply_senses(text, learn, shaky)

    names = _merge_names(names)
    for group in (learn, shaky):
        group.sort(key=lambda w: (w.get("first_page") or 999, -w["score"]))

    stats.update(
        new_candidates=len(new), learn=len(learn), shaky=len(shaky), names=len(names),
        importance_rated=len(importance),
        sense_checked=n_checked,
        reglossed=sum(1 for w in learn + shaky if w.get("regloss")),
        sense_rerouted=sum(1 for w in learn if w.get("sense_from")),
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


def practice_names(db: Session, names: list[dict]) -> dict:
    """Enter the pages' names as learner picks, like the learn words.

    A stored ``proper_name`` lemma is moved to ``practiced_name``; a name with
    no lemma yet is created in that category. A name whose spelling resolves to
    an ordinary word is left alone: re-labelling that lemma would turn a
    real word into a name. Commits per name. Returns the ids that still need
    quality gates (``gate_ids``) and every practised id (``ids``)."""
    from app.routers.discover import _normalize
    from app.services.canonical_resolution import resolve_canonical_lemma_id
    from app.services.lemma_quality import assign_frequency_rank
    from app.services.sentence_validator import build_comprehensive_lemma_lookup, lookup_lemma_citation
    from app.services.word_selector import introduce_word

    gate_ids: list[int] = []
    ids: list[int] = []
    for w in names[:NAMES_MAX]:
        try:
            bare = _normalize(w.get("lemma_ar_bare") or "")
            if not bare:
                continue
            lemma_id = w.get("lemma_id")
            if not lemma_id:
                lookup = build_comprehensive_lemma_lookup(db)
                lemma_id = lookup_lemma_citation(bare, lookup, original_bare=w.get("lemma_ar") or bare)
            lemma = None
            if lemma_id:
                lemma = db.get(Lemma, resolve_canonical_lemma_id(db, int(lemma_id)))
            if lemma is not None:
                if lemma.word_category not in ("proper_name", PRACTICED_NAME_CATEGORY):
                    w["added"] = False
                    w["skipped"] = "ordinary word"
                    continue
                lemma.word_category = PRACTICED_NAME_CATEGORY
            else:
                gloss = (w.get("gloss_en") or "").strip()
                if not gloss:
                    w["added"] = False
                    w["skipped"] = "no gloss"
                    continue
                lemma = Lemma(
                    lemma_ar=w.get("lemma_ar") or bare,
                    lemma_ar_bare=bare,
                    gloss_en=gloss,
                    pos="noun_prop",
                    word_category=PRACTICED_NAME_CATEGORY,
                    transliteration_ala_lc=w.get("transliteration"),
                    source=PREREAD_SOURCE,
                )
                db.add(lemma)
                db.flush()
                assign_frequency_rank(lemma)
            res = introduce_word(
                db, lemma.lemma_id, source=PREREAD_SOURCE,
                due_immediately=True, enforce_daily_cap=True, learner_pick=True,
            )
            needs_gates = lemma.gates_completed_at is None
            db.commit()
            if needs_gates:
                gate_ids.append(lemma.lemma_id)
            w["lemma_id"] = lemma.lemma_id
            w["added"] = res.get("state") == "acquiring"
            ids.append(lemma.lemma_id)
        except Exception as e:
            db.rollback()
            w["added"] = False
            w["error"] = str(e)[:200]
            logger.warning("preread name failed for %s: %s", w.get("lemma_ar_bare"), e)
    return {"gate_ids": gate_ids, "ids": ids}
