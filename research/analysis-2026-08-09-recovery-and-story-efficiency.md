# Recovery and embedded-story efficiency — 2026-08-09

## Decision summary

Keep embedded stories, but shorten the default card and pair each Arabic child
sentence with its English translation after reveal. The early word-level result
is encouraging: selected story targets are not missed more often than ordinary
sentence targets. The present 53-token median story is nevertheless less
time-efficient per distinct reviewed lemma than a normal sentence, and the
current whole-Arabic-then-whole-English layout makes each long card needlessly
expensive to check.

Do not use whole-card `understood` as the primary story-versus-sentence outcome.
It mechanically penalizes a card containing many words. Use the percentage of
distinct reviewed lemmas marked red (rating 1) or yellow (rating 2), with a
separate result for the deliberately selected story targets.

## Snapshot and method

- Read-only production database and interaction logs through
  2026-08-08 21:10 UTC (2026-08-09 00:10 Europe/Tallinn).
- Reading-mode `sentence_review` events only for the direct layout/efficiency
  comparison. Listening cards are excluded.
- New stories are `clustered_short_stories_v2` maintenance passages.
- Reading-time summaries discard card times at or above 20 minutes. Two of 11
  v2 story cards still lacked a clean time sample.
- A word outcome is one unique scheduled lemma judgment on the card. Repeated
  occurrences of the same lemma do not create multiple ratings.
- This is an observational N-of-1 readout with 11 new-story reading cards. It is
  enough for a product adjustment, not a durable retention claim.

## Vacation recovery and work-trip usage

The usage trace identifies 2026-06-26 through 2026-07-08 as the vacation-like
low-usage interval. If the actual trip boundaries differ, the period labels
should be shifted; the underlying daily results do not change.

| Period | Active days | Cards | Cards/day | Reviewed lemmas | Red or yellow | Graduations |
|---|---:|---:|---:|---:|---:|---:|
| Pre-vacation, Jun 10–25 | 16/16 | 987 | 61.7 | 5,157 | 10.4% | 141 |
| Vacation-like interval, Jun 26–Jul 8 | 6/13 | 52 | 4.0 | 342 | 18.7% | 7 |
| Recovery, Jul 9–Aug 4 | 27/27 | 1,479 | 54.8 | 8,620 | 15.3% | 275 |
| Work trip so far, Aug 5–8 | 4/4 | 111 | 27.8 | 986 | 9.6% | 12 |

The recovery is real rather than merely a return of app opens: the 27-day
recovery produced 275 graduations, versus 141 in the 16-day pre-vacation
baseline. Current seven-day all-review retention is 89.6% (1,896/2,116), versus
85.7% over 30 days. Work-trip volume is uneven, but its word-level red/yellow
rate is currently better than both the recovery period and pre-vacation
baseline.

The remaining caution is debt, not demonstrated learning quality. Current
state is 2,639 known, 60 learning, 57 lapsed, and 89 acquiring. There are 865
due items (790 FSRS and 75 acquisition), so the system is still correctly in a
recovery regime. The older-gap cold-recall bands remain the weak point: 75.2%
at 7–14 days and 67.0% at 14–30 days. Sporadic reps are useful, but the debt has
not yet been cleared.

## Story word outcomes: the primary comparison

### All reviewed words

| Format, last 7 days | Ratings | Red | Yellow | Red or yellow |
|---|---:|---:|---:|---:|
| Ordinary sentences | 1,563 | 10.0% | 1.9% | 11.9% |
| New v2 stories | 296 | 6.4% | 1.0% | 7.4% |

The all-word story result is better, but it includes many easy support words.
It should not be used alone to claim that stories teach due targets better.

### Target words

| Comparison, last 7 days | Ratings | Red | Yellow | Red or yellow |
|---|---:|---:|---:|---:|
| Selected v2 story targets | 31 | 19.4% | 0.0% | 19.4% |
| Ordinary sentence primary targets | 236 | 17.8% | 2.5% | 20.3% |
| The same story-target lemmas when seen in sentence cards | 21 | 19.0% | 4.8% | 23.8% |

The selected targets are effectively tied with ordinary sentence targets and
directionally better in the within-lemma comparison. With only 31 and 21
matched judgments, the differences are not reliable enough to rank the two
formats, but there is no signal that stories impair immediate word review.

Only five story-exposed words have yet produced a qualifying later primary
review; all five were green. That is reassuring but far too small for a delayed
retention conclusion.

## Efficiency and length

| Typical reading card, last 7 days | Normal sentence | New v2 story |
|---|---:|---:|
| Arabic tokens | 7 | 53 |
| Distinct reviewed lemmas | 6 | 26 |
| Time per card | 33.9 s | 223.4 s |
| Time per Arabic token | 4.61 s | 3.92 s |
| Time per distinct reviewed lemma | 5.58 s | 8.59 s |
| Lookups per card | 1.05 | 3.73 |

