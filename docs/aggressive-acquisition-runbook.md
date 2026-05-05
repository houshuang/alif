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
curl -s http://alifstian.duckdns.org:3000/api/stats/analytics | jq '.daily_goal, .frequency_core'
```

Check:

- `daily_goal.introduced_today` and `introduced_remaining`,
- `daily_goal.main_maintenance_remaining`,
- `daily_goal.slow_lane_remaining`,
- recent accuracy (`retention_7d.retention_pct` from `/deep-analytics`),
- `frequency_core.next_gaps` — the actionable shortlist,
- acquiring words with no active sentence (deep-analytics).

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
- frequency-core unmapped count remains high (until builder runs with all sources),
- daily maintenance target grows during the day as acquisition cards become due.

## Hypotheses Under Test

The four hypotheses from `research/aggressive-vocab-experiment-2026-05-04.md`,
each with the specific signal that confirms or refutes it. Track these for the
duration of the experiment, not just the 48-hour gate.

### H1 — 30/day is feasible at >=90% accuracy

A 30/day target is feasible at >=90% recent accuracy if the daily goal requires
main-lane maintenance but only samples low/null-rank artifact debt.

| Signal | Source | Confirms | Refutes |
|---|---|---|---|
| `daily_goal.introduced_today` (rolling 3-day mean) | `/api/stats/analytics` | >= 28 | < 22 for 3 consecutive days |
| 7-day retention | `/api/stats/deep-analytics` | >= 90% | < 88% rolling 2-day |
| End-of-day `main_maintenance_remaining` | `/api/stats/analytics` | <= 5 most days | > 40 for 2 consecutive days |

### H2 — Frequency-core priority closes general-reading gaps faster

Frequency-core priority will improve general-reading progress faster than
book-specific priority alone because the top-N curriculum closes the largest
cross-domain gaps first.

| Signal | Source | Confirms | Refutes |
|---|---|---|---|
| `frequency_core.learned_prefix_count` weekly delta | `/api/stats/analytics` | >= +20/week | < +10/week with budget unspent |
| `frequency_core.bands[top_n=1000].coverage_pct` | `/api/stats/analytics` | rising | flat |
| Share of intros with `priority_tier="freq_core_*"` | session-build logs | >= 40% on intro-heavy days | < 20% (book/source still dominates) |

### H3 — Due-targeting efficiency improves within 48 hours

Combining demand-weighted multi-target grouping, validator-enforced 2+ target
sentences, inactive-sentence salvage, oldest-overdue-first selection, and
freshness/diversity relaxation for >=2 main-lane due words.

| Signal | Source | Confirms | Refutes |
|---|---|---|---|
| Mean main-lane due hits per selected sentence | session-build logs | >= 1.5 | <= 1.05 |
| Multi-target acceptance rate (validated/returned) | `multi_target_returned` events in `data/logs/llm_calls_*.jsonl` | >= 50% | < 35% |
| Zero-accepted multi-target group rate | same | < 15% | >= 25% |
| Salvaged-from-inactive sentence count per cron | `sentences_salvaged` ActivityLog event | > 0 most runs | always 0 (search broken or no candidates) |

### H4 — Refute conditions

The aggressive target is unsafe and the experiment must pause if **any** of
these holds for two consecutive days. These are the rollback triggers:

| Refute signal | Threshold | Action |
|---|---|---|
| 2-day rolling accuracy | < 88% | Reduce `DAILY_AUTO_INTRO_TARGET` to 20 |
| Acquiring backlog | > 140 not falling | Reduce target to 15, raise quality gates |
| Sentence-less acquiring count | +10 net per day | Pause intros, run extra `update_material.py` cycles |
| Main-lane due carryover | > 40 for two days | Reduce target, investigate session-time vs throughput |
| Multi-target zero-accepted group rate | worsening for 2 cron runs | Investigate prompt + group sizing |
| End-of-session daily goal | hits 100% with obvious due main-lane cards visible | Lane filter has a bug, investigate before continuing |

## Stop Rules (summary)

Pause aggressive intros or roll back if any condition persists across two days:

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

All three are in `backend/app/services/sentence_selector.py`.

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

## Frequency-Core Builder

The `frequency_core_entries` table is empty until populated. CAMeL+Kelly are
default sources; richer fusion needs files dropped into
`backend/data/frequency_sources/`:

```bash
ssh alif "cd /opt/alif/backend && PYTHONPATH=/opt/limbic .venv/bin/python3 \
  scripts/build_frequency_core.py --dry-run --entries 200"
# inspect output, then drop --dry-run

ssh alif "cd /opt/alif/backend && PYTHONPATH=/opt/limbic .venv/bin/python3 \
  scripts/build_frequency_core.py --entries 5000"
```

Until run: main/slow lane still works (falls back to `lemma.frequency_rank`
and source classification), but the freq-core word-selector bonus and stats
card show nothing.

## Cadence

| Frequency | Action |
|---|---|
| After every session | Glance at the SessionComplete daily-goal card |
| Daily | Open Stats screen → daily-goal card + freq-core card + retention |
| Daily (deep) | `curl analytics \| jq '.daily_goal, .frequency_core'` if numbers look off |
| Weekly | Append a row to "48-Hour Review" extended below — track learned_prefix_count, accuracy, intros/day, multi-target acceptance |
| 48 hours | First go/no-go checkpoint — see Hypotheses table above |
| 2 weeks | Decide: keep 30/day, reduce, or roll back constants |

## 48-Hour Review

After two full days, record below and append weekly thereafter:

| Date | Intros/day | Sent. reviews/day | Accuracy | Main due (EoD) | Slow due (EoD) | Sentence-less acquiring | Useful units/sentence | Multi-target accept | Top-500 learned | Top-1000 learned |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| _baseline 2026-05-04_ | 24 | 219 | 92.4% | 45 | 209 | tbd | ~1.0 | 37.7% | tbd | tbd |
| | | | | | | | | | | |

Decision after 48h:

- Keep 30/day if pass criteria hold.
- Reduce to 20/day if accuracy or backlog is marginal.
- Roll back to previous intro constants if sentence quality or maintenance
  stability fails.
