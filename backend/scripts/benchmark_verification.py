#!/usr/bin/env python3
"""Benchmark different LLM models on mapping verification quality.

Uses real flagged sentences as ground truth — cases where the verification
LLM approved bad mappings that users later caught.

Usage:
    python3 scripts/benchmark_verification.py
    python3 scripts/benchmark_verification.py --models claude_haiku,gemini
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

# Clear proxy env vars that block API calls locally
for k in ["ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "all_proxy", "https_proxy", "http_proxy"]:
    os.environ.pop(k, None)

sys.path.insert(0, ".")

# Ground truth: sentences with known-bad mappings that should have been flagged.
# Each entry: arabic, english, mappings (position, surface, lemma_ar, gloss),
# and expected_bad_positions (positions that SHOULD be flagged).
GROUND_TRUTH = [
    {
        "id": "flag50",
        "arabic": "لازمني أياما وهو يعالجني من أثر ذلك الغاز.",
        "english": "He stayed by my side for days, treating me for the effects of that gas.",
        "mappings": [
            (0, "لَازَمَنِي", "لَازِمٌ", "necessary, urgent, unavoidable"),
            (1, "أَيَّامًا", "يَوْم", "day"),
            (2, "وَهُوَ", "هُوَ", "he"),
            (3, "يُعَالِجُنِي", "عالَج", "to treat"),
            (4, "مِنْ", "مِن", "from; than"),
            (5, "أَثَرِ", "آثَار", "monuments"),
            (6, "ذَلِكَ", "ذٰلِكَ", "that (masc.)"),
            (7, "الْغَازِ.", "غَازٍ", "raider, invader, conqueror"),
        ],
        "expected_bad": [0, 5, 7],
        "notes": "لَازَمَنِي=accompanied me (not 'necessary'), أَثَر=effect (not monuments), غاز=gas (not raider)",
    },
    {
        "id": "flag49",
        "arabic": "قرأت كتبا متنوعة وعرفت حينذاك أنني أميل إلى نوع معين من العلوم...",
        "english": "I read many different books and realized then that I was drawn to a specific type of science...",
        "mappings": [
            (0, "قَرَأْتُ", "قَرَأَ", "to read"),
            (1, "كُتُبًا", "كِتاب", "book"),
            (2, "مُتَنَوِّعَةً", "مُتَنَوِّعَة", "diverse / various"),
            (3, "وَعَرَفْتُ", "عَرَفَ", "to know"),
            (4, "حِينَذَاكَ", "حِينَذاك", "at that time"),
            (5, "أَنَّنِي", "الآن", "now"),
            (6, "أَمِيلُ", "مَالٌ", "money, wealth"),
            (7, "إِلَى", "إِلَى", "to, towards"),
            (8, "نَوْعٍ", "نَوَّعَ", "to diversify"),
            (9, "مُعَيَّنٍ", "مُعَيَّنَة", "specific"),
            (10, "مِنَ", "مِن", "from; than"),
            (11, "الْعُلُومِ...", "عَلِمَ", "to know"),
        ],
        "expected_bad": [5, 6, 8, 11],
        "notes": "أَنَّنِي=that I (not now), أَمِيلُ=I incline (not money), نَوْعٍ=type (not diversify), العلوم=sciences (not to know)",
    },
    {
        "id": "flag46",
        "arabic": "المَقْعَدُ عَالٍ وَأَنَا أَحْتَاجُ سُلَّمًا لِلْجُلُوسِ عَلَيْهِ.",
        "english": "The seat is high and I need a ladder to sit on it.",
        "mappings": [
            (0, "المَقْعَدُ", "مَقْعَد", "seat"),
            (1, "عَالٍ", "عَالٍ", "high"),
            (2, "وَأَنَا", "أَنا", "I"),
            (3, "أَحْتَاجُ", "ٱِحْتاج", "to need"),
            (4, "سُلَّمًا", "سِلْمٌ", "peace"),
            (5, "لِلْجُلُوسِ", "جُلُوس", "sitting"),
            (6, "عَلَيْهِ.", "عَلى", "on, on top of"),
        ],
        "expected_bad": [4],
        "notes": "سُلَّم=ladder (not سِلْم=peace)",
    },
    {
        "id": "flag45",
        "arabic": "هَلْ اِتَّصَلَ أَحَدٌ بِالنَّادِي لِلسُّؤَالِ؟",
        "english": "Did anyone contact the club to ask?",
        "mappings": [
            (0, "هَلْ", "هَل", "(question particle)"),
            (1, "اِتَّصَلَ", "اِتَّصَلَ", "to contact"),
            (2, "أَحَدٌ", "احد", "Sunday"),
            (3, "بِالنَّادِي", "نَادِي", "club"),
            (4, "لِلسُّؤَالِ؟", "سُؤال", "question"),
        ],
        "expected_bad": [2],
        "notes": "أَحَدٌ=anyone/someone (not Sunday)",
    },
    {
        "id": "flag44",
        "arabic": "يَشْعُرُ المَرِيضُ بِدُوَارٍ كُلَّمَا وَقَفَ بِسُرْعَةٍ، لٰكِنَّ الطَّبِيبَ قَالَ إِنَّهُ أَمْرٌ عَادِيٌّ.",
        "english": "The patient feels dizziness whenever he stands quickly, but the doctor said it is a normal matter.",
        "mappings": [
            (0, "يَشْعُرُ", "شَعَرَ", "to feel"),
            (1, "المَرِيضُ", "مَرِيض", "sick"),
            (2, "بِدُوَارٍ", "دُوَارٌ", "dizziness, vertigo"),
            (3, "كُلَّمَا", "كلما", "whenever"),
            (4, "وَقَفَ", "وَقَفَ", "to come to a stop, to come to a standstill"),
            (5, "بِسُرْعَةٍ،", "بِسُرْعة", "quickly"),
            (6, "لٰكِنَّ", "لكن", "but"),
            (7, "الطَّبِيبَ", "طَبِيب", "doctor"),
            (8, "قَالَ", "قالَ", "to say"),
            (9, "إِنَّهُ", "الآن", "now"),
            (10, "أَمْرٌ", "أَمْر", "matter/affair"),
            (11, "عَادِيٌّ.", "عَادِيٌّ", "normal"),
        ],
        "expected_bad": [9],
        "notes": "إِنَّهُ=indeed it/that it (not الآن=now)",
    },
    {
        "id": "flag41",
        "arabic": "أَمَلَ الصَّيَّادُ فِي حَظٍّ أَكْثَرَ اليَوْمَ.",
        "english": "The fisherman hoped for more luck today.",
        "mappings": [
            (0, "أَمَلَ", "أَمَلَ", "to hope for"),
            (1, "الصَّيَّادُ", "صَيّادِيَة", "Sayadieh (dish)"),
            (2, "فِي", "فِي", "in"),
            (3, "حَظٍّ", "حَظّ", "luck"),
            (4, "أَكْثَرَ", "كَثِير", "many, many, a lot"),
            (5, "اليَوْمَ.", "اَلْيَوْمَ", "today"),
        ],
        "expected_bad": [1],
        "notes": "الصَّيَّادُ=fisherman/hunter (not Sayadieh dish)",
    },
    {
        "id": "flag40",
        "arabic": "الأَبُ يُعِدُّ طَعَاماً لَذِيذاً لِلْأُسْرَةِ كُلَّ يَوْمٍ.",
        "english": "The father prepares delicious food for the family every day.",
        "mappings": [
            (0, "الأَبُ", "أب", "father"),
            (1, "يُعِدُّ", "عَدَد", "count"),
            (2, "طَعَاماً", "طَعام", "food"),
            (3, "لَذِيذاً", "لَذِيذ", "delicious, tasty"),
            (4, "لِلْأُسْرَةِ", "سَرَّ", "to gladden, delight"),
            (5, "كُلَّ", "كُلّ", "all (of), each"),
            (6, "يَوْمٍ.", "يَوْم", "day"),
        ],
        "expected_bad": [1, 4],
        "notes": "يُعِدُّ=prepares (Form IV of عدّ, not عدد=count), لِلْأُسْرَةِ=for the family (not سَرَّ=to gladden)",
    },
    {
        "id": "flag39",
        "arabic": "قَضَى الرَّجُلُ يَوْمَهُ فِي المَكْتَبَةِ يَقْرَأُ كُتُباً عَنِ التَّارِيخِ وَالأَدَبِ.",
        "english": "The man spent his day in the library reading books about history and literature.",
        "mappings": [
            (0, "قَضَى", "قَضَى", "to spend time"),
            (1, "الرَّجُلُ", "رَجُل", "man"),
            (2, "يَوْمَهُ", "اَلْيَوْمَ", "today"),
            (3, "فِي", "فِي", "in"),
            (4, "المَكْتَبَةِ", "مَكْتَبَة", "bookstore, library"),
            (5, "يَقْرَأُ", "قَرَأَ", "to read"),
            (6, "كُتُباً", "كِتاب", "book"),
            (7, "عَنِ", "عَن", "about"),
            (8, "التَّارِيخِ", "تَارِيخٌ", "history, date"),
            (9, "وَالأَدَبِ.", "دَبَّ", "to creep, to crawl"),
        ],
        "expected_bad": [2, 9],
        "notes": "يَوْمَهُ=his day (يوم not اليوم=today), وَالأَدَبِ=literature (أدب not دبّ=to creep)",
    },
    {
        "id": "flag34",
        "arabic": "هُنَاكَ تِرٌّ قَدِيمٌ فِي المَخْزَنِ، وَلَكِنْ لَا يَعْرِفُ أَحَدٌ مَنْ وَضَعَهُ هُنَاكَ.",
        "english": "There is an old plumb line in the storeroom, but no one knows who put it there.",
        "mappings": [
            (0, "هُنَاكَ", "هُناك", "there"),
            (1, "تِرٌّ", "تِرٌّ", "plumb line"),
            (2, "قَدِيمٌ", "قَديم", "old"),
            (3, "فِي", "فِي", "in"),
            (4, "المَخْزَنِ،", "مَخْزَنٌ", "storeroom, storehouse"),
            (5, "وَلَكِنْ", "وَلَكِنْ", "but"),
            (6, "لَا", "لَا", "neither"),
            (7, "يَعْرِفُ", "عَرَفَ", "to know"),
            (8, "أَحَدٌ", "احد", "Sunday"),
            (9, "مَنْ", "مِن", "from; than"),
            (10, "وَضَعَهُ", "وَضْع", "situation, state"),
            (11, "هُنَاكَ.", "هُناك", "there"),
        ],
        "expected_bad": [8, 9, 10],
        "notes": "أَحَدٌ=anyone (not Sunday), مَنْ=who (not مِن=from), وَضَعَهُ=put it (verb وضع, not noun وَضْع=situation)",
    },
    {
        "id": "flag30",
        "arabic": "حَلَقَ أَبِي شَارِبَهُ أَمْسِ.",
        "english": "My father shaved his mustache yesterday.",
        "mappings": [
            (0, "حَلَقَ", "حَلَق", "throat"),
            (1, "أَبِي", "أب", "father"),
            (2, "شَارِبَهُ", "شَارِبٌ", "mustache"),
            (3, "أَمْسِ.", "أَمْسٌ", "yesterday"),
        ],
        "expected_bad": [0],
        "notes": "حَلَقَ=to shave (verb, not حَلَق=throat)",
    },
    {
        "id": "flag29",
        "arabic": "أَعْطَى الصَّيَّادُ القِرْدَ تُفَّاحًا لَذِيذًا.",
        "english": "The hunter gave the monkey delicious apples.",
        "mappings": [
            (0, "أَعْطَى", "أَعْطَى", "to give"),
            (1, "الصَّيَّادُ", "صَيّادِيَة", "Sayadieh (dish)"),
            (2, "القِرْدَ", "قِرْد", "monkey"),
            (3, "تُفَّاحًا", "تُفَّاح", "apples"),
            (4, "لَذِيذًا.", "لَذِيذ", "delicious, tasty"),
        ],
        "expected_bad": [1],
        "notes": "الصَّيَّادُ=hunter (not Sayadieh dish)",
    },
    {
        "id": "flag14",
        "arabic": "وَضَعَ الطَّالِبُ الحَقِيبَةَ عَلَى كَتِفِهِ وَذَهَبَ إِلَى الجَامِعَةِ.",
        "english": "The student put the bag on his shoulder and went to the university.",
        "mappings": [
            (0, "وَضَعَ", "وَضْع", "situation, state"),
            (1, "الطَّالِبُ", "طالِب", "student"),
            (2, "الحَقِيبَةَ", "حَقيبَة", "bag"),
            (3, "عَلَى", "عَلى", "on, on top of"),
            (4, "كَتِفِهِ", "كَتِف", "shoulder"),
            (5, "وَذَهَبَ", "ذَهَبَ", "to go"),
            (6, "إِلَى", "إِلَى", "to, towards"),
            (7, "الجَامِعَةِ.", "جامِعة", "university"),
        ],
        "expected_bad": [0],
        "notes": "وَضَعَ=to put/place (verb, not وَضْع=situation noun)",
    },
    {
        "id": "flag12",
        "arabic": "هَلْ لَمَعَ الذَّهَبُ تَحْتَ الشَّمْسِ؟",
        "english": "Did the gold glisten under the sun?",
        "mappings": [
            (0, "هَلْ", "هَل", "(question particle)"),
            (1, "لَمَعَ", "لَمَعَ", "to glisten, to gleam"),
            (2, "الذَّهَبُ", "ذَهَبَ", "to go"),
            (3, "تَحْتَ", "تَحْت", "under"),
            (4, "الشَّمْسِ؟", "شَمْس", "sun"),
        ],
        "expected_bad": [2],
        "notes": "الذَّهَبُ=gold (noun, not ذَهَبَ=to go)",
    },
    {
        "id": "flag47",
        "arabic": "كَفَّ الطُّلَّابُ عَنِ الحَدِيثِ عِنْدَمَا دَخَلَ المُعَلِّمُ إِلَى الصَّفِّ.",
        "english": "The students stopped talking when the teacher entered the classroom.",
        "mappings": [
            (0, "كَفَّ", "كَفَّ", "desist, cease"),
            (1, "الطُّلَّابُ", "طُلّاب", "students"),
            (2, "عَنِ", "عَن", "about"),
            (3, "الحَدِيثِ", "حَدِيثٌ", "modern"),
            (4, "عِنْدَمَا", "عندما", "when"),
            (5, "دَخَلَ", "دَخَلَ", "to enter"),
            (6, "المُعَلِّمُ", "مُعَلِّم", "teacher"),
            (7, "إِلَى", "إِلَى", "to, towards"),
            (8, "الصَّفِّ.", "صَفّ", "class, classroom"),
        ],
        "expected_bad": [3],
        "notes": "الحَدِيثِ=talking/conversation (not حَدِيثٌ=modern). Homograph: حديث means both 'modern' and 'speech/conversation'",
    },
]


def build_prompt(case: dict) -> tuple[str, str]:
    """Build the exact same prompt used in production verification."""
    word_lines = []
    for pos, surface, lemma_ar, gloss in case["mappings"]:
        word_lines.append(f"  {pos}: {surface} → {lemma_ar} ({gloss})")

    prompt = f"""Arabic sentence: {case['arabic']}
