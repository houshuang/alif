"""Run one warm-cache pass for the configured language.

Standalone wrapper around ``material_generator.warm_sentence_cache`` so the
cron job can call a Python script directly without going through the HTTP
endpoint. Exits 0 on success (including "no gaps"), 1 on hard failure.

Usage:

    .venv/bin/python scripts/warm_sentence_cache.py --language el --max-lemmas 16

Env vars:

    POLYGLOT_GEN_MODEL / POLYGLOT_VERIFY_MODEL    model overrides
    POLYGLOT_BATCH_WORD_SIZE                       targets per Sonnet call
    POLYGLOT_SENTENCES_PER_TARGET                  sentences per target
    POLYGLOT_ACTIVE_TARGET                         min active sentences per word
    POLYGLOT_COVERAGE_GEN                          1/0 toggle for Lever B coverage
    POLYGLOT_COVERAGE_MAX_LEMMAS                   assumed-known words planted/pass
    POLYGLOT_COVERAGE_TARGET                       reviewable-sentence floor (default 1)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from app.services.material_generator import warm_sentence_cache


def main() -> int:
    parser = argparse.ArgumentParser(description="Warm polyglot sentence cache.")
    parser.add_argument("--language", default="el", help="Language code (el/grc/la). Default: el")
    parser.add_argument("--max-lemmas", type=int, default=16,
                        help="Max retrieval-gap lemmas to fill per pass. Default: 16")
    parser.add_argument("--sentences-per-target", type=int, default=2,
                        help="Sentences to generate per target. Default: 2")
    parser.add_argument("--coverage-max-lemmas", type=int, default=None,
                        help="Max never-confirmed assumed-known words to plant "
                             "into the corpus per pass (Lever B coverage). "
                             "Default: POLYGLOT_COVERAGE_MAX_LEMMAS.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    warm_kwargs = dict(
        language_code=args.language,
        max_lemmas=args.max_lemmas,
        sentences_per_target=args.sentences_per_target,
    )
    if args.coverage_max_lemmas is not None:
        warm_kwargs["coverage_max_lemmas"] = args.coverage_max_lemmas
    result = warm_sentence_cache(**warm_kwargs)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
