"""Tiny gloss generation — called on-demand when a user marks a lemma as
'unknown' or otherwise wants a quick English equivalent.

Deferred from page processing per the "defer as much work as possible"
directive: most words on a page are never looked up. We pay the LLM call only
for the words the user actually engages with.

Batched by the caller (e.g. mark a page of unknowns at once) — but the
single-lemma path is fine for the modal's "mark unknown → see gloss" loop,
since one Claude CLI call is ~2-3s and the modal can show a spinner.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Lemma

log = logging.getLogger(__name__)

GLOSS_MODEL = os.environ.get("POLYGLOT_GLOSS_MODEL", "claude-haiku-4-5-20251001")
TIMEOUT_S = int(os.environ.get("POLYGLOT_GLOSS_TIMEOUT", "60"))
# Haiku reliably handles ~50 lemma definitions in one structured-output call;
# larger batches start dropping entries. Configurable for tuning.
BATCH_CHUNK_SIZE = int(os.environ.get("POLYGLOT_GLOSS_CHUNK", "50"))
LANG_DISPLAY = {"el": "Modern Greek", "grc": "Ancient Greek", "la": "Latin"}


def ensure_gloss(db: Session, lemma_id: int, *, context: str | None = None, force: bool = False) -> Lemma | None:
    """Return the lemma with a populated `gloss_en`. If missing and not
    force=False, runs Claude. Idempotent — once glossed, returns immediately.
    """
    lemma = db.get(Lemma, lemma_id)
    if lemma is None:
        return None
    if lemma.gloss_en and not force:
        return lemma

    gloss = _call_claude_for_gloss(lemma, context=context)
    if not gloss:
        return lemma  # leave NULL; caller can show "no gloss yet"

    lemma.gloss_en = gloss
    db.commit()
    db.refresh(lemma)
    return lemma


def ensure_glosses_batch(db: Session, lemma_ids: list[int]) -> int:
    """Gloss multiple lemmas in chunked Haiku calls. Returns the count that
    were successfully glossed.

    Filters performed before LLM call (free to skip — they don't need gloss):
      - already-glossed lemmas (idempotent)
      - function words (e.g. articles, particles — gloss is uninteresting)
      - proper names (lemma_form is the gloss)

    Chunked at ``BATCH_CHUNK_SIZE`` lemmas per call because Haiku starts
    dropping entries when asked for too many at once. Each chunk commits
    independently so a later-chunk failure doesn't lose earlier work and
    doesn't hold the SQLite write lock across multiple LLM calls
    (CLAUDE.md rule #10).
    """
    targets = [
        l for l in db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all()
        if not l.gloss_en
        and l.word_category not in ("function_word", "proper_name")
    ]
    if not targets:
        return 0
    by_lang: dict[str, list[Lemma]] = {}
    for l in targets:
        by_lang.setdefault(l.language_code, []).append(l)
    glossed = 0
    for lang, lemmas in by_lang.items():
        for i in range(0, len(lemmas), BATCH_CHUNK_SIZE):
            chunk = lemmas[i:i + BATCH_CHUNK_SIZE]
            results = _call_claude_for_gloss_batch(chunk, lang)
            for lemma, gloss in zip(chunk, results):
                if gloss:
                    lemma.gloss_en = gloss
                    glossed += 1
            db.commit()
    return glossed


# ─── Internals ─────────────────────────────────────────────────────────────

def _call_claude_for_gloss(lemma: Lemma, *, context: str | None) -> str | None:
    schema = {
        "type": "object",
        "properties": {
            "gloss": {"type": "string"},
            "register": {"type": "string", "enum": ["neutral", "formal", "colloquial", "archaic", "technical"]},
        },
        "required": ["gloss"],
    }
    lang_name = LANG_DISPLAY.get(lemma.language_code, lemma.language_code)
    ctx_block = f"\nContext: «{context}»" if context else ""
    prompt = f"""Give a tiny English gloss for this {lang_name} lemma. Stay under 6 words. If it's a verb, gloss in dictionary form (e.g. "to read"). If a noun, just the bare noun ("book", not "the book"). No examples, no etymology, just the gloss.{ctx_block}

Lemma: {lemma.lemma_form}{f" ({lemma.pos})" if lemma.pos else ""}"""
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", GLOSS_MODEL,
        "--json-schema", json.dumps(schema),
        prompt,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        log.warning("Gloss CLI timeout for lemma_id=%d", lemma.lemma_id)
        return None
    if proc.returncode != 0:
        log.warning("Gloss CLI failed: %s", proc.stderr[:300])
        return None
    try:
        wrapper = json.loads(proc.stdout)
        structured = wrapper.get("structured_output") if isinstance(wrapper, dict) else None
        if isinstance(structured, dict):
            return structured.get("gloss")
    except json.JSONDecodeError:
        pass
    return None


def _call_claude_for_gloss_batch(lemmas: list[Lemma], language_code: str) -> list[str | None]:
    schema = {
        "type": "object",
        "properties": {
            "glosses": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "lemma": {"type": "string"},
                        "gloss": {"type": "string"},
                    },
                    "required": ["lemma", "gloss"],
                },
            },
        },
        "required": ["glosses"],
    }
    lang_name = LANG_DISPLAY.get(language_code, language_code)
    lemma_list = "\n".join(
        f"- {l.lemma_form}" + (f" ({l.pos})" if l.pos else "")
        for l in lemmas
    )
    prompt = f"""For each {lang_name} lemma below, give a tiny English gloss. Stay under 6 words per lemma. Verbs in dictionary form ("to read"), nouns bare ("book"). No examples, no etymology, no Greek/Latin in the gloss.

Lemmas:
{lemma_list}"""
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", GLOSS_MODEL,
        "--json-schema", json.dumps(schema),
        prompt,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return [None] * len(lemmas)
    if proc.returncode != 0:
        return [None] * len(lemmas)
    try:
        wrapper = json.loads(proc.stdout)
        structured = wrapper.get("structured_output") if isinstance(wrapper, dict) else None
        glosses = (structured or {}).get("glosses", []) if isinstance(structured, dict) else []
    except json.JSONDecodeError:
        return [None] * len(lemmas)
    by_form = {g["lemma"]: g["gloss"] for g in glosses if isinstance(g, dict) and "lemma" in g}
    return [by_form.get(l.lemma_form) for l in lemmas]
