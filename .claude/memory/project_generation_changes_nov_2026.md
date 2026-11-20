---
name: project-generation-changes-nov-2026
description: Six sentence-generation changes went live 18–19 Nov 2026 (dense cards, GPT writer, lemma metadata repair, strict-on-language judge); review-side effect not yet measured
metadata:
  type: project
---

Between 2026-11-18 evening and 2026-11-19 ~11:45 UTC, six generation-side changes went
live on prod (PRs #324–#329). All were measured only on dry runs of generation. The
learner-side check (due words per card, accuracy on dense cards, supply under the new
judge) was still open when the session ended; do it not before 2026-11-23.

Start at `research/experiment-log.md`, entry "2026-11-19 (5): Timeline and the one open
measurement": it has the timeline, baselines, the seven things to check and the confounds.

Stian's decisions on 2026-11-19: dense-sentence writer moved to GPT-6.1 Sol; judge is
"strict on language only" (odd scenes are not rejected, only reported as `contrived`).
Related: [[project_codex_switched_off]], [[reference_lemma_pos_feeds_verifier]].
