"""Analyze the intro card A/B experiment results.

Compares:
  Group A (intro_ab_sentence): sentence-first (control)
  Group B (intro_ab_card): info card before first sentence

Metrics:
  1. Reviews to graduation (mean, median)
  2. First-review accuracy (% correct on first sentence review)
  3. Time to graduation (days from acquisition start)
  4. Overall acquisition accuracy

Usage:
  python3 scripts/analyze_intro_experiment.py [--db path/to/alif.db]
"""
import argparse
import os
import sys
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine, text


def analyze(db_path: str) -> None:
    engine = create_engine(f"sqlite:///{db_path}")

    with engine.connect() as conn:
        # Word counts per group
        rows = conn.execute(text("""
            SELECT experiment_group, COUNT(*) as n,
                   SUM(CASE WHEN graduated_at IS NOT NULL THEN 1 ELSE 0 END) as graduated,
                   AVG(times_seen) as avg_reviews,
                   AVG(CAST(times_correct AS FLOAT) / NULLIF(times_seen, 0)) as avg_accuracy
            FROM user_lemma_knowledge
            WHERE experiment_group IS NOT NULL
            GROUP BY experiment_group
        """)).fetchall()

        if not rows:
            print("No experiment data yet. Words need to be introduced first.")
            return

        print("=" * 65)
        print("INTRO CARD A/B EXPERIMENT RESULTS")
        print("=" * 65)
        print()

        for row in rows:
            group, n, graduated, avg_reviews, avg_accuracy = row
            label = "Card first" if group == "intro_ab_card" else "Sentence first"
            print(f"  {label} ({group})")
            print(f"    Words:       {n}")
            print(f"    Graduated:   {graduated} ({graduated/n*100:.0f}%)" if n else "")
            print(f"    Avg reviews: {avg_reviews:.1f}" if avg_reviews else "")
            print(f"    Avg accuracy:{avg_accuracy*100:.0f}%" if avg_accuracy else "")
            print()

        # Time to graduation
        grad_rows = conn.execute(text("""
            SELECT experiment_group,
                   AVG(julianday(graduated_at) - julianday(acquisition_started_at)) as avg_days,
                   MIN(julianday(graduated_at) - julianday(acquisition_started_at)) as min_days,
                   MAX(julianday(graduated_at) - julianday(acquisition_started_at)) as max_days,
                   AVG(times_seen) as avg_reviews_at_grad
            FROM user_lemma_knowledge
            WHERE experiment_group IS NOT NULL AND graduated_at IS NOT NULL
            GROUP BY experiment_group
        """)).fetchall()

        if grad_rows:
            print("-" * 65)
            print("GRADUATION METRICS")
            print("-" * 65)
            for row in grad_rows:
                group, avg_days, min_days, max_days, avg_reviews = row
                label = "Card first" if group == "intro_ab_card" else "Sentence first"
                print(f"  {label}")
                print(f"    Avg days to grad:    {avg_days:.1f}")
                print(f"    Range:               {min_days:.1f} - {max_days:.1f} days")
                print(f"    Avg reviews at grad: {avg_reviews:.1f}")
                print()

        # First-review accuracy
        first_review_rows = conn.execute(text("""
            SELECT u.experiment_group,
                   COUNT(*) as n,
                   SUM(CASE WHEN r.rating >= 3 THEN 1 ELSE 0 END) as correct
            FROM user_lemma_knowledge u
            JOIN review_log r ON r.lemma_id = u.lemma_id
            WHERE u.experiment_group IS NOT NULL
              AND r.is_acquisition = 1
              AND r.id = (
                  SELECT MIN(r2.id) FROM review_log r2
                  WHERE r2.lemma_id = u.lemma_id AND r2.is_acquisition = 1
              )
            GROUP BY u.experiment_group
        """)).fetchall()

        if first_review_rows:
            print("-" * 65)
            print("FIRST REVIEW ACCURACY")
            print("-" * 65)
            for row in first_review_rows:
                group, n, correct = row
                label = "Card first" if group == "intro_ab_card" else "Sentence first"
                pct = correct / n * 100 if n else 0
                print(f"  {label}: {correct}/{n} correct ({pct:.0f}%)")
            print()

        # Per-word detail
        detail_rows = conn.execute(text("""
            SELECT u.experiment_group, l.lemma_ar, l.gloss_en,
                   u.times_seen, u.times_correct, u.knowledge_state,
                   CASE WHEN u.graduated_at IS NOT NULL
                        THEN ROUND(julianday(u.graduated_at) - julianday(u.acquisition_started_at), 1)
                        ELSE NULL END as days_to_grad
            FROM user_lemma_knowledge u
            JOIN lemmas l ON l.lemma_id = u.lemma_id
            WHERE u.experiment_group IS NOT NULL
            ORDER BY u.experiment_group, u.entered_acquiring_at
        """)).fetchall()

        if detail_rows:
            print("-" * 65)
            print("PER-WORD DETAIL")
            print("-" * 65)
            current_group = None
            for row in detail_rows:
                group, ar, en, seen, correct, state, days = row
                if group != current_group:
                    label = "Card first" if group == "intro_ab_card" else "Sentence first"
                    print(f"\n  {label}:")
                    current_group = group
                acc = f"{correct}/{seen}" if seen else "0/0"
                grad = f"{days}d" if days else state
                print(f"    {ar:>12} {(en or '')[:20]:<20} {acc:<6} {grad}")

        print()
        print("=" * 65)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze intro card A/B experiment")
    parser.add_argument("--db", default="data/alif.db", help="Path to SQLite database")
    args = parser.parse_args()
    analyze(args.db)
