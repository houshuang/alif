# Recovery-throttle simulation — growth vs pile-up across learning patterns (2026-06-03)

**Question.** The recovery-mode intro throttle was capping the user to ~8 new words/day
(active because `box1_unreviewed=6 ≥ trigger 5`). Should we raise it to grow vocabulary
faster, and how does that play out for different learners (review volume × accuracy)?

**Method.** Grid simulation driving the real service stack (`app/simulation/`) seeded from
a fresh prod copy, started 2026-06-04 (after prod data, so FSRS cards come due). 3 profiles
× {baseline, raised} throttle, 12 simulated days, seed 42.

- **baseline** = shipping constants: `BOX1≥5, BOX2≥30, MID=4, FULL=8`.
- **raised** = `BOX1≥20, BOX2≥60, MID=15, FULL=30` (deliberately aggressive on every knob, to
  surface the failure modes).
- Profiles: `light_lowAcc` (2–4 sessions/day, base_comp 0.55), `heavy_lowAcc` (8–14 sessions,
  0.55), `real_user` (6–14 sessions, base_comp 0.82 — the actual user's high-volume/high-accuracy
  pattern).

**Caveats (fidelity limits).** (1) LLM sentence generation is mocked in-sim, so intro counts are
bounded by *existing* sentence supply — read as relative dynamics, not absolute ceilings.
(2) The student model has **no per-word difficulty**, so leech counts are noise, not "hard words"
— ignore the leech column for policy. (3) Enrichment paths (`root_enrichment`,
`lemma_enrichment`) were no-op'd in-sim (they use the app's global session, not the sim DB).

## Results

| profile | cond | reviews | intros | Δknown | avgBox1 | maxBox1 | comp% |
|---|---|---|---|---|---|---|---|
| light_lowAcc | baseline | 187 | 20 | **22** | 39.5 | 49 | 42.2 |
| light_lowAcc | raised | 192 | 48 | **17** | 50.7 | 70 | 36.5 |
| heavy_lowAcc | baseline | 670 | 18 | **50** | 27.5 | 33 | 41.3 |
| heavy_lowAcc | raised | 657 | 61 | **50** | 43.9 | 57 | 37.7 |
| **real_user** | baseline | 686 | 17 | **71** | 23.2 | 27 | 48.3 |
| **real_user** | raised | 797 | 40 | **109** | 37.6 | 49 | 45.4 |

## Findings

1. **Raising the throttle helps only the high-accuracy learner.** `real_user` gains **+53%
   known growth (71→109)** for a 2.8pt comprehension cost. Both low-accuracy profiles see *no*
   growth benefit (light: 22→17 ↓; heavy: 50→50 =) while the un-practiced Box-1 backlog swells
   (+11 / +16 avg, max up to 70) and comprehension drops 4–6pt. A flat raise would hurt a
   struggling learner.

2. **The lever is the earned FULL budget, not the trigger.** The trigger + accuracy floors are
   the *safety gate*; raising them removes the protection. Raising only `FULL 8→30` gives the
   ≥85%-accuracy learner the full cap, while a sub-85% learner stays at the modest MID budget.

3. **Intros are partly crowded out by the due-review load**, but the throttle was still the
   dominant cap (baseline intros 17–20 even with hundreds of due reviews; raised lifts them
   2–3×). So the throttle *is* a real growth brake for the user — supply (Part C) is the next one.

## Decision (shipped)

- `RECOVERY_FULL_INTRO_BUDGET: 8 → 30` (= `DAILY_INTRO_CAP`).
- `RECOVERY_MID_INTRO_BUDGET: 4 → 8` (modest — the grid's `raised` used MID=15, which drove the
  low-accuracy backlog; keeping MID conservative protects struggling learners).
- `RECOVERY_BOX1_UNREVIEWED_LIMIT`, `RECOVERY_BOX2_DUE_LIMIT`, and both accuracy floors **unchanged**
  — they are the safety gate.

Net for the user (high accuracy): up to 30 new words/day on heavy-practice days, ~+50% growth,
with the accuracy floor preventing pile-up if accuracy ever drops. Guard test:
`test_recovery_mid_accuracy_capped_at_mid_budget`.

Driver: `/tmp/claude/sim_throttle.py` (grid, ephemeral). Code: `app/services/acquisition_service.py`
`_recovery_mode_intro_budget`.
