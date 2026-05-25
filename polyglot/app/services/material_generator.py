"""LLM-driven sentence generation for due lemmas.

Picks up where ``sentence_selector.pick_sentence_for_lemma`` leaves off: when
no harvested textbook sentence covers a due lemma, this module generates one
via the configured LLM CLI and writes a ``Sentence`` + ``SentenceWord`` row
that the picker can find on the next session.

Pipeline (mirrors Alif's three-phase pattern for SQLite write-lock discipline):

    Phase 1 — DB read:   pull target lemmas + active known vocabulary, close.
    Phase 2 — LLM work:  one Sonnet call generates N sentences; Haiku verifies
                         per-position lemma mappings and reviews sentence
                         quality. No DB lock held across those calls.
    Phase 3 — DB write:  open fresh session, write verified sentences in a
                         single commit (milliseconds).

Hard invariants honored:

- **Verification mandatory** — every generated sentence passes through
  ``verify_sentence_mappings_llm``. Failures are discarded; ``mappings_verified_at``
  is only stamped on sentences whose mapping verdicts all passed. Mirror of
  Alif's "all sentence generation must go through generate_material_for_word"
  invariant.
- **Quality review mandatory** — every verified candidate passes through
  ``review_sentences_quality`` before storage. Failures are discarded and
  approvals are stamped on the row.
- **Canonical at write time** — SentenceWord.lemma_id is resolved through
  ``resolve_canonical_via_map`` before insert. Defense in depth: the picker
  re-resolves on read.
- **Gloss gate** — sentences with any content lemma lacking ``gloss_en`` are
  rejected before write.
- **No bare-word path** — sentences only; never creates ULKs or alters
  knowledge state.
- **DB lock discipline** — LLM calls happen with no SQLAlchemy session open.

Cost defaults: Sonnet for generation, Haiku for verification + quality review
(10× cheaper, and those checks are structurally simpler than generation).
Overridable via ``POLYGLOT_GEN_MODEL``, ``POLYGLOT_VERIFY_MODEL``, and
``POLYGLOT_QUALITY_MODEL``.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app import database
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.canonical_resolution import (
    resolve_canonical_lemma_id,
    resolve_canonical_via_map,
)
from app.services.lemma_quality import FUNCTION_WORD_SETS, is_noncontent_lemma
from app.services.llm_cli import call_structured_json, resolve_model
from app.services.sentence_validator import (
    Mapping,
    build_lemma_lookup,
    map_tokens_to_lemmas,
    normalize_bare,
    surface_bares_for_lemma,
    tokenize_display,
    validate_sentence,
)

log = logging.getLogger(__name__)


_MODEL_ALIASES = {
    "sonnet": "claude-sonnet-4-5-20250929",
    "haiku": "claude-haiku-4-5-20251001",
}


def _resolve_model(raw: str) -> str:
    return resolve_model(raw, _MODEL_ALIASES)


GEN_MODEL = _resolve_model(os.environ.get("POLYGLOT_GEN_MODEL", "sonnet"))
VERIFY_MODEL = _resolve_model(os.environ.get("POLYGLOT_VERIFY_MODEL", "haiku"))
# Candidate-level sentence quality review. This mirrors Alif's naturalness +
# translation gate and is deliberately separate from lemma-mapping verification.
QUALITY_MODEL = _resolve_model(os.environ.get("POLYGLOT_QUALITY_MODEL", "haiku"))
# Translation of harvested book sentences is structurally simpler than
# generation — Haiku is plenty, and 10× cheaper.
TRANSLATE_MODEL = _resolve_model(os.environ.get("POLYGLOT_TRANSLATE_MODEL", "haiku"))
GEN_TIMEOUT_S = int(os.environ.get("POLYGLOT_GEN_TIMEOUT", "240"))
VERIFY_TIMEOUT_S = int(os.environ.get("POLYGLOT_VERIFY_TIMEOUT", "180"))
QUALITY_TIMEOUT_S = int(os.environ.get("POLYGLOT_QUALITY_TIMEOUT", "180"))
TRANSLATE_TIMEOUT_S = int(os.environ.get("POLYGLOT_TRANSLATE_TIMEOUT", "180"))
# Sentences per translation LLM call. Translation is short and uniform, so we
# batch generously — one Haiku call covers a whole page's worth of fallbacks.
TRANSLATE_BATCH_SIZE = max(1, int(os.environ.get("POLYGLOT_TRANSLATE_BATCH_SIZE", "12")))

# Cap each batch to a reasonable size — Alif measured the sweet spot at 4-6
# targets per Sonnet call. More than that and the prompt context noise
# degrades per-target quality.
BATCH_WORD_SIZE = max(1, int(os.environ.get("POLYGLOT_BATCH_WORD_SIZE", "4")))

# How many sentences to request per target. The picker keeps the best; extras
# hedge against deterministic-validation failures. The cron wrapper requests 3
# per pass; the module default stays lower for manual/API calls.
SENTENCES_PER_TARGET = max(1, int(os.environ.get("POLYGLOT_SENTENCES_PER_TARGET", "2")))

# A target is considered "covered" when it has at least this many active +
# verified Sentence rows referencing it. The picker still chooses among them
# per-session; this threshold just governs the warm-cache backfill loop.
ACTIVE_TARGET = max(1, int(os.environ.get("POLYGLOT_ACTIVE_TARGET", "5")))

# Known-words sample passed to the generation prompt. Larger = more lexical
# diversity in generated sentences, but the prompt gets bigger. Bumped from
# 60 → 500 on 2026-05-21 once polyglot's engaged-vocabulary pool grew to
# ~2.4k lemmas. With only 60, Sonnet ran out of scaffold vocabulary fast and
# reached for words outside the DB (78% validation-rejection rate). Matches
# Alif's sample size — Alif has been generating well from the get-go with
# this value.
KNOWN_SAMPLE_SIZE = 500

# Minimum weight for inverse-frequency sampling. Floors a hugely-overused
# word's probability so it can still appear occasionally if the LLM really
# wants it — but most picks go to under-represented vocabulary.
MIN_SAMPLE_WEIGHT = 0.05

# Words covered by more than this many existing sentences land on the
# "avoid" list passed to the LLM as an explicit instruction. The threshold
# is computed dynamically per run (max of MEDIAN * 1.5 and ABS_FLOOR).
AVOID_ABS_FLOOR = 3
MAX_AVOID_WORDS = 30
GENERATION_MIN_CANDIDATES_PER_TARGET = 8
GENERATION_EXTRA_CANDIDATES_PER_TARGET = 5
COMMON_SCAFFOLD_SAMPLE_SIZE = 180
HIGH_UTILITY_SCAFFOLD_WORDS_BY_LANG: dict[str, tuple[str, ...]] = {
    "el": (
        "είμαι", "έχω", "κάνω", "βλέπω", "λέω", "δίνω", "παίρνω",
        "έρχομαι", "μένω", "θέλω", "μπορώ", "γίνομαι", "πηγαίνω",
        "άνθρωπος", "παιδί", "φίλος", "φίλη", "σπίτι", "μέρα", "νύχτα",
        "φωνή", "πόρτα", "δρόμος", "χρόνος", "μάτι", "χέρι", "λίγο",
    ),
    # Highest-utility Latin scaffolds: common verbs + concrete nouns + a few
    # core adjectives, so a generated sentence has natural connective tissue.
    "la": (
        "sum", "habeo", "facio", "video", "dico", "do", "possum", "venio",
        "eo", "fero", "ago", "capio", "puto", "scio",
        "vir", "puer", "puella", "homo", "femina", "dies", "annus", "tempus",
        "urbs", "rex", "bellum", "locus", "res", "manus", "pars", "verbum",
        "magnus", "bonus", "multus", "omnis", "parvus", "novus",
    ),
}
GENERATION_FUNCTION_WORDS = {
    "la": [
        # coordinating + subordinating conjunctions / particles
        "et", "ac", "atque", "que", "sed", "aut", "vel", "nec", "neque",
        "nam", "enim", "autem", "ergo", "igitur", "tamen", "itaque", "quoque",
        "si", "nisi", "ut", "ne", "cum", "quia", "quod", "quamquam", "dum",
        "non", "ne", "num", "an",
        # prepositions
        "ad", "ab", "a", "de", "ex", "e", "in", "cum", "sine", "per", "pro",
        "sub", "super", "ante", "post", "inter", "apud", "contra", "trans",
        "propter", "ob", "circum",
        # pronouns / determiners (closed class)
        "qui", "quae", "quod", "is", "ea", "id", "ille", "illa", "illud",
        "hic", "haec", "hoc", "ipse", "se", "ego", "tu", "nos", "vos",
        "meus", "tuus", "suus", "noster", "vester",
    ],
    "el": [
        "ο", "η", "το", "οι", "τα", "τον", "την", "τους", "τις",
        "του", "της", "των", "ένας", "μια", "ένα", "ενός", "μιας",
        "και", "ή", "αλλά", "μα", "ότι", "πως", "που", "γιατί",
        "όταν", "αν", "όμως", "λοιπόν", "μη", "μην", "δεν", "θα",
        "να", "ας", "με", "σε", "στο", "στη", "στην", "στον",
        "στους", "στις", "στα", "στου", "στης", "στων", "από",
        "για", "προς", "παρά", "κατά", "ως", "έως", "μετά", "πριν",
        "κάθε", "μόνο", "μόνον", "σαν", "κοντά", "μακριά", "μέσα",
        "έξω", "πάνω", "επάνω", "κάτω", "γύρω", "δίπλα", "μπροστά",
        "πίσω", "απέναντι", "εδώ", "εκεί", "κάπου", "πάντα", "ποτέ",
        "τότε", "σήμερα", "αύριο", "χθες",
    ],
}

LANG_DISPLAY = {
    "el": "Modern Greek",
    "grc": "Ancient Greek",
    "la": "Latin",
}


_warm_cache_lock = threading.Lock()
_translate_lock = threading.Lock()


def _log_dir() -> Path:
    path = Path(__file__).resolve().parents[2] / "data" / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _log_pipeline(entry: dict) -> None:
    try:
        path = _log_dir() / f"generation_pipeline_{datetime.now():%Y-%m-%d}.jsonl"
        entry = {"ts": datetime.now().isoformat(), **entry}
        with open(path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ─── Data carriers ─────────────────────────────────────────────────────────


@dataclass
class GenTarget:
    """One word we're generating sentences for. Snapshot of DB state at Phase 1
    read time so the LLM call doesn't need a DB session."""
    lemma_id: int
    lemma_form: str
    lemma_bare: str
    gloss_en: str
    pos: str
    example_src: str = ""
    example_en: str = ""


