#!/usr/bin/env python3
"""Phase 1 awkward-sentence sweep — retire sentences that fail the
corpus regex pre-filter (anaphoric openers, missing terminal
punctuation, dialogue-only, demonstrative/pronoun subject).

Scans ALL is_active=True sentences regardless of source: regex catches
fragment-shaped sentences whether the LLM, a textbook OCR, or the
Hindawi import produced them. Retired sentences are flipped to
is_active=False; we leave the rows in place so existing review-log
references stay intact.

Usage:
    # Dry run (no writes), shows per-source breakdown
    python3 scripts/retire_corpus_fragments.py

    # Apply (sets is_active=False, writes ContentFlag + ActivityLog)
    python3 scripts/retire_corpus_fragments.py --apply
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.database import SessionLocal
from app.models import ContentFlag, Sentence
from app.services.activity_log import log_activity
from app.services.sentence_quality import fails_corpus_regex_filter

BATCH_SIZE = 500


def main():
    parser = argparse.ArgumentParser(description="Retire awkward sentences via regex sweep")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually retire sentences. Default is dry-run (no writes).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap how many sentences are scanned (debug aid).",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        q = db.query(Sentence.id, Sentence.arabic_text, Sentence.source).filter(
            Sentence.is_active == True  # noqa: E712
        )
        if args.limit:
            q = q.limit(args.limit)
        rows = q.all()
        print(f"Scanning {len(rows):,} active sentences...")

        # Bucket by (rule, source) and collect ids to retire.
        per_rule: dict[str, int] = {}
        per_source: dict[str, int] = {}
        per_source_rule: dict[tuple[str, str], int] = {}
        to_retire: list[tuple[int, str, str, str]] = []  # (id, rule, source, text)

        for sid, arabic, source in rows:
            fail, rule = fails_corpus_regex_filter(arabic or "")
            if not fail:
                continue
            src = source or "(none)"
            per_rule[rule] = per_rule.get(rule, 0) + 1
            per_source[src] = per_source.get(src, 0) + 1
            per_source_rule[(src, rule)] = per_source_rule.get((src, rule), 0) + 1
            to_retire.append((sid, rule, src, arabic or ""))

        # ── Reporting ────────────────────────────────────────────────
        print(f"\nFlagged {len(to_retire):,} sentences "
              f"({100*len(to_retire)/max(len(rows),1):.1f}% of active)")

        print("\nPer-rule:")
        for rule, n in sorted(per_rule.items(), key=lambda x: -x[1]):
            print(f"  {rule:30s} {n:>6,}")

        print("\nPer-source:")
        for src, n in sorted(per_source.items(), key=lambda x: -x[1]):
            print(f"  {src:30s} {n:>6,}")

        print("\nPer-source x rule:")
        for (src, rule), n in sorted(per_source_rule.items(), key=lambda x: -x[1]):
            print(f"  {src:15s} {rule:30s} {n:>6,}")

        print("\nFirst 30 flagged:")
        for sid, rule, src, text in to_retire[:30]:
            preview = text.replace("\n", " ")[:60]
            print(f"  [{sid:>6}] [{src:8s}] [{rule:25s}] {preview}")

        if not args.apply:
            print("\n[DRY RUN] No writes. Re-run with --apply to retire.")
            return

        # ── Apply ────────────────────────────────────────────────────
        print(f"\nApplying retirement to {len(to_retire):,} sentences in batches of {BATCH_SIZE}...")
        now = datetime.now(timezone.utc)
        retired = 0
        for batch_start in range(0, len(to_retire), BATCH_SIZE):
            batch = to_retire[batch_start : batch_start + BATCH_SIZE]
            ids = [sid for sid, _, _, _ in batch]
            # Flip is_active in one UPDATE per batch.
            db.query(Sentence).filter(Sentence.id.in_(ids)).update(
                {Sentence.is_active: False},
                synchronize_session=False,
            )
            # Drop a ContentFlag breadcrumb so the retirement reason is
            # visible from the in-app flags surface.
            for sid, rule, _src, _text in batch:
                db.add(
                    ContentFlag(
                        content_type="sentence_arabic",
                        sentence_id=sid,
                        status="dismissed",
                        resolution_note=f"auto-retired by regex: {rule}",
                        created_at=now,
                        resolved_at=now,
                    )
                )
            db.commit()
            retired += len(batch)
            print(f"  ... committed {retired:,}/{len(to_retire):,}")

        # Activity log summary.
        per_source_str = " ".join(f"{src}={n}" for src, n in sorted(per_source.items()))
        log_activity(
            db,
            event_type="sentences_retired",
            summary=f"Phase 1 regex sweep: retired {retired} sentences ({per_source_str})",
            detail={
                "phase": "1_corpus_regex",
                "retired": retired,
                "scanned": len(rows),
                "per_rule": per_rule,
                "per_source": per_source,
            },
        )
        print(f"\nDone. Retired {retired:,} sentences.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
