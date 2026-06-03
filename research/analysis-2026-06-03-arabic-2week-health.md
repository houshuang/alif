# Arabic 2-week health check (2026-05-20 → 06-03)

Fresh prod pull (2026-06-03). Goal lens: grow vocabulary fast **and** maintain what's learned.

## Verdict: healthy, one real leak (the due-coverage deficit)

### Snapshot
- **2170 known**, 61 learning, 42 acquiring, 117 encountered, 50 lapsed, 106 suspended (2546 total).
- **14/14 days studied**, 115-day streak, 5,978 reviews in 14d, 7.6 sessions/day.

### Growth (14d)
- 132 introduced, 110 graduated to FSRS-known. Daily intros only 5–11 (cap is 30).
- Frequency coverage: top-100 content **95%**, top-500 90%, top-1000 93%, top-2000 91%.
- Median 2 reviews to graduate (Tier-0 instant grad working).

### Maintenance (14d)
- **FSRS retention 92.8%** (94.8% last 7d, trending up). Median known-word stability **87 days**
  (p25 30, p75 184). 129 known words with stability <7d (fresh grads); 0 below the 1d floor.
- Overdue FSRS: 335 cards (128 in 0–1d, 120 in 2–7d, **84 in 8–30d**, 3 >30d).
- Leeches: 116 suspension events in 14d, but 65 already reintroduced & progressing (system working).
  311 words ever leeched; chronic tail ~102 leeched 3+ times.

### The leak — due-coverage deficit (recurring)
- **79 due FSRS words have no reviewable sentence** (66 known, 7 learning, 6 lapsed); 28 ≥8d overdue.
- Root cause: retirement protects only the *target* word's sentence count, not collateral; salvage
  required ≥2 due-word coverage. Fixed 2026-06-03 (last-sentence guard + deficit-aware salvage).
- Stale-verification orphans: only 6% (114) — the May-29 sweep held; no action.

## Why intros are slow (corrected from an initial "supply-bound" read — it's gates)
1. **Recovery throttle active** (`box1_unreviewed=6 ≥ trigger 5`) → daily budget 4–8 not 30. Fixed
   2026-06-03 (accuracy-gated FULL budget 8→30; see throttle-simulation analysis).
2. **Supply wall** rank 2000–3000: 224/225 un-imported `frequency_core_entries ≤3000` flagged
   `needs_manual_review`; only ~50 gated+ready. → Part C (LLM value judge).
3. Mid-freq selection tier 120 < textbook 220 / book 200 / story 195.

## Stats display is dishonest
"Top frequency gaps" lists function words (ال), merged compounds (اليوم→يوم), and suspended
leeches — not genuine missing content. → Part D (classify gaps; honest denominator).