@dataclass
class GeneratedSentence:
    target_index: int       # index into the batch's target list
    text: str
    translation_en: str


# ─── LLM CALLS ─────────────────────────────────────────────────────────────


def _gen_prompt(language_code: str, targets: list[GenTarget],
                known_sample: list[str], sentences_per_target: int,
                avoid_words: Optional[list[str]] = None) -> str:
    lang = LANG_DISPLAY.get(language_code, language_code)
    known_block = ", ".join(known_sample[:KNOWN_SAMPLE_SIZE]) or "(none)"
    avoid_block = ", ".join(avoid_words) if avoid_words else "(none)"
    function_block = ", ".join(GENERATION_FUNCTION_WORDS.get(language_code, [])) or "(none)"
    target_lines: list[str] = []
    for i, t in enumerate(targets):
        line = (
            f"  {i}. {t.lemma_form}"
            + (f" ({t.pos})" if t.pos else "")
            + f" — {t.gloss_en or '(no gloss)'}"
        )
        if t.example_src and t.example_en:
            line += f"\n     intended-sense example: {t.example_src} -> {t.example_en}"
        target_lines.append(line)
    target_block = "\n".join(target_lines)
    register_instruction = _register_instruction(language_code)
    return f"""You are generating {lang} sentence cards for a language learner.

For each target lemma below, try to produce exactly {sentences_per_target}
candidate sentences that use the lemma in its primary sense. Return fewer only
when a target cannot be used naturally with the available vocabulary. These are
not drills: the accepted sentence should be worth reading. It may be
meaningful, poignant, nostalgic, beautiful, literary, surprising, insightful,
quietly funny, or just a clear human moment.

Hard constraints:
- Use the target lemma exactly once per sentence.
- Every non-target content word MUST come from the known-word pool below.
- You may use the listed function words freely for grammar. Do not use other
  connectors, particles, helper adverbs, or pronouns unless they appear in the
  known-word pool.
- If a non-target content word is not visibly present in the known-word pool,
  do not use it, even if it feels basic.
- Write a standalone complete thought with a finite verb. The learner must
  understand who did what without any previous sentence.
- Use one clear scene, observation, memory, joke, or fact. Do not maximize
  vocabulary count.
- {register_instruction} No headlines, no all-caps, no proper-noun-heavy
  contexts unless the target itself is a proper noun.
- Never return catalog/list fragments, colon-separated vocabulary lists,
  comma chains, abstract noun piles, tautologies, dictionary definitions, or
  sentences that only exist to force unrelated words into one line.
- Avoid anaphoric openings or context-dependent subjects. Do not start with
  "and then", "so", "but", or "afterwards"; avoid αυτός/αυτή/αυτό as a subject
  unless the referent appears in the same sentence.
- Prefer explicit noun subjects for third-person verbs. Do not use a
  first-person verb form with a third-person subject.
- Check article, noun, adjective, and verb agreement before returning.
- For Modern Greek, make the target token validator-safe: normally use the
  target surface form exactly as written in the Targets list, with the same
  accent/case, exactly once. This is especially important for adjectives and
  -ομαι/-ουμαι verbs, because dictionary lookup often cannot map
  feminine/neuter/plural adjective forms or third-person mediopassive verb
  forms back to the citation lemma.
- Before writing each Modern Greek sentence, do a morphology check around the
  target. If the target is an adjective, choose a nearby noun or subject that
  agrees with the target form you actually wrote. If the target is a verb
  citation form ending in -ω/-ώ/-ομαι/-ουμαι, treat that exact form as
  first-person singular present. If agreement would force an inflected target
  form, rephrase so the exact target form is grammatical.
- Do not pair a masculine adjective target such as -ος/-ικός/-μένος with a
  neuter or feminine subject. Use an explicit masculine subject from the pool
  when the target form is masculine.
- Make each sentence a grounded micro-scene: concrete, emotionally legible,
  and a little surprising, but still plausible.
- No surreal personification: revelations, signatures, machines, food, tools,
  abstract nouns, and body parts must not telephone, wait, imagine, comfort,
  demand, or perform human actions.
- Avoid random comic, sexual, violent, horror, or gross props unless the target
  itself requires them.
- Include articles and prepositions where natural; clipped textbook-heading
  style is rejected by the quality gate.
- Prefer common scaffold words from the pool for grammar. Use rare/colorful
  words sparingly, never more than one per sentence.
- The first words in the known-word pool are safe scaffolding; prefer them for
  subjects, verbs, and simple predicates.
- Avoid the listed over-represented words — pick less-used vocabulary from
  the pool when that does not damage naturalness.
- If a target cannot be used naturally within this constraint, return fewer
  sentences rather than reaching outside the pool.
- Provide a faithful English translation. Do not transliterate.

Known-words pool ({len(known_sample)} words available, all in the learner's vocabulary):
{known_block}

Allowed function words outside the pool:
{function_block}

Words to avoid (already over-represented): {avoid_block}

Targets:
{target_block}

Return JSON with this shape, one entry per generated sentence:
{{"sentences": [{{"target_index": <int>, "text": "<sentence>", "english": "<translation>"}}, ...]}}
"""


def _register_instruction(language_code: str) -> str:
    if language_code == "grc":
        return (
            "Use a classical Attic-style prose register; avoid Koine, late, "
            "or modern forms unless the target itself requires them."
        )
    if language_code == "la":
        return (
            "Use classical Latin prose in a Caesar/Cicero-style register; "
            "avoid ecclesiastical or modern Neo-Latin phrasing."
        )
    return "Stay in modern, everyday register."


