"""Move five imported lemmas to their correct root (2026-11-26 import audit).

أَحْصَى was filed under أ.ح.ص, تَشَاوَرَ and اِسْتِشَارِيّ under ش.ي.ر, اِنْتَابَ under ن.ت.ب
and أَرِيكَة under ر.ك.ي. Roots go through find_or_create_root (hamza-safe). The word
cards of the moved lemmas are rebuilt, since their root list changes.

    PYTHONPATH=/opt/limbic python3 scripts/fix_roots_2026_11_26.py           # dry run
    PYTHONPATH=/opt/limbic python3 scripts/fix_roots_2026_11_26.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models import Lemma
from app.services.morphology import find_or_create_root

# lemma_id -> (correct root, core meaning used only if the root is new)
MOVES = {
    4305: ("ح.ص.ي", "counting, enumerating (from pebbles used to count)"),
    4307: ("ش.و.ر", ""),
    4993: ("ش.و.ر", ""),
    4362: ("ن.و.ب", ""),
    4367: ("أ.ر.ك", "couch, sofa; reclining"),
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    db = SessionLocal()
    for lid, (root_str, meaning) in MOVES.items():
        lemma = db.get(Lemma, lid)
        old = lemma.root.root if lemma.root else None
        root = find_or_create_root(db, root_str, meaning)
        db.flush()
        print(f"{lid} {lemma.lemma_ar}: {old} -> {root.root} (root_id {root.root_id})")
        lemma.root_id = root.root_id
        lemma.word_card_json = None
    if not args.apply:
        db.rollback()
        print("dry run: nothing written")
        return
    db.commit()

    from app.services.activity_log import log_activity
    from app.services.word_card import enrich_word_cards_for

    print("word cards:", enrich_word_cards_for(db, list(MOVES)))
    log_activity(db, "manual_action", f"Moved {len(MOVES)} lemmas to their correct root (2026-11-26 audit)",
                 {"script": Path(__file__).name, "lemma_ids": list(MOVES)})
    db.close()


if __name__ == "__main__":
    main()
