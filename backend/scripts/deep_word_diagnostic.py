#!/usr/bin/env python3
"""
Deep Word Diagnostic — Outlier & Grey-Zone Hunter for Alif

Unlike learning_analysis.py (aggregate stats), this script hunts for
individual problematic words and algorithmic edge cases:

  1. State integrity violations (inconsistent DB state)
  2. Stuck acquirers (in acquisition too long, or 0 reviews)
  3. Encountered limbo (never promoted despite being old)
  4. FSRS anomalies (known with low stability, extreme difficulty, lapse cycles)
  5. Accuracy paradoxes (high accuracy but stuck, low accuracy but graduated fast)
  6. Review pattern outliers (response time, review gaps, same-day spam)
  7. Sentence orphans (active words with no sentences)
  8. Confused words (high was_confused rate)
  9. Rating oscillation (wildly inconsistent reviews)
 10. Collateral-only words (never primary target)
 11. Graduation anomalies (instant grads, suspiciously fast, very slow)
 12. Leech analysis (suspended, near-leech candidates)

Usage:
    python3 scripts/deep_word_diagnostic.py --db /app/data/alif.db
    python3 scripts/deep_word_diagnostic.py --db backend/alif.db  # local
"""

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from statistics import mean, median, stdev

# Mirrors FUNCTION_WORD_GLOSSES in sentence_validator.py
FUNCTION_WORDS_BARE = {
    "في", "من", "على", "الى", "إلى", "عن", "مع", "بين", "حتى",
    "منذ", "خلال", "عند", "نحو", "فوق", "تحت", "امام", "أمام",
    "وراء", "بعد", "قبل", "حول", "دون",
    "ب", "ل", "ك", "و", "ف",
    "او", "أو", "ان", "أن", "إن", "لكن", "ثم", "بل",
    "انا", "أنا", "انت", "أنت", "انتم", "أنتم", "هو", "هي",
    "هم", "هن", "نحن", "انتما", "هما",
    "هذا", "هذه", "ذلك", "تلك", "هؤلاء", "اولئك", "أولئك",
    "الذي", "التي", "الذين", "اللذان", "اللتان", "اللواتي",
    "ما", "ماذا", "لماذا", "كيف", "اين", "أين", "متى", "هل",
    "كم", "اي", "أي",
    "لا", "لم", "لن", "ليس", "ليست",
    "كان", "كانت", "يكون", "تكون", "قد", "سوف",
    "ايضا", "أيضا", "جدا", "فقط", "كل", "بعض", "كلما",
    "هنا", "هناك", "الان", "الآن", "لذلك", "هكذا", "معا",
    "اذا", "إذا", "لو", "عندما", "بينما", "حيث", "كما",
    "لان", "لأن", "كي", "لكي", "حين", "حينما",
    "لقد", "اما", "أما", "الا", "إلا", "اذن", "إذن",
    "انه", "إنه", "انها", "إنها", "مثل", "غير",
    "يوجد", "توجد",
}


# ───────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────

def parse_dt(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def header(title):
    eprint()
    eprint("=" * 76)
    eprint(f"  {title}")
    eprint("=" * 76)


def subheader(title):
    eprint()
    eprint(f"  --- {title} ---")


def word_label(row):
    """Format a word for display: arabic (gloss) [id]"""
    ar = row.get("lemma_ar", "?")
    gloss = row.get("gloss_en", "")
    lid = row.get("lemma_id", "?")
    return f"{ar} ({gloss}) [#{lid}]"


def days_ago(dt):
    if not dt:
        return None
    return (NOW - dt).total_seconds() / 86400


NOW = datetime.now()


# ───────────────────────────────────────────────────────────
# Data loading
# ───────────────────────────────────────────────────────────

def load_words(conn):
    """Load all words with knowledge + lemma info."""
    cur = conn.cursor()
    cur.execute("""
        SELECT l.lemma_id, l.lemma_ar, l.lemma_ar_bare, l.gloss_en, l.pos,
               l.frequency_rank, l.canonical_lemma_id,
               ulk.knowledge_state, ulk.times_seen, ulk.times_correct,
               ulk.total_encounters, ulk.distinct_contexts, ulk.source,
               ulk.acquisition_box, ulk.acquisition_next_due,
               ulk.acquisition_started_at, ulk.graduated_at,
               ulk.entered_acquiring_at, ulk.last_reviewed,
               ulk.fsrs_card_json, ulk.leech_suspended_at, ulk.leech_count,
               ulk.introduced_at
        FROM user_lemma_knowledge ulk
        JOIN lemmas l ON l.lemma_id = ulk.lemma_id
    """)
    cols = [d[0] for d in cur.description]
    words = []
    for row in cur.fetchall():
        d = dict(zip(cols, row))
        # parse dates
        for k in ("acquisition_next_due", "acquisition_started_at", "graduated_at",
                   "entered_acquiring_at", "last_reviewed", "leech_suspended_at", "introduced_at"):
            d[k] = parse_dt(d[k])
        # parse FSRS card — handle "null" string (JSON null stored as string in SQLite)
        card_raw = d.get("fsrs_card_json")
        parsed_card = None
        if card_raw:
            try:
                parsed = json.loads(card_raw) if isinstance(card_raw, str) else card_raw
                if isinstance(parsed, dict):
                    parsed_card = parsed
            except (json.JSONDecodeError, TypeError):
                pass
        d["fsrs_card"] = parsed_card
        d["is_function_word"] = d.get("lemma_ar_bare", "") in FUNCTION_WORDS_BARE
        words.append(d)
    return words


def load_reviews(conn):
    """Load all reviews."""
    cur = conn.cursor()
    # Check which columns exist (was_confused may not be present in older DBs)
    cur.execute("PRAGMA table_info(review_log)")
    col_names = {row[1] for row in cur.fetchall()}
    has_confused = "was_confused" in col_names

    cols_list = ["id", "lemma_id", "rating", "reviewed_at", "response_ms", "session_id",
                 "is_acquisition", "credit_type", "sentence_id", "fsrs_log_json"]
    if has_confused:
        cols_list.append("was_confused")

    cur.execute(f"SELECT {', '.join(cols_list)} FROM review_log ORDER BY reviewed_at")
    cols = [d[0] for d in cur.description]
    reviews = []
    for row in cur.fetchall():
        d = dict(zip(cols, row))
        d["reviewed_at"] = parse_dt(d["reviewed_at"])
        reviews.append(d)
    return reviews


def load_active_sentences(conn):
    """Load active sentence counts per lemma (any position, not just target)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT sw.lemma_id, COUNT(DISTINCT sw.sentence_id) as cnt
        FROM sentence_words sw
        JOIN sentences s ON s.id = sw.sentence_id
        WHERE s.is_active = 1 AND sw.lemma_id IS NOT NULL
        GROUP BY sw.lemma_id
    """)
    return dict(cur.fetchall())


