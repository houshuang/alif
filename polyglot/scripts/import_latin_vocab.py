"""Seed the Latin vocabulary: DCC core + LLPSI Familia Romana + Roma Aeterna.

The Latin counterpart of ``import_subtlex_gr.py``. Three data sources, each a
delimited file (CSV or TSV) with a header row; columns are detected by fuzzy
name match so a DCC export, an LLPSI Anki TSV, or a hand-normalized list all
work without bespoke parsing. Recognized columns:

    lemma / headword / word / latin      → the citation form (required)
    gloss / definition / meaning / english → English gloss
    pos  / part(of speech)               → part of speech
    rank / frequency / freq / order      → numeric frequency rank (optional;
                                            falls back to row order)
    chapter / cap                        → LLPSI capitulum (optional, stored in notes)

Phases (each idempotent, runnable independently; ``all`` runs them in order):

  dcc            ingest the DCC core list as FrequencyEntry(source='dickinson_core')
                 — the frequency-rank backbone.
  roma_aeterna   ingest the Roma Aeterna (LLPSI 2) list as
                 FrequencyEntry(source='roma_aeterna') — the learn-frontier.
  promote        create Lemma rows (source='frequency_core') for DCC + RA
                 frequency entries that don't yet have one, carrying gloss/pos/
                 rank. These are the "to learn" pool; no ULK is created.
  llpsi          ingest LLPSI Familia Romana → create/link Lemma rows and mark
                 each ULK(knowledge_state='known', source='llpsi_known') with NO
                 FSRS card — assumed-known scaffold. Collateral exposure later
                 confirms (record_scaffold_confirmation) or a red miss lapses it
                 into acquisition. This is the "which words do I already know?"
                 seed.

Everything is scoped to language_code='la' and uses the language-scoped lemma
lookup, so a Latin import can never touch or match a Greek row.

Usage:
    .venv/bin/python scripts/import_latin_vocab.py --phase dcc --dcc-file data/vocab/dcc_core.tsv
    .venv/bin/python scripts/import_latin_vocab.py --phase llpsi --llpsi-file data/vocab/llpsi_fr.tsv
    .venv/bin/python scripts/import_latin_vocab.py --phase all \
        --dcc-file data/vocab/dcc_core.tsv \
        --llpsi-file data/vocab/llpsi_fr.tsv \
        --ra-file data/vocab/roma_aeterna.tsv
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models import FrequencyEntry, Lemma, UserLemmaKnowledge  # noqa: E402
from app.services.activity_log import log_activity  # noqa: E402
from app.services.canonical_resolution import resolve_canonical_lemma_id  # noqa: E402
from app.services.knowledge_lifecycle import ORIGIN_PRE_KNOWN  # noqa: E402
from app.services.languages.la import LatinProvider  # noqa: E402
from app.services.lemma_quality import FUNCTION_WORD_SETS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("import_latin_vocab")

LANG = "la"
SOURCE_DCC = "dickinson_core"
SOURCE_RA = "roma_aeterna"

_provider = LatinProvider()  # normalize_bare is pure string folding — no model load

# When True, citation forms are canonicalized through LatinCy so the stored key
# matches reading-time lemmatization. LLPSI lists verbs by infinitive
# ("facere") but LatinCy lemmatizes reading text to 1sg ("facio"); without this
# the learner's known verbs would never match what they read. main() enables it
# (production); it defaults off so the fixture tests stay fast + model-free.
_USE_LEMMATIZER = False


def _norm(form: str) -> str:
    return _provider.normalize_bare(form)


def _bare(form: str) -> str:
    """Canonical lemma key. With the lemmatizer on, runs the citation form
    through LatinCy (facere→facio, capere→capio) so it matches reading-time
    lemmas; falls back to plain normalization on any failure or when off."""
    if _USE_LEMMATIZER:
        try:
            cand = _provider.lemmatize(form)
            if cand.lemma_bare:
                return cand.lemma_bare
        except Exception:
            pass
    return _norm(form)


def _function_words() -> set[str]:
    return FUNCTION_WORD_SETS.get(LANG, set())


# ─── Parsing ────────────────────────────────────────────────────────────────


@dataclass
class VocabRow:
    lemma_form: str
    lemma_bare: str
    gloss_en: str | None
    pos: str | None
    rank: int | None
    chapter: str | None


def _detect_columns(header: list[str]) -> dict[str, int | None]:
    low = [h.strip().lower() for h in header]

    def find(*names: str) -> int | None:
        for i, h in enumerate(low):
            if any(n in h for n in names):
                return i
        return None

    return {
        "lemma": find("lemma", "headword", "latin", "word"),
        "gloss": find("gloss", "definition", "meaning", "english", "translation"),
        "pos": find("part", "pos"),
        "rank": find("rank", "frequency", "freq", "order"),
        "chapter": find("chapter", "cap"),
    }


def _sniff_delimiter(path: Path) -> str:
    return "," if path.suffix.lower() == ".csv" else "\t"


def parse_vocab_file(path: Path) -> list[VocabRow]:
    """Parse a delimited vocab file into VocabRow, detecting columns by header.

    Rank: numeric column when present and parseable, otherwise the 1-based row
    order (so an already-frequency-sorted list keeps its order).
    """
    if not path.exists():
        raise FileNotFoundError(f"vocab file not found: {path}")
    delim = _sniff_delimiter(path)
    rows: list[VocabRow] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, delimiter=delim)
        header = next(reader, None)
        if not header:
            return rows
        cols = _detect_columns(header)
        if cols["lemma"] is None:
            raise ValueError(
                f"{path}: could not find a lemma/headword/word column in header {header!r}"
            )
        order = 0
        for raw in reader:
            if not raw or all(not c.strip() for c in raw):
                continue
            def cell(key: str) -> str | None:
                idx = cols[key]
                if idx is None or idx >= len(raw):
                    return None
                v = raw[idx].strip()
                return v or None

            lemma_form = cell("lemma")
            if not lemma_form:
                continue
            order += 1
            rank_val = cell("rank")
            rank: int | None
            try:
                rank = int(rank_val) if rank_val is not None else order
            except ValueError:
                rank = order  # frequency-group label, not numeric → use order
            bare = _bare(lemma_form)
            if not bare:
                continue
            rows.append(VocabRow(
                lemma_form=lemma_form,
                lemma_bare=bare,
                gloss_en=cell("gloss"),
                pos=cell("pos"),
                rank=rank,
                chapter=cell("chapter"),
            ))
    return rows


# ─── Lemma helpers (language-scoped) ─────────────────────────────────────────


def _lookup_lemma(db: Session, lemma_bare: str) -> Lemma | None:
    return (
        db.query(Lemma)
        .filter(Lemma.language_code == LANG, Lemma.lemma_bare == lemma_bare)
        .first()
    )


def _word_category(lemma_bare: str) -> str | None:
    return "function_word" if lemma_bare in _function_words() else None


def _get_or_create_lemma(db: Session, row: VocabRow, source: str) -> Lemma:
    existing = _lookup_lemma(db, row.lemma_bare)
    now = datetime.now(timezone.utc)
    if existing:
        if existing.gloss_en is None and row.gloss_en:
            existing.gloss_en = row.gloss_en
        if existing.frequency_rank is None and row.rank is not None:
            existing.frequency_rank = row.rank
        if existing.pos is None and row.pos:
            existing.pos = row.pos
        return existing
    lemma = Lemma(
        language_code=LANG,
        # Latin display policy: display form IS the normalized key (lowercase,
        # macron-free, v→u, j→i) so every source renders identically.
        lemma_form=row.lemma_bare,
        lemma_bare=row.lemma_bare,
        gloss_en=row.gloss_en,
        pos=row.pos,
        frequency_rank=row.rank,
        source=source,
        word_category=_word_category(row.lemma_bare),
        gates_completed_at=now,  # authoritative list: forms + glosses are trusted
        notes_json=({"llpsi_chapter": row.chapter} if row.chapter else None),
    )
    db.add(lemma)
    db.flush()
    return lemma


def _mark_assumed_known(db: Session, lemma_id: int, source: str) -> bool:
    """Create a no-card ULK in 'known' state — assumed-known scaffold.

    Mirrors cognate_detector._auto_mark_known: canonical-resolve at entry, never
    overwrite an existing ULK (the learner may already have a real card or have
    marked it unknown). Returns True if a new ULK was created.
    """
    target_id = resolve_canonical_lemma_id(db, lemma_id)
    existing = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id == target_id)
        .first()
    )
    if existing:
        return False
    db.add(UserLemmaKnowledge(
        lemma_id=target_id,
        knowledge_state="known",
        source=source,
        knowledge_origin=ORIGIN_PRE_KNOWN,
        introduced_at=datetime.now(timezone.utc),
    ))
    return True


# ─── Phases ──────────────────────────────────────────────────────────────────


def phase_frequency(db: Session, path: Path, source: str) -> int:
    """Ingest a frequency list as FrequencyEntry(source=...). Idempotent:
    clears existing rows for (LANG, source) first."""
    rows = parse_vocab_file(path)
    db.query(FrequencyEntry).filter(
        FrequencyEntry.language_code == LANG,
        FrequencyEntry.source == source,
    ).delete(synchronize_session=False)
    db.commit()
    seen: set[str] = set()
    inserted = 0
    for row in rows:
        if row.lemma_bare in seen:
            continue
        seen.add(row.lemma_bare)
        db.add(FrequencyEntry(
            language_code=LANG,
            source=source,
            rank=row.rank or (inserted + 1),
            lemma_key=row.lemma_bare,
            display_form=row.lemma_bare,
            gloss_en=row.gloss_en,
            pos=row.pos,
        ))
        inserted += 1
        if inserted % 500 == 0:
            db.commit()
    db.commit()
    log.info("[%s] ingested %d frequency entries", source, inserted)
    return inserted


def phase_promote(db: Session, source: str) -> int:
    """Create Lemma rows for frequency entries of ``source`` lacking a backing
    Lemma. These are the learn-frontier; no ULK is created (the user hasn't
    studied them yet)."""
    entries = (
        db.query(FrequencyEntry)
        .filter(FrequencyEntry.language_code == LANG, FrequencyEntry.source == source)
        .order_by(FrequencyEntry.rank)
        .all()
    )
    created = 0
    for entry in entries:
        existing = _lookup_lemma(db, entry.lemma_key)
        if existing:
            if entry.lemma_id != existing.lemma_id:
                entry.lemma_id = existing.lemma_id
            if existing.frequency_rank is None or existing.frequency_rank > entry.rank:
                existing.frequency_rank = entry.rank
            continue
        row = VocabRow(
            lemma_form=entry.display_form,
            lemma_bare=entry.lemma_key,
            gloss_en=entry.gloss_en,
            pos=entry.pos,
            rank=entry.rank,
            chapter=None,
        )
        lemma = _get_or_create_lemma(db, row, source="frequency_core")
        entry.lemma_id = lemma.lemma_id
        created += 1
        if created % 500 == 0:
            db.commit()
    db.commit()
    log.info("[%s] promoted %d new lemmas", source, created)
    return created


def phase_llpsi(db: Session, path: Path) -> tuple[int, int]:
    """Create/link Lemma rows for LLPSI Familia Romana vocab and mark each as
    assumed-known (no card). Function words are created/linked for mapping but
    NOT enrolled as known scaffold targets. Returns (lemmas_touched, marked)."""
    rows = parse_vocab_file(path)
    touched = 0
    marked = 0
    for row in rows:
        lemma = _get_or_create_lemma(db, row, source="llpsi")
        touched += 1
        # Don't enrol function words / proper names as review/verification
        # targets — they stay mappable but never become scaffold ULKs.
        if lemma.word_category in ("function_word", "proper_name", "not_word"):
            continue
        if _mark_assumed_known(db, lemma.lemma_id, source="llpsi_known"):
            marked += 1
        if touched % 300 == 0:
            db.commit()
    db.commit()
    log.info("LLPSI: %d lemmas touched, %d marked assumed-known", touched, marked)
    return touched, marked


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--phase",
        choices=["dcc", "roma_aeterna", "promote", "llpsi", "all"],
        default="all",
    )
    parser.add_argument("--dcc-file", type=Path, default=REPO_ROOT / "data" / "vocab" / "dcc_core.tsv")
    parser.add_argument("--llpsi-file", type=Path, default=REPO_ROOT / "data" / "vocab" / "llpsi_fr.tsv")
    parser.add_argument("--ra-file", type=Path, default=REPO_ROOT / "data" / "vocab" / "roma_aeterna.tsv")
    parser.add_argument("--no-canonicalize", action="store_true",
                        help="Skip LatinCy canonicalization of citation forms "
                             "(faster, but verb infinitives won't match reading lemmas)")
    args = parser.parse_args(argv)

    global _USE_LEMMATIZER
    _USE_LEMMATIZER = not args.no_canonicalize

    db = SessionLocal()
    try:
        stats: dict[str, int] = {}
        if args.phase in ("dcc", "all"):
            stats["dcc_entries"] = phase_frequency(db, args.dcc_file, SOURCE_DCC)
        if args.phase in ("roma_aeterna", "all"):
            if args.ra_file.exists():
                stats["ra_entries"] = phase_frequency(db, args.ra_file, SOURCE_RA)
            else:
                log.warning("Roma Aeterna file %s not found; skipping", args.ra_file)
        if args.phase in ("promote", "all"):
            stats["dcc_promoted"] = phase_promote(db, SOURCE_DCC)
            if (db.query(FrequencyEntry)
                    .filter(FrequencyEntry.language_code == LANG,
                            FrequencyEntry.source == SOURCE_RA).first()):
                stats["ra_promoted"] = phase_promote(db, SOURCE_RA)
        if args.phase in ("llpsi", "all"):
            touched, marked = phase_llpsi(db, args.llpsi_file)
            stats["llpsi_touched"] = touched
            stats["llpsi_marked_known"] = marked

        log_activity(
            db,
            event_type="latin_vocab_seeded",
            summary=f"Latin vocab import (phase={args.phase}): {stats}",
            detail=stats,
            language_code=LANG,
        )
        log.info("done: %s", stats)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
