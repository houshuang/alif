"""Discover vocabulary from external Arabic text (e.g. the Dragoman magazine).

Two jobs:
  POST /api/discover/words  — given a block of Arabic text, return the highest-value
       lemmas that are NOT yet in Alif's pool, ranked by MSA frequency, each glossed.
  POST /api/discover/add[-batch] — create the chosen lemma(s), introduce them into the
       acquisition pipeline immediately (explicit user adds bypass the daily intro cap),
       and (in the background) run the quality gates + generate review material. Fast
       write up front; the LLM-heavy gating never holds the write lock (own session in
       a BackgroundTask).

This is the Dragoman → Alif integration: a reader sees an Arabic essay in the magazine,
Dragoman asks this endpoint which words are worth learning, and renders "add to Alif"
buttons that POST back here.

Word identity goes through the production-hardened lookup path
(`build_comprehensive_lemma_lookup` + `lookup_lemma`): clitic stripping, CAMeL
disambiguation, collision handling, and variant→canonical resolution — the same path
the corpus importer uses. We never hand-roll tokenize+normalize+classify here (that
surface-only shortcut leaks clitic-attached and variant forms — 2026-06-03 lesson).
"""
import logging
import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import Lemma
from app.services.interaction_logger import log_interaction
from app.services.lemma_quality import (
    _load_rank_map,
    _normalize,
    assign_frequency_rank,
    run_quality_gates,
)
from app.services.llm import generate_completion
from app.services.material_generator import (
    MIN_SENTENCES_PER_WORD,
    generate_material_for_word,
)
from app.services.morphology import get_best_lemma_mle
from app.services.sentence_validator import (
    _is_function_word,
    build_comprehensive_lemma_lookup,
    lookup_lemma,
    strip_diacritics,
)
from app.services.word_selector import introduce_word

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/discover", tags=["discover"])

# A token is a maximal run of Arabic letters + diacritics (matches the reference
# tokenizer in scripts/reading_readiness.py). Deliberately excludes Arabic
# punctuation (، ؛ ؟) and digits, which sit in adjacent code points.
_ARABIC_TOKEN = re.compile(r"[ء-يٰ-ۿݐ-ݿ]+")
_MAX_WORDS = 20
# CAMeL POS prefixes that count as teachable content; everything else (preps,
# particles, pronouns, conjunctions) is a readable function word, not a gap.
_CONTENT_POS_PREFIXES = ("noun", "adj", "verb", "adv")
# LLM/CAMeL part-of-speech values that mark a proper noun (never vocabulary).
_PROPER_NOUN_POS = {"proper_noun", "noun_prop"}

_GLOSS_SCHEMA = {
    "type": "object",
    "properties": {
        "glosses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "gloss_en": {"type": "string"},
                    "pos": {"type": "string"},
                    "transliteration": {"type": "string"},
                    "is_proper_noun": {"type": "boolean"},
                },
                "required": ["index", "gloss_en", "pos", "transliteration", "is_proper_noun"],
            },
        }
    },
    "required": ["glosses"],
}


def _camel_content(token: str, cache: dict) -> tuple[str, str, str] | None:
    """For an OOV surface token, return (diacritized citation lemma, normalized bare,
    pos) if CAMeL analyses it as a content word; None for function/proper/unanalyzable.
    Groups inflected forms of the same word under one citation lemma."""
    if token in cache:
        return cache[token]
    result = None
    try:
        a = get_best_lemma_mle(token)
    except Exception:
        a = None
    if a and a.get("lex"):
        pos = (a.get("pos") or "").lower()
        # noun_prop = proper name: readable, not a vocab gap → skip.
        if pos != "noun_prop" and any(pos.startswith(p) for p in _CONTENT_POS_PREFIXES):
            lex = a["lex"]
            result = (lex, _normalize(lex), pos)
    cache[token] = result
    return result