def _gen_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "sentences": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "target_index": {"type": "integer"},
                        "text": {"type": "string"},
                        "english": {"type": "string"},
                    },
                    "required": ["target_index", "text", "english"],
                },
            },
        },
        "required": ["sentences"],
    }


def _call_llm(
    *,
    prompt: str,
    schema: dict,
    model: str,
    timeout_s: int,
    log_context: str,
) -> Optional[dict]:
    """Run the configured structured-output LLM CLI. None on total failure."""
    return call_structured_json(
        prompt=prompt,
        schema=schema,
        model=model,
        timeout_s=timeout_s,
        log_context=log_context,
        runner=subprocess.run,
    )


def generate_sentences_batch(
    language_code: str,
    targets: list[GenTarget],
    known_sample: list[str],
    sentences_per_target: int = SENTENCES_PER_TARGET,
    avoid_words: Optional[list[str]] = None,
) -> list[GeneratedSentence]:
    """One Sonnet call → list of (target_index, text, english).

    Returns ``[]`` on total LLM failure. Per-target zero-coverage is signaled
    by an empty list for that index — caller decides how to log/backoff.
    """
    if not targets:
        return []
    prompt = _gen_prompt(language_code, targets, known_sample,
                        sentences_per_target, avoid_words=avoid_words)
    started = time.time()
    structured = _call_llm(
        prompt=prompt,
        schema=_gen_schema(),
        model=GEN_MODEL,
        timeout_s=GEN_TIMEOUT_S,
        log_context="material_generation",
    )
    elapsed = time.time() - started

    if not structured:
        _log_pipeline({
            "event": "gen_batch_failed",
            "language_code": language_code,
            "target_lemma_ids": [t.lemma_id for t in targets],
            "elapsed_s": round(elapsed, 1),
            "model": GEN_MODEL,
        })
        return []

    out: list[GeneratedSentence] = []
    for item in structured.get("sentences", []) or []:
        if not isinstance(item, dict):
            continue
        tid = item.get("target_index")
        text = (item.get("text") or "").strip()
        english = (item.get("english") or "").strip()
        if not isinstance(tid, int) or not (0 <= tid < len(targets)) or not text:
            continue
        out.append(GeneratedSentence(target_index=tid, text=text, translation_en=english))

    _log_pipeline({
        "event": "gen_batch_returned",
        "language_code": language_code,
        "target_lemma_ids": [t.lemma_id for t in targets],
        "requested_per_target": sentences_per_target,
        "sentences_returned": len(out),
        "elapsed_s": round(elapsed, 1),
        "model": GEN_MODEL,
    })
    return out


# ─── Sentence Quality Review ───────────────────────────────────────────────


@dataclass
class SentenceQualityReview:
    sentence_index: int
    natural: bool
    translation_correct: bool
    reason: str


def _quality_prompt(language_code: str, items: list[dict]) -> str:
    lang = LANG_DISPLAY.get(language_code, language_code)
    block_lines = []
    for it in items:
        target_line = ""
        if it.get("target") or it.get("target_gloss"):
            target_line = (
                f"\n    target: {it.get('target') or '(unknown)'}"
                f" — {it.get('target_gloss') or '(no gloss)'}"
            )
        block_lines.append(
            f"[{it['id']}] {lang}: {it['text']}\n"
            f"    English: {it['english'] or '(missing)'}"
            f"{target_line}"
        )
    items_block = "\n\n".join(block_lines)
    return f"""Review each {lang} sentence for a language-learning review card.

The learner sees one standalone sentence and its English translation. Be
strict: the corpus is generated in bulk, so rejecting a weak candidate is cheap.

For each item, judge:
1. `natural`: grammatically correct, complete, and something a real speaker
   could plausibly write or say in some ordinary, textbook, historical, or
   explanatory context.
2. `translation_correct`: the English faithfully matches the source sentence.

Reject with `natural=false` for:
- Semantic nonsense or word salad: words individually fit but the proposition
  does not form a coherent scene or claim.
- Forced vocabulary combinations, especially abstract noun chains like
  "the work constitutes supervision by the center" with no plausible context.
- Catalog/list fragments: comma-separated nouns, headings, labels, or glossary
  snippets without a real predicate and complete thought.
- Context-dependent/anaphoric sentences that need a previous sentence to tell
  who did what.
- Tautologies, dictionary-entry paraphrases, or examples that merely restate
  the target gloss.
- Invented proper-name-like phrases built from content words.
- Surreal personification or random-prop weirdness: inanimate/abstract things
  acting like people, or comic/violent/gross props that appear only because the
  generator had the words available.
- Grammar/agreement/case/preposition errors or wrong register for the language.
- Wrong target sense when a target gloss is supplied.

Accept simple or textbook-like sentences when they are coherent and complete.

Return one review per item, keyed by the bracketed id.

Sentences:
{items_block}
"""


def _quality_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "reviews": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "natural": {"type": "boolean"},
                        "translation_correct": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "natural", "translation_correct", "reason"],
                },
            },
        },
        "required": ["reviews"],
    }


def _fail_closed_quality_reviews(count: int, reason: str) -> list[SentenceQualityReview]:
    return [
        SentenceQualityReview(
            sentence_index=i,
            natural=False,
            translation_correct=False,
            reason=reason,
        )
        for i in range(count)
    ]


def review_sentences_quality(
    language_code: str,
    sentences: list[dict],
) -> list[SentenceQualityReview]:
    """Review candidate sentences for naturalness + translation fidelity.

    `sentences` entries should contain `text` and `english`; `target` and
    `target_gloss` are optional sense anchors. On total LLM failure or an
    incomplete response, fail closed so generated material never becomes
    reviewable just because the reviewer was unavailable.
    """
    if not sentences:
        return []

    items = [
        {
            "id": i,
            "text": (s.get("text") or "").strip(),
            "english": (s.get("english") or s.get("translation_en") or "").strip(),
            "target": (s.get("target") or "").strip(),
            "target_gloss": (s.get("target_gloss") or "").strip(),
        }
        for i, s in enumerate(sentences)
    ]
    started = time.time()
    structured = _call_llm(
        prompt=_quality_prompt(language_code, items),
        schema=_quality_schema(),
        model=QUALITY_MODEL,
        timeout_s=QUALITY_TIMEOUT_S,
        log_context="material_quality",
    )
    elapsed = time.time() - started
    if not structured:
        _log_pipeline({
            "event": "quality_failed",
            "language_code": language_code,
            "candidate_count": len(sentences),
            "elapsed_s": round(elapsed, 1),
            "model": QUALITY_MODEL,
        })
        return _fail_closed_quality_reviews(len(sentences), "quality review unavailable")

    raw_reviews = structured.get("reviews", []) if isinstance(structured, dict) else []
    if not isinstance(raw_reviews, list):
        return _fail_closed_quality_reviews(len(sentences), "quality review parse error")

    by_id: dict[int, SentenceQualityReview] = {}
    for item in raw_reviews:
        if not isinstance(item, dict):
            continue
        idx = item.get("id")
        if not isinstance(idx, int) or not (0 <= idx < len(sentences)):
            continue
        by_id[idx] = SentenceQualityReview(
            sentence_index=idx,
            natural=bool(item.get("natural", False)),
            translation_correct=bool(item.get("translation_correct", False)),
            reason=str(item.get("reason", ""))[:500],
        )

    if set(by_id) != set(range(len(sentences))):
        missing = sorted(set(range(len(sentences))) - set(by_id))
        _log_pipeline({
            "event": "quality_incomplete",
            "language_code": language_code,
            "candidate_count": len(sentences),
            "missing": missing[:20],
            "elapsed_s": round(elapsed, 1),
            "model": QUALITY_MODEL,
        })
        return _fail_closed_quality_reviews(len(sentences), "quality review incomplete")

    _log_pipeline({
        "event": "quality_returned",
        "language_code": language_code,
        "candidate_count": len(sentences),
        "approved": sum(
            1 for r in by_id.values()
            if r.natural and r.translation_correct
        ),
        "elapsed_s": round(elapsed, 1),
        "model": QUALITY_MODEL,
    })
    return [by_id[i] for i in range(len(sentences))]


