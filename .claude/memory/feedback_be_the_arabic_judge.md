---
name: feedback-be-the-arabic-judge
description: "Stian does not speak Arabic; never ask him to rule on glosses, senses or lexical quality — decide with model judges, dictionaries and measurement, automatically"
metadata:
  node_type: memory
  type: feedback
  originSessionId: 2da4fada-f0f3-48c0-b683-65041feaca08
  modified: 2026-11-26T11:12:14.301Z
---

Never put Arabic-quality decisions to Stian (is this gloss right, should this widening be
written, which sense is meant). Decide them: independent model judges, dictionary evidence
(Wiktionary index in `data/wiktionary_index.json.gz`), measured against labelled sets.
Prefer stricter automatic checks over dropping good work or asking.

**Why:** 2026-11-26, after I reverted 110 gloss widenings (78 good) and asked him how widenings
should be handled: "i'm unhappy about dropping 78 good widenings. this all needs to happen
automatically, with stricter tests. it's useless to ask me - i don't speak arabic, i'm not an
expert judge, you have to be."

**How to apply:** when a fix risks data quality, build the gate and the measurement, then apply
automatically; when undoing harm, keep the good part (re-run through the gate) rather than
reverting wholesale. Product and scope decisions still go to him. Related: [[project-snap-word-sheet]].
