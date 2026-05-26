"""Philological enrichment for lemmas.

Generates ``Lemma.enrichment_json`` payloads using the configured structured
LLM CLI. Surfaced in the lookup card + lemma detail screen (Modern Editorial
design).

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
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app import database
from app.models import Lemma
from app.schemas import LemmaEnrichment
from app.services.llm_cli import call_structured_json, resolve_model

log = logging.getLogger(__name__)


_MODEL_ALIASES = {
    "sonnet": "claude-sonnet-4-5-20250929",
    "haiku": "claude-haiku-4-5-20251001",
}


def _resolve_model(raw: str) -> str:
    return resolve_model(raw, _MODEL_ALIASES)


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
    from app.schemas import era_enum_for

    lang = LANG_DISPLAY.get(language_code, language_code)
    era_list = " / ".join(era_enum_for(language_code))
    if language_code == "la":
        orthography_note = (
            "Latin text in classical orthography; macrons optional but consistent. "
            "ancient_form holds the attested Old Latin / Proto-Italic parent form "
            "when known (Latin has no Ancient Greek parent)."
        )
    elif language_code == "grc":
        orthography_note = "Greek text in polytonic throughout."
    else:
        orthography_note = (
            "Greek text uses polytonic for Ancient/Koine/Byzantine, monotonic for Modern."
        )
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
  {era_list}), form used at
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
  "loanword-from-latin" (English took it from Latin), "borrowed-via-latin"
  (Greek → Latin → English), "shared-pie-root" (both inherit from PIE),
  "calque" (semantic parallel, not cognate), "descendant" (a Romance reflex —
  Italian/Spanish/French — of the Latin word), "cognate" (related form in a
  sister language). For Latin lemmas, the most useful entries are usually the
  Romance "descendant" reflexes and the English "loanword-from-latin".
- gloss_en for non-English forms. Optional note for false-friend warnings or
  semantic drift.

QUOTES (list, 1-3 entries):
- Short, ≤25 words each. Famous, surprising, or representative usages across
  eras. Translations to English.
- Source must be specific enough to locate: "Homer, Iliad 1.5", "Plato,
  Republic 484a", "John 1:1", "Cavafy, Ithaca". Avoid bare attributions.
- `translation_en` MUST be a faithful English rendering of `text`. Do NOT
  emit meta-commentary like `[Context: ...]`, `[This passage illustrates ...]`,
  `[The line shows ...]`, or any other bracketed editorial note in place of
  a translation. If a quote cannot be translated cleanly within ≤25 words,
  choose a shorter quote instead — never substitute a meta-comment.
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
- {orthography_note}
- Translations in plain English, no scholarly markup. Be evocative but
  precise — this surfaces in a learning UI, not a journal article.
- Be honest about gaps. If you don't know the form at an era, omit that stage.
  If you don't know a PIE root, set pie_root to null.

Lemmas:
{target_block}
"""


