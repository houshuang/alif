# Maintenance Passage Efficacy — Re-run (2026-06-03)

**Verdict: KEEP, and raise the generation cap.** The "longer passages for
well-known maintenance words" experiment pays for itself. The 2026-05-13
first-look's headline ("passages are 4× slower per word") does **not**
replicate once idle outliers are filtered and the sample is larger — clean-read
speed is equal to single sentences, throughput per card is ~3× higher, and
band-matched FSRS retention is equal-or-better. The binding constraint now is
the generation throttle, not the format.

This is the re-run the 2026-05-13 snapshot (`passage-efficacy-2026-05-13.md`)
explicitly scheduled for "early June, ~3 weeks out."

---

## What the experiment is

Maintenance passages = 3–5 connected sentences (`Story.format_type =
"maintenance_passage"`, sentences carry `source="passage"`) generated from a
learner's **comfortable, due FSRS vocabulary** and shown as a single reading
block instead of as isolated single-sentence cards. The hypothesis (from
`experiment-passage-reading-speed.md`, 2026-03-03): known words can be
maintained "in flow" inside connected text at least as efficiently as in
disconnected sentences, with better context scaffolding and enjoyment.

- Assembly gate (`sentence_selector.py`): `PASSAGE_MIN_SENTENCES=3`,
  `PASSAGE_MAX_SENTENCES=5`, `PASSAGE_MIN_DUE_WORDS=3`,
  `PASSAGE_PREFERRED_DUE_WORDS=4`, `PASSAGE_REVIEW_STATES={known,learning,lapsed}`.
- Generation throttle (`material_generator.py`): `MAINTENANCE_PASSAGE_MAX_RECENT=2`
  per `RECENT_WINDOW=12h`, `MIN_DUE_TARGETS=6`. → **max ~4 passages/day.**

## Window & method

- **Window:** 2026-05-08 → 2026-06-03 (~26 days), prod.
- **Source:** `review_log` joined to `sentences.source` (retention cohort) +
  `interactions_*.jsonl` `sentence_review` events (speed/ratings cohort).
- Scripts: `/tmp/claude/analyze_passages_v2.py` (retention via DB),
  `analyze_passages_v3.py` (speed/ratings via logs).
- **Label fix:** `parent_card_type` is `null` in **every** event — all reviews
  arrive through the offline **sync-replay path** (`source:"sync"`), which logs
  `sentence_review` *without* `parent_card_type` even though the direct endpoint
  sets it. Passage-block cards are instead identified by `len(sentence_ids) >= 2`
  (a passage card submits one event covering all its sentences); the retention
  cohort is labeled by `sentences.source = "passage"`. See "Instrumentation gap."

## Volume — still thin, throttle-bound

| Card type | Block cards (≥2 sent) | Word-reviews | Share of reviews |
|---|---:|---:|---:|
| **passage** | **13** | **596** | ~5 % |
| single sentence | 1,234 | 11,334 | ~95 % |

~0.5 passage cards/day actually shown. The funnel is intentionally narrow
(`MAX_RECENT=2/12h`, `MIN_DUE_TARGETS=6`), so the experiment runs at low volume.
Retention numbers (596 word-reviews) are reasonably sized; the block-card speed
sample (13 cards) is directional.

## Reading speed — the 4× claim does NOT replicate

Idle-filtered (<20 min/card), per Arabic word:

| Card type | n | median ms/word | p25 | p75 |
|---|---:|---:|---:|---:|
| passage | 10 | **4,103** | 3,536 | 4,480 |
| single sentence | 1,185 | 4,557 | 3,151 | 6,867 |

Passages are **equal-to-marginally-faster per word**, not 4× slower. The
2026-05-13 "4×" was an artifact of n=8 plus unfiltered idle time. Of the 13
passage cards, **9 are clean reads at 2.6–6.9 s/word**; 4 were left open
30–105 min (`response_ms` of 2.1M / 4.7M / 1.0M / 6.3M ms) — phone-down time,
not reading time. Those 4 dominate any mean and produced the old p75 of
~2 min/word. Medians + idle-filter remove them.

**Throughput per card:** passage median **15 words/card** vs sentence **5**.
At equal per-word cost, a passage maintains ~3× the words per card-load/swipe —
i.e. less per-card overhead for the same maintenance volume.

## Accuracy & retention — equal or better

Per-word ratings (1=Again … 3=Good):

| Card type | %Good | %Again |
|---|---:|---:|
| passage | 94.5 % (189/201) | 4.9 % (10) |
| single sentence | 88.7 % (5,553/6,269) | 8.3 % (517) |

Passages look cleaner overall, but that's partly because they target
higher-stability words. Controlling for that — **median FSRS stability delta
(days gained per review), by pre-review stability band**:

| pre-stability band | passage Δ (n) | sentence Δ (n) | passage %Again | sentence %Again |
|---|---:|---:|---:|---:|
| <1d | **0.56** (19) | 0.05 (331) | 26 % | 29 % |
| 1–7d | **1.68** (71) | 0.77 (1,591) | 17 % | 12 % |
| 7–21d | **3.56** (60) | 2.89 (1,548) | 8 % | 12 % |
| 21–60d | 1.66 (88) | **3.62** (1,776) | 5 % | 4 % |
| 60d+ | **3.07** (332) | 3.03 (4,176) | **0.3 %** | 1.2 % |

- Passages **match or beat** sentence stability gains in **4 of 5 bands**.
- The dominant passage band is **60d+ (56 % of passage reviews)** — exactly the
  comfortable words it targets — where it's **dead even (3.07 vs 3.03) with 4×
  fewer lapses** (0.3 % vs 1.2 % Again).
- Only **21–60d** underperforms (1.66 vs 3.62). Most plausible cause: passages
  opportunistically exercise words that are *in the passage* but not yet due, and
  reviewing a known word early yields a smaller stability jump (FSRS diminishing
  returns). This is the same collateral-credit tradeoff the whole app runs on —
  not waste, just lower marginal gain. Not a reason to kill.

## Conclusion

Against the 2026-05-13 kill criterion ("if passages still show 4× ms/word *and*
their lemmas regress in stability faster than matched sentences, kill it"):
**neither condition holds.** Speed is equal, throughput is 3× higher, retention
is equal-or-better in the band that matters. The format works.

### Recommendations

1. **Keep the experiment.** It is not wasting time relative to short sentences;
   if anything it's more card-efficient and adds connected-reading value.
2. **Raise the generation cap.** Volume is throttle-bound at ~0.5 cards/day shown.
   Loosen `MAINTENANCE_PASSAGE_MAX_RECENT` (2→4–6) and/or shorten `RECENT_WINDOW`
   so more known-word maintenance flows through passages. Re-measure the 21–60d
   band after, to confirm the early-review dip doesn't widen at scale.
3. **Fix the instrumentation gap (cheap).** The sync-replay branch in
   `routers/review.py` (the `source:"sync"` path, ~L547) logs `sentence_review`
   **without** `parent_card_type`, so the field the 2026-05-13 doc asked for is
   null in practice and this analysis had to fall back to `len(sentence_ids)>=2`.
   Pass `parent_card_type` through the sync payload + replay so future rollups
   don't depend on the heuristic.
4. **Clip idle time.** `response_ms` includes phone-down time (4/13 passage cards
   = 30–105 min). Cap or record active-read time at submit so time metrics aren't
   polluted by backgrounded cards.

## Caveats

- 13 block cards is small; speed is directional. The 596-review retention cohort
  is the load-bearing evidence and points clearly positive.
- Passage cohort skews to high stability by design — read every comparison
  band-stratified, not pooled.
