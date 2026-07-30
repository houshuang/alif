# NLP Pipeline

## Rule-based (sentence_validator.py)
1. Whitespace tokenization + Arabic punctuation removal
2. Resolve the narrowly approved, fully vocalized running-text identities
   `أُنَاسٌ` → `نَاسٌ` and `فَقَدْ` → `قَدْ` before any lossy normalization.
   Registration requires exactly one stored destination, that sole row must be
   gated, and no stored exact source identity may exist; a missing, ungated,
   duplicate, or conflicting destination fails closed rather than falling
   through to a normalized/CAMeL guess.
3. Diacritic stripping + tatweel removal + alef normalization (أ إ آ ٱ → ا)
4. Clitic stripping: proclitics (و، ف، ب، ل، ك، وال، بال، فال، لل، كال) and enclitics (ه، ها، هم، هن، هما، كم، كن، ك، نا، ني)
5. Taa marbuta handling (ة → ت before suffixes)
6. Match against known bare forms set (with and without ال prefix variants)
7. 60+ hardcoded function words treated as always-known

Running-text callers must preserve the original token through step 2: use
`map_tokens_to_lemmas()` for a sentence or `lookup_lemma_id(surface, lookup)`
for one token. `lookup_lemma()` is the lower-level bare-form engine used only
after the caller has established that no exact-surface policy applies.

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
- **Defined in FUNCTION_WORDS set** in sentence_validator.py (60+ entries, bare forms); detection strips diacritics, tatweel, and wrapping punctuation before lookup so quoted/dialogue tokens like `«هَلْ` and `عِنْدَها؟»` still classify correctly
- **FUNCTION_WORD_FORMS dict** maps conjugated forms to base lemma (كانت→كان, يكون→كان, etc.)
- **Clitic stripping is NOT applied** to function words in map_tokens_to_lemmas() to prevent false analysis (e.g., كانت → ك+انت)

## Proper Names
Proper names are represented as real lemmas with `word_category="proper_name"` and gloss `(proper name)`:
- **Clickable in review**: sentence words keep the lemma_id so tap-to-lookup works and the UI can show that the token is a proper name
- **NOT scheduled**: no FSRS cards, acquisition boxes, intro cards, due state, or review credit
- **Ignored by scoring**: excluded from scaffold/comprehensibility counts, due coverage, missed/confused submission, and story unknown counts
- **Created conservatively**: storage paths can pass declared names; Hindawi promotion also infers high-confidence single-token guillemet names like `«لَيْلَى»`

## CAMeL Disambiguation in Lemma Mapping
After `lookup_lemma_id()` has handled the full surface, its bare-form
`lookup_lemma()` stage calls `_camel_disambiguate()` when a clitic-stripped
form is ambiguous. `_camel_disambiguate()` delegates to
`find_best_db_match()` in morphology.py and also provides the ordinary
last-resort fallback. Minimum-length guard: al-prefix is not prepended to
stems shorter than 3 characters (prevents false matches like ال + بت).

## LLM Disambiguation for Ambiguous Mappings
When the bare engine encounters collisions (multiple lemmas normalizing to the
same key) or multiple clitic-stripping candidates, it reports alternatives on
`TokenMapping`. Hamza-preserving bases `أن`/`إن` and compounds `بأن`, `وإن`,
`وأن`, `فأن`, and `فإن` expose only the applicable `أَنْ/أَنَّ` or
`إِنْ/إِنَّ` pair instead of falling through to a normalized winner.
Unhamzated `ان`/`فان` fail closed, exact madda `آن` excludes normalized
particle alternatives, and `بان` remains the verb. Attached-pronoun forms
canonicalize compositionally to base `أَنَّ` or `إِنَّ` under the approved
و/ف/ب prefixes; the original surface stays on `SentenceWord`, and legacy
compound lemma rows cannot win the mapping. A stored lexical `لأنّ` identity
remains authoritative for its pronoun family, exact `لِأَنْ` remains base
`أَنْ`, and unsupported ب+إن forms fail closed. Contextless import/dedup and
strict citation lookup use the same fail-closed identity policy.

