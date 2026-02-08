#!/usr/bin/env python3
"""Read-only database analysis script."""
import sqlite3
import json
from datetime import datetime, date
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "alif.db"

def main():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("=" * 80)
    print("ALIF DATABASE ANALYSIS")
    print(f"Database: {DB_PATH}")
    print(f"Analysis time: {datetime.now().isoformat()}")
    print("=" * 80)

    # =========================================================================
    # A) All UserLemmaKnowledge records with FSRS card details
    # =========================================================================
    print("\n" + "=" * 80)
    print("A) ALL UserLemmaKnowledge RECORDS")
    print("=" * 80)

    cur.execute("""
        SELECT ulk.lemma_id, ulk.knowledge_state, ulk.times_seen, ulk.times_correct,
               ulk.total_encounters, ulk.last_reviewed, ulk.source, ulk.fsrs_card_json,
               ulk.introduced_at, ulk.distinct_contexts,
               l.lemma_ar, l.lemma_ar_bare, l.gloss_en, l.pos
        FROM user_lemma_knowledge ulk
        JOIN lemmas l ON l.lemma_id = ulk.lemma_id
        ORDER BY ulk.knowledge_state, ulk.lemma_id
    """)
    rows = cur.fetchall()
    print(f"\nTotal records: {len(rows)}\n")

    print(f"{'ID':>5} {'State':<10} {'Seen':>5} {'Corr':>5} {'Enc':>5} {'Src':<12} "
          f"{'Stab':>8} {'Diff':>6} {'FSRS_St':>8} {'Due Date':<20} {'Arabic':<15} {'English':<25}")
    print("-" * 160)

    for r in rows:
        card = json.loads(r['fsrs_card_json']) if r['fsrs_card_json'] else {}
        stability = card.get('stability', '-')
        difficulty = card.get('difficulty', '-')
        fsrs_state = card.get('state', '-')
        due = card.get('due', '-')
        if isinstance(due, str) and len(due) > 19:
            due = due[:19]

        stab_str = f"{stability:.2f}" if isinstance(stability, (int, float)) else str(stability)
        diff_str = f"{difficulty:.2f}" if isinstance(difficulty, (int, float)) else str(difficulty)

        arabic = (r['lemma_ar'] or '')[:14]
        english = (r['gloss_en'] or '')[:24]

        print(f"{r['lemma_id']:>5} {r['knowledge_state']:<10} {r['times_seen']:>5} "
              f"{r['times_correct']:>5} {r['total_encounters']:>5} {(r['source'] or 'null'):<12} "
              f"{stab_str:>8} {diff_str:>6} {str(fsrs_state):>8} {str(due):<20} {arabic:<15} {english:<25}")

    # =========================================================================
    # B) Count per knowledge_state
    # =========================================================================
    print("\n" + "=" * 80)
    print("B) WORDS PER KNOWLEDGE STATE")
    print("=" * 80)

    cur.execute("""
        SELECT knowledge_state, COUNT(*) as cnt
        FROM user_lemma_knowledge
        GROUP BY knowledge_state
        ORDER BY cnt DESC
    """)
    for r in cur.fetchall():
        print(f"  {r['knowledge_state']:<12}: {r['cnt']:>5}")

    # =========================================================================
    # C) Known words with very low stability (<1.0)
    # =========================================================================
    print("\n" + "=" * 80)
    print("C) KNOWN WORDS WITH LOW STABILITY (<1.0)")
    print("=" * 80)

    cur.execute("""
        SELECT ulk.lemma_id, ulk.knowledge_state, ulk.fsrs_card_json, ulk.times_seen,
               l.lemma_ar, l.gloss_en
        FROM user_lemma_knowledge ulk
        JOIN lemmas l ON l.lemma_id = ulk.lemma_id
        WHERE ulk.knowledge_state = 'known'
    """)
    found = False
    for r in cur.fetchall():
        card = json.loads(r['fsrs_card_json']) if r['fsrs_card_json'] else {}
        stab = card.get('stability', None)
        if stab is not None and stab < 1.0:
            found = True
            print(f"  lemma_id={r['lemma_id']} stability={stab:.4f} seen={r['times_seen']} "
                  f"arabic={r['lemma_ar']} english={r['gloss_en']}")
    if not found:
        print("  None found.")

    # =========================================================================
    # D) Learning words with very high stability (>30)
    # =========================================================================
    print("\n" + "=" * 80)
    print("D) LEARNING WORDS WITH HIGH STABILITY (>30)")
    print("=" * 80)

    cur.execute("""
        SELECT ulk.lemma_id, ulk.knowledge_state, ulk.fsrs_card_json, ulk.times_seen,
               ulk.times_correct, l.lemma_ar, l.gloss_en
        FROM user_lemma_knowledge ulk
        JOIN lemmas l ON l.lemma_id = ulk.lemma_id
        WHERE ulk.knowledge_state = 'learning'
    """)
    found = False
    for r in cur.fetchall():
        card = json.loads(r['fsrs_card_json']) if r['fsrs_card_json'] else {}
        stab = card.get('stability', None)
        if stab is not None and stab > 30:
            found = True
            print(f"  lemma_id={r['lemma_id']} stability={stab:.2f} seen={r['times_seen']} "
                  f"correct={r['times_correct']} arabic={r['lemma_ar']} english={r['gloss_en']}")
    if not found:
        print("  None found.")

    # =========================================================================
    # E) Words with times_seen > 0 but times_correct = 0
    # =========================================================================
    print("\n" + "=" * 80)
    print("E) ALWAYS-FAILING WORDS (times_seen > 0, times_correct = 0)")
    print("=" * 80)

    cur.execute("""
        SELECT ulk.lemma_id, ulk.knowledge_state, ulk.times_seen, ulk.times_correct,
               ulk.total_encounters, l.lemma_ar, l.gloss_en, ulk.fsrs_card_json
        FROM user_lemma_knowledge ulk
        JOIN lemmas l ON l.lemma_id = ulk.lemma_id
        WHERE ulk.times_seen > 0 AND ulk.times_correct = 0
        ORDER BY ulk.times_seen DESC
    """)
    rows = cur.fetchall()
    if rows:
        for r in rows:
            card = json.loads(r['fsrs_card_json']) if r['fsrs_card_json'] else {}
            stab = card.get('stability', '-')
            print(f"  lemma_id={r['lemma_id']} state={r['knowledge_state']} "
                  f"seen={r['times_seen']} encounters={r['total_encounters']} "
                  f"stability={stab} arabic={r['lemma_ar']} english={r['gloss_en']}")
    else:
        print("  None found.")

    # =========================================================================
    # F) Words with total_encounters >> times_seen
    # =========================================================================
    print("\n" + "=" * 80)
    print("F) HIGH ENCOUNTER/SEEN RATIO (encounters > 2 * times_seen, encounters >= 5)")
    print("=" * 80)

    cur.execute("""
        SELECT ulk.lemma_id, ulk.knowledge_state, ulk.times_seen, ulk.total_encounters,
               ulk.times_correct, l.lemma_ar, l.gloss_en
        FROM user_lemma_knowledge ulk
        JOIN lemmas l ON l.lemma_id = ulk.lemma_id
        WHERE ulk.total_encounters > 2 * ulk.times_seen
          AND ulk.total_encounters >= 5
        ORDER BY (ulk.total_encounters - ulk.times_seen) DESC
        LIMIT 30
    """)
    rows = cur.fetchall()
    if rows:
        print(f"  {'ID':>5} {'State':<10} {'Seen':>5} {'Enc':>5} {'Ratio':>7} {'Arabic':<15} {'English':<25}")
        for r in rows:
            ratio = r['total_encounters'] / max(r['times_seen'], 1)
            print(f"  {r['lemma_id']:>5} {r['knowledge_state']:<10} {r['times_seen']:>5} "
                  f"{r['total_encounters']:>5} {ratio:>6.1f}x {r['lemma_ar']:<15} {(r['gloss_en'] or ''):<25}")
    else:
        print("  None found.")

    # =========================================================================
    # G) Today's ReviewLog entries
    # =========================================================================
    print("\n" + "=" * 80)
    print("G) TODAY'S REVIEW LOG ENTRIES")
    print("=" * 80)

    today_str = date.today().isoformat()
    cur.execute("""
        SELECT rl.lemma_id, rl.rating, rl.reviewed_at, rl.review_mode,
               rl.comprehension_signal, rl.credit_type, rl.sentence_id,
               l.lemma_ar, l.gloss_en
        FROM review_log rl
        JOIN lemmas l ON l.lemma_id = rl.lemma_id
        WHERE date(rl.reviewed_at) = ?
        ORDER BY rl.lemma_id, rl.reviewed_at
    """, (today_str,))
    rows = cur.fetchall()
    print(f"\nTotal reviews today ({today_str}): {len(rows)}\n")

    if rows:
        # Group by lemma_id
        from collections import defaultdict
        grouped = defaultdict(list)
        for r in rows:
            grouped[r['lemma_id']].append(r)

        for lemma_id, reviews in sorted(grouped.items()):
            first = reviews[0]
            ratings = [str(r['rating']) for r in reviews]
            modes = set(r['review_mode'] or '?' for r in reviews)
            signals = set(r['comprehension_signal'] or '?' for r in reviews)
            credits = set(r['credit_type'] or '?' for r in reviews)
            print(f"  lemma_id={lemma_id} ({first['lemma_ar']} = {first['gloss_en']})")
            print(f"    reviews: {len(reviews)}, ratings: [{', '.join(ratings)}], "
                  f"modes: {modes}, signals: {signals}, credits: {credits}")
            for r in reviews:
                print(f"      {r['reviewed_at']} rating={r['rating']} mode={r['review_mode']} "
                      f"signal={r['comprehension_signal']} credit={r['credit_type']} "
                      f"sent_id={r['sentence_id']}")

    # Also show summary stats
    cur.execute("""
        SELECT rating, COUNT(*) as cnt
        FROM review_log
        WHERE date(reviewed_at) = ?
        GROUP BY rating
        ORDER BY rating
    """, (today_str,))
    print(f"\n  Rating distribution today:")
    for r in cur.fetchall():
        print(f"    rating {r['rating']}: {r['cnt']} reviews")

    cur.execute("""
        SELECT credit_type, COUNT(*) as cnt
        FROM review_log
        WHERE date(reviewed_at) = ?
        GROUP BY credit_type
        ORDER BY credit_type
    """, (today_str,))
    print(f"\n  Credit type distribution today:")
    for r in cur.fetchall():
        print(f"    {r['credit_type'] or 'null'}: {r['cnt']} reviews")

    # =========================================================================
    # H) Lemmas reviewed today without UserLemmaKnowledge
    # =========================================================================
    print("\n" + "=" * 80)
    print("H) LEMMAS REVIEWED TODAY WITHOUT UserLemmaKnowledge RECORD")
    print("=" * 80)

    cur.execute("""
        SELECT DISTINCT rl.lemma_id, l.lemma_ar, l.gloss_en
        FROM review_log rl
        JOIN lemmas l ON l.lemma_id = rl.lemma_id
        LEFT JOIN user_lemma_knowledge ulk ON ulk.lemma_id = rl.lemma_id
        WHERE date(rl.reviewed_at) = ?
          AND ulk.id IS NULL
    """, (today_str,))
    rows = cur.fetchall()
    if rows:
        for r in rows:
            print(f"  lemma_id={r['lemma_id']} arabic={r['lemma_ar']} english={r['gloss_en']}")
    else:
        print("  None found (all reviewed lemmas have knowledge records).")

    # =========================================================================
    # I) Sentences reviewed today
    # =========================================================================
    print("\n" + "=" * 80)
    print("I) SENTENCES REVIEWED TODAY")
    print("=" * 80)

    cur.execute("""
        SELECT srl.sentence_id, srl.comprehension, srl.reviewed_at, srl.review_mode,
               s.times_shown,
               s.last_reading_comprehension, s.last_listening_comprehension,
               substr(s.arabic_text, 1, 80) as arabic_preview
        FROM sentence_review_log srl
        JOIN sentences s ON s.id = srl.sentence_id
        WHERE date(srl.reviewed_at) = ?
        ORDER BY srl.reviewed_at
    """, (today_str,))
    rows = cur.fetchall()
    print(f"\nSentence reviews today: {len(rows)}\n")

    if rows:
        for r in rows:
            print(f"  sent_id={r['sentence_id']} times_shown={r['times_shown']} "
                  f"comprehension={r['comprehension']} mode={r['review_mode']}")
            print(f"    last_reading={r['last_reading_comprehension']} "
                  f"last_listening={r['last_listening_comprehension']}")
            print(f"    arabic: {r['arabic_preview']}")
            print(f"    reviewed_at: {r['reviewed_at']}")
            print()

    # =========================================================================
    # BONUS: Quick summary stats
    # =========================================================================
    print("\n" + "=" * 80)
    print("BONUS: QUICK SUMMARY")
    print("=" * 80)

    cur.execute("SELECT COUNT(*) FROM lemmas")
    print(f"  Total lemmas: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM roots")
    print(f"  Total roots: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM sentences")
    print(f"  Total sentences: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM user_lemma_knowledge")
    print(f"  Total knowledge records: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM review_log")
    print(f"  Total review log entries: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM sentence_review_log")
    print(f"  Total sentence review logs: {cur.fetchone()[0]}")
    cur.execute("SELECT COUNT(*) FROM stories")
    print(f"  Total stories: {cur.fetchone()[0]}")
    cur.execute("SELECT MIN(reviewed_at), MAX(reviewed_at) FROM review_log")
    r = cur.fetchone()
    print(f"  Review log range: {r[0]} to {r[1]}")

    conn.close()
    print("\n" + "=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
