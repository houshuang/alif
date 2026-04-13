"""Counterfactual replay: feed actual rating sequences through both default and optimized
FSRS schedulers and compare outcomes.

Answers:
  1. What stability would each card have ended at under each scheduler?
  2. After each lapse, what intervals would each scheduler have recommended?
  3. What's the aggregate distribution of scheduler-intended intervals?
  4. How much calendar time would recovery take after a lapse under each scheduler?
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone
from statistics import mean, median

import argparse
from fsrs import Scheduler, Card, Rating, State


DEFAULT_W = (0.2172, 1.1771, 3.2602, 16.1507, 7.0114, 0.57, 2.0966, 0.0069,
             1.5261, 0.112, 1.0178, 1.849, 0.1133, 0.3127, 2.2934, 0.2191,
             3.0004, 0.7536, 0.3332, 0.1437, 0.2)

# Weights produced by optimize_fsrs.py on 21,363-review dataset (2026-02-08..04-12).
# Kept here for reproducibility of the replay comparison. Not deployed — see
# research/experiment-log.md entry 2026-04-13 for why.
OPT_W = (0.4561, 0.8986, 3.3274, 8.2956, 6.7088, 0.6613, 2.3324, 0.1555,
         1.8420, 0.3975, 0.7796, 1.2168, 0.2500, 0.1249, 1.2062, 0.4289,
         1.8729, 0.5514, 0.6694, 0.5113, 0.1259)

RATING_MAP = {1: Rating.Again, 2: Rating.Hard, 3: Rating.Good, 4: Rating.Easy}


def load_histories(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    rows = cur.execute("""
        SELECT lemma_id, rating, reviewed_at
        FROM review_log
        WHERE is_acquisition=0 OR is_acquisition IS NULL
        ORDER BY lemma_id, reviewed_at
    """).fetchall()
    conn.close()
    histories: dict[int, list[tuple[Rating, datetime]]] = defaultdict(list)
    for lid, r, ts in rows:
        if r not in RATING_MAP:
            continue
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        histories[lid].append((RATING_MAP[r], dt))
    return histories


def replay(history, weights, desired_retention=0.9):
    """Replay a single card's history through a scheduler.
    Returns list of (rating, timestamp, state_after, stability_after, intended_next_due).
    Uses ACTUAL review timestamps (not scheduler-chosen ones)."""
    sched = Scheduler(parameters=weights, desired_retention=desired_retention)
    card = Card()
    out = []
    for rating, ts in history:
        # Use ACTUAL timestamp, regardless of what scheduler wanted
        card, _ = sched.review_card(card, rating, ts)
        out.append({
            "rating": rating,
            "ts": ts,
            "state": card.state,
            "stability": card.stability,
            "difficulty": card.difficulty,
            "intended_due": card.due.replace(tzinfo=timezone.utc) if card.due else None,
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/tmp/claude/alif_fresh.db",
                    help="Path to SQLite database (default: /tmp/claude/alif_fresh.db)")
    args = ap.parse_args()
    histories = load_histories(args.db)
    print(f"Loaded {len(histories)} card histories, "
          f"total {sum(len(h) for h in histories.values())} reviews")

    default_results = {}
    opt_results = {}
    print("Replaying under DEFAULT weights (ret=0.90)…")
    for lid, h in histories.items():
        default_results[lid] = replay(h, DEFAULT_W, 0.90)
    print("Replaying under OPTIMIZED weights (ret=0.90)…")
    for lid, h in histories.items():
        opt_results[lid] = replay(h, OPT_W, 0.90)

    # ===== Analysis 1: End-of-history stability distribution =====
    print("\n=== End-of-history stability distribution ===")
    def_final = [r[-1]["stability"] for r in default_results.values() if r]
    opt_final = [r[-1]["stability"] for r in opt_results.values() if r]

    def pct(vals, p):
        v = sorted(vals)
        return v[int(len(v) * p / 100)]

    print(f"{'metric':<25}{'default':>12}{'optimized':>12}")
    print("-" * 50)
    print(f"{'mean stability':<25}{mean(def_final):>12.2f}{mean(opt_final):>12.2f}")
    print(f"{'median':<25}{pct(def_final, 50):>12.2f}{pct(opt_final, 50):>12.2f}")
    print(f"{'p25':<25}{pct(def_final, 25):>12.2f}{pct(opt_final, 25):>12.2f}")
    print(f"{'p75':<25}{pct(def_final, 75):>12.2f}{pct(opt_final, 75):>12.2f}")
    print(f"{'p90':<25}{pct(def_final, 90):>12.2f}{pct(opt_final, 90):>12.2f}")

    # ===== Analysis 2: Post-lapse recovery — intervals recommended =====
    print("\n=== Post-lapse recovery analysis ===")
    print("For each lapse in history, compare: if the user had followed the scheduler's")
    print("recommendation, how long to recover to pre-lapse stability?")
    print()

    def lapse_recovery(results, history):
        """For each lapse event, track: pre-S, recommended post-lapse interval,
        stability trajectory in the actual next reviews."""
        out = []
        for i, step in enumerate(results):
            if step["rating"] not in (Rating.Again, Rating.Hard):
                continue
            if i == 0:
                continue  # first review can't be a "lapse"
            pre_step = results[i - 1]
            pre_stab = pre_step["stability"]
            if pre_stab < 7:  # only look at lapses of well-learned cards
                continue
            post_stab = step["stability"]
            # Intended next interval
            intended = (step["intended_due"] - step["ts"]).total_seconds() / 86400
            # How many subsequent reviews to reach pre-lapse stability again?
            reviews_to_recover = None
            days_to_recover = None
            for j in range(i + 1, len(results)):
                if results[j]["stability"] >= pre_stab:
                    reviews_to_recover = j - i
                    days_to_recover = (results[j]["ts"] - step["ts"]).total_seconds() / 86400
                    break
            out.append({
                "pre_stab": pre_stab,
                "post_stab": post_stab,
                "intended_interval_days": intended,
                "reviews_to_recover": reviews_to_recover,
                "days_to_recover": days_to_recover,
            })
        return out

    lapse_stats_def = []
    lapse_stats_opt = []
    for lid in histories:
        lapse_stats_def.extend(lapse_recovery(default_results[lid], histories[lid]))
        lapse_stats_opt.extend(lapse_recovery(opt_results[lid], histories[lid]))

    print(f"Lapses of S>=7d cards under DEFAULT:  {len(lapse_stats_def)}")
    print(f"Lapses of S>=7d cards under OPTIMIZED: {len(lapse_stats_opt)}")
    print()

    def summarize(stats, label):
        print(f"--- {label} ---")
        print(f"  Mean pre-lapse stability:       {mean(s['pre_stab'] for s in stats):6.2f}d")
        print(f"  Mean post-lapse stability:      {mean(s['post_stab'] for s in stats):6.2f}d")
        print(f"  Mean intended next interval:    {mean(s['intended_interval_days'] for s in stats):6.2f}d")
        print(f"  Median intended next interval:  {median(s['intended_interval_days'] for s in stats):6.2f}d")
        recov = [s for s in stats if s['reviews_to_recover'] is not None]
        print(f"  Cards that recovered in dataset: {len(recov)}/{len(stats)}")
        if recov:
            print(f"  Mean reviews to recover:        {mean(s['reviews_to_recover'] for s in recov):6.2f}")
            print(f"  Mean days to recover:           {mean(s['days_to_recover'] for s in recov):6.2f}")
        print()

    summarize(lapse_stats_def, "DEFAULT weights")
    summarize(lapse_stats_opt, "OPTIMIZED weights")

    # ===== Analysis 3: Aggregate interval distribution =====
    print("=== Scheduler-intended interval distribution (all reviews) ===")
    def get_intervals(results_dict):
        intervals = []
        for lid, steps in results_dict.items():
            for s in steps:
                if s["intended_due"] is None:
                    continue
                interval = (s["intended_due"] - s["ts"]).total_seconds() / 86400
                intervals.append(interval)
        return intervals

    def_intervals = get_intervals(default_results)
    opt_intervals = get_intervals(opt_results)

    print(f"{'bucket':<15}{'default':>10}{'optimized':>12}")
    print("-" * 37)
    buckets = [
        ("< 6h", 0, 0.25),
        ("6h-1d", 0.25, 1),
        ("1-3d", 1, 3),
        ("3-7d", 3, 7),
        ("7-30d", 7, 30),
        ("30-90d", 30, 90),
        (">=90d", 90, 999999),
    ]
    for label, lo, hi in buckets:
        dc = sum(1 for x in def_intervals if lo <= x < hi)
        oc = sum(1 for x in opt_intervals if lo <= x < hi)
        print(f"{label:<15}{dc:>10}{oc:>12}")
    print(f"{'TOTAL':<15}{len(def_intervals):>10}{len(opt_intervals):>12}")

    # ===== Analysis 4: "Would have been due" over past 30 days =====
    print("\n=== Counterfactual due queue over past 30 days ===")
    print("At end of each day, how many cards did each scheduler think should be due?")

    # Collect all (card_id, last_review_ts, intended_due) from each scheduler
    def get_due_timeline(results_dict, start, end):
        """For each day, count cards whose intended due date falls at or before that day
        AND whose last review is before that day (i.e., currently due)."""
        day_counts = []
        cur_day = start
        # For each card, pick its last replay step; simulate the due evolution
        while cur_day <= end:
            cur_due = 0
            for lid, steps in results_dict.items():
                # Find latest step before cur_day
                latest = None
                for s in steps:
                    if s["ts"] <= cur_day:
                        latest = s
                    else:
                        break
                if latest and latest["intended_due"] and latest["intended_due"] <= cur_day:
                    cur_due += 1
            day_counts.append((cur_day, cur_due))
            cur_day += timedelta(days=1)
        return day_counts

    end_date = datetime(2026, 4, 13, 0, 0, tzinfo=timezone.utc)
    start_date = end_date - timedelta(days=30)
    def_timeline = get_due_timeline(default_results, start_date, end_date)
    opt_timeline = get_due_timeline(opt_results, start_date, end_date)

    print(f"{'date':<12}{'default due':>12}{'optimized due':>14}")
    print("-" * 38)
    for (d1, c1), (d2, c2) in zip(def_timeline, opt_timeline):
        print(f"{d1.strftime('%Y-%m-%d'):<12}{c1:>12}{c2:>14}")

    # ===== Analysis 5: What fraction of time do schedulers produce intervals
    #       within 1.5x of the actual next-review gap? =====
    print("\n=== Alignment with actual user behavior ===")
    print("How often does each scheduler's suggested interval match what the user actually did?")

    def alignment(results, hist):
        matches_tight = 0  # within 0.67x - 1.5x
        matches_loose = 0  # within 0.33x - 3x
        total = 0
        over_scheduled = 0  # scheduler said sooner than user came back
        under_scheduled = 0  # scheduler said later than user came back
        for i, step in enumerate(results):
            if i + 1 >= len(results):
                continue
            intended = (step["intended_due"] - step["ts"]).total_seconds() / 86400
            actual = (results[i + 1]["ts"] - step["ts"]).total_seconds() / 86400
            if intended <= 0 or actual <= 0:
                continue
            ratio = actual / intended
            total += 1
            if 0.67 <= ratio <= 1.5:
                matches_tight += 1
            if 0.33 <= ratio <= 3:
                matches_loose += 1
            if ratio > 1.5:
                under_scheduled += 1  # user came back later — scheduler wanted sooner
            if ratio < 0.67:
                over_scheduled += 1  # user came back sooner — scheduler wanted later
        return {
            "total": total,
            "tight_match_pct": matches_tight / total * 100 if total else 0,
            "loose_match_pct": matches_loose / total * 100 if total else 0,
            "over_scheduled_pct": over_scheduled / total * 100 if total else 0,
            "under_scheduled_pct": under_scheduled / total * 100 if total else 0,
        }

    def_align = {"total": 0, "tight_match_pct": 0, "loose_match_pct": 0,
                 "over_scheduled_pct": 0, "under_scheduled_pct": 0}
    opt_align = {"total": 0, "tight_match_pct": 0, "loose_match_pct": 0,
                 "over_scheduled_pct": 0, "under_scheduled_pct": 0}
    # accumulate
    for lid in histories:
        d = alignment(default_results[lid], histories[lid])
        o = alignment(opt_results[lid], histories[lid])
        for k in def_align:
            if k == "total":
                def_align[k] += d[k]
                opt_align[k] += o[k]
            else:
                def_align[k] += d[k] * d["total"]
                opt_align[k] += o[k] * o["total"]
    # normalize
    for k in def_align:
        if k != "total":
            def_align[k] /= def_align["total"] or 1
            opt_align[k] /= opt_align["total"] or 1

    print(f"{'metric':<30}{'default':>12}{'optimized':>12}")
    print("-" * 54)
    print(f"{'tight match (0.67-1.5x)':<30}{def_align['tight_match_pct']:>11.1f}%{opt_align['tight_match_pct']:>11.1f}%")
    print(f"{'loose match (0.33-3x)':<30}{def_align['loose_match_pct']:>11.1f}%{opt_align['loose_match_pct']:>11.1f}%")
    print(f"{'scheduler wanted later':<30}{def_align['under_scheduled_pct']:>11.1f}%{opt_align['under_scheduled_pct']:>11.1f}%")
    print(f"{'scheduler wanted sooner':<30}{def_align['over_scheduled_pct']:>11.1f}%{opt_align['over_scheduled_pct']:>11.1f}%")
    print(f"total evaluated: {def_align['total']}")


if __name__ == "__main__":
    main()