Stories are read about 15% faster per running token, so narrative flow itself
is not the problem. They are about 54% slower per distinct scheduled lemma in
the robust median comparison. Repetition explains much of the difference: a
53-token story produces only 26 distinct lemma judgments, whereas a seven-token
sentence produces six.

The preliminary length split points to a practical threshold:

| V2 story length | Cards | Median time / reviewed lemma | Reviewed lemmas / active minute |
|---|---:|---:|---:|
| Up to 45 tokens | 5 | 4.90 s | 9.07 |
| 46–55 tokens | 1 | 10.26 s | 5.85 |
| Over 55 tokens | 5 | 10.65 s | 5.97 |

The sample is too small to fit a length-response curve, and the middle band is
one unusually difficult card. Still, the short-story cards were at least as
efficient as ordinary sentences, while the longer cards were not. A provisional
35–45-token budget is therefore better supported than abandoning stories.

## Repetition and form diversity

Among the 33 selected-target slots on the 11 reviewed v2 stories:

- 24/33 (72.7%) repeated the target lemma;
- 16/33 (48.5%) used at least two surface forms;
- by implication, roughly one quarter repeated a target only in the same form,
  while another quarter did not repeat it.

Across all repeated reviewed lemmas, not only the selected targets, 88.9% had
more than one exact surface. That aggregate looks good but masks the outcome the
learner values: varied forms of the deliberately practiced target. Generation
should optimize that target-level measure directly. Prefer two genuinely
different mapped surfaces when a target repeats, especially for verbs, and cap
same-form repetition unless the narrative requires it. The corpus-level supply
does contain morphology contrasts (all seven designated morphology stories
passed their gate), but only about half of the targets in the actually read
cards delivered surface variation.

## Layout finding

The frontend currently renders the whole Arabic passage first and, after
reveal, renders all English child-sentence lines below it. Sentence boundaries
are preserved, but corresponding Arabic and English lines are spatially
separated. On a 53-token card this predictably creates back-and-forth scrolling;
the 3.73 lookups per story card versus 1.05 per sentence card confirms higher
per-card checking activity.

Recommended answer-side layout on mobile:

1. Keep the uninterrupted Arabic passage on the front.
2. After reveal, render child-sentence blocks: Arabic sentence, then its English
   translation immediately beneath it.
3. Preserve red/yellow word marking in each Arabic block and keep word taps
   available for an inline gloss.
4. Default v2 generation to 35–45 Arabic tokens; allow a longer card only when
   it buys a verified morphological contrast or unusually high target yield.

This addresses the observed friction without changing review semantics or
giving repeated occurrences extra scheduling credit.

## Statistics accounting

The current statistic is not literally counting one long story as one
sentence. The backend writes one `SentenceReviewLog` row for every child
sentence in a passage. It nevertheless flattens units with very different
reading loads.

In the last seven days of reading-mode activity:

- 253 review cards were completed;
- those became 292 stored sentence-review rows;
- 15 story cards accounted for 54 of those child-sentence rows;
- 2,625 Arabic tokens were read;
- 1,916 distinct lemma judgments were recorded.

The product should report at least three separate units: **cards completed,
Arabic words read, and distinct words reviewed**. Story cards and child
sentences can be a secondary breakdown. A compact session summary could read:
“10 cards · 13 sentences · 94 Arabic words · 71 words reviewed,” with a story
badge when applicable. This makes both effort and learning yield visible.

## Product decision

- Keep the increased story supply.
- Judge it primarily by target red/yellow rate and later target recall, not
  whole-card comprehension.
- Shorten the default story to 35–45 tokens.
- Pair Arabic and English per child sentence after reveal.
- Make varied-form target repetition a first-class generation metric.
- Replace the lone “sentences” activity count with cards, running words, and
  distinct reviewed lemmas.
- Re-run after at least 30 reading-mode v2 story cards and at least 30 delayed
  target reviews; current delayed-retention evidence is not mature.

## Implementation status (2026-08-09)

Implemented on `sh/story-review-efficiency`: new v2 drafts default to three
sentences and are rejected outside 35–45 mapped running words; repeated selected
targets require multiple mapped surfaces, with a maximum of two identical-form
uses, and morphology-focus targets require three forms. The reveal screen now
pairs each interactive Arabic child sentence with its English and transliteration.
Session-end and lifetime analytics separately report cards, passage cards, child
sentences, Arabic words, and reviewed-lemma judgments; the session UI promotes
red/yellow word percentages instead of full-sentence comprehension.
