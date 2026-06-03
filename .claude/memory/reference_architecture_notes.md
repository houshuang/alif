---
name: reference_architecture_notes
description: Cross-cutting architecture facts not in CLAUDE.md — incl. the CRITICAL SQLite naive-datetime pitfall
metadata:
  type: reference
---

Architecture facts that recur across sessions but don't have a natural home in CLAUDE.md or docs/.

- **🔴 CRITICAL — SQLite naive-datetime pitfall** (caused production crashes 3×): SQLite stores ALL datetimes as naive strings. Every datetime comparison in Python must either use naive datetimes (`datetime.utcnow()`) or convert DB values with `.replace(tzinfo=timezone.utc)` before comparing to aware datetimes. Affects: FSRS `reviewed_at` replay, `acquisition_next_due` comparison, FSRS due-date comparison. (Distinct from the write-lock discipline in CLAUDE.md §10.)
- **FSRS stability floor**: "known" with stability < 1.0 → "lapsed".
- **Interaction logger** is skipped when `TESTING=1` (set in `conftest.py`).
- **Frontend tests**: `cd frontend && npx jest --watchman=false` (Jest + ts-jest; mocks for AsyncStorage/expo-constants/netinfo).
- **Import dedup**: all 11 lemma-creation sites use clitic-aware dedup (`resolve_existing_lemma()`). The two former exceptions — `app/services/quran_service.py` and `scripts/backfill_function_word_lemmas.py` — were patched in lemma-decomposition audit Phase 2 step 1 (2026-04-24). See [[project_lemma_decomposition_audit]].
- **Hamza normalization**: preserve in storage, normalize at lookup/comparison time only.
- **Sentence generation**: rejected-word feedback + collocate auto-introduction on failure.
