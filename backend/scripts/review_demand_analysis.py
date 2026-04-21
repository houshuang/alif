#!/usr/bin/env python3
"""
Review demand analysis & ingestion simulation for Alif.

Pulls actual learner parameters from production DB, then simulates
forward review demand under different word-ingestion scenarios.

Usage:
    python3 scripts/review_demand_analysis.py --db /app/data/alif.db
"""

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import mean, median


def parse_dt(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S+00:00", "%Y-%m-%dT%H:%M:%S.%f+00:00"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def eprint(*a, **kw):
    print(*a, file=sys.stderr, **kw)


def section(title):
    eprint()
    eprint("=" * 72)
    eprint(f"  {title}")
    eprint("=" * 72)


def subsection(title):
    eprint()
    eprint(f"  --- {title} ---")


def bar(count, total, w=30):
    if total == 0:
        return "[" + " " * w + "]"
    f = int(round(count / total * w))
    return "[" + "#" * f + " " * (w - f) + "]"


def pct(c, t):
    return f"{c/t*100:.1f}%" if t else "0%"


# ── Part 1: Extract learner parameters ──────────────────────────────────

def extract_learner_params(conn):
    """Extract real learner parameters from DB for simulation."""
    cur = conn.cursor()
    results = {}

    # Current vocabulary state
    cur.execute("""
        SELECT knowledge_state, COUNT(*)
        FROM user_lemma_knowledge
        GROUP BY knowledge_state ORDER BY COUNT(*) DESC
    """)
    results["state_counts"] = dict(cur.fetchall())

    # Acquiring pipeline detail
    cur.execute("""
        SELECT ulk.acquisition_box, ulk.acquisition_next_due,
               ulk.times_seen, ulk.times_correct,
               ulk.entered_acquiring_at,
               l.lemma_ar, l.gloss_en
        FROM user_lemma_knowledge ulk
        JOIN lemmas l ON l.lemma_id = ulk.lemma_id
        WHERE ulk.knowledge_state = 'acquiring'
        ORDER BY ulk.acquisition_box, ulk.acquisition_next_due
    """)
    acq_rows = cur.fetchall()
    now = datetime.utcnow()
    box_counts = Counter()
    box_due_now = Counter()
    acq_details = []
    for box, next_due_s, seen, correct, entered_s, ar, en in acq_rows:
        b = box or 1
        box_counts[b] += 1
        nd = parse_dt(next_due_s)
        if nd and nd <= now:
            box_due_now[b] += 1
        entered = parse_dt(entered_s)
        days_in = (now - entered).total_seconds() / 86400 if entered else 0
        acc = correct / seen if seen and seen > 0 else 0
        acq_details.append({
            "box": b, "due_now": nd is not None and nd <= now,
            "seen": seen or 0, "correct": correct or 0, "acc": acc,
            "days_in": round(days_in, 1), "ar": ar, "en": en,
        })
    results["acquiring"] = {
        "total": len(acq_rows),
        "box_counts": dict(box_counts),
        "box_due_now": dict(box_due_now),
        "total_due_now": sum(box_due_now.values()),
        "details": acq_details,
    }

    # FSRS words: stability & due distribution
    cur.execute("""
        SELECT ulk.knowledge_state, ulk.fsrs_card_json
        FROM user_lemma_knowledge ulk
        WHERE ulk.knowledge_state IN ('learning', 'known', 'lapsed')
          AND ulk.fsrs_card_json IS NOT NULL
    """)
    fsrs_due_buckets = {"overdue": 0, "today": 0, "tomorrow": 0,
                        "this_week": 0, "next_week": 0, "later": 0}
    stab_values = []
    stab_by_state = defaultdict(list)
    fsrs_total = 0
    today_end = now.replace(hour=23, minute=59, second=59)
    tomorrow_end = today_end + timedelta(days=1)
    week_end = today_end + timedelta(days=7)
    two_week_end = today_end + timedelta(days=14)

    for state, cj in cur.fetchall():
        try:
            card = json.loads(cj) if isinstance(cj, str) else cj
        except (json.JSONDecodeError, TypeError):
            continue
        if not card:
            continue
        fsrs_total += 1
        stab = card.get("stability") or card.get("s")
        if stab is not None:
            stab = float(stab)
            stab_values.append(stab)
            stab_by_state[state].append(stab)

        due_s = card.get("due")
        due = parse_dt(due_s) if due_s else None
        if not due or due <= now:
            fsrs_due_buckets["overdue"] += 1
        elif due <= today_end:
            fsrs_due_buckets["today"] += 1
        elif due <= tomorrow_end:
            fsrs_due_buckets["tomorrow"] += 1
        elif due <= week_end:
            fsrs_due_buckets["this_week"] += 1
        elif due <= two_week_end:
            fsrs_due_buckets["next_week"] += 1
        else:
            fsrs_due_buckets["later"] += 1

    stab_buckets = Counter()
    for s in stab_values:
        if s < 1: stab_buckets["<1d"] += 1
        elif s < 7: stab_buckets["1-7d"] += 1
        elif s < 30: stab_buckets["7-30d"] += 1
        elif s < 90: stab_buckets["30-90d"] += 1
        else: stab_buckets["90+d"] += 1

    results["fsrs"] = {
        "total": fsrs_total,
        "due_buckets": fsrs_due_buckets,
        "stability_median": round(median(stab_values), 1) if stab_values else 0,
        "stability_mean": round(mean(stab_values), 1) if stab_values else 0,
        "stability_buckets": dict(stab_buckets),
        "by_state": {
            st: {"n": len(v), "median": round(median(v), 1), "mean": round(mean(v), 1)}
            for st, v in stab_by_state.items() if v
        },
    }

    # Graduation rate & acquisition success metrics
    cur.execute("""
        SELECT entered_acquiring_at, graduated_at, times_seen, times_correct
        FROM user_lemma_knowledge
        WHERE entered_acquiring_at IS NOT NULL AND graduated_at IS NOT NULL
    """)
    grad_durations_h = []
    grad_reviews = []
    for ea, ga, seen, correct in cur.fetchall():
        ea_dt, ga_dt = parse_dt(ea), parse_dt(ga)
        if ea_dt and ga_dt:
            h = (ga_dt - ea_dt).total_seconds() / 3600
            if h >= 0:
                grad_durations_h.append(h)
                grad_reviews.append(seen or 0)

    # Words that entered acquisition but haven't graduated (drop-out / stuck)
    cur.execute("""
        SELECT COUNT(*) FROM user_lemma_knowledge
        WHERE knowledge_state = 'acquiring'
          AND entered_acquiring_at IS NOT NULL AND graduated_at IS NULL
    """)
    still_acquiring = cur.fetchone()[0]
    cur.execute("""
        SELECT COUNT(*) FROM user_lemma_knowledge
        WHERE knowledge_state = 'suspended'
          AND entered_acquiring_at IS NOT NULL AND graduated_at IS NULL
    """)
    suspended_before_grad = cur.fetchone()[0]

    total_ever_acquired = len(grad_durations_h) + still_acquiring + suspended_before_grad
    grad_rate = len(grad_durations_h) / total_ever_acquired if total_ever_acquired else 0

    results["graduation"] = {
        "total_graduated": len(grad_durations_h),
        "still_acquiring": still_acquiring,
        "suspended_before_grad": suspended_before_grad,
        "graduation_rate": round(grad_rate * 100, 1),
        "duration_hours_median": round(median(grad_durations_h), 1) if grad_durations_h else 0,
        "duration_hours_mean": round(mean(grad_durations_h), 1) if grad_durations_h else 0,
        "duration_days_median": round(median(grad_durations_h) / 24, 1) if grad_durations_h else 0,
        "reviews_to_grad_median": int(median(grad_reviews)) if grad_reviews else 0,
        "reviews_to_grad_mean": round(mean(grad_reviews), 1) if grad_reviews else 0,
    }

    # Weekly graduations (last 4 weeks)
    cur.execute("SELECT graduated_at FROM user_lemma_knowledge WHERE graduated_at IS NOT NULL")
    weekly_grads = Counter()
    for (ga,) in cur.fetchall():
        dt = parse_dt(ga)
        if dt:
            iso = dt.isocalendar()
            weekly_grads[f"{iso[0]}-W{iso[1]:02d}"] += 1
    results["weekly_graduations"] = dict(sorted(weekly_grads.items()))

    # Accuracy metrics (last 14 days)
    cutoff_14d = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        SELECT is_acquisition, rating, COUNT(*)
        FROM review_log WHERE reviewed_at >= ?
        GROUP BY is_acquisition, rating
    """, (cutoff_14d,))
    acq_ratings = {}
    fsrs_ratings = {}
    for is_acq, rating, cnt in cur.fetchall():
        d = acq_ratings if is_acq else fsrs_ratings
        d[rating] = cnt
    acq_total = sum(acq_ratings.values())
    acq_correct = sum(v for k, v in acq_ratings.items() if k >= 3)
    fsrs_rtotal = sum(fsrs_ratings.values())
    fsrs_correct = sum(v for k, v in fsrs_ratings.items() if k >= 3)

    results["accuracy_14d"] = {
        "acquisition": {
            "total": acq_total,
            "correct": acq_correct,
            "rate": round(acq_correct / acq_total * 100, 1) if acq_total else 0,
            "distribution": acq_ratings,
        },
        "fsrs": {
            "total": fsrs_rtotal,
            "correct": fsrs_correct,
            "rate": round(fsrs_correct / fsrs_rtotal * 100, 1) if fsrs_rtotal else 0,
            "distribution": fsrs_ratings,
        },
    }

    # FSRS retention by stability bucket (last 14 days)
    cur.execute("""
        SELECT rl.rating, ulk.fsrs_card_json
        FROM review_log rl
        JOIN user_lemma_knowledge ulk ON rl.lemma_id = ulk.lemma_id
        WHERE rl.is_acquisition = 0 AND rl.reviewed_at >= ?
          AND ulk.fsrs_card_json IS NOT NULL
    """, (cutoff_14d,))
    retention_by_stab = defaultdict(lambda: {"total": 0, "correct": 0})
    for rating, cj in cur.fetchall():
        try:
            card = json.loads(cj) if isinstance(cj, str) else cj
        except:
            continue
        if not card or not isinstance(card, dict):
            continue
        stab = card.get("stability") or card.get("s") or 0
        if stab < 7: bucket = "<7d"
        elif stab < 30: bucket = "7-30d"
        elif stab < 90: bucket = "30-90d"
        else: bucket = "90+d"
        retention_by_stab[bucket]["total"] += 1
        if rating >= 3:
            retention_by_stab[bucket]["correct"] += 1
    results["retention_by_stability"] = {
        k: {**v, "rate": round(v["correct"] / v["total"] * 100, 1) if v["total"] else 0}
        for k, v in retention_by_stab.items()
    }

    # Daily activity last 7 days
    cutoff_7d = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        SELECT DATE(reviewed_at) as d,
               COUNT(*) as total,
               SUM(CASE WHEN rating >= 3 THEN 1 ELSE 0 END) as correct,
               SUM(CASE WHEN is_acquisition = 1 THEN 1 ELSE 0 END) as acq,
               SUM(CASE WHEN is_acquisition = 0 THEN 1 ELSE 0 END) as fsrs_r
        FROM review_log WHERE reviewed_at >= ?
        GROUP BY d ORDER BY d
    """, (cutoff_7d,))
    daily_word_reviews = []
    for d, total, correct, acq_r, fsrs_r in cur.fetchall():
        daily_word_reviews.append({
            "date": d, "total": total, "correct": correct,
            "acq": acq_r, "fsrs": fsrs_r,
            "accuracy": round(correct / total * 100, 1) if total else 0,
        })
    results["daily_word_reviews_7d"] = daily_word_reviews

    cur.execute("""
        SELECT DATE(reviewed_at) as d, COUNT(*) as cnt,
               SUM(CASE WHEN response_ms IS NOT NULL AND response_ms < 300000 THEN response_ms ELSE 0 END) as total_ms,
               SUM(CASE WHEN response_ms IS NOT NULL AND response_ms < 300000 THEN 1 ELSE 0 END) as valid_n
        FROM sentence_review_log WHERE reviewed_at >= ?
        GROUP BY d ORDER BY d
    """, (cutoff_7d,))
    daily_sentence_reviews = []
    for d, cnt, total_ms, valid_n in cur.fetchall():
        mins = total_ms / 1000 / 60 if total_ms else 0
        daily_sentence_reviews.append({
            "date": d, "sentences": cnt,
            "minutes": round(mins, 1),
            "avg_seconds": round(total_ms / valid_n / 1000, 1) if valid_n else 0,
        })
    results["daily_sentence_reviews_7d"] = daily_sentence_reviews

    # Daily introductions & graduations (last 7 days)
    cur.execute("""
        SELECT DATE(entered_acquiring_at) as d, COUNT(*) as cnt
        FROM user_lemma_knowledge
        WHERE entered_acquiring_at >= ?
        GROUP BY d ORDER BY d
    """, (cutoff_7d,))
    results["daily_intros_7d"] = [{"date": d, "count": c} for d, c in cur.fetchall()]

    cur.execute("""
        SELECT DATE(graduated_at) as d, COUNT(*) as cnt
        FROM user_lemma_knowledge
        WHERE graduated_at >= ?
        GROUP BY d ORDER BY d
    """, (cutoff_7d,))
    results["daily_grads_7d"] = [{"date": d, "count": c} for d, c in cur.fetchall()]

    # Sentence pool health
    cur.execute("SELECT COUNT(*) FROM sentences WHERE is_active = 1")
    active_sentences = cur.fetchone()[0]
    cur.execute("""
        SELECT COALESCE(sc.cnt, 0) as n_sentences, COUNT(*) as n_words
        FROM user_lemma_knowledge ulk
        LEFT JOIN (
            SELECT sw.lemma_id, COUNT(DISTINCT sw.sentence_id) as cnt
            FROM sentence_words sw
            JOIN sentences s ON s.id = sw.sentence_id AND s.is_active = 1
            GROUP BY sw.lemma_id
        ) sc ON sc.lemma_id = ulk.lemma_id
        WHERE ulk.knowledge_state IN ('acquiring', 'learning', 'known', 'lapsed')
        GROUP BY COALESCE(sc.cnt, 0)
        ORDER BY n_sentences
    """)
    coverage_dist = {row[0]: row[1] for row in cur.fetchall()}
    # Real cap lives in material_generator.PIPELINE_CAP
    PIPELINE_CAP = 2000
    results["sentence_pool"] = {
        "active": active_sentences,
        "cap": PIPELINE_CAP,
        "headroom": PIPELINE_CAP - active_sentences,
        "coverage_distribution": coverage_dist,
        "words_with_0": coverage_dist.get(0, 0),
        "words_with_1": coverage_dist.get(1, 0),
        "words_with_2": coverage_dist.get(2, 0),
        "words_with_3_plus": sum(v for k, v in coverage_dist.items() if k >= 3),
    }

    # Overused sentences
    cur.execute("""
        SELECT COUNT(*) FROM sentences
        WHERE is_active = 1 AND times_shown >= 10
    """)
    results["overused_sentences"] = cur.fetchone()[0]

    # Median response time (for time estimates)
    cur.execute("""
        SELECT response_ms FROM sentence_review_log
        WHERE response_ms IS NOT NULL AND response_ms < 300000
        ORDER BY response_ms
    """)
    all_times = [r[0] for r in cur.fetchall()]
    results["median_response_ms"] = all_times[len(all_times) // 2] if all_times else 15000

    # Words per sentence (due words covered per sentence in recent reviews)
    # Approximate from sentence_words: avg target words per sentence
    cur.execute("""
        SELECT AVG(wc) FROM (
            SELECT sw.sentence_id, COUNT(DISTINCT sw.lemma_id) as wc
            FROM sentence_words sw
            JOIN sentences s ON s.id = sw.sentence_id AND s.is_active = 1
            GROUP BY sw.sentence_id
        )
    """)
    avg_words = cur.fetchone()[0] or 4
    # Due words per sentence is typically lower — roughly 1.5-2.5
    # Use target_lemma based metric
    cur.execute("""
        SELECT AVG(tc) FROM (
            SELECT sw.sentence_id, SUM(CASE WHEN sw.is_target_word = 1 THEN 1 ELSE 0 END) as tc
            FROM sentence_words sw
            JOIN sentences s ON s.id = sw.sentence_id AND s.is_active = 1
            GROUP BY sw.sentence_id
        )
    """)
    avg_targets = cur.fetchone()[0] or 1.5
    results["avg_words_per_sentence"] = round(avg_words, 1)
    results["avg_target_words_per_sentence"] = round(avg_targets, 1)

    # Leech-criterion check: recent sliding-window accuracy, matching
    # leech_service.py (LEECH_MIN_REVIEWS=5, LEECH_MAX_ACCURACY=0.50,
    # LEECH_WINDOW_SIZE=8). Words matching this while still 'acquiring'
    # are genuine anomalies — the auto-suspend on review submission
    # should have caught them.
    cur.execute("""
        WITH latest AS (
            SELECT rl.lemma_id, rl.rating,
                   ROW_NUMBER() OVER (
                       PARTITION BY rl.lemma_id
                       ORDER BY rl.reviewed_at DESC
                   ) AS rn
            FROM review_log rl
        ),
        recent8 AS (
            SELECT lemma_id,
                   COUNT(*) AS n,
                   SUM(CASE WHEN rating >= 3 THEN 1 ELSE 0 END) AS n_correct
            FROM latest
            WHERE rn <= 8
            GROUP BY lemma_id
        )
        SELECT l.lemma_ar, l.gloss_en, ulk.acquisition_box,
               ulk.times_seen, ulk.times_correct, ulk.entered_acquiring_at,
               r.n, r.n_correct
        FROM user_lemma_knowledge ulk
        JOIN lemmas l ON l.lemma_id = ulk.lemma_id
        JOIN recent8 r ON r.lemma_id = ulk.lemma_id
        WHERE ulk.knowledge_state = 'acquiring'
          AND r.n >= 5
          AND (1.0 * r.n_correct / r.n) < 0.50
    """)
    stuck = []
    for ar, en, box, seen, correct, entered_s, n, n_correct in cur.fetchall():
        entered = parse_dt(entered_s)
        days = (now - entered).total_seconds() / 86400 if entered else 0
        stuck.append({
            "ar": ar, "en": en, "box": box,
            "seen": seen, "correct": correct,
            "recent_n": n, "recent_correct": n_correct,
            "recent_acc": round(100.0 * n_correct / n, 1),
            "days_in": round(days, 1),
        })
    results["stuck_words"] = stuck

    return results


# ── Part 2: Simulate forward review demand ──────────────────────────────

def simulate_demand(conn, params, projection_days=30):
    """Simulate forward review demand from current state."""
    cur = conn.cursor()
    now = datetime.utcnow()

    # Initialize daily buckets
    days = []
    for d in range(projection_days):
        dt = now + timedelta(days=d)
        days.append({
            "day": d,
            "date": dt.strftime("%Y-%m-%d"),
            "acq_word_reviews": 0,
            "fsrs_word_reviews": 0,
        })
    day_map = {d["date"]: d for d in days}

    # ── Acquiring word demand ──
    BOX_H = {1: 4, 2: 24, 3: 72}
    cur.execute("""
        SELECT ulk.acquisition_box, ulk.acquisition_next_due,
               ulk.times_seen, ulk.times_correct
        FROM user_lemma_knowledge ulk
        WHERE ulk.knowledge_state = 'acquiring'
    """)
    for box, nd_s, seen, correct in cur.fetchall():
        b = box or 1
        nd = parse_dt(nd_s) or now
        sim_time = max(nd, now)
        sim_box = b
        sim_seen = seen or 0
        sim_correct = correct or 0
        end = now + timedelta(days=projection_days)

        while sim_time < end:
            ds = sim_time.strftime("%Y-%m-%d")
            if ds in day_map:
                day_map[ds]["acq_word_reviews"] += 1

            # Simulate advancement (best case)
            sim_seen += 1
            sim_correct += 1
            if sim_box < 3:
                sim_box += 1
            else:
                acc = sim_correct / sim_seen if sim_seen else 0
                if sim_seen >= 5 and acc >= 0.60:
                    # Graduates → FSRS with S₀ ≈ 2.3d
                    next_due = sim_time + timedelta(days=2.3)
                    nds = next_due.strftime("%Y-%m-%d")
                    if nds in day_map:
                        day_map[nds]["fsrs_word_reviews"] += 1
                    # After that, stability grows: 2.3 * 2.5 ≈ 5.75d
                    s = 2.3 * 2.5
                    t2 = next_due + timedelta(days=s)
                    while t2 < end:
                        ds2 = t2.strftime("%Y-%m-%d")
                        if ds2 in day_map:
                            day_map[ds2]["fsrs_word_reviews"] += 1
                        s *= 2.5
                        t2 = t2 + timedelta(days=s)
                    break

            sim_time += timedelta(hours=BOX_H.get(sim_box, 72))

    # ── FSRS word demand ──
    cur.execute("""
        SELECT ulk.fsrs_card_json
        FROM user_lemma_knowledge ulk
        WHERE ulk.knowledge_state IN ('learning', 'known', 'lapsed')
          AND ulk.fsrs_card_json IS NOT NULL
    """)
    end = now + timedelta(days=projection_days)
    for (cj,) in cur.fetchall():
        try:
            card = json.loads(cj) if isinstance(cj, str) else cj
        except:
            continue
        if not card:
            continue
        due_s = card.get("due")
        stab = card.get("stability") or card.get("s") or 1.0
        due = parse_dt(due_s) if due_s else now

        sim_time = max(due, now) if due else now
        sim_stab = float(stab)

        while sim_time < end:
            ds = sim_time.strftime("%Y-%m-%d")
            if ds in day_map:
                day_map[ds]["fsrs_word_reviews"] += 1
            sim_stab *= 2.5
            sim_time += timedelta(days=sim_stab)

    # Compute sentence & time estimates
    median_ms = params["median_response_ms"]
    avg_targets = max(params["avg_target_words_per_sentence"], 1.0)

    for d in days:
        # Acquiring words need ~4 exposures per session appearance
        # but set cover means ~avg_targets due words per sentence
        acq_sentences = max(1, int(d["acq_word_reviews"] * 4 / avg_targets)) if d["acq_word_reviews"] else 0
        fsrs_sentences = max(1, int(d["fsrs_word_reviews"] / avg_targets)) if d["fsrs_word_reviews"] else 0
        d["total_word_reviews"] = d["acq_word_reviews"] + d["fsrs_word_reviews"]
        d["est_sentences"] = acq_sentences + fsrs_sentences
        d["est_minutes"] = round(d["est_sentences"] * median_ms / 1000 / 60, 1)

    return days


# ── Part 3: Ingestion scenario simulations ──────────────────────────────

def simulate_ingestion_scenario(params, n_new_words, ingestion_day, projection_days=30):
    """
    Simulate what happens when N new words enter acquisition on a given day.

    Uses learner's actual graduation rate, accuracy, and timing parameters.
    Returns daily review demand projections.
    """
    now = datetime.utcnow()
    days = []
    for d in range(projection_days):
        days.append({
            "day": d,
            "date": (now + timedelta(days=d)).strftime("%Y-%m-%d"),
            "new_acq_reviews": 0,
            "new_fsrs_reviews": 0,
        })

    BOX_H = {1: 4, 2: 24, 3: 72}

    # Use actual learner accuracy for simulation
    acq_acc = params["accuracy_14d"]["acquisition"]["rate"] / 100
    # Probability of passing each box review
    p_pass = max(0.5, min(0.95, acq_acc))
    # Probability of failing → reset to box 1
    p_fail = 1 - p_pass

    # Simulate each new word independently
    for w in range(n_new_words):
        sim_box = 1
        sim_time = now + timedelta(days=ingestion_day)
        sim_seen = 0
        sim_correct = 0
        end = now + timedelta(days=projection_days)
        max_iterations = 100  # safety

        iterations = 0
        while sim_time < end and iterations < max_iterations:
            iterations += 1
            day_idx = (sim_time - now).days
            if 0 <= day_idx < projection_days:
                days[day_idx]["new_acq_reviews"] += 1

            sim_seen += 1
            # Simulate with actual accuracy
            if sim_seen <= 2:
                passes = True  # first exposures in learn mode are typically correct
            else:
                # Use probability
                import random
                passes = random.random() < p_pass

            if passes:
                sim_correct += 1
                if sim_box < 3:
                    sim_box += 1
                else:
                    acc = sim_correct / sim_seen if sim_seen else 0
                    if sim_seen >= 5 and acc >= 0.60:
                        # Graduates → FSRS
                        s = 2.3
                        t = sim_time + timedelta(days=s)
                        while t < end:
                            di = (t - now).days
                            if 0 <= di < projection_days:
                                days[di]["new_fsrs_reviews"] += 1
                            s *= 2.5
                            t += timedelta(days=s)
                        break
            else:
                sim_box = 1  # reset

            sim_time += timedelta(hours=BOX_H.get(sim_box, 72))

    for d in days:
        d["total_new"] = d["new_acq_reviews"] + d["new_fsrs_reviews"]

    return days


def run_scenarios(params):
    """Run multiple ingestion scenarios and return results."""
    import random
    random.seed(42)  # reproducible

    scenarios = [
        {"label": "No new words (maintenance only)", "words": 0, "day": 0},
        {"label": "Small batch: 15 words (1 short reading)", "words": 15, "day": 0},
        {"label": "Medium batch: 30 words (focused reading session)", "words": 30, "day": 0},
        {"label": "Large batch: 50 words (intensive book session)", "words": 50, "day": 0},
        {"label": "Weekend binge: 80 words (full weekend reading)", "words": 80, "day": 0},
        {"label": "Two weekends: 50 now + 50 in 7 days", "words": 50, "day": 0, "extra": [{"words": 50, "day": 7}]},
    ]

    results = []
    for sc in scenarios:
        random.seed(42)
        proj = simulate_ingestion_scenario(params, sc["words"], sc["day"])

        # Add extra batches if any
        if "extra" in sc:
            for ex in sc["extra"]:
                random.seed(42 + ex["day"])
                extra_proj = simulate_ingestion_scenario(params, ex["words"], ex["day"])
                for i, d in enumerate(proj):
                    d["new_acq_reviews"] += extra_proj[i]["new_acq_reviews"]
                    d["new_fsrs_reviews"] += extra_proj[i]["new_fsrs_reviews"]
                    d["total_new"] += extra_proj[i]["total_new"]

        # Compute sentences & minutes
        median_ms = params["median_response_ms"]
        avg_targets = max(params["avg_target_words_per_sentence"], 1.0)

        for d in proj:
            acq_s = max(1, int(d["new_acq_reviews"] * 4 / avg_targets)) if d["new_acq_reviews"] else 0
            fsrs_s = max(1, int(d["new_fsrs_reviews"] / avg_targets)) if d["new_fsrs_reviews"] else 0
            d["est_sentences"] = acq_s + fsrs_s
            d["est_minutes"] = round(d["est_sentences"] * median_ms / 1000 / 60, 1)

        peak_sent = max(d["est_sentences"] for d in proj) if proj else 0
        peak_min = max(d["est_minutes"] for d in proj) if proj else 0
        avg_sent = round(mean(d["est_sentences"] for d in proj), 1) if proj else 0
        avg_min = round(mean(d["est_minutes"] for d in proj), 1) if proj else 0
        # Weeks 1 & 2 averages
        w1 = proj[:7]
        w2 = proj[7:14]
        w1_avg_sent = round(mean(d["est_sentences"] for d in w1), 1) if w1 else 0
        w2_avg_sent = round(mean(d["est_sentences"] for d in w2), 1) if w2 else 0
        w1_avg_min = round(mean(d["est_minutes"] for d in w1), 1) if w1 else 0
        w2_avg_min = round(mean(d["est_minutes"] for d in w2), 1) if w2 else 0

        results.append({
            "label": sc["label"],
            "words": sc["words"],
            "projection": proj,
            "peak_sentences": peak_sent,
            "peak_minutes": peak_min,
            "avg_sentences_30d": avg_sent,
            "avg_minutes_30d": avg_min,
            "week1_avg_sentences": w1_avg_sent,
            "week1_avg_minutes": w1_avg_min,
            "week2_avg_sentences": w2_avg_sent,
            "week2_avg_minutes": w2_avg_min,
        })

    return results


# ── Part 4: Print report ────────────────────────────────────────────────

def print_report(params, baseline_demand, scenarios):
    now = datetime.utcnow()

    section("CURRENT VOCABULARY STATE")
    sc = params["state_counts"]
    total = sum(sc.values())
    for state in ["known", "learning", "acquiring", "encountered", "lapsed", "suspended"]:
        cnt = sc.get(state, 0)
        eprint(f"  {state:<16s} {cnt:>5d}  {bar(cnt, total)}  {pct(cnt, total)}")
    eprint(f"  {'TOTAL':<16s} {total:>5d}")

    section("ACQUIRING PIPELINE")
    acq = params["acquiring"]
    for b in [1, 2, 3]:
        interval = {1: "4h", 2: "1d", 3: "3d"}[b]
        cnt = acq["box_counts"].get(b, 0)
        due = acq["box_due_now"].get(b, 0)
        eprint(f"  Box {b} ({interval}):  {cnt:>3d} words  ({due} due now)")
    eprint(f"  TOTAL:       {acq['total']:>3d} words  ({acq['total_due_now']} due now)")
    if acq["total"] > 40:
        eprint(f"  ⚠ ABOVE PIPELINE_BACKLOG_THRESHOLD (40) — auto-intro SUPPRESSED")

    section("FSRS REVIEW WORDS")
    fsrs = params["fsrs"]
    eprint(f"  Total FSRS words:    {fsrs['total']}")
    eprint(f"  Stability median:    {fsrs['stability_median']}d")
    eprint(f"  Stability mean:      {fsrs['stability_mean']}d")
    subsection("Due schedule")
    for k in ["overdue", "today", "tomorrow", "this_week", "next_week", "later"]:
        eprint(f"  {k:<16s} {fsrs['due_buckets'].get(k, 0):>5d}")
    subsection("Stability distribution")
    for k in ["<1d", "1-7d", "7-30d", "30-90d", "90+d"]:
        cnt = fsrs["stability_buckets"].get(k, 0)
        eprint(f"  {k:<8s} {cnt:>5d}  {bar(cnt, fsrs['total'])}")
    subsection("Retention by stability (14d)")
    for k in ["<7d", "7-30d", "30-90d", "90+d"]:
        d = params["retention_by_stability"].get(k, {})
        if d.get("total", 0):
            eprint(f"  {k:<8s}  {d['rate']:>5.1f}% retention  (n={d['total']})")

    section("YOUR LEARNING RATE (Quantified)")
    grad = params["graduation"]
    a14 = params["accuracy_14d"]
    eprint(f"  Graduation success rate:     {grad['graduation_rate']}%")
    eprint(f"  Median time to graduate:     {grad['duration_days_median']} days ({grad['duration_hours_median']}h)")
    eprint(f"  Median reviews to graduate:  {grad['reviews_to_grad_median']}")
    eprint(f"  Mean reviews to graduate:    {grad['reviews_to_grad_mean']}")
    eprint(f"  Acquisition accuracy (14d):  {a14['acquisition']['rate']}% ({a14['acquisition']['total']} reviews)")
    eprint(f"  FSRS accuracy (14d):         {a14['fsrs']['rate']}% ({a14['fsrs']['total']} reviews)")
    eprint(f"  Median response time:        {params['median_response_ms']/1000:.1f}s per sentence")

    subsection("Weekly graduations")
    wg = params["weekly_graduations"]
    recent = list(sorted(wg.items()))[-6:]
    max_g = max(v for _, v in recent) if recent else 1
    for w, c in recent:
        eprint(f"  {w}  {c:>4d}  {bar(c, max_g)}")

    section("LAST 7 DAYS ACTIVITY")
    subsection("Word reviews per day")
    for d in params["daily_word_reviews_7d"]:
        eprint(f"  {d['date']}  {d['total']:>5d} reviews  (acq={d['acq']}, fsrs={d['fsrs']})  accuracy={d['accuracy']}%")
    subsection("Sentences per day")
    for d in params["daily_sentence_reviews_7d"]:
        eprint(f"  {d['date']}  {d['sentences']:>4d} sentences  {d['minutes']:>5.1f} min  ({d['avg_seconds']}s avg)")
    subsection("Introductions & graduations")
    intro_map = {d["date"]: d["count"] for d in params["daily_intros_7d"]}
    grad_map = {d["date"]: d["count"] for d in params["daily_grads_7d"]}
    all_dates = sorted(set(list(intro_map.keys()) + list(grad_map.keys())))
    for d in all_dates:
        eprint(f"  {d}  introduced={intro_map.get(d, 0):>3d}  graduated={grad_map.get(d, 0):>3d}")

    section("SENTENCE POOL HEALTH")
    sp = params["sentence_pool"]
    eprint(f"  Active: {sp['active']} / {sp['cap']} cap  (headroom: {sp['headroom']})")
    eprint(f"  Words with 0 sentences:    {sp['words_with_0']}")
    eprint(f"  Words with 1 sentence:     {sp['words_with_1']}")
    eprint(f"  Words with 2 sentences:    {sp['words_with_2']}")
    eprint(f"  Words with 3+ sentences:   {sp['words_with_3_plus']}")
    eprint(f"  Overused sentences (10+x): {params['overused_sentences']}")

    section("BASELINE DEMAND (current words, no new ingestion)")
    subsection("Next 14 days projection")
    eprint(f"  {'Day':<5s} {'Date':<12s} {'Acq':>5s} {'FSRS':>5s} {'Total':>6s} {'Sent':>5s} {'Min':>6s}")
    eprint(f"  {'-'*5} {'-'*12} {'-'*5} {'-'*5} {'-'*6} {'-'*5} {'-'*6}")
    for d in baseline_demand[:14]:
        eprint(f"  {d['day']:<5d} {d['date']:<12s} {d['acq_word_reviews']:>5d} "
               f"{d['fsrs_word_reviews']:>5d} {d['total_word_reviews']:>6d} "
               f"{d['est_sentences']:>5d} {d['est_minutes']:>6.1f}")

    section("INGESTION SCENARIO SIMULATIONS")
    eprint(f"  (Reviews needed from NEW words only — add to baseline above)")
    eprint()
    eprint(f"  {'Scenario':<50s} {'Peak':>5s} {'Wk1 avg':>8s} {'Wk2 avg':>8s} {'30d avg':>8s}")
    eprint(f"  {'':50s} {'sent':>5s} {'sent/min':>8s} {'sent/min':>8s} {'sent/min':>8s}")
    eprint(f"  {'-'*50} {'-'*5} {'-'*8} {'-'*8} {'-'*8}")
    for sc in scenarios:
        eprint(f"  {sc['label']:<50s} {sc['peak_sentences']:>5d} "
               f"{sc['week1_avg_sentences']:>3.0f}/{sc['week1_avg_minutes']:>3.0f}m "
               f"{sc['week2_avg_sentences']:>3.0f}/{sc['week2_avg_minutes']:>3.0f}m "
               f"{sc['avg_sentences_30d']:>3.0f}/{sc['avg_minutes_30d']:>3.0f}m")

    section("COMBINED DAILY BUDGET (baseline + scenarios)")
    # For each scenario, combine with baseline
    baseline_w1_sent = mean(d["est_sentences"] for d in baseline_demand[:7])
    baseline_w1_min = mean(d["est_minutes"] for d in baseline_demand[:7])
    baseline_w2_sent = mean(d["est_sentences"] for d in baseline_demand[7:14])
    baseline_w2_min = mean(d["est_minutes"] for d in baseline_demand[7:14])

    eprint(f"  {'Scenario':<50s} {'Wk1':>10s} {'Wk2':>10s}")
    eprint(f"  {'':50s} {'sent/min':>10s} {'sent/min':>10s}")
    eprint(f"  {'-'*50} {'-'*10} {'-'*10}")
    eprint(f"  {'Baseline (existing words only)':<50s} "
           f"{baseline_w1_sent:>4.0f}/{baseline_w1_min:>3.0f}m "
           f"{baseline_w2_sent:>4.0f}/{baseline_w2_min:>3.0f}m")
    for sc in scenarios:
        if sc["words"] == 0:
            continue
        w1_s = baseline_w1_sent + sc["week1_avg_sentences"]
        w1_m = baseline_w1_min + sc["week1_avg_minutes"]
        w2_s = baseline_w2_sent + sc["week2_avg_sentences"]
        w2_m = baseline_w2_min + sc["week2_avg_minutes"]
        eprint(f"  {sc['label']:<50s} "
               f"{w1_s:>4.0f}/{w1_m:>3.0f}m "
               f"{w2_s:>4.0f}/{w2_m:>3.0f}m")

    if params["stuck_words"]:
        section("UN-SUSPENDED LEECHES (recent <50%, should have been auto-suspended)")
        for w in params["stuck_words"]:
            eprint(f"  {w['ar']:<15s} ({w['en']:<20s}) box={w['box']} "
                   f"recent {w['recent_correct']}/{w['recent_n']}={w['recent_acc']}%  "
                   f"cum {w['correct']}/{w['seen']} — {w['days_in']}d in acquisition")

    section("ALGORITHM RECOMMENDATIONS")
    acq_count = params["acquiring"]["total"]
    if acq_count > 40:
        grad_rate = sum(d["count"] for d in params["daily_grads_7d"]) / max(len(params["daily_grads_7d"]), 1)
        drain_days = (acq_count - 40) / max(grad_rate, 0.1)
        eprint(f"  [PIPELINE] Acquiring={acq_count} > threshold=40. Auto-intro suppressed.")
        eprint(f"    At {grad_rate:.1f} grads/day, drains to 40 in ~{drain_days:.0f} days.")
        if acq_count > 60:
            eprint(f"    Consider: raise threshold temporarily, or focus on clearing backlog")
        else:
            eprint(f"    Working as designed. Pipeline will self-clear.")

    sp = params["sentence_pool"]
    if sp["words_with_0"] > 0:
        eprint(f"  [SENTENCES] {sp['words_with_0']} active words have 0 sentences — they can't appear in review.")
        eprint(f"    Run update_material.py to backfill, or wait for next cron cycle (3h).")
    if sp["headroom"] < 30:
        eprint(f"  [POOL] Only {sp['headroom']} sentence slots left. Pool near capacity.")
        eprint(f"    Consider running rotate_stale_sentences.py to free up space.")

    if params["overused_sentences"] > 20:
        eprint(f"  [DIVERSITY] {params['overused_sentences']} sentences shown 10+ times.")
        eprint(f"    Run rotate_stale_sentences.py to retire and generate fresh material.")

    # Check if generation rate is keeping up
    daily_sent_reviews = params["daily_sentence_reviews_7d"]
    if daily_sent_reviews:
        avg_daily_consumption = mean(d["sentences"] for d in daily_sent_reviews)
        eprint(f"  [GENERATION] Avg consumption: {avg_daily_consumption:.0f} sentences/day reviewed.")
        eprint(f"    Generation rate: ~240/day (cron every 3h). ", end="")
        if avg_daily_consumption > 200:
            eprint("Consider increasing cron frequency.")
        else:
            eprint("Adequate.")

    eprint()


# ── Main ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Alif review demand analysis")
    parser.add_argument("--db", default="/app/data/alif.db")
    parser.add_argument("--days", type=int, default=30, help="Projection window")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")

    eprint(f"Alif Review Demand Analysis | {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    # Part 1: Extract parameters
    params = extract_learner_params(conn)

    # Part 2: Baseline demand (current words, no new ingestion)
    baseline = simulate_demand(conn, params, args.days)

    # Part 3: Ingestion scenarios
    scenarios = run_scenarios(params)

    # Part 4: Report
    print_report(params, baseline, scenarios)

    # JSON output
    output = {
        "params": params,
        "baseline_demand": baseline,
        "scenarios": [{k: v for k, v in sc.items() if k != "projection"}
                      for sc in scenarios],
        "_meta": {
            "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "db_path": args.db,
            "projection_days": args.days,
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))

    conn.close()


if __name__ == "__main__":
    main()
