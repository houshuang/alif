#!/usr/bin/env python3
"""Analyze sentence quality from review data.

Mines the link between sentence properties and learning outcomes.
Uses sentence_review_log (comprehension signals) joined with sentence_words
(scaffold composition) and review_log (word-level ratings).

Usage:
    python3 scripts/analyze_sentence_quality.py [--db path/to/alif.db]
"""

import argparse
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text


def main():
    parser = argparse.ArgumentParser(description="Analyze sentence quality from review data")
    parser.add_argument("--db", default="data/alif.db", help="Path to SQLite database")
    parser.add_argument("--output", default=None, help="Output JSON path (default: stdout)")
    args = parser.parse_args()

    engine = create_engine(f"sqlite:///{args.db}")

    results = {}

    with engine.connect() as c:
        # ── 1. Overall comprehension breakdown ──
        rows = c.execute(text("""
            SELECT comprehension, COUNT(*) as cnt,
                   AVG(response_ms) as avg_ms,
                   MIN(response_ms) as min_ms
            FROM sentence_review_log
            WHERE response_ms < 300000
            GROUP BY comprehension
        """)).fetchall()
        results["comprehension_breakdown"] = [
            {"signal": r[0], "count": r[1], "avg_ms": round(r[2]), "min_ms": r[3]}
            for r in rows
        ]
        total_reviews = sum(r[1] for r in rows)

        # ── 2. Word count → comprehension ──
        rows = c.execute(text("""
            SELECT word_count, comprehension, COUNT(*) as cnt FROM (
                SELECT srl.id, srl.comprehension,
                       (SELECT COUNT(*) FROM sentence_words sw WHERE sw.sentence_id = srl.sentence_id) as word_count
                FROM sentence_review_log srl
            )
            GROUP BY word_count, comprehension
            ORDER BY word_count
        """)).fetchall()

        wc_data: dict[int, dict] = defaultdict(lambda: {"understood": 0, "partial": 0, "no_idea": 0, "total": 0})
        for wc, comp, cnt in rows:
            wc_data[wc][comp] = cnt
            wc_data[wc]["total"] += cnt

        results["word_count_vs_comprehension"] = [
            {
                "word_count": wc,
                "total": d["total"],
                "understood_pct": round(d["understood"] / d["total"] * 100, 1) if d["total"] > 0 else 0,
                "partial_pct": round(d["partial"] / d["total"] * 100, 1) if d["total"] > 0 else 0,
                "no_idea_pct": round(d["no_idea"] / d["total"] * 100, 1) if d["total"] > 0 else 0,
            }
            for wc, d in sorted(wc_data.items())
            if d["total"] >= 5  # need minimum sample
        ]

        # ── 3. Scaffold composition → comprehension ──
        # For each reviewed sentence, count known vs acquiring vs encountered scaffold words
        rows = c.execute(text("""
            SELECT
                srl.id as review_id,
                srl.comprehension,
                srl.response_ms,
                s.id as sentence_id,
                COUNT(sw.id) as total_words,
                SUM(CASE WHEN ulk.knowledge_state IN ('known', 'learning') THEN 1 ELSE 0 END) as known_words,
                SUM(CASE WHEN ulk.knowledge_state = 'acquiring' THEN 1 ELSE 0 END) as acquiring_words,
                SUM(CASE WHEN ulk.knowledge_state = 'encountered' OR ulk.id IS NULL THEN 1 ELSE 0 END) as unknown_words,
                SUM(CASE WHEN sw.is_target_word = 1 THEN 1 ELSE 0 END) as target_words
            FROM sentence_review_log srl
            JOIN sentences s ON s.id = srl.sentence_id
            JOIN sentence_words sw ON sw.sentence_id = s.id
            LEFT JOIN user_lemma_knowledge ulk ON ulk.lemma_id = sw.lemma_id
            WHERE srl.response_ms < 300000
            GROUP BY srl.id
        """)).fetchall()

        scaffold_buckets: dict[str, dict] = defaultdict(lambda: {"understood": 0, "partial": 0, "no_idea": 0, "total": 0, "avg_ms": []})
        for row in rows:
            _, comp, ms, _, total, known, acq, unknown, target = row
            if total == 0:
                continue
            known_pct = (known or 0) / total
            if known_pct >= 0.8:
                bucket = "≥80% known"
            elif known_pct >= 0.6:
                bucket = "60-80% known"
            elif known_pct >= 0.4:
                bucket = "40-60% known"
            else:
                bucket = "<40% known"

            scaffold_buckets[bucket][comp] = scaffold_buckets[bucket].get(comp, 0) + 1
            scaffold_buckets[bucket]["total"] += 1
            if ms:
                scaffold_buckets[bucket]["avg_ms"].append(ms)

        results["scaffold_pct_vs_comprehension"] = [
            {
                "bucket": bucket,
                "total": d["total"],
                "understood_pct": round(d.get("understood", 0) / d["total"] * 100, 1),
                "partial_pct": round(d.get("partial", 0) / d["total"] * 100, 1),
                "avg_response_ms": round(statistics.mean(d["avg_ms"])) if d["avg_ms"] else None,
            }
            for bucket, d in sorted(scaffold_buckets.items())
        ]

        # ── 4. Sentences reviewed multiple times — comprehension trajectory ──
        rows = c.execute(text("""
            SELECT sentence_id, COUNT(*) as review_count,
                   SUM(CASE WHEN comprehension = 'understood' THEN 1 ELSE 0 END) as understood_count,
                   MIN(comprehension) as first_comp,
                   MAX(reviewed_at) as last_reviewed
            FROM sentence_review_log
            GROUP BY sentence_id
            HAVING review_count >= 2
            ORDER BY review_count DESC
            LIMIT 100
        """)).fetchall()
        results["multi_review_sentences"] = {
            "count": len(rows),
            "avg_reviews": round(statistics.mean(r[1] for r in rows), 1) if rows else 0,
            "improvement_rate": round(
                sum(1 for r in rows if r[2] > 0 and r[2] < r[1]) / len(rows) * 100, 1
            ) if rows else 0,
        }

        # ── 5. Word-level: which sentence properties predict better word learning? ──
        rows = c.execute(text("""
            SELECT
                rl.rating,
                rl.credit_type,
                rl.is_acquisition,
                rl.was_confused,
                (SELECT COUNT(*) FROM sentence_words sw2 WHERE sw2.sentence_id = rl.sentence_id) as sent_word_count,
                (SELECT comprehension FROM sentence_review_log srl2
                 WHERE srl2.sentence_id = rl.sentence_id AND srl2.session_id = rl.session_id
                 LIMIT 1) as sent_comprehension
            FROM review_log rl
            WHERE rl.sentence_id IS NOT NULL
              AND rl.rating IS NOT NULL
        """)).fetchall()

        # Rating distribution by credit type
        credit_ratings: dict[str, list[int]] = defaultdict(list)
        # Rating by sentence word count
        wc_ratings: dict[int, list[int]] = defaultdict(list)
        # Confusion by word count
        wc_confused: dict[int, dict] = defaultdict(lambda: {"confused": 0, "total": 0})

        for rating, credit, is_acq, confused, wc, sent_comp in rows:
            if credit:
                credit_ratings[credit].append(rating)
            if wc:
                wc_ratings[wc].append(rating)
                wc_confused[wc]["total"] += 1
                if confused:
                    wc_confused[wc]["confused"] += 1

        results["rating_by_credit_type"] = {
            credit: {
                "count": len(ratings),
                "avg_rating": round(statistics.mean(ratings), 2),
                "pct_good": round(sum(1 for r in ratings if r >= 3) / len(ratings) * 100, 1),
            }
            for credit, ratings in sorted(credit_ratings.items())
        }

        results["rating_by_word_count"] = [
            {
                "word_count": wc,
                "reviews": len(ratings),
                "avg_rating": round(statistics.mean(ratings), 2),
                "pct_good": round(sum(1 for r in ratings if r >= 3) / len(ratings) * 100, 1),
                "confusion_rate": round(wc_confused[wc]["confused"] / wc_confused[wc]["total"] * 100, 1) if wc_confused[wc]["total"] > 0 else 0,
            }
            for wc, ratings in sorted(wc_ratings.items())
            if len(ratings) >= 10
        ]

        # ── 6. Response time distribution and speed→accuracy correlation ──
        rows = c.execute(text("""
            SELECT response_ms, comprehension
            FROM sentence_review_log
            WHERE response_ms IS NOT NULL AND response_ms < 300000
            ORDER BY response_ms
        """)).fetchall()

        time_buckets = {"<10s": [], "10-30s": [], "30-60s": [], "60-120s": [], ">120s": []}
        for ms, comp in rows:
            s = ms / 1000
            if s < 10:
                bucket = "<10s"
            elif s < 30:
                bucket = "10-30s"
            elif s < 60:
                bucket = "30-60s"
            elif s < 120:
                bucket = "60-120s"
            else:
                bucket = ">120s"
            time_buckets[bucket].append(comp)

        results["response_time_vs_comprehension"] = [
            {
                "bucket": bucket,
                "count": len(comps),
                "understood_pct": round(comps.count("understood") / len(comps) * 100, 1) if comps else 0,
            }
            for bucket, comps in time_buckets.items()
            if comps
        ]

        # ── 7. Sentence source → quality ──
        rows = c.execute(text("""
            SELECT s.source, srl.comprehension, COUNT(*) as cnt
            FROM sentence_review_log srl
            JOIN sentences s ON s.id = srl.sentence_id
            GROUP BY s.source, srl.comprehension
        """)).fetchall()

        source_data: dict[str, dict] = defaultdict(lambda: {"understood": 0, "partial": 0, "no_idea": 0, "total": 0})
        for src, comp, cnt in rows:
            source_data[src or "unknown"][comp] = cnt
            source_data[src or "unknown"]["total"] += cnt

        results["source_vs_comprehension"] = {
            src: {
                "total": d["total"],
                "understood_pct": round(d["understood"] / d["total"] * 100, 1) if d["total"] > 0 else 0,
            }
            for src, d in sorted(source_data.items(), key=lambda x: -x[1]["total"])
        }

        # ── 8. Summary stats ──
        results["summary"] = {
            "total_sentence_reviews": total_reviews,
            "total_sentences_in_db": c.execute(text("SELECT COUNT(*) FROM sentences")).scalar(),
            "active_sentences": c.execute(text("SELECT COUNT(*) FROM sentences WHERE is_active=1")).scalar(),
            "total_word_reviews": c.execute(text("SELECT COUNT(*) FROM review_log")).scalar(),
            "unique_sentences_reviewed": c.execute(text("SELECT COUNT(DISTINCT sentence_id) FROM sentence_review_log")).scalar(),
            "analysis_date": datetime.now(timezone.utc).isoformat(),
        }

    # ── Output ──
    output = json.dumps(results, indent=2, ensure_ascii=False)

    if args.output:
        Path(args.output).write_text(output)
        print(f"Written to {args.output}")
    else:
        print(output)

    # ── Print summary ──
    print("\n" + "=" * 60)
    print("SENTENCE QUALITY ANALYSIS SUMMARY")
    print("=" * 60)

    s = results["summary"]
    print(f"\nData: {s['total_sentence_reviews']} reviews across {s['unique_sentences_reviewed']} sentences")
    print(f"DB: {s['total_sentences_in_db']} total, {s['active_sentences']} active")

    print("\n── Comprehension Breakdown ──")
    for r in results["comprehension_breakdown"]:
        pct = round(r["count"] / total_reviews * 100, 1)
        print(f"  {r['signal']:12s}: {r['count']:5d} ({pct:5.1f}%)  avg {r['avg_ms']}ms")

    print("\n── Word Count → Comprehension (understood %) ──")
    for r in results["word_count_vs_comprehension"]:
        bar = "█" * int(r["understood_pct"] / 5) + "░" * (20 - int(r["understood_pct"] / 5))
        print(f"  {r['word_count']:2d} words: {bar} {r['understood_pct']:5.1f}%  (n={r['total']})")

    print("\n── Scaffold % → Comprehension ──")
    for r in results["scaffold_pct_vs_comprehension"]:
        print(f"  {r['bucket']:14s}: {r['understood_pct']:5.1f}% understood  (n={r['total']})")

    print("\n── Response Time → Understood % ──")
    for r in results["response_time_vs_comprehension"]:
        print(f"  {r['bucket']:8s}: {r['understood_pct']:5.1f}% understood  (n={r['count']})")

    print("\n── Rating by Credit Type ──")
    for credit, d in results["rating_by_credit_type"].items():
        print(f"  {credit:12s}: avg {d['avg_rating']:.2f}  {d['pct_good']:.1f}% good  (n={d['count']})")

    print("\n── Source → Understood % ──")
    for src, d in results["source_vs_comprehension"].items():
        print(f"  {src:16s}: {d['understood_pct']:5.1f}%  (n={d['total']})")


if __name__ == "__main__":
    main()
