#!/usr/bin/env python3
"""Check added words against the sentence they were added from.

Each Snap or pre-read add stores its sentence in ``word_add_contexts``; the
add path checks it in the background. This script checks any rows still
unchecked (a failed or interrupted check). ``broader`` widens the gloss;
``different`` is recorded for review (the enrolled word is not changed).

    python3 scripts/verify_add_contexts.py            # up to 50 unchecked rows
    python3 scripts/verify_add_contexts.py --limit 200
    python3 scripts/verify_add_contexts.py --report   # list 'different' verdicts
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import WordAddContext  # noqa: E402
from app.services.add_context import verify_add_contexts  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.report:
            for r in (db.query(WordAddContext).filter(WordAddContext.check_verdict == "different")
                      .order_by(WordAddContext.id.desc()).limit(args.limit)):
                j = r.check_json or {}
                print(f"#{r.id} lemma {r.lemma_id} {r.surface!r}: entered "
                      f"{j.get('gloss_from')!r}; sentence uses {j.get('lemma_ar')} {j.get('gloss_en')!r}")
            return
        ids = [r.id for r in db.query(WordAddContext.id).filter(WordAddContext.checked_at.is_(None))
               .order_by(WordAddContext.id).limit(args.limit)]
    finally:
        db.close()
    print(f"checking {len(ids)} rows")
    print(verify_add_contexts(ids))


if __name__ == "__main__":
    main()
