#!/usr/bin/env python3
"""One-time gloss repair for multi-sense entries that lost their separators.

Sibling to `regloss_lemmas.py`. Different bug class: `regloss_lemmas.py`
trusts the form and replaces the gloss when it describes a different word
(e.g. `locus` paired with `loco`'s verb gloss). This script trusts both the
form AND the meanings — it just fixes glosses where multiple senses are
concatenated without a separator (e.g. `excidium → "demolition setting of
the sun"`, which should be `"demolition; setting of the sun"`). Root cause:
the Roma Aeterna .apkg parser collapsed `<div>sense1</div><div>sense2</div>`
to a single space (fixed in `parse_roma_aeterna_apkg.py` 2026-05-26, but
that fix only prevents re-occurrence — it doesn't repair the existing TSV
which was already loaded into prod).

The heuristic picks candidates with the run-on pattern (2+ words, no
separators, doesn't look like a verb/article phrase); the LLM judges each
("is this run-on or is it one fluent definition?") and rewrites only when
clearly multi-sense. Studied-lemma-first ordering so the lookup card damage
is repaired first.

Usage (mirror regloss_lemmas.py):
    # 1. AUDIT (read-only)
    python scripts/repair_runon_glosses.py audit --language la
    python scripts/repair_runon_glosses.py audit --language la --only-studied

    # 2. APPLY (dry-run unless --apply)
    python scripts/repair_runon_glosses.py apply --language la
    python scripts/repair_runon_glosses.py apply --language la --apply

Always back up the DB before `--apply`:
    cp polyglot.db /opt/alif-backups/polyglot_pre_runon_$(date +%Y%m%d_%H%M%S).db
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import database
from app.models import Lemma, UserLemmaKnowledge
from app.services.activity_log import log_activity
from app.services.llm_cli import call_structured_json, resolve_model

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("repair_runon_glosses")

_MODEL_ALIASES = {
    "sonnet": "claude-sonnet-4-5-20250929",
    "haiku":  "claude-haiku-4-5-20251001",
}
MODEL = resolve_model(os.environ.get("POLYGLOT_REPAIR_MODEL", "claude-sonnet-4-5-20250929"), _MODEL_ALIASES)
BATCH = int(os.environ.get("POLYGLOT_REPAIR_BATCH", "30"))
TIMEOUT_S = int(os.environ.get("POLYGLOT_REPAIR_TIMEOUT", "300"))

LANG_NAME = {"el": "Modern Greek", "la": "Latin", "grc": "Ancient Greek"}
VALID_STATUS = {"ok", "repair"}

# Run-on candidate filter. Pattern:
#   - 2+ tokens (2-word run-ons are common: `aeneus → "copper bronze"`,
#     `anxius → "anxious unshorn"`, `administer → "assistant attendant"`)
#   - no comma, semicolon, period, parens, quotes, or numbered list markers
#   - doesn't start with a verb-phrase or article-phrase prefix
# This is intentionally a permissive filter — the LLM is the second-pass
# judge that decides whether each candidate is actually run-on. Tightening
# here just costs LLM calls; loosening risks missing real damage.
_PHRASE_PREFIXES = ("to ", "i ", "the ", "a ", "an ", "of ", "in ", "by ", "for ")


def _looks_runon(gloss: str | None) -> bool:
    if not gloss:
        return False
    g = gloss.strip()
    if not g:
        return False
    if any(ch in g for ch in ",;.()\"'"):
        return False
    if re.search(r"\b\d+\.", g):
        return False
    words = g.split()
    if len(words) < 2:
        return False
    # Multiple first-person verb senses fused without a separator
    # ("I surround I circle", "I feed I pasture I consume") are run-ons even
    # though they start with the "i " phrase prefix. This whole class — the
    # dominant shape of Latin DCC-seed verb glosses — was invisible before
    # because the prefix-skip below dropped any gloss starting with "i ". A
    # single "I <verb>" sense ("I stand firm") still falls through to the skip.
    if len(re.findall(r"\bI\s+[a-z]", g)) >= 2:
        return True
    low = g.lower()
    if low.startswith(_PHRASE_PREFIXES):
        return False
    return True


def _data_dir() -> Path:
    d = Path(__file__).resolve().parents[1] / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _checkpoint_path(language: str, shard: int = 0, num_shards: int = 1) -> Path:
    if num_shards > 1:
        return _data_dir() / f"lemma_runon_repair_{language}_s{shard}of{num_shards}.jsonl"
    return _data_dir() / f"lemma_runon_repair_{language}.jsonl"


def _prompt(language: str, items: list[dict]) -> str:
    lang = LANG_NAME.get(language, language)
    block = "\n".join(
        f"[{it['index']}] form={it['form']!r}  pos={it.get('pos') or '?'!r}  "
        f"gloss={it['gloss']!r}"
        for it in items
    )
    return f"""You are a {lang} lexicographer auditing GLOSS PUNCTUATION. Each
