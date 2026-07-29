#!/usr/bin/env python3
"""Import common Arabic words that the LLM uses as scaffold in sentences.

These words were identified by mining correction_failed logs — the LLM
keeps generating natural sentences using them, but the mapping pipeline
rejects the sentences because the correct lemma isn't in the DB.

Importing them as source="scaffold" lets them serve as valid mapping
targets without forcing them into the learner's review queue.

Usage:
    python scripts/import_scaffold_lemmas.py              # import all
    python scripts/import_scaffold_lemmas.py --dry-run    # preview
    python scripts/import_scaffold_lemmas.py --only فَعَلَ --dry-run
"""

import argparse
import sys
import unicodedata
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal, Base, engine
from app.models import Lemma, Root
from app.services.lemma_quality import run_quality_gates
from app.services.sentence_validator import (
    strip_diacritics,
    normalize_alef,
    resolve_existing_lemma,
    build_comprehensive_lemma_lookup,
)


def _nfc(text: str) -> str:
    """Return the canonical Unicode identity used for vocalized headwords."""
    return unicodedata.normalize("NFC", text)


# Words mined from correction_failed pipeline logs (2026-03 through 2026-04).
# Each: (diacritized_form, gloss_en, pos)
SCAFFOLD_WORDS = [
    ("أَخِيرًا", "finally, at last", "adv"),
    ("فَضَّلَ", "to prefer", "verb"),
    ("اِحْتَاجَ", "to need, to require", "verb"),
    ("سُرْعَة", "speed, quickness", "noun"),
    ("مُنَاسِب", "suitable, appropriate", "adj"),
    ("صِدْق", "honesty, truthfulness", "noun"),
    ("ضَحِكَ", "to laugh", "verb"),
    ("إِنْسَان", "human being, person", "noun"),
    ("مَاهِر", "skilled, expert", "adj"),
    ("رَأَى", "to see", "verb"),
    ("وُصُول", "arrival", "noun"),
    ("تَرَكَ", "to leave, to abandon", "verb"),
    ("اِكْتِشَاف", "discovery", "noun"),
    ("لَمَّا", "when", "particle"),
    ("مُقْبِل", "next, upcoming", "adj"),
    ("دُخُول", "entry, entering", "noun"),
    ("قَطَعَ", "to cut", "verb"),
    ("مَوْجُود", "present, existing", "adj"),
    ("مَزْرَعَة", "farm", "noun"),
    ("مُغْلَق", "closed", "adj"),
    ("حَلّ", "solution", "noun"),
    ("تَأْكِيد", "confirmation, certainty", "noun"),
    ("ذَهَاب", "going, departure", "noun"),
    ("مُرَاجَعَة", "review, revision", "noun"),
    ("يَمِين", "right (direction)", "noun"),
    ("مَعْرِفَة", "knowledge", "noun"),
    ("خُرُوج", "exit, going out", "noun"),
    ("كَسَرَ", "to break", "verb"),
    ("صَارِم", "strict, firm", "adj"),
    ("سَرِقَة", "theft, robbery", "noun"),
    ("رَحْمَة", "mercy, compassion", "noun"),
    ("نَزَلَ", "to descend, to come down", "verb"),
    ("حُبّ", "love", "noun"),
    ("مَصْنَع", "factory", "noun"),
    ("لَمَسَ", "to touch", "verb"),
    ("مُرْتَاح", "relaxed, comfortable", "adj"),
    ("تَنْظِيف", "cleaning", "noun"),
    ("أَدْرَكَ", "to realize, to perceive", "verb"),
    ("زِيَادَة", "increase", "noun"),
    # 2026-04-17 — from missing_lemma_candidates on fresh Hindawi corpus.
    # طَيْر omitted: maps to existing #2461 طائر by project convention
    # (collective and singular share a lemma via hamza normalization).
    ("قَدِمَ", "to come, to arrive", "verb"),
    ("قَدَّمَ", "to submit, to present", "verb"),
    ("أَخْبَرَ", "to inform, to tell", "verb"),
    ("مِثْل", "like, similar to", "noun"),
    ("قِطّ", "cat", "noun"),
    # 2026-07-29 — reviewed Momo inventory gaps from the PR #232 copied-DB
    # rehearsal. These add dictionary inventory only; they do not seed ULK,
    # remap, prepare, or activate corpus sentences.
    ("كُلِّيّ", "total; overall; all-encompassing", "adj"),
    ("إِلٰه", "god; deity", "noun"),
    ("فَعَلَ", "to do", "verb"),
]

# Entries that share a bare form with an existing lemma but are a distinct
# lemma (different pattern / sense). Bypasses bare-form dedup, which would
# otherwise block them. Exact diacritized match is still enforced.
# Example: قَدِمَ "to come" vs existing قَدَمَ "to precede" — same bare قدم.
ALLOW_HOMOGRAPH = {
    "قَدِمَ",   # vs #561 قَدَمَ "to precede"
    "قَدَّمَ",  # vs #561 قَدَمَ "to precede"
    "أَخْبَرَ",  # Form IV "inform" vs Form I #975 خَبَرَ "try" — resolver mis-strips أ
    "مِثْل",   # vs #976 مَثَلَ "to resemble"
    "قِطّ",    # vs #490 قَطَّ "to carve"
    "كُلِّيّ",  # resolver over-strips to #452 لـِ "to"
    "إِلٰه",   # resolver over-strips to #441 الْ "the"
    "فَعَلَ",  # verb "to do" vs #207 noun فِعْل "verb"
}

