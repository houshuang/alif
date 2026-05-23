"""Backfill English translations for harvested textbook sentences."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from app.services.material_generator import translate_untranslated_sentences


def main() -> int:
    parser = argparse.ArgumentParser(description="Translate harvested Polyglot sentences.")
    parser.add_argument("--language", default="el", help="Language code (el/grc/la). Default: el")
    parser.add_argument("--max-sentences", type=int, default=200,
                        help="Max sentences to translate in this pass. Default: 200")
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
