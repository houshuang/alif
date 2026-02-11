# Variant Detection: Problem Analysis & Future Direction

**Date**: 2026-02-11
**Context**: After multiple rounds of hardening the ingestion pipeline (OCR fix, clitic-aware imports, hamza normalization, MLE disambiguator), we still have ~146 detected "variants" that can't be auto-applied because CAMeL Tools produces too many false positives. This document captures everything we've learned and evaluates the path forward.

---

## 1. The Core Problem

When Arabic text is imported (via OCR, Duolingo, Wiktionary, AVP), surface forms like كتابي (my book), وكتاب (and a book), الكتاب (the book), كتب (books) should all map to the single lemma كتاب rather than creating separate FSRS review cards. Currently ~300 of our ~700 active words are variant forms that leaked through as separate lemmas.

### What we want:
- **Clitics** (كتابي, وكتاب, بالكتاب): strip silently at import, map to base lemma, no separate tracking
- **Morphological variants** (كتاب/كتب, أسود/سوداء, يكتب/كتب): ONE lemma, but track per-form comprehension independently via `variant_stats_json`
- **Distinct lemmas** (غرفة/غرف, ملكة/ملك, سمك/سم): these happen to overlap when you strip taa marbuta or analyze with CAMeL, but they're genuinely different dictionary entries

### The difficulty:
Distinguishing category 2 from category 3 requires semantic understanding that rule-based approaches consistently fail at.

---

## 2. Current Architecture (What We Built)

### Layer 1: Rule-based clitic stripping (`sentence_validator.py`)
- `lookup_lemma()` / `resolve_existing_lemma()` — public API for import dedup
- `_strip_clitics()` strips proclitics (و، ف، ب، ل، ك، وال، بال، فال، لل، كال) and enclitics (ه، ها، هم، هن، هما، كم، كن، ك، نا، ني)
- Handles taa marbuta restoration (ة→ت before suffixes)
- `build_lemma_lookup()` indexes `forms_json` entries (plural, present, masdar, feminine, etc.)
- **What it catches well**: و-prefixed, ال-prefixed, multi-char possessive suffixes (كتابها, كتابهم), ب/ل prepositions
- **What it misses**: Single-char possessives (كتابي — ي not in ENCLITICS to avoid over-stripping عربي→عرب), verb conjugations, broken plurals, feminine forms

### Layer 2: CAMeL Tools morphological analysis (`morphology.py`)
- `analyze_word_camel()` returns all possible analyses with lex, root, pos, enc0
- `find_best_db_match()` iterates ALL analyses, picks first whose lex matches a known DB lemma (hamza-normalized)
- `get_best_lemma_mle()` uses MLE disambiguator for probability-weighted analysis
- **What it catches well**: Root extraction, POS tagging, basic lemmatization of well-formed diacritized words
- **What it gets wrong**: See section 3

### Layer 3: Variant detection (`variant_detection.py`)
- `detect_variants()` combines CAMeL analysis with DB matching + gloss overlap check
- `detect_definite_variants()` catches ال-prefixed duplicates
- Post-import: all 4 import paths (Duolingo, Wiktionary, AVP, OCR) run variant detection after creating lemmas
- **What it catches well**: Definite article duplicates, some possessive forms where gloss clearly overlaps
- **What it gets wrong**: See section 3

### Layer 4: Hamza normalization
- **Storage**: Hamza preserved in `lemma_ar_bare` (أكل stored as أكل, not اكل)
- **Lookup/comparison**: `normalize_alef()` applied at comparison time (أ→ا, إ→ا, آ→ا)
- **Rationale**: Standard Arabic NLP practice. AraToken paper confirms preserving Alif variants reduces language model loss. Root-final hamza is a real consonant (بدأ "begin" ≠ بدا "appear")
- **Risk**: At learner level, hamza minimal pairs are rare. Over-normalizing is safer than under-normalizing for our use case. But we chose the principled approach.

---

