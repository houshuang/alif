"""Philological enrichment for lemmas.

Generates ``Lemma.enrichment_json`` payloads using Claude Sonnet via the
``claude -p --json-schema`` CLI. Surfaced in the lookup card + lemma detail
screen (Modern Editorial design).

Pipeline mirrors ``material_generator.batch_generate_material`` for SQLite
write-lock discipline:

    Phase 1 — DB read:   pull lemmas + glosses + POS, close session.
    Phase 2 — LLM work:  one Sonnet call per batch (~4 lemmas), no DB held.
    Phase 3 — DB write:  open fresh session, write enrichment + stamp status.

Constraints:
- **Verification not applicable** (no per-token mappings to verify).
  Quality control is the JSON-schema constraint (eras enum, required fields)
  plus a per-lemma sanity check on the parsed payload before writing.
- **Sonnet, not Haiku** — etymology + literary quotes need real reasoning;
  Haiku produces shallow output that doesn't carry the design's weight.
- **Glossless lemmas are skipped** — if we don't know what the word means we
  can't ask Claude to philologize it. Same rule as the picker / generator.
- **Variant lemmas are skipped** — they re-use the canonical's enrichment.
  Function-word + proper-name lemmas are also skipped (no philological
  payoff).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app import database
from app.models import Lemma
from app.schemas import LemmaEnrichment

log = logging.getLogger(__name__)


_MODEL_ALIASES = {
    "sonnet": "claude-sonnet-4-5-20250929",
    "haiku": "claude-haiku-4-5-20251001",
}


def _resolve_model(raw: str) -> str:
    return _MODEL_ALIASES.get(raw.strip().lower(), raw)


ENRICH_MODEL = _resolve_model(os.environ.get("POLYGLOT_ENRICH_MODEL", "sonnet"))
VERIFY_MODEL = _resolve_model(os.environ.get("POLYGLOT_ENRICH_VERIFY_MODEL", "haiku"))
ENRICH_TIMEOUT_S = int(os.environ.get("POLYGLOT_ENRICH_TIMEOUT", "240"))
VERIFY_TIMEOUT_S = int(os.environ.get("POLYGLOT_ENRICH_VERIFY_TIMEOUT", "180"))
ENRICH_BATCH_SIZE = max(1, int(os.environ.get("POLYGLOT_ENRICH_BATCH_SIZE", "4")))

LANG_DISPLAY = {
    "el": "Modern Greek",
    "grc": "Ancient Greek",
    "la": "Latin",
}


def _log_dir() -> Path:
    path = Path(__file__).resolve().parents[2] / "data" / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _log_pipeline(entry: dict) -> None:
    try:
        path = _log_dir() / f"enrichment_pipeline_{datetime.now():%Y-%m-%d}.jsonl"
        entry = {"ts": datetime.now().isoformat(), **entry}
        with open(path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ─── Data carriers ─────────────────────────────────────────────────────────


@dataclass
class _EnrichTarget:
    lemma_id: int
    lemma_form: str
    pos: str
    gloss_en: str
    cognate_form: Optional[str]   # Ancient Greek cognate if any, for context


# ─── Prompt + schema ───────────────────────────────────────────────────────


def _gen_prompt(language_code: str, targets: list[_EnrichTarget]) -> str:
    lang = LANG_DISPLAY.get(language_code, language_code)
    target_block_lines = []
    for t in targets:
        line = (
            f"- lemma_form: {t.lemma_form}\n"
            f"  pos: {t.pos}\n"
            f"  gloss_en: {t.gloss_en}"
        )
        if t.cognate_form:
            line += f"\n  ancient_cognate: {t.cognate_form}"
        target_block_lines.append(line)
    target_block = "\n".join(target_block_lines)
    return f"""You are a {lang} philology assistant. For each lemma below,
produce rich enrichment for an intermediate learner who is literate and
curious — they care about etymology, semantic drift across eras, cross-language
cognates, and how the word lives in literature.

For each lemma, produce a structured JSON entry. Field guidance:

FACTUAL ACCURACY ON PHILOLOGY:
- These claims will be surfaced to a learner who trusts the app's etymology.
  A wrong PIE root or a wrong claim about what a word meant in Byzantine
  Greek is a real problem — much worse than an honest "we don't know."
