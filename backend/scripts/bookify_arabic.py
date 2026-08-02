"""Bookify Arabic: render an Arabic chapter as a PDF with per-page glossary
of new lemmas (those not in user's known/learning vocabulary).

Two stages:

    ingest   tokenize text, lemma-lookup via Alif's pipeline, gloss unknowns
             via claude -p, emit aligned.json
    render   aligned.json → paginated HTML → PDF (WeasyPrint)
    both     ingest then render

Usage:
    python3 scripts/bookify_arabic.py ingest <input.txt> <output.json> \\
        [--title "..."] [--author "..."]
    python3 scripts/bookify_arabic.py render <aligned.json> <output.pdf>
    python3 scripts/bookify_arabic.py both <input.txt> <output.pdf> \\
        [--title "..."] [--author "..."]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
# Allow --db-path / ALIF_BOOKIFY_DB to redirect SQLAlchemy *before* importing
# anything that touches the engine.
_db_override = None
for _i, _a in enumerate(sys.argv):
    if _a == "--db-path" and _i + 1 < len(sys.argv):
        _db_override = sys.argv[_i + 1]
if not _db_override:
    _db_override = os.environ.get("ALIF_BOOKIFY_DB")
if _db_override:
    os.environ["DATABASE_URL"] = f"sqlite:///{os.path.abspath(_db_override)}"
    os.environ["ALIF_SKIP_MIGRATIONS"] = "1"

from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge
from app.services.llm import generate_completion, AllProvidersFailed
from app.services.sentence_validator import (
    _is_function_word,
    _strip_clitics,
    build_comprehensive_lemma_lookup,
    lookup_lemma_id,
    normalize_alef,
    strip_diacritics,
    strip_punctuation,
    strip_tatweel,
    tokenize_display,
)


def canonical_bare(token: str) -> str:
    """Pick a canonical bare form for an unknown token by stripping clitics.

    Takes surface-form input; returns the shortest meaningful stripped form
    (prefers forms without و/ف/ال attached). If no stripping applies, returns
    the normalized bare form.
    """
    bare = normalize_alef(strip_tatweel(strip_punctuation(strip_diacritics(token)))).strip()
    if not bare:
        return bare
    candidates = _strip_clitics(bare)
    if not candidates:
        return bare
    # Prefer forms that don't start with ال or و or ف
    ranked = sorted(
        candidates + [bare],
        key=lambda s: (
            s.startswith("ال"),
            s.startswith("و") or s.startswith("ف"),
            len(s),
        ),
    )
    return ranked[0]

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("bookify")

# Any state in ULK = the user has at least seen this lemma. Only lemmas with
# *no* ULK row at all (or state="new") count as truly unfamiliar.
SEEN_STATES = {"known", "learning", "acquiring", "encountered", "lapsed", "suspended"}

# ── Text prep ────────────────────────────────────────────────────────

_ARABIC_LETTER = re.compile(r"[\u0621-\u064A]")
_SENT_TERMINATORS = re.compile(r"([.!؟?])\s+")


def split_paragraphs(text: str) -> list[str]:
    """Split into paragraphs on blank lines, then merge any short fragments.

    Classical Arabic (Wikisource Kalila) often runs one long paragraph per
    chapter. We split on blank lines first (works for sources with breaks),
    and if the result is one giant chunk, fall back to sentence-level
    grouping into ~3-sentence display paragraphs.
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) <= 2 and any(len(p) > 1500 for p in paras):
        # One big blob — break into ~3-sentence groups
        merged = "\n".join(paras)
        sentences = _SENT_TERMINATORS.split(merged)
        # Re-glue terminator + next sentence
        glued: list[str] = []
        i = 0
        while i < len(sentences):
            chunk = sentences[i]
            if i + 1 < len(sentences) and len(sentences[i + 1]) == 1:
                chunk += sentences[i + 1]
                i += 2
            else:
                i += 1
            glued.append(chunk.strip())
        # Group into paragraphs of ~3 sentences
        out: list[str] = []
        for k in range(0, len(glued), 3):
            out.append(" ".join(glued[k:k + 3]).strip())
        return [p for p in out if p]
    return paras


# ── Lemma lookup ─────────────────────────────────────────────────────

_PROCLITIC_PREFIXES = ("و", "ف", "ب", "ل", "ك", "س", "ال")


def _function_word_after_prefix(bare: str) -> str | None:
    """If bare = <short-proclitic> + <function_word>, return the function word.

    Handles compounds like فلم (ف+لم "and/so didn't"), ولا (و+لا), فإن, ولكن...
    where classical Arabic writes the particle prefix attached to another
    function word. Alif's single-particle function_word list misses these.
    """
    for pre in _PROCLITIC_PREFIXES:
        if bare.startswith(pre) and len(bare) > len(pre):
            rest = bare[len(pre):]
            if _is_function_word(rest):
                return rest
    return None


def classify_token(
    token: str,
    lemma_lookup: dict,
    lemma_meta: dict[int, dict],
    knowledge: dict[int, str],
    known_bare_forms: set[str],
    known_lemma_ids: set[int],
) -> dict:
    """Classify a single token.

    'Known' means: ANY lemma whose normalized bare form matches this token (or a
    clitic-stripped variant) is in the user's known/learning/acquiring set.
    This avoids homograph false-positives where the lookup picks one sense and
    the user actually knows a different sense of the same surface form.
    """
    bare = strip_punctuation(strip_tatweel(strip_diacritics(token))).strip()
    if not bare or not _ARABIC_LETTER.search(bare):
        return {"surface": token, "kind": "punct"}

    bare_norm = normalize_alef(bare)

    # Bare-form match: do the user's known lemmas cover this surface in ANY sense?
    is_known_by_form = bare_norm in known_bare_forms

    # Function word direct or via single proclitic prefix — check BEFORE the
    # general noun lookup so classical compounds like فلم (ف+لم "and/so didn't")
    # don't get misclassified to homograph nouns like فِلْمٌ (modern "film").
    fn_stem = bare if _is_function_word(bare) else _function_word_after_prefix(bare_norm)
    if fn_stem:
        lemma_id = lookup_lemma_id(fn_stem, lemma_lookup)
        meta = lemma_meta.get(lemma_id) if lemma_id else None
        return {
            "surface": token,
            "lemma_id": lemma_id,
            "lemma_ar": meta["lemma_ar"] if meta else None,
            "gloss_en": meta["gloss_en"] if meta else None,
            "kind": "function",
            "status": "function",
        }

    lemma_id = lookup_lemma_id(bare, lemma_lookup)
    if lemma_id is None:
        # Even if the comprehensive lookup didn't match, the user might still
        # know a lemma with this bare form (e.g. a clitic-stripped match).
        return {
            "surface": token,
            "kind": "unknown_to_alif",
            "status": "known" if is_known_by_form else "new",
        }

    meta = lemma_meta.get(lemma_id, {})
    state = knowledge.get(lemma_id, "new")
    is_known = (
        state in SEEN_STATES
        or is_known_by_form
        or lemma_id in known_lemma_ids
    )
    return {
        "surface": token,
        "lemma_id": lemma_id,
        "lemma_ar": meta.get("lemma_ar"),
        "gloss_en": meta.get("gloss_en"),
        "pos": meta.get("pos"),
        "knowledge_state": state,
        "kind": "content",
        "status": "known" if is_known else "new",
    }


