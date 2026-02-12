# NLP Pipeline

## Rule-based (sentence_validator.py)
1. Whitespace tokenization + Arabic punctuation removal
2. Diacritic stripping + tatweel removal + alef normalization (أ إ آ ٱ → ا)
3. Clitic stripping: proclitics (و، ف، ب، ل، ك، وال، بال، فال، لل، كال) and enclitics (ه، ها، هم، هن، هما، كم، كن، ك، نا، ني)
4. Taa marbuta handling (ة → ت before suffixes)
5. Match against known bare forms set (with and without ال prefix variants)
6. 60+ hardcoded function words treated as always-known

## CAMeL Tools (morphology.py)
1. Input word → `analyze_word_camel()` → list of morphological analyses
2. Each analysis dict: `lex` (base lemma), `root`, `pos`, `enc0` (pronominal enclitic), `num`, `gen`, `stt`
3. `get_base_lemma()` returns top analysis lex; `get_best_lemma_mle()` uses MLE disambiguator for probability-weighted analysis (reduces false positives)
4. `is_variant_form()` and `find_matching_analysis()` use hamza normalization (`normalize_alef`) at comparison time — hamza preserved in storage, normalized only for matching
5. `find_best_db_match()` iterates ALL analyses, matches against known DB lemma bare forms with hamza normalization
6. Graceful fallback: if `camel-tools` not installed, all functions return stub/empty data. MLE falls back to raw analyzer if model unavailable.
7. Requires `cmake` build dep + `camel_data -i light` download (~660MB) in Docker
8. **Variant cleanup**: `scripts/cleanup_lemma_variants.py` uses DB-aware CAMeL Tools disambiguation. `scripts/normalize_and_dedup.py` does 3-pass cleanup: variant detection + clitic-aware dedup + forms_json enrichment.

## Function Words
Function words (pronouns, prepositions, conjunctions, demonstratives, copular verbs like كان/ليس) are:
- **Tappable in sentence review**: show correct gloss, root, forms, with a "function word" badge
- **NOT given FSRS cards**: no spaced repetition scheduling, no "due" state, no review cards
- **Tracked in SentenceWord**: keep lemma_id for lookup purposes, but sentence_review_service skips them for credit
- **Have Lemma entries in DB**: with proper glosses and forms, but no ULK (UserLemmaKnowledge) records
- **Defined in FUNCTION_WORDS set** in sentence_validator.py (60+ entries, bare forms)
- **FUNCTION_WORD_FORMS dict** maps conjugated forms to base lemma (كانت→كان, يكون→كان, etc.)
- **Clitic stripping is NOT applied** to function words in map_tokens_to_lemmas() to prevent false analysis (e.g., كانت → ك+انت)

## Planned (future)
1. MLE disambiguator for sentence-level analysis (currently single-word only)
2. Validate LLM grammar tags against morphological analysis