- If you are not confident a PIE root is correct, set `pie_root: null`
  rather than guess. Same for any diachronic stage: omit the stage rather
  than invent a plausible-sounding meaning.
- When citing a derivation that is one of multiple competing hypotheses,
  say so explicitly in origin_note ("one hypothesis is...", "traditionally
  derived from X, though some scholars prefer Y"). Don't present
  hypotheses as fact.
- DO NOT chain etymology through unrelated roots. E.g., for a contracted
  form, the PIE root field should be the root of the SEMANTIC PARENT (the
  preposition's source, not the article's source). When in doubt, set null.

ETYMOLOGY (1 object):
- pie_root: the reconstructed Proto-Indo-European root with asterisk and gloss
  in parentheses, e.g. "*ḱerd- (heart)". Null when:
  * etymology is truly opaque or non-IE (Pre-Greek substrate, Semitic loan)
  * the word is a contraction/compound where the PIE root field would be
    ambiguous (which constituent's root?)
  * you are not confident in the reconstruction
- ancient_form: the Ancient Greek citation form with full polytonic accents,
  e.g. "ἄλογος", "λόγος", "καρδία". For Latin or Ancient Greek lemmas, this is
  the parent/source form.
- origin_note: 1-2 sentence prose explanation of the word's origin and any
  notable morphological compounding. Write for a reader, not a database.
- morphology: a structural breakdown if the word is transparently compound,
  e.g. "ἀ- (negative) + λόγος (reason) → 'without reason'". Null for simple
  roots.

DIACHRONY (ordered list, ancient to modern):
- 2-5 stages tracking meaning shift. Each stage: era (one of
  Mycenaean / Homeric / Classical / Koine / Byzantine / Modern), form used at
  that era (often the same form but with shifted sense), the meaning then, and
  an optional one-line note for context.
- Skip eras with no meaningful change — only include a stage when the meaning
  or register shifts.
- If you don't know what a word specifically meant in a particular era, OMIT
  that stage. Never claim "in Byzantine Greek X meant Y" unless you are
  confident — that's the exact kind of error the learner will catch when
  they look it up.

COGNATES (list, 3-6 entries):
- Cross-language relatives. Pick high-utility ones for an English speaker.
- relation enum: "loanword-from-greek" (English took it from Greek),
  "borrowed-via-latin" (Latin borrowed Greek, then English from Latin),
  "shared-pie-root" (both languages inherit from PIE), "calque" (semantic
  parallel, not cognate), "descendant" (Romance from Latin form of Greek).
- gloss_en for non-English forms. Optional note for false-friend warnings or
  semantic drift.

QUOTES (list, 1-3 entries):
- Short, ≤25 words each. Famous, surprising, or representative usages across
  eras. Translations to English.
- Source must be specific enough to locate: "Homer, Iliad 1.5", "Plato,
  Republic 484a", "John 1:1", "Cavafy, Ithaca". Avoid bare attributions.
- Skip if no good attestation exists.

REGISTER (1 object):
- formality: how the modern form sits today — "formal" / "neutral" /
  "colloquial" / "literary". Null if unclear or evenly distributed.
- collocations: 3-5 common modern collocations, with accents.
- false_friends_en: English words that look or sound similar but mean
  something different (false-friend trap). Empty list if none.
- usage_note: 1 sentence on modern usage flavor — when a learner would (or
  wouldn't) reach for this word vs. a synonym.

VERSION: always 1.

Style:
- Greek text uses polytonic for Ancient/Koine/Byzantine, monotonic for Modern.
- Translations in plain English, no scholarly markup. Be evocative but
  precise — this surfaces in a learning UI, not a journal article.
- Be honest about gaps. If you don't know a Mycenaean form, omit that stage.
  If you don't know a PIE root, set pie_root to null.

Lemmas:
{target_block}
"""


def _gen_schema() -> dict:
    era_enum = ["Mycenaean", "Homeric", "Classical", "Koine", "Byzantine", "Modern"]
    relation_enum = [
        "loanword-from-greek",
        "shared-pie-root",
        "calque",
        "descendant",
        "borrowed-via-latin",
    ]
    formality_enum = ["formal", "neutral", "colloquial", "literary"]
    return {
        "type": "object",
        "properties": {
            "lemmas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "lemma_form": {"type": "string"},
                        "enrichment": {
                            "type": "object",
                            "properties": {
                                "version": {"type": "integer"},
                                "etymology": {
                                    "type": "object",
                                    "properties": {
                                        "pie_root": {"type": ["string", "null"]},
                                        "ancient_form": {"type": ["string", "null"]},
                                        "origin_note": {"type": "string"},
                                        "morphology": {"type": ["string", "null"]},
                                    },
                                    "required": ["origin_note"],
                                },
                                "diachrony": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "era": {"type": "string", "enum": era_enum},
                                            "form": {"type": "string"},
                                            "meaning": {"type": "string"},
                                            "note": {"type": ["string", "null"]},
                                        },
                                        "required": ["era", "form", "meaning"],
                                    },
                                },
                                "cognates": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "language": {"type": "string"},
                                            "form": {"type": "string"},
                                            "relation": {
                                                "type": "string",
                                                "enum": relation_enum,
                                            },
                                            "gloss_en": {"type": ["string", "null"]},
                                            "note": {"type": ["string", "null"]},
                                        },
                                        "required": ["language", "form", "relation"],
                                    },
                                },
                                "quotes": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "text": {"type": "string"},
                                            "source": {"type": "string"},
                                            "era": {"type": "string", "enum": era_enum},
                                            "translation_en": {"type": "string"},
                                        },
                                        "required": ["text", "source", "era", "translation_en"],
                                    },
                                },
                                "register": {
                                    "type": "object",
                                    "properties": {
                                        "formality": {
                                            "type": ["string", "null"],
                                            "enum": [*formality_enum, None],
                                        },
                                        "collocations": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "false_friends_en": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "usage_note": {"type": ["string", "null"]},
                                    },
                                },
                            },
                            "required": ["version", "diachrony", "cognates", "quotes"],
                        },
                    },
                    "required": ["lemma_form", "enrichment"],
                },
            },
        },
        "required": ["lemmas"],
    }


# ─── CLI call ──────────────────────────────────────────────────────────────


def _call_cli(cmd: list[str], timeout_s: int) -> Optional[dict]:
    """Run ``claude -p --json-schema`` and return parsed ``structured_output``.

    Mirrors ``material_generator._call_cli`` — same fallback to ``result`` if
    structured_output is absent (some CLI builds). Returns ``None`` on any
    failure (timeout, non-zero, parse error).
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        log.warning("Claude CLI timed out after %ds", timeout_s)
        return None
    if proc.returncode != 0:
        log.warning("Claude CLI failed (exit %d): %s", proc.returncode, proc.stderr[:500])
        return None
    try:
        wrapper = json.loads(proc.stdout)
    except json.JSONDecodeError:
        log.warning("Could not parse CLI envelope JSON: %s", proc.stdout[:300])
        return None
    if not isinstance(wrapper, dict):
        return None
    structured = wrapper.get("structured_output")
    if isinstance(structured, dict):
        return structured
    result_str = wrapper.get("result", "")
    if not result_str:
        return None
    try:
        parsed = json.loads(result_str)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _enrich_batch_call(
    language_code: str,
    targets: list[_EnrichTarget],
) -> Optional[dict[str, LemmaEnrichment]]:
    """One Sonnet call → ``{lemma_form: LemmaEnrichment}``.

    Returns ``None`` on total LLM failure. Missing entries for individual
    lemmas come back as absent keys (caller marks them failed).
    """
    if not targets:
        return {}
    prompt = _gen_prompt(language_code, targets)
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", ENRICH_MODEL,
        "--json-schema", json.dumps(_gen_schema()),
        prompt,
    ]
    started = time.time()
    structured = _call_cli(cmd, ENRICH_TIMEOUT_S)
    elapsed = time.time() - started
    if not structured:
        _log_pipeline({
            "event": "enrich_batch_failed",
            "language_code": language_code,
            "lemma_ids": [t.lemma_id for t in targets],
            "elapsed_s": round(elapsed, 1),
            "model": ENRICH_MODEL,
        })
        return None
    parsed: dict[str, LemmaEnrichment] = {}
    for item in structured.get("lemmas", []) or []:
        if not isinstance(item, dict):
            continue
        form = item.get("lemma_form")
        payload = item.get("enrichment")
        if not isinstance(form, str) or not isinstance(payload, dict):
            continue
        try:
            parsed[form] = LemmaEnrichment.model_validate(payload)
        except Exception as e:
            log.warning("Failed to validate enrichment for %s: %s", form, e)
            continue
    _log_pipeline({
        "event": "enrich_batch_returned",
        "language_code": language_code,
        "lemma_ids": [t.lemma_id for t in targets],
        "parsed_count": len(parsed),
        "elapsed_s": round(elapsed, 1),
        "model": ENRICH_MODEL,
    })
    return parsed


# ─── Fact-verification pass (Haiku) ────────────────────────────────────────
# Mirrors material_generator's verify pattern: after Sonnet generates rich
# content, send the *factual* slices (etymology + diachrony) to Haiku for a
# cheap accuracy check. Per user spec (2026-05-21), quotes/cognates/register
# are NOT verified — those are memory hooks where mild inaccuracy is fine.
# Etymology and diachrony are claims the learner will trust, so wrong PIE
# roots and wrong "in era X this meant Y" are real problems.


def _verify_prompt(language_code: str, items: list[dict]) -> str:
    lang = LANG_DISPLAY.get(language_code, language_code)
    block_lines = []
    for it in items:
        e = it["etymology"] or {}
        block_lines.append(
            f"[lemma_id={it['lemma_id']}] {it['lemma_form']} ({it['gloss_en']})\n"
            f"  etymology.pie_root: {e.get('pie_root')!r}\n"
            f"  etymology.ancient_form: {e.get('ancient_form')!r}\n"
            f"  etymology.origin_note: {e.get('origin_note', '')[:300]!r}\n"
            f"  etymology.morphology: {e.get('morphology')!r}\n"
            f"  diachrony stages:"
        )
        for stage in it["diachrony"]:
            block_lines.append(
                f"    - era={stage['era']}, form={stage['form']!r}, "
                f"meaning={stage['meaning'][:120]!r}"
            )
    items_block = "\n".join(block_lines)
    return f"""You are a {lang} philology fact-checker. For each lemma below,
review ONLY the etymology and diachrony claims (ignore quotes, cognates,
register — those are not your concern). Flag anything that is:

1. A clearly WRONG PIE root attribution (e.g. claiming a root that gives a
   different word entirely, or chaining etymology through an unrelated form).
2. A WRONG factual claim about what a word meant in a specific era.
3. A FOLK ETYMOLOGY presented as fact (e.g. "ξέρω derives from ξηρός 'dry'"
   is a popular folk etymology, not the standard derivation).

DO NOT flag:
- Anything you're not >90% confident is wrong (when uncertain, mark "ok").
- Stylistic disagreements, prose phrasing, or "I would have said it
  differently" issues.
- Quote attributions, cognate choices, register classification — those are
  out of scope here.

For each lemma, return a verdict:
- "ok": etymology + diachrony look correct (or any errors are too minor to bother).
- "etymology_issue": specific factual problem in the etymology block. Put the
  correction in `note`.
- "diachrony_issue": specific factual problem in one of the diachrony stages.
  Put the correction in `note`.

Be parsimonious — most should come back "ok". Only flag when you're confident
the published claim would be challenged by a philology reference.

Lemmas:
{items_block}
"""


def _verify_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "lemma_id": {"type": "integer"},
                        "verdict": {
                            "type": "string",
                            "enum": ["ok", "etymology_issue", "diachrony_issue"],
                        },
                        "note": {"type": ["string", "null"]},
                    },
                    "required": ["lemma_id", "verdict"],
                },
            },
        },
        "required": ["verdicts"],
    }