def load_db_views(db, *, assume_known_rank: int = 1000):
    """Build all the lookups we need from the DB up front.

    assume_known_rank: any Alif lemma whose frequency_rank is <= this threshold
    is treated as 'known' even if there's no ULK row. Reflects the reality that
    high-frequency Arabic words a serious learner has surely encountered, even
    if Alif's seeded vocab missed them.
    """
    log.info("Building comprehensive lemma lookup ...")
    lookup = build_comprehensive_lemma_lookup(db)
    log.info("  → %d lookup keys", len(lookup))

    log.info("Loading lemma metadata ...")
    meta: dict[int, dict] = {}
    for lemma_id, ar, ar_bare, gloss, pos, rank in db.query(
        Lemma.lemma_id, Lemma.lemma_ar, Lemma.lemma_ar_bare,
        Lemma.gloss_en, Lemma.pos, Lemma.frequency_rank,
    ).filter(Lemma.canonical_lemma_id.is_(None)):
        meta[lemma_id] = {
            "lemma_ar": ar or ar_bare,
            "gloss_en": gloss,
            "pos": pos,
            "frequency_rank": rank,
        }
    log.info("  → %d lemmas", len(meta))

    log.info("Loading user knowledge ...")
    knowledge: dict[int, str] = {}
    for lemma_id, state in db.query(
        UserLemmaKnowledge.lemma_id, UserLemmaKnowledge.knowledge_state,
    ):
        knowledge[lemma_id] = state or "new"
    log.info("  → %d known/encountered lemmas", len(knowledge))

    # Treat high-frequency lemmas as "known" regardless of ULK state.
    rank_known: set[int] = {
        lid for lid, m in meta.items()
        if m.get("frequency_rank") and m["frequency_rank"] <= assume_known_rank
    }
    log.info("  → %d high-freq lemmas (rank <= %d) assumed known",
             len(rank_known), assume_known_rank)

    # Build "known bare forms" — the set of all normalized bare forms whose
    # lemma the user knows in ANY sense. Used to dodge homograph false positives
    # (lookup picked sense A, user knows sense B with same surface).
    known_bare_forms: set[str] = set()
    known_lemma_ids: set[int] = set(rank_known)
    for lid, state in knowledge.items():
        if state in SEEN_STATES:
            known_lemma_ids.add(lid)
    for lid in known_lemma_ids:
        m = meta.get(lid)
        if not m:
            continue
        bare = strip_diacritics(m.get("lemma_ar") or "")
        bare = normalize_alef(strip_tatweel(bare))
        if bare:
            known_bare_forms.add(bare)
            if not bare.startswith("ال"):
                known_bare_forms.add("ال" + bare)
            elif len(bare) > 2:
                known_bare_forms.add(bare[2:])
    log.info("  → %d distinct known bare forms", len(known_bare_forms))

    return lookup, meta, knowledge, known_bare_forms, known_lemma_ids


# ── LLM calls (claude -p) ────────────────────────────────────────────

_GLOSS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "surface": {"type": "string"},
                    "lemma_ar_vowelized": {"type": "string"},
                    "gloss_en": {"type": "string"},
                    "pos": {"type": "string"},
                },
                "required": ["index", "surface", "lemma_ar_vowelized",
                             "gloss_en", "pos"],
            },
        },
    },
    "required": ["items"],
}

# CAMeL marks a backoff/unreliable analysis with this sentinel log-probability,
# and a defective root with O or a #-heavy skeleton. Either means "don't trust
# the lexicon entry" — the same rule alif's discover API applies.
_CAMEL_BACKOFF_LOGPROB = -90.0


def _clean_stemgloss(g: str | None) -> str:
    """SAMA glosses are underscore/semicolon-encoded: `be_concluded;be_completed`."""
    if not g or g == "NO_ANALYSIS":
        return ""
    return ", ".join(part.replace("_", " ").strip()
                     for part in g.split(";") if part.strip())


def camel_propose(surface: str) -> dict:
    """CAMeL's proposal for one surface form: citation lemma + root + POS + gloss.

    The SAMA/CALIMA lexicon that ships with CAMeL already carries an English
    `stemgloss` per analysis, so the dictionary work is a local lookup rather
    than something an LLM has to invent. Returns {} when CAMeL has nothing.
    `confident` is False for backoff analyses and defective roots, which is the
    signal for "make the LLM adjudicate this one".
    """
    from app.services.morphology import analyze_word_camel, get_best_lemma_mle

    bare = strip_punctuation(strip_tatweel(strip_diacritics(surface))).strip()
    if not bare:
        return {}
    analyses = analyze_word_camel(bare)
    if not analyses:
        return {}
    mle = get_best_lemma_mle(bare) or {}
    lex = mle.get("lex")
    a = next((x for x in analyses if x.get("lex") == lex), analyses[0])

    root = a.get("root") or ""
    logprob = a.get("lex_logprob")
    confident = (
        logprob is not None
        and logprob > _CAMEL_BACKOFF_LOGPROB
        and root not in ("", "O")
        and root.count("#") < 2
        and a.get("stemgloss") != "NO_ANALYSIS"
    )
    return {
        # CAMeL writes alif wasla in citation lemmas (ٱِنْصَرَف); normalise to
        # plain alif so the printed headword matches how the text spells it.
        "lemma_ar_vowelized": (a.get("lex") or "").replace("ٱ", "ا"),
        "gloss_en": _clean_stemgloss(a.get("stemgloss")),
        "pos": a.get("pos") or "",
        "root": root,
        "confident": confident,
    }


def gloss_unknown_words(
    unique_unknowns: list[str], context_sample: str, batch_size: int = 40,
    variants: dict[str, list[str]] | None = None,
) -> dict[str, dict]:
    """Gloss unknown surface forms: CAMeL proposes, one LLM call adjudicates.

    Returns {surface: {lemma_ar_vowelized, gloss_en, pos}}.

    Two things this gets right that a bare LLM call does not:

    1. **CAMeL goes first.** Its bundled SAMA lexicon supplies a vocalized
       citation lemma, root, POS and English gloss offline, so the LLM's job
       shrinks to picking the contextually right sense and fixing tashkeel.
    2. **Results are joined by position, not by echoed surface.** Keying on the
       surface string the model repeats back silently dropped every hamza-
       carrying form (أ/إ/آ retyped as ا) — 37 of 479 entries in the Kalila
       pilot, all of them alif-initial.

    `variants` maps a surface to other inflections of the same word found in the
    text. Pass it whenever you have them: a single form can be genuinely
    ambiguous in isolation — `نرها` alone reads as a name, but shown beside
    `يراه` and `أر` it is plainly رَأَى "to see".
    """
    if not unique_unknowns:
        return {}

    proposals = {w: camel_propose(w) for w in unique_unknowns}
    n_conf = sum(1 for p in proposals.values() if p.get("confident"))
    log.info("CAMeL proposed %d/%d lemmas (%d confident); adjudicating via LLM "
             "in batches of %d ...",
             sum(1 for p in proposals.values() if p), len(unique_unknowns),
             n_conf, batch_size)

    # Seed with CAMeL so a failed LLM batch degrades to the lexicon rather
    # than to an empty gloss.
    out: dict[str, dict] = {
        w: dict(p) for w, p in proposals.items() if p.get("gloss_en")
    }

    for batch_idx in range(0, len(unique_unknowns), batch_size):
        batch = unique_unknowns[batch_idx:batch_idx + batch_size]
        lines = []
        for i, w in enumerate(batch):
            p = proposals.get(w) or {}
            hint = ""
            if p.get("lemma_ar_vowelized"):
                hint = (f"   [CAMeL: lemma={p['lemma_ar_vowelized']}"
                        f" pos={p.get('pos') or '?'}"
                        f" gloss={p.get('gloss_en') or '?'}"
                        + ("" if p.get("confident") else " LOW-CONFIDENCE")
                        + "]")
            others = [v for v in (variants or {}).get(w, []) if v != w]
            if others:
                hint += f"   [other forms in text: {' '.join(others)}]"
            lines.append(f"{i}. {w}{hint}")
        prompt = (
            "You are an Arabic lexicographer working on a classical Arabic "
            "text. For each numbered surface form, return the dictionary "
            "citation lemma (fully vowelized with tashkeel) and a concise "
            "English gloss.\n\n"
            "A morphological analyser (CAMeL) has proposed an analysis for "
            "most entries. Accept it when it is right. Correct it when the "
            "lemma is wrong or the gloss does not fit this text's usage — "
            "entries marked LOW-CONFIDENCE are backoff guesses and are often "
            "wrong. Classical senses take precedence over modern ones.\n\n"
            f"Context from the text:\n\n{context_sample[:1200]}\n\n"
            "Return exactly one item per numbered entry, echoing its `index`:\n"
            + "\n".join(lines)
        )
        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt="You are a precise Arabic lexicographer. "
                              "Return JSON with one item per numbered entry, "
                              "echoing the given index.",
                json_schema=_GLOSS_SCHEMA,
                temperature=0.2,
                timeout=180,
                model_override="claude_haiku",
                task_type="bookify_gloss",
                cli_only=True,
            )
        except AllProvidersFailed as e:
            log.warning("  batch %d failed, keeping CAMeL proposals: %s",
                        batch_idx // batch_size + 1, e)
            continue

        applied = 0
        for item in result.get("items", []):
            idx = item.get("index")
            if not isinstance(idx, int) or not (0 <= idx < len(batch)):
                continue
            if not (item.get("gloss_en") or "").strip():
                continue
            surface = batch[idx]
            merged = dict(proposals.get(surface) or {})
            merged.update({k: item[k] for k in
                           ("lemma_ar_vowelized", "gloss_en", "pos")
                           if item.get(k)})
            merged["surface"] = surface
            out[surface] = merged
            applied += 1
        log.info("  batch %d/%d: %d/%d adjudicated",
                 batch_idx // batch_size + 1,
                 (len(unique_unknowns) + batch_size - 1) // batch_size,
                 applied, len(batch))

    log.info("Total glossed: %d / %d", len(out), len(unique_unknowns))
    return out


_TRANSLATE_ONE_SCHEMA = {
    "type": "object",
    "properties": {"translation": {"type": "string"}},
    "required": ["translation"],
}


def translate_paragraphs(paragraphs: list[str]) -> list[str]:
    """Translate Arabic paragraphs to English — one Sonnet call per paragraph.

    Why per-paragraph: a single bundled call over all paragraphs (~17K chars in,
    ~17K out) fails the CLI/JSON contract intermittently. Per-paragraph is
    smaller, retryable, and a single failure doesn't nuke the whole run.
    """
    log.info("Translating %d paragraphs via claude -p (per-paragraph) ...",
             len(paragraphs))
    out: list[str] = []
    for i, p in enumerate(paragraphs):
        if not p.strip():
            out.append("")
            continue
        prompt = (
            "Translate the following Arabic paragraph into clear, faithful, "
            "literary English. Preserve the narrative voice and proper names. "
            "Return JSON with a single field 'translation'.\n\n"
            f"{p}"
        )
        try:
            res = generate_completion(
                prompt=prompt,
                system_prompt="You are a literary translator of classical Arabic.",
                json_schema=_TRANSLATE_ONE_SCHEMA,
                temperature=0.3,
                timeout=180,
                model_override="claude_sonnet",
                task_type="bookify_translate_one",
                cli_only=True,
            )
            out.append((res.get("translation") or "").strip())
            log.info("  para %d/%d: %d → %d chars", i + 1, len(paragraphs),
                     len(p), len(out[-1]))
        except AllProvidersFailed as e:
            log.warning("  para %d/%d FAILED: %s", i + 1, len(paragraphs), e)
            out.append("")
    return out


_ALIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ar": {"type": "string"},
                    "en": {"type": "string"},
                },
                "required": ["ar", "en"],
            },
        },
    },
    "required": ["pairs"],
}


