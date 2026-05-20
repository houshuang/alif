"""Cognate detection — find transparent L1 cognates for new Greek/Latin lemmas.

Two distinct concerns:

1. **Modern ↔ Ancient Greek linking** (`link_intra_greek_cognates`): cheap,
   deterministic. Match `lemma_bare` across `el` and `grc` lemmas; set
   `cognate_lemma_id` bidirectionally. Used so marking φιλία known in Modern
   can propagate to Ancient φιλία (as 'encountered' — semantic drift means
   it isn't automatically 'known').

2. **External L1 cognates** (`detect_external_cognates`): LLM-based. For each
   new lemma, ask Claude whether it has transparent cognates in the user's
   known languages (English, Norwegian, German, French, Italian, Spanish by
   default). Results stamped to `Lemma.cognates_json`. If transparency is
   'high' and the profile's threshold allows, auto-create a ULK in 'known'
   state (source='cognate').

Both run *after* page processing, not as part of the page-view critical path.
For now they're opt-in via env var so we can iterate on quality before turning
on auto-marking.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Lemma, UserLemmaKnowledge, UserProfile

log = logging.getLogger(__name__)

COGNATE_DETECTION_ENABLED = os.environ.get("POLYGLOT_DETECT_COGNATES", "0") == "1"
COGNATE_AUTO_MARK = os.environ.get("POLYGLOT_AUTO_MARK_COGNATES", "0") == "1"
COGNATE_BATCH_SIZE = int(os.environ.get("POLYGLOT_COGNATE_BATCH", "20"))


# ─── Modern ↔ Ancient bare-form linking ────────────────────────────────────

def link_intra_greek_cognates(db: Session, lemma: Lemma) -> Lemma | None:
    """If the new lemma is Greek (`el` or `grc`), find a counterpart in the
    other variety with the same `lemma_bare` and bidirectionally link them
    via `cognate_lemma_id`. Returns the linked counterpart, or None.

    Cheap — pure DB lookup. Called after every reading_intake Lemma creation.
    """
    pair = {"el": "grc", "grc": "el"}
    other = pair.get(lemma.language_code)
    if not other:
        return None
    match = (
        db.query(Lemma)
        .filter(Lemma.language_code == other, Lemma.lemma_bare == lemma.lemma_bare)
        .first()
    )
    if not match:
        return None
    if lemma.cognate_lemma_id is None:
        lemma.cognate_lemma_id = match.lemma_id
    if match.cognate_lemma_id is None:
        match.cognate_lemma_id = lemma.lemma_id
    db.flush()
    log.debug("Linked %s ↔ %s via bare match '%s'",
              lemma.lemma_form, match.lemma_form, lemma.lemma_bare)
    return match


def propagate_known_via_cognate(db: Session, lemma_id: int):
    """When a lemma is marked known, mark its cognate (Modern↔Ancient) as
    'encountered' so the user sees it pre-flagged in future pages without
    auto-promoting to 'known'. Semantic drift is real — let the user confirm.

    The cognate target is redirected to its canonical before ULK lookup/create
    per Hard Invariant #9.
    """
    from app.services.canonical_resolution import resolve_canonical_lemma_id

    lemma = db.get(Lemma, lemma_id)
    if not lemma or not lemma.cognate_lemma_id:
        return
    target_id = resolve_canonical_lemma_id(db, lemma.cognate_lemma_id)
    cognate_ulk = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == target_id)
        .first()
    )
    if cognate_ulk is not None:
        return  # don't overwrite existing state
    db.add(UserLemmaKnowledge(
        lemma_id=target_id,
        knowledge_state="encountered",
        source="cognate_propagation",
        introduced_at=datetime.now(timezone.utc),
    ))
    db.commit()
    log.info("Propagated 'encountered' to cognate lemma_id=%d", target_id)


# ─── External L1 cognates (LLM-based) ──────────────────────────────────────

@dataclass
class CognateBatch:
    lemmas: list[Lemma]
    known_languages: list[str]
    language_name: str   # 'Modern Greek' / 'Ancient Greek' / 'Latin'


def get_user_profile(db: Session) -> UserProfile:
    """Return the singleton UserProfile, creating defaults if missing."""
    profile = db.query(UserProfile).first()
    if profile is None:
        profile = UserProfile()
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


def detect_external_cognates(
    db: Session,
    lemmas: list[Lemma],
    *,
    force: bool = False,
) -> int:
    """Detect L1 cognates for a batch of lemmas via Claude CLI. Stamps
    `cognates_json` + `cognates_detected_at`. Optionally auto-marks high-
    transparency cognates as known.

    Returns the count of lemmas successfully processed.

    Gated by POLYGLOT_DETECT_COGNATES=1 — off by default so we can iterate on
    prompt quality before live use.
    """
    if not COGNATE_DETECTION_ENABLED and not force:
        log.debug("Cognate detection disabled (POLYGLOT_DETECT_COGNATES != 1)")
        return 0

    targets = [l for l in lemmas if l.cognates_detected_at is None or force]
    if not targets:
        return 0

    profile = get_user_profile(db)
    known_languages = profile.known_languages or ["en"]

    # Group by language (rare but defensive)
    by_lang: dict[str, list[Lemma]] = {}
    for l in targets:
        by_lang.setdefault(l.language_code, []).append(l)

    LANG_NAMES = {"el": "Modern Greek", "grc": "Ancient Greek", "la": "Latin"}
    L1_NAMES = {"en": "English", "no": "Norwegian", "de": "German",
                "fr": "French", "it": "Italian", "es": "Spanish"}

    processed = 0
    for lang_code, group in by_lang.items():
        for i in range(0, len(group), COGNATE_BATCH_SIZE):
            chunk = group[i:i + COGNATE_BATCH_SIZE]
            try:
                results = _call_claude_for_cognates(
                    chunk,
                    source_language=LANG_NAMES.get(lang_code, lang_code),
                    l1_names=[L1_NAMES.get(c, c) for c in known_languages],
                )
            except Exception as e:
                log.warning("Cognate detection batch failed (%s): %s", lang_code, e)
                continue
            now = datetime.now(timezone.utc)
            for lemma, cognates in zip(chunk, results):
                lemma.cognates_json = cognates
                lemma.cognates_detected_at = now
                processed += 1
                if COGNATE_AUTO_MARK and _has_high_transparency(cognates, profile.cognate_auto_mark_threshold):
                    _auto_mark_known(db, lemma)
            db.commit()
    return processed


def _call_claude_for_cognates(
    lemmas: list[Lemma],
    *,
    source_language: str,
    l1_names: list[str],
) -> list[list[dict]]:
    """Single Claude CLI call covering up to COGNATE_BATCH_SIZE lemmas.

    Uses `--json-schema` for constrained decoding (mandatory for CLI per
    CLAUDE.md — without it Sonnet wraps JSON in prose and parsing breaks).
    """
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "lemma": {"type": "string"},
                "cognates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "lang": {"type": "string"},
                            "form": {"type": "string"},
                            "transparency": {"type": "string", "enum": ["high", "medium", "low"]},
                            "note": {"type": "string"},
                        },
                        "required": ["lang", "form", "transparency"],
                    },
                },
            },
            "required": ["lemma", "cognates"],
        },
    }
    lemma_list = "\n".join(f"- {l.lemma_form} ({l.pos or '?'})" for l in lemmas)
    l1_csv = ", ".join(l1_names)
    prompt = f"""For each {source_language} lemma below, identify transparent cognates in: {l1_csv}.