English translation: {case['english']}

Word-to-lemma mappings:
{chr(10).join(word_lines)}

Your task: check that each word's lemma MAKES SENSE in the context of this sentence and its English translation. For each wrong mapping, provide the correct lemma.

Flag as WRONG (and provide correction):
- The lemma's English gloss doesn't match what the word means in this sentence (e.g. "to sleep" in a sentence about growing, "classroom" in a sentence about describing)
- **Homograph collisions**: same consonants but different meanings depending on voweling (e.g. جَدّ "grandfather" vs جِدّ "seriousness", حَرَم "to deprive" vs حَرَم "sanctuary", عِلم "knowledge" vs عَلَم "flag"). If the English translation uses a meaning that doesn't match the mapped gloss, FLAG IT even if they share the same root.
- A verb mapped to an unrelated noun or vice versa when they happen to share consonants (e.g. طَائِر "bird" mapped to طار "to fly" — these are different lemmas)
- A clitic prefix (و/ف/ب/ل/ك) wrongly stripped from a word where the letter is part of the root (e.g. وَصْف "description" stripped to صف "row/class")
- An active participle / verbal noun mapped to the root verb when it should be its own lemma (e.g. حُضُور "attendance" mapped to حاضر "present")
- A noun/verb homograph mapped to the wrong part of speech (e.g. ذَهَب "gold" mapped to ذَهَبَ "to go")

