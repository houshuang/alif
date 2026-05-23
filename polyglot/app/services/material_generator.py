"""LLM-driven sentence generation for due lemmas.

Picks up where ``sentence_selector.pick_sentence_for_lemma`` leaves off: when
no harvested textbook sentence covers a due lemma, this module generates one
via the configured LLM CLI and writes a ``Sentence`` + ``SentenceWord`` row
that the picker can find on the next session.

Pipeline (mirrors Alif's three-phase pattern for SQLite write-lock discipline):

    Phase 1 — DB read:   pull target lemmas + active known vocabulary, close.
    Phase 2 — LLM work:  one Sonnet call generates N sentences; one Haiku call
                         verifies each per-position lemma mapping. No DB lock
                         held across either call.
    Phase 3 — DB write:  open fresh session, write verified sentences in a
                         single commit (milliseconds).

Hard invariants honored:

- **Verification mandatory** — every generated sentence passes through
  ``verify_sentence_mappings_llm``. Failures are discarded; ``mappings_verified_at``
  is only stamped on sentences whose mapping verdicts all passed. Mirror of
  Alif's "all sentence generation must go through generate_material_for_word"
  invariant.
- **Canonical at write time** — SentenceWord.lemma_id is resolved through
  ``resolve_canonical_via_map`` before insert. Defense in depth: the picker
  re-resolves on read.
- **Gloss gate** — sentences with any content lemma lacking ``gloss_en`` are
  rejected before write.
- **No bare-word path** — sentences only; never creates ULKs or alters
  knowledge state.
- **DB lock discipline** — LLM calls happen with no SQLAlchemy session open.

Cost defaults: Sonnet for generation, Haiku for verification (10× cheaper, and
verification is structurally simpler than generation). Both overridable via
``POLYGLOT_GEN_MODEL`` and ``POLYGLOT_VERIFY_MODEL``.
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

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import database
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.canonical_resolution import (
    resolve_canonical_lemma_id,
    resolve_canonical_via_map,
)
from app.services.lemma_quality import FUNCTION_WORD_SETS
from app.services.llm_cli import call_structured_json, resolve_model
from app.services.sentence_validator import (
    Mapping,
    build_lemma_lookup,
    map_tokens_to_lemmas,
    normalize_bare,
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
# Translation of harvested book sentences is structurally simpler than
# generation — Haiku is plenty, and 10× cheaper.
TRANSLATE_MODEL = _resolve_model(os.environ.get("POLYGLOT_TRANSLATE_MODEL", "haiku"))
GEN_TIMEOUT_S = int(os.environ.get("POLYGLOT_GEN_TIMEOUT", "240"))
VERIFY_TIMEOUT_S = int(os.environ.get("POLYGLOT_VERIFY_TIMEOUT", "180"))
TRANSLATE_TIMEOUT_S = int(os.environ.get("POLYGLOT_TRANSLATE_TIMEOUT", "180"))
# Sentences per translation LLM call. Translation is short and uniform, so we
# batch generously — one Haiku call covers a whole page's worth of fallbacks.
TRANSLATE_BATCH_SIZE = max(1, int(os.environ.get("POLYGLOT_TRANSLATE_BATCH_SIZE", "12")))

# Cap each batch to a reasonable size — Alif measured the sweet spot at 4-6
# targets per Sonnet call. More than that and the prompt context noise
# degrades per-target quality.
BATCH_WORD_SIZE = max(1, int(os.environ.get("POLYGLOT_BATCH_WORD_SIZE", "4")))

# How many sentences to request per target. The picker keeps the best; extras
# hedge against deterministic-validation failures.
SENTENCES_PER_TARGET = max(1, int(os.environ.get("POLYGLOT_SENTENCES_PER_TARGET", "2")))

# A target is considered "covered" when it has at least this many active +
# verified Sentence rows referencing it. The picker still chooses among them
# per-session; this threshold just governs the warm-cache backfill loop.
ACTIVE_TARGET = max(1, int(os.environ.get("POLYGLOT_ACTIVE_TARGET", "3")))

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
    target_block = "\n".join(
        f"  {i}. {t.lemma_form}"
        + (f" ({t.pos})" if t.pos else "")
        + f" — {t.gloss_en or '(no gloss)'}"
        for i, t in enumerate(targets)
    )
    register_instruction = _register_instruction(language_code)
    return f"""You are generating natural {lang} practice sentences for a learner.

For each target lemma below, produce {sentences_per_target} short sentences
(6–12 words each) that use the lemma in its primary sense. Every non-target
word in the sentence MUST come from the known-words pool below — sentences
that reach for vocabulary outside the pool will be rejected by the validator
and the work wasted.

