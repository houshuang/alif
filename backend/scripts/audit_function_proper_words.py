#!/usr/bin/env python3
"""Audit function-word and proper-name handling for leaks and mis-categorization.

Checks whether the (sound) gates have residual data issues the gates can't fix
retroactively, and — more importantly — surfaces mis-categorization in BOTH
directions (the false-positive suppression that the LLM word-value judge should
own; see IDEAS.md "proper_name_vs_content"):

  1. pos=noun_prop but word_category != proper_name (drift). NOTE: not
     necessarily a leak — CAMeL mis-tags loanwords (papaya, jeans) as noun_prop,
     so these may be correctly-learnable content. Inspect before flipping.
  2. function-word lemmas carrying a learned ULK row (legacy pre-filter residue;
     mostly accurate — the user does know كان/محمد — at most a stats concern).
  3. proper_name lemmas carrying a ULK row — split into genuine names (residue)
     and content wrongly tagged proper_name (suppressed vocabulary, e.g. شديد).

Read-only. Usage:
  cd backend && PYTHONPATH=. python3 scripts/audit_function_proper_words.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("ALIF_SKIP_MIGRATIONS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func

from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge
from app.services.sentence_validator import _is_function_word, normalize_alef, strip_diacritics

LEARNED = ("known", "learning", "acquiring", "lapsed")


def main() -> None:
    db = SessionLocal()
    try:
        total_np = db.query(func.count(Lemma.lemma_id)).filter(Lemma.pos == "noun_prop").scalar()
        total_pn = db.query(func.count(Lemma.lemma_id)).filter(Lemma.word_category == "proper_name").scalar()
        drift = (db.query(Lemma).filter(Lemma.pos == "noun_prop")
                 .filter((Lemma.word_category.is_(None)) | (Lemma.word_category != "proper_name")).all())
        print(f"pos=noun_prop: {total_np}; word_category=proper_name: {total_pn}; drift: {len(drift)}")
        for l in drift:
            print(f"   #{l.lemma_id} {l.lemma_ar} cat={l.word_category!r} gloss={l.gloss_en!r} src={l.source}")

        rows = (db.query(Lemma, UserLemmaKnowledge.knowledge_state)
                .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
                .filter(UserLemmaKnowledge.knowledge_state.in_(LEARNED)).all())
        fw = [(l, s) for l, s in rows
              if _is_function_word(normalize_alef(strip_diacritics(l.lemma_ar_bare or l.lemma_ar or "")))]
        print(f"\nfunction-word lemmas with a learned ULK row: {len(fw)}")
        for l, s in fw:
            print(f"   #{l.lemma_id} {l.lemma_ar} pos={l.pos} state={s}")

        pn = (db.query(Lemma, UserLemmaKnowledge.knowledge_state)
              .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
              .filter(Lemma.word_category == "proper_name").all())
        print(f"\nproper_name lemmas with a ULK row: {len(pn)}")
        for l, s in pn:
            print(f"   #{l.lemma_id} {l.lemma_ar} pos={l.pos} gloss={l.gloss_en!r} state={s} src={l.source}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