# ─── Verification ──────────────────────────────────────────────────────────


@dataclass
class VerifyDecision:
    sentence_index: int
    position: int
    verdict: str        # "ok" / "wrong" / "unclear"
    correct_lemma: Optional[str] = None
    reason: Optional[str] = None


def _verify_prompt(language_code: str, items: list[dict]) -> str:
    lang = LANG_DISPLAY.get(language_code, language_code)
    block_lines = []
    for it in items:
        block_lines.append(
            f"[s={it['sentence_index']}, p={it['position']}] sentence: «{it['sentence_text']}»\n"
            f"    surface: {it['surface']}\n"
            f"    proposed lemma: {it['proposed_lemma']}"
            + (f"  (gloss: {it['proposed_gloss']})" if it.get("proposed_gloss") else "")
        )
    items_block = "\n".join(block_lines)
    return f"""You are a {lang} lemmatization quality gate for *generated* sentences.

For each token below, decide whether the proposed lemma is correct in this
sentence's context.

Rules:
- verdict "ok": proposed lemma is the correct citation form for this surface
  in this sentence.
- verdict "wrong": there's a clearly better citation form. Put it in
  `correct_lemma` (one word, with proper accents/diacritics).
- verdict "unclear": ambiguous (homograph the sentence doesn't disambiguate)
  or the surface isn't a real {lang} word. Leave correct_lemma blank.

Each decision must reference the bracketed (s,p) pair so we can route it back.
Skip nothing.

Tokens:
{items_block}
"""


def _verify_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sentence_index": {"type": "integer"},
                        "position": {"type": "integer"},
                        "verdict": {"type": "string", "enum": ["ok", "wrong", "unclear"]},
                        "correct_lemma": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["sentence_index", "position", "verdict"],
                },
            },
        },
        "required": ["decisions"],
    }


def verify_sentence_mappings_llm(
    language_code: str,
    candidates: list[dict],
    lemma_by_id: dict[int, Lemma],
) -> Optional[list[list[VerifyDecision]]]:
    """One Haiku call covering all (sentence × content-position) tuples.

    ``candidates`` is a list of ``{"text": str, "mappings": list[Mapping]}``.
    Returns a parallel list — one ``list[VerifyDecision]`` per candidate — or
    ``None`` on total LLM failure (caller MUST discard all candidates in that
    case; this preserves Hard Invariant "verification failure ≠ success").
    """
    if not candidates:
        return []
    items: list[dict] = []
    expected_positions: set[tuple[int, int]] = set()
    function_words = FUNCTION_WORD_SETS.get(language_code, set())
    for s_idx, cand in enumerate(candidates):
        for m in cand["mappings"]:
            if m.lemma_id is None:
                continue
            if normalize_bare(m.surface_form, language_code) in function_words:
                continue
            lemma = lemma_by_id.get(m.lemma_id)
            if lemma is None:
                log.warning(
                    "Verification snapshot missing lemma_id=%s for candidate=%s position=%s",
                    m.lemma_id, s_idx, m.position,
                )
                return None
            if is_noncontent_lemma(
                lemma,
                language_code=language_code,
                function_words=function_words,
            ):
                continue
            expected_positions.add((s_idx, m.position))
            items.append({
                "sentence_index": s_idx,
                "position": m.position,
                "sentence_text": cand["text"],
                "surface": m.surface_form,
                "proposed_lemma": lemma.lemma_form,
                "proposed_gloss": lemma.gloss_en or "",
            })

    if not items:
        # Nothing content-bearing to verify. Treat as all-ok per candidate.
        return [[] for _ in candidates]

    started = time.time()
    structured = _call_llm(
        prompt=_verify_prompt(language_code, items),
        schema=_verify_schema(),
        model=VERIFY_MODEL,
        timeout_s=VERIFY_TIMEOUT_S,
        log_context="material_verification",
    )
    elapsed = time.time() - started
    if not structured:
        _log_pipeline({
            "event": "verify_failed",
            "language_code": language_code,
            "candidate_count": len(candidates),
            "tokens": len(items),
            "elapsed_s": round(elapsed, 1),
            "model": VERIFY_MODEL,
        })
        return None

    per_candidate: list[list[VerifyDecision]] = [[] for _ in candidates]
    for d in structured.get("decisions", []) or []:
        if not isinstance(d, dict):
            continue
        s_idx = d.get("sentence_index")
        pos = d.get("position")
        verdict = d.get("verdict")
        if not isinstance(s_idx, int) or not isinstance(pos, int):
            continue
        if not (0 <= s_idx < len(candidates)):
            continue
        if (s_idx, pos) not in expected_positions:
            continue
        if verdict not in ("ok", "wrong", "unclear"):
            continue
        per_candidate[s_idx].append(VerifyDecision(
            sentence_index=s_idx,
            position=pos,
            verdict=verdict,
            correct_lemma=(d.get("correct_lemma") or None),
            reason=(d.get("reason") or None),
        ))

    covered_positions = {
        (decision.sentence_index, decision.position)
        for verdicts in per_candidate
        for decision in verdicts
    }
    if covered_positions != expected_positions:
        missing = sorted(expected_positions - covered_positions)[:20]
        extra = sorted(covered_positions - expected_positions)[:20]
        _log_pipeline({
            "event": "verify_incomplete",
            "language_code": language_code,
            "candidate_count": len(candidates),
            "expected": len(expected_positions),
            "covered": len(covered_positions),
            "missing": missing,
            "extra": extra,
            "elapsed_s": round(elapsed, 1),
            "model": VERIFY_MODEL,
        })
        return None

    _log_pipeline({
        "event": "verify_returned",
        "language_code": language_code,
        "candidate_count": len(candidates),
        "tokens": len(items),
        "decisions": sum(len(p) for p in per_candidate),
        "elapsed_s": round(elapsed, 1),
        "model": VERIFY_MODEL,
    })
    return per_candidate


def _wrong_verdict_rejects_candidate(
    decision: VerifyDecision,
    mappings: list[Mapping],
    lemma_by_id: dict[int, Lemma],
    *,
    language_code: str,
    function_words: set[str],
) -> bool:
    """Whether a verifier ``wrong`` decision should discard a candidate.

    The verifier is useful for catching content-lemma mistakes, but production
    logs show it sometimes returns ``wrong`` for closed-class tokens while
    proposing the same lemma (articles, prepositions, adverbs such as κοντά).
    Those are not retrieval targets and should not tank an otherwise good
    generated sentence. Keep failing closed for real content corrections.
    """
    mapping = next((m for m in mappings if m.position == decision.position), None)
    if mapping is None or mapping.lemma_id is None:
        return False
    if normalize_bare(mapping.surface_form, language_code) in function_words:
        return False
    lemma = lemma_by_id.get(mapping.lemma_id)
    if lemma is None:
        return True
    if is_noncontent_lemma(
        lemma,
        language_code=language_code,
        function_words=function_words,
    ):
        return False
    correct_bare = normalize_bare(decision.correct_lemma or "", language_code)
    if correct_bare and correct_bare in function_words:
        return False
    proposed_bares = {
        lemma.lemma_bare or "",
        normalize_bare(lemma.lemma_form or "", language_code),
    }
    if correct_bare and correct_bare in proposed_bares:
        return False
    return True


# ─── Orchestration ─────────────────────────────────────────────────────────


