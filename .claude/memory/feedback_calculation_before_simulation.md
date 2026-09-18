---
name: calculation-before-simulation
description: "User pushed back twice on hours-long full simulations — lead with the back-of-envelope calculation, use simulation only to validate its shakiest inputs"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: f59db1f0-915e-4a47-aa30-7b0a9dc3d2fd
---

During the 2026-07-14 volume-sweep session, the user challenged multi-hour simulation runs twice ("you will use 10 hours??", "not sure the value vs just doing calculations"). The calculated projection (intake caps × recovery gates × coverage math) answered ~80% of the question in minutes and the user was satisfied with it.

**Why:** full-stack simulation of Alif's scheduler costs hours of wall-clock; most of the answer is determined by known constants (DAILY_INTRO_CAP, recovery budgets, graduation rates) and simple arithmetic. Only state-dependent interactions (recovery-exit timing, intro crowding, break pile-up) genuinely need simulation.

**How to apply:** for "what if I do X" projections, FIRST deliver the calculation with explicit assumptions, THEN name the 2-3 hand-wavy parameters and offer a targeted (short-horizon) sim to pin only those down. Never make a 90-day × N-variant sweep the primary vehicle. See `research/analysis-2026-07-14-momo-readiness-volume-sweep.md`.