# Roots independently reviewed against the production inventory. Pinning these
# avoids asking enrichment to infer a nisba base or hamza seat and accidentally
# creating duplicate roots such as ك.ل.ي or ا.ل.ه.
SCAFFOLD_ROOTS = {
    "كُلِّيّ": "ك.ل.ل",
    "إِلٰه": "ء.ل.ه",
    "فَعَلَ": "ف.ع.ل",
}


def select_scaffold_words(
    only: list[str] | None = None,
) -> list[tuple[str, str, str]]:
    """Return the exact reviewed subset requested by the operator.

    ``--only`` is deliberately matched against the fully vocalized headword.
    A typo must abort rather than silently falling back to importing the whole
    scaffold list.
    """
    if not only:
        return list(SCAFFOLD_WORDS)

    requested = {_nfc(headword) for headword in only}
    known = {_nfc(arabic) for arabic, _gloss, _pos in SCAFFOLD_WORDS}
    unknown = sorted(requested - known)
    if unknown:
        raise ValueError(
            "unknown --only headword(s): " + ", ".join(unknown)
        )
    return [
        (_nfc(arabic), gloss, pos)
        for arabic, gloss, pos in SCAFFOLD_WORDS
        if _nfc(arabic) in requested
    ]


def _exact_scaffold_mismatches(
    lemma: Lemma,
    *,
    arabic: str,
    bare: str,
    gloss: str,
    pos: str,
    expected_root_id: int | None = None,
) -> list[str]:
    """Describe fields that make an exact display row unsafe to resume."""
    expected = {
        "lemma_ar": arabic,
        "lemma_ar_bare": bare,
        "gloss_en": gloss,
        "pos": pos,
        "source": "scaffold",
        "canonical_lemma_id": None,
    }
    actual = {
        "lemma_ar": _nfc(lemma.lemma_ar),
        "lemma_ar_bare": lemma.lemma_ar_bare,
        "gloss_en": lemma.gloss_en,
        "pos": lemma.pos,
        "source": lemma.source,
        "canonical_lemma_id": lemma.canonical_lemma_id,
    }
    if expected_root_id is not None:
        expected["root_id"] = expected_root_id
        actual["root_id"] = lemma.root_id
    return [
        f"{field}={actual[field]!r} (expected {value!r})"
        for field, value in expected.items()
        if actual[field] != value
    ]