Transparency:
- "high": instantly recognizable spelling+meaning to a reader of the L1 (e.g. φιλοσοφία → philosophy / Philosophie / philosophie)
- "medium": recognizable with a short hint (e.g. ποίηση → poetry / poésie)
- "low": etymologically related but not obvious at sight (e.g. πατέρας → father / Vater)

Skip languages where no cognate exists. Skip lemmas with no cognates anywhere. Return one entry per input lemma in input order; if no cognates found, return cognates: [].

Lemmas:
{lemma_list}
"""
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", "claude-sonnet-4-5-20250929",
        "--json-schema", json.dumps(schema),
        prompt,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {proc.stderr[:500]}")
    # CLI wraps output as {"result": "..."} — parse the result string as JSON
    try:
        wrapper = json.loads(proc.stdout)
        result = wrapper.get("result", proc.stdout) if isinstance(wrapper, dict) else proc.stdout
        parsed = json.loads(result) if isinstance(result, str) else result
    except json.JSONDecodeError as e:
        raise RuntimeError(f"could not parse claude JSON output: {e}") from e

    # Align response order with input — match by lemma form, fall back to position
    by_form = {entry["lemma"]: entry.get("cognates", []) for entry in parsed if isinstance(entry, dict)}
    return [by_form.get(l.lemma_form, []) for l in lemmas]


def _has_high_transparency(cognates: list[dict], threshold: str) -> bool:
    if threshold == "never" or not cognates:
        return False
    order = {"high": 3, "medium": 2, "low": 1}
    floor = order.get(threshold, 3)
    return any(order.get(c.get("transparency", "low"), 1) >= floor for c in cognates)


def _auto_mark_known(db: Session, lemma: Lemma):
    """Create a ULK in 'known' state for a lemma with high-transparency cognate.

    Redirects to canonical at entry per Hard Invariant #9.
    """
    from app.services.canonical_resolution import resolve_canonical_lemma_id

    target_id = resolve_canonical_lemma_id(db, lemma.lemma_id)
    existing = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == target_id)
        .first()
    )
    if existing:
        return
    db.add(UserLemmaKnowledge(
        lemma_id=target_id,
        knowledge_state="known",
        source="cognate",
        introduced_at=datetime.now(timezone.utc),
    ))
    log.info("Auto-marked %s as known via L1 cognate", lemma.lemma_form)
