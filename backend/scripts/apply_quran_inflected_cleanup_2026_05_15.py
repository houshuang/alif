#!/usr/bin/env python3
"""One-off apply: cleanup_inflected_quran_lemmas.py dry-run → curated decisions.

Audit ran 2026-05-15 on prod, found 71 source="quran" canonicals, of which
26 needed action (5 LINK_EXISTING, 21 PROMOTE_NEW). Spot-check of the 21
PROMOTE_NEW revealed CAMeL's MLE picked the wrong lex for ~7 of them
(noun for verb, wrong root, Quranic-typography artifacts). This script:

  - Applies the 5 LINK_EXISTING (link variant → existing canonical in DB)
  - Applies 14 trustworthy PROMOTE_NEW (CAMeL lex matches gloss/pos)
  - Applies 6 manually-overridden PROMOTE_NEW with corrected lex
  - SUSPENDs 1 compound-particle row (#2872 يايها) — can't be cleanly
    linked since it's a 2-word compound يا + أيُّها

Each action includes the merge_variant data migration so sentence_words,
review_logs, sentence target_lemma_ids, and ULK state move onto the
canonical — leaving the variant row inert.

Usage:
    python3 scripts/apply_quran_inflected_cleanup_2026_05_15.py --dry-run
    python3 scripts/apply_quran_inflected_cleanup_2026_05_15.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models import Lemma, Root, UserLemmaKnowledge  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402
from app.services.lemma_quality import run_quality_gates  # noqa: E402
from app.services.morphology import is_valid_root  # noqa: E402
from app.services.sentence_validator import (  # noqa: E402
    build_lemma_lookup,
    normalize_alef,
    strip_diacritics,
)
from app.services.variant_detection import mark_variants  # noqa: E402
from scripts.cleanup_lemma_variants import merge_variant  # noqa: E402

AUDIT_REPORT = BACKEND_ROOT / "data" / "inflected_quran_audit.json"

# ---------- 2 LINK_EXISTING after the 2026-05-15 audit re-run with direct
# bare-form SQL lookup (build_lemma_lookup's generated forms had previously
# produced bogus links into other inflected rows / proper names).
LINK_EXISTING: list[tuple[int, int, str]] = [
    (2879, None, "مثله → مِثْل #3083 (noun, scaffold)"),
    (2904, None, "يفسد → أَفْسَد #3353 (verb, audit_2026-05-06)"),
]

# ---------- 17 trustworthy PROMOTE_NEW (CAMeL's lex from audit JSON).
# Three IDs (2828, 2856, 2884) moved from LINK_EXISTING after the re-audit
# correctly rejected their previous (bogus) link targets.
TRUSTED_PROMOTE_IDS: set[int] = {
    2825, 2828, 2853, 2855, 2856, 2857, 2859, 2867, 2868, 2869,
    2878, 2884, 2886, 2888, 2889, 2891, 2896,
}

# ---------- 6 manual overrides where CAMeL's lex was wrong
# (orphan_id, lex_vocalized, lex_bare, gloss_en, pos, root_dotted, note)
MANUAL_OVERRIDES: list[tuple[int, str, str, str, str, str, str]] = [
    (2818, "هَدَى",   "هدى", "to guide",            "verb", "ه.د.ي", "CAMeL picked verbal noun هَدْي"),
    (2849, "خَلَا",   "خلا", "to be alone, pass",   "verb", "خ.ل.و", "CAMeL picked noun خِلْو"),
    (2863, "ظُلْمَة", "ظلمة", "darkness",            "noun", "ظ.ل.م", "CAMeL picked verb ظَلَم"),
    (2883, "حَجَر",   "حجر", "stone",               "noun", "ح.ج.ر", "CAMeL picked agent noun حَجّار"),
    (2892, "نَقَض",   "نقض", "to break, undo",      "verb", "ن.ق.ض", "CAMeL picked ٱنْقَضَى (different root)"),
    (2908, "نَبَّأ",  "نبأ", "to inform",           "verb", "ن.ب.أ", "CAMeL picked Quranic typography artifact"),
]

# ---------- Suspend: compound particle that can't be cleanly canonicalized
SUSPEND_IDS: list[tuple[int, str]] = [
    (2872, "يايها — Quranic vocative compound يا + أيُّها; no single canonical"),
]


def _resolve_root_id(db, root_str: str | None) -> int | None:
    if not root_str:
        return None
    import re as _re
    cleaned = _re.sub(r'[^؀-ۿ.]', '', root_str)
    if not cleaned or not is_valid_root(cleaned):
        return None
    root = db.query(Root).filter(Root.root == cleaned).first()
    if not root:
        root = Root(root=cleaned)
        db.add(root)
        db.flush()
    return root.root_id


def _find_or_create_canonical(
    db,
    lemma_lookup: dict[str, int],
    lex_vocalized: str,
    lex_bare: str,
    gloss_en: str,
    pos: str,
    root_str: str | None,
    dry_run: bool,
    variant_id: int | None = None,
) -> tuple[int, bool]:
    """Return (canonical_lemma_id, created_new). Direct-bare SQL lookup against
    non-variant rows, POS-filtered, never reusing the variant itself. If no
    suitable canonical exists, create one."""
    bare_norm = normalize_alef(lex_bare)
    candidates = (
        db.query(Lemma)
        .filter(
            Lemma.lemma_ar_bare == bare_norm,
            Lemma.canonical_lemma_id.is_(None),
        )
        .all()
    )
    cp = (pos or "").lower()
    def _ok(c):
        if c.lemma_id == variant_id:
            return False
        if c.word_category == "proper_name" or (c.pos or "").lower() == "noun_prop":
            return False
        cpos = (c.pos or "").lower()
        if cp.startswith("verb"):
            return cpos.startswith("verb")
        if cp.startswith("noun") or cp.startswith("adj"):
            return cpos.startswith("noun") or cpos.startswith("adj")
        return cpos == cp
    valid = [c for c in candidates if _ok(c)]
    if valid:
        valid.sort(key=lambda c: (c.source == "quran", c.lemma_id))
        return valid[0].lemma_id, False

    if dry_run:
        print(f"  [dry] would CREATE canonical: {lex_vocalized} ({lex_bare}) - {gloss_en}")
        return -1, True

    new_canonical = Lemma(
        lemma_ar=lex_vocalized,
        lemma_ar_bare=lex_bare,
        gloss_en=gloss_en,
        pos=pos,
        source="quran",
        root_id=_resolve_root_id(db, root_str),
        word_category=None,
    )
    db.add(new_canonical)
    db.flush()

    if not db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == new_canonical.lemma_id
    ).first():
        db.add(UserLemmaKnowledge(
            lemma_id=new_canonical.lemma_id,
            knowledge_state="encountered",
            source="quran",
            total_encounters=0,
        ))
    try:
        run_quality_gates(db, [new_canonical.lemma_id], background_enrich=False)
    except Exception as e:
        print(f"  quality_gates warning for {lex_bare}: {e}", file=sys.stderr)

    lemma_lookup[normalize_alef(lex_bare)] = new_canonical.lemma_id
    return new_canonical.lemma_id, True


def _apply_action(db, lemma_lookup, audit_index, variant_id, canonical_id, note,
                  dry_run, source_label, form_key="inflected"):
    variant = db.get(Lemma, variant_id)
    canonical = db.get(Lemma, canonical_id)
    if not variant or not canonical:
        print(f"  SKIP #{variant_id}: missing variant or canonical", file=sys.stderr)
        return False
    if variant.canonical_lemma_id is not None:
        print(f"  SKIP #{variant_id}: already linked to #{variant.canonical_lemma_id}")
        return False

    print(f"  {source_label} #{variant_id} {variant.lemma_ar_bare} → "
          f"#{canonical_id} {canonical.lemma_ar_bare} ({note})")

    if dry_run:
        merge_variant(db, variant_id, canonical_id, form_key, dry_run=True)
        return True

    mark_variants(db, [(variant_id, canonical_id, form_key,
                        {"source": "apply_quran_inflected_cleanup_2026_05_15"})])
    merge_variant(db, variant_id, canonical_id, form_key, dry_run=False)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not AUDIT_REPORT.exists():
        print(f"ERROR: audit report not found at {AUDIT_REPORT}", file=sys.stderr)
        print("Run cleanup_inflected_quran_lemmas.py --dry-run first.", file=sys.stderr)
        sys.exit(1)

    audit = json.loads(AUDIT_REPORT.read_text())
    audit_index = {item["lemma_id"]: item for item in audit["items"]}

    db = SessionLocal()
    try:
        lemma_lookup = build_lemma_lookup(db.query(Lemma).all())

        applied = {"link_existing": 0, "promote_trusted": 0, "manual": 0, "suspended": 0}

        # 1. LINK_EXISTING — read canonical_id from the audit JSON
        print("\n=== Phase 1: LINK_EXISTING (5) ===")
        for variant_id, _, note in LINK_EXISTING:
            entry = audit_index.get(variant_id)
            if not entry:
                print(f"  SKIP #{variant_id}: not in audit JSON", file=sys.stderr)
                continue
            canonical_id = entry.get("canonical_id")
            if not canonical_id:
                print(f"  SKIP #{variant_id}: no canonical_id in audit", file=sys.stderr)
                continue
            if _apply_action(db, lemma_lookup, audit_index, variant_id, canonical_id,
                             note, args.dry_run, "LINK"):
                applied["link_existing"] += 1
            if not args.dry_run:
                db.commit()

        # 2. TRUSTED PROMOTE_NEW — use CAMeL's lex from audit
        print(f"\n=== Phase 2: PROMOTE_NEW trusted ({len(TRUSTED_PROMOTE_IDS)}) ===")
        for variant_id in sorted(TRUSTED_PROMOTE_IDS):
            entry = audit_index.get(variant_id)
            if not entry:
                print(f"  SKIP #{variant_id}: not in audit", file=sys.stderr)
                continue
            variant = db.get(Lemma, variant_id)
            if not variant:
                continue
            lex_vocalized = entry["lex_vocalized"]
            lex_bare = entry["lex_bare"]
            gloss = (variant.gloss_en or "").strip() or "(no gloss)"
            pos = entry.get("camel_pos") or variant.pos or "noun"
            camel_root = entry.get("camel_root")
            if isinstance(camel_root, str) and "." not in camel_root and 1 < len(camel_root) <= 5:
                camel_root = ".".join(list(camel_root))

            canonical_id, created = _find_or_create_canonical(
                db, lemma_lookup, lex_vocalized, lex_bare,
                gloss_en=gloss, pos=pos, root_str=camel_root, dry_run=args.dry_run,
                variant_id=variant_id,
            )
            if canonical_id < 0:
                continue
            if canonical_id == variant_id:
                print(f"  SKIP #{variant_id}: would link to self", file=sys.stderr)
                continue
            note = f"CAMeL lex (created={created})"
            if _apply_action(db, lemma_lookup, audit_index, variant_id, canonical_id,
                             note, args.dry_run, "PROMOTE"):
                applied["promote_trusted"] += 1
            if not args.dry_run:
                db.commit()

        # 3. MANUAL_OVERRIDES — corrected lex
        print(f"\n=== Phase 3: MANUAL_OVERRIDES ({len(MANUAL_OVERRIDES)}) ===")
        for variant_id, lex_voc, lex_bare, gloss, pos, root_str, note in MANUAL_OVERRIDES:
            canonical_id, created = _find_or_create_canonical(
                db, lemma_lookup, lex_voc, lex_bare,
                gloss_en=gloss, pos=pos, root_str=root_str, dry_run=args.dry_run,
                variant_id=variant_id,
            )
            if canonical_id < 0:
                continue
            if canonical_id == variant_id:
                print(f"  SKIP #{variant_id}: would link to self", file=sys.stderr)
                continue
            full_note = f"{note} → use {lex_voc} (created={created})"
            if _apply_action(db, lemma_lookup, audit_index, variant_id, canonical_id,
                             full_note, args.dry_run, "MANUAL"):
                applied["manual"] += 1
            if not args.dry_run:
                db.commit()

        # 4. SUSPEND — compound particles with no single canonical
        print(f"\n=== Phase 4: SUSPEND ({len(SUSPEND_IDS)}) ===")
        for variant_id, note in SUSPEND_IDS:
            ulk = (db.query(UserLemmaKnowledge)
                   .filter(UserLemmaKnowledge.lemma_id == variant_id).first())
            print(f"  SUSPEND #{variant_id}: {note}")
            if args.dry_run:
                continue
            if ulk:
                ulk.knowledge_state = "suspended"
                ulk.experiment_group = None
                ulk.experiment_intro_shown_at = None
            applied["suspended"] += 1
            db.commit()

        # Summary
        print(f"\nApplied: {applied}")

        if not args.dry_run:
            log_activity(
                db,
                event_type="manual_action",
                summary=(
                    "Quran inflected-lemma curated cleanup: "
                    f"link_existing={applied['link_existing']} "
                    f"promote_trusted={applied['promote_trusted']} "
                    f"manual={applied['manual']} "
                    f"suspended={applied['suspended']}"
                ),
                detail={
                    "script": "apply_quran_inflected_cleanup_2026_05_15",
                    **applied,
                    "trusted_ids": sorted(TRUSTED_PROMOTE_IDS),
                    "manual_overrides": [
                        {"id": v[0], "lex": v[1], "gloss": v[3], "note": v[6]}
                        for v in MANUAL_OVERRIDES
                    ],
                    "suspend_ids": [v[0] for v in SUSPEND_IDS],
                },
            )
            db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