def _gloss(items: list[dict]) -> dict[int, dict]:
    """LLM-gloss the candidate lemmas. Returns {index: {gloss_en, pos, transliteration,
    is_proper_noun}}. Empty on failure — callers degrade gracefully."""
    if not items:
        return {}
    listing = "\n".join(
        f'{it["index"]}. {it["lemma_ar"]} (bare {it["bare"]}'
        + (f', {it["pos"]}' if it.get("pos") else "")
        + ")"
        for it in items
    )
    prompt = (
        "Give a concise English gloss for each Modern Standard Arabic lemma below.\n"
        "- gloss_en: 1-4 words, the core meaning (no article, no parenthetical).\n"
        "- pos: noun / verb / adjective / adverb / particle / proper_noun.\n"
        "- transliteration: ALA-LC romanization of the lemma.\n"
        "- is_proper_noun: true for names of people/places/organizations.\n\n"
        f"{listing}"
    )
    try:
        res = generate_completion(
            prompt=prompt,
            system_prompt="You are a precise Arabic-English lexicographer.",
            json_schema=_GLOSS_SCHEMA,
            temperature=0.0,
            model_override="claude_haiku",
            task_type="dragoman_gloss",
        )
    except Exception as e:
        logger.warning("dragoman gloss failed: %s", e)
        return {}
    return {g["index"]: g for g in res.get("glosses", []) if "index" in g}


class DiscoverIn(BaseModel):
    text: str
    count: int = 8


@router.post("/words")
def discover_words(req: DiscoverIn, db: Session = Depends(get_db)):
    """High-value Arabic lemmas in `text` that aren't in Alif yet, ranked by frequency."""
    lemma_lookup = build_comprehensive_lemma_lookup(db)
    ranks = _load_rank_map()
    cand: dict[str, dict] = {}  # citation bare -> {surface, lemma_ar, pos, count}
    camel_cache: dict = {}

    for tok in _ARABIC_TOKEN.findall(req.text or ""):
        bare = _normalize(tok)
        if len(bare) < 2 or _is_function_word(bare) or _is_function_word(bare.replace("ى", "ي")):
            continue
        # Already in Alif's vocabulary? The hardened lookup resolves clitics and
        # variants → canonical, so this catches forms a surface match would miss.
        if lookup_lemma(bare, lemma_lookup, original_bare=strip_diacritics(tok)) is not None:
            continue
        # Genuinely OOV: CAMeL confirms it's a content word, groups inflections under
        # one citation form, and gives a diacritized lemma for glossing.
        analysis = _camel_content(tok, camel_cache)
        if analysis is None:
            continue  # function / proper / unanalyzable — not a vocab candidate
        lex, lex_bare, pos = analysis
        # The citation form itself may already be known even when the surface wasn't
        # (e.g. an inflection CAMeL reduced to an in-vocab lemma).
        if lex_bare != bare and lookup_lemma(lex_bare, lemma_lookup) is not None:
            continue
        entry = cand.get(lex_bare)
        if entry:
            entry["count"] += 1
        else:
            cand[lex_bare] = {"surface": tok, "lemma_ar": lex, "pos": pos, "count": 1}

    if not cand:
        return {"words": [], "count": 0}

    # Rank: frequency-listed words first (most frequent first), then by occurrences.
    ordered = sorted(
        cand.items(),
        key=lambda kv: (0, ranks[kv[0]]) if kv[0] in ranks else (1, -kv[1]["count"]),
    )[: max(1, min(req.count, _MAX_WORDS))]

    glosses = _gloss(
        [
            {"index": i, "lemma_ar": v["lemma_ar"], "bare": b, "pos": v["pos"]}
            for i, (b, v) in enumerate(ordered)
        ]
    )
    words = []
    for i, (b, v) in enumerate(ordered):
        g = glosses.get(i, {})
        if g.get("is_proper_noun"):
            continue  # names aren't vocabulary worth scheduling
        words.append(
            {
                "surface": v["surface"],
                "lemma_ar": v["lemma_ar"],
                "lemma_ar_bare": b,
                "gloss_en": g.get("gloss_en"),
                "pos": g.get("pos") or v["pos"],
                "transliteration": g.get("transliteration"),
                "freq_rank": ranks.get(b),
                "count_in_text": v["count"],
            }
        )
    return {"words": words, "count": len(words)}


class WordIn(BaseModel):
    lemma_ar_bare: str
    lemma_ar: str | None = None
    gloss_en: str | None = None
    pos: str | None = None
    transliteration: str | None = None


