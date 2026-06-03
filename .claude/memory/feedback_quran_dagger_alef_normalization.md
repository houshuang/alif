---
name: feedback_quran_dagger_alef_normalization
description: Malformed Arabic Quran lemma / missing medial alif → suspect dagger-alef (U+0670) strip-before-convert collapse
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 836a19ef-eadf-48b4-82b8-1384394803e8
---

A malformed Quran-sourced lemma (missing a medial long vowel, e.g. خَلِدُونَ instead of خَالِدُونَ; transliteration/etymology citing a *different* word like Ibn Khaldūn) is almost always the **dagger-alef (ٰ, U+0670) strip-before-convert** bug.

**Why:** Uthmani orthography writes the long ā in many words with a dagger alef, which lives in the Unicode *diacritic* range. A `strip_diacritics()` that runs before promoting it **deletes the long vowel**, collapsing the word onto a bare consonant skeleton that can be a different real word — خَٰلِدُونَ ("abiding forever") → خلدون = the proper name Khaldūn. CAMeL then lemmatizes the collapsed skeleton as `noun_prop`, and enrichment chimerizes the lemma.

**How to apply:**
- The fix is normalization *order*, not a detector: `normalize_quranic_to_msa()` (converts ٰ→ا) MUST run before `strip_diacritics()`. It's wired into `normalize_arabic()`; `quran_service` now uses `_quran_bare()`. Convert the dagger only on the **content/surface** side — keep the function-word check + CAMeL's own lex on the raw-stripped form, because silent-alef demonstratives where MSA *omits* the alef (هذا/ذلك/ولكن, and CAMeL's lex لٰكِنَّ) must NOT gain an alef.
- Audit existing damage with `backend/scripts/audit_quran_dagger_alef.py` (source-grounded: re-normalizes the Uthmani surfaces in `QuranicVerseWord.surface_form`). Repair no-collision cases with `fix_quran_dagger_alef_lemmas.py`; duplicates need canonical merges, clitic forms need decomposition.
- Generalizable auto-detectors for this class are NOISE (measured 2026-06-02: "headword bare missing a mater its forms share" = 1,139 false positives; "CAMeL bare is noun_prop" = 227, mostly legit names). Don't add one as a gate.

Caught 2026-06-02 (PR #186): lemma #2887 خَلِدُونَ→خَالِد. Related: [[feedback_camel_mle_fem_ta_marbuta_misread]], [[feedback_polyglot_latin_homograph_override]].