# ───────────────────────────────────────────────────────────
# Diagnostic checks
# ───────────────────────────────────────────────────────────

def check_state_integrity(words):
    """Check for inconsistent DB states."""
    header("1. STATE INTEGRITY VIOLATIONS")
    issues = []

    for w in words:
        lid = w["lemma_id"]
        state = w["knowledge_state"]
        box = w["acquisition_box"]
        card = w["fsrs_card"]
        ts = w["times_seen"]
        grad = w["graduated_at"]

        # Acquiring but no box
        if state == "acquiring" and box is None:
            issues.append({"word": word_label(w), "issue": "acquiring with no box",
                           "detail": f"times_seen={ts}"})

        # Has box but not acquiring
        if box is not None and state != "acquiring":
            issues.append({"word": word_label(w), "issue": f"box={box} but state={state}",
                           "detail": f"graduated_at={grad}"})

        # Known/learning/lapsed but no FSRS card (skip function words — they don't get cards)
        if state in ("known", "learning", "lapsed") and card is None and not w.get("is_function_word"):
            issues.append({"word": word_label(w), "issue": f"state={state} but no FSRS card",
                           "detail": f"times_seen={ts}"})

        # Has FSRS card but still in acquiring
        if state == "acquiring" and card is not None:
            stab = card.get("stability") or card.get("s")
            issues.append({"word": word_label(w), "issue": "acquiring with FSRS card",
                           "detail": f"stability={stab}, box={box}"})

        # Graduated but still acquiring
        if state == "acquiring" and grad is not None:
            issues.append({"word": word_label(w), "issue": "acquiring but has graduated_at",
                           "detail": f"graduated_at={grad}"})

        # times_correct > times_seen
        if w["times_correct"] > ts:
            issues.append({"word": word_label(w), "issue": "times_correct > times_seen",
                           "detail": f"{w['times_correct']} > {ts}"})

        # Known/learning with times_seen=0 (skip function words — auto-promoted)
        if state in ("known", "learning") and ts == 0 and not w.get("is_function_word"):
            issues.append({"word": word_label(w), "issue": f"state={state} but never reviewed",
                           "detail": f"times_seen=0"})

        # FSRS stability < 0
        if card:
            stab = card.get("stability") or card.get("s")
            if stab is not None and float(stab) < 0:
                issues.append({"word": word_label(w), "issue": "negative FSRS stability",
                               "detail": f"stability={stab}"})

    if issues:
        for i in issues:
            eprint(f"  [!] {i['word']}: {i['issue']} — {i['detail']}")
    else:
        eprint("  No integrity violations found.")

    eprint(f"\n  Total violations: {len(issues)}")
    return issues


