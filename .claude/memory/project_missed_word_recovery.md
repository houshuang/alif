---
name: project-missed-word-recovery
description: "Missed-word lane and acquiring reserve live since 2026-11-20; phrase follow-up trial ended early 2026-11-25, phrases now for every miss"
metadata:
  node_type: memory
  type: project
  originSessionId: a493c62b-afe8-4528-b87c-a6547f17c075
  modified: 2026-11-25T06:31:34.198Z
---

Live since 2026-11-20: `missed_word_recovery_v1` (open misses get up to 30% of base
cards), `acquiring_reserved_v1` (30%), due-menu priority for missed words, retry queue v4.

`fragment_followup_v1` (a 2–3-word phrase card in place of a sentence card for a missed
word's follow-up) was a randomized trial from 2026-11-20 to 2026-11-25. Stian ended it
early on 2026-11-25 (PR #348, `FOLLOWUP_AB_ACTIVE = False`): phrases looked at least as
good (33/39 against 18/23 at the next full sentence), he finds them more enjoyable, and the
open trial was blocking other experiments on missed words. Every miss now gets phrases;
the last success that closes a miss is still a full sentence. The check is observational
from here: share correct at the closing sentence, reference 85%.

Start at `research/experiment-log.md`, entry "2026-11-25 (2)" and
`research/analysis-2026-11-25-sentence-review-check.md`. Lane readout items of the
"2026-11-20" entries (suspensions, return times) were read on 2026-11-25 in that analysis.

Stian's decisions on 2026-11-20: closing a miss on an older word is good value and
motivating; a hit on one word card is not enough evidence, a miss on one is a strong
signal for better hooks; fragments were his idea and he meant them in place of scheduled
sentences.

Decided against on 2026-11-25 (my calculation, he had asked about it): restoring part of
the old interval after a closed miss. History shows 91% recall at 3–10 days and 75% at
30–90 days after a miss plus two correct reviews, so the short FSRS interval fits.
Refitting FSRS weights was also advised against (April and June attempts, 18 Nov check).
Also live since 2026-11-25 (PR #349): red-marked words can be named as mixed up (was
yellow-only), and the trial `verb_phrase_forms_v1` (half of the verbs, by lemma hash, get
phrases that must include present-tense forms). Readout at 50 closed verb misses per arm,
roughly two months; rules in experiment-log entry "2026-11-25 (4)". Stian asked for both
after the finding that verbs are missed 32–39% against 19–24% for nouns.

Related: [[project-generation-changes-nov-2026]].