The separate exact-running-text alias registry maps only `أُنَاسٌ` to existing
`نَاسٌ` and `فَقَدْ` to existing `قَدْ`, preserves the visible source token,
and replaces rather than supplements the lossy bare target identity. It is
enabled only when exactly one stored destination exists, that row is gated,
and there is no stored exact-source conflict; declared-but-unresolved aliases
remain unknown and unclassified
through mapping, correction, proper-name creation, CAMeL rescue, Discover,
story/book/OCR/Quran intake, analysis, and repair. Punctuation and
NFC-equivalent spelling are accepted, but unvocalized `أناس`/`فقد` and other
case forms do not gain an alias. `apply_corrections()` refuses any verifier
proposal other than the resolved destination, so a later correction cannot
overwrite the exact identity. At generation time, the batch verifier receives
every ambiguity with the full sentence (Arabic + English) and must return an
explicit contextual choice or issue. Target matching happens after exact
identity, and every generation path recomputes target flags after
disambiguation/correction so a changed or missing canonical target cannot be
published.

## LLM Mapping Verification
Active in production (`VERIFY_MAPPINGS_LLM=1`). After disambiguation and `map_tokens_to_lemmas()` in `material_generator.py`, `verify_and_correct_mappings_llm()` sends word-lemma pairs for contextual correctness checking (Claude Sonnet → Claude Haiku fallback, `json_schema=` for constrained decoding). Catches homograph mismatches that rule-based lookup cannot resolve (e.g., كَتَبَ "he wrote" vs كُتُب "books", أَكَلَ "he ate" vs أَكْل "food"). Returns `None` on total LLM failure — callers must discard/skip the sentence (verification failure ≠ success). Corrections applied via `apply_corrections()` — the single shared function for all 7 correction sites. Uses `correct_mapping()` internally (finds existing DB lemma only, never auto-creates). Three outcomes per correction: (1) different lemma found with compatible POS/gloss → applied, (2) no semantically compatible match → failed, (3) same compatible lemma returned → failed in generation-time correction (or treated as an overcall only in the calibrated rolling reverify path). Callers receive failed positions and decide fate (reject sentence vs null out mapping). Sentences with unfixable mappings are discarded at generation time or retired at verification time. Since 2026-05-17, `correct_mapping()` no longer accepts a same-bare candidate by Arabic form alone: the verifier proposal's English gloss/POS must plausibly match the DB lemma. This prevents missing homographs such as شال "shawl" from being "fixed" to شال "to rise" just because the bare key exists.

High-volume paths use `batch_verify_sentences()` instead of per-sentence `mapping_verification` calls. Sentence generation already verifies deterministic survivors in `batch_verification` calls. Since 2026-05-11, `update_material.py` Step A2 verifies corpus/book enrichment candidates in chunks of `ALIF_CORPUS_VERIFY_BATCH_SIZE` (default 10), and cron/warm-cache multi-target generation verifies generated candidates in chunks of `ALIF_MULTI_TARGET_VERIFY_BATCH_SIZE` (default 10). Top-level shape, cardinality, and missing/unknown/duplicate indices remain batch-fatal because row ownership is not trustworthy. Once a unique valid index is established, malformed/contradictory/unsolicited semantic content can be returned as that row's explicit invalid marker; generation and rescue skip it, while corpus preparation releases only that exact row for retry. These paths no longer launch one Claude Code session per sentence for mapping verification. Lower-volume import cleanup paths still use the single-sentence verifier where preserving path-specific failure semantics matters.

