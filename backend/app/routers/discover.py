"""Discover vocabulary from external Arabic text (e.g. the Dragoman magazine).

Two jobs:
  POST /api/discover/words  — given a block of Arabic text, return the highest-value
       lemmas that are NOT yet in Alif's pool, ranked by MSA frequency, each glossed.
  POST /api/discover/add[-batch] — create the chosen lemma(s), introduce them into the
       acquisition pipeline, and (in the background) run the quality gates + generate
       review material. Fast write up front; the LLM-heavy gating never holds the
       write lock (own session in a BackgroundTask).

This is the Dragoman → Alif integration: a reader sees an Arabic essay in the magazine,
Dragoman asks this endpoint which words are worth learning, and renders "add to Alif"
buttons that POST back here.
"""
import logging
import re

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import Lemma
from app.services.interaction_logger import log_interaction
from app.services.llm import generate_completion
from app.services.lemma_quality import (
    _load_rank_map,
    _normalize,
    assign_frequency_rank,
    run_quality_gates,
)
from app.services.material_generator import (
    MIN_SENTENCES_PER_WORD,
    generate_material_for_word,
)
from app.services.morphology import get_best_lemma_mle
from app.services.sentence_validator import _is_function_word, _strip_clitics
from app.services.word_selector import introduce_word

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/discover", tags=["discover"])

# Arabic letters + diacritics + tatweel + superscript alef only — deliberately excludes
# Arabic punctuation (، ؛ ؟ at U+060C/061B/061F), which sit in the same Unicode block.
_ARABIC_TOKEN = re.compile(r"[ء-ْٰ]+")
_MAX_WORDS = 20

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


def _lemmatize(token: str) -> tuple[str, str, str | None] | None:
    """(diacritized lemma, normalized bare, pos) for an Arabic surface token, or None
    if it isn't a content word. Falls back to clitic-stripping when CAMeL is unavailable."""
    norm = _normalize(token)
    if len(norm) < 2 or _is_function_word(norm):
        return None
    lex, pos = token, None
    try:
        analysis = get_best_lemma_mle(token)
    except Exception:
        analysis = None
    if analysis and analysis.get("lex"):
        lex, pos = analysis["lex"], analysis.get("pos")
        bare = _normalize(lex)
    else:
        try:
            stems = _strip_clitics(norm)
        except Exception:
            stems = []
        bare = _normalize(stems[0]) if stems else norm
    if len(bare) < 2 or _is_function_word(bare):
        return None
    return lex, bare, pos


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
    cand: dict[str, dict] = {}  # bare -> {surface, lemma_ar, pos, count}
    for tok in _ARABIC_TOKEN.findall(req.text or ""):
        lem = _lemmatize(tok)
        if not lem:
            continue
        lex, bare, pos = lem
        if bare in cand:
            cand[bare]["count"] += 1
        else:
            cand[bare] = {"surface": tok, "lemma_ar": lex, "pos": pos, "count": 1}
    if not cand:
        return {"words": [], "count": 0}

    # Genuinely-new only: drop any bare form Alif already has a lemma for.
    bares = list(cand.keys())
    existing: set[str] = set()
    for i in range(0, len(bares), 400):
        rows = db.query(Lemma.lemma_ar_bare).filter(
            Lemma.lemma_ar_bare.in_(bares[i : i + 400])
        ).all()
        existing.update(r[0] for r in rows)
    fresh = {b: v for b, v in cand.items() if b not in existing}
    if not fresh:
        return {"words": [], "count": 0}

    # Rank: frequency-listed words first (most frequent first), then by occurrences.
    ranks = _load_rank_map()
    ordered = sorted(
        fresh.items(),
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


class WordsIn(BaseModel):
    words: list[WordIn]


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


def _create_and_introduce(db: Session, w: WordIn) -> dict:
    bare = _normalize(w.lemma_ar_bare)
    existing = (
        db.query(Lemma)
        .filter(Lemma.canonical_lemma_id.is_(None), Lemma.lemma_ar_bare == bare)
        .first()
    )
    created = False
    if existing:
        lemma = existing
    else:
        lemma = Lemma(
            lemma_ar=(w.lemma_ar or bare),
            lemma_ar_bare=bare,
            gloss_en=w.gloss_en,
            pos=w.pos,
            transliteration_ala_lc=w.transliteration,
            source="study",
        )
        db.add(lemma)
        db.flush()
        assign_frequency_rank(lemma)
        created = True
    res = introduce_word(db, lemma.lemma_id, source="study")
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
    out = _create_and_introduce(db, w)
    db.commit()
    if out["created"]:
        background_tasks.add_task(_gate_and_generate, [out["lemma_id"]])
    log_interaction(event="dragoman_word_added", lemma_id=out["lemma_id"])
    return out


@router.post("/add-batch")
def add_words(req: WordsIn, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Create + introduce several words at once."""
    results, new_ids = [], []
    for w in req.words:
        try:
            out = _create_and_introduce(db, w)
            results.append(out)
            if out["created"]:
                new_ids.append(out["lemma_id"])
        except Exception as e:
            db.rollback()
            results.append({"lemma_ar_bare": w.lemma_ar_bare, "error": str(e)})
    db.commit()
    if new_ids:
        background_tasks.add_task(_gate_and_generate, new_ids)
        for lid in new_ids:
            log_interaction(event="dragoman_word_added", lemma_id=lid)
    return {"added": results, "count": len(results)}
