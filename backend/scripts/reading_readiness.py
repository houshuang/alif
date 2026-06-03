#!/usr/bin/env python3
"""Reading-readiness analysis: how far is the learner from reading a given text?

Cheap, LLM-free coverage analysis (the "type vs token" / Nation-threshold model):
tokenize the text, lemmatize each token against Alif's own lemmas (the same
clitic-stripping + CAMeL-disambiguation lookup the corpus importer uses — no LLM),
join the learner's UserLemmaKnowledge state, and report:

  - token-weighted coverage % (the "can I read this yet" headline), and
  - the unknown lemmas ranked by their frequency *in this text* — the biggest
    unlocks (learn these N words → +X% coverage), with a coverage curve.

Sort the gap by token frequency *within the target text*, not global frequency:
the highest-leverage words to learn for THIS book are the ones that recur in it.

Usage:
  cd backend
  PYTHONPATH=. python3 scripts/reading_readiness.py --text /path/to/book.txt \
      [--title "The Bamboo Stalk"] [--top 40] [--json out.json]

Takes a plain-text file. No text content or provenance is stored in this repo.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("ALIF_SKIP_MIGRATIONS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge
from app.services.sentence_validator import (
    _is_function_word,
    build_comprehensive_lemma_lookup,
    lookup_lemma,
    normalize_alef,
    strip_diacritics,
    strip_tatweel,
)

# A token is a maximal run of Arabic letters (incl. the standard diacritic range).
ARABIC_TOKEN_RE = re.compile(r"[ء-يٰ-ۿݐ-ݿ]+")


def tokenize(text: str) -> list[str]:
    return ARABIC_TOKEN_RE.findall(text)


def norm_bare(token: str) -> str:
    return normalize_alef(strip_tatweel(strip_diacritics(token)))


# CAMeL POS prefixes that count as teachable content (everything else — preps,
# particles, pronouns, conjunctions, interrogatives — is treated as a readable
# function word, not a learning gap).
_CONTENT_POS_PREFIXES = ("noun", "adj", "verb", "adv")


def _camel_content_lemma(surface: str, cache: dict) -> str | None:
    """CAMeL lemma (bare) for an OOV surface if it's content; None if function/unknown.

    Groups inflected forms of the same unknown word (اغتسلت/يغتسل → اغتسل) so the
    'biggest unlocks' ranking counts a word once, not once per inflection.
    """
    if surface in cache:
        return cache[surface]
    result = None
    try:
        from app.services.morphology import get_best_lemma_mle
        a = get_best_lemma_mle(surface)
        if a and a.get("lex"):
            pos = (a.get("pos") or "").lower()
            # noun_prop = proper name: readable, not a vocab gap → skip (None).
            if pos != "noun_prop" and any(pos.startswith(p) for p in _CONTENT_POS_PREFIXES):
                result = normalize_alef(strip_tatweel(strip_diacritics(a["lex"])))
    except Exception:
        result = None
    cache[surface] = result
    return result


def analyze(db, text: str, top: int) -> dict:
    lemma_lookup = build_comprehensive_lemma_lookup(db)
    states = dict(
        db.query(UserLemmaKnowledge.lemma_id, UserLemmaKnowledge.knowledge_state).all()
    )
    glosses = dict(db.query(Lemma.lemma_id, Lemma.gloss_en).all())
    bares = dict(db.query(Lemma.lemma_id, Lemma.lemma_ar).all())
    poss = dict(db.query(Lemma.lemma_id, Lemma.pos).all())
    camel_cache: dict = {}

    tokens = tokenize(text)
    # Buckets are token counts (occurrences), the unit the coverage threshold uses.
    counts = {
        "total": 0, "function": 0,
        "known": 0,        # mapped + ULK known/learning
        "in_progress": 0,  # mapped + ULK acquiring/lapsed/encountered
        "new_in_vocab": 0, # mapped to a real lemma but no ULK row (we have a gloss)
        "unmapped": 0,     # not in Alif's vocabulary at all
    }
    KNOWN = {"known", "learning"}
    PROGRESS = {"acquiring", "lapsed", "encountered"}

    # Per-gap aggregation (frequency within THIS text).
    new_lemma_freq: dict[int, int] = defaultdict(int)        # lemma_id -> count
    unmapped_surface_freq: dict[str, int] = defaultdict(int) # bare surface -> count

    for tok in tokens:
        bare = norm_bare(tok)
        if len(bare) < 1:
            continue
        counts["total"] += 1
        # Function words: trivially readable, count toward coverage, not a gap.
        if _is_function_word(bare) or _is_function_word(bare.replace("ى", "ي")):
            counts["function"] += 1
            continue
        lemma_id = lookup_lemma(bare, lemma_lookup, original_bare=strip_diacritics(tok))
        if lemma_id is None:
            # OOV — try CAMeL to group inflections under one lemma and drop
            # function/proper words (no content POS) from the gap list.
            camel_lemma = _camel_content_lemma(tok, camel_cache)
            if camel_lemma is None:
                counts["function"] += 1  # function word / unanalyzable — readable
            else:
                counts["unmapped"] += 1
                unmapped_surface_freq[camel_lemma] += 1
            continue
        # A mapped lemma whose POS is a particle is really a function word.
        if poss.get(lemma_id) == "particle":
            counts["function"] += 1
            continue
        state = states.get(lemma_id)
        if state in KNOWN:
            counts["known"] += 1
        elif state in PROGRESS:
            counts["in_progress"] += 1
        else:
            counts["new_in_vocab"] += 1
            new_lemma_freq[lemma_id] += 1

    total = max(counts["total"], 1)
    # "Readable now" = function words + already-known content.
    readable = counts["function"] + counts["known"]
    readable_plus = readable + counts["in_progress"]

    # Biggest unlocks: rank gaps (new-in-vocab + unmapped) by in-text frequency.
    gaps: list[dict] = []
    for lemma_id, c in new_lemma_freq.items():
        gaps.append({"kind": "new_in_vocab", "lemma_id": lemma_id,
                     "display": bares.get(lemma_id, "?"),
                     "gloss": glosses.get(lemma_id), "count": c})
    for surface, c in unmapped_surface_freq.items():
        gaps.append({"kind": "unmapped", "lemma_id": None,
                     "display": surface, "gloss": None, "count": c})
    gaps.sort(key=lambda g: -g["count"])

    # Coverage curve: learning the top-k gap words lifts coverage from `readable`.
    curve = []
    cum = 0
    milestones = [10, 25, 50, 100, 150, 200, 300, 500]
    for i, g in enumerate(gaps, 1):
        cum += g["count"]
        if i in milestones:
            curve.append({"words_learned": i,
                          "coverage_pct": round((readable + cum) / total * 100, 1)})

    return {
        "tokens_total": counts["total"],
        "counts": counts,
        "distinct_gaps": len(gaps),
        "coverage": {
            "readable_now_pct": round(readable / total * 100, 1),
            "with_in_progress_pct": round(readable_plus / total * 100, 1),
            "function_pct": round(counts["function"] / total * 100, 1),
            "known_content_pct": round(counts["known"] / total * 100, 1),
            "unmapped_pct": round(counts["unmapped"] / total * 100, 1),
        },
        "top_unlocks": gaps[:top],
        "coverage_curve": curve,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Reading-readiness coverage analysis")
    ap.add_argument("--text", required=True, type=Path, help="Plain-text file to analyze")
    ap.add_argument("--title", default=None, help="Display title for the report")
    ap.add_argument("--top", type=int, default=40, help="How many top unlocks to show")
    ap.add_argument("--json", type=Path, default=None, help="Write full result JSON here")
    args = ap.parse_args()

    text = args.text.read_text(encoding="utf-8", errors="replace")
    db = SessionLocal()
    try:
        r = analyze(db, text, args.top)
    finally:
        db.close()

    title = args.title or args.text.name
    c = r["counts"]
    cov = r["coverage"]
    print(f"\n=== READING READINESS: {title} ===")
    print(f"tokens: {r['tokens_total']}  (distinct gap words: {r['distinct_gaps']})")
    print(f"\nToken breakdown:")
    print(f"  function/names : {c['function']:6d} ({cov['function_pct']:5.1f}%)  [particles + proper names + OOV-unanalyzed; readable]")
    print(f"  known content  : {c['known']:6d} ({cov['known_content_pct']:5.1f}%)")
    print(f"  in progress    : {c['in_progress']:6d}  [acquiring/lapsed/encountered]")
    print(f"  new (in vocab) : {c['new_in_vocab']:6d}  [have a gloss; not yet learned]")
    print(f"  unmapped (OOV) : {c['unmapped']:6d} ({cov['unmapped_pct']:5.1f}%)  [incl. proper names]")
    print(f"\nCOVERAGE NOW (function + known)         : {cov['readable_now_pct']:5.1f}%")
    print(f"COVERAGE incl. in-progress vocab        : {cov['with_in_progress_pct']:5.1f}%")
    print(f"(Nation: ~95% = minimal comprehension, ~98% = comfortable incidental reading)")

    print(f"\n=== TOP {args.top} UNLOCKS (unknown lemmas by frequency in THIS text) ===")
    cum = 0
    for i, g in enumerate(r["top_unlocks"], 1):
        cum += g["count"]
        gain = cum / r["tokens_total"] * 100
        tag = "" if g["kind"] == "new_in_vocab" else " [OOV]"
        gloss = f" — {g['gloss']}" if g["gloss"] else ""
        print(f"  {i:3d}. {g['count']:4d}x  {g['display']:14}{gloss}{tag}  (cum +{gain:.1f}%)")

    print(f"\n=== COVERAGE CURVE (learn top-N gap words → coverage) ===")
    for pt in r["coverage_curve"]:
        print(f"  +{pt['words_learned']:4d} words → {pt['coverage_pct']:5.1f}%")

    if args.json:
        args.json.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nFull JSON → {args.json}")


if __name__ == "__main__":
    main()
