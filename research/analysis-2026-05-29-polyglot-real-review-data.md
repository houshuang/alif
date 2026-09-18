# Polyglot (Latin/Greek) — real review data vs Arabic assumptions

**Date:** 2026-05-29
**Source:** prod `/opt/alif/polyglot/polyglot.db` (read-only), 3,727 review_log rows, 2026-05-20→29.
**Script:** `/tmp/claude/poly_analysis.py` (read-only; not committed — re-pull from prod).
**Supersedes the framing of:** `analysis-2026-05-29-alt-vs-both-scheduling.md` and the two
`sim_*_2026-05-29.py` scripts, which assumed an Arabic-style learner introducing many new
words per day. That is NOT how Polyglot is used (see below). Those sims remain valid only
as illustrations of SRS timing mechanics, not as models of this learner.

## The setup is fundamentally different from Arabic (documented in polyglot/CLAUDE.md)

- **Arabic:** sentence-SRS is the primary loop; ~every review_log row is a graded recall test.
- **Polyglot:** **reading-as-mapping** is primary. Advancing a page logs a green
  "comprehension review" over every *untapped* content word (`reading_intake.apply_page_review`).
  For assumed-known scaffold (known, no FSRS card) this calls `record_scaffold_confirmation`,
  which writes a ReviewLog with `scaffold_confirmation: True` but **creates no FSRS card**.
  Discriminators live in `app/routers/stats.py:548-659` (`knowledge_origin` ∈
  {pre_known, cognate_known} = assumed; `fsrs_card_json IS NOT NULL` = real target;
  `confirmed_at` = reading-confirmed; `judged` = real red/yellow/green signal).

## What the data shows

### Word classes (most vocabulary bypasses SRS)
| | Latin | Greek |
|---|---|---|
| known (total) | 1528 | 1706 |
| — assumed scaffold (no card) | 1519 | 1667 |
| — of those exposure-confirmed by reading | 693 | 548 |
| — FSRS-carded (genuinely tested) | 9 | 39 |
| acquiring (real pipeline) | 113 (b1 49 / b2 64 / b3 0) | 204 (b1 55 / b2 132 / b3 17) |
| learning / lapsed / encountered | 17 / 0 / 0 | 13 / 2 / 34 |
| graduated through system | 27 | 51 |

### The familiarity illusion is real but bounded
Assumed-known words that later **failed** (`first_failed_at` set):
- **Latin: 131 (8.6% of known). Greek: 118 (6.9%).**
- → "I recognize it" holds ~92% of the time, but ~249 words have already bounced.
- Self-corrects: a red tap in reading lapses the word into acquisition.

### The words actually flagged unknown are HARD (not easy)
Acquisition recall tests (rating ≥3 = recall):
- **Latin: 234 tests, 39% Again. Greek: 650 tests, 48% Again.**
- FSRS-card tests: Latin 20 (95% Good), Greek 235 (95% Good) — over the 90% target, but young/small.

### Empirical forgetting curve (real tests only, scaffold excluded)
| elapsed | Latin recall | Greek recall |
|---|---|---|
| <2h | 97% (n=31) | 95% (n=164) |
| 2–6h | 100% (n=5) | 80% (n=65) |
| 6–18h | 73% (n=22) | 61% (n=71) |
| 18–36h (≈1d box) | 67% (n=3) | 59% (n=90) |
| 36–72h (≈3d box) | 66% (n=59) | 60% (n=177) |
| 3–7d | — | 84% (n=44) |

## Interpretation

1. **Prior knowledge validated for the bulk.** ~92% of marked-known holds; the reading-as-mapping
   model keeps time low by confirming, not drilling.
2. **Two real taxes:** ~8% familiarity illusion + ~44% (avg) acquisition fail rate on flagged words.
   The residual you tap is genuinely hard and needs spacing — it does NOT stick easily.
3. **Box-lengthening is NOT supported by the data.** Flagged-unknown words decay to ~60% within a
   day — the short 4h/1d boxes catch them near the right time. The "strong learner → longer boxes"
   idea applies to the recognition pool, which already skips boxes via reading confirmation.
4. **Real levers** for "more learning, less time": memory hooks / intro support for the hard
   residual (deferred in Polyglot), not interval tuning.

## Open data gaps / next steps
- Long-interval buckets (1d–7d) are thin on real targets; revisit in a few weeks.
- Greek has 235 FSRS reviews → enough to trial `backend/scripts/optimize_fsrs.py --db polyglot.db`
  for a Polyglot-specific retention/weights, but treat as provisional (young, 95% recall suggests
  intervals could stretch).
- Consider logging an explicit `graduation_tier` and `event_class` to avoid reconstructing
  scaffold-vs-test from `fsrs_log_json` each time.
```
