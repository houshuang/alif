"""Import sentences from Hindawi Arabic E-Book Corpus (children's books).

Corpus: 1,745 books, 81.5M words, CC-BY-4.0
Source: https://huggingface.co/datasets/mohres/The_Arabic_E-Book_Corpus

Pipeline:
  1. Load parquet, filter by category (default: children.stories)
  2. Extract sentences (configurable word count range)
  3. First pass: collect unmapped words, detect proper names
  4. Second pass: map tokens with name detection, reject sentences
     with any unmapped content words
  5. Batch translate via Claude CLI (free)
  6. Create Sentence + SentenceWord records (source="corpus")

Rules:
  - NO new Lemma records created
  - NO new ULK records created
  - Every non-function, non-name content word must map to an existing lemma
  - Sentences start is_active=True (sentence selector's comprehensibility
    gate handles filtering at review time)

Usage:
  # Analyze only (no DB writes)
  python3 scripts/import_hindawi.py --parquet /path/to/hindawi.parquet --analyze

  # Import (creates sentences)
  python3 scripts/import_hindawi.py --parquet /path/to/hindawi.parquet --import

  # Import specific categories
  python3 scripts/import_hindawi.py --parquet /path/to/hindawi.parquet --import --category novels
"""

import argparse
import logging
import re
import sys
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, ".")
from app.database import SessionLocal
from app.models import Sentence, SentenceWord
from app.services.sentence_validator import (
    build_lemma_lookup,
    detect_proper_names,
    map_tokens_to_lemmas,
    normalize_alef,
    strip_diacritics,
    strip_punctuation,
    strip_tatweel,
    tokenize_display,
    _is_function_word,
)
from app.services.transliteration import transliterate_arabic

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ── Sentence extraction ──────────────────────────────────────────────

SENT_SPLIT = re.compile(r"[.!?؟\n]+")
# Quotes, dashes, and other non-sentence characters
TRIM_CHARS = re.compile(r'^[«»"\'\-–—\s:؛,،]+|[«»"\'\-–—\s:؛,،]+$')


def extract_sentences(text: str, min_words: int = 5, max_words: int = 14) -> list[str]:
    """Split book text into candidate sentences by length."""
    sents = []
    for chunk in SENT_SPLIT.split(text):
        chunk = TRIM_CHARS.sub("", chunk.strip())
        if not chunk:
            continue
        words = chunk.split()
        if min_words <= len(words) <= max_words:
            if any("\u0600" <= c <= "\u06FF" for c in chunk):
                sents.append(chunk)
    return sents


# ── Name detection ───────────────────────────────────────────────────

def build_name_set(
    all_sentences: list[dict],
    lemma_lookup: dict[str, int],
) -> set[str]:
    """Two-pass name detection: collect unmapped words, then classify.

    Uses two strategies:
    1. Static list of known foreign names (from detect_proper_names)
    2. Book-concentration heuristic: words that appear frequently but
       only in 1-3 books are likely character names (جوناثان, بلاكي),
       while words spread across many books are real vocabulary gaps.
    """
    logger.info("Pass 1: collecting unmapped words...")
    unmapped_freq: dict[str, int] = {}
    # Track which books each unmapped word appears in
    unmapped_books: dict[str, set[str]] = {}

    for s in all_sentences:
        tokens = tokenize_display(s["text"])
        mappings = map_tokens_to_lemmas(
            tokens=tokens,
            lemma_lookup=lemma_lookup,
            target_lemma_id=0,
            target_bare="",
        )
        for m in mappings:
            if m.lemma_id is None and not m.is_function_word:
                bare = normalize_alef(strip_diacritics(strip_punctuation(
                    strip_tatweel(m.surface_form)
                )))
                if bare and len(bare) > 1:
                    unmapped_freq[bare] = unmapped_freq.get(bare, 0) + 1
                    if bare not in unmapped_books:
                        unmapped_books[bare] = set()
                    unmapped_books[bare].add(s["title"])

    logger.info(f"Found {len(unmapped_freq)} unique unmapped words")

    # Step 1: known foreign names from static list
    names = detect_proper_names(unmapped_freq, lemma_lookup, min_frequency=3)

    # Step 2: book-concentration heuristic for character names
    # A word in 1-3 books with 20+ occurrences is almost certainly a character name.
    # Real vocabulary appears across many books.
    for word, count in unmapped_freq.items():
        if word in names or word in lemma_lookup:
            continue
        n_books = len(unmapped_books.get(word, set()))
        if count >= 20 and n_books <= 3:
            names.add(word)

    logger.info(f"Detected {len(names)} proper names ({len(names)} static + book-concentrated)")

    # Report top remaining unmapped (not names) for manual review
    remaining = {w: c for w, c in unmapped_freq.items() if w not in names}
    top_remaining = sorted(remaining.items(), key=lambda x: -x[1])[:30]
    if top_remaining:
        logger.info("Top 30 unmapped words NOT classified as names:")
        for word, count in top_remaining:
            n_books = len(unmapped_books.get(word, set()))
            logger.info(f"  {word}: {count} sentences, {n_books} books")

    return names


