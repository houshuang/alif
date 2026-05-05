# Aggressive Acquisition Runbook

This runbook covers the 30-new-words/day frequency-core experiment started on
2026-05-04.

## Daily Definition Of 100%

A day is complete when all three are true:

1. `introduced_today >= 30`.
2. Main-lane maintenance remaining is zero.
3. Today's slow-lane budget is complete.

Slow-lane completion does not mean all old low/null-rank artifact debt is gone.
It means the system sampled the planned 10% budget for the day without letting
that debt dominate the curriculum.

## Morning Check

Use the stats UI first. Then, if the numbers look odd, inspect the API:

```bash
curl -s http://localhost:8000/api/stats/analytics | jq '.daily_goal, .frequency_core'
```

Check:

- introduced today,
- main maintenance remaining,
- slow-lane remaining,
- recent accuracy,
- frequency-core next gaps,
- acquiring words with no active sentence.

## End-Of-Day Check

Pass for the day:

- 30+ new words introduced,
- accuracy >=90%,
- main maintenance cleared,
- no new growth in sentence-less acquiring words,
- slow-lane budget completed or nearly completed,
- user did not report random-feeling sentences or misleading source labels.

Watch, but do not panic:

- slow-lane total debt remains high,
- frequency-core unmapped count remains high,
- daily maintenance target grows during the day as acquisition cards become due.

## Stop Rules

Pause aggressive intros or roll back if any condition persists:

- two-day rolling accuracy <88%,
- acquiring backlog >140 and not falling,
- sentence-less acquiring words increase by >10,
- main-lane due carryover >40 for two consecutive days,
- zero-accepted multi-target group rate worsens,
- end-of-session daily goal reaches 100% while obvious due main-lane cards are
  still present.

## Rollback

The lowest-risk rollback is to reduce the experiment constants without removing
the frequency-core table:

- `DAILY_AUTO_INTRO_TARGET`: 30 -> 20 or 15,
- `HIGH_ACCURACY_INTRO_BACKLOG_CAP`: 120 -> 80,
- `INTRO_RESERVE_FRACTION`: 0.3 -> 0.2.

Keep:

- frequency-core stats,
- source provenance fix,
- multi-target validator requiring two targets,
- inactive salvage safety gates.

These are correctness improvements independent of the aggressive target.

## Operational Notes

- Do not add LLM calls to session build.
- Do not introduce bare-word review cards.
- Do not auto-create lemmas from generation repair.
- Do not hide unmapped frequency-core rows.
- Do not reduce acquisition exposure targets further in this experiment.

## 48-Hour Review

After two full days, record:

- introductions/day,
- sentence reviews/day,
- review accuracy,
- main-lane due at end of day,
- slow-lane due at end of day,
- acquiring words without active sentences,
- mean useful main-lane target units per sentence,
- multi-target generation acceptance rate,
- frequency-core top 500/1,000/2,000 learned and pipeline coverage.

Decision:

- Keep 30/day if pass criteria hold.
- Reduce to 20/day if accuracy or backlog is marginal.
- Roll back to previous intro constants if sentence quality or maintenance
  stability fails.
