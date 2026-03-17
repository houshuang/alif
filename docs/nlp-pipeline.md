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

## CAMeL Disambiguation in Lemma Mapping
When `lookup_lemma()` finds a match via clitic stripping but the stripped form is ambiguous (multiple possible lemmas), it calls `_camel_disambiguate()` which delegates to `find_best_db_match()` in morphology.py to pick the best DB lemma. This also serves as a last-resort fallback when no rule-based match is found. Minimum-length guard: al-prefix is not prepended to stems shorter than 3 characters (prevents false matches like ال + بت).

## LLM Disambiguation for Ambiguous Mappings
When `lookup_lemma()` encounters collisions (multiple lemmas normalizing to same key) or multiple clitic stripping candidates, it reports the alternatives via `out_alternatives` on the `TokenMapping`. At generation time, `disambiguate_mappings_llm()` batches all ambiguous tokens into a single Gemini Flash call with the full sentence (Arabic + English) for contextual disambiguation. Runs before verification. Only updates mappings when the LLM picks a different candidate than the default winner.

## LLM Mapping Verification
Active in production (`VERIFY_MAPPINGS_LLM=1`). After disambiguation and `map_tokens_to_lemmas()` in `material_generator.py`, `verify_and_correct_mappings_llm()` sends word-lemma pairs for contextual correctness checking (Gemini Flash → Claude Haiku fallback). Catches homograph mismatches that rule-based lookup cannot resolve (e.g., كَتَبَ "he wrote" vs كُتُب "books", أَكَلَ "he ate" vs أَكْل "food"). Returns `None` on total LLM failure — callers must discard/skip the sentence (verification failure ≠ success). Corrections applied via `correct_mapping()` (finds existing DB lemma only, never auto-creates). Sentences with unfixable mappings (correct lemma not in DB) are discarded at generation time or retired at verification time. Prompt tuned for low false positives: explicitly allows morphological derivations (conjugations, plurals, possessives, masdar→verb) and only flags genuine semantic mismatches.

## Flag-Driven Feedback Loop
When a user flags a word mapping and the flag evaluator fixes it:
1. **Fix or retire**: If the correct lemma exists in DB, the mapping is fixed. If the correct lemma is NOT in the DB, the **sentence is retired** (`is_active=False`) — lemmas are never auto-created from flag reports to avoid introducing unvetted words into the review pipeline.
2. **Bulk propagation**: `_propagate_mapping_fix()` finds other active sentences where the same surface form is mapped to the same wrong lemma. Each candidate is LLM-verified (Claude Haiku, batches of 10) before fixing. Capped at 50 propagations per flag to bound cost.

## Extended forms_json Indexing
`build_lemma_lookup()` Pass 2 indexes ALL string-valued keys from `forms_json` (except metadata keys `gender`, `verb_form`). No hardcoded key whitelist — any new enrichment key is auto-indexed. Current keys include: `present`, `past_3fs`, `past_3p`, `past_1s`, `past_3fp`, `present_3fp`, `present_3mp`, `masdar`, `active_participle`, `passive_participle`, `imperative`, `plural`, `feminine`, `elative`, `sound_f_plural`, `sound_m_plural`, `dual`, plus `variant_*` keys. Generated by `lemma_enrichment.py` via `FORMS_VALID_KEYS`.

## Verb Conjugation Recognition (Pass 3)
`build_lemma_lookup()` Pass 3 generates ~36 conjugation forms per verb using `_generate_verb_conjugations()`:
- **Past 3rd person**: 3ms base + 5 suffixes (ت, ا, تا, وا, ن) for 3fs/3md/3fd/3mp/3fp
- **Past 1st/2nd person**: uses `past_1s` stem when available (crucial for weak verbs: قال→قل, مشى→مشي). Falls back to 3ms base for sound verbs. Generates 1s/2fs/2md/2mp/2fp/1p.
- **Present tense**: extracts stem from 3ms present (e.g., يكتب→كتب), applies 4 prefixes (ي,ت,ا,ن) alone and with 5 suffixes (ون,ان,ين,ن,ي)
- Only applies to verbs with `present` in forms_json (~393 verbs)
- Weak verb coverage: with `past_1s` from LLM enrichment, hollow (قلت) and defective (مشيت) verbs get correct 1st/2nd person forms. Without `past_1s`, falls back to regular suffixation (sound verbs only).

## Noun Inflection Recognition (Pass 3)
`_generate_noun_inflections()` generates sound plural and dual forms for nouns/adjectives:
- **Sound feminine plural**: stem + ات (strips ة/ه first: معلمة→معلمات)
- **Sound masculine plural**: stem + ون/ين (مهندس→مهندسون/مهندسين)
- **Dual**: stem + ان/ين (كتاب→كتابان/كتابين)
- These are speculative — many nouns use broken plurals. LLM-provided forms (Pass 2) take priority via `set_if_new`.

## Tanwin-Alif Stripping
`strip_tanwin_alif()` removes trailing alif that serves as the seat of fathatan (accusative indefinite marker): سعيدا→سعيد, درسا→درس. Applied in both `validate_sentence()` and `validate_sentence_multi_target()` to scaffold words AND target words, including after clitic stripping.

## Lookup Collision Handling
`build_lemma_lookup()` uses two-pass construction: (1) register all lemma bare forms first, (2) register derived forms from `forms_json` second. This ensures direct lemma bare forms always take priority over derived forms (e.g., حول "around" wins over حَوْل masdar of حال "to change"). Returns a `LemmaLookupDict` (dict subclass) that tracks collisions — cases where two different lemmas normalize to the same key (e.g., أب "father" and آب "August" both → اب). First entry wins in the lookup. When `lookup_lemma()` hits a collision key and has the pre-normalized form (`original_bare`), it uses hamza-sensitive matching then CAMeL fallback to pick the correct lemma. Collisions are logged at INFO (count) and DEBUG (details).

## Planned (future)
1. MLE disambiguator for sentence-level analysis (currently single-word only)
2. Validate LLM grammar tags against morphological analysis
