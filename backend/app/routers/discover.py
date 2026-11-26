"""Discover vocabulary from external Arabic text (Dragoman magazine, Bookifier glossaries).

Endpoints:
  POST /api/discover/words  — given a block of Arabic text, return the highest-value
       lemmas that are NOT yet in the learner's active curriculum, glossed. Two consumers:
         • Dragoman: "what should I learn next" — MSA, ranked common-first (default).
         • Bookifier: a glossary attached to a *specific* (often dialectal/vulgar)
           text — `selection="distinctive"` ranks the load-bearing vocabulary of
           THIS text, and `include_oov=true` keeps out-of-CAMeL-vocabulary words
           (dialect/slang/loanwords) via a clitic-stripped surface fallback instead
           of silently dropping them.
  POST /api/discover/add[-batch] — create the chosen lemma(s), introduce them into the
       acquisition pipeline immediately (explicit user adds bypass the daily intro cap),
       and (in the background) run quality gates + generate review material.

Word identity for /words (running-text tokens) goes through the production-hardened
lookup path (`build_comprehensive_lemma_lookup` + `lookup_lemma_id`): exact-surface
identity first, then clitic stripping,
CAMeL disambiguation, collision handling, and variant→canonical resolution — the same
path the corpus importer uses. We never hand-roll surface-only classification (that
leaks clitic-attached and variant forms — 2026-06-03 lesson). /add[-batch] submits
*citation forms*, which use `lookup_lemma_citation` instead: exact/variant/ال-prefix
matches only, no single-letter clitic strips, no CAMeL fuzzy fallback (18 documented
collisions like كناس→نَاس — 2026-07-15 lesson, spec-2026-07-15-lookup-clitic-collision.md).
"""
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import Lemma, SnapPage, UserLemmaKnowledge, WordAddContext
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
from app.services.ocr_service import extract_text_and_translation
from app.services.sentence_validator import (
    LemmaMatchProvenance,
    _candidate_matches_correction,
    _is_function_word,
    build_comprehensive_lemma_lookup,
    correct_mapping,
    lookup_lemma,
    lookup_lemma_citation,
    lookup_lemma_id,
    requires_exact_running_text_alias,
)
from app.services.add_context import record_add_context, verify_add_contexts
from app.services.attention_policy import LEARNER_PICK_SOURCES
from app.services.word_selector import introduce_word

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/discover", tags=["discover"])

