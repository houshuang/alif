#!/usr/bin/env python3
"""Audit and clean up Quran lemmas stored at inflected/conjugated surface forms.

Background
----------
Before the 2026-05-15 fix to `_create_unknown_quran_lemmas`, the Quran
lemmatization path created brand-new "canonical" lemmas from inflected
Quran surface forms whenever the dictionary form (the CAMeL lex) wasn't
already in the lemma table. The LLM prompt only asked for translation and
root, never for lemmatization, and `find_best_db_match`/`detect_variants`
both require the canonical to already exist before they can link a variant.

Concrete victim: نَزَّلْنَا "we sent down" (1st person plural perfect of نَزَّلَ)
was stored as its own lemma with conjugation cards, Form II label, and the
ن.ز.ل root — bypassing every "no inflected forms as lemmas" invariant.

This script audits all `source="quran"` lemmas where canonical_lemma_id IS
NULL, asks CAMeL Tools for each one's dictionary form, and classifies:

  LOOKS_CANONICAL — CAMeL lex matches the lemma's own bare → keep as-is.
  LINK_EXISTING   — CAMeL lex matches a DIFFERENT lemma already in DB →
                    mark current as variant of that canonical.
  PROMOTE_NEW     — CAMeL lex bare is not in DB → create a new canonical
                    from the CAMeL lex (LLM gloss) and mark current as variant.
  NO_CAMEL        — CAMeL has no analysis → skipped, logged for manual review.

Modes
-----
  --dry-run         Report only; no DB writes.
  --link-only       Apply LINK_EXISTING; skip PROMOTE_NEW.
  (default)         Apply LINK_EXISTING + PROMOTE_NEW.

  --limit N         Cap the audit to N lemmas (for testing).
  --verbose         Print per-lemma decisions.

Output
------
Writes a JSON report to backend/data/inflected_quran_audit.json with the
full classification breakdown for later review. Logs a `manual_action`
activity entry on apply runs.

Usage
-----
    python3 scripts/cleanup_inflected_quran_lemmas.py --dry-run
    python3 scripts/cleanup_inflected_quran_lemmas.py --link-only
    python3 scripts/cleanup_inflected_quran_lemmas.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models import Lemma, Root, UserLemmaKnowledge  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402
from app.services.lemma_quality import run_quality_gates  # noqa: E402
from app.services.morphology import (  # noqa: E402
    CAMEL_AVAILABLE,
    get_best_lemma_mle,
    is_valid_root,
)
from app.services.sentence_validator import (  # noqa: E402
    build_lemma_lookup,
    normalize_alef,
    strip_diacritics,
)
from app.services.variant_detection import mark_variants  # noqa: E402

REPORT_FILE = BACKEND_ROOT / "data" / "inflected_quran_audit.json"


def _classify(lemma: Lemma, lemma_lookup: dict[str, int]) -> dict[str, Any]:
    """Run CAMeL on a lemma and return classification info."""
    surface = lemma.lemma_ar or lemma.lemma_ar_bare or ""
    if not surface:
        return {"verdict": "NO_CAMEL", "reason": "empty surface"}

    mle = get_best_lemma_mle(surface) or get_best_lemma_mle(strip_diacritics(surface))
    if not mle or not mle.get("lex"):
        return {"verdict": "NO_CAMEL", "reason": "no MLE analysis"}

    lex = mle["lex"]
    lex_bare = normalize_alef(strip_diacritics(lex))
    own_bare = normalize_alef(lemma.lemma_ar_bare or "")

    if not lex_bare:
        return {"verdict": "NO_CAMEL", "reason": "empty lex bare"}

    info = {
        "lex_vocalized": lex,
        "lex_bare": lex_bare,
        "camel_root": mle.get("root"),
        "camel_pos": mle.get("pos"),
        "camel_enc0": mle.get("enc0", ""),
    }

    if lex_bare == own_bare:
        return {"verdict": "LOOKS_CANONICAL", **info}

    canonical_id = lemma_lookup.get(lex_bare)
    if canonical_id is not None and canonical_id != lemma.lemma_id:
        return {"verdict": "LINK_EXISTING", "canonical_id": canonical_id, **info}

    return {"verdict": "PROMOTE_NEW", **info}


def _llm_gloss_for_canonical(lex_vocalized: str, lex_bare: str,
                              inherit_gloss: str | None,
                              inherit_pos: str | None) -> dict[str, Any]:
    """Ask the LLM for a citation-form gloss for the canonical we're creating.

    We pass the inherited (inflected-form) gloss as context so the LLM can
    convert "we sent down" → "to send down" without re-translating from
    scratch.
    """
    from app.services.llm import generate_completion

    prompt = (
        "You are converting an Arabic inflected-form gloss into a clean "
        "citation-form gloss.\n\n"
        f"Dictionary (citation) form: {lex_vocalized} ({lex_bare})\n"
        f"Previous gloss for an inflected form: {inherit_gloss or '(none)'}\n"
        f"Previous pos: {inherit_pos or '(unknown)'}\n\n"
        "Return JSON with:\n"
        "- gloss_en: a clean citation-form gloss (1-3 words). For verbs use "
        "the infinitive ('to send down', 'to leave'); for nouns the "
        "indefinite singular; for adjectives the masculine singular.\n"
        "- pos: noun/verb/adj/adv/prep/particle/name\n"
        "- root: dotted consonantal root (e.g. ن.ز.ل) or null"
    )
    try:
        result = generate_completion(
            prompt,
            json_mode=True,
            task_type="quran_canonical_promote",
            model_override="claude_haiku",
        )
    except Exception as e:
        print(f"  LLM gloss generation failed: {e}", file=sys.stderr)
        return {}
    if not isinstance(result, dict):
        return {}
    return result


def _apply_link(db, lemma: Lemma, canonical_id: int, verbose: bool) -> None:
    """Mark a lemma as a variant of an existing canonical."""
    mark_variants(db, [(lemma.lemma_id, canonical_id, "inflected",
                        {"source": "cleanup_inflected_quran_lemmas"})])
    if verbose:
        canonical = db.get(Lemma, canonical_id)
        print(f"  LINK: {lemma.lemma_ar_bare} → {canonical.lemma_ar_bare}")


def _apply_promote(db, lemma: Lemma, info: dict, verbose: bool) -> int | None:
    """Create a new canonical lemma from CAMeL lex; mark current as variant.

    Returns the new canonical's lemma_id, or None on failure.
    """
    lex_vocalized = info["lex_vocalized"]
    lex_bare = info["lex_bare"]

    gloss_data = _llm_gloss_for_canonical(
        lex_vocalized, lex_bare,
        inherit_gloss=lemma.gloss_en,
        inherit_pos=lemma.pos,
    )
    gloss = (gloss_data.get("gloss_en") or "").strip()
    if not gloss:
        if verbose:
            print(f"  SKIP PROMOTE for {lemma.lemma_ar_bare}: no LLM gloss")
        return None

    pos = gloss_data.get("pos") or info.get("camel_pos") or lemma.pos or "noun"

    # Resolve root: prefer LLM, fall back to CAMeL, fall back to inherit
    root_str = gloss_data.get("root")
    if not root_str and info.get("camel_root"):
        cr = info["camel_root"]
        if isinstance(cr, str):
            root_str = ".".join(list(cr)) if "." not in cr and 1 < len(cr) <= 5 else cr

    root_id = None
    if root_str:
        import re as _re
        cleaned = _re.sub(r'[^؀-ۿ.]', '', root_str)
        if cleaned and is_valid_root(cleaned):
            root = db.query(Root).filter(Root.root == cleaned).first()
            if not root:
                root = Root(root=cleaned)
                db.add(root)
                db.flush()
            root_id = root.root_id
    if not root_id and lemma.root_id:
        root_id = lemma.root_id

    new_canonical = Lemma(
        lemma_ar=lex_vocalized,
        lemma_ar_bare=lex_bare,
        gloss_en=gloss,
        pos=pos,
        source="quran",
        root_id=root_id,
        word_category=None,
    )
    db.add(new_canonical)
    db.flush()

    # Encountered ULK so it can enter the standard pipeline
    if not db.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == new_canonical.lemma_id
    ).first():
        db.add(UserLemmaKnowledge(
            lemma_id=new_canonical.lemma_id,
            knowledge_state="encountered",
            source="quran",
            total_encounters=0,
        ))

    # Quality gates: enrichment, variant detection, stamp
    try:
        run_quality_gates(db, [new_canonical.lemma_id], background_enrich=False)
    except Exception as e:
        print(f"  Quality gates failed for new canonical {lex_bare}: {e}",
              file=sys.stderr)

    # Mark the old (inflected-form) lemma as a variant of the new canonical
    mark_variants(db, [(lemma.lemma_id, new_canonical.lemma_id, "inflected",
                        {"source": "cleanup_inflected_quran_lemmas",
                         "promoted_new_canonical": True})])

    if verbose:
        print(f"  PROMOTE: {lemma.lemma_ar_bare} → NEW {lex_bare} "
              f"(\"{gloss}\", #{new_canonical.lemma_id})")
    return new_canonical.lemma_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Report only; no DB writes.")
    parser.add_argument("--link-only", action="store_true",
                        help="Apply LINK_EXISTING only; skip PROMOTE_NEW.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap audit to N lemmas (testing).")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if not CAMEL_AVAILABLE:
        print("CAMeL Tools not available. Install with: pip install camel-tools",
              file=sys.stderr)
        sys.exit(1)

    db = SessionLocal()
    try:
        all_lemmas = db.query(Lemma).all()
        lemma_lookup = build_lemma_lookup(all_lemmas)

        quran_candidates = (
            db.query(Lemma)
            .filter(Lemma.source == "quran", Lemma.canonical_lemma_id.is_(None))
            .order_by(Lemma.lemma_id)
            .all()
        )
        if args.limit:
            quran_candidates = quran_candidates[: args.limit]

        print(f"Auditing {len(quran_candidates)} Quran canonical lemmas...")
        start = time.time()

        verdicts: Counter[str] = Counter()
        detailed: list[dict[str, Any]] = []

        for lemma in quran_candidates:
            classification = _classify(lemma, lemma_lookup)
            verdict = classification["verdict"]
            verdicts[verdict] += 1
            detailed.append({
                "lemma_id": lemma.lemma_id,
                "lemma_ar": lemma.lemma_ar,
                "lemma_ar_bare": lemma.lemma_ar_bare,
                "gloss_en": lemma.gloss_en,
                "pos": lemma.pos,
                **classification,
            })

            if args.verbose:
                print(f"#{lemma.lemma_id} {lemma.lemma_ar_bare} "
                      f"({lemma.gloss_en}) → {verdict}")

        elapsed = time.time() - start
        print(f"\nClassification complete in {elapsed:.1f}s:")
        for v, n in verdicts.most_common():
            print(f"  {v:18s} {n}")

        REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        REPORT_FILE.write_text(json.dumps({
            "ts": datetime.now().isoformat(),
            "total": len(quran_candidates),
            "verdicts": dict(verdicts),
            "items": detailed,
        }, ensure_ascii=False, indent=2))
        print(f"\nReport: {REPORT_FILE}")

        if args.dry_run:
            print("\nDry run — no DB writes.")
            return

        # Apply phase
        linked = 0
        promoted = 0
        for item in detailed:
            lemma = db.get(Lemma, item["lemma_id"])
            if lemma is None:
                continue

            if item["verdict"] == "LINK_EXISTING":
                _apply_link(db, lemma, item["canonical_id"], args.verbose)
                linked += 1
            elif item["verdict"] == "PROMOTE_NEW" and not args.link_only:
                new_id = _apply_promote(db, lemma, item, args.verbose)
                if new_id is not None:
                    promoted += 1
                    # Update local lookup so subsequent PROMOTE iterations
                    # see this new canonical and can convert to LINK instead.
                    lemma_lookup[item["lex_bare"]] = new_id

        db.commit()
        print(f"\nApplied: linked={linked} promoted={promoted}")

        log_activity(
            db,
            event_type="manual_action",
            summary=(
                f"Inflected-Quran-lemma cleanup: linked={linked} "
                f"promoted={promoted} (audited {len(quran_candidates)})"
            ),
            detail={
                "script": "cleanup_inflected_quran_lemmas",
                "verdicts": dict(verdicts),
                "linked": linked,
                "promoted": promoted,
                "link_only": args.link_only,
            },
        )
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    main()
