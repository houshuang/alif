"""Per-page LLM quality gate for lemmatization.

Mirrors Alif's `verify_and_correct_mappings_llm` + `apply_corrections` pattern,
adapted to read-and-mark rather than sentence-generation:

- After simplemma assigns lemmas to a page, batch the (surface, proposed_lemma,
  sentence_context) tuples and send to Claude CLI with a JSON schema.
- For each token, Claude returns verdict ∈ {ok, wrong, unclear} and, when wrong,
  the correct_lemma in citation form.
- Corrections: find an existing Lemma by normalised bare form; if not present,
  CREATE one with `source='quality_gate'` so the page mapping has somewhere to
  point. (Alif's no-auto-create invariant applies to *generated* sentences,
  not authentic imported text — here the source of truth is the textbook, so
  the correct word must be representable.)
- Stamp `Page.mappings_verified_at` + per-word `PageWord.verified_at` so the
  gate is idempotent.

Cost & latency: ~25 tokens/batch × ~3s/call. Skip punctuation, function words,
and tokens whose surface == lemma (simplemma didn't change anything — no real
mapping decision was made). A 500-word page typically reduces to ~6-8 batches.

Gated by POLYGLOT_QUALITY_GATE=1 — off by default while we tune the prompt.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import Lemma, Page, PageWord
from app.services.languages import get_provider

log = logging.getLogger(__name__)

QUALITY_GATE_ENABLED = os.environ.get("POLYGLOT_QUALITY_GATE", "0") == "1"
BATCH_SIZE = int(os.environ.get("POLYGLOT_QG_BATCH", "25"))
MODEL = os.environ.get("POLYGLOT_QG_MODEL", "claude-sonnet-4-5-20250929")
TIMEOUT_S = int(os.environ.get("POLYGLOT_QG_TIMEOUT", "240"))

# Greek function words that don't need verification. Article forms, common
# prepositions, conjunctions, common particles. Listed by normalized
# (lowercase, accent-stripped) bare form. Mirror Alif's FUNCTION_WORDS pattern
# but smaller — we add more as we observe verifier-pass surprises.
EL_FUNCTION_WORDS: set[str] = {
    # articles (definite)
    "ο", "η", "το", "οι", "τα", "του", "της", "των", "τον", "την",
    # articles (indefinite)
    "ενας", "μια", "ενα", "μιας", "ενος",
    # prepositions
    "σε", "για", "με", "απο", "προς", "παρα", "κατα", "ως", "εως",
    # common particles / conjunctions
    "και", "ή", "αλλα", "οτι", "πως", "γιατι", "αν", "ομως", "λοιπον",
    "μη", "μην", "δεν", "θα", "να", "ας",
    # demonstratives
    "αυτος", "αυτη", "αυτο", "αυτοι", "αυτες", "αυτα",
    # pronouns
    "εγω", "εσυ", "αυτος", "εμεις", "εσεις", "μου", "σου", "του", "της",
    "μας", "σας", "τους", "τις",
}

LA_FUNCTION_WORDS: set[str] = {
    "et", "ad", "in", "ex", "de", "cum", "per", "sub", "sed", "non",
    "si", "ut", "qui", "quae", "quod", "ille", "haec", "hoc", "is", "ea",
    "id", "ego", "tu", "nos", "vos", "atque", "ac", "que",
}

GRC_FUNCTION_WORDS: set[str] = {
    "ο", "η", "το", "οι", "αι", "τα", "και", "δε", "γαρ", "μεν", "ουν",
    "τε", "η", "αν", "ει", "ως", "ινα", "οτι", "ου", "μη", "εν", "εις",
    "εκ", "απο", "προς", "παρα", "συν", "δια", "κατα", "ανα", "μετα", "επι",
    "υπο", "υπερ", "περι", "αμφι",
}


FUNCTION_WORD_SETS = {"el": EL_FUNCTION_WORDS, "la": LA_FUNCTION_WORDS, "grc": GRC_FUNCTION_WORDS}


# ─── Dataclasses ──────────────────────────────────────────────────────────

@dataclass
class TokenCheck:
    """One token submitted to the gate."""
    pageword_id: int
    surface: str
    proposed_lemma: str
    proposed_gloss: str | None
    sentence_context: str


@dataclass
class Verdict:
    pageword_id: int
    verdict: str            # ok / wrong / unclear
    correct_lemma: str | None = None
    reason: str | None = None


# ─── Public API ────────────────────────────────────────────────────────────

def verify_page_mappings(
    db: Session,
    page: Page,
    *,
    force: bool = False,
) -> int:
    """Run the quality gate on every interesting token on a page.

    Returns the count of mappings that were corrected. Idempotent unless
    `force=True`. Sets `page.mappings_verified_at` and per-word
    `verified_at` on success.

    Skipped by default unless POLYGLOT_QUALITY_GATE=1 (or force=True).
    """
    if not QUALITY_GATE_ENABLED and not force:
        return 0
    if page.mappings_verified_at and not force:
        return 0

    words = (
        db.query(PageWord)
        .filter(PageWord.page_id == page.id)
        .order_by(PageWord.position)
        .all()
    )
    if not words:
        return 0

    # Build sentence index → text map for context lookup
    sentences = _build_sentence_index(words)

    # Filter to interesting tokens
    language_code = page.story.language_code
    function_words = FUNCTION_WORD_SETS.get(language_code, set())
    interesting: list[TokenCheck] = []
    lemmas_by_id = _load_lemmas_for_words(db, words)
    for w in words:
        if not w.lemma_id:
            continue
        lemma = lemmas_by_id.get(w.lemma_id)
        if not lemma:
            continue
        if lemma.lemma_bare in function_words:
            continue
        if w.verified_at and not force:
            continue
        # Skip when the lemma is identical to the surface — simplemma made no
        # mapping decision. Still verifiable but lower-yield; we'd burn the
        # budget on these. (Caller can opt in via env var if needed.)
        if os.environ.get("POLYGLOT_QG_SKIP_IDENTITY", "1") == "1":
            if lemma.lemma_form.lower() == w.surface_form.lower():
                continue
        interesting.append(TokenCheck(
            pageword_id=w.id,
            surface=w.surface_form,
            proposed_lemma=lemma.lemma_form,
            proposed_gloss=lemma.gloss_en,
            sentence_context=sentences.get(w.sentence_index, ""),
        ))

    if not interesting:
        log.info("Page %d: nothing to verify", page.id)
        _stamp_verified(db, page, failures=0)
        return 0

    # Batch
    corrected = 0
    unclear = 0
    for chunk_start in range(0, len(interesting), BATCH_SIZE):
        chunk = interesting[chunk_start:chunk_start + BATCH_SIZE]
        verdicts = _call_claude(chunk, language_name=_LANG_DISPLAY[language_code])
        if verdicts is None:
            log.warning("Page %d: batch %d returned no verdicts (LLM failure?)",
                        page.id, chunk_start // BATCH_SIZE)
            continue  # leave those tokens unverified, try next batch
        for v in verdicts:
            applied = _apply_verdict(db, v, language_code)
            if applied == "corrected":
                corrected += 1
            elif applied == "unclear":
                unclear += 1
        db.commit()

    _stamp_verified(db, page, failures=unclear)
    log.info("Page %d verified: %d corrected, %d unclear, %d checked",
             page.id, corrected, unclear, len(interesting))
    return corrected


# ─── Internals ─────────────────────────────────────────────────────────────

_LANG_DISPLAY = {"el": "Modern Greek", "grc": "Ancient Greek", "la": "Latin"}


def _build_sentence_index(words: list[PageWord]) -> dict[int, str]:
    """Reconstruct sentence text by joining surface forms in order, grouped by
    sentence_index. Not perfectly faithful to original whitespace but good
    enough for LLM context."""
    by_idx: dict[int, list[PageWord]] = {}
    for w in words:
        by_idx.setdefault(w.sentence_index, []).append(w)
    return {
        idx: " ".join(w.surface_form for w in sorted(ws, key=lambda x: x.position))
        for idx, ws in by_idx.items()
    }


def _load_lemmas_for_words(db: Session, words: list[PageWord]) -> dict[int, Lemma]:
    ids = {w.lemma_id for w in words if w.lemma_id is not None}
    if not ids:
        return {}
    return {l.lemma_id: l for l in db.query(Lemma).filter(Lemma.lemma_id.in_(ids)).all()}


def _call_claude(chunk: list[TokenCheck], language_name: str) -> list[Verdict] | None:
    """Single Claude CLI call covering BATCH_SIZE tokens. Returns None on
    total LLM failure so callers can distinguish failure from empty-result."""
    schema = {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "verdict": {"type": "string", "enum": ["ok", "wrong", "unclear"]},
                        "correct_lemma": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "verdict"],
                },
            },
        },
        "required": ["decisions"],
    }
    items_block = "\n".join(
        f"[{c.pageword_id}] sentence: «{c.sentence_context}»\n"
        f"         surface: {c.surface}\n"
        f"         proposed lemma: {c.proposed_lemma}"
        + (f"  (gloss: {c.proposed_gloss})" if c.proposed_gloss else "")
        for c in chunk
    )
    prompt = f"""You are a {language_name} lemmatization quality gate. For each token below, the lemmatizer proposed a lemma. Check whether the proposed lemma is correct *in this sentence's context* (not just morphologically plausible).

