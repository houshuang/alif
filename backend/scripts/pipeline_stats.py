"""Aggregate generation_pipeline_*.jsonl events.

Reports daily success rate of the self-correct batch path (default since
2026-04-20), legacy-path counts, and the dominant validation-failure shapes.

Without this report we have no observable success rate for the path that
handles ~80% of generation. Companion to ``sentence_gen_stats.py`` which
covers the older ``sentence_gen_*.jsonl`` schema.

Usage:
    python scripts/pipeline_stats.py                # last 7 days
    python scripts/pipeline_stats.py --days 21
    python scripts/pipeline_stats.py --date 2026-05-03
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings


def _open(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt")
    return open(path, "r")


def iter_pipeline_entries(log_dir: Path, dates: list[date]):
    for d in dates:
        for suffix in ("", ".gz"):
            path = log_dir / f"generation_pipeline_{d.isoformat()}.jsonl{suffix}"
            if path.exists():
                with _open(path) as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            yield d, json.loads(line)
                        except json.JSONDecodeError:
                            continue
                break


def _per_day(entries):
    by_day: dict[date, Counter] = defaultdict(Counter)
    self_correct_returned: dict[date, list[dict]] = defaultdict(list)
    multi_target_summaries: list[dict] = []
    multi_target_accepted: list[dict] = []
    issues: Counter[str] = Counter()
    empty_groups: list[dict] = []

    for d, e in entries:
        ev = e.get("event") or "unknown"
        by_day[d][ev] += 1

        if ev == "batch_self_correct_returned":
            self_correct_returned[d].append(e)
        elif ev == "batch_self_correct_empty":
            empty_groups.append({"date": d.isoformat(), **e})
        elif ev == "multi_target_summary":
            multi_target_summaries.append(e)
        elif ev == "multi_target_accepted":
            multi_target_accepted.append(e)
        elif ev == "batch_validation_failed":
            for iss in e.get("issues") or []:
                issues[iss[:90]] += 1

    return (
        by_day,
        self_correct_returned,
        multi_target_summaries,
        multi_target_accepted,
        issues,
        empty_groups,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--date", type=str, help="Single date YYYY-MM-DD")
    ap.add_argument("--log-dir", type=str, default=None)
    args = ap.parse_args()

    log_dir = Path(args.log_dir) if args.log_dir else settings.log_dir

    if args.date:
        dates = [datetime.strptime(args.date, "%Y-%m-%d").date()]
        label = args.date
    else:
        today = date.today()
        dates = [today - timedelta(days=i) for i in range(args.days)]
        label = f"last {args.days} days ({dates[-1]} .. {dates[0]})"

    (
        by_day,
        self_correct_returned,
        multi_target_summaries,
        multi_target_accepted,
        issues,
        empty_groups,
    ) = _per_day(iter_pipeline_entries(log_dir, dates))

    print(f"=== Generation Pipeline Stats — {label} ===")
    print(f"Log dir: {log_dir}")
    print()

    if not by_day:
        print("(no entries)")
        return

    # ── Daily breakdown ──
    # mt_grp = number of multi_target_summary events (one per group call).
    # mt_ret = sum of sentences_returned across summaries (sentences from LLM).
    # mt_acc = sum of accepted across summaries (post-quality-review survivors).
    mt_by_day: dict[date, tuple[int, int, int]] = defaultdict(lambda: (0, 0, 0))
    for s in multi_target_summaries:
        # Find the date by parsing ts; fall back to no-op if absent
        ts = s.get("ts", "")
        try:
            d = datetime.fromisoformat(ts).date() if ts else None
        except ValueError:
            d = None
        if d is None:
            continue
        g, r, a = mt_by_day[d]
        mt_by_day[d] = (
            g + 1,
            r + (s.get("sentences_returned") or 0),
            a + (s.get("accepted") or 0),
        )

    print("Self-correct (single + batch fallback) | Multi-target (Phase 1) | shared")
    print(f"{'date':<12} {'sc_ret':>6} {'sc_acc':>6} {'sc_emp':>6} | "
          f"{'mt_grp':>6} {'mt_ret':>6} {'mt_acc':>6} {'mt_fail':>7} | "
          f"{'val_fail':>8}")
    sc_ret_sum = sc_acc_sum = sc_emp_sum = 0
    mt_ret_sum = mt_acc_sum = mt_grp_sum = 0
    for d in sorted(by_day.keys()):
        c = by_day[d]
        sc_ret = c.get("batch_self_correct_returned", 0)
        sc_acc = c.get("batch_self_correct_accepted", 0)
        sc_emp = c.get("batch_self_correct_empty", 0)
        mt_grp, mt_ret, mt_acc = mt_by_day.get(d, (0, 0, 0))
        mt_fail = c.get("multi_target_failed", 0)
        val_fail = c.get("batch_validation_failed", 0)
        sc_ret_sum += sc_ret
        sc_acc_sum += sc_acc
        sc_emp_sum += sc_emp
        mt_ret_sum += mt_ret
        mt_acc_sum += mt_acc
        mt_grp_sum += mt_grp
        print(f"{d.isoformat():<12} {sc_ret:>6} {sc_acc:>6} {sc_emp:>6} | "
              f"{mt_grp:>6} {mt_ret:>6} {mt_acc:>6} {mt_fail:>7} | "
              f"{val_fail:>8}")

    # ── Self-correct effectiveness ──
    print()
    print("Self-correct path (single-word + batch fallback):")
    print(f"  groups returned non-empty: {sc_ret_sum}")
    print(f"  empty-response failures:   {sc_emp_sum}"
          + (f"  ({100*sc_emp_sum/(sc_ret_sum+sc_emp_sum):.1f}% of attempted groups)"
             if sc_ret_sum + sc_emp_sum else ""))
    print(f"  sentences accepted (post-Haiku verify): {sc_acc_sum}")

    if self_correct_returned:
        all_groups = [e for ents in self_correct_returned.values() for e in ents]
        per_target = []
        zero_groups = 0
        for g in all_groups:
            counts = (g.get("per_target_counts") or {}).values()
            if not counts:
                continue
            per_target.extend(counts)
            if sum(counts) == 0:
                zero_groups += 1
        if per_target:
            from statistics import mean
            print(f"  mean sentences/target: {mean(per_target):.2f}"
                  f"  (over {len(per_target)} target slots)")
            print(f"  groups returning zero sentences: {zero_groups}/{len(all_groups)}")

    # ── Multi-target effectiveness ──
    print()
    print("Multi-target path (Phase 1 of cron, dominant generation source):")
    print(f"  groups attempted: {mt_grp_sum}")
    print(f"  sentences returned by LLM: {mt_ret_sum}")
    print(f"  sentences accepted (post-quality-review): {mt_acc_sum}"
          + (f"  ({100*mt_acc_sum/mt_ret_sum:.1f}% of returned)" if mt_ret_sum else ""))

    if multi_target_summaries:
        target_slots: set[int] = set()
        for s in multi_target_summaries:
            target_slots.update(s.get("target_lemma_ids") or [])
        covered_targets: set[int] = set()
        for a in multi_target_accepted:
            covered_targets.update(a.get("target_lemma_ids") or [])
        zero_groups = sum(
            1 for s in multi_target_summaries
            if not (s.get("accepted") or 0)
        )
        if target_slots:
            print(f"  distinct target lemmas: {len(target_slots)}")
            print(f"  targets covered ≥1 accepted sentence: {len(covered_targets)}"
                  f"  ({100*len(covered_targets)/len(target_slots):.1f}%)")
            print(f"  groups with zero accepted sentences: {zero_groups}/{len(multi_target_summaries)}")

    # ── Empty-response details ──
    if empty_groups:
        print()
        print(f"Empty-response failures (most recent 10 of {len(empty_groups)}):")
        for g in empty_groups[-10:]:
            ids = g.get("target_lemma_ids") or []
            print(f"  {g['date']}  group_size={g.get('group_size')}"
                  f"  elapsed={g.get('elapsed_s')}s  ids={ids[:5]}"
                  f"{'...' if len(ids) > 5 else ''}")

    # ── Top validation issues (legacy + self-correct shared event) ──
    if issues:
        print()
        print("Top batch_validation_failed issues:")
        for issue, count in issues.most_common(10):
            print(f"  {count:>4}  {issue}")


if __name__ == "__main__":
    main()