Rules:
- Use the target lemma exactly once per sentence (any inflected form is fine).
- {register_instruction} No headlines, no all-caps, no proper-noun
  heavy contexts unless the target itself is a proper noun.
- Provide a faithful English translation. Do not transliterate.
- Use only vocabulary from the known-words pool below for every non-target
  word. If you can't construct a natural sentence within this constraint,
  return fewer sentences rather than reaching outside the pool.
- Avoid the listed over-represented words — pick less-used vocabulary from
  the pool to keep the corpus diverse.

Known-words pool ({len(known_sample)} words available, all in the learner's vocabulary):
{known_block}

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
    for s_idx, cand in enumerate(candidates):
        for m in cand["mappings"]:
            if m.lemma_id is None:
                continue
            lemma = lemma_by_id.get(m.lemma_id)
            if lemma is None:
                log.warning(
                    "Verification snapshot missing lemma_id=%s for candidate=%s position=%s",
                    m.lemma_id, s_idx, m.position,
                )
                return None
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
        db.query(Lemma.lemma_id, Lemma.lemma_form)
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
        {"lemma_id": lid, "lemma_form": lf}
        for lid, lf in rows.all()
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
        return [w["lemma_form"] for w in pool]

    weighted: list[tuple[float, dict]] = []
    for w in pool:
        cnt = counts.get(w["lemma_id"], 0)
        weight = max(MIN_SAMPLE_WEIGHT, 1.0 / (1 + cnt))
        jittered = weight * random.uniform(0.5, 1.5)
        weighted.append((jittered, w))
    weighted.sort(key=lambda x: x[0], reverse=True)
    return [w["lemma_form"] for _, w in weighted[:sample_size]]


def _compute_avoid_words(
    pool: list[dict],
    counts: dict[int, int],
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
    result = [by_id_to_form[lid] for lid, _ in over[:MAX_AVOID_WORDS]]
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

    known_sample = _sample_known_words_weighted(known_pool, sentence_counts, KNOWN_SAMPLE_SIZE)
    avoid_words = _compute_avoid_words(known_pool, sentence_counts)

    # ── Phase 2a: Sonnet generation ──
    raw_sentences = generate_sentences_batch(
        language_code=language_code,
        targets=targets,
        known_sample=known_sample,
        sentences_per_target=sentences_per_target,
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
        if per_target_kept[target_lemma_id] >= sentences_per_target:
            continue

        # All bare forms the picker would consider scaffold-known. We don't have
        # full ULK state here without a DB hit; but `validate_sentence` just
        # needs to confirm that every token resolves to *some* DB lemma. The
        # comprehensibility judgment happens at picker time, not at gen time.
        validation = validate_sentence(
            text=raw.text,
            target_bare=target.lemma_bare,
            known_bare_forms=set(lemma_lookup.keys()),
            function_word_bares=function_words,
            language_code=language_code,
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
            if m.lemma_id is None
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

    # Reject any candidate where Haiku flagged ANY position as "wrong" — we
    # don't auto-create new lemmas from a generated sentence (the no-auto-
    # create-from-corrections invariant). "unclear" is tolerated; the picker
    # / sentence-review pipeline will surface bad cases via leech detection.
    accepted: list[dict] = []
    for cand, verdicts in zip(candidates, verify_per_cand):
        wrong = [v for v in verdicts if v.verdict == "wrong"]
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
            if m.lemma_id is not None
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

    # ── Phase 3: DB write (fast, single commit) ──
    db = database.SessionLocal()
    stored = 0
    covered_ids: set[int] = set()
    try:
        now = datetime.now(timezone.utc)
        for cand in accepted:
            sentence = Sentence(
                language_code=language_code,
                text=cand["text"],
                translation_en=cand["translation_en"],
                source="llm",
                target_lemma_id=cand["target_lemma_id"],
                is_active=True,
                mappings_verified_at=now,
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
    """Lemmas in active study (acquiring/learning/known/lapsed) with fewer than
    ``target_count`` active+verified sentences.

    Acquiring words sorted first by ``acquisition_next_due`` ASC so the warm
    cache prioritizes what the next session will actually pull. Variant
    lemmas are filtered out (only canonicals get material).
    """
    sentence_counts = dict(
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

    rows = (
        db.query(Lemma, UserLemmaKnowledge)
        .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        .filter(
            Lemma.language_code == language_code,
            Lemma.canonical_lemma_id.is_(None),
            UserLemmaKnowledge.knowledge_state.in_(
                ["acquiring", "learning", "known", "lapsed"]
            ),
            (Lemma.word_category.is_(None) | Lemma.word_category.notin_(
                ["function_word", "proper_name"]
            )),
            Lemma.gloss_en.isnot(None),
            func.length(func.trim(Lemma.gloss_en)) > 0,
        )
        .all()
    )

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