@dataclass
class _Verdict:
    lemma_id: int
    verdict: str   # "ok" | "etymology_issue" | "diachrony_issue"
    note: Optional[str]


def _verify_enrichment_facts(
    language_code: str,
    parsed: dict[str, LemmaEnrichment],
    targets: list[_EnrichTarget],
) -> dict[int, _Verdict]:
    """Haiku check on etymology + diachrony factual accuracy. Returns
    ``{lemma_id: _Verdict}`` covering only lemmas that came back from
    Sonnet (others fail upstream and never reach here).

    On total LLM failure, returns an empty dict — caller treats this as
    "no extra information" and writes the Sonnet output as-is. This is
    intentional: the verifier is best-effort. A timed-out Haiku shouldn't
    block the user from seeing enrichment that's probably fine.
    """
    targets_by_form = {t.lemma_form: t for t in targets}
    items: list[dict] = []
    for form, e in parsed.items():
        t = targets_by_form.get(form)
        if t is None:
            continue
        items.append({
            "lemma_id": t.lemma_id,
            "lemma_form": form,
            "gloss_en": t.gloss_en,
            "etymology": e.etymology.model_dump() if e.etymology else None,
            "diachrony": [s.model_dump() for s in e.diachrony],
        })
    if not items:
        return {}

    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--model", VERIFY_MODEL,
        "--json-schema", json.dumps(_verify_schema()),
        _verify_prompt(language_code, items),
    ]
    started = time.time()
    structured = _call_cli(cmd, VERIFY_TIMEOUT_S)
    elapsed = time.time() - started
    if not structured:
        _log_pipeline({
            "event": "verify_failed",
            "language_code": language_code,
            "lemma_ids": [it["lemma_id"] for it in items],
            "elapsed_s": round(elapsed, 1),
            "model": VERIFY_MODEL,
        })
        return {}

    verdicts: dict[int, _Verdict] = {}
    for v in structured.get("verdicts", []) or []:
        if not isinstance(v, dict):
            continue
        lid = v.get("lemma_id")
        kind = v.get("verdict")
        if not isinstance(lid, int) or kind not in ("ok", "etymology_issue", "diachrony_issue"):
            continue
        verdicts[lid] = _Verdict(
            lemma_id=lid, verdict=kind, note=v.get("note") or None,
        )

    flagged = [v for v in verdicts.values() if v.verdict != "ok"]
    _log_pipeline({
        "event": "verify_returned",
        "language_code": language_code,
        "lemma_count": len(items),
        "verdict_count": len(verdicts),
        "flagged": [
            {"lemma_id": v.lemma_id, "verdict": v.verdict, "note": v.note}
            for v in flagged
        ],
        "elapsed_s": round(elapsed, 1),
        "model": VERIFY_MODEL,
    })
    return verdicts


