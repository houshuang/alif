"""Incremental intake for unmapped frequency-core rows.

The frequency core deliberately keeps high-rank rows whose source form does
not yet map to an Alif lemma. This service resolves a very small number of
those rows during background material generation, so missing core vocabulary
can enter the normal gated lemma -> material -> introduction pipeline.

This is intentionally not a bulk import path.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import FrequencyCoreEntry, Lemma, Root
from app.services.lemma_quality import run_quality_gates
from app.services.morphology import get_word_features, is_valid_root
from app.services.sentence_validator import (
    lookup_lemma,
    normalize_alef,
    resolve_existing_lemma,
    strip_diacritics,
    strip_tatweel,
    build_comprehensive_lemma_lookup,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_RANK = 1000
DEFAULT_LIMIT = 5
DEFAULT_RETRY_LIMIT = 1
DEFAULT_RETRY_COOLDOWN_HOURS = 24

_ARABIC_ONLY_RE = re.compile(r"[^\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\u0640]")


@dataclass
class _CoreAnalysis:
    entry: FrequencyCoreEntry
    normalized: str
    without_al: str | None
    camel_lex: str | None
    camel_lex_norm: str | None
    camel_pos: str | None
    camel_root: str | None


def _normalize_form(text: str | None) -> str:
    cleaned = _ARABIC_ONLY_RE.sub("", (text or "").strip())
    cleaned = strip_tatweel(strip_diacritics(cleaned))
    return normalize_alef(cleaned)


def _analyze_entry(entry: FrequencyCoreEntry) -> _CoreAnalysis:
    norm = _normalize_form(entry.display_form)
    without_al = norm[2:] if norm.startswith("ال") and len(norm) > 2 else None
    features = get_word_features((entry.display_form or "").replace("\u0671", "\u0627"))
    camel_lex = features.get("lex")
    camel_lex_norm = _normalize_form(camel_lex) if camel_lex else None
    return _CoreAnalysis(
        entry=entry,
        normalized=norm,
        without_al=without_al,
        camel_lex=camel_lex,
        camel_lex_norm=camel_lex_norm,
        camel_pos=features.get("pos"),
        camel_root=features.get("root"),
    )


def _resolve_existing(analysis: _CoreAnalysis, lemma_lookup: dict[str, int]) -> int | None:
    original = strip_tatweel(strip_diacritics(analysis.entry.display_form or ""))
    for candidate in (
        analysis.normalized,
        analysis.without_al,
        analysis.camel_lex_norm,
    ):
        if not candidate:
            continue
        found = lookup_lemma(candidate, lemma_lookup, original_bare=original)
        if found:
            return found
        found = resolve_existing_lemma(candidate, lemma_lookup)
        if found:
            return found
    return None


def _map_entry_to_lemma(db: Session, entry: FrequencyCoreEntry, lemma_id: int) -> bool:
    lemma = db.query(Lemma).filter(Lemma.lemma_id == lemma_id).first()
    if not lemma:
        return False
    if lemma.canonical_lemma_id:
        canonical = db.query(Lemma).filter(Lemma.lemma_id == lemma.canonical_lemma_id).first()
        if canonical:
            lemma = canonical
    if lemma.word_category in {"proper_name", "onomatopoeia", "junk"}:
        return False
    entry.lemma_id = lemma.lemma_id
    entry.lemma_key = f"lemma:{lemma.lemma_id}"
    entry.gloss_en = lemma.gloss_en
    entry.pos = lemma.pos
    entry.gap_status = None
    entry.updated_at = datetime.now(timezone.utc)
    return True


def remap_variant_frequency_core_entries(db: Session, lemma_ids=None) -> dict:
    """Re-point frequency-core entries that map to a *variant* lemma onto its
    canonical, and exclude any that thereby duplicate the canonical's own entry.

    Why this exists: an entry can become mis-mapped after it was created — variant
    detection later links its lemma to a canonical (e.g. the inflected form نَزَّلنَا
    → canonical نزل), so the entry lingers on the inflection and the stats card shows
    "we sent down" as a top-1000 word. This is the invariant enforcer: NO active
    frequency-core entry may point to a variant lemma. It is idempotent and is
    called both at the variant-linking chokepoint (`mark_variants`) and as a cron
    safety net (so direct `canonical_lemma_id =` assignments in scripts are healed
    within a cycle too).

    Pass `lemma_ids` to scope the entries acted on (the chokepoint passes the lemmas
    it just marked); ownership/dedup is always computed against the full table.
    """
    redirect = {
        lid: canon
        for lid, canon in db.query(Lemma.lemma_id, Lemma.canonical_lemma_id)
        .filter(Lemma.canonical_lemma_id.isnot(None))
        .all()
    }

    def resolve(lid: int) -> int:
        seen: set[int] = set()
        while lid in redirect and lid not in seen:
            seen.add(lid)
            lid = redirect[lid]
        return lid

    q = db.query(FrequencyCoreEntry).filter(
        FrequencyCoreEntry.excluded_reason.is_(None),
        FrequencyCoreEntry.lemma_id.isnot(None),
    )
    if lemma_ids is not None:
        ids = list(lemma_ids)
        if not ids:
            return {"remapped": 0, "excluded": 0}
        q = q.filter(FrequencyCoreEntry.lemma_id.in_(ids))
    targets = sorted(
        (e for e in q.all() if resolve(e.lemma_id) != e.lemma_id),
        key=lambda e: e.core_rank,
    )
    if not targets:
        return {"remapped": 0, "excluded": 0}

    all_active = (
        db.query(FrequencyCoreEntry)
        .filter(FrequencyCoreEntry.excluded_reason.is_(None), FrequencyCoreEntry.lemma_id.isnot(None))
        .all()
    )
    by_canon: dict[int, list[FrequencyCoreEntry]] = {}
    for e in all_active:
        by_canon.setdefault(resolve(e.lemma_id), []).append(e)

    now = datetime.now(timezone.utc)
    remapped = excluded = 0
    for e in targets:
        canon = resolve(e.lemma_id)
        owner_exists = any(
            o.id != e.id and o.lemma_id == canon and o.excluded_reason is None
            for o in by_canon.get(canon, [])
        )
        if owner_exists:
            e.excluded_reason = "duplicate_variant_of_canonical"
            e.gap_status = None
            e.updated_at = now
            excluded += 1
        else:
            canon_lemma = db.query(Lemma).filter(Lemma.lemma_id == canon).first()
            if not canon_lemma:
                continue
            e.lemma_id = canon
            e.lemma_key = f"lemma:{canon}"
            e.display_form = canon_lemma.lemma_ar or e.display_form
            e.gloss_en = canon_lemma.gloss_en
            e.pos = canon_lemma.pos
            e.gap_status = None
            e.updated_at = now
            remapped += 1
    return {"remapped": remapped, "excluded": excluded}


def _mark_needs_manual_review(entry: FrequencyCoreEntry, reason: str) -> None:
    flags = entry.source_flags_json if isinstance(entry.source_flags_json, dict) else {}
    flags = dict(flags)
    flags["frequency_core_intake"] = {
        "status": "needs_manual_review",
        "reason": reason,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    entry.source_flags_json = flags
    entry.gap_status = "needs_manual_review"
    entry.updated_at = datetime.now(timezone.utc)


def _manual_retry_due(entry: FrequencyCoreEntry, cooldown_hours: int) -> bool:
    if cooldown_hours <= 0:
        return True
    flags = entry.source_flags_json if isinstance(entry.source_flags_json, dict) else {}
    intake = flags.get("frequency_core_intake") if isinstance(flags, dict) else None
    if not isinstance(intake, dict) or not intake.get("at"):
        return True
    try:
        last_attempt = datetime.fromisoformat(str(intake["at"]).replace("Z", "+00:00"))
    except ValueError:
        return True
    if last_attempt.tzinfo is None:
        last_attempt = last_attempt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last_attempt >= timedelta(hours=cooldown_hours)


def _classification_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "core_rank": {"type": "integer"},
                        "action": {"type": "string", "enum": ["create", "skip"]},
                        "lemma_ar": {"type": "string"},
                        "lemma_ar_bare": {"type": "string"},
                        "gloss_en": {"type": "string"},
                        "pos": {"type": "string"},
                        "root": {"type": ["string", "null"]},
                        "word_category": {
                            "type": "string",
                            "enum": ["standard", "proper_name", "onomatopoeia", "junk"],
                        },
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "reason": {"type": "string"},
                    },
                    "required": [
                        "core_rank",
                        "action",
                        "lemma_ar",
                        "lemma_ar_bare",
                        "gloss_en",
                        "pos",
                        "root",
                        "word_category",
                        "confidence",
                        "reason",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def _classify_unmapped_entries(analyses: list[_CoreAnalysis]) -> dict[int, dict[str, Any]]:
    """Ask for dictionary lemmas for unresolved rows.

    Callers only accept high-confidence standard vocabulary whose proposed
    lemma is morphologically related to the source form. Anything else stays
    unmapped for manual or richer-corpus handling.
    """
    from app.services.llm import generate_completion

    rows = [
        {
            "core_rank": a.entry.core_rank,
            "display_form": a.entry.display_form,
            "normalized_source": a.normalized,
            "camel_lex": a.camel_lex,
            "camel_pos": a.camel_pos,
            "camel_root": a.camel_root,
        }
        for a in analyses
    ]
    prompt = f"""For each unmapped Arabic high-frequency source form, decide whether it can safely become a teachable Alif lemma.