def _gen_schema(language_code: str) -> dict:
    from app.schemas import era_enum_for

    era_enum = list(era_enum_for(language_code))
    # Union of Greek- and Latin-relevant relations (constrained-decoding enum;
    # the prompt steers which apply per language).
    relation_enum = [
        "loanword-from-greek",
        "loanword-from-latin",
        "borrowed-via-latin",
        "shared-pie-root",
        "calque",
        "descendant",
        "cognate",
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


def _call_llm(
    *,
    prompt: str,
    schema: dict,
    model: str,
    timeout_s: int,
    log_context: str,
) -> Optional[dict]:
    """Run the configured structured-output LLM CLI."""
    return call_structured_json(
        prompt=prompt,
        schema=schema,
        model=model,
        timeout_s=timeout_s,
        log_context=log_context,
        runner=subprocess.run,
    )


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
    started = time.time()
    structured = _call_llm(
        prompt=prompt,
        schema=_gen_schema(language_code),
        model=ENRICH_MODEL,
        timeout_s=ENRICH_TIMEOUT_S,
        log_context="lemma_enrichment",
    )
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
        _strip_meta_commentary_quotes(payload, form)
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


# Deterministic post-check: the QUOTES prompt is fact-skipped by the verifier
# (2026-05-21 spec — quotes/cognates/register are memory hooks where mild
# inaccuracy is acceptable), so a quote whose `translation_en` is a bracketed
# editorial note ("[Context: ...]") or a meta-description ("This passage
# illustrates X") slips through silently. The prompt now bans this explicitly,
# but defense in depth: strip such quotes here too, before the row is written.
# Caught in production 2026-05-26: `fere` quote 1 was a 734-char Caesar passage
# whose translation was "[Context: the passage contains 'omnibus fere annis'
# illustrating fere with quantities]". The audit doc lives at
# research/polyglot-latin-philology-and-translation-audit-2026-05-26.md.
_META_PREFIX = ("[", "(context", "(this passage", "(the line")
_META_PHRASES = (
    "illustrating",
    "illustrates",
    "this passage shows",
    "this passage contains",
    "this line shows",
    "this quote",
    "the line shows",
    "the passage contains",
    "[context",
)


def _is_meta_commentary(translation: str) -> bool:
    t = (translation or "").strip().lower()
    if not t:
        return False
    if t.startswith(_META_PREFIX):
        return True
    return any(p in t for p in _META_PHRASES)


def _strip_meta_commentary_quotes(payload: dict, form: str) -> None:
    """Remove quote entries whose translation_en is meta-commentary."""
    quotes = payload.get("quotes")
    if not isinstance(quotes, list):
        return
    kept: list[dict] = []
    for q in quotes:
        if not isinstance(q, dict):
            continue
        tr = q.get("translation_en")
        if isinstance(tr, str) and _is_meta_commentary(tr):
            log.info("quote_meta_commentary_stripped lemma_form=%s text=%r tr=%r",
                     form, (q.get("text") or "")[:80], tr[:120])
            continue
        kept.append(q)
    payload["quotes"] = kept


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

    started = time.time()
    structured = _call_llm(
        prompt=_verify_prompt(language_code, items),
        schema=_verify_schema(),
        model=VERIFY_MODEL,
        timeout_s=VERIFY_TIMEOUT_S,
        log_context="lemma_enrichment_verify",
    )
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


# ─── Self-correct pass ─────────────────────────────────────────────────────
# When the Haiku verifier flags etymology / diachrony, send the bad payload +
# the verifier's correction note back to Sonnet for ONE corrective call, then
# re-verify. Loop up to SELF_CORRECT_MAX_ATTEMPTS times. After that, accept
# defeat — strip the flagged field (etymology=None or diachrony=[]) and stamp
# `done_partial` so a wrong PIE root never reaches the lookup card.


SELF_CORRECT_MAX_ATTEMPTS = max(0, int(os.environ.get("POLYGLOT_ENRICH_SELF_CORRECT_MAX", "2")))


def _self_correct_prompt(
    language_code: str,
    target: _EnrichTarget,
    payload: LemmaEnrichment,
    verdict: _Verdict,
) -> str:
    lang = LANG_DISPLAY.get(language_code, language_code)
    payload_json = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2)
    issue_label = "etymology" if verdict.verdict == "etymology_issue" else "diachrony"
    return f"""You are a {lang} philology assistant. A previous draft enrichment
for the lemma below was flagged by an independent fact-checker. Produce a
CORRECTED version that fixes the specific {issue_label} issue noted, while
preserving everything the fact-checker did not flag.

Lemma: {target.lemma_form}
POS: {target.pos}
Gloss: {target.gloss_en}

Original enrichment (JSON):
{payload_json}

Fact-checker verdict: {verdict.verdict}
Fact-checker note:
{verdict.note}

Rules for the correction:
- Address the specific factual problem identified above.
- Preserve all OTHER content (other diachrony stages, cognates, quotes,
  register, etc.) byte-for-byte unless the same content is downstream of the
  flagged claim.
- If the correction would require new information you are not confident in
  (e.g. "what's the RIGHT PIE root if this one is wrong?"), set that
  specific field to null rather than guessing. A null is correct; a
  plausible-sounding wrong fact is the failure mode we are trying to fix.
- Return the FULL enrichment payload (etymology + diachrony + cognates +
  quotes + register), not just the changed slice.
- Style rules: polytonic for Ancient/Koine/Byzantine, monotonic for Modern.
  Plain English translations. version: 1.
"""


