# Passage Card Efficacy — First Look (2026-05-13)

The "long passages for maintenance" experiment has been live since the
2026-05-10 commits (`Prioritize storing passage cards`, `Show generated
maintenance stories as passages`). Track 4 asks: is it working, or wasting
time? This is a 7-day snapshot. Numbers are too small for a real verdict,
but they're enough to characterise the question.

Source: `card_shown` and `sentence_review` events in
`/opt/alif/backend/data/logs/interactions_*.jsonl` plus
`sentence_words` row counts from prod. Analysis script:
`/tmp/claude/analyze_passages.py`.

## Volume — there isn't much

| Card type | Cards shown (7d) | Share |
|---|---:|---:|
| sentence | 817 | 85.8 % |
| intro    | 124 | 13.0 % |
| **passage** | **11** | **1.2 %** |

11 passages in 7 days is roughly 1.5 per day. `MAINTENANCE_PASSAGE_MAX_RECENT`
in `material_generator.py` caps how often new passages get generated, and
`_is_maintenance_passage_candidate` only admits FSRS-maintenance words —
so the funnel is intentionally narrow. **At this rate, a single user week
is statistically thin.** Any comparison below should be read as directional,
not significant.

## Reading speed — passages are 4× slower per word

| Card type | n | median ms/word | p25 | p75 |
|---|---:|---:|---:|---:|
| passage  | 8   | 16,980 | 10,978 | 121,979 |
| sentence | 687 | 4,068 | 2,685 | 6,523 |

**Passages take ~4.2× longer per Arabic word** than single sentences. The
p75 on passage (~2 minutes/word) suggests at least some sessions where the
user was re-reading or pausing — i.e. real engagement, not skimming.

This is *expected*: the experiment hypothesis was that passages let known
words be exercised in flow, even if the time-per-word is higher. The
right comparison is not "are they faster" but "do they pay for themselves
in retention." That requires data we don't yet have at scale.

## Comprehension — not measured the way we'd like

`sentence_review` events carry a `comprehension` field, but for passages
it's currently a single field per sentence-in-passage, not a per-passage
rollup. The analysis pulled 0 comprehension samples for passages because
the `card_shown → sentence_review` join keyed on `sentence_id` but
maintenance passages emit per-sentence reviews against the *individual*
sentence rows. So we have the reviews, they just don't tag back as
"passage." This is the first concrete instrumentation gap.

## What's needed for a real verdict

1. **Tag reviews with their session card context.** When `sentence_review`
   fires inside a passage card, the event should carry
   `parent_card_type: "passage"` (or `passage_id`). Currently the only
   linkage is via `card_shown` events, and matching by `sentence_id` only
   works for cards that contain exactly one sentence.
2. **Wait for sample size.** At ~1.5 passages/day, a defensible
   passage-vs-sentence retention comparison needs ~30 days minimum.
3. **Cohort-match by lemma stability.** Passage sentences target
   FSRS-maintenance lemmas (stability ≥ ~7 days). Comparison should be
   apples-to-apples with sentence cards in the same stability band, not
   the entire sentence-card population.

## Recommendation

Don't kill the experiment. Don't double down yet either. Two cheap actions:

- **Add `parent_card_type` to `sentence_review` events** so the next
  rollup can split passage-internal reviews from standalone ones.
- **Re-run this analysis in ~3 weeks** (early June). If passage cards
  still show 4× ms/word *and* their lemmas regress in FSRS stability faster
  than matched non-passage sentences, kill it. If they hold equal or
  better stability at lower mental load (re-reading is information-dense),
  keep and raise the cap.

Until then, the experiment is **running at low cost and producing
insufficient data to judge**. The "wasting time" framing is premature.
