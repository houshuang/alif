# Snap page pre-match: measurement, 26 November 2026

A Snap tap waited on two calls: `/api/discover/lookup` (vocabulary match; a
model gloss only for words not in the vocabulary) and then
`/api/discover/lookup/sense` (one Sonnet call per tap: 3.7 s median, 6.0 s at
p90, 101 calls in November). The question was whether one background pass per
page, run right after OCR, can give the same answers so a tap needs no model call.

## Data

- Four snapped pages with logged taps (pages 10, 14, 15, 16 of `snap_pages`;
  179–227 Arabic tokens each), against a copy of the 26 Nov prod backup.
- 39 logged per-tap sense checks (`discover_lookup_sense`), 34 with a verdict
  (5 failed calls); 62 logged taps (`discover_lookup`).
- The vocabulary changed after the taps: words the checks found to be
  "different" were since added (مُرَاقَبَة, مَرْدُود, تَفَوُّق, كُلْيَة,
  اِرْتِقَاء, أَمْضَى, مُقِيم, إِثْر), and widened glosses were written. So the
  comparison is on the **word each method ends on** (its vocalized citation
  form), and the page pass was given the gloss the tap saw (`gloss_from`).

## Results

| Measure | Result |
|---|---|
| Taps the page pass covers | 62 of 62 |
| Same final word as the per-tap check | 30 of 34 |
| The 4 others, judged by hand | all acceptable: الداخلية (both say residency/internship), مثيراً (مُثِير = مُثِيرَة, a merged variant), خضوعها (masdar خُضُوع now exists; better than the verb), وصلب (صُلْب "hard" fits; the tap's صَلْب is doubtful) |
| Same word, but the tap widened the gloss and the pass did not | 3: غالباً (adverb "often"), بدورهما ("in turn"), ناحية ("aspect"); all three widenings are already stored |
| Matched surfaces the pass flags as another word or too narrow a gloss | 14–24 per page (14–20 % of ~100–120 surfaces), e.g. السلطة → سَلَطَة "salad" for سُلْطَة, أجد → جَادّ for وَجَدَ, مرتين → مُرّ "bitter" for مَرَّة |
| Time per page after OCR (local, Claude CLI) | 22–27 s with the gloss and the three sense calls in parallel |

**The check must be per surface, not per entry.** The first run checked one line
per vocabulary entry, as pre-read does, listing all its surfaces. It got two
words wrong that the per-tap check got right: عيون and عيّنت both resolve to
عَيْن, so the entry was judged as عَيَّنَ and عيون showed "to appoint"; آثار
and إثر both resolve to إِثْر, so إثر showed "trace". With one line per surface
both are right and nothing else got worse.

**The model step cannot be skipped.** On these pages one matched surface in
five or six is another word or needs a wider gloss, and the per-tap data showed
the same (the model changed 6 of 17 "clean" unique matches).

## What was built

`app/services/snap_prematch.py`, started after `/api/discover/snap` saves a page
(and by `GET /api/discover/snaps/{id}/prematch` for older pages). The Snap screen
polls that endpoint and answers a tap from it; taps before it is ready, or on
surfaces whose check failed, use the per-tap path. Every tap is logged as
`card_shown` with `card_type="snap_tap"`, `detail.source` (`prematch`/`live`)
and `detail.ms`, so the change in waiting time can be read from the logs.

## Review of the deployed version and the gloss restore (same day)

Version 1, deployed on 2026-11-26, wrote every "broader" widening it found to the
lemma (compare-and-set, as pre-read does). A Fable review of all 16 stored pages
(`review-2026-11-26.md`) judged its 113 writes: 78 good, 10 no-value rewrites,
11 doubtful, 14 bad (حَيّ → "life; alive", which is حَيَاة; سَمَّى → "to poison",
which is سَمَّ; عَشْرَة narrowed to compound numbers; book-specific senses on
تَنْظِيم, جَمْعِيَّة, دَاخِلِيَّة …), and 80 memory hooks cleared. All 110 lemmas
were restored from the pre-deploy backup (gloss and hooks; list in
`/opt/alif-backups/snap_prematch_gloss_restore_20261126.json`). Version 2 stores
the proposal on the page and shows it as "Here: …"; it never writes. The review's
other findings (verdict for every line, names, repeated surfaces with different
meanings, partial-failure retry) are open; see its fix list.

## Files

- `measure_prematch.py` — the per-surface measurement run.
- `compare_final_word.py` — the final-word comparison.
- `review-2026-11-26.md` — the Fable review of the deployed version.
- `per_tap_comparison.txt` — its output for the 34 checks. In the "pre" column
  the gloss shown is today's stored gloss; the check itself used the tap's.
