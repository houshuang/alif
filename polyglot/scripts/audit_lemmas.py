#!/usr/bin/env python3
"""LLM audit + repair of every Lemma row's citation form.

simplemma routinely stored inflected surface forms as lemmas (εξελίχθηκαν for
εξελίσσομαι, πλεονάσματος for πλεόνασμα). This script uses Claude — a far better
Greek lemmatizer than any dictionary library — to judge every lemma and rewrite
the database so each row is a correct dictionary citation form.

Two phases, run separately:

    # 1. AUDIT (read-only): LLM judges every lemma, appends to a checkpoint.
    #    Resumable + shardable for parallel runs.
    python scripts/audit_lemmas.py audit --language el
    python scripts/audit_lemmas.py audit --language el --shard 0 --num-shards 4

    # 2. APPLY: read the checkpoint, repair the DB. Dry-run unless --apply.
    python scripts/audit_lemmas.py apply --language el            # preview
    python scripts/audit_lemmas.py apply --language el --apply    # commit

Checkpoint: data/lemma_audit_<lang>.jsonl — one verdict per lemma, append-only.
Always back up the DB before `--apply` (see CLAUDE.md server-ops).
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
from app.models import Lemma
from app.services.lemma_integrity import apply_citation_fix

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("audit_lemmas")

MODEL = os.environ.get("POLYGLOT_AUDIT_MODEL", "claude-sonnet-4-5-20250929")
BATCH_SIZE = int(os.environ.get("POLYGLOT_AUDIT_BATCH", "40"))
TIMEOUT_S = int(os.environ.get("POLYGLOT_AUDIT_TIMEOUT", "300"))

LANG_NAME = {"el": "Modern Greek", "grc": "Ancient Greek", "la": "Latin"}

VALID_STATUS = {"ok", "fix", "proper_name", "function_word", "not_word"}
VALID_POS = {
    "noun", "verb", "adjective", "adverb", "pronoun", "preposition",
    "conjunction", "article", "particle", "numeral", "proper_noun", "other",
}


def _data_dir() -> Path:
    d = Path(__file__).resolve().parents[1] / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _checkpoint_path(language: str, shard: int = 0, num_shards: int = 1) -> Path:
    # Per-shard files so parallel audit processes never interleave JSONL lines.
    if num_shards > 1:
        return _data_dir() / f"lemma_audit_{language}_s{shard}of{num_shards}.jsonl"
    return _data_dir() / f"lemma_audit_{language}.jsonl"


def _prompt(language: str, items: list[dict]) -> str:
    lang = LANG_NAME.get(language, language)
    lines = []
    for it in items:
        g = f"  (current gloss: {it['gloss']})" if it.get("gloss") else ""
        lines.append(f"[{it['index']}] {it['form']}{g}")
    block = "\n".join(lines)
    return f"""You are a {lang} lexicographer auditing a learner's vocabulary database.

Each entry below has a stored "lemma" that SHOULD be the dictionary citation
form, plus an English gloss. Many entries were produced by an automatic
lemmatizer that frequently failed, leaving an INFLECTED surface form stored as
if it were the lemma (a verb in a past/passive tense, a noun in genitive or
plural, an adjective in a comparative or non-masculine form).

For each entry decide a `status`:
- "ok": the stored form already IS the correct {lang} citation form.
- "fix": it is an inflected or misspelled form — give the correct citation form
  in `citation` (with correct accents/diacritics).
- "proper_name": it is a proper noun (person, place, ethnonym such as Σουμέριοι);
  put the standard nominative form in `citation`.
- "function_word": article, pronoun, preposition, conjunction, or particle.
- "not_word": OCR fragment / truncation / junk. If the intended word is
  inferable from the gloss put it in `citation`, otherwise leave it blank.

Citation-form rules for {lang}:
- Verbs: 1st person singular present (active -ω; deponent/passive-only -ομαι/-άμαι).
  e.g. εξελίχθηκαν → εξελίσσομαι, στηριζόταν → στηρίζομαι, οργανώθηκαν → οργανώνω.
- Nouns: nominative singular. e.g. πλεονάσματος → πλεόνασμα, εκδηλώσεις → εκδήλωση.
- Adjectives: nominative singular masculine, positive degree.
  e.g. ευρύτερο → ευρύς, συστηματική → συστηματικός.

