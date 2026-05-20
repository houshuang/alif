"""LLM body-text cleaner for raw PDF page extractions.

PyMuPDF gives us text that's *physically* correct (every glyph that's in the
PDF lands in body_src) but *structurally* polluted for a reader: a page
typically interleaves headers, footers, page numbers, section titles,
sidebars, footnote markers (superscript digits fused into words like
``σιτηρών1``), bibliographic citations, and end-of-line soft-hyphens that
break a single word across two lines (``διαρ-\\nρέουν`` → two tokens, both
junk). Tokenizing this raw text creates phantom lemmas the user will never
want to learn.

This service is the structural cleanup pass. For each Page we ask Haiku
(via Claude CLI, free under Max plan) to return the cleaned reading prose
plus an audit list of every segment it removed. The cleaned text is what
``process_page`` then tokenizes.

Hard constraints on the LLM:

- **Verbatim preservation.** The cleaner may DROP segments and REJOIN
  hyphen-split words. It MUST NOT paraphrase, translate, modernise spelling,
  re-order sentences, or insert words that aren't in the source.
- **Audit trail.** Every removed segment is returned verbatim so we can
  ``body_src.find(seg)`` to verify it was actually in the source. If any
  removed segment is not a substring of body_src, the cleaner hallucinated
  and the whole result is discarded (caller falls back to body_src).
- **Failure ≠ success.** Mirrors Hard Invariant #8: a Haiku timeout or parse
  error returns ``None``. Callers leave ``page.body_clean`` NULL and tokenize
  from raw ``body_src``; the next warm-cycle will retry.

Cost: Haiku is ~$1/M input tokens. A typical page is ~3KB ≈ ~1k tokens in +
~1k tokens out = ~$0.001-0.002 per page. A 300-page textbook costs <$1
worst case; under Max plan it's free.

Gated by ``POLYGLOT_BODY_CLEAN=1`` (default on once deployed). Override
model via ``POLYGLOT_BODY_CLEAN_MODEL=haiku`` (default) or ``=sonnet``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)


BODY_CLEAN_ENABLED = os.environ.get("POLYGLOT_BODY_CLEAN", "1") == "1"
TIMEOUT_S = int(os.environ.get("POLYGLOT_BODY_CLEAN_TIMEOUT", "180"))
# Audit-failure retries. LLM non-determinism means a second attempt often
# passes — Haiku's most common mistake is truncating a removed segment at the
# wrong sentence boundary, which retries away. After this many retries we
# accept the page can't be cleaned and let the caller fall back to body_src.
MAX_AUDIT_RETRIES = int(os.environ.get("POLYGLOT_BODY_CLEAN_RETRIES", "2"))

_MODEL_ALIASES = {
    "sonnet": "claude-sonnet-4-5-20250929",
    "haiku": "claude-haiku-4-5",
}


def _resolve_model(raw: str) -> str:
    return _MODEL_ALIASES.get(raw.strip().lower(), raw)


MODEL = _resolve_model(os.environ.get("POLYGLOT_BODY_CLEAN_MODEL", "haiku"))


_LANG_DISPLAY = {"el": "Modern Greek", "grc": "Ancient Greek", "la": "Latin"}


@dataclass
class CleanResult:
    cleaned: str
    removed: list[str]          # verbatim source segments dropped
    hyphen_joins: list[str]     # joined-word forms (post-join), for logging


def clean_body(body_src: str, language_code: str) -> CleanResult | None:
    """Run the cleaner on one page's raw body_src. Returns None on LLM
    failure or audit failure (hallucinated removals). Pure function — no DB.

    Short-circuits to a trivially-cleaned result if body_src is too small to
    contain pollution (one line, no newlines, ≤200 chars). That covers
    blank pages and short paste imports — no LLM call worth the latency.

    Audit-failure retry loop: when Haiku truncates a sidebar at the wrong
    sentence boundary (or otherwise paraphrases a removed segment), the
    audit rejects the result. We retry up to ``MAX_AUDIT_RETRIES`` times —
    LLM non-determinism usually breaks the deadlock. After exhausting
    retries we return None and let the caller fall back to body_src.
    """
    if not body_src or not body_src.strip():
        return CleanResult(cleaned="", removed=[], hyphen_joins=[])
    if len(body_src) < 200 and body_src.count("\n") <= 1:
        return CleanResult(cleaned=body_src.strip(), removed=[], hyphen_joins=[])

    language_name = _LANG_DISPLAY.get(language_code, language_code)
    for attempt in range(MAX_AUDIT_RETRIES + 1):
        result = _clean_body_once(body_src, language_name)
        if result is not None:
            if attempt > 0:
                log.info("body_clean succeeded on attempt %d/%d",
                         attempt + 1, MAX_AUDIT_RETRIES + 1)
            return result
    log.warning("body_clean exhausted %d retries — falling back to raw body_src",
                MAX_AUDIT_RETRIES + 1)
    return None


def _clean_body_once(body_src: str, language_name: str) -> CleanResult | None:
    """One Haiku attempt. Returns None on LLM failure or audit failure."""
    schema = {
        "type": "object",
        "properties": {
            "cleaned": {"type": "string"},
            "removed": {
                "type": "array",
                "items": {"type": "string"},
            },
            "hyphen_joins": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["cleaned", "removed"],
    }
    prompt = _build_prompt(body_src, language_name)
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
        log.error("body_clean timed out after %ds (%d chars)", TIMEOUT_S, len(body_src))
        return None
    if proc.returncode != 0:
        log.error("body_clean CLI failed: %s", proc.stderr[:500])
        return None

    try:
        wrapper = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        log.error("body_clean envelope parse failed: %s\n%s", e, proc.stdout[:500])
        return None
    if not isinstance(wrapper, dict):
        log.error("body_clean unexpected CLI shape: %s", proc.stdout[:300])
        return None

    structured = wrapper.get("structured_output")
    if not isinstance(structured, dict):
        # Legacy `result` fallback, matches lemma_quality._call_claude pattern.
        result_str = wrapper.get("result", "")
        try:
            structured = json.loads(result_str) if result_str else None
        except json.JSONDecodeError:
            structured = None
    if not isinstance(structured, dict):
        log.error("body_clean no structured output: %s", proc.stdout[:300])
        return None

    cleaned = structured.get("cleaned")
    removed = structured.get("removed") or []
    hyphen_joins = structured.get("hyphen_joins") or []
    if not isinstance(cleaned, str) or not isinstance(removed, list):
        log.error("body_clean wrong field types in structured output")
        return None

    if not _verify_removals(body_src, removed):
        log.warning(
            "body_clean discarded — at least one removed segment is not "
            "verbatim in body_src (possible paraphrase)"
        )
        return None

    return CleanResult(
        cleaned=cleaned.strip(),
        removed=[r for r in removed if isinstance(r, str)],
        hyphen_joins=[h for h in hyphen_joins if isinstance(h, str)],
    )


def _verify_removals(body_src: str, removed: list) -> bool:
    """Every removed segment must be a substring of body_src. If any isn't,
    the LLM hallucinated content — discard the whole result.

    Tolerated normalisations on both sides of the comparison:

    1. **Whitespace collapse.** A citation that wraps across two lines in
       the source ("Ηρόδοτος, Α, 193 μετ.\\nΑγγ. Βλάχου") arrives from the
       LLM as a single line; collapsing runs of whitespace to one space on
       both sides masks the layout drift.
    2. **Soft-hyphen joining.** Haiku silently joins line-break hyphens
       inside removed segments too: source ``δυναμώ-\\nνει`` shows up as
       ``δυναμώνει`` in the returned segment. We strip ``-\\n`` from both
       sides before comparing so that's not a false hallucination.
    3. **Footnote-digit detach.** A word with a fused footnote marker
       (``γεράνια1``) is returned by Haiku as the bare word (``γεράνια``)
       inside removed segments too — same DETACH rule the prompt asks for
       on the kept prose. We strip the trailing digit on both sides.

    These are the same structural normalisations the LLM is allowed to do
    on the *kept* prose; we extend them to the audit so we don't punish
    consistent behaviour. We do NOT loosen further (no fuzzy-match, no
    word-overlap thresholds) — the whole point of the audit is to catch
    paraphrasing.
    """
    if not removed:
        return True
    src_norm = _normalize_for_audit(body_src)
    for seg in removed:
        if not isinstance(seg, str) or not seg.strip():
            continue
        if seg in body_src:
            continue
        if _normalize_for_audit(seg) in src_norm:
            continue
        log.warning("body_clean removal not found in source: %r", seg[:160])
        return False
    return True


_SOFTHYPHEN_LINEBREAK = re.compile(r"[-‐‑–][ \t]*\n[ \t]*")
# Trailing digit(s) immediately after a letter, when followed by a non-word
# char or end-of-string — the footnote-marker pattern (γεράνια1. → γεράνια.).
# `[^\W\d_]` is the Unicode-letter idiom in plain `re` (no `regex` dependency).
_FOOTNOTE_DIGIT = re.compile(r"([^\W\d_])\d+(?=\W|$)", re.UNICODE)
# C0/C1 control chars PyMuPDF leaks from PDFs (e.g. BEL=0x07 in front of
# footnote markers). Keep \t and \n so the whitespace collapse handles them
# uniformly.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0B-\x1F\x7F-\x9F]")


def _normalize_for_audit(s: str) -> str:
    """Apply every transformation Haiku is allowed to silently perform on
    text it keeps OR removes: control-char strip (PyMuPDF leaks BEL etc.),
    soft-hyphen joining, footnote-digit detach, whitespace collapse. Used
    for the verbatim audit only — never for storage."""
    s = _CONTROL_CHARS.sub("", s)
    s = _SOFTHYPHEN_LINEBREAK.sub("", s)
    s = _FOOTNOTE_DIGIT.sub(r"\1", s)
    return " ".join(s.split())


def _build_prompt(body_src: str, language_name: str) -> str:
    return f"""You are a {language_name} reading-app text cleaner. The input below is one page extracted from a PDF textbook by PyMuPDF. Your job is to return ONLY the body prose a learner should read — strip pollution that was inlined by the extractor.