def _snapshot_known_pool(
    db: Session, language_code: str, exclude_lemma_ids: set[int],
) -> list[dict]:
    """Full engaged-vocabulary pool. Each entry is
    ``{"lemma_id": int, "lemma_form": str}``.

    Drawn from acquiring/learning/known ULK states (the lemmas the learner
    has *touched*), filtered to canonical lemmas (skip variants — they re-use
    the canonical's identity downstream). Function words + proper names are
    NOT excluded here; the validator handles those separately and they
    sometimes belong inside generated sentences as connectives.

    Returns the full pool so caller can apply inverse-frequency weighting.
    No SQLite limit — at ~2.4k engaged lemmas this is still <1ms.
    """
    rows = (
        db.query(
            Lemma.lemma_id,
            Lemma.lemma_form,
            Lemma.lemma_bare,
            Lemma.pos,
            Lemma.frequency_rank,
        )
        .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        .filter(
            Lemma.language_code == language_code,
            Lemma.canonical_lemma_id.is_(None),
            UserLemmaKnowledge.knowledge_state.in_(["acquiring", "learning", "known"]),
        )
    )
    if exclude_lemma_ids:
        rows = rows.filter(~Lemma.lemma_id.in_(exclude_lemma_ids))
    return [
        {
            "lemma_id": lid,
            "lemma_form": lf,
            "lemma_bare": lb,
            "pos": pos,
            "frequency_rank": frequency_rank,
        }
        for lid, lf, lb, pos, frequency_rank in rows.all()
        if lf
    ]


def _content_sentence_counts(db: Session, language_code: str) -> dict[int, int]:
    """For every lemma_id, how many active+verified Sentence rows reference
    it. Powers the inverse-frequency weighting AND the avoid-list — words
    that already appear in many sentences should be down-weighted (don't
    keep generating more material for already-saturated vocabulary).
    """
    from sqlalchemy import func
    rows = (
        db.query(SentenceWord.lemma_id, func.count(func.distinct(Sentence.id)))
        .join(Sentence, Sentence.id == SentenceWord.sentence_id)
        .filter(
            Sentence.language_code == language_code,
            Sentence.is_active.is_(True),
            Sentence.mappings_verified_at.isnot(None),
            SentenceWord.lemma_id.isnot(None),
        )
        .group_by(SentenceWord.lemma_id)
        .all()
    )
    return {lid: cnt for lid, cnt in rows}


