#!/usr/bin/env python3
"""LLM audit + repair of every Lemma row's citation form.

simplemma routinely stored inflected surface forms as lemmas (εξελίχθηκαν for
εξελίσσομαι, πλεονάσματος for πλεόνασμα). This script uses the configured LLM
as a stronger Greek lemmatizer than dictionary libraries to judge every lemma
and rewrite the database so each row is a correct dictionary citation form.

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
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import database
from app.models import Lemma
from app.services.lemma_integrity import (
    _apply_verdict_to_lemma, judge_lemmas, VALID_STATUS,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("audit_lemmas")

MODEL = os.environ.get("POLYGLOT_AUDIT_MODEL", "claude-sonnet-4-5-20250929")
BATCH_SIZE = int(os.environ.get("POLYGLOT_AUDIT_BATCH", "40"))


def _data_dir() -> Path:
    d = Path(__file__).resolve().parents[1] / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _checkpoint_path(language: str, shard: int = 0, num_shards: int = 1) -> Path:
    # Per-shard files so parallel audit processes never interleave JSONL lines.
    if num_shards > 1:
        return _data_dir() / f"lemma_audit_{language}_s{shard}of{num_shards}.jsonl"
    return _data_dir() / f"lemma_audit_{language}.jsonl"


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
            by_index = judge_lemmas(args.language, items, model=MODEL)
            dt = time.time() - t0
            if by_index is None:
                log.warning("batch @%d failed (%.0fs) — will retry on resume", start, dt)
                continue
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
            res = _apply_verdict_to_lemma(db, lemma_id, v)
            actions[res.action] += 1
            if res.action in ("rename", "merge") and len(samples) < 30:
                tgt = f" -> #{res.target_id}" if res.target_id else ""
                samples.append(f"  [{res.action}] {res.old_form} → {res.new_form}{tgt}")
            elif res.action == "retire" and len(samples) < 30:
                samples.append(f"  [retire] {res.old_form}")

        if args.apply:
            db.commit()
            log.info("COMMITTED.")
            try:
                from app.services.activity_log import log_activity
                log_activity(
                    db,
                    event_type="manual_action",
                    summary=(
                        f"Lemma citation-form cleanup ({args.language}): "
                        f"{actions.get('rename',0)} renamed, {actions.get('merge',0)} merged"
                    ),
                    detail=dict(actions),
                    language_code=args.language,
                )
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
