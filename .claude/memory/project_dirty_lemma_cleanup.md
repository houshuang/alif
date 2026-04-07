---
name: Dirty lemma cleanup (DONE)
description: Fixed OCR lemmas with al-prefix and ta-marbuta-as-ha — LLM-powered quality gate in import_quality.py + cleanup script
type: project
---

**Status: Fixed and deployed (2026-04-06)**

**Root cause:** OCR/book imports stored dirty `lemma_ar_bare` (e.g. المطحونه instead of مطحونة). Sentence generation always failed because the validator couldn't match LLM-generated correct Arabic back to the dirty bare form.

**Solution: LLM-in-the-loop, not rule-based.** Rule-based ال-stripping corrupts legitimate words (الله, الذي, التقى, والد). The LLM understands Arabic morphology and correctly classifies each case.

**Two fixes:**
1. **Prevention** (`import_quality.classify_lemmas()`): prompt now asks LLM to return `cleaned_arabic` when it detects OCR artifacts. `ocr_service.py` applies the cleaned form before creating Lemma records.
2. **One-off cleanup** (`scripts/cleanup_dirty_bare_forms.py`): LLM-powered cleanup of existing dirty lemmas. Run 2026-04-06: 41 cleaned, 10 marked as variants.

**Why not rule-based:** `clean_bare_form()` attempted automatic ال-stripping but had dangerous false positives: الله→لة, والد→د, التقى→تقى. Reverted to punctuation-only. The lesson: Arabic morphology is too complex for regex — always use LLM for disambiguation.
