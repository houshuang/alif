#!/usr/bin/env python3
"""Move stored sentence words onto lemmas that were created after the sentence.

A sentence stored before a word had its own lemma maps that word to whatever
caught it then: a participle or verbal noun to its verb (مُجْتَمَع on اِجْتَمَعَ), a
noun to a look-alike. Creating the lemma fixes new sentences only. This script
re-resolves every stored `SentenceWord` surface with the current lookup and
moves the row when the answer is one of the newly created lemmas.

A row moves only when the resolver is sure (no CAMeL guess, no homograph tie)
and the new lemma is a noun or adjective whose headword the stored surface
spells out: same letters and hamza seats after the proclitics, no vowel mark
that contradicts the headword, and the headword's shadda present. The resolver
alone is not enough: it also proposed أَجَابَهُ for إِجابَة and حُلْواً for حَلَّ.
Verbs, broken plurals and أُخْرَى-type forms are left where they are, and so is
a word its current noun or adjective lemma spells at least as fully (ذُبَابَة stays
off ذُباب). When the moved word was the sentence's only token of its target lemma,
`Sentence.target_lemma_id` follows it. Reviewable sentences that changed are
then re-verified through `mapping_rescue.reverify_all_active_sentences`, which
hides any sentence the verifier does not accept. Review history is not
rewritten.

Usage (dry run unless --apply):
  cd backend
  PYTHONPATH=. python3 scripts/remap_to_new_lemmas.py --since 2026-11-25 [--apply]
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

os.environ.setdefault("ALIF_SKIP_MIGRATIONS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal  # noqa: E402
from app.models import ActivityLog, Lemma, Sentence, SentenceWord  # noqa: E402
from app.services.canonical_resolution import resolve_canonical_via_map  # noqa: E402
from app.services.sentence_eligibility import MAPPING_VERIFICATION_MIN_AT  # noqa: E402
from app.services.sentence_validator import (  # noqa: E402
    LemmaMatchProvenance,
    build_comprehensive_lemma_lookup,
    lookup_lemma_id,
)

DEFAULT_SOURCES = ("book_frequency", "textbook_list")


MARKS = "\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652"
SHADDA = "\u0651"
TANWEEN = "\u064b\u064c\u064d"
PROCLITICS = ("وبال", "فبال", "وكال", "ولل", "فلل", "وال", "فال", "بال", "كال", "لل", "ال", "و", "ف", "ب", "ل", "ك", "")
MOVABLE_POS = {"noun", "adj", "adjective"}


def _letters(text: str) -> list[tuple[str, str]]:
    """(letter, marks) pairs; wasla and a kasra-carrying initial alif are plain alif."""
    out: list[tuple[str, str]] = []
    for ch in text:
        if ch in MARKS:
            if out:
                out[-1] = (out[-1][0], out[-1][1] + ch)
        elif "\u0621" <= ch <= "\u064a" or ch == "\u0671":
            out.append(("ا" if ch == "\u0671" else ch, ""))
    return out


def spells_headword(surface: str, headword: str) -> bool:
    surf, head = _letters(surface), _letters(headword)
    if not head:
        return False
    if head[0][0] == "ا":
        head[0] = ("ا", "")
    vocalised = sum(1 for _, marks in surf if marks) >= 2
    bare = "".join(letter for letter, _ in surf)
    stem = [letter for letter, _ in head]
    for clitic in PROCLITICS:
        if not bare.startswith(clitic):
            continue
        start = len(clitic)
        segment = surf[start:start + len(head)]
        if len(segment) < len(head):
            continue
        ok = True
        for index, ((s_letter, s_marks), (h_letter, h_marks)) in enumerate(zip(segment, head)):
            last = index == len(head) - 1
            if s_letter != h_letter and not (last and h_letter == "ة" and s_letter == "ت"):
                ok = False
                break
            ends_here = last and start + len(head) == len(surf)
            assimilated = index == 0 and clitic.endswith("ل") and SHADDA in s_marks
            if (SHADDA in h_marks) != (SHADDA in s_marks) and (vocalised or ends_here) and not assimilated:
                ok = False
                break
            s_vowel = s_marks.replace(SHADDA, "")
            h_vowel = h_marks.replace(SHADDA, "")
            if not last and s_vowel and h_vowel and s_vowel != h_vowel:
                ok = False
                break
        if ok and stem:
            return True
    return False


def find_moves(db, sources: set[str], since: datetime) -> tuple[list[dict], dict[int, Lemma]]:
    lemmas = {l.lemma_id: l for l in db.query(Lemma).all()}
    parents = {lid: l.canonical_lemma_id for lid, l in lemmas.items()}
    new_ids = {
        lid
        for lid, l in lemmas.items()
        if l.source in sources
        and l.gates_completed_at is not None
        and l.gates_completed_at.replace(tzinfo=None) >= since
        and l.canonical_lemma_id is None
    }
    lookup = build_comprehensive_lemma_lookup(db)
    cache: dict[str, int | None] = {}

    def resolve(surface: str) -> int | None:
        if surface not in cache:
            provenance = LemmaMatchProvenance()
            found = lookup_lemma_id(surface, lookup, provenance, strict_fallback=True)
            if found is not None and (provenance.is_guess or provenance.homograph_tie):
                found = None
            if found is not None:
                found = resolve_canonical_via_map(found, parents)
            if found not in new_ids or (lemmas[found].pos or "") not in MOVABLE_POS:
                found = None
            elif not spells_headword(surface, lemmas[found].lemma_ar or ""):
                found = None
            cache[surface] = found
        return cache[surface]

    moves = []
    rows = (
        db.query(SentenceWord.id, SentenceWord.sentence_id, SentenceWord.surface_form, SentenceWord.lemma_id)
        .filter(SentenceWord.lemma_id.isnot(None))
        .yield_per(5000)
    )
    for sw_id, sentence_id, surface, lemma_id in rows:
        if lemma_id in new_ids or not surface:
            continue
        target = resolve(surface)
        if target is None or target == resolve_canonical_via_map(lemma_id, parents):
            continue
        current = lemmas.get(lemma_id)
        keeps = (
            current is not None
            and (current.pos or "") in MOVABLE_POS
            and len(current.lemma_ar_bare or "") >= len(lemmas[target].lemma_ar_bare or "")
            and spells_headword(surface, current.lemma_ar or "")
        )
        if not keeps:
            moves.append({"sw_id": sw_id, "sentence_id": sentence_id, "surface": surface,
                          "old": lemma_id, "new": target})
    return moves, lemmas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", action="append", help="lemma source (repeatable)")
    parser.add_argument("--since", required=True, help="lemmas whose quality gates completed on or after YYYY-MM-DD")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-reverify", action="store_true")
    parser.add_argument("--all-pairs", action="store_true", help="print every old -> new pair")
    args = parser.parse_args()

    sources = set(args.source or DEFAULT_SOURCES)
    since = datetime.strptime(args.since, "%Y-%m-%d")

    db = SessionLocal()
    try:
        moves, lemmas = find_moves(db, sources, since)
        db.rollback()
        by_sentence: dict[int, list[dict]] = defaultdict(list)
        for move in moves:
            by_sentence[move["sentence_id"]].append(move)
        sentences = {
            s.id: s for s in db.query(Sentence).filter(Sentence.id.in_(list(by_sentence))).all()
        } if by_sentence else {}
        reviewable = {
            sid for sid, s in sentences.items()
            if s.is_active and s.mappings_verified_at is not None
            and s.mappings_verified_at >= MAPPING_VERIFICATION_MIN_AT
        }
        print(f"{len(moves)} sentence words in {len(by_sentence)} sentences "
              f"({len(reviewable)} reviewable) would move to a new lemma")
        pairs = Counter((m["old"], m["new"]) for m in moves)
        surfaces: dict[tuple[int, int], Counter] = defaultdict(Counter)
        for m in moves:
            surfaces[(m["old"], m["new"])][m["surface"]] += 1
        for (old, new), n in pairs.most_common(None if args.all_pairs else 60):
            seen = " ".join(f"{form}={count}" for form, count in surfaces[(old, new)].most_common(4))
            print(f"  {n:4d}  {old}>{new}  {lemmas[old].lemma_ar} ({(lemmas[old].gloss_en or '')[:24]})"
                  f" -> {lemmas[new].lemma_ar} ({(lemmas[new].gloss_en or '')[:24]})  | {seen}")
        if not args.apply:
            print("Dry run. Pass --apply to write.")
            return

        retargeted = 0
        for sid, sentence_moves in by_sentence.items():
            moved_ids = {m["sw_id"] for m in sentence_moves}
            for move in sentence_moves:
                db.query(SentenceWord).filter(
                    SentenceWord.id == move["sw_id"], SentenceWord.lemma_id == move["old"]
                ).update({"lemma_id": move["new"]})
            sentence = sentences.get(sid)
            if sentence is None or sentence.target_lemma_id is None:
                continue
            from_target = {m["new"] for m in sentence_moves if m["old"] == sentence.target_lemma_id}
            if len(from_target) != 1:
                continue
            still_there = (
                db.query(SentenceWord.id)
                .filter(
                    SentenceWord.sentence_id == sid,
                    SentenceWord.lemma_id == sentence.target_lemma_id,
                    SentenceWord.id.notin_(moved_ids),
                )
                .first()
            )
            if still_there is None:
                sentence.target_lemma_id = from_target.pop()
                retargeted += 1
        db.add(ActivityLog(
            event_type="sentence_words_remapped",
            summary=(f"Moved {len(moves)} stored sentence words in {len(by_sentence)} sentences "
                     f"onto lemmas created since {args.since}; {retargeted} sentence targets followed"),
            detail_json={
                "pairs": [[old, new, n] for (old, new), n in pairs.most_common()],
                "reverify_sentence_ids": sorted(reviewable),
                "retargeted": retargeted,
                "source": "remap_to_new_lemmas.py",
            },
        ))
        db.commit()
        print(f"APPLIED: {len(moves)} rows moved, {retargeted} sentence targets followed")
    finally:
        db.close()

    if reviewable and not args.no_reverify:
        from app.services.mapping_rescue import reverify_all_active_sentences

        print(f"re-verifying {len(reviewable)} reviewable sentences")
        print(f"reverify stats: {reverify_all_active_sentences(sentence_ids=sorted(reviewable))}")


if __name__ == "__main__":
    main()