def check_stuck_acquirers(words, reviews_by_lemma):
    """Words stuck in acquisition too long or with 0 reviews."""
    header("2. STUCK ACQUIRERS")
    findings = {"zero_reviews": [], "long_acquisition": [], "overdue": []}

    for w in words:
        if w["knowledge_state"] != "acquiring":
            continue

        lid = w["lemma_id"]
        entered = w["entered_acquiring_at"]
        ts = w["times_seen"]
        box = w["acquisition_box"]
        due = w["acquisition_next_due"]

        # Zero reviews — box-1 starvation
        if ts == 0:
            days_in = days_ago(entered) if entered else None
            findings["zero_reviews"].append({
                "word": word_label(w), "days_in_acquisition": round(days_in, 1) if days_in else "?",
                "box": box, "source": w["source"],
            })

        # In acquisition > 14 days
        if entered:
            d = days_ago(entered)
            if d and d > 14:
                acc = w["times_correct"] / ts * 100 if ts > 0 else 0
                findings["long_acquisition"].append({
                    "word": word_label(w), "days_in": round(d, 1),
                    "box": box, "times_seen": ts, "accuracy": f"{acc:.0f}%",
                })

        # Overdue by > 3 days
        if due:
            overdue_days = days_ago(due)
            if overdue_days and overdue_days > 3:
                findings["overdue"].append({
                    "word": word_label(w), "overdue_days": round(overdue_days, 1),
                    "box": box, "times_seen": ts,
                })

    subheader("Zero-review acquirers (box-1 starvation)")
    if findings["zero_reviews"]:
        for f in sorted(findings["zero_reviews"], key=lambda x: x.get("days_in_acquisition", 0) or 0, reverse=True):
            eprint(f"  {f['word']}  in acquisition {f['days_in_acquisition']}d  box={f['box']}  src={f['source']}")
    else:
        eprint("  None found.")
    eprint(f"  Count: {len(findings['zero_reviews'])}")

    subheader("Long acquisition (>14 days)")
    if findings["long_acquisition"]:
        for f in sorted(findings["long_acquisition"], key=lambda x: x["days_in"], reverse=True):
            eprint(f"  {f['word']}  {f['days_in']}d  box={f['box']}  seen={f['times_seen']}  acc={f['accuracy']}")
    else:
        eprint("  None found.")
    eprint(f"  Count: {len(findings['long_acquisition'])}")

    subheader("Overdue by >3 days")
    if findings["overdue"]:
        for f in sorted(findings["overdue"], key=lambda x: x["overdue_days"], reverse=True)[:15]:
            eprint(f"  {f['word']}  overdue {f['overdue_days']}d  box={f['box']}  seen={f['times_seen']}")
    else:
        eprint("  None found.")
    eprint(f"  Count: {len(findings['overdue'])}")

    return findings


def check_encountered_limbo(words):
    """Words stuck in 'encountered' state for too long."""
    header("3. ENCOUNTERED LIMBO")
    limbo = []

    for w in words:
        if w["knowledge_state"] != "encountered":
            continue

        introduced = w["introduced_at"]
        if introduced:
            d = days_ago(introduced)
            if d and d > 14:
                limbo.append({
                    "word": word_label(w), "days_encountered": round(d, 1),
                    "encounters": w["total_encounters"], "source": w["source"],
                })

    limbo.sort(key=lambda x: x["days_encountered"], reverse=True)

    if limbo:
        for f in limbo[:20]:
            eprint(f"  {f['word']}  encountered {f['days_encountered']}d ago  "
                   f"encounters={f['encounters']}  src={f['source']}")
    else:
        eprint("  No old encountered words (all <14 days or none).")

    eprint(f"\n  Total encountered >14d: {len(limbo)}")
    return limbo


def check_fsrs_anomalies(words):
    """FSRS edge cases: known with low stability, high difficulty, lapse cycles."""
    header("4. FSRS ANOMALIES")
    findings = {"low_stability_known": [], "high_difficulty": [],
                "many_lapses": [], "learning_high_stability": []}

    for w in words:
        card = w["fsrs_card"]
        if not card:
            continue

        state = w["knowledge_state"]
        stab = card.get("stability") or card.get("s")
        diff = card.get("difficulty") or card.get("d")
        lapses = card.get("lapses") or card.get("l") or 0

        if stab is not None:
            stab = float(stab)
        if diff is not None:
            diff = float(diff)

        # Known but stability < 3 days — about to lapse?
        if state == "known" and stab is not None and stab < 3:
            findings["low_stability_known"].append({
                "word": word_label(w), "stability": round(stab, 2),
                "difficulty": round(diff, 2) if diff else "?",
                "times_seen": w["times_seen"], "lapses": lapses,
            })

        # Very high difficulty (>8)
        if diff is not None and diff > 8:
            findings["high_difficulty"].append({
                "word": word_label(w), "difficulty": round(diff, 2),
                "stability": round(stab, 2) if stab else "?",
                "state": state, "times_seen": w["times_seen"],
            })

        # Many lapses (>3) — potential leech not caught
        if lapses > 3:
            findings["many_lapses"].append({
                "word": word_label(w), "lapses": lapses,
                "stability": round(stab, 2) if stab else "?",
                "state": state, "leech_count": w["leech_count"],
            })

        # Learning with very high stability (>30d) — should be "known"?
        if state == "learning" and stab is not None and stab > 30:
            findings["learning_high_stability"].append({
                "word": word_label(w), "stability": round(stab, 2),
                "state": state, "times_seen": w["times_seen"],
            })

    for key, label in [
        ("low_stability_known", "Known with stability < 3 days (about to lapse?)"),
        ("high_difficulty", "Very high difficulty (>8)"),
        ("many_lapses", "Many lapses (>3, potential undetected leech)"),
        ("learning_high_stability", "Learning with stability >30d (should be known?)"),
    ]:
        subheader(label)
        items = findings[key]
        if items:
            for f in items[:15]:
                parts = [f"{k}={v}" for k, v in f.items() if k != "word"]
                eprint(f"  {f['word']}  {', '.join(parts)}")
        else:
            eprint("  None found.")
        eprint(f"  Count: {len(items)}")

    return findings


