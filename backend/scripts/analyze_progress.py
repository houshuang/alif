"""Analyze learner progress from production database.

Usage:
    python3 scripts/analyze_progress.py          # Today's analysis
    python3 scripts/analyze_progress.py --days 7  # Last 7 days
"""
import argparse
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict
from sqlalchemy import func
from app.database import SessionLocal
from app.models import (
    ReviewLog, SentenceReviewLog, Sentence, Lemma, UserLemmaKnowledge,
)


def main():
    parser = argparse.ArgumentParser(description="Analyze learner progress")
    parser.add_argument("--days", type=int, default=1, help="Analysis window in days (default: 1 = today)")
    args = parser.parse_args()

    db = SessionLocal()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = now - timedelta(days=args.days) if args.days > 1 else today_start

    # --- Knowledge States ---
    print("=" * 60)
    print("KNOWLEDGE STATES")
    print("=" * 60)
    states = (
        db.query(UserLemmaKnowledge.knowledge_state, func.count())
        .group_by(UserLemmaKnowledge.knowledge_state)
        .all()
    )
    for state, count in sorted(states, key=lambda x: -x[1]):
        print(f"  {state}: {count}")

    # --- Acquisition Pipeline ---
    print(f"\n{'=' * 60}")
    print("ACQUISITION PIPELINE")
    print("=" * 60)
    acq_words = (
        db.query(UserLemmaKnowledge, Lemma)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(UserLemmaKnowledge.knowledge_state == "acquiring")
        .order_by(UserLemmaKnowledge.acquisition_box, Lemma.lemma_ar)
        .all()
    )
    boxes = defaultdict(list)
    for ulk, lem in acq_words:
        box = ulk.acquisition_box or 1
        acc = f"{ulk.times_correct}/{ulk.times_seen}" if ulk.times_seen else "0/0"
        boxes[box].append(f"  {lem.lemma_ar} ({lem.gloss_en}) {acc}")
    for box in [1, 2, 3]:
        interval = {1: "4h", 2: "1d", 3: "3d"}.get(box, "?")
        print(f"\nBox {box} ({interval}) — {len(boxes[box])} words:")
        for line in boxes[box]:
            print(line)

    # --- Recent Graduations ---
    print(f"\n{'=' * 60}")
    print("RECENT GRADUATIONS (7 days)")
    print("=" * 60)
    grad_cutoff = now - timedelta(days=7)
    grads = (
        db.query(UserLemmaKnowledge, Lemma)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            UserLemmaKnowledge.graduated_at >= grad_cutoff,
            UserLemmaKnowledge.graduated_at.isnot(None),
        )
        .order_by(UserLemmaKnowledge.graduated_at.desc())
        .all()
    )
    for ulk, lem in grads:
        dt = str(ulk.graduated_at)[:16] if ulk.graduated_at else "?"
        print(f"  {dt} {lem.lemma_ar} ({lem.gloss_en}) seen={ulk.times_seen} state={ulk.knowledge_state}")
    if not grads:
        print("  (none)")

    # --- Session Breakdown ---
    print(f"\n{'=' * 60}")
    period_label = "TODAY" if args.days <= 1 else f"LAST {args.days} DAYS"
    print(f"SESSION BREAKDOWN ({period_label})")
    print("=" * 60)
    srevs = (
        db.query(SentenceReviewLog)
        .filter(SentenceReviewLog.reviewed_at >= cutoff)
        .order_by(SentenceReviewLog.reviewed_at)
        .all()
    )
    sessions = defaultdict(list)
    for sr in srevs:
        sid = sr.session_id or "unknown"
        sessions[sid].append(sr)

    for sid, srs in sessions.items():
        comps = Counter(sr.comprehension for sr in srs)
        times = [sr.response_ms for sr in srs if sr.response_ms and sr.response_ms < 300000]
        avg_time = sum(times) / len(times) / 1000 if times else 0
        total_time = sum(times) / 1000 / 60 if times else 0
        print(f"  Session {sid[:8]}...: {len(srs)} sentences, "
              f"understood={comps.get('understood', 0)} partial={comps.get('partial', 0)} "
              f"no_idea={comps.get('no_idea', 0)}, avg={avg_time:.0f}s, total={total_time:.1f}min")

    if not sessions:
        print("  (no sessions)")

    # --- Comprehension by Word Count ---
    print(f"\n{'=' * 60}")
    print(f"COMPREHENSION BY WORD COUNT ({period_label})")
    print("=" * 60)
    by_wc = defaultdict(lambda: {"total": 0, "understood": 0, "partial": 0, "no_idea": 0})
    for sr in srevs:
        s = db.get(Sentence, sr.sentence_id) if sr.sentence_id else None
        wc = len((s.arabic_text or "").split()) if s else 0
        by_wc[wc]["total"] += 1
        comp = sr.comprehension or "unknown"
        if comp in by_wc[wc]:
            by_wc[wc][comp] += 1

    for wc in sorted(by_wc.keys()):
        d = by_wc[wc]
        pct_u = d["understood"] / d["total"] * 100 if d["total"] else 0
        print(f"  {wc} words: {d['total']} reviews — "
              f"understood={d['understood']} ({pct_u:.0f}%) partial={d['partial']} no_idea={d['no_idea']}")

    # --- Rating Distribution ---
    print(f"\n{'=' * 60}")
    print(f"RATING DISTRIBUTION ({period_label})")
    print("=" * 60)
    reviews = (
        db.query(ReviewLog)
        .filter(ReviewLog.reviewed_at >= cutoff)
        .all()
    )
    acq_ratings = [r.rating for r in reviews if r.is_acquisition]
    fsrs_ratings = [r.rating for r in reviews if not r.is_acquisition]
    print(f"  Acquisition ({len(acq_ratings)}): {dict(sorted(Counter(acq_ratings).items()))}")
    print(f"  FSRS ({len(fsrs_ratings)}): {dict(sorted(Counter(fsrs_ratings).items()))}")

    # --- Response Time ---
    print(f"\n{'=' * 60}")
    print(f"RESPONSE TIME ({period_label})")
    print("=" * 60)
    for comp_type in ["understood", "partial", "no_idea"]:
        times = sorted([
            sr.response_ms for sr in srevs
            if sr.comprehension == comp_type and sr.response_ms and sr.response_ms < 300000
        ])
        if times:
            median = times[len(times) // 2]
            print(f"  {comp_type}: n={len(times)}, median={median/1000:.1f}s, "
                  f"mean={sum(times)/len(times)/1000:.1f}s, "
                  f"min={min(times)/1000:.1f}s, max={max(times)/1000:.1f}s")

    # --- Leech/Struggling ---
    print(f"\n{'=' * 60}")
    print("STRUGGLING WORDS (seen >= 5, accuracy < 60%)")
    print("=" * 60)
    struggling = (
        db.query(UserLemmaKnowledge, Lemma)
        .join(Lemma, Lemma.lemma_id == UserLemmaKnowledge.lemma_id)
        .filter(
            UserLemmaKnowledge.times_seen >= 5,
            UserLemmaKnowledge.knowledge_state.in_(["learning", "known", "lapsed", "acquiring"]),
        )
        .all()
    )
    found = False
    for ulk, lem in struggling:
        if ulk.times_seen and ulk.times_correct is not None:
            acc = ulk.times_correct / ulk.times_seen
            if acc < 0.6:
                found = True
                print(f"  {lem.lemma_ar} ({lem.gloss_en}): {ulk.times_correct}/{ulk.times_seen} = "
                      f"{acc:.0%} state={ulk.knowledge_state}")
    if not found:
        print("  (none)")

    # --- Yesterday vs Today ---
    if args.days <= 1:
        print(f"\n{'=' * 60}")
        print("YESTERDAY vs TODAY")
        print("=" * 60)
        yesterday_start = today_start - timedelta(days=1)
        y_srevs = (
            db.query(SentenceReviewLog)
            .filter(
                SentenceReviewLog.reviewed_at >= yesterday_start,
                SentenceReviewLog.reviewed_at < today_start,
            )
            .all()
        )
        y_u = sum(1 for sr in y_srevs if sr.comprehension == "understood")
        y_p = sum(1 for sr in y_srevs if sr.comprehension == "partial")
        y_n = sum(1 for sr in y_srevs if sr.comprehension == "no_idea")
        y_total = len(y_srevs)

        t_u = sum(1 for sr in srevs if sr.comprehension == "understood")
        t_p = sum(1 for sr in srevs if sr.comprehension == "partial")
        t_n = sum(1 for sr in srevs if sr.comprehension == "no_idea")
        t_total = len(srevs)

        if y_total:
            print(f"  Yesterday: {y_total} sentences — understood={y_u} ({y_u/y_total*100:.0f}%) "
                  f"partial={y_p} no_idea={y_n}")
        else:
            print("  Yesterday: no reviews")
        if t_total:
            print(f"  Today:     {t_total} sentences — understood={t_u} ({t_u/t_total*100:.0f}%) "
                  f"partial={t_p} no_idea={t_n}")
        else:
            print("  Today: no reviews yet")

    # --- Sentence Stats ---
    print(f"\n{'=' * 60}")
    print("SENTENCE POOL")
    print("=" * 60)
    active = db.query(func.count(Sentence.id)).filter(Sentence.is_active == True).scalar()
    print(f"  Active sentences: {active}")

    db.close()


if __name__ == "__main__":
    main()