Also return `pos` (one of: noun, verb, adjective, adverb, pronoun, preposition,
conjunction, article, particle, numeral, proper_noun, other) and `gloss` — an
accurate English gloss for the CITATION form. If the existing gloss already fits
the citation form, repeat it; if it described the inflection (e.g. "you will
shoot"), correct it to the lemma gloss ("to shoot").

Reference every entry by its bracketed [index]. Skip nothing.

Entries:
{block}
"""


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "status": {"type": "string", "enum": sorted(VALID_STATUS)},
                        "citation": {"type": "string"},
                        "pos": {"type": "string"},
                        "gloss": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    "required": ["index", "status"],
                },
            },
        },
        "required": ["results"],
    }


def _call_cli(prompt: str) -> dict | None:
    cmd = [
        "claude", "-p", "--output-format", "json",
        "--model", MODEL,
        "--json-schema", json.dumps(_schema()),
        prompt,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        log.warning("CLI timeout after %ds", TIMEOUT_S)
        return None
    if proc.returncode != 0:
        log.warning("CLI exit %d: %s", proc.returncode, proc.stderr[:300])
        return None
    try:
        wrapper = json.loads(proc.stdout)
    except json.JSONDecodeError:
        log.warning("envelope parse fail: %s", proc.stdout[:200])
        return None
    structured = wrapper.get("structured_output")
    if isinstance(structured, dict):
        return structured
    result_str = wrapper.get("result", "")
    if result_str:
        try:
            parsed = json.loads(result_str)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def cmd_audit(args) -> None:
    ckpt = _checkpoint_path(args.language, args.shard, args.num_shards)
    done: set[int] = set()
    if ckpt.exists():
        for line in ckpt.open():
            line = line.strip()
            if line:
                try:
                    done.add(json.loads(line)["lemma_id"])
                except Exception:
                    pass
    log.info("Checkpoint %s has %d lemmas already audited", ckpt, len(done))

    db = database.SessionLocal()
    try:
        rows = (
            db.query(Lemma.lemma_id, Lemma.lemma_form, Lemma.gloss_en)
            .filter(Lemma.language_code == args.language)
            .order_by(Lemma.lemma_id.asc())
            .all()
        )
    finally:
        db.close()

    pending = [
        {"lemma_id": lid, "form": form, "gloss": gloss or ""}
        for lid, form, gloss in rows
        if lid not in done
        and (args.num_shards == 1 or lid % args.num_shards == args.shard)
    ]
    log.info("Auditing %d lemmas (shard %d/%d, batch %d, model %s)",
             len(pending), args.shard, args.num_shards, BATCH_SIZE, MODEL)

    written = 0
    with ckpt.open("a") as out:
        for start in range(0, len(pending), BATCH_SIZE):
            chunk = pending[start:start + BATCH_SIZE]
            items = [{"index": i, "form": c["form"], "gloss": c["gloss"]}
                     for i, c in enumerate(chunk)]
            t0 = time.time()
            structured = _call_cli(_prompt(args.language, items))
            dt = time.time() - t0
            if not structured:
                log.warning("batch @%d failed (%.0fs) — will retry on resume", start, dt)
                continue
            by_index = {}
            for r in structured.get("results", []) or []:
                if isinstance(r, dict) and isinstance(r.get("index"), int):
                    by_index[r["index"]] = r
            missing = 0
            for i, c in enumerate(chunk):
                r = by_index.get(i)
                if r is None:
                    missing += 1
                    continue
                status = r.get("status")
                if status not in VALID_STATUS:
                    missing += 1
                    continue
                rec = {
                    "lemma_id": c["lemma_id"],
                    "old_form": c["form"],
                    "old_gloss": c["gloss"],
                    "status": status,
                    "citation": (r.get("citation") or "").strip(),
                    "pos": (r.get("pos") or "").strip().lower(),
                    "gloss": (r.get("gloss") or "").strip(),
                    "note": (r.get("note") or "").strip(),
                }
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
            out.flush()
            log.info("batch @%d: %d/%d ok, %d missing (%.0fs)",
                     start, len(chunk) - missing, len(chunk), missing, dt)
    log.info("AUDIT DONE: wrote %d new verdicts to %s", written, ckpt)


def _load_verdicts(language: str) -> dict[int, dict]:
    # Read every shard's checkpoint plus the single-process file.
    files = sorted(_data_dir().glob(f"lemma_audit_{language}*.jsonl"))
    out: dict[int, dict] = {}
    if not files:
        log.error("No checkpoint matching lemma_audit_%s*.jsonl — run `audit` first", language)
        sys.exit(1)
    for f in files:
        for line in f.open():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                out[rec["lemma_id"]] = rec   # last write wins
            except Exception:
                pass
    log.info("Read verdicts from %d checkpoint file(s)", len(files))
    return out


def cmd_apply(args) -> None:
    verdicts = _load_verdicts(args.language)
    log.info("Loaded %d verdicts; status distribution: %s",
             len(verdicts), dict(Counter(v["status"] for v in verdicts.values())))

    actions = Counter()
    samples: list[str] = []
    db = database.SessionLocal()
    try:
        for lemma_id, v in verdicts.items():
            status = v["status"]
            citation = v.get("citation", "")
            pos = v.get("pos") or None
            if pos and pos not in VALID_POS:
                pos = None
            gloss = v.get("gloss") or None
            word_category = None
            if status == "proper_name":
                word_category = "proper_name"
                pos = pos or "proper_noun"
            elif status == "function_word":
                word_category = "function_word"
            elif status == "ok":
                # confirm form; only fill metadata (pos/gloss) — pass current form
                lemma = db.get(Lemma, lemma_id)
                if lemma is None:
                    actions["skip_missing"] += 1
                    continue
                citation = lemma.lemma_form

            res = apply_citation_fix(
                db, lemma_id, citation, pos=pos, gloss=gloss,
                word_category=word_category,
            )
            actions[res.action] += 1
            if res.action in ("rename", "merge") and len(samples) < 30:
                tgt = f" -> #{res.target_id}" if res.target_id else ""
                samples.append(f"  [{res.action}] {res.old_form} → {res.new_form}{tgt}")

        if args.apply:
            db.commit()
            log.info("COMMITTED.")
            try:
                from app.services.activity_log import log_activity
                log_activity(
                    db, "manual_action",
                    f"Lemma citation-form cleanup ({args.language}): "
                    f"{actions.get('rename',0)} renamed, {actions.get('merge',0)} merged",
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
        log.info("Sample changes:\n%s", "\n".join(samples))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("audit", help="LLM-judge every lemma (read-only)")
    pa.add_argument("--language", default="el")
    pa.add_argument("--shard", type=int, default=0)
    pa.add_argument("--num-shards", type=int, default=1)
    pa.set_defaults(func=cmd_audit)

    pp = sub.add_parser("apply", help="Repair DB from checkpoint")
    pp.add_argument("--language", default="el")
    pp.add_argument("--apply", action="store_true", help="commit (default: dry run)")
    pp.set_defaults(func=cmd_apply)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