# ─── Orchestration ─────────────────────────────────────────────────────────


def _snapshot_targets(db: Session, language_code: str, lemma_ids: list[int]) -> tuple[list[_EnrichTarget], list[int]]:
    """Return (eligible targets, skipped ids).

    Skipped: glossless, variant, function_word, proper_name. The picker /
    learner UI never surfaces these as standalone lemmas worth philologizing.
    """
    lemmas = (
        db.query(Lemma)
        .filter(Lemma.lemma_id.in_(lemma_ids))
        .filter(Lemma.language_code == language_code)
        .all()
    )
    targets: list[_EnrichTarget] = []
    skipped: list[int] = []
    by_id = {l.lemma_id: l for l in lemmas}
    cognate_ids = [l.cognate_lemma_id for l in lemmas if l.cognate_lemma_id]
    cognate_forms: dict[int, str] = {}
    if cognate_ids:
        for cog in db.query(Lemma).filter(Lemma.lemma_id.in_(cognate_ids)).all():
            cognate_forms[cog.lemma_id] = cog.lemma_form

    for lid in lemma_ids:
        lemma = by_id.get(lid)
        if lemma is None:
            skipped.append(lid)
            continue
        if lemma.canonical_lemma_id is not None:
            skipped.append(lid)
            continue
        if lemma.word_category in ("function_word", "proper_name"):
            skipped.append(lid)
            continue
        if not (lemma.gloss_en or "").strip():
            skipped.append(lid)
            continue
        cognate_form = cognate_forms.get(lemma.cognate_lemma_id) if lemma.cognate_lemma_id else None
        targets.append(_EnrichTarget(
            lemma_id=lemma.lemma_id,
            lemma_form=lemma.lemma_form,
            pos=lemma.pos or "",
            gloss_en=lemma.gloss_en or "",
            cognate_form=cognate_form,
        ))
    return targets, skipped


