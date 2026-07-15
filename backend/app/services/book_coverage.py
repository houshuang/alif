"""Token-weighted book coverage for the stats panel.

Answers "how much of this specific book can I read right now?" — the metric
that drives the Momo goal (research/analysis-2026-07-14-momo-readiness-volume-
sweep.md). Coverage is token-weighted (occurrences, not types), mirroring the
reference classifier in scripts/reading_readiness.py and the research
map_coverage.py method:

- covered      = function/inert tokens + tokens of known/learning lemmas
- in-progress  = acquiring / lapsed / encountered lemmas
- gap          = mapped to a vocabulary lemma but never started
- unmapped     = not in the vocabulary at all

Data files live in ``data/benchmarks/book_*_tokenmap.json``::

    {"title": "Momo", "target_pct": 95.0,
     "total": 37803, "function": 16104,
     "mapped": {"<lemma_id>": token_count, ...},
     "unmapped_freq": {"<bare surface>": token_count, ...}}

``mapped``/``function`` were classified at scan time through the hardened
``build_comprehensive_lemma_lookup`` + ``lookup_lemma`` path (per the CLAUDE.md
mapping invariant). ``unmapped_freq`` surfaces are re-resolved through that
same path at request time — cached on (file mtime, lemma count) — so words
imported after the scan (e.g. the Momo bookifier tranches) move from unmapped
into live state buckets without rebuilding the file.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Lemma, UserLemmaKnowledge
from app.schemas import BookCoverageOut, BookGapWord, BookSourceCohort

logger = logging.getLogger(__name__)

_BENCHMARKS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "benchmarks"

_KNOWN_STATES = {"known", "learning"}
_PROGRESS_STATES = {"acquiring", "lapsed", "encountered"}
_INERT_CATEGORIES = {"proper_name", "onomatopoeia"}

# Per-file cache of unmapped-surface resolution. Rebuilding the comprehensive
# lookup costs seconds, but its output only changes when the vocabulary does,
# so the key is (tokenmap mtime, total lemma count) rather than a TTL.
_resolution_cache: dict[str, tuple[tuple[float, int], dict[str, int | None]]] = {}


def _resolve_unmapped_surfaces(
    db: Session, cache_key: str, mtime: float, surfaces: list[str]
) -> dict[str, int | None]:
    lemma_count = db.query(func.count(Lemma.lemma_id)).scalar() or 0
    key = (mtime, lemma_count)
    cached = _resolution_cache.get(cache_key)
    if cached and cached[0] == key:
        return cached[1]

    from app.services.sentence_validator import (
        build_comprehensive_lemma_lookup,
        lookup_lemma_citation,
    )

    # The unmapped surfaces are CAMeL citation lemmas, not running text, so use
    # the strict citation resolver: the fuzzy running-text fallbacks in
    # lookup_lemma mis-resolve isolated citation forms (تالي→أَلَا class of
    # collisions, research/momo-vocab-queue-2026-07-15.md).
    lemma_lookup = build_comprehensive_lemma_lookup(db)
    resolved = {
        surface: lookup_lemma_citation(surface, lemma_lookup, original_bare=surface)
        for surface in surfaces
    }
    _resolution_cache[cache_key] = (key, resolved)
    return resolved


def _load_tokenmaps(benchmarks_dir: Path) -> list[tuple[str, float, dict]]:
    out = []
    for path in sorted(benchmarks_dir.glob("book_*_tokenmap.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            logger.warning("Skipping unreadable book tokenmap %s", path)
            continue
        if not isinstance(data.get("mapped"), dict) or not data.get("total"):
            logger.warning("Skipping malformed book tokenmap %s", path)
            continue
        out.append((str(path), path.stat().st_mtime, data))
    return out


def compute_source_cohort(db: Session, source: str) -> BookSourceCohort | None:
    """Lifecycle funnel for all lemmas introduced from one import source."""
    rows = (
        db.query(
            UserLemmaKnowledge.knowledge_state,
            UserLemmaKnowledge.acquisition_box,
            func.count(UserLemmaKnowledge.id),
        )
        .filter(UserLemmaKnowledge.source == source)
        .group_by(
            UserLemmaKnowledge.knowledge_state,
            UserLemmaKnowledge.acquisition_box,
        )
        .all()
    )
    if not rows:
        return None
    cohort = BookSourceCohort(source=source, total=0)
    for state, box, count in rows:
        cohort.total += count
        if state == "acquiring":
            if box == 1:
                cohort.box_1 += count
            elif box == 2:
                cohort.box_2 += count
            else:
                cohort.box_3 += count
        elif state == "encountered":
            cohort.encountered += count
        elif state == "learning":
            cohort.learning += count
        elif state == "known":
            cohort.known += count
        elif state == "lapsed":
            cohort.lapsed += count
        elif state == "suspended":
            cohort.suspended += count
    return cohort


def compute_book_coverage(
    db: Session,
    benchmarks_dir: Path | None = None,
    cohort_source: str = "bookifier",
    top_gap_count: int = 8,
) -> list[BookCoverageOut]:
    """Live token-weighted coverage for every committed book tokenmap."""
    from app.services.canonical_resolution import resolve_canonical_via_map
    from app.services.sentence_validator import (
        _is_function_word,
        normalize_alef,
        strip_diacritics,
    )

    tokenmaps = _load_tokenmaps(benchmarks_dir or _BENCHMARKS_DIR)
    if not tokenmaps:
        return []

    lemma_rows = db.query(
        Lemma.lemma_id,
        Lemma.canonical_lemma_id,
        Lemma.lemma_ar,
        Lemma.lemma_ar_bare,
        Lemma.gloss_en,
        Lemma.pos,
        Lemma.word_category,
    ).all()
    by_id = {row.lemma_id: row for row in lemma_rows}
    canonical_by_id = {row.lemma_id: row.canonical_lemma_id for row in lemma_rows}
    states = dict(
        db.query(
            UserLemmaKnowledge.lemma_id, UserLemmaKnowledge.knowledge_state
        ).all()
    )

    def is_inert_or_function(lemma_id: int) -> bool:
        row = by_id.get(lemma_id)
        if row is None:
            return False
        if row.word_category in _INERT_CATEGORIES or row.pos == "particle":
            return True
        bare = normalize_alef(strip_diacritics(row.lemma_ar_bare or ""))
        return _is_function_word(bare)

    results: list[BookCoverageOut] = []
    cohort = compute_source_cohort(db, cohort_source)

    for cache_key, mtime, data in tokenmaps:
        total = int(data["total"])
        mapped_tokens: Counter[int] = Counter(
            {int(lid): int(count) for lid, count in data["mapped"].items()}
        )
        unmapped_freq = {
            str(surface): int(count)
            for surface, count in (data.get("unmapped_freq") or {}).items()
        }

        # Surfaces the scan couldn't map may resolve now that later imports
        # (e.g. the Momo tranches) created their lemmas.
        resolved = _resolve_unmapped_surfaces(
            db, cache_key, mtime, list(unmapped_freq)
        )
        unmapped_tokens = 0
        unresolved_surfaces: list[tuple[str, int]] = []
        for surface, count in unmapped_freq.items():
            lemma_id = resolved.get(surface)
            if lemma_id is None:
                unmapped_tokens += count
                unresolved_surfaces.append((surface, count))
            else:
                mapped_tokens[lemma_id] += count

        covered = int(data.get("function") or 0)
        in_progress = 0
        gap_tokens = 0
        gap_by_lemma: Counter[int] = Counter()
        for lemma_id, count in mapped_tokens.items():
            canonical_id = resolve_canonical_via_map(lemma_id, canonical_by_id)
            if is_inert_or_function(canonical_id):
                covered += count
                continue
            state = states.get(canonical_id)
            if state in _KNOWN_STATES:
                covered += count
            elif state in _PROGRESS_STATES:
                in_progress += count
            else:
                gap_tokens += count
                gap_by_lemma[canonical_id] += count

        top_gaps = [
            BookGapWord(
                lemma_id=lemma_id,
                display=(by_id[lemma_id].lemma_ar if lemma_id in by_id else "?"),
                gloss_en=(by_id[lemma_id].gloss_en if lemma_id in by_id else None),
                tokens=count,
                status="new",
            )
            for lemma_id, count in gap_by_lemma.most_common(top_gap_count)
        ]
        remaining = max(0, top_gap_count - len(top_gaps))
        if remaining:
            unresolved_surfaces.sort(key=lambda item: -item[1])
            top_gaps.extend(
                BookGapWord(display=surface, tokens=count, status="unmapped")
                for surface, count in unresolved_surfaces[:remaining]
            )

        results.append(
            BookCoverageOut(
                title=str(data.get("title") or Path(cache_key).stem),
                target_pct=float(data.get("target_pct") or 95.0),
                total_tokens=total,
                covered_tokens=covered,
                in_progress_tokens=in_progress,
                gap_tokens=gap_tokens,
                unmapped_tokens=unmapped_tokens,
                covered_pct=round(covered / total * 100, 1),
                in_progress_pct=round((covered + in_progress) / total * 100, 1),
                top_gaps=top_gaps,
                cohort=cohort,
            )
        )
    return results
