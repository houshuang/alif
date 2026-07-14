# Momo readiness + daily-volume sweep (2026-07-14)

**Question.** "What difference does 30 / 50 / 100 / 120 / 150 cards per day make over the
next 3 months — and when can I read an interesting YA book (Momo) fairly fluently?"

## Part 1 — Momo is the right goal, and it is close (SOLID)

Method: stratified 23-page OCR sample (~2,586 Arabic tokens) from the user's own Momo
PDF (`bookifier/bilingual/input/momo/momo_ar_michael_ende.pdf`), Gemini
`gemini-3-flash-preview`, one page verified word-for-word against the scan. (One blank
page produced a fully hallucinated Islamic text at temperature 0 — blank-page filter
added; OCR of scans must never be trusted without a visual spot-check.) Token→lemma
mapping via the production-hardened `build_comprehensive_lemma_lookup` + `lookup_lemma`
path (per CLAUDE.md invariant), classification by resolved lemma. Learner states from
the 2026-07-14 09:00 prod backup. Raw token map:
`research/simdata_volume_2026-07-14/momo_tokenmap.json`; sample text kept out of the
repo (`tmp/momo_sample_2026-07-14.txt`).

| metric | Momo (2026-07-14) | Bamboo Stalk (2026-06-03, full novel) |
|---|---|---|
| coverage now (function + known/learning) | **87.3%** | 83.7% |
| + in-progress (acquiring/lapsed/encountered) | **91.0%** | 87.8% |
| + top 25 in-book gaps | 94.0% | — |
| + top 50 in-book gaps | **95.0%** | 86.5% |
| + top 150 | ~98.8%¹ | 88.4% |
| distinct gap words in sample | 177 | 2,464 (full text) |
| unmapped/OOV tokens | 8.0%² | — |

¹ Sample-based curve overstates large-N gains (Heaps' law: a 47k-token novel has a
longer gap tail than a 2.6k sample). Treat +150 → 98.8% as optimistic; the +25/+50
numbers are reliable.
² OCR errors count as unknown, so true coverage is slightly *higher* than reported.

**Reading: the in-progress bucket matters.** 3.7pp of the gap is words already in
acquisition/lapsed — recovery-mode review (no new intake) converts these first. Coverage
~91% is reachable with zero new words; 95% ("reading propels me" territory per Nation's
thresholds, YA + known story lowers the effective bar) needs roughly the top 50–100
Momo-specific gaps beyond that.

Time cost per volume from real response times (median 24.7s/card, last 30 days):
30/day ≈ 15 min · 50 ≈ 25 min · 100 ≈ 45 min · 120 ≈ 55 min · 150 ≈ 70 min.
Historical actual volume: Apr 69, May 93, Jun 67, Jul 40 cards/active day — 100/day is a
proven habit level, 150/day is +60% above the best month.

## Part 2 — 45-day volume sweep (IN FLIGHT at write time)

Driver: `research/sim_volume_sweep_2026-07-14.py` — real service stack from the fresh
prod backup, one day at a time, with two production supply lines emulated (LLM is mocked
in-sim): daily frequency-core intake (FCE→Lemma, rank order, gates stamped) and
synthetic verified sentences for the intro frontier + starving in-flight lemmas.
Variants: v30/v50/v100/v150 at 7d/wk, v100 weekdays-only, v100 with a 2-week mid-plan
break; 45 simulated days, checkpoints every 15 with full known/learning/acquiring
lemma-id sets; days 46–90 to be extrapolated from each run's day-25–45 slope. Data lands
in `research/simdata_volume_2026-07-14/*.json`; summarize with `summarize_sweep.py`,
map to Momo coverage with `map_coverage.py <momo_tokenmap.json> <run jsons...>`.

Day-1 sanity (all variants): total_due ≈ 985 and intros gated to 0 — matches the live
recovery state exactly (cross-check: `analysis-2026-07-14-recovery-health-check.md`).

### Sim-harness performance lessons (they cost the evening)
- `sentence_words.lemma_id` has **no index** in the prod schema — added sim-side
  (`ix_sim_sw_lemma`). Worth considering for prod.
- `db_setup.py` sessionmaker now uses `expire_on_commit=False` — `build_session`
  commits mid-call, and expiry made every subsequent ULK attribute access a single-row
  refresh SELECT (~4k extra queries per build).
- SQLite page cache must exceed DB size (~117MB): at 64MB the selector's scan-heavy
  queries thrash permanently (10× slowdown, processes that look "stuck").
- Sim engine gets no app PRAGMAs (listener is bound to `app.database.engine`) — driver
  applies WAL/synchronous=OFF/cache/mmap itself.
- Root/pattern/lemma-enrichment + memory-hooks hooks spawn threads that use the app's
  global session (not the sim DB) — no-op them in any sim driver (root/pattern ones were
  already known from the 2026-06-03 throttle sim; lemma/hooks ones are new finds).
- Background-shell children run niced/darwinbg on macOS; don't chase phantom
  "contention" before checking `ps -o nice,pri` and the *page cache*, and `sample <pid>`
  beats theorizing: the "0.1% CPU stuck" process was CPU-pinned in a B-tree scan.

**Fidelity caveats** (inherit 2026-06-03 list): synthetic sentences are not natural
Arabic (this measures scheduling dynamics, not comprehension difficulty); no per-word
difficulty in the student model; comprehension params = calibrated profile (base 0.80).

## Part 3 — Goal frame (to finalize when sweep lands)

- Anchor: reach **≥95% Momo coverage** (function+known), then switch primary time to
  actually reading Momo with Alif as lookup+capture, per the Jul 10 source assessment
  ("physical book remains the primary reading surface").
- The sweep decides: date each volume crosses 95%, marginal value of 100→150, cost of
  weekends-off and of a 2-week break.

**Status: Part 2/3 pending sweep completion — resume by running `summarize_sweep.py` and
`map_coverage.py` over `research/simdata_volume_2026-07-14/` and finishing this doc +
experiment-log entry.**
