# Which words fill a review sentence, and could they do more work? (2026-11-18)

The learner's impression: the same few words (teacher, student, doctor, old, new) come back
constantly, while engineer, carpenter and street sweeper are rare and words like plumber and
yellow were never learned. Proposed remedy: a "pattern language" that rotates long lists of
people, things and qualities through sentence slots. This note checks the impression against
production data and tests one alternative. No production change was made.

Scripts and raw output: [`sentence-diversity-2026-11-18/`](sentence-diversity-2026-11-18/)
(`div1.py`–`div3.py` statistics, `exp1.py` generation experiment,
`generation-experiment.jsonl` every generated sentence with its verdict). All read-only.

## What the data shows

**Lifetime counts are very uneven, and the impression is right about which words.** Among 2,775
established content words the median has been reviewed 19 times. The top of the list:
طالب student 663, جديد new 524, رجل man 444, قديم old 425, ولد boy 362, طفل child 358,
معلم teacher 309 (plus مدرس 191 and أستاذ 178), طبيب doctor 281. Compare مهندس engineer 63,
نجار carpenter 36, كناس street sweeper 13, منخفض low 18.

**Recently it is much flatter, but the "person" slot is still concentrated.** Counting every
word actually on screen (sentence views × mapped words, not only logged reviews), the last 90
days had 1,614 sentence views and 10,621 content-word slots. The 77 words with more than 100
lifetime reviews took 10% of slots, about 3.6 times their share of the vocabulary. The words at
the top are mostly sentence subjects: leader 49, boy (three lemmas: صبي 42, ولد 29, غلام 26),
girl 34, man 29, teacher 22. All three generation prompts in `llm.py` say "Prefer pronouns and
generic definite nouns (المعلم، الولد، الفتاة) as subjects", which names the teacher, the boy
and the girl outright.

**The larger loss is that most slots do no scheduling work.** In the last 30 days a card showed
7.0 content words on average. 2.4 of them were due or in acquisition. 48% were not logged at
all, which means they were known words at retrievability ≥0.97 (exposure only under
`low_energy_maintenance_v1`). Meanwhile 979 words are due and waiting.

| Last 30 days, reading | per card | share |
|---|---|---|
| Content-word slots | 7.0 | 100% |
| Due or acquiring (scheduled work) | 2.4 | 34% |
| Not due, logged | 1.2 | 18% |
| Not logged (exposure only) | 3.3 | 48% |

**The inventory cannot do much better as it stands.** Of 1,533 active single sentences, 360
contain no currently due word, 599 contain one, 403 two, and only 171 three or more. A
best-case greedy pick of 40 cards covers 151 due words (3.8 per card) and then falls to 2 per
card. 141 due words have no sentence at all.

**Why generation produces this.** The live path (`warm_sentence_cache` →
`generate_validated_sentences_multi_target`) takes words that lack sentences, sorts them by tier
and due date, cuts them into groups of four, and asks for sentences containing at least two of
the four. The rest of each sentence comes from 500 known words sampled uniformly at random
(the at-risk weighting was switched off in reading attention v1, September 2026). Two
consequences: the four words are an arbitrary set, so the model often forces them together or
misuses one; and the filler is whatever the model finds easiest among 500 mostly safe words.

**The selection-side "freshness" penalty measures the wrong thing.** `_scaffold_freshness`
penalises a sentence by its scaffold words' lifetime `times_seen` (5 / times_seen, geometric
mean, floor 0.1). 80% of active sentences score between 0.10 and 0.30, so it mostly separates
sentences by how old their vocabulary is, not by whether the words need a review now.

**Some basic words were never in the system.** A hand-written checklist of 187 everyday words
(colours, trades, kinds of people, dimension adjectives, household objects, animals) found
roughly 40 with no lemma (for example أصفر yellow, سباك plumber, حداد blacksmith, فلاح farmer,
ضيق narrow, عريض wide, جاف dry, مطرقة hammer, إبرة needle, بقرة cow, دجاجة chicken, نحلة bee)
and 9 in the database but never learned (برتقالي orange, محام lawyer, سمين fat, وسادة pillow).
The match was by bare form, so a few of the 40 may exist under another spelling. Vocabulary
came from frequency lists and books, which under-sample concrete household words. "Tall" is not
a gap: طويل covers long and tall.

## Experiment: give the model a menu of due words

Three prompts, same production model (Sonnet via CLI), same deterministic validator, same
quality gate (`review_sentences_quality`), 24 sentences each, nothing written to the database.

- **A, current.** The live function with the 24 longest-overdue words in groups of four and 500
  random known words.
- **B, menu, at least 3.** One call receives 150 due words grouped by part of speech as
  "priority words" plus 200 safe supporting words, and writes 12 sentences of 7–11 words with at
  least three priority words each, each priority word used once, choosing combinations that
  belong together. The teacher/boy/girl hint is replaced by "take people, things, actions and
  qualities from the priority list".
- **C, menu, at least 4.** Same, 9–13 words, at least four.

| | A current | B menu ≥3 | C menu ≥4 |
|---|---|---|---|
| Passed validator | 24/24 | 24/24 | 24/24 |
| Passed quality gate | 17 (71%) | 22 (92%) | 21 (88%) |
| Words per sentence | 9.2 | 7.2 | 9.4 |
| Due words per sentence | 2.3 | 4.0 | 5.1 |
| Safe words per sentence (not due, stability ≥180 d) | 3.2 | 1.1 | 1.4 |
| Words with >100 lifetime reviews per sentence | 1.6 | 0.5 | 0.6 |
| Distinct due words covered by passed sentences | 23 | 85 | 104 |
| LLM calls | 6 | 2 | 2 |

Arm A's failures were the forced kind: وَجَمَ used as "gathered" three times because it had to be
combined with three unrelated words. With a menu the model picks sets that fit (the photographer
stayed up alone next to the bank; hunger led the turtle to the moist grass). This matches the
September finding that forced sentences track rare-word density.

Limits: one run, 24 sentences per arm; arm A's words were the longest overdue and so somewhat
harder than the random menu; mapping verification (the last production gate) was not run.

## Assessment of the pattern-language idea

Fixed templates with rotating slot lists would fix the repetition but not the larger loss, and
they bring their own costs: Arabic agreement and collocation make slot-filling error-prone, and
templated sentences become recognisable, so the learner reads the frame instead of the words.
The menu prompt does the rotation by need instead: the people, things and qualities offered for
each batch are the ones due for review, so the cast changes with every batch and no list has to
be maintained.

## Recommendation (built the same day; see the experiment-log entry "Due-word menu generation, card ceiling 4 → 6")

1. **Menu generation for due words.** Add a generation pass that offers a rotating menu of due
   words and stores the results through the existing multi-target validation, verification and
   write path. Start with the B setting. At about 37 cards a day, 4.0 due words per card
   instead of 2.4 is roughly 60 more due-word reviews a day, enough to clear the 911-word
   backlog in about two to three weeks if generation keeps up (about 40 sentences, four calls,
   per day).
2. **Remove the teacher/boy/girl subject hint** from the three generation prompts.
3. **Basic-vocabulary top-up.** Add the missing everyday words as a staged list that ordinary
   intake draws from.
4. **Leave selection alone for now.** `due_coverage ^ 1.5` already prefers dense sentences.
   Replacing lifetime-count freshness with a need-based measure is a later question.

The cost of 1 is harder cards: with four due words at about 84% recall each, the
same arithmetic that gives about two thirds fully green cards today (0.84^2.4) gives about half
(0.84^4).