def align_paragraph(ar: str, en: str) -> list[dict]:
    """Split a paragraph into aligned AR/EN sentence pairs via Sonnet.

    Output pairs render as side-by-side rows that fit on a page — we get
    two-language visibility on every page regardless of paragraph length.
    """
    if not ar.strip() or not en.strip():
        return [{"ar": ar, "en": en}]
    prompt = (
        "Split the following Arabic paragraph and its English translation "
        "into aligned sentence-level pairs. Preserve the full content of both "
        "sides — no dropping, no paraphrasing. Each pair should be one "
        "Arabic sentence (or short clause) and the corresponding English "
        "sentence(s). Return JSON with a 'pairs' array.\n\n"
        f"ARABIC:\n{ar}\n\nENGLISH:\n{en}"
    )
    try:
        res = generate_completion(
            prompt=prompt,
            system_prompt="You are a bilingual editor aligning classical Arabic prose "
                          "to its English translation.",
            json_schema=_ALIGN_SCHEMA,
            temperature=0.2,
            timeout=240,
            model_override="claude_sonnet",
            task_type="bookify_align",
            cli_only=True,
        )
    except AllProvidersFailed as e:
        log.warning("  alignment FAILED, falling back to whole paragraph: %s", e)
        return [{"ar": ar, "en": en}]
    pairs = [
        {"ar": (p.get("ar") or "").strip(), "en": (p.get("en") or "").strip()}
        for p in res.get("pairs", [])
        if p.get("ar", "").strip()
    ]
    if not pairs:
        return [{"ar": ar, "en": en}]
    return pairs


def align_paragraphs(paragraphs_ar: list[str],
                     translations_en: list[str]) -> list[list[dict]]:
    log.info("Aligning %d paragraphs into sentence pairs ...", len(paragraphs_ar))
    out = []
    for i, (ar, en) in enumerate(zip(paragraphs_ar, translations_en)):
        pairs = align_paragraph(ar, en)
        log.info("  para %d/%d: %d pairs", i + 1, len(paragraphs_ar), len(pairs))
        out.append(pairs)
    return out


# ── Ingest ───────────────────────────────────────────────────────────

