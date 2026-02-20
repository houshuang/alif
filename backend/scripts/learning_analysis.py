#!/usr/bin/env python3
"""
Comprehensive learning analysis script for Alif.

Reads raw SQLite database and produces:
  - JSON output on stdout (machine-parseable)
  - Human-readable summary on stderr (with bar charts and aligned columns)

Usage:
    python3 scripts/learning_analysis.py --db /app/data/alif.db
    python3 scripts/learning_analysis.py --db /app/data/alif.db 2>/dev/null  # JSON only
    python3 scripts/learning_analysis.py --db /app/data/alif.db 2>&1 1>/dev/null  # console only
"""

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import mean, median


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_dt(s):
    """Parse naive datetime string from SQLite. Returns None on failure."""
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def bar(count, total, width=30):
    """Render a simple bar chart string."""
    if total == 0:
        return "[" + " " * width + "]"
    filled = int(round(count / total * width))
    return "[" + "#" * filled + " " * (width - filled) + "]"


def pct(count, total):
    """Return percentage string."""
    if total == 0:
        return "0.0%"
    return f"{count / total * 100:.1f}%"


def iso_week(dt):
    """Return ISO year-week string like '2026-W07'."""
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def iso_date(dt):
    """Return ISO date string."""
    return dt.strftime("%Y-%m-%d")


def eprint(*args, **kwargs):
    """Print to stderr."""
    print(*args, file=sys.stderr, **kwargs)


def section(title):
    """Print a section header to stderr."""
    eprint()
    eprint("=" * 70)
    eprint(f"  {title}")
    eprint("=" * 70)


def subsection(title):
    """Print a subsection header to stderr."""
    eprint()
    eprint(f"  --- {title} ---")


def table_row(label, value, extra=""):
    """Print an aligned table row to stderr."""
    eprint(f"  {label:<40s} {str(value):>8s}  {extra}")


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------

def analyze_vocabulary_by_status(conn):
    """1. Vocabulary by knowledge_state and source."""
    cur = conn.cursor()

    # Overall counts per state
    cur.execute("SELECT knowledge_state, COUNT(*) FROM user_lemma_knowledge GROUP BY knowledge_state ORDER BY COUNT(*) DESC")
    state_counts = dict(cur.fetchall())

    # Breakdown by source per state
    cur.execute("""
        SELECT knowledge_state, source, COUNT(*)
        FROM user_lemma_knowledge
        GROUP BY knowledge_state, source
        ORDER BY knowledge_state, COUNT(*) DESC
    """)
    by_source = defaultdict(dict)
    for state, source, cnt in cur.fetchall():
        by_source[state][source or "unknown"] = cnt

    total = sum(state_counts.values())

    # Console
    section("1. VOCABULARY BY STATUS")
    for state in ["known", "learning", "acquiring", "encountered", "new", "lapsed", "suspended"]:
        cnt = state_counts.get(state, 0)
        table_row(state, str(cnt), f"{bar(cnt, total)}  {pct(cnt, total)}")

    table_row("TOTAL", str(total))

    subsection("Breakdown by source")
    for state, sources in sorted(by_source.items()):
        eprint(f"  {state}:")
        for src, cnt in sorted(sources.items(), key=lambda x: -x[1]):
            eprint(f"    {src:<30s} {cnt:>6d}")

    return {
        "state_counts": state_counts,
        "by_source": dict(by_source),
        "total": total,
    }


