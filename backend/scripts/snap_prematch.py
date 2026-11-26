"""Run the Snap page pre-match (services/snap_prematch.py) on stored pages.

New pages get it in the background after OCR, and opening an older page starts
it; this runs it by hand, for pages snapped before it existed or to time it.
A "broader" verdict widens a stored gloss (compare-and-set), as on a tap.

    python3 scripts/snap_prematch.py --snap-id 16
    python3 scripts/snap_prematch.py --missing     # every page without a pass
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.models import SnapPage  # noqa: E402
from app.services.snap_prematch import needs_pass, run_prematch  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--snap-id", type=int, action="append")
    g.add_argument("--missing", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if args.missing:
            ids = [p.id for p in db.query(SnapPage).order_by(SnapPage.id)
                   if needs_pass(p.prematch_json)]
        else:
            ids = args.snap_id
    finally:
        db.close()

    for snap_id in ids:
        run_prematch(snap_id)
        db = SessionLocal()
        try:
            pm = db.get(SnapPage, snap_id).prematch_json or {}
        finally:
            db.close()
        print(snap_id, pm.get("status"), f'{pm.get("seconds")} s',
              f'{len(pm.get("words") or {})} surfaces', f'{pm.get("failed_checks")} failed checks',
              flush=True)


if __name__ == "__main__":
    main()