def _sample_known_words_weighted(
    pool: list[dict],
    counts: dict[int, int],
    sample_size: int = KNOWN_SAMPLE_SIZE,
    language_code: str = "el",
) -> list[str]:
    """Inverse-frequency weighted sampling. Words appearing in many existing
    sentences get lower probability so the LLM is nudged toward
    under-represented vocabulary. Jittered to keep selection non-deterministic
    across runs. Returns lemma_form strings.

    Ported from Alif's ``sample_known_words_weighted`` in sentence_generator.py
    — same shape, same weighting law, smaller surface to keep polyglot lean.
    """
    import random
    if len(pool) <= sample_size:
        return _ensure_high_utility_scaffold_words(
            [w["lemma_form"] for w in pool],
            pool,
            sample_size,
            language_code,
        )

    common_limit = min(COMMON_SCAFFOLD_SAMPLE_SIZE, max(0, sample_size // 2))
    common_pool = [
        w for w in pool
        if w.get("frequency_rank") is not None
    ]
    common_pool.sort(key=lambda w: int(w.get("frequency_rank") or 10**9))
    common = common_pool[:common_limit]
    common_ids = {w["lemma_id"] for w in common}
    rest_pool = [w for w in pool if w["lemma_id"] not in common_ids]

    weighted: list[tuple[float, dict]] = []
    for w in rest_pool:
        cnt = counts.get(w["lemma_id"], 0)
        weight = max(MIN_SAMPLE_WEIGHT, 1.0 / (1 + cnt))
        jittered = weight * random.uniform(0.5, 1.5)
        weighted.append((jittered, w))
    weighted.sort(key=lambda x: x[0], reverse=True)
    return _ensure_high_utility_scaffold_words(
        [
            *[w["lemma_form"] for w in common],
            *[w["lemma_form"] for _, w in weighted[:max(0, sample_size - len(common))]],
        ],
        pool,
        sample_size,
        language_code,
    )


def _high_utility_words(language_code: str) -> tuple[str, ...]:
    return HIGH_UTILITY_SCAFFOLD_WORDS_BY_LANG.get(language_code, ())


def _high_utility_scaffold_bares(language_code: str) -> set[str]:
    return {normalize_bare(w, language_code) for w in _high_utility_words(language_code)}


def _ensure_high_utility_scaffold_words(
    sample: list[str],
    pool: list[dict],
    sample_size: int,
    language_code: str,
) -> list[str]:
    scaffold_bares = _high_utility_scaffold_bares(language_code)
    if not scaffold_bares or not sample:
        return sample[:sample_size]

    available: dict[str, str] = {}
    for item in pool:
        form = item.get("lemma_form") or ""
        if not form:
            continue
        for bare in {
            item.get("lemma_bare") or normalize_bare(form, language_code),
            normalize_bare(form, language_code),
        }:
            if bare in scaffold_bares:
                available.setdefault(bare, form)

    ordered_scaffolds: list[str] = []
    seen_bares: set[str] = set()
    for word in _high_utility_words(language_code):
        bare = normalize_bare(word, language_code)
        form = available.get(bare)
        if form and bare not in seen_bares:
            ordered_scaffolds.append(form)
            seen_bares.add(bare)

    result = ordered_scaffolds[:sample_size]
    result_bares = {normalize_bare(form, language_code) for form in result}
    for form in sample:
        form_bare = normalize_bare(form, language_code)
        if form_bare in result_bares:
            continue
        result.append(form)
        result_bares.add(form_bare)
        if len(result) >= sample_size:
            break
    return result[:sample_size]


def _known_pool_bare_forms(pool: list[dict], language_code: str) -> set[str]:
    bares: set[str] = set()
    for item in pool:
        form = item.get("lemma_form") or ""
        lemma_bare = item.get("lemma_bare") or normalize_bare(form, language_code)
        pos = item.get("pos") or ""
        for bare in surface_bares_for_lemma(language_code, lemma_bare, pos):
            if bare:
                bares.add(bare)
        form_bare = normalize_bare(form, language_code)
        if form_bare:
            bares.add(form_bare)
    return bares


def _compute_avoid_words(
    pool: list[dict],
    counts: dict[int, int],
    language_code: str,
) -> Optional[list[str]]:
    """Return up to MAX_AVOID_WORDS lemma_form strings the LLM should avoid.

    Threshold: a lemma is "over-represented" if its sentence count is at
    least max(median * 1.5, AVOID_ABS_FLOOR). Ports the threshold logic from
    Alif's ``get_avoid_words`` so the polyglot corpus stays diverse for the
    same reasons.
    """
    import statistics
    if not counts:
        return None
    vals = sorted(counts.values())
    median = statistics.median(vals)
    threshold = max(median * 1.5, AVOID_ABS_FLOOR)
    by_id_to_form = {w["lemma_id"]: w["lemma_form"] for w in pool}
    over = [
        (lid, cnt) for lid, cnt in counts.items()
        if cnt >= threshold and lid in by_id_to_form
    ]
    over.sort(key=lambda x: x[1], reverse=True)
    scaffold_bares = _high_utility_scaffold_bares(language_code)
    result: list[str] = []
    for lid, _cnt in over:
        form = by_id_to_form[lid]
        if normalize_bare(form, language_code) in scaffold_bares:
            continue
        result.append(form)
        if len(result) >= MAX_AVOID_WORDS:
            break
    return result or None


def batch_generate_material(
    language_code: str,
    lemma_ids: list[int],
    sentences_per_target: int = SENTENCES_PER_TARGET,
) -> dict:
    """Generate sentences for ``lemma_ids`` (one Sonnet + one Haiku call total).

    Returns ``{"generated": N, "words_covered": N, "words_failed": [ids]}``.
    Idempotent at the picker level — duplicates of an existing Sentence row
    would be picked by the picker just as well, but we don't dedupe by text
    here. If the cron loop runs frequently, ``warm_sentence_cache`` already
    filters out lemmas that already meet ``ACTIVE_TARGET``.
    """
    if not lemma_ids:
        return {"generated": 0, "words_covered": 0, "words_failed": []}

    # ── Phase 1: DB read ──
    db = database.SessionLocal()
    try:
        target_lemmas: dict[int, Lemma] = {
            l.lemma_id: l
            for l in db.query(Lemma)
            .filter(Lemma.lemma_id.in_(lemma_ids))
            .filter(Lemma.language_code == language_code)
            .all()
        }
        targets: list[GenTarget] = []
        target_indexed_ids: list[int] = []
        for lid in lemma_ids:
            canonical_id = resolve_canonical_lemma_id(db, lid)
            if canonical_id is None:
                continue
            lem = target_lemmas.get(canonical_id) or target_lemmas.get(lid)
            if lem is None:
                lem = db.query(Lemma).filter(Lemma.lemma_id == canonical_id).first()
                if lem is None:
                    continue
            if not (lem.gloss_en or "").strip():
                # Gloss gate (Hard Invariant): never generate for a glossless
                # target — the picker would reject the resulting sentence on
                # the read side anyway.
                continue
            targets.append(GenTarget(
                lemma_id=canonical_id,
                lemma_form=lem.lemma_form,
                lemma_bare=lem.lemma_bare or normalize_bare(lem.lemma_form, language_code),
                gloss_en=lem.gloss_en or "",
                pos=lem.pos or "",
                example_src=lem.example_src or "",
                example_en=lem.example_en or "",
            ))
            target_indexed_ids.append(canonical_id)

        if not targets:
            return {"generated": 0, "words_covered": 0, "words_failed": lemma_ids}

        lemma_lookup = build_lemma_lookup(db, language_code)
        known_pool = _snapshot_known_pool(
            db, language_code, exclude_lemma_ids={t.lemma_id for t in targets}
        )
        sentence_counts = _content_sentence_counts(db, language_code)
    finally:
        db.close()

    known_sample = _sample_known_words_weighted(
        known_pool,
        sentence_counts,
        KNOWN_SAMPLE_SIZE,
        language_code,
    )
    known_bare_forms = _known_pool_bare_forms(known_pool, language_code)
    avoid_words = _compute_avoid_words(known_pool, sentence_counts, language_code)
    generation_candidates_per_target = max(
        sentences_per_target + GENERATION_EXTRA_CANDIDATES_PER_TARGET,
        GENERATION_MIN_CANDIDATES_PER_TARGET,
    )

    # ── Phase 2a: Sonnet generation ──
    raw_sentences = generate_sentences_batch(
        language_code=language_code,
        targets=targets,
        known_sample=known_sample,
        sentences_per_target=generation_candidates_per_target,
        avoid_words=avoid_words,
    )
    if not raw_sentences:
        return {
            "generated": 0,
            "words_covered": 0,
            "words_failed": target_indexed_ids,
        }

    # ── Phase 2b: deterministic validation + mapping ──
    function_words = FUNCTION_WORD_SETS.get(language_code, set())
    candidates: list[dict] = []
    per_target_kept: dict[int, int] = {t.lemma_id: 0 for t in targets}

    for raw in raw_sentences:
        target = targets[raw.target_index]
        target_lemma_id = target.lemma_id
        if per_target_kept[target_lemma_id] >= generation_candidates_per_target:
            continue

        validation = validate_sentence(
            text=raw.text,
            target_bare=target.lemma_bare,
            known_bare_forms=known_bare_forms,
            function_word_bares=function_words,
            language_code=language_code,
            lemma_lookup=lemma_lookup,
            target_lemma_id=target_lemma_id,
        )
        if not validation.valid:
            _log_pipeline({
                "event": "gen_validation_failed",
                "language_code": language_code,
                "lemma_id": target_lemma_id,
                "text": raw.text,
                "issues": validation.issues,
                "unknown": validation.unknown_words[:5],
            })
            continue

        tokens = tokenize_display(raw.text, language_code)
        mappings = map_tokens_to_lemmas(
            tokens=tokens,
            lemma_lookup=lemma_lookup,
            language_code=language_code,
            target_lemma_id=target_lemma_id,
            target_bare=target.lemma_bare,
        )

        # Function words live in FUNCTION_WORD_SETS rather than as DB lemmas
        # in many cases. A NULL-lemma SentenceWord for a function word is fine
        # (sentence_harvest does the same). Only flag content tokens we
        # genuinely couldn't map.
        unmapped = [
            m.surface_form for m in mappings
            if not m.is_punctuation
            and m.lemma_id is None
            and normalize_bare(m.surface_form, language_code) not in function_words
        ]
        if unmapped:
            _log_pipeline({
                "event": "gen_unmapped",
                "language_code": language_code,
                "lemma_id": target_lemma_id,
                "text": raw.text,
                "unmapped": unmapped[:5],
            })
            continue

        candidates.append({
            "target_lemma_id": target_lemma_id,
            "text": raw.text,
            "translation_en": raw.translation_en,
            "mappings": mappings,
        })
        per_target_kept[target_lemma_id] += 1

    if not candidates:
        return {
            "generated": 0,
            "words_covered": 0,
            "words_failed": target_indexed_ids,
        }

    # ── Phase 2c: Haiku verification (no DB session open) ──
    # Build lemma_by_id snapshot before the LLM call so the verify pass has the
    # text/gloss data without holding a connection.
    db = database.SessionLocal()
    try:
        all_ids: set[int] = set()
        for c in candidates:
            for m in c["mappings"]:
                if m.lemma_id is not None:
                    all_ids.add(m.lemma_id)
        lemma_by_id: dict[int, Lemma] = {
            l.lemma_id: l
            for l in db.query(Lemma).filter(Lemma.lemma_id.in_(all_ids)).all()
        }
        # Pre-load canonical map for the write phase too.
        canonical_map: dict[int, Optional[int]] = {
            lid: lem.canonical_lemma_id
            for lid, lem in lemma_by_id.items()
        }
    finally:
        db.close()

    verify_per_cand = verify_sentence_mappings_llm(
        language_code=language_code,
        candidates=candidates,
        lemma_by_id=lemma_by_id,
    )
    if verify_per_cand is None:
        # Total verification failure — discard everything (Hard Invariant).
        return {
            "generated": 0,
            "words_covered": 0,
            "words_failed": target_indexed_ids,
        }

    # Reject candidates where Haiku flagged a content position as "wrong" — we
    # don't auto-create new lemmas from a generated sentence (the no-auto-
    # create-from-corrections invariant). "unclear" is tolerated; wrong
    # verdicts on non-content/same-lemma positions are ignored because the
    # verifier often nitpicks articles and prepositions even when the proposed
    # lemma is already right.
    accepted: list[dict] = []
    for cand, verdicts in zip(candidates, verify_per_cand):
        wrong = [
            v for v in verdicts
            if v.verdict == "wrong"
            and _wrong_verdict_rejects_candidate(
                v,
                cand["mappings"],
                lemma_by_id,
                language_code=language_code,
                function_words=function_words,
            )
        ]
        if wrong:
            _log_pipeline({
                "event": "verify_rejected",
                "language_code": language_code,
                "lemma_id": cand["target_lemma_id"],
                "text": cand["text"],
                "wrong_positions": [
                    {"position": v.position, "correct_lemma": v.correct_lemma}
                    for v in wrong
                ],
            })
            continue

        # Gloss gate: every content-mapping must point at a lemma with a non-
        # empty gloss_en. The picker enforces this on the read side too.
        empty_gloss = [
            m.surface_form for m in cand["mappings"]
            if not m.is_punctuation
            and m.lemma_id is not None
            and m.lemma_id in lemma_by_id
            and lemma_by_id[m.lemma_id].word_category != "function_word"
            and lemma_by_id[m.lemma_id].lemma_bare not in function_words
            and not (lemma_by_id[m.lemma_id].gloss_en or "").strip()
        ]
        if empty_gloss:
            _log_pipeline({
                "event": "gloss_gate_rejected",
                "language_code": language_code,
                "lemma_id": cand["target_lemma_id"],
                "text": cand["text"],
                "glossless": empty_gloss[:5],
            })
            continue

        accepted.append(cand)

    if not accepted:
        return {
            "generated": 0,
            "words_covered": 0,
            "words_failed": target_indexed_ids,
        }

    # ── Phase 2d: sentence-level naturalness + translation quality ──
    # Mapping verification only proves that each token points at a plausible
    # lemma. It does not catch grammatically parseable but meaningless review
    # cards. Mirror Alif's fail-closed candidate quality gate before storage.
    target_by_id = {t.lemma_id: t for t in targets}
    quality_reviews = review_sentences_quality(
        language_code,
        [
            {
                "text": cand["text"],
                "english": cand["translation_en"],
                "target": target_by_id.get(cand["target_lemma_id"]).lemma_form
                if target_by_id.get(cand["target_lemma_id"]) else "",
                "target_gloss": target_by_id.get(cand["target_lemma_id"]).gloss_en
                if target_by_id.get(cand["target_lemma_id"]) else "",
            }
            for cand in accepted
        ],
    )
    quality_filtered: list[dict] = []
    for cand, review in zip(accepted, quality_reviews):
        if review.natural and review.translation_correct:
            cand["quality_review"] = review
            quality_filtered.append(cand)
            continue
        _log_pipeline({
            "event": "quality_rejected",
            "language_code": language_code,
            "lemma_id": cand["target_lemma_id"],
            "text": cand["text"],
            "natural": review.natural,
            "translation_correct": review.translation_correct,
            "reason": review.reason[:200],
        })
    accepted = quality_filtered

    if not accepted:
        return {
            "generated": 0,
            "words_covered": 0,
            "words_failed": target_indexed_ids,
        }
    selected: list[dict] = []
    per_target_selected: dict[int, int] = {t.lemma_id: 0 for t in targets}
    for cand in accepted:
        target_lemma_id = cand["target_lemma_id"]
        if per_target_selected[target_lemma_id] >= sentences_per_target:
            continue
        selected.append(cand)
        per_target_selected[target_lemma_id] += 1
    accepted = selected

    # ── Phase 3: DB write (fast, single commit) ──
    db = database.SessionLocal()
    stored = 0
    covered_ids: set[int] = set()
    try:
        now = datetime.now(timezone.utc)
        for cand in accepted:
            review = cand.get("quality_review")
            sentence = Sentence(
                language_code=language_code,
                text=cand["text"],
                translation_en=cand["translation_en"],
                source="llm",
                target_lemma_id=cand["target_lemma_id"],
                is_active=True,
                mappings_verified_at=now,
                quality_reviewed_at=now if review is not None else None,
                quality_natural=bool(review.natural) if review is not None else None,
                quality_translation_correct=(
                    bool(review.translation_correct) if review is not None else None
                ),
                quality_reason=review.reason[:500] if review is not None else None,
                created_at=now,
            )
            db.add(sentence)
            db.flush()

            for m in cand["mappings"]:
                lemma_id = m.lemma_id
                if lemma_id is not None:
                    lemma_id = resolve_canonical_via_map(lemma_id, canonical_map)
                db.add(SentenceWord(
                    sentence_id=sentence.id,
                    position=m.position,
                    surface_form=m.surface_form,
                    lemma_id=lemma_id,
                    is_target_word=bool(m.is_target),
                ))
            stored += 1
            covered_ids.add(cand["target_lemma_id"])

        db.commit()
    except Exception:
        db.rollback()
        log.exception("Failed to commit generated sentences (language=%s)", language_code)
        return {
            "generated": 0,
            "words_covered": 0,
            "words_failed": target_indexed_ids,
        }
    finally:
        db.close()

    _log_pipeline({
        "event": "gen_batch_stored",
        "language_code": language_code,
        "stored": stored,
        "covered": list(covered_ids),
    })

    failed = [lid for lid in target_indexed_ids if lid not in covered_ids]
    return {
        "generated": stored,
        "words_covered": len(covered_ids),
        "words_failed": failed,
    }


def generate_material_for_lemma(
    language_code: str,
    lemma_id: int,
    sentences_per_target: int = SENTENCES_PER_TARGET,
) -> int:
    """Convenience: generate for a single lemma. Returns count stored."""
    result = batch_generate_material(
        language_code=language_code,
        lemma_ids=[lemma_id],
        sentences_per_target=sentences_per_target,
    )
    return int(result.get("generated", 0))


# ─── Warm cache ───────────────────────────────────────────────────────────


def _due_lemmas_missing_material(
    db: Session,
    language_code: str,
    target_count: int,
    limit: int,
) -> list[int]:
    """Review-target lemmas with fewer than ``target_count`` generated
    quality-approved sentences.

    Acquiring words sorted first by ``acquisition_next_due`` ASC so the warm
    cache prioritizes what the next session will actually pull. FSRS-backed
    ``learning``/``known``/``lapsed`` cards are eligible too. Bulk-known and
    cognate-known rows without an FSRS card are assumed-known scaffolding, not
    retrieval targets, until the learner marks them missed and they enter
    acquisition. Variant and non-content lemmas are filtered out.
    """
    sentence_counts = dict(
        db.query(SentenceWord.lemma_id, func.count(func.distinct(Sentence.id)))
        .join(Sentence, Sentence.id == SentenceWord.sentence_id)
        .filter(
            Sentence.language_code == language_code,
            Sentence.source == "llm",
            Sentence.is_active.is_(True),
            Sentence.mappings_verified_at.isnot(None),
            Sentence.quality_natural.is_(True),
            Sentence.quality_translation_correct.is_(True),
            SentenceWord.lemma_id.isnot(None),
        )
        .group_by(SentenceWord.lemma_id)
        .all()
    )

    rows = (
        db.query(Lemma, UserLemmaKnowledge)
        .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        .filter(
            Lemma.language_code == language_code,
            Lemma.canonical_lemma_id.is_(None),
            or_(
                UserLemmaKnowledge.knowledge_state == "acquiring",
                and_(
                    UserLemmaKnowledge.knowledge_state.in_(
                        ["learning", "known", "lapsed"]
                    ),
                    UserLemmaKnowledge.fsrs_card_json.isnot(None),
                ),
            ),
            (Lemma.word_category.is_(None) | Lemma.word_category.notin_(
                ["function_word", "proper_name", "not_word"]
            )),
            Lemma.gloss_en.isnot(None),
            func.length(func.trim(Lemma.gloss_en)) > 0,
        )
        .all()
    )
    function_words = FUNCTION_WORD_SETS.get(language_code, set())
    rows = [
        (lemma, ulk)
        for lemma, ulk in rows
        if not is_noncontent_lemma(
            lemma,
            language_code=language_code,
            function_words=function_words,
        )
    ]

    def _due_sort_key(item):
        lemma, ulk = item
        # 0 = acquiring (sort by next_due), 1 = others (sort by lemma_id for stability)
        if ulk.knowledge_state == "acquiring":
            due = ulk.acquisition_next_due or datetime.max.replace(tzinfo=timezone.utc)
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            return (0, due.timestamp(), lemma.lemma_id)
        return (1, 0.0, lemma.lemma_id)

    rows.sort(key=_due_sort_key)

    gap_ids: list[int] = []
    for lemma, _ulk in rows:
        if sentence_counts.get(lemma.lemma_id, 0) >= target_count:
            continue
        gap_ids.append(lemma.lemma_id)
        if len(gap_ids) >= limit:
            break
    return gap_ids


def warm_sentence_cache(
    language_code: str = "el",
    *,
    max_lemmas: int = 16,
    sentences_per_target: int = SENTENCES_PER_TARGET,
) -> dict:
    """Background task: top up generated material for any due lemma below
    ``ACTIVE_TARGET`` sentences.

    Locked so concurrent calls don't double-spend Claude budget on the same
    gaps. Caller (cron wrapper / endpoint) just calls; returns a small dict
    summarising what happened so we can log it.
    """
    if not _warm_cache_lock.acquire(blocking=False):
        return {"skipped": True, "reason": "warm_cache_busy"}

    run_id = uuid.uuid4().hex[:8]
    started = time.monotonic()
    try:
        db = database.SessionLocal()
        try:
            gap_ids = _due_lemmas_missing_material(
                db=db,
                language_code=language_code,
                target_count=ACTIVE_TARGET,
                limit=max_lemmas,
            )
        finally:
            db.close()

        if not gap_ids:
            _log_pipeline({
                "event": "warm_cache_no_gaps",
                "language_code": language_code,
                "run_id": run_id,
            })
            return {"run_id": run_id, "gap_count": 0, "generated": 0}

        total_generated = 0
        total_covered = 0
        failed_ids: list[int] = []
        for i in range(0, len(gap_ids), BATCH_WORD_SIZE):
            batch = gap_ids[i:i + BATCH_WORD_SIZE]
            result = batch_generate_material(
                language_code=language_code,
                lemma_ids=batch,
                sentences_per_target=sentences_per_target,
            )
            total_generated += int(result.get("generated", 0))
            total_covered += int(result.get("words_covered", 0))
            failed_ids.extend(result.get("words_failed", []))

        elapsed = time.monotonic() - started
        _log_pipeline({
            "event": "warm_cache_done",
            "language_code": language_code,
            "run_id": run_id,
            "gap_count": len(gap_ids),
            "generated": total_generated,
            "words_covered": total_covered,
            "words_failed": failed_ids,
            "elapsed_s": round(elapsed, 1),
        })
        return {
            "run_id": run_id,
            "gap_count": len(gap_ids),
            "generated": total_generated,
            "words_covered": total_covered,
            "words_failed": failed_ids,
        }
    finally:
        _warm_cache_lock.release()


# ─── Book-sentence translation ──────────────────────────────────────────────
#
# Harvested textbook sentences (``sentence_harvest``) are created with
# ``translation_en = NULL`` — harvesting is pure DB compute and holds no LLM
# call (write-lock discipline). The picker prefers generated sentences, but a
# book sentence is still served as a graceful fallback when no LLM sentence
# covers a due lemma yet. Without this pass those fallbacks render with a blank
# English line. We translate them lazily in the cron (the "pre-warm" window),
# never on the read path.


def _translate_prompt(language_code: str, items: list[dict]) -> str:
    lang = LANG_DISPLAY.get(language_code, language_code)
    block = "\n".join(f"[id={it['id']}] «{it['text']}»" for it in items)
    return f"""You are translating {lang} sentences to English for a learner.

Translate each sentence below to faithful, natural English. Do not
transliterate, do not add notes or commentary, and keep each translation to a
single concise English rendering of the input.

Each translation must reference the bracketed id so we can route it back.
Skip nothing.

Sentences:
{block}

Return JSON with this shape:
{{"translations": [{{"id": <int>, "english": "<translation>"}}, ...]}}
"""


def _translate_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "translations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "english": {"type": "string"},
                    },
                    "required": ["id", "english"],
                },
            },
        },
        "required": ["translations"],
    }


