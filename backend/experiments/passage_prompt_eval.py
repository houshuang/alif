#!/usr/bin/env python3
"""Read-only passage prompt evaluation for generated maintenance passages.

Runs a small prompt sweep against the production learner vocabulary, validates
outputs, and emits JSON results. It does not write stories, sentences, or review
state.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.database import SessionLocal
from app.services.llm import generate_completion
from app.services.passage_generator import (
    _due_maintenance_targets,
    _eligible_passage_words,
)
from app.services.sentence_self_correct import _write_batch_files, _write_validator_script
from app.services.sentence_validator import (
    build_comprehensive_lemma_lookup,
    build_lemma_lookup,
    map_tokens_to_lemmas,
    normalize_alef,
    strip_diacritics,
    tokenize_display,
    validate_sentence_multi_target,
)


SCHEMA = {
    "type": "object",
    "properties": {
        "title_ar": {"type": "string"},
        "title_en": {"type": "string"},
        "style_tag": {"type": "string"},
        "premise": {"type": "string"},
        "selected_target_lemma_ids": {
            "type": "array",
            "items": {"type": "integer"},
        },
        "sentences": {
            "type": "array",
            "minItems": 3,
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "arabic": {"type": "string"},
                    "english": {"type": "string"},
                    "target_lemma_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
                "required": ["arabic", "english"],
            },
        },
    },
    "required": [
        "title_ar",
        "title_en",
        "style_tag",
        "premise",
        "selected_target_lemma_ids",
        "sentences",
    ],
}


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "cohesion": {"type": "integer", "minimum": 1, "maximum": 5},
        "reward": {"type": "integer", "minimum": 1, "maximum": 5},
        "beauty": {"type": "integer", "minimum": 1, "maximum": 5},
        "forcedness": {"type": "integer", "minimum": 1, "maximum": 5},
        "disconnected_examples": {"type": "boolean"},
        "translation_correct": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": [
        "cohesion",
        "reward",
        "beauty",
        "forcedness",
        "disconnected_examples",
        "translation_correct",
        "reason",
    ],
}


SYSTEM_BASE = """\
You are a brilliant Arabic (MSA / fusha) miniaturist: think Borges flash
fiction with a very limited palette. The constraint is the creative challenge.

The task is to create one tiny, cohesive, rewarding learner passage. This is
reading, not a drill. No exercises, no grammar explanations, no questions for
the learner.

Hard priorities, in order:
1. Coherence and beauty: one miniature scene, memory, observation, joke, or fact
   with a beginning, middle, and end.
2. Natural Arabic with full tashkeel.
3. Vocabulary validity: only learner vocabulary, selected target words, and
   function words.
4. Target coverage: useful, but never at the expense of the passage.

It is better to skip awkward due words than force them. Skipped words will get
single-sentence review later.

The last sentence matters most. Land a small ending: a turn, a joke, a
bittersweet observation, a useful final fact, or quiet closure. If the sentences
could be shuffled without harming the passage, the passage has failed.

Keep physical and narrative logic plausible. Do not make foods "melt" unless
the food really melts, do not make animals suddenly become huge, and do not
combine random objects for surreal effect unless the whole passage clearly earns
that effect.
"""


VARIANTS = {
    "subset_anchor": """\
Pick 1-4 candidate target words that naturally belong together. Prefer one
target word that can recur naturally over four awkward one-off targets. Write 4
connected sentences. Keep one recurring lexical anchor across the passage: a
concrete object, place, person role, image, or topic word must appear in at
least two sentences. The same selected target may appear in several sentences.
Every sentence should advance the same scene or idea.
""",
    "premise_first": """\
First silently choose a one-sentence premise that can carry 1-3 target words
without strain. Then write 4 connected sentences from that premise. If a word
does not serve the premise, drop it. Reuse the best anchor target naturally
rather than assigning a different target to every sentence. The final sentence
should have a small payoff, turn, or emotional closure.
""",
    "literary_micro": """\
Write a small literary micro-scene rather than examples. Prefer nostalgia,
gentle humor, surprise, or a concrete human observation. Use fewer target words
if needed, even one target repeated well. Repeat a meaningful image or topic
across the passage so it reads as one text.
""",
    "single_anchor_story": """\
Choose exactly one strong target word as the passage anchor, plus at most two
secondary targets only if they fit effortlessly. Write 4 connected sentences
around the anchor. The anchor may appear in every sentence. The passage should
read like a tiny memory, scene, or observation, not vocabulary coverage.
""",
    "support_anchor_one_target": """\
Choose one concrete support-vocabulary anchor first: a place, object, person
role, season, or situation that can recur across the passage. Then choose one
candidate target word that fits that scene literally and naturally. Use that
target once, or twice at most. The other sentences may contain no target word.
The passage should read like a real tiny memory or observation, not a drill.
""",
    "story_service_micro": """\
