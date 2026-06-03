#!/usr/bin/env python3
"""Lemmatize the full Quran reading corpus from the Quranic Arabic Corpus.

Alif's Quran *reading* mode lemmatized verses through a slow per-verse LLM/CAMeL
pipeline and only ever covered ~40 of 6,236 verses. The QAC v0.4 morphology file
(committed for the frequency track) is a complete, manually-verified per-token
lemmatization of every verse — so we can backfill QuranicVerseWord for the whole
Quran by position-aligning QAC tokens to verses and reusing the same QAC→Alif
lemma mapping (quran_frequency.resolve_qac_lemma).

Each QAC *word* is a group of segments (prefix/stem/suffix); the STEM segment
carries the dictionary lemma + POS. A word is content (gets a mapped lemma_id)
iff its stem POS is a content tag; otherwise it's a function word (lemma_id NULL,
is_function_word=True — readable via FUNCTION_WORD_GLOSSES, like the old import).

Surface form: when our stored verse text tokenizes to the same word count as the
QAC word groups (both are Uthmani/Tanzil), we keep the displayed token; otherwise
we fall back to the QAC-reconstructed form so positions stay consistent.

Usage:
  cd backend
  PYTHONPATH=. python3 scripts/lemmatize_quran_from_qac.py --dry-run
  PYTHONPATH=. python3 scripts/lemmatize_quran_from_qac.py --apply
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ALIF_SKIP_MIGRATIONS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from camel_tools.utils.charmap import CharMapper

from app.database import SessionLocal
from app.models import Lemma, QuranicVerse, QuranicVerseWord
from app.services.quran_frequency import QAC_DEFAULT_PATH, resolve_qac_lemma
from app.services.sentence_validator import build_comprehensive_lemma_lookup

_BW2AR = CharMapper.builtin_mapper("bw2ar")
_LOC_RE = re.compile(r"\((\d+):(\d+):(\d+):(\d+)\)")
_LEM_RE = re.compile(r"LEM:([^|]+)")
_POS_RE = re.compile(r"POS:([^|]+)")

# QAC POS tags that denote teachable content (everything else is a function word).
_CONTENT_POS = {"N", "PN", "ADJ", "V", "ADV", "IMPN", "NUM"}


def parse_qac_words(path: Path | str):
    """Return {(surah, ayah): [word, ...]} where each word is a dict.

    word = {stem_lem, stem_pos, forms: [bw, ...]}; stem_* are None for words
    with no STEM segment (pure particles).
    """
    verses: dict[tuple[int, int], dict[int, dict]] = defaultdict(dict)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith("("):
                continue
            m = _LOC_RE.match(line)
            if not m:
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 4:
                continue
            surah, ayah, word_no, _seg = (int(g) for g in m.groups())
            form, _tag, feats = parts[1], parts[2], parts[3]
            words = verses[(surah, ayah)]
            w = words.get(word_no)
            if w is None:
                w = {"stem_lem": None, "stem_pos": None, "forms": []}
                words[word_no] = w
            w["forms"].append(form)
            if "STEM" in feats:
                lem = _LEM_RE.search(feats)
                pos = _POS_RE.search(feats)
                if lem:
                    w["stem_lem"] = lem.group(1)
                    w["stem_pos"] = pos.group(1) if pos else None
    # Order words by their index within each verse.
    return {
        key: [words[i] for i in sorted(words)]
        for key, words in verses.items()
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill QuranicVerseWord from QAC")
    ap.add_argument("--qac", type=Path, default=QAC_DEFAULT_PATH)
    ap.add_argument("--apply", action="store_true", help="Write to DB (default: dry-run)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run

    qac = parse_qac_words(args.qac)
    print(f"QAC verses parsed: {len(qac)}")

    db = SessionLocal()
    try:
        lemma_lookup = build_comprehensive_lemma_lookup(db)
        lemmas_by_id = {l.lemma_id: l for l in db.query(Lemma).all()}
        verses = {(v.surah, v.ayah): v for v in db.query(QuranicVerse).all()}
        print(f"DB verses: {len(verses)}")

        stats = {"verses": 0, "aligned": 0, "misaligned": 0, "words": 0,
                 "content": 0, "mapped": 0, "function": 0, "unmapped_content": 0}
        lemma_resolve_cache: dict[tuple, tuple] = {}

        if apply:
            # Rebuild all word rows from the gold QAC source.
            db.query(QuranicVerseWord).delete()
            db.commit()

        now = datetime.now(timezone.utc)
        batch = 0
        for (surah, ayah), words in qac.items():
            verse = verses.get((surah, ayah))
            if verse is None:
                continue
            stats["verses"] += 1
            our_tokens = (verse.arabic_text or "").split()
            use_our = len(our_tokens) == len(words)
            stats["aligned" if use_our else "misaligned"] += 1

            for idx, w in enumerate(words):
                stats["words"] += 1
                surface = our_tokens[idx] if use_our else "".join(_BW2AR(f) for f in w["forms"])
                stem_pos = w["stem_pos"]
                lemma_id = None
                is_func = True
                if w["stem_lem"] and stem_pos in _CONTENT_POS:
                    is_func = False
                    stats["content"] += 1
                    ck = (w["stem_lem"], stem_pos)
                    if ck not in lemma_resolve_cache:
                        lemma_resolve_cache[ck] = resolve_qac_lemma(
                            w["stem_lem"], stem_pos, lemma_lookup, lemmas_by_id)
                    lemma_id = lemma_resolve_cache[ck][0]
                    if lemma_id is not None:
                        stats["mapped"] += 1
                    else:
                        stats["unmapped_content"] += 1
                else:
                    stats["function"] += 1

                if apply:
                    db.add(QuranicVerseWord(
                        verse_id=verse.id, position=idx + 1, surface_form=surface,
                        lemma_id=lemma_id, is_function_word=is_func,
                    ))
                    batch += 1
            if apply:
                verse.lemmatized_at = now
                if batch >= 2000:
                    db.commit()
                    batch = 0
        if apply:
            db.commit()

        print("\n=== RESULT ===")
        for k, v in stats.items():
            print(f"  {k:18}: {v}")
        content = max(stats["content"], 1)
        print(f"  content lemma coverage: {stats['mapped']}/{stats['content']} "
              f"= {stats['mapped']/content*100:.1f}%")
        word_total = max(stats["words"], 1)
        readable = stats["function"] + stats["mapped"]
        print(f"  token coverage (function + mapped): {readable}/{stats['words']} "
              f"= {readable/word_total*100:.1f}%")
        print(f"  verses with token-count mismatch (used QAC surface): {stats['misaligned']}")
        if apply:
            print(f"\nApplied. QuranicVerseWord rows: "
                  f"{db.query(QuranicVerseWord).count()}")
        else:
            print("\n[dry-run] no rows written. Re-run with --apply.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