def translate_sentences_batch(
    language_code: str,
    items: list[dict],
) -> dict[int, str]:
    """One configured LLM call → ``{sentence_id: english}``.

    ``items`` is a list of ``{"id": int, "text": str}``. Returns an empty dict
    on total LLM failure (caller skips that batch). Only ids present in the
    request are accepted back; empty translations are dropped.
    """
    if not items:
        return {}
    requested_ids = {it["id"] for it in items}
    started = time.time()
    structured = _call_llm(
        prompt=_translate_prompt(language_code, items),
        schema=_translate_schema(),
        model=TRANSLATE_MODEL,
        timeout_s=TRANSLATE_TIMEOUT_S,
        log_context="sentence_translation",
    )
    elapsed = time.time() - started
    if not structured:
        _log_pipeline({
            "event": "translate_batch_failed",
            "language_code": language_code,
            "sentence_ids": sorted(requested_ids),
            "elapsed_s": round(elapsed, 1),
            "model": TRANSLATE_MODEL,
        })
        return {}

    out: dict[int, str] = {}
    for t in structured.get("translations", []) or []:
        if not isinstance(t, dict):
            continue
        sid = t.get("id")
        english = (t.get("english") or "").strip()
        if not isinstance(sid, int) or sid not in requested_ids or not english:
            continue
        out[sid] = english

    _log_pipeline({
        "event": "translate_batch_returned",
        "language_code": language_code,
        "requested": len(requested_ids),
        "returned": len(out),
        "elapsed_s": round(elapsed, 1),
        "model": TRANSLATE_MODEL,
    })
    return out


