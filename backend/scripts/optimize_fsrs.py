"""Run the FSRS-6 optimizer on the review_log table and report personalized weights.

Usage:
    python3 scripts/optimize_fsrs.py [--db PATH]

Only non-acquisition reviews (is_acquisition=0) are used, since acquisition reviews
are Leitner-box transitions, not FSRS card updates.
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fsrs import Optimizer, ReviewLog as FsrsReviewLog, Rating, Scheduler, Card


RATING_MAP = {1: Rating.Again, 2: Rating.Hard, 3: Rating.Good, 4: Rating.Easy}


def load_reviews(db_path: Path) -> list[FsrsReviewLog]:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT lemma_id, rating, reviewed_at, response_ms
        FROM review_log
        WHERE is_acquisition = 0 OR is_acquisition IS NULL
        ORDER BY lemma_id, reviewed_at
        """
    ).fetchall()
    conn.close()

    logs: list[FsrsReviewLog] = []
    skipped = 0
    for lemma_id, rating_int, ts_str, response_ms in rows:
        if rating_int not in RATING_MAP:
            skipped += 1
            continue
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        logs.append(
            FsrsReviewLog(
                card_id=int(lemma_id),
                rating=RATING_MAP[rating_int],
                review_datetime=dt,
                review_duration=int(response_ms) if response_ms else None,
            )
        )
    if skipped:
        print(f"Skipped {skipped} reviews with unrecognized rating")
    return logs


# FSRS-6 default weights (what the library ships with; what we're running in production)
DEFAULT_W = [
    0.2172, 1.1771, 3.2602, 16.1507, 7.0114, 0.57, 2.0966, 0.0069,
    1.5261, 0.112, 1.0178, 1.849, 0.1133, 0.3127, 2.2934, 0.2191,
    3.0004, 0.7536, 0.3332, 0.1437, 0.2,
]
W_LABELS = [
    "w0  init stability (Again)",
    "w1  init stability (Hard)",
    "w2  init stability (Good)",
    "w3  init stability (Easy)",
    "w4  init difficulty base",
    "w5  init difficulty modifier",
    "w6  difficulty decay",
    "w7  difficulty mean-reversion",
    "w8  stability growth (success)",
    "w9  stability diminish (hard penalty)",
    "w10 stability-retrievability coupling",
    "w11 POST-LAPSE stability base",
    "w12 post-lapse stability (difficulty)",
    "w13 post-lapse stability (stability)",
    "w14 post-lapse stability (retrievability)",
    "w15 hard-rating stability multiplier",
    "w16 easy-rating stability multiplier",
    "w17 short-term stability (same-day)",
    "w18 short-term stability modifier",
    "w19 initial retrievability ceiling",
    "w20 (unused/reserved)",
]


def pct(a: float, b: float) -> str:
    if b == 0:
        return " inf%"
    diff = (a - b) / b * 100
    sign = "+" if diff >= 0 else ""
    return f"{sign}{diff:6.1f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/tmp/claude/alif_fresh.db")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    print(f"Loading review logs from {db_path}…")
    logs = load_reviews(db_path)
    print(f"Loaded {len(logs)} FSRS reviews across "
          f"{len({l.card_id for l in logs})} lemmas")

    print("\nRunning optimizer (this takes a minute)…\n")
    opt = Optimizer(logs)
    new_w = opt.compute_optimal_parameters(verbose=False)

    print(f"\nOptimal retention (given these weights): ", end="")
    try:
        optimal_retention = opt.compute_optimal_retention(new_w)
        print(optimal_retention)
    except Exception as e:
        print(f"(failed: {e})")

    print("\n== Weight comparison (default → optimized) ==")
    print(f"{'weight':<42} {'default':>10} {'optimized':>10} {'change':>9}")
    print("-" * 75)
    for i, (label, d, n) in enumerate(zip(W_LABELS, DEFAULT_W, new_w)):
        marker = "  <-- LAPSE" if i in (11, 12, 13, 14) else ""
        print(f"{label:<42} {d:>10.4f} {n:>10.4f} {pct(n, d):>9}{marker}")

    # Sanity: what post-lapse stability does each parameter set predict
    # for a well-learned word (stability=30, difficulty=5, retrievability=0.9)?
    def post_lapse_stability(w, S=30.0, D=5.0, R=0.9):
        # FSRS-6 post-lapse stability formula
        return w[11] * (D ** -w[12]) * ((S + 1) ** w[13] - 1) * (2.718281828 ** ((1 - R) * w[14]))

    print("\n== Predicted post-lapse stability for S=30d, D=5, R=0.9 ==")
    d_lapse = post_lapse_stability(DEFAULT_W)
    n_lapse = post_lapse_stability(new_w)
    print(f"  default params:   {d_lapse:7.2f} days")
    print(f"  optimized params: {n_lapse:7.2f} days  ({pct(n_lapse, d_lapse).strip()})")
    print()
    print("Higher post-lapse stability = gentler recovery path after a lapse.")
    print()
    print("To deploy: copy the optimized weights into Scheduler(parameters=...) "
          "in backend/app/services/fsrs_service.py and restart alif-backend.")


if __name__ == "__main__":
    main()