def analyze_acquisition_rate(conn):
    """2. Acquisition rate: daily/weekly graduations, time in acquisition."""
    cur = conn.cursor()

    # Graduations by date
    cur.execute("""
        SELECT graduated_at FROM user_lemma_knowledge
        WHERE graduated_at IS NOT NULL
    """)
    grad_dates = []
    for (ga,) in cur.fetchall():
        dt = parse_dt(ga)
        if dt:
            grad_dates.append(dt)

    daily_grads = Counter(iso_date(d) for d in grad_dates)
    weekly_grads = Counter(iso_week(d) for d in grad_dates)

    # Time in acquisition
    cur.execute("""
        SELECT entered_acquiring_at, graduated_at FROM user_lemma_knowledge
        WHERE entered_acquiring_at IS NOT NULL AND graduated_at IS NOT NULL
    """)
    durations_hours = []
    for ea, ga in cur.fetchall():
        ea_dt = parse_dt(ea)
        ga_dt = parse_dt(ga)
        if ea_dt and ga_dt:
            delta = (ga_dt - ea_dt).total_seconds() / 3600
            if delta >= 0:
                durations_hours.append(delta)

    med_hours = median(durations_hours) if durations_hours else 0
    avg_hours = mean(durations_hours) if durations_hours else 0

    # Console
    section("2. ACQUISITION RATE")
    table_row("Total graduations", str(len(grad_dates)))
    table_row("Median time in acquisition", f"{med_hours:.1f}h")
    table_row("Mean time in acquisition", f"{avg_hours:.1f}h")

    if weekly_grads:
        subsection("Weekly graduations (last 8 weeks)")
        weeks_sorted = sorted(weekly_grads.keys())[-8:]
        max_wg = max(weekly_grads[w] for w in weeks_sorted) if weeks_sorted else 1
        for w in weeks_sorted:
            cnt = weekly_grads[w]
            eprint(f"  {w}  {cnt:>4d}  {bar(cnt, max_wg)}")

    return {
        "total_graduations": len(grad_dates),
        "daily_graduations": dict(sorted(daily_grads.items())),
        "weekly_graduations": dict(sorted(weekly_grads.items())),
        "median_hours_in_acquisition": round(med_hours, 1),
        "mean_hours_in_acquisition": round(avg_hours, 1),
        "durations_hours_p25_p50_p75": _percentiles(durations_hours),
    }


def analyze_review_accuracy(conn):
    """3. Review accuracy: rating distribution, by mode, by credit_type, weekly, acquisition vs FSRS."""
    cur = conn.cursor()

    # Overall rating distribution
    cur.execute("SELECT rating, COUNT(*) FROM review_log GROUP BY rating ORDER BY rating")
    rating_dist = dict(cur.fetchall())
    total_reviews = sum(rating_dist.values())

    # By mode
    cur.execute("SELECT review_mode, rating, COUNT(*) FROM review_log GROUP BY review_mode, rating")
    by_mode = defaultdict(dict)
    for mode, rating, cnt in cur.fetchall():
        by_mode[mode or "unknown"][rating] = cnt

    # By credit_type
    cur.execute("SELECT credit_type, rating, COUNT(*) FROM review_log GROUP BY credit_type, rating")
    by_credit = defaultdict(dict)
    for ct, rating, cnt in cur.fetchall():
        by_credit[ct or "null"][rating] = cnt

    # By is_acquisition
    cur.execute("SELECT is_acquisition, rating, COUNT(*) FROM review_log GROUP BY is_acquisition, rating")
    by_acq = defaultdict(dict)
    for is_acq, rating, cnt in cur.fetchall():
        label = "acquisition" if is_acq else "fsrs"
        by_acq[label][rating] = cnt

    # Weekly trend: % rating >= 3
    cur.execute("SELECT reviewed_at, rating FROM review_log WHERE reviewed_at IS NOT NULL")
    weekly_acc = defaultdict(lambda: {"correct": 0, "total": 0})
    for ra, rating in cur.fetchall():
        dt = parse_dt(ra)
        if dt:
            w = iso_week(dt)
            weekly_acc[w]["total"] += 1
            if rating >= 3:
                weekly_acc[w]["correct"] += 1

    weekly_accuracy = {w: round(d["correct"] / d["total"] * 100, 1) if d["total"] > 0 else 0
                       for w, d in sorted(weekly_acc.items())}

    # Console
    section("3. REVIEW ACCURACY")
    for r in [1, 2, 3, 4]:
        cnt = rating_dist.get(r, 0)
        table_row(f"Rating {r}", str(cnt), f"{bar(cnt, total_reviews)}  {pct(cnt, total_reviews)}")
    table_row("Total reviews", str(total_reviews))

    correct = sum(rating_dist.get(r, 0) for r in [3, 4])
    table_row("Overall accuracy (>=3)", pct(correct, total_reviews))

    subsection("By mode")
    for mode, ratings in sorted(by_mode.items()):
        mode_total = sum(ratings.values())
        mode_correct = sum(ratings.get(r, 0) for r in [3, 4])
        table_row(f"  {mode}", str(mode_total), f"accuracy: {pct(mode_correct, mode_total)}")

    subsection("By credit type")
    for ct, ratings in sorted(by_credit.items()):
        ct_total = sum(ratings.values())
        ct_correct = sum(ratings.get(r, 0) for r in [3, 4])
        table_row(f"  {ct}", str(ct_total), f"accuracy: {pct(ct_correct, ct_total)}")

    subsection("Acquisition vs FSRS")
    for label, ratings in sorted(by_acq.items()):
        t = sum(ratings.values())
        c = sum(ratings.get(r, 0) for r in [3, 4])
        table_row(f"  {label}", str(t), f"accuracy: {pct(c, t)}")

    subsection("Weekly accuracy trend (last 8 weeks)")
    weeks_sorted = sorted(weekly_accuracy.keys())[-8:]
    for w in weeks_sorted:
        acc = weekly_accuracy[w]
        eprint(f"  {w}  {acc:>5.1f}%  {bar(int(acc), 100)}")

    return {
        "rating_distribution": rating_dist,
        "total_reviews": total_reviews,
        "overall_accuracy_pct": round(correct / total_reviews * 100, 1) if total_reviews else 0,
        "by_mode": dict(by_mode),
        "by_credit_type": dict(by_credit),
        "by_acquisition": dict(by_acq),
        "weekly_accuracy": weekly_accuracy,
    }