def check_accuracy_paradoxes(words, reviews_by_lemma):
    """High accuracy but stuck, or low accuracy but graduated fast."""
    header("5. ACCURACY PARADOXES")
    findings = {"high_acc_stuck": [], "low_acc_fast_grad": [], "perfect_but_acquiring": []}

    for w in words:
        ts = w["times_seen"]
        tc = w["times_correct"]
        state = w["knowledge_state"]
        acc = tc / ts if ts > 0 else 0

        # High accuracy (>= 90%) but still acquiring with many reviews
        if state == "acquiring" and ts >= 5 and acc >= 0.9:
            findings["high_acc_stuck"].append({
                "word": word_label(w), "accuracy": f"{acc*100:.0f}%",
                "times_seen": ts, "box": w["acquisition_box"],
                "entered": w["entered_acquiring_at"],
            })

        # 100% accuracy and still acquiring
        if state == "acquiring" and ts >= 3 and acc == 1.0:
            findings["perfect_but_acquiring"].append({
                "word": word_label(w), "times_seen": ts,
                "box": w["acquisition_box"],
                "days_in": round(days_ago(w["entered_acquiring_at"]), 1)
                           if w["entered_acquiring_at"] else "?",
            })

        # Low accuracy but graduated fast (< 2 days)
        if w["graduated_at"] and w["entered_acquiring_at"]:
            grad_hours = (w["graduated_at"] - w["entered_acquiring_at"]).total_seconds() / 3600
            if grad_hours < 48 and ts >= 3 and acc < 0.6:
                findings["low_acc_fast_grad"].append({
                    "word": word_label(w), "accuracy": f"{acc*100:.0f}%",
                    "times_seen": ts, "grad_hours": round(grad_hours, 1),
                    "state": state,
                })

    for key, label in [
        ("perfect_but_acquiring", "100% accuracy but still acquiring (>=3 reviews)"),
        ("high_acc_stuck", ">=90% accuracy, 5+ reviews, still acquiring"),
        ("low_acc_fast_grad", "<60% accuracy but graduated in <48h"),
    ]:
        subheader(label)
        items = findings[key]
        if items:
            for f in items:
                parts = [f"{k}={v}" for k, v in f.items() if k != "word"]
                eprint(f"  {f['word']}  {', '.join(parts)}")
        else:
            eprint("  None found.")
        eprint(f"  Count: {len(items)}")

    return findings


def check_review_patterns(reviews_by_lemma, words_dict):
    """Response time outliers, review gaps, same-day spam."""
    header("6. REVIEW PATTERN OUTLIERS")
    findings = {"fast_responses": [], "slow_responses": [],
                "long_gaps_acquiring": [], "same_day_spam": []}

    for lid, revs in reviews_by_lemma.items():
        if lid not in words_dict:
            continue
        w = words_dict[lid]

        # Response time analysis
        times = [r["response_ms"] for r in revs if r.get("response_ms") and r["response_ms"] > 0]

        # Very fast responses (< 400ms, >= 5 such reviews) — possible tap-through?
        fast_count = sum(1 for t in times if t < 400)
        if fast_count >= 5:
            findings["fast_responses"].append({
                "word": word_label(w), "fast_reviews": fast_count,
                "total_reviews": len(revs),
                "median_ms": int(median(times)) if times else 0,
            })

        # Very slow responses (> 30s median)
        if times and median(times) > 30000:
            findings["slow_responses"].append({
                "word": word_label(w), "median_ms": int(median(times)),
                "total_reviews": len(revs), "state": w["knowledge_state"],
            })

        # Review gaps for acquiring words
        if w["knowledge_state"] == "acquiring" and len(revs) >= 2:
            sorted_revs = sorted(revs, key=lambda r: r["reviewed_at"] or datetime.min)
            for i in range(1, len(sorted_revs)):
                if sorted_revs[i]["reviewed_at"] and sorted_revs[i-1]["reviewed_at"]:
                    gap = (sorted_revs[i]["reviewed_at"] - sorted_revs[i-1]["reviewed_at"]).total_seconds() / 86400
                    if gap > 7:
                        findings["long_gaps_acquiring"].append({
                            "word": word_label(w), "gap_days": round(gap, 1),
                            "between_review": f"#{i} and #{i+1} of {len(revs)}",
                            "box": w["acquisition_box"],
                        })

        # Same-day review spam (> 5 reviews of same word in one day)
        by_day = Counter()
        for r in revs:
            if r["reviewed_at"]:
                by_day[r["reviewed_at"].date()] += 1
        for day, cnt in by_day.items():
            if cnt > 5:
                findings["same_day_spam"].append({
                    "word": word_label(w), "date": str(day),
                    "reviews_that_day": cnt,
                })

    for key, label in [
        ("fast_responses", "Suspiciously fast responses (<400ms, 5+ times)"),
        ("slow_responses", "Very slow median response (>30s)"),
        ("long_gaps_acquiring", "Long review gaps while acquiring (>7 days)"),
        ("same_day_spam", "Same-day review spam (>5 reviews of one word)"),
    ]:
        subheader(label)
        items = findings[key]
        if items:
            for f in items[:15]:
                parts = [f"{k}={v}" for k, v in f.items() if k != "word"]
                eprint(f"  {f['word']}  {', '.join(parts)}")
        else:
            eprint("  None found.")
        eprint(f"  Count: {len(items)}")

    return findings


