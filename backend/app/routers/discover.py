"""Discover vocabulary from external Arabic text (Dragoman magazine, Bookifier glossaries).

Endpoints:
  POST /api/discover/words  — given a block of Arabic text, return the highest-value
       lemmas that are NOT yet in Alif's pool, glossed. Two consumers:
         • Dragoman: "what should I learn next" — MSA, ranked common-first (default).
         • Bookifier: a glossary attached to a *specific* (often dialectal/vulgar)
           text — `selection="distinctive"` ranks the load-bearing vocabulary of
           THIS text, and `include_oov=true` keeps out-of-CAMeL-vocabulary words
           (dialect/slang/loanwords) via a clitic-stripped surface fallback instead
           of silently dropping them.
  POST /api/discover/add[-batch] — create the chosen lemma(s), introduce them into the
       acquisition pipeline immediately (explicit user adds bypass the daily intro cap),
       and (in the background) run quality gates + generate review material.

Word identity goes through the production-hardened lookup path
(`build_comprehensive_lemma_lookup` + `lookup_lemma`): clitic stripping, CAMeL
disambiguation, collision handling, and variant→canonical resolution — the same path
the corpus importer uses. We never hand-roll surface-only classification (that leaks
clitic-attached and variant forms — 2026-06-03 lesson).
"""
import logging
import math
import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
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
# Clause boundaries for pulling one example occurrence per word.
_CLAUSE_SPLIT = re.compile(r"[.!?؟\n،؛]+")
_MAX_WORDS = 50
# CAMeL POS prefixes that count as teachable content; everything else (preps,
# particles, pronouns, conjunctions) is a readable function word, not a gap.
_CONTENT_POS_PREFIXES = ("noun", "adj", "verb", "adv")
# LLM/CAMeL part-of-speech values that mark a proper noun (never vocabulary).
_PROPER_NOUN_POS = {"proper_noun", "noun_prop"}
# Conservative clitic sets for the OOV surface fallback (most-specific first), each
# with a minimum remaining-stem length. We strip ONLY unambiguous clitics: the
# definite article (and its conjunction/preposition compounds), the conjunctions
# و/ف (guarded to ≥3 so short root-initial-و words survive), and MULTI-char enclitic
# pronouns. Single-letter proclitics ب/ك/ل and single-letter enclitics ه/ك/ي are
# deliberately NOT stripped — they collide with root letters (كسها→سها, أنيك→أني).
_FB_PROCLITICS = (("وال", 2), ("فال", 2), ("بال", 2), ("كال", 2), ("لل", 2), ("ال", 2),
                  ("و", 3), ("ف", 3))
_FB_ENCLITICS = (("هما", 2), ("كما", 2), ("هم", 2), ("هن", 2), ("كم", 2), ("كن", 2),
                 ("نا", 2), ("ني", 2), ("ها", 2))
# Rank assigned to words absent from the MSA frequency list (treated as maximally
# rare for distinctive ranking — they carry the most lift).
_OOV_RANK = 100_000

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
                    "register": {"type": "string"},
                    "dialect": {"type": "string"},
                    # Corrected citation form when the supplied lemma is wrong for the
                    # example context (e.g. an OOV verb a morph-analyzer mis-rooted).
                    "lemma_ar": {"type": "string"},
                },
                "required": ["index", "gloss_en", "pos", "transliteration", "is_proper_noun"],
            },
        }
    },
    "required": ["glosses"],
}


def _clean_root(root: str | None) -> str | None:
    """CAMeL root → space-separated radicals, or None when unavailable. CAMeL uses
    'O' for no-root and '#' for an irregular/defective slot — treat those as absent."""
    if not root or "O" in root:
        return None
    return root.replace(".", " ").strip() or None


def _fallback_bare(bare: str) -> str:
    """Clitic-stripped citation bare for an OOV surface — strip at most one leading
    proclitic and one trailing enclitic (conservative, keeps ≥2 chars). Recovers
    كسها/الكس/كسك → كس. Verb conjugations of OOV roots can't be unified
    deterministically; the gloss step corrects those per-occurrence."""
    s = bare
    for pre, minrem in _FB_PROCLITICS:
        if s.startswith(pre) and len(s) - len(pre) >= minrem:
            s = s[len(pre):]
            break
    for suf, minrem in _FB_ENCLITICS:
        if s.endswith(suf) and len(s) - len(suf) >= minrem:
            s = s[: -len(suf)]
            break
    return s


def _camel_raw(token: str, cache: dict):
    if token in cache:
        return cache[token]
    try:
        a = get_best_lemma_mle(token)
    except Exception:
        a = None
    cache[token] = a
    return a


