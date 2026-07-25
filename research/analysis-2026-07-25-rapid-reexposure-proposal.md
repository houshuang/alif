# Rapid Re-exposure After Failure — Data Analysis + Design Proposal

**Date:** 2026-07-25
**Status:** Implemented 2026-07-25 (same day) — see the 2026-07-25 experiment-log entry for the pre-registration and final parameter values. §5–§7 below are the design as proposed; the implementation follows it with one addition (wrap-up endpoint also filters function words / proper names / open exact-surface episodes).
**Trigger:** User report — "introductions have gotten better, but I still get waffara far too frequently… after flipping cards and noting the ones I don't know, I might not spend enough time/attention to fix the missed words to my memory."

---

## 1. The waffara case study (prod data, 2026-07-25)

وَفَّرَ (lemma 4300, "to save/provide") turns out to be the perfect specimen — but not for the reason expected:

| Fact | Value |
|---|---|
| Introduced | 2026-07-15, via bookifier `/api/discover/add-batch` (straight to Box 1) |
| Intro card ever shown | **Never** (bookifier cohort intro backlog: 49 un-introed words draining at ≤6/session) |
| Primary card ever shown | **Never** — all 4 reviews are `credit_type='collateral'` |
| Review record | 4 reviews, 4 failures (0/4), all rating 1, all as scaffold in other words' sentences |
| Same-session re-exposure | Yes, once: 2026-07-18, failed at 13:22, re-seen 14:11 (49 min later, same session) — **failed again** |
| Sentence inventory | 1 target sentence, 6 sentences containing it |

Two lessons baked into one word:
1. **The failure loop is real**: the scheduler keeps surfacing the word collaterally, the user keeps failing it, and nothing between exposures helps consolidation.
2. **Mere re-exposure is not the treatment**: the one natural same-session re-exposure (49 min) failed too, because no re-teach/retrieval step intervened. The user's instinct — that the follow-up needs a deliberate "fixing" step — matches the data.

Waffara's root cause is partly upstream (add-batch words never got an intro card; that backlog is already draining per the 2026-07-20 checkup), but the general mechanism — *failed word gets no deliberate follow-up within the consolidation window* — applies to the whole struggling cohort (25 words with ≥3 failures in 30 days, most now suspended or lapsed).

## 2. Failure → re-exposure gap → downstream retention (120 days, 3,762 failures)

Method: for every rating-1 review, measure the gap to the lemma's next review (r1) and the outcome of the review after that (r2, rating ≥3 = success). r2 is the honest endpoint — r1 success at short gaps is inflated by working memory.

### All failures

| Gap to re-exposure | n | r1 success | **r2 success** |
|---|---|---|---|
| <10 min | 555 | 87.4% | 74.6% |
| 10 min–2 h | 326 | 73.3% | 77.6% |
| 2–24 h | 984 | 72.6% | **79.1%** |
| 1–3 d | 686 | 57.1% | 71.9% |
| >3 d | 970 | 49.3% | 67.6% |

### FSRS primary-credit failures only

| Gap | n | r1 success | r2 success |
|---|---|---|---|
| <10 min | 9 | 88.9% | 77.8% |
| 10 min–2 h | 29 | 89.7% | **85.7%** |
| 2–24 h | 159 | 78.0% | 75.6% |
| 1–3 d | 149 | 69.1% | 73.6% |
| >3 d | 230 | 50.4% | 73.3% |

### Readings

- **The <10 min bucket shows classic massed-practice inflation**: highest immediate success (87%), *no* downstream advantage (74.6% vs 79.1% for 2–24 h). Matches the 2026-02-10 H1 reintro finding ("3 words failed first reintro, succeeded 10 min later… FSRS stability remains <0.2d") and the FAST_GRAD_INTRO_GAP rationale.
- **The sweet spot is minutes-to-hours, not seconds**: 10 min–24 h buckets have the best r2. For FSRS primary failures, the 10 min–2 h bucket is the best of all (85.7% r2), though n=29 is small.
- **The current system almost never delivers this window for FSRS words**: only 38/576 primary failures (6.6%) were re-seen within 2 h. 241/3,762 failures were never re-seen at all in the window.
- **Caveat (observational, confounded)**: short-gap re-exposures today are mostly accidental collateral appearances; bucket membership correlates with word state (Box-1 words dominate short buckets). This motivates the experiment; it doesn't prove the effect. The randomized design in §5 is what settles it.

Session fragmentation confirms the user's report: of the last 70 sessions, 28 spanned >60 min and 5 spanned >6 h (max ~14.6 h). Any design must be **wall-clock-based and survive session breakage**.

## 3. Prior art — what constrains the design (Rule #14 sweep)