def ingest(input_path: Path, output_path: Path, *, title: str, author: str,
           do_translate: bool = True, do_gloss: bool = True,
           assume_known_rank: int = 1000) -> None:
    text = input_path.read_text(encoding="utf-8")
    paragraphs = split_paragraphs(text)
    log.info("Split into %d paragraphs", len(paragraphs))

    db = SessionLocal()
    try:
        lookup, meta, knowledge, known_bare, known_lemmas = load_db_views(
            db, assume_known_rank=assume_known_rank,
        )
    finally:
        db.close()

    classified_paras: list[list[dict]] = []
    unknowns_set: set[str] = set()
    for p in paragraphs:
        toks = tokenize_display(p)
        classified = [
            classify_token(t, lookup, meta, knowledge, known_bare, known_lemmas)
            for t in toks
        ]
        # For unknown-to-alif tokens, attach a canonical bare form (clitic-stripped)
        # so frequency counts fold الجرذ/للجرذ/والجرذ into one entry.
        for c in classified:
            if c.get("kind") == "unknown_to_alif":
                c["canonical_bare"] = canonical_bare(c["surface"])
                if c["canonical_bare"]:
                    unknowns_set.add(c["canonical_bare"])
        classified_paras.append(classified)

    log.info(
        "Classified %d tokens; %d unique surface forms unknown to Alif",
        sum(len(p) for p in classified_paras), len(unknowns_set),
    )

    # Single batch claude -p call to gloss unknowns
    context_sample = "\n".join(paragraphs[:3])
    if do_gloss:
        unknown_glosses = gloss_unknown_words(sorted(unknowns_set), context_sample)
    else:
        log.info("Skipping unknown-word glossing (--no-gloss)")
        unknown_glosses = {}

    # Patch unknowns with the glosses
    for para in classified_paras:
        for tok in para:
            if tok.get("kind") == "unknown_to_alif":
                gloss = (
                    unknown_glosses.get(tok.get("canonical_bare", ""))
                    or unknown_glosses.get(tok["surface"])
                )
                if gloss:
                    tok["lemma_ar"] = gloss.get("lemma_ar_vowelized")
                    tok["gloss_en"] = gloss.get("gloss_en")
                    tok["pos"] = gloss.get("pos")

    translations = translate_paragraphs(paragraphs) if do_translate else [""] * len(paragraphs)
    if do_translate and any(translations):
        aligned_pairs = align_paragraphs(paragraphs, translations)
    else:
        aligned_pairs = [[] for _ in paragraphs]

    # Frequency analysis of "new" lemmas. Group by (a) lemma_id when known to
    # Alif, or (b) clitic-stripped canonical bare form when unknown — so
    # surface variants like الجرذ/للجرذ/والجرذ all count toward "جرذ".
    from collections import Counter
    freq_counter: Counter = Counter()
    new_meta: dict = {}
    for para in classified_paras:
        for tok in para:
            if tok.get("status") != "new":
                continue
            if tok.get("lemma_id"):
                key = ("lemma", tok["lemma_id"])
            else:
                key = ("bare", tok.get("canonical_bare") or tok["surface"])
            freq_counter[key] += 1
            if key not in new_meta:
                new_meta[key] = {
                    "lemma_ar": tok.get("lemma_ar"),
                    "gloss_en": tok.get("gloss_en"),
                    "pos": tok.get("pos"),
                    "in_alif": tok.get("kind") != "unknown_to_alif",
                    "knowledge_state": tok.get("knowledge_state"),
                    "lemma_id": tok.get("lemma_id"),
                    "canonical_bare": tok.get("canonical_bare"),
                }
    frequency_analysis = []
    for (kind, key), count in freq_counter.most_common():
        m = new_meta[(kind, key)]
        # Display form: vowelized lemma_ar if available, else canonical bare
        display = m["lemma_ar"] or m.get("canonical_bare") or str(key)
        frequency_analysis.append({
            "key": str(key),
            "display": display,
            "count": count,
            "lemma_ar": m["lemma_ar"],
            "gloss_en": m["gloss_en"],
            "pos": m["pos"],
            "in_alif": m["in_alif"],
            "knowledge_state": m["knowledge_state"],
            "lemma_id": m["lemma_id"],
        })
    log.info("Frequency: %d distinct new lemmas/forms; top-5: %s",
             len(frequency_analysis),
             [(f["lemma_ar"] or f["key"], f["count"]) for f in frequency_analysis[:5]])

    aligned = {
        "title": title,
        "author": author,
        "paragraphs": [
            {"ar": p, "en": en, "tokens": tokens, "pairs": pairs}
            for p, en, tokens, pairs in zip(
                paragraphs, translations, classified_paras, aligned_pairs
            )
        ],
        "frequency_analysis": frequency_analysis,
        "stats": {
            "total_tokens": sum(len(p) for p in classified_paras),
            "distinct_new": len(frequency_analysis),
            "unknown_to_alif": sum(1 for f in frequency_analysis if not f["in_alif"]),
        },
    }
    output_path.write_text(json.dumps(aligned, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    log.info("Wrote %s", output_path)


# ── Render ───────────────────────────────────────────────────────────

FONT_DIR = Path(__file__).resolve().parents[1] / "data" / "fonts" / "ScheherazadeNew"


def _build_css(fmt: str = "glossary", *, body_pt: float = 15.0,
               line_height: float = 1.95, page_margin: str | None = None,
               footnotes_per_page: bool = False) -> str:
    font_url = lambda f: f"file://{FONT_DIR / f}"
    # Bilingual uses A4 landscape (wide enough for AR|EN columns to breathe).
    # Glossary/footnotes use A5 portrait (pocket reader feel).
    if fmt == "bilingual":
        page_size = "A4 landscape"
        default_margin = "18mm 20mm 20mm 20mm"
    else:
        page_size = "A5"
        default_margin = "16mm 14mm 20mm 14mm"
    page_margin = page_margin or default_margin
    return f"""
@page {{
  size: {page_size};
  margin: {page_margin};
  @bottom-center {{
    content: counter(page);
    font-family: "EB Garamond", "Georgia", serif;
    font-size: 8.5pt;
    color: #9a8f85;
    letter-spacing: 0.4pt;
  }}
}}

/* Scheherazade New — SIL's pedagogy-grade Arabic face. Precise tashkeel
   positioning, minimal ligatures (better for vocalized text). */
@font-face {{
  font-family: "ScheherazadeNew";
  src: url({font_url('ScheherazadeNew-Regular.ttf')});
  font-weight: normal;
}}
@font-face {{
  font-family: "ScheherazadeNew";
  src: url({font_url('ScheherazadeNew-Medium.ttf')});
  font-weight: 500;
}}
@font-face {{
  font-family: "ScheherazadeNew";
  src: url({font_url('ScheherazadeNew-SemiBold.ttf')});
  font-weight: 600;
}}
@font-face {{
  font-family: "ScheherazadeNew";
  src: url({font_url('ScheherazadeNew-Bold.ttf')});
  font-weight: bold;
}}

:root {{
  --ink: #1c1a17;
  --ink-muted: #6b6159;
  --ink-faint: #9a8f85;
  --rule: #e2dcd2;
  --accent: #c4941f;       /* muted saffron for subtle highlights */
}}

html {{ direction: rtl; }}
body {{
  font-family: "ScheherazadeNew", "Amiri", serif;
  font-size: {body_pt}pt;
  line-height: {line_height};  /* generous vertical room for tashkeel */
  color: var(--ink);
  text-align: justify;
  hyphens: none;
}}

/* ── Title page ───────────────────────────────────────────────────── */
.title-block {{
  text-align: center;
  margin: 22mm 0 0 0;
  page-break-after: always;
}}
.title-block .chapter-kicker {{
  font-family: "EB Garamond", "Georgia", serif;
  font-size: 9pt;
  color: var(--ink-faint);
  direction: ltr;
  letter-spacing: 2pt;
  font-variant: small-caps;
  margin-bottom: 18mm;
}}
.title-block h1 {{
  font-size: 26pt;
  font-weight: 500;
  margin: 0 0 8mm 0;
  letter-spacing: 0.3pt;
  line-height: 1.3;
}}
.title-block .author {{
  font-size: 12pt;
  color: var(--ink-muted);
  font-weight: normal;
  margin-bottom: 28mm;
}}
.title-block .ornament {{
  font-family: "EB Garamond", "Georgia", serif;
  font-size: 16pt;
  color: var(--ink-faint);
  letter-spacing: 6pt;
  direction: ltr;
}}

/* ── Preface (words-to-learn-first) ──────────────────────────────── */
.preface {{ page-break-after: always; }}
.preface-h {{
  text-align: center;
  font-size: 17pt;
  font-weight: 500;
  margin: 0 0 2mm 0;
  letter-spacing: 0.3pt;
}}
.preface-sub {{
  text-align: center;
  font-family: "EB Garamond", "Georgia", serif;
  font-size: 8.5pt;
  color: var(--ink-muted);
  margin-bottom: 8mm;
  direction: ltr;
  font-style: italic;
}}
.preface-cols {{
  column-count: 2;
  column-gap: 8mm;
  column-rule: 0.3pt solid var(--rule);
}}
.row {{
  display: flex;
  align-items: baseline;
  gap: 2mm;
  padding: 0.7mm 0;
  border-bottom: 0.2pt solid #f2ede5;
  break-inside: avoid;
}}
.row .num {{ font-family: "EB Garamond", "Georgia", serif; font-size: 7.5pt;
             color: var(--ink-faint); min-width: 5mm; text-align: right; direction: ltr; }}
.row .cnt {{ font-family: "EB Garamond", "Georgia", serif; font-size: 8pt;
             color: var(--ink-muted); min-width: 7mm; text-align: right; direction: ltr; }}
.row .ar {{ font-family: "ScheherazadeNew", serif; font-size: 13pt;
             direction: rtl; text-align: right;
             min-width: 22mm; flex-shrink: 0; line-height: 1.5; }}
.row .en {{ font-family: "EB Garamond", "Georgia", serif;
             direction: ltr; text-align: left;
             font-size: 9pt; line-height: 1.35; color: var(--ink); flex-grow: 1; }}
.row .pos {{ color: var(--ink-faint); font-size: 7.5pt; font-style: italic; }}
.row .not-in-alif {{ color: var(--accent); font-weight: bold; }}
.preface-foot {{
  font-family: "EB Garamond", "Georgia", serif;
  font-size: 7.5pt;
  color: var(--ink-faint);
  margin-top: 5mm;
  direction: ltr;
  text-align: left;
  font-style: italic;
}}
.legend-new {{
  text-decoration: underline solid rgba(196, 148, 31, 0.75);
  text-decoration-thickness: 0.6pt;
  text-underline-offset: 2pt;
  color: var(--ink);
  font-style: normal;
}}
.legend-dim {{
  text-decoration: underline dotted rgba(130, 120, 110, 0.65);
  text-decoration-thickness: 0.35pt;
  text-underline-offset: 2pt;
  color: var(--ink);
  font-style: normal;
}}

/* ── New-word marker — VERY subtle ────────────────────────────────
   Thin dotted underline in muted saffron. No background fill, no padding,
   no box. Reads as part of the running text. */
/* Two tiers of highlight:
   .tok.new    — in the preface key (saffron solid, subtle but visible)
   .tok.new-dim — new to the learner but NOT in the preface; marked with a
                   very light gray dotted underline so the reader can see
                   at a glance that it's a word to infer, not one keyed */
.tok.new {{
  text-decoration: underline solid rgba(196, 148, 31, 0.75);
  text-decoration-thickness: 0.6pt;
  text-underline-offset: 2.5pt;
}}
.tok.new-dim {{
  text-decoration: underline dotted rgba(130, 120, 110, 0.45);
  text-decoration-thickness: 0.35pt;
  text-underline-offset: 2.5pt;
}}

/* ── Glossary / reader body (single column) ──────────────────────── */
.body {{ }}
.body p {{
  margin: 0 0 5.5mm 0;
  text-indent: 0;
  orphans: 3; widows: 3;
}}

/* ── Footnotes (WeasyPrint CSS3 floats) ──────────────────────────── */
.fn {{ float: footnote; font-size: 8pt; line-height: 1.2; }}
@page {{ @footnote {{ border-top: 0.35pt solid var(--rule);
                      padding-top: 1.2mm; margin-top: 2mm; }} }}
/* Per-page numbering can't use counter(footnote): WeasyPrint scopes that
   counter to the whole document and ignores `counter-reset: footnote` in
   @page (see build.py's "TODO: define the footnote counter where it can be
   updated by page"). Instead each note carries its own number in data-n,
   which render() fills in from an actual layout pass. */
::footnote-call {{ content: {'attr(data-n)' if footnotes_per_page else 'counter(footnote)'};
                    font-size: 0.65em;
                    vertical-align: super; color: var(--accent); }}
::footnote-marker {{ content: {'attr(data-n) ". "' if footnotes_per_page else 'counter(footnote) ". "'};
                      color: var(--accent);
                      font-size: 7.5pt; }}
.fn .ar {{ font-family: "ScheherazadeNew", serif; font-size: 10pt; }}
.fn .gloss {{ font-family: "EB Garamond", "Georgia", serif;
              color: var(--ink); font-size: 8pt; }}
.fn .pos {{ color: var(--ink-faint); font-size: 7pt; font-style: italic; }}

/* ── Bilingual (sentence-pair rows, side-by-side) ────────────────── */
.bilingual {{ }}
.bi-pair {{
  display: table;
  width: 100%;
  table-layout: fixed;
  margin: 0 0 3.5mm 0;
  break-inside: avoid;
}}
.bi-para-gap {{
  height: 6mm;
  break-after: auto;
}}
.bi-para-gap .sep {{
  text-align: center;
  color: var(--ink-faint);
  letter-spacing: 8pt;
  font-size: 8pt;
  direction: ltr;
  padding-top: 2.5mm;
}}
.bi-cell {{
  display: table-cell;
  vertical-align: top;
  padding: 0 5mm;
}}
.bi-cell.ar {{
  font-family: "ScheherazadeNew", serif;
  direction: rtl;
  text-align: justify;
  font-size: 14pt;
  line-height: 2.0;           /* tashkeel needs room */
  width: 55%;
  color: var(--ink);
  border-left: 0.3pt solid var(--rule);
}}
.bi-cell.en {{
  font-family: "EB Garamond", "Georgia", "Cambria", serif;
  direction: ltr;
  text-align: justify;
  font-size: 10.5pt;
  line-height: 1.55;
  width: 45%;
  color: #2a2621;
}}
.bi-cell.en .en-drop::first-letter {{
  font-size: 1.4em;
  line-height: 1;
}}

/* ── Colophon (back page) ────────────────────────────────────────── */
.colophon {{
  page-break-before: always;
  text-align: center;
  margin-top: 45mm;
  font-family: "EB Garamond", "Georgia", serif;
  direction: ltr;
  color: var(--ink-muted);
  font-size: 9pt;
  line-height: 1.6;
}}
.colophon .rule {{
  width: 24mm;
  height: 0;
  border-top: 0.3pt solid var(--rule);
  margin: 8mm auto;
}}
.colophon .small {{ font-size: 7.5pt; color: var(--ink-faint); }}
"""



def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _render_title_block(aligned: dict) -> str:
    parts = ['<div class="title-block">']
    parts.append('<div class="chapter-kicker">Kalīla wa Dimna · Reader Edition</div>')
    parts.append(f'<h1>{_esc(aligned["title"])}</h1>')
    if aligned.get("author"):
        parts.append(f'<div class="author">{_esc(aligned["author"])}</div>')
    parts.append('<div class="ornament">· · ·</div>')
    parts.append('</div>')
    return "".join(parts)


def _render_preface(aligned: dict, preface_top_n: int) -> str:
    freq = aligned.get("frequency_analysis") or []
    stats = aligned.get("stats") or {}
    if not freq or preface_top_n <= 0:
        return ""
    rows = freq[:preface_top_n]
    parts = ['<div class="preface">']
    parts.append('<h2 class="preface-h">كلمات لتعلّمها أوّلاً</h2>')
    parts.append('<div class="preface-sub">'
                 f'Words to learn first — top {len(rows)} most frequent '
                 f'unfamiliar lemmas (of {stats.get("distinct_new", len(freq))} '
                 'total in the chapter)</div>')
    parts.append('<div class="preface-cols">')
    for i, f in enumerate(rows, 1):
        ar = f.get("display") or f.get("lemma_ar") or f.get("key") or "—"
        gloss = f.get("gloss_en") or "—"
        pos = f.get("pos") or ""
        tag = "" if f.get("in_alif") else ' <span class="not-in-alif">·</span>'
        parts.append(
            '<div class="row">'
            f'<span class="num">{i}.</span>'
            f'<span class="cnt">{f["count"]}×</span>'
            f'<span class="ar">{_esc(ar)}</span>'
            f'<span class="en">{_esc(gloss)}'
            + (f' <span class="pos">({_esc(pos)})</span>' if pos else "")
            + f'{tag}</span>'
            '</div>'
        )
    parts.append('</div>')
    parts.append('<div class="preface-foot">'
                 '· = not yet in your Alif vocabulary &nbsp;·&nbsp; '
                 'in the body text, a <span class="legend-new">saffron underline</span> '
                 'marks words introduced in this key; a '
                 '<span class="legend-dim">faint gray underline</span> marks '
                 'other unfamiliar words to infer from context'
                 '</div>')
    parts.append('</div>')
    return "".join(parts)


_PUNCT_STRIP = ".,،؛:؟!?\"'()[]«»—"


def _render_ar_with_highlights(ar_text: str, new_surfaces: set[str]) -> str:
    """Render an Arabic string, wrapping any surface in `new_surfaces` with
    the subtle-highlight span. Matching is punctuation-insensitive on both
    sides so that tokens like `الْفَيْلَسُوفِ:` still match stored
    `الْفَيْلَسُوفِ`.
    """
    # Normalize the match set once.
    norm = {s.strip(_PUNCT_STRIP) for s in new_surfaces}
    norm.discard("")
    out: list[str] = []
    for chunk in re.split(r"(\s+)", ar_text):
        if not chunk:
            continue
        if chunk.isspace():
            out.append(chunk)
            continue
        stripped = chunk.strip(_PUNCT_STRIP)
        if stripped and stripped in norm:
            out.append(f'<span class="tok new">{_esc(chunk)}</span>')
        else:
            out.append(_esc(chunk))
    return "".join(out)


def _preface_key_set(aligned: dict, preface_top_n: int) -> set[str]:
    """Return the set of tokens-keys (lemma_id-as-str or canonical-bare) that
    correspond to entries shown on the preface page. Only these will get the
    'in preface' highlight; other new-to-the-learner words get a fainter
    highlight so the reader can see at a glance which lookups the key
    contains."""
    freq = aligned.get("frequency_analysis") or []
    out: set[str] = set()
    for entry in freq[:preface_top_n]:
        lid = entry.get("lemma_id")
        if lid is not None:
            out.add(f"lemma:{lid}")
        key = entry.get("canonical_bare") or entry.get("key")
        if key:
            out.add(f"bare:{key}")
    return out


def _tok_keys(tok: dict) -> set[str]:
    keys = set()
    if tok.get("lemma_id") is not None:
        keys.add(f"lemma:{tok['lemma_id']}")
    cb = tok.get("canonical_bare") or tok.get("surface", "").strip(_PUNCT_STRIP)
    if cb:
        keys.add(f"bare:{cb}")
    return keys


def _render_ar_with_tiered_highlights(
    ar_text: str,
    preface_surfaces: set[str],
    other_new_surfaces: set[str],
) -> str:
    """Two-tier highlight: preface words get .tok.new (saffron solid);
    other new words get .tok.new-dim (thin neutral underline).
    Matching is punctuation-insensitive on both sides."""
    pref_norm = {s.strip(_PUNCT_STRIP) for s in preface_surfaces} - {""}
    other_norm = {s.strip(_PUNCT_STRIP) for s in other_new_surfaces} - {""}
    out: list[str] = []
    for chunk in re.split(r"(\s+)", ar_text):
        if not chunk:
            continue
        if chunk.isspace():
            out.append(chunk)
            continue
        stripped = chunk.strip(_PUNCT_STRIP)
        if stripped and stripped in pref_norm:
            out.append(f'<span class="tok new">{_esc(chunk)}</span>')
        elif stripped and stripped in other_norm:
            out.append(f'<span class="tok new-dim">{_esc(chunk)}</span>')
        else:
            out.append(_esc(chunk))
    return "".join(out)


def _render_bilingual(aligned: dict, preface_top_n: int) -> str:
    preface_keys = _preface_key_set(aligned, preface_top_n)
    parts = ['<div class="bilingual">']
    for pi, para in enumerate(aligned["paragraphs"]):
        pairs = para.get("pairs") or []
        if not pairs:
            pairs = [{"ar": para.get("ar", ""), "en": para.get("en", "")}]
        preface_surfaces: set[str] = set()
        other_new_surfaces: set[str] = set()
        for tok in para.get("tokens", []):
            if tok.get("status") != "new":
                continue
            surf = tok.get("surface", "")
            if not surf:
                continue
            if _tok_keys(tok) & preface_keys:
                preface_surfaces.add(surf)
            else:
                other_new_surfaces.add(surf)
        for pair in pairs:
            ar = pair.get("ar", "")
            en = pair.get("en", "") or "—"
            parts.append('<div class="bi-pair">')
            parts.append('<div class="bi-cell ar">')
            parts.append(
                _render_ar_with_tiered_highlights(ar, preface_surfaces, other_new_surfaces)
            )
            parts.append('</div>')
            parts.append(f'<div class="bi-cell en">{_esc(en)}</div>')
            parts.append('</div>')
        if pi < len(aligned["paragraphs"]) - 1:
            parts.append('<div class="bi-para-gap"><div class="sep">· · ·</div></div>')
    parts.append('</div>')
    return "".join(parts)


def footnote_key(tok: dict):
    """Identity used to decide whether a word has already been footnoted.

    Prefers, in order: alif's lemma id; the citation lemma CAMeL/the glosser
    resolved (stripped of tashkeel so spelling wobble doesn't split it); and
    only then `canonical_bare`. That last one is a regex clitic-stripper, not a
    lemmatizer — it removes و/ف/ال and pronoun suffixes but cannot unify
    inflections, so keying on it footnoted انْتَهَيْنَا / انْتَهَتِ / انْتَهَى /
    انْتَهَيْتُ as four separate words.
    """
    if tok.get("lemma_id"):
        return ("id", tok["lemma_id"])
    lemma = tok.get("lemma_ar")
    if lemma:
        key = normalize_alef(strip_tatweel(strip_diacritics(lemma))).strip()
        if key:
            return ("lemma", key)
    return ("bare", tok.get("canonical_bare") or tok["surface"])


def _render_reading_body(aligned: dict, inline_footnotes: bool,
                         *, highlight: bool = True,
                         footnote_numbers: dict[int, int] | None = None) -> str:
    """Render the running text.

    `footnote_numbers` maps a footnote's stable id to the number to print for
    it. It is only used for per-page numbering, where the number can't be known
    until the text has actually been laid out — render() supplies it from a
    previous layout pass and re-renders until the mapping stops changing.
    """
    tok_cls = "tok new" if highlight else "tok"
    fid = 0
    seen_keys: set = set()
    parts = ['<div class="body">']
    for para in aligned["paragraphs"]:
        parts.append("<p>")
        for tok in para["tokens"]:
            kind = tok.get("kind", "content")
            if kind == "punct":
                parts.append(_esc(tok["surface"]) + " ")
                continue
            status = tok.get("status", "")
            if status == "new":
                lemma_ar = tok.get("lemma_ar") or tok["surface"]
                gloss = tok.get("gloss_en") or "—"
                pos = tok.get("pos") or ""
                key = footnote_key(tok)
                already = key in seen_keys
                seen_keys.add(key)
                if already or not inline_footnotes:
                    parts.append(
                        f'<span class="{tok_cls}">{_esc(tok["surface"])}</span> '
                    )
                else:
                    attrs = ""
                    if footnote_numbers is not None:
                        shown = footnote_numbers.get(fid, fid + 1)
                        attrs = f' data-fid="{fid}" data-n="{shown}"'
                    fid += 1
                    fn = (
                        f'<span class="fn"{attrs}><span class="ar">{_esc(lemma_ar)}</span>'
                        f' <span class="gloss">— {_esc(gloss)}'
                        + (f" ({_esc(pos)})" if pos else "")
                        + "</span></span>"
                    )
                    parts.append(
                        f'<span class="{tok_cls}">{_esc(tok["surface"])}{fn}</span> '
                    )
            else:
                parts.append(f'{_esc(tok["surface"])} ')
        parts.append("</p>")
    parts.append('</div>')
    return "".join(parts)


def _render_colophon(aligned: dict) -> str:
    stats = aligned.get("stats") or {}
    parts = ['<div class="colophon">']
    parts.append(f'<div>{stats.get("total_tokens", 0):,} tokens · '
                 f'{stats.get("distinct_new", 0)} unfamiliar lemmas · '
                 f'{stats.get("unknown_to_alif", 0)} not yet in Alif</div>')
    parts.append('<div class="rule"></div>')
    parts.append('<div class="small">Typeset in Scheherazade New &amp; EB Garamond · '
                 'generated by bookify_arabic · Alif reader edition</div>')
    parts.append('</div>')
    return "".join(parts)


def render_html(aligned: dict, preface_top_n: int = 25,
                fmt: str = "glossary", *, highlight: bool = True,
                show_preface: bool = True, body_pt: float = 15.0,
                line_height: float = 1.95,
                page_margin: str | None = None,
                footnotes_per_page: bool = False,
                footnote_numbers: dict[int, int] | None = None) -> str:
    """Render aligned data to HTML for WeasyPrint.

    fmt:
      glossary  — title + preface vocab + reading body with subtle new-word
                  underlines (no backgrounds). A5 pocket-book feel.
      bilingual — title + preface + sentence-pair rows (AR right, EN left)
                  so every page shows both languages. A4, uses `pairs`
                  field written during ingest.
      footnotes — title + preface + reading body with inline CSS3 footnotes
                  for first occurrence of each new lemma. Empirical: 432
                  footnotes (full Kalila chapter) → 37 A5 pages in ~30s.
                  Don't switch to paged.js — see 2026-04-22 spike: it silently
                  drops 85-95% of body on long-paragraph content.
    """
    parts = [
        '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">',
        f'<title>{_esc(aligned["title"])}</title>',
        f'<style>{_build_css(fmt, body_pt=body_pt, line_height=line_height, page_margin=page_margin, footnotes_per_page=footnotes_per_page)}</style>',
        '</head><body>',
    ]

    parts.append(_render_title_block(aligned))
    if show_preface:
        parts.append(_render_preface(aligned, preface_top_n))

    if fmt == "bilingual":
        parts.append(_render_bilingual(aligned, preface_top_n))
    else:
        parts.append(_render_reading_body(
            aligned, inline_footnotes=(fmt == "footnotes"),
            highlight=highlight,
            footnote_numbers=(footnote_numbers if footnotes_per_page else None)))

    parts.append(_render_colophon(aligned))
    parts.append("</body></html>")
    return "".join(parts)


def _register_story(db, data: dict, dry_run: bool) -> int | None:
    """Register the chapter as an active Story so the imported words are visible.

    Without this, `introduce` produces ghosts. A lemma created with
    source='scaffold' gets no entry in word_selector._SOURCE_TIER_BONUS, so its
    priority bonus is 0.0 and it loses to every active book. The strong tier
    comes from `_active_story_lemma_ids()`, which reads StoryWord rows joined to
    an active Story — so the words have to belong to a story to be scheduled at
    all. (The 19 Kalila lemmas imported 2026-04-22 sat at times_seen=0 for a day
    until a story was hand-registered for them.)

    Idempotent on title_ar. Returns the story id.
    """
    from app.models import Story
    from app.services.sentence_validator import build_lemma_lookup
    from app.services.story_service import _create_story_words, _build_knowledge_map
    from app.models import Lemma

    title = (data.get("title") or "").strip() or None
    body = "\n".join(p.get("ar", "") for p in data.get("paragraphs", [])).strip()
    if not body:
        log.warning("  no Arabic body in aligned JSON — skipping story registration")
        return None

    existing = db.query(Story).filter(Story.title_ar == title).first() if title else None
    if existing:
        log.info("  [story exists] #%d %s — %d words",
                 existing.id, title, len(existing.words))
        return existing.id
    if dry_run:
        log.info("  [dry-run] would register story %r (%d paragraphs)",
                 title, len(data.get("paragraphs", [])))
        return None

    story = Story(
        title_ar=title,
        title_en=data.get("author") or None,
        body_ar=body,
        source="book_ocr",
        status="active",
        page_count=len(data.get("paragraphs", [])) or 1,
    )
    db.add(story)
    db.flush()

    lemmas = db.query(Lemma).filter(Lemma.canonical_lemma_id.is_(None)).all()
    total, known, func = _create_story_words(
        db, story, body, build_lemma_lookup(lemmas), _build_knowledge_map(db))
    story.total_words = total
    story.known_count = known
    db.commit()
    log.info("  [story] #%d %s — %d words (%d known, %d function)",
             story.id, title, total, known, func)
    return story.id


def introduce(input_path: Path, top_n: int = 25, dry_run: bool = False,
              register_story: bool = True) -> None:
    """Import preface top-N lemmas into Alif:
      - If missing, create a Lemma row (source='scaffold') with the
        ingested vocalized form + gloss. Runs run_quality_gates on new
        entries, same as scripts/import_scaffold_lemmas.py.
      - Create/ensure a UserLemmaKnowledge row for each with
        state='encountered' (no-op if the user already knows the lemma
        at any higher state).
      - Register the chapter as an active Story so word_selector can actually
        see the imported words (see _register_story).
    Idempotent — safe to re-run. Uses the DB that --db-path selected.
    """
    from app.database import SessionLocal, Base, engine
    from app.models import Lemma, UserLemmaKnowledge
    from app.services.lemma_quality import run_quality_gates
    from app.services.sentence_validator import (
        strip_diacritics, normalize_alef, correct_mapping,
        build_comprehensive_lemma_lookup,
    )

    data = json.loads(input_path.read_text(encoding="utf-8"))
    freq = data.get("frequency_analysis") or []
    if not freq:
        log.error("No frequency_analysis in %s — run `ingest` first.", input_path)
        return
    entries = freq[:top_n]
    log.info("Introducing top %d lemmas from %s ...", len(entries), input_path)

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        lookup = build_comprehensive_lemma_lookup(db)
        existing_bare = set(
            r[0] for r in db.query(Lemma.lemma_ar_bare).all()
        )
        existing_dia = set(
            r[0] for r in db.query(Lemma.lemma_ar).all()
        )

        resolved: list[int] = []   # lemma_ids we'll mark encountered
        created_ids: list[int] = []
        skipped = 0

        for e in entries:
            lemma_ar = e.get("lemma_ar") or e.get("display") or ""
            lemma_ar = lemma_ar.strip()
            if not lemma_ar:
                log.warning("  skip (no lemma_ar): %s", e)
                skipped += 1
                continue
            gloss = e.get("gloss_en") or ""
            pos = e.get("pos") or ""
            lemma_id = e.get("lemma_id")

            # 1) Already an Alif lemma via ingest's classification?
            if lemma_id:
                resolved.append(int(lemma_id))
                log.info("  [exists] #%d %s — %s", lemma_id, lemma_ar, gloss)
                continue

            bare = strip_diacritics(lemma_ar)
            # 2) Exact vocalized match in DB
            if lemma_ar in existing_dia:
                row = db.query(Lemma).filter(Lemma.lemma_ar == lemma_ar).first()
                if row:
                    resolved.append(row.lemma_id)
                    log.info("  [exists] #%d %s — exact match", row.lemma_id, lemma_ar)
                    continue
            # 3) Sense-checked resolution.
            #
            # Matching on the bare (undiacritized) form alone conflates
            # homographs: مَلِك "king" and مَلَك "angel" are both ملك bare, and
            # جُنْد "army" collides with the verb جَنَّدَ "to conscript". The old
            # code took `.first()` on a bare match, so importing a chapter's
            # vocabulary could mark a word the reader never met as encountered.
            # A 2026-07-21 dry run of 35 Kalila words resolved 5 of 10 bare
            # collisions to the wrong lemma.
            #
            # correct_mapping() is Alif's hardened resolver for exactly this:
            # it expands collisions, then keeps only candidates whose gloss and
            # POS are compatible (_candidate_matches_correction) — the same gate
            # that stopped شال "shawl" being "fixed" to شال "to rise". No
            # compatible candidate means the word is genuinely new.
            existing = correct_mapping(db, lemma_ar, gloss, pos,
                                       lemma_lookup=lookup)
            if existing:
                row = db.query(Lemma).filter(Lemma.lemma_id == existing).first()
                resolved.append(existing)
                log.info("  [exists] #%d %s — %s (sense-checked)",
                         existing, (row.lemma_ar if row else lemma_ar),
                         (row.gloss_en if row else "") or "")
                continue

            # 5) Otherwise create new
            if dry_run:
                log.info("  [dry-run import] %s (%s) — %s", lemma_ar, pos, gloss)
                continue
            lemma = Lemma(
                lemma_ar=lemma_ar,
                lemma_ar_bare=bare,
                gloss_en=gloss,
                pos=pos,
                source="scaffold",
            )
            db.add(lemma)
            db.flush()
            created_ids.append(lemma.lemma_id)
            resolved.append(lemma.lemma_id)
            existing_dia.add(lemma_ar)
            existing_bare.add(bare)
            log.info("  [import] #%d %s — %s [%s]",
                     lemma.lemma_id, lemma_ar, gloss, pos)

        if created_ids and not dry_run:
            db.commit()
            log.info("Running quality gates on %d new lemmas ...", len(created_ids))
            gates = run_quality_gates(db, created_ids, background_enrich=False)
            db.commit()
            log.info("  finalized=%s variants=%s stamped=%s",
                     gates.get("finalize"), gates.get("variants"),
                     gates.get("stamped"))

        # Now seed UserLemmaKnowledge rows = 'encountered' for everything we
        # resolved. No-op if the user is already at a higher state.
        seeded = 0
        upgraded = 0
        for lid in resolved:
            ulk = db.query(UserLemmaKnowledge).filter(
                UserLemmaKnowledge.lemma_id == lid,
            ).first()
            if ulk is None:
                if dry_run:
                    seeded += 1
                    continue
                from datetime import datetime
                ulk = UserLemmaKnowledge(
                    lemma_id=lid,
                    knowledge_state="encountered",
                    source="book",
                    introduced_at=datetime.utcnow(),
                )
                db.add(ulk)
                seeded += 1
            else:
                continue
        if seeded and not dry_run:
            db.commit()

        # Must come after the lemmas exist, so _create_story_words can map the
        # chapter's tokens onto the rows we just created.
        if register_story:
            _register_story(db, data, dry_run)

        log.info(
            "Done: imported=%d, resolved-existing=%d, ULK-seeded=%d, skipped=%d%s",
            len(created_ids), len(resolved) - len(created_ids), seeded, skipped,
            " (DRY RUN — no DB writes)" if dry_run else "",
        )
    finally:
        db.close()


def regloss(input_path: Path, output_path: Path | None = None,
            *, only_missing: bool = True) -> None:
    """Fill in glosses on an already-ingested aligned JSON.

    Lets a chapter that was ingested before the CAMeL-first gloss path recover
    its dropped entries without paying for a full re-ingest. Tokens are keyed
    by `canonical_bare`, so every inflection of a lemma is repaired together.
    """
    aligned = json.loads(input_path.read_text(encoding="utf-8"))
    output_path = output_path or input_path

    targets: dict[str, str] = {}       # canonical_bare -> representative surface
    for para in aligned["paragraphs"]:
        for tok in para["tokens"]:
            if tok.get("status") != "new":
                continue
            if only_missing and tok.get("gloss_en"):
                continue
            key = tok.get("canonical_bare") or tok["surface"]
            targets.setdefault(key, tok["surface"])

    if not targets:
        log.info("Nothing to regloss.")
        return

    log.info("Reglossing %d distinct lemmas ...", len(targets))
    context = " ".join(p.get("ar", "") for p in aligned["paragraphs"])[:1200]
    glosses = gloss_unknown_words(list(targets.values()), context)

    by_key = {k: glosses.get(s) for k, s in targets.items()}
    patched = 0
    for para in aligned["paragraphs"]:
        for tok in para["tokens"]:
            if tok.get("status") != "new":
                continue
            if only_missing and tok.get("gloss_en"):
                continue
            g = by_key.get(tok.get("canonical_bare") or tok["surface"])
            if not g or not g.get("gloss_en"):
                continue
            tok["lemma_ar"] = g.get("lemma_ar_vowelized") or tok.get("lemma_ar")
            tok["gloss_en"] = g["gloss_en"]
            tok["pos"] = g.get("pos") or tok.get("pos")
            if g.get("root"):
                tok["root"] = g["root"]
            patched += 1

    if output_path == input_path:
        backup = input_path.with_suffix(input_path.suffix + ".pre-regloss")
        backup.write_text(input_path.read_text(encoding="utf-8"), encoding="utf-8")
        log.info("Backed up original to %s", backup)
    output_path.write_text(json.dumps(aligned, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    still = sum(1 for k in targets if not (by_key.get(k) or {}).get("gloss_en"))
    log.info("Patched %d tokens across %d lemmas (%d still unglossed) → %s",
             patched, len(targets) - still, still, output_path)


def _footnote_placement(doc) -> tuple[list[list[str]], int]:
    """Which footnotes landed on which page, and how many calls were placed.

    Returns (per-page lists of footnote ids in layout order, total calls seen).
    A call whose id never shows up in any footnote area is a footnote whose
    body WeasyPrint dropped — it happens when the call falls near a page break,
    and it is silent, so it has to be checked for explicitly.
    """
    def areas(box, out):
        if type(box).__name__ == "FootnoteAreaBox":
            out.append(box)
            return
        for child in getattr(box, "children", ()) or ():
            areas(child, out)

    def collect(box, acc):
        el = getattr(box, "element", None)
        if el is not None and el.get("data-fid") is not None:
            fid = el.get("data-fid")
            if fid not in acc:
                acc.append(fid)
        for child in getattr(box, "children", ()) or ():
            collect(child, acc)

    pages: list[list[str]] = []
    all_calls: list[str] = []
    for page in doc.pages:
        found: list[str] = []
        for area in (lambda o: (areas(page._page_box, o), o)[1])([]):
            collect(area, found)
        pages.append(found)
        collect(page._page_box, all_calls)
    return pages, len(all_calls)


def render(input_path: Path, output_path: Path, fmt: str = "glossary",
           *, max_numbering_passes: int = 6, **html_kw) -> None:
    from weasyprint import HTML
    aligned = json.loads(input_path.read_text(encoding="utf-8"))
    per_page = html_kw.get("footnotes_per_page", False)

    numbers: dict[int, int] = {}
    html = doc = None
    for attempt in range(max_numbering_passes if per_page else 1):
        html = render_html(aligned, fmt=fmt, footnote_numbers=numbers, **html_kw)
        doc = HTML(string=html).render()
        if not per_page:
            break
        pages, _ = _footnote_placement(doc)
        settled = {int(fid): i for page in pages
                   for i, fid in enumerate(page, start=1)}
        if settled == numbers:
            log.info("Footnote numbering settled after %d pass(es)", attempt + 1)
            break
        numbers = settled
    else:
        if per_page:
            log.warning("Footnote numbering did not settle in %d passes; "
                        "the last layout is used", max_numbering_passes)

    # Integrity gate. WeasyPrint drops a footnote body when the call sits at an
    # awkward page break, without warning and without a non-zero exit — so a
    # book can ship with a call number that keys nothing.
    if fmt == "footnotes":
        placed = sum(len(p) for p in _footnote_placement(doc)[0])
        expected = html.count('class="fn"')
        if per_page and placed != expected:
            raise SystemExit(
                f"footnote integrity check failed: {expected} footnote calls "
                f"but only {placed} bodies laid out — {expected - placed} "
                f"gloss(es) would be missing from the PDF. Nudge the layout "
                f"(--line-height / --page-margin / --body-font-size) and retry.")
        if not per_page:
            log.info("%d footnote calls emitted (bodies unverified: pass "
                     "--footnotes-per-page to enable the integrity check)",
                     expected)

    debug_html = output_path.with_suffix(".html")
    debug_html.write_text(html, encoding="utf-8")
    log.info("Wrote debug HTML %s", debug_html)
    doc.write_pdf(str(output_path))
    log.info("Wrote PDF %s", output_path)


# ── CLI ──────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db-path", default=None,
                   help="SQLite path (overrides DATABASE_URL). Use prod DB pulled to data/alif.prod.db")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_in = sub.add_parser("ingest")
    p_in.add_argument("input")
    p_in.add_argument("output")
    p_in.add_argument("--title", default="نص عربي")
    p_in.add_argument("--author", default="")
    p_in.add_argument("--no-translate", action="store_true")
    p_in.add_argument("--no-gloss", action="store_true",
                      help="Skip LLM glossing of unknown surface forms (fast)")
    p_in.add_argument("--assume-known-rank", type=int, default=1000,
                      help="Assume any lemma with frequency_rank <= N is known "
                           "(default 1000). Use 0 to disable.")

    p_re = sub.add_parser("render")
    p_re.add_argument("input")
    p_re.add_argument("output")
    p_re.add_argument("--format", choices=["glossary", "bilingual", "footnotes"],
                      default="glossary",
                      help="Output layout. glossary=preface+highlighted body (fast, default). "
                           "bilingual=side-by-side AR|EN (fast, needs translation in JSON). "
                           "footnotes=inline CSS3 footnotes, first-occurrence per lemma "
                           "(~30s for ~430 footnotes via WeasyPrint).")
    p_re.add_argument("--no-highlight", action="store_true",
                      help="Drop the two-tier new-word underlines from the body. "
                           "With --format footnotes the footnote calls still key "
                           "the vocabulary, so the text reads unmarked.")
    p_re.add_argument("--no-preface", action="store_true",
                      help="Omit the words-to-learn-first table.")
    p_re.add_argument("--body-font-size", type=float, default=15.0,
                      help="Arabic body size in pt (default 15). 17-18 for print.")
    p_re.add_argument("--line-height", type=float, default=1.95,
                      help="Body line-height (default 1.95). Raise with font size "
                           "so tashkeel keeps its vertical room.")
    p_re.add_argument("--footnotes-per-page", action="store_true",
                      help="Restart footnote numbering at 1 on each page "
                           "instead of running 1..N through the whole text.")
    p_re.add_argument("--page-margin", default=None,
                      help='CSS @page margin, e.g. "18mm 16mm 22mm 16mm". '
                           "Widen for a bound edition that loses paper to the gutter.")

    p_both = sub.add_parser("both")
    p_both.add_argument("input")
    p_both.add_argument("output")
    p_both.add_argument("--title", default="نص عربي")
    p_both.add_argument("--author", default="")
    p_both.add_argument("--no-translate", action="store_true")

    p_rg = sub.add_parser(
        "regloss",
        help="Fill missing glosses on an already-ingested aligned JSON "
             "(CAMeL proposes, LLM adjudicates). Backs up in place.")
    p_rg.add_argument("input", help="aligned JSON (from ingest)")
    p_rg.add_argument("output", nargs="?", default=None,
                      help="Output JSON (default: patch input in place)")
    p_rg.add_argument("--all", action="store_true",
                      help="Re-gloss every new lemma, not just the missing ones")

    p_intro = sub.add_parser(
        "introduce",
        help="Import the preface top-N lemmas into the Alif DB as "
             "source='scaffold' (if missing) and seed UserLemmaKnowledge "
             "rows with state='encountered'. Safe to re-run (skips dupes).",
    )
    p_intro.add_argument("input", help="aligned JSON (from ingest)")
    p_intro.add_argument("--top", type=int, default=25,
                         help="Number of preface entries to import (default 25)")
    p_intro.add_argument("--dry-run", action="store_true")
    p_intro.add_argument("--no-story", action="store_true",
                         help="Skip registering the chapter as an active Story. "
                              "Only do this if a story already covers the text — "
                              "without one the imported words are invisible to "
                              "word_selector.")

    args = p.parse_args()

    if args.cmd == "ingest":
        ingest(Path(args.input), Path(args.output),
               title=args.title, author=args.author,
               do_translate=not args.no_translate,
               do_gloss=not args.no_gloss,
               assume_known_rank=args.assume_known_rank)
    elif args.cmd == "render":
        render(Path(args.input), Path(args.output), fmt=args.format,
               highlight=not args.no_highlight,
               show_preface=not args.no_preface,
               body_pt=args.body_font_size,
               line_height=args.line_height,
               page_margin=args.page_margin,
               footnotes_per_page=args.footnotes_per_page)
    elif args.cmd == "both":
        json_path = Path(args.output).with_suffix(".json")
        ingest(Path(args.input), json_path,
               title=args.title, author=args.author,
               do_translate=not args.no_translate)
        render(json_path, Path(args.output))
    elif args.cmd == "regloss":
        regloss(Path(args.input),
                Path(args.output) if args.output else None,
                only_missing=not args.all)
    elif args.cmd == "introduce":
        introduce(Path(args.input), top_n=args.top, dry_run=args.dry_run,
                  register_story=not args.no_story)


if __name__ == "__main__":
    main()
