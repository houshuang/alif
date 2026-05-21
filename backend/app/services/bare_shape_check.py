"""Bare-shape consistency check.

Runs as Gate 1b of ``run_quality_gates`` to catch chimera lemmas at import
time, before they ship into the pipeline and burn LLM calls.

Three concrete shapes we detect (the chimeras audited 2026-05-20):

  Form V/VI/VII/VIII/X verb with 3-letter root bare
    lemma_ar='تَشَجَّعَ' bare='شجع'  →  expected bare='تشجع'
    The validator can't match the conjugated surface (يَتَشَجَّعُ) against the
    root bare because ي/ت conjugation prefixes aren't clitics. Auto-corrects
    the bare when ``lemma_ar`` undiacritized starts with one of the form
    patterns and contains the bare as a substring.

  Defective active participle without explicit ya
    lemma_ar='غَازٍ' bare='غاز'  →  expected bare='غازي'
    The dictionary form ends in kasratan on an implicit ya seat; real
    surface forms always carry the ya (الغازي, غازياً, غازية). Auto-corrects
    by appending ي, unless that collides with an existing lemma.

  Cross-root forms_json value
    bare='توب' forms_json={"plural": "لابتوبات"}
    Length diff >5 with no shared root letters → almost certainly a
    chimera (the #65 laptop/repentance case). We log a warning here but
    do NOT auto-correct — the right fix is usually to rewrite the gloss
    or split into two lemmas, both of which need human judgment.

Auto-corrections emit ``import_chimera_warning`` ActivityLog rows so each
intervention is auditable.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Lemma

logger = logging.getLogger(__name__)

_DIACRITICS = re.compile(r"[ً-ْٰـ]")


def _strip_diacritics(text: str) -> str:
    return _DIACRITICS.sub("", text or "")


# Form V/VI/VII/VIII/X verb prefix patterns (undiacritized).
# After stripping diacritics and an optional ال prefix, lemma_ar for a
# derived form starts with these:
_FORM_V_PREFIX = "ت"          # تَفَعَّلَ → تشجع (4 chars)
_FORM_VII_PREFIX = "ان"       # اِنْفَعَلَ → انكسر (5 chars)
_FORM_X_PREFIX = "است"        # اِسْتَفْعَلَ → استعمل (6 chars)
# Form VI and VIII don't have fixed prefixes (variable root char between
# fixed positions) and are detected by structural shape inline below.


def _derive_expected_bare_for_verb(ar_undiac: str, root_bare: str) -> Optional[str]:
    """If ar_undiac is a derived verb form whose stem differs from the root,
    return the expected stem bare. Otherwise None.

    The check is conservative: it only fires when ar_undiac DEFINITELY looks
    like a derived form (specific prefix shapes + root chars present in order)
    and the root_bare is exactly 3 chars. The function returns None on any
    ambiguity — the caller treats None as "no correction needed".
    """
    if len(root_bare) != 3:
        return None
    # Need all 3 root chars present in the same order.
    pos = 0
    for ch in root_bare:
        pos = ar_undiac.find(ch, pos)
        if pos < 0:
            return None
        pos += 1

    # Form X: اِسْتَفْعَلَ undiacritized = استفعل (6 chars)
    if ar_undiac.startswith(_FORM_X_PREFIX) and len(ar_undiac) == 6:
        return ar_undiac  # bare matches stem 1:1

    # Form VII: انـ. اِنْفَعَلَ → انكسر (5)
    if ar_undiac.startswith(_FORM_VII_PREFIX) and len(ar_undiac) == 5:
        return ar_undiac

    # Form VIII: اـتـ. اِفْتَعَلَ → اجتمع (5, ت at index 2)
    if (
        len(ar_undiac) == 5
        and ar_undiac.startswith("ا")
        and ar_undiac[2] == "ت"
    ):
        return ar_undiac

    # Form VI: تَفَاعَلَ → تشاجع (5 chars, "ا" sits between root[0] and root[1]
    # at index 2). Not detectable by a fixed prefix because the prefix is
    # ت + variable root letter.
    if (
        len(ar_undiac) == 5
        and ar_undiac.startswith("ت")
        and ar_undiac[2] == "ا"
    ):
        return ar_undiac

    # Form V: تَفَعَّلَ → تشجع (4)
    if (
        ar_undiac.startswith(_FORM_V_PREFIX)
        and len(ar_undiac) == 4
    ):
        return ar_undiac

    return None


def _is_defective_participle_missing_ya(lemma_ar: str, bare: str) -> bool:
    """True when lemma_ar ends in kasratan (implicit ya seat) but bare doesn't
    carry the explicit ya. Caller decides whether to append ya.
    """
    KASRATAN = "ٍ"
    if not (lemma_ar or "").endswith(KASRATAN):
        return False
    if bare.endswith("ي"):
        return False  # already has explicit ya
    if len(bare) < 3:
        return False  # too short to confidently identify the pattern
    return True


def _forms_json_cross_root_warnings(
    bare: str, forms_json: Optional[dict]
) -> list[str]:
    """Return warning strings for forms_json values that look like a different
    root than the lemma's bare. Pure detection — no auto-correction.

    Triggers when a form value (after diacritic strip + ال strip) differs from
    the bare by >=5 chars AND the bare's first 3 chars don't appear as a
    substring of the form value.
    """
    if not forms_json or not isinstance(forms_json, dict) or not bare:
        return []
    warnings = []
    bare_strip = _strip_diacritics(bare).strip()
    if len(bare_strip) < 3:
        return []
    for key, val in forms_json.items():
        if key in ("gender", "verb_form") or not isinstance(val, str):
            continue
        if " " in val:
            continue  # multi-word periphrastic values (e.g. أَكْثَرُ X) are noisy
        v = _strip_diacritics(val).strip()
        v_nopfx = v[2:] if v.startswith("ال") and len(v) > 4 else v
        if not v_nopfx:
            continue
        if (
            abs(len(v_nopfx) - len(bare_strip)) >= 5
            and bare_strip[:3] not in v_nopfx
        ):
            warnings.append(
                f"forms_json[{key!r}]={val!r} differs from bare={bare!r} "
                "by >5 chars with no root overlap"
            )
    return warnings


@dataclass
class ShapeCheckResult:
    lemma_id: int
    auto_corrected: bool = False
    new_bare: Optional[str] = None
    new_lemma_ar: Optional[str] = None
    warnings: list[str] = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


def check_and_correct_bare_shape(
    db: Session,
    lemma_ids: list[int],
) -> list[ShapeCheckResult]:
    """Run shape consistency on each lemma_id, auto-correcting Form-V-style
    root-bare and defective-participle-missing-ya patterns. Returns one result
    per lemma that had any finding.

    Collision-safe: if the expected new bare is already in use by a different
    non-variant lemma, we log a warning but do NOT auto-correct (the caller
    will see ``auto_corrected=False`` with a warning string).
    """
    if not lemma_ids:
        return []

    results: list[ShapeCheckResult] = []
    lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all()

    for lem in lemmas:
        result = ShapeCheckResult(lemma_id=lem.lemma_id)
        bare = (lem.lemma_ar_bare or "").strip()
        if not bare or lem.canonical_lemma_id is not None:
            continue
        ar = lem.lemma_ar or ""
        ar_undiac = _strip_diacritics(ar)
        ar_nopfx = ar_undiac[2:] if ar_undiac.startswith("ال") else ar_undiac

        # ── Verb stem-vs-root ──────────────────────────────────────────────
        if lem.pos in (None, "verb"):
            expected = _derive_expected_bare_for_verb(ar_nopfx, bare)
            if expected and expected != bare:
                collision = (
                    db.query(Lemma.lemma_id)
                    .filter(
                        Lemma.lemma_ar_bare == expected,
                        Lemma.lemma_id != lem.lemma_id,
                        Lemma.canonical_lemma_id.is_(None),
                    )
                    .first()
                )
                if collision:
                    result.warnings.append(
                        f"verb stem bare={expected!r} collides with #{collision[0]} "
                        "— skipping auto-correction"
                    )
                else:
                    lem.lemma_ar_bare = expected
                    result.auto_corrected = True
                    result.new_bare = expected

        # ── Defective participle ya-append ─────────────────────────────────
        if not result.auto_corrected and _is_defective_participle_missing_ya(ar, bare):
            expected = bare + "ي"
            collision = (
                db.query(Lemma.lemma_id)
                .filter(
                    Lemma.lemma_ar_bare == expected,
                    Lemma.lemma_id != lem.lemma_id,
                    Lemma.canonical_lemma_id.is_(None),
                )
                .first()
            )
            if collision:
                result.warnings.append(
                    f"defective ya-append bare={expected!r} collides with "
                    f"#{collision[0]} — skipping (homograph; merge candidate)"
                )
            else:
                lem.lemma_ar_bare = expected
                result.auto_corrected = True
                result.new_bare = expected

        # ── forms_json cross-root (warning only) ──────────────────────────
        cross_root_warns = _forms_json_cross_root_warnings(bare, lem.forms_json)
        result.warnings.extend(cross_root_warns)

        if result.auto_corrected or result.warnings:
            results.append(result)

    return results
