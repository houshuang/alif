"""DB-wide chimera audit — finds lemmas whose ``lemma_ar`` / ``lemma_ar_bare`` /
``forms_json`` carry data from different roots or different morphological
shapes.

Five categories, each catching a different chimera shape (documented in the
2026-05-20 audit, ``research/experiment-log.md``):

  D1 — Form V/VI verbs whose bare is the 3-letter root instead of the stem
  D2 — Form VII/VIII verbs with root-only bare
  D3 — Form X verbs with root-only bare
  D4 — Defective ـٍ noun whose bare lacks the explicit ya
  D5 — forms_json field from a different root than bare
  D6 — etymology_json describes a different word than the gloss (LLM-confirmed)

This module is the importable engine. The companion CLI runner is
``scripts/chimera_audit.py``. It is also called from ``warm_sentence_cache``
phase 7 once per cron pass; emits a ``chimera_audit_findings`` ActivityLog
row only when the candidate set has changed since the previous run.

Pure read — no DB writes from this module other than the idempotent
ActivityLog emission in ``emit_chimera_alert``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import ActivityLog, Lemma, Root
from app.services.activity_log import log_activity

logger = logging.getLogger(__name__)


_DIACRITICS = re.compile(r"[ً-ْٰـ]")


def _strip(text: str) -> str:
    return _DIACRITICS.sub("", text or "")


@dataclass(frozen=True)
class ChimeraCandidate:
    lemma_id: int
    category: str
    lemma_ar: str
    lemma_ar_bare: str
    gloss_en: str
    note: str


def find_chimera_candidates(db: Session) -> list[ChimeraCandidate]:
    """Scan all non-variant lemmas; return candidates flagged by D1..D5."""
    out: list[ChimeraCandidate] = []
    lemmas = (
        db.query(Lemma)
        .filter(Lemma.canonical_lemma_id.is_(None))
        .all()
    )

    for lem in lemmas:
        bare = (lem.lemma_ar_bare or "").strip()
        if not bare:
            continue
        ar = _strip(lem.lemma_ar or "")
        ar_nopfx = ar[2:] if ar.startswith("ال") else ar
        pos = lem.pos

        # All-three-root-chars-in-order helper (cheap).
        def root_chars_in_order(rb: str, target: str) -> bool:
            pos_idx = 0
            for ch in rb:
                pos_idx = target.find(ch, pos_idx)
                if pos_idx < 0:
                    return False
                pos_idx += 1
            return True

        # D1 — Form V (4 chars, ت prefix) / Form VI (5 chars, ت + root + ا + ...)
        if (
            len(bare) == 3
            and pos in (None, "verb")
            and ar_nopfx.startswith("ت")
            and (
                (len(ar_nopfx) == 4)
                or (len(ar_nopfx) == 5 and ar_nopfx[2] == "ا")
            )
            and root_chars_in_order(bare, ar_nopfx)
        ):
            out.append(ChimeraCandidate(
                lemma_id=lem.lemma_id, category="D1",
                lemma_ar=lem.lemma_ar or "", lemma_ar_bare=bare,
                gloss_en=lem.gloss_en or "",
                note=f"Form V/VI verb; bare='{bare}' should be stem (~'{ar_nopfx}')",
            ))
            continue

        # D2 — Form VII / VIII (5 chars, اِنْفَعَلَ / اِفْتَعَلَ)
        if (
            len(bare) == 3
            and pos in (None, "verb")
            and len(ar_nopfx) == 5
            and (
                ar_nopfx.startswith("ان")
                or (ar_nopfx.startswith("ا") and ar_nopfx[2] == "ت")
            )
            and root_chars_in_order(bare, ar_nopfx)
        ):
            out.append(ChimeraCandidate(
                lemma_id=lem.lemma_id, category="D2",
                lemma_ar=lem.lemma_ar or "", lemma_ar_bare=bare,
                gloss_en=lem.gloss_en or "",
                note=f"Form VII/VIII verb; bare='{bare}' should be stem (~'{ar_nopfx}')",
            ))
            continue

        # D3 — Form X (6 chars, اِسْتَفْعَلَ)
        if (
            len(bare) == 3
            and pos in (None, "verb")
            and ar_nopfx.startswith("است")
            and len(ar_nopfx) == 6
            and root_chars_in_order(bare, ar_nopfx)
        ):
            out.append(ChimeraCandidate(
                lemma_id=lem.lemma_id, category="D3",
                lemma_ar=lem.lemma_ar or "", lemma_ar_bare=bare,
                gloss_en=lem.gloss_en or "",
                note=f"Form X verb; bare='{bare}' should be stem (~'{ar_nopfx}')",
            ))
            continue

        # D4 — Defective ـٍ participle (only the active-participle shape; we
        # filter out regular nouns in genitive case by requiring len(bare)==3
        # to keep false-positive rate low; bigger words like سرور slip through
        # the heuristic).
        KASRATAN = "ٍ"
        if (
            (lem.lemma_ar or "").endswith(KASRATAN)
            and not bare.endswith("ي")
            and len(bare) == 3
            # Exclude proper names: those are inert anyway.
            and (lem.word_category or "") != "proper_name"
            and (lem.pos or "") != "noun_prop"
        ):
            out.append(ChimeraCandidate(
                lemma_id=lem.lemma_id, category="D4",
                lemma_ar=lem.lemma_ar or "", lemma_ar_bare=bare,
                gloss_en=lem.gloss_en or "",
                note=f"Defective participle; bare='{bare}' should end in ي",
            ))
            continue

        # D5 — forms_json field from a different root than bare
        forms = lem.forms_json if isinstance(lem.forms_json, dict) else {}
        bare_norm = bare
        for k, v in forms.items():
            if k in ("gender", "verb_form") or not isinstance(v, str):
                continue
            v_clean = _strip(v).strip()
            if not v_clean or " " in v_clean:
                continue
            v_nopfx = v_clean[2:] if v_clean.startswith("ال") and len(v_clean) > 4 else v_clean
            if (
                abs(len(v_nopfx) - len(bare_norm)) >= 5
                and bare_norm[:3] not in v_nopfx
            ):
                out.append(ChimeraCandidate(
                    lemma_id=lem.lemma_id, category="D5",
                    lemma_ar=lem.lemma_ar or "", lemma_ar_bare=bare,
                    gloss_en=lem.gloss_en or "",
                    note=f"forms_json[{k!r}]={v!r} root differs from bare",
                ))
                break

    return out


# ── D6: etymology_json that describes a different word than the gloss ──────
# Cheap deterministic pre-filter (no LLM) then a bounded LLM coherence confirm.
# The pre-filter excludes genuine loanwords (gloss "jacket" ↔ "From English
# 'jacket'") by an accent-aware overlap between gloss and derivation, so the
# LLM mostly sees true suspects (the #65 laptop/repentance case). Fills the gap
# left by bare_shape_check, which only inspects forms_json, never etymology_json.
#
# The pre-filter can't textually exclude every loanword (gloss "living room" ↔
# "From French 'salon'" share no string), so the *cron* path only LLM-checks
# lemmas created since the last run (id-floor checkpoint) — the inline coherence
# gate in lemma_enrichment is the real generation-time guard, and the one-time
# cleanup_etymology_mismatches.py sweep handled the historical backlog. A manual
# `chimera_audit.py --etymology` (since_lemma_id=0) re-scans everything.

_ETYM_CHECKPOINT_FILE = Path(__file__).resolve().parents[2] / "data" / "etym_coherence_checkpoint.json"

_ETYM_STOPWORDS = frozenset({
    "from", "the", "a", "an", "of", "to", "or", "and", "in", "into", "via",
    "by", "with", "for", "its", "this", "that", "which", "also", "as",
    "original", "originally", "meaning", "means", "word", "sense", "modern",
    "colloquial", "informal", "spoken", "likely", "probably", "reinforced",
    "english", "french", "latin", "greek", "italian", "spanish", "persian",
    "turkish", "arabic", "chinese", "aramaic", "portuguese", "german",
    "dutch", "nahuatl", "european", "universal", "nursery", "ancient",
    "loanword", "loanwords", "borrowed", "borrowing", "cognate", "cognates",
    "derived", "root", "pattern", "form", "verb", "noun", "adjective",
})


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", (text or "").lower())
        if not unicodedata.combining(c)
    )


def _etym_tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z]+", (text or "").lower())
        if len(t) > 2 and t not in _ETYM_STOPWORDS
    }


def _etym_gloss_matches_derivation(gloss: str, derivation: str) -> bool:
    """True if the etymology plausibly belongs to this gloss (genuine loanword).

    Accent-aware and bidirectional: a gloss content word appearing in the
    derivation (television ↔ télévision), or a quoted source word appearing in
    the gloss (cake' ↔ cakes). Cheap funnel only — the LLM is the arbiter for
    survivors.
    """
    deriv_norm = _strip_accents(derivation)
    gloss_norm = _strip_accents(gloss)
    if any(t in deriv_norm for t in _etym_tokens(gloss)):
        return True
    quoted = re.findall(r"'([^']+)'", deriv_norm)
    return any(len(q) > 2 and q in gloss_norm for q in quoted)


def _is_loanword_mode(etym: dict) -> bool:
    return (
        not etym.get("root_meaning")
        and not etym.get("pattern")
        and (etym.get("derivation") or "").strip().lower().startswith("from ")
    )


def find_etymology_incoherence_candidates(
    db: Session,
    limit: int = 30,
    llm_verify: bool = True,
    since_lemma_id: int = 0,
) -> list[ChimeraCandidate]:
    """Find lemmas whose etymology_json describes a different word than the gloss.

    Pre-filter (cheap): a loanword-mode etymology (no root_meaning / pattern,
    "From <lang>..." derivation) on a lemma that HAS an Arabic root, where the
    gloss does not match the derivation (accent-aware, bidirectional). That
    funnels out genuine loanwords and leaves true suspects (gloss "repentance"
    ↔ a "laptop" etymology — the #65 case).

    ``since_lemma_id`` restricts to lemmas created after that id (the cron passes
    the checkpoint floor so it only checks new arrivals). ``llm_verify`` (default)
    confirms suspects with a Haiku pass and returns only confirmed-incoherent
    ones; off returns the raw pre-filter suspects (unverified) for a cheap CLI run.
    """
    suspects: list[Lemma] = []
    rows = (
        db.query(Lemma)
        .filter(
            Lemma.canonical_lemma_id.is_(None),
            Lemma.root_id.isnot(None),
            Lemma.etymology_json.isnot(None),
            Lemma.lemma_id > since_lemma_id,
        )
        .order_by(Lemma.lemma_id)
        .all()
    )
    for lem in rows:
        etym = lem.etymology_json if isinstance(lem.etymology_json, dict) else None
        if not etym or not _is_loanword_mode(etym):
            continue
        if _etym_gloss_matches_derivation(lem.gloss_en or "", etym.get("derivation") or ""):
            continue  # source word matches the gloss → genuine loanword
        suspects.append(lem)
        if len(suspects) >= limit:
            break

    if not suspects:
        return []

    def _cand(lem: Lemma, note: str) -> ChimeraCandidate:
        return ChimeraCandidate(
            lemma_id=lem.lemma_id, category="D6",
            lemma_ar=lem.lemma_ar or "", lemma_ar_bare=lem.lemma_ar_bare or "",
            gloss_en=lem.gloss_en or "", note=note,
        )

    if not llm_verify:
        return [
            _cand(l, "etymology may not match gloss (unverified): "
                     f"{((l.etymology_json or {}).get('derivation') or '')[:80]}")
            for l in suspects
        ]

    from app.services.lemma_enrichment import verify_etymology_coherence_batch

    root_ids = {l.root_id for l in suspects if l.root_id}
    roots_by_id: dict = {}
    if root_ids:
        for r in db.query(Root).filter(Root.root_id.in_(root_ids)).all():
            roots_by_id[r.root_id] = r

    by_id = {l.lemma_id: l for l in suspects}
    out: list[ChimeraCandidate] = []
    for i in range(0, len(suspects), 10):
        chunk = [(l, l.etymology_json) for l in suspects[i:i + 10]]
        incoherent = verify_etymology_coherence_batch(chunk, roots_by_id)
        if not incoherent:  # None (LLM failure) or empty → nothing this chunk
            continue
        for lid in incoherent:
            lem = by_id.get(lid)
            if lem:
                out.append(_cand(
                    lem, "etymology describes a different word: "
                         f"{((lem.etymology_json or {}).get('derivation') or '')[:80]}"))
    return out


def emit_chimera_alert(
    db: Session,
    candidates: list[ChimeraCandidate],
    window_hours: int = 24,
) -> Optional[ActivityLog]:
    """Emit a ``chimera_audit_findings`` ActivityLog row if the candidate set
    differs from the most recent prior alert within ``window_hours``.

    Idempotent — same set of lemma_ids in the same window does not re-emit.
    Returns the new ActivityLog row, or None when suppressed.
    """
    if not candidates:
        return None
    lemma_ids = sorted({c.lemma_id for c in candidates})
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    existing = (
        db.query(ActivityLog)
        .filter(
            ActivityLog.event_type == "chimera_audit_findings",
            ActivityLog.created_at >= cutoff,
        )
        .order_by(ActivityLog.created_at.desc())
        .first()
    )
    if existing:
        prev = sorted((existing.detail_json or {}).get("lemma_ids") or [])
        if prev == lemma_ids:
            return None

    by_cat: dict[str, list[ChimeraCandidate]] = {}
    for c in candidates:
        by_cat.setdefault(c.category, []).append(c)
    cat_summary = ", ".join(f"{k}={len(v)}" for k, v in sorted(by_cat.items()))
    summary = (
        f"Chimera audit: {len(candidates)} candidate(s) "
        f"({cat_summary}); see detail for IDs"
    )
    return log_activity(
        db,
        event_type="chimera_audit_findings",
        summary=summary,
        detail={
            "lemma_ids": lemma_ids,
            "by_category": {
                cat: [
                    {
                        "lemma_id": c.lemma_id,
                        "lemma_ar": c.lemma_ar,
                        "lemma_ar_bare": c.lemma_ar_bare,
                        "gloss_en": c.gloss_en,
                        "note": c.note,
                    }
                    for c in items
                ]
                for cat, items in sorted(by_cat.items())
            },
            "window_hours": window_hours,
        },
    )


def _read_etym_checkpoint() -> int:
    """Highest lemma_id already D6-checked, persisted in a small JSON file."""
    try:
        return int(json.loads(_ETYM_CHECKPOINT_FILE.read_text()).get("max_lemma_id", 0))
    except Exception:
        return 0


def _write_etym_checkpoint(max_lemma_id: int) -> None:
    try:
        _ETYM_CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ETYM_CHECKPOINT_FILE.write_text(json.dumps({
            "max_lemma_id": max_lemma_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }))
    except Exception:
        logger.warning("Could not write etym coherence checkpoint", exc_info=True)


def check_and_alert(db: Session) -> list[ChimeraCandidate]:
    """Convenience wrapper for cron / warm-cache callers.

    Runs the structural D1..D5 scan plus the LLM-confirmed D6 etymology
    coherence check (set ``ALIF_ETYM_COHERENCE_AUDIT=0`` to skip). D6 only
    LLM-checks lemmas created since the last run (id-floor checkpoint), so the
    cron stays cheap — the inline generation-time gate is the real guard. D6 LLM
    calls run before any ActivityLog write, so no SQLite write lock is held.
    """
    candidates = find_chimera_candidates(db)
    if os.environ.get("ALIF_ETYM_COHERENCE_AUDIT", "1") == "1":
        try:
            floor = _read_etym_checkpoint()
            current_max = db.query(func.max(Lemma.lemma_id)).scalar() or 0
            if current_max > floor:
                candidates += find_etymology_incoherence_candidates(
                    db, limit=50, since_lemma_id=floor,
                )
                _write_etym_checkpoint(current_max)
        except Exception:
            logger.exception("Etymology coherence audit failed; continuing")
    if candidates:
        emit_chimera_alert(db, candidates)
    return candidates
