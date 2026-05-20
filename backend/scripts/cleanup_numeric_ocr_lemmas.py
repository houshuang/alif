#!/usr/bin/env python3
"""Suspend + delete OCR-imported lemmas whose bare form is digits only.

Background: Gemini OCR routinely extracts page numbers, ISBN strings, and
footnote markers (e.g. ١٤, ٨٢٦١٤٩٣٥) as "Arabic words". These slipped past
the OCR sanitize step in `sentence_validator.sanitize_arabic_word` because
the pre-2026-05-20 implementation only filtered length-1 strings, not
letter-free strings. They surface in Stats under "NEW WORDS STARTED" with
an English gloss like "14" — visibly nonsensical.

The import-side fix (`no_letters` warning) prevents new ones from being
created. This script cleans up the existing ones.

For each numeric Lemma:
  1. Delete dependent UserLemmaKnowledge rows.
  2. Delete dependent ReviewLog rows (lemma_id is NOT NULL).
  3. NULL out nullable FK references: SentenceWord.lemma_id,
     StoryWord.lemma_id, Sentence.target_lemma_id,
     FrequencyCoreEntry.lemma_id, ContentFlag.lemma_id,
     QuranicVerseWord.lemma_id, Lemma.canonical_lemma_id
     (the storage / variant gates allow NULL; runtime gates hide the
     affected sentences until remapping).
  4. Hard-delete the Lemma row.

Activity log entry at the end.

Usage:
    python3 scripts/cleanup_numeric_ocr_lemmas.py            # dry-run
    python3 scripts/cleanup_numeric_ocr_lemmas.py --apply    # apply
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    ContentFlag, FrequencyCoreEntry, Lemma, QuranicVerseWord, ReviewLog,
    Sentence, SentenceWord, StoryWord, UserLemmaKnowledge,
)
from app.services.activity_log import log_activity  # noqa: E402


# ASCII 0-9, Arabic-Indic ٠-٩ (U+0660-U+0669), Extended ۰-۹ (U+06F0-U+06F9),
# plus common separators that show up in OCR'd numeric strings.
NUMERIC_ONLY = re.compile(r"^[0-9٠-٩۰-۹.,\-/]+$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Apply changes. Default is dry-run.")
    args = parser.parse_args()
    dry_run = not args.apply

    db = SessionLocal()
    try:
        lemmas = (
            db.query(Lemma)
            .filter(Lemma.lemma_ar_bare.isnot(None))
            .all()
        )
        hits = [l for l in lemmas if NUMERIC_ONLY.fullmatch(l.lemma_ar_bare)]

        if not hits:
            print("No numeric-only lemmas found. Nothing to clean.")
            return 0

        print(f"Found {len(hits)} numeric-only lemma(s):")
        counters = {
            "ulk": 0, "review_log": 0, "sentence_word": 0, "story_word": 0,
            "sentence_target": 0, "freq_core": 0, "content_flag": 0,
            "quran_verse_word": 0, "variant_pointers": 0,
        }
        for l in hits:
            row = {
                "ulk": db.query(UserLemmaKnowledge).filter(UserLemmaKnowledge.lemma_id == l.lemma_id).count(),
                "review_log": db.query(ReviewLog).filter(ReviewLog.lemma_id == l.lemma_id).count(),
                "sentence_word": db.query(SentenceWord).filter(SentenceWord.lemma_id == l.lemma_id).count(),
                "story_word": db.query(StoryWord).filter(StoryWord.lemma_id == l.lemma_id).count(),
                "sentence_target": db.query(Sentence).filter(Sentence.target_lemma_id == l.lemma_id).count(),
                "freq_core": db.query(FrequencyCoreEntry).filter(FrequencyCoreEntry.lemma_id == l.lemma_id).count(),
                "content_flag": db.query(ContentFlag).filter(ContentFlag.lemma_id == l.lemma_id).count(),
                "quran_verse_word": db.query(QuranicVerseWord).filter(QuranicVerseWord.lemma_id == l.lemma_id).count(),
                "variant_pointers": db.query(Lemma).filter(Lemma.canonical_lemma_id == l.lemma_id).count(),
            }
            for k, v in row.items():
                counters[k] += v
            print(
                f"  #{l.lemma_id}  bare={l.lemma_ar_bare!r}  ar={l.lemma_ar!r}  "
                f"gloss={l.gloss_en!r}  src={l.source}  "
                + "  ".join(f"{k}={v}" for k, v in row.items() if v)
            )
        print("\nTotal dependent rows: " + ", ".join(f"{k}={v}" for k, v in counters.items() if v))

        if dry_run:
            print("\nDry-run only. Re-run with --apply to delete.")
            return 0

        ids = [l.lemma_id for l in hits]

        # Delete required-FK rows first
        ulk_deleted = (
            db.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id.in_(ids))
            .delete(synchronize_session=False)
        )
        rl_deleted = (
            db.query(ReviewLog)
            .filter(ReviewLog.lemma_id.in_(ids))
            .delete(synchronize_session=False)
        )

        # NULL out nullable-FK references
        sw_nulled = (
            db.query(SentenceWord)
            .filter(SentenceWord.lemma_id.in_(ids))
            .update({SentenceWord.lemma_id: None}, synchronize_session=False)
        )
        story_nulled = (
            db.query(StoryWord)
            .filter(StoryWord.lemma_id.in_(ids))
            .update({StoryWord.lemma_id: None}, synchronize_session=False)
        )
        sent_nulled = (
            db.query(Sentence)
            .filter(Sentence.target_lemma_id.in_(ids))
            .update({Sentence.target_lemma_id: None}, synchronize_session=False)
        )
        fce_nulled = (
            db.query(FrequencyCoreEntry)
            .filter(FrequencyCoreEntry.lemma_id.in_(ids))
            .update({FrequencyCoreEntry.lemma_id: None}, synchronize_session=False)
        )
        cf_nulled = (
            db.query(ContentFlag)
            .filter(ContentFlag.lemma_id.in_(ids))
            .update({ContentFlag.lemma_id: None}, synchronize_session=False)
        )
        qvw_nulled = (
            db.query(QuranicVerseWord)
            .filter(QuranicVerseWord.lemma_id.in_(ids))
            .update({QuranicVerseWord.lemma_id: None}, synchronize_session=False)
        )
        canon_nulled = (
            db.query(Lemma)
            .filter(Lemma.canonical_lemma_id.in_(ids))
            .update({Lemma.canonical_lemma_id: None}, synchronize_session=False)
        )

        lemma_deleted = (
            db.query(Lemma)
            .filter(Lemma.lemma_id.in_(ids))
            .delete(synchronize_session=False)
        )
        db.commit()

        summary = {
            "lemma_ids": ids,
            "lemmas_deleted": lemma_deleted,
            "ulk_deleted": ulk_deleted,
            "review_log_deleted": rl_deleted,
            "sentence_word_nulled": sw_nulled,
            "story_word_nulled": story_nulled,
            "sentence_target_nulled": sent_nulled,
            "freq_core_nulled": fce_nulled,
            "content_flag_nulled": cf_nulled,
            "quran_verse_word_nulled": qvw_nulled,
            "variant_pointers_nulled": canon_nulled,
        }
        log_activity(
            db,
            event_type="manual_action",
            summary=f"Removed {lemma_deleted} numeric-only OCR lemmas",
            detail=summary,
        )
        print(f"\nApplied: {summary}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