# A token is a maximal run of Arabic letters + diacritics (matches the reference
# tokenizer in scripts/reading_readiness.py). Deliberately excludes Arabic
# punctuation (، ؛ ؟) and digits, which sit in adjacent code points.
_ARABIC_TOKEN = re.compile(
    r"[\u0621-\u064A\u064B-\u065F\u0670-\u06FF\u0750-\u077F]+"
)
# Clause boundaries for pulling one example occurrence per word.
_CLAUSE_SPLIT = re.compile(r"[.!?؟\n،؛]+")
_MAX_WORDS = 50
_MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20MB per snapped page (mirrors the OCR router)
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
_GLOSS_CHUNK = 60
# These states mean a word is already in the learner's active curriculum.  A lemma
# merely existing in the corpus database does not mean the learner knows it: corpus
# import creates many Lemma rows without UserLemmaKnowledge rows.
_ALREADY_LEARNING_STATES = {
    "new", "acquiring", "learning", "known", "lapsed", "suspended"
}

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
                    # The meaning in the example clause, for the reader only; never stored.
                    "gloss_here": {"type": "string"},
                    # A set phrase or idiom that gives the clause its meaning, if any.
                    "phrase_ar": {"type": "string"},
                    "phrase_en": {"type": "string"},
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
    dialect, lemma_ar?}}. Empty on failure — callers degrade gracefully.
    Long lists (multi-page pre-reading) are glossed in parallel chunks so one
    call never has to carry hundreds of words."""
    if not items:
        return {}
    if len(items) > _GLOSS_CHUNK:
        from concurrent.futures import ThreadPoolExecutor

        chunks = [items[i:i + _GLOSS_CHUNK] for i in range(0, len(items), _GLOSS_CHUNK)]
        merged: dict[int, dict] = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            for part in pool.map(_gloss, chunks):
                merged.update(part)
        return merged
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
        "Give English glosses for each Arabic lemma below. gloss_en is stored in a "
        "learner's dictionary and is shown for this word in every future sentence, so "
        "it must be the word's own general meaning, never a meaning that only an idiom "
        "or this one clause gives it.\n"
        "- gloss_en: 1-6 words, the general dictionary meaning(s) of the lemma, most "
        "common first, including the sense used in the example when it is a sense of "
        "the word itself (no leading article). Example: أَبْصَرَ in «أبصر النور» is "
        "\"to see, perceive, catch sight of\", not \"was born\".\n"
        "- gloss_here: the meaning in the example clause, 1-6 words (\"was born\" "
        "for the example above). Same as gloss_en when nothing differs.\n"
        "- phrase_ar / phrase_en: only when the clause meaning comes from a set phrase "
        "or idiom: the vocalized phrase containing the word (أَبْصَرَ النُّورَ) and its "
        "meaning (was born; came out). Omit otherwise.\n"
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
    # Claude CLI sometimes returns the bare glosses array instead of the
    # {"glosses": [...]} object the schema asks for.
    glosses = res.get("glosses", []) if isinstance(res, dict) else res
    if not isinstance(glosses, list):
        logger.warning("discover gloss returned %s, not a gloss list", type(res).__name__)
        return {}
    return {g["index"]: g for g in glosses if isinstance(g, dict) and "index" in g}


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
    """High-value Arabic lemmas in `text` not yet being learned, glossed."""
    words = discover_words_in_text(
        db, req.text, count=req.count, selection=req.selection, include_oov=req.include_oov
    )
    return {"words": words, "count": len(words)}


def discover_words_in_text(
    db: Session,
    text: str,
    count: int = 8,
    selection: str = "common_first",
    include_oov: bool = False,
    include_learning: bool = False,
    context: str | None = None,
    max_words: int = _MAX_WORDS,
    database_only: bool = False,
) -> list[dict]:
    """Core word-discovery: the highest-value lemmas in `text` not yet being learned,
    glossed. Shared by the /words endpoint and the photo /snap endpoint so both go
    through one tokenize → classify → rank → gloss path.

    ``include_learning`` keeps words the learner already studies (the Snap
    tap-a-word lookup wants every word); each result carries ``knowledge_state``.
    ``context`` is the running text used for example clauses and sense-aware
    glossing when ``text`` is a single tapped word. ``max_words`` lifts the
    per-call cap for multi-page pre-reading. ``database_only`` keeps only
    tokens that matched a vocabulary entry, so no model call is made."""
    lemma_lookup = build_comprehensive_lemma_lookup(db)
    state_by_id = dict(
        db.query(UserLemmaKnowledge.lemma_id, UserLemmaKnowledge.knowledge_state).all()
    )
    already_learning_ids = (
        set()
        if include_learning
        else {lid for lid, st in state_by_id.items() if st in _ALREADY_LEARNING_STATES}
    )
    lemma_cache: dict[int, Lemma | None] = {}

    def get_lemma(lemma_id: int) -> Lemma | None:
        if lemma_id not in lemma_cache:
            lemma_cache[lemma_id] = db.get(Lemma, lemma_id)
        return lemma_cache[lemma_id]

    ranks = _load_rank_map()
    # key bare -> {surfaces: {form: count}, lemma_ar, pos, root, count, source}
    cand: dict[str, dict] = {}
    camel_cache: dict = {}

    for tok in _ARABIC_TOKEN.findall(text or ""):
        bare = _normalize(tok)
        exact_alias_required = requires_exact_running_text_alias(tok)
        if len(bare) < 2 or (
            not exact_alias_required
            and (
                _is_function_word(bare)
                or _is_function_word(bare.replace("ى", "ي"))
            )
        ):
            continue
        # Resolve against the corpus, but only suppress words the learner has actually
        # introduced.  Previously every corpus Lemma was treated as "known", which
        # left ordinary book pages with zero or one suggestion.
        # Nothing checks this match before it is shown or used to suppress a
        # suggestion, so the last-resort CAMeL guess is held to lemma headwords.
        provenance = LemmaMatchProvenance()
        existing_id = lookup_lemma_id(
            tok, lemma_lookup, provenance, strict_fallback=True
        )
        if existing_id in already_learning_ids:
            continue
        # Approved exact-running-text identities are fail-closed. If their
        # unique gated destination is unavailable, do not let CAMeL or the OOV
        # surface fallback reinterpret the stripped consonant skeleton.
        if existing_id is None and exact_alias_required:
            continue
        existing = get_lemma(existing_id) if existing_id is not None else None
        if existing is None and database_only:
            continue
        if existing is not None:
            existing_bare = _normalize(existing.lemma_ar_bare)
            c = (
                existing_bare,
                existing.lemma_ar or existing.lemma_ar_bare,
                existing.pos,
                existing.root.root if existing.root else None,
                "database",
            )
        else:
            c = _classify(tok, bare, camel_cache, include_oov)
        if c is None:
            continue
        key, lemma_ar, pos, root, source = c
        if not key or _is_function_word(key):
            continue
        # The citation/fallback form itself may already be in active learning.
        key_id = (
            lookup_lemma(key, lemma_lookup, strict_fallback=True)
            if key != bare
            else existing_id
        )
        if key_id in already_learning_ids:
            continue
        tied = (
            provenance.tied_lemma_ids
            if existing is not None and provenance.homograph_tie
            else []
        )
        entry = cand.get(key)
        if entry:
            if entry.get("db_lemma_id") == existing_id:
                entry["tied"].extend(t for t in tied if t not in entry["tied"])
            entry["count"] += 1
            entry["surfaces"][tok] = entry["surfaces"].get(tok, 0) + 1
            # A confident CAMeL analysis upgrades an earlier surface-fallback guess.
            if source == "camel" and entry["source"] != "camel":
                entry.update(lemma_ar=lemma_ar, pos=pos, root=root, source="camel")
        else:
            cand[key] = {
                "surfaces": {tok: 1}, "lemma_ar": lemma_ar, "pos": pos,
                "root": root, "count": 1, "source": source,
                "gloss_en": existing.gloss_en if existing is not None else None,
                "transliteration": (
                    existing.transliteration_ala_lc if existing is not None else None
                ),
                "register": existing.register if existing is not None else None,
                "dialect": existing.dialect if existing is not None else None,
                "db_lemma_id": existing_id if existing is not None else None,
                # Same-spelling entries the token could not rule out.
                "tied": list(tied),
            }

    if not cand:
        return []

    if selection == "distinctive":
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
    ordered = ordered[: max(1, min(count, max_words))]

    find_example = _example_finder(context or text or "")
    examples = {b: find_example(set(v["surfaces"])) for b, v in ordered}

    # Corpus lemmas already have reviewed lexical metadata, so use it directly.
    # Only genuinely new/OOV forms need the second model call.  Besides lowering
    # latency, this means the common photographed-page path is not made fragile by
    # an unrelated text-model/CLI failure after OCR has already succeeded.
    glosses = {
        i: {
            "gloss_en": v.get("gloss_en"),
            "gloss_here": None,
            "pos": v.get("pos"),
            "transliteration": v.get("transliteration"),
            "is_proper_noun": (v.get("pos") or "").lower() in _PROPER_NOUN_POS,
            "register": v.get("register"),
            "dialect": v.get("dialect"),
        }
        for i, (_, v) in enumerate(ordered)
        if v["source"] == "database" and v.get("gloss_en")
    }
    needs_gloss = [
        {"index": i, "lemma_ar": v["lemma_ar"], "bare": b, "pos": v["pos"],
         "example": examples.get(b)}
        for i, (b, v) in enumerate(ordered)
        if i not in glosses
    ]
    if needs_gloss:
        glosses.update(_gloss(needs_gloss))

    words = []
    for i, (b, v) in enumerate(ordered):
        g = glosses.get(i, {})
        is_proper = bool(g.get("is_proper_noun"))
        # Learn-next mode drops names (not vocabulary to schedule). Glossary mode
        # (include_oov) keeps everything and exposes the flag — the gloss model
        # over-tags dialect/vulgar OOV nouns as proper, and a glossary wants names
        # (place/person) anyway; the consumer filters on `is_proper_noun`.
        if is_proper and not include_oov:
            continue
        # The gloss step may correct an OOV mis-analysis; trust its citation form.
        corrected = (g.get("lemma_ar") or "").strip()
        lemma_ar = corrected or v["lemma_ar"]
        lemma_bare = _normalize(corrected) if corrected else b
        # Final known-check on the (possibly corrected) citation form, mirroring what
        # /add does. The pre-gloss checks ran on the surface/key; a gloss correction
        # can land on a form that's already in the vocabulary, so re-check here —
        # otherwise we'd offer a word that /add immediately reports "already known".
        corrected_id = lookup_lemma_citation(
            lemma_bare,
            lemma_lookup,
            original_bare=corrected or lemma_ar,
        )
        if corrected_id in already_learning_ids:
            continue
        state_id = corrected_id
        if state_id is not None:
            state_lemma = get_lemma(state_id)
            if state_lemma is not None and state_lemma.canonical_lemma_id:
                state_id = state_lemma.canonical_lemma_id
        surfaces = sorted(v["surfaces"], key=lambda s: -v["surfaces"][s])
        words.append(
            {
                "surface": surfaces[0],
                "surface_forms": surfaces,
                "lemma_ar": lemma_ar,
                "lemma_ar_bare": lemma_bare,
                "root": v["root"],
                "gloss_en": g.get("gloss_en"),
                "gloss_here": _gloss_here(g),
                "phrase": _gloss_phrase(g),
                "pos": g.get("pos") or v["pos"],
                "is_proper_noun": is_proper,
                "register": g.get("register"),
                "dialect": g.get("dialect"),
                "transliteration": g.get("transliteration"),
                "freq_rank": ranks.get(lemma_bare),
                "count_in_text": v["count"],
                "example_ar": examples.get(b),
                "lemma_source": v["source"],
                "lemma_id": state_id,
                "knowledge_state": state_by_id.get(state_id) if state_id else None,
                "homograph_candidates": (
                    v["tied"] if state_id is not None and state_id == v["db_lemma_id"] else []
                ),
            }
        )
    return words


def _gloss_here(g: dict) -> str | None:
    """The clause meaning when it differs from the stored gloss, for display."""
    here = (g.get("gloss_here") or "").strip()
    return here if here and here.lower() != (g.get("gloss_en") or "").strip().lower() else None


def _gloss_phrase(g: dict) -> dict | None:
    ar, en = (g.get("phrase_ar") or "").strip(), (g.get("phrase_en") or "").strip()
    return {"ar": ar, "en": en} if ar and en else None


class LookupIn(BaseModel):
    word: str
    context: str | None = None


@router.post("/lookup")
def lookup_word(req: LookupIn, db: Session = Depends(get_db)):
    """Snap tap-a-word: gloss one surface from a snapped page, known or not.

    Read-only, like /snap. Returns ``{"word": null}`` for function words and
    tokens that resolve to nothing. Adding still goes through /add."""
    words = discover_words_in_text(
        db, req.word, count=1, include_oov=True, include_learning=True,
        context=req.context,
    )
    w = words[0] if words else None
    log_interaction(
        event="discover_lookup", found=bool(w), surface=req.word,
        context=(req.context or "")[:500],
        lemma_id=w.get("lemma_id") if w else None,
        lemma_ar=w.get("lemma_ar") if w else None,
        gloss_en=w.get("gloss_en") if w else None,
        lemma_source=w.get("lemma_source") if w else None,
    )
    return {"word": w}


class LookupSenseIn(BaseModel):
    lemma_id: int
    surface: str
    context: str


def _word_out(lemma: Lemma, surface: str, state: str | None, source: str) -> dict:
    return {
        "surface": surface, "surface_forms": [surface],
        "lemma_ar": lemma.lemma_ar or lemma.lemma_ar_bare,
        "lemma_ar_bare": _normalize(lemma.lemma_ar_bare),
        "root": lemma.root.root if lemma.root else None,
        "gloss_en": lemma.gloss_en, "pos": lemma.pos,
        "is_proper_noun": lemma.word_category == "proper_name",
        "register": lemma.register, "dialect": lemma.dialect,
        "transliteration": lemma.transliteration_ala_lc,
        "freq_rank": lemma.frequency_rank, "count_in_text": 1, "example_ar": None,
        "lemma_source": source, "lemma_id": lemma.lemma_id, "knowledge_state": state,
    }


def _resolve_different_word(
    db: Session, surface: str, matched_id: int, v: dict, lookup: dict | None = None
) -> dict:
    """The word the sentence actually uses: an existing sense-compatible entry
    when there is one, otherwise a proposal that /add can create."""
    if lookup is None:
        lookup = build_comprehensive_lemma_lookup(db)
    bare = _normalize(v["lemma_ar"])
    lid = lookup_lemma_citation(bare, lookup, original_bare=v["lemma_ar"])
    lemma = db.get(Lemma, lid) if lid is not None and lid != matched_id else None
    if lemma is not None and lemma.canonical_lemma_id:
        lemma = db.get(Lemma, lemma.canonical_lemma_id)
    if (
        lemma is not None and lemma.lemma_id != matched_id and (lemma.gloss_en or "").strip()
        and _candidate_matches_correction(lemma, v["gloss_en"], v["pos"] or "")
    ):
        state = (
            db.query(UserLemmaKnowledge.knowledge_state)
            .filter(UserLemmaKnowledge.lemma_id == lemma.lemma_id).scalar()
        )
        return _word_out(lemma, surface, state, "sense_check_existing")
    pos = (v.get("pos") or "").lower()
    is_name = pos.startswith("proper") or pos == "noun_prop" or bool(
        re.search(r"\((?:personal |place |proper )?name\)|\bproper name\b", v.get("gloss_en") or "", re.I))
    return {
        "surface": surface, "surface_forms": [surface],
        "lemma_ar": v["lemma_ar"], "lemma_ar_bare": bare, "root": None,
        "gloss_en": v["gloss_en"], "pos": v["pos"], "is_proper_noun": is_name,
        "register": None, "dialect": None, "transliteration": None,
        "freq_rank": None, "count_in_text": 1, "example_ar": None,
        "lemma_source": "sense_check_new", "lemma_id": None, "knowledge_state": None,
    }


def _sibling_word(db: Session, lemma_id: int, surface: str) -> dict | None:
    """Snap word for the same-spelling entry a sense check chose."""
    lemma = db.get(Lemma, lemma_id)
    if lemma is None:
        return None
    state = (
        db.query(UserLemmaKnowledge.knowledge_state)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id).scalar()
    )
    return _word_out(lemma, surface, state, "homograph_sibling")


@router.post("/lookup/sense")
def lookup_sense(req: LookupSenseIn, db: Session = Depends(get_db)):
    """Second step of a Snap lookup that matched a vocabulary entry: does that
    entry mean what the word means in this sentence?

    ``broader`` widens the stored gloss in place (compare-and-set, as pre-read
    does); ``different`` returns the word actually used, existing or proposed,
    so the reader can add it; ``fits``/``name`` change nothing. ``verdict`` is
    null when the check failed."""
    from app.services.lookup_sense import check_lookup_sense
    from app.services.preread_service import load_candidates, regloss

    lemma = db.get(Lemma, req.lemma_id)
    if lemma is None:
        raise HTTPException(status_code=404, detail="Lemma not found")
    entry = {"lemma_id": lemma.lemma_id, "lemma_ar": lemma.lemma_ar, "gloss_en": lemma.gloss_en,
             "pos": lemma.pos}
    # A homograph tie: offer the same-spelling entries the word did not rule out.
    provenance = LemmaMatchProvenance()
    tied_owner = lookup_lemma_id(
        req.surface, build_comprehensive_lemma_lookup(db), provenance, strict_fallback=True
    )
    if provenance.homograph_tie and tied_owner == req.lemma_id:
        entry["candidates"] = load_candidates(db, provenance.tied_lemma_ids)
    db.rollback()  # hold no transaction across the LLM call
    v = check_lookup_sense(req.context, req.surface, entry)
    out: dict = {"verdict": v["verdict"] if v else None}
    if v and v["verdict"] == "sibling":
        # Reported as "different" with an existing entry, which clients
        # already show; ``sibling`` says it came from the tie.
        out = {"verdict": "different", "sibling": True,
               "word": _sibling_word(db, v["lemma_id"], req.surface)}
        db.rollback()
        if out["word"] is None:
            out = {"verdict": None}
    elif v and v["verdict"] == "broader":
        w = {"lemma_id": req.lemma_id, "regloss": {"from": entry["gloss_en"], "to": v["gloss_en"],
                                                    "approved": v.get("approved") is True}}
        try:
            regloss(db, [w], source="snap_tap")
            db.commit()
        except Exception:
            logger.exception("lookup regloss failed for %s", req.lemma_id)
            db.rollback()
        out.update(gloss_en=v["gloss_en"], gloss_from=entry["gloss_en"],
                   written=bool(w.get("reglossed")))
    elif v and v["verdict"] == "different":
        out["word"] = _resolve_different_word(db, req.surface, req.lemma_id, v)
        db.rollback()
    log_interaction(
        event="discover_lookup_sense", surface=req.surface, lemma_id=req.lemma_id,
        lemma_ar=entry["lemma_ar"], gloss_from=entry["gloss_en"],
        context=req.context[:500], verdict=out["verdict"], sibling=out.get("sibling", False),
        candidate_ids=[c["lemma_id"] for c in entry.get("candidates") or []],
        gloss_to=out.get("gloss_en"), written=out.get("written"),
        word_lemma_id=(out.get("word") or {}).get("lemma_id"),
        word_lemma_ar=(out.get("word") or {}).get("lemma_ar"),
        word_gloss=(out.get("word") or {}).get("gloss_en"),
    )
    return out


class SnapOut(BaseModel):
    snap_id: int | None = None
    arabic_text: str
    translation_en: str
    # Sentence pairs with Arabic↔English phrase links; empty when unavailable.
    sentences: list[dict] = []
    words: list[dict]
    count: int
    # The suggested words are still being found (in the background after a new
    # snap); reload the page with GET /snaps/{snap_id} to get them.
    words_pending: bool = False


# A page whose suggestions never arrived (a restart killed the task) stops
# reporting them as pending after this long.
_WORDS_PENDING_MAX = timedelta(minutes=5)


def _snap_chips(
    db: Session, text: str, count: int, selection: str, include_oov: bool
) -> list[dict]:
    """The page's suggested words: discovered, names dropped, checked against
    the page. A failure in either step keeps what it has."""
    try:
        words = discover_words_in_text(
            db, text, count=count, selection=selection, include_oov=include_oov
        )
    except Exception:
        logger.exception("snap word discovery failed; the page gets no suggestions")
        db.rollback()
        return []
    # Names are not vocabulary to learn; /add refuses them anyway.
    words = [w for w in words if not w.get("is_proper_noun")]
    try:
        words = _sense_check_chips(db, text, words)
    except Exception:
        logger.exception("snap chip sense check failed; keeping unchecked chips")
        db.rollback()
    return words


def _store_snap_chips(snap_id: int, count: int, selection: str, include_oov: bool) -> None:
    db = SessionLocal()
    try:
        page = db.get(SnapPage, snap_id)
        if page is None:
            return
        text = page.arabic_text
        db.rollback()
        words = _snap_chips(db, text, count, selection, include_oov)
        page = db.get(SnapPage, snap_id)
        page.words_json = words
        db.commit()
        log_interaction(event="snap_words_ready", snap_id=snap_id, word_count=len(words))
    except Exception:
        logger.exception("snap suggestions failed for page %s", snap_id)
        db.rollback()
        page = db.get(SnapPage, snap_id)
        if page is not None and page.words_json is None:
            page.words_json = []
            db.commit()
    finally:
        db.close()


def chips_from_prematch(text: str, words: dict, count: int, selection: str) -> list[dict]:
    """The page's suggested words, picked from the page pre-match so a chip and
    a tap on the same word show the same answer: the word the check settled on
    (a "different" verdict's word, with ``sense_from``), not yet learning, not a
    name, with a gloss; ranked as ``discover_words_in_text`` ranks them."""
    ranks = _load_rank_map()
    occurrences: dict[str, int] = {}
    for tok in _ARABIC_TOKEN.findall(text or ""):
        occurrences[tok] = occurrences.get(tok, 0) + 1
    find_example = _example_finder(text)
    chosen: dict[str, dict] = {}
    for surface, entry in (words or {}).items():
        base = entry.get("word") or {}
        sense = entry.get("sense") or {}
        rerouted = sense.get("verdict") == "different" and bool(sense.get("word"))
        w = dict(sense["word"]) if rerouted else dict(base)
        if rerouted:
            w["sense_from"] = {"lemma_id": base.get("lemma_id"), "lemma_ar": base.get("lemma_ar"),
                               "gloss_en": base.get("gloss_en")}
        if sense.get("verdict") == "name":
            continue
        if sense.get("verdict") == "broader":
            if sense.get("written"):
                w["gloss_en"] = sense["gloss_en"]
            else:
                w["gloss_here"] = w.get("gloss_here") or sense["gloss_en"]
        if (w.get("is_proper_noun") or (w.get("pos") or "").lower() in _PROPER_NOUN_POS
                or w.get("knowledge_state") in _ALREADY_LEARNING_STATES or not w.get("gloss_en")):
            continue
        key = _normalize(w.get("lemma_ar_bare") or w.get("lemma_ar") or surface)
        if not key or _is_function_word(key):
            continue
        n = occurrences.get(surface, 1)
        if key in chosen:
            c = chosen[key]
            c["count_in_text"] += n
            if surface not in c["surface_forms"]:
                c["surface_forms"].append(surface)
            continue
        w.update(surface=surface, surface_forms=[surface], count_in_text=n,
                 freq_rank=ranks.get(key), homograph_candidates=[])
        chosen[key] = w
    if selection == "distinctive":
        def score(w):
            return -w["count_in_text"] * math.log10((w["freq_rank"] or _OOV_RANK) + 10)
    else:
        def score(w):
            return (0, w["freq_rank"], 0) if w["freq_rank"] else (1, 0, -w["count_in_text"])
    out = sorted(chosen.values(), key=score)[:max(1, min(count, _MAX_WORDS))]
    for w in out:
        w["surface_forms"].sort(key=lambda f: -occurrences.get(f, 0))
        w["surface"] = w["surface_forms"][0]
        w["example_ar"] = find_example(set(w["surface_forms"]))
    return out


def _store_chips_from_prematch(snap_id: int, count: int, selection: str, include_oov: bool) -> None:
    """Suggested words from the stored page pre-match; the older discovery path
    when the pre-match is missing or failed."""
    db = SessionLocal()
    try:
        page = db.get(SnapPage, snap_id)
        if page is None:
            return
        pm = page.prematch_json or {}
        text = page.arabic_text
        if pm.get("status") != "ready":
            db.close()
            _store_snap_chips(snap_id, count, selection, include_oov)
            return
        page.words_json = chips_from_prematch(text, pm.get("words") or {}, count, selection)
        db.commit()
        log_interaction(event="snap_words_ready", snap_id=snap_id,
                        word_count=len(page.words_json), source="prematch")
    except Exception:
        logger.exception("snap suggestions from the pre-match failed for page %s", snap_id)
        db.rollback()
        page = db.get(SnapPage, snap_id)
        if page is not None and page.words_json is None:
            page.words_json = []
            db.commit()
    finally:
        db.close()


def _finish_snap(snap_id: int, count: int, selection: str, include_oov: bool) -> None:
    """After a new page is returned: the page pre-match, then the suggested
    words picked from it (one check per page, and a chip always agrees with a
    tap on the same word)."""
    from app.services.snap_prematch import run_prematch

    run_prematch(snap_id)
    _store_chips_from_prematch(snap_id, count, selection, include_oov)


def _sense_check_chips(db: Session, text: str, words: list[dict]) -> list[dict]:
    """Check the suggested words that matched a vocabulary entry against the
    page (one batched Sonnet call, as pre-read does). A widened gloss is written
    (compare-and-set); a different word replaces the chip, or drops it when the
    learner already studies that word. On a failed check the chips are kept:
    nothing is written until Add, and the add-context check covers it then."""
    from app.services.preread_service import check_senses, load_candidates, regloss

    matched = [w for w in words if w.get("lemma_id")]
    if not matched:
        return words
    items = []
    for i, w in enumerate(matched):
        item = {"index": i, "lemma_id": w.get("lemma_id"), "lemma_ar": w["lemma_ar"],
                "gloss_en": w.get("gloss_en"), "pos": w.get("pos"),
                "forms": w.get("surface_forms") or [w["surface"]]}
        candidates = load_candidates(db, w.get("homograph_candidates"))
        if candidates:
            item["candidates"] = candidates
        items.append(item)
    db.rollback()  # hold no transaction across the LLM call
    verdicts = check_senses(text, items)
    if not verdicts:
        return words
    drop: set[int] = set()
    for i, w in enumerate(matched):
        v = verdicts.get(i)
        if not v:
            continue
        if v["verdict"] == "broader":
            rg = {"lemma_id": w["lemma_id"], "regloss": {"from": w.get("gloss_en"), "to": v["gloss_en"],
                                                          "approved": v.get("approved") is True}}
            regloss(db, [rg], source="snap_chip")
            db.commit()
            if rg.get("reglossed"):
                w["gloss_en"] = v["gloss_en"]
            else:
                w["gloss_here"] = w.get("gloss_here") or v["gloss_en"]
            continue
        if v["verdict"] == "sibling":
            real = _sibling_word(db, v["lemma_id"], w["surface"])
        else:
            real = _resolve_different_word(db, w["surface"], w["lemma_id"], v)
        db.rollback()
        if real is None:
            continue
        if real.get("knowledge_state") in _ALREADY_LEARNING_STATES:
            drop.add(id(w))
            continue
        w["sense_from"] = {"lemma_id": w["lemma_id"], "lemma_ar": w["lemma_ar"],
                           "gloss_en": w.get("gloss_en")}
        for k in ("lemma_ar", "lemma_ar_bare", "gloss_en", "pos", "root", "lemma_id",
                  "knowledge_state", "transliteration", "register", "dialect", "lemma_source"):
            w[k] = real.get(k)
        w["homograph_candidates"] = []
    return [w for w in words if id(w) not in drop]


@router.post("/snap", response_model=SnapOut)
async def discover_snap(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    count: int = Query(default=5, ge=1, le=_MAX_WORDS),
    # "common_first" surfaces the most generally-frequent unknown words on the page
    # (best learning ROI, most likely to recur). "distinctive" surfaces the rarer
    # words that characterise THIS passage. include_oov keeps archaic/dialectal words
    # that aren't in the MSA frequency list — on by default for authentic texts.
    selection: str = Query(default="common_first"),
    include_oov: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    """Snap-to-read: OCR + translate a photographed Arabic page in one shot, then
    return the top unknown words (glossed) ready to add to Alif. Synchronous — the
    Gemini OCR/translation and the Haiku gloss both run before the response, so the
    reader gets translation + chips in a single ~5-8s round trip.

    The DB stays read-only here (vocabulary lookup + gloss); words are only written
    when the user taps Add, which goes through the existing /add[-batch] path.
    """
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 20MB limit")

    try:
        ocr = extract_text_and_translation(image_bytes)
    except Exception as e:
        logger.exception("snap OCR/translation failed")
        raise HTTPException(status_code=502, detail=f"OCR failed: {e}")

    arabic_text = (ocr.get("arabic_text") or "").strip()
    translation = (ocr.get("translation_en") or "").strip()
    if not arabic_text:
        raise HTTPException(status_code=422, detail="No Arabic text found in image")

    sentences = ocr.get("sentences") or []
    # The page and its translation go back as soon as they exist; the suggested
    # words (two model calls, 8-17 s) and the page pre-match follow in the
    # background. Keep the page so it survives a reload.
    snap_id = None
    try:
        page = SnapPage(
            arabic_text=arabic_text, translation_en=translation,
            sentences_json=sentences, words_json=None,
        )
        db.add(page)
        db.commit()
        snap_id = page.id
    except Exception:
        logger.exception("snap page could not be saved")
        db.rollback()
    if snap_id is None:
        # Nowhere to store background results: find the words now, as before.
        words = _snap_chips(db, arabic_text, count, selection, include_oov)
    else:
        words = []
        background_tasks.add_task(_finish_snap, snap_id, count, selection, include_oov)
    log_interaction(
        event="discover_snap", word_count=len(words), text_len=len(arabic_text),
        snap_id=snap_id, words_pending=snap_id is not None,
    )
    return {
        "snap_id": snap_id,
        "arabic_text": arabic_text,
        "translation_en": translation,
        "sentences": sentences,
        "words": words,
        "count": len(words),
        "words_pending": snap_id is not None,
    }


@router.get("/snaps")
def list_snaps(limit: int = Query(default=30, ge=1, le=200), db: Session = Depends(get_db)):
    """Recently snapped pages, newest first."""
    pages = db.query(SnapPage).order_by(SnapPage.id.desc()).limit(limit).all()
    return {"snaps": [
        {
            "snap_id": p.id,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "preview_en": (p.translation_en or "")[:140],
            "preview_ar": (p.arabic_text or "")[:80],
        }
        for p in pages
    ]}


@router.get("/snaps/{snap_id}", response_model=SnapOut)
def get_snap(snap_id: int, db: Session = Depends(get_db)):
    """A saved page, with each suggested word's current learning state."""
    page = db.get(SnapPage, snap_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Snap page not found")
    words = [dict(w) for w in (page.words_json or []) if isinstance(w, dict)]
    created = page.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    words_pending = page.words_json is None and (
        created is None or datetime.now(timezone.utc) - created < _WORDS_PENDING_MAX
    )
    ids = {w["lemma_id"] for w in words if w.get("lemma_id")}
    if ids:
        states = dict(
            db.query(UserLemmaKnowledge.lemma_id, UserLemmaKnowledge.knowledge_state)
            .filter(UserLemmaKnowledge.lemma_id.in_(ids)).all()
        )
        for w in words:
            if w.get("lemma_id"):
                w["knowledge_state"] = states.get(w["lemma_id"])
    return {
        "snap_id": page.id,
        "arabic_text": page.arabic_text,
        "translation_en": page.translation_en or "",
        "sentences": page.sentences_json or [],
        "words": words,
        "count": len(words),
        "words_pending": words_pending,
    }


@router.get("/snaps/{snap_id}/prematch")
def get_snap_prematch(
    snap_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    """Every word of a saved page, matched and sense-checked in the background
    after OCR: ``{"status": "pending"|"ready"|"failed", "words": {surface:
    {word, sense?}}}``, in the shapes /lookup and /lookup/sense return. A page
    without a pass (snapped before it existed), or whose pass failed ten or more
    minutes ago, gets one started here."""
    from app.services.snap_prematch import is_running, needs_pass, run_prematch

    page = db.get(SnapPage, snap_id)
    if page is None:
        raise HTTPException(status_code=404, detail="Snap page not found")
    pm = page.prematch_json or {}
    if is_running(snap_id):
        return {"status": "pending"}
    if needs_pass(pm):
        background_tasks.add_task(run_prematch, snap_id)
        return {"status": "pending"}
    if pm.get("status") != "ready":
        return {"status": pm.get("status") or "failed"}
    words = pm.get("words") or {}
    # Learning states change while the reader works; report the current ones.
    entries = [e.get("word") for e in words.values()] + [
        (e.get("sense") or {}).get("word") for e in words.values()
    ]
    ids = {w["lemma_id"] for w in entries if w and w.get("lemma_id")}
    states = dict(
        db.query(UserLemmaKnowledge.lemma_id, UserLemmaKnowledge.knowledge_state)
        .filter(UserLemmaKnowledge.lemma_id.in_(ids)).all()
    ) if ids else {}
    for w in entries:
        if w and w.get("lemma_id"):
            w["knowledge_state"] = states.get(w["lemma_id"])
    return {"status": "ready", "words": words}


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
    # Provenance: which consumer added this word. Defaults to "dragoman" (the
    # in-app discover screen) so existing callers keep their tag; other consumers
    # (e.g. the Bookifier glossary) pass their own to stay distinguishable.
    source: str | None = None
    # Where the learner met the word: the printed form and its sentence, stored
    # in word_add_contexts so the entry can be checked against real use.
    surface: str | None = None
    context_ar: str | None = None
    snap_id: int | None = None
    # A set phrase that gave the sentence its meaning ({"ar", "en"}); offered to the
    # word card. ``gloss_en`` stays the general gloss.
    phrase: dict | None = None


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


def _ensure_material(lemma_ids: list[int]) -> None:
    """Background: give each newly acquiring existing word enough review
    sentences. A word that already existed skips quality gating, but it may
    have no reviewable sentence, and an acquiring word without one is never
    practised. Own session; the read is closed before any LLM call."""
    from sqlalchemy import exists, or_

    from app.models import Sentence, SentenceWord
    from app.services.sentence_eligibility import reviewable_sentence_clauses

    db = SessionLocal()
    try:
        missing = []
        for lid in lemma_ids:
            have = (
                db.query(Sentence.id)
                .filter(
                    reviewable_sentence_clauses(),
                    or_(
                        Sentence.target_lemma_id == lid,
                        exists().where(
                            SentenceWord.sentence_id == Sentence.id,
                            SentenceWord.lemma_id == lid,
                        ),
                    ),
                )
                .limit(MIN_SENTENCES_PER_WORD)
                .count()
            )
            if have < MIN_SENTENCES_PER_WORD:
                missing.append((lid, MIN_SENTENCES_PER_WORD - have))
    finally:
        db.close()
    for lid, needed in missing:
        try:
            generate_material_for_word(lid, needed=needed)
        except Exception as e:
            logger.warning("discover material gen failed for %s: %s", lid, e)


def _create_and_introduce(
    db: Session,
    w: WordIn,
    lemma_lookup: dict,
    preread_set_id: int | None = None,
    existing_lemma_id: int | None = None,
) -> dict:
    """Find-or-create the canonical lemma for `w`, then introduce it immediately
    (bypassing the daily intro cap — explicit user add). Newly created lemmas are
    registered in `lemma_lookup` so a repeated word in the same batch resolves to
    the row we just made instead of being created twice.

    ``existing_lemma_id`` (internal callers only) names the lemma already
    decided, e.g. the homograph sibling pre-read's sense check chose: it is
    introduced as is, never looked up again and never created."""
    if (w.pos or "").lower() in _PROPER_NOUN_POS:
        raise ValueError(f"refusing to add proper noun {w.lemma_ar_bare!r}")
    source = (w.source or "").strip() or "dragoman"
    if existing_lemma_id is not None:
        from app.services.canonical_resolution import resolve_canonical_lemma_id

        lemma = db.get(Lemma, resolve_canonical_lemma_id(db, int(existing_lemma_id)))
        if lemma is None:
            raise ValueError(f"lemma #{existing_lemma_id} does not exist")
        return _introduce_existing(db, lemma, w, source, preread_set_id)
    bare = _normalize(w.lemma_ar_bare)
    citation_surface = (w.lemma_ar or w.lemma_ar_bare).strip()
    exact_alias_required = requires_exact_running_text_alias(
        citation_surface
    )
    # Citation-strict existence check: exact/variant/ال-prefix matches only.
    # The full lookup_lemma fuzzy path (single-letter clitic strip + CAMeL
    # last resort) mis-resolved 18 documented citation forms onto wrong
    # lemmas (لاحظ→حَظّ, كناس→نَاس …) — see
    # research/spec-2026-07-15-lookup-clitic-collision.md §7.
    existing_id = lookup_lemma_citation(
        bare,
        lemma_lookup,
        original_bare=citation_surface,
    )
    if (
        existing_id is None
        and exact_alias_required
    ):
        raise ValueError(
            "exact running-text alias destination is unavailable for "
            f"{citation_surface!r}"
        )
    created = False
    sense_rerouted_from = None
    if existing_id is not None:
        lemma = db.query(Lemma).filter(Lemma.lemma_id == existing_id).first()
        # A bare-form match can be the wrong same-skeleton homograph: مَلِك
        # "king" bare-matches مَلَك "angel" (and would no-op as already-known),
        # a verb bare-matches its masdar noun. When the caller supplied a
        # sense, hold the match to the same fail-closed gloss/POS gate that
        # corrections use; on conflict, re-route to a sense-compatible
        # same-bare sibling or fall through to creation. A same-lexeme
        # duplicate created because two gloss phrasings share no token is
        # folded back by run_quality_gates variant detection — the opposite
        # failure (silently enrolling the wrong sense) has no healer.
        # 2026-07-21 Kalila import: 5/36 curated words hit this.
        if lemma is not None and (w.gloss_en or "").strip():
            if not _candidate_matches_correction(lemma, w.gloss_en, w.pos or ""):
                if exact_alias_required:
                    # The exact citation is authoritative. A conflicting
                    # supplied sense must not reroute to the stripped-bare
                    # collision or fall through to creating an exact-source
                    # lemma that would disable the alias globally.
                    raise ValueError(
                        "supplied sense conflicts with exact running-text "
                        f"alias destination for {citation_surface!r}"
                    )
                sense_rerouted_from = existing_id
                sibling = correct_mapping(
                    db, w.lemma_ar or bare, w.gloss_en, w.pos or "",
                    current_lemma_id=existing_id, lemma_lookup=lemma_lookup,
                )
                if sibling is not None and sibling != existing_id:
                    existing_id = sibling
                    lemma = db.query(Lemma).filter(
                        Lemma.lemma_id == sibling
                    ).first()
                else:
                    existing_id = None
                    lemma = None
                logger.info(
                    "discover add: %r (%s) sense-conflicts with bare-match "
                    "#%s — %s",
                    w.lemma_ar or bare, w.gloss_en, sense_rerouted_from,
                    f"re-routed to #{existing_id}" if existing_id else
                    "treating as new lemma",
                )
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
            source=source,
        )
        db.add(lemma)
        db.flush()
        assign_frequency_rank(lemma)
        lemma_lookup[bare] = lemma.lemma_id  # in-batch dedupe
        created = True
    return _introduce_existing(
        db, lemma, w, source, preread_set_id,
        created=created, sense_rerouted_from=sense_rerouted_from,
    )


def _introduce_existing(
    db: Session,
    lemma: Lemma,
    w: WordIn,
    source: str,
    preread_set_id: int | None,
    *,
    created: bool = False,
    sense_rerouted_from: int | None = None,
) -> dict:
    """Introduce a found or just-created lemma and keep its add sentence."""
    res = introduce_word(
        db, lemma.lemma_id, source=source,
        due_immediately=True, enforce_daily_cap=True,
        learner_pick=source in LEARNER_PICK_SOURCES,
    )
    context = record_add_context(
        db, lemma.lemma_id, source, w.context_ar, surface=w.surface,
        snap_id=w.snap_id, preread_set_id=preread_set_id,
        introduced=bool(res.get("introduced_at")),
    )
    if context is not None:
        phrase = w.phrase or {}
        if (phrase.get("ar") or "").strip() and (phrase.get("en") or "").strip():
            context.phrase_json = {"ar": phrase["ar"].strip(), "en": phrase["en"].strip()}
        db.flush()
    return {
        "context_id": context.id if context is not None else None,
        "lemma_id": lemma.lemma_id,
        "lemma_ar": lemma.lemma_ar,
        "gloss_en": lemma.gloss_en,
        "source": source,
        "created": created,
        "state": res.get("state"),
        "already_known": res.get("already_known", False),
        "sense_rerouted_from": sense_rerouted_from,
    }


def _log_add(out: dict) -> None:
    """One event per Add, whether the lemma was created or already existed, so
    every consumer's adds (snap, bookifier, discover screen) can be traced.
    Named ``dragoman_word_added`` before 2026-11-15 and logged only on create."""
    log_interaction(
        event="discover_word_added",
        lemma_id=out["lemma_id"],
        lemma_ar=out["lemma_ar"],
        source=out["source"],
        created=out["created"],
        state=out["state"],
        already_known=out["already_known"],
    )


def _enrich_now(lemma_id: int, created: bool) -> None:
    """Gate and fully enrich one word, then write its word card, before replying.
    Own session: the request's session holds nothing across the model calls."""
    from app.services.word_card import enrich_word_cards_for

    session = SessionLocal()
    try:
        if created:
            run_quality_gates(session, [lemma_id], background_enrich=False)
            session.commit()
        enrich_word_cards_for(session, [lemma_id])
        session.commit()
    except Exception:
        logger.exception("enrich-now failed for %s", lemma_id)
        session.rollback()
    finally:
        session.close()


@router.post("/add")
def add_word(
    w: WordIn, background_tasks: BackgroundTasks,
    enrich: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Create/stage one word; quality-gate in the background. With ``enrich``
    the gates, enrichment and word card run before the reply (Snap's Enrich)."""
    lemma_lookup = build_comprehensive_lemma_lookup(db)
    try:
        out = _create_and_introduce(db, w, lemma_lookup)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    _log_add(out)
    # Check the entry against the sentence before material is generated for it.
    if out["context_id"]:
        background_tasks.add_task(verify_add_contexts, [out["context_id"]])
    if enrich:
        _enrich_now(out["lemma_id"], out["created"])
        if out["created"]:
            background_tasks.add_task(_ensure_material, [out["lemma_id"]])
    elif out["created"]:
        background_tasks.add_task(_gate_and_generate, [out["lemma_id"]])
    if not out["created"] and out["state"] == "acquiring" and not out["already_known"]:
        background_tasks.add_task(_ensure_material, [out["lemma_id"]])
    return out


class LookupMissIn(BaseModel):
    lemma_id: int
    surface: str | None = None
    context: str | None = None
    snap_id: int | None = None
    # Current reasons for this tap; an empty list withdraws the tap's miss.
    causes: list[str]


_LOOKUP_CAUSES = {"retrieval_lapse", "mixed_up", "unfamiliar_form", "missing_tashkeel"}
_FSRS_STATES = {"known", "learning", "lapsed"}


@router.post("/lookup/miss")
def lookup_miss(req: LookupMissIn, db: Session = Depends(get_db)):
    """Reasons the learner did not recognise a word they study, from a Snap tap.

    One row per (snap, word, sentence): a new reason list replaces it, an empty
    list removes it. A word in FSRS review gets an open missed-word entry (it
    comes back in review soon); FSRS and the review log are untouched. For a
    word still being acquired the reasons are kept as evidence only."""
    from app.models import LookupMiss
    from app.services.canonical_resolution import resolve_canonical_lemma_id

    causes = sorted({c for c in req.causes if c in _LOOKUP_CAUSES})
    lemma_id = resolve_canonical_lemma_id(db, req.lemma_id)
    if db.get(Lemma, lemma_id) is None:
        raise HTTPException(status_code=404, detail="Lemma not found")
    existing = (
        db.query(LookupMiss)
        .filter(LookupMiss.source == "snap", LookupMiss.lemma_id == lemma_id,
                LookupMiss.snap_id.is_(req.snap_id) if req.snap_id is None
                else LookupMiss.snap_id == req.snap_id,
                LookupMiss.sentence_ar.is_(None) if req.context is None
                else LookupMiss.sentence_ar == req.context)
        .first()
    )
    state = (
        db.query(UserLemmaKnowledge.knowledge_state)
        .filter(UserLemmaKnowledge.lemma_id == lemma_id).scalar()
    )
    if not causes:
        if existing is not None:
            db.delete(existing)
    elif existing is not None:
        existing.causes_json = causes
    else:
        db.add(LookupMiss(
            lemma_id=lemma_id, source="snap", surface=req.surface, sentence_ar=req.context,
            snap_id=req.snap_id, causes_json=causes, knowledge_state=state,
            opens_miss=state in _FSRS_STATES,
        ))
    db.commit()
    log_interaction(
        event="discover_lookup_miss", lemma_id=lemma_id, surface=req.surface,
        snap_id=req.snap_id, causes=causes, knowledge_state=state,
        context=(req.context or "")[:500],
    )
    return {"lemma_id": lemma_id, "causes": causes,
            "opens_miss": bool(causes) and state in _FSRS_STATES}


class WordsIn(BaseModel):
    words: list[WordIn]


@router.post("/add-batch")
def add_words(req: WordsIn, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Create/stage several words at once. Each word is committed independently
    so one failure can't roll back the rest of the batch."""
    results, new_ids, existing_ids, context_ids = [], [], [], []
    for w in req.words:
        try:
            # Each prior item commits independently and may have changed an
            # exact identity's uniqueness or source-conflict precondition.
            # Rebuild rather than carrying stale alias metadata across the
            # public batch boundary.
            lemma_lookup = build_comprehensive_lemma_lookup(db)
            out = _create_and_introduce(db, w, lemma_lookup)
            db.commit()
            results.append(out)
            _log_add(out)
            if out["context_id"]:
                context_ids.append(out["context_id"])
            if out["created"]:
                new_ids.append(out["lemma_id"])
            elif out["state"] == "acquiring" and not out["already_known"]:
                existing_ids.append(out["lemma_id"])
        except Exception as e:
            db.rollback()
            logger.warning("discover add failed for %s: %s", w.lemma_ar_bare, e)
            results.append({"lemma_ar_bare": w.lemma_ar_bare, "error": str(e)})
    if context_ids:
        background_tasks.add_task(verify_add_contexts, context_ids)
    if new_ids:
        background_tasks.add_task(_gate_and_generate, new_ids)
    if existing_ids:
        background_tasks.add_task(_ensure_material, existing_ids)
    return {"added": results, "count": len(results)}


def _add_check_out(db: Session, row: WordAddContext) -> dict | None:
    """One added word whose saved sentence uses a different word: the entry that
    was enrolled and the word the sentence actually uses (existing or proposed)."""
    v = row.check_json or {}
    added_id = v.get("checked_lemma_id") or row.lemma_id
    added = db.get(Lemma, added_id)
    if added is None or not v.get("lemma_ar"):
        return None
    if added.canonical_lemma_id:
        added = db.get(Lemma, added.canonical_lemma_id) or added
    state = (
        db.query(UserLemmaKnowledge.knowledge_state)
        .filter(UserLemmaKnowledge.lemma_id == added.lemma_id).scalar()
    )
    surface = row.surface or added.lemma_ar
    return {
        "id": row.id,
        "source": row.source,
        "surface": surface,
        "sentence_ar": row.sentence_ar,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "added": _word_out(added, surface, state, "added"),
        # A homograph sibling the check chose is an existing entry by id.
        "proposed": (
            _sibling_word(db, v["sibling_lemma_id"], surface)
            if v.get("sibling_lemma_id")
            else None
        ) or _resolve_different_word(db, surface, added.lemma_id, v),
    }


@router.get("/add-checks")
def list_add_checks(db: Session = Depends(get_db)):
    """Added words whose saved sentence uses a different word than the entry
    that was enrolled, still waiting for the learner to swap or keep."""
    rows = (
        db.query(WordAddContext)
        .filter(WordAddContext.check_verdict == "different",
                WordAddContext.resolution.is_(None))
        .order_by(WordAddContext.created_at.desc())
        .limit(50)
        .all()
    )
    items = [item for item in (_add_check_out(db, r) for r in rows) if item]
    return {"items": items, "count": len(items)}


class AddCheckResolveIn(BaseModel):
    action: Literal["swap", "keep"]


@router.post("/add-checks/{context_id}")
def resolve_add_check(
    context_id: int, req: AddCheckResolveIn, background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """``swap`` enrolls the word the sentence uses and, when this add was what
    started the wrongly matched word and it is still in acquisition, returns
    that word to the encountered pool. ``keep`` changes nothing but the record."""
    row = db.get(WordAddContext, context_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Add context not found")
    if row.check_verdict != "different" or row.resolution is not None:
        raise HTTPException(status_code=409, detail="Nothing to resolve")
    item = _add_check_out(db, row)
    if item is None:
        raise HTTPException(status_code=409, detail="Entry no longer available")
    now = datetime.now(timezone.utc)
    out: dict = {"id": context_id, "action": req.action}
    if req.action == "keep":
        row.resolution, row.resolved_at = "kept", now
        db.commit()
        log_interaction(event="add_check_resolved", context_id=context_id,
                        action="keep", lemma_id=item["added"]["lemma_id"])
        return out

    proposed = item["proposed"]
    word = WordIn(
        lemma_ar_bare=proposed["lemma_ar_bare"] or _normalize(proposed["lemma_ar"]),
        lemma_ar=proposed["lemma_ar"], gloss_en=proposed["gloss_en"], pos=proposed["pos"],
        source=row.source, surface=row.surface, context_ar=row.sentence_ar, snap_id=row.snap_id,
    )
    try:
        if proposed.get("lemma_id"):
            lid = proposed["lemma_id"]
            res = introduce_word(
                db, lid, source=row.source, due_immediately=True, enforce_daily_cap=True,
                learner_pick=row.source in LEARNER_PICK_SOURCES,
            )
            ctx = record_add_context(
                db, lid, row.source, row.sentence_ar, surface=row.surface,
                snap_id=row.snap_id, introduced=bool(res.get("introduced_at")),
            )
            db.flush()
            added = {"lemma_id": lid, "created": False, "state": res.get("state"),
                     "already_known": res.get("already_known", False),
                     "context_id": ctx.id if ctx is not None else None}
        else:
            added = _create_and_introduce(db, word, build_comprehensive_lemma_lookup(db))
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

    wrong_id = item["added"]["lemma_id"]
    released = False
    enrolled = added["state"] in ("acquiring", "learning", "known") or added["already_known"]
    if row.introduced_by_add and enrolled and wrong_id != added["lemma_id"]:
        ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == wrong_id).first()
        if ulk is not None and ulk.knowledge_state == "acquiring":
            ulk.knowledge_state = "encountered"
            ulk.acquisition_box = None
            ulk.acquisition_next_due = None
            ulk.fsrs_card_json = None
            released = True
    row = db.get(WordAddContext, context_id)
    row.resolution, row.resolved_at = "swapped", now
    row.check_json = {**(row.check_json or {}), "swapped_to": added["lemma_id"],
                      "released_lemma_id": wrong_id if released else None}
    db.commit()
    log_interaction(event="add_check_resolved", context_id=context_id, action="swap",
                    lemma_id=wrong_id, swapped_to=added["lemma_id"],
                    created=added["created"], released=released)
    if added.get("context_id"):
        background_tasks.add_task(verify_add_contexts, [added["context_id"]])
    if added["created"]:
        background_tasks.add_task(_gate_and_generate, [added["lemma_id"]])
    elif added["state"] == "acquiring" and not added["already_known"]:
        background_tasks.add_task(_ensure_material, [added["lemma_id"]])
    out.update({"lemma_id": added["lemma_id"], "created": added["created"],
                "state": added["state"], "released_lemma_id": wrong_id if released else None})
    return out
