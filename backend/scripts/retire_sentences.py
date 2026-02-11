#!/usr/bin/env python3
"""Retire overexposed sentences to improve diversity.

Scores each sentence by scaffold word overexposure. Retires the most
repetitive sentences while keeping at least MIN_ACTIVE per target word.

After retiring, run update_material.py to backfill fresh replacements.

Usage:
    python scripts/retire_sentences.py --dry-run          # preview
    python scripts/retire_sentences.py                    # retire
    python scripts/retire_sentences.py --threshold 0.4    # stricter
    python scripts/retire_sentences.py --min-active 5     # keep more
"""

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.sentence_validator import FUNCTION_WORDS, strip_diacritics

FRESHNESS_BASELINE = 8  # matches sentence_selector.py


def compute_overexposure_index(
    sentence: Sentence,
    sentence_words: list[SentenceWord],
    knowledge_map: dict[int, UserLemmaKnowledge],
) -> float:
    """Compute overexposure index for a sentence (0.0 = totally overexposed, 1.0 = fresh).

    Uses geometric mean of per-scaffold-word penalties.
    """
    scaffold = []
    for sw in sentence_words:
        if not sw.lemma_id:
            continue
        if sw.is_target_word:
            continue
        bare = strip_diacritics(sw.surface_form)
        if bare in FUNCTION_WORDS:
            continue
        scaffold.append(sw)

    if not scaffold:
        return 1.0

    product = 1.0
    for sw in scaffold:
        k = knowledge_map.get(sw.lemma_id)
        times_seen = (k.times_seen or 0) if k else 0
        penalty = min(1.0, FRESHNESS_BASELINE / max(times_seen, 1))
        product *= penalty

    return product ** (1.0 / len(scaffold))


def get_starter(sentence_words: list[SentenceWord]) -> str | None:
    """Get the bare form of the first word in a sentence."""
    if not sentence_words:
        return None
    first = min(sentence_words, key=lambda sw: sw.position)
    return strip_diacritics(first.surface_form)


def main():
    parser = argparse.ArgumentParser(description="Retire overexposed sentences")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, don't modify")
    parser.add_argument("--threshold", type=float, default=0.3,
                        help="Retire sentences with overexposure index below this (default: 0.3)")
    parser.add_argument("--min-active", type=int, default=3,
                        help="Keep at least this many active sentences per target word (default: 3)")
    args = parser.parse_args()

    db = SessionLocal()

    try:
        # Load all data
        sentences = db.query(Sentence).filter(Sentence.is_active == True).all()  # noqa: E712
        all_sw = db.query(SentenceWord).all()
        all_ulk = db.query(UserLemmaKnowledge).all()

        knowledge_map = {k.lemma_id: k for k in all_ulk}

        sw_by_sentence: dict[int, list[SentenceWord]] = {}
        for sw in all_sw:
            sw_by_sentence.setdefault(sw.sentence_id, []).append(sw)

        # Count active sentences per target word
        active_per_target: dict[int | None, int] = {}
        for s in sentences:
            active_per_target[s.target_lemma_id] = active_per_target.get(s.target_lemma_id, 0) + 1

        # Score and decide
        to_retire: list[tuple[Sentence, float, str]] = []  # (sentence, score, reason)
        stats = {"total": len(sentences), "scored": 0, "below_threshold": 0, "hal_retired": 0}

        for sent in sentences:
            sws = sw_by_sentence.get(sent.id, [])
            if not sws:
                continue

            score = compute_overexposure_index(sent, sws, knowledge_map)
            stats["scored"] += 1

            starter = get_starter(sws)
            target_id = sent.target_lemma_id
            active_count = active_per_target.get(target_id, 0)

            # Check: هل heuristic — retire هل sentences when alternatives exist
            if starter == "هل" and active_count > args.min_active + 1:
                to_retire.append((sent, score, "هل starter"))
                stats["hal_retired"] += 1
                continue

            # Check: overexposure threshold
            if score < args.threshold and active_count > args.min_active:
                if sent.times_shown and sent.times_shown >= 1:
                    to_retire.append((sent, score, f"overexposed ({score:.2f})"))
                    stats["below_threshold"] += 1

        # Verify min_active constraint before retiring
        retire_per_target: dict[int | None, int] = {}
        final_retire: list[tuple[Sentence, float, str]] = []
        for sent, score, reason in sorted(to_retire, key=lambda x: x[1]):
            target_id = sent.target_lemma_id
            already_retiring = retire_per_target.get(target_id, 0)
            active = active_per_target.get(target_id, 0)
            if active - already_retiring > args.min_active:
                final_retire.append((sent, score, reason))
                retire_per_target[target_id] = already_retiring + 1

        # Load target word names for display
        target_ids = {s.target_lemma_id for s, _, _ in final_retire if s.target_lemma_id}
        lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(target_ids)).all() if target_ids else []
        lemma_names = {l.lemma_id: f"{l.lemma_ar} ({l.gloss_en})" for l in lemmas}

        # Print results
        print(f"\n{'DRY RUN — ' if args.dry_run else ''}Sentence Retirement Report")
        print(f"{'=' * 60}")
        print(f"Total active sentences: {stats['total']}")
        print(f"Sentences to retire: {len(final_retire)}")
        print(f"  - هل starters: {stats['hal_retired']}")
        print(f"  - Below threshold ({args.threshold}): {stats['below_threshold']}")
        print()

        for sent, score, reason in final_retire[:50]:
            target = lemma_names.get(sent.target_lemma_id, "?")
            arabic = sent.arabic_diacritized or sent.arabic_text
            shown = sent.times_shown or 0
            print(f"  [{reason}] score={score:.2f} shown={shown} target={target}")
            print(f"    {arabic[:80]}")
            print()

        if len(final_retire) > 50:
            print(f"  ... and {len(final_retire) - 50} more")

        # Retire per-target summary
        print(f"\nPer-target retirement summary:")
        for target_id, count in sorted(retire_per_target.items(), key=lambda x: x[1], reverse=True):
            name = lemma_names.get(target_id, f"id={target_id}")
            active = active_per_target.get(target_id, 0)
            remaining = active - count
            print(f"  {name}: retiring {count}, keeping {remaining}")

        # Apply
        if not args.dry_run and final_retire:
            for sent, _, _ in final_retire:
                sent.is_active = False
            db.commit()
            print(f"\nRetired {len(final_retire)} sentences.")

            log_activity(
                db,
                event_type="sentences_retired",
                summary=f"Retired {len(final_retire)} overexposed sentences (threshold {args.threshold})",
                detail={
                    "retired": len(final_retire),
                    "total_active": stats["total"],
                    "threshold": args.threshold,
                    "hal_starters": stats["hal_retired"],
                },
            )
        elif args.dry_run:
            print(f"\nDry run complete. Use without --dry-run to apply.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