def _gate_and_generate(lemma_ids: list[int]) -> None:
    """Background: quality-gate the new lemmas, then generate review material. Own
    session so it never blocks the request's write lock."""
    db = SessionLocal()
    try:
        run_quality_gates(db, lemma_ids, background_enrich=True)
        db.commit()
    except Exception as e:
        logger.warning("dragoman gating failed for %s: %s", lemma_ids, e)
        db.rollback()
    finally:
        db.close()
    for lid in lemma_ids:
        try:
            generate_material_for_word(lid, needed=MIN_SENTENCES_PER_WORD)
        except Exception as e:
            logger.warning("dragoman material gen failed for %s: %s", lid, e)


def _create_and_introduce(db: Session, w: WordIn, lemma_lookup: dict) -> dict:
    """Find-or-create the canonical lemma for `w`, then introduce it immediately
    (bypassing the daily intro cap — this is an explicit user add). Newly created
    lemmas are registered in `lemma_lookup` so a repeated word in the same batch
    resolves to the row we just made instead of being created twice."""
    if (w.pos or "").lower() in _PROPER_NOUN_POS:
        raise ValueError(f"refusing to add proper noun {w.lemma_ar_bare!r}")
    bare = _normalize(w.lemma_ar_bare)
    # Hardened existence check: resolves clitics + variants → canonical.
    existing_id = lookup_lemma(bare, lemma_lookup, original_bare=strip_diacritics(w.lemma_ar_bare))
    created = False
    if existing_id is not None:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == existing_id).first()
    if existing_id is None or lemma is None:
        gloss = (w.gloss_en or "").strip()
        if not gloss:
            # Hard invariant: no words without an English gloss, ever.
            raise ValueError(f"refusing to create gloss-less lemma {bare!r}")
        word_category = "proper_name" if (w.pos or "").lower() in _PROPER_NOUN_POS else None
        lemma = Lemma(
            lemma_ar=(w.lemma_ar or bare),
            lemma_ar_bare=bare,
            gloss_en=gloss,
            pos=w.pos,
            word_category=word_category,
            transliteration_ala_lc=w.transliteration,
            source="dragoman",
        )
        db.add(lemma)
        db.flush()
        assign_frequency_rank(lemma)
        lemma_lookup[bare] = lemma.lemma_id  # in-batch dedupe
        created = True
    res = introduce_word(
        db, lemma.lemma_id, source="dragoman",
        due_immediately=True, enforce_daily_cap=False,
    )
    return {
        "lemma_id": lemma.lemma_id,
        "lemma_ar": lemma.lemma_ar,
        "gloss_en": lemma.gloss_en,
        "created": created,
        "state": res.get("state"),
        "already_known": res.get("already_known", False),
    }


@router.post("/add")
def add_word(w: WordIn, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Create + introduce one word; gate and generate material in the background."""
    lemma_lookup = build_comprehensive_lemma_lookup(db)
    try:
        out = _create_and_introduce(db, w, lemma_lookup)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    if out["created"]:
        background_tasks.add_task(_gate_and_generate, [out["lemma_id"]])
        log_interaction(event="dragoman_word_added", lemma_id=out["lemma_id"])
    return out


class WordsIn(BaseModel):
    words: list[WordIn]


@router.post("/add-batch")
def add_words(req: WordsIn, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Create + introduce several words at once. Each word is committed independently
    so one failure can't roll back the rest of the batch."""
    lemma_lookup = build_comprehensive_lemma_lookup(db)
    results, new_ids = [], []
    for w in req.words:
        try:
            out = _create_and_introduce(db, w, lemma_lookup)
            db.commit()
            results.append(out)
            if out["created"]:
                new_ids.append(out["lemma_id"])
                log_interaction(event="dragoman_word_added", lemma_id=out["lemma_id"])
        except Exception as e:
            db.rollback()
            logger.warning("dragoman add failed for %s: %s", w.lemma_ar_bare, e)
            results.append({"lemma_ar_bare": w.lemma_ar_bare, "error": str(e)})
    if new_ids:
        background_tasks.add_task(_gate_and_generate, new_ids)
    return {"added": results, "count": len(results)}
