#!/usr/bin/env python3
"""Generate validated sentences using Claude Code CLI with validator-in-the-loop.

Instead of the normal 7-retry external loop (generate → validate → retry),
Claude reads the vocabulary file, generates sentences, runs the validator script
itself, and self-corrects — all within one session.

Usage:
    # Generate sentences for 10 words needing them (dry-run)
    python3 scripts/generate_sentences_claude.py --db data/alif.db --words 10 --dry-run

    # Generate and store in DB
    python3 scripts/generate_sentences_claude.py --db data/alif.db --words 10

    # Specify model and sentences per word
    python3 scripts/generate_sentences_claude.py --db data/alif.db --words 5 --per-word 3 --model opus

    # Use a backup DB (read-only, won't store)
    python3 scripts/generate_sentences_claude.py --db ~/alif-backups/latest.db --words 5 --dry-run
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

WORDS_PER_SESSION = 10  # batch this many words into one Claude Code session
DEFAULT_PER_WORD = 2
WORK_DIR = "/tmp/claude/alif-sentences"
VALIDATOR_SCRIPT = str(Path(__file__).resolve().parent / "validate_sentence_cli.py")

SENTENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target_word": {"type": "string", "description": "The target word (bare form)"},
                    "sentences": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "arabic": {"type": "string"},
                                "english": {"type": "string"},
                                "transliteration": {"type": "string"},
                            },
                            "required": ["arabic", "english", "transliteration"],
                        },
                    },
                },
                "required": ["target_word", "sentences"],
            },
        },
    },
    "required": ["results"],
}

SYSTEM_PROMPT = """\
You are an Arabic language tutor creating MSA (fusha) sentences for reading practice. \
You generate natural Arabic sentences featuring target words, using ONLY the learner's \
known vocabulary.

You have access to two files and a validation script:
1. vocab_prompt.txt — The learner's full vocabulary, grouped by part of speech. \
Words marked "CURRENTLY LEARNING" are being actively acquired — use them as supporting \
vocabulary whenever possible.
2. vocab_lookup.tsv — Machine-readable lookup of all known word forms (for the validator)
3. validate_sentence_cli.py — Validates sentences against the vocabulary

WORKFLOW for each target word:
1. Read vocab_prompt.txt to understand which words you can use
2. Generate a sentence featuring the target word
3. Run the validator: python3 {validator} --arabic "YOUR_SENTENCE" --target-bare "TARGET" --vocab-file {work_dir}/vocab_lookup.tsv
4. If the validator says valid=false, read the issues, fix the sentence, and re-validate
5. Repeat until valid, then move to the next sentence

Arabic naturalness rules:
- Mix VSO and SVO word order. VSO is more formal; SVO more contemporary
- VSO agreement: verb matches person + gender only, NOT number
- NO copula: never insert هُوَ/هِيَ as "is" with indefinite predicates
- Idafa: first noun has NO ال and NO tanween
- Correct i'rab: nominative ضمة, accusative فتحة, genitive كسرة
- Full diacritics (tashkeel) on ALL Arabic words
- Arabic punctuation: ؟ for questions, . for statements, ، between clauses
- Transliteration: ALA-LC standard with macrons for long vowels

Vocabulary constraint:
- Use ONLY words from vocab_prompt.txt + the target word + common function words
- Common function words you may freely use: في، من، على، إلى، و، ب، ل، ك، هذا، هذه، \
ذلك، تلك، هو، هي، أنا، أنت، نحن، هم، ما، لا، أن، إن، كان، كانت، ليس، هل، لم، \
لن، قد، الذي، التي، كل، بعض، هنا، هناك، الآن، جدا، فقط، أيضا، أو، ثم، لكن
- You may use conjugated forms of verbs in the vocabulary
- You may use case endings and possessive suffixes on nouns

