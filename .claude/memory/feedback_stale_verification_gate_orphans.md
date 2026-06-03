---
name: feedback_stale_verification_gate_orphans
description: "Bumping the verification cutoff orphans pre-cutoff sentences; warm-cache reverify can't see them — run a manual full sweep"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: b3e03692-76c2-44ce-9e29-e1d8b10ac09c
---

When `MAPPING_VERIFICATION_MIN_AT` (a.k.a. `MAPPING_VERIFICATION_HARDENED_AT`) in `backend/app/services/sentence_eligibility.py` is bumped to harden the mapping verifier, **every active sentence stamped before the new cutoff is instantly hidden from review** (the reviewability gate fail-closes on stale `mappings_verified_at`). These heal only slowly and partially: of the three reverify paths in `mapping_rescue.py`, the two scheduled ones — full sweep `reverify_all_active_sentences` (no-arg) and continuous `reverify_oldest_active_sentences` — both filter candidates through `reviewable_sentence_clauses()`, so they can NEVER see a gated sentence. Only the lazy rescue `rescue_sentences_for_lemmas` reaches pre-cutoff rows, and only for gap lemmas capped at `MAX_RESCUE_LEMMAS_PER_RUN=10` × `MAX_RESCUE_SENTENCES_PER_LEMMA=5` = 50/warm-pass. Stale sentences belonging to adequately-covered words are never reached → a large post-cutoff backlog persists until a manual targeted sweep re-stamps them.

**Why:** caught 2026-05-29 — the 2026-05-17 18:59 cutoff had left 838 of 1,961 active Arabic sentences (43%) gated for ~12 days, halving session coverage (213 of 473 due cohort words had no showable sentence). User reported "short sessions."

**How to apply:** after bumping the cutoff (or when sessions are short and `reviewable sentences` is far below active count), run a one-shot sweep that feeds the *stale* IDs explicitly to `reverify_all_active_sentences(sentence_ids=...)` — NOT the no-arg / `reverify_active_sentences.py` form, which only re-checks already-reviewable sentences. Select stale IDs via `is_active AND not_has_unmapped_words() AND mappings_verified_at < MIN_AT AND != 2000-01-01`. Passing/correctable sentences get re-stamped (un-gated); genuinely unfixable ones (correct lemma not in vocabulary) get positions NULL'd and stay hidden. ~20 min for ~830 sentences via free Claude CLI. The 2026-05-29 run: 833 attempted → 656 passed + 55 corrected (711 un-gated), 122 unfixable, 0 LLM failures. Back up the DB first. See [[feedback_check_prior_work_first]] and [[feedback_polyglot_local_db_stale]].
