---
name: project-missed-word-recovery
description: "Missed-word lane, acquiring reserve and the phrase follow-up trial went live 2026-11-20 (PRs #330, #331); readout due after 2026-12-04"
metadata:
  node_type: memory
  type: project
  originSessionId: aa2fb53e-2461-48da-a05f-ae11d6f144ad
  modified: 2026-11-20T09:18:02.991Z
---

Live since 2026-11-20: `missed_word_recovery_v1` (open misses get up to 30% of base
cards), `acquiring_reserved_v1` (30%), due-menu priority for missed words, retry queue v4,
and `fragment_followup_v1` (trial: a 2–3-word phrase card in place of a sentence card for
a missed word's scheduled follow-up; last required success stays a full sentence).

Start at `research/experiment-log.md`, entries "2026-11-20 (2): Phrase follow-ups replace
the retry-card trial" (running trial, readout rules: not before 2026-12-04, 50 words per
arm) and "2026-11-20: Missed-word recovery lane …" (baselines, lane rules, rollback).
Scripts: `research/missed-word-recovery-2026-11-20/`.

Stian's decisions on 2026-11-20: closing a miss on an older word is good value and
motivating; a hit on one word card is not enough evidence, a miss on one is a strong
signal for better hooks; fragments were his idea and he meant them in place of scheduled
sentences; asked to run only the most consequential trial for a quicker readout, so the
retry-card trial (`RETEST_AB_ACTIVE`) is off.

Not verified at session end: how the phrase card looks on the phone.
Related: [[project-generation-changes-nov-2026]].