def analyze_session_patterns(conn):
    """4. Session patterns: size, frequency, comprehension."""
    cur = conn.cursor()

    # Sentences per session
    cur.execute("""
        SELECT session_id, COUNT(*) as cnt
        FROM sentence_review_log
        WHERE session_id IS NOT NULL
        GROUP BY session_id
    """)
    session_sizes = [cnt for _, cnt in cur.fetchall()]

    # Sessions per day
    cur.execute("""
        SELECT session_id, MIN(reviewed_at) as first_review
        FROM sentence_review_log
        WHERE session_id IS NOT NULL
        GROUP BY session_id
    """)
    sessions_by_day = Counter()
    for _, first_review in cur.fetchall():
        dt = parse_dt(first_review)
        if dt:
            sessions_by_day[iso_date(dt)] += 1

    # Comprehension distribution
    cur.execute("SELECT comprehension, COUNT(*) FROM sentence_review_log GROUP BY comprehension")
    comp_dist = dict(cur.fetchall())

    # Size distribution buckets
    size_buckets = Counter()
    for s in session_sizes:
        if s <= 5:
            size_buckets["1-5"] += 1
        elif s <= 10:
            size_buckets["6-10"] += 1
        elif s <= 15:
            size_buckets["11-15"] += 1
        elif s <= 20:
            size_buckets["16-20"] += 1
        elif s <= 30:
            size_buckets["21-30"] += 1
        else:
            size_buckets["31+"] += 1

    # Console
    section("4. SESSION PATTERNS")
    table_row("Total sessions", str(len(session_sizes)))
    if session_sizes:
        table_row("Median session size", str(int(median(session_sizes))))
        table_row("Mean session size", f"{mean(session_sizes):.1f}")
        table_row("Min / Max session size", f"{min(session_sizes)} / {max(session_sizes)}")

    subsection("Session size distribution")
    total_sess = len(session_sizes)
    for bucket in ["1-5", "6-10", "11-15", "16-20", "21-30", "31+"]:
        cnt = size_buckets.get(bucket, 0)
        table_row(f"  {bucket} sentences", str(cnt), f"{bar(cnt, total_sess)}  {pct(cnt, total_sess)}")

    subsection("Comprehension distribution")
    comp_total = sum(comp_dist.values())
    for comp in ["understood", "partial", "no_idea"]:
        cnt = comp_dist.get(comp, 0)
        table_row(f"  {comp}", str(cnt), f"{bar(cnt, comp_total)}  {pct(cnt, comp_total)}")

    if sessions_by_day:
        spd_values = list(sessions_by_day.values())
        subsection("Sessions per day")
        table_row("Mean sessions/day", f"{mean(spd_values):.1f}")
        table_row("Median sessions/day", str(int(median(spd_values))))
        table_row("Max sessions in a day", str(max(spd_values)))

    return {
        "total_sessions": len(session_sizes),
        "session_size_median": int(median(session_sizes)) if session_sizes else 0,
        "session_size_mean": round(mean(session_sizes), 1) if session_sizes else 0,
        "session_size_buckets": dict(size_buckets),
        "comprehension_distribution": comp_dist,
        "sessions_per_day": dict(sorted(sessions_by_day.items())),
    }


