"""Phase 8 of root-showcase: link textbook_scan inflected verb forms to canonical bases.

Background. The textbook_scan OCR import created standalone canonical lemmas
for verb conjugations it encountered on the page (e.g. يَكْتُبُونَ as lemma
#1558, أَكْتُبُ as #1682). When the Form-I canonical (e.g. كَتَبَ) wasn't
already in the DB, no canonical was created either — the inflected form just
sat there with canonical_lemma_id=NULL, masquerading as a canonical itself.

This breaks the mapping-verification pipeline: the verifier knows MSA
morphology, says "the correct lemma should be كَتَبَ," but كَتَبَ isn't in
the DB → apply_corrections returns same_lemma → sentence rejected. Pre-Phase 7
this killed ~80% of root-showcase yield. Phase 7 narrowly bypasses for
showcase palette positions, but the underlying data shape still degrades
regular sentence generation across the system.

This script:
  1. Identifies textbook_scan canonical verbs with imperfect/conjugated shape
  2. For each, uses Claude to classify: is it really an inflection? what's
     the canonical lemma_ar + wazn?
  3. If canonical exists in DB → set canonical_lemma_id (variant link)
  4. If canonical missing → create new Lemma + run_quality_gates, then link

Conservative scope: does NOT touch ULK rows, SentenceWord.lemma_id, ReviewLog,
or Sentence.target_lemma_id. The existing variant chain in canonical_resolution
handles credit flow automatically. If overshadowed-variant cleanup is needed
after this, run scripts/suspend_variant_ulks.py separately.

Dry-run by default. Use --apply to commit. Backup DB before --apply.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models import Lemma, Root
from app.services.activity_log import log_activity
from app.services.lemma_quality import run_quality_gates
from app.services.llm import generate_completion
from app.services.sentence_validator import (
    build_comprehensive_lemma_lookup,
    resolve_existing_lemma,
    strip_diacritics,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("cleanup_textbook_inflected_verbs")


IMPERFECT_PREFIX = re.compile(r"^[يتنأ]")
CONJUG_SUFFIX = re.compile(r"(وا|ون|ين|تم|تن|نا|ت|ن)$")


CLASSIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "classifications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lemma_id": {"type": "integer"},
                    "is_inflected": {"type": "boolean"},
                    "canonical_lemma_ar": {"type": "string"},
                    "canonical_lemma_ar_bare": {"type": "string"},
                    "canonical_wazn": {"type": "string"},
                    "canonical_gloss_en": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": [
                    "lemma_id", "is_inflected", "canonical_lemma_ar",
                    "canonical_lemma_ar_bare", "canonical_wazn",
                    "canonical_gloss_en", "reason",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["classifications"],
    "additionalProperties": False,
}


SYSTEM_PROMPT = """\
You are an Arabic morphology expert. For each Arabic verb-tagged lemma below, \
determine: is it an INFLECTED FORM (conjugation) of some canonical base verb, \
or is it itself a canonical dictionary lemma?

For inflected forms, the canonical is conventionally the past-tense 3rd-person \
masculine singular (e.g. كَتَبَ for يَكْتُبُونَ). For Form II-X verbs the \
canonical is the corresponding past 3sg masc form (e.g. تَعَلَّمَ, اِسْتَخْدَمَ, etc).

