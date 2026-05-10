#!/usr/bin/env python3
"""Promote a curated Hindawi passage window into maintenance passage cards.

This script is deliberately conservative: it does not bulk-import Hindawi text
into review. It promotes one hand-selected window after checking coverage,
selecting at most one or two review targets, validating translations, and
storing through the same Story + Sentence(source="passage") path used by
generated maintenance passages.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts.rank_hindawi_passages import (
    ACTIVE_STATES,
    PassageWindow,
    SentenceCoverage,
    LemmaContext,
    _clean_bare,
    _configure_database,
    _load_context,
    _load_runtime,
    sentence_coverage,
    window_to_dict,
)


MAINTENANCE_STATES = {"known", "learning", "lapsed"}
DEFAULT_MIN_ACTIVE_PCT = 0.90
DEFAULT_MAX_UNMAPPED_PCT = 0.0
DEFAULT_MAX_TARGETS = 1


class PromotionError(RuntimeError):
    pass


@dataclass(frozen=True)
class TargetCandidate:
    lemma_id: int
    arabic: str
    bare: str
    gloss: str
    pos: str
    state: str
    occurrence_count: int
    surface_forms: tuple[str, ...]
    due_at: datetime | None = None
    frequency_rank: int | None = None

    @property
    def due_now(self) -> bool:
        if self.state not in MAINTENANCE_STATES or self.due_at is None:
            return False
        return self.due_at <= datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lemma_id": self.lemma_id,
            "arabic": self.arabic,
            "bare": self.bare,
            "gloss": self.gloss,
            "pos": self.pos,
            "state": self.state,
            "occurrence_count": self.occurrence_count,
            "surface_forms": list(self.surface_forms),
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "due_now": self.due_now,
            "frequency_rank": self.frequency_rank,
        }


def _ensure_backend_path() -> None:
    if str(_BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(_BACKEND_ROOT))


def _aware_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _parse_due(card_json: Any) -> datetime | None:
    if not card_json:
        return None
    if isinstance(card_json, str):
        try:
            card_json = json.loads(card_json)
        except json.JSONDecodeError:
            return None
    if not isinstance(card_json, dict):
        return None
    due_raw = card_json.get("due")
    if not due_raw:
        return None
    try:
        return _aware_datetime(datetime.fromisoformat(str(due_raw)))
    except ValueError:
        return None


def _extract_window(
    sentences: list[str],
    start_sentence: int,
    sentence_count: int,
) -> list[str]:
    if start_sentence < 1:
        raise PromotionError("--start-sentence is 1-based and must be >= 1")
    if sentence_count < 3 or sentence_count > 5:
        raise PromotionError("--sentence-count must be between 3 and 5")
    start = start_sentence - 1
    end = start + sentence_count
    if end > len(sentences):
        raise PromotionError(
            f"Requested sentences {start_sentence}-{end}, but the book only has "
            f"{len(sentences)} eligible sentences"
        )
    return sentences[start:end]


def _content_counts(
    arabic_sentences: list[str],
    lookup: Any,
    context: LemmaContext,
    runtime: dict[str, Any],
) -> tuple[Counter[int], dict[int, set[str]]]:
    counts: Counter[int] = Counter()
    surfaces: dict[int, set[str]] = {}
    for sentence in arabic_sentences:
        mappings = runtime["map_tokens_to_lemmas"](
            tokens=runtime["tokenize_display"](sentence),
            lemma_lookup=lookup,
            target_lemma_id=0,
            target_bare="",
        )
        for mapping in mappings:
            bare = _clean_bare(mapping.surface_form, runtime)
            if not bare or len(bare) <= 1:
                continue
            if mapping.is_function_word or runtime["_is_function_word"](bare):
                continue
            canonical = context.canonical_id(mapping.lemma_id)
            if canonical is None or context.is_skipped(canonical):
                continue
            counts[canonical] += 1
            surfaces.setdefault(canonical, set()).add(mapping.surface_form)
    return counts, surfaces


def _target_word_dict(lemma: Any, state: str | None) -> dict[str, Any]:
    return {
        "lemma_id": lemma.lemma_id,
        "arabic": lemma.lemma_ar,
        "arabic_bare": lemma.lemma_ar_bare,
        "english": lemma.gloss_en or "",
        "pos": lemma.pos or "",
        "state": state or "new",
        "forms_json": lemma.forms_json,
    }


def _eligible_words_for_window(
    db: Any,
    content_counts: Counter[int],
) -> list[dict[str, Any]]:
    from app.models import Lemma, UserLemmaKnowledge

    if not content_counts:
        return []
    lemmas = (
        db.query(Lemma)
        .filter(Lemma.lemma_id.in_(list(content_counts.keys())))
        .all()
    )
    ulks = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id.in_([l.lemma_id for l in lemmas]))
        .all()
    )
    states = {ulk.lemma_id: ulk.knowledge_state for ulk in ulks}
    return [
        _target_word_dict(lemma, states.get(lemma.lemma_id))
        for lemma in lemmas
    ]


def _candidate_sort_key(candidate: TargetCandidate) -> tuple[Any, ...]:
    if candidate.due_now:
        state_bucket = 0
    elif candidate.state in MAINTENANCE_STATES:
        state_bucket = 1
    elif candidate.state == "acquiring":
        state_bucket = 2
    else:
        state_bucket = 3

    repeated_bucket = 0 if candidate.occurrence_count >= 2 else 1
    pos = (candidate.pos or "").lower()
    if pos.startswith("noun"):
        pos_bucket = 0
    elif pos.startswith("verb"):
        pos_bucket = 1
    else:
        pos_bucket = 2

    due_sort = candidate.due_at or datetime.max.replace(tzinfo=timezone.utc)
    rank_sort = candidate.frequency_rank or 999_999
    return (
        state_bucket,
        repeated_bucket,
        pos_bucket,
        due_sort,
        rank_sort,
        -candidate.occurrence_count,
        candidate.lemma_id,
    )


def _candidate_targets(
    db: Any,
    content_counts: Counter[int],
    surfaces: dict[int, set[str]],
    context: LemmaContext,
) -> list[TargetCandidate]:
    _ensure_backend_path()
    from app.models import Lemma, UserLemmaKnowledge
    from app.services.sentence_validator import _is_function_word

    if not content_counts:
        return []

    lemmas = (
        db.query(Lemma)
        .filter(Lemma.lemma_id.in_(list(content_counts.keys())))
        .all()
    )
    ulks = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id.in_([lemma.lemma_id for lemma in lemmas]))
        .all()
    )
    ulk_by_lid = {ulk.lemma_id: ulk for ulk in ulks}

    candidates: list[TargetCandidate] = []
    for lemma in lemmas:
        if context.is_skipped(lemma.lemma_id):
            continue
        if not lemma.gloss_en:
            continue
        if lemma.lemma_ar_bare and _is_function_word(lemma.lemma_ar_bare):
            continue
        ulk = ulk_by_lid.get(lemma.lemma_id)
        state = (
            ulk.knowledge_state
            if ulk is not None
            else context.states.get(lemma.lemma_id)
        ) or "new"
        if state not in ACTIVE_STATES:
            continue
        candidates.append(
            TargetCandidate(
                lemma_id=lemma.lemma_id,
                arabic=lemma.lemma_ar or "",
                bare=lemma.lemma_ar_bare or "",
                gloss=lemma.gloss_en or "",
                pos=lemma.pos or "",
                state=state,
                occurrence_count=int(content_counts[lemma.lemma_id]),
                surface_forms=tuple(sorted(surfaces.get(lemma.lemma_id, set()))),
                due_at=_parse_due(ulk.fsrs_card_json if ulk else None),
                frequency_rank=lemma.frequency_rank,
            )
        )

    return sorted(candidates, key=_candidate_sort_key)


def _select_target_ids(
    db: Any,
    content_counts: Counter[int],
    surfaces: dict[int, set[str]],
    context: LemmaContext,
    *,
    explicit_target_ids: list[int] | None = None,
    max_targets: int = DEFAULT_MAX_TARGETS,
) -> tuple[list[int], list[TargetCandidate]]:
    max_targets = max(1, min(2, max_targets))
    candidates = _candidate_targets(db, content_counts, surfaces, context)
    candidate_by_id = {candidate.lemma_id: candidate for candidate in candidates}

    if explicit_target_ids:
        selected: list[int] = []
        for raw_id in explicit_target_ids:
            canonical = context.canonical_id(raw_id)
            if canonical is None or canonical not in candidate_by_id:
                raise PromotionError(
                    f"Requested target lemma #{raw_id} is not an active, eligible "
                    "content word in this window"
                )
            if canonical not in selected:
                selected.append(canonical)
        if len(selected) > max_targets:
            raise PromotionError(
                f"Requested {len(selected)} targets, but max target count is {max_targets}"
            )
        return selected, candidates

    selected = [candidate.lemma_id for candidate in candidates[:max_targets]]
    if not selected:
        raise PromotionError("No active eligible target words found in this window")
    return selected, candidates


def _target_words_for_ids(db: Any, lemma_ids: list[int]) -> list[dict[str, Any]]:
    from app.models import Lemma, UserLemmaKnowledge

    lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all()
    lemma_by_id = {lemma.lemma_id: lemma for lemma in lemmas}
    ulks = (
        db.query(UserLemmaKnowledge)
        .filter(UserLemmaKnowledge.lemma_id.in_(lemma_ids))
        .all()
    )
    states = {ulk.lemma_id: ulk.knowledge_state for ulk in ulks}
    missing = [lid for lid in lemma_ids if lid not in lemma_by_id]
    if missing:
        raise PromotionError(f"Target lemmas not found: {missing}")
    return [
        _target_word_dict(lemma_by_id[lid], states.get(lid))
        for lid in lemma_ids
    ]


def _build_generated_payload(
    title_ar: str,
    title_en: str,
    arabic_sentences: list[str],
    english_sentences: list[str],
) -> dict[str, Any]:
    if len(arabic_sentences) != len(english_sentences):
        raise PromotionError("Arabic sentence count and translation count differ")
    return {
        "title_ar": title_ar,
        "title_en": title_en,
        "style_tag": "hindawi_authentic",
        "sentences": [
            {"arabic": arabic, "english": english}
            for arabic, english in zip(arabic_sentences, english_sentences)
        ],
    }


def _load_translations(path: str | None, sentence_count: int) -> list[str] | None:
    if not path:
        return None
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("sentences")
    if not isinstance(data, list):
        raise PromotionError("--translations-json must contain a list or {'sentences': [...]}")
    translations: list[str] = []
    for item in data:
        if isinstance(item, str):
            translations.append(item)
        elif isinstance(item, dict) and item.get("english"):
            translations.append(str(item["english"]))
        else:
            raise PromotionError("Every translation item needs an English string")
    if len(translations) != sentence_count:
        raise PromotionError(
            f"Expected {sentence_count} translations, got {len(translations)}"
        )
    return translations


TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "arabic": {"type": "string"},
                    "english": {"type": "string"},
                },
                "required": ["arabic", "english"],
            },
        },
    },
    "required": ["sentences"],
}


def _translate_sentences(arabic_sentences: list[str]) -> list[str]:
    _ensure_backend_path()
    from app.services.llm import generate_completion

    numbered = "\n".join(
        f"{idx + 1}. {sentence}"
        for idx, sentence in enumerate(arabic_sentences)
    )
    result = generate_completion(
        prompt=(
            "Translate these MSA Arabic sentences into concise, natural English. "
            "Keep sentence order and do not add explanations.\n\n"
            f"{numbered}"
        ),
        system_prompt=(
            "You translate Arabic reading material literally enough for language "
            "study but naturally enough to be readable."
        ),
        json_schema=TRANSLATION_SCHEMA,
        temperature=0,
        timeout=120,
        model_override="claude_haiku",
        task_type="hindawi_passage_translation",
    )
    if not isinstance(result, dict) or not isinstance(result.get("sentences"), list):
        raise PromotionError("Translation call returned invalid JSON")
    translations = [
        str(item.get("english") or "").strip()
        for item in result["sentences"]
        if isinstance(item, dict)
    ]
    if len(translations) != len(arabic_sentences) or any(not t for t in translations):
        raise PromotionError("Translation call returned missing translations")
    return translations


def _find_duplicate_story(db: Any, body_ar: str) -> Any | None:
    from app.models import Story

    return (
        db.query(Story)
        .filter(
            Story.format_type == "maintenance_passage",
            Story.body_ar == body_ar,
        )
        .first()
    )


def _resolve_book(books: Any, title: str | None, book_index: int | None) -> tuple[Any, int]:
    rows = books
    if title:
        rows = rows[rows["title"].astype(str).str.contains(title, case=False, na=False)]
    if len(rows) == 0:
        raise PromotionError("No Hindawi book matched the requested title/category")
    if book_index is not None:
        if book_index < 0 or book_index >= len(rows):
            raise PromotionError(
                f"--book-index {book_index} is outside the {len(rows)} matched books"
            )
        row = rows.iloc[book_index]
        return row, int(rows.index[book_index])
    if len(rows) > 1:
        examples = ", ".join(str(rows.iloc[i].get("title") or "") for i in range(min(5, len(rows))))
        raise PromotionError(
            f"Title matched {len(rows)} books; pass --book-index. First matches: {examples}"
        )
    return rows.iloc[0], int(rows.index[0])


def _build_window(
    title: str,
    author: str,
    arabic_sentences: list[str],
    start_sentence: int,
    lookup: Any,
    context: LemmaContext,
    runtime: dict[str, Any],
) -> PassageWindow:
    covered: list[SentenceCoverage] = [
        sentence_coverage(sentence, lookup, context, runtime)
        for sentence in arabic_sentences
    ]
    empty = [idx + start_sentence for idx, coverage in enumerate(covered) if coverage.content_tokens <= 0]
    if empty:
        raise PromotionError(f"Selected sentences have zero content words: {empty}")
    return PassageWindow(
        title=title,
        author=author,
        start_index=start_sentence - 1,
        sentences=covered,
    )


def _assert_coverage_gates(
    window: PassageWindow,
    *,
    min_active_pct: float,
    max_unmapped_pct: float,
) -> None:
    if window.active_pct < min_active_pct:
        raise PromotionError(
            f"Window is only {window.active_pct:.1%} active; required {min_active_pct:.1%}"
        )
    if window.unmapped_pct > max_unmapped_pct:
        raise PromotionError(
            f"Window is {window.unmapped_pct:.1%} unmapped; required <= {max_unmapped_pct:.1%}"
        )


def _patch_story_metadata(
    db: Any,
    story: Any,
    *,
    args: argparse.Namespace,
    book_title: str,
    author: str,
    book_index: int,
    target_ids: list[int],
    coverage: dict[str, Any],
) -> None:
    metadata = story.metadata_json if isinstance(story.metadata_json, dict) else {}
    metadata = {
        **metadata,
        "authentic_source": "hindawi",
        "target_lemma_ids": target_ids,
        "hindawi": {
            "book_title_ar": book_title,
            "author_ar": author,
            "book_index": book_index,
            "start_sentence": args.start_sentence,
            "sentence_count": args.sentence_count,
            "source_parquet": Path(args.parquet).name,
            "coverage": coverage,
            "promoted_at": datetime.now(timezone.utc).isoformat(),
            "promotion_version": 1,
        },
    }
    story.metadata_json = metadata
    db.add(story)
    db.commit()


def _print_human_summary(
    *,
    window_data: dict[str, Any],
    target_ids: list[int],
    candidates: list[TargetCandidate],
    translations: list[str] | None,
    duplicate_story_id: int | None,
) -> None:
    print(
        f"{window_data['title']} - sentence {window_data['start_sentence']} "
        f"({window_data['active_pct']}% active, {window_data['unmapped_pct']}% unmapped)"
    )
    print(f"Selected targets: {target_ids}")
    print("Top candidates:")
    for candidate in candidates[:8]:
        due = candidate.due_at.isoformat() if candidate.due_at else "no due"
        print(
            f"  #{candidate.lemma_id} {candidate.arabic} ({candidate.gloss}; "
            f"{candidate.state}; {candidate.occurrence_count}x; {due})"
        )
    if window_data.get("sentences"):
        print("Sentences:")
        for sentence in window_data["sentences"]:
            print(f"  - {sentence}")
    if translations:
        print("Translations:")
        for translation in translations:
            print(f"  - {translation}")
    if duplicate_story_id:
        print(f"Duplicate maintenance passage already exists: story #{duplicate_story_id}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Promote one curated Hindawi passage window into review"
    )
    parser.add_argument("--parquet", required=True, help="Path to Hindawi parquet")
    parser.add_argument("--db", help="SQLite DB path; overrides DATABASE_URL")
    parser.add_argument("--category", default="children", help="Category filter")
    parser.add_argument("--title", help="Book title substring")
    parser.add_argument("--book-index", type=int, help="Positional index among title/category matches")
    parser.add_argument("--start-sentence", type=int, required=True, help="1-based Hindawi sentence offset")
    parser.add_argument("--sentence-count", type=int, default=4)
    parser.add_argument("--min-words", type=int, default=5)
    parser.add_argument("--max-words", type=int, default=18)
    parser.add_argument("--min-active-pct", type=float, default=DEFAULT_MIN_ACTIVE_PCT)
    parser.add_argument("--max-unmapped-pct", type=float, default=DEFAULT_MAX_UNMAPPED_PCT)
    parser.add_argument("--target-lemma-id", type=int, action="append", default=[])
    parser.add_argument("--max-targets", type=int, default=DEFAULT_MAX_TARGETS)
    parser.add_argument("--title-en", help="English title for the promoted story")
    parser.add_argument("--translations-json", help="JSON list of English translations")
    parser.add_argument("--translate", action="store_true", help="Generate sentence translations with the LLM")
    parser.add_argument("--quality-gate", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--allow-duplicate", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Write the passage to the database")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        _configure_database(args.db)
        _ensure_backend_path()
        runtime = _load_runtime(disable_camel=False)

        try:
            import pandas as pd
        except ImportError as exc:
            raise PromotionError("pandas/pyarrow are required to read the Hindawi parquet") from exc

        lookup, context = _load_context(runtime)
        df = pd.read_parquet(args.parquet)
        books = df[df["category"].str.contains(args.category, case=False, na=False)]
        book, book_index = _resolve_book(books, args.title, args.book_index)
        raw_sentences = runtime["extract_sentences"](
            str(book.get("text") or ""),
            min_words=args.min_words,
            max_words=args.max_words,
        )
        arabic_sentences = _extract_window(
            raw_sentences,
            args.start_sentence,
            args.sentence_count,
        )
        book_title = str(book.get("title") or "")
        author = str(book.get("author") or "")
        window = _build_window(
            book_title,
            author,
            arabic_sentences,
            args.start_sentence,
            lookup,
            context,
            runtime,
        )
        _assert_coverage_gates(
            window,
            min_active_pct=args.min_active_pct,
            max_unmapped_pct=args.max_unmapped_pct,
        )
        window_data = window_to_dict(window, context, include_text=True)
        counts, surfaces = _content_counts(arabic_sentences, lookup, context, runtime)

        SessionLocal = runtime["SessionLocal"]
        db = SessionLocal()
        try:
            target_ids, candidates = _select_target_ids(
                db,
                counts,
                surfaces,
                context,
                explicit_target_ids=args.target_lemma_id,
                max_targets=args.max_targets,
            )
            translations = _load_translations(args.translations_json, len(arabic_sentences))
            if args.translate:
                translations = _translate_sentences(arabic_sentences)

            body_ar = "\n".join(arabic_sentences)
            duplicate = _find_duplicate_story(db, body_ar)
            duplicate_id = duplicate.id if duplicate else None
            if duplicate and args.apply and not args.allow_duplicate:
                raise PromotionError(
                    f"Duplicate maintenance passage already exists: story #{duplicate.id}"
                )

            if not args.apply:
                if args.json:
                    print(json.dumps({
                        "window": window_data,
                        "selected_target_ids": target_ids,
                        "target_candidates": [candidate.to_dict() for candidate in candidates[:20]],
                        "translations": translations,
                        "duplicate_story_id": duplicate_id,
                    }, ensure_ascii=False, indent=2))
                else:
                    _print_human_summary(
                        window_data=window_data,
                        target_ids=target_ids,
                        candidates=candidates,
                        translations=translations,
                        duplicate_story_id=duplicate_id,
                    )
                return

            if translations is None:
                raise PromotionError("Use --translations-json or --translate when applying")

            from app.services.passage_generator import store_maintenance_passage

            target_words = _target_words_for_ids(db, target_ids)
            eligible_words = _eligible_words_for_window(db, counts)
            generated = _build_generated_payload(
                title_ar=book_title,
                title_en=args.title_en or f"Hindawi passage from {book_title}",
                arabic_sentences=arabic_sentences,
                english_sentences=translations,
            )
            story = store_maintenance_passage(
                db,
                generated,
                target_words,
                eligible_words,
                quality_gate=args.quality_gate,
            )
            _patch_story_metadata(
                db,
                story,
                args=args,
                book_title=book_title,
                author=author,
                book_index=book_index,
                target_ids=target_ids,
                coverage=window_data,
            )
            db.refresh(story)
            from app.models import Sentence

            sentence_ids = [
                row.id
                for row in db.query(Sentence)
                .filter(Sentence.story_id == story.id)
                .order_by(Sentence.id)
                .all()
            ]
            result = {
                "story_id": story.id,
                "sentence_ids": sentence_ids,
                "target_lemma_ids": target_ids,
                "window": window_data,
            }
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(
                    f"Promoted Hindawi passage as story #{story.id}; "
                    f"sentences {sentence_ids}; targets {target_ids}"
                )
        finally:
            db.close()
    except PromotionError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
