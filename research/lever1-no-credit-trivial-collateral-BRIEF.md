# Lever 1 — Stop FSRS-crediting trivial collateral recalls (implementation brief)

*Hand this to a fresh session. Self-contained. Read the "first reads" before touching code.*

## One-line goal
When an already-mastered word appears only as **collateral scaffold** (not the card's
target) and is at **high retrievability** (you definitely still know it right now), record
it as an **exposure** — keep showing it, keep it in the corpus — but do **not** feed it to
FSRS as a graded retrieval test. Let mature words actually decay so they come due and get
occasional *real* tests, and so the retention metric measures real recall again.

## Why (evidence — all from `research/analysis-2026-06-06-mature-words-fsrs-retune.md`)
- **76% of FSRS reviews happen at R ≥ 0.97** (63% at R ≥ 0.99). These are not tests — the
  user already knew the word; near-zero retrieval effort, near-zero information for the model.
- **95% of mature reviews fire early** (median actual gap 1.8d vs median stability 79.7d).
  The mature tier is not really being *scheduled* by FSRS — incidental collateral
  co-occurrence re-shows words every 1–2 days.
- **Stability is inflated by massed re-exposure.** Real-gap retention is ~84% (post-grad
  hard reviews, 10–30d and 30–90d bands all agree) versus the 0.95 target → stabilities are
  overestimated; the retention number is dominated by trivial recalls and is ~meaningless.
- Root cause is the FOUNDATIONAL invariant doing its job too literally: *every word in every
  sentence earns a graded review*, including mature words pulled in only as scaffold.

