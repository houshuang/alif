# Post-injection learning checkup (2026-07-20)

**Question (user).** Analyze the last 10 days: 2-day absence mid-window, plus two large
deliberate word injections (finished bilingual book + a project import). Are the many
"new word" intro cards brand-new intake (which would be wrong with Box 1 this full), or
first-teach cards for words already in Box 1? Will daily reviews dig the hole out?
Anything to tweak?

**Method.** Read-only prod queries (`/tmp/learning_analysis_10d.py`, `/tmp/flow_analysis.py`,
`/tmp/cohort_check.py` — ReviewLog, UserLemmaKnowledge, `recovery_status()`,
`reviewable_coverage_counts()`) + interaction-log parse (`session_start`, `card_shown`) +
code reading of `_build_intro_cards` / `_auto_introduce_words` / discover `add-batch`.

## Finding 1 — The intro cards are Box-1 first-teach, not new intake ✅

`_build_intro_cards` (sentence_selector.py:2217) builds "new word" cards only for lemmas
with `knowledge_state='acquiring'` **and** `times_seen=0` — i.e. words already admitted to
Box 1 that have never had their first review. It is exactly the "introduction cards for the
words in box 1 that haven't been seen yet" behavior the user hoped was happening.

Acquisition starts by day confirm zero automatic intake since the injection:

| Day | Starts | Detail |
|-----|-------|--------|
| 07-08 | 8 | 3 leech_reintro, 2 book, 3 textbook_scan |
| 07-09 | 2 | 1 frequency_core, 1 collateral |
| 07-10 | 1 | snap |
| 07-13 | 1 | collateral |
| **07-15** | **205** | **202 bookifier + 3 textbook_scan, all kind='new'** |
| 07-16 → 07-20 | 0 | recovery budget = 0, gate holding |

The 205-in-one-day injection bypassed `DAILY_INTRO_CAP=30` **by design**: explicit user
adds via `/api/discover/add-batch` pass `enforce_daily_cap=False` (documented in
`routers/discover.py`). The system then correctly slammed the door on all automatic
intake: `recovery_status()` shows all three gates tripped — Box-1 actionable **217** (limit
5), Box-2 due **52** (limit 30), strict main-lane FSRS due **869** (limit 750),
`intro_budget_today=0`. Leech reintro admission also closed (Box-1 ≥ 20). All per the
2026-07-09/PR #208 design.

Remaining intro-card runway: 49 never-reviewed Box-1 words (47 bookifier). At the observed
~6–16 intro cards/day, roughly 4–8 more days of intro cards, all from the injections.

## Finding 2 — The injected cohort is landing as designed

Of the 202 bookifier words (5 days in): **35 already graduated** (15 known, 20 learning —
tiered fast graduation absorbing the already-familiar ones), 167 acquiring
(box 1/2/3 = 133/29/5). Cohort accuracy over 329 reviews: **65.3%** — genuinely new, hard
words. This cohort fully explains the overall accuracy dip to 73–76% on 07-17/18 (the days
its reviews peaked); overall accuracy recovered to 89% on 07-19.

Sentence supply is healthy — no stall risk: 224/226 non-inert Box-1 words have ≥1
reviewable sentence (2 without; 9 in generation backoff).

## Finding 3 — Dig-out trajectory: the only lever is daily volume

Due-backlog at session start (from `session_start.total_due_words`):

- Pre-injection: 1,219 (07-08) → 1,009 (07-14) = **net −35/day** at ~250 reviews/day.
- Injection reset it to ~1,120–1,170; last 5 days averaged only ~150 reviews/day →
  **treading water** (1,015 on 07-15 → 1,162 on 07-20, light days 07-16/19/20).

FSRS inflow is modest and falling: only +25–40 newly-due/day over the next two weeks
(sparse reviewing pushed intervals out). Back-of-envelope: at ~200–250 word-credits/day
(≈45–55 sentence cards), net burndown ≈ 35–40/day → **~3 weeks of consistent daily
sessions** to return to a normal ~400-due baseline. Behind that, 289 `encountered` words
(202 textbook_scan) queue for promotion at the earned 0/8/30 budget once gates reopen.

## Recommendation — no algorithm changes now

Everything checked is behaving exactly as the freshly deployed (07-10, PR #208) recovery
machinery prescribes, and that change has pre-registered evaluation windows (delivery
balance at 4–5 active weeks; retention/safety at 8–10; leech verdicts after ≥50 resolved
episodes) — none has elapsed. Tuning on top of an in-flight experiment would blind those
evaluations.

Dials to watch (all already surfaced by the 07-15 stats panel):
1. **Burndown dot line** — should resume the ~−35/day slope on full-volume days; if it
   stays flat at ≥200 reviews/day, investigate.
2. **Bookifier cohort accuracy** — 65% is fine for week 1; should trend toward ~80% as
   Box 2/3 fills. If still <70% in 2 weeks, the leech engine will (correctly) start
   triaging the hardest ones.
3. **Cron "Found N to enrich"** — post-07-15 fix, should shrink pass over pass.

One observation logged, not actioned: `INTRO_NEW_CARDS_PER_SESSION=6` is a flat per-session
ceiling independent of session size, so short sessions can feel intro-dense (07-20: 6
intros vs 9 sentences). The 2026-04-27 tightening addressed rescue-card density but left
the new-card ceiling flat. Only worth revisiting if the density *feels* wrong during the
remaining ~1 week of injection intro cards — it is self-limiting once the 49 un-introed
words are through.
