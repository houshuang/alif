---
name: Verify before recommending
description: Before recommending manual actions or quoting derived metrics, check that the action isn't already automated and that metric denominators are filtered symmetrically with numerators.
type: feedback
originSessionId: e88adbf3-a48f-4e89-b82b-2336a7899e9b
---
Before recommending any manual operational step, check whether it's already
automated (cron, scheduler, hook). Before quoting a derived metric, check
that the denominator is filtered the same way as the numerator.

**Why:** In one alif session (2026-04-21), the user caught me twice on
the same class of mistake:

1. Recommended running `update_material.py` and `rotate_stale_sentences.py`
   manually — they run every 3h via `/opt/alif-update-material.sh` in root's
   crontab, and have been for months. User: "arene't those triggered
   automatically in cron?"

2. Reported "Top 100 coverage: 58% known" from `learning_analysis.py` and
   drew conclusions from it — but the denominator counted function words
   while the numerator couldn't (function words aren't SRS-tracked). User:
   "i think your analysis is wrong given the amount of function words that
   are not tracked." Real coverage was 81%.

Both mistakes led me to overclaim severity ("gummed up", "pool over
capacity") on things that were actually fine.

**How to apply:**
- Before any "you should run X" recommendation: grep crontab, systemd
  timers, and scheduled-task configs for X. If found, say "it's
  already automated" instead of recommending a manual run.
- Before quoting a percentage or ratio from an analysis script: read
  the numerator/denominator construction and confirm the same filter
  applies to both. If the denominator is "anything in corpus" and the
  numerator requires SRS-tracked knowledge, the metric will systematically
  understate reality whenever the two categories differ.
- "Check the harness before blaming the contents" — if production data
  looks alarming, suspect the metric before the data.
