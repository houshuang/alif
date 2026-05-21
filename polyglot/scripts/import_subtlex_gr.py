"""Import SUBTLEX-GR top-N lemmas as a scaffold for sentence generation.

Three independently-runnable phases:

  1. ``ingest``  parse data/frequency/SUBTLEX-GR_restricted.txt, lemmatize
                 each surface form via simplemma, aggregate per-lemma raw
                 counts, store top-N as FrequencyEntry(source='subtlex_gr').

  2. ``promote`` for each top-N FrequencyEntry without a backing Lemma,
                 create Lemma(source='frequency_core', frequency_rank=...),
                 stamp ``word_category`` ('function_word' / 'proper_name'
                 when applicable), link Modern↔Ancient cognates, batch-gloss
                 via Claude, stamp ``gates_completed_at``.

  3. ``cognates`` run ``detect_external_cognates`` over every promoted lemma
                 that lacks ``cognates_detected_at``. With the user profile's
                 ``cognate_auto_mark_threshold`` set to 'low' and
                 POLYGLOT_AUTO_MARK_COGNATES=1, this also stamps
                 UserLemmaKnowledge(state='known', source='cognate') inline.

Default ``all`` runs the three phases in order; any phase can be re-run
independently (each is idempotent).

Usage:

    .venv/bin/python scripts/import_subtlex_gr.py --top 5000
    .venv/bin/python scripts/import_subtlex_gr.py --phase ingest --top 5000
    .venv/bin/python scripts/import_subtlex_gr.py --phase promote
    .venv/bin/python scripts/import_subtlex_gr.py --phase cognates --batch 25
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
import time
from pathlib import Path

from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models import FrequencyEntry, Lemma, UserLemmaKnowledge, UserProfile  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402
from app.services.cognate_detector import (  # noqa: E402
    detect_external_cognates,
    link_intra_greek_cognates,
)
from app.services.languages.el import ModernGreekProvider  # noqa: E402
from app.services.lemma_gloss import ensure_glosses_batch  # noqa: E402
from app.services.lemma_quality import FUNCTION_WORD_SETS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("import_subtlex_gr")

LANG = "el"
SOURCE = "subtlex_gr"
DEFAULT_DATA_PATH = REPO_ROOT / "data" / "frequency" / "SUBTLEX-GR_restricted.txt"


# ─── Phase 1: ingest ──────────────────────────────────────────────────────

def _is_greek_word(s: str) -> bool:
    """At least one Greek letter, no Latin letters or digits in the form."""
    if not s:
        return False
    has_greek = any("Ͱ" <= ch <= "Ͽ" or "ἀ" <= ch <= "῿" for ch in s)
    has_latin_or_digit = any(("a" <= ch.lower() <= "z") or ch.isdigit() for ch in s)
    return has_greek and not has_latin_or_digit


def _looks_like_proper_name(surface: str, lemma: str) -> bool:
    """First letter uppercase in both surface and lemmatized form. SUBTLEX
    entries are word *types*, so dominant casing carries signal: ``Ααρών``
    is always capitalized in the corpus, vs ``θάλασσα`` always lowercase.
    """
    return bool(surface) and bool(lemma) and surface[0].isupper() and lemma[0].isupper()


def phase_ingest(db: Session, *, data_path: Path, top_n: int) -> int:
    """Parse SUBTLEX-GR, lemmatize, aggregate, store top-N as FrequencyEntry.

    Idempotent: deletes existing ``subtlex_gr`` rows before re-inserting.
    """
    if not data_path.exists():
        raise FileNotFoundError(f"SUBTLEX-GR file not found: {data_path}")

    provider = ModernGreekProvider()
    provider._ensure_simplemma()

    log.info("Parsing %s", data_path)
    agg: dict[str, dict] = {}  # lemma_bare → {display, count, is_proper}
    skipped_non_greek = 0
    skipped_low_count = 0
    rows_read = 0

    with data_path.open("r", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t", quotechar='"')
        header_seen = False
        col_word = col_freq = -1
        for row in reader:
            if not row or len(row) < 3:
                continue
            if not header_seen:
                if row[0].strip('"') == "ID" and any(c.strip('"') == "Word" for c in row):
                    col_word = next(i for i, c in enumerate(row) if c.strip('"') == "Word")
                    col_freq = next(i for i, c in enumerate(row) if c.strip('"') == "FREQcount")
                    header_seen = True
                continue
            try:
                surface = row[col_word].strip('"').strip()
                freq = int(row[col_freq])
            except (ValueError, IndexError):
                continue
            rows_read += 1
            if not _is_greek_word(surface):
                skipped_non_greek += 1
                continue
            if freq < 3:  # SUBTLEX-GR's restricted file already filters low counts
                skipped_low_count += 1
                continue
            cand = provider.lemmatize(surface)
            lemma = cand.lemma
            lemma_bare = cand.lemma_bare
            if not lemma_bare:
                continue
            entry = agg.get(lemma_bare)
            if entry is None:
                agg[lemma_bare] = {"display": lemma, "count": freq}
            else:
                entry["count"] += freq
                # Prefer the lowercase/canonical display if any surface lemmatized
                # to a lowercase form (handles "Ααρών"/"ααρών" co-occurrence).
                if lemma[0].islower() and entry["display"][0].isupper():
                    entry["display"] = lemma

    log.info("Read %d rows, %d aggregated lemmas (skipped %d non-Greek, %d low-count)",
             rows_read, len(agg), skipped_non_greek, skipped_low_count)

    ranked = sorted(agg.items(), key=lambda kv: kv[1]["count"], reverse=True)[:top_n]

    log.info("Clearing existing %s rows", SOURCE)
    db.query(FrequencyEntry).filter(
        FrequencyEntry.language_code == LANG,
        FrequencyEntry.source == SOURCE,
    ).delete(synchronize_session=False)
    db.commit()

    log.info("Writing top %d to frequency_entries", len(ranked))
    inserted = 0
    for rank, (lemma_bare, data) in enumerate(ranked, start=1):
        db.add(FrequencyEntry(
            language_code=LANG,
            source=SOURCE,
            rank=rank,
            lemma_key=lemma_bare,
            display_form=data["display"],
            count=data["count"],
        ))
        inserted += 1
        if inserted % 1000 == 0:
            db.commit()
            log.info("  ... %d/%d", inserted, len(ranked))
    db.commit()
    log.info("Ingested %d FrequencyEntry rows", inserted)
    return inserted


# ─── Phase 2: promote ─────────────────────────────────────────────────────

def _classify(lemma_bare: str, display: str) -> str | None:
    if lemma_bare in FUNCTION_WORD_SETS.get(LANG, set()):
        return "function_word"
    if display[:1].isupper():
        return "proper_name"
    return None


def phase_promote(db: Session, *, top_n: int, gloss_batch: int) -> tuple[int, int]:
    """Create Lemma rows for top-N FrequencyEntry rows that don't yet exist.

    Returns (created, linked_existing).
    """
    rows = (
        db.query(FrequencyEntry)
        .filter(FrequencyEntry.language_code == LANG,
                FrequencyEntry.source == SOURCE,
                FrequencyEntry.rank <= top_n)
        .order_by(FrequencyEntry.rank)
        .all()
    )
    log.info("Promoting up to %d FrequencyEntry rows", len(rows))

    created_ids: list[int] = []
    linked = 0
    for entry in rows:
        existing = (
            db.query(Lemma)
            .filter(Lemma.language_code == LANG, Lemma.lemma_bare == entry.lemma_key)
            .first()
        )
        if existing:
            if entry.lemma_id != existing.lemma_id:
                entry.lemma_id = existing.lemma_id
                linked += 1
            if existing.frequency_rank is None or existing.frequency_rank > entry.rank:
                existing.frequency_rank = entry.rank
            continue
        category = _classify(entry.lemma_key, entry.display_form)
        lemma = Lemma(
            language_code=LANG,
            lemma_form=entry.display_form,
            lemma_bare=entry.lemma_key,
            frequency_rank=entry.rank,
            source="frequency_core",
            word_category=category,
        )
        db.add(lemma)
        db.flush()
        link_intra_greek_cognates(db, lemma)
        entry.lemma_id = lemma.lemma_id
        if category is None:
            created_ids.append(lemma.lemma_id)
    db.commit()
    log.info("Created %d new Lemma rows, linked %d existing", len(created_ids), linked)

    log.info("Glossing %d new content lemmas in batches of %d", len(created_ids), gloss_batch)
    glossed = 0
    for i in range(0, len(created_ids), gloss_batch):
        chunk = created_ids[i:i + gloss_batch]
        try:
            glossed += ensure_glosses_batch(db, chunk)
        except Exception as e:
            log.warning("Gloss batch failed at offset %d: %s", i, e)
        if (i // gloss_batch) % 5 == 0:
            log.info("  ... glossed %d / %d", glossed, len(created_ids))

    # Stamp gates_completed_at for everything that has a gloss
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    stamped = (
        db.query(Lemma)
        .filter(Lemma.lemma_id.in_(created_ids),
                Lemma.gloss_en.isnot(None),
                Lemma.gates_completed_at.is_(None))
        .update({Lemma.gates_completed_at: now}, synchronize_session=False)
    )
    db.commit()
    log.info("Stamped gates_completed_at on %d lemmas", stamped)
    return len(created_ids), linked


# ─── Phase 3: external cognate detection ─────────────────────────────────

def phase_cognates(db: Session, *, batch: int) -> int:
    """Detect external L1 cognates for every gated content lemma that hasn't
    been checked yet. Auto-marks per the user profile when enabled.
    """
    targets = (
        db.query(Lemma)
        .filter(Lemma.language_code == LANG,
                Lemma.gates_completed_at.isnot(None),
                Lemma.cognates_detected_at.is_(None),
                Lemma.word_category.is_(None))  # skip function_word + proper_name
        .order_by(Lemma.frequency_rank.asc().nullslast())
        .all()
    )
    log.info("%d lemmas pending external cognate detection", len(targets))
    if not targets:
        return 0

    processed = 0
    t0 = time.time()
    for i in range(0, len(targets), batch):
        chunk = targets[i:i + batch]
        n = detect_external_cognates(
            db, chunk, force=True, auto_mark=True, batch_size=batch,
        )
        processed += n
        elapsed = time.time() - t0
        log.info("  ... %d / %d processed (%.1fs elapsed)", processed, len(targets), elapsed)
    return processed


# ─── CLI ──────────────────────────────────────────────────────────────────

def _ensure_profile_threshold(db: Session, threshold: str) -> None:
    profile = db.query(UserProfile).first()
    if profile is None:
        profile = UserProfile(cognate_auto_mark_threshold=threshold)
        db.add(profile)
    elif profile.cognate_auto_mark_threshold != threshold:
        log.info("Updating profile.cognate_auto_mark_threshold: %s → %s",
                 profile.cognate_auto_mark_threshold, threshold)
        profile.cognate_auto_mark_threshold = threshold
    db.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phase", choices=["ingest", "promote", "cognates", "all"], default="all")
    parser.add_argument("--top", type=int, default=5000, help="Top-N lemmas to import (default 5000)")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH,
                        help=f"Path to SUBTLEX-GR_restricted.txt (default {DEFAULT_DATA_PATH})")
    parser.add_argument("--gloss-batch", type=int, default=20)
    parser.add_argument("--cognate-batch", type=int, default=20)
    parser.add_argument("--threshold", choices=["high", "medium", "low", "never"], default="low",
                        help="Auto-mark transparency floor (default 'low')")
    parser.add_argument("--skip-threshold-update", action="store_true",
                        help="Don't touch UserProfile.cognate_auto_mark_threshold")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        if not args.skip_threshold_update:
            _ensure_profile_threshold(db, args.threshold)

        if args.phase in ("ingest", "all"):
            phase_ingest(db, data_path=args.data, top_n=args.top)
        if args.phase in ("promote", "all"):
            phase_promote(db, top_n=args.top, gloss_batch=args.gloss_batch)
        if args.phase in ("cognates", "all"):
            phase_cognates(db, batch=args.cognate_batch)

        if args.phase == "all":
            cog_ulks = (db.query(UserLemmaKnowledge)
                        .filter(UserLemmaKnowledge.source == "cognate").count())
            log_activity(
                db,
                event_type="frequency_seed_completed",
                summary=f"SUBTLEX-GR top-{args.top} import + cognate auto-mark "
                        f"(threshold={args.threshold}); {cog_ulks} cognate ULKs in pool",
                detail={"top_n": args.top, "threshold": args.threshold,
                        "cognate_ulks": cog_ulks},
                language_code=LANG,
            )
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
