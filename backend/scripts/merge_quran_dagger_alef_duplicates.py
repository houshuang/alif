"""Merge the Quran dagger-alef duplicate/clitic lemmas into their canonical entry.

Companion to fix_quran_dagger_alef_lemmas.py (which handled the no-collision
re-headwords). The audit (scripts/audit_quran_dagger_alef.py) also surfaced
lemmas whose correct dictionary form ALREADY EXISTS as another lemma — either a
plain duplicate (صدقين → صادق) or a clitic-inflected form whose dictionary lemma
exists (شيطينهم "their devils" → شيطان). These must be MERGED into the canonical,
not re-headworded (that would create a collision).

Merge = repoint every inbound FK from the damaged lemma to the canonical, merge
the UserLemmaKnowledge row into the canonical's (sum counts, keep the more
advanced state), then delete the damaged lemma. Covers all 12 columns that
reference lemmas.lemma_id (verified against models.py 2026-06-02).

    python3 scripts/merge_quran_dagger_alef_duplicates.py            # dry run
    python3 scripts/merge_quran_dagger_alef_duplicates.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models import (
    ConfusionCapture,
    ContentFlag,
    FrequencyCoreEntry,
    Lemma,
    QuranicVerseWord,
    ReviewLog,
    Sentence,
    SentenceWord,
    StoryWord,
    UserLemmaKnowledge,
)

# damaged lemma_id -> canonical/dictionary target lemma_id
MERGES: dict[int, int] = {
    # plain duplicates (correct dictionary form already existed)
    2881: 2542,  # صدقين -> صادق  (truthful)
    2885: 2608,  # متشبها -> متشابه (similar)
    2863: 3448,  # ظلمت -> ظلمة (darkness)
    # clitic-inflected forms; dictionary lemma exists
    2850: 1444,  # شيطينهم -> شيطان (devil)
    2854: 3350,  # طغينهم -> طغيان (transgression)
    2858: 3351,  # تجرتهم -> تجارة (trade)
    2865: 3168,  # اصبعهم -> إصبع (finger)
    2902: 1450,  # للملئكة -> ملائكة (angels)
}

# State-advancement order for merging two ULK rows.
_STATE_RANK = {
    "new": 0, "encountered": 1, "acquiring": 2, "learning": 3,
    "lapsed": 4, "known": 5, "suspended": 6,
}


def _repoint(db, damaged: int, target: int) -> dict:
    counts = {}
    counts["review_log"] = (
        db.query(ReviewLog).filter_by(lemma_id=damaged).update({"lemma_id": target})
    )
    counts["sentence_words"] = (
        db.query(SentenceWord).filter_by(lemma_id=damaged).update({"lemma_id": target})
    )
    counts["quranic_vw"] = (
        db.query(QuranicVerseWord).filter_by(lemma_id=damaged).update({"lemma_id": target})
    )
    counts["sentences_target"] = (
        db.query(Sentence).filter_by(target_lemma_id=damaged).update({"target_lemma_id": target})
    )
    counts["story_words"] = (
        db.query(StoryWord).filter_by(lemma_id=damaged).update({"lemma_id": target})
    )
    counts["content_flags"] = (
        db.query(ContentFlag).filter_by(lemma_id=damaged).update({"lemma_id": target})
    )
    counts["fce"] = (
        db.query(FrequencyCoreEntry).filter_by(lemma_id=damaged).update({"lemma_id": target})
    )
    counts["cc_failed"] = (
        db.query(ConfusionCapture).filter_by(failed_lemma_id=damaged).update({"failed_lemma_id": target})
    )
    counts["cc_confused"] = (
        db.query(ConfusionCapture).filter_by(confused_with_lemma_id=damaged).update({"confused_with_lemma_id": target})
    )
    counts["cc_resolved"] = (
        db.query(ConfusionCapture).filter_by(resolved_lemma_id=damaged).update({"resolved_lemma_id": target})
    )
    # variant children: re-parent to the canonical
    counts["variant_children"] = (
        db.query(Lemma).filter_by(canonical_lemma_id=damaged).update({"canonical_lemma_id": target})
    )
    return counts


def _merge_ulk(db, damaged: int, target: int) -> str:
    dk = db.query(UserLemmaKnowledge).filter_by(lemma_id=damaged).first()
    if not dk:
        return "no damaged ULK"
    tk = db.query(UserLemmaKnowledge).filter_by(lemma_id=target).first()
    if not tk:
        # No target ULK: just move the damaged one over.
        dk.lemma_id = target
        return "moved damaged ULK to target"
    # Both exist: fold counts into target, keep the more-advanced state, drop damaged.
    tk.times_seen = (tk.times_seen or 0) + (dk.times_seen or 0)
    tk.times_correct = (tk.times_correct or 0) + (dk.times_correct or 0)
    tk.total_encounters = (tk.total_encounters or 0) + (dk.total_encounters or 0)
    if _STATE_RANK.get(dk.knowledge_state or "new", 0) > _STATE_RANK.get(tk.knowledge_state or "new", 0):
        tk.knowledge_state = dk.knowledge_state
    db.delete(dk)
    return f"folded counts into target (now seen={tk.times_seen}, state={tk.knowledge_state})"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        done = []
        for damaged, target in MERGES.items():
            Ld = db.get(Lemma, damaged)
            Lt = db.get(Lemma, target)
            if not Ld:
                print(f"#{damaged}: NOT FOUND, skipping")
                continue
            if not Lt:
                print(f"#{damaged}: target #{target} NOT FOUND, skipping")
                continue
            print(f"\n#{damaged} {Ld.lemma_ar} ({Ld.gloss_en!r})  ->  #{target} {Lt.lemma_ar} ({Lt.gloss_en!r})")
            if not args.apply:
                continue
            counts = _repoint(db, damaged, target)
            ulk_note = _merge_ulk(db, damaged, target)
            db.flush()
            db.delete(Ld)
            db.commit()
            print(f"   repointed: { {k: v for k, v in counts.items() if v} }")
            print(f"   ULK: {ulk_note}")
            print(f"   deleted lemma #{damaged}")
            done.append((damaged, target))

        if args.apply and done:
            from app.services.activity_log import log_activity
            log_activity(
                db,
                event_type="manual_action",
                summary=f"Merged {len(done)} Quran dagger-alef duplicate/clitic lemmas into canonicals",
                detail={"merges": {str(d): t for d, t in done}},
            )
            db.commit()
        elif not args.apply:
            print("\n(dry run — re-run with --apply to write)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
