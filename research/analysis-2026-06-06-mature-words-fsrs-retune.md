# Mature-word repetition & FSRS retune analysis — 2026-06-06

**Question (user):** How is it going with the long-term words — do they get enough
repetitions? Should we retune the FSRS learning rate now that we have much more data?

**Data:** prod `alif.db`, snapshot 2026-06-06 14:27 UTC. Window: last 14 days
(2026-05-24 → 2026-06-06). Script: `/tmp/mature_word_analysis.py` (run on prod) +
`scripts/optimize_fsrs.py --db .../alif.db`. FSRS = library-default weights,
`desired_retention=0.95` (`fsrs_service.py:16`).

## Headline

1. **Long-term words get *more* than enough repetition — over-reviewed, not starved.**
   Mature pool (stability ≥ 60d) = 1,316 words. **69% (909) were reviewed in the last
   14 days**, at a **median actual gap of 1.8 days** — against a **median stability of
   79.7d**. **95% of mature reviews fire *early*** (before FSRS due); only 4% late.
   Overdue right now: **18/1,316 mature, 0/579 very-mature (≥180d)**. There is no
   starvation anywhere in the long-term tier.

2. **Do NOT re-deploy optimizer weights.** The optimizer now fits on 41,229 reviews
   (up from 21k) but the input is dominated by the same incidental over-review signal,
   so "more data" is more of the same corrupted signal, not better signal — and the
   production scheduler over-reviews regardless of what interval it computes, so new
   weights would barely change behaviour. The optimizer's proposed post-lapse weights
   are also *harsher* (recovery 3.73d → 1.74d), contradicting the deliberate
   2026-04-13 gentle-recovery tuning. Same conclusion as 2026-04-13, now stronger.

3. **The real, non-obvious finding: stability is being inflated by massed re-exposure.**
   When a word *does* get a genuine gap, retention is well below the 0.95 target:
   30–90d gap → **76.9%** (n=493), 10–30d → 84.1%. Words almost never reach a 90d+ gap
   (n=1 in all of history) precisely because collateral exposure keeps re-showing them
   every 1–2 days. So the median "79.7d stability" is largely unvalidated — FSRS keeps
   growing stability on cheap short-interval successes it never has to honour.
   (Caveat: long-gap words skew toward rarer/genuinely-harder vocab, so composition
   confounds the 77% — it is a directional signal, not a clean retention-at-due number.)

## Tables

**State distribution:** known 2213 · acquiring 200 · suspended 108 · encountered 99 ·
learning 69 · lapsed 52.

**14-day reviews:** FSRS n=5,650, acc(≥3)=**91.0%** (r1 6.1% / r2 2.8% / r3 91.0% /
r4 0.0% — Easy never used). Acquisition n=1,006, acc=74.5%. Reviews/day swing 12–1001
with study volume. (Note: 14d FSRS acc 91.0% vs cumulative 94.9% on 2026-04-23 — the
low-stability learning/lapsed tail drags the aggregate; mature-only short-gap retention
is 96.4%.)

**Stability bands (2,275 carded words):** 0–7d 238 · 7–21d 280 · 21–60d 441 ·
60–180d 737 · 180–400d 579 · 400d+ 0. Median 79.7d, mean 106.2d, p90 246d, max 326d.

**Mature cadence (≥60d):** reviewed-in-14d 909/1316 (69%); vs scheduled due —
EARLY 95% / on-time(±1d) 1% / LATE 4%; actual gap median 1.8d / mean 5.8d / max 82d.

**Overdue now:** mature 18/1316 (median 3.9d, max 18d, 0 over 30d); very-mature 0/579;
all carded 383/2275 (median 3.8d, max 101d, 5 over 30d).

**Retention by prior-gap band (all history):** 0–3d 96.4% (n=28137) · 3–10d 90.4%
(n=7200) · 10–30d 84.1% (n=3019) · 30–90d 76.9% (n=493) · 90d+ 100% (n=1).

**Optimizer (41,229 reviews):** lowers initial stabilities (w0–w3 −25% to −72%) and
post-lapse base/coupling (w11/w14 −26%/−42%) — i.e. it wants *shorter* intervals and
*harsher* lapse recovery. Not deployed.

## Why retuning weights is the wrong lever

FSRS assumes reviews happen near the scheduled due date. Here 95% of mature reviews
fire early at a 1.8d median gap vs an 80d schedule — the data-generating process
violates the model's core assumption. Fitting weights to that log optimizes for a
cramming pattern, not for the forgetting curve. The `desired_retention=0.95` target is
already structurally satisfied because early-reviewing guarantees high success; the
knob does nothing the over-review isn't already doing.

## The actual lever (decision for user — NOT changed)

If we want the long-term tier to *earn its spacing* (and surface real
forgetting-curve signal so FSRS estimates become trustworthy), the lever is **how
collateral re-exposure of already-mature words is credited**, not FSRS weights:

- **(a) Leave as-is.** The over-review is a feature: the user values long-gap
  recognition events, and the FOUNDATIONAL "every word in every sentence earns credit"
  invariant + repeated "target == collateral" feedback make suppression a hard sell.
  Cost: stability numbers are inflated/decorative; FSRS isn't really scheduling the
  mature tier — co-occurrence is.
