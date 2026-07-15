#!/usr/bin/env python3
"""Import authentic Momo sentences as corpus material — carefully.

Thin wrapper over the Hindawi corpus pipeline (`import_hindawi.py`) for a
page-marked OCR text file (`=== jNNN-NNN ===` markers). Care layers:

  1. Sentences are extracted PER PAGE — nothing spans a page break.
  2. The inherited Hindawi filters: 5-14 words, fragment regex pre-filter,
     strict all-mapped rule (every content token must resolve to a lemma —
     OCR-corrupted tokens are unmappable, so corrupted sentences self-exclude),
     >=2 content lemmas, dedup vs existing sentences.
  3. Momo-purpose filter: sentence must contain >=1 lemma imported for the
     book (default `--require-source bookifier`).
  4. Rows land is_active=False, unverified, untranslated — invisible to review
     until the update_material cron diacritizes/translates/LLM-verifies them.
  5. Serve-time: the book/corpus acquiring-gate in sentence_selector keeps
     these away from Box-1 words (durable user rule, 2026-05-26).

Usage:
  cd backend
  python3 scripts/import_momo_corpus.py --text-file ../tmp/momo_full_2026-07-15.txt --analyze
  python3 scripts/import_momo_corpus.py --text-file ../tmp/momo_full_2026-07-15.txt --import
"""