def analyze_fsrs_stability(conn):
    """5. FSRS stability and difficulty distributions from fsrs_card_json."""
    cur = conn.cursor()

    cur.execute("""
        SELECT fsrs_card_json, knowledge_state
        FROM user_lemma_knowledge
        WHERE fsrs_card_json IS NOT NULL
    """)

    stability_values = []
    difficulty_values = []
    stability_by_state = defaultdict(list)

    for card_json_raw, state in cur.fetchall():
        try:
            card = json.loads(card_json_raw) if isinstance(card_json_raw, str) else card_json_raw
        except (json.JSONDecodeError, TypeError):
            continue
        if not card or not isinstance(card, dict):
            continue

        stab = card.get("stability") or card.get("s")
        diff = card.get("difficulty") or card.get("d")

        if stab is not None:
            stab = float(stab)
            stability_values.append(stab)
            stability_by_state[state].append(stab)
        if diff is not None:
            difficulty_values.append(float(diff))

    # Stability buckets
    stab_buckets = Counter()
    for s in stability_values:
        if s < 1:
            stab_buckets["<1d"] += 1
        elif s < 7:
            stab_buckets["1-7d"] += 1
        elif s < 30:
            stab_buckets["7-30d"] += 1
        elif s < 90:
            stab_buckets["30-90d"] += 1
        else:
            stab_buckets["90+d"] += 1

    # Difficulty buckets
    diff_buckets = Counter()
    for d in difficulty_values:
        if d < 3:
            diff_buckets["<3 (easy)"] += 1
        elif d < 5:
            diff_buckets["3-5 (medium)"] += 1
        elif d < 7:
            diff_buckets["5-7 (hard)"] += 1
        else:
            diff_buckets["7+ (very hard)"] += 1

    # Console
    section("5. FSRS STABILITY & DIFFICULTY")
    total_cards = len(stability_values)
    table_row("Cards with FSRS data", str(total_cards))

    if stability_values:
        table_row("Median stability", f"{median(stability_values):.1f}d")
        table_row("Mean stability", f"{mean(stability_values):.1f}d")

    subsection("Stability buckets")
    for bucket in ["<1d", "1-7d", "7-30d", "30-90d", "90+d"]:
        cnt = stab_buckets.get(bucket, 0)
        table_row(f"  {bucket}", str(cnt), f"{bar(cnt, total_cards)}  {pct(cnt, total_cards)}")

    subsection("Stability by knowledge state")
    for state in ["known", "learning", "lapsed"]:
        vals = stability_by_state.get(state, [])
        if vals:
            table_row(f"  {state} (n={len(vals)})", f"median {median(vals):.1f}d", f"mean {mean(vals):.1f}d")

    subsection("Difficulty distribution")
    total_diff = len(difficulty_values)
    for bucket in ["<3 (easy)", "3-5 (medium)", "5-7 (hard)", "7+ (very hard)"]:
        cnt = diff_buckets.get(bucket, 0)
        table_row(f"  {bucket}", str(cnt), f"{bar(cnt, total_diff)}  {pct(cnt, total_diff)}")

    return {
        "total_cards_with_fsrs": total_cards,
        "stability_median": round(median(stability_values), 1) if stability_values else 0,
        "stability_mean": round(mean(stability_values), 1) if stability_values else 0,
        "stability_buckets": dict(stab_buckets),
        "stability_by_state": {
            state: {
                "count": len(vals),
                "median": round(median(vals), 1) if vals else 0,
                "mean": round(mean(vals), 1) if vals else 0,
            }
            for state, vals in stability_by_state.items()
        },
        "difficulty_buckets": dict(diff_buckets),
        "difficulty_median": round(median(difficulty_values), 1) if difficulty_values else 0,
    }


