# Can Alif measure per-word reading speed? (2026-11-25)

Snapshot: production copy, morning of 25 November 2026. Every number comes from
`research/reading-speed-2026-11-25/analysis.py` (its output is in `output.txt`).

## Answer

No. The clean sentence timings cannot support a per-word speed claim today, and waiting will
not fix this within a useful time. The weakest link is the unit of measurement, not the
amount of data: one clock covers a sentence of about nine words.

- **Volume.** Clean timing reached the phone on 21 November, not 3 November: 165 timed
  sentence reviews in five days, 65 eligible (13 a day; 7.9 a day at the autumn activity
  level). 91 of the 100 exclusions are only a marked word. Three content words have appeared
  in three or more eligible sentences, none in five.
- **Signal against noise.** Sentence length explains 26% of reading time. After adjusting
  for length an eligible sentence is typically 35% faster or slower than expected (about 6 s
  on a 17 s sentence). On 778 clean-looking reviews with the old `response_ms` clock (a
  volume test only) the part that belongs to individual words is about 1.4 s per sentence.
  Projected reliability of a per-word average: 0.18 from 3 sentences, 0.27 from 5, 0.42 from
  10, 0.60 from 20. Split-half reliability on the 49 words with two eligible sentences is
  0.14 or lower; a regression with per-word effects explains under 2%.
- **What is measurable now.** The learner's overall clean reading rate: median 17.1 s per
  sentence, 2.1 s per running word (28 words a minute), 464 ms per letter. It can be tracked
  weekly on about 90 eligible sentences.

## Coverage of the book-frequency bands

| Band | Firm | Timed in ≥1 / ≥3 / ≥5 eligible sentences |
|---|---:|---|
| 1–500 | 368 | 81 / 1 / 0 |
| 501–1,000 | 279 | 70 / 0 / 0 |
| 1,001–2,000 | 422 | 85 / 2 / 0 |

A replay of past card selection puts half of band 1–500 at three timed appearances in about
three months and the other two bands in about six (five and nine to twelve months at the
autumn rate). Twenty appearances per word is out of reach for most band words within a year.

## A definition that could be used later

A word is fast when it appears in at least five different eligible timed sentences in the
last 180 days and their median pace is at or below 1.0. Pace is the actual time divided by
(intercept + slope × letters), fitted on the learner's eligible timings of the last 60 days,
and divided by that day's median (days hold 29% of the variance). Otherwise the word is
"not yet timed". Under it, firm-and-fast is 0 / 0 / 0 today. Relaxed to three sentences it
labels 8 words fast where shuffled times label 8.7, so it carries no information yet.

## Changes that would make speed measurable

1. A clean clock on the existing word and phrase cards (about 35 a day already). One clock,
   one word.
2. Timed one-second checks on firm band words. Thirty a day would give 1,069 firm band words
   three direct timings in about 107 days. This is a new card type and the learner's decision.
3. Compare each timing with the same day's clean sentences (noise 0.30 → 0.25).
4. Hold the vowel-mark state constant in the baseline (cards with tashkeel on the front read
   at pace 1.08, without at 0.88, p = 0.04).
5. Break ties in sentence choice toward band words with fewer than three timed appearances.

Shorter sentences do not help: relative noise is higher at 4–6 words than at 10–14.

## Caveats

Five days of data, one learner. Eligible sentences are those where every word was known, so
a word is only timed on its good appearances. The reliability projections come from the old
clock and could be off by a factor of two.
