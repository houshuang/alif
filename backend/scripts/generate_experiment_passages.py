#!/usr/bin/env python3
"""Generate 5 mini-stories for the passage reading speed experiment.

Each story: exactly 4 sentences, comfortable vocabulary (FSRS stability ≥ 7d),
plus 1 "challenge" word (stability 1-7d) per story.

Usage:
    # Dry-run: see the prompt and vocabulary stats
    python3 scripts/generate_experiment_passages.py --db ~/alif-backups/alif_20260303_090005.db --dry-run

    # Generate all 5 stories
    python3 scripts/generate_experiment_passages.py --db ~/alif-backups/alif_20260303_090005.db

    # Generate and import into production
    python3 scripts/generate_experiment_passages.py --db ~/alif-backups/alif_20260303_090005.db \
        --import-url http://46.225.75.29:3000

See: research/experiment-passage-reading-speed.md
"""

import argparse
import json
import random
import sqlite3
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

# Reuse the Claude CLI wrapper and compliance check from generate_story_claude
from scripts.generate_story_claude import (
    SimpleLemma,
    call_claude,
    check_compliance,
    format_vocab_grouped,
    STORY_JSON_SCHEMA,
)

MAX_RETRIES = 3
COMPLIANCE_THRESHOLD = 75.0

GENRES = [
    "a short poem (4 lines) — lyrical, evocative imagery, each line a complete thought",
    "a joke or riddle with a punchline — setup, misdirection, payoff",
    "a tiny fable where an animal teaches a surprising lesson",
    "a love letter or confession — passionate, specific, bittersweet",
    "a dramatic monologue — someone speaking at a pivotal moment in their life",
    "a ghost story or supernatural encounter — atmospheric and eerie",
    "a diary entry from an unusual day — intimate voice, vivid detail",
    "a speech by a village elder giving life advice — wise and poetic",
    "a scene from a bazaar — haggling, colors, smells, a twist",
    "a letter home from someone far away — nostalgia and longing",
]

SYSTEM_PROMPT = """\
You are a master Arabic storyteller — think Naguib Mahfouz writing flash fiction, \
or Mahmoud Darwish writing prose poetry. Write for an adult reader who happens to be \
learning Arabic. The writing must be genuinely good: vivid, surprising, emotionally resonant.

ABSOLUTE RULES:
- EXACTLY 4 sentences (or 4 lines for poems). Not 3, not 5.
- Each sentence: 5-10 words (allow slightly longer for natural flow)
- Full diacritics (tashkeel) on ALL Arabic words with correct i'rab
- Arabic punctuation: ؟ . ، ! — use naturally
- Use dialogue with «» when it serves the piece
- ALA-LC transliteration with macrons

CRAFT:
- Every word must earn its place — no filler, no "vocabulary exercise" feeling
- Use concrete sensory details (a specific color, sound, smell)
- Vary sentence rhythm — mix short punchy sentences with flowing ones
- Characters need names AND a specific situation (not "Ali went to school" generics)
- The ending must land — a twist, a punchline, an image that lingers

WHAT MAKES IT BAD (avoid these):
- Generic school/classroom settings (overused)
- "Ali did X. Ali felt Y. Ali saw Z." repetitive structure
- Explaining the point — trust the reader
- Flat descriptions with no tension or surprise

Vocabulary constraint:
- Use ONLY words from the provided vocabulary list and common function words
- Common function words you may freely use: في، من، على، إلى، و، ب، ل، ك، هذا، هذه، \
ذلك، تلك، هو، هي، أنا، أنت، نحن، هم، ما، لا، أن، إن، كان، كانت، ليس، هل، لم، \
لن، قد، الذي، التي، كل، بعض، هنا، هناك، الآن، جدا، فقط، أيضا، أو، ثم، لكن، يا
- You may use conjugated forms of verbs (past, present, imperative, all persons)
- You may use case endings and possessive suffixes on nouns"""


