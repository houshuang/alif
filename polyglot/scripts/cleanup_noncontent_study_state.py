"""Report or retire non-content lemmas from active study state.

The scheduler and sentence-review service intentionally skip function words,
proper names, and junk/OCR fragments. If old rows already sit in
``acquiring``/FSRS states, they can remain due forever because review credit is
not written for them. This script finds those rows and, with ``--apply``,
retires them to ``knowledge_state='ignore'``.

Usage::

    .venv/bin/python scripts/cleanup_noncontent_study_state.py --dry-run
    .venv/bin/python scripts/cleanup_noncontent_study_state.py --language el --apply
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models import Lemma, UserLemmaKnowledge  # noqa: E402
from app.services.lemma_quality import (
    FUNCTION_WORD_SETS,
    NONCONTENT_WORD_CATEGORIES,
    is_noncontent_lemma,
)  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ACTIVE_STUDY_STATES = ("acquiring", "learning", "known", "lapsed")


def _reason_for(lemma: Lemma) -> str:
    if lemma.word_category in NONCONTENT_WORD_CATEGORIES:
        return lemma.word_category or "noncontent_category"
    function_words = FUNCTION_WORD_SETS.get(lemma.language_code, set())
    if lemma.lemma_bare in function_words:
        return "function_word_bare"
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Write changes. Default is dry-run.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only (default).")
    parser.add_argument("--language", default=None,
                        help="Limit to one language code, e.g. 'el'.")
    args = parser.parse_args()

    apply = args.apply and not args.dry_run
    db = SessionLocal()
    try:
        q = (
            db.query(UserLemmaKnowledge, Lemma)
            .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
            .filter(UserLemmaKnowledge.knowledge_state.in_(ACTIVE_STUDY_STATES))
        )
        if args.language:
            q = q.filter(Lemma.language_code == args.language)

        rows = [
            (ulk, lemma)
            for ulk, lemma in q.all()
            if is_noncontent_lemma(lemma, language_code=lemma.language_code)
        ]

        by_state = Counter(ulk.knowledge_state for ulk, _lemma in rows)
        by_reason = Counter(_reason_for(lemma) for _ulk, lemma in rows)
        log.info("Found %d active non-content ULKs", len(rows))
        log.info("By state: %s", dict(sorted(by_state.items())))
        log.info("By reason: %s", dict(sorted(by_reason.items())))

        for ulk, lemma in rows:
            log.info(
                "  lemma_id=%d lang=%s bare=%s category=%s state=%s box=%s "
                "source=%s reason=%s",
                lemma.lemma_id,
                lemma.language_code,
                lemma.lemma_bare,
                lemma.word_category,
                ulk.knowledge_state,
                ulk.acquisition_box,
                ulk.source,
                _reason_for(lemma),
            )

        if not apply:
            log.info("Dry-run: no changes written. Pass --apply to retire rows.")
            return 0

        for ulk, _lemma in rows:
            ulk.knowledge_state = "ignore"
            ulk.acquisition_box = None
            ulk.acquisition_next_due = None
            ulk.fsrs_card_json = None
            ulk.leech_suspended_at = None
        db.commit()
        log.info("Retired %d active non-content ULKs to ignore", len(rows))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
