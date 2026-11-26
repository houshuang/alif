"""Move stored word links onto the lemma the word actually is
(services/lemma_remap.py), over sentence, story and Quran words and all lemma
sources. Runs automatically as Step 0c of the material cron; this runs it by
hand. Dry run unless --apply; a dry run still judges unjudged moves (stored in
lemma_remap_verdicts) unless --no-judge.

    PYTHONPATH=. python3 scripts/remap_stale_links.py [--apply] [--judge-limit N] [--no-judge]
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

os.environ.setdefault("ALIF_SKIP_MIGRATIONS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.services import lemma_remap  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--judge-limit", type=int, default=None)
    ap.add_argument("--no-judge", action="store_true", help="only list candidate moves")
    ap.add_argument("--no-reverify", action="store_true")
    args = ap.parse_args()

    if args.no_judge:
        db = SessionLocal()
        try:
            moves, lemmas = lemma_remap.find_moves(db)
        finally:
            db.close()
        print(f"{len(moves)} candidate moves, {len({m.key for m in moves})} distinct")
        for (old, new), n in Counter((m.old, m.new) for m in moves).most_common(40):
            print(f"  {n:4} {lemmas[old].lemma_ar} ({(lemmas[old].gloss_en or '')[:24]}) -> "
                  f"{lemmas[new].lemma_ar} ({(lemmas[new].gloss_en or '')[:24]})")
        return
    stats = lemma_remap.run(apply=args.apply, judge_limit=args.judge_limit,
                            reverify=not args.no_reverify)
    print(stats)


if __name__ == "__main__":
    main()
