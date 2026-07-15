"""Remediate SentenceWord rows damaged by the citation-form lookup collisions.

Background (research/spec-2026-07-15-lookup-clitic-collision.md §7): before
PR #212, `lookup_lemma`'s fuzzy fallbacks mapped tokens of then-unknown words
onto wrong existing lemmas (أَصْبَحَ→صُبْح "morning", تَوَقَّفَ→وَقَفَ …). The affected
(query, wrong_lemma) pairs are the momo traces + FCE future collisions in
research/lookup-collision-findings-2026-07-15.json. Census-A pairs (skeleton
homographs like درس) are deliberately excluded — for those the surface
legitimately matches both lemmas and context decides.

Modes:
  --audit (default): count matching rows, split by reviewability. Idempotent.
  --fix: three-step remediation —
    1. Re-resolve each matched surface through the comprehensive lookup.
       Momo-class rows now direct-match their (recently imported) correct
       lemma; rows that still resolve to the wrong lemma (correct lemma not
       in vocabulary, e.g. ستين "sixty" vs سِتّ "six") are left for step 2.
    2. Affected ACTIVE sentences go through
       mapping_rescue.reverify_all_active_sentences(sentence_ids=…) — the
       sanctioned machinery: re-stamps now-correct mappings, deactivates
       sentences whose correct lemma isn't in the vocabulary (lemmas are
       never auto-created here).
    3. Inactive rows are remapped in place only; they get verified by the
       normal enrichment pipeline if ever activated. Rows that cannot be
       remapped stay as documented residue (need Fix B or a vocab import;
       they are invisible to review while the sentence is inactive).

Run on the server:
  cd /opt/alif/backend && .venv/bin/python3 scripts/remap_collision_mismaps.py [--fix]
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models import ActivityLog, Sentence, SentenceWord  # noqa: E402
from app.services.lemma_quality import _normalize  # noqa: E402
from app.services.sentence_eligibility import MAPPING_VERIFICATION_MIN_AT  # noqa: E402
from app.services.sentence_validator import (  # noqa: E402
    build_comprehensive_lemma_lookup,
    lookup_lemma,
    strip_diacritics,
)

FINDINGS = Path(__file__).resolve().parents[2] / "research" / "lookup-collision-findings-2026-07-15.json"


def load_pairs() -> list[tuple[str, int, str]]:
    """(normalized query bare, wrong lemma_id, label) from the findings JSON."""
    data = json.loads(FINDINGS.read_text())
    pairs: list[tuple[str, int, str]] = []
    for t in data["q1_momo_traces"]:
        if t.get("lemma_id") is not None:
            pairs.append((_normalize(t["word"]), t["lemma_id"], f"momo:{t['word']}"))
    for c in data["q2_fce_true_collisions"]:
        pairs.append((
            _normalize(c["display_form"]),
            c["resolved"]["lemma_id"],
            f"fce:{c['display_form']}",
        ))
    return pairs


def find_mismaps(db, pairs):
    """Rows whose surface bare equals a collision query but whose lemma_id is
    the collision's wrong target. Returns list of dicts."""
    out = []
    for query, wrong_id, label in pairs:
        rows = (
            db.query(SentenceWord, Sentence)
            .join(Sentence, Sentence.id == SentenceWord.sentence_id)
            .filter(SentenceWord.lemma_id == wrong_id)
            .all()
        )
        for sw, s in rows:
            surf_norm = _normalize(sw.surface_form or "")
            if surf_norm not in (query, "ال" + query):
                continue
            reviewable = bool(
                s.is_active
                and s.mappings_verified_at is not None
                and s.mappings_verified_at >= MAPPING_VERIFICATION_MIN_AT
            )
            out.append({
                "sw_id": sw.id, "sentence_id": s.id, "label": label,
                "surface": sw.surface_form, "wrong_lemma_id": wrong_id,
                "is_active": bool(s.is_active), "reviewable": reviewable,
            })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="remap + reverify (default: audit only)")
    args = ap.parse_args()

    pairs = load_pairs()
    print(f"collision pairs loaded: {len(pairs)}")

    db = SessionLocal()
    try:
        mismaps = find_mismaps(db, pairs)
        by_state = Counter(
            "reviewable" if m["reviewable"] else ("active-stale" if m["is_active"] else "inactive")
            for m in mismaps
        )
        print(f"mis-mapped sentence_words: {len(mismaps)}  {dict(by_state)}")
        for label, n in Counter(m["label"] for m in mismaps).most_common(15):
            print(f"  {label}: {n}")

        if not args.fix:
            return

        lookup = build_comprehensive_lemma_lookup(db)
        remapped, unresolvable = 0, []
        active_sentence_ids: set[int] = set()
        for m in mismaps:
            sw = db.get(SentenceWord, m["sw_id"])
            bare = _normalize(sw.surface_form or "")
            new_id = lookup_lemma(
                bare, lookup,
                original_bare=strip_diacritics(sw.surface_form or ""),
            )
            if new_id is not None and new_id != m["wrong_lemma_id"]:
                sw.lemma_id = new_id
                remapped += 1
            else:
                unresolvable.append(m)
            if m["is_active"]:
                active_sentence_ids.add(m["sentence_id"])

        db.add(ActivityLog(
            event_type="collision_mismap_remediation",
            summary=(
                f"Remapped {remapped}/{len(mismaps)} collision-damaged sentence words; "
                f"{len(active_sentence_ids)} active sentences queued for reverification"
            ),
            detail_json={
                "total_mismaps": len(mismaps),
                "remapped": remapped,
                "unresolvable": [
                    {k: u[k] for k in ("sw_id", "sentence_id", "label")} for u in unresolvable
                ],
                "reverify_sentence_ids": sorted(active_sentence_ids),
                "source": "remap_collision_mismaps.py (spec-2026-07-15 §7 remediation)",
            },
        ))
        db.commit()
        print(f"remapped: {remapped}; unresolvable (left for reverify/Fix B): {len(unresolvable)}")
        for u in unresolvable:
            state = "ACTIVE" if u["is_active"] else "inactive"
            print(f"  unresolvable [{state}] {u['label']} sent={u['sentence_id']}")
    finally:
        db.close()

    # Step 2 outside our session (reverify manages its own read/LLM/write phases)
    if args.fix and active_sentence_ids:
        from app.services.mapping_rescue import reverify_all_active_sentences
        print(f"reverifying {len(active_sentence_ids)} active sentences …")
        stats = reverify_all_active_sentences(sentence_ids=sorted(active_sentence_ids))
        print(f"reverify stats: {stats}")


if __name__ == "__main__":
    main()
