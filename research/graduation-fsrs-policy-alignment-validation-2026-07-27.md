# Graduation FSRS policy alignment — 2026-07-27

## Finding

The acquisition graduation path created its FSRS card with a local
`Scheduler()`, whose default desired retention is 0.90. All subsequent reviews
use Alif's production scheduler at 0.95.

This difference is invisible for an ordinary `Good` graduate: both schedulers
enter the default 10-minute learning step. It is material for a root-family
graduate initialized with `Easy`:

| Initialization | State | Stability | First due |
|---|---|---:|---:|
| Local default, 90% (before) | Review | 8.2956 d | ~8 d |
| Production policy, 95% (after) | Review | 8.2956 d | ~2–4 d with fuzzing |

The stability evidence is unchanged. The fix makes the due date honor the
declared retention policy and removes a five-day gap before the first
root-boost follow-up.

## Production evidence and workload bound

The available immutable interaction-log window (July 8–26, cutoff
`2026-07-26T13:41:55.819524Z`) contained 199 graduation events, of which 71
(35.7%) were root-boosted. The last seven days contained 42 root boosts among
110 graduations (38.2%).

Root boosts occurred on 16 active days in the 30-day window:

- mean 4.44 per active day;
- maximum 14;
- median long-run cost is one earlier first-due arrival per affected graduate,
  not an extra initialization review.

Right-censored first-FSRS delivery for root-boost episodes was 35/51 by four
days with 91.4% strict success. Among all delivered first outcomes, the delay
bands were:

| First FSRS outcome delay | Delivered | Strict success |
|---|---:|---:|
| <1 day | 42 | 90.5% |
| 1–4 days | 6 | 100% |
| 4–7 days | 5 | 60% |
| 7–10 days | 4 | 50% |

The late cells are small and selector-confounded, so they do not prove that
three days is optimal. They do rule out evidence for preserving the accidental
eight-day exception and point in the same direction as the already-selected
95% retention policy.

Every sentence-word outcome is counted equally. Primary/collateral is not an
evidence-quality distinction.

## Change and validation

`acquisition_service._graduate()` now calls the shared production scheduler.
The acquisition `ReviewLog.fsrs_log_json` nests
`graduation_fsrs_initialization` with:

- initialization policy version;
- scheduler policy version;
- desired retention;
- applied FSRS rating;
- root-boost flag;
- computed due timestamp.

Regression tests assert:

1. Easy/root-boost graduation is due in 2–4 days at 0.95;
2. Good graduation retains its 9–11-minute learning step;
3. the complete initialization decision is stamped.

No historical card is rewritten. Existing due dates remain untouched.

## Rollback and monitoring

Rollback is a code-only reversion to a local default scheduler; no data repair
is required. Segment future graduation cohorts at initialization-policy v1 and
monitor:

1. root-boost first-FSRS delivery by 3/4/7 days;
2. strict first outcome and later ≥7-day outcome;
3. daily due arrivals and session completion;
4. root-boost lapse rate versus non-boost graduates, with graduation reason
   reported as a confounder.

Do not retune weights or rewrite old Easy cards from this evidence.