Rules:
- Create only standard vocabulary words.
- Return the dictionary lemma, not the surface form: strip definite article, reduce plurals to singular, reduce conjugated verbs to 3rd-person masculine singular past.
- Do not create proper names, forum handles, URLs, UI junk, particles/function words, or uncertain homographs.
- Use a concise English dictionary gloss. Verbs must be "to ...".
- Set confidence to "high" only when the lemmatization is unambiguous without sentence context.
- If context is needed to choose a lemma or sense, action must be "skip".

Rows:
{json.dumps(rows, ensure_ascii=False)}
"""
    result = generate_completion(
        prompt=prompt,
        system_prompt="You are a conservative Arabic lexicographer for a spaced-repetition reading app. Return JSON only.",
        json_schema=_classification_schema(),
        temperature=0.0,
        task_type="frequency_core_intake",
        model_override="claude_haiku",
    )
    items = result.get("items", []) if isinstance(result, dict) else []
    return {int(item["core_rank"]): item for item in items if isinstance(item, dict)}


def _related_to_source(item: dict[str, Any], analysis: _CoreAnalysis) -> bool:
    proposed = _normalize_form(item.get("lemma_ar_bare") or item.get("lemma_ar"))
    if not proposed:
        return False
    allowed = {
        analysis.normalized,
        analysis.without_al,
        analysis.camel_lex_norm,
    }
    allowed = {a for a in allowed if a}
    if proposed in allowed:
        return True
    # Allow the common definite-source case even if the LLM returned vocalized
    # bare text that normalizes slightly differently after alef handling.
    if analysis.normalized.startswith("ال") and proposed == analysis.normalized[2:]:
        return True
    return False


def _safe_to_create(item: dict[str, Any], analysis: _CoreAnalysis) -> bool:
    if item.get("action") != "create":
        return False
    if item.get("confidence") != "high":
        return False
    if item.get("word_category") != "standard":
        return False
    if not (item.get("lemma_ar") and item.get("lemma_ar_bare") and item.get("gloss_en")):
        return False
    if not _related_to_source(item, analysis):
        return False
    return True


def _classify_import_quality(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Reuse the import-quality gate; return accepted items and rejected count."""
    if not items:
        return [], 0
    try:
        from app.services.import_quality import classify_lemmas
        useful, rejected = classify_lemmas([
            {"arabic": item["lemma_ar_bare"], "english": item["gloss_en"]}
            for item in items
        ])
    except Exception:
        logger.exception("Frequency core intake import-quality check failed")
        return [], len(items)

    accepted: list[dict[str, Any]] = []
    useful_by_arabic = {u.get("arabic"): u for u in useful}
    for item in items:
        quality = useful_by_arabic.get(item["lemma_ar_bare"])
        if not quality:
            continue
        cat = quality.get("word_category", "standard")
        if cat != "standard":
            continue
        if quality.get("cleaned_arabic"):
            cleaned = quality["cleaned_arabic"]
            if _normalize_form(cleaned) != _normalize_form(item["lemma_ar_bare"]):
                continue
            item = dict(item)
            item["lemma_ar_bare"] = cleaned
        accepted.append(item)
    return accepted, len(items) - len(accepted)


