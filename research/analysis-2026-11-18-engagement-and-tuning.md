# Engagement and tuning check

18 November 2026. Read-only analysis of the production database and server journal at
13:15 UTC. It led to the changes in the experiment-log entry of the same date.

The learner's question: activity fell after the summer and is picking up again around
pages read with Snap and pre-read. Is the scheduling tuned correctly for keeping what is
known while learning new words faster?

## Activity

| Period | Days per week | Sentence cards per week | Words started per week |
|---|---|---|---|
| February to mid-June | 7 | 450–900 | 35–230 |
| Late June to early July | 3–6 | 12–190 | 14–94 |
| Mid-July to August | 5–7 | 170–450 | 6–200 |
| September | 4–7 | 33–230 | 0–17 |
| October to 14 November | 4–7 | 27–124 | 2 in six weeks |
| 16–18 November (3 days) | 3 | 112 | 96, all from Snap and pre-read |

One card takes about 0.7 minutes.

## What is known now

- 2,650 known words, 36 learning, 91 lapsed, 118 in acquisition, 229 suspended as leeches.
- The FSRS model predicts 91% average recall today across the 2,737 established, maintained
  words: 2,041 above 90%, 490 between 70% and 90%, 206 below 70%.
- 911 of them are overdue, by a median of 50 days. About 14 more fall due per day over the
  next 30 days. A sentence card covers about 3 due words, so 20 maintenance cards a day
  clear the backlog in roughly four weeks.

## Is the memory model calibrated?

Due reading reviews of established words, predicted recall at the moment of review against
the rating given.

| Period | Reviews | Predicted | Observed, Hard or Good | Observed, Good only |
|---|---|---|---|---|
| Last 75 days | 2,545 | 85.4% | 83.8% | 78.5% |
| June–July | 2,973 | 89.8% | 83.3% | 78.3% |

FSRS counts Hard as a pass, so the model is within two points now. No parameter change is
warranted. Recall is under the 90% target because reviews come late:

| Days overdue at review | Reviews | Good |
|---|---|---|
| 0–7 | 1,123 | 87.8% |
| 7–30 | 931 | 76.5% |
| 30–90 | 462 | 62.8% |
| 90+ | 29 | 37.9% |

Not-due words seen in passing cannot be read from `review_log`: a clean sighting of a
well-known word is logged as exposure only, so the log keeps mostly the failures.

## New-word throughput

- A word needs about 4.5 acquisition reviews, on at least two days, to graduate.
- 16–18 November: about 37 cards a day carried about 33 acquisition reviews. That is
  roughly 7 new words a day; 32 a day were added. Sixty of the 96 had not been shown once.
- Fifteen words a day needs about 70 cards (about 50 minutes). The spring pace was about
  85 cards and 14 words a day.
- 55 of the 73 cards with a new word carried one new word, 18 carried two.
- The first pre-read batch entered 49 words and 28 names. All 19 name reviews so far were
  correct, and each name still needed a second-day card.
- Pre-read priority ended four days after the batch, with 49 of 77 entries unseen after 2.5.

## Automatic introduction had stopped

Words are introduced automatically only in a session build with `prefetch=false`. The
phone shows sessions from its prefetched queue and refills it in the background. In the
journal (9–18 November) 149 of 150 `GET /api/review/next-sentences` calls had
`prefetch=true`. The same fresh builds were the only caller of `refresh_attention`.
Consequences: 191 open *Men in the Sun* targets and about 60 words met in sentences were
never started; only explicit picks entered acquisition. October's journal is gone, so the
start date of the stall is inferred from `acquisition_started_at`.

## Sentence supply

- 119 of 121 acquiring words have two or more reviewable sentences.
- 27 of the 911 overdue words have none; the three-hourly refill covered 1 of 4 attempts on
  the 18th. Claude CLI calls failed 38 times in 1,197 over three days (the token changed on
  the 18th).

## Decisions and changes

Kept: target retention 0.90, FSRS parameters, the daily and recovery intake budgets.
Changed (see the experiment log): introduction is triggered by real reviews; practised
names graduate on one correct answer; unseen pre-read words keep their priority; pre-read
generates sentences that pair two batch words; *Men in the Sun* targets retired.

Scripts used for the numbers were ad hoc and are not kept; every figure is a direct count
from `user_lemma_knowledge`, `review_log`, `sentence_review_log`, `preread_sets` and the
backend journal.