def analyze_time_to_acquisition(conn):
    """6. Time-to-acquisition: duration + reviews-to-graduation."""
    cur = conn.cursor()

    # Duration
    cur.execute("""
        SELECT ulk.lemma_id, ulk.entered_acquiring_at, ulk.graduated_at
        FROM user_lemma_knowledge ulk
        WHERE ulk.entered_acquiring_at IS NOT NULL AND ulk.graduated_at IS NOT NULL
    """)
    durations_hours = []
    graduated_lemma_ids = []
    for lemma_id, ea, ga in cur.fetchall():
        ea_dt = parse_dt(ea)
        ga_dt = parse_dt(ga)
        if ea_dt and ga_dt:
            delta_h = (ga_dt - ea_dt).total_seconds() / 3600
            if delta_h >= 0:
                durations_hours.append(delta_h)
                graduated_lemma_ids.append((lemma_id, ea_dt, ga_dt))

    # Reviews per graduated word
    reviews_per_word = []
    for lemma_id, ea_dt, ga_dt in graduated_lemma_ids:
        ea_str = ea_dt.strftime("%Y-%m-%d %H:%M:%S")
        ga_str = ga_dt.strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("""
            SELECT COUNT(*) FROM review_log
            WHERE lemma_id = ? AND reviewed_at >= ? AND reviewed_at <= ?
        """, (lemma_id, ea_str, ga_str))
        cnt = cur.fetchone()[0]
        reviews_per_word.append(cnt)

    # Console
    section("6. TIME-TO-ACQUISITION")
    table_row("Graduated words analyzed", str(len(durations_hours)))

    if durations_hours:
        p = _percentiles(durations_hours)
        table_row("Duration P25", f"{p[0]:.1f}h")
        table_row("Duration P50 (median)", f"{p[1]:.1f}h")
        table_row("Duration P75", f"{p[2]:.1f}h")

        # Bucket by days
        dur_days = [h / 24 for h in durations_hours]
        day_buckets = Counter()
        for d in dur_days:
            if d < 1:
                day_buckets["<1 day"] += 1
            elif d < 3:
                day_buckets["1-3 days"] += 1
            elif d < 7:
                day_buckets["3-7 days"] += 1
            elif d < 14:
                day_buckets["1-2 weeks"] += 1
            else:
                day_buckets["2+ weeks"] += 1

        subsection("Duration buckets")
        total_g = len(durations_hours)
        for bucket in ["<1 day", "1-3 days", "3-7 days", "1-2 weeks", "2+ weeks"]:
            cnt = day_buckets.get(bucket, 0)
            table_row(f"  {bucket}", str(cnt), f"{bar(cnt, total_g)}  {pct(cnt, total_g)}")

    if reviews_per_word:
        subsection("Reviews to graduation")
        table_row("Median reviews", str(int(median(reviews_per_word))))
        table_row("Mean reviews", f"{mean(reviews_per_word):.1f}")
        p = _percentiles(reviews_per_word)
        table_row("P25 / P50 / P75", f"{p[0]:.0f} / {p[1]:.0f} / {p[2]:.0f}")

    return {
        "graduated_words": len(durations_hours),
        "duration_hours_percentiles": _percentiles(durations_hours),
        "reviews_to_graduation_median": int(median(reviews_per_word)) if reviews_per_word else 0,
        "reviews_to_graduation_mean": round(mean(reviews_per_word), 1) if reviews_per_word else 0,
        "reviews_to_graduation_percentiles": _percentiles(reviews_per_word),
    }


def analyze_retention_rate(conn):
    """7. Retention rate for FSRS reviews, weekly trend."""
    cur = conn.cursor()

    cur.execute("""
        SELECT reviewed_at, rating FROM review_log
        WHERE is_acquisition = 0 AND reviewed_at IS NOT NULL
    """)

    total = 0
    correct = 0
    weekly = defaultdict(lambda: {"correct": 0, "total": 0})

    for ra, rating in cur.fetchall():
        total += 1
        if rating >= 3:
            correct += 1
        dt = parse_dt(ra)
        if dt:
            w = iso_week(dt)
            weekly[w]["total"] += 1
            if rating >= 3:
                weekly[w]["correct"] += 1

    weekly_retention = {w: round(d["correct"] / d["total"] * 100, 1) if d["total"] > 0 else 0
                        for w, d in sorted(weekly.items())}

    # Console
    section("7. RETENTION RATE (FSRS reviews only)")
    table_row("FSRS reviews total", str(total))
    table_row("Correct (rating >= 3)", str(correct))
    table_row("Retention rate", pct(correct, total))

    subsection("Weekly retention (last 8 weeks)")
    weeks_sorted = sorted(weekly_retention.keys())[-8:]
    for w in weeks_sorted:
        r = weekly_retention[w]
        t = weekly[w]["total"]
        eprint(f"  {w}  {r:>5.1f}%  (n={t:>4d})  {bar(int(r), 100)}")

    return {
        "fsrs_reviews_total": total,
        "fsrs_correct": correct,
        "retention_rate_pct": round(correct / total * 100, 1) if total else 0,
        "weekly_retention": weekly_retention,
    }