Write a true micro-story with beginning, middle, and end. The limited vocabulary
is the creative constraint, not an excuse for boredom. Pick only the target
word or target words that serve a concrete situation. Use concrete details,
warmth, irony, suspense, a small twist, or poetry. The last sentence must matter.
Do not write generic examples, inventories, or a parade of due words.
""",
    "premise_scout": """\
In scratch, create at least five possible pairings of target subset + premise.
Reject any pairing that would become disconnected examples or forced coverage.
Choose the premise an adult would most want to finish reading. Then write four
sentences from that premise, using as few due targets as necessary. Sentence
order must matter.
""",
    "scene_or_fact": """\
First decide whether the target pool best supports a tiny scene, a funny moment,
a nostalgic memory, or a compact informative paragraph. Choose that genre
opportunistically. Use one recurring support-vocabulary anchor and only the due
target words that fit naturally. End with either a small reveal or a useful
closing fact.
""",
    "one_due_story": """\
Choose exactly one story-suitable due target as the anchor. Do not use any other
candidate due target in the Arabic passage. Build the rest of the passage from
support vocabulary. The selected target may appear in several sentences if that
helps cohesion, but it should feel like part of the scene rather than a drill.
Write a tiny adult-readable story, memory, joke, observation, or fact with a
beginning, middle, and end.
""",
    "realistic_one_due": """\
Choose exactly one story-suitable due target. Write a realistic tiny scene or
observation for an adult reader. No slapstick transformation, no impossible
object behavior, no disconnected metaphor, no random kitchen/food chain unless
the cause and effect are obvious. The ending should reframe the scene gently,
not add a new target word.
""",
}


_PASSAGE_STRONG_GLOSS_CUES = (
    "animal",
    "book",
    "cantaloupe",
    "cat",
    "coffee",
    "cup",
    "door",
    "garden",
    "house",
    "kitchen",
    "market",
    "pear",
    "pears",
    "rat",
    "racket",
    "room",
    "shoe",
    "slipper",
    "table",
    "tree",
    "window",
)

_PASSAGE_HARD_GLOSS_CUES = (
    "feminine",
    "masculine",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "measles",
    "smallpox",
    "thigh",
    "index finger",
    "folkloric",
    "contribute",
    "is located",
    "read it",
)


def _passage_target_story_score(word: dict[str, Any]) -> int:
    pos = str(word.get("pos") or "").lower()
    gloss = str(word.get("english") or "").lower()
    score = 0
    if "noun" in pos:
        score += 4
    if "verb" in pos:
        score += 2
    if "adj" in pos or "adjective" in pos:
        score -= 1
    if any(cue in gloss for cue in _PASSAGE_STRONG_GLOSS_CUES):
        score += 4
    if any(cue in gloss for cue in _PASSAGE_HARD_GLOSS_CUES):
        score -= 5
    if gloss.startswith("to "):
        score += 1
    if len(gloss) > 28:
        score -= 1
    return score


def _rank_targets_for_passage(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        words,
        key=lambda w: (
            -_passage_target_story_score(w),
            int(w["lemma_id"]),
        ),
    )


def _agent_rows(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "lemma_id": int(w["lemma_id"]),
            "lemma_ar": w["arabic"],
            "lemma_ar_bare": w.get("arabic_bare") or strip_diacritics(w["arabic"]),
            "gloss_en": w.get("english") or "",
            "pos": w.get("pos") or "",
            "forms_json": w.get("forms_json"),
            "knowledge_state": w.get("state") or "known",
        }
        for w in words
    ]


def _agent_targets(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "target_lemma_id": int(w["lemma_id"]),
            "target_word": w["arabic"],
            "target_bare": w.get("arabic_bare") or strip_diacritics(w["arabic"]),
            "target_translation": w.get("english") or "",
            "pos": w.get("pos") or "",
            "state": w.get("state") or "known",
        }
        for w in words
    ]


def _load_pool(limit: int, target_ids: list[int] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    db = SessionLocal()
    try:
        targets = _due_maintenance_targets(db, limit=limit)
        eligible = _eligible_passage_words(db)
        if target_ids:
            by_id = {int(w["lemma_id"]): w for w in eligible}
            targets = [by_id[lid] for lid in target_ids if lid in by_id]
        targets = _rank_targets_for_passage(targets)
        return (targets, eligible)
    finally:
        db.close()


def _run_generation(
    variant_name: str,
    variant_prompt: str,
    style: str,
    targets: list[dict[str, Any]],
    eligible: list[dict[str, Any]],
    model: str,
    feedback: str | None = None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"passage-eval-{variant_name}-") as work_dir:
        _write_batch_files(_agent_rows(eligible), work_dir, _agent_targets(targets), prompt_sample_size=320)
        _write_validator_script(work_dir)
        prompt = f"""\