| Prior work | Constraint it imposes |
|---|---|
| **IDEAS.md "Within-Session Spacing for Failed Items" (2026-02-09)** — proposed, never implemented, never rejected | Re-show after 5–10 intervening items; **if a word fails twice in one session, drop it for the session** (anticipates the frustration failure mode) |
| **Acquisition scheduler already intends this** — Again w/ 0 correct → due +5 min; Hard → +10 min; FSRS Again → relearning step 10 min (py-fsrs default, FSRS-6 same-day w17–w19) | **This is an operational gap, not a policy gap.** The 5–10 min due dates are computed and stored today; nothing delivers them until the next `build_session()`. We are building delivery, not a new policy. |
| **H1 reintro result (2026-02-10)** + **FAST_GRAD_INTRO_GAP (2026-05-17)**: success <10 min after teach proves nothing; blocked from Tier-0/Box-advance | **Same-session re-test success must not advance a box, graduate, or count toward suspension-verdict evidence.** |
| **PR #207 (2026-07-09)**: reintro "Show again" button wrote FSRS credit → 62 zero-correct lemmas crossed into FSRS, 40 leeched | Re-exposure cards must not write misleading credit. Asymmetric credit (see §5) is the safe shape. |
| **PR #217 (2026-07-20)**: `REINTRO_SHOWN_COOLDOWN_HOURS=20` — user complained about the *same passive reintro cards repeating* (waffara got 6 in 3 days) | A rapid re-exposure feature must be sharply distinguished: **active retrieval in a new context, hard-capped**, not another passive re-teach loop. Cap: 1 re-test per lemma per session, drop after second failure. |
| **Exact-form pilot `__exact_surface_v1` (PR #208, live, unread out until ~Aug)** — triggers on **Hard/`was_confused`, non-acquisition**; reserves ≤1 already-due sentence/session | **Trigger populations must stay disjoint**: rapid re-exposure triggers on **rating 1 (Again) only**, and skips lemmas with an open exact-surface episode. Endpoint design mirrors its intention-to-treat shape. |
| **"No bare word cards in review" (Core Principle #1)** — but the **wrap-up quiz** (`POST /api/review/wrap-up` → front: word+translit, back: gloss/root/wazn/hook, Got-it/Missed → real review with `sentence_id: null, review_mode="quiz"`) is a live, sanctioned, *user-initiated* exception | The bare-quiz card the user is asking for **already exists and already round-trips through the review pipeline** — it's just opt-in and almost never used ("may not be reaching users effectively", scheduling-system.md). We extend its delivery, not invent a card type. |
| **Dynamic session sizing rejected (PR #22, 2026-05-22)** — low-accuracy sessions are *smaller* because the user self-quits | Don't grow bad sessions much. Cap total re-tests per session at 2. |
| **Sentence inventory**: ~36% of studied lemmas have ≤1 eligible sentence (04-16 snapshot proxy); waffara has 6 containing / 1 target | The "second sentence" variant hits an inventory wall for a third of words. The quiz card works for 100%. Sentence variant is Phase 3, not the core. |

## 4. Architecture facts that shape the mechanism

- The session is a **fixed pre-built list fetched once**; `submitSentenceReview` goes through the AsyncStorage sync queue and returns an empty stub — **the client never sees the server's review response**, and prefetched next sessions were built before current reviews were submitted. ⇒ A server-push design is fighting the architecture; **the client must own the timer and the trigger**.
- Reintro / grammar / wrap-up cards render as **out-of-band phases** that don't consume `cardIndex` and aren't in `totalCards`. ⇒ Delivering the re-test as a phase (like wrap-up) **avoids the entire splice risk** (`totalCards`, `cardReviewIds[]`, `cardSnapshots[]`, auto-skip walk, and the `results.total >= totalCards` end condition all assume fixed length).
- The client already has the wall-clock hook (`lastReviewedAt` ref + the 15-min staleness check in `advanceAfterSubmit`) and already knows which lemmas failed (`wordOutcomes`).
- `POST /api/review/wrap-up` already accepts `missed_lemma_ids` and returns fully-enriched recall cards (gloss, root family, wazn, memory hook). Calling it mid-session with 1–2 lemma IDs is a cheap read; card content doesn't depend on post-review state, so the sync-queue pre-review-state problem doesn't bite.
- Broken sessions need no special handling: the 5-min/10-min due dates already stored mean the **next session build naturally prioritizes the failed word** (Box-1 due + LAPSED_BOOST). The mid-session mechanism only needs to cover the continuous case; the session-end and next-session paths are the fallback tiers.

## 5. Proposal: three delivery tiers for the already-intended 5–10 min re-test

**One policy** ("a failed word gets one active retrieval attempt a few minutes later"), delivered by whichever tier the user's actual behavior reaches first:

### Tier 1 — Checkpoint re-test (mid-session, the core novelty)
- On a **rating-1** failure (primary or collateral; acquisition or FSRS), the client adds `{lemma_id, failedAt}` to a local re-test list.
- In `advanceAfterSubmit`, when an entry is **≥4 min old AND ≥3 cards have intervened**, inject a **checkpoint phase** before the next slot: the existing wrap-up quiz card for that lemma (word + transliteration front → attempt retrieval → flip → full enrichment back → Got it / Missed).
- Content fetch: mid-session call to the existing wrap-up endpoint (offline → skip silently; the word stays covered by Tier 2/3).
- **Expiry**: entries older than 20 min are dropped (the word is due anyway; next build handles it). Entries unmatured at session end flow into Tier 2.
- **Caps** (the PR #217 lesson): max 1 re-test per lemma per session; max 2 checkpoint cards per session; a lemma that fails its re-test is dropped for the session (the user's own 2026-02-09 IDEAS bullet).
- **Exclusions**: function words, proper names, lemmas with an open `__exact_surface_v1` episode, Hard-only misses (`was_confused` without rating 1 — that's the exact-form pilot's population).

### Tier 2 — Auto wrap-up (session end; cheapest, ship first)
- When the completion screen mounts and any failed words exist, **auto-run the wrap-up quiz for the missed lemmas** instead of hiding it behind the action-menu button. 1–4 cards, ~20 s. This alone converts every completed session into a minutes-scale re-test and requires ~zero new machinery. (scheduling-system.md already flags wrap-up under-delivery, citing Nakata 2017's ~3 retrievals/session sweet spot.)

### Tier 3 — Next-build priority (already exists)
- Broken/abandoned sessions: the stored +5/+10 min due dates and LAPSED/Box-1 priority already front-load the failed word into the next session. No change; this tier is the safety net that makes fragmentation a non-problem.

### Credit rules (the load-bearing safety design)
- The re-test submits through the existing quiz path (`review_mode="quiz"`, `sentence_id: null`) with a distinguishing context (`checkpoint` vs `wrapup`).
- **Asymmetric consequences**:
  - **Missed** → counts normally (Box-1 reset / relearning re-entry — it is genuine evidence).
  - **Got it** → logs the review and clears the short-term due date, but **must not** advance an acquisition box, trigger Tier-0/1/2 graduation, or count as suspension-verdict evidence — same mechanism as `FAST_GRAD_INTRO_GAP`, extended to key off the *failure* timestamp rather than the intro timestamp. FSRS words: a Good at 10 min just steps through the relearning step (standard FSRS-6 same-day behavior, w17–w19 discount applies — this is safe by construction).
- This is exactly the shape that avoids both prior incidents: no phantom Good credit (PR #207), no fast-graduation laundering (2026-05-17).

## 6. Experiment design (pre-register before implementing)

- **Unit of randomization**: failure event, deterministic 50/50 hash of `(session_id, lemma_id, first_failure_ts)` — same pattern as the exact-surface pilot, stable across offline retries. Treatment = eligible for Tier 1+2 delivery; control = today's behavior (Tier 3 only).
- **Primary endpoint (intention-to-treat)**: success rate of the lemma's **first review ≥12 h after the failure**, any form, any credit type — deliberately *not* the re-test itself and *not* same-session success (the H1 lesson).
- **Secondary**: time-to-next-success; leech-suspension rate of treated vs control failures within 30 days; user-felt repetition (subjective check-in, given PR #217 history).
- **Guardrail metric**: session completion rate and cards-per-session must not drop (PR #22 finding: bad sessions end early — if re-tests push users to quit, that shows up here).
- **Power sanity check (back-of-envelope, per the calculation-before-simulation preference)**: ~31 failures/day observed (3,762/120 d). Detecting a 10 pp lift (68%→78%) at α=0.05, power 0.8 needs ~2×330 events ≈ **3 weeks of data**. A 5 pp lift needs ~12 weeks — so pre-register the readout at 4 weeks for a coarse read, 8+ for a fine one.
- **Instrumentation**: `log_interaction` events `checkpoint_retest_{scheduled, shown, expired, outcome}` + arm tag; `card_shown` already covers delivery reconstruction; `parent_card_type` split (2026-05-13) separates wrap-up reviews in analysis.

## 7. Cost / risk profile

- **No LLM calls, no TTS, no session-build changes** (`build_session()` untouched — <1 s invariant holds), **no new tables** (arm is derivable from the hash; episode state is client-local + interaction logs), no new card component (reuses wrap-up rendering).
- Main integration risk is frontend-phase plumbing in `index.tsx` (checkpoint phase alongside the existing reintro/grammar/wrapup phases) and the backend graduation guard.
- Rollback: a single client flag disables Tier 1/2; Tier 3 is the status quo.

## 8. Deliberately out of scope (for now)

- **Second-sentence re-test** (show the failed word in a *new* sentence instead of a quiz card): better aligned with the sentences-always principle, but hits the ~36% inventory wall, the near-duplicate Jaccard veto, comprehensibility gates, and the sync-queue pre-review-state problem. Worth a Phase-3 arm later — cheapest version is **reordering**: when the session already contains a second sentence for the failed lemma (acquisition_repeat often provides one), delay it to the ≥4-min mark instead of its planned position. Zero new inventory needed.
- **Server-computed re-test scheduling**: fights the fetch-once + sync-queue architecture for no benefit at n=1 scale.
- **Waffara-class upstream fix** (add-batch intro starvation) is a separate, already-diagnosed issue draining under PR #217's rescue slots — rapid re-exposure complements it but doesn't replace intro cards.

## 9. Data provenance

Read-only prod queries 2026-07-25 (`review_log`, `user_lemma_knowledge`, `sentences`, 120-day window). Analysis script preserved at the session scratchpad (`failure_gap_analysis.py`); rerun against prod to refresh. Architecture and prior-art file references verified against the working tree at commit `cde098da`.