def _classify(token: str, bare: str, cache: dict, include_oov: bool):
    """Classify an OOV-at-lookup token. Returns
    (key_bare, lemma_ar, pos|None, root|None, lemma_source) or None to skip.

    - Clean CAMeL content analysis → use it (the default Dragoman path).
    - Otherwise (noun_prop, no analysis, non-content): only when include_oov, fall
      back to a clitic-stripped surface lemma and let the gloss LLM decide whether
      it's a real word, a name, and its register. Without include_oov, skip."""
    a = _camel_raw(token, cache)
    if a and a.get("lex"):
        pos = (a.get("pos") or "").lower()
        if pos != "noun_prop" and any(pos.startswith(p) for p in _CONTENT_POS_PREFIXES):
            root = a.get("root") or ""
            # In OOV mode, distrust defective/irregular-root analyses (O/# in the
            # root) — they are usually backoff guesses for dialect/slang and produce
            # confidently-wrong lemmas (وكسك→وكس). Prefer the surface fallback. The
            # default (Dragoman) path keeps the prior behavior unchanged.
            if not (include_oov and ("O" in root or "#" in root)):
                lex = a["lex"]
                return (_normalize(lex), lex, pos, _clean_root(root), "camel")
    if not include_oov:
        return None
    fb = _fallback_bare(bare)
    return (fb, fb, None, None, "surface_fallback")


def _example_finder(text: str):
    """Return a fn(surface_set) -> first clause containing any of those surfaces."""
    clauses = []
    for c in _CLAUSE_SPLIT.split(text or ""):
        c = c.strip()
        if c:
            clauses.append((c, set(_ARABIC_TOKEN.findall(c))))

    def find(surfaces: set[str]) -> str | None:
        for clause, toks in clauses:
            if toks & surfaces:
                return clause
        return None

    return find


def _gloss(items: list[dict]) -> dict[int, dict]:
    """Context-aware LLM gloss. Each item: {index, lemma_ar, bare, pos?, example?}.
    Returns {index: {gloss_en, pos, transliteration, is_proper_noun, register,
    dialect, lemma_ar?}}. Empty on failure — callers degrade gracefully."""
    if not items:
        return {}
    lines = []
    for it in items:
        head = f'{it["index"]}. {it["lemma_ar"]} (bare {it["bare"]}'
        if it.get("pos"):
            head += f', {it["pos"]}'
        head += ")"
        if it.get("example"):
            head += f' — example: «{it["example"]}»'
        lines.append(head)
    prompt = (
        "Give a precise English study gloss for each Arabic lemma below, AS USED in "
        "its example clause when one is given.\n"
        "- gloss_en: 1-5 words, the core meaning in context (no leading article).\n"
        "- pos: noun / verb / adjective / adverb / particle / proper_noun.\n"
        "- transliteration: ALA-LC romanization of the lemma.\n"
        "- is_proper_noun: true ONLY for actual names of people/places/organizations.\n"
        "- register: one of neutral | literary | colloquial | vulgar | clinical.\n"
        "- dialect: one of msa | gulf | egyptian | levantine | mixed.\n"
        "- lemma_ar: the correct diacritized citation form. If the supplied form looks "
        "WRONG for the example (an automatic analyzer can pick an unrelated same-letters "
        "word), return the corrected citation form here.\n\n"
        "This is a scholarly lexicon for adult language learners. Gloss vulgar, slang, "
        "and dialectal terms ACCURATELY and clinically — do not censor, euphemize, or "
        "refuse; precision matters for comprehension.\n\n"
        + "\n".join(lines)
    )
    try:
        res = generate_completion(
            prompt=prompt,
            system_prompt="You are a precise Arabic-English lexicographer.",
            json_schema=_GLOSS_SCHEMA,
            temperature=0.0,
            model_override="claude_haiku",
            task_type="discover_gloss",
        )
    except Exception as e:
        logger.warning("discover gloss failed: %s", e)
        return {}
    return {g["index"]: g for g in res.get("glosses", []) if "index" in g}


class DiscoverIn(BaseModel):
    text: str
    count: int = 8
    # "common_first" (Dragoman default: surface generally-common words to learn next)
    # | "distinctive" (Bookifier: words that carry THIS text — frequent here, rare in
    #   general — TF-IDF-style lift against the MSA frequency table).
    selection: str = "common_first"
    # When true, keep out-of-CAMeL-vocabulary words (dialect/slang/loanwords) via a
    # surface fallback instead of dropping them. Off by default → Dragoman unaffected.
    include_oov: bool = False


