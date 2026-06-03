---
name: feedback_text_to_lemma_hardened_path
description: "Any text→lemma mapping must reuse the production-hardened lookup, not hand-rolled normalization/function-word checks"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c5f68377-bb2e-4066-a646-37e55d06c206
---

For **any** task that maps Arabic text → Alif lemmas (reading-readiness scans, external frequency lists, the QAC/Quran work, importing book/corpus text, vocab-coverage analysis), use the **production-hardened path** — do NOT hand-roll tokenization, normalization, function-word detection, or proper-name detection.

The canonical path:
- `build_comprehensive_lemma_lookup(db)` + `lookup_lemma(bare, lookup, original_bare=…)` — clitic stripping + CAMeL disambiguation + collision handling, all in one.
- Classify each token by the **RESOLVED lemma's** production attributes: `_is_function_word(normalize_alef(strip_diacritics(lemma_ar_bare)))` and `word_category in {proper_name, onomatopoeia}` — NOT by a surface-only check or a CAMeL POS guess.

**Reference implementation: `backend/scripts/reading_readiness.py`** (`analyze()`). Reuse/extend it; don't reimplement the loop.

**Why (the 2026-06-03 flail this came from):** I surface-checked function words, which misses clitic-attached forms (بعضهم → lemma بعض) → بعض leaked into the "gap" list and inflated coverage; and I guessed proper names from CAMeL `noun_prop` (imperfect) instead of the authoritative `word_category`. The hardened signals were right there. **How to apply:** before writing a new text→lemma scan, check whether `reading_readiness.analyze()` or `lookup_lemma` already does it. Justified divergence only for genuinely source-specific normalization (e.g. `quran_frequency.normalize_qac_lemma` for the QAC maddah caret) — and that still calls the shared `normalize_arabic`/`lookup_lemma` underneath. See [[project_quran_frequency_track]], [[feedback_quran_dagger_alef_normalization]].