## 3. Where CAMeL Tools Fails (Detailed)

### 3a. Taa marbuta feminines misidentified as possessives
CAMeL analyzes words like غرفة (room) and finds an analysis where:
- lex = غرف (rooms/to scoop)
- enc0 = ة (interpreted as an enclitic)

This triggers `_has_enclitic()` → true, and if غرف exists in our DB, the gloss overlap check may pass (both relate to "room"). Result: غرفة gets marked as a variant of غرف.

**Affected words from production (all false positives)**:
- غرفة (room) → غرف (rooms) — different lemmas
- جامعة (university) → جامع (mosque) — different lemmas
- ملكة (queen) → ملك (angel/king) — different lemmas
- شاشة (screen) → شاش (muslin) — different lemmas
- سنة (year) → سن (age/tooth) — different lemmas
- كلمة (word) → كلم (to speak) — different lemmas
- نقطة (dot) → قط (to carve) — different lemmas
- كتابة (writing) → كتاب (book) — related but distinct lemmas
- مكتبة (library) → مكتب (office) — related but distinct lemmas

### 3b. Short-stem false positives
When CAMeL strips too aggressively:
- سمك (fish) → سم (poison) — completely different words
- بنك (bank) → بن (son) — completely different words (بنك is a loanword)
- مشى (to walk) → مش (to suck marrow) — completely different words
- هناك (there) → هنا (here) — related but distinct function words

### 3c. Nisba adjectives misidentified
- عربي (Arabic) → عرب (to translate) — the adjective is derived from the root but is a distinct lemma
- صيني (Chinese) → صين (China) — adjective from country name
- قطري (Qatari) → قطر (to drip) — nationality from country name
- مصري (Egyptian) → مصر (Egypt) — nationality from country name

### 3d. Legitimate variants CAMeL correctly identifies
Among the 146 detected, maybe ~50 are genuinely correct:
- تحبون (you all love) → أحب (to love) ✓
- يحبون (they love) → أحب (to love) ✓
- سعيدة (happy, f.) → سعيد (happy) ✓
- مريحة (comfortable, f.) → مريح (comfortable) ✓
- صديقة (friend, f.) → صديق (friend) ✓
- ممرضة (nurse, f.) → ممرض (nurse) ✓
- مدرستي (my teacher) → مدرس (teacher) ✓
- اصدقائي (my friends) → صديق (friend) ✓
- والمسجد (and the mosque) → مسجد (mosque) ✓

### 3e. Summary
Roughly: ~50 correct, ~96 false positives. A 34% true positive rate is unusable for automated merging.

---

## 4. The Hamza Dedup Problem

Our first version of Pass 2 in normalize_and_dedup.py used `resolve_existing_lemma()` which normalizes hamza. This produced false merges:

- سأل (to ask) → سال (to flow) — different roots entirely (س.أ.ل vs س.ي.ل)
- أمام (in front of) → إمام (leader, Imam) — different words
- أب (father) → آب (to return) — different words

**Solution applied**: Pass 2 now only does exact al-prefix dedup (الكتاب→كتاب), no hamza normalization. This is conservative but safe.

**Remaining question**: Should hamza normalization in `resolve_existing_lemma()` be removed for import scripts too? Currently new imports would match أحب against احب, which is correct (same word, different orthographic convention). But they'd also match سأل against سال, which is wrong. The risk is low because import data usually has consistent hamza spelling, but it's not zero.

---

## 5. The LLM Alternative

### What we'd replace
Replace `detect_variants()` (the CAMeL-based function in variant_detection.py) with an LLM call for ambiguous cases. Keep CAMeL for:
- Root extraction (works well)
- POS tagging (works well)
- Basic lemmatization of clearly-diacritized words (works well)
- MLE disambiguation for OCR base_lemma extraction (works well enough)

### Proposed LLM variant detection

