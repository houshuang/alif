---
name: feedback_due_coverage_deficit_recurs
description: Why due-cohort words lose their only sentence and how to refill the deficit cheaply
metadata: 
  node_type: memory
  type: feedback
  originSessionId: b3e03692-76c2-44ce-9e29-e1d8b10ac09c
---

The "due word with no reviewable sentence" deficit is a recurring structural churn, not a one-off bug. Mechanism: cap-enforcement (`update_material.py` step 0) and `rotate_stale_sentences.py` retire sentences whose scaffold is all-known (no cross-training value). For an FSRS-`known` word, its sentences usually have all-known scaffold, so they get retired — and when that word next comes due it has zero active sentences. The cron's reactivation safety net `salvage_due_dense_inactive_sentences` does NOT catch these: it requires a retired sentence to cover **≥2 due words** (`len(due_hits) >= 2`), but a churned known word's retired sentence covers only 1 due word. So single-coverage known words fall into deficit until generation refills them.

**Why:** found 2026-05-29 — after [[feedback_stale_verification_gate_orphans]] cleared the stale-gate backlog (coverage 55%→78%), 99 due-cohort words still had no sentence; 83 of them had only *inactive* (retired) sentences, 87/99 were FSRS-`known`.

**How to apply (cheap refill):** compute the deficit = `(due ∩ focus_cohort) − {lemmas with a reviewable sentence}`, drop lemmas in generation backoff (`generation_backoff_until > now`), then run the verified pipeline `batch_generate_material(lemma_ids, count_per_word=2)` in chunks of ~12 under the `/tmp/alif-update-material.lock` flock + nohup. 2026-05-29 run: 99 ready → 81 sentences, 61 words covered, ~20 min, deficit 105→44. Remaining failures are genuinely hard words (no comprehensible sentence under vocab constraint) — leave to the cron. Don't worry about briefly exceeding the 2000 active-sentence cap; the next cron step-0 trims low-value sentences and protects due-word ones. For known words an all-known-scaffold sentence is a fine recall test, so pure reactivation also works when a currently-verified+mapped inactive sentence exists (only ~25/99 did here).