def batch_enrich(
    language_code: str,
    lemma_ids: list[int],
) -> dict:
    """Enrich the given lemmas. Returns a summary dict:

        {"enriched": int, "failed_lemma_ids": [...], "skipped_lemma_ids": [...]}

    Internally splits ``lemma_ids`` into chunks of ``ENRICH_BATCH_SIZE`` and
    issues one Sonnet call per chunk. Each chunk independently commits, so a
    partial run still yields persisted enrichment for the successful chunks.
    """
    if not lemma_ids:
        return {"enriched": 0, "failed_lemma_ids": [], "skipped_lemma_ids": []}

    db = database.SessionLocal()
    try:
        targets, skipped = _snapshot_targets(db, language_code, lemma_ids)
    finally:
        db.close()

    if not targets:
        return {"enriched": 0, "failed_lemma_ids": [], "skipped_lemma_ids": skipped}

    total_enriched = 0
    failed: list[int] = []

    for i in range(0, len(targets), ENRICH_BATCH_SIZE):
        chunk = targets[i:i + ENRICH_BATCH_SIZE]
        parsed = _enrich_batch_call(language_code, chunk)
        if parsed is None:
            # Total LLM failure for this chunk — mark all as failed and stamp
            # status='failed' so the next cron can retry on a fresh run.
            failed.extend(t.lemma_id for t in chunk)
            _stamp_failure(language_code, [t.lemma_id for t in chunk])
            continue

        # Best-effort fact-check on etymology + diachrony. Returns {} on
        # total Haiku failure — we still write the Sonnet output as-is, just
        # without a verifier-clean stamp.
        verdicts = _verify_enrichment_facts(language_code, parsed, chunk)

        chunk_enriched, chunk_failed = _write_chunk(
            language_code, chunk, parsed, verdicts=verdicts,
        )
        total_enriched += chunk_enriched
        failed.extend(chunk_failed)

    _log_pipeline({
        "event": "batch_enrich_done",
        "language_code": language_code,
        "requested": len(lemma_ids),
        "eligible": len(targets),
        "enriched": total_enriched,
        "failed": len(failed),
        "skipped": len(skipped),
    })
    return {
        "enriched": total_enriched,
        "failed_lemma_ids": failed,
        "skipped_lemma_ids": skipped,
    }


