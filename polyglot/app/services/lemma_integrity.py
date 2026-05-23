"""Lemma integrity primitives — repair a Lemma row to its correct citation form.

simplemma frequently stores an inflected surface form as if it were the lemma
(εξελίχθηκαν instead of εξελίσσομαι, πλεονάσματος instead of πλεόνασμα). This
module is the single, FK-safe way to fix such a row, used by:

- the one-time bulk cleanup (``scripts/audit_lemmas.py``), and
- the going-forward reading-intake LLM citation pass.

Two outcomes:

- **rename** — the correct citation form has no existing Lemma row, so we update
  this row in place (``lemma_form`` / ``lemma_bare`` / ``pos`` / ``gloss_en``).
- **merge** — the correct citation form already exists as a separate Lemma, so
  this row is a duplicate. We repoint every inbound FK to the canonical row,
  consolidate the ``UserLemmaKnowledge`` study record, and delete the duplicate.
  ``surface_form`` text on ``sentence_words`` / ``page_words`` preserves which
  inflection actually appeared, so no information is lost by repointing lemma_id.

Inbound FK columns to ``lemmas.lemma_id`` (must all be handled on merge):
  user_lemma_knowledge.lemma_id  (UNIQUE, NOT NULL)
  review_log.lemma_id            (NOT NULL)
  sentence_words.lemma_id
  sentences.target_lemma_id
  page_words.lemma_id
  frequency_entries.lemma_id
  content_flags.lemma_id
  lemmas.canonical_lemma_id      (self)
  lemmas.cognate_lemma_id        (self)
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Lemma, UserLemmaKnowledge
from app.services.languages import get_provider
from app.services.llm_cli import call_structured_json, resolve_model

log = logging.getLogger(__name__)

# ─── LLM citation-form judge (LLM as the lemmatizer of record) ──────────────
# simplemma/library lemmatizers have poor recall on inflected Modern Greek;
# the configured structured-output LLM is the authority for what a lemma's
# citation form is. Shared by the one-time bulk cleanup (scripts/audit_lemmas.py)
# and the going-forward reading-intake repair pass (repair_lemmas).

_MODEL_ALIASES = {
    "sonnet": "claude-sonnet-4-5-20250929",
    "haiku": "claude-haiku-4-5-20251001",
}
AUDIT_MODEL = resolve_model(os.environ.get("POLYGLOT_AUDIT_MODEL", "claude-sonnet-4-5-20250929"), _MODEL_ALIASES)
AUDIT_TIMEOUT_S = int(os.environ.get("POLYGLOT_AUDIT_TIMEOUT", "300"))

LANG_NAME = {"el": "Modern Greek", "grc": "Ancient Greek", "la": "Latin"}
VALID_STATUS = {"ok", "fix", "proper_name", "function_word", "not_word"}
VALID_POS = {
    "noun", "verb", "adjective", "adverb", "pronoun", "preposition",
    "conjunction", "article", "particle", "numeral", "proper_noun", "other",
}


def judge_prompt(language: str, items: list[dict]) -> str:
    """``items`` = ``[{"index": int, "form": str, "gloss": str}]``."""
    lang = LANG_NAME.get(language, language)
    lines = []
    for it in items:
        g = f"  (current gloss: {it['gloss']})" if it.get("gloss") else ""
        lines.append(f"[{it['index']}] {it['form']}{g}")
    block = "\n".join(lines)
    return f"""You are a {lang} lexicographer auditing a learner's vocabulary database.

Each entry below has a stored "lemma" that SHOULD be the dictionary citation
form, plus an English gloss. Many entries were produced by an automatic
lemmatizer that frequently failed, leaving an INFLECTED surface form stored as
if it were the lemma (a verb in a past/passive tense, a noun in genitive or
plural, an adjective in a comparative or non-masculine form).

For each entry decide a `status`:
- "ok": the stored form already IS the correct {lang} citation form.
- "fix": it is an inflected or misspelled form — give the correct citation form
  in `citation` (with correct accents/diacritics).
