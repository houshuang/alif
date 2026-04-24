#!/usr/bin/env python3
"""Batch sentence quality audit using Claude Code with vocabulary context.

Claude reads all active sentences and the learner's vocabulary, reviews each
sentence for grammar, translation accuracy, and vocabulary compliance, and
produces a structured report with retire/fix/ok recommendations.

Usage:
    # Dry-run audit (default)
    python3 scripts/audit_sentences_claude.py --db data/alif.db

    # Audit and apply fixes
    python3 scripts/audit_sentences_claude.py --db data/alif.db --apply

    # Limit batch size
    python3 scripts/audit_sentences_claude.py --db data/alif.db --batch-size 20

    # Use a backup DB
    python3 scripts/audit_sentences_claude.py --db ~/alif-backups/latest.db --verbose
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.claude_code import (
    dump_vocabulary_for_claude,
    generate_with_tools,
    is_available,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WORK_DIR = "/tmp/claude/alif-audit"
VALIDATOR_SCRIPT = str(Path(__file__).resolve().parent / "validate_sentence_cli.py")
DEFAULT_BATCH_SIZE = 25

AUDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "reviews": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sentence_id": {"type": "integer"},
                    "verdict": {
                        "type": "string",
                        "enum": ["ok", "fix", "retire"],
                    },
                    "issues": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "fixed_arabic": {"type": "string", "description": "Corrected Arabic (only if verdict=fix)"},
                    "fixed_english": {"type": "string", "description": "Corrected English (only if verdict=fix)"},
                    "fixed_transliteration": {"type": "string", "description": "Corrected transliteration (only if verdict=fix)"},
                },
                "required": ["sentence_id", "verdict"],
            },
        },
    },
    "required": ["reviews"],
}

SYSTEM_PROMPT = """\
You are an Arabic language quality auditor reviewing generated sentences for a \
learner's spaced repetition system. Your job is to check each sentence for:

