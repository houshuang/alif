"""Translate untranslated harvested book sentences for the configured language.

Standalone wrapper around ``material_generator.translate_untranslated_sentences``
so the cron job can call a Python script directly without going through the HTTP
endpoint. Exits 0 on success (including "nothing pending"), 1 on hard failure.

Book sentences are harvested with ``translation_en = NULL`` (harvest holds no
LLM call). The picker serves them as a graceful fallback when no generated
sentence covers a due lemma yet; this pass fills their English so a fallback
never reaches the screen blank. Runs lazily in the material cron, never on the
read path.

Usage:

    .venv/bin/python scripts/translate_sentences.py --language el --max-sentences 200

Env vars:

    POLYGLOT_TRANSLATE_MODEL        model override (default haiku)
    POLYGLOT_TRANSLATE_BATCH_SIZE   sentences per Claude call
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from app.services.material_generator import translate_untranslated_sentences


def main() -> int:
    parser = argparse.ArgumentParser(description="Translate untranslated polyglot book sentences.")
    parser.add_argument("--language", default="el", help="Language code (el/grc/la). Default: el")
    parser.add_argument("--max-sentences", type=int, default=200,
                        help="Max sentences to translate per pass. Default: 200")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    result = translate_untranslated_sentences(
        language_code=args.language,
        max_sentences=args.max_sentences,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