VOCABULARY DIVERSITY (critical):
- The vocab file has a "CURRENTLY LEARNING" section at the top. These are words the \
learner is actively acquiring. Use them as supporting vocabulary in your sentences \
whenever they fit naturally — this gives the learner extra exposure.
- VARY the supporting vocabulary across sentences. Do NOT always use the same common \
words (بيت، ولد، جديد, etc.) as filler. Spread usage across the full vocabulary.
- Each sentence should ideally include 2-3 different non-target content words from the \
vocabulary, drawn from different parts of the list each time.
- Think of each sentence as a chance to reinforce multiple words, not just the target.

Sentence quality:
- Each sentence must express a complete thought
- Vary syntactic structures (VSO, SVO, nominal, prepositional starts)
- NEVER start with هَلْ unless the target word requires a question
- Use proper names sparingly
- Keep sentences 4-10 words long
- Make sentences natural and interesting, not textbook-style"""


# ---------------------------------------------------------------------------
# Word selection
# ---------------------------------------------------------------------------

def find_words_needing_sentences(db_path: str, limit: int, per_word: int) -> list[dict]:
    """Find words that need more sentences, prioritized by FSRS due date."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT l.lemma_id, l.lemma_ar, l.lemma_ar_bare, l.gloss_en, l.pos,
               ulk.knowledge_state,
               (SELECT COUNT(*) FROM sentences s
                WHERE s.target_lemma_id = l.lemma_id AND s.is_active = 1) as active_count
        FROM lemmas l
        JOIN user_lemma_knowledge ulk ON ulk.lemma_id = l.lemma_id
        WHERE ulk.knowledge_state IN ('learning', 'known', 'acquiring', 'lapsed')
          AND l.canonical_lemma_id IS NULL
        ORDER BY
            active_count ASC,
            CASE WHEN ulk.knowledge_state = 'acquiring' THEN 0 ELSE 1 END
        LIMIT ?
    """, (limit * 3,)).fetchall()  # fetch more, then filter

    conn.close()

    words = []
    for r in rows:
        if r["active_count"] < per_word:
            words.append({
                "lemma_id": r["lemma_id"],
                "lemma_ar": r["lemma_ar"],
                "lemma_ar_bare": r["lemma_ar_bare"],
                "gloss_en": r["gloss_en"] or "",
                "pos": r["pos"] or "",
                "state": r["knowledge_state"],
                "active_count": r["active_count"],
                "needed": per_word - r["active_count"],
            })
            if len(words) >= limit:
                break

    return words


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def build_prompt(words: list[dict], per_word: int, work_dir: str) -> str:
    """Build the user prompt for a batch of words."""
    word_lines = []
    for w in words:
        word_lines.append(
            f"- {w['lemma_ar']} ({w['gloss_en']}) [bare: {w['lemma_ar_bare']}] — "
            f"generate {w['needed']} sentence(s)"
        )

    return f"""Generate validated Arabic sentences for these target words:

{chr(10).join(word_lines)}

STEPS:
1. First, read the vocabulary file at {work_dir}/vocab_prompt.txt
2. For each target word:
   a. Generate a sentence using only vocabulary from the file + function words
   b. Validate it by running: python3 {VALIDATOR_SCRIPT} --arabic "SENTENCE" --target-bare "BARE_FORM" --vocab-file {work_dir}/vocab_lookup.tsv
   c. If valid=false, look at the issues, fix the sentence, and re-validate
   d. Keep the validated sentence
3. Return all validated sentences in the structured output