def analyze_learning_velocity(conn):
    """8. Learning velocity: introductions and graduations per week, cumulative."""
    cur = conn.cursor()

    # Introductions
    cur.execute("SELECT introduced_at FROM user_lemma_knowledge WHERE introduced_at IS NOT NULL")
    intro_weeks = Counter()
    intro_dates = []
    for (ia,) in cur.fetchall():
        dt = parse_dt(ia)
        if dt:
            intro_weeks[iso_week(dt)] += 1
            intro_dates.append(dt)

    # Graduations
    cur.execute("SELECT graduated_at FROM user_lemma_knowledge WHERE graduated_at IS NOT NULL")
    grad_weeks = Counter()
    grad_dates = []
    for (ga,) in cur.fetchall():
        dt = parse_dt(ga)
        if dt:
            grad_weeks[iso_week(dt)] += 1
            grad_dates.append(dt)

    # Cumulative
    all_weeks = sorted(set(list(intro_weeks.keys()) + list(grad_weeks.keys())))
    cumulative = []
    cum_intro = 0
    cum_grad = 0
    for w in all_weeks:
        cum_intro += intro_weeks.get(w, 0)
        cum_grad += grad_weeks.get(w, 0)
        cumulative.append({"week": w, "cumulative_introduced": cum_intro, "cumulative_graduated": cum_grad})

    # Console
    section("8. LEARNING VELOCITY")
    table_row("Total introduced", str(sum(intro_weeks.values())))
    table_row("Total graduated", str(sum(grad_weeks.values())))

    subsection("Weekly velocity (last 8 weeks)")
    recent = all_weeks[-8:] if all_weeks else []
    eprint(f"  {'Week':<12s} {'Introduced':>11s} {'Graduated':>10s}")
    eprint(f"  {'-'*12} {'-'*11} {'-'*10}")
    for w in recent:
        eprint(f"  {w:<12s} {intro_weeks.get(w, 0):>11d} {grad_weeks.get(w, 0):>10d}")

    return {
        "total_introduced": sum(intro_weeks.values()),
        "total_graduated": sum(grad_weeks.values()),
        "weekly_introduced": dict(sorted(intro_weeks.items())),
        "weekly_graduated": dict(sorted(grad_weeks.items())),
        "cumulative": cumulative,
    }


def analyze_frequency_coverage(conn):
    """9. Frequency coverage: what % of top-N are known, and top frequency gaps."""
    cur = conn.cursor()

    # Join lemmas with knowledge
    cur.execute("""
        SELECT l.lemma_id, l.lemma_ar, l.lemma_ar_bare, l.gloss_en, l.frequency_rank,
               ulk.knowledge_state
        FROM lemmas l
        LEFT JOIN user_lemma_knowledge ulk ON l.lemma_id = ulk.lemma_id
        WHERE l.frequency_rank IS NOT NULL AND l.frequency_rank > 0
        ORDER BY l.frequency_rank
    """)

    rows = cur.fetchall()

    active_states = {"acquiring", "learning", "known"}
    known_states = {"known"}

    thresholds = [100, 500, 1000, 2000]
    coverage = {}
    for thr in thresholds:
        within = [r for r in rows if r[4] <= thr]
        total_in_thr = len(within)
        active_in_thr = len([r for r in within if r[5] in active_states])
        known_in_thr = len([r for r in within if r[5] in known_states])
        coverage[thr] = {
            "total_in_corpus": total_in_thr,
            "active_count": active_in_thr,
            "known_count": known_in_thr,
            "active_pct": round(active_in_thr / total_in_thr * 100, 1) if total_in_thr else 0,
            "known_pct": round(known_in_thr / total_in_thr * 100, 1) if total_in_thr else 0,
        }

    # Top frequency gaps: high-frequency words not in active states
    gaps = []
    for r in rows:
        lemma_id, lemma_ar, lemma_bare, gloss, rank, state = r
        if state not in active_states:
            gaps.append({
                "lemma_id": lemma_id,
                "lemma_ar": lemma_ar,
                "gloss_en": gloss or "",
                "frequency_rank": rank,
                "state": state or "none",
            })
        if len(gaps) >= 10:
            break

    # Console
    section("9. FREQUENCY COVERAGE")
    eprint(f"  {'Threshold':<15s} {'In corpus':>10s} {'Active':>8s} {'Known':>8s} {'Active%':>9s} {'Known%':>9s}")
    eprint(f"  {'-'*15} {'-'*10} {'-'*8} {'-'*8} {'-'*9} {'-'*9}")
    for thr in thresholds:
        c = coverage[thr]
        eprint(f"  Top {thr:<10d} {c['total_in_corpus']:>10d} {c['active_count']:>8d} {c['known_count']:>8d} {c['active_pct']:>8.1f}% {c['known_pct']:>8.1f}%")

    subsection("Top 10 frequency gaps (highest-freq unknown words)")
    for g in gaps:
        eprint(f"  #{g['frequency_rank']:<5d} {g['lemma_ar']:<15s} {g['gloss_en']:<25s} ({g['state']})")

    return {
        "coverage": {str(k): v for k, v in coverage.items()},
        "top_frequency_gaps": gaps,
    }


