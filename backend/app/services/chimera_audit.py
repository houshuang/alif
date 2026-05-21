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

This module is the importable engine. The companion CLI runner is
``scripts/chimera_audit.py``. It is also called from ``warm_sentence_cache``
phase 7 once per cron pass; emits a ``chimera_audit_findings`` ActivityLog
row only when the candidate set has changed since the previous run.

Pure read — no DB writes from this module other than the idempotent
ActivityLog emission in ``emit_chimera_alert``.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models import ActivityLog, Lemma
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


def check_and_alert(db: Session) -> list[ChimeraCandidate]:
    """Convenience wrapper for cron / warm-cache callers."""
    candidates = find_chimera_candidates(db)
    if candidates:
        emit_chimera_alert(db, candidates)
    return candidates