def intake_frequency_core_gaps(
    db: Session,
    *,
    limit: int = DEFAULT_LIMIT,
    max_rank: int = DEFAULT_MAX_RANK,
    retry_limit: int = DEFAULT_RETRY_LIMIT,
    retry_cooldown_hours: int = DEFAULT_RETRY_COOLDOWN_HOURS,
    create_missing: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Resolve or create a tiny batch of unmapped high-frequency core lemmas.

    Existing-lemma resolution is deterministic. New lemma creation is allowed
    only for high-confidence standard vocabulary and always goes through the
    central quality gates. No UserLemmaKnowledge rows are created here; the
    normal selector/material-generation/session flow handles introduction.
    """
    stats: dict[str, Any] = {
        "scanned": 0,
        "resolved_existing": 0,
        "created": 0,
        "rejected": 0,
        "skipped": 0,
        "errors": 0,
        "mapped_ranks": [],
        "created_ids": [],
    }
    if limit <= 0 and retry_limit <= 0:
        return stats

    fresh_entries: list[FrequencyCoreEntry] = []
    if limit > 0:
        fresh_entries = (
            db.query(FrequencyCoreEntry)
            .filter(
                FrequencyCoreEntry.excluded_reason.is_(None),
                FrequencyCoreEntry.lemma_id.is_(None),
                FrequencyCoreEntry.core_rank <= max_rank,
                or_(
                    FrequencyCoreEntry.gap_status.is_(None),
                    FrequencyCoreEntry.gap_status == "unmapped",
                ),
            )
            .order_by(FrequencyCoreEntry.core_rank.asc())
            .limit(limit)
            .all()
        )

    retry_entries: list[FrequencyCoreEntry] = []
    if retry_limit > 0:
        retry_candidates = (
            db.query(FrequencyCoreEntry)
            .filter(
                FrequencyCoreEntry.excluded_reason.is_(None),
                FrequencyCoreEntry.lemma_id.is_(None),
                FrequencyCoreEntry.core_rank <= max_rank,
                FrequencyCoreEntry.gap_status == "needs_manual_review",
            )
            .order_by(FrequencyCoreEntry.core_rank.asc())
            .all()
        )
        for entry in retry_candidates:
            if not _manual_retry_due(entry, retry_cooldown_hours):
                continue
            retry_entries.append(entry)
            if len(retry_entries) >= retry_limit:
                break

    entries_by_id = {entry.id: entry for entry in retry_entries + fresh_entries}
    entries = sorted(entries_by_id.values(), key=lambda e: e.core_rank)
    stats["scanned"] = len(entries)
    if not entries:
        return stats

    lemma_lookup = build_comprehensive_lemma_lookup(db)
    unresolved: list[_CoreAnalysis] = []
    for entry in entries:
        analysis = _analyze_entry(entry)
        existing_id = _resolve_existing(analysis, lemma_lookup)
        if existing_id:
            if not dry_run and _map_entry_to_lemma(db, entry, existing_id):
                stats["resolved_existing"] += 1
                stats["mapped_ranks"].append(entry.core_rank)
            elif dry_run:
                stats["resolved_existing"] += 1
                stats["mapped_ranks"].append(entry.core_rank)
            continue
        unresolved.append(analysis)

    if stats["resolved_existing"] and not dry_run:
        db.commit()

    if not create_missing or dry_run or not unresolved:
        stats["skipped"] += len(unresolved)
        return stats

    try:
        classified = _classify_unmapped_entries(unresolved)
    except Exception:
        logger.exception("Frequency core intake classification failed")
        stats["errors"] += len(unresolved)
        return stats

    safe_items: list[dict[str, Any]] = []
    analysis_by_rank = {a.entry.core_rank: a for a in unresolved}
    mapped_after_classification = 0
    marked_manual = 0
    for analysis in unresolved:
        item = classified.get(analysis.entry.core_rank)
        if not item or not _safe_to_create(item, analysis):
            if not item:
                _mark_needs_manual_review(analysis.entry, "classifier returned no item")
            else:
                _mark_needs_manual_review(analysis.entry, item.get("reason") or "not safe to create")
            marked_manual += 1
            stats["rejected"] += 1
            continue

        # Final dedupe after the LLM-proposed dictionary form.
        existing = resolve_existing_lemma(item["lemma_ar_bare"], lemma_lookup)
        if existing:
            if _map_entry_to_lemma(db, analysis.entry, existing):
                stats["resolved_existing"] += 1
                mapped_after_classification += 1
                stats["mapped_ranks"].append(analysis.entry.core_rank)
            continue
        safe_items.append(item)

    quality_candidate_ranks = {int(item["core_rank"]) for item in safe_items}
    safe_items, rejected_by_quality = _classify_import_quality(safe_items)
    accepted_ranks = {int(item["core_rank"]) for item in safe_items}
    for rank in quality_candidate_ranks - accepted_ranks:
        analysis = analysis_by_rank.get(rank)
        if analysis:
            _mark_needs_manual_review(analysis.entry, "import quality rejected")
            marked_manual += 1
    stats["rejected"] += rejected_by_quality

    create_groups: dict[str, tuple[dict[str, Any], list[FrequencyCoreEntry]]] = {}
    for item in safe_items:
        analysis = analysis_by_rank.get(int(item["core_rank"]))
        if not analysis:
            stats["rejected"] += 1
            continue
        proposed_norm = _normalize_form(item["lemma_ar_bare"])
        if proposed_norm in create_groups:
            create_groups[proposed_norm][1].append(analysis.entry)
        else:
            create_groups[proposed_norm] = (item, [analysis.entry])

    created_pairs: list[tuple[list[FrequencyCoreEntry], int]] = []
    for item, entries_to_map in create_groups.values():
        root_id = None
        root_str = item.get("root")
        if root_str and is_valid_root(root_str):
            root = db.query(Root).filter(Root.root == root_str).first()
            if root is None:
                root = Root(root=root_str, core_meaning_en="")
                db.add(root)
                db.flush()
            root_id = root.root_id
        lemma = Lemma(
            lemma_ar=item["lemma_ar"],
            lemma_ar_bare=strip_diacritics(item["lemma_ar_bare"]),
            root_id=root_id,
            pos=item.get("pos") or None,
            gloss_en=item["gloss_en"],
            source="frequency_core",
        )
        db.add(lemma)
        db.flush()
        created_pairs.append((entries_to_map, lemma.lemma_id))
        stats["created_ids"].append(lemma.lemma_id)

    if created_pairs:
        db.commit()
        created_ids = [lid for _, lid in created_pairs]
        gate_result = run_quality_gates(db, created_ids, background_enrich=False)
        stats["created"] = gate_result.get("stamped", len(created_ids))
        for entries_to_map, lemma_id in created_pairs:
            for entry in entries_to_map:
                if _map_entry_to_lemma(db, entry, lemma_id):
                    stats["mapped_ranks"].append(entry.core_rank)
        db.commit()
    elif mapped_after_classification or marked_manual:
        db.commit()

    return stats