def _write_chunk(
    language_code: str,
    chunk: list[_EnrichTarget],
    parsed: dict[str, LemmaEnrichment],
    verdicts: Optional[dict[int, _Verdict]] = None,
) -> tuple[int, list[int]]:
    """Persist parsed enrichments for one chunk. Returns (enriched, failed_ids).

    Phase 3 of the read/LLM/write pattern. Single commit per chunk.

    When ``verdicts`` flags a lemma as ``etymology_issue`` / ``diachrony_issue``,
    the payload is still written (since rejecting it loses all 5 slices including
    the unverified-OK ones), but ``enrichment_status`` is stamped as
    ``done_flagged`` and the verifier's note is appended to the payload's
    ``_verifier_note`` field so downstream consumers can react. ``done`` =
    Sonnet + Haiku both OK; ``done_unverified`` = Haiku failed to run.
    """
    verdicts = verdicts or {}
    now = datetime.now(timezone.utc)
    enriched = 0
    failed: list[int] = []
    db = database.SessionLocal()
    try:
        for target in chunk:
            payload = parsed.get(target.lemma_form)
            lemma = db.query(Lemma).filter(Lemma.lemma_id == target.lemma_id).first()
            if lemma is None:
                failed.append(target.lemma_id)
                continue
            if payload is None:
                lemma.enrichment_status = "failed"
                failed.append(target.lemma_id)
                continue
            data = payload.model_dump(mode="json")
            verdict = verdicts.get(target.lemma_id)
            if verdict is None:
                lemma.enrichment_status = "done_unverified"
            elif verdict.verdict == "ok":
                lemma.enrichment_status = "done"
            else:
                lemma.enrichment_status = "done_flagged"
                data["_verifier_note"] = {
                    "verdict": verdict.verdict, "note": verdict.note,
                }
            lemma.enrichment_json = data
            lemma.enriched_at = now
            enriched += 1
        db.commit()
    except Exception:
        db.rollback()
        log.exception("Failed to commit enrichments (language=%s)", language_code)
        failed = [t.lemma_id for t in chunk]
        enriched = 0
    finally:
        db.close()
    return enriched, failed