entry below has a stored English gloss with no separators ("demolition setting
of the sun"). Some are RUN-ON multi-sense glosses where commas/semicolons were
lost in seed-import ("demolition" + "setting of the sun" → should be
"demolition; setting of the sun"). Others are FLUENT single-sense definitions
("instrument whereby anything is bound") that read fine without separators.

The form is TRUSTED. The semantic meaning is also TRUSTED — do NOT add or remove
meanings, only re-punctuate. If multiple senses are clearly fused, output them
semicolon-separated in the order they appear, lightly trimmed for readability.

Examples (Latin):
  excidium  "demolition setting of the sun"          → repair: "demolition; setting of the sun"
  exordium  "beginning introduction foundation"      → repair: "beginning; introduction; foundation"
  administer "assistant attendant"                    → repair: "assistant; attendant"
  aeneus   "copper bronze"                            → repair: "(of) copper; (of) bronze"  (kept as adjective senses)
  anxius   "anxious unshorn"                          → repair: "anxious; unshorn"
  finitimus "bordering upon originating from neighboring people"
                                                       → repair: "bordering upon; originating from neighboring people"
  vinculum "instrument whereby anything is bound or tied up"
                                                       → ok    (single definition with relative clause; no run-on)
  senatus  "senate or parliament"                     → ok    (disjunctive within ONE sense)
  defensor "one who defends"                          → ok    (relative-clause definition)
  totidem  "just as many"                             → ok    (single adverbial sense)

For each entry decide a `status`:
- "ok": the gloss is a single fluent definition, even if multi-word — no repair.
- "repair": the gloss is run-on multi-sense — provide a properly punctuated
   `gloss` using ";" between senses. Keep meanings unchanged.

Be conservative — when in doubt, mark "ok". A wrong repair is worse than a
missed one because we can re-run this with a wider filter later.

Reference every entry by its bracketed [index]. Skip nothing.

Entries:
{block}
"""


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "results": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "status": {"type": "string", "enum": sorted(VALID_STATUS)},
                    "gloss": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["index", "status"],
            }},
        },
        "required": ["results"],
    }


def cmd_audit(args) -> None:
    ckpt = _checkpoint_path(args.language, args.shard, args.num_shards)
    done: set[int] = set()
    if ckpt.exists():
        for line in ckpt.open():
            line = line.strip()
            if line:
                try: done.add(json.loads(line)["lemma_id"])
                except Exception: pass
    log.info("Checkpoint %s has %d already audited", ckpt, len(done))

    db = database.SessionLocal()
    try:
        q = db.query(Lemma.lemma_id, Lemma.lemma_form, Lemma.gloss_en, Lemma.pos,
                     Lemma.frequency_rank).filter(
            Lemma.language_code == args.language,
            Lemma.canonical_lemma_id.is_(None),
        )
        if args.only_studied:
            q = q.join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        rows = q.order_by(Lemma.frequency_rank.asc().nullslast()).all()
    finally:
        db.close()

    pending = [
        {"lemma_id": lid, "form": form, "gloss": gloss or "", "pos": pos}
        for lid, form, gloss, pos, _rank in rows
        if lid not in done
        and _looks_runon(gloss)
        and (args.num_shards == 1 or lid % args.num_shards == args.shard)
    ]
    log.info("Auditing %d run-on candidates (lang=%s shard=%d/%d batch=%d model=%s)",
             len(pending), args.language, args.shard, args.num_shards, BATCH, MODEL)

    written = 0
    with ckpt.open("a") as out:
        for start in range(0, len(pending), BATCH):
            chunk = pending[start:start + BATCH]
            items = [{"index": i, "form": c["form"], "gloss": c["gloss"], "pos": c["pos"]}
                     for i, c in enumerate(chunk)]
            t0 = time.time()
            structured = call_structured_json(
                prompt=_prompt(args.language, items),
                schema=_schema(),
                model=MODEL,
                timeout_s=TIMEOUT_S,
                log_context="gloss_runon_repair",
                runner=subprocess.run,
            )
            dt = time.time() - t0
            if not isinstance(structured, dict):
                log.warning("batch @%d failed (%.0fs) — retry on resume", start, dt)
                continue
            by_index = {r["index"]: r for r in structured.get("results", [])
                        if isinstance(r, dict) and isinstance(r.get("index"), int)
                        and r.get("status") in VALID_STATUS}
            missing = 0
            for i, c in enumerate(chunk):
                r = by_index.get(i)
                if r is None:
                    missing += 1
                    continue
                row = {
                    "lemma_id": c["lemma_id"],
                    "form": c["form"],
                    "old_gloss": c["gloss"],
                    "status": r["status"],
                    "new_gloss": r.get("gloss", "").strip() or None,
                    "note": r.get("note", ""),
                }
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
            log.info("batch %d-%d done in %.0fs (%d written, %d missing)",
                     start, start + len(chunk), dt, len(chunk) - missing, missing)
            out.flush()

    log.info("Audit complete: %d new rows written to %s", written, ckpt)


def cmd_apply(args) -> None:
    """Replay checkpoint(s) and update the DB. Dry-run unless --apply."""
    pattern = f"lemma_runon_repair_{args.language}*.jsonl"
    ckpts = sorted(_data_dir().glob(pattern))
    if not ckpts:
        log.error("No checkpoints found matching %s", pattern)
        return
    log.info("Replaying %d checkpoint(s): %s", len(ckpts), [c.name for c in ckpts])

    records: dict[int, dict] = {}
    for ckpt in ckpts:
        for line in ckpt.open():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                records[r["lemma_id"]] = r
            except Exception:
                pass

    counts = Counter(r["status"] for r in records.values())
    log.info("Verdict histogram: %s", dict(counts))

    repairs = [r for r in records.values()
               if r["status"] == "repair" and r.get("new_gloss")
               and r["new_gloss"] != r["old_gloss"]]
    log.info("%d repairs to apply (other rows are ok or no-change)", len(repairs))

    if not args.apply:
        log.info("DRY RUN — examples:")
        for r in repairs[:20]:
            log.info("  #%d %s: %r → %r",
                     r["lemma_id"], r["form"], r["old_gloss"], r["new_gloss"])
        log.info("Pass --apply to commit.")
        return

    db = database.SessionLocal()
    try:
        applied = 0
        for r in repairs:
            lemma = db.get(Lemma, r["lemma_id"])
            if lemma is None:
                continue
            if (lemma.gloss_en or "") != r["old_gloss"]:
                log.info("skip #%d %s: gloss drifted (DB=%r ckpt=%r)",
                         r["lemma_id"], r["form"], lemma.gloss_en, r["old_gloss"])
                continue
            lemma.gloss_en = r["new_gloss"]
            applied += 1
            if applied % 50 == 0:
                db.commit()
                log.info("committed %d", applied)
        db.commit()
        log_activity(
            db,
            event_type="gloss_runon_repair_applied",
            summary=f"Re-punctuated {applied} run-on glosses on {args.language}",
            detail={"lemmas": applied, "language": args.language},
        )
        log.info("Applied %d repairs", applied)
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    aud = sub.add_parser("audit", help="LLM-judge candidates, write checkpoint")
    aud.add_argument("--language", required=True, choices=["la", "el", "grc"])
    aud.add_argument("--only-studied", action="store_true",
                     help="Restrict to lemmas with a UserLemmaKnowledge row")
    aud.add_argument("--shard", type=int, default=0)
    aud.add_argument("--num-shards", type=int, default=1)

    apl = sub.add_parser("apply", help="Replay checkpoints, update DB")
    apl.add_argument("--language", required=True, choices=["la", "el", "grc"])
    apl.add_argument("--apply", action="store_true",
                     help="Actually write to the DB (default: dry-run)")

    args = ap.parse_args(argv)
    if args.cmd == "audit":
        cmd_audit(args)
    else:
        cmd_apply(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