def analyze_study_consistency(conn):
    """10. Study consistency: active days, streaks, day-of-week, sessions per day."""
    cur = conn.cursor()

    # Active study days from review_log
    cur.execute("SELECT DISTINCT DATE(reviewed_at) FROM review_log WHERE reviewed_at IS NOT NULL")
    active_days = sorted([row[0] for row in cur.fetchall() if row[0]])

    # Current streak
    today = datetime.utcnow().date()
    streak = 0
    check_date = today
    active_set = set(active_days)
    # Allow today to be missing (might not have studied yet)
    if str(check_date) not in active_set:
        check_date = check_date - timedelta(days=1)
    while str(check_date) in active_set:
        streak += 1
        check_date = check_date - timedelta(days=1)

    # Longest streak
    longest_streak = 0
    current = 0
    if active_days:
        prev = datetime.strptime(active_days[0], "%Y-%m-%d").date()
        current = 1
        for ds in active_days[1:]:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
            if (d - prev).days == 1:
                current += 1
            else:
                longest_streak = max(longest_streak, current)
                current = 1
            prev = d
        longest_streak = max(longest_streak, current)

    # Day-of-week distribution
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow_counts = Counter()
    for ds in active_days:
        d = datetime.strptime(ds, "%Y-%m-%d").date()
        dow_counts[dow_names[d.weekday()]] += 1

    # Sessions per day (from review_log session_id)
    cur.execute("""
        SELECT DATE(reviewed_at) as d, COUNT(DISTINCT session_id) as sess
        FROM review_log
        WHERE reviewed_at IS NOT NULL AND session_id IS NOT NULL
        GROUP BY d
    """)
    sessions_per_day = {}
    spd_values = []
    for d, s in cur.fetchall():
        if d:
            sessions_per_day[d] = s
            spd_values.append(s)

    spd_hist = Counter(spd_values)

    # Console
    section("10. STUDY CONSISTENCY")
    table_row("Total active study days", str(len(active_days)))
    table_row("Current streak", f"{streak} days")
    table_row("Longest streak", f"{longest_streak} days")
    if active_days:
        first = active_days[0]
        last = active_days[-1]
        total_span = (datetime.strptime(last, "%Y-%m-%d") - datetime.strptime(first, "%Y-%m-%d")).days + 1
        table_row("Date range", f"{first} to {last}")
        table_row("Days active / total span", f"{len(active_days)} / {total_span}", f"({pct(len(active_days), total_span)})")

    subsection("Day-of-week distribution")
    max_dow = max(dow_counts.values()) if dow_counts else 1
    for day in dow_names:
        cnt = dow_counts.get(day, 0)
        eprint(f"  {day}  {cnt:>4d}  {bar(cnt, max_dow)}")

    subsection("Sessions per day histogram")
    if spd_values:
        table_row("Mean sessions/day", f"{mean(spd_values):.1f}")
        for n_sess in sorted(spd_hist.keys()):
            cnt = spd_hist[n_sess]
            eprint(f"  {n_sess} session(s): {cnt:>4d} days  {bar(cnt, len(spd_values))}")

    return {
        "total_active_days": len(active_days),
        "current_streak": streak,
        "longest_streak": longest_streak,
        "first_active_day": active_days[0] if active_days else None,
        "last_active_day": active_days[-1] if active_days else None,
        "day_of_week": dict(dow_counts),
        "sessions_per_day_histogram": dict(spd_hist),
        "mean_sessions_per_day": round(mean(spd_values), 1) if spd_values else 0,
    }


