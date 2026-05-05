#!/usr/bin/env python3
"""Build the ranked high-frequency core used for learning targets.

The core is a weighted, teachable-content list. Raw frequency sources are
surface-form lists, so this script maps each source form to Alif lemmas, keeps
unmapped entries as honest gaps, and de-duplicates by canonical lemma.

Default sources:
  - CAMeL MSA frequency list (downloaded/cached in backend/data)
  - Kelly/Leeds Arabic frequency list (downloaded/cached when available)

Optional ranked TSV/CSV sources can be added for Buckwalter/Parkinson,
arTenTen, Hindawi children/books, news/OSIAN-style corpora, or Islamic/
classical corpora.

Usage:
  cd backend
  PYTHONPATH=. python3 scripts/build_frequency_core.py --dry-run
  PYTHONPATH=. python3 scripts/build_frequency_core.py --entries 5000
"""

from __future__ import annotations

import argparse
import csv
import io
import math
import os
import re
import sys
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

os.environ.setdefault("ALIF_SKIP_MIGRATIONS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.models import FrequencyCoreEntry, Lemma, UserLemmaKnowledge
from app.services.sentence_validator import (
    _is_function_word,
    build_comprehensive_lemma_lookup,
    lookup_lemma,
    normalize_alef,
    strip_diacritics,
    strip_tatweel,
)
from app.services.word_selector import _is_noise_lemma


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CAMEL_URL = "https://github.com/CAMeL-Lab/Camel_Arabic_Frequency_Lists/releases/download/v1.0/MSA_freq_lists.tsv.zip"
CAMEL_CACHE = DATA_DIR / "MSA_freq_lists.tsv"
KELLY_HTML_URL = "http://corpus.leeds.ac.uk/frqc/arabic-m3.num.html"
KELLY_HTML_CACHE = DATA_DIR / "kelly_arabic_m3.html"

SOURCE_WEIGHTS = {
    "camel": 1000.0,
    "buckwalter": 900.0,
    "artenten": 800.0,
    "kelly": 600.0,
    "hindawi": 400.0,
    "news": 300.0,
    "islamic": 150.0,
}
BROAD_FREQUENCY_SOURCES = {"camel", "buckwalter", "artenten", "kelly"}

CEFR_BOOST = {"A1": 120.0, "A2": 75.0, "B1": 35.0, "B2": 15.0}
DB_SOURCE_BOOST = {
    "avp_a1": 50.0,
    "duolingo": 20.0,
    "textbook_scan": 10.0,
}

ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
ARABIC_ONLY_CLEAN_RE = re.compile(r"[^\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\u0640]")
KELLY_LINE_RE = re.compile(r"(\d+)\s+([\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+)\s+([\d.]+)")
HTML_CELL_RE = re.compile(
    r"<tr[^>]*>.*?<td[^>]*>(\d+)</td>.*?"
    r"<td[^>]*>([\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]+)</td>.*?"
    r"<td[^>]*>([\d.]+)</td>.*?</tr>",
    re.DOTALL,
)


@dataclass
class CoreCandidate:
    key: str
    display_form: str
    normalized: str
    lemma_id: int | None = None
    gloss_en: str | None = None
    pos: str | None = None
    score: float = 0.0
    camel_rank: int | None = None
    camel_count: int | None = None
    buckwalter_rank: int | None = None
    artenten_rank: int | None = None
    kelly_rank: int | None = None
    kelly_cefr: str | None = None
    hindawi_rank: int | None = None
    news_rank: int | None = None
    islamic_rank: int | None = None
    broad_source_count: int = 0
    confidence_tier: str = "low"
    gap_status: str | None = None
    best_rank: int = 1_000_000_000
    source_flags: dict[str, object] = field(default_factory=dict)


def normalize_form(text: str) -> str:
    """Normalize an Arabic source form for source de-duplication and DB lookup."""
    cleaned = ARABIC_ONLY_CLEAN_RE.sub("", text.strip())
    cleaned = strip_tatweel(strip_diacritics(cleaned))
    return normalize_alef(cleaned)


def rank_points(source: str, rank: int) -> float:
    weight = SOURCE_WEIGHTS[source]
    return weight / math.log2(rank + 2)


def rank_to_cefr(rank: int) -> str:
    if rank <= 500:
        return "A1"
    if rank <= 1200:
        return "A2"
    if rank <= 2500:
        return "B1"
    if rank <= 5000:
        return "B2"
    if rank <= 8000:
        return "C1"
    return "C2"


def download_bytes(url: str, timeout: int = 120) -> bytes:
    import requests

    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def load_camel_counts(path: Path | None = None) -> list[tuple[str, int]]:
    """Return CAMeL forms ranked by total normalized count."""
    cache = path or CAMEL_CACHE
    if not cache.exists():
        print("Downloading CAMeL MSA frequency list...")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        content = download_bytes(CAMEL_URL)
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            tsv_name = next(n for n in zf.namelist() if n.endswith(".tsv"))
            cache.write_bytes(zf.read(tsv_name))
        print(f"  cached {cache}")

    counts: dict[str, int] = defaultdict(int)
    display: dict[str, str] = {}
    with cache.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 2:
                continue
            form, count_s = parts
            norm = normalize_form(form)
            if not norm:
                continue
            try:
                count = int(count_s)
            except ValueError:
                continue
            counts[norm] += count
            display.setdefault(norm, form.strip())

    return sorted(
        ((display[norm], count) for norm, count in counts.items()),
        key=lambda item: item[1],
        reverse=True,
    )


def load_kelly_html(path: Path | None = None) -> list[tuple[str, int, str]]:
    """Return (form, rank, cefr) from the Kelly/Leeds HTML list."""
    cache = path or KELLY_HTML_CACHE
    if not cache.exists():
        print("Downloading Kelly/Leeds Arabic frequency list...")
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            cache.write_bytes(download_bytes(KELLY_HTML_URL, timeout=60))
            print(f"  cached {cache}")
        except Exception as exc:
            print(f"  Kelly download failed: {exc}")
            return []

    content = cache.read_text(encoding="utf-8", errors="replace")
    matches = KELLY_LINE_RE.findall(content) or HTML_CELL_RE.findall(content)
    rows: list[tuple[str, int, str]] = []
    seen: set[str] = set()
    for rank_s, form, _freq in matches:
        rank = int(rank_s)
        norm = normalize_form(form)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        rows.append((form, rank, rank_to_cefr(rank)))
    return rows


def sniff_rows(path: Path) -> list[list[str]]:
    sample = path.read_text(encoding="utf-8", errors="replace")[:4096]
    delimiter = "\t" if sample.count("\t") >= sample.count(",") else ","
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        return [row for row in csv.reader(f, delimiter=delimiter) if row and any(c.strip() for c in row)]


def load_ranked_file(path: Path) -> list[tuple[str, int, int | None, str | None]]:
    """Load a simple ranked TSV/CSV file.

    Supported headers: word/form/lemma/arabic, rank/frequency_rank, count/freq,
    cefr/level. Headerless files may be either `rank<TAB>word` or
    `word<TAB>count`; count-only rows are sorted descending and ranked here.
    """
    rows = sniff_rows(path)
    if not rows:
        return []

    header = [c.strip().lower() for c in rows[0]]
    has_header = any(h in header for h in ("word", "form", "lemma", "arabic", "rank", "count", "cefr"))
    body = rows[1:] if has_header else rows

    parsed: list[tuple[str, int | None, int | None, str | None, int]] = []
    if has_header:
        def find_col(names: tuple[str, ...]) -> int | None:
            for name in names:
                if name in header:
                    return header.index(name)
            return None

        word_col = find_col(("word", "form", "lemma", "arabic", "token"))
        rank_col = find_col(("rank", "frequency_rank"))
        count_col = find_col(("count", "freq", "frequency"))
        cefr_col = find_col(("cefr", "level"))
        if word_col is None:
            return []
        for order, row in enumerate(body, 1):
            if word_col >= len(row):
                continue
            form = row[word_col].strip()
            rank = parse_int(row[rank_col]) if rank_col is not None and rank_col < len(row) else None
            count = parse_int(row[count_col]) if count_col is not None and count_col < len(row) else None
            cefr = row[cefr_col].strip().upper() if cefr_col is not None and cefr_col < len(row) else None
            parsed.append((form, rank, count, cefr if cefr else None, order))
    else:
        for order, row in enumerate(body, 1):
            cells = [c.strip() for c in row if c.strip()]
            if not cells:
                continue
            rank = None
            count = None
            if len(cells) >= 2 and parse_int(cells[0]) is not None and ARABIC_RE.search(cells[1]):
                rank = parse_int(cells[0])
                form = cells[1]
            else:
                form = cells[0]
                if len(cells) >= 2:
                    count = parse_int(cells[1])
            parsed.append((form, rank, count, None, order))

    if any(rank is None for _form, rank, count, _cefr, _order in parsed) and any(count for _f, _r, count, _c, _o in parsed):
        parsed.sort(key=lambda r: r[2] or 0, reverse=True)

    result: list[tuple[str, int, int | None, str | None]] = []
    for order, (form, rank, count, cefr, _original_order) in enumerate(parsed, 1):
        if rank is None:
            rank = order
        result.append((form, rank, count, cefr))
    return result


def parse_int(value: object) -> int | None:
    try:
        if value is None:
            return None
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        return int(float(text))
    except ValueError:
        return None


def should_skip_norm(norm: str, include_function_words: bool) -> bool:
    if not norm or len(norm) < 2:
        return True
    if not ARABIC_RE.search(norm):
        return True
    if not include_function_words and _is_function_word(norm):
        return True
    return False


def should_skip_lemma(lemma: Lemma, include_function_words: bool) -> bool:
    if _is_noise_lemma(lemma):
        return True
    if lemma.word_category in {"proper_name", "onomatopoeia"}:
        return True
    if not include_function_words and lemma.lemma_ar_bare and _is_function_word(lemma.lemma_ar_bare):
        return True
    return False


def resolve_lemma_id(norm: str, original: str, lemma_lookup: dict[str, int]) -> int | None:
    if not norm:
        return None
    return lookup_lemma(norm, lemma_lookup, original_bare=strip_diacritics(original))


def candidate_key(lemma_id: int | None, norm: str) -> str:
    return f"lemma:{lemma_id}" if lemma_id is not None else f"missing:{norm}"


def add_source(
    candidates: dict[str, CoreCandidate],
    *,
    form: str,
    source: str,
    rank: int,
    lemma_lookup: dict[str, int],
    lemmas_by_id: dict[int, Lemma],
    count: int | None = None,
    cefr: str | None = None,
    include_function_words: bool = False,
) -> None:
    norm = normalize_form(form)
    if should_skip_norm(norm, include_function_words):
        return

    lemma_id = resolve_lemma_id(norm, form, lemma_lookup)
    lemma = lemmas_by_id.get(lemma_id) if lemma_id is not None else None
    if lemma is not None and should_skip_lemma(lemma, include_function_words):
        return

    key = candidate_key(lemma_id, norm)
    cand = candidates.get(key)
    if cand is None:
        cand = CoreCandidate(
            key=key,
            display_form=lemma.lemma_ar if lemma is not None else form,
            normalized=norm,
            lemma_id=lemma_id,
            gloss_en=lemma.gloss_en if lemma is not None else None,
            pos=lemma.pos if lemma is not None else None,
        )
        candidates[key] = cand

    points = rank_points(source, rank)
    previous = cand.source_flags.get(source)
    if isinstance(previous, dict):
        old_points = float(previous.get("points") or 0.0)
        if points > old_points:
            cand.score += points - old_points
        previous["rank"] = min(int(previous.get("rank") or rank), rank)
        previous["points"] = round(max(points, old_points), 2)
        if count is not None:
            previous["count"] = int(previous.get("count") or 0) + count
    else:
        cand.score += points
        cand.source_flags[source] = {"rank": rank, "points": round(points, 2)}
        if count is not None:
            cand.source_flags[source]["count"] = count
    cand.best_rank = min(cand.best_rank, rank)

    if source == "camel":
        cand.camel_rank = rank if cand.camel_rank is None else min(cand.camel_rank, rank)
        cand.camel_count = (cand.camel_count or 0) + (count or 0)
    elif source == "buckwalter":
        cand.buckwalter_rank = rank if cand.buckwalter_rank is None else min(cand.buckwalter_rank, rank)
    elif source == "artenten":
        cand.artenten_rank = rank if cand.artenten_rank is None else min(cand.artenten_rank, rank)
    elif source == "kelly":
        previous_cefr = cand.kelly_cefr
        previous_boost = CEFR_BOOST.get(previous_cefr or "", 0.0)
        if cand.kelly_rank is None or rank < cand.kelly_rank:
            cand.kelly_rank = rank
            if cefr:
                cand.kelly_cefr = cefr
                cand.score += max(0.0, CEFR_BOOST.get(cefr, 0.0) - previous_boost)
                cand.source_flags["kelly"]["cefr"] = cefr
    elif source == "hindawi":
        cand.hindawi_rank = rank if cand.hindawi_rank is None else min(cand.hindawi_rank, rank)
    elif source == "news":
        cand.news_rank = rank if cand.news_rank is None else min(cand.news_rank, rank)
    elif source == "islamic":
        cand.islamic_rank = rank if cand.islamic_rank is None else min(cand.islamic_rank, rank)


def finalize_candidate_confidence(cand: CoreCandidate) -> None:
    broad_sources = {
        source for source in BROAD_FREQUENCY_SOURCES
        if source in cand.source_flags
    }
    cand.broad_source_count = len(broad_sources)
    if cand.lemma_id is None:
        cand.confidence_tier = "low"
        cand.gap_status = "unmapped"
        return
    if cand.broad_source_count >= 2:
        cand.confidence_tier = "high"
    elif cand.broad_source_count >= 1 and (
        "hindawi" in cand.source_flags
        or "news" in cand.source_flags
        or cand.kelly_cefr in {"A1", "A2"}
    ):
        cand.confidence_tier = "medium"
    elif cand.kelly_cefr in {"A1", "A2"}:
        cand.confidence_tier = "medium"
    else:
        cand.confidence_tier = "low"
    cand.gap_status = None


def add_db_source_boosts(
    candidates: dict[str, CoreCandidate],
    lemmas: Iterable[Lemma],
    include_function_words: bool,
) -> None:
    for lemma in lemmas:
        if should_skip_lemma(lemma, include_function_words):
            continue
        boost = DB_SOURCE_BOOST.get(lemma.source or "", 0.0)
        if boost <= 0:
            continue
        norm = normalize_form(lemma.lemma_ar_bare or lemma.lemma_ar)
        if should_skip_norm(norm, include_function_words):
            continue
        key = candidate_key(lemma.lemma_id, norm)
        cand = candidates.get(key)
        if cand is None:
            cand = CoreCandidate(
                key=key,
                display_form=lemma.lemma_ar,
                normalized=norm,
                lemma_id=lemma.lemma_id,
                gloss_en=lemma.gloss_en,
                pos=lemma.pos,
            )
            candidates[key] = cand
        cand.score += boost
        cand.source_flags[f"db_{lemma.source}"] = {"points": boost}


def build_candidates(args: argparse.Namespace) -> list[CoreCandidate]:
    db = SessionLocal()
    try:
        lemma_lookup = build_comprehensive_lemma_lookup(db)
        lemmas = db.query(Lemma).filter(Lemma.canonical_lemma_id.is_(None)).all()
        lemmas_by_id = {lemma.lemma_id: lemma for lemma in lemmas}
        candidates: dict[str, CoreCandidate] = {}

        if not args.no_camel:
            camel_rows = load_camel_counts(args.camel_path)
            for rank, (form, count) in enumerate(camel_rows[: args.camel_limit], 1):
                add_source(
                    candidates,
                    form=form,
                    source="camel",
                    rank=rank,
                    count=count,
                    lemma_lookup=lemma_lookup,
                    lemmas_by_id=lemmas_by_id,
                    include_function_words=args.include_function_words,
                )

        if not args.no_kelly:
            for form, rank, cefr in load_kelly_html(args.kelly_path):
                if rank > args.kelly_limit:
                    continue
                add_source(
                    candidates,
                    form=form,
                    source="kelly",
                    rank=rank,
                    cefr=cefr,
                    lemma_lookup=lemma_lookup,
                    lemmas_by_id=lemmas_by_id,
                    include_function_words=args.include_function_words,
                )

        optional_sources = (
            ("buckwalter", args.buckwalter),
            ("artenten", args.artenten),
            ("hindawi", args.hindawi),
            ("news", args.news),
            ("islamic", args.islamic),
        )
        for source, path in optional_sources:
            if not path:
                continue
            for form, rank, count, cefr in load_ranked_file(path):
                add_source(
                    candidates,
                    form=form,
                    source=source,
                    rank=rank,
                    count=count,
                    cefr=cefr,
                    lemma_lookup=lemma_lookup,
                    lemmas_by_id=lemmas_by_id,
                    include_function_words=args.include_function_words,
                )

        add_db_source_boosts(candidates, lemmas, args.include_function_words)
        for cand in candidates.values():
            finalize_candidate_confidence(cand)
        ranked = sorted(candidates.values(), key=lambda c: (-c.score, c.best_rank, c.normalized))
        return ranked[: args.entries]
    finally:
        db.close()


def write_entries(entries: list[CoreCandidate], dry_run: bool) -> None:
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        if dry_run:
            print(f"[dry-run] would replace frequency_core_entries with {len(entries)} rows")
            print_summary(db, entries)
            return

        db.query(FrequencyCoreEntry).delete()
        for core_rank, cand in enumerate(entries, 1):
            db.add(FrequencyCoreEntry(
                core_rank=core_rank,
                lemma_id=cand.lemma_id,
                lemma_key=cand.key,
                display_form=cand.display_form,
                gloss_en=cand.gloss_en,
                pos=cand.pos,
                score=round(cand.score, 4),
                camel_rank=cand.camel_rank,
                camel_count=cand.camel_count,
                buckwalter_rank=cand.buckwalter_rank,
                artenten_rank=cand.artenten_rank,
                kelly_rank=cand.kelly_rank,
                kelly_cefr=cand.kelly_cefr,
                hindawi_rank=cand.hindawi_rank,
                news_rank=cand.news_rank,
                islamic_rank=cand.islamic_rank,
                broad_source_count=cand.broad_source_count,
                confidence_tier=cand.confidence_tier,
                gap_status=cand.gap_status,
                source_flags_json=cand.source_flags,
                excluded_reason=None,
                created_at=now,
                updated_at=now,
            ))
        db.commit()
        print(f"Replaced frequency_core_entries with {len(entries)} rows")
        print_summary(db, entries)
    finally:
        db.close()


def print_summary(db, entries: list[CoreCandidate]) -> None:
    states = dict(db.query(UserLemmaKnowledge.lemma_id, UserLemmaKnowledge.knowledge_state).all())
    learned_states = {"known", "learning"}
    pipeline_states = learned_states | {"acquiring", "lapsed", "encountered"}

    print("\nCoverage preview:")
    for top_n in (100, 250, 500, 1000, 2000, 5000):
        band = entries[:top_n]
        if not band:
            continue
        learned = sum(1 for cand in band if cand.lemma_id is not None and states.get(cand.lemma_id) in learned_states)
        pipeline = sum(1 for cand in band if cand.lemma_id is not None and states.get(cand.lemma_id) in pipeline_states)
        print(f"  top {top_n:4d}: learned {learned:4d}/{len(band):4d}, pipeline {pipeline:4d}/{len(band):4d}")
    print("\nConfidence preview:")
    for tier in ("high", "medium", "low"):
        print(f"  {tier:6s}: {sum(1 for cand in entries if cand.confidence_tier == tier):4d}")
    unresolved_top_500 = sum(1 for cand in entries[:500] if cand.lemma_id is None or cand.confidence_tier == "low")
    print(f"  unresolved/low-confidence in top 500: {unresolved_top_500}")

    # `prefix` mirrors the API's `learned_prefix_count` in stats._compute_frequency_core_progress:
    # the continuous learned prefix from rank 1, NOT the highest rank ever learned. Lock it on
    # the first gap so a single gap doesn't get masked by later learned entries.
    prefix = 0
    prefix_locked = False
    gaps: list[tuple[int, CoreCandidate, str]] = []
    for rank, cand in enumerate(entries, 1):
        state = states.get(cand.lemma_id) if cand.lemma_id is not None else "missing_from_db"
        if state in learned_states:
            if not prefix_locked:
                prefix = rank
            continue
        prefix_locked = True
        if len(gaps) < 12:
            gaps.append((rank, cand, state or "new"))
        if len(gaps) >= 12:
            break

    print(f"  continuous learned prefix: top {prefix}")
    if gaps:
        print("  next gaps:")
        for rank, cand, state in gaps:
            gloss = f" — {cand.gloss_en}" if cand.gloss_en else ""
            print(f"    #{rank}: {cand.display_form}{gloss} ({state})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build weighted high-frequency core list")
    parser.add_argument("--entries", type=int, default=5000, help="Number of ranked rows to write")
    parser.add_argument("--dry-run", action="store_true", help="Preview without replacing frequency_core_entries")
    parser.add_argument("--include-function-words", action="store_true", help="Include grammar/function words in the ranked core")
    parser.add_argument("--no-camel", action="store_true", help="Do not use CAMeL frequency data")
    parser.add_argument("--no-kelly", action="store_true", help="Do not use Kelly/Leeds frequency data")
    parser.add_argument("--camel-path", type=Path, default=None, help="Local CAMeL MSA_freq_lists.tsv path")
    parser.add_argument("--kelly-path", type=Path, default=None, help="Local Kelly/Leeds HTML path")
    parser.add_argument("--camel-limit", type=int, default=50000, help="Max CAMeL forms to consider before filtering")
    parser.add_argument("--kelly-limit", type=int, default=9000, help="Max Kelly ranks to consider before filtering")
    parser.add_argument("--buckwalter", type=Path, default=None, help="Optional Buckwalter/Parkinson ranked TSV/CSV")
    parser.add_argument("--artenten", type=Path, default=None, help="Optional arTenTen lemma/POS ranked TSV/CSV")
    parser.add_argument("--hindawi", type=Path, default=None, help="Optional Hindawi/book ranked TSV/CSV")
    parser.add_argument("--news", type=Path, default=None, help="Optional news/OSIAN ranked TSV/CSV")
    parser.add_argument("--islamic", type=Path, default=None, help="Optional Islamic/classical ranked TSV/CSV")
    args = parser.parse_args()

    entries = build_candidates(args)
    write_entries(entries, args.dry_run)


if __name__ == "__main__":
    main()
