#!/usr/bin/env python3
"""CLI wrapper around sentence_validator for use by Claude Code sessions.

Validates an Arabic sentence against a vocabulary lookup file, checking that
the target word is present and all other words are known.

Usage:
    python3 scripts/validate_sentence_cli.py \
      --arabic "الوَلَدُ يَقْرَأُ الكِتَابَ" \
      --target-bare "كتاب" \
      --vocab-file /tmp/claude/lookup.tsv

    # Multi-target validation
    python3 scripts/validate_sentence_cli.py \
      --arabic "الوَلَدُ يَقْرَأُ الكِتَابَ فِي البَيْتِ" \
      --target-bare "كتاب,بيت" \
      --vocab-file /tmp/claude/lookup.tsv

The vocab-file is a TSV with columns: bare_form<TAB>lemma_id
Built by dump_vocabulary_for_claude() using the same build_lemma_lookup() logic.

Output: JSON on stdout with validation results.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.sentence_validator import (
    normalize_alef,
    validate_sentence,
    validate_sentence_multi_target,
)


def load_vocab_lookup(vocab_file: str) -> set[str]:
    """Load bare forms from a TSV lookup file into a set."""
    bare_forms: set[str] = set()
    with open(vocab_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if parts:
                bare_forms.add(parts[0])
    return bare_forms


def main():
    parser = argparse.ArgumentParser(description="Validate an Arabic sentence against known vocabulary")
    parser.add_argument("--arabic", required=True, help="Arabic sentence text (with or without diacritics)")
    parser.add_argument("--target-bare", required=True, help="Bare form(s) of target word(s), comma-separated for multi-target")
    parser.add_argument("--vocab-file", required=True, help="Path to TSV file with bare_form<TAB>lemma_id")
    args = parser.parse_args()

    known_bare_forms = load_vocab_lookup(args.vocab_file)
    targets = [t.strip() for t in args.target_bare.split(",") if t.strip()]

    if len(targets) == 1:
        result = validate_sentence(
            arabic_text=args.arabic,
            target_bare=targets[0],
            known_bare_forms=known_bare_forms,
        )
        output = {
            "valid": result.valid,
            "target_found": result.target_found,
            "unknown_words": result.unknown_words,
            "known_words": result.known_words,
            "function_words": result.function_words,
            "issues": result.issues,
        }
    else:
        target_bares = {t: i for i, t in enumerate(targets)}
        result = validate_sentence_multi_target(
            arabic_text=args.arabic,
            target_bares=target_bares,
            known_bare_forms=known_bare_forms,
        )
        output = {
            "valid": result.valid,
            "targets_found": result.targets_found,
            "target_count": result.target_count,
            "unknown_words": result.unknown_words,
            "known_words": result.known_words,
            "function_words": result.function_words,
            "issues": result.issues,
        }

    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
