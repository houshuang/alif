---
name: feedback_display_headword_citation_form
description: "lemma_ar display headword must be citation form (no al-, singular); scans leak surface forms — guard in finalize_new_lemmas"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: a00d775c-136f-4d9d-85fa-a7b48baa266a
---

Intro/reintro cards render `lemma_ar` (the diacritized display field). It must be the **dictionary citation form**: no definite article ال, singular, uninflected. `lemma_ar_bare` is already article-free + singular, so the two must agree. Scans (`textbook_scan`, OCR, book import) captured in-text surface forms — الْكَهْف instead of كَهْف, آثَار instead of أَثَر, plural هَوَايَات for هِوَايَة — so the headword diverged from the bare and the user saw "the cave" / a plural on the card (caught 2026-06-13).

**Why:** the `al-` is only in the DISPLAY field, never in the scheduling/matching key (`lemma_ar_bare` drives clitic stripping, FSRS keying, lookup, dedup). So it's a cosmetic headword bug, invisible to the review engine but visible to the user. This is *why* the bare/display split is load-bearing — see [[feedback_text_to_lemma_hardened_path]].

**How to apply:** the fix lives in `finalize_new_lemmas` (inside `run_quality_gates`, which EVERY import path must call — don't patch individual importers, "fix one site miss the clones"). `strip_display_definite_article()` strips a leading ال from `lemma_ar` when the bare lacks it (incl. sun-letter shadda السَّمَاوِيّ→سَمَاوِيّ) and only when the result still normalizes back to the stored bare (leaves الله/الذي alone). Plural/conjugated headwords can't be auto-corrected (need re-vocalization) → the gate logs a warning for review. Test: `backend/tests/test_headword_definite_article.py`. Docs: design-principles.md § "Display headword = citation form". When a stored bare is itself wrong (e.g. 2504 خصوصي for the noun خُصُوصِيَّة), fix bare too after a collision check — don't let the safety net silently skip it.
