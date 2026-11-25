#!/usr/bin/env python3
"""Count recurring 2- and 3-word phrases around each lemma in the book corpus.

Writes a JSON file of phrase candidates per lemma for ``word_card.py``: the
model that writes a word's set phrases sees which combinations really recur in
books and marks the phrases it keeps as attested. Read-only on the database.

Usage:
  cd backend
  PYTHONPATH=. python3 scripts/build_phrase_candidates.py \
      --corpus data/corpora/barec_sentences.json \
      --out data/corpora/lemma_phrase_candidates.json

The corpus (BAREC sentences) and the output stay out of Git, like the corpus
behind ``build_book_frequency.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

os.environ.setdefault("ALIF_SKIP_MIGRATIONS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import Lemma
from app.services.canonical_resolution import resolve_canonical_via_map
from app.services.sentence_validator import build_comprehensive_lemma_lookup, lookup_lemma_id
from build_book_frequency import is_function_surface
from reading_readiness import norm_bare, tokenize

MIN_COUNT = 2
KEEP_PER_LEMMA = 12


def count_phrases(db, texts) -> dict[int, Counter]:
    lemmas = {l.lemma_id: l for l in db.query(Lemma).all()}
    parents = {lid: l.canonical_lemma_id for lid, l in lemmas.items()}
    lookup = build_comprehensive_lemma_lookup(db)
    cache: dict[str, int | None] = {}

    def lemma_of(tok: str) -> int | None:
        if tok not in cache:
            bare = norm_bare(tok)
            lid = None
            if bare and not is_function_surface(bare):
                found = lookup_lemma_id(tok, lookup, strict_fallback=True)
                if found is not None:
                    lemma = lemmas.get(resolve_canonical_via_map(found, parents))
                    if lemma is not None and lemma.word_category not in {"proper_name", "onomatopoeia"}:
                        lid = lemma.lemma_id
            cache[tok] = lid
        return cache[tok]

    phrases: dict[int, Counter] = defaultdict(Counter)
    for text in texts:
        toks = [t for t in tokenize(text) if norm_bare(t)]
        bares = [norm_bare(t) for t in toks]
        for i, tok in enumerate(toks):
            lid = lemma_of(tok)
            if lid is None:
                continue
            seen = set()
            for start in range(max(0, i - 2), i + 1):
                for size in (2, 3):
                    end = start + size
                    if end > len(toks) or not (start <= i < end):
                        continue
                    others = [bares[j] for j in range(start, end) if j != i]
                    # A phrase needs another content word: "this book", "in the book" are not phrases.
                    if not any(len(o) >= 2 and not is_function_surface(o) for o in others):
                        continue
                    key = " ".join(bares[start:end])
                    if key not in seen:
                        seen.add(key)
                        phrases[lid][key] += 1
    return phrases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source", action="append", help="BAREC source name (repeatable; default all)")
    args = parser.parse_args()

    # Every source by default: phrases need more text than the word ranking does.
    rows = json.loads(args.corpus.read_text(encoding="utf-8"))
    texts = [r["text"] for r in rows if not args.source or r.get("source") in set(args.source)]
    db = SessionLocal()
    try:
        phrases = count_phrases(db, texts)
    finally:
        db.rollback()
        db.close()

    out = {}
    for lid, counter in phrases.items():
        kept = [[p, n] for p, n in counter.most_common() if n >= MIN_COUNT][:KEEP_PER_LEMMA]
        if kept:
            out[str(lid)] = kept
    args.out.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"{len(texts)} sentences; phrase candidates for {len(out)} lemmas -> {args.out}")


if __name__ == "__main__":
    main()