- **(b) Suppress-credit for high-stability collateral.** Stop feeding FSRS a "review"
  when a stability-≥Xd word appears only as scaffold and isn't near due. Still *show*
  it; just don't log a retrieval event. Lets gaps grow → real retention signal → valid
  stabilities. **Conflicts with a foundational invariant — needs explicit user sign-off
  and a gate audit (CLAUDE.md Rule #8).**
- **(c) Log-as-exposure, not test.** Middle path: record collateral re-exposures of
  mature words as exposures (no FSRS stability update) but keep acquisition/box credit.

**Recommendation:** keep FSRS weights + retention as-is (option a on tuning). Surface
(b)/(c) as a real design question if the user wants the mature tier to be genuinely
spaced rather than incidentally hammered.

---

# Follow-up: is the primary+collateral SELECTION optimal? (same session)

**Question (user):** Is the current selection of words to expose (primary + collateral)
optimal for both acquiring new words and retaining learnt ones? Anything better given
the data? Run simulations?

**Why the simulator can't answer it.** `app/simulation/student.py:37`
`word_understood_probability` is a fixed function of `knowledge_state` + `stability`
only — **blind to context diversity, collateral-vs-primary, and spacing.** A word gets
"easier" in the sim solely by accruing review credit that advances its FSRS state. So
the sim can measure *throughput / load / coverage* but **cannot validate the
pedagogical value of content selection** — it would just reward cramming more
exposures. Ground truth must come from the real review log (51,836 reviews; **81%
collateral, 19% primary**). The sim is a second-stage tool for the coverage/throughput
dimension only.

Natural experiments on prod (`/tmp/selection_quality.py`, `/tmp/exp2_fix.py`):

**EXP-1 — context diversity → retention: NO measurable effect.** For known words at
matched review-count × frequency, split low/high diversity (distinct sentences / total
reviews). Pooled hard-retrieval accuracy (rating≥3 on reviews with prior gap ≥7d):
low-div 85.9% (n=455) vs high-div 84.5% (n=406), **Δ −1.4%** (noise; cells mixed
−7.5%..+12%). Diversity's payoff is UX (varied, non-boring sentences), **not memory**.
(Confound: low-diversity here = same sentence repeated = more massed reinforcement =
inflated stability, so the metric is entangled — but no signal survives anyway.)

**EXP-2 — acquisition is near-instant and richness doesn't affect sticking.**
1,522 graduations: median **2.37 d**, 21% same-session, median **3** exposures during
acquiring, p90 10 d. **Post-graduation hard-retrieval accuracy is identical regardless
of how the word was acquired**: few-exposure 83.7% vs many-exposure 84.5%; INSTANT
graduators 84.5% vs SLOW(>1d) 84.2%. → Rushing words through Tier-0 does **not** cost
retention; enriching the acquiring phase would **not** help it. Acquisition selection is
not a lever — the only acquisition question worth asking is *which* words to introduce
(curriculum / classical-lit north-star), not how.

**EXP-3 — 76% of FSRS reviews are trivial recalls.** Computing retrievability R at each
review from `pre_card.stability` + actual gap (FSRS-6 power curve): **63% at R≥0.99,
76% at R≥0.97** (last 14d: 74%). These come free as collateral while reading, so they
cost ~no user time — but they're logged as graded tests, which inflates stability and
makes the retention metric mostly trivial recalls. This is the dominant inefficiency and
it ties directly to the collateral-credit lever above.

**EXP-4 — collateral budget tilts to already-mature words.** Last-14d collateral: 5,097
exposures over 1,556 words — reasonably spread (top-10 = 7%, top-100 = 28%, so corpus
diversity tuning *is* working). But by recipient stability: **mature (≥60d) 60% / mid
(21–60d) 18% / needy (<21d) 20%.** Top recipients are unavoidable common scaffold
(رَجُل 174d, جَار 282d, اللّه 241d, شَجَرَة 246d, صَغِير 187d) — words you can't write
comprehensible Arabic without. Most of the free reinforcement lands where it's least
needed.

**Cross-cut robustness:** real-gap retention reads **~84%** three independent ways
(post-grad hard reviews 84%, 10–30d gap band 84.1%, 30–90d 76.9%) — consistently below
the 0.95 target → stabilities overestimated (confirms the retune section).

## Levers (decision for user)

- **NOT a lever:** more diversity or richer acquisition — data shows neither moves
  retention. Don't invest there for memory's sake.
- **Lever 1 (biggest; touches FOUNDATIONAL invariant):** stop FSRS-crediting collateral
  exposures of words already at R≥~0.97. Still *show* them (comprehension scaffold) and
  keep them in the corpus; just don't grade a non-test into FSRS. Lets stability reflect
  real retrieval, lets gaps grow so words get occasional *real* tests, makes the metric
  meaningful. Attacks the 76% directly. Needs user sign-off + gate audit; not
  sim-testable (learner blind) — validate via real-data projection + careful rollout.
- **Lever 2 (safe; sim-testable):** add a due/weak-coverage bonus to sentence scoring so,
  among comprehensible candidates, selection prefers sentences whose scaffold reinforces
  *due / lapsed / low-stability* words. Redirects the 60%-to-mature skew without touching
  the credit invariant. The simulator *can* measure this (due-coverage per session,
  deficit reduction — a throughput metric the learner model handles).
- **Curriculum thread:** acquisition machinery is healthy; the open question is whether
  intro selection is reaching classical/Quran vocab vs generic MSA frequency
  (north-star). Separate from the selection algorithm.

---

# Follow-up 2: is the comprehensibility gate the lever? (No) + the real root cause is generation

**Q (user): do we have data on the comprehensibility threshold (0.6)? A/B it? Or is
there low-hanging fruit within the margin first?**

**The gate is NOT binding — skip the A/B.** Across 1,950 active reviewable sentences
(classified with the real fw/proper-name path, `/tmp/comprehensibility_headroom.py`):

| comprehensibility | share |
|---|---|
| 100% known (zero unknown content words) | **82%** |
| 1 unknown | 16% |
| 2 unknown | 2% |
| **fail the gate today** | **~1%** |

Relaxing `COMPREHENSIBILITY_THRESHOLD` 0.6→0.5 unlocks **10 sentences (+1%)**; 0 sentences
are blocked by the `MAX_UNKNOWN_SCAFFOLD=2` cap that the ratio wouldn't already pass. An
A/B would test a knob connected to nothing. **Root cause exposed: the corpus is 82%
all-known — the bottleneck is supply (generation), not the gate.**

**Generation is the lever (user reached this independently).** Code path:
`material_generator.py:545` builds `known_words` dicts `{arabic, english, lemma_id, pos}`
— **drops knowledge_state/stability** — and `sentence_generator.sample_known_words_weighted`
weights scaffold *only* by `content_word_counts` (corpus frequency), never by learner need.
So the generator literally cannot prefer at-risk/acquiring scaffold; the LLM gets 500
frequency-balanced known words and writes with the easy common ones → 82% all-known.
Already noted in `IDEAS.md:1318`. Proven analogue: Polyglot `UNCONFIRMED_SCAFFOLD_BOOST=2.5`
(experiment-log 2026-06-01).

Proposed change (inside the single verified pipeline): (1) carry state+stability into the
`known_words` dicts; (2) bias `sample_known_words_weighted` toward at-risk words (ranking-
only, can't reduce yield); (3) optional stronger prompt-level "include 1–2 of these
review-due words", gated behind the quality reviewer. Guardrails hold: acquiring counts as
known for the gate (`sentence_selector.py:1341`); `MAX_UNKNOWN_SCAFFOLD=2` only caps truly-
unknown, which we're not adding. **Couples with Lever 1:** generation steers at-risk words
into sentences; Lever 1 (let mature words decay) supplies more at-risk words to steer.

**⚠ Correction after building + measuring it (experiment-log 2026-06-06 "At-risk scaffold
bias").** The "82% all-known → corpus too easy" framing above was over-read. "All-known"
means *comprehensible* (no encountered/new words), which the gate REQUIRES — it does NOT mean
*mature*. A before/after generation run (`scripts/measure_at_risk_scaffold.py`) showed fresh
sentences are already ~85% at-risk scaffold even with the bias OFF, because inverse-frequency
weighting already correlates with fragility. The bias is a modest, safe win (at-risk
words/sentence 1.93→2.38, +23%) and was merged, but it is NOT the lever. The review-side
maturity skew (EXP-3/EXP-4) comes from the aged sentence *stock* + unavoidable ubiquitous
scaffold, not fresh generation. **Lever 1 (don't credit R≥0.97 trivial collateral recalls)
remains the real lever.**

## ⚠ Metric correction: the "in danger" pool, measured by actual misses

The earlier "307 danger-zone (R 0.80–0.95)" snapshot badly undercounts lived experience.
R = 1.0 immediately after *any* review (pass or miss-then-pass), so a point-in-time R
snapshot excludes every word reviewed today, including all just-missed-and-fixed ones —
hence the word looks "safe" an hour after you missed it. (The system itself is fine: a
miss drops *stability* hard and sets state `lapsed` + `LAPSED_BOOST=3.0`; only the
analysis metric was wrong.)

Empirical miss-flow (`/tmp/mature_misses.py`, last 14d): **395 distinct words rated
red/yellow/confused (~32 distinct/day)**, 726 events (382 red / 172 yellow / 172 confused;
`confused` doesn't penalize FSRS per 2026-03-03). By stability-at-miss: **<21d 54% (215),
21–60d 74, 60–180d 60, ≥180d only 21** — durable vocab holds; churn is the acquisition
frontier (healthy). 114 words missed 2+ times (repeat offenders / leeches; several already
suspended). **Implication: define "at-risk" for the generation bias by stability/state +
recent misses (low-stability known + lapsed + acquiring + recently-missed = several hundred
words), NOT momentary R.** Ample material to steer.
