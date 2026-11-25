# Sentence review check, 25 November 2026

Read-only check of sentence review one week after the changes of 18–21 November, on a
production snapshot taken 25 Nov 06:12 UTC. Scripts: `progress-check-2026-11-25/` (they
read `snap.db`, an online backup of the production database, from the working directory)
plus `missed-word-recovery-2026-11-20/review_followup.py`. Nothing was changed.

## Volume

Sentence cards per day: 134 (20 Nov), 47, 50, 40, 17, 11 (25 Nov, morning only), against
2–67 in the two weeks before.

## What works

| Measure | Before | Since 19–20 Nov |
|---|---|---|
| Red miss on an established word: back in a sentence within 24 h | 8% (10/118) | 37% (47/126) |
| Same, within 72 h | 16% | 66% (61/93) |
| Median wait until the next sentence | 13.6 days | 18.7 hours |
| Correct at the first sentence ≥12 h after the miss | 76% (79/104) | 90% (53/59) |
| Reviews of words being learned, per active day | 0 on 19 Nov | 23–76 on 20–23 Nov, 3 on 24 and 25 Nov |
| Established words due | about 980 (18 Nov) | 538 |
| Leech suspensions per day | 11 on 20 Nov | 2–3 |

Yellow misses return later than red ones: 0 of 52 within 24 h, 6 of 41 within 72 h. A
yellow is scheduled as a lapse without the ten-minute step, so it is due after about four
days, and the lane only takes due words.

## Recall on due words fell

Due established words (stability ≥ 7 days), share answered green:

| Days overdue | 10 Sep – 18 Nov | 19 Nov on |
|---|---|---|
| 0–7 | 88% (483/547) | 79% (60/76) |
| 7–30 | 80% (436/544) | 68% (55/81) |
| 30–90 | 71% (208/294) | 65% (128/198) |
| 90+ | 41% (9/22) | 44% (20/45) |

Most of the overall drop (word miss rate 20% → 28%) is composition: 61% of due reviews are
now more than 30 days overdue, against 22% before, because the dense cards work through
the backlog. Within the 0–30 day buckets recall is still about ten points lower on small
samples. Candidates that this data cannot separate: sentences written since 18 Nov (366 of
400 due reviews were on them), less known context around each due word, stricter marking
at higher volume. Number of due words on the card does not explain it: words with
stability ≥ 30 days were green 75% (24/32) on cards with 1–2 due words, 68% (69/102) with
4, 69% (51/74) with 5–6.

## Phrase follow-up trial (`fragment_followup_v1`), interim, not a readout

| | Phrase arm | Sentence arm |
|---|---|---|
| Misses assigned | 225 | 228 |
| First follow-up happened | 140 (88 as a phrase card) | 104 |
| First follow-up correct | 86% (phrase cards 77/88) | 78% |
| Median hours from miss to first follow-up | 27 | 53 |
| Seconds for the follow-up | 8.0 (phrase card) | 36.4 (whole sentence card) |
| Primary endpoint: next full sentence correct | 33/39 (85%) | 18/23 (78%) |

The rule is 50 completed outcomes per arm and not before 4 December; neither arm is there.
The phrase arm gets its follow-up a day earlier, which the readout has to account for. One
phrase card went to a miss whose hashed arm is `sentence`.

## Reading timing

165 cards carry `reading_timing`; 65 are clean (98 excluded for help or a marked word, 8
for background or clock gaps). Clean reading: median 2.1 s per token, about 28 words per
minute, flat across sentence lengths of 6–11 tokens. No sentence has been timed twice.
The only consumer is `confusion_learning.py`, which requires a clean timed reading before
it counts a sentence as clean evidence for a confusion pair.

## Cause marks

Of 363 marked misses on content words since 19 Nov, 8 carry a cause (7 mixed up, 1
unfamiliar form, 0 missing tashkeel); 20 Oct – 18 Nov: 12 of 283. Form protection
(`form_recovery_v1`) applied 5 times in five weeks; the mixed-up rule 9 times.

## What a miss costs

Misses since 1 September on words with stability ≥ 30 days, median stability in days:

| | Before | After miss | 1 success | 2 | 3 | 4 |
|---|---|---|---|---|---|---|
| Red (n 350) | 124 | 3.9 | 4.2 | 8.6 | 15.5 | 25.2 |
| Yellow (n 175) | 142 | 4.1 | 7.8 | 12.2 | 15.5 | 16.1 |

Observed recall after a red miss before the lane existed: 92% at 2–7 days (n 13), 71%
after more than 7 days (n 77). A four-day stability predicts about 90% and 76%, so the
short interval after a miss matches the data; a word does not come back to its old
interval quickly, and the data does not say it should after one success.

## Words being learned

100 acquiring words, 85 in Box 1 (84 due), 37 never reviewed; 47 started 16 Nov and 32 on
24 Nov, almost all from Snap and Pre-read. Graduations: 52 in the week of 16 Nov, 3 since.

## Addendum: reading 45 missed words in the new sentences

A random 45 of the 108 misses on words with stability ≥ 30 days in sentences written
since 18 Nov. The Arabic is correct and the missed word nearly always appears in a plain
form and its usual sense (الْغَابَةِ, الْحُوتُ, حَضَرَ, خَلَطَ, حَجْمَ). What differs from older
cards is the company: 33 of the 45 sat on cards with two to six misses, and the other
content words are themselves rarely seen due words (a burrowing animal's eyesight and
sense of smell; worms in a bag at customs; a locally made shield), so the sentence gives
little help in recalling any one of them. About five cases use an unusual sense or
construction (خَيْرٌ in "there is goodness in the group's future", مَدَّ الْبَصَرِ, الْأُنْثَى
as "the female", الْأَهْلِيِّ twice). The lower recall is therefore mostly a stricter test
of words that earlier, easier contexts let through, not faulty sentences.

Lemma records seen while reading that need a look: طَابِق gloss "floor?"; آذىً stored
with a madda against the usual أَذًى; هِنْد "India" is a noun, not a proper name; مَدّ is
labelled a preposition "for duration of".

## Proposed trials (none started)

1. Interval after a closed miss: when a missed word with long prior stability has its two
   clean sentence reviews, restore a fraction of the old stability in one arm. Endpoint:
   recall at the next review. Start after the phrase readout, since both act on missed words.
2. Yellow returns next day: make yellow misses lane-eligible at once in one arm. Endpoint:
   recall at the first sentence ≥ 12 h later.
3. Old sentence against new dense sentence for the same due word, to separate the recall
   drop above from marking and lateness.
