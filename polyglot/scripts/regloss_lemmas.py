#!/usr/bin/env python3
"""One-time gloss re-audit. Frequency-core seed imports occasionally pair a
lemma with the GLOSS of a homograph/derivative — `locus` (place) stored with
`I put, I place` (the verb `loco`'s gloss). The naive citation audit would
change the FORM to match the wrong gloss and corrupt the correct citation. This
tool trusts the form and judges the gloss instead, replacing only when the
gloss clearly describes a different word.

Phases: `audit` (read-only LLM judgment, checkpoint JSONL, shardable +
resumable) and `apply` (dry-run by default; --apply commits). One-shot — going
forward the existing batch-gloss pass (`lemma_gloss.ensure_glosses_batch`)
produces correct glosses for new lemmas.

Usage:

    # 1. AUDIT (read-only). For parallel shards, run several in nohup with
    #    --shard 0..N-1 --num-shards N; each writes its own checkpoint.
    python scripts/regloss_lemmas.py audit --language la
    python scripts/regloss_lemmas.py audit --language la --only-studied

    # 2. APPLY. Dry-run unless --apply.
    python scripts/regloss_lemmas.py apply --language la
    python scripts/regloss_lemmas.py apply --language la --apply

Always back up the DB before `--apply` (see CLAUDE.md server-ops).
Production run 2026-05-26: 301 reglosses + 3,511 pos-fills on Latin; 153 wrong
meanings fixed on studied lemmas (locus, pater, magnus, forum, domus, uideo…).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import database
from app.models import Lemma, UserLemmaKnowledge
from app.services.llm_cli import call_structured_json, resolve_model

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("regloss_lemmas")

_MODEL_ALIASES = {
    "sonnet": "claude-sonnet-4-5-20250929",
    "haiku":  "claude-haiku-4-5-20251001",
}
MODEL = resolve_model(os.environ.get("POLYGLOT_REGLOSS_MODEL", "claude-sonnet-4-5-20250929"), _MODEL_ALIASES)
BATCH = int(os.environ.get("POLYGLOT_REGLOSS_BATCH", "40"))
TIMEOUT_S = int(os.environ.get("POLYGLOT_REGLOSS_TIMEOUT", "300"))

LANG_NAME = {"el": "Modern Greek", "la": "Latin", "grc": "Ancient Greek"}
VALID_STATUS = {"ok", "regloss"}
VALID_POS = {
    "noun","verb","adjective","adverb","pronoun","preposition","conjunction",
    "article","particle","numeral","proper_noun","other",
}


def _data_dir() -> Path:
    d = Path(__file__).resolve().parents[1] / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _checkpoint_path(language: str, shard: int = 0, num_shards: int = 1) -> Path:
    if num_shards > 1:
        return _data_dir() / f"lemma_regloss_{language}_s{shard}of{num_shards}.jsonl"
    return _data_dir() / f"lemma_regloss_{language}.jsonl"


def _prompt(language: str, items: list[dict]) -> str:
    lang = LANG_NAME.get(language, language)
    block = "\n".join(
        f"[{it['index']}] form={it['form']!r}  existing_gloss={(it['gloss'] or '')!r}"
        for it in items
    )
    return f"""You are a {lang} lexicographer auditing GLOSSES (English meanings) — not forms.

The stored form is TRUSTED — it is the canonical dictionary citation form (verb
1sg present / noun nom. sg. / adj nom. sg. masc. positive). The English gloss,
however, may be wrong: a seed-import bug paired some lemmas with the gloss of a
HOMOGRAPH or DERIVATIVE of a different word.

Latin examples where the gloss describes a different word (regloss):
  locus  with stored gloss "I put, I place"        → regloss; gloss: "place; location"  (locus is a noun; the gloss describes the verb loco)
  forum  with stored gloss "I bore; I pierce"      → regloss; gloss: "public square; marketplace; forum"
  domus  with stored gloss "I tame; I subdue"      → regloss; gloss: "house; household"
  uideo  with stored gloss "vision; mental image"  → regloss; gloss: "to see"
  durus  with stored gloss "I harden"              → regloss; gloss: "hard; tough"
  centurio with stored gloss "century: assembly of 100" → regloss; gloss: "centurion"
  iubeo  with stored gloss "participle: commanded; noun: order" → regloss; gloss: "to order, command, bid"

Cases where the existing gloss is fine (ok):
  consul "consul"                       → ok
  res    "thing; matter; affair"        → ok
  senex  "old man"                      → ok
  ferrum "iron"                         → ok

For each entry decide a `status`:
- "ok": the existing gloss is a reasonable English meaning for this form.
- "regloss": the existing gloss describes a DIFFERENT word (homograph/derivative);
   provide the correct English gloss for the FORM in `gloss`.

Also return `pos` (noun/verb/adjective/adverb/pronoun/preposition/conjunction/
article/particle/numeral/proper_noun/other).