def check_sentence_orphans(words, active_sentences):
    """Active words that don't appear in ANY active sentence (not even as scaffold)."""
    header("7. SENTENCE ORPHANS (words not in any active sentence)")
    orphans = []

    for w in words:
        state = w["knowledge_state"]
        if state not in ("acquiring", "learning", "lapsed"):
            continue  # known words don't need sentences proactively

        lid = w["lemma_id"]
        sentence_count = active_sentences.get(lid, 0)
        if sentence_count == 0:
            orphans.append({
                "word": word_label(w), "state": state,
                "times_seen": w["times_seen"], "source": w["source"],
            })

    orphans.sort(key=lambda x: x["state"])

    if orphans:
        by_state = Counter(o["state"] for o in orphans)
        for state, cnt in sorted(by_state.items()):
            eprint(f"  {state}: {cnt} words without active sentences")
        eprint()
        for o in orphans[:20]:
            eprint(f"  {o['word']}  state={o['state']}  seen={o['times_seen']}  src={o['source']}")
        if len(orphans) > 20:
            eprint(f"  ... and {len(orphans) - 20} more")
    else:
        eprint("  No sentence orphans found.")

    eprint(f"\n  Total orphans: {len(orphans)}")
    return orphans


def check_confused_words(reviews_by_lemma, words_dict):
    """Words with high confusion rate."""
    header("8. MOST CONFUSED WORDS")
    confused = []

    for lid, revs in reviews_by_lemma.items():
        if lid not in words_dict:
            continue
        w = words_dict[lid]
        total = len(revs)
        confused_count = sum(1 for r in revs if r.get("was_confused"))
        if total >= 3 and confused_count >= 2:
            confused.append({
                "word": word_label(w), "confused_rate": f"{confused_count/total*100:.0f}%",
                "confused_count": confused_count, "total_reviews": total,
                "state": w["knowledge_state"],
                "accuracy": f"{w['times_correct']/w['times_seen']*100:.0f}%"
                            if w["times_seen"] > 0 else "0%",
            })

    confused.sort(key=lambda x: x["confused_count"], reverse=True)

    if confused:
        for f in confused[:15]:
            eprint(f"  {f['word']}  confused {f['confused_count']}/{f['total_reviews']} "
                   f"({f['confused_rate']})  state={f['state']}  acc={f['accuracy']}")
    else:
        eprint("  No significantly confused words found.")

    eprint(f"\n  Words with 2+ confusions: {len(confused)}")
    return confused


def check_rating_oscillation(reviews_by_lemma, words_dict):
    """Words where ratings swing wildly between sessions."""
    header("9. RATING OSCILLATION (inconsistent reviews)")
    oscillators = []

    for lid, revs in reviews_by_lemma.items():
        if lid not in words_dict or len(revs) < 5:
            continue
        w = words_dict[lid]

        # Sort by time
        sorted_revs = sorted(revs, key=lambda r: r["reviewed_at"] or datetime.min)
        ratings = [r["rating"] for r in sorted_revs if r.get("rating")]
        if len(ratings) < 5:
            continue

        # Count direction changes (1→4→1→4 = 3 changes)
        changes = 0
        big_swings = 0
        for i in range(1, len(ratings)):
            diff = abs(ratings[i] - ratings[i-1])
            if diff >= 2:
                changes += 1
            if diff >= 3:
                big_swings += 1

        # Flag if >40% of transitions are big changes
        change_rate = changes / (len(ratings) - 1)
        if change_rate > 0.4 and changes >= 3:
            oscillators.append({
                "word": word_label(w), "reviews": len(ratings),
                "big_changes": changes, "change_rate": f"{change_rate*100:.0f}%",
                "recent_5": "→".join(str(r) for r in ratings[-5:]),
                "state": w["knowledge_state"],
            })

    oscillators.sort(key=lambda x: x["big_changes"], reverse=True)

    if oscillators:
        for f in oscillators[:15]:
            eprint(f"  {f['word']}  {f['big_changes']} swings in {f['reviews']} reviews "
                   f"({f['change_rate']})  recent: {f['recent_5']}  state={f['state']}")
    else:
        eprint("  No significant oscillators found.")

    eprint(f"\n  Oscillating words: {len(oscillators)}")
    return oscillators


