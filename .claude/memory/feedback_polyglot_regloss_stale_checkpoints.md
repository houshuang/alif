---
name: feedback_polyglot_regloss_stale_checkpoints
description: "Polyglot regloss/runon repair apply globs ALL checkpoint shards; stale old-run shards silently override a fresh run's verdicts"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 3f9fbc83-71bf-4395-94a5-06c5e9e097db
---

`scripts/regloss_lemmas.py apply` (and `repair_runon_glosses.py apply`) replay
**every** checkpoint matching `lemma_<tool>_<lang>*.jsonl` and merge them with
`records[lemma_id]=row` — last file read wins. Files sort with the unsuffixed
fresh file (`lemma_regloss_la.jsonl`) BEFORE old sharded ones
(`lemma_regloss_la_s0of4.jsonl`), so a **prior run's shards override the new
run's verdicts** for any shared lemma_id → the new reglosses silently no-op.

Caught 2026-06-01: a fresh studied-Latin regloss applied only 2 of 10 because
the 2026-05-26 `_s?of4` shards (status `ok` for those ids) overwrote the new
`regloss` verdicts. Fix: `mv` old shards to `data/_archived_checkpoints/` before
`apply`, then re-run.

**Before any regloss/runon apply: `ls polyglot/data/lemma_*_<lang>*.jsonl` and
move aside checkpoints from earlier runs.** Neither tool is in cron (only
`repair_runon_glosses` audit+apply is, phase 6) so this only bites manual runs.

Related: [[feedback_polyglot_resplit_gotchas]] (polyglot `log_activity` signature
differs from Alif's — the regloss tool's own activity-log call fails for the
same reason; apply still commits).
