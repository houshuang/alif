---
name: understand-system-before-concluding
description: "Before drawing headline conclusions from metrics, read the code that produces each metric's events — the 2026-06-10 \"north-star collapse\" was a stock-vs-flow misread the user had to correct"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c15ff244-d6cc-4a94-96b5-a2b3b1620dea
---

On 2026-06-10 I declared a "north-star collapse" (net = graduations − leech suspensions
fell ~95/wk → ~20/wk) in a state-of-project review. The user pushed back from felt
experience ("I'm seeing lots of new words and gradually acquiring them") and was right:
event-level analysis showed known-word *stock* grew +89/week (near-record), the new-word
funnel converted at 43% in ≤2 weeks, and suspended words cost only 5.5% of review volume.
The user then told me directly: "make sure you do this properly in the future — you should
have read up and understood better how the system is designed before jumping to conclusions."

**Why:** My metric treated a leech suspension as −1 known word. In Alif's design a
suspension is a 3–14 day *cooldown* on a word that mostly was never known (only 20/132
suspended words had ever graduated), and leech verdicts are rate-based (accuracy <50% over
last 8 reviews) so a 2× review-volume increase mechanically compresses time-to-verdict and
spikes weekly suspension counts without any learning regression. I built the headline
metric before reading `leech_service.py` semantics or checking the stock curve.

**How to apply:**
1. Before interpreting any metric built from system events, READ the service that emits
   those events (what does a suspension/graduation/lapse actually mean in the lifecycle?).
   This is CLAUDE.md Rule 14 applied to *analysis*, not just fixes.
2. Stock before flow: check the level (known-word count over time) before netting flows
   (grads − suspensions). Flows from triage engines front-load and lie during intake spikes.
3. Treat the user's felt experience as a data point to reconcile, not to override — when
   aggregates and lived experience disagree, drop to event level (actual sentences, rating
   sequences, per-word funnels) before publishing a headline.
4. Cost-weight churn claims: "120 words suspended" sounded dire but was 5.5% of review
   volume. Always ask what fraction of user time/effort a problem actually consumed.

Related: [[verify-before-recommending]], [[check-prior-work-first]],
[[progress-metrics-verified-not-activity]].