A blank or extremely short existing gloss (≤ 1 character, "?", "-") should be
treated as `regloss` and supplied with the correct gloss. Reference every entry
by its bracketed [index]. Skip nothing.

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
                    "pos": {"type": "string"},
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
        q = db.query(Lemma.lemma_id, Lemma.lemma_form, Lemma.gloss_en).filter(
            Lemma.language_code == args.language
        )
        if args.only_studied:
            q = q.join(UserLemmaKnowledge, UserLemmaKnowledge.lemma_id == Lemma.lemma_id)
        rows = q.order_by(Lemma.lemma_id.asc()).all()
    finally:
        db.close()

    pending = [
        {"lemma_id": lid, "form": form, "gloss": gloss or ""}
        for lid, form, gloss in rows
        if lid not in done
        and (args.num_shards == 1 or lid % args.num_shards == args.shard)
    ]
    log.info("Auditing %d lemmas (lang=%s shard=%d/%d batch=%d model=%s)",
             len(pending), args.language, args.shard, args.num_shards, BATCH, MODEL)

    written = 0
    with ckpt.open("a") as out:
        for start in range(0, len(pending), BATCH):
            chunk = pending[start:start + BATCH]
            items = [{"index": i, "form": c["form"], "gloss": c["gloss"]}
                     for i, c in enumerate(chunk)]
            t0 = time.time()
            structured = call_structured_json(
                prompt=_prompt(args.language, items),
                schema=_schema(),
                model=MODEL,
                timeout_s=TIMEOUT_S,
                log_context="gloss_regloss_judge",
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
                rec = {
                    "lemma_id": c["lemma_id"],
                    "old_form": c["form"],
                    "old_gloss": c["gloss"],
                    "status": r["status"],
                    "new_gloss": (r.get("gloss") or "").strip(),
                    "pos": (r.get("pos") or "").strip().lower(),
                    "note": (r.get("note") or "").strip(),
                }
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
            out.flush()
            log.info("batch @%d: %d/%d ok, %d missing (%.0fs)",
                     start, len(chunk) - missing, len(chunk), missing, dt)
    log.info("AUDIT DONE: wrote %d new verdicts to %s", written, ckpt)


def _load_verdicts(language: str) -> dict[int, dict]:
    files = sorted(_data_dir().glob(f"lemma_regloss_{language}*.jsonl"))
    out: dict[int, dict] = {}
    if not files:
        log.error("No checkpoint matching lemma_regloss_%s*.jsonl — run `audit` first", language)
        sys.exit(1)
    for f in files:
        for line in f.open():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                out[rec["lemma_id"]] = rec
            except Exception:
                pass
    log.info("Read verdicts from %d checkpoint file(s); total %d", len(files), len(out))
    return out


def cmd_apply(args) -> None:
    verdicts = _load_verdicts(args.language)
    log.info("status mix: %s", dict(Counter(v["status"] for v in verdicts.values())))
    actions: Counter[str] = Counter()
    samples: list[str] = []
    db = database.SessionLocal()
    try:
        for lemma_id, v in verdicts.items():
            lemma = db.get(Lemma, lemma_id)
            if lemma is None:
                actions["skip_missing"] += 1
                continue
            pos = v.get("pos") or None
            if pos and pos not in VALID_POS:
                pos = None
            if v["status"] == "regloss":
                new_gloss = (v.get("new_gloss") or "").strip()
                if not new_gloss:
                    actions["regloss_blank_skip"] += 1
                    continue
                if (lemma.gloss_en or "").strip() == new_gloss:
                    actions["regloss_noop"] += 1
                    continue
                if len(samples) < 25:
                    samples.append(f"  #{lemma_id} {lemma.lemma_form!r}: {(lemma.gloss_en or '')!r} -> {new_gloss!r}")
                lemma.gloss_en = new_gloss
                if pos and not lemma.pos:
                    lemma.pos = pos
                actions["reglossed"] += 1
            else:  # status == ok
                if pos and not lemma.pos:
                    lemma.pos = pos
                    actions["pos_filled"] += 1
                else:
                    actions["ok_no_change"] += 1

        if args.apply:
            db.commit()
            log.info("COMMITTED.")
            try:
                from app.services.activity_log import log_activity
                log_activity(
                    db, "manual_action",
                    f"Latin/{args.language} gloss re-audit: "
                    f"{actions.get('reglossed',0)} reglossed, "
                    f"{actions.get('pos_filled',0)} pos-filled",
                    detail=dict(actions),
                )
                db.commit()
            except Exception as e:
                log.warning("activity log failed: %s", e)
        else:
            db.rollback()
            log.info("DRY RUN — no changes written. Re-run with --apply to commit.")
    finally:
        db.close()

    log.info("Action summary: %s", dict(actions))
    if samples:
        log.info("Sample reglosses:\n%s", "\n".join(samples))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("audit", help="LLM-judge each lemma's gloss against its form (read-only)")
    pa.add_argument("--language", default="la")
    pa.add_argument("--shard", type=int, default=0)
    pa.add_argument("--num-shards", type=int, default=1)
    pa.add_argument("--only-studied", action="store_true",
                    help="restrict to lemmas that have a UserLemmaKnowledge row")
    pa.set_defaults(func=cmd_audit)

    pp = sub.add_parser("apply", help="rewrite glosses from the checkpoint")
    pp.add_argument("--language", default="la")
    pp.add_argument("--apply", action="store_true", help="commit (default: dry run)")
    pp.set_defaults(func=cmd_apply)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
