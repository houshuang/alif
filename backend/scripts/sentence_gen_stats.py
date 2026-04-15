"""Aggregate sentence-generation JSONL logs to report rejection rates and reasons.

Reads data/logs/sentence_gen_YYYY-MM-DD.jsonl (written by sentence_generator.py)
and summarizes:
  - generation attempts: total, valid rate, top validation issues
  - quality reviews: total, approved rate, naturalness-fail rate, top rejection reasons

Usage:
  python3 scripts/sentence_gen_stats.py              # last 7 days
  python3 scripts/sentence_gen_stats.py --days 30    # last 30 days
  python3 scripts/sentence_gen_stats.py --date 2026-04-15  # one day
"""

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

# Allow running as a script from the backend dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings


def iter_entries(log_dir: Path, dates: list[date]):
    for d in dates:
        log_file = log_dir / f"sentence_gen_{d:%Y-%m-%d}.jsonl"
        if not log_file.exists():
            continue
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def summarize(entries):
    gen_total = 0
    gen_valid = 0
    gen_issues: Counter[str] = Counter()

    review_total = 0
    review_approved = 0
    review_natural_fail = 0
    review_translation_fail = 0
    review_reasons: Counter[str] = Counter()
    review_by_caller: Counter[str] = Counter()
    review_approved_by_caller: Counter[str] = Counter()

    for e in entries:
        event = e.get("event")
        if event == "sentence_generation":
            gen_total += 1
            if e.get("valid"):
                gen_valid += 1
            for issue in e.get("issues") or []:
                gen_issues[issue[:80]] += 1
        elif event == "quality_review":
            review_total += 1
            caller = e.get("caller", "unknown")
            review_by_caller[caller] += 1
            if e.get("approved"):
                review_approved += 1
                review_approved_by_caller[caller] += 1
            if not e.get("natural", True):
                review_natural_fail += 1
                reason = (e.get("reason") or "").strip()
                if reason:
                    review_reasons[reason[:120]] += 1
            if not e.get("translation_correct", True):
                review_translation_fail += 1

    return {
        "gen_total": gen_total,
        "gen_valid": gen_valid,
        "gen_issues": gen_issues,
        "review_total": review_total,
        "review_approved": review_approved,
        "review_natural_fail": review_natural_fail,
        "review_translation_fail": review_translation_fail,
        "review_reasons": review_reasons,
        "review_by_caller": review_by_caller,
        "review_approved_by_caller": review_approved_by_caller,
    }


def pct(n: int, total: int) -> str:
    return f"{(n / total * 100):.1f}%" if total else "—"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7, help="Days back from today (default 7)")
    ap.add_argument("--date", type=str, help="Single date YYYY-MM-DD")
    ap.add_argument("--log-dir", type=str, default=None, help="Override log dir")
    args = ap.parse_args()

    log_dir = Path(args.log_dir) if args.log_dir else settings.log_dir

    if args.date:
        dates = [datetime.strptime(args.date, "%Y-%m-%d").date()]
        label = args.date
    else:
        today = date.today()
        dates = [today - timedelta(days=i) for i in range(args.days)]
        label = f"last {args.days} days ({dates[-1]} .. {dates[0]})"

    s = summarize(iter_entries(log_dir, dates))

    print(f"=== Sentence Generation Stats — {label} ===")
    print(f"Log dir: {log_dir}")
    print()

    print(f"Generation attempts: {s['gen_total']}")
    if s["gen_total"]:
        print(f"  Valid (passed deterministic validation): {s['gen_valid']} ({pct(s['gen_valid'], s['gen_total'])})")
        if s["gen_issues"]:
            print(f"  Top validation issues:")
            for issue, n in s["gen_issues"].most_common(8):
                print(f"    {n:4d}  {issue}")
    print()

    print(f"Quality reviews: {s['review_total']}")
    if s["review_total"]:
        print(f"  Approved (natural AND translation correct): {s['review_approved']} ({pct(s['review_approved'], s['review_total'])})")
        print(f"  Rejected for naturalness:  {s['review_natural_fail']} ({pct(s['review_natural_fail'], s['review_total'])})")
        print(f"  Rejected for translation:  {s['review_translation_fail']} ({pct(s['review_translation_fail'], s['review_total'])})")
        print()
        print(f"  By caller:")
        for caller, n in s["review_by_caller"].most_common():
            approved = s["review_approved_by_caller"][caller]
            print(f"    {caller:14s}  {n} reviews, {approved} approved ({pct(approved, n)})")
        if s["review_reasons"]:
            print()
            print(f"  Top naturalness-rejection reasons:")
            for reason, n in s["review_reasons"].most_common(10):
                print(f"    {n:4d}  {reason}")


if __name__ == "__main__":
    main()