def analyze_tashkeel_readiness(conn):
    """Tashkeel Readiness: words eligible for tashkeel fading (stability >= 30d)."""
    cur = conn.cursor()

    cur.execute("""
        SELECT fsrs_card_json, knowledge_state
        FROM user_lemma_knowledge
        WHERE knowledge_state IN ('known', 'learning')
          AND fsrs_card_json IS NOT NULL
    """)

    total_review_words = 0
    eligible = 0
    stab_values = []

    for card_json_raw, state in cur.fetchall():
        try:
            card = json.loads(card_json_raw) if isinstance(card_json_raw, str) else card_json_raw
        except (json.JSONDecodeError, TypeError):
            continue
        if not card or not isinstance(card, dict):
            continue

        stab = card.get("stability") or card.get("s")
        if stab is None:
            continue

        stab = float(stab)
        total_review_words += 1
        stab_values.append(stab)
        if stab >= 30:
            eligible += 1

    # Stability buckets for known/learning
    stab_buckets = Counter()
    for s in stab_values:
        if s < 1:
            stab_buckets["<1d"] += 1
        elif s < 7:
            stab_buckets["1-7d"] += 1
        elif s < 30:
            stab_buckets["7-30d"] += 1
        elif s < 90:
            stab_buckets["30-90d"] += 1
        elif s < 180:
            stab_buckets["90-180d"] += 1
        else:
            stab_buckets["180+d"] += 1

    # Console
    section("TASHKEEL READINESS")
    table_row("Known/learning words with FSRS", str(total_review_words))
    table_row("Eligible (stability >= 30d)", str(eligible), pct(eligible, total_review_words))
    table_row("Not yet eligible", str(total_review_words - eligible))

    if stab_values:
        table_row("Median stability", f"{median(stab_values):.1f}d")
        table_row("Mean stability", f"{mean(stab_values):.1f}d")

    subsection("Stability distribution (known/learning)")
    for bucket in ["<1d", "1-7d", "7-30d", "30-90d", "90-180d", "180+d"]:
        cnt = stab_buckets.get(bucket, 0)
        table_row(f"  {bucket}", str(cnt), f"{bar(cnt, total_review_words)}  {pct(cnt, total_review_words)}")

    return {
        "total_review_words": total_review_words,
        "eligible_count": eligible,
        "eligible_pct": round(eligible / total_review_words * 100, 1) if total_review_words else 0,
        "stability_median": round(median(stab_values), 1) if stab_values else 0,
        "stability_mean": round(mean(stab_values), 1) if stab_values else 0,
        "stability_buckets": dict(stab_buckets),
    }


def _percentiles(values):
    """Return [p25, p50, p75] for a list of numbers. Returns [0,0,0] if empty."""
    if not values:
        return [0, 0, 0]
    s = sorted(values)
    n = len(s)
    p25 = s[int(n * 0.25)]
    p50 = s[int(n * 0.50)]
    p75 = s[int(n * 0.75)]
    return [round(p25, 2), round(p50, 2), round(p75, 2)]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Alif learning analysis")
    parser.add_argument("--db", default="/app/data/alif.db", help="Path to SQLite database")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = None  # tuples
    conn.execute("PRAGMA journal_mode=WAL")

    eprint(f"Alif Learning Analysis  |  DB: {args.db}  |  {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    results = {}

    results["vocabulary_by_status"] = analyze_vocabulary_by_status(conn)
    results["acquisition_rate"] = analyze_acquisition_rate(conn)
    results["review_accuracy"] = analyze_review_accuracy(conn)
    results["session_patterns"] = analyze_session_patterns(conn)
    results["fsrs_stability"] = analyze_fsrs_stability(conn)
    results["time_to_acquisition"] = analyze_time_to_acquisition(conn)
    results["retention_rate"] = analyze_retention_rate(conn)
    results["learning_velocity"] = analyze_learning_velocity(conn)
    results["frequency_coverage"] = analyze_frequency_coverage(conn)
    results["study_consistency"] = analyze_study_consistency(conn)
    results["tashkeel_readiness"] = analyze_tashkeel_readiness(conn)

    results["_meta"] = {
        "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "db_path": args.db,
    }

    conn.close()

    # JSON to stdout
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))

    eprint()
    eprint("=" * 70)
    eprint("  JSON output written to stdout. Pipe to file with: > analysis.json")
    eprint("=" * 70)


if __name__ == "__main__":
    main()
