#!/usr/bin/env python3
"""Build ``book_frequency_entries``: words ranked by running-text count in real books.

The rank is counted from text, not from the vocabulary: every token of the
chosen BAREC sources goes through the production resolver, tokens with no Alif
lemma are grouped by their CAMeL lemma and kept with ``lemma_id = NULL``. A
frequent word Alif lacks therefore holds its true rank instead of sinking below
the words already in the database (the defect of ``frequency_core_entries``).

Usage (read-only unless --apply):
  cd backend
  PYTHONPATH=. python3 scripts/build_book_frequency.py \
      --corpus data/corpora/barec_sentences.json [--apply]

The corpus file (BAREC sentences, CC-BY-SA) is not in Git. Re-run after new
lemmas are created so their rows get linked.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("ALIF_SKIP_MIGRATIONS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import BookFrequencyEntry, Lemma
from app.services.canonical_resolution import resolve_canonical_via_map
from app.services.frequency_lanes import BOOK_FREQUENCY_VERSION
from app.services.sentence_validator import (
    _is_function_word,
    build_comprehensive_lemma_lookup,
    lookup_lemma_id,
    normalize_alef,
    requires_exact_running_text_alias,
    strip_diacritics,
    strip_tatweel,
)
from reading_readiness import norm_bare, tokenize

DEFAULT_SOURCES = ("Hindawi", "Kalima", "Green Library")
MIN_COUNT = 2
CONTENT_POS = ("noun", "adj", "verb", "adv")
# One proclitic in front of a function word (لذلك، كذلك، بهذا) is still a function word.
PROCLITICS = "وفبلك"


def is_function_surface(bare: str) -> bool:
    if _is_function_word(bare) or _is_function_word(bare.replace("ى", "ي")):
        return True
    return len(bare) > 2 and bare[0] in PROCLITICS and _is_function_word(bare[1:])


def count_words(db, texts) -> tuple[Counter, dict, int]:
    """Return (counts keyed by ("L", lemma_id) or ("C", bare, pos), key->pos, tokens)."""
    from app.services.morphology import get_best_lemma_mle

    lemmas = {l.lemma_id: l for l in db.query(Lemma).all()}
    parents = {lid: l.canonical_lemma_id for lid, l in lemmas.items()}
    lookup = build_comprehensive_lemma_lookup(db)
    cache: dict[str, tuple | None] = {}
    counts: Counter = Counter()
    total = 0

    def classify(tok: str):
        bare = norm_bare(tok)
        if not requires_exact_running_text_alias(tok) and is_function_surface(bare):
            return None
        lemma_id = lookup_lemma_id(tok, lookup, strict_fallback=True)
        if lemma_id is not None:
            lemma = lemmas.get(resolve_canonical_via_map(lemma_id, parents))
            if lemma is None or lemma.word_category in {"proper_name", "onomatopoeia"}:
                return None
            lemma_bare = normalize_alef(strip_diacritics(lemma.lemma_ar_bare or ""))
            if lemma.pos == "particle" or lemma.function_word_override or _is_function_word(lemma_bare):
                return None
            return ("L", lemma.lemma_id)
        try:
            analysis = get_best_lemma_mle(tok)
        except Exception:
            analysis = None
        if not analysis or not analysis.get("lex"):
            return None
        pos = (analysis.get("pos") or "").lower()
        if pos == "noun_prop" or not pos.startswith(CONTENT_POS):
            return None
        key = normalize_alef(strip_tatweel(strip_diacritics(analysis["lex"])))
        return ("C", key, pos.split("_")[0]) if len(key) >= 2 else None

    for text in texts:
        for tok in tokenize(text):
            if not norm_bare(tok):
                continue
            total += 1
            if tok not in cache:
                cache[tok] = classify(tok)
            key = cache[tok]
            if key is not None:
                counts[key] += 1
    return counts, lemmas, total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--source", action="append", help="BAREC source name (repeatable)")
    parser.add_argument("--apply", action="store_true", help="Replace the table contents")
    args = parser.parse_args()

    sources = set(args.source or DEFAULT_SOURCES)
    texts = [r["text"] for r in json.loads(args.corpus.read_text(encoding="utf-8")) if r.get("source") in sources]
    db = SessionLocal()
    counts, lemmas, total = count_words(db, texts)
    db.rollback()  # the count is read-only and slow: hold no transaction across it

    ranked = sorted(
        ((key, n) for key, n in counts.items() if n >= MIN_COUNT),
        key=lambda item: (-item[1], str(item[0])),
    )
    linked = sum(1 for key, _ in ranked if key[0] == "L")
    print(f"{len(texts)} sentences, {total} tokens, {len(ranked)} ranked words "
          f"({linked} linked to a lemma, {len(ranked) - linked} not in Alif)")
    for band in (500, 1000, 2000, 3000, 5000):
        seg = ranked[:band]
        print(f"  top {band}: {sum(1 for k, _ in seg if k[0] == 'L')} linked, "
              f"{100 * sum(n for _, n in seg) / max(total, 1):.1f}% of tokens")
    if not args.apply:
        print("Dry run. Pass --apply to write.")
        return

    db.query(BookFrequencyEntry).delete()
    for rank, (key, n) in enumerate(ranked, 1):
        if key[0] == "L":
            lemma = lemmas[key[1]]
            entry = BookFrequencyEntry(
                rank=rank, lemma_id=lemma.lemma_id,
                lemma_key=normalize_alef(strip_diacritics(lemma.lemma_ar_bare or "")),
                pos=lemma.pos, count=n, version=BOOK_FREQUENCY_VERSION,
            )
        else:
            entry = BookFrequencyEntry(
                rank=rank, lemma_id=None, lemma_key=key[1], pos=key[2],
                count=n, version=BOOK_FREQUENCY_VERSION,
            )
        db.add(entry)
    db.commit()
    print(f"Wrote {len(ranked)} rows ({BOOK_FREQUENCY_VERSION}).")


if __name__ == "__main__":
    main()
