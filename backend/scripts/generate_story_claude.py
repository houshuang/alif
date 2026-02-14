#!/usr/bin/env python3
"""Generate stories using Claude Code CLI (claude -p) with Opus via Max plan.

Reliable, predictable story generation with vocabulary compliance validation
and retry loop. Uses claude -p with --tools "" (no tool access) and
--json-schema for structured output.

Usage:
    # Generate a story (uses most recent backup DB)
    python3 scripts/generate_story_claude.py --db ~/alif-backups/alif_*.db

    # Specify genre and length
    python3 scripts/generate_story_claude.py --db data/alif.db --genre mystery --sentences 6

    # Dry-run: print prompt without calling claude
    python3 scripts/generate_story_claude.py --db data/alif.db --dry-run

    # Import result into production DB via API
    python3 scripts/generate_story_claude.py --db data/alif.db --import-url http://localhost:8000
"""

import argparse
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.sentence_validator import (
    FUNCTION_WORD_GLOSSES,
    FUNCTION_WORD_FORMS,
    build_lemma_lookup,
    lookup_lemma,
    normalize_alef,
    strip_diacritics,
    strip_tatweel,
    tokenize,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_RETRIES = 3
COMPLIANCE_THRESHOLD = 70.0  # minimum % to accept without retry
CLAUDE_TIMEOUT = 120  # seconds

GENRES = [
    "a funny story with a punchline at the end",
    "a mystery — something is not what it seems",
    "a heartwarming story about an unexpected friendship",
    "a story with an ironic twist ending",
    "a short adventure with a moment of danger",
    "a story where someone learns a surprising lesson",
    "a story with a philosophical observation about daily life",
    "a story where a misunderstanding leads to an unexpected outcome",
]

STORY_JSON_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "title_ar": {"type": "string", "description": "Arabic title with diacritics"},
        "title_en": {"type": "string", "description": "English title"},
        "body_ar": {"type": "string", "description": "Full Arabic story with diacritics"},
        "body_en": {"type": "string", "description": "English translation"},
        "transliteration": {"type": "string", "description": "ALA-LC transliteration"},
    },
    "required": ["title_ar", "title_en", "body_ar", "body_en", "transliteration"],
})

SYSTEM_PROMPT = """\
You are a creative Arabic storyteller writing for language learners. Write genuinely \
engaging mini-stories in MSA (fusha) with a real narrative arc, characters, and a satisfying ending.

CRITICAL: Write a COHESIVE STORY with beginning, middle, and end. Every sentence must \
connect to the previous one and advance the narrative.

Arabic style rules:
- Full diacritics (tashkeel) on ALL Arabic words with correct i'rab case endings
- Arabic punctuation: ؟ for questions, . for statements, ، between clauses
- VSO word order for narration (ذَهَبَ الرَّجُلُ), SVO for emphasis (الرَّجُلُ ذَهَبَ وَحْدَهُ)
- Nominal sentences for scene-setting (اللَّيْلُ طَوِيلٌ)
- Use dialogue (with قَالَ/قَالَتْ) when it serves the story
- ALA-LC transliteration with macrons for long vowels

Story craft:
- Give the main character a name and a situation/problem
- Build tension or curiosity
- End with a twist, punchline, resolution, or poetic moment
- Make it genuinely interesting — not a vocabulary exercise
- Every sentence must advance the narrative

Vocabulary constraint:
- Use ONLY words from the provided vocabulary list and common function words
- Common function words you may freely use: في، من، على، إلى، و، ب، ل، ك، هذا، هذه، \
ذلك، تلك، هو، هي، أنا، أنت، نحن، هم، ما، لا، أن، إن، كان، كانت، ليس، هل، لم، \
لن، قد، الذي، التي، كل، بعض، هنا، هناك، الآن، جدا، فقط، أيضا، أو، ثم، لكن، يا
- Do NOT use Arabic content words not in the vocabulary list
- You may use conjugated forms of verbs in the vocabulary (past, present, imperative, all persons)
- You may use case endings and possessive suffixes on nouns in the vocabulary"""


# ---------------------------------------------------------------------------
# Vocabulary loading (reused from benchmark)
# ---------------------------------------------------------------------------

class SimpleLemma:
    def __init__(self, lemma_id, lemma_ar_bare, forms_json=None):
        self.lemma_id = lemma_id
        self.lemma_ar_bare = lemma_ar_bare
        self.forms_json = forms_json


