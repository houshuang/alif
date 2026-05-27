"""Propose missing canonical derivations for root-showcase candidates.

For each selected root, consults the LLM with the full existing lemma palette
and asks: which canonical derivations from this root (Form I-X verbs, active
participle, passive participle, masdar, place/instrument noun, common
adjective patterns) are MISSING and would be in active MSA use?

The LLM works from the actual palette glosses, not from Lemma.wazn (which is
NULL on ~50% of gated lemmas). Each proposal includes Arabic with tashkīl,
bare form, POS, gloss, wazn label, and a brief justification.

Dry-run by default. With --apply, valid proposals are inserted as new Lemma
rows and routed through run_quality_gates() — same path any other lemma
creation uses, so variant detection + enrichment + gates_completed_at stamp
all fire. Auto-skip if a clitic-aware lookup finds an existing match.

Reads the JSON output of root_showcase_candidates.py. Run that first.
"""
from __future__ import annotations

import argparse
import json
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


PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lemma_ar": {"type": "string"},
                    "lemma_ar_bare": {"type": "string"},
                    "pos": {
                        "type": "string",
                        "enum": ["verb", "noun", "adj", "adjective", "adverb"],
                    },
                    "gloss_en": {"type": "string"},
                    "wazn": {"type": "string"},
                    "family": {"type": "string"},
                    "justification": {"type": "string"},
                },
                "required": [
                    "lemma_ar", "lemma_ar_bare", "pos", "gloss_en",
                    "wazn", "family", "justification",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["proposals"],
    "additionalProperties": False,
}


SYSTEM_PROMPT = """\
You are an Arabic morphology expert helping to fill canonical-derivation gaps \
in a learner's vocabulary database. The goal is to enable "root-showcase" \
sentences that pack multiple derivations of one root for pedagogical reinforcement.

For a given root and its EXISTING lemma palette, identify the canonical \
derivations (Form I-X verbs, active participle, passive participle, masdar, \
place/time noun, instrument noun, common adjective patterns like fa'iil/nisba) \
that are MISSING and that are in active MSA usage for THIS specific root.

Skip any derivation that:
- is not natural for this root semantically (don't force a Form X if no one says it)
- is obscure or only attested in classical/Quranic registers
- duplicates an existing lemma in the palette (compare bare forms, allow for clitics)

For each proposal include:
- lemma_ar: fully diacritized Arabic (تَشْكِيل on every letter)
- lemma_ar_bare: un-vocalized form, no clitics, no ال prefix unless integral
- pos: one of verb, noun, adj, adverb
- gloss_en: concise English meaning (2-6 words)
- wazn: pattern label using the same conventions as the existing palette \
(form_1..form_10 for verbs, fa'il, maf'ul, maf'al, mif'al, fa''al, fi'ala, \
taf'il, ifti'al, istif'al, etc.)
- family: one of verb_I..verb_X, agent, patient, masdar_I, masdar_II, \
masdar_IV, masdar_VIII, masdar_X, place_or_time, instrument, intensive_profession, \
adj_fa'iil, nisba_adj, elative
- justification: one sentence why this is a canonical MSA derivation from this \
specific root (cite usage, not theory)

Prefer high-frequency, everyday MSA derivations. Maximum 6 proposals per root."""


def build_user_prompt(root: dict[str, Any]) -> str:
    palette_lines = []
    for p in root["palette"]:
        wazn = f" / {p['wazn']}" if p["wazn"] else ""
        fam = f" [{p['family']}]" if p["family"] else " [unmapped]"
        palette_lines.append(
            f"- {p['lemma_ar']} ({p['pos']}{wazn}){fam}: {p['gloss_en']}"
        )
    return f"""\
Root: {root['root']}
Core meaning: {root.get('core_meaning_en') or '(unknown)'}

EXISTING PALETTE ({root['total_canonical_lemmas']} lemmas):
{chr(10).join(palette_lines)}

DIAGNOSTIC (heuristic, may overstate gaps — verify against the palette above):
Families present: {', '.join(root.get('families_present') or []) or '(none mapped)'}
Possibly missing target families: {', '.join(root.get('missing_target_families') or [])}

Propose canonical derivations that are MISSING and in active MSA use. \
Skip any whose forms or glosses overlap with the existing palette.
"""


def proposal_collides_with_existing(
    prop: dict[str, Any], lemma_lookup: dict[str, int], root_lemma_ids: set[int]
) -> int | None:
    """Return the colliding lemma_id if the proposal matches an existing one."""
    bare = (prop.get("lemma_ar_bare") or "").strip()
    if not bare:
        bare = strip_diacritics(prop.get("lemma_ar", ""))
    if not bare:
        return None
    existing = resolve_existing_lemma(bare, lemma_lookup)
    return existing


def validate_proposal_shape(prop: dict[str, Any]) -> str | None:
    """Return error message if proposal is malformed, else None."""
    if not prop.get("lemma_ar") or not prop.get("lemma_ar_bare"):
        return "missing lemma_ar or lemma_ar_bare"
    if prop.get("pos") not in {"verb", "noun", "adj", "adjective", "adverb"}:
        return f"invalid pos: {prop.get('pos')}"
    if not prop.get("gloss_en"):
        return "missing gloss_en"
    if "ـ" in prop["lemma_ar_bare"]:
        return "bare contains tatweel"
    return None