def load_experiment_vocabulary(db_path):
    """Load vocabulary split by stability tier."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Strong words: FSRS stability ≥ 7 days
    strong_rows = conn.execute("""
        SELECT l.lemma_id, l.lemma_ar, l.lemma_ar_bare, l.gloss_en, l.pos,
               l.forms_json, ulk.knowledge_state,
               CAST(json_extract(ulk.fsrs_card_json, '$.stability') AS REAL) as stability
        FROM lemmas l
        JOIN user_lemma_knowledge ulk ON l.lemma_id = ulk.lemma_id
        WHERE ulk.knowledge_state IN ('learning', 'known')
          AND l.canonical_lemma_id IS NULL
          AND ulk.fsrs_card_json IS NOT NULL
          AND CAST(json_extract(ulk.fsrs_card_json, '$.stability') AS REAL) >= 7.0
    """).fetchall()

    # Challenge words: FSRS stability 1-7 days (graduated but fragile)
    challenge_rows = conn.execute("""
        SELECT l.lemma_id, l.lemma_ar, l.lemma_ar_bare, l.gloss_en, l.pos,
               l.forms_json, ulk.knowledge_state,
               CAST(json_extract(ulk.fsrs_card_json, '$.stability') AS REAL) as stability
        FROM lemmas l
        JOIN user_lemma_knowledge ulk ON l.lemma_id = ulk.lemma_id
        WHERE ulk.knowledge_state IN ('learning', 'known')
          AND l.canonical_lemma_id IS NULL
          AND ulk.fsrs_card_json IS NOT NULL
          AND CAST(json_extract(ulk.fsrs_card_json, '$.stability') AS REAL) >= 1.0
          AND CAST(json_extract(ulk.fsrs_card_json, '$.stability') AS REAL) < 7.0
    """).fetchall()

    def row_to_word(r):
        return {
            "lemma_id": r["lemma_id"],
            "arabic": r["lemma_ar"],
            "arabic_bare": r["lemma_ar_bare"],
            "english": r["gloss_en"] or "",
            "pos": r["pos"] or "",
            "state": r["knowledge_state"],
            "stability": r["stability"],
        }

    strong_words = [row_to_word(r) for r in strong_rows]
    challenge_words = [row_to_word(r) for r in challenge_rows]

    # Build compliance lookup from strong + challenge words
    all_usable_rows = strong_rows + challenge_rows
    usable_lemmas = []
    for r in all_usable_rows:
        forms = None
        if r["forms_json"]:
            try:
                forms = json.loads(r["forms_json"]) if isinstance(r["forms_json"], str) else r["forms_json"]
            except (json.JSONDecodeError, TypeError):
                forms = None
        usable_lemmas.append(SimpleLemma(r["lemma_id"], r["lemma_ar_bare"], forms))

    # Also load all lemmas for the full lookup
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
        "strong_words": strong_words,
        "challenge_words": challenge_words,
        "compliance_lookup": build_lemma_lookup(usable_lemmas),
        "all_lemma_lookup": build_lemma_lookup(all_lemmas),
        "function_word_bares": func_bares,
    }


def build_experiment_prompt(strong_words, challenge_word, genre, retry_feedback=None):
    """Build prompt for a single 4-sentence story."""
    vocab = format_vocab_grouped(strong_words)

    retry_section = ""
    if retry_feedback:
        retry_section = f"""
IMPORTANT CORRECTION: Your previous attempt used words NOT in the vocabulary list.
These words are NOT allowed: {', '.join(retry_feedback['unknown_words'][:15])}
Please rewrite using ONLY words from the vocabulary list below.
Previous compliance was {retry_feedback['compliance_pct']}% — aim for 90%+.
"""

    prompt = f"""{retry_section}Write EXACTLY 4 sentences (or 4 lines if a poem).

GENRE/FORM: {genre}

FEATURED WORD (MUST appear naturally):
{challenge_word['arabic']} ({challenge_word['english']}) [{challenge_word['pos']}]

VOCABULARY (use ONLY these content words, plus common function words):
{vocab}