def load_vocabulary(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT l.lemma_id, l.lemma_ar, l.lemma_ar_bare, l.gloss_en, l.pos,
               l.forms_json, ulk.knowledge_state, ulk.acquisition_box
        FROM lemmas l
        JOIN user_lemma_knowledge ulk ON l.lemma_id = ulk.lemma_id
        WHERE ulk.knowledge_state IN ('learning', 'known', 'acquiring')
          AND l.canonical_lemma_id IS NULL
    """).fetchall()

    usable_words = []
    acquiring_ids = set()
    usable_lemmas = []

    for r in rows:
        forms = None
        if r["forms_json"]:
            try:
                forms = json.loads(r["forms_json"]) if isinstance(r["forms_json"], str) else r["forms_json"]
            except (json.JSONDecodeError, TypeError):
                forms = None

        usable_words.append({
            "lemma_id": r["lemma_id"],
            "arabic": r["lemma_ar"],
            "arabic_bare": r["lemma_ar_bare"],
            "english": r["gloss_en"] or "",
            "pos": r["pos"] or "",
            "state": r["knowledge_state"],
            "box": r["acquisition_box"],
        })
        usable_lemmas.append(SimpleLemma(r["lemma_id"], r["lemma_ar_bare"], forms))

        if r["knowledge_state"] == "acquiring":
            acquiring_ids.add(r["lemma_id"])

    all_rows = conn.execute(
        "SELECT lemma_id, lemma_ar_bare, forms_json FROM lemmas"
    ).fetchall()
    all_lemmas = []
    for r in all_rows:
        forms = None
        if r["forms_json"]:
            try:
                forms = json.loads(r["forms_json"]) if isinstance(r["forms_json"], str) else r["forms_json"]
            except (json.JSONDecodeError, TypeError):
                forms = None
        all_lemmas.append(SimpleLemma(r["lemma_id"], r["lemma_ar_bare"], forms))

    conn.close()

    func_bares = set()
    for fw in FUNCTION_WORD_GLOSSES:
        func_bares.add(normalize_alef(fw))
    for fw in FUNCTION_WORD_FORMS:
        func_bares.add(normalize_alef(fw))

    return {
        "usable_words": usable_words,
        "acquiring_ids": acquiring_ids,
        "compliance_lookup": build_lemma_lookup(usable_lemmas),
        "all_lemma_lookup": build_lemma_lookup(all_lemmas),
        "function_word_bares": func_bares,
    }


# ---------------------------------------------------------------------------
# Vocabulary formatting
# ---------------------------------------------------------------------------

def format_vocab_grouped(words):
    groups = {"NOUNS": [], "VERBS": [], "ADJECTIVES": [], "OTHER": []}
    for w in words:
        pos = (w.get("pos") or "").lower()
        entry = f"{w['arabic']} ({w['english']})"
        if pos in ("noun", "noun_prop"):
            groups["NOUNS"].append(entry)
        elif pos == "verb":
            groups["VERBS"].append(entry)
        elif pos in ("adj", "adj_comp"):
            groups["ADJECTIVES"].append(entry)
        else:
            groups["OTHER"].append(entry)
    lines = []
    for label, items in groups.items():
        if items:
            lines.append(f"{label}: {', '.join(items)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Compliance checking
# ---------------------------------------------------------------------------

def check_compliance(body_ar, compliance_lookup, function_word_bares, acquiring_ids):
    tokens = tokenize(body_ar)
    content_total = 0
    content_known = 0
    unknown_list = []

    for token in tokens:
        bare = normalize_alef(strip_tatweel(strip_diacritics(token)))
        if bare in function_word_bares:
            continue
        if bare in {normalize_alef(k) for k in FUNCTION_WORD_FORMS}:
            continue

        content_total += 1
        lid = lookup_lemma(bare, compliance_lookup)
        if lid:
            content_known += 1
        else:
            if bare not in unknown_list:
                unknown_list.append(bare)

    pct = round(content_known / content_total * 100, 1) if content_total > 0 else 0
    return {
        "compliance_pct": pct,
        "content_total": content_total,
        "content_known": content_known,
        "unknown_words": unknown_list,
        "word_count": len(tokens),
    }


# ---------------------------------------------------------------------------
# Claude CLI wrapper
# ---------------------------------------------------------------------------

def call_claude(prompt, model="opus"):
    """Call claude -p and return structured output.

    Returns (story_dict, metadata) or raises on failure.
    """
    cmd = [
        "claude", "-p",
        "--tools", "",
        "--output-format", "json",
        "--model", model,
        "--no-session-persistence",
        "--json-schema", STORY_JSON_SCHEMA,
        "--system-prompt", SYSTEM_PROMPT,
    ]

    start = time.time()
    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=CLAUDE_TIMEOUT,
    )
    elapsed = time.time() - start

    if proc.returncode != 0:
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[:500]}")

    response = json.loads(proc.stdout)

    if response.get("is_error"):
        raise RuntimeError(f"claude error: {response.get('result', 'unknown')}")

    story = response.get("structured_output")
    if not story:
        # Fall back to parsing result text
        text = response.get("result", "")
        text = re.sub(r"^```(?:json)?\s*", "", text.strip())
        text = re.sub(r"\s*```$", "", text)
        if text:
            story = json.loads(text)

    if not story or not story.get("body_ar"):
        raise RuntimeError("Empty story returned")

    metadata = {
        "duration_ms": int(elapsed * 1000),
        "cost_usd": response.get("total_cost_usd", 0),
        "num_turns": response.get("num_turns", 0),
        "session_id": response.get("session_id", ""),
    }
    return story, metadata


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def build_prompt(words, genre, num_sentences, retry_feedback=None):
    vocab = format_vocab_grouped(words)

    # Highlight acquiring words
    acquiring = [w for w in words if w["state"] == "acquiring"]
    acquiring_section = ""
    if acquiring:
        acq_list = ", ".join(f"{w['arabic']} ({w['english']})" for w in acquiring[:15])
        acquiring_section = f"""
REINFORCEMENT WORDS (the reader is currently learning these — try to feature them prominently):
{acq_list}
"""

    retry_section = ""
    if retry_feedback:
        retry_section = f"""
IMPORTANT CORRECTION: Your previous attempt used words NOT in the vocabulary list.
These words are NOT allowed: {', '.join(retry_feedback['unknown_words'][:20])}
Please rewrite using ONLY words from the vocabulary list below. Replace unknown words
with synonyms from the list, or restructure sentences to avoid them.
Previous compliance was {retry_feedback['compliance_pct']}% — aim for 90%+.
"""

    prompt = f"""{retry_section}Write a cohesive mini-story ({num_sentences} sentences) for a beginner Arabic learner.

GENRE: {genre}
{acquiring_section}
VOCABULARY (use ONLY these content words, plus common function words):
{vocab}

RULES:
- Use ONLY words from the vocabulary list above (any conjugated form is fine)
- Write a REAL STORY with narrative arc: setup → development → resolution/punchline
- Give the main character a name
- Include full diacritics (tashkeel) on ALL Arabic words
- Make it genuinely interesting — an adult should enjoy reading it
- Every sentence must connect to the next"""

    return prompt


# ---------------------------------------------------------------------------
# Main generation loop
# ---------------------------------------------------------------------------

def generate_story(db_path, genre=None, num_sentences=6, model="opus", dry_run=False, verbose=False,
                    max_retries=MAX_RETRIES, compliance_threshold=COMPLIANCE_THRESHOLD):
    print(f"Loading vocabulary from {db_path}...")
    vocab = load_vocabulary(db_path)
    words = vocab["usable_words"]
    print(f"  {len(words)} usable words ({len(vocab['acquiring_ids'])} acquiring)")

    if not genre:
        genre = random.choice(GENRES)
    print(f"  Genre: {genre}")
    print(f"  Sentences: {num_sentences}")

    best_story = None
    best_compliance = 0

    for attempt in range(max_retries):
        retry_feedback = None
        if attempt > 0 and best_story:
            last_check = check_compliance(
                best_story["body_ar"],
                vocab["compliance_lookup"],
                vocab["function_word_bares"],
                vocab["acquiring_ids"],
            )
            retry_feedback = {
                "unknown_words": last_check["unknown_words"],
                "compliance_pct": last_check["compliance_pct"],
            }

        prompt = build_prompt(words, genre, num_sentences, retry_feedback)

        if dry_run:
            print(f"\n{'='*60}")
            print("DRY RUN — Prompt that would be sent to claude -p:")
            print(f"{'='*60}")
            print(prompt)
            print(f"{'='*60}")
            print(f"System prompt length: {len(SYSTEM_PROMPT)} chars")
            print(f"User prompt length: {len(prompt)} chars")
            return None

        attempt_label = f"Attempt {attempt + 1}/{MAX_RETRIES}"
        if retry_feedback:
            attempt_label += f" (prev: {retry_feedback['compliance_pct']}%, {len(retry_feedback['unknown_words'])} unknown)"
        print(f"\n  {attempt_label}...", end=" ", flush=True)

        try:
            story, meta = call_claude(prompt, model=model)
        except Exception as e:
            print(f"FAIL: {e}")
            continue

        compliance = check_compliance(
            story["body_ar"],
            vocab["compliance_lookup"],
            vocab["function_word_bares"],
            vocab["acquiring_ids"],
        )

        pct = compliance["compliance_pct"]
        n_unknown = len(compliance["unknown_words"])
        print(f"OK ({meta['duration_ms']}ms, ${meta['cost_usd']:.3f})")
        print(f"    Compliance: {pct}% ({compliance['content_known']}/{compliance['content_total']} content words)")
        if compliance["unknown_words"]:
            print(f"    Unknown: {', '.join(compliance['unknown_words'][:10])}")

        if pct > best_compliance:
            best_compliance = pct
            best_story = story
            best_story["_compliance"] = compliance
            best_story["_meta"] = meta

        if pct >= compliance_threshold:
            print(f"    Above threshold ({compliance_threshold}%)")
            break
        else:
            print(f"    Below threshold ({compliance_threshold}%), retrying...")

    if not best_story:
        print("\nFailed to generate a story after all attempts.")
        return None

    print(f"\n{'='*60}")
    print(f"BEST STORY (compliance: {best_compliance}%)")
    print(f"{'='*60}")
    print(f"\nTitle: {best_story['title_ar']}")
    print(f"       {best_story['title_en']}")
    print(f"\n{best_story['body_ar']}")
    print(f"\n{best_story['body_en']}")
    print(f"\n{best_story.get('transliteration', '')}")

    if verbose:
        c = best_story["_compliance"]
        print(f"\nCompliance: {c['compliance_pct']}%")
        print(f"Content words: {c['content_total']} ({c['content_known']} known)")
        print(f"Total words: {c['word_count']}")
        if c["unknown_words"]:
            print(f"Unknown: {', '.join(c['unknown_words'])}")

    return best_story


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate stories using claude -p")
    parser.add_argument("--db", required=True, help="Path to SQLite DB (backup or production)")
    parser.add_argument("--genre", help="Story genre (random if not specified)")
    parser.add_argument("--sentences", type=int, default=6, help="Number of sentences (default: 6)")
    parser.add_argument("--model", default="opus", help="Claude model alias (default: opus)")
    parser.add_argument("--retries", type=int, default=MAX_RETRIES, help=f"Max retries (default: {MAX_RETRIES})")
    parser.add_argument("--threshold", type=float, default=COMPLIANCE_THRESHOLD, help=f"Min compliance %% (default: {COMPLIANCE_THRESHOLD})")
    parser.add_argument("--dry-run", action="store_true", help="Print prompt without calling claude")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed compliance info")
    parser.add_argument("--output", "-o", help="Save story JSON to file")
    parser.add_argument("--import-url", help="Import into production via API (e.g. http://localhost:8000)")
    args = parser.parse_args()

    story = generate_story(
        db_path=args.db,
        genre=args.genre,
        num_sentences=args.sentences,
        model=args.model,
        dry_run=args.dry_run,
        verbose=args.verbose,
        max_retries=args.retries,
        compliance_threshold=args.threshold,
    )

    if not story:
        sys.exit(1)

    # Save to file
    if args.output:
        clean = {k: v for k, v in story.items() if not k.startswith("_")}
        Path(args.output).write_text(json.dumps(clean, ensure_ascii=False, indent=2))
        print(f"\nSaved to {args.output}")

    # Import via API
    if args.import_url:
        import urllib.request
        url = f"{args.import_url.rstrip('/')}/api/stories/import"
        data = json.dumps({"arabic_text": story["body_ar"], "title": story["title_ar"]}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:
                result = json.loads(resp.read())
                print(f"\nImported as story #{result.get('id', '?')} (readiness: {result.get('readiness_pct', '?')}%)")
        except Exception as e:
            print(f"\nImport failed: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
