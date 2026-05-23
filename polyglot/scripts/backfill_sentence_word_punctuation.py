"""Backfill punctuation rows for stored generated review sentences.

Early Polyglot generated sentences kept punctuation in ``Sentence.text`` but
stored only content tokens in ``SentenceWord``. That made the reusable review
payload incomplete. This script rebuilds ``SentenceWord`` rows from the stored
sentence text, preserving the existing content-token lemma mappings and adding
punctuation/layout tokens as ``lemma_id=NULL``.

Usage::

    .venv/bin/python scripts/backfill_sentence_word_punctuation.py --dry-run
    .venv/bin/python scripts/backfill_sentence_word_punctuation.py --source llm
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models import Sentence, SentenceWord
from app.services.sentence_validator import (
    is_punctuation_surface,
    normalize_bare,
    tokenize_display,
)


def _content_words(words: list[SentenceWord]) -> list[SentenceWord]:
    return [w for w in words if not is_punctuation_surface(w.surface_form)]


def _rebuild_sentence_words(db, sentence: Sentence, *, dry_run: bool) -> str:
    existing = (
        db.query(SentenceWord)
        .filter(SentenceWord.sentence_id == sentence.id)
        .order_by(SentenceWord.position.asc())
        .all()
    )
    existing_content = _content_words(existing)
    display_tokens = tokenize_display(sentence.text, sentence.language_code)
    display_content = [
        (pos, surface)
        for pos, surface in display_tokens
        if not is_punctuation_surface(surface)
    ]

    if len(existing_content) != len(display_content):
        return "content_count_mismatch"

    for old, (_, surface) in zip(existing_content, display_content):
        old_bare = normalize_bare(old.surface_form, sentence.language_code)
        new_bare = normalize_bare(surface, sentence.language_code)
        if old_bare != new_bare:
            return "content_surface_mismatch"
    content_snapshots = [
        {
            "lemma_id": old.lemma_id,
            "is_target_word": bool(old.is_target_word),
            "grammar_role_json": old.grammar_role_json,
        }
        for old in existing_content
    ]

    existing_shape = [
        (w.position, w.surface_form, w.lemma_id, bool(w.is_target_word))
        for w in existing
    ]
    rebuilt_shape = []
    content_iter = iter(content_snapshots)
    for pos, surface in display_tokens:
        if is_punctuation_surface(surface):
            rebuilt_shape.append((pos, surface, None, False))
        else:
            old = next(content_iter)
            rebuilt_shape.append((pos, surface, old["lemma_id"], old["is_target_word"]))

    if existing_shape == rebuilt_shape:
        return "unchanged"

    if dry_run:
        return "would_update"

    db.query(SentenceWord).filter(SentenceWord.sentence_id == sentence.id).delete(
        synchronize_session=False,
    )
    content_iter = iter(content_snapshots)
    for pos, surface in display_tokens:
        if is_punctuation_surface(surface):
            db.add(SentenceWord(
                sentence_id=sentence.id,
                position=pos,
                surface_form=surface,
                lemma_id=None,
                is_target_word=False,
            ))
        else:
            old = next(content_iter)
            db.add(SentenceWord(
                sentence_id=sentence.id,
                position=pos,
                surface_form=surface,
                lemma_id=old["lemma_id"],
                is_target_word=old["is_target_word"],
                grammar_role_json=old["grammar_role_json"],
            ))
    return "updated"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill punctuation SentenceWord rows from Sentence.text.",
    )
    parser.add_argument("--source", default="llm",
                        help="Sentence.source to repair. Default: llm")
    parser.add_argument("--language", default="el",
                        help="Language code. Default: el")
    parser.add_argument("--limit", type=int, default=None,
                        help="Only inspect this many sentences.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report changes without writing.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("backfill_sentence_word_punctuation")

    db = SessionLocal()
    summary = {
        "language_code": args.language,
        "source": args.source,
        "dry_run": args.dry_run,
        "examined": 0,
        "updated": 0,
        "would_update": 0,
        "unchanged": 0,
        "skipped": {},
    }
    try:
        q = (
            db.query(Sentence)
            .filter(Sentence.language_code == args.language)
            .filter(Sentence.source == args.source)
            .filter(Sentence.is_active.is_(True))
            .order_by(Sentence.id.asc())
        )
        if args.limit is not None:
            q = q.limit(args.limit)
        for sentence in q.all():
            summary["examined"] += 1
            status = _rebuild_sentence_words(db, sentence, dry_run=args.dry_run)
            if status == "updated":
                summary["updated"] += 1
            elif status == "would_update":
                summary["would_update"] += 1
                log.info("[dry-run] would update sentence %d: %s", sentence.id, sentence.text)
            elif status == "unchanged":
                summary["unchanged"] += 1
            else:
                summary["skipped"][status] = summary["skipped"].get(status, 0) + 1
                log.warning("Skipping sentence %d (%s): %s", sentence.id, status, sentence.text)
        if not args.dry_run:
            db.commit()
            summary["committed_at"] = datetime.now(timezone.utc).isoformat()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if not summary["skipped"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