def _stamp_failure(language_code: str, lemma_ids: list[int]) -> None:
    """Stamp enrichment_status='failed' on lemmas whose chunk's LLM call failed
    entirely. The next cron pass can retry by filtering on this status."""
    if not lemma_ids:
        return
    db = database.SessionLocal()
    try:
        for lemma in db.query(Lemma).filter(Lemma.lemma_id.in_(lemma_ids)).all():
            lemma.enrichment_status = "failed"
        db.commit()
    except Exception:
        db.rollback()
        log.exception("Failed to stamp enrichment failure (language=%s)", language_code)
    finally:
        db.close()


_ACTIVE_STATES = ("acquiring", "learning", "lapsed")
_BACKFILL_STATES = ("encountered",)
_ELIGIBLE_STATES = _ACTIVE_STATES + _BACKFILL_STATES


def find_unenriched_lemmas(
    language_code: str = "el",
    limit: int = 10,
    include_failed: bool = False,
) -> list[int]:
    """Return lemma_ids that still need enrichment, prioritised by review proximity.

    Eligibility: canonical, not function-word/proper-name, has a gloss, has a
    UserLemmaKnowledge row, and ``knowledge_state != 'known'``. Already-known
    lemmas are excluded by design (2026-05-21) — the lookup card shows
    enrichment when the learner is still building a memory hook for a word, not
    after they've graduated it.

    Order (priority buckets, drained in sequence):
      0. ``acquiring`` — sorted by ``acquisition_next_due`` ASC. The picker
         tends to surface these next, so enriching by next-due means the
         lookup card is ready when the lemma shows up in the next session.
      1. ``learning`` + ``lapsed`` — FSRS-engaged but no acquisition due-date;
         sorted by ``frequency_rank`` ASC (most common first).
      2. ``encountered`` — tapped but not yet enrolled; same frequency order.
         Drained last so the cron eventually covers every engaged lemma.

    With ``include_failed=True``, also re-picks lemmas whose previous
    enrichment failed entirely (status='failed') OR was written but flagged
    by the verifier (status='done_flagged'). Use to clean up known-bad
    enrichment after a prompt improvement.
    """
    from sqlalchemy import or_
    from app.models import UserLemmaKnowledge

    db = database.SessionLocal()
    try:
        status_filter = Lemma.enrichment_status.is_(None)
        if include_failed:
            status_filter = or_(
                status_filter,
                Lemma.enrichment_status == "failed",
                Lemma.enrichment_status == "done_flagged",
            )
        rows = (
            db.query(
                Lemma.lemma_id,
                Lemma.frequency_rank,
                UserLemmaKnowledge.knowledge_state,
                UserLemmaKnowledge.acquisition_next_due,
            )
            .join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
            .filter(
                Lemma.language_code == language_code,
                Lemma.canonical_lemma_id.is_(None),
                (Lemma.word_category.is_(None) | Lemma.word_category.notin_(
                    ["function_word", "proper_name"]
                )),
                Lemma.gloss_en.isnot(None),
                UserLemmaKnowledge.knowledge_state.in_(_ELIGIBLE_STATES),
                status_filter,
            )
            .all()
        )
    finally:
        db.close()

    far_future = datetime.max.replace(tzinfo=timezone.utc).timestamp()

    def _sort_key(row):
        lemma_id, freq_rank, state, next_due = row
        if state == "acquiring":
            if next_due is None:
                due_ts = far_future
            else:
                if next_due.tzinfo is None:
                    next_due = next_due.replace(tzinfo=timezone.utc)
                due_ts = next_due.timestamp()
            return (0, due_ts, lemma_id)
        if state in ("learning", "lapsed"):
            return (1, freq_rank is None, freq_rank or 0, lemma_id)
        # encountered (backfill bucket)
        return (2, freq_rank is None, freq_rank or 0, lemma_id)

    rows.sort(key=_sort_key)
    return [r[0] for r in rows[:limit]]
