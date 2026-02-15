#!/usr/bin/env python3
"""Rotate stale sentences to improve vocabulary diversity.

Identifies sentences where all non-target content words are fully "known"
(high FSRS stability), meaning the sentence provides no cross-training value
beyond the target word. Retires these to make room for new sentences that
incorporate currently-acquiring words as supporting vocabulary.

After running, use generate_sentences_claude.py or update_material.py to
backfill fresh, vocabulary-diverse replacements.

Usage:
    python3 scripts/rotate_stale_sentences.py --dry-run          # preview
    python3 scripts/rotate_stale_sentences.py                     # retire stale
    python3 scripts/rotate_stale_sentences.py --min-shown 2       # only retire if shown ≥2 times
    python3 scripts/rotate_stale_sentences.py --min-active 2      # keep at least 2 per word
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.activity_log import log_activity


def compute_diversity_score(
    sentence_words: list[SentenceWord],
    knowledge_map: dict[int, UserLemmaKnowledge],
) -> dict:
    """Score a sentence's vocabulary diversity value.

    Returns dict with:
        scaffold_count: number of non-target content words with lemma_id
        acquiring_count: how many scaffold words are in acquiring/learning state
        known_count: how many scaffold words are fully known
        diversity_score: fraction of scaffold that is acquiring/learning (0.0-1.0)
        scaffold_lemma_ids: set of lemma_ids for scaffold words
    """
    scaffold_lemma_ids: set[int] = set()
    acquiring_count = 0
    known_count = 0
    other_count = 0

    for sw in sentence_words:
        if not sw.lemma_id:
            continue
        if sw.is_target_word:
            continue
        # Deduplicate by lemma_id (same word appearing twice shouldn't double-count)
        if sw.lemma_id in scaffold_lemma_ids:
            continue
        scaffold_lemma_ids.add(sw.lemma_id)

        ulk = knowledge_map.get(sw.lemma_id)
        if not ulk:
            other_count += 1
            continue

        if ulk.knowledge_state in ("acquiring", "learning", "lapsed"):
            acquiring_count += 1
        elif ulk.knowledge_state == "known":
            known_count += 1
        else:
            other_count += 1

    total = len(scaffold_lemma_ids)
    diversity_score = acquiring_count / total if total > 0 else 0.0

    return {
        "scaffold_count": total,
        "acquiring_count": acquiring_count,
        "known_count": known_count,
        "other_count": other_count,
        "diversity_score": diversity_score,
        "scaffold_lemma_ids": scaffold_lemma_ids,
    }


def main():
    parser = argparse.ArgumentParser(description="Rotate stale sentences for vocabulary diversity")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, don't modify")
    parser.add_argument("--min-shown", type=int, default=1,
                        help="Only retire sentences shown at least this many times (default: 1)")
    parser.add_argument("--min-active", type=int, default=2,
                        help="Keep at least this many active sentences per target word (default: 2)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()

    try:
        sentences = db.query(Sentence).filter(Sentence.is_active == True).all()  # noqa: E712
        all_sw = db.query(SentenceWord).all()
        all_ulk = db.query(UserLemmaKnowledge).all()

        knowledge_map = {k.lemma_id: k for k in all_ulk}

        sw_by_sentence: dict[int, list[SentenceWord]] = {}
        for sw in all_sw:
            sw_by_sentence.setdefault(sw.sentence_id, []).append(sw)

        active_per_target: dict[int | None, int] = {}
        for s in sentences:
            active_per_target[s.target_lemma_id] = active_per_target.get(s.target_lemma_id, 0) + 1

        # Score all sentences
        stale: list[tuple[Sentence, dict]] = []
        all_scores: list[dict] = []

        for sent in sentences:
            sws = sw_by_sentence.get(sent.id, [])
            if not sws:
                continue

            scores = compute_diversity_score(sws, knowledge_map)
            scores["sentence_id"] = sent.id
            scores["times_shown"] = sent.times_shown or 0
            scores["target_lemma_id"] = sent.target_lemma_id
            all_scores.append(scores)

            # Stale = no acquiring/learning words in scaffold AND has been shown enough
            is_stale = (
                scores["acquiring_count"] == 0
                and scores["scaffold_count"] >= 2  # need scaffold to evaluate
                and (sent.times_shown or 0) >= args.min_shown
            )

            if is_stale:
                stale.append((sent, scores))

        # Sort by lowest diversity (least useful sentences first)
        stale.sort(key=lambda x: (x[1]["diversity_score"], -x[1]["scaffold_count"]))

        # Enforce min_active constraint
        retire_per_target: dict[int | None, int] = {}
        final_retire: list[tuple[Sentence, dict]] = []
        for sent, scores in stale:
            target_id = sent.target_lemma_id
            already_retiring = retire_per_target.get(target_id, 0)
            active = active_per_target.get(target_id, 0)
            if active - already_retiring > args.min_active:
                final_retire.append((sent, scores))
                retire_per_target[target_id] = already_retiring + 1

        # Load lemma names for display
        target_ids = {s.target_lemma_id for s, _ in final_retire if s.target_lemma_id}
        lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(target_ids)).all() if target_ids else []
        lemma_names = {l.lemma_id: f"{l.lemma_ar} ({l.gloss_en})" for l in lemmas}

        # Summary stats
        total_acquiring = sum(1 for s in all_scores if s["acquiring_count"] > 0)
        total_stale = len(stale)
        avg_diversity = sum(s["diversity_score"] for s in all_scores) / max(len(all_scores), 1)

        print(f"\n{'DRY RUN — ' if args.dry_run else ''}Stale Sentence Rotation Report")
        print(f"{'=' * 60}")
        print(f"Total active sentences: {len(sentences)}")
        print(f"Sentences with acquiring words: {total_acquiring} ({total_acquiring*100//max(len(all_scores),1)}%)")
        print(f"Stale sentences (0 acquiring words, shown≥{args.min_shown}): {total_stale}")
        print(f"After min-active constraint: {len(final_retire)} to retire")
        print(f"Average diversity score: {avg_diversity:.2f}")
        print()

        # Show sentences to retire
        if final_retire:
            print(f"Sentences to retire:")
            for sent, scores in final_retire[:30]:
                target = lemma_names.get(sent.target_lemma_id, "?")
                arabic = sent.arabic_diacritized or sent.arabic_text
                shown = sent.times_shown or 0
                print(f"  id={sent.id} shown={shown} scaffold={scores['scaffold_count']} "
                      f"known={scores['known_count']} target={target}")
                print(f"    {arabic[:80]}")
                if args.verbose:
                    print(f"    scores={scores}")
                print()

            if len(final_retire) > 30:
                print(f"  ... and {len(final_retire) - 30} more")

        # Per-target summary
        if retire_per_target:
            print(f"\nPer-target retirement summary:")
            for target_id, count in sorted(retire_per_target.items(), key=lambda x: x[1], reverse=True)[:20]:
                name = lemma_names.get(target_id, f"id={target_id}")
                active = active_per_target.get(target_id, 0)
                remaining = active - count
                print(f"  {name}: retiring {count}, keeping {remaining}")

        # Words that will need new sentences
        words_needing_regen = set()
        for target_id, retiring_count in retire_per_target.items():
            active = active_per_target.get(target_id, 0)
            remaining = active - retiring_count
            if remaining < 2 and target_id:
                words_needing_regen.add(target_id)

        if words_needing_regen:
            print(f"\nWords needing regeneration ({len(words_needing_regen)}):")
            regen_lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(words_needing_regen)).all()
            for l in regen_lemmas[:20]:
                print(f"  {l.lemma_ar} ({l.gloss_en})")

        # Apply
        if not args.dry_run and final_retire:
            for sent, _ in final_retire:
                sent.is_active = False
            db.commit()
            print(f"\nRetired {len(final_retire)} stale sentences.")

            log_activity(
                db,
                event_type="sentences_retired",
                summary=f"Rotated {len(final_retire)} stale sentences (0 acquiring words in scaffold)",
                detail={
                    "retired": len(final_retire),
                    "total_active": len(sentences),
                    "words_needing_regen": len(words_needing_regen),
                    "min_shown": args.min_shown,
                },
            )

            print(f"\nRun generate_sentences_claude.py or update_material.py to backfill.")
        elif args.dry_run:
            print(f"\nDry run complete. Use without --dry-run to apply.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