Create exactly 4 connected Arabic sentence objects.

Style target: {style}

Variant instruction:
{variant_prompt}

Files:
- Candidate target pool: {work_dir}/targets.json
- Supporting vocabulary: {work_dir}/vocab_prompt.txt
- Validator: python3 {work_dir}/validator.py "<arabic sentence>" "<target_bare>"

Workflow:
1. Read targets.json and vocab_prompt.txt.
2. In scratch only, compare several possible target groups and premises.
3. Choose the subset and premise that makes the best passage. Prefer one due
   target. Add a second due target only if it improves the passage. Never use a
   third due target.
4. Draft the passage.
5. At least one sentence must contain a selected target word. Connector
   sentences may contain only support vocabulary when that makes the passage
   read naturally.
6. Validate every sentence with validator.py. Use a selected target as the
   validator target when present; otherwise use the repeated support anchor.
   If a sentence fails because of
   unknown_words, replace only the offending words and revalidate.
7. Keep the scene physically plausible and narratively motivated. No sudden
   giant animals, no melting non-melting foods, no random object pairings.
8. Return JSON only. Include the chosen premise.

{f'''Previous rejected draft/editor feedback:
{feedback}

Use this feedback to revise the premise or target choice. Do not repeat the same
failure pattern.
''' if feedback else ''}
"""
        from limbic.cerebellum.claude_cli import generate as limbic_generate

        result, _meta = limbic_generate(
            prompt=prompt,
            project="alif",
            purpose="passage_prompt_eval",
            system=SYSTEM_BASE,
            schema=SCHEMA,
            model=model,
            tools="Read,Bash",
            allowed_tools="Bash Read",
            max_budget=0.60,
            work_dir=work_dir,
            dangerously_skip_permissions=False,
            timeout=300,
        )
        return result


def _validate_result(result: dict[str, Any], targets: list[dict[str, Any]], eligible: list[dict[str, Any]]) -> dict[str, Any]:
    db = SessionLocal()
    try:
        target_bares = {
            normalize_alef(strip_diacritics(w["arabic"])): int(w["lemma_id"])
            for w in targets
        }
        target_order = [int(w["lemma_id"]) for w in targets]
        target_bare_by_id = {lid: bare for bare, lid in target_bares.items()}
        all_ids = {int(w["lemma_id"]) for w in eligible} | set(target_order)
        from app.models import Lemma

        all_lemmas = db.query(Lemma).filter(Lemma.lemma_id.in_(all_ids)).all()
        story_lookup = build_lemma_lookup(all_lemmas)
        allowed_bare_forms = set(story_lookup.keys())
        mapping_lookup = build_comprehensive_lemma_lookup(db)
        sentences = result.get("sentences") or []
        errors: list[str] = []
        used_targets: set[int] = set()
        per_sentence_content_ids: list[set[int]] = []
        for idx, sent in enumerate(sentences):
            arabic = str(sent.get("arabic") or "").strip()
            validation = validate_sentence_multi_target(
                arabic_text=arabic,
                target_bares=target_bares,
                known_bare_forms=allowed_bare_forms,
                min_targets=0,
                known_lemma_lookup=story_lookup,
                comprehensive_lemma_lookup=mapping_lookup,
            )
            found = [target_bares[bare] for bare, ok in validation.targets_found.items() if ok]
            used_targets.update(found)
            if not validation.valid and validation.unknown_words:
                errors.append(f"s{idx + 1}: {validation.issues}")
                continue
            primary_id = (
                min(found, key=lambda lid: target_order.index(lid) if lid in target_order else 999)
                if found
                else target_order[0]
            )
            mappings = map_tokens_to_lemmas(
                tokenize_display(arabic),
                mapping_lookup,
                target_lemma_id=primary_id,
                target_bare=target_bare_by_id[primary_id],
            )
            unmapped = [
                m.surface_form
                for m in mappings
                if m.lemma_id is None and not m.is_function_word and not m.is_proper_name
            ]
            if unmapped:
                errors.append(f"s{idx + 1}: unmapped {unmapped}")
            per_sentence_content_ids.append({
                int(m.lemma_id)
                for m in mappings
                if m.lemma_id and not m.is_function_word and not m.is_proper_name
            })
        counts = Counter(lid for ids in per_sentence_content_ids for lid in ids)
        repeated_anchor_ids = sorted(lid for lid, count in counts.items() if count >= 2)
        return {
            "valid": (
                not errors
                and 3 <= len(sentences) <= 5
                and bool(repeated_anchor_ids)
                and bool(used_targets)
            ),
            "errors": errors,
            "sentence_count": len(sentences),
            "used_target_ids": sorted(used_targets),
            "used_target_count": len(used_targets),
            "repeated_anchor_ids": repeated_anchor_ids,
        }
    finally:
        db.close()


def _judge(result: dict[str, Any]) -> dict[str, Any]:
    lines = []
    for idx, sent in enumerate(result.get("sentences") or [], 1):
        lines.append(f"{idx}. AR: {sent.get('arabic', '')}\n   EN: {sent.get('english', '')}")
    return generate_completion(
        prompt=(
            "Judge this Arabic learner passage. Penalize disconnected example "
            "sentences, forced target-word combinations, childish prose, and weak "
            "or inaccurate translations.\n\n" + "\n".join(lines)
        ),
        system_prompt=(
            "You are a strict Arabic reading-material editor. Return JSON only. "
            "Use 5 only for genuinely cohesive, rewarding, natural passages."
        ),
        json_schema=JUDGE_SCHEMA,
        temperature=0,
        timeout=120,
        model_override="claude_haiku",
        task_type="passage_prompt_eval_judge",
    )


def _score(validation: dict[str, Any], judge: dict[str, Any]) -> float:
    if not validation["valid"] or judge.get("translation_correct") is not True:
        return 0.0
    if judge.get("disconnected_examples") is True:
        return 0.0
    cohesion = float(judge.get("cohesion") or 1) / 5.0
    reward = float(judge.get("reward") or 1) / 5.0
    beauty = float(judge.get("beauty") or 1) / 5.0
    forced_penalty = (float(judge.get("forcedness") or 5) - 1.0) / 4.0
    coverage = min(1.0, validation["used_target_count"] / 2.0)
    return round(0.38 * cohesion + 0.34 * reward + 0.23 * beauty + 0.05 * coverage - 0.25 * forced_penalty, 4)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=24)
    parser.add_argument("--styles", default="nostalgic,humorous")
    parser.add_argument("--variants", default="subset_anchor,premise_first,literary_micro")
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--revision-rounds", type=int, default=0)
    parser.add_argument("--target-ids", default="")
    parser.add_argument("--out", default="/tmp/passage_prompt_eval_results.json")
    args = parser.parse_args()

    target_ids = [int(x) for x in args.target_ids.split(",") if x.strip()]
    targets, eligible = _load_pool(args.limit, target_ids=target_ids or None)
    styles = [s.strip() for s in args.styles.split(",") if s.strip()]
    variant_names = [v.strip() for v in args.variants.split(",") if v.strip()]

    runs = []
    started = time.time()
    for variant_name in variant_names:
        for style in styles:
            run_started = time.time()
            try:
                feedback = None
                result = {}
                validation = {}
                judge = {}
                score = 0.0
                for round_idx in range(args.revision_rounds + 1):
                    result = _run_generation(
                        variant_name,
                        VARIANTS[variant_name],
                        style,
                        targets,
                        eligible,
                        args.model,
                        feedback=feedback,
                    )
                    validation = _validate_result(result, targets, eligible)
                    judge = _judge(result) if validation["sentence_count"] else {}
                    score = _score(validation, judge)
                    if score > 0:
                        break
                    draft_lines = " | ".join(
                        str(s.get("arabic") or "").strip()
                        for s in (result.get("sentences") or [])
                        if isinstance(s, dict)
                    )
                    feedback = (
                        f"Rejected in eval round {round_idx + 1}. "
                        f"Validation: {validation}. Judge: {judge}. "
                        f"Previous Arabic sentences: {draft_lines[:1200]}"
                    )
                status = "ok"
                error = None
            except Exception as exc:
                result = {}
                validation = {}
                judge = {}
                score = 0.0
                status = "error"
                error = f"{type(exc).__name__}: {exc}"
            runs.append({
                "variant": variant_name,
                "style": style,
                "status": status,
                "score": score,
                "error": error,
                "elapsed_s": round(time.time() - run_started, 1),
                "result": result,
                "validation": validation,
                "judge": judge,
            })

    best = max(runs, key=lambda r: r["score"]) if runs else None
    payload = {
        "target_pool": targets,
        "eligible_count": len(eligible),
        "elapsed_s": round(time.time() - started, 1),
        "best": {
            "variant": best["variant"],
            "style": best["style"],
            "score": best["score"],
        } if best else None,
        "runs": runs,
    }
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(json.dumps({
        "best": payload["best"],
        "runs": [
            {
                "variant": r["variant"],
                "style": r["style"],
                "score": r["score"],
                "status": r["status"],
                "validation": r["validation"],
                "judge": r["judge"],
                "elapsed_s": r["elapsed_s"],
                "error": r["error"],
            }
            for r in runs
        ],
        "out": args.out,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
