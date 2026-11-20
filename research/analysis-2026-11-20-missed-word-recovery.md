# Missed words after the denser cards: review data, 19–20 November 2026

First reading of the learner's reviews after the generation changes of 18–19 November
(dense due-word sentences, card ceiling 6). The after-period is 40 sentence cards over two
days, so its numbers are early. Scripts: `missed-word-recovery-2026-11-20/` (run on the
server against the production database, read-only). The changes that followed are in the
experiment log, entry `2026-11-20 "Missed-word recovery lane, acquiring reserve, and the
fragment retry A/B"`.

## What the denser cards changed

| | Before (19 Oct – 18 Nov noon, 355 cards) | After (19–20 Nov, 40 cards) |
|---|---|---|
| Content words per card | 3.6 | 4.9 |
| Due or acquiring words per card | 2.39 | 4.05 |
| Median time per card | 35.5 s | 37.1 s |
| Due words missed (red or yellow) | 22.0% | 30.2% |
| Cards fully green | 53% | 28% |

The higher miss rate is mostly composition. Due established words by lateness (time past
due as a multiple of the interval):

| Lateness | Before | After |
|---|---|---|
| under 0.5× | 14% missed (232) | 13% (31) |
| 0.5–2× | 19% (166) | 19% (42) |
| over 2× | 30% (326) | 41% (87) |

More of the reviewed words are long overdue (median lateness 1.5× → 2.6×).

## A missed established word waits about two weeks

118 red misses between 12 October and 11 November on words whose stability before the
miss was at least 7 days (median 84 days; 3.4 days after):

- seen again in a sentence within 24 h: 8%; within 72 h: 16%; within 7 days: 25%
- first sentence appearance at least 12 h later: median 13.6 days, 78% correct
- never seen again by 20 November: 31
- by wait until the next appearance: 12–48 h 80% correct (10), 2–7 days 92% (13),
  over 7 days 72% (58)

Yellow misses (44): 14% back within 7 days, 64% correct at the next appearance (14), no
word card.

Supply is not the limit: of 66 recently missed words due on 20 November, 55 had a
never-shown sentence. The word is rescheduled about two days out and then competes with
about 800 due words; the dense generator drew its menu at random from the due pile.

## Word cards

- Throughput: at most one per minute, about one per two sentence cards. Red misses per
  card went from 0.42 to 0.83. On 19 November: 33 red misses, 19 word cards; the cards
  shown were for misses a median of 22 hours old (expiry is 24 hours).
- A correct word card moves the word from `lapsed` to `known` (80 of 110), with a median
  interval of 2.0 days. The selector's lapsed boost and established-lapse lane apply only
  to `lapsed`. The next sentence then came a median of 6.9 days later.
- Outcome at the first sentence at least 12 h later: word card correct 82% (55, median
  10 days), no word card 69% (26, median 18 days). Not a controlled comparison.
- The stored response time for word cards (median 1.1 s) is measured from the reveal and
  says nothing about the time a card costs.

## Words being learned

On 19–20 November 1 of 195 word reviews was an acquisition review, against 62 of 217 on
18 November, with 92 Box-1 and 21 Box-2 words due. A card's selection score is
coverage ** 1.5, so while the stock of 4–6-due-word cards is full they take every base
slot; an acquiring word that has been reviewed once has no boost. A replay on a snapshot
taken after most of the stock had been used showed acquiring words returning, which fits.

## Reading time by sentence length (since 1 August)

| Tokens | Cards | Median seconds |
|---|---|---|
| 4–5 | 371 | 22.7 |
| 6–8 | 1,025 | 33.0 |
| 9+ | 945 | 47.2 |

About 4 seconds per token plus a few per card. A 2–3-word phrase would cost roughly
12–15 seconds for one target word; a dense card costs about 9 seconds per due word.
Only 3 cards of three tokens or fewer have ever been reviewed.

## Size of the trade

About 13 red or yellow misses on established words per active day (305 over 24 active
days). Three follow-ups each is about 40 word-slots a day against 170–200 word reviews on
an active day.