def check_collateral_only(reviews_by_lemma, words_dict):
    """Words that only receive collateral credit, never primary."""
    header("10. COLLATERAL-ONLY WORDS")
    collateral_only = []

    for lid, revs in reviews_by_lemma.items():
        if lid not in words_dict:
            continue
        w = words_dict[lid]
        if w["knowledge_state"] in ("new", "encountered"):
            continue
        if w["times_seen"] < 3:
            continue

        primary_count = sum(1 for r in revs if r.get("credit_type") == "primary")
        collateral_count = sum(1 for r in revs if r.get("credit_type") == "collateral")
        null_count = sum(1 for r in revs if not r.get("credit_type"))

        if primary_count == 0 and collateral_count > 0:
            collateral_only.append({
                "word": word_label(w), "collateral_reviews": collateral_count,
                "null_reviews": null_count, "total": len(revs),
                "state": w["knowledge_state"],
            })

    collateral_only.sort(key=lambda x: x["collateral_reviews"], reverse=True)

    if collateral_only:
        for f in collateral_only[:15]:
            eprint(f"  {f['word']}  {f['collateral_reviews']} collateral, 0 primary, "
                   f"{f['null_reviews']} null  state={f['state']}")
    else:
        eprint("  No collateral-only words found.")

    eprint(f"\n  Collateral-only words: {len(collateral_only)}")
    return collateral_only


def check_graduation_anomalies(words, reviews_by_lemma):
    """Analyze graduation timing: instant, fast, slow."""
    header("11. GRADUATION ANALYSIS")
    findings = {"instant": [], "very_fast": [], "very_slow": [], "no_entered_at": []}

    graduated = [w for w in words if w["graduated_at"] is not None]

    durations = []
    for w in graduated:
        entered = w["entered_acquiring_at"]
        grad = w["graduated_at"]

        if not entered:
            findings["no_entered_at"].append({
                "word": word_label(w), "graduated_at": str(grad),
                "times_seen": w["times_seen"],
            })
            continue

        hours = (grad - entered).total_seconds() / 3600
        durations.append(hours)

        # Instant graduation (< 1 minute)
        if hours < 1/60:
            findings["instant"].append({
                "word": word_label(w), "seconds": round(hours * 3600, 1),
                "times_seen": w["times_seen"],
            })

        # Very fast (< 4 hours) with 1 review — the fast-track tier
        elif hours < 4 and w["times_seen"] == 1:
            findings["very_fast"].append({
                "word": word_label(w), "hours": round(hours, 1),
                "accuracy": f"{w['times_correct']/w['times_seen']*100:.0f}%"
                            if w["times_seen"] > 0 else "?",
            })

        # Very slow (> 30 days)
        elif hours > 30 * 24:
            findings["very_slow"].append({
                "word": word_label(w), "days": round(hours / 24, 1),
                "times_seen": w["times_seen"],
                "accuracy": f"{w['times_correct']/w['times_seen']*100:.0f}%"
                            if w["times_seen"] > 0 else "?",
            })

    # Stats
    subheader("Graduation timing stats")
    if durations:
        eprint(f"  Total graduated: {len(graduated)}")
        eprint(f"  Median: {median(durations):.1f}h ({median(durations)/24:.1f}d)")
        eprint(f"  Mean: {mean(durations):.1f}h ({mean(durations)/24:.1f}d)")
        if len(durations) > 1:
            eprint(f"  Stdev: {stdev(durations):.1f}h")
        eprint(f"  Min: {min(durations):.1f}h  Max: {max(durations):.1f}h")

        # Distribution
        subheader("Graduation time distribution")
        buckets = Counter()
        for h in durations:
            if h < 1:
                buckets["<1h (instant)"] += 1
            elif h < 24:
                buckets["1-24h (same day)"] += 1
            elif h < 72:
                buckets["1-3 days"] += 1
            elif h < 168:
                buckets["3-7 days"] += 1
            elif h < 336:
                buckets["1-2 weeks"] += 1
            else:
                buckets["2+ weeks"] += 1

        total_g = len(durations)
        for bucket in ["<1h (instant)", "1-24h (same day)", "1-3 days",
                        "3-7 days", "1-2 weeks", "2+ weeks"]:
            cnt = buckets.get(bucket, 0)
            pct = cnt / total_g * 100 if total_g else 0
            bar = "#" * int(pct / 2)
            eprint(f"  {bucket:<22s}  {cnt:>4d}  ({pct:>5.1f}%)  {bar}")

    for key, label in [
        ("instant", "Instant graduations (<1 min)"),
        ("very_fast", "Fast-track graduations (<4h, 1 review)"),
        ("very_slow", "Very slow graduations (>30 days)"),
        ("no_entered_at", "Graduated but missing entered_acquiring_at"),
    ]:
        subheader(label)
        items = findings[key]
        if items:
            for f in items[:10]:
                parts = [f"{k}={v}" for k, v in f.items() if k != "word"]
                eprint(f"  {f['word']}  {', '.join(parts)}")
        else:
            eprint("  None found.")
        eprint(f"  Count: {len(items)}")

    return findings


