"""Per-page LLM quality gate for lemmatization.

Mirrors Alif's `verify_and_correct_mappings_llm` + `apply_corrections` pattern,
adapted to read-and-mark rather than sentence-generation:

- After simplemma assigns lemmas to a page, batch the (surface, proposed_lemma,
  sentence_context) tuples and send to the configured LLM CLI with a JSON schema.
- For each token, the LLM returns verdict ∈ {ok, wrong, unclear} and, when wrong,
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

import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import Lemma, Page, PageWord
from app.services.languages import get_provider
from app.services.llm_cli import call_structured_json, resolve_model

log = logging.getLogger(__name__)

QUALITY_GATE_ENABLED = os.environ.get("POLYGLOT_QUALITY_GATE", "0") == "1"
BATCH_SIZE = int(os.environ.get("POLYGLOT_QG_BATCH", "25"))
TIMEOUT_S = int(os.environ.get("POLYGLOT_QG_TIMEOUT", "240"))

# Model routing. Accept either a full Anthropic model id or the shortname
# "sonnet"/"haiku" so users can A/B cost vs quality without remembering the
# version string. Sonnet stays default — Haiku is ~10x cheaper but hasn't been
# validated against the homograph rules yet.
_MODEL_ALIASES = {
    "sonnet": "claude-sonnet-4-5-20250929",
    "haiku": "claude-haiku-4-5",
}


def _resolve_model(raw: str) -> str:
    return resolve_model(raw, _MODEL_ALIASES)


MODEL = _resolve_model(os.environ.get("POLYGLOT_QG_MODEL", "sonnet"))

# Heading detection: a sentence_index counts as a heading when ≥80% of its
# letter-bearing tokens are all-caps, total tokens fall in
# [HEADING_MIN_WORDS, HEADING_MAX_WORDS]. Headings are stamped verified_at +
# quality_note='heading' and never sent to the LLM. The minimum guards
# isolated all-caps tokens (emphasis, acronyms) from being mis-classified —
# they fall through to the verifier instead, which can propose a citation
# form.
HEADING_UPPERCASE_RATIO = 0.80
HEADING_MIN_WORDS = 2
HEADING_MAX_WORDS = 10

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
    # σε + article crasis (στον/στην/στο…) — preposition+article fused into one
    # token. These are function words, not vocabulary; without them the
    # lemmatizer maps the fused form to a content lemma and the verifier
    # correctly flags it (2026-05-22 yield audit: ~half of all verify
    # rejections were σε-crasis / article mis-maps).
    "στον", "στην", "στο", "στη", "στους", "στις", "στα",
    "στου", "στης", "στων",
    # common particles / conjunctions
    "και", "ή", "αλλα", "μα", "οτι", "πως", "που", "γιατι", "οταν",
    "αν", "ομως", "λοιπον",
    "μη", "μην", "δεν", "θα", "να", "ας",
    # additional closed-class forms observed in the lemma audit; keep this list
    # narrow so lexical adverbs (e.g. δωρεάν, πλήρως, φέτος) remain learnable.
    "προτου", "ωσπου", "αμα", "εστω", "προ", "συν", "υπερ", "εφοσον",
    "υπ", "εφ", "οσον", "παρολο", "παρολα", "διχως", "ημων", "εντος",
    "οτου", "ενωπιον", "δα", "αφ", "βασει", "προκειμενου", "καμμια",
    "κεινος", "κεινον", "κεινο", "νατος", "αραγε", "ιδου", "ειθε", "μπας",
    # closed-class spatial/temporal adverbs and preposition-like forms that
    # frequently appear as scaffolding in generated sentences.
    "κοντα", "επανω", "πανω", "κατω", "μεσα", "εξω", "διπλα", "γυρω",
    "μπροστα", "πισω", "μακρια", "καπου", "εδω", "εκει", "παντα",
    "ποτε", "τοτε", "σημερα", "αυριο", "χθες", "μετα", "πριν", "καθε",
    "μονο", "μονον", "σαν", "απεναντι",
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
NONCONTENT_WORD_CATEGORIES = frozenset({"function_word", "proper_name", "not_word"})


def is_noncontent_lemma(
    lemma: Lemma,
    *,
    language_code: str | None = None,
    function_words: set[str] | None = None,
) -> bool:
    """True for lemmas that should not enter vocabulary scheduling.

    These lemmas can still be mapped in sentences for readability and quality
    gates, but they are scaffolding rather than study targets.
    """
    if lemma.word_category in NONCONTENT_WORD_CATEGORIES:
        return True
    bares = function_words
    if bares is None and language_code is not None:
        bares = FUNCTION_WORD_SETS.get(language_code, set())
    return bool(bares and lemma.lemma_bare in bares)


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
        # Blank / image-only pages (e.g. chapter dividers in scanned textbooks)
        # have nothing to verify. Stamp them as verified so the page-warm cron
        # doesn't retry them on every pass and so they count toward the
        # "pages ahead" buffer. Parallels the `if not interesting` branch below.
        log.info("Page %d: no tokens, marking as trivially verified", page.id)
        _stamp_verified(db, page, failures=0)
        return 0

    # Build sentence index → text map for context lookup
    sentences = _build_sentence_index(words)
    heading_indices = _detect_heading_sentence_indices(words)
    if heading_indices:
        _mark_heading_words(db, words, heading_indices)

    # Filter to interesting tokens
    language_code = page.story.language_code
    function_words = FUNCTION_WORD_SETS.get(language_code, set())
    skip_identity_default = os.environ.get("POLYGLOT_QG_SKIP_IDENTITY", "1") == "1"
    interesting: list[TokenCheck] = []
    lemmas_by_id = _load_lemmas_for_words(db, words)
    for w in words:
        if not w.lemma_id:
            continue
        if w.sentence_index in heading_indices:
            continue
        lemma = lemmas_by_id.get(w.lemma_id)
        if not lemma:
            continue
        if lemma.lemma_bare in function_words:
            continue
        if w.verified_at and not force:
            continue
        # Skip when the lemma is identical to the surface — simplemma made no
        # mapping decision. Exception: all-caps surfaces, where the equality
        # is a false signal (Greek caps shed accents). The verifier still
        # needs to see those so it can propose the citation form.
        if skip_identity_default and not _is_all_caps(w.surface_form):
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

    # Reset verification state on the page and on every word about to be
    # re-batched. Without this, a force-rerun that hits a partial batch
    # failure would inherit the stale page stamp (and stale per-word stamps)
    # from the prior successful pass — silently re-verifying the page even
    # though some tokens never got fresh verdicts. CLAUDE.md hard invariant
    # #8: verification failure ≠ success.
    interesting_pw_ids = {tc.pageword_id for tc in interesting}
    for w in words:
        if w.id in interesting_pw_ids and w.verified_at is not None:
            w.verified_at = None
    page.mappings_verified_at = None
    page.quality_gate_failures = None
    db.commit()

    # Batch. Any batch returning None counts as a partial failure — we leave
    # mappings_verified_at NULL so the next page-view retries. Stamping anyway
    # would silently swallow unverified tokens (see CLAUDE.md hard invariant #8).
    corrected = 0
    unclear = 0
    any_batch_failed = False
    for chunk_start in range(0, len(interesting), BATCH_SIZE):
        chunk = interesting[chunk_start:chunk_start + BATCH_SIZE]
        verdicts = _call_claude(chunk, language_name=_LANG_DISPLAY[language_code])
        if verdicts is None:
            any_batch_failed = True
            log.warning("Page %d: batch %d returned no verdicts (LLM failure?)",
                        page.id, chunk_start // BATCH_SIZE)
            continue
        for v in verdicts:
            applied = _apply_verdict(db, v, language_code)
            if applied == "corrected":
                corrected += 1
            elif applied == "unclear":
                unclear += 1
        db.commit()

    if any_batch_failed:
        page.quality_gate_failures = unclear
        db.commit()
        log.info("Page %d partial: %d corrected, %d unclear, %d checked, "
                 "left unverified for retry", page.id, corrected, unclear, len(interesting))
    else:
        _stamp_verified(db, page, failures=unclear)
        log.info("Page %d verified: %d corrected, %d unclear, %d checked",
                 page.id, corrected, unclear, len(interesting))
    return corrected


# ─── Internals ─────────────────────────────────────────────────────────────

_LANG_DISPLAY = {"el": "Modern Greek", "grc": "Ancient Greek", "la": "Latin"}


def _is_all_caps(surface: str) -> bool:
    """True when the token has letters and none of them are lowercase.

    Used to bypass the identity-skip: Greek uppercase strips accents, so a
    surface like ΠΟΛΙΤΙΣΜΟΙ lowercases to πολιτισμοι (no accent), which
    equals the unaccented lemma simplemma falls back to — the identity check
    would wrongly mark it as "no decision to verify."
    """
    has_alpha = False
    for c in surface:
        if c.isalpha():
            has_alpha = True
            if c.islower():
                return False
    return has_alpha


def _detect_heading_sentence_indices(words: list[PageWord]) -> set[int]:
    """Return sentence_index values that look like section/chapter headings.

    Heuristic: a sentence with ≤HEADING_MAX_WORDS tokens where at least
    HEADING_UPPERCASE_RATIO of the letter-bearing tokens are all-caps. PDFs
    typeset headings in caps without accents; those don't carry vocabulary
    value, so we keep them out of the LLM batch.
    """
    by_idx: dict[int, list[PageWord]] = {}
    for w in words:
        by_idx.setdefault(w.sentence_index, []).append(w)
    out: set[int] = set()
    for idx, ws in by_idx.items():
        letter_tokens = [w for w in ws if any(c.isalpha() for c in w.surface_form)]
        n = len(letter_tokens)
        if n < HEADING_MIN_WORDS or n > HEADING_MAX_WORDS:
            continue
        caps = sum(1 for w in letter_tokens if _is_all_caps(w.surface_form))
        if caps / n >= HEADING_UPPERCASE_RATIO:
            out.add(idx)
    return out


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
    """Single structured LLM call covering BATCH_SIZE tokens. Returns None on
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
    structured = call_structured_json(
        prompt=prompt,
        schema=schema,
        model=MODEL,
        timeout_s=TIMEOUT_S,
        log_context="quality_gate",
        runner=subprocess.run,
    )
    if not isinstance(structured, dict):
        return None
    decisions = structured.get("decisions", [])

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
    covered = {v.pageword_id for v in out}
    missing = by_id - covered
    if missing:
        log.error(
            "Quality gate returned incomplete decisions: missing pageword ids %s",
            sorted(missing)[:10],
        )
        return None
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
    function_words = FUNCTION_WORD_SETS.get(language_code, set())
    if target is None:
        # Create — quality_gate is an authorised lemma source for polyglot
        # (in contrast to Alif, where bookCorpus prefers retire over create).
        target = Lemma(
            language_code=language_code,
            lemma_form=v.correct_lemma,
            lemma_bare=correct_bare,
            source="quality_gate",
            word_category=(
                "function_word" if correct_bare in function_words else None
            ),
        )
        db.add(target)
        db.flush()
    elif correct_bare in function_words and target.word_category is None:
        target.word_category = "function_word"

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


def _mark_heading_words(db: Session, words: list[PageWord], heading_indices: set[int]) -> None:
    """Stamp heading-sentence words so they're excluded from the LLM batch
    and from later review-eligibility checks. We use quality_note='heading'
    rather than is_function_word=True because headings aren't grammatical
    function words — they're meta-text outside the vocabulary stream."""
    now = datetime.now(timezone.utc)
    for w in words:
        if w.sentence_index in heading_indices and w.verified_at is None:
            w.verified_at = now
            w.quality_note = "heading"
    db.commit()
