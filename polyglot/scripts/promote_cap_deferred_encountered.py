"""Promote cap-deferred encountered ULKs back to acquiring.

Recovers from the 2026-05-20 incident where the daily intro cap silently
downgraded user-driven reading-screen red taps to ``encountered`` instead of
``acquiring``. After the cap-bypass change (CAP_EXEMPT_SOURCES includes
``reading_intake``), this script catches up the rows the old logic stranded.

Selection: rows in ``knowledge_state='encountered'`` whose ``source`` is in the
cap-exempt set (default: ``reading_intake``, ``leech_reintro``) and which have
no review activity yet (``times_seen=0``). These are the rows that exist only
because of the cap; they should never have been encountered in the first
place.

Each promoted row gets:
- ``knowledge_state='acquiring'``
- ``acquisition_box=1``, ``acquisition_next_due=now`` (due immediately)
- ``acquisition_started_at=now``, ``entered_acquiring_at=now``,
  ``introduced_at=now``

Usage::

    .venv/bin/python scripts/promote_cap_deferred_encountered.py --dry-run
    .venv/bin/python scripts/promote_cap_deferred_encountered.py --apply

Idempotent: re-running after ``--apply`` is a no-op once all eligible rows
have been promoted.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge
from app.services.acquisition_service import CAP_EXEMPT_SOURCES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true",
                   help="Write changes. Default is dry-run.")
    p.add_argument("--dry-run", action="store_true", help="Preview only (default).")
    p.add_argument("--language", default=None,
                   help="Limit to one language code (e.g. 'el').")
    p.add_argument("--sources", nargs="*", default=sorted(CAP_EXEMPT_SOURCES),
                   help=f"Source filter. Default: {sorted(CAP_EXEMPT_SOURCES)}")
    args = p.parse_args()

    apply = args.apply and not args.dry_run

    db = SessionLocal()
    try:
        q = (
            db.query(UserLemmaKnowledge, Lemma)
            .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
            .filter(
                UserLemmaKnowledge.knowledge_state == "encountered",
                UserLemmaKnowledge.source.in_(args.sources),
                (UserLemmaKnowledge.times_seen == 0) | (UserLemmaKnowledge.times_seen.is_(None)),
            )
        )
        if args.language:
            q = q.filter(Lemma.language_code == args.language)
        rows = q.all()

        log.info("Found %d eligible encountered ULKs", len(rows))
        for ulk, lemma in rows:
            log.info("  lemma_id=%d (%s, %s) source=%s",
                     lemma.lemma_id, lemma.language_code, lemma.lemma_bare, ulk.source)

        if not apply:
            log.info("Dry-run: no changes written. Pass --apply to promote.")
            return 0

        now = datetime.now(timezone.utc)
        for ulk, _lemma in rows:
            ulk.knowledge_state = "acquiring"
            ulk.acquisition_box = 1
            ulk.acquisition_next_due = now
            ulk.acquisition_started_at = now
            ulk.entered_acquiring_at = now
            ulk.introduced_at = now
        db.commit()
        log.info("Promoted %d ULKs to acquiring/Box1 due now", len(rows))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
