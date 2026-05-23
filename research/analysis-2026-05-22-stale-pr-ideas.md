# Stale PR / Idea Triage — 2026-05-22

Cleanup pass that drove open branches and PRs to zero, plus a data-driven
re-evaluation of five abandoned 2026-03-21 experiment PRs. Goal: every open
branch/PR either merged or consciously discarded.

## Repository hygiene done this pass
- Removed 6 stale worktrees; deleted 38 already-merged local branches.
- Closed obsolete **PR #83** ("Add Polyglot") — polyglot + its bundled Arabic
  fixes had all landed on main via later PRs; merging would have reverted ~25K
  lines. Deleted its branch.
- Deleted **29 already-merged remote branches** (3 ancestor-merged + 26
  squash-merged via merged PRs) and the stale local `sh/textbook-imports-new-words-merge`
  (its "imports → encountered" idea already shipped via `book_import_service.py`).
- End state: **0 open PRs**, `origin/main` the only remote branch.

## The five 2026-03-21 experiment PRs (#21–#25)

All were created the same day, last touched 2026-03-30, then abandoned for ~2
months. All were `CONFLICTING` against the rewritten `sentence_selector.py`. The
user's instruction: don't merge the stale code — investigate whether each *idea*
is still worth pursuing fresh, using dev logs + production data (44,741 reviews,
2,071 known words, snapshot 2026-05-22).

| PR | Idea | Code/log finding | Production data | Verdict |
|----|------|------------------|-----------------|---------|
| #22 | Dynamic session sizing (10→14→18 by accuracy) | Unbuilt; accuracy already drives intro inflow + backlog caps | Sessions already **median 11 / mean 12 sentence-reviews** (exceed base 10); 60% reach ≥10; size does **not** rise with accuracy bucket; low-acc sessions are *smaller* (self-quit) | **Drop** |
| #23 | Response-time / fluency signal (boost slow words) | Built once on the branch, never merged; `response_ms` captured but unused except anti-cheat | **Hypothesis falsified**: next-review lapse after slow-correct = **10.5%** vs fast-correct **12.7%** (backwards). 17% coverage; reading median 25s = whole-sentence noise, not word recognition | **Drop** |
| #24 | Rasm confusable-pair exclusion in session build | Display-only confusion help shipped; session-builder exclusion unbuilt; literature mixed (interleaving may help) | Directionally real but tiny: same-rasm co-occur was_confused **2.71%** vs **1.75%** clean — only **9 confused reviews** in the affected cohort | **Drop (park)** |
| #25 | Familiar-encountered gate (encountered≥8 counts as known) | Looked strongest on code-reading (real documented dead-zone, plumbing precedent) | **Killed by data**: only **2** encountered words have ≥8 encounters; recomputing the gate, **exactly 1** sentence becomes newly eligible. The 2026-03-18 collateral auto-intro flow already drains encountered words (only 85 exist) | **Drop** |
| #21 | Mnemonic regeneration for stuck words | ~80% already shipped (premium regen on lapse + box demotion); leech engine threshold nearly matches | Real cohort exists: **77 stuck words still carry a hook**; ~64 words re-leech 3+ times | **Reframe → hook quality gate** |

### Key lesson
Real data **inverted** the code-level read. On code-reading, #25 looked like the
strongest candidate; production data showed its dead-zone is empty because the
March collateral-introduction flow already solved the problem it targeted. A
2-month-old idea can be correct-in-March and obsolete-now. (See CLAUDE.md
Critical Rule #14 — check prior work, and here, check current data.)

## #21 reframed — memory-hook quality gate

The user's steer: most stored mnemonics are weak; good ones are near-impossible
for many (esp. abstract) words; some are genuinely great. Don't burn tokens
chasing the impossible — surface only the good ones, skip the rest.

Findings on the current system (`backend/app/services/memory_hooks.py`):
- A generation gate **exists** (`hook_quality_reason`: `sound_match`/`interaction`/
  `extraction` all ≥4, or a direct borrowing) — but it trusts the LLM's **own
  self-evaluation** scores, which are lenient (the SMART/Balepur caveat the dev
  logs already flagged).
- `prepare_hooks_for_storage()` **strips the scores before saving** (1 of 2,078
  hooks retains them) — so there is no persisted quality signal to audit or gate
  on after the fact.
- Quality is **bimodal on concreteness**: concrete nouns get good hooks
  (سفينة "SUB-FIN-A", سرطان "SERRATED claw"); abstract words get broken ones —
  e.g. توب *repentance* → "squeeze a TUBE of toothpaste on your keyboard" (the
  meaning is entirely absent). The 77 stuck-with-hook words are dominated by
  abstractions.

Direction (in progress): an **independent** critic (separate judge, not
self-eval) to score each hook → **persist** the score → a **display gate** that
shows a hook only above the bar and stops retrying hopeless words. The bar is
being calibrated against the user's own good/bad ratings
(`research/eval-hook-quality-2026-05-22.html`) before any bulk run.

## Data probe scripts (read-only, run on prod)
- `pr_ideas_data.py` — the five-idea production probe (headroom, lapse
  correlation, session sizes, rasm co-occurrence).
- `hook_quality.py` — hook coverage/structure + sampled mnemonics.
- `prep_calib.py` — sampled 25 random + 25 stuck hooks for calibration.
