"""Run one philology-enrichment pass for the configured language.

Standalone wrapper around ``lemma_philology.batch_enrich`` so the cron job
can call a Python script directly without going through the HTTP endpoint.
Exits 0 on success (including "nothing to enrich"), 1 on hard failure.

Usage:

    .venv/bin/python scripts/enrich_lemma_philology.py --language el --max-lemmas 10
    .venv/bin/python scripts/enrich_lemma_philology.py --lemma-ids 1245 1247

By default, picks up to ``--max-lemmas`` un-enriched lemmas from the engaged-
vocabulary pool (those with a UserLemmaKnowledge row), ranked by frequency
rank ascending. Lemmas without ULK rows are skipped — no point philologizing
words the learner has never touched.

Env vars:

    POLYGLOT_ENRICH_MODEL                  Claude model (default: sonnet)
    POLYGLOT_ENRICH_TIMEOUT                CLI timeout in seconds (default: 240)
    POLYGLOT_ENRICH_BATCH_SIZE             lemmas per Sonnet call (default: 4)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from app.services.lemma_philology import batch_enrich, find_unenriched_lemmas


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich polyglot lemmas with philological data.")
    parser.add_argument("--language", default="el", help="Language code (el/grc/la). Default: el")
    parser.add_argument("--max-lemmas", type=int, default=10,
                        help="Max lemmas to enrich per pass. Default: 10")
    parser.add_argument("--lemma-ids", type=int, nargs="*", default=None,
                        help="Enrich exactly these lemma IDs (overrides --max-lemmas).")
    parser.add_argument("--include-failed", action="store_true",
                        help="Also pick lemmas whose previous enrichment failed.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.lemma_ids:
        ids = args.lemma_ids
    else:
        ids = find_unenriched_lemmas(
            language_code=args.language,
            limit=args.max_lemmas,
            include_failed=args.include_failed,
        )
    if not ids:
        print(json.dumps({"enriched": 0, "failed_lemma_ids": [], "skipped_lemma_ids": []}))
        return 0

    result = batch_enrich(language_code=args.language, lemma_ids=ids)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