Each sentence must include full diacritics and a natural English translation + ALA-LC transliteration."""


# ---------------------------------------------------------------------------
# Result storage
# ---------------------------------------------------------------------------

def _load_lookup_tsv(tsv_path: str) -> dict[str, int]:
    """Load bare_form → lemma_id lookup from the TSV file."""
    lookup: dict[str, int] = {}
    with open(tsv_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                lookup[parts[0]] = int(parts[1])
    return lookup


def _build_comprehensive_lookup(db_path: str) -> dict[str, int]:
    """Build lookup from ALL lemmas in the DB for sentence_word mapping."""
    from app.services.sentence_validator import normalize_alef, strip_diacritics

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT lemma_id, lemma_ar_bare, forms_json
        FROM lemmas WHERE canonical_lemma_id IS NULL
    """).fetchall()
    conn.close()

    lookup: dict[str, int] = {}
    for row in rows:
        bare_norm = normalize_alef(row["lemma_ar_bare"])
        lookup[bare_norm] = row["lemma_id"]
        if bare_norm.startswith("ال") and len(bare_norm) > 2:
            lookup[bare_norm[2:]] = row["lemma_id"]
        elif not bare_norm.startswith("ال"):
            lookup["ال" + bare_norm] = row["lemma_id"]

        forms_raw = row["forms_json"]
        if forms_raw:
            try:
                forms = json.loads(forms_raw) if isinstance(forms_raw, str) else forms_raw
            except (json.JSONDecodeError, TypeError):
                forms = {}
            if isinstance(forms, dict):
                for key, form_val in forms.items():
                    if key in ("plural", "present", "masdar", "active_participle",
                               "feminine", "elative") or key.startswith("variant_"):
                        if form_val and isinstance(form_val, str):
                            form_bare = normalize_alef(strip_diacritics(form_val))
                            if form_bare not in lookup:
                                lookup[form_bare] = row["lemma_id"]
                            al_form = "ال" + form_bare
                            if not form_bare.startswith("ال") and al_form not in lookup:
                                lookup[al_form] = row["lemma_id"]

    return lookup