def _untranslated_sentence_rows(
    db: Session,
    language_code: str,
    limit: int,
) -> list[tuple[int, str]]:
    """Active + verified sentences that lack an English translation AND cover a
    lemma in active study (acquiring/learning/known/lapsed).

    Scoping to active-study lemmas is both correct and frugal: those are the
    only book sentences the picker can serve as a fallback, so a sentence
    covering only never-engaged vocabulary would never be shown and isn't worth
    an LLM call. Newest sentences first — freshly harvested pages are the
    ones the learner is most likely reading right now. Joining the ULK table
    (rather than an ``IN`` over thousands of ids) keeps the statement bounded.
    """
    rows = (
        db.query(Sentence.id, Sentence.text)
        .join(SentenceWord, SentenceWord.sentence_id == Sentence.id)
        .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == SentenceWord.lemma_id)
        .filter(
            Sentence.language_code == language_code,
            Sentence.source == "textbook",
            Sentence.is_active.is_(True),
            Sentence.mappings_verified_at.isnot(None),
            (Sentence.translation_en.is_(None))
            | (func.length(func.trim(Sentence.translation_en)) == 0),
            UserLemmaKnowledge.knowledge_state.in_(
                ["acquiring", "learning", "known", "lapsed"]
            ),
        )
        .distinct()
        .order_by(Sentence.id.desc())
        .limit(limit)
        .all()
    )
    return [(sid, text) for sid, text in rows if text]


def translate_untranslated_sentences(
    language_code: str = "el",
    *,
    max_sentences: int = 200,
    batch_size: int = TRANSLATE_BATCH_SIZE,
) -> dict:
    """Background task: fill ``translation_en`` for untranslated book sentences
    covering active-study lemmas.

    Three-phase, write-lock-safe: read pending ids+text (commit/close), call
    the configured LLM with no DB session open, then write each batch's results
    in a fast per-batch commit. Idempotent — the write only fills rows whose
    translation is still NULL/empty, so a concurrent reader or a re-run never
    clobbers an existing translation. Locked so two cron passes don't
    double-spend LLM budget.
    """
    if not _translate_lock.acquire(blocking=False):
        return {"skipped": True, "reason": "translate_busy", "pending": 0, "translated": 0}

    run_id = uuid.uuid4().hex[:8]
    started = time.monotonic()
    try:
        # ── Phase 1: read ──
        db = database.SessionLocal()
        try:
            pending = _untranslated_sentence_rows(db, language_code, max_sentences)
        finally:
            db.close()

        if not pending:
            _log_pipeline({
                "event": "translate_no_pending",
                "language_code": language_code,
                "run_id": run_id,
            })
            return {"run_id": run_id, "pending": 0, "translated": 0}

        total_translated = 0
        for i in range(0, len(pending), batch_size):
            batch = pending[i:i + batch_size]
            items = [{"id": sid, "text": text} for sid, text in batch]

            # ── Phase 2: LLM (no DB session open) ──
            translations = translate_sentences_batch(language_code, items)
            if not translations:
                continue

            # ── Phase 3: write (fast per-batch commit) ──
            db = database.SessionLocal()
            try:
                written = 0
                for sid, english in translations.items():
                    eng = (english or "").strip()
                    if not eng:
                        continue
                    written += (
                        db.query(Sentence)
                        .filter(
                            Sentence.id == sid,
                            Sentence.language_code == language_code,
                            (Sentence.translation_en.is_(None))
                            | (func.length(func.trim(Sentence.translation_en)) == 0),
                        )
                        .update(
                            {Sentence.translation_en: eng},
                            synchronize_session=False,
                        )
                    )
                db.commit()
                total_translated += written
            except Exception:
                db.rollback()
                log.exception(
                    "Failed to write translations batch (language=%s)", language_code
                )
            finally:
                db.close()

        elapsed = time.monotonic() - started
        _log_pipeline({
            "event": "translate_done",
            "language_code": language_code,
            "run_id": run_id,
            "pending": len(pending),
            "translated": total_translated,
            "elapsed_s": round(elapsed, 1),
        })
        return {
            "run_id": run_id,
            "pending": len(pending),
            "translated": total_translated,
        }
    finally:
        _translate_lock.release()
