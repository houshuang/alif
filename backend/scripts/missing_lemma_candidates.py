#!/usr/bin/env python3
"""Rank lemmas the LLM keeps proposing as corrections but aren't in vocab.

Reads mapping_corrections_*.jsonl logs (written by apply_corrections) and
aggregates the per-position failure_reasons. Every "same_lemma" failure
means the verifier flagged a mapping as wrong and proposed a correct_lemma_ar
that resolved to the same lemma_id already assigned — i.e. the LLM wants a
different meaning of a homograph, or a lemma we don't have.

Top candidates are strong signals for vocabulary expansion. After review,
add them via scripts/import_scaffold_lemmas.py (edit SCAFFOLD_WORDS).

Usage:
    python scripts/missing_lemma_candidates.py           # last 14 days
    python scripts/missing_lemma_candidates.py --days 30 # wider window
    python scripts/missing_lemma_candidates.py --reason any  # include not_found
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.services.sentence_validator import strip_diacritics, normalize_alef


def _norm(text: str) -> str:
    return normalize_alef(strip_diacritics(text or "")).strip()


def collect(days: int, reason_filter: str) -> dict:
    cutoff = datetime.now() - timedelta(days=days)
    candidates: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"count": 0, "glosses": defaultdict(int), "forms": defaultdict(int),
                 "examples": [], "reasons": defaultdict(int)}
    )

    log_dir = settings.log_dir
    files = sorted(log_dir.glob("mapping_corrections_*.jsonl"))
    for fp in files:
        try:
            date_str = fp.stem.split("_")[-1]
            file_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if file_date < cutoff:
            continue

        with open(fp) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("success"):
                    continue
                reasons = entry.get("failure_reasons", {}) or {}
                # JSON keys are strings even though positions are ints
                pos_to_reason = {int(k): v for k, v in reasons.items()}
                for corr in entry.get("corrections", []):
                    pos = corr.get("position")
                    if not isinstance(pos, int):
                        continue
                    reason = pos_to_reason.get(pos)
                    if not reason:
                        continue
                    if reason_filter != "any" and reason != reason_filter:
                        continue
                    ar = corr.get("correct_lemma_ar", "") or ""
                    bare = _norm(ar)
                    if not bare or len(bare) <= 1:
                        continue
                    pos_tag = (corr.get("correct_pos", "") or "").strip().lower()
                    key = (bare, pos_tag)
                    rec = candidates[key]
                    rec["count"] += 1
                    rec["reasons"][reason] += 1
                    rec["glosses"][corr.get("correct_gloss", "") or ""] += 1
                    rec["forms"][ar] += 1
                    if len(rec["examples"]) < 3:
                        preview = entry.get("sentence_preview", "")
                        if preview and preview not in rec["examples"]:
                            rec["examples"].append(preview)
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--reason", choices=("same_lemma", "not_found", "any"),
                        default="same_lemma",
                        help="which failure mode to include (default: same_lemma)")
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument("--min-count", type=int, default=2,
                        help="hide candidates below this count (default: 2)")
    args = parser.parse_args()

    candidates = collect(args.days, args.reason)
    ranked = sorted(
        ((k, v) for k, v in candidates.items() if v["count"] >= args.min_count),
        key=lambda x: -x[1]["count"],
    )[: args.top]

    if not ranked:
        print(f"No candidates in last {args.days} days (reason={args.reason}).")
        return

    print(f"Top {len(ranked)} missing-lemma candidates (last {args.days} days, "
          f"reason={args.reason}, min_count={args.min_count}):\n")
    print(f"{'#':>5}  {'bare':<20} {'pos':<8} {'count':>6}  top gloss / example form")
    print("-" * 100)
    for (bare, pos), rec in ranked:
        top_gloss = max(rec["glosses"].items(), key=lambda x: x[1])[0] if rec["glosses"] else ""
        top_form = max(rec["forms"].items(), key=lambda x: x[1])[0] if rec["forms"] else bare
        reasons_str = ",".join(f"{r}={c}" for r, c in rec["reasons"].items())
        print(f"{rec['count']:>5}  {bare:<20} {pos:<8} [{reasons_str}]")
        print(f"       form: {top_form}   gloss: {top_gloss}")
        if rec["examples"]:
            print(f"       e.g.: {rec['examples'][0]}")
        print()


if __name__ == "__main__":
    main()
