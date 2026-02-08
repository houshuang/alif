#!/usr/bin/env python3
"""Analyze word distribution across generated sentences.

Reports over-represented content words and identifies sentences
that could benefit from regeneration for vocabulary diversity.

Usage:
    python scripts/analyze_word_distribution.py               # report only
    python scripts/analyze_word_distribution.py --top 30      # show top 30
    python scripts/analyze_word_distribution.py --regenerate  # replace worst offenders
    python scripts/analyze_word_distribution.py --regenerate --dry-run
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func as sa_func

from app.database import SessionLocal
from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.sentence_generator import (
    KNOWN_SAMPLE_SIZE,
    get_avoid_words,
    get_content_word_counts,
    sample_known_words_weighted,
)
from app.services.sentence_validator import (
    build_lemma_lookup,
    map_tokens_to_lemmas,
    strip_diacritics,
    tokenize,
    validate_sentence,
)
from app.services.llm import AllProvidersFailed, generate_sentences_batch


def get_lemma_names(db) -> dict[int, tuple[str, str]]:
    """Return {lemma_id: (arabic, english)} for all lemmas."""
    rows = db.query(Lemma.lemma_id, Lemma.lemma_ar, Lemma.gloss_en).all()
    return {lid: (ar, en or "") for lid, ar, en in rows}


def get_sentence_content_words(db) -> dict[int, list[int]]:
    """Return {sentence_id: [lemma_ids]} for non-target, non-function content words."""
    rows = (
        db.query(SentenceWord.sentence_id, SentenceWord.lemma_id)
        .filter(
            SentenceWord.lemma_id.isnot(None),
            SentenceWord.is_target_word == False,  # noqa: E712
        )
        .all()
    )
    result: dict[int, list[int]] = {}
    for sid, lid in rows:
        result.setdefault(sid, []).append(lid)
    return result


def score_sentence_overrepresentation(
    content_lemma_ids: list[int],
    top_overused: set[int],
) -> float:
    """Score how many of a sentence's content words are in the top overused set.

    Returns fraction 0.0-1.0.
    """
    if not content_lemma_ids:
        return 0.0
    overused_count = sum(1 for lid in content_lemma_ids if lid in top_overused)
    return overused_count / len(content_lemma_ids)


def analyze(db, top_n: int = 20) -> dict:
    """Run analysis and return structured results."""
    content_word_counts = get_content_word_counts(db)
    lemma_names = get_lemma_names(db)

    if not content_word_counts:
        print("No sentence data found.")
        return {}

    total_sentences = db.query(sa_func.count(Sentence.id)).scalar()
    counts = sorted(content_word_counts.values())
    median = statistics.median(counts)
    mean = statistics.mean(counts)
    stdev = statistics.stdev(counts) if len(counts) > 1 else 0

    # Top over-represented
    sorted_by_count = sorted(content_word_counts.items(), key=lambda x: x[1], reverse=True)

    print(f"\nContent word distribution ({len(content_word_counts)} lemmas, {total_sentences} sentences):")
    print(f"  Median appearances: {median:.1f}")
    print(f"  Mean appearances:   {mean:.1f}")
    print(f"  Std dev:            {stdev:.1f}")
    print(f"\nTop {top_n} over-represented content words:")

    for i, (lid, cnt) in enumerate(sorted_by_count[:top_n]):
        ar, en = lemma_names.get(lid, ("?", "?"))
        pct = (cnt / total_sentences * 100) if total_sentences else 0
        print(f"  {i+1:3}. {ar:20} ({en:20}) — {cnt:4} sentences ({pct:.1f}%)")

    # Find worst-offender sentences
    threshold = max(median * 2, 3)
    top_overused = {lid for lid, cnt in content_word_counts.items() if cnt >= threshold}

    print(f"\nOveruse threshold: {threshold:.0f} sentences (2x median or 3, whichever is higher)")
    print(f"Words above threshold: {len(top_overused)}")

    sentence_content = get_sentence_content_words(db)
    scored_sentences = []
    for sid, lemma_ids in sentence_content.items():
        score = score_sentence_overrepresentation(lemma_ids, top_overused)
        if score >= 0.5:
            scored_sentences.append((sid, score, len(lemma_ids)))

    scored_sentences.sort(key=lambda x: x[1], reverse=True)

    print(f"\nSentences with >= 50% overused content words: {len(scored_sentences)}")
    if scored_sentences:
        # Show top 10
        for sid, score, n_words in scored_sentences[:10]:
            sent = db.query(Sentence).filter(Sentence.id == sid).first()
            target_ar, target_en = lemma_names.get(sent.target_lemma_id, ("?", "?")) if sent else ("?", "?")
            print(f"  ID {sid}: {score:.0%} overused ({n_words} content words) — target: {target_ar} ({target_en})")
            if sent:
                print(f"    {sent.arabic_text[:80]}")

    return {
        "total_sentences": total_sentences,
        "content_word_counts": content_word_counts,
        "top_overused": top_overused,
        "scored_sentences": scored_sentences,
        "lemma_names": lemma_names,
    }


def regenerate_worst_offenders(
    db,
    scored_sentences: list[tuple[int, float, int]],
    content_word_counts: dict[int, int],
    lemma_names: dict[int, tuple[str, str]],
    model: str = "gemini",
    dry_run: bool = False,
    max_regen: int = 50,
) -> None:
    """Delete and regenerate sentences with high overrepresentation scores."""
    if not scored_sentences:
        print("\nNo sentences to regenerate.")
        return

    to_regen = scored_sentences[:max_regen]
    print(f"\nRegenerating {len(to_regen)} worst-offender sentences...")

    # Load vocabulary for generation
    all_lemmas = (
        db.query(Lemma)
        .join(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.fsrs_card_json.isnot(None))
        .all()
    )
    known_words = [
        {"arabic": lem.lemma_ar, "english": lem.gloss_en or "", "lemma_id": lem.lemma_id}
        for lem in all_lemmas
    ]
    lemma_lookup = build_lemma_lookup(all_lemmas)
    avoid_words = get_avoid_words(content_word_counts, known_words)

    # Group by target_lemma_id for batch regeneration
    target_sentences: dict[int, list[int]] = {}
    for sid, score, _ in to_regen:
        sent = db.query(Sentence).filter(Sentence.id == sid).first()
        if sent and sent.target_lemma_id:
            target_sentences.setdefault(sent.target_lemma_id, []).append(sid)

    total_deleted = 0
    total_regenerated = 0

    for target_lid, sentence_ids in target_sentences.items():
        target_lemma = db.query(Lemma).filter(Lemma.lemma_id == target_lid).first()
        if not target_lemma:
            continue

        ar, en = lemma_names.get(target_lid, ("?", "?"))
        print(f"\n  {ar} ({en}): replacing {len(sentence_ids)} sentences")

        if dry_run:
            total_deleted += len(sentence_ids)
            total_regenerated += len(sentence_ids)
            continue

        # Delete old sentences + their word mappings
        for sid in sentence_ids:
            db.query(SentenceWord).filter(SentenceWord.sentence_id == sid).delete()
            db.query(Sentence).filter(Sentence.id == sid).delete()
            total_deleted += 1

        db.flush()

        # Generate replacements with diversity
        sample = sample_known_words_weighted(
            known_words, content_word_counts, KNOWN_SAMPLE_SIZE,
            target_lemma_id=target_lid,
        )

        try:
            results = generate_sentences_batch(
                target_word=target_lemma.lemma_ar,
                target_translation=target_lemma.gloss_en or "",
                known_words=sample,
                count=len(sentence_ids) + 1,
                difficulty_hint="beginner",
                model_override=model,
                avoid_words=avoid_words,
            )
        except AllProvidersFailed as e:
            print(f"    LLM error: {e}")
            continue

        target_bare = strip_diacritics(target_lemma.lemma_ar)
        all_bare = set(lemma_lookup.keys())

        stored = 0
        for res in results:
            if stored >= len(sentence_ids):
                break

            validation = validate_sentence(
                arabic_text=res.arabic,
                target_bare=target_bare,
                known_bare_forms=all_bare,
            )
            if not validation.valid:
                continue

            sent = Sentence(
                arabic_text=res.arabic,
                arabic_diacritized=res.arabic,
                english_translation=res.english,
                transliteration=res.transliteration,
                source="llm",
                target_lemma_id=target_lid,
            )
            db.add(sent)
            db.flush()

            tokens = tokenize(res.arabic)
            mappings = map_tokens_to_lemmas(
                tokens=tokens,
                lemma_lookup=lemma_lookup,
                target_lemma_id=target_lid,
                target_bare=target_bare,
            )
            for m in mappings:
                sw = SentenceWord(
                    sentence_id=sent.id,
                    position=m.position,
                    surface_form=m.surface_form,
                    lemma_id=m.lemma_id,
                    is_target_word=m.is_target,
                )
                db.add(sw)

            stored += 1

        total_regenerated += stored
        print(f"    Deleted {len(sentence_ids)}, regenerated {stored}")

        db.commit()
        time.sleep(1)  # rate limiting

    prefix = "[dry-run] " if dry_run else ""
    print(f"\n{prefix}Total: deleted {total_deleted}, regenerated {total_regenerated}")


def main():
    parser = argparse.ArgumentParser(description="Analyze word distribution in sentences")
    parser.add_argument("--top", type=int, default=20, help="Show top N over-represented words (default: 20)")
    parser.add_argument("--regenerate", action="store_true", help="Replace worst-offender sentences")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without changing DB")
    parser.add_argument("--max-regen", type=int, default=50, help="Max sentences to regenerate (default: 50)")
    parser.add_argument("--model", default="gemini", help="LLM model for regeneration (default: gemini)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        results = analyze(db, top_n=args.top)
        if not results:
            return

        if args.regenerate:
            regenerate_worst_offenders(
                db,
                scored_sentences=results["scored_sentences"],
                content_word_counts=results["content_word_counts"],
                lemma_names=results["lemma_names"],
                model=args.model,
                dry_run=args.dry_run,
                max_regen=args.max_regen,
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