```
Given these Arabic words from our vocabulary database, determine if the NEW word
is a morphological variant of any EXISTING word:

NEW WORD: غرفة (room)
EXISTING CANDIDATES (from CAMeL analysis matching):
1. غرف (rooms) — gloss: "rooms"

Rules:
- A taa marbuta feminine noun (Xة) is NOT a variant of its root verb or the
  masculine pattern — it's a separate dictionary entry (غرفة ≠ غرف, ملكة ≠ ملك)
- A possessive form (كتابي, كتابها) IS a variant of the base noun
- A verb conjugation (يكتبون, كتبت) IS a variant of the base verb
- A feminine adjective (سعيدة) IS a variant of the masculine (سعيد)
- A broken plural (كتب) IS a variant of the singular (كتاب)
- A nisba adjective (مصري) is NOT a variant of the country name (مصر)
- A loanword (بنك "bank") is NEVER a variant of a native Arabic word (بن "son")

Is غرفة a variant of غرف?
Answer: {is_variant: false, reason: "taa marbuta feminine noun is a separate dictionary entry"}
```

### Cost estimate
- ~50 tokens per call (prompt can be cached)
- Gemini Flash: ~$0.0001 per call
- 700 words × 1 call each = $0.07 total for full DB scan
- Future imports: 1 call per new word = negligible

### Latency estimate
- Gemini Flash: ~200-500ms per call
- Could batch 10-20 words per call to reduce round trips
- Import scripts are already slow (network downloads), so +5s for LLM calls is fine

### Accuracy estimate
Based on our experience with Gemini for sentence generation, grammar tagging, and OCR:
- Should handle taa marbuta feminines correctly (semantic understanding)
- Should handle loanwords correctly (knows بنك is "bank")
- Should handle nisba adjectives correctly (knows مصري is "Egyptian")
- Might occasionally hallucinate on rare/archaic words
- Can be prompted with our specific never-merge list for known edge cases

### Implementation sketch
1. Keep `detect_variants()` but add a confidence field
2. For each detected variant where CAMeL is unsure (taa marbuta, short stem, nisba):
   - Call LLM with the candidate pair + glosses + context
   - LLM returns {is_variant: bool, variant_type: str, confidence: float}
3. Auto-apply high-confidence LLM verdicts (>0.9)
4. Flag low-confidence for manual review
5. Cache LLM decisions in a lookup table to avoid re-querying