Corpus diacritization also treats the external model as untrusted. The provider
receives the exact non-diacritic content tokens and may return reformatted
punctuation or spacing, but its sentence is not stored. The pipeline requires
the same NFC tokens and word boundaries, aligns Arabic letters, transfers only
ordinary U+064B–U+0652 harakat, and rebuilds the result from the original
source. Orthographic changes (`ى`/`ي`, hamza/maddah identity, dagger alef),
digit or embedded-Latin changes, word joining/splitting, and invalid mark
clusters retry only that row. `ALIF_CORPUS_ENRICH_PROVIDER` can pin `openai` or
`anthropic` for a reviewed API diagnostic/retry; unset keeps the normal
Codex/Claude/API chain. It does not bypass any content, mapping, QA,
compare-and-set, or activation gate.

Since 2026-05-18, the runtime reviewability cutoff is the 2026-05-17 sense-aware resolver deploy (`MAPPING_VERIFICATION_MIN_AT = 2026-05-17 18:59`). `/api/review/next-sentences` does **not** run LLM verification synchronously; selected sentences must already have current stamps. Legacy rows are refreshed by warm-cache rescue/maintenance outside the response path, which avoids both a huge sweep and request-time SQLite lock contention.

## Flag-Driven Feedback Loop
When a user flags a word mapping and the flag evaluator fixes it:
1. **Fix or retire**: If the correct lemma exists in DB, the mapping is fixed. If the correct lemma is NOT in the DB, the **sentence is retired** (`is_active=False`) — lemmas are never auto-created from flag reports to avoid introducing unvetted words into the review pipeline.
2. **Bulk propagation**: `_propagate_mapping_fix()` finds other active sentences where the same surface form is mapped to the same wrong lemma. Each candidate is LLM-verified (Claude Haiku, batches of 10) before fixing. Capped at 50 propagations per flag to bound cost.

## Extended forms_json Indexing
`build_lemma_lookup()` Pass 2 indexes ALL string-valued keys from `forms_json` (except metadata keys `gender`, `verb_form`). No hardcoded key whitelist — any new enrichment key is auto-indexed. Current keys include: `present`, `past_3fs`, `past_3p`, `past_1s`, `past_3fp`, `present_3fp`, `present_3mp`, `masdar`, `active_participle`, `passive_participle`, `imperative`, `plural`, `feminine`, `elative`, `sound_f_plural`, `sound_m_plural`, `dual`, plus `variant_*` keys. Generated by `lemma_enrichment.py` via `FORMS_VALID_KEYS`. Since 2026-05-11, forms enrichment is batched through `_generate_forms_batch()` with `FORMS_BATCH_SIZE=10` and constrained `json_schema=` output; the persisted `forms_json` shape is unchanged and still passes through the same key cleaner.

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
`build_lemma_lookup()` uses layered construction: (1) register all lemma bare forms first, (1b) for every ى-final lemma also index a ي-final variant so clitic-stripped residues like `إِلَيْهَا` → `الي` resolve to the alef-maksura-keyed lemma `إلى`, (2) register derived forms from `forms_json`, (3) generate verb conjugations + noun inflections. Each layer uses `set_if_new`, so direct lemma bare forms always take priority over derived/variant forms (e.g., حول "around" wins over حَوْل masdar of حال "to change", and موسيقي "musical" wins over the ي-variant of موسيقى "music"). The exact-running-text alias registry is metadata on `LemmaLookupDict`, not another normalized key: it is derived only after the full inventory establishes a unique gated destination and the absence of an exact-source identity. The dict also tracks collisions — cases where two different lemmas normalize to the same key (e.g., أب "father" and آب "August" both → اب). First entry wins in the ordinary lookup. When `lookup_lemma()` hits a collision key and has the pre-normalized form (`original_bare`), it uses hamza-sensitive matching then CAMeL fallback to pick the correct lemma. Collisions are logged at INFO (count) and DEBUG (details).