def insert_proposal(db, prop: dict[str, Any], root_id: int) -> int:
    """Insert one proposal as a Lemma row. Returns the new lemma_id."""
    # POS normalisation: schema-permitted enums use "adj" not "adjective"
    pos = prop["pos"]
    if pos == "adjective":
        pos = "adj"
    lemma = Lemma(
        lemma_ar=prop["lemma_ar"],
        lemma_ar_bare=prop["lemma_ar_bare"],
        root_id=root_id,
        pos=pos,
        gloss_en=prop["gloss_en"],
        wazn=prop.get("wazn") or None,
        source="root_showcase_gap_fill",
    )
    db.add(lemma)
    db.flush()
    return lemma.lemma_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidates-json",
        default=None,
        help="Path to root_showcase_candidates JSON (default: research/root-showcase-candidates-<today>.json)",
    )
    parser.add_argument("--top", type=int, default=5, help="Top N candidate roots to process")
    parser.add_argument("--apply", action="store_true", help="Persist proposals + run quality gates (default: dry-run)")
    parser.add_argument("--max-per-root", type=int, default=6, help="Cap proposals per root")
    parser.add_argument(
        "--output",
        default=None,
        help="Where to write the proposal JSON log (default: research/root-palette-proposals-<date>.json)",
    )
    args = parser.parse_args()

    today = datetime.now().strftime("%Y-%m-%d")
    repo_root = Path(__file__).resolve().parents[2]
    candidates_path = Path(args.candidates_json) if args.candidates_json else repo_root / "research" / f"root-showcase-candidates-{today}.json"
    output_path = Path(args.output) if args.output else repo_root / "research" / f"root-palette-proposals-{today}.json"

    if not candidates_path.exists():
        print(f"ERROR: candidates file not found: {candidates_path}")
        print("Run root_showcase_candidates.py first.")
        sys.exit(1)

    candidates_doc = json.loads(candidates_path.read_text())
    top_roots = candidates_doc["candidates"][: args.top]

    print(f"Loaded {len(candidates_doc['candidates'])} candidates; processing top {len(top_roots)}.")
    print(f"Mode: {'APPLY (will create lemmas + run gates)' if args.apply else 'DRY-RUN'}")
    print()

    db = SessionLocal()
    try:
        lemma_lookup = build_comprehensive_lemma_lookup(db)
    finally:
        db.close()

    report: list[dict[str, Any]] = []
    new_lemma_ids: list[int] = []

    for i, root in enumerate(top_roots, 1):
        print(f"[{i}/{len(top_roots)}] {root['root']} (palette={root['total_canonical_lemmas']}, cov={root['user_coverage']:.0%})")
        prompt = build_user_prompt(root)

        try:
            result = generate_completion(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                json_schema=PROPOSAL_SCHEMA,
                model_override="claude_haiku",
                task_type="root_showcase_gap_fill",
                temperature=0.3,
                timeout=120,
            )
        except Exception as e:
            print(f"  LLM failed: {e}")
            report.append({"root": root["root"], "error": str(e)})
            continue

        proposals = result.get("proposals") if isinstance(result, dict) else []
        if not isinstance(proposals, list):
            proposals = []
        proposals = proposals[: args.max_per_root]
        print(f"  LLM proposed {len(proposals)} derivation(s)")

        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        existing_root_lemma_ids = {p["lemma_id"] for p in root["palette"]}

        for prop in proposals:
            shape_err = validate_proposal_shape(prop)
            if shape_err:
                rejected.append({**prop, "reason": shape_err})
                continue
            collision = proposal_collides_with_existing(prop, lemma_lookup, existing_root_lemma_ids)
            if collision is not None:
                rejected.append({**prop, "reason": f"collides with existing lemma #{collision}"})
                continue
            accepted.append(prop)

        for p in accepted:
            print(f"    + {p['lemma_ar']:<15} ({p['pos']}/{p['wazn']}) = {p['gloss_en']}")
        for r in rejected:
            print(f"    - skipped {r.get('lemma_ar') or '?'}: {r.get('reason')}")

        root_record: dict[str, Any] = {
            "root_id": root["root_id"],
            "root": root["root"],
            "accepted": accepted,
            "rejected": rejected,
        }

        if args.apply and accepted:
            db = SessionLocal()
            try:
                inserted_ids: list[int] = []
                for prop in accepted:
                    lid = insert_proposal(db, prop, root["root_id"])
                    inserted_ids.append(lid)
                    # Update the lookup so within-batch dedup still works
                    lemma_lookup[strip_diacritics(prop["lemma_ar_bare"])] = lid
                db.commit()
                run_quality_gates(
                    db,
                    inserted_ids,
                    skip_variants=False,
                    enrich=True,
                    background_enrich=False,
                )
                db.commit()
                root_record["inserted_lemma_ids"] = inserted_ids
                new_lemma_ids.extend(inserted_ids)
                print(f"    inserted + gated: {inserted_ids}")
            except Exception:
                db.rollback()
                print(f"    APPLY FAILED for {root['root']}, rolled back")
                raise
            finally:
                db.close()

        report.append(root_record)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "candidates_source": str(candidates_path),
        "top_processed": len(top_roots),
        "applied": args.apply,
        "new_lemma_ids": new_lemma_ids,
        "roots": report,
    }, ensure_ascii=False, indent=2))
    print()
    print(f"Report: {output_path}")
    if args.apply and new_lemma_ids:
        db = SessionLocal()
        try:
            log_activity(
                db,
                event_type="root_palette_extended",
                summary=f"Root-showcase gap-fill: created {len(new_lemma_ids)} lemmas across {len(top_roots)} roots",
                detail={
                    "new_lemma_ids": new_lemma_ids,
                    "roots_processed": [r["root"] for r in top_roots],
                    "report_path": str(output_path),
                },
            )
        finally:
            db.close()


if __name__ == "__main__":
    main()