Mark is_inflected=true ONLY when the input is clearly a conjugation/inflection, \
not a canonical form. Examples:
  - يَكْتُبُونَ (they write, imperfect) → is_inflected=true, canonical=كَتَبَ
  - أَكْتُبُ (I write, imperfect) → is_inflected=true, canonical=كَتَبَ
  - نَدْرُسُ (we study) → is_inflected=true, canonical=دَرَسَ
  - كَاتَبَ (Form III past, canonical) → is_inflected=false
  - أَنْفَقَ (Form IV past, canonical) → is_inflected=false
  - نَاسَبَ (Form III "to suit", canonical) → is_inflected=false
  - نَمِر (tiger, mistagged as verb) → is_inflected=false (it's a noun)

For each lemma marked is_inflected=true, also provide:
  - canonical_lemma_ar: fully diacritized past 3sg masc (or appropriate canonical)
  - canonical_lemma_ar_bare: undiacritized version, no clitics, no ال prefix
  - canonical_wazn: form_1, form_2, form_3, form_4, form_5, form_8, form_10, etc.
  - canonical_gloss_en: short English meaning of the canonical (2-6 words)

For is_inflected=false, leave canonical_* fields as empty strings.

Always include a short reason explaining your classification."""


def find_candidate_verbs(db) -> list[Lemma]:
    """Find textbook_scan verbs with inflected-looking surface forms."""
    verbs = (
        db.query(Lemma)
        .filter(Lemma.source == "textbook_scan")
        .filter(Lemma.canonical_lemma_id.is_(None))
        .filter(Lemma.pos == "verb")
        .all()
    )
    candidates = []
    for l in verbs:
        bare = (l.lemma_ar_bare or "").strip()
        if not bare:
            continue
        if IMPERFECT_PREFIX.match(bare) or CONJUG_SUFFIX.search(bare):
            candidates.append(l)
    return candidates


def classify_batch(lemmas: list[Lemma]) -> list[dict[str, Any]]:
    """Send a batch to Claude for classification."""
    lemma_lines = []
    for l in lemmas:
        lemma_lines.append(
            f"  - lemma_id={l.lemma_id}, ar={l.lemma_ar}, bare={l.lemma_ar_bare}, "
            f"gloss=\"{l.gloss_en or ''}\""
        )
    prompt = f"""Classify each Arabic verb lemma below as either inflected (a \
conjugation) or canonical (a dictionary form). For inflected ones, provide \
the canonical lemma.

LEMMAS:
{chr(10).join(lemma_lines)}

Return a classification for EVERY lemma_id above (do not skip any)."""

    try:
        result = generate_completion(
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            json_schema=CLASSIFY_SCHEMA,
            model_override="claude_haiku",
            task_type="textbook_inflected_classify",
            temperature=0.2,
            timeout=120,
        )
    except Exception as e:
        logger.warning(f"LLM classify failed: {e}")
        return []
    if not isinstance(result, dict):
        return []
    classifications = result.get("classifications", [])
    return classifications if isinstance(classifications, list) else []


def create_canonical_lemma(
    db, *, canonical_lemma_ar: str, canonical_lemma_ar_bare: str,
    canonical_wazn: str, canonical_gloss_en: str, root_id: int,
) -> int:
    """Create a new canonical Lemma + run quality gates. Returns lemma_id."""
    lemma = Lemma(
        lemma_ar=canonical_lemma_ar,
        lemma_ar_bare=canonical_lemma_ar_bare,
        root_id=root_id,
        pos="verb",
        gloss_en=canonical_gloss_en,
        wazn=canonical_wazn or None,
        source="textbook_inflected_cleanup",
    )
    db.add(lemma)
    db.flush()
    new_id = lemma.lemma_id
    # Quality gates: enrichment, variant detection, gates_completed_at stamp
    run_quality_gates(
        db, [new_id],
        skip_variants=True,  # we're linking variants manually below
        enrich=True,
        background_enrich=False,
    )
    db.commit()
    return new_id


def link_as_variant(db, inflected_id: int, canonical_id: int) -> None:
    """Set canonical_lemma_id on inflected lemma. Per-row commit (lock discipline)."""
    inflected = db.query(Lemma).filter(Lemma.lemma_id == inflected_id).first()
    if not inflected:
        return
    inflected.canonical_lemma_id = canonical_id
    db.commit()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Default: classify + report, no writes.")
    ap.add_argument("--apply", action="store_true", help="Create canonicals + link variants.")
    ap.add_argument("--limit", type=int, default=None, help="Process at most N candidates.")
    ap.add_argument("--lemma-id", type=int, default=None, help="Process a single specific lemma_id.")
    ap.add_argument("--batch-size", type=int, default=10, help="Lemmas per LLM classify call.")
    ap.add_argument("--output", default=None, help="Report JSON path (default: research/textbook-inflected-cleanup-<date>.json)")
    args = ap.parse_args()

    if not args.apply and not args.dry_run:
        args.dry_run = True
    if args.apply and args.dry_run:
        logger.error("Pick one of --dry-run or --apply, not both")
        sys.exit(1)

    today = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    repo_root = Path(__file__).resolve().parents[2]
    output_path = Path(args.output) if args.output else repo_root / "research" / f"textbook-inflected-cleanup-{today}.json"

    db = SessionLocal()
    try:
        if args.lemma_id:
            l = db.query(Lemma).filter(Lemma.lemma_id == args.lemma_id).first()
            if not l:
                logger.error(f"Lemma #{args.lemma_id} not found")
                sys.exit(1)
            candidates = [l]
        else:
            candidates = find_candidate_verbs(db)
            if args.limit:
                candidates = candidates[: args.limit]
        logger.info(f"Found {len(candidates)} candidate inflected textbook_scan verbs")
        logger.info(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    finally:
        db.close()

    # Classify in batches
    all_classifications: list[dict[str, Any]] = []
    for i in range(0, len(candidates), args.batch_size):
        batch = candidates[i : i + args.batch_size]
        logger.info(f"Classifying batch {i // args.batch_size + 1} ({len(batch)} lemmas)")
        classifications = classify_batch(batch)
        all_classifications.extend(classifications)

    by_id = {c["lemma_id"]: c for c in all_classifications}

    # Resolution phase
    actions: list[dict[str, Any]] = []
    created_canonical_ids: list[int] = []
    linked_inflected_ids: list[int] = []
    skipped_not_inflected: list[int] = []
    skipped_no_classification: list[int] = []
    failed: list[dict[str, Any]] = []

    db = SessionLocal()
    try:
        lemma_lookup = build_comprehensive_lemma_lookup(db)
    finally:
        db.close()

    for cand in candidates:
        c = by_id.get(cand.lemma_id)
        if not c:
            skipped_no_classification.append(cand.lemma_id)
            logger.info(f"  #{cand.lemma_id} {cand.lemma_ar}: no classification — skipping")
            continue
        if not c.get("is_inflected"):
            skipped_not_inflected.append(cand.lemma_id)
            logger.info(f"  #{cand.lemma_id} {cand.lemma_ar}: not inflected ({c.get('reason', '')[:50]}) — skipping")
            continue

        canonical_bare = (c.get("canonical_lemma_ar_bare") or "").strip()
        canonical_ar = (c.get("canonical_lemma_ar") or "").strip()
        if not canonical_bare or not canonical_ar:
            failed.append({"lemma_id": cand.lemma_id, "reason": "missing canonical fields"})
            logger.warning(f"  #{cand.lemma_id} {cand.lemma_ar}: missing canonical info — skipping")
            continue

        # Look up canonical in DB
        existing_canon_id = resolve_existing_lemma(canonical_bare, lemma_lookup)
        action = {
            "lemma_id": cand.lemma_id,
            "lemma_ar": cand.lemma_ar,
            "canonical_lemma_ar": canonical_ar,
            "canonical_wazn": c.get("canonical_wazn"),
            "reason": c.get("reason", "")[:200],
        }

        if existing_canon_id is not None and existing_canon_id != cand.lemma_id:
            action["action"] = "link_existing"
            action["canonical_lemma_id"] = existing_canon_id
            logger.info(
                f"  #{cand.lemma_id} {cand.lemma_ar} → link to existing canonical "
                f"#{existing_canon_id} ({canonical_ar})"
            )
            if args.apply:
                try:
                    db = SessionLocal()
                    try:
                        link_as_variant(db, cand.lemma_id, existing_canon_id)
                        linked_inflected_ids.append(cand.lemma_id)
                    finally:
                        db.close()
                except Exception as e:
                    action["error"] = str(e)
                    failed.append({"lemma_id": cand.lemma_id, "reason": f"link failed: {e}"})
                    logger.warning(f"    link failed: {e}")
                    continue
        elif existing_canon_id == cand.lemma_id:
            # The lookup mapped back to the inflected lemma itself — happens when
            # clitic-strip + lookup loops. The canonical bare doesn't exist as a
            # distinct lemma. Treat as missing.
            existing_canon_id = None

        if existing_canon_id is None:
            action["action"] = "create_and_link"
            logger.info(
                f"  #{cand.lemma_id} {cand.lemma_ar} → CREATE canonical {canonical_ar} "
                f"({c.get('canonical_wazn')}) then link"
            )
            if args.apply:
                if not cand.root_id:
                    failed.append({"lemma_id": cand.lemma_id, "reason": "no root_id on inflected lemma"})
                    logger.warning("    cannot create canonical without root_id — skipping")
                    continue
                try:
                    db = SessionLocal()
                    try:
                        new_canon_id = create_canonical_lemma(
                            db,
                            canonical_lemma_ar=canonical_ar,
                            canonical_lemma_ar_bare=canonical_bare,
                            canonical_wazn=c.get("canonical_wazn", ""),
                            canonical_gloss_en=c.get("canonical_gloss_en", ""),
                            root_id=cand.root_id,
                        )
                        created_canonical_ids.append(new_canon_id)
                        action["new_canonical_lemma_id"] = new_canon_id
                    finally:
                        db.close()
                    db = SessionLocal()
                    try:
                        link_as_variant(db, cand.lemma_id, new_canon_id)
                        linked_inflected_ids.append(cand.lemma_id)
                    finally:
                        db.close()
                    # Refresh lemma_lookup so subsequent batch-members
                    # checking the same canonical bare hit the cache
                    lemma_lookup[canonical_bare] = new_canon_id
                except Exception as e:
                    action["error"] = str(e)
                    failed.append({"lemma_id": cand.lemma_id, "reason": f"create+link failed: {e}"})
                    logger.warning(f"    create+link failed: {e}")
                    continue

        actions.append(action)

    summary = {
        "generated_at": datetime.now().isoformat(),
        "applied": args.apply,
        "candidates_processed": len(candidates),
        "linked_existing_canonical": sum(1 for a in actions if a.get("action") == "link_existing"),
        "created_and_linked": sum(1 for a in actions if a.get("action") == "create_and_link"),
        "skipped_not_inflected": len(skipped_not_inflected),
        "skipped_no_classification": len(skipped_no_classification),
        "failed": failed,
        "new_canonical_ids": created_canonical_ids,
        "linked_inflected_ids": linked_inflected_ids,
        "actions": actions,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    logger.info(f"\nReport: {output_path}")
    logger.info(f"Linked to existing canonical: {summary['linked_existing_canonical']}")
    logger.info(f"Created canonical + linked:    {summary['created_and_linked']}")
    logger.info(f"Skipped (not inflected):       {summary['skipped_not_inflected']}")
    logger.info(f"Failed:                        {len(failed)}")

    if args.apply and (created_canonical_ids or linked_inflected_ids):
        db = SessionLocal()
        try:
            log_activity(
                db,
                event_type="manual_action",
                summary=(
                    f"Textbook inflected verb cleanup: created {len(created_canonical_ids)} "
                    f"canonical verbs, linked {len(linked_inflected_ids)} inflected forms as variants"
                ),
                detail={
                    "new_canonical_ids": created_canonical_ids,
                    "linked_inflected_ids": linked_inflected_ids,
                    "report_path": str(output_path),
                    "script": "cleanup_textbook_inflected_verbs.py",
                },
            )
        finally:
            db.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