### Alternative: LLM-only approach
Skip CAMeL variant detection entirely. For each new word at import time:
1. Strip clitics (rule-based, keep this — it's fast and reliable)
2. If no direct match, ask LLM: "Is [new word] a form of any of these existing lemmas: [top 20 by edit distance]?"
3. LLM returns match or "new lemma"

This is simpler but more expensive (every new word needs an LLM call, not just ambiguous ones).

---

## 6. What's Working Well (Don't Change)

1. **Clitic stripping** in `_strip_clitics()` — fast, deterministic, no false positives for the clitics it handles
2. **`build_lemma_lookup()` with `forms_json` indexing** — catches known plurals, feminines, etc. at O(1) lookup time
3. **Al-prefix dedup** — simple and always correct
4. **`variant_stats_json` tracking** — already tracks per-surface-form comprehension during review, exactly what the user wanted
5. **Post-import variant detection pattern** — all 4 import paths run detection, even if the detection itself needs improvement
6. **Hamza normalization strategy** — preserve in storage, normalize at lookup

---

## 7. What Needs Fixing

### Immediate (before next import)
1. **Single-char possessive ي**: Currently not in ENCLITICS because it over-strips (عربي→عرب). Could add a minimum-stem-length rule: only strip ي if remaining stem ≥ 3 chars AND matches a known lemma. This would catch كتابي→كتاب without false-positive on عربي.

2. **`detect_variants()` false positive rate**: The 34% true positive rate means it can ONLY be used in dry-run/manual-review mode, never auto-applied. Either fix the logic or replace with LLM.

### Medium-term
3. **LLM-assisted variant detection**: Replace CAMeL-based `detect_variants()` for the hard cases. Keep rule-based for easy cases (al-prefix, multi-char possessives).

4. **forms_json enrichment at import time**: When a new word is identified as a variant (by any method), add it to the base lemma's `forms_json` so future lookups catch it without needing re-analysis.

### Long-term
5. **Sentence-level MLE disambiguation**: Currently we use MLE per-word. CAMeL supports full-sentence disambiguation which would be more accurate for words like سمك (fish in "I ate fish" vs poison in "the poison spread").

6. **User-driven variant linking**: In the app, let the user tap two words and say "these are the same word". This would be the most accurate method and would generate training data for improving automated detection.

---

## 8. Production Data Snapshot (2026-02-11)

After all cleanups:
- **Total lemmas**: ~1200 (includes wiktionary reference entries)
- **Active (non-variant, non-suspended)**: ~600
- **Already-marked variants**: ~120 (from previous cleanup rounds + today's 12 al-prefix merges)
- **Suspected remaining variants**: ~146 detected by CAMeL, ~50 genuinely correct, ~96 false positives
- **forms_json enriched**: 97 lemmas now have variant forms indexed for future lookup
- **Leeches**: 3 remaining (الحائط, طوب, محامي)

---

## 9. Decision Record

| Decision | Rationale | Date |
|----------|-----------|------|
| Preserve hamza in storage | Standard Arabic NLP practice, prevents conflation of minimal pairs | 2026-02-11 |
| Normalize hamza at lookup time only | Catches orthographic variation without data loss | 2026-02-11 |
| Don't auto-apply CAMeL variant detection | 34% true positive rate too low for automated merging | 2026-02-11 |
| Al-prefix dedup is safe to auto-apply | 100% precision — الكتاب and كتاب are always the same lemma | 2026-02-11 |
| ي not in ENCLITICS list | Prevents false positives (عربي→عرب) but misses كتابي→كتاب | Original design |
| Clitic-aware dedup in all import scripts | Prevents variant proliferation at ingestion time | 2026-02-11 |
| MLE disambiguator in OCR pipeline | Reduces false lemma extraction (سمك→سم type errors) | 2026-02-11 |
| LLM-confirmed variant detection | CAMeL candidates + Gemini Flash confirmation. 100% on 21 ground truth, 77/135 confirmed on production. Replaces CAMeL-only 34% accuracy. | 2026-02-11 |
| غرفة IS variant of غرف | Singular/broken plural = same dictionary entry for learner tracking | 2026-02-11 |
| ملكة NOT variant of ملك (angel) | DB has ملك="angel" not "king" — genuinely different word | 2026-02-11 |
| VariantDecision cache table | Prevents re-querying LLM for known pairs on future runs | 2026-02-11 |

---

## 10. Files Reference

| File | Role |
|------|------|
| `backend/app/services/sentence_validator.py` | `lookup_lemma()`, `resolve_existing_lemma()`, `build_lemma_lookup()`, `_strip_clitics()` |
| `backend/app/services/morphology.py` | `analyze_word_camel()`, `find_best_db_match()`, `get_best_lemma_mle()` |
| `backend/app/services/variant_detection.py` | `detect_variants()`, `detect_definite_variants()`, `mark_variants()` |
| `backend/app/services/ocr_service.py` | `_step2_morphology()` uses MLE, `process_textbook_page()` uses `lookup_lemma()` |
| `backend/scripts/import_duolingo.py` | Uses `resolve_existing_lemma()` for clitic-aware dedup |
| `backend/scripts/import_wiktionary.py` | Same pattern |
| `backend/scripts/import_avp_a1.py` | Same pattern |
| `backend/scripts/normalize_and_dedup.py` | 3-pass cleanup: report variants + al-prefix dedup + forms_json enrichment |
| `backend/scripts/cleanup_lemma_variants.py` | Older cleanup with `--merge` for manual use |
| `backend/tests/test_sentence_validator.py` | Tests for `lookup_lemma`, `resolve_existing_lemma` |