CONSTRAINTS:
- EXACTLY 4 sentences/lines
- Each: 5-10 words
- The featured word MUST appear at least once
- ONLY vocabulary list words (any conjugated form is fine)
- Full diacritics (tashkeel) on ALL Arabic words
- NO generic school/classroom settings — be specific and vivid
- Give characters real names (not just Ali) and specific situations
- The ending must surprise, move, or delight the reader"""

    return prompt


def generate_experiment_stories(db_path, model="opus", dry_run=False, num_stories=5):
    """Generate all experiment stories."""
    print(f"Loading vocabulary from {db_path}...")
    vocab = load_experiment_vocabulary(db_path)

    strong = vocab["strong_words"]
    challenges = vocab["challenge_words"]

    print(f"  Strong words (stability ≥ 7d): {len(strong)}")
    print(f"  Challenge words (stability 1-7d): {len(challenges)}")

    if len(challenges) < num_stories:
        print(f"  WARNING: Only {len(challenges)} challenge words available, need {num_stories}")
        num_stories = len(challenges)

    # Pick challenge words — prefer content-rich POS (nouns, verbs, adjectives)
    content_challenges = [w for w in challenges if w["pos"] in ("noun", "verb", "adj", "adj_comp")]
    if len(content_challenges) >= num_stories:
        random.shuffle(content_challenges)
        selected_challenges = content_challenges[:num_stories]
    else:
        random.shuffle(challenges)
        selected_challenges = challenges[:num_stories]

    print(f"\n  Selected challenge words:")
    for i, w in enumerate(selected_challenges):
        print(f"    {i+1}. {w['arabic']} ({w['english']}) — stability: {w['stability']:.1f}d")

    if dry_run:
        prompt = build_experiment_prompt(strong, selected_challenges[0], GENRES[0])
        print(f"\n{'='*60}")
        print("DRY RUN — Sample prompt:")
        print(f"{'='*60}")
        print(prompt[:2000])
        print(f"...\n[{len(prompt)} chars total, {len(strong)} strong words in vocab]")
        return []

    stories = []
    for i, challenge in enumerate(selected_challenges):
        genre = GENRES[i % len(GENRES)]
        print(f"\n--- Story {i+1}/{num_stories}: {challenge['arabic']} ({challenge['english']}) ---")
        print(f"    Genre: {genre}")

        best_story = None
        best_compliance = 0

        for attempt in range(MAX_RETRIES):
            retry_feedback = None
            if attempt > 0 and best_story:
                last_check = check_compliance(
                    best_story["body_ar"],
                    vocab["compliance_lookup"],
                    vocab["function_word_bares"],
                    set(),  # no acquiring IDs in this experiment
                )
                retry_feedback = {
                    "unknown_words": last_check["unknown_words"],
                    "compliance_pct": last_check["compliance_pct"],
                }

            prompt = build_experiment_prompt(strong, challenge, genre, retry_feedback)

            label = f"  Attempt {attempt + 1}/{MAX_RETRIES}"
            if retry_feedback:
                label += f" (prev: {retry_feedback['compliance_pct']}%)"
            print(f"{label}...", end=" ", flush=True)

            try:
                story, meta = call_claude(prompt, model=model)
            except Exception as e:
                print(f"FAIL: {e}")
                continue

            compliance = check_compliance(
                story["body_ar"],
                vocab["compliance_lookup"],
                vocab["function_word_bares"],
                set(),
            )

            pct = compliance["compliance_pct"]
            print(f"OK ({meta['duration_ms']}ms, ${meta['cost_usd']:.3f})")
            print(f"    Compliance: {pct}% ({compliance['content_known']}/{compliance['content_total']})")

            # Count sentences
            sentences = [s.strip() for s in story["body_ar"].split(".") if s.strip()]
            print(f"    Sentences: {len(sentences)}")

            if compliance["unknown_words"]:
                print(f"    Unknown: {', '.join(compliance['unknown_words'][:8])}")

            if pct > best_compliance:
                best_compliance = pct
                best_story = story
                best_story["_compliance"] = compliance
                best_story["_meta"] = meta
                best_story["_challenge_word"] = challenge

            if pct >= COMPLIANCE_THRESHOLD:
                break

        if best_story:
            stories.append(best_story)
            print(f"\n  Title: {best_story['title_en']}")
            print(f"  Arabic: {best_story['body_ar'][:120]}...")
        else:
            print(f"  FAILED to generate story for {challenge['arabic']}")

    return stories


def import_story(story, import_url):
    """Import a story via the API."""
    import urllib.request

    url = f"{import_url.rstrip('/')}/api/stories/import"
    # Tag the title so we can identify experiment stories
    title = f"[EXP] {story.get('title_ar', '')}"
    data = json.dumps({
        "arabic_text": story["body_ar"],
        "title": title,
        "english_text": story.get("body_en", ""),
        "transliteration": story.get("transliteration", ""),
    }).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
        return result


def main():
    parser = argparse.ArgumentParser(description="Generate experiment passage stories")
    parser.add_argument("--db", required=True, help="Path to SQLite DB backup")
    parser.add_argument("--model", default="opus", help="Claude model (default: opus)")
    parser.add_argument("--dry-run", action="store_true", help="Show prompt without generating")
    parser.add_argument("--num-stories", type=int, default=5, help="Number of stories (default: 5)")
    parser.add_argument("--output", "-o", help="Save all stories as JSON to file")
    parser.add_argument("--import-url", help="Import into production (e.g. http://46.225.75.29:3000)")
    args = parser.parse_args()

    stories = generate_experiment_stories(
        db_path=args.db,
        model=args.model,
        dry_run=args.dry_run,
        num_stories=args.num_stories,
    )

    if not stories:
        if not args.dry_run:
            print("\nNo stories generated.")
            sys.exit(1)
        return

    # Summary
    print(f"\n{'='*60}")
    print(f"GENERATED {len(stories)} EXPERIMENT STORIES")
    print(f"{'='*60}")
    for i, s in enumerate(stories):
        c = s.get("_challenge_word", {})
        comp = s.get("_compliance", {})
        print(f"\n{i+1}. {s['title_en']}")
        print(f"   Challenge: {c.get('arabic', '?')} ({c.get('english', '?')})")
        print(f"   Compliance: {comp.get('compliance_pct', '?')}%")
        print(f"   Arabic: {s['body_ar'][:100]}...")

    # Save to file
    if args.output:
        clean = []
        for s in stories:
            clean.append({
                k: v for k, v in s.items() if not k.startswith("_")
            })
        Path(args.output).write_text(json.dumps(clean, ensure_ascii=False, indent=2))
        print(f"\nSaved to {args.output}")

    # Import to production
    if args.import_url:
        print(f"\nImporting to {args.import_url}...")
        for i, s in enumerate(stories):
            try:
                result = import_story(s, args.import_url)
                sid = result.get("id", "?")
                readiness = result.get("readiness_pct", "?")
                print(f"  Story {i+1}: imported as #{sid} (readiness: {readiness}%)")
            except Exception as e:
                print(f"  Story {i+1}: import failed — {e}")


if __name__ == "__main__":
    main()
