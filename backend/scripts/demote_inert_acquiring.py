"""Demote function-word and proper-name lemmas out of `acquiring` state.

Per CLAUDE.md, function words and proper names are inert in the SRS
pipeline — no FSRS card, no acquisition box, no review credit, no intro
card. But legacy book/textbook imports occasionally stamped a ULK row
with `knowledge_state="acquiring"` for these lemmas before the upstream
filter existed. Those rows then surface as intro cards in `_build_intro_cards`.

This script identifies them and resets `knowledge_state` to `encountered`,
clearing the acquisition box / next-due / generation backoff fields so
the lemma reverts to a passive scaffold helper. Run with --dry-run first.

Usage:
    python3 scripts/demote_inert_acquiring.py --dry-run
    python3 scripts/demote_inert_acquiring.py --apply

Logged via ActivityLog as `manual_action`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from app.models import Lemma, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.sentence_validator import _is_function_word


def find_inert_acquiring(db) -> list[tuple[UserLemmaKnowledge, Lemma, str]]:
    """Return (ulk, lemma, reason) tuples for acquiring rows that should not be acquiring."""
    rows = (
        db.query(UserLemmaKnowledge, Lemma)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(UserLemmaKnowledge.knowledge_state == "acquiring")
        .all()
    )
    out = []
    for ulk, lem in rows:
        if lem.word_category == "proper_name":
            out.append((ulk, lem, "proper_name"))
            continue
        if lem.lemma_ar_bare and _is_function_word(lem.lemma_ar_bare):
            out.append((ulk, lem, "function_word"))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Demote inert acquiring lemmas")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        targets = find_inert_acquiring(db)
        if not targets:
            print("No acquiring rows match the function-word / proper-name criteria.")
            return 0

        print(f"{len(targets)} ULK rows to demote:")
        for ulk, lem, reason in targets:
            print(
                f"  #{lem.lemma_id} ar={lem.lemma_ar!r} bare={lem.lemma_ar_bare!r} "
                f"gloss={(lem.gloss_en or '')[:30]!r} reason={reason} "
                f"times_seen={ulk.times_seen} times_correct={ulk.times_correct}"
            )

        if args.dry_run:
            print("\n--dry-run; no changes written. Re-run with --apply.")
            return 0

        for ulk, _lem, _reason in targets:
            ulk.knowledge_state = "encountered"
            ulk.acquisition_box = None
            ulk.acquisition_next_due = None
            ulk.acquisition_started_at = None
            # Intentionally NOT clearing experiment_intro_shown_at: keeping the
            # historical marker prevents an intro card from re-firing if a
            # future regression ever lets one of these back into acquiring.
        db.commit()

        log_activity(
            db,
            event_type="manual_action",
            summary=f"Demoted {len(targets)} inert lemmas (function_word/proper_name) from acquiring to encountered",
            detail={
                "lemma_ids": [t[1].lemma_id for t in targets],
                "by_reason": {
                    r: [t[1].lemma_id for t in targets if t[2] == r]
                    for r in {t[2] for t in targets}
                },
            },
        )
        print(f"\nDemoted {len(targets)} rows. Logged to ActivityLog.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
