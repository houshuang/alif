#!/usr/bin/env python3
"""Rank authentic Hindawi children's-book passage windows by current knowledge.

This is a read-only scouting tool for longer passage work. It finds consecutive
3-5 sentence windows in the raw Hindawi parquet and scores them against the
current production lemma state, so promising authentic passages can be promoted
through the existing Story + Sentence(source="passage") path later.

Usage:
    DATABASE_URL=sqlite:///data/alif.db \
      python3 scripts/rank_hindawi_passages.py --parquet /tmp/hindawi.parquet

    python3 scripts/rank_hindawi_passages.py \
      --db /opt/alif/backend/data/alif.db \
      --parquet /tmp/hindawi.parquet \
      --title "لَيْلَى وَالذِّئْبُ" --include-text
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


ACTIVE_STATES = {"known", "learning", "acquiring", "lapsed"}
KNOWN_STATES = {"known"}
SKIP_CATEGORIES = {"proper_name", "onomatopoeia", "junk"}
STATE_RANK = {
    "suspended": 0,
    "encountered": 1,
    "new": 1,
    "lapsed": 2,
    "acquiring": 3,
    "learning": 4,
    "known": 5,
}


@dataclass
class LemmaInfo:
    lemma_id: int
    arabic: str
    bare: str
    gloss: str
    pos: str
    state: str | None = None
    rank: int | None = None
    word_category: str | None = None
    canonical_lemma_id: int | None = None


@dataclass
class SentenceCoverage:
    text: str
    content_tokens: int
    known_tokens: int
    active_tokens: int
    missing: Counter[int] = field(default_factory=Counter)
    unmapped: Counter[str] = field(default_factory=Counter)

    @property
    def unmapped_tokens(self) -> int:
        return sum(self.unmapped.values())

    @property
    def mapped_missing_tokens(self) -> int:
        return sum(self.missing.values())


@dataclass
class PassageWindow:
    title: str
    author: str
    start_index: int
    sentences: list[SentenceCoverage]

    @property
    def content_tokens(self) -> int:
        return sum(s.content_tokens for s in self.sentences)

    @property
    def known_tokens(self) -> int:
        return sum(s.known_tokens for s in self.sentences)

    @property
    def active_tokens(self) -> int:
        return sum(s.active_tokens for s in self.sentences)

    @property
    def missing(self) -> Counter[int]:
        total: Counter[int] = Counter()
        for sent in self.sentences:
            total.update(sent.missing)
        return total

    @property
    def unmapped(self) -> Counter[str]:
        total: Counter[str] = Counter()
        for sent in self.sentences:
            total.update(sent.unmapped)
        return total

    @property
    def active_pct(self) -> float:
        return self.active_tokens / self.content_tokens if self.content_tokens else 0.0

    @property
    def known_pct(self) -> float:
        return self.known_tokens / self.content_tokens if self.content_tokens else 0.0

    @property
    def unmapped_pct(self) -> float:
        return sum(self.unmapped.values()) / self.content_tokens if self.content_tokens else 0.0

    @property
    def mapped_ceiling_pct(self) -> float:
        mapped = self.active_tokens + sum(self.missing.values())
        return mapped / self.content_tokens if self.content_tokens else 0.0

    def own_top_gain_pct(self, n: int) -> float:
        gain = sum(count for _lid, count in self.missing.most_common(n))
        return (self.active_tokens + gain) / self.content_tokens if self.content_tokens else 0.0

    def score(self) -> float:
        # Prefer currently readable windows, but keep an eye on the mapped
        # ceiling so a short pre-study list can lift promising candidates.
        return (
            self.active_pct * 100
            + self.own_top_gain_pct(10) * 20
            - self.unmapped_pct * 55
            - max(0, 24 - self.content_tokens) * 0.15
        )


class LemmaContext:
    def __init__(self, infos: dict[int, LemmaInfo], states: dict[int, str]):
        self.infos = infos
        self.states = states
        self.canonical_next = {
            lid: info.canonical_lemma_id
            for lid, info in infos.items()
            if info.canonical_lemma_id
        }
        self.active_ids = {
            lid for lid, state in states.items() if state in ACTIVE_STATES
        }
        self.known_ids = {
            lid for lid, state in states.items() if state in KNOWN_STATES
        }

    def canonical_id(self, lemma_id: int | None) -> int | None:
        if lemma_id is None:
            return None
        seen: set[int] = set()
        current = lemma_id
        while current in self.canonical_next and current not in seen:
            seen.add(current)
            next_id = self.canonical_next.get(current)
            if not next_id:
                break
            current = next_id
        return current

    def is_skipped(self, lemma_id: int | None) -> bool:
        canonical = self.canonical_id(lemma_id)
        if canonical is None:
            return False
        info = self.infos.get(canonical)
        return bool(info and (info.word_category or "standard") in SKIP_CATEGORIES)

    def label(self, lemma_id: int, count: int) -> str:
        info = self.infos.get(lemma_id)
        if not info:
            return f"#{lemma_id} x{count}"
        state = self.states.get(lemma_id) or "new"
        rank = f"rank {info.rank}" if info.rank is not None else "rank ?"
        return f"#{lemma_id} {info.arabic or info.bare} ({info.gloss}; {state}; {rank}) x{count}"


def _round_pct(value: float) -> float:
    return round(value * 100.0, 1)


def window_to_dict(
    window: PassageWindow,
    context: LemmaContext,
    include_text: bool = False,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "title": window.title,
        "author": window.author,
        "start_sentence": window.start_index + 1,
        "sentence_count": len(window.sentences),
        "content_tokens": window.content_tokens,
        "known_pct": _round_pct(window.known_pct),
        "active_pct": _round_pct(window.active_pct),
        "unmapped_pct": _round_pct(window.unmapped_pct),
        "mapped_ceiling_pct": _round_pct(window.mapped_ceiling_pct),
        "after_top_10_mapped_pct": _round_pct(window.own_top_gain_pct(10)),
        "after_top_25_mapped_pct": _round_pct(window.own_top_gain_pct(25)),
        "top_missing": [
            context.label(lid, count)
            for lid, count in window.missing.most_common(10)
        ],
        "top_unmapped": [
            f"{surface} x{count}"
            for surface, count in window.unmapped.most_common(10)
        ],
    }
    if include_text:
        data["sentences"] = [s.text for s in window.sentences]
    return data


def build_windows(
    title: str,
    author: str,
    sentences: list[SentenceCoverage],
    sentence_count: int,
) -> Iterable[PassageWindow]:
    if len(sentences) < sentence_count:
        return []
    return (
        PassageWindow(
            title=title,
            author=author,
            start_index=i,
            sentences=sentences[i:i + sentence_count],
        )
        for i in range(0, len(sentences) - sentence_count + 1)
    )


def _configure_database(db_path: str | None) -> None:
    if not db_path:
        return
    db_abs = Path(db_path).expanduser().resolve()
    os.environ["DATABASE_URL"] = f"sqlite:///{db_abs}"


def _load_runtime(disable_camel: bool = False):
    # Late imports keep the module importable in tests without pandas/CAMeL.
    repo_backend = Path(__file__).resolve().parents[1]
    if str(repo_backend) not in sys.path:
        sys.path.insert(0, str(repo_backend))
    import app.services.sentence_validator as sentence_validator
    from app.database import SessionLocal
    from app.models import Lemma, UserLemmaKnowledge
    from app.services.sentence_validator import (
        build_lemma_lookup,
        map_tokens_to_lemmas,
        normalize_alef,
        strip_diacritics,
        strip_punctuation,
        strip_tatweel,
        tokenize_display,
        _is_function_word,
    )
    from scripts.import_hindawi import extract_sentences

    if disable_camel:
        sentence_validator._camel_disambiguate = lambda word, lemma_lookup: None

    return {
        "SessionLocal": SessionLocal,
        "Lemma": Lemma,
        "UserLemmaKnowledge": UserLemmaKnowledge,
        "build_lemma_lookup": build_lemma_lookup,
        "map_tokens_to_lemmas": map_tokens_to_lemmas,
        "normalize_alef": normalize_alef,
        "strip_diacritics": strip_diacritics,
        "strip_punctuation": strip_punctuation,
        "strip_tatweel": strip_tatweel,
        "tokenize_display": tokenize_display,
        "_is_function_word": _is_function_word,
        "extract_sentences": extract_sentences,
    }


def _load_context(runtime) -> tuple[Any, LemmaContext]:
    SessionLocal = runtime["SessionLocal"]
    Lemma = runtime["Lemma"]
    UserLemmaKnowledge = runtime["UserLemmaKnowledge"]
    build_lemma_lookup = runtime["build_lemma_lookup"]

    db = SessionLocal()
    try:
        lemmas = db.query(Lemma).filter(Lemma.canonical_lemma_id.is_(None)).all()
        lookup = build_lemma_lookup(lemmas)

        all_lemmas = db.query(Lemma).all()
        infos = {
            l.lemma_id: LemmaInfo(
                lemma_id=l.lemma_id,
                arabic=l.lemma_ar or "",
                bare=l.lemma_ar_bare or "",
                gloss=l.gloss_en or "",
                pos=l.pos or "",
                rank=l.frequency_rank,
                word_category=l.word_category,
                canonical_lemma_id=l.canonical_lemma_id,
            )
            for l in all_lemmas
        }

        states: dict[int, str] = {}
        for ulk in db.query(UserLemmaKnowledge).all():
            lemma_id = ulk.lemma_id
            state = ulk.knowledge_state or "new"
            current = states.get(lemma_id)
            if STATE_RANK.get(state, 0) > STATE_RANK.get(current or "", -1):
                states[lemma_id] = state
        context = LemmaContext(infos, states)

        # Canonicalize user states after the context can resolve chains.
        canonical_states: dict[int, str] = {}
        for lemma_id, state in states.items():
            canonical = context.canonical_id(lemma_id)
            if canonical is None:
                continue
            current = canonical_states.get(canonical)
            if STATE_RANK.get(state, 0) > STATE_RANK.get(current or "", -1):
                canonical_states[canonical] = state
        context.states = canonical_states
        context.active_ids = {
            lid for lid, state in canonical_states.items() if state in ACTIVE_STATES
        }
        context.known_ids = {
            lid for lid, state in canonical_states.items() if state in KNOWN_STATES
        }
        return lookup, context
    finally:
        db.close()


def _clean_bare(surface: str, runtime) -> str:
    return runtime["normalize_alef"](
        runtime["strip_diacritics"](
            runtime["strip_punctuation"](
                runtime["strip_tatweel"](surface or "")
            )
        )
    )


def sentence_coverage(
    text: str,
    lookup,
    context: LemmaContext,
    runtime,
    proper_names: set[str] | None = None,
) -> SentenceCoverage:
    mappings = runtime["map_tokens_to_lemmas"](
        tokens=runtime["tokenize_display"](text),
        lemma_lookup=lookup,
        target_lemma_id=0,
        target_bare="",
        proper_names=proper_names,
    )
    coverage = SentenceCoverage(
        text=text,
        content_tokens=0,
        known_tokens=0,
        active_tokens=0,
    )
    for mapping in mappings:
        bare = _clean_bare(mapping.surface_form, runtime)
        if not bare or len(bare) <= 1:
            continue
        if mapping.is_function_word or runtime["_is_function_word"](bare):
            continue
        canonical = context.canonical_id(mapping.lemma_id)
        if canonical and context.is_skipped(canonical):
            continue
        coverage.content_tokens += 1
        if canonical is None:
            coverage.unmapped[bare] += 1
            continue
        if canonical in context.known_ids:
            coverage.known_tokens += 1
        if canonical in context.active_ids:
            coverage.active_tokens += 1
        else:
            coverage.missing[canonical] += 1
    return coverage


def rank_books(
    books,
    lookup,
    context: LemmaContext,
    runtime,
    *,
    sentence_count: int,
    min_words: int,
    max_words: int,
    title_filter: str | None,
    min_active_pct: float,
    max_unmapped_pct: float,
) -> list[PassageWindow]:
    all_windows: list[PassageWindow] = []
    extract_sentences = runtime["extract_sentences"]

    for _, book in books.iterrows():
        title = str(book.get("title") or "")
        if title_filter and title_filter not in title:
            continue
        raw_sentences = extract_sentences(
            str(book.get("text") or ""),
            min_words=min_words,
            max_words=max_words,
        )
        if len(raw_sentences) < sentence_count:
            continue
        covered = [
            sentence_coverage(text, lookup, context, runtime)
            for text in raw_sentences
        ]
        covered = [s for s in covered if s.content_tokens > 0]
        for window in build_windows(
            title=title,
            author=str(book.get("author") or ""),
            sentences=covered,
            sentence_count=sentence_count,
        ):
            if window.active_pct < min_active_pct:
                continue
            if window.unmapped_pct > max_unmapped_pct:
                continue
            all_windows.append(window)

    all_windows.sort(key=lambda w: w.score(), reverse=True)
    return all_windows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rank Hindawi children's-book passage windows by current lemma knowledge"
    )
    parser.add_argument("--parquet", required=True, help="Path to Hindawi parquet")
    parser.add_argument("--db", help="SQLite DB path; overrides DATABASE_URL")
    parser.add_argument("--category", default="children", help="Category filter")
    parser.add_argument("--title", help="Restrict to book titles containing this text")
    parser.add_argument("--sentence-count", type=int, default=4)
    parser.add_argument("--min-words", type=int, default=5)
    parser.add_argument("--max-words", type=int, default=18)
    parser.add_argument("--min-active-pct", type=float, default=0.72)
    parser.add_argument("--max-unmapped-pct", type=float, default=0.20)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--include-text", action="store_true")
    parser.add_argument(
        "--disable-camel",
        action="store_true",
        help="Skip CAMeL fallback for fast broad scans; rerun top candidates without this flag",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    _configure_database(args.db)
    runtime = _load_runtime(disable_camel=args.disable_camel)

    try:
        import pandas as pd
    except ImportError as exc:
        raise SystemExit("pandas/pyarrow are required to read the Hindawi parquet") from exc

    lookup, context = _load_context(runtime)
    df = pd.read_parquet(args.parquet)
    books = df[df["category"].str.contains(args.category, case=False, na=False)]

    windows = rank_books(
        books,
        lookup,
        context,
        runtime,
        sentence_count=max(3, min(5, args.sentence_count)),
        min_words=args.min_words,
        max_words=args.max_words,
        title_filter=args.title,
        min_active_pct=args.min_active_pct,
        max_unmapped_pct=args.max_unmapped_pct,
    )[: args.limit]

    rows = [window_to_dict(w, context, include_text=args.include_text) for w in windows]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    for idx, row in enumerate(rows, start=1):
        print(
            f"{idx}. {row['title']} — sentence {row['start_sentence']} "
            f"({row['active_pct']}% active, {row['unmapped_pct']}% unmapped, "
            f"{row['after_top_10_mapped_pct']}% after top 10 mapped)"
        )
        if row["top_missing"]:
            print("   missing:", "; ".join(row["top_missing"][:5]))
        if row["top_unmapped"]:
            print("   unmapped:", "; ".join(row["top_unmapped"][:5]))
        if args.include_text:
            for sentence in row["sentences"]:
                print(f"   - {sentence}")


if __name__ == "__main__":
    main()