def store_sentences(db_path: str, results: list[dict], word_map: dict[str, dict]):
    """Store generated sentences with SentenceWord records in the database."""
    from app.services.sentence_validator import (
        map_tokens_to_lemmas,
        normalize_alef,
        tokenize_display,
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Build comprehensive lookup from ALL lemmas (not just learned vocab TSV)
    lemma_lookup = _build_comprehensive_lookup(db_path)

    # Build normalized word_map for hamza-resilient matching
    norm_word_map: dict[str, dict] = {}
    for bare, info in word_map.items():
        norm_word_map[normalize_alef(bare)] = info

    stored = 0
    for item in results:
        target_bare = item.get("target_word", "")
        word_info = norm_word_map.get(normalize_alef(target_bare))
        if not word_info:
            print(f"  WARNING: Unknown target word '{target_bare}', skipping")
            continue

        for sent in item.get("sentences", []):
            arabic = sent.get("arabic", "").strip()
            english = sent.get("english", "").strip()
            translit = sent.get("transliteration", "").strip()

            if not arabic or not english:
                continue

            word_count = len(arabic.split())
            cursor = conn.execute("""
                INSERT INTO sentences (
                    arabic_text, arabic_diacritized, english_translation,
                    transliteration, target_lemma_id, source, is_active,
                    max_word_count, created_at
                ) VALUES (?, ?, ?, ?, ?, 'claude_code', 1, ?, datetime('now'))
            """, (arabic, arabic, english, translit, word_info["lemma_id"], word_count))
            sentence_id = cursor.lastrowid

            # Create SentenceWord records
            tokens = tokenize_display(arabic)
            mappings = map_tokens_to_lemmas(
                tokens=tokens,
                lemma_lookup=lemma_lookup,
                target_lemma_id=word_info["lemma_id"],
                target_bare=target_bare,
            )
            unmapped = [m.surface_form for m in mappings if m.lemma_id is None]
            if unmapped:
                print(f"  WARNING: Skipping sentence with unmapped words: {unmapped}")
                conn.execute("DELETE FROM sentences WHERE id = ?", (sentence_id,))
                continue

            for m in mappings:
                conn.execute("""
                    INSERT INTO sentence_words (
                        sentence_id, position, surface_form, lemma_id,
                        is_target_word
                    ) VALUES (?, ?, ?, ?, ?)
                """, (sentence_id, m.position, m.surface_form, m.lemma_id,
                      1 if m.is_target else 0))

            stored += 1

    conn.commit()
    conn.close()
    return stored


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate sentences using Claude Code validator-in-the-loop")
    parser.add_argument("--db", required=True, help="Path to SQLite DB")
    parser.add_argument("--words", type=int, default=10, help="Number of words to generate for (default: 10)")
    parser.add_argument("--per-word", type=int, default=DEFAULT_PER_WORD, help=f"Sentences per word (default: {DEFAULT_PER_WORD})")
    parser.add_argument("--model", default="opus", help="Claude model (default: opus)")
    parser.add_argument("--batch-size", type=int, default=WORDS_PER_SESSION, help=f"Words per Claude session (default: {WORDS_PER_SESSION})")
    parser.add_argument("--dry-run", action="store_true", help="Generate but don't store")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    args = parser.parse_args()

    if not is_available():
        print("ERROR: claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code")
        sys.exit(1)

    # Find words needing sentences
    print(f"Finding words needing sentences from {args.db}...")
    words = find_words_needing_sentences(args.db, args.words, args.per_word)
    if not words:
        print("No words need sentences.")
        return

    total_needed = sum(w["needed"] for w in words)
    print(f"  Found {len(words)} words needing {total_needed} sentences total")
    for w in words[:5]:
        print(f"    {w['lemma_ar']} ({w['gloss_en']}) — {w['state']}, has {w['active_count']}, needs {w['needed']}")
    if len(words) > 5:
        print(f"    ... and {len(words) - 5} more")

    # Dump vocabulary
    print(f"\nDumping vocabulary to {WORK_DIR}...")
    prompt_path, lookup_path = dump_vocabulary_for_claude(args.db, WORK_DIR)
    print(f"  Vocab prompt: {prompt_path}")
    print(f"  Vocab lookup: {lookup_path}")

    # Build word map for storage
    word_map = {w["lemma_ar_bare"]: w for w in words}

    # Process in batches
    all_results = []
    batches = [words[i:i + args.batch_size] for i in range(0, len(words), args.batch_size)]

    for batch_idx, batch in enumerate(batches):
        batch_label = f"Batch {batch_idx + 1}/{len(batches)}"
        batch_words_str = ", ".join(f"{w['lemma_ar']}" for w in batch)
        print(f"\n{batch_label}: {batch_words_str}")

        system = SYSTEM_PROMPT.format(
            validator=VALIDATOR_SCRIPT,
            work_dir=WORK_DIR,
        )
        prompt = build_prompt(batch, args.per_word, WORK_DIR)

        if args.verbose:
            print(f"  Prompt length: {len(prompt)} chars")
            print(f"  System prompt length: {len(system)} chars")

        start = time.time()
        try:
            result = generate_with_tools(
                prompt=prompt,
                system_prompt=system,
                json_schema=SENTENCE_SCHEMA,
                work_dir=WORK_DIR,
                model=args.model,
            )
        except Exception as e:
            print(f"  ERROR: {e}")
            continue

        elapsed = time.time() - start
        print(f"  Done in {elapsed:.1f}s")

        batch_results = result.get("results", [])
        total_sentences = sum(len(r.get("sentences", [])) for r in batch_results)
        print(f"  Generated {total_sentences} sentences for {len(batch_results)} words")

        for item in batch_results:
            target = item.get("target_word", "?")
            sents = item.get("sentences", [])
            print(f"    {target}: {len(sents)} sentences")
            if args.verbose:
                for s in sents:
                    print(f"      {s.get('arabic', '?')}")
                    print(f"      → {s.get('english', '?')}")

        all_results.extend(batch_results)

    # Store results
    if all_results and not args.dry_run:
        print(f"\nStoring sentences in {args.db}...")
        stored = store_sentences(args.db, all_results, word_map)
        print(f"  Stored {stored} sentences")
    elif args.dry_run:
        total = sum(len(r.get("sentences", [])) for r in all_results)
        print(f"\nDry run — would store {total} sentences")

    # Summary
    print(f"\n{'='*60}")
    print(f"Summary: {len(all_results)} words processed, "
          f"{sum(len(r.get('sentences', [])) for r in all_results)} sentences generated")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
