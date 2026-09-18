# Latin vs Greek: alternate-days or a-little-of-both-daily?

**Date:** 2026-05-29
**Question:** Arabic stays the primary focus. With only a small daily budget left for
the Polyglot languages, is it better to **alternate** (all Latin one day, all Greek
the next) or do **a little of both every day**?
**Method:** Budget-matched simulation of Alif/Polyglot's actual scheduling engine.
Script: `research/sim_alt_vs_both_2026-05-29.py` (8 seeds, 90 days).

## TL;DR

**Do a little of both, every day.** If you only have one short sitting per day — the
realistic "Arabic is the focus" case — daily contact wins by a wide margin and
*cannot be beaten by alternating* across any plausible amount of Latin↔Greek
confusion. The only regime where alternating competes is "two sittings per day **and**
the two languages genuinely confuse you," which is unlikely because they use
**different scripts**.

## Why timing dominates

The acquisition engine uses Leitner boxes of **4h → 1d → 3d** before a word graduates
to FSRS. Boxes 1 and 2 are *sub-day to one-day* intervals. Every-other-day study
physically cannot review a box-1 or box-2 word near its due time — the card sits
~40–48h late, far down the forgetting curve, and a failed recall **resets it to box 1**.
That is a leaky bucket: you keep re-learning the same words. Daily contact lets those
boxes fire near their windows, so words actually graduate.

The simulation makes this visible in the *still-acquiring* (churning, never-graduated)
count: alternating consistently leaves **2–4× more words stuck in acquisition**.

Once words reach FSRS (intervals of days-to-weeks), being a day late barely matters —
so the policy choice only bites during the acquisition phase, which is exactly where a
new Latin/Greek learner lives.

## Results (known-and-retained words after 90 days, budget-matched)

Both policies spend the *same* cards per language; only the timing differs (spend
parity asserted in the sim).

### One sitting per day (the realistic time-limited case)

| Daily budget | both/day | alternate | BOTH advantage (no interference) |
|---|---|---|---|
| 6 cards  | 3+3 | 8 every other day-equiv | **+112%** (53.8 vs 25.4) |
| 12 cards | 6+6 | — | **+86%** (107.8 vs 58.0) |
| 20 cards | 10+10 | — | **+81%** (182.4 vs 100.9) |
| 32 cards | 16+16 | — | **+95%** (294.1 vs 150.9) |

### Two sittings per day

| Daily budget | BOTH advantage (no interference) |
|---|---|
| 6  | +13% |
| 12 | +19% |
| 20 | +17% |
| 32 | +16% |

## The one knob that can flip it: same-day interference

The only force pulling *toward* alternating is interference — studying two similar
languages the same day causing confusion. The break-even (budget 12/day):

| Sittings/day | Same-day interference needed for ALTERNATE to win |
|---|---|
| **1** | none in tested range — BOTH wins even at a 10% recall penalty |
| **2** | ~3% (between 0.98 and 0.96) |

For **Latin (Latin script) + Greek (Greek alphabet)**, real interference is small:
different scripts are a strong discriminative retrieval cue. Cross-linguistic
interference is worst for typologically close, *same-script*, cognate-dense pairs
(e.g. Spanish/Italian). Latin and Greek share grammatical *structure* (cases, genders)
but their surface forms — and especially their scripts — diverge sharply. So the
realistic interference is likely 0–3%, keeping the recommendation on "both."

## Caveats / what the model does not capture

- **Tier-0 instant graduation** (first correct review → graduate) amplifies the
  daily-contact edge, because daily contact lands that first review sooner and at
  higher retrievability. Removing it would make the leaky-bucket penalty for
  alternating *larger*, not smaller — so the conclusion is robust to it.
- **Human context-switch cost** of doing two languages in one sitting is not modeled.
  In practice the switch is cheap when scripts differ.
- **Adherence beats optimization.** The biggest real-world lever is simply showing up.
  If "today is Greek day" is the rule that keeps you consistent, that consistency is
  worth more than the scheduling delta. But there is no adherence reason to prefer
  alternating per se.

## Practical recommendation

1. **Touch both Latin and Greek every day**, even if it's tiny (3 + 3 cards still
   beats 8-every-other-day by 2×).
2. With a small budget, **clear due reviews first in both languages**, then introduce
   only a few new words — don't let intros outrun your daily review capacity, or you
   recreate a leaky bucket within a single language.
3. Keep Arabic primary; this concerns only the leftover Polyglot budget.