import argparse
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import Lemma, Sentence, SentenceWord  # noqa: E402
from app.services.proper_name_lemmas import get_or_create_proper_name_lemma  # noqa: E402
from app.services.sentence_quality import fails_corpus_regex_filter  # noqa: E402
from app.services.sentence_validator import (  # noqa: E402
    normalize_alef,
    strip_diacritics,
    strip_punctuation,
    strip_tatweel,
)
from app.services.transliteration import transliterate_arabic  # noqa: E402
from scripts.import_hindawi import (  # noqa: E402
    build_lemma_lookup,
    build_name_set,
    extract_sentences,
    get_existing_arabic_texts,
    map_tokens_to_lemmas,
    tokenize_display,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

PAGE_MARKER = re.compile(r"=== j\d+-\d+ ===")


def run(text_path: str, require_source: str | None, min_words: int,
        max_words: int, analyze_only: bool, limit: int | None,
        dump_path: str | None = None, keep_path: str | None = None):
    raw = Path(text_path).read_text(encoding="utf-8")
    pages = [p.strip() for p in PAGE_MARKER.split(raw) if p.strip()]
    logger.info(f"{len(pages)} pages of text")

    all_sentences: list[dict] = []
    for page in pages:
        for s in extract_sentences(page, min_words, max_words):
            all_sentences.append({"text": s, "title": "Momo", "author": "Michael Ende"})
    logger.info(f"Extracted {len(all_sentences):,} candidates ({min_words}-{max_words} words, per-page)")

    rule_counts: dict[str, int] = {}
    kept = []
    for s in all_sentences:
        fail, rule = fails_corpus_regex_filter(s["text"])
        if fail:
            rule_counts[rule] = rule_counts.get(rule, 0) + 1
            continue
        kept.append(s)
    logger.info(f"Regex pre-filter removed {len(all_sentences) - len(kept):,} "
                f"({' '.join(f'{k}={v}' for k, v in sorted(rule_counts.items()))})")
    all_sentences = kept

    db = SessionLocal()
    try:
        all_lemmas = db.query(Lemma).filter(Lemma.canonical_lemma_id.is_(None)).all()
        lookup = build_lemma_lookup(all_lemmas)
        required_ids: set[int] = set()
        if require_source:
            required_ids = {
                l.lemma_id for l in all_lemmas if l.source == require_source
            }
            logger.info(f"Momo-purpose filter: sentence must contain one of "
                        f"{len(required_ids)} '{require_source}' lemmas")
        names = build_name_set(all_sentences, lookup)
        existing = get_existing_arabic_texts(db)

        accepted, rejected_unmapped, rejected_no_target, dedup = [], 0, 0, 0
        for s in all_sentences:
            text_norm = normalize_alef(strip_diacritics(s["text"]))
            if text_norm in existing:
                dedup += 1
                continue
            tokens = tokenize_display(s["text"])
            mappings = map_tokens_to_lemmas(
                tokens=tokens, lemma_lookup=lookup, target_lemma_id=0,
                target_bare="", proper_names=names,
            )
            has_unmapped = False
            mapped_ids: set[int] = set()
            for m in mappings:
                if m.is_function_word or m.is_proper_name:
                    continue
                bare = normalize_alef(strip_diacritics(strip_punctuation(
                    strip_tatweel(m.surface_form))))
                if not bare or len(bare) <= 1:
                    continue
                if m.lemma_id is None:
                    has_unmapped = True
                    break
                mapped_ids.add(m.lemma_id)
            if has_unmapped:
                rejected_unmapped += 1
                continue
            if len(mapped_ids) < 2:
                rejected_unmapped += 1
                continue
            if required_ids and not (mapped_ids & required_ids):
                rejected_no_target += 1
                continue
            existing.add(text_norm)
            accepted.append({"text": s["text"], "mappings": mappings,
                             "lemma_ids": mapped_ids})

        logger.info(f"\nAccepted: {len(accepted):,} | rejected unmapped/thin: "
                    f"{rejected_unmapped:,} | no Momo target: {rejected_no_target:,} "
                    f"| dupes: {dedup:,}")
        covered = set()
        for s in accepted:
            covered.update(s["lemma_ids"] & required_ids)
        if required_ids:
            logger.info(f"Momo lemmas covered by accepted sentences: "
                        f"{len(covered)}/{len(required_ids)}")
        import random
        random.seed(42)
        for s in random.sample(accepted, min(12, len(accepted))):
            logger.info(f"  {s['text']}")

        if dump_path:
            import json
            with open(dump_path, "w", encoding="utf-8") as f:
                for i, s in enumerate(accepted):
                    f.write(json.dumps({"i": i, "text": s["text"]}, ensure_ascii=False) + "\n")
            logger.info(f"Dumped {len(accepted)} accepted sentences to {dump_path}")
        if keep_path:
            keep_texts = {
                __import__("json").loads(line)["text"]
                for line in open(keep_path, encoding="utf-8") if line.strip()
            }
            before = len(accepted)
            accepted = [s for s in accepted if s["text"] in keep_texts]
            logger.info(f"Keep-file filter: {before} -> {len(accepted)}")
        if analyze_only:
            logger.info("\n[Analyze mode — no DB writes]")
            return
        if limit:
            accepted = accepted[:limit]

        now = datetime.now(timezone.utc)
        created = 0
        for s in accepted:
            target_lid = None
            for m in s["mappings"]:
                if (m.lemma_id and m.lemma_id in required_ids
                        and not m.is_function_word and not m.is_proper_name):
                    target_lid = m.lemma_id
                    break
            if target_lid is None:
                for m in s["mappings"]:
                    if m.lemma_id and not m.is_function_word and not m.is_proper_name:
                        target_lid = m.lemma_id
                        break
            sent = Sentence(
                arabic_text=s["text"],
                english_translation=None,  # translated on-demand by cron
                transliteration=transliterate_arabic(s["text"]) or "",
                source="corpus",
                kind="momo_book",
                target_lemma_id=target_lid,
                created_at=now,
                mappings_verified_at=None,  # enriched on-demand by cron step A2
                is_active=False,  # activated after enrichment
            )
            db.add(sent)
            db.flush()
            for m in s["mappings"]:
                lid = m.lemma_id if m.lemma_id and m.lemma_id != 0 else None
                if lid is None and m.is_proper_name:
                    lid = get_or_create_proper_name_lemma(
                        db, m.surface_form, source="corpus")
                db.add(SentenceWord(
                    sentence_id=sent.id, position=m.position,
                    surface_form=m.surface_form, lemma_id=lid,
                    is_target_word=(lid == target_lid and lid is not None),
                ))
            created += 1
            if created % 200 == 0:
                db.commit()
        db.commit()
        logger.info(f"Done: {created} sentences (source='corpus', kind='momo_book', inactive until enrichment)")
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser(description="Import Momo corpus sentences")
    ap.add_argument("--text-file", required=True)
    ap.add_argument("--require-source", default="bookifier",
                    help="Sentence must contain a lemma with this source ('' disables)")
    ap.add_argument("--min-words", type=int, default=5)
    ap.add_argument("--max-words", type=int, default=14)
    ap.add_argument("--analyze", action="store_true")
    ap.add_argument("--import", dest="do_import", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dump-jsonl", help="Write accepted sentences to this JSONL for manual vetting")
    ap.add_argument("--keep-file", help="JSONL of vetted sentences; only these are imported")
    args = ap.parse_args()
    if not args.analyze and not args.do_import:
        ap.error("Must specify --analyze or --import")
    run(args.text_file, args.require_source or None, args.min_words,
        args.max_words, args.analyze, args.limit,
        dump_path=args.dump_jsonl, keep_path=args.keep_file)


if __name__ == "__main__":
    main()