Do NOT flag (these are CORRECT):
- A conjugated verb mapped to its dictionary form, when the MEANING matches the sentence (e.g. يَكْتُبُ "he writes" mapped to كَتَبَ "to write")
- A plural/feminine/dual form mapped to its base lemma (e.g. مُعَلِّمَة mapped to مُعَلِّم)
- A noun with possessive suffix mapped to the base noun (e.g. أُمِّي mapped to أُمّ)
- A word with preposition prefix where the base word is correct (e.g. بِالعَرَبِيَّة mapped to عَرَبِيّ)

Words marked [via clitic stripping] had a prefix/suffix removed during lookup — these are higher risk for errors. Pay extra attention to them.

When in doubt, flag it — a false positive just causes a retry, but a false negative reaches the user.

Return JSON: {{"issues": []}} if all correct, or:
{{"issues": [{{"position": <int>, "correct_lemma_ar": "<bare form>", "correct_gloss": "<English>", "correct_pos": "<noun/verb/adj/etc>", "explanation": "<brief>"}}]}}"""

    system = "You are an Arabic morphology expert. Check each mapping against the English translation. Flag any mapping where the gloss doesn't fit the sentence meaning."

    return prompt, system


def run_model(model: str, prompt: str, system: str) -> tuple[list[int], float, str | None]:
    """Run verification with a specific model, return (flagged_positions, latency_s, error)."""
    from app.services.llm import generate_completion, AllProvidersFailed

    start = time.time()
    try:
        result = generate_completion(
            prompt=prompt,
            system_prompt=system,
            json_mode=True,
            temperature=0.0,
            model_override=model,
            task_type="mapping_verification",
        )
        elapsed = time.time() - start
        issues = result.get("issues", [])
        if not isinstance(issues, list):
            return [], elapsed, f"Bad response format: {type(issues)}"
        positions = [int(iss["position"]) for iss in issues if isinstance(iss, dict) and "position" in iss]
        return positions, elapsed, None
    except (AllProvidersFailed, Exception) as e:
        return [], time.time() - start, str(e)


def score_result(flagged: list[int], expected: list[int], total_mappings: int) -> dict:
    """Compute precision/recall/F1 for a single case."""
    flagged_set = set(flagged)
    expected_set = set(expected)

    tp = len(flagged_set & expected_set)
    fp = len(flagged_set - expected_set)
    fn = len(expected_set - flagged_set)
    tn = total_mappings - tp - fp - fn

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision, "recall": recall, "f1": f1,
        "missed": sorted(expected_set - flagged_set),
        "false_alarms": sorted(flagged_set - expected_set),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models", default="claude_haiku,gemini,claude_sonnet",
        help="Comma-separated model names to benchmark",
    )
    parser.add_argument("--cases", default="all", help="Comma-separated case IDs or 'all'")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",")]
    cases = GROUND_TRUTH if args.cases == "all" else [
        c for c in GROUND_TRUTH if c["id"] in args.cases.split(",")
    ]

    print(f"Benchmarking {len(models)} models on {len(cases)} cases")
    print(f"Models: {', '.join(models)}")
    print(f"{'='*80}\n")

    # results[model] = list of per-case scores
    all_results: dict[str, list] = {m: [] for m in models}
    all_latencies: dict[str, list] = {m: [] for m in models}
    all_errors: dict[str, int] = {m: 0 for m in models}

    for case in cases:
        prompt, system = build_prompt(case)
        total = len(case["mappings"])
        expected = case["expected_bad"]

        print(f"Case {case['id']}: {case['arabic'][:60]}...")
        print(f"  Expected bad positions: {expected}")
        print(f"  ({case['notes']})")

        for model in models:
            flagged, latency, error = run_model(model, prompt, system)
            if error:
                print(f"  {model:20s}: ERROR ({latency:.1f}s) — {error[:80]}")
                all_errors[model] += 1
                all_results[model].append({"recall": 0, "precision": 0, "f1": 0, "fn": len(expected), "fp": 0})
                all_latencies[model].append(latency)
                continue

            scores = score_result(flagged, expected, total)
            all_results[model].append(scores)
            all_latencies[model].append(latency)

            status = "PERFECT" if scores["fn"] == 0 and scores["fp"] == 0 else (
                "GOOD" if scores["fn"] == 0 else "MISSED"
            )
            missed_str = f" missed={scores['missed']}" if scores["missed"] else ""
            false_str = f" false_alarms={scores['false_alarms']}" if scores["false_alarms"] else ""
            flagged_str = str(sorted(flagged))
            print(f"  {model:20s}: flagged={flagged_str:30s} {status:8s} "
                  f"P={scores['precision']:.0%} R={scores['recall']:.0%} F1={scores['f1']:.0%} "
                  f"({latency:.1f}s){missed_str}{false_str}")

        print()

    # Summary
    print(f"{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}\n")

    for model in models:
        results = all_results[model]
        if not results:
            print(f"{model}: no results")
            continue

        avg_recall = sum(r["recall"] for r in results) / len(results)
        avg_precision = sum(r["precision"] for r in results) / len(results)
        avg_f1 = sum(r["f1"] for r in results) / len(results)
        total_fn = sum(r["fn"] for r in results)
        total_fp = sum(r["fp"] for r in results)
        avg_latency = sum(all_latencies[model]) / len(all_latencies[model])
        perfect = sum(1 for r in results if r["fn"] == 0 and r["fp"] == 0)
        no_miss = sum(1 for r in results if r["fn"] == 0)
        errors = all_errors[model]

        print(f"{model}:")
        print(f"  Avg Recall:    {avg_recall:.0%}  (catches bad mappings)")
        print(f"  Avg Precision: {avg_precision:.0%}  (doesn't flag good ones)")
        print(f"  Avg F1:        {avg_f1:.0%}")
        print(f"  Total missed:  {total_fn}  (false negatives — bad mappings approved)")
        print(f"  Total false:   {total_fp}  (false positives — good mappings flagged)")
        print(f"  Perfect cases: {perfect}/{len(results)}")
        print(f"  No-miss cases: {no_miss}/{len(results)}  (caught all bad, maybe some false alarms)")
        print(f"  Avg latency:   {avg_latency:.1f}s")
        print(f"  Errors:        {errors}")
        print()

    # Cost comparison note
    total_bad = sum(len(c["expected_bad"]) for c in cases)
    print(f"Total ground-truth bad mappings across all cases: {total_bad}")
    print(f"\nNote: For production use, recall matters most — a false negative")
    print(f"reaches the user, while a false positive just retries generation.")


if __name__ == "__main__":
    main()