def _self_correct_call(
    language_code: str,
    target: _EnrichTarget,
    payload: LemmaEnrichment,
    verdict: _Verdict,
) -> Optional[LemmaEnrichment]:
    """One Sonnet self-correct call. Returns the corrected enrichment or
    ``None`` on LLM failure / parse failure / lemma mismatch."""
    prompt = _self_correct_prompt(language_code, target, payload, verdict)
    started = time.time()
    structured = _call_llm(
        prompt=prompt,
        schema=_gen_schema(language_code),
        model=ENRICH_MODEL,
        timeout_s=ENRICH_TIMEOUT_S,
        log_context="lemma_enrichment_self_correct",
    )
    elapsed = time.time() - started
    if not structured:
        _log_pipeline({
            "event": "self_correct_failed",
            "language_code": language_code,
            "lemma_id": target.lemma_id,
            "elapsed_s": round(elapsed, 1),
            "model": ENRICH_MODEL,
        })
        return None
    for item in structured.get("lemmas", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("lemma_form") != target.lemma_form:
            continue
        e = item.get("enrichment")
        if not isinstance(e, dict):
            continue
        _strip_meta_commentary_quotes(e, target.lemma_form)
        try:
            corrected = LemmaEnrichment.model_validate(e)
        except Exception as ex:
            log.warning("Self-correct payload failed validation for %s: %s", target.lemma_form, ex)
            return None
        _log_pipeline({
            "event": "self_correct_returned",
            "language_code": language_code,
            "lemma_id": target.lemma_id,
            "original_verdict": verdict.verdict,
            "elapsed_s": round(elapsed, 1),
            "model": ENRICH_MODEL,
        })
        return corrected
    return None


def _self_correct_pass(
    language_code: str,
    chunk: list[_EnrichTarget],
    parsed: dict[str, LemmaEnrichment],
    verdicts: dict[int, _Verdict],
) -> None:
    """For each flagged lemma in the chunk, try up to SELF_CORRECT_MAX_ATTEMPTS
    self-correct + re-verify rounds. Mutates ``parsed`` and ``verdicts`` in
    place so the final state reflects the best version we could produce.

    Loop semantics per lemma:
      attempt 1: self-correct → re-verify
                 - if OK, done.
                 - if still flagged, loop.
                 - if re-verifier itself failed, accept the corrected payload
                   and clear the verdict (caller writes `done_unverified`).
                 - if Sonnet self-correct failed, break (caller keeps the
                   pre-correct state).
      attempt 2: same as attempt 1.
      after loop: caller looks at the final verdict — if still flagged, the
                  write phase strips the bad field and stamps `done_partial`.
    """
    if SELF_CORRECT_MAX_ATTEMPTS <= 0:
        return
    by_id = {t.lemma_id: t for t in chunk}
    flagged_ids = [lid for lid, v in verdicts.items() if v.verdict != "ok"]
    for lid in flagged_ids:
        target = by_id.get(lid)
        if target is None:
            continue
        if target.lemma_form not in parsed:
            continue
        for attempt in range(1, SELF_CORRECT_MAX_ATTEMPTS + 1):
            current_verdict = verdicts.get(lid)
            if current_verdict is None or current_verdict.verdict == "ok":
                break
            corrected = _self_correct_call(
                language_code, target, parsed[target.lemma_form], current_verdict,
            )
            if corrected is None:
                # Sonnet self-correct gave up; keep prior payload + verdict.
                break
            parsed[target.lemma_form] = corrected
            re_verdicts = _verify_enrichment_facts(
                language_code, {target.lemma_form: corrected}, [target],
            )
            new_verdict = re_verdicts.get(lid)
            if new_verdict is None:
                # Re-verifier itself failed. Accept the corrected payload as
                # best-effort and clear the verdict so the caller writes
                # `done_unverified`.
                verdicts.pop(lid, None)
                _log_pipeline({
                    "event": "self_correct_reverify_failed",
                    "language_code": language_code,
                    "lemma_id": lid,
                    "attempt": attempt,
                })
                break
            verdicts[lid] = new_verdict
            _log_pipeline({
                "event": "self_correct_attempt_done",
                "language_code": language_code,
                "lemma_id": lid,
                "attempt": attempt,
                "verdict_after": new_verdict.verdict,
            })
            if new_verdict.verdict == "ok":
                break


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


_enrich_lock = threading.Lock()


def batch_enrich(
    language_code: str,
    lemma_ids: list[int],
) -> dict:
    """Enrich the given lemmas. Returns a summary dict:

        {"enriched": int, "failed_lemma_ids": [...], "skipped_lemma_ids": [...]}

    Internally splits ``lemma_ids`` into chunks of ``ENRICH_BATCH_SIZE`` and
    issues one Sonnet call per chunk. Each chunk independently commits, so a
    partial run still yields persisted enrichment for the successful chunks.

    Process-level non-blocking lock prevents overlapping runs from picking the
    same un-enriched lemmas and double-spending Claude budget. Mirrors
    ``material_generator._warm_cache_lock``. Returns ``{"skipped": True,
    "reason": "enrich_busy"}`` if another run is already in progress.
    """
    if not lemma_ids:
        return {"enriched": 0, "failed_lemma_ids": [], "skipped_lemma_ids": []}

    if not _enrich_lock.acquire(blocking=False):
        log.info("Enrichment already running; skipping this invocation")
        return {
            "skipped": True,
            "reason": "enrich_busy",
            "enriched": 0,
            "failed_lemma_ids": [],
            "skipped_lemma_ids": list(lemma_ids),
        }
    try:
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

            # Self-correct flagged lemmas inline so bad facts never reach the
            # lookup card. Mutates parsed + verdicts in place; persistent flags
            # after SELF_CORRECT_MAX_ATTEMPTS get their bad field stripped in
            # _write_chunk and stamped `done_partial`.
            _self_correct_pass(language_code, chunk, parsed, verdicts)

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
    finally:
        _enrich_lock.release()


def _write_chunk(
    language_code: str,
    chunk: list[_EnrichTarget],
    parsed: dict[str, LemmaEnrichment],
    verdicts: Optional[dict[int, _Verdict]] = None,
) -> tuple[int, list[int]]:
    """Persist parsed enrichments for one chunk. Returns (enriched, failed_ids).

    Phase 3 of the read/LLM/write pattern. Single commit per chunk.

    Status taxonomy:
      - ``done``           — Sonnet + Haiku both OK (possibly after self-correct).
      - ``done_unverified``— Haiku verifier itself failed to run; payload kept.
      - ``done_partial``   — verifier persistently flagged after the self-correct
                             pass exhausted attempts. The flagged top-level
                             field is STRIPPED before writing so a wrong PIE
                             root / wrong diachrony stage never reaches the
                             lookup card. ``_verifier_note`` records the final
                             verdict + ``stripped=True``.
      - ``failed``         — Sonnet itself never produced a parseable payload.
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
                # Persistent flag after self-correct. Strip the flagged
                # top-level field so the bad fact never ships to the UI.
                if verdict.verdict == "etymology_issue":
                    data["etymology"] = None
                elif verdict.verdict == "diachrony_issue":
                    data["diachrony"] = []
                data["_verifier_note"] = {
                    "verdict": verdict.verdict,
                    "note": verdict.note,
                    "stripped": True,
                }
                lemma.enrichment_status = "done_partial"
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
    enrichment fell short of clean:
      - ``failed``        — Sonnet never produced a parseable payload.
      - ``done_flagged``  — legacy (pre-self-correct) verifier-flagged rows.
      - ``done_partial``  — verifier persistently flagged after self-correct;
                            field was stripped. Retry in case a prompt
                            improvement or fresh model run produces a clean
                            verdict.
    The cron passes ``--include-failed`` by default so these never linger.
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
                Lemma.enrichment_status == "done_partial",
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