### Citation forms: `lookup_lemma_citation()` (2026-07-15)
`lookup_lemma_id(surface, lookup)` is the running-text API; after exact-surface
resolution it delegates ordinary tokens to `lookup_lemma()`, whose
single-letter clitic stripping (و ف ب ل ك + enclitics) and greedy CAMeL last
resort buy recall. For an **isolated citation form** (dictionary headword
submitted to `/api/discover/add`), those fuzzy fallbacks are exactly wrong: a
new word shaped like clitic+known-word silently resolves onto the wrong lemma
(لاحظ→حَظّ, كناس→نَاس, سيجار→جَار — 18 documented cases; 16 from CAMeL, 2
from clitic strips). `lookup_lemma_citation()` keeps only the high-confidence
layers — exact approved identities, function-form overrides, direct match with
collision resolution, plain ال add/strip, and stripping the ال-bearing
`CITATION_AL_PREFIXES` (وال بال فال كال لل, so بالمكتبة still resolves to
مكتبة) — and never consults CAMeL. It returns `None` for unknown citation
forms, which `/add` treats as “create.” Evidence, fix-shape scoring, and
regression fixtures: `research/spec-2026-07-15-lookup-clitic-collision.md` §7
+ `research/lookup-collision-findings-2026-07-15.json`. Tests:
`TestLookupLemmaCitation` in `test_sentence_validator.py`.

## Uthmani-Specific Normalization (Quran Lemmatization)
Quranic text uses Uthmani orthography which differs from standard Arabic in several ways. The lemmatization pipeline in `quran_service.py` handles:
1. **Ta maftouha → ta marbuta fallback**: When lemma lookup fails for a Quranic word, tries replacing word-final ت (ta maftouha, the Uthmani spelling) with ة (ta marbuta, standard spelling) and re-lookup. Handles ~15 high-frequency words: رحمت→رحمة, نعمت→نعمة, سنت→سنة, كلمت→كلمة, شجرت→شجرة, etc.
2. **Uthmani diacritics in transliteration**: The transliteration engine recognizes U+06E1 (small high dotless head of khaa / Uthmani sukun), U+06DF (small high rounded zero), U+06E2 (small high meem) — these are Uthmani-specific marks not present in standard Arabic text.

## Import-time lemmatization (OCR / Quran)
Beyond the lookup/disambiguation paths above, two import pipelines run CAMeL morphology *at intake time* to avoid storing inflected/clitic-attached surface forms as canonical lemmas.

1. **Quran intake — per-surface MLE canonicalization (#76)**: `quran_service._camel_canonicalize_unknowns()` runs `get_best_lemma_mle()` on each unknown surface form (falling back to the diacritic-stripped surface) *before* any new canonical lemma is created. It takes the CAMeL `lex`, normalizes it (`strip_diacritics` + `normalize_alef`) to a bare key, and routes the surface into one of three buckets: link to an existing DB lemma when the canonical bare already exists, group under a to-be-created canonical (multiple inflected surfaces sharing a canonical collapse into one group), or fall back to LLM-only handling when CAMeL gives no analysis. This fixed the 2026-05-15 leak where conjugated surfaces like نَزَّلْنَا "we sent down" were stored as canonicals because the old prompt asked for "bare as given" and never lemmatized.
2. **OCR textbook intake — vocalized lex for the headword (#78)**: `ocr_service._step2_morphology()` returns `base_lemma_vocalized` (the CAMeL `lex` *with* diacritics) alongside the bare/root/pos. `process_textbook_page()` uses it as the stored `lemma_ar` whenever its diacritic-stripped form matches the chosen `import_bare` — so an al-prefixed surface like الْمَاشِي (`prc0='Al_det'`) is stored with headword ماشِي instead of the clitic-attached surface. Falls back to the OCR surface if CAMeL gave nothing or its stripped form would change the bare key.

## Planned (future)
1. MLE disambiguator for sentence-level analysis (now only *partly* single-word only — Quran intake runs per-surface MLE canonicalization, see "Import-time lemmatization" above; full sentence-level MLE disambiguation is still unimplemented)
2. Validate LLM grammar tags against morphological analysis
