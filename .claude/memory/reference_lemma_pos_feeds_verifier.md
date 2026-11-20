---
name: reference-lemma-pos-feeds-verifier
description: Rejected sentences at the mapping check are usually wrong lemma metadata (pos, gloss, headword), not missing vocabulary; where to look first
metadata:
  type: reference
---

When generated sentences are rejected at the mapping check, read
`data/logs/mapping_corrections_YYYY-MM-DD.jsonl` on prod and compare each rejected
correction with the lemma row before blaming the writer model or missing words.
The verifier is shown each lemma's `pos` and gloss; a wrong label rejects a correct mapping.

On 2026-11-18, 162 of 367 rejected corrections were wrong `pos` labels, 54 noun/adjective
disagreements, 65 truly missing lemmas. I had first reported "missing verbs" without
reading the log (my interpretation was wrong). Fix tools: `scripts/audit_lemma_pos.py`
(448 labels corrected 2026-11-19), `scripts/import_scaffold_lemmas.py` with
`ALLOW_HOMOGRAPH` for words the lookup resolves to a look-alike.
Related: [[feedback_understand_system_before_concluding]], [[feedback_dont_weaken_same_lemma_gate]].

Since 2026-11-19 (PR #327) `run_quality_gates()` gate 2b sets `pos` at import
(`app/services/lemma_pos_check.py`). Trap found the same day: code that branches on
`pos` must accept both `adj` and `adjective` — relabelling `adjective` → `adj` silently
dropped generated plural keys in `build_lemma_lookup` until the tuple was widened.
Before bulk-normalising any lemma field, grep for code that compares against the old spelling.