1. **Grammar correctness** — proper Arabic grammar (i'rab, agreement, word order)
2. **Translation accuracy** — English translation matches the Arabic meaning
3. **Vocabulary compliance** — all content words should be in the learner's vocabulary
4. **Naturalness** — sentence sounds like something a native speaker would say

You have access to:
- vocab_prompt.txt — the learner's vocabulary
- vocab_lookup.tsv — machine-readable vocabulary lookup
- validate_sentence_cli.py — validates vocabulary compliance

WORKFLOW:
1. Read vocab_prompt.txt to understand the learner's vocabulary
2. For each sentence in the sentences file, evaluate quality
3. Run the validator on suspicious sentences: python3 {validator} --arabic "SENTENCE" --target-bare "TARGET_BARE" --vocab-file {work_dir}/vocab_lookup.tsv
4. Classify each sentence:
   - "ok" — sentence is correct and useful
   - "fix" — sentence has fixable issues (provide corrected version with full diacritics)
   - "retire" — sentence is unfixable (severe grammar errors, nonsensical, or wrong translation)

Guidelines:
- Be lenient on style — simple/textbook style is OK for learners
- Be strict on grammar errors and translation accuracy
- Vocabulary compliance: if the validator flags unknown words, the sentence may still \
be OK if the "unknown" words are conjugated forms of known words
- For fixes: maintain the same target word and difficulty level
- Include full diacritics (tashkeel) on all fixed Arabic text"""


# ---------------------------------------------------------------------------
# Sentence export
# ---------------------------------------------------------------------------

def export_sentences(db_path: str, limit: int | None = None) -> list[dict]:
    """Export active sentences from the database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT s.sentence_id, s.arabic_text, s.english_text, s.transliteration,
               s.source, s.times_shown, s.created_at,
               l.lemma_ar, l.lemma_ar_bare, l.gloss_en
        FROM sentences s
        LEFT JOIN lemmas l ON s.target_lemma_id = l.lemma_id
        WHERE s.is_active = 1
        ORDER BY s.created_at DESC
    """
    if limit:
        query += f" LIMIT {limit}"

    rows = conn.execute(query).fetchall()
    conn.close()

    return [dict(r) for r in rows]


def write_sentences_file(sentences: list[dict], output_dir: str) -> str:
    """Write sentences to a JSONL file for Claude to read."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "sentences.jsonl")
    with open(path, "w") as f:
        for s in sentences:
            entry = {
                "id": s["sentence_id"],
                "arabic": s["arabic_text"],
                "english": s["english_text"],
                "transliteration": s.get("transliteration", ""),
                "target_word": s.get("lemma_ar", ""),
                "target_bare": s.get("lemma_ar_bare", ""),
                "source": s.get("source", ""),
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


# ---------------------------------------------------------------------------
# Apply fixes
# ---------------------------------------------------------------------------

def apply_audit_results(db_path: str, reviews: list[dict]) -> dict:
    """Apply audit results to the database."""
    conn = sqlite3.connect(db_path)
    stats = {"retired": 0, "fixed": 0, "ok": 0, "skipped": 0}

    for review in reviews:
        sid = review.get("sentence_id")
        verdict = review.get("verdict", "ok")

        if verdict == "retire":
            conn.execute(
                "UPDATE sentences SET is_active = 0 WHERE sentence_id = ?",
                (sid,),
            )
            stats["retired"] += 1
        elif verdict == "fix":
            fixed_ar = review.get("fixed_arabic")
            fixed_en = review.get("fixed_english")
            fixed_tr = review.get("fixed_transliteration")
            if fixed_ar and fixed_en:
                updates = ["arabic_text = ?", "english_text = ?"]
                params = [fixed_ar, fixed_en]
                if fixed_tr:
                    updates.append("transliteration = ?")
                    params.append(fixed_tr)
                params.append(sid)
                conn.execute(
                    f"UPDATE sentences SET {', '.join(updates)} WHERE sentence_id = ?",
                    params,
                )
                stats["fixed"] += 1
            else:
                stats["skipped"] += 1
        else:
            stats["ok"] += 1

    conn.commit()
    conn.close()
    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Audit sentences using Claude Code with vocabulary context")
    parser.add_argument("--db", required=True, help="Path to SQLite DB")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"Sentences per Claude session (default: {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--limit", type=int, help="Max total sentences to audit")
    parser.add_argument("--model", default="opus", help="Claude model (default: opus)")
    parser.add_argument("--apply", action="store_true", help="Apply fixes and retirements to DB")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    args = parser.parse_args()

    if not is_available():
        print("ERROR: claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code")
        sys.exit(1)

    # Export sentences
    print(f"Exporting active sentences from {args.db}...")
    sentences = export_sentences(args.db, args.limit)
    if not sentences:
        print("No active sentences found.")
        return
    print(f"  {len(sentences)} active sentences")

    # Dump vocabulary
    print(f"Dumping vocabulary to {WORK_DIR}...")
    prompt_path, lookup_path = dump_vocabulary_for_claude(args.db, WORK_DIR)

    # Process in batches
    all_reviews = []
    batches = [sentences[i:i + args.batch_size] for i in range(0, len(sentences), args.batch_size)]

    for batch_idx, batch in enumerate(batches):
        batch_label = f"Batch {batch_idx + 1}/{len(batches)} ({len(batch)} sentences)"
        print(f"\n{batch_label}...")

        # Write this batch to a file
        sentences_path = write_sentences_file(batch, WORK_DIR)

        system = SYSTEM_PROMPT.format(
            validator=VALIDATOR_SCRIPT,
            work_dir=WORK_DIR,
        )

        prompt = f"""Review the sentences in {sentences_path} for quality.

STEPS:
1. Read {WORK_DIR}/vocab_prompt.txt to understand the learner's vocabulary
2. Read {sentences_path} — each line is a JSON object with: id, arabic, english, target_word, target_bare
3. For each sentence, evaluate grammar, translation accuracy, and naturalness
4. Run the validator on any sentence you suspect has vocabulary issues:
   python3 {VALIDATOR_SCRIPT} --arabic "SENTENCE" --target-bare "TARGET_BARE" --vocab-file {lookup_path}
5. Return your review for each sentence (verdict: ok/fix/retire)

For "fix" verdicts, provide corrected arabic, english, and transliteration with full diacritics."""

        start = time.time()
        try:
            result = generate_with_tools(
                prompt=prompt,
                system_prompt=system,
                json_schema=AUDIT_SCHEMA,
                work_dir=WORK_DIR,
                model=args.model,
                max_budget_usd=1.00,  # higher budget for audit
                timeout=300,  # longer timeout for large batches
            )
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        elapsed = time.time() - start
        reviews = result.get("reviews", [])
        print(f"  Done in {elapsed:.1f}s — reviewed {len(reviews)} sentences")

        # Summary for this batch
        verdicts = {"ok": 0, "fix": 0, "retire": 0}
        for r in reviews:
            v = r.get("verdict", "ok")
            verdicts[v] = verdicts.get(v, 0) + 1

        print(f"  Results: {verdicts['ok']} ok, {verdicts['fix']} fix, {verdicts['retire']} retire")

        if args.verbose:
            for r in reviews:
                if r.get("verdict") != "ok":
                    sid = r.get("sentence_id", "?")
                    verdict = r.get("verdict", "?")
                    issues = r.get("issues", [])
                    print(f"    [{verdict.upper()}] #{sid}: {'; '.join(issues)}")
                    if r.get("fixed_arabic"):
                        print(f"      Fixed: {r['fixed_arabic']}")

        all_reviews.extend(reviews)

    # Overall summary
    total_verdicts = {"ok": 0, "fix": 0, "retire": 0}
    for r in all_reviews:
        v = r.get("verdict", "ok")
        total_verdicts[v] = total_verdicts.get(v, 0) + 1

    print(f"\n{'='*60}")
    print(f"Audit Summary: {len(all_reviews)} sentences reviewed")
    print(f"  OK: {total_verdicts['ok']}")
    print(f"  Fix: {total_verdicts['fix']}")
    print(f"  Retire: {total_verdicts['retire']}")
    print(f"{'='*60}")

    # Apply if requested
    if args.apply and (total_verdicts["fix"] > 0 or total_verdicts["retire"] > 0):
        print(f"\nApplying changes to {args.db}...")
        stats = apply_audit_results(args.db, all_reviews)
        print(f"  Fixed: {stats['fixed']}, Retired: {stats['retired']}, Skipped: {stats['skipped']}")
    elif not args.apply and (total_verdicts["fix"] > 0 or total_verdicts["retire"] > 0):
        print(f"\nDry run — use --apply to apply {total_verdicts['fix']} fixes and {total_verdicts['retire']} retirements")


if __name__ == "__main__":
    main()
