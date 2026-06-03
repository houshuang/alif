---
name: feedback_progress_metrics_verified_not_activity
description: "How the user wants learning-progress framed — verified words as the goal, never headline activity/review volume, always label state-vs-event units"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 1a872ea0-429c-4995-b658-61df0d71706f
---

For Polyglot/Alif stats and any progress display, the user's mental model:

- **The goal number is VERIFIED words** — lemmas actually proven (met in real
  text and not flagged, or recalled cold), PLUS ex-gaps that climbed into FSRS.
  In Polyglot data terms: `known_summary.exposure_confirmed + fsrs_known`.
  "Assumed known but never seen" (`assumed_unconfirmed`) is shown dimmer/secondary.
- **Never headline FSRS-review volume.** "How many flashcard reviews I did" is
  not interesting — reading is the primary learning mode (~87% of Polyglot
  reviews are passive scaffold-confirmations, not recall tests). Recall accuracy,
  if shown, must exclude `scaffold_confirmation` rows or it's a meaningless ~95%.
- **Never put an ACTIVITY count (events: "342 reviews today") next to a STATE
  count (words: "10 recall-tested") without labelling units.** Conflating the two
  was the core confusion that triggered the 2026-05-29 stats redesign (he asked
  "10 recall-tested but I did 342 reviews?" and "+720 this week but only 708
  total?"). Weekly deltas must be defined so they can never exceed the total
  (e.g. "currently-verified words first proven in the last 7 days").
- Likes Alif-style **flow/funnel** views (Leitner Box 1→2→3→graduated;
  gaps found→in-recovery→closed) and per-day charts with graduations + gaps
  overlaid. Wants both daily/weekly progress AND overall lifetime totals.

**Why:** repeated, strongly-held — he iterated 6+ times on the stats redesign.
**How to apply:** when building/changing any stats or progress UI (either app),
lead with verified/known word counts, label event-vs-word units, and put the
trend in a clearly-captioned chart (flag one-time seed-import spikes). Shipped in
PR #176 (`polyglot-stats.tsx` + `routers/stats.py`); see [[feedback_polyglot_mirror_alif]].
