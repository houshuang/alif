#!/usr/bin/env python3
"""Merge root families that were stored under several spellings.

Applies the reviewed plan in `root_merge_plan_2026_11_25.py`: hamza-seat
duplicates are merged into one row, misspelled roots are repointed, and a few
rootless or misfiled words get their root. Dry run by default; `--apply` writes everything
in one transaction and logs an ActivityLog entry. Back up the database first.

Usage:
    cd backend && python scripts/merge_split_roots.py            # dry run
    cd backend && python scripts/merge_split_roots.py --apply
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Lemma, Root, Sentence
from app.services.activity_log import log_activity
from app.services.morphology import is_valid_root, root_spellings
from root_merge_plan_2026_11_25 import LEMMA_ROOTS, MERGES, NEW_ROOT_MEANINGS, RENAMES, REPOINT


class PlanError(RuntimeError):
    pass


def _root(db: Session, root_str: str) -> Root | None:
    return db.query(Root).filter(Root.root == root_str).one_or_none()


def _require(db: Session, root_str: str) -> Root:
    row = _root(db, root_str)
    if row is None:
        raise PlanError(f"root {root_str!r} not found")
    return row


def _lemmas(db: Session, root_id: int) -> list[Lemma]:
    return db.query(Lemma).filter(Lemma.root_id == root_id).order_by(Lemma.lemma_id).all()


def _move(db: Session, src: Root, dst_id: int | None) -> tuple[int, int]:
    lemmas = _lemmas(db, src.root_id)
    for lemma in lemmas:
        lemma.root_id = dst_id
    sentences = db.query(Sentence).filter(Sentence.root_focus_id == src.root_id).all()
    for sentence in sentences:
        sentence.root_focus_id = dst_id
    return len(lemmas), len(sentences)


def _delete(db: Session, row: Root) -> None:
    db.flush()
    if db.query(Lemma).filter(Lemma.root_id == row.root_id).count():
        raise PlanError(f"root {row.root!r} still has lemmas")
    db.delete(row)


def _get_or_create(db: Session, root_str: str, fallback_meaning: str = "") -> tuple[Root, bool]:
    row = _root(db, root_str)
    if row is not None:
        return row, False
    if not is_valid_root(root_str):
        raise PlanError(f"invalid target {root_str!r}")
    row = Root(root=root_str, core_meaning_en=NEW_ROOT_MEANINGS.get(root_str, fallback_meaning))
    db.add(row)
    db.flush()
    return row, True


def _words(db: Session, row: Root) -> str:
    return ", ".join(l.lemma_ar for l in _lemmas(db, row.root_id)) or "(none)"


def run(db: Session) -> dict:
    """Apply the plan to the session (caller commits or rolls back)."""
    stats = {"merged": 0, "renamed": 0, "repointed": 0, "unrooted": 0, "lemmas_set": 0, "roots_created": 0,
             "lemmas_moved": 0, "sentences_moved": 0}

    for old, new in MERGES:
        loser, winner = _require(db, old), _require(db, new)
        print(f"MERGE  {old} [{_words(db, loser)}] -> {new} [{_words(db, winner)}]")
        if not winner.core_meaning_en and loser.core_meaning_en:
            winner.core_meaning_en = loser.core_meaning_en
        if not winner.enrichment_json and loser.enrichment_json:
            winner.enrichment_json = loser.enrichment_json
        n_lem, n_sent = _move(db, loser, winner.root_id)
        stats["lemmas_moved"] += n_lem
        stats["sentences_moved"] += n_sent
        _delete(db, loser)
        stats["merged"] += 1

    for old, new in RENAMES:
        row = _require(db, old)
        if _root(db, new) is not None:
            raise PlanError(f"rename target {new!r} already exists")
        print(f"RENAME {old} -> {new} [{_words(db, row)}]")
        row.root = new
        stats["renamed"] += 1

    for old, target in REPOINT:
        row = _require(db, old)
        if target is None:
            print(f"UNROOT {old} [{_words(db, row)}]")
            n_lem, n_sent = _move(db, row, None)
            stats["lemmas_moved"] += n_lem
            stats["sentences_moved"] += n_sent
            stats["unrooted"] += n_lem
            _delete(db, row)
            continue
        if _root(db, target) is None and old in root_spellings(target):
            print(f"RENAME {old} -> {target} [{_words(db, row)}]")
            row.root = target
            stats["renamed"] += 1
            continue
        existing, created = _get_or_create(db, target, row.core_meaning_en or "")
        stats["roots_created"] += created
        where = "(new root)" if created else f"[{_words(db, existing)}]"
        print(f"MOVE   {old} [{_words(db, row)}] -> {target} {where}")
        n_lem, n_sent = _move(db, row, existing.root_id)
        stats["lemmas_moved"] += n_lem
        stats["sentences_moved"] += n_sent
        stats["repointed"] += 1
        _delete(db, row)

    for lemma_id, lemma_ar, target in LEMMA_ROOTS:
        lemma = db.get(Lemma, lemma_id)
        if lemma is None or lemma.lemma_ar != lemma_ar:
            raise PlanError(f"lemma {lemma_id} is not {lemma_ar!r}")
        if target is None:
            print(f"UNROOT {lemma_ar}")
            lemma.root_id = None
        else:
            root, created = _get_or_create(db, target)
            stats["roots_created"] += created
            where = "(new root)" if created else f"[{_words(db, root)}]"
            print(f"SET    {lemma_ar} -> {target} {where}")
            lemma.root_id = root.root_id
        stats["lemmas_set"] += 1

    db.flush()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--apply", action="store_true", help="write the changes")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        stats = run(db)
        print(stats)
        if not args.apply:
            db.rollback()
            print("Dry run: nothing written. Re-run with --apply.")
            return
        db.commit()
        log_activity(db, "manual_fix", "Merged split root families (hamza spellings, misspelled roots)", stats)
        print("Applied.")
    except PlanError as exc:
        db.rollback()
        sys.exit(f"Plan does not match the database: {exc}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