DROP (return in `removed`, verbatim from the source):
- Standalone page numbers, running headers, running footers
- Section/chapter numbers and titles (e.g. "1.1 Η χώρα", "Ι. ΟΙ ΠΟΛΙΤΙΣΜΟΙ"), even when they appear mid-flow
- Footnote definitions at the bottom of the page (e.g. "1. γεράνι ή γερανός: ανυψωτικό μηχάνημα.")
- Bibliographic citations attached to inline quotes (e.g. "Ηρόδοτος, Α, 193 μετ. Αγγ. Βλάχου, εκδ. Δ. Παπαδήμα.")
- Caption/figure labels, "Πηγή:", "Εικόνα N", "Πίνακας N"
- Sidebar headers that duplicate the section title

JOIN (silently, but list each joined form in `hyphen_joins`):
- End-of-line soft-hyphens that split a single word: `διαρ-\\nρέουν` → `διαρρέουν`, `ανατολι-\\nκά` → `ανατολικά`. The hyphen must be at the end of a line AND followed by an alphabetic letter on the next line.
- DO NOT join real compound-word hyphens that appear mid-line (e.g. proper-noun ranges, "Bizans-Kayseri" style).

DETACH (silently):
- Footnote markers fused into words. PyMuPDF extracts superscript digits as plain digits stuck to the preceding word: `σιτηρών1` → `σιτηρών`, `γεράνια1` → `γεράνια`, `θεός2` → `θεός`. Drop the trailing digit(s) when the rest of the token is a real {language_name} word.

PRESERVE EXACTLY:
- Every other character of the body prose. Do not paraphrase, modernise spelling, translate, re-order sentences, or "fix" punctuation. Whitespace may be normalised (collapse runs of spaces/newlines into single spaces or paragraph breaks), but no actual letters may change.
- If a section title is the ONLY content on the page (a chapter divider), return it as the cleaned text and put nothing in `removed`. Don't return an empty page just because there's no prose.

OUTPUT:
- `cleaned`: the prose, ready to read. Paragraphs separated by single blank lines.
- `removed`: each dropped segment verbatim from the source. One entry per removed run; don't merge unrelated removals into one string.
- `hyphen_joins`: each post-join word form (e.g. "διαρρέουν"). Optional; helps with debugging.

SOURCE TEXT:
\"\"\"
{body_src}
\"\"\"
"""