def check_leech_analysis(words, reviews_by_lemma):
    """Leech detection: suspended leeches + near-leech candidates."""
    header("12. LEECH ANALYSIS")
    findings = {"suspended_leeches": [], "near_leeches": []}

    for w in words:
        ts = w["times_seen"]
        tc = w["times_correct"]
        acc = tc / ts if ts > 0 else 0
        state = w["knowledge_state"]

        # Currently suspended as leech
        if state == "suspended" and w["leech_suspended_at"]:
            findings["suspended_leeches"].append({
                "word": word_label(w), "leech_count": w["leech_count"],
                "times_seen": ts, "accuracy": f"{acc*100:.0f}%",
                "suspended_ago": f"{days_ago(w['leech_suspended_at']):.0f}d",
            })

        # Near-leech: low accuracy, many reviews, not yet suspended
        if state in ("acquiring", "learning", "lapsed") and ts >= 6 and acc < 0.5:
            lid = w["lemma_id"]
            recent_revs = reviews_by_lemma.get(lid, [])
            recent_ratings = [r["rating"] for r in sorted(recent_revs,
                              key=lambda r: r["reviewed_at"] or datetime.min)[-5:]
                              if r.get("rating")]
            recent_acc = sum(1 for r in recent_ratings if r >= 3) / len(recent_ratings) if recent_ratings else 0

            findings["near_leeches"].append({
                "word": word_label(w), "state": state,
                "times_seen": ts, "overall_acc": f"{acc*100:.0f}%",
                "recent_5_acc": f"{recent_acc*100:.0f}%",
                "recent_5": "→".join(str(r) for r in recent_ratings),
            })

    for key, label in [
        ("suspended_leeches", "Currently suspended leeches"),
        ("near_leeches", "Near-leech candidates (acc <50%, 6+ reviews)"),
    ]:
        subheader(label)
        items = findings[key]
        if items:
            for f in items:
                parts = [f"{k}={v}" for k, v in f.items() if k != "word"]
                eprint(f"  {f['word']}  {', '.join(parts)}")
        else:
            eprint("  None found.")
        eprint(f"  Count: {len(items)}")

    return findings


def check_review_recency(words):
    """Words that should be actively reviewed but haven't been seen recently."""
    header("13. REVIEW RECENCY — STALE ACTIVE WORDS")
    stale = []

    for w in words:
        state = w["knowledge_state"]
        if state not in ("acquiring", "learning", "known", "lapsed"):
            continue

        last = w["last_reviewed"]
        if not last:
            if w["times_seen"] > 0:
                stale.append({
                    "word": word_label(w), "state": state,
                    "times_seen": w["times_seen"],
                    "staleness": "never (but has reviews!)",
                })
            continue

        d = days_ago(last)
        threshold = {
            "acquiring": 7,   # should be reviewed within a week
            "learning": 14,   # within 2 weeks
            "known": 60,      # within 2 months (unless stability is high)
            "lapsed": 7,      # should be reviewed soon
        }.get(state, 30)

        # For known words, adjust threshold based on FSRS stability
        if state == "known" and w["fsrs_card"]:
            stab = w["fsrs_card"].get("stability") or w["fsrs_card"].get("s")
            if stab:
                threshold = max(threshold, float(stab) * 1.5)

        if d and d > threshold:
            stale.append({
                "word": word_label(w), "state": state,
                "last_reviewed_days_ago": round(d, 1),
                "threshold": threshold,
                "times_seen": w["times_seen"],
            })

    stale.sort(key=lambda x: x.get("last_reviewed_days_ago", 9999), reverse=True)

    if stale:
        by_state = Counter(s["state"] for s in stale)
        for state, cnt in sorted(by_state.items()):
            eprint(f"  {state}: {cnt} stale words")
        eprint()
        for f in stale[:20]:
            eprint(f"  {f['word']}  state={f['state']}  "
                   f"last seen {f.get('last_reviewed_days_ago', '?')}d ago  "
                   f"(threshold={f.get('threshold', '?')}d)")
        if len(stale) > 20:
            eprint(f"  ... and {len(stale) - 20} more")
    else:
        eprint("  No stale active words found.")

    eprint(f"\n  Total stale: {len(stale)}")
    return stale


def check_variant_issues(words, conn):
    """Words that are variants of canonical forms — check for split scheduling."""
    header("14. VARIANT & DUPLICATE ANALYSIS")
    cur = conn.cursor()

    # Find words with canonical_lemma_id set
    cur.execute("""
        SELECT l.lemma_id, l.lemma_ar, l.gloss_en, l.canonical_lemma_id,
               cl.lemma_ar as canonical_ar, cl.gloss_en as canonical_gloss,
               ulk.knowledge_state, ulk.times_seen,
               ulk2.knowledge_state as canonical_state, ulk2.times_seen as canonical_seen
        FROM lemmas l
        JOIN lemmas cl ON l.canonical_lemma_id = cl.lemma_id
        LEFT JOIN user_lemma_knowledge ulk ON l.lemma_id = ulk.lemma_id
        LEFT JOIN user_lemma_knowledge ulk2 ON cl.lemma_id = ulk2.lemma_id
        WHERE l.canonical_lemma_id IS NOT NULL
    """)

    variants = []
    split_scheduling = []
    for row in cur.fetchall():
        lid, ar, gloss, can_id, can_ar, can_gloss, state, seen, can_state, can_seen = row
        variants.append({
            "variant": f"{ar} ({gloss}) [#{lid}]",
            "canonical": f"{can_ar} ({can_gloss}) [#{can_id}]",
            "variant_state": state, "variant_seen": seen or 0,
            "canonical_state": can_state, "canonical_seen": can_seen or 0,
        })

        # Flag if both variant and canonical are being reviewed independently
        active_states = {"acquiring", "learning", "known", "lapsed"}
        if state in active_states and can_state in active_states:
            split_scheduling.append(variants[-1])

    eprint(f"  Total variants: {len(variants)}")

    if split_scheduling:
        subheader("SPLIT SCHEDULING — both variant and canonical being reviewed!")
        for v in split_scheduling:
            eprint(f"  Variant: {v['variant']}  state={v['variant_state']}  seen={v['variant_seen']}")
            eprint(f"    → Canonical: {v['canonical']}  state={v['canonical_state']}  seen={v['canonical_seen']}")
    else:
        subheader("Split scheduling")
        eprint("  No split scheduling issues found.")

    return {"variants": len(variants), "split_scheduling": split_scheduling}


