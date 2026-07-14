# Recovery health check — 2026-07-14

Post-absence recovery verification (14-day break ~Jun 21–Jul 4, recovery from Jul 5). Checked prod interaction logs, sentence-gen logs, LLM call logs, cron log, and live DB gate state.

## Learning side — recovering as designed

Per-day word-level review results (from `sentence_review.word_ratings`):

| day | cards | word reviews | word acc % | comprehension signals | lookups |
|-----|------:|------:|------:|---|------:|
| Jul 5 | 7 | 50 | 40.0 | 2 understood / 5 no_idea | 896 |
| Jul 8 | 21 | 127 | 82.7 | 7 understood / 14 partial | 790 |
| Jul 9 | 58 | 344 | 79.9 | 20 / 38 | 694 |
| Jul 10 | 77 | 415 | 78.3 | 33 / 44 | 418 |
| Jul 11 | 16 | 97 | 90.7 | 10 / 6 | 248 |
| Jul 12 | 3 | 24 | 75.0 | 0 / 3 | 366 |
| Jul 13 | 75 | 440 | 86.4 | 31 / 44 | 494 |
| Jul 14 (am) | 7 | 39 | 87.2 | 4 / 3 | 117 |

- Word accuracy climbed 40% → ~78–80% → 86–90%; "no_idea" disappeared after day 1, sessions now mostly partial/understood. Matches the user's felt experience (recognize after marking; conservative marking).
- Lookups per card fell from ~128/card (Jul 5) to ~6.6/card (Jul 13).

## Recovery gates — active and correct

Live gate state (Jul 14 ~10:30 UTC): `box1_actionable=113` (trigger ≥5), `box2_due=15` (<30), `main_fsrs_due=874` (trigger ≥750) → recovery mode ON. Effective intro budget today = 0 (7 primary reading cards so far, below the earn-in minimum). True-new acquisitions during recovery: 0–5/day (vs cap 30) — intake correctly throttled. Leech reintro admission correctly closed (Box 1 ≥20): `leech_reintro_capacity_deferred` events firing Jul 12–14.

- Leech engine suspended 42 words over the window (peak 15 on Jul 9, 13 on Jul 10) — expected under post-break failure rates; they queue for reintro once Box 1 drains.
- Graduations resumed (16 on Jul 13). Acquiring pile: 148 (121 Box 1) — pre-break acquiring words now all due; draining through sessions.
- Session `total_due_words` trend: 1219 (Jul 8) → 1009 (Jul 14). Slowly declining; strict main-lane due (874) still above the 750 recovery trigger, so intros stay throttled — correct.

## Generation side — healthy, two known warts

- `warm_sentence_cache` + cron pipeline producing sentences daily; quality gate approving 48–69% (rejections mostly `not_natural`, with sound Arabic-language reasons — gate is doing its job).
- LLM failover chain working: ~12–15% of Codex CLI calls fail with `codex CLI exit 1` dying mid-banner (truncated at "reasoning effort:") plus occasional 60s timeouts; each fails over to Claude CLI. Pattern predates the recovery window (visible Jul 4). Not user-affecting; worth watching if the rate grows.
- Jul 14 09:30 cron pass skipped entirely ("another material update active") — the shared flock was legitimately held, almost certainly by post-session `warm_sentence_cache` (user reviewing 09:00–10:06). Verified no flock holder remains; the 12:30 pass picks up the deficit refill (26 FSRS-due words with zero reviewable sentences, 12 generatable, rest backoff/inert).

## Verdict

All systems behaving as designed for a post-hiatus recovery: intake gated to ~zero, accuracy recovering steeply, leech + reintro caps engaging, generation and failover healthy. No action needed. Watch: Codex CLI failure rate; Box-1 drain over the next week (should fall from 121 toward ~20 before reintro admission reopens).