- "proper_name": it is a true named entity (person, place, organization,
  divinity, title); put the standard nominative form in `citation`.
  Do NOT use "proper_name" merely for capitalization. Demonyms/nationality
  words, weekdays, months, and lexical adverbs are learnable vocabulary unless
  the entry is clearly a named entity in its own right.
- "function_word": article, pronoun, preposition, conjunction, or particle.
- "not_word": OCR fragment / truncation / junk. If the intended word is
  inferable from the gloss put it in `citation`, otherwise leave it blank.

Citation-form rules for {lang}:
- Verbs: 1st person singular present (active -ω; deponent/passive-only -ομαι/-άμαι).
  e.g. εξελίχθηκαν → εξελίσσομαι, στηριζόταν → στηρίζομαι, οργανώθηκαν → οργανώνω.
- Nouns: nominative singular. e.g. πλεονάσματος → πλεόνασμα, εκδηλώσεις → εκδήλωση.
- Adjectives: nominative singular masculine, positive degree.
  e.g. ευρύτερο → ευρύς, συστηματική → συστηματικός.

Also return `pos` (one of: noun, verb, adjective, adverb, pronoun, preposition,
conjunction, article, particle, numeral, proper_noun, other) and `gloss` — an
accurate English gloss for the CITATION form. If the existing gloss already fits
the citation form, repeat it; if it described the inflection (e.g. "you will
shoot"), correct it to the lemma gloss ("to shoot").

Reference every entry by its bracketed [index]. Skip nothing.

Entries:
{block}
"""


def judge_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "status": {"type": "string", "enum": sorted(VALID_STATUS)},
                        "citation": {"type": "string"},
                        "pos": {"type": "string"},
                        "gloss": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    "required": ["index", "status"],
                },
            },
        },
        "required": ["results"],
    }


def judge_lemmas(language: str, items: list[dict], *, model: str = AUDIT_MODEL) -> Optional[dict[int, dict]]:
    """One LLM call over a batch of lemmas. Returns ``{index: verdict}`` or
    ``None`` on LLM failure (caller must treat failure as 'leave unchanged' —
    never as 'all ok'). Pure function: no DB access."""
    if not items:
        return {}
    structured = call_structured_json(
        prompt=judge_prompt(language, items),
        schema=judge_schema(),
        model=model,
        timeout_s=AUDIT_TIMEOUT_S,
        log_context="citation_judge",
        runner=subprocess.run,
    )
    if not isinstance(structured, dict):
        return None
    out: dict[int, dict] = {}
    for r in structured.get("results", []) or []:
        if isinstance(r, dict) and isinstance(r.get("index"), int) and r.get("status") in VALID_STATUS:
            out[r["index"]] = r
    return out


def _verdict_to_fix_args(v: dict) -> dict:
    """Normalize a raw judge verdict into apply_citation_fix kwargs."""
    status = v.get("status")
    pos = (v.get("pos") or "").strip().lower() or None
    if pos and pos not in VALID_POS:
        pos = None
    gloss = (v.get("gloss") or "").strip() or None
    citation = (v.get("citation") or "").strip()
    word_category = None
    if status == "proper_name":
        word_category = "proper_name"
        pos = pos or "proper_noun"
    elif status == "function_word":
        word_category = "function_word"
    clear_word_category = status in {"ok", "fix"}
    return {"citation": citation, "pos": pos, "gloss": gloss, "word_category": word_category,
            "clear_word_category": clear_word_category, "status": status}


def _apply_verdict_to_lemma(db: Session, lemma_id: int, v: dict) -> "FixResult":
    """Apply one judge verdict to one lemma row."""
    args = _verdict_to_fix_args(v)
    status = args.pop("status")
    if status == "not_word" and not args.get("citation"):
        return retire_noise_lemma(db, lemma_id, reason=(v.get("note") or "").strip() or None)
    if status == "ok":
        # Confirm form; only fill metadata.
        lemma = db.get(Lemma, lemma_id)
        if lemma is None:
            return FixResult(lemma_id=lemma_id, action="skip", detail={"reason": "missing"})
        args["citation"] = lemma.lemma_form
    return apply_citation_fix(db, lemma_id, **args)


def repair_lemma(
    db: Session, language: str, lemma_id: int, *,
    model: str = AUDIT_MODEL,
) -> "FixResult":
    """LLM-audit and repair a single lemma. Caller commits."""
    row = (
        db.query(Lemma.lemma_id, Lemma.lemma_form, Lemma.gloss_en)
        .filter(Lemma.lemma_id == lemma_id)
        .first()
    )
    if row is None:
        return FixResult(lemma_id=lemma_id, action="skip", detail={"reason": "missing"})
    verdicts = judge_lemmas(
        language,
        [{"index": 0, "form": row.lemma_form, "gloss": row.gloss_en or ""}],
        model=model,
    )
    if verdicts is None:
        return FixResult(lemma_id=lemma_id, action="skip", old_form=row.lemma_form,
                         detail={"reason": "llm_failed"})
    verdict = verdicts.get(0)
    if verdict is None:
        return FixResult(lemma_id=lemma_id, action="skip", old_form=row.lemma_form,
                         detail={"reason": "unjudged"})
    return _apply_verdict_to_lemma(db, lemma_id, verdict)


def repair_lemmas(
    db: Session, language: str, lemma_ids: list[int], *,
    batch_size: int = 40, model: str = AUDIT_MODEL,
) -> dict:
    """Citation-audit the given lemmas via the configured LLM and repair them in place.

    Used by the reading-intake going-forward path: every newly-created lemma is
    judged on its form + gloss before it can enter the study pool, so simplemma
    can never leave an inflected form in the vocabulary. Lock-safe: the LLM call
    holds no DB write lock; fixes are applied + committed per batch.
    """
    if not lemma_ids:
        return {}
    rows = (
        db.query(Lemma.lemma_id, Lemma.lemma_form, Lemma.gloss_en)
        .filter(Lemma.lemma_id.in_(set(lemma_ids)))
        .all()
    )
    work = [{"lemma_id": lid, "form": form, "gloss": gloss or ""} for lid, form, gloss in rows]

    from collections import Counter
    actions: Counter = Counter()
    for start in range(0, len(work), batch_size):
        chunk = work[start:start + batch_size]
        items = [{"index": i, "form": c["form"], "gloss": c["gloss"]} for i, c in enumerate(chunk)]
        verdicts = judge_lemmas(language, items, model=model)  # no write lock held
        if verdicts is None:
            actions["llm_failed"] += len(chunk)
            continue
        for i, c in enumerate(chunk):
            v = verdicts.get(i)
            if v is None:
                actions["unjudged"] += 1
                continue
            res = _apply_verdict_to_lemma(db, c["lemma_id"], v)
            actions[res.action] += 1
        db.commit()
    return dict(actions)

# Tables/columns holding a plain (non-self) FK to lemmas.lemma_id.
_FK_REFS: list[tuple[str, str]] = [
    ("review_log", "lemma_id"),
    ("sentence_words", "lemma_id"),
    ("sentences", "target_lemma_id"),
    ("page_words", "lemma_id"),
    ("frequency_entries", "lemma_id"),
    ("content_flags", "lemma_id"),
]
# Self-referential FK columns on lemmas (rows that *point at* the source).
_SELF_REFS: list[str] = ["canonical_lemma_id", "cognate_lemma_id"]

# Which knowledge_state wins when consolidating two study records.
_STATE_RANK = {
    "new": 0, "encountered": 1, "acquiring": 2,
    "lapsed": 3, "learning": 4, "known": 5,
}


@dataclass
class FixResult:
    lemma_id: int
    action: str                       # "rename" | "merge" | "retire" | "noop" | "skip"
    old_form: str = ""
    new_form: str = ""
    target_id: Optional[int] = None   # canonical row kept, on merge
    detail: dict = field(default_factory=dict)


def _merge_ulk(db: Session, source: UserLemmaKnowledge, target: UserLemmaKnowledge) -> None:
    """Fold the source study record into the target, keeping the more-advanced
    state. Counters sum; timestamps take the earliest start / latest activity.
    The source row is deleted by the caller (after FK repoint)."""
    target.times_seen = (target.times_seen or 0) + (source.times_seen or 0)
    target.times_correct = (target.times_correct or 0) + (source.times_correct or 0)
    target.total_encounters = (target.total_encounters or 0) + (source.total_encounters or 0)
    target.distinct_contexts = max(target.distinct_contexts or 0, source.distinct_contexts or 0)
    target.leech_count = (target.leech_count or 0) + (source.leech_count or 0)

    # Keep the more-advanced knowledge state and its scheduling payload.
    if _STATE_RANK.get(source.knowledge_state or "new", 0) > _STATE_RANK.get(target.knowledge_state or "new", 0):
        target.knowledge_state = source.knowledge_state
        target.fsrs_card_json = source.fsrs_card_json
        target.acquisition_box = source.acquisition_box
        target.acquisition_next_due = source.acquisition_next_due
        target.graduated_at = source.graduated_at

    def _earliest(a, b):
        xs = [x for x in (a, b) if x is not None]
        return min(xs) if xs else None

    def _latest(a, b):
        xs = [x for x in (a, b) if x is not None]
        return max(xs) if xs else None

    target.introduced_at = _earliest(target.introduced_at, source.introduced_at)
    target.acquisition_started_at = _earliest(target.acquisition_started_at, source.acquisition_started_at)
    target.entered_acquiring_at = _earliest(target.entered_acquiring_at, source.entered_acquiring_at)
    target.last_reviewed = _latest(target.last_reviewed, source.last_reviewed)
    target.experiment_intro_shown_at = _latest(target.experiment_intro_shown_at, source.experiment_intro_shown_at)
    if source.leech_suspended_at and not target.leech_suspended_at:
        target.leech_suspended_at = source.leech_suspended_at


def merge_lemma_into(db: Session, source_id: int, target_id: int) -> dict:
    """Repoint every inbound FK from ``source_id`` to ``target_id``, consolidate
    the ULK study record, then delete the source row. Caller commits.

    Returns a summary of rows touched. No-op-safe if source == target.
    """
    if source_id == target_id:
        return {"skipped": "same_lemma"}

    counts: dict[str, int] = {}
    for table, col in _FK_REFS:
        res = db.execute(
            text(f"UPDATE {table} SET {col} = :tgt WHERE {col} = :src"),
            {"tgt": target_id, "src": source_id},
        )
        if res.rowcount:
            counts[f"{table}.{col}"] = res.rowcount
    res = db.execute(
        text("UPDATE page_words SET original_lemma_id = :tgt WHERE original_lemma_id = :src"),
        {"tgt": target_id, "src": source_id},
    )
    if res.rowcount:
        counts["page_words.original_lemma_id"] = res.rowcount

    # Rows pointing AT the source via a self-ref move to the target. A row whose
    # canonical/cognate becomes itself is cleared (no self-loops).
    for col in _SELF_REFS:
        res = db.execute(
            text(f"UPDATE lemmas SET {col} = :tgt WHERE {col} = :src"),
            {"tgt": target_id, "src": source_id},
        )
        if res.rowcount:
            counts[f"lemmas.{col}"] = res.rowcount
    db.execute(
        text(f"UPDATE lemmas SET canonical_lemma_id = NULL WHERE canonical_lemma_id = lemma_id"),
    )
    db.execute(
        text(f"UPDATE lemmas SET cognate_lemma_id = NULL WHERE cognate_lemma_id = lemma_id"),
    )

    # Consolidate ULK. UNIQUE(lemma_id) means at most one each.
    src_ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == source_id).first()
    tgt_ulk = db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == target_id).first()
    if src_ulk is not None:
        if tgt_ulk is None:
            src_ulk.lemma_id = target_id
            counts["ulk_repointed"] = 1
        else:
            _merge_ulk(db, src_ulk, tgt_ulk)
            db.delete(src_ulk)
            counts["ulk_merged"] = 1
    db.flush()

    # Delete the source row via raw SQL. An ORM ``db.delete(Lemma)`` would fire
    # the ``Lemma.knowledge`` relationship cascade and NULL the FK on the ULK we
    # just repointed; raw SQL (after the FK migration above) is the safe path.
    # Expunge the now-deleted row from the identity map so a later ``db.get``
    # doesn't return the stale cached object.
    src_obj = db.get(Lemma, source_id)
    db.execute(text("DELETE FROM lemmas WHERE lemma_id = :src"), {"src": source_id})
    if src_obj is not None:
        db.expunge(src_obj)
    counts["lemma_deleted"] = 1
    return counts


def retire_noise_lemma(db: Session, lemma_id: int, *, reason: Optional[str] = None) -> FixResult:
    """Delete a row that the judge says is not a word and has no recoverable
    citation form.

    Nullable content mappings are nulled. Generated sentences targeting the
    junk row are deactivated. ULK and ReviewLog rows are deleted because they
    describe practice on a non-word and cannot be meaningfully migrated.
    """
    lemma = db.get(Lemma, lemma_id)
    if lemma is None:
        return FixResult(lemma_id=lemma_id, action="skip", detail={"reason": "missing"})
    old_form = lemma.lemma_form
    counts: dict[str, int | str] = {}

    for table, col in [
        ("sentence_words", "lemma_id"),
        ("page_words", "lemma_id"),
        ("frequency_entries", "lemma_id"),
        ("content_flags", "lemma_id"),
    ]:
        res = db.execute(
            text(f"UPDATE {table} SET {col} = NULL WHERE {col} = :src"),
            {"src": lemma_id},
        )
        if res.rowcount:
            counts[f"{table}.{col}_nulled"] = res.rowcount

    res = db.execute(
        text("UPDATE sentences SET target_lemma_id = NULL, is_active = 0 WHERE target_lemma_id = :src"),
        {"src": lemma_id},
    )
    if res.rowcount:
        counts["sentences.target_lemma_id_retired"] = res.rowcount

    for col in _SELF_REFS:
        res = db.execute(
            text(f"UPDATE lemmas SET {col} = NULL WHERE {col} = :src"),
            {"src": lemma_id},
        )
        if res.rowcount:
            counts[f"lemmas.{col}_nulled"] = res.rowcount

    res = db.execute(
        text("UPDATE page_words SET original_lemma_id = NULL WHERE original_lemma_id = :src"),
        {"src": lemma_id},
    )
    if res.rowcount:
        counts["page_words.original_lemma_id_nulled"] = res.rowcount

    for table in ["review_log", "user_lemma_knowledge"]:
        res = db.execute(text(f"DELETE FROM {table} WHERE lemma_id = :src"), {"src": lemma_id})
        if res.rowcount:
            counts[f"{table}_deleted"] = res.rowcount

    db.execute(text("DELETE FROM lemmas WHERE lemma_id = :src"), {"src": lemma_id})
    db.expunge(lemma)
    counts["lemma_deleted"] = 1
    if reason:
        counts["reason"] = reason[:300]
    return FixResult(lemma_id=lemma_id, action="retire", old_form=old_form, detail=counts)


def apply_citation_fix(
    db: Session,
    lemma_id: int,
    citation: str,
    pos: Optional[str] = None,
    gloss: Optional[str] = None,
    *,
    word_category: Optional[str] = None,
    clear_word_category: bool = False,
) -> FixResult:
    """Make ``lemma_id`` carry the correct citation form.

    - If an existing *canonical* (non-variant) lemma already holds that citation
      form, this row is a duplicate → ``merge_lemma_into`` it.
    - Otherwise rename this row in place.

    ``citation`` should be the dictionary form with accents. ``pos`` /
    ``gloss`` / ``word_category`` are written when provided. Caller commits.
    """
    lemma = db.get(Lemma, lemma_id)
    if lemma is None:
        return FixResult(lemma_id=lemma_id, action="skip", detail={"reason": "missing"})

    provider = get_provider(lemma.language_code)
    now = datetime.now(timezone.utc)
    citation = (citation or "").strip()
    if not citation:
        # No proposed form (e.g. unidentifiable fragment) — just stamp metadata.
        if pos and not lemma.pos:
            lemma.pos = pos
        if word_category:
            lemma.word_category = word_category
        elif clear_word_category:
            lemma.word_category = None
        lemma.gates_completed_at = now
        return FixResult(lemma_id=lemma_id, action="noop", old_form=lemma.lemma_form,
                         detail={"reason": "blank_citation"})

    new_bare = provider.normalize_bare(citation)
    old_form = lemma.lemma_form

    # Already correct (form matches up to accents) → only fill metadata.
    if new_bare == lemma.lemma_bare and citation == lemma.lemma_form:
        changed = {}
        if pos and lemma.pos != pos:
            lemma.pos = pos; changed["pos"] = pos
        if gloss and gloss.strip() and gloss.strip() != (lemma.gloss_en or "").strip():
            lemma.gloss_en = gloss.strip(); changed["gloss"] = True
        if word_category and lemma.word_category != word_category:
            lemma.word_category = word_category; changed["word_category"] = word_category
        elif clear_word_category and lemma.word_category is not None:
            lemma.word_category = None; changed["word_category"] = None
        lemma.gates_completed_at = now
        return FixResult(lemma_id=lemma_id, action="noop", old_form=old_form,
                         new_form=citation, detail=changed)

    # Is there another canonical lemma already holding this exact citation
    # form? Do not merge merely on lemma_bare: Modern Greek accent placement
    # distinguishes real words/proper names (Αθήνα = Athens, Αθηνά = Athena).
    target = (
        db.query(Lemma)
        .filter(
            Lemma.language_code == lemma.language_code,
            Lemma.lemma_bare == new_bare,
            Lemma.lemma_form == citation,
            Lemma.lemma_id != lemma_id,
            Lemma.canonical_lemma_id.is_(None),
        )
        .order_by(Lemma.lemma_id.asc())
        .first()
    )

    if target is not None:
        # Make sure the kept row carries the best metadata before we fold in.
        if citation and target.lemma_form != citation and provider.normalize_bare(target.lemma_form) == new_bare:
            target.lemma_form = citation
        if pos and not target.pos:
            target.pos = pos
        if gloss and gloss.strip() and not (target.gloss_en or "").strip():
            target.gloss_en = gloss.strip()
        if word_category and not target.word_category:
            target.word_category = word_category
        elif clear_word_category and target.word_category is not None:
            target.word_category = None
        target.gates_completed_at = now
        counts = merge_lemma_into(db, lemma_id, target.lemma_id)
        return FixResult(lemma_id=lemma_id, action="merge", old_form=old_form,
                         new_form=citation, target_id=target.lemma_id, detail=counts)

    # Rename in place.
    lemma.lemma_form = citation
    lemma.lemma_bare = new_bare
    if pos:
        lemma.pos = pos
    if gloss and gloss.strip():
        lemma.gloss_en = gloss.strip()
    if word_category:
        lemma.word_category = word_category
    elif clear_word_category:
        lemma.word_category = None
    lemma.gates_completed_at = now
    # Re-link Modern↔Ancient cognate now that the bare form is correct.
    try:
        from app.services.reading_intake import link_intra_greek_cognates
        link_intra_greek_cognates(db, lemma)
    except Exception:
        pass
    return FixResult(lemma_id=lemma_id, action="rename", old_form=old_form,
                     new_form=citation, detail={"new_bare": new_bare})