This is the lever every cut of the data points back to. The 2026-06-06 generation change
(at-risk scaffold bias, PR #197) was the *supply* side and is deliberately modest; this is
the *crediting* side and is the real retention-integrity win.

## First reads (mandatory — iterated area, CLAUDE.md Rule #14)
- `git log --since="3 months ago" --oneline -- backend/app/services/fsrs_service.py backend/app/services/sentence_review_service.py`
- `research/experiment-log.md` — grep `2026-04-13 "Lapse Recovery Tuning"`, `2026-03-18 "Every Word Earns Credit"` (the invariant), `2026-05-06` variant-ULK bug (review credit went to canonical but box never advanced → sentence reappeared as "Rescue" forever — **the exact failure mode to avoid recreating**).
- `docs/scheduling-system.md` §19.17 gate registry; `docs/design-principles.md`.
- This file's sibling analysis doc (full numbers + the metric-correction section).

## The invariant tension — read before designing
CLAUDE.md hard invariant: *"FOUNDATIONAL: Every word in every sentence earns review credit."*
Plus repeated user feedback "target == collateral are equal." This change carves a **narrow,
explicit exception** and must be framed as **exposure vs. test**, not "demoting collateral":
- An exposure is still **recorded** (the word is never invisible — that was the 307-snapshot
  lesson; see analysis doc). It just doesn't mutate the FSRS card or count in retention math.
- The exception applies **only** to high-R **mature** collateral. It must **not** touch:
  acquiring words (they need every collateral credit to graduate), lapsed, low-stability
  known, recently-missed, or any **due** word (a due word being covered IS the point of the
  card). Those keep full FSRS/acquisition credit exactly as today.
- **Requires explicit user sign-off** before flipping default-on.

## Where to intervene (anchored)
`backend/app/services/sentence_review_service.py` — the per-word credit loop:
- Line ~251: `credit_type = "primary" if ... else "collateral"`.
- Lines ~302–314: the `else` branch (non-acquiring, i.e. FSRS-carded words) calls
  `submit_review(...)`. **This is the intercept point.** For a `collateral` word whose card
  is high-R-mature-and-not-due, skip `submit_review` and instead write an exposure record.
- `backend/app/services/fsrs_service.py` `submit_review()` — the FSRS card mutation +
  `review_log` row. Either add a `credit_mode="test"|"exposure"` param here, or branch in the
  caller. Computing R: use the card's `stability` + `last_review` from `fsrs_card_json` and
  the FSRS-6 power curve `R = (1 + 0.2345 * t/S) ** -0.5` (helper already in
  `scripts/measure_at_risk_scaffold.py` and the analysis scripts).

## Design decisions (resolve with user / pick defaults)
1. **Threshold.** Simplest: intercept when `credit_type == "collateral"` AND `knowledge_state
   == "known"` AND `R_at_review ≥ 0.97` AND not currently due. (R≥0.97 ≈ the 76% bucket.)
   Alternative: `stability ≥ S_min AND due_date > now`. Prefer R-based — it's what "trivial"
   means.
2. **What "exposure, not test" records.** Recommend: (a) **no** FSRS card mutation (stability
   frozen so it can decay naturally toward due), and (b) a lightweight exposure record — a
   `review_log` row with a new `credit_type="exposure"` (or an `is_exposure` flag) that is
   **excluded** from FSRS optimizer + retention metrics, OR a separate interaction-log event.
   Keep `times_seen`/`total_encounters`/`last_seen` bookkeeping so the word is still "seen."
3. **Recency for selection.** An exposure SHOULD still update the recency the selector uses
   (we don't want to immediately re-show the same sentence), but must NOT reset the FSRS due
   clock. Verify which field the selector reads.
4. **Leeches & graduation.** Leech sliding-window and graduation count *reviews*. If exposures
   aren't reviews, audit those consumers so the math doesn't silently shift (see gate audit).

## Gate audit (CLAUDE.md Rule #8 — mandatory, this changes state-flow)
Walk every consumer of review credit / review_log and decide exposure handling:
- Retention % surfaces: `app/routers/stats.py`, `app/routers/review.py`, frontend stats —
  exclude exposures (they're not tests; including them would re-inflate the number).
- FSRS optimizer: `scripts/optimize_fsrs.py` (already filters `is_acquisition`; add exposure
  filter).
- Leech: `leech_service` sliding window — does an exposure count toward the window? (Probably
  not — a non-test shouldn't.)
- Graduation / acquisition: unaffected by construction (only `known` words intercepted), but
  confirm `start_acquisition` / canonical-resolution paths don't see a behavior change.
- Selector boosts: `NEVER_REVIEWED_BOOST`, `LAPSED_BOOST`, due-coverage, overdue escalation —
  these now see mature words actually going due. Expect more mature words in the due list;
  confirm sessions don't balloon or starve acquiring words.
- **The 2026-05-06 reappearance bug**: ensure an intercepted (non-credited) collateral word
  does NOT create a state where its sentence keeps reappearing because some box/clock never
  advances. The word's card is intentionally frozen, but it must not be flagged "due/rescue"
  spuriously by the freeze.

## Risks / what NOT to do
- Do **not** make words invisible — always record the exposure (307-snapshot lesson).
- Do **not** apply to acquiring/lapsed/low-stability/recently-missed/due — only high-R mature.
- Do **not** ship default-on without user sign-off + the gate audit above.
- Reversible: env flag (e.g. `ALIF_COLLATERAL_EXPOSURE_R` threshold, or
  `ALIF_NO_CREDIT_TRIVIAL_COLLATERAL=0` to disable).

## Sequencing (recommended — de-risk in stages)
1. **Observe first (flag default-OFF, no behavior change):** log exposure *candidates* (which
   collateral reviews WOULD be reclassified) for a few days; quantify real volume on prod and
   confirm it matches the ~76% projection.
2. **Flip the no-credit behavior** behind the flag; backfill nothing (only affects new reviews).
3. **Reverify metrics:** retention should *drop toward the true ~84%* then re-stabilize as
   FSRS starts seeing real gaps; watch leech rate, graduation rate, session size, due-coverage
   deficit, and the "rescue" reappearance rate.

## Measurement / validation
- **Real-data projection (pre-implementation):** replay how many mature words would start
  coming due (gap growth), expected stability deflation, and the retention-metric shift.
- **Simulation can't validate the pedagogy** — `app/simulation/student.py` learner model is
  blind to spacing (see analysis doc) — but CAN confirm no due-coverage/throughput regression.
- Success = mature words get genuine spaced gaps, retention metric reflects real recall,
  FSRS stabilities become trustworthy, with no leech/graduation/session-size regression.