def summary_stats(words, reviews_by_lemma):
    """Quick summary to put everything in context."""
    header("SUMMARY CONTEXT")

    states = Counter(w["knowledge_state"] for w in words)
    total = len(words)
    total_reviews = sum(len(revs) for revs in reviews_by_lemma.values())

    eprint(f"  Total words in system: {total}")
    for state in ["known", "learning", "acquiring", "encountered", "lapsed", "suspended", "new"]:
        cnt = states.get(state, 0)
        eprint(f"    {state:<15s} {cnt:>5d}  ({cnt/total*100:.1f}%)")
    eprint(f"  Total reviews: {total_reviews}")

    # Active vocabulary (acquiring + learning + known)
    active = states.get("acquiring", 0) + states.get("learning", 0) + states.get("known", 0)
    eprint(f"  Active vocabulary: {active}")

    # Average reviews per active word
    active_words = [w for w in words if w["knowledge_state"] in ("acquiring", "learning", "known")]
    if active_words:
        avg_reviews = mean(w["times_seen"] for w in active_words)
        eprint(f"  Avg reviews per active word: {avg_reviews:.1f}")


# ───────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Deep word diagnostic for Alif")
    parser.add_argument("--db", default="/app/data/alif.db", help="Path to SQLite database")
    parser.add_argument("--json", default=None, help="Write JSON results to file")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")

    eprint(f"Deep Word Diagnostic  |  DB: {args.db}  |  {NOW.strftime('%Y-%m-%d %H:%M UTC')}")
    eprint("─" * 76)

    # Load data
    eprint("  Loading data...")
    words = load_words(conn)
    reviews = load_reviews(conn)
    active_sentences = load_active_sentences(conn)

    # Index reviews by lemma
    reviews_by_lemma = defaultdict(list)
    for r in reviews:
        reviews_by_lemma[r["lemma_id"]].append(r)

    words_dict = {w["lemma_id"]: w for w in words}

    eprint(f"  Loaded {len(words)} words, {len(reviews)} reviews, "
           f"{len(active_sentences)} words with active sentences")

    # Run all checks
    results = {}
    summary_stats(words, reviews_by_lemma)
    results["state_integrity"] = check_state_integrity(words)
    results["stuck_acquirers"] = check_stuck_acquirers(words, reviews_by_lemma)
    results["encountered_limbo"] = check_encountered_limbo(words)
    results["fsrs_anomalies"] = check_fsrs_anomalies(words)
    results["accuracy_paradoxes"] = check_accuracy_paradoxes(words, reviews_by_lemma)
    results["review_patterns"] = check_review_patterns(reviews_by_lemma, words_dict)
    results["sentence_orphans"] = check_sentence_orphans(words, active_sentences)
    results["confused_words"] = check_confused_words(reviews_by_lemma, words_dict)
    results["rating_oscillation"] = check_rating_oscillation(reviews_by_lemma, words_dict)
    results["collateral_only"] = check_collateral_only(reviews_by_lemma, words_dict)
    results["graduation_anomalies"] = check_graduation_anomalies(words, reviews_by_lemma)
    results["leech_analysis"] = check_leech_analysis(words, reviews_by_lemma)
    results["review_recency"] = check_review_recency(words)
    results["variant_issues"] = check_variant_issues(words, conn)

    # Count total issues
    header("ISSUE TOTALS")
    total_issues = 0
    for key, val in results.items():
        if isinstance(val, list):
            cnt = len(val)
        elif isinstance(val, dict):
            cnt = sum(len(v) if isinstance(v, list) else 0 for v in val.values())
        else:
            cnt = 0
        if cnt > 0:
            eprint(f"  {key:<30s} {cnt:>5d} findings")
            total_issues += cnt

    eprint(f"\n  TOTAL FINDINGS: {total_issues}")

    conn.close()

    # Optional JSON output
    if args.json:
        with open(args.json, "w") as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)
        eprint(f"\n  JSON written to {args.json}")


if __name__ == "__main__":
    main()