Rules:
- verdict "ok": proposed lemma is the correct citation form for this surface in this sentence.
- verdict "wrong": there's a clearly better citation form. Provide it in `correct_lemma` (one word, with accents/diacritics).
- verdict "unclear": ambiguous or the surface isn't a real {language_name} word (typo, OCR garbage, foreign word, proper name where the lemma is fine as-is). Leave correct_lemma blank.

Common pitfalls:
- Homographs: e.g. Modern Greek χώρα (noun, country) vs χωρώ (verb, to fit) — pick by sentence role.
- All-caps headings often lack accents → proposed lemma keeps the unaccented form. Provide the standard accented citation form if you can identify it.
- Don't change a lemma just for style; only flag actual errors.

Return one decision per token, keyed by the bracketed id. Skip nothing.

Tokens:
{items_block}
"""
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", MODEL,
        "--json-schema", json.dumps(schema),
        prompt,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        log.error("Quality gate batch timed out after %ds", TIMEOUT_S)
        return None
    if proc.returncode != 0:
        log.error("claude CLI failed: %s", proc.stderr[:500])
        return None
    # When --json-schema is set, the CLI puts structured output in
    # `structured_output` (a JSON object) and leaves `result` empty. Older
    # docs said `result` would contain the JSON string; check both.
    try:
        wrapper = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        log.error("Could not parse CLI envelope: %s\n%s", e, proc.stdout[:500])
        return None
    if not isinstance(wrapper, dict):
        log.error("Unexpected CLI output shape: %s", proc.stdout[:300])
        return None
    structured = wrapper.get("structured_output")
    if isinstance(structured, dict):
        decisions = structured.get("decisions", [])
    else:
        # Fallback: try to parse `result` as JSON (legacy path)
        result_str = wrapper.get("result", "")
        try:
            parsed = json.loads(result_str) if result_str else {}
            decisions = parsed.get("decisions", []) if isinstance(parsed, dict) else []
        except json.JSONDecodeError:
            log.error("No structured_output and result isn't JSON: %s", result_str[:300])
            return None

    by_id = {c.pageword_id for c in chunk}
    out: list[Verdict] = []
    for d in decisions:
        if not isinstance(d, dict):
            continue
        pid = d.get("id")
        if pid not in by_id:
            continue
        out.append(Verdict(
            pageword_id=pid,
            verdict=d.get("verdict", "unclear"),
            correct_lemma=d.get("correct_lemma") or None,
            reason=d.get("reason") or None,
        ))
    return out


def _apply_verdict(db: Session, v: Verdict, language_code: str) -> str:
    """Apply one verdict. Returns 'corrected', 'unclear', or 'ok'."""
    pw = db.get(PageWord, v.pageword_id)
    if pw is None:
        return "ok"
    now = datetime.now(timezone.utc)

    if v.verdict == "ok":
        pw.verified_at = now
        return "ok"

    if v.verdict == "unclear":
        pw.verified_at = now
        pw.quality_note = v.reason
        return "unclear"

    # verdict == "wrong"
    if not v.correct_lemma:
        # Bad LLM output — treat as unclear
        pw.verified_at = now
        pw.quality_note = "wrong-without-correction"
        return "unclear"

    # Resolve correct lemma: find existing or create new
    provider = get_provider(language_code)
    correct_bare = provider.normalize_bare(v.correct_lemma)
    target = (
        db.query(Lemma)
        .filter(Lemma.language_code == language_code, Lemma.lemma_bare == correct_bare)
        .first()
    )
    if target is None:
        # Create — quality_gate is an authorised lemma source for polyglot
        # (in contrast to Alif, where bookCorpus prefers retire over create).
        target = Lemma(
            language_code=language_code,
            lemma_form=v.correct_lemma,
            lemma_bare=correct_bare,
            source="quality_gate",
        )
        db.add(target)
        db.flush()

    if pw.lemma_id == target.lemma_id:
        # LLM disagreed with itself or said "wrong" but proposed same lemma
        pw.verified_at = now
        pw.quality_note = "wrong-but-same-lemma"
        return "unclear"

    pw.original_lemma_id = pw.lemma_id  # audit
    pw.lemma_id = target.lemma_id
    pw.verified_at = now
    pw.quality_note = v.reason
    log.info("Page-word %d corrected: %s → %s (%s)",
             pw.id, pw.surface_form, target.lemma_form, (v.reason or "")[:80])
    return "corrected"


def _stamp_verified(db: Session, page: Page, *, failures: int):
    page.mappings_verified_at = datetime.now(timezone.utc)
    page.quality_gate_failures = failures
    db.commit()