@router.post("/words")
def discover_words(req: DiscoverIn, db: Session = Depends(get_db)):
    """High-value Arabic lemmas in `text` not yet in Alif, glossed."""
    lemma_lookup = build_comprehensive_lemma_lookup(db)
    ranks = _load_rank_map()
    # key bare -> {surfaces: {form: count}, lemma_ar, pos, root, count, source}
    cand: dict[str, dict] = {}
    camel_cache: dict = {}

    for tok in _ARABIC_TOKEN.findall(req.text or ""):
        bare = _normalize(tok)
        if len(bare) < 2 or _is_function_word(bare) or _is_function_word(bare.replace("ى", "ي")):
            continue
        # Already in Alif's vocabulary? The hardened lookup resolves clitics and
        # variants → canonical, so this catches forms a surface match would miss.
        if lookup_lemma(bare, lemma_lookup, original_bare=strip_diacritics(tok)) is not None:
            continue
        c = _classify(tok, bare, camel_cache, req.include_oov)
        if c is None:
            continue
        key, lemma_ar, pos, root, source = c
        if not key or _is_function_word(key):
            continue
        # The citation/fallback form itself may already be known.
        if key != bare and lookup_lemma(key, lemma_lookup) is not None:
            continue
        entry = cand.get(key)
        if entry:
            entry["count"] += 1
            entry["surfaces"][tok] = entry["surfaces"].get(tok, 0) + 1
            # A confident CAMeL analysis upgrades an earlier surface-fallback guess.
            if source == "camel" and entry["source"] != "camel":
                entry.update(lemma_ar=lemma_ar, pos=pos, root=root, source="camel")
        else:
            cand[key] = {
                "surfaces": {tok: 1}, "lemma_ar": lemma_ar, "pos": pos,
                "root": root, "count": 1, "source": source,
            }

    if not cand:
        return {"words": [], "count": 0}

    if req.selection == "distinctive":
        # Frequent-here × generally-rare: count weighted by inverse general frequency.
        def score(kv):
            rank = ranks.get(kv[0], _OOV_RANK)
            return kv[1]["count"] * math.log10(rank + 10)
        ordered = sorted(cand.items(), key=score, reverse=True)
    else:
        # common_first: frequency-listed words first (most common), then by occurrences.
        ordered = sorted(
            cand.items(),
            key=lambda kv: (0, ranks[kv[0]]) if kv[0] in ranks else (1, -kv[1]["count"]),
        )
    ordered = ordered[: max(1, min(req.count, _MAX_WORDS))]

    find_example = _example_finder(req.text or "")
    examples = {b: find_example(set(v["surfaces"])) for b, v in ordered}

    glosses = _gloss(
        [
            {"index": i, "lemma_ar": v["lemma_ar"], "bare": b, "pos": v["pos"],
             "example": examples.get(b)}
            for i, (b, v) in enumerate(ordered)
        ]
    )

    words = []
    for i, (b, v) in enumerate(ordered):
        g = glosses.get(i, {})
        if g.get("is_proper_noun"):
            continue  # names aren't vocabulary worth scheduling
        # The gloss step may correct an OOV mis-analysis; trust its citation form.
        corrected = (g.get("lemma_ar") or "").strip()
        lemma_ar = corrected or v["lemma_ar"]
        lemma_bare = _normalize(corrected) if corrected else b
        surfaces = sorted(v["surfaces"], key=lambda s: -v["surfaces"][s])
        words.append(
            {
                "surface": surfaces[0],
                "surface_forms": surfaces,
                "lemma_ar": lemma_ar,
                "lemma_ar_bare": lemma_bare,
                "root": v["root"],
                "gloss_en": g.get("gloss_en"),
                "pos": g.get("pos") or v["pos"],
                "register": g.get("register"),
                "dialect": g.get("dialect"),
                "transliteration": g.get("transliteration"),
                "freq_rank": ranks.get(lemma_bare),
                "count_in_text": v["count"],
                "example_ar": examples.get(b),
                "lemma_source": v["source"],
            }
        )
    return {"words": words, "count": len(words)}


class WordIn(BaseModel):
    # `register` is aliased (the JSON key stays "register") to avoid shadowing
    # pydantic BaseModel's inherited ABC `register` method.
    model_config = ConfigDict(populate_by_name=True)
    lemma_ar_bare: str
    lemma_ar: str | None = None
    gloss_en: str | None = None
    pos: str | None = None
    transliteration: str | None = None
    register_: str | None = Field(default=None, alias="register")
    dialect: str | None = None


def _gate_and_generate(lemma_ids: list[int]) -> None:
    """Background: quality-gate the new lemmas, then generate review material. Own
    session so it never blocks the request's write lock."""
    db = SessionLocal()
    try:
        run_quality_gates(db, lemma_ids, background_enrich=True)
        db.commit()
    except Exception as e:
        logger.warning("discover gating failed for %s: %s", lemma_ids, e)
        db.rollback()
    finally:
        db.close()
    for lid in lemma_ids:
        try:
            generate_material_for_word(lid, needed=MIN_SENTENCES_PER_WORD)
        except Exception as e:
            logger.warning("discover material gen failed for %s: %s", lid, e)


def _create_and_introduce(db: Session, w: WordIn, lemma_lookup: dict) -> dict:
    """Find-or-create the canonical lemma for `w`, then introduce it immediately
    (bypassing the daily intro cap — explicit user add). Newly created lemmas are
    registered in `lemma_lookup` so a repeated word in the same batch resolves to
    the row we just made instead of being created twice."""
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
        lemma = Lemma(
            lemma_ar=(w.lemma_ar or bare),
            lemma_ar_bare=bare,
            gloss_en=gloss,
            pos=w.pos,
            transliteration_ala_lc=w.transliteration,
            register=w.register_,
            dialect=w.dialect,
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
            logger.warning("discover add failed for %s: %s", w.lemma_ar_bare, e)
            results.append({"lemma_ar_bare": w.lemma_ar_bare, "error": str(e)})
    if new_ids:
        background_tasks.add_task(_gate_and_generate, new_ids)
    return {"added": results, "count": len(results)}
