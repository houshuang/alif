#!/usr/bin/env python3
"""One-time cleanup: fix stale acquisition_box and circular canonical references.

1. Clear acquisition_box/acquisition_next_due on non-acquiring words
2. Fix circular canonical_lemma_id references (A→B and B→A)

Usage:
    # Dry run (default)
    python3 scripts/cleanup_stale_state.py --db /app/data/alif.db

    # Apply changes
    python3 scripts/cleanup_stale_state.py --db /app/data/alif.db --apply
"""

import argparse
import sqlite3
import sys


def cleanup_stale_acquisition_box(conn, apply: bool) -> int:
    """Clear acquisition_box on non-acquiring words."""
    cur = conn.cursor()
    cur.execute("""
        SELECT ulk.id, l.lemma_id, l.lemma_ar, l.gloss_en,
               ulk.knowledge_state, ulk.acquisition_box
        FROM user_lemma_knowledge ulk
        JOIN lemmas l ON l.lemma_id = ulk.lemma_id
        WHERE ulk.acquisition_box IS NOT NULL
          AND ulk.knowledge_state != 'acquiring'
    """)
    rows = cur.fetchall()

    print(f"\n=== Stale acquisition_box: {len(rows)} words ===")
    for ulk_id, lid, ar, gloss, state, box in rows:
        print(f"  #{lid} {ar} ({gloss}): state={state} box={box} → clearing")

    if apply and rows:
        cur.execute("""
            UPDATE user_lemma_knowledge
            SET acquisition_box = NULL, acquisition_next_due = NULL
            WHERE acquisition_box IS NOT NULL
              AND knowledge_state != 'acquiring'
        """)
        conn.commit()
        print(f"  Applied: cleared {cur.rowcount} rows")
    elif rows:
        print("  (dry run — use --apply to commit)")

    return len(rows)


def fix_circular_canonicals(conn, apply: bool) -> int:
    """Fix circular canonical_lemma_id references."""
    cur = conn.cursor()
    # Find cases where A.canonical = B AND B.canonical = A
    cur.execute("""
        SELECT a.lemma_id, a.lemma_ar, a.gloss_en, a.canonical_lemma_id,
               b.lemma_id, b.lemma_ar, b.gloss_en, b.canonical_lemma_id
        FROM lemmas a
        JOIN lemmas b ON a.canonical_lemma_id = b.lemma_id
        WHERE b.canonical_lemma_id = a.lemma_id
          AND a.lemma_id < b.lemma_id
    """)
    circles = cur.fetchall()

    print(f"\n=== Circular canonical references: {len(circles)} pairs ===")
    fixed = 0
    for a_id, a_ar, a_gloss, a_canon, b_id, b_ar, b_gloss, b_canon in circles:
        # Lower ID becomes canonical (deterministic rule)
        print(f"  #{a_id} {a_ar} ↔ #{b_id} {b_ar}")
        print(f"    Fix: #{b_id} → canonical of #{a_id}, clear #{a_id}.canonical")

        if apply:
            # a (lower ID) is canonical: clear its canonical_lemma_id
            cur.execute("UPDATE lemmas SET canonical_lemma_id = NULL WHERE lemma_id = ?", (a_id,))
            # b (higher ID) points to a
            cur.execute("UPDATE lemmas SET canonical_lemma_id = ? WHERE lemma_id = ?", (a_id, b_id))
            fixed += 1

    if apply and fixed:
        conn.commit()
        print(f"  Applied: fixed {fixed} circular references")
    elif circles:
        print("  (dry run — use --apply to commit)")

    return len(circles)


def fix_conjugated_variants_with_own_ulk(conn, apply: bool) -> int:
    """Find conjugated forms (with canonical set) that have their own acquiring ULK."""
    cur = conn.cursor()
    cur.execute("""
        SELECT l.lemma_id, l.lemma_ar, l.gloss_en, l.canonical_lemma_id,
               cl.lemma_ar as canon_ar, cl.gloss_en as canon_gloss,
               ulk.knowledge_state, ulk.times_seen
        FROM lemmas l
        JOIN lemmas cl ON l.canonical_lemma_id = cl.lemma_id
        JOIN user_lemma_knowledge ulk ON l.lemma_id = ulk.lemma_id
        JOIN user_lemma_knowledge ulk2 ON cl.lemma_id = ulk2.lemma_id
        WHERE ulk.knowledge_state = 'acquiring'
          AND ulk.times_seen = 0
          AND ulk2.knowledge_state IN ('known', 'learning')
    """)
    rows = cur.fetchall()

    print(f"\n=== Conjugated variants acquiring with canonical already known: {len(rows)} ===")
    for lid, ar, gloss, canon_id, canon_ar, canon_gloss, state, seen in rows:
        print(f"  #{lid} {ar} ({gloss}) acquiring, seen=0")
        print(f"    → canonical #{canon_id} {canon_ar} ({canon_gloss}) is already known")

        if apply:
            # Set to encountered — no point acquiring a conjugated form separately
            cur.execute("""
                UPDATE user_lemma_knowledge
                SET knowledge_state = 'encountered',
                    acquisition_box = NULL,
                    acquisition_next_due = NULL
                WHERE lemma_id = ?
            """, (lid,))

    if apply and rows:
        conn.commit()
        print(f"  Applied: set {len(rows)} to encountered")
    elif rows:
        print("  (dry run — use --apply to commit)")

    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="Clean up stale state")
    parser.add_argument("--db", default="/app/data/alif.db")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry run)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")

    mode = "APPLYING" if args.apply else "DRY RUN"
    print(f"Cleanup stale state  |  {mode}  |  DB: {args.db}")

    total = 0
    total += cleanup_stale_acquisition_box(conn, args.apply)
    total += fix_circular_canonicals(conn, args.apply)
    total += fix_conjugated_variants_with_own_ulk(conn, args.apply)

    print(f"\nTotal issues found: {total}")
    conn.close()


if __name__ == "__main__":
    main()