def import_scaffold_words(
    db,
    scaffold_words: list[tuple[str, str, str]],
    *,
    dry_run: bool = False,
    strict_exact: bool = False,
    quality_gate_runner: Callable | None = None,
) -> dict[str, int]:
    """Import a reviewed subset and resume compatible interrupted gate runs.

    New rows are committed before the centralized gates because those gates use
    their own commits and enrichment sessions. An exact compatible row with no
    gate stamp is therefore a resumable interrupted import, not a duplicate.
    """
    quality_gate_runner = quality_gate_runner or run_quality_gates

    try:
        lookup = build_comprehensive_lemma_lookup(db)
        existing_lemmas = db.query(Lemma).all()
        existing_bare = set(
            lemma.lemma_ar_bare for lemma in existing_lemmas
        )
        existing_by_display: dict[str, list[Lemma]] = {}
        for lemma in existing_lemmas:
            existing_by_display.setdefault(_nfc(lemma.lemma_ar), []).append(lemma)
        required_root_names = {
            SCAFFOLD_ROOTS[arabic]
            for raw_arabic, _gloss, _pos in scaffold_words
            if (arabic := _nfc(raw_arabic)) in SCAFFOLD_ROOTS
        }
        roots_by_name = {
            root.root: root
            for root in db.query(Root)
            .filter(Root.root.in_(required_root_names))
            .all()
        }

        imported = 0
        resumed = 0
        skipped = 0
        gate_ids: list[int] = []
        planned: list[tuple[str, str, str, str, Lemma | None]] = []

        # Validate and plan the entire request before mutating the database.
        for raw_arabic, gloss, pos in scaffold_words:
            arabic = _nfc(raw_arabic)
            bare = normalize_alef(strip_diacritics(arabic))
            exact_rows = existing_by_display.get(arabic, [])
            required_root = SCAFFOLD_ROOTS.get(arabic)
            root = roots_by_name.get(required_root) if required_root else None

            if len(exact_rows) > 1:
                if strict_exact:
                    ids = ", ".join(str(row.lemma_id) for row in exact_rows)
                    raise ValueError(
                        f"{arabic}: multiple canonically equivalent display "
                        f"rows already exist (lemma IDs {ids})"
                    )
                # Preserve the legacy full-import behavior: any exact display
                # identity skips. A bounded --only run remains fail-closed.
                planned.append(
                    ("duplicate_skip", arabic, gloss, pos, exact_rows[0])
                )
                continue

            if exact_rows:
                exact = exact_rows[0]
                if strict_exact and required_root and root is None:
                    raise ValueError(
                        f"{arabic}: reviewed root {required_root} is missing "
                        "from the root inventory"
                    )
                mismatches = _exact_scaffold_mismatches(
                    exact,
                    arabic=arabic,
                    bare=bare,
                    gloss=gloss,
                    pos=pos,
                    expected_root_id=root.root_id if root else None,
                )
                if mismatches:
                    if strict_exact:
                        raise ValueError(
                            f"{arabic}: exact row #{exact.lemma_id} is not the "
                            "reviewed scaffold entry: " + "; ".join(mismatches)
                        )
                    planned.append(("skip", arabic, gloss, pos, exact))
                elif exact.gates_completed_at is None:
                    planned.append(("resume", arabic, gloss, pos, exact))
                else:
                    planned.append(("skip", arabic, gloss, pos, exact))
                continue

            # Bare-form dedup (default). Bypass for curated homograph entries.
            if arabic not in ALLOW_HOMOGRAPH:
                if normalize_alef(bare) in existing_bare or bare in existing_bare:
                    planned.append(("bare_skip", arabic, gloss, pos, None))
                    continue

                existing = resolve_existing_lemma(bare, lookup)
                if existing:
                    planned.append(
                        ("resolve_skip", arabic, gloss, pos, existing)
                    )
                    continue

            if required_root and root is None:
                raise ValueError(
                    f"{arabic}: reviewed root {required_root} is missing from "
                    "the root inventory"
                )
            planned.append(("import", arabic, gloss, pos, None))

        for action, arabic, gloss, pos, existing in planned:
            bare = normalize_alef(strip_diacritics(arabic))

            if action == "skip":
                assert existing is not None
                suffix = (
                    "already in DB and gated"
                    if existing.gates_completed_at is not None
                    else "already in DB with different metadata"
                )
                print(f"  [skip] {arabic} ({gloss}) — {suffix}")
                skipped += 1
                continue
            if action == "duplicate_skip":
                print(
                    f"  [skip] {arabic} ({gloss}) — canonically equivalent "
                    "display row already in DB"
                )
                skipped += 1
                continue
            if action == "bare_skip":
                print(
                    f"  [skip] {arabic} ({gloss}) — bare collides with "
                    "existing lemma"
                )
                skipped += 1
                continue
            if action == "resolve_skip":
                print(
                    f"  [skip] {arabic} ({gloss}) — resolves to existing "
                    f"#{existing}"
                )
                skipped += 1
                continue
            if action == "resume":
                assert existing is not None
                print(
                    f"  [resume gates] #{existing.lemma_id} {arabic} — "
                    f"{gloss} [{pos}]"
                )
                resumed += 1
                if not dry_run:
                    gate_ids.append(existing.lemma_id)
                continue

            assert action == "import"
            if dry_run:
                print(f"  [dry-run] {arabic} (bare: {bare}) — {gloss} [{pos}]")
                imported += 1
                continue

            lemma = Lemma(
                lemma_ar=arabic,
                lemma_ar_bare=bare,
                gloss_en=gloss,
                pos=pos,
                source="scaffold",
                root_id=(
                    roots_by_name[SCAFFOLD_ROOTS[arabic]].root_id
                    if arabic in SCAFFOLD_ROOTS
                    else None
                ),
            )
            db.add(lemma)
            db.flush()
            gate_ids.append(lemma.lemma_id)
            imported += 1
            print(f"  [import] #{lemma.lemma_id} {arabic} — {gloss} [{pos}]")

        if gate_ids and not dry_run:
            db.commit()
            print(f"\nRunning quality gates on {len(gate_ids)} lemmas...")
            gates = quality_gate_runner(
                db, gate_ids,
                background_enrich=False,
            )
            db.commit()
            print(f"  Finalized: {gates.get('finalize', {})}")
            print(f"  Variants marked: {gates.get('variants', 0)}")
            print(f"  Stamped: {gates.get('stamped', 0)}")

        print(
            f"\nDone: {imported} imported, {resumed} gate runs resumed, "
            f"{skipped} skipped"
        )
        return {
            "imported": imported,
            "resumed": resumed,
            "skipped": skipped,
        }
    except Exception:
        db.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description="Import scaffold lemmas")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="VOCALIZED_HEADWORD",
        help=(
            "Import only this exact reviewed headword; repeat for multiple "
            "words. Unknown values abort."
        ),
    )
    args = parser.parse_args()
    try:
        scaffold_words = select_scaffold_words(args.only)
    except ValueError as exc:
        parser.error(str(exc))

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        try:
            import_scaffold_words(
                db,
                scaffold_words,
                dry_run=args.dry_run,
                strict_exact=bool(args.only),
            )
        except ValueError as exc:
            parser.error(str(exc))
    finally:
        db.close()


if __name__ == "__main__":
    main()