# ── Deduplication ────────────────────────────────────────────────────

def get_existing_arabic_texts(db) -> set[str]:
    """Load normalized Arabic text of all existing sentences for dedup."""
    from sqlalchemy import text
    rows = db.execute(text("SELECT arabic_text FROM sentences")).fetchall()
    return {normalize_alef(strip_diacritics(r[0])) for r in rows if r[0]}


# ── Main pipeline ────────────────────────────────────────────────────

def run_pipeline(
    parquet_path: str,
    category: str = "children",
    min_words: int = 5,
    max_words: int = 14,
    analyze_only: bool = False,
    limit: int | None = None,
):
    # Load corpus
    logger.info(f"Loading {parquet_path}...")
    df = pd.read_parquet(parquet_path)
    logger.info(f"Total books: {len(df)}")

    # Filter by category
    filtered = df[df["category"].str.contains(category, case=False, na=False)]
    logger.info(f"Books matching '{category}': {len(filtered)}")
    if filtered.empty:
        logger.error(f"No books match category '{category}'")
        logger.info(f"Available categories: {df['category'].unique().tolist()[:20]}")
        return

    total_wc = filtered["wc"].sum()
    logger.info(f"Total word count: {total_wc:,}")

    # Extract sentences
    all_sentences: list[dict] = []
    for _, b in filtered.iterrows():
        text = b["text"] or ""
        sents = extract_sentences(text, min_words, max_words)
        for s in sents:
            all_sentences.append({
                "text": s,
                "title": b["title"],
                "author": b["author"],
            })

    logger.info(f"Extracted {len(all_sentences):,} candidate sentences ({min_words}-{max_words} words)")

    # Build lemma lookup
    db = SessionLocal()
    try:
        from app.models import Lemma
        all_lemmas = db.query(Lemma).filter(Lemma.canonical_lemma_id.is_(None)).all()
        lookup = build_lemma_lookup(all_lemmas)
        logger.info(f"Lemma lookup: {len(lookup)} entries from {len(all_lemmas)} lemmas")

        # Detect names
        names = build_name_set(all_sentences, lookup)

        # Load existing sentences for dedup
        existing = get_existing_arabic_texts(db)
        logger.info(f"Existing sentences in DB: {len(existing)}")

        # Second pass: map with names, filter
        logger.info("Pass 2: mapping sentences with name detection...")
        accepted: list[dict] = []
        rejected_count = 0
        dedup_count = 0

        for s in all_sentences:
            # Dedup check
            text_norm = normalize_alef(strip_diacritics(s["text"]))
            if text_norm in existing:
                dedup_count += 1
                continue

            tokens = tokenize_display(s["text"])
            mappings = map_tokens_to_lemmas(
                tokens=tokens,
                lemma_lookup=lookup,
                target_lemma_id=0,
                target_bare="",
                proper_names=names,
            )

            # Check: every non-function, non-name content word must have a lemma
            has_unmapped = False
            mapped_lemma_ids: set[int] = set()
            for m in mappings:
                if m.is_function_word or m.is_proper_name:
                    continue
                bare = normalize_alef(strip_diacritics(strip_punctuation(
                    strip_tatweel(m.surface_form)
                )))
                if not bare or len(bare) <= 1:
                    continue
                if m.lemma_id is None:
                    has_unmapped = True
                    break
                mapped_lemma_ids.add(m.lemma_id)

            if has_unmapped:
                rejected_count += 1
                continue

            # Must have at least 2 mapped content lemmas to be useful
            if len(mapped_lemma_ids) < 2:
                rejected_count += 1
                continue

            existing.add(text_norm)  # prevent intra-batch dupes
            accepted.append({
                "text": s["text"],
                "title": s["title"],
                "author": s["author"],
                "mappings": mappings,
                "lemma_ids": mapped_lemma_ids,
            })

        logger.info(f"\n=== RESULTS ===")
        logger.info(f"Accepted: {len(accepted):,}")
        logger.info(f"Rejected (unmapped words): {rejected_count:,}")
        logger.info(f"Deduplicated: {dedup_count:,}")

        # Coverage stats
        all_lemma_ids = set()
        for s in accepted:
            all_lemma_ids.update(s["lemma_ids"])
        logger.info(f"Distinct lemmas covered: {len(all_lemma_ids)}/{len(all_lemmas)}")

        # Book distribution
        book_counts: dict[str, int] = {}
        for s in accepted:
            book_counts[s["title"]] = book_counts.get(s["title"], 0) + 1
        logger.info(f"From {len(book_counts)} different books")

        # Samples
        import random
        random.seed(42)
        samples = random.sample(accepted, min(10, len(accepted)))
        logger.info("\nSample accepted sentences:")
        for s in samples:
            logger.info(f"  {s['text']}")
            logger.info(f"    from: {s['title']}")

        if analyze_only:
            logger.info("\n[Analyze mode — no DB writes]")
            return

        if limit:
            accepted = accepted[:limit]
            logger.info(f"\nLimited to {limit} sentences for import")

        # Translation is on-demand: corpus sentences are imported without
        # english_translation. The update_material cron translates sentences
        # for due/acquiring words before they're needed in sessions.

        # ── DB Write ──
        logger.info(f"\nWriting {len(accepted)} sentences to DB...")
        now = datetime.now(timezone.utc)
        created = 0

        for s in accepted:
            transliteration = transliterate_arabic(s["text"]) or ""

            # Pick a target_lemma_id (first mapped content word)
            target_lid = None
            for m in s["mappings"]:
                if m.lemma_id and not m.is_function_word and not m.is_proper_name:
                    target_lid = m.lemma_id
                    break

            sent = Sentence(
                arabic_text=strip_diacritics(s["text"]),
                arabic_diacritized=s["text"],
                english_translation=None,  # translated on-demand by cron
                transliteration=transliteration,
                source="corpus",
                target_lemma_id=target_lid,
                created_at=now,
                mappings_verified_at=now,
                is_active=True,
            )
            db.add(sent)
            db.flush()

            for m in s["mappings"]:
                # For corpus sentences, no word is "the target" — all are scaffold.
                # lemma_id=0 from target matching must be replaced with None.
                lid = m.lemma_id if m.lemma_id and m.lemma_id != 0 else None
                sw = SentenceWord(
                    sentence_id=sent.id,
                    position=m.position,
                    surface_form=m.surface_form,
                    lemma_id=lid,
                    is_target_word=False,
                )
                db.add(sw)

            created += 1
            if created % 500 == 0:
                db.commit()
                logger.info(f"  ...committed {created}/{len(accepted)}")

        db.commit()
        logger.info(f"\nDone! Created {created} sentences with source='corpus'")

    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Import Hindawi corpus sentences")
    parser.add_argument("--parquet", required=True, help="Path to hindawi.parquet")
    parser.add_argument("--category", default="children", help="Category filter (default: children)")
    parser.add_argument("--min-words", type=int, default=5, help="Min words per sentence")
    parser.add_argument("--max-words", type=int, default=14, help="Max words per sentence")
    parser.add_argument("--analyze", action="store_true", help="Analyze only, no DB writes")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--import", dest="do_import", action="store_true", help="Import to DB")
    parser.add_argument("--limit", type=int, help="Limit number of sentences to import")
    args = parser.parse_args()

    if not args.analyze and not args.do_import:
        parser.error("Must specify --analyze or --import")

    run_pipeline(
        parquet_path=args.parquet,
        category=args.category,
        min_words=args.min_words,
        max_words=args.max_words,
        analyze_only=args.analyze,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
