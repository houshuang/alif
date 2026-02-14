# Alif — Master Ideas File

> This file tracks ALL ideas for the project. Never delete ideas. Mark as [DEFERRED], [REJECTED], or [DONE] with reasoning. Every agent should add new ideas discovered during work.

---

## Core Learning Model

### Word Knowledge Tracking
- Track at three levels: root, lemma (base form), conjugation/inflected form
- Primary tracking at lemma level, root familiarity derived from its lemmas
- Conjugation-level tracking deferred to Phase 2 [DEFERRED — reduces MVP complexity]
- When user clicks a word: show root, base form, translation. User marks known/unknown
- Imported words get partial credit (not full "known" status) — need verification through review
- [DONE] Knowledge score (0-100) per word: 70% FSRS stability (log-scaled, measures memory durability) + 30% accuracy, scaled by confidence ramp (diminishing returns on review count). Stability dominates because it only grows through successful spaced repetition.
- [DONE] Al- prefix deduplication: "الكلب" and "كلب" are the same lemma. Import strips ال before dedup check. Merged 14 duplicates in existing data.

### Focus Cohort Size Analysis
- Current MAX_COHORT_SIZE=100. Research recommends 30-50 for 2-3 reviews/word/day. Need data to decide.
- Write a script that queries production DB: count of FSRS-due words per day over last 2 weeks, cohort utilization (how many due words are outside the cohort), average reviews per word per day.
- If typical due count is <50, reducing cohort has no practical effect. If >50, smaller cohort prioritizes fragile words more aggressively.
- Decision deferred until data analysis completed.

### Spaced Repetition
- Use FSRS algorithm (py-fsrs), superior to SM-2
- Reading-focused: user sees Arabic → tries to comprehend → reveals translation → rates
- No production exercises (no typing Arabic, no translation to Arabic)
- Self-assessed: trust user not to cheat since no gamification pressure
- Could track response time as implicit difficulty signal
- Consider separate FSRS cards for recognition vs. recall if we ever add production

### Root-Based Learning
- Learning KTB root → Maktaba, Maktab, Kataba etc. are highly productive
- Identify morphological patterns (e.g., how to form "place of doing X" = maf3al)
- Verb form patterns (Form I-X) as learning accelerators
- Group kitchen appliances, professions, etc. by pattern
- Root family exploration UI: show all known/unknown words from a root
- Prioritize roots by "productivity" (number of common derivatives)

### Curriculum Design
- Structure learning by word frequency + domain
- Use CAMeL MSA Frequency Lists (11.4M types from 17.3B tokens)
- KELLY project for CEFR-level word mapping
- Learning progression: A1 (top 100 roots, Form I only) → C1 (all forms, dialectal variants)
- Domain-based modules (food, family, politics, religion, etc.)

---

## Sentence Generation & Validation

### LLM + Deterministic Validation
- Generate-then-validate pattern: LLM generates sentence → CAMeL Tools lemmatizes every word → check against known-word DB → verify exactly 1 unknown word
- Retry loop with feedback to LLM (max 3 attempts)
- [DONE] All words are now learnable — no function word exclusions. Particles, prepositions etc. get full FSRS tracking.
- Sentence templates for quick generation: "the X is Y", "I went to the X"
- Pre-generate and cache validated sentences for offline use

### Per-Word Contextual Translations
- Currently the LLM returns only sentence-level data (arabic, english, transliteration). Per-word glosses come from the Lemma table or hardcoded `FUNCTION_WORD_GLOSSES`. Words without either have no gloss.
- **Idea**: Ask the LLM to return per-word contextual translations during generation. Solves missing glosses AND adds learning value (context-specific meanings for polysemous words like عين = eye vs spring).
- Requires `gloss_en` column on `SentenceWord` (currently only `StoryWord` has one), modified LLM prompt, and matching logic between LLM word keys and tokenized surface forms.
- Interim option: lemma-based backfill (covers ~95% of words without LLM changes, no contextual value).
- **Full writeup**: [`research/per-word-contextual-translations.md`](research/per-word-contextual-translations.md)
- **Timing**: Revisit during sentence generation redesign.

### Sentence Sources
- LLM-generated sentences with vocabulary constraints
- Tatoeba corpus (8.5M Arabic-English pairs, CC BY 2.0)
- BAREC corpus (69K sentences across 19 readability levels)
- Quran (Tanzil corpus) — gold-standard diacritized text
- News articles segmented into sentences

### Difficulty Assessment
- [DONE] SAMER lexicon: 40K lemmas with 5-level readability scale — backfilled to cefr_level (1365/1610 matched), auto-runs in update_material.py cron. TSV at backend/data/samer.tsv on server (not in git, license: non-commercial/no redistribution).
- BAREC: 19-level sentence difficulty — investigated as sentence source, but only ~50% diacritized and many are context-dependent excerpts. Not a drop-in replacement for LLM generation. ~3,700 fully diacritized usable sentences at levels 5-10 in 5-14w range.
- Word frequency rank as proxy for difficulty
- Sentence difficulty = function of (unknown words, grammar complexity, length)

---

## Text Processing Features

### Text Import & Analysis
- Paste any Arabic text → extract all words → analyze with CAMeL Tools
- Show: total words, unique lemmas, known/unknown breakdown, difficulty score
- Create training plan: learn unknown words in frequency order until text is readable
- Track progress toward "ready to read" target text

### Text Rewriting
- Rewrite text to a desired difficulty level using LLM
- Replace unknown words with known synonyms where possible
- Simplify grammar while preserving meaning
- Output both simplified and original for comparison

### Glossing
- Generate interlinear glosses for any text
- Annotate only unknown words (based on user's knowledge)
- Export glossed text as PDF for offline reading
- Progressive glossing: reduce annotations as knowledge grows

---

## Audio & Listening

### Text-to-Speech
- ElevenLabs for high-quality audio generation
- Google Cloud TTS (1M chars/month free) as fallback for MSA
- ARBML/Klaam for self-hosted open-source option
- Generate audio per-sentence and for full texts
- Cache all generated audio
- Use Duolingo CDN audio URLs as fallback for words that were imported from Duolingo (already have per-word audio URLs in the export)
- Audio filename keyed by SHA256 of (text + voice_id) for deterministic caching
- Consider pre-generating audio for all sentences during off-peak hours to avoid API latency during reviews

### Listening Practice Modes
- Listen-only mode: hear sentence, try to understand, then see text
- Read-along mode: see text + hear audio simultaneously
- Sentence-by-sentence: practice individual sentences, then full story
- Speed control: slow down audio for beginners
- Minimal pair practice: distinguish similar-sounding words

### Story Mode
- Generate stories with controlled vocabulary (LLM + validation)
- Progressive difficulty: each story slightly harder than the last
- Story series: recurring characters/themes for context building
- Record which stories have been "mastered" (all words known + comprehension)

---

## Duolingo Import
- 302 lexemes exported with diacritics and audio URLs
- Many inflected forms (كَلْبِك، كَلْبَك، كَلْبي from كَلْب)
- Includes proper nouns, country/city names to filter
- Audio URLs from Duolingo CDN — could potentially cache these
- Import as "learning" state, not "known" — verify through review cycle

---

## Diacritization (Tashkeel)

### Tools
- CATT (Apache 2.0) — best open-source accuracy, pip-installable
- Mishkal — rule-based, good for simple cases
- CAMeL Tools — built-in diacritization
- Risk: published benchmarks inflated by 34.6% data leakage

### Application
- Diacritize all displayed Arabic text by default
- Option to hide diacritics for advanced practice
- Partial diacritization: only show diacritics on difficult/ambiguous words
- Pre-diacritize and cache for lesson content
- Human review for critical educational materials

---

## UI / UX Ideas

### Review Interface
- Large Arabic text (32pt+), RTL-aligned
- Tap to reveal translation, root, morphological info
- Four-button rating: Again / Hard / Good / Easy
- Progress indicator: cards remaining, streak, session stats
- Night mode for comfortable reading
- [DONE] Removed redundant missed word summary below transliteration — words already highlighted red/yellow in sentence
- [DONE] Root family in word info card filters out self (no longer shows looked-up word as its own sibling)
- [DONE] Root meaning text wraps properly (flexShrink) instead of overflowing card
- [DONE] Back/undo in review: go back to previous card after submitting, undo the review (restores pre-review FSRS state from snapshots in fsrs_log_json). Handles both sync queue (not yet flushed) and backend (already flushed) cases. Idempotent.

### Word Detail View
- Show: Arabic (diacritized), English gloss, root, POS
- All known words from same root
- Verb conjugation table (via Qutrub)
- Example sentences using this word
- Audio pronunciation
- Frequency rank / difficulty level
- [DONE] Suspend/reactivate + flag translation actions (via ActionMenu)

### Action Menu
- [DONE] Generic "⋯" action menu replacing AskAI FAB across all screens (review, learn, story, word detail)
- [DONE] Consolidates: Ask AI, Suspend word, Flag content (translation/Arabic/transliteration)
- [DONE] Sentence info debug modal: shows sentence ID, source, difficulty score, times shown, review history, and per-word FSRS difficulty/stability/accuracy. Accessible from "..." menu during review.
- Future: add "Never show this sentence again" action to retire specific sentences from review
- Future: "Report pronunciation" to flag TTS audio quality issues
- [DONE] LLM-generated memory hooks per word: mnemonic, cognates (11 languages), collocations, usage context, fun fact. JIT on introduction. `memory_hooks_json` on Lemma. Personal notes still future.
- Future: "Add personal note" per word/sentence for custom mnemonics (in addition to LLM-generated hooks)

### Content Quality
- [DONE] Flag system: user flags suspicious content → background LLM (GPT-5.2) evaluates and auto-fixes
- [DONE] Activity log: tracks flag resolutions, batch job results, backfills
- Future: periodic quality sweep — run all glosses through LLM evaluation proactively
- Future: track which import source produces the most flags → surface data quality insights
- Future: crowd-source corrections if multi-user (far future)

### Word List Browser
- Filter by: knowledge state, POS, root, frequency, source
- Sort by: due date, frequency, alphabetical
- Search by Arabic or English
- Bulk operations: mark known, mark for review, delete
- [DONE] Sort by review status: failed words first (red tint + border), then passed (green), then unseen
- [DONE] Show review stats per word: "Seen 3x · 2 correct · 1 failed" with colored counts
- [DONE] "Reviewed" filter chip, knowledge score display, refresh on tab focus
- [DONE] Search icon + clear button, horizontally scrollable filter chips, full state names in badges
- [DONE] Two-column compact grid layout with review sparklines on word cards
- [DONE] Category tabs: Vocabulary / Function / Names (with proper noun rendering)
- [DONE] Smart filter tabs: Leeches (high review, low accuracy), Struggling (recent failures), Recent (newly learning), Solid (high score), Next Up (learn algorithm candidates)
- [DONE] Next Up tab: shows learn algorithm's top 20 candidates with score breakdown (frequency, root familiarity, known siblings)
- Shared design system: extract common card/button/badge styles into theme.ts or shared components to prevent screens drifting apart visually

### Text Reader View
- Display Arabic text with word-level tap interactions
- Color-code words: known (green), learning (yellow), unknown (red)
- Tap unknown word → add to learning queue
- Show difficulty score for the text
- Track reading progress

---

## Data & Analytics

### Interaction Logging
- Log every interaction in JSONL format
- Fields: timestamp, event type, lemma/word ID, rating, response time, context, session ID
- Append-only log files, partitioned by date
- Essential for: algorithm tuning, learning curve analysis, identifying problem words
- [DONE] JSONL events: session_start, sentence_selected, sentence_review, tts_request, legacy_review
- [DONE] DB tables: review_log (per-word with credit_type, sentence context), sentence_review_log (per-sentence)
- [DONE] Fixed: /sync endpoint (offline queue) now also writes JSONL logs (was only writing to DB)
- [DONE] Enriched logging: ai_ask logs question text, quiz_review logs comprehension_signal, sentence_review logs per-word rating map + audio_play_count + lookup_count, story complete/skip/too_difficult logs word counts + reading_time_ms, review_word_lookup logs word details + root
- [DONE] Test data separation: interaction logger skips when TESTING env var is set (conftest.py sets it); logger tests use autouse fixture to temporarily re-enable
- [DONE] FSRS stability floor: cards labeled "known" with stability < 1.0 get relabeled to "lapsed"
- [DONE] Session ID consistency: backend now generates full UUIDs (was 8-char truncated), frontend uses backend's session_id instead of replacing it. Reviews now correlate to session_start events in logs.
- [DONE] Story event logging: all story log_interaction calls now use proper keyword args (story_id, surface_form, position) instead of embedding in context string
- [DONE] ULK provenance tracking: source field on UserLemmaKnowledge distinguishes study (Learn mode), auto_intro (inline review), collocate (sentence gen), duolingo (import), encountered (collateral credit in sentence review). introduce_word() accepts source param.

### Analytics Dashboard
- Words learned over time (cumulative)
- Review accuracy by category (POS, frequency band, root family)
- Time per review card
- Retention curves
- Root coverage: % of top-N roots mastered
- Predicted vocabulary size

### Simulation-Driven Analysis
- [DONE] Multi-day simulation framework: drives real services against DB copy, profiles (beginner/strong/casual/intensive), freezegun time control
- Run simulations after algorithm changes to predict impact before deploying
- Compare simulation outcomes across profiles to find "sweet spot" parameters
- Use simulation CSV output to generate matplotlib charts (review load curves, state transition Sankey diagrams)
- Add "adversarial" profiles: always-wrong student, always-skip student, binge-then-vanish student
- Simulate specific scenarios: e.g., vary MAX_ACQUIRING_WORDS (now 40), test different graduation thresholds

### Algorithm Optimization
- Use logged data to tune FSRS parameters per-user
- Identify words that are consistently hard → provide extra context/examples
- Detect if difficulty ratings are miscalibrated
- A/B test different presentation modes (with logging data)
- **Response time as difficulty signal**: response_ms is already captured for reading/listening reviews (stored in ReviewLog + SentenceReviewLog + JSONL logs) but never used. Possible uses: slow response → word is harder (could influence FSRS scheduling or sentence selection), decreasing response time over repeated reviews of same word = fluency/acquisition signal, analytics dashboard showing time-per-card trends. Caveat: response time is noisy (distracted vs. genuinely struggling), best as supplementary signal alongside ratings.
- [DONE] **Learn mode quiz timing gap**: `frontend/app/learn.tsx` hardcoded `response_ms: 0` for quiz reviews — fixed with `quizStartTime` ref that measures actual elapsed ms

---

## Technical Ideas

### Frontend Testing
- [DONE] Jest + ts-jest test infrastructure with mocks for AsyncStorage, expo-constants, netinfo
- [DONE] Sync queue tests (enqueue/remove/pending/dedup)
- [DONE] Offline store tests (mark/unmark reviewed, session cache, invalidation, story lookups)
- [DONE] Smart filter logic tests (leech/struggling/recent/solid detection with boundary cases)
- [DONE] API interaction tests (sentence review submit/undo, word lookup caching, story ops, learn mode, flagging, offline fallback)
- Component-level tests with React Testing Library (render review cards, word list, story reader in various states)
- Snapshot tests for key UI states (empty, loading, error, populated)
- E2E tests with Detox or Maestro for critical user flows (review session, learn flow, story import)

### Offline Architecture
- All review data in IndexedDB (web) / SQLite (mobile)
- Pre-sync: download next N days of review cards + sentences + audio
- Background sync when online: upload logs, download new content
- Service worker for web PWA caching
- Expo offline-first with AsyncStorage or expo-sqlite
- [DONE] Clear Cache button in More screen: flushes sessions, word lookups, stats, analytics from AsyncStorage

### Deployment
- [DONE] Backend: Hetzner Helsinki, direct docker-compose (Coolify removed — too complex for single-user app)
- Fly.io as alternative (~$7-8/mo with persistent volume for SQLite)
- Pre-process everything server-side, client only needs processed data
- Consider edge functions for simple lookups

### Data Sources to Integrate
- CAMeL Lab MSA Frequency Lists (11.4M types)
- KELLY project (CEFR-tagged Arabic)
- Arabic Roots & Derivatives DB (142K records, 10K+ roots, CC BY-SA)
- [DONE] Kaikki.org Wiktionary (57K Arabic entries, JSONL) — import_wiktionary.py streams the 385MB JSONL, filters nouns/verbs/adj, imports top N
- [DONE] AVP A1 dataset (~800 validated A1 Arabic words) — import_avp_a1.py scrapes from lailafamiliar.github.io
- Arramooz dictionary (SQL/XML/TSV)
- Tashkeela (75M diacritized words)
- UN Parallel Corpus (20M pairs)
- Buckwalter Morphological Analyzer (83K entries, GPL-2.0)

### API Strategy
- Farasa REST API — free morphology/diacritization (research use only)
- Azure Translator — 2M chars/month free
- Google Cloud TTS — 1M chars/month free (MSA voices)
- LibreTranslate — self-hostable, unlimited
- HuggingFace Inference — free tier for AraBERT/CAMeLBERT

---

## Patterns from Other Projects

### From Bookifier (content-hash caching, glossed PDFs)
- **Content-hash caching for LLM outputs**: Cache sentence translations and generated sentences by SHA256 hash of (input + model + prompt_version). Avoids regenerating identical content. Use SQLite cache table with content_hash as primary key.
- **WeasyPrint for glossed PDFs**: Generate Arabic reading PDFs with CSS page footnotes for glosses. WeasyPrint supports `float: footnote` CSS for scholarly annotations. Perfect for annotating unknown words in a text.
- **Stage-based processing**: Independent pipeline stages (extract → translate → annotate → assemble) with JSON intermediate outputs. Each stage can be inspected/adjusted independently.
- **Vocabulary extraction per paragraph**: When processing a text, extract 2-3 difficult words per paragraph with definitions. Store in vocabulary_json field.
- **Bilingual EPUB generation**: Side-by-side original + translation with highlighted vocabulary and clickable glossary anchors.
- **Rate limiting with Bottleneck**: Use bottleneck library pattern for API rate limiting (max concurrent + min time between requests).

### From Comenius (production schema, ingestion, offline sync)
- **Drizzle ORM schema**: Normalized: Languages → Lemmas → Senses → Surface Forms → Inflections → Sentence Tokens. Consider adopting for Phase 2.
- **Book Bundle protocol**: Server queries only lemmas/inflections relevant to a specific text. Client syncs only what's needed. Critical for keeping mobile app lightweight.
- **Gemini JSON schema enforcement**: `responseMimeType: 'application/json'` + `temperature: 0` + `topK: 1` for deterministic, validated JSON output from LLM.
- **SM-2 scheduler as pure function**: Immutable `advanceReviewState(state, outcome, now)` — same pattern for our FSRS wrapper. Pure, testable, no side effects.
- **AsyncStorage + interaction queue**: Buffer offline changes, sync when online. Silent background sync.
- **Intl.Segmenter for sentence splitting**: Native API with fallback regex. Add Arabic punctuation (U+061F, U+060C, U+061B).

### From NRK/Kulturperler (LiteLLM, multi-model, logging)
- **LiteLLM unified API**: Single `call_with_search()` function wrapping Gemini + GPT with automatic fallback, retry, and exponential backoff.
- **API call logging**: Log every LLM call with provider, response time, success/failure, prompt hash. Essential for cost tracking and debugging.
- **Proposal-based data changes**: For curated content, use a proposal → review → apply workflow instead of direct edits.

### From Ninjaord (ElevenLabs patterns)
- **REST API over SDK**: Direct fetch to `https://api.elevenlabs.io/v1` with `xi-api-key` header. Simpler, fewer dependencies.
- **Audio provider fallback**: ElevenLabs → Browser Web Speech API fallback chain.
- **Voice selection UI**: Load voices from API, filter by language, let user pick and test.

### Sentence Validation Improvements (discovered during implementation)
- [DONE] **Suffix/clitic handling in validator**: Rule-based clitic stripping implemented in sentence_validator.py. Handles proclitics (و، ف، ب، ل، ك، وال، بال، فال، لل، كال), enclitics (ه، ها، هم، هن، هما، كم، كن، ك، نا، ني), and taa marbuta (ة→ت). CAMeL Tools will improve accuracy further.
- **Morphological pattern matching**: Instead of exact bare form matching, match words by root + pattern. E.g., if user knows "كتاب" (kitāb), they likely can parse "كتب" (kutub, plural) and "مكتبة" (maktaba, library). This requires root extraction from CAMeL Tools.
- **Sentence difficulty scoring**: Beyond word-level validation, score sentences by syntactic complexity (clause depth, verb forms used, agreement patterns). Could use sentence length + unknown-word ratio as simple proxy.
- **Multi-sentence generation**: Generate 2-3 variant sentences per target word in one LLM call to reduce API calls and provide variety.
- [DONE] **Negative examples in prompt**: Include words the LLM should NOT use (recently failed unknown words from previous attempts) to make retries more effective — implemented as `rejected_words` param in `generate_sentences_batch()`, fed back from validation failures in `update_material.py`

## Future / Speculative Ideas

- Dialect support: track MSA vs. Levantine/Egyptian/Gulf vocabulary separately
- Reading difficulty predictor: given a URL, estimate how ready the user is to read it
- Browser extension: highlight unknown words on any Arabic webpage
- Anki export: generate Anki decks from the app's word database
- Social features: share word lists, compare progress (far future, if ever)
- Handwriting recognition: practice writing Arabic letters (contradicts reading-only focus, but useful for letter learning)
- Grammar drills: sentence transformation exercises (passive, negation, etc.)
- Cloze deletion: show sentence with one word blanked, user guesses from context
- [REMOVED] Collocations: reactive collocate auto-introduction — was auto-introducing words during sentence generation. Removed because it flooded the user with 24 unfamiliar words in one evening (Feb 8 2026), cratering next-day comprehension to 10% understood. Word introduction should be user-driven via Learn mode only.
- Collocations — proactive: build explicit prerequisite graph so collocated words are learned together (e.g. يوم before day-name words). Could be auto-discovered from generation failures or manually curated. Better approach than reactive auto-introduction which flooded the user.
- Collocations — suggestion-based: when sentence generation fails due to unknown collocate, surface the collocate as a "suggested next word" in Learn mode rather than auto-introducing it. Track which target words are blocked by which collocates.
- Arabic-to-Arabic definitions: as level increases, use Arabic definitions instead of English
- Morphological pattern drills: given root + pattern → predict meaning
- Spaced reading: schedule re-reading of texts at increasing intervals
- Vocabulary prediction: estimate total passive vocabulary from tested sample (like a placement test)

### Ideas from Arabic Linguistic Challenges Research (2026-02-08)

#### Root Explorer UI
- Root Explorer as a first-class feature: tap any root to see a tree/map of all derivatives organized by pattern type (agent nouns, place nouns, verb forms, etc.)
- Color-code words in reader view by root family (subtle background tint) to build unconscious root awareness
- "Root discovery" celebrations: when user learns 3rd word from a new root, show root family and how many more words they can now partially understand
- Root productivity ranking: prioritize teaching high-productivity roots (most common derivatives) first

#### Pattern-Based Learning Acceleration
- Pattern bonus in SRS: after user knows N words following same wazn (e.g., maf'al = place), reduce initial difficulty for new words with that pattern
- Verb form semantic labels in UI: always show "Form II = intensive/causative" next to verb form number
- Broken plural pattern grouping: review broken plurals in pattern clusters (fu'ul, af'al, etc.) rather than individually
- Masdar (verbal noun) pattern teaching: Forms II-X have predictable masdars; only Form I masdars need individual memorization

#### Diacritics Training System
- 4-level progressive diacritics mode: (a) full tashkeel, (b) no case endings, (c) ambiguous/unknown words only, (d) bare text
- Diacritics independence assessment: periodically present undiacritized versions of known words to measure reading ability without crutch
- Partial diacritization based on user knowledge: show tashkeel only on words the user has not yet mastered

#### Phonological Training for Listening
- Minimal pair exercises for emphatic consonants: ص/س, ض/د, ط/ت, ظ/ذ
- Pharyngeal consonant training: ع/ا and ح/ه discrimination drills
- Sun/moon letter assimilation highlighting: visually show assimilation of lam in definite article
- Confusion pair tracking: when user confuses two phonologically similar words, link them and schedule targeted review of both

#### Grammar Concept Tagging
- [DONE] Tag every sentence with grammar concepts it illustrates — implemented via grammar_tagger.py (LLM-based) and sentence_grammar_features table
- [DONE] Grammar concept progression: 5-tier system (Tier 0 always available → Tier 4 requiring comfort ≥ 0.5) with comfort score formula based on exposure, accuracy, and recency decay
- [DONE] Grammar familiarity tracking: user_grammar_exposure table tracks times_seen, times_correct, comfort_score per feature

#### Register-Aware Content
- Tag all content by MSA register (news, literary, religious, academic, everyday)
- Register selection in onboarding: let user choose primary interest
- Register-specific frequency ranks: a word common in news Arabic may be rare in literary Arabic
- Gradual register expansion as proficiency grows

#### Conjugation Transparency
- Regular conjugations of known verbs should NOT count as separate vocabulary items
- Track conjugation pattern familiarity separately (does user recognize 3rd person feminine plural?)
- Only create explicit review cards for irregular verb forms (hollow, defective, doubled, hamzated)
- Mini conjugation tables available on tap for any verb in context

#### Function Word Bootstrap
- [DONE] **All words are now learnable**: FUNCTION_WORDS set emptied — prepositions, pronouns, conjunctions, demonstratives all get full FSRS tracking. No words excluded from sentence generation or review credit. FUNCTION_WORD_FORMS kept for clitic analysis prevention, FUNCTION_WORD_GLOSSES kept as fallback.
- [DONE] **Grammar particle info**: 12 core particles (في، من، على، إلى، عن، مع، ب، ل، ك، و، ف، ال) have rich grammar info (meaning, examples, grammar notes) shown in WordInfoCard via `grammar-particles.ts`.
- [REJECTED] Exclude function words from "unknown word" count — all words should be treated equally. The learner wants to track their knowledge of all words including particles.
- [REJECTED] Pre-load as Phase 1 — automated introduction handles this naturally via frequency-based ordering.

#### Writing System Features
- Hamzat al-wasl vs al-qat' visual distinction in reading mode (gray out hamzat al-wasl to show it's elided)
- Ta' marbuta pronunciation context indicator (show when /t/ is pronounced vs. silent)
- Letter similarity overlay: option to highlight dot-differentiated letter pairs for beginners
- Font selection prioritizing maximum letter distinctiveness (especially ة/ه, ى/ي)

#### Story and Extensive Reading Mode
- [DONE] Generated story mode: LLM generates 4-8 sentence stories using only known vocabulary, validates all words
- [DONE] Imported story mode: paste any Arabic text, app analyzes known/unknown words, calculates readiness percentage, tracks learning progress toward reading the story
- [DONE] Story reading UI: full-screen Arabic with word-level tapping, fixed translation panel, Arabic/English tab toggle
- [DONE] Story completion: complete (FSRS credit for all words), skip (no effect), too difficult (mark for later)
- [DONE] Story list with readiness indicators (green/yellow/red), generate + import buttons
- [DONE] Story list design polish: bottom-sheet modals, larger Arabic titles (24px), icon badges, refined card layout
- [DONE] Story reader declutter: moved Complete/Skip/Too Hard from fixed bottom bar to end of scroll content, maximizing reading space
- [DONE] Morphological fallback for story word lookup: CAMeL Tools analysis resolves conjugated forms (قالت→قال) that clitic stripping misses
- [DONE] Story import creates Lemma entries for unknown words (CAMeL + LLM translation). No ULK — words become Learn mode candidates with story_bonus priority. Completes the import → learn → read pipeline.
- [DONE] Story completion auto-creates ULK: all words get FSRS credit, not just words with existing knowledge records
- [DONE] Word provenance: word detail screen shows "From story: [title]" / "From textbook scan" badge with tap-to-navigate
- Expand forms_json to include all verb conjugation paradigms (past 3fs, past 3mp, present, etc.) — would make lookup faster than morphological analysis at import time
- Graded text mode supporting 95-98% vocabulary coverage for extensive reading
- Narrow reading: offer multiple texts on the same topic to recycle vocabulary
- Story series with recurring characters/themes for context building
- Three-stage listening reveal: audio only -> Arabic text -> English translation
- Story difficulty auto-selection: pick stories where readiness is 85-95% for optimal learning
- Story audio: TTS for full story, sentence-by-sentence playback with highlighting
- Story sharing: export stories as formatted PDF with glossary of unknown words
- **[BENCHMARK 2026-02-14]** Switch story generation from GPT-5.2 to Claude Opus — benchmark showed Opus produces 4.3 composite (vs 2.6 OpenAI) at 93% compliance on best attempts. Cost $0.15/story, acceptable for 2-3 stories/week.
- Cross-model two-pass story pipeline: Sonnet generates freely (best narrative quality), Gemini Flash rewrites for vocabulary compliance — not yet tested but promising given that Sonnet scored 4.75 composite (highest) but only 33% compliance
- Story retry loop: port sentence generator's 7-attempt retry loop to stories, feeding back unknown words as feedback
- Story quality gate: add Gemini Flash review (grammar + translation accuracy) like sentences have
- Expand forms_json with verb conjugation paradigms — the compliance validator misses known words in conjugated forms (يوم, رأى, قالت, صغير flagged as unknown despite being in vocabulary). Fixing this would improve reported compliance by ~10-15%.
- Recurring character universe for stories: pre-define characters (سمير، ليلى، عمر) with traits. Models produce more coherent stories with established characters.
- Story-aware vocabulary constraint: include acquiring words (box 1-3) in story vocabulary — currently excluded from `_get_known_words()`

### Ideas from Cognitive Load Theory Research (2026-02-08)

#### Sentence Difficulty Scaling by Word Maturity
- [DONE] Tie sentence complexity directly to FSRS stability of the target word — implemented in sentence_selector.py difficulty matching formula (stability < 1d → scaffold stability > 7d; stability 1-7d → scaffold avg > 14d; stability > 7d → mixed OK)
- Store a "sentence difficulty tier" (1-5) on generated sentences and select appropriate tier based on the target word's FSRS state
- [DONE] Sentence validator should check not just that surrounding words are "known" but that they have FSRS stability > 14 days for sentences containing new words — implemented via difficulty_match_quality scoring in greedy set cover
- Consider using CAMeL token count (after clitic separation) rather than raw word count for sentence length targets, since Arabic agglutination makes raw word count misleading

#### Comprehension-Aware Sentence Recency
- [DONE] Sentences the user struggled with should reappear sooner: replaced fixed 7-day cooldown with comprehension-based cutoffs — "understood" sentences wait 7 days, "partial" wait 2 days, "no_idea" wait 4 hours. Uses last_comprehension column on Sentence model, checked in sentence_selector.py candidate filtering.

#### All Words Get FSRS Credit in Sentence Reviews
- [DONE] Every word seen in a sentence now gets a full FSRS card and enters the normal review process. Previously, words without existing knowledge records only got encounter tracking (total_encounters increment). Now fsrs_service.submit_review() auto-creates UserLemmaKnowledge records for unknown words, and sentence_review_service calls submit_review() for every lemma_id in the sentence (not just those with existing cards).

#### Adaptive Session Pacing
- Track rolling accuracy over the last 10 items during a session; if it drops below 75%, automatically pause new word introductions and show only easy review items until accuracy recovers above 85%
- Track response time as a cognitive load signal: if average response time increases beyond 2x the learner's rolling average, treat it as overload even if accuracy is maintained
- Default to 5 new words per 20-minute session for Arabic (conservative, research-backed); allow learner to adjust but show guidance about cognitive load tradeoffs
- After 3 consecutive "Again" ratings on different items: insert 5 easy review items as a cognitive "rest stop"
- If learner continues a session beyond 20 minutes, show only review items (no new introductions in "overtime")
- Session-level accuracy trend tracking: compare first-half vs. second-half accuracy; if second half degrades, suggest shorter sessions in settings

#### New/Review Item Interleaving
- [DONE] Inline intro candidates in review sessions: build_session() suggests up to 2 intro candidates at positions 4 and 8, gated by accuracy > 75% over last 20 reviews and minimum 4 review items. Reading mode only (no intros in listening). **Candidates are suggestions only** — not auto-introduced at session fetch time. User must accept via Learn mode.
- Never show two new word introductions back-to-back -- always interleave with 4-6 review items between new introductions
- Start each session with 3-4 easy review items (FSRS stability > 30 days) as warm-up before any new items
- End each session with 3-4 easy review items for positive session closure (recency effect protects motivation)
- Distribute new items evenly throughout the session rather than front-loading them
- Maintain a 1:4 to 1:6 ratio of new to review items throughout the session

#### Flashcard-First Introduction Flow
- [DONE] Auto-generate sentences + audio on word introduction: /api/learn/introduce now triggers background generation of up to 3 sentences + TTS audio when a word is introduced
- [DONE] Quiz results now feed FSRS: learn-mode quiz "Got it" → rating 3, "Missed" → rating 1 via /api/learn/quiz-result endpoint
- Introduce new words initially as isolated flashcards (word + transliteration + gloss + root + audio) before embedding them in sentences -- isolated word pairs have low element interactivity (Sweller), allowing form-meaning mapping before the higher-load sentence processing task
- First sentence review should come only after the initial flashcard introduction succeeds (rated Good or Easy)
- Consider a two-step learning flow: step 1 = flashcard with root info, step 2 = simple sentence with strong context clues, step 3 = varied sentence contexts in subsequent reviews

#### Within-Session Spacing for Failed Items
- If a word is rated "Again," re-show it after 5-10 intervening items rather than immediately -- leverages spacing effect even within a single session
- If the same word fails twice in one session, do not show it again in that session; let FSRS schedule it for the next session to avoid frustration and wasted working memory

#### Expertise Reversal Awareness
- As the learner advances, progressively reduce scaffolding: offer transliteration as tap-to-reveal rather than always-visible, increase default sentence complexity, reduce auto-display of root/morphology info
- Optionally reduce diacritization on well-known words (FSRS stability > 60 days) as an advanced reading challenge
- Track when scaffolding reduction is appropriate based on accuracy patterns, not just vocabulary count

#### Sentence Context Quality Labels
- Distinguish between "informative context" (sentence provides clues to word meaning) and "opaque context" (word must be recalled from memory) -- both are useful at different learning stages
- For newly introduced words: generate sentences with informative context (the surrounding words should help the learner infer the target word's meaning)
- For mature words: generate sentences with opaque context (the learner must recall the meaning from memory, not from contextual clues) -- this is a desirable difficulty that strengthens long-term retention
- Tag generated sentences with context informativeness so the appropriate type can be selected based on word maturity

#### Generation/Prediction Effect
- [DONE] Before revealing a new word's meaning, offer the learner a chance to predict it from root knowledge or morphological patterns — implemented as front-phase word lookup during sentence review: tapping an unknown word checks if it has 2+ known siblings from the same root, and if so shows root + siblings with "Can you guess?" prompt before revealing meaning. Uses GET /api/review/word-lookup/{lemma_id} endpoint with root family + knowledge state.
- Only use prediction prompts when the learner has relevant prior knowledge (known root, known pattern, known cognate); uninformed guessing has no benefit

### Ideas from Confused/Misread State & CAMeL Tools Integration (2026-02-09)

#### Confused/Misread Review State
- [DONE] Triple-tap word marking during review: off → confused (yellow, FSRS rating 2 Hard) → missed (red, rating 1 Again) → off cycle
- [DONE] Backend `confused_lemma_ids` field in sentence review submission, flows through offline sync queue
- Track confusion patterns: which words get confused with which? Could build confusion pairs for targeted review
- Show "frequently confused" words in word detail view
- If a word has a high confusion rate (>30% of encounters marked confused), consider generating sentences that specifically contrast it with the confusable word

#### CAMeL Tools Integration
- [DONE] Replaced morphology.py stub with real CAMeL Tools analyzer (graceful fallback to stub when not installed)
- [DONE] Added `canonical_lemma_id` to Lemma model for variant tracking
- [DONE] Added `variant_stats_json` to UserLemmaKnowledge — tracks per-variant-form seen/missed/confused counts
- [DONE] Root family display filters out variants (canonical_lemma_id IS NOT NULL)
- [DONE] Learn mode word selection filters out variants
- [DONE] Cleanup script: `scripts/cleanup_lemma_variants.py` using CAMeL Tools to detect possessives, inflected forms, definite-form duplicates
- [DONE] DB-aware variant disambiguation: cleanup script now iterates ALL CAMeL analyses (not just top-ranked) and picks the one whose lex matches a lemma already in the DB. Eliminates false positives like سمك→سم (fish→poison) and غرفة→غرف (room→rooms) without needing a large hardcoded never-merge list (reduced from 22 entries to 2). Helper `find_best_db_match()` in morphology.py is reusable for other disambiguation tasks.
- Variant difficulty scheduling: if a specific variant form has a high miss rate (e.g., بنتي missed >50% of encounters), prefer sentences containing that variant to strengthen recognition
- [DONE] CAMeL Tools MLE disambiguator integrated: `get_best_lemma_mle()` in morphology.py, used by OCR pipeline. Single-word MLE for now; sentence-level disambiguation (passing full sentence context) is a future enhancement
- [DONE] Import pipeline improvement: all three import scripts (duolingo, wiktionary, avp_a1) now run CAMeL Tools variant detection as a post-import pass — new lemmas are checked against all existing DB lemmas, variants get `canonical_lemma_id` set immediately. Shared logic in `app/services/variant_detection.py`.

### OCR / Textbook Scanner (2026-02-09)
- [DONE] Gemini Vision OCR for Arabic text extraction from images
- [DONE] Textbook page scanning: upload photos of textbook pages, extract vocabulary words, import new lemmas, mark existing as seen with encounter count increment
- [DONE] Batch upload support: multiple pages at once with immediate response and background processing
- [DONE] Upload history view: list of batch uploads with per-page results (new/existing word counts), expandable to see individual words
- [DONE] Story OCR import: upload image of Arabic text in story import modal, extract text via Gemini Vision, populate text field for standard story import flow
- [DONE] Post-OCR variant detection: after importing new lemmas from textbook scans, run CAMeL Tools variant detection to catch possessives/inflected forms
- [DONE] OCR base_lemma fix: use CAMeL Tools base_lemma from Step 2 morphology for DB lookup (was being computed but ignored, causing conjugated forms to be imported as separate lemmas)
- [DONE] OCR prompt hardening: Step 1 now explicitly requests dictionary base forms, not conjugated/possessive forms
- [DONE] Leech identification script: `scripts/identify_leeches.py` finds high-review low-accuracy words with optional auto-suspend
- Leech auto-detection in FSRS: automatically flag words after N consecutive failures (beyond the current struggling-word re-intro cards)
- [DONE] Root validation guard: shared `is_valid_root()` in morphology.py rejects garbage roots (Latin letters, `#` placeholders, wrong length). Applied to all import paths (OCR, Wiktionary, backfill_roots). Cleanup script fixed 133 affected lemmas from prior OCR imports.
- [DONE] Auto-backfill root meanings: `backfill_root_meanings()` in morphology.py uses LLM to fill empty `core_meaning_en` on roots. Called automatically from all import paths after new root creation.
- OCR confidence scoring: have Gemini rate its confidence per word, flag low-confidence extractions for user review
- Textbook progress tracking: track which textbook/chapter pages have been scanned, show coverage progress
- OCR for handwritten Arabic: test Gemini Vision on handwritten notes (likely lower accuracy but worth exploring)
- Scan-to-story pipeline: detect whether a scanned page is vocabulary (extract words) or continuous text (extract as story) automatically
- Multi-page story scanning: scan multiple pages and stitch the extracted text together as one story

### Sentence Diversity & Corpus Quality (2026-02-11)
- [DONE] Scaffold freshness penalty: penalize sentences whose scaffold words are over-reviewed
- [DONE] Post-generation diversity rejection: deterministic rejection of sentences with overexposed scaffold words
- [DONE] Sentence retirement: soft-delete old low-diversity sentences via is_active flag
- [DONE] Starter diversity in LLM prompts: discourage هل-default and محمد overuse
- [DONE] ALWAYS_AVOID_NAMES: proper nouns always in avoid list
- [DONE] Sentence pipeline cap: due-date priority generation (most urgent first), TARGET_PIPELINE_SENTENCES=300, MIN_SENTENCES=2 per word. JIT-first strategy: MAX_ON_DEMAND=10/session generates with current vocabulary for better calibration. Pre-generated pool is warm cache, not primary source.
- Automatic periodic rebalancing: integrate retire_sentences logic into update_material.py as Step 0
- Sentence quality scoring dashboard: show diversity metrics on analytics page
- Corpus diversity entropy: track Shannon entropy of word distribution across sentences over time
- [DONE] Sentence length progression: dynamic difficulty via `get_sentence_difficulty_params()` — brand new 5-7 words, same-day 6-9, first week 8-11, established 11-14. Floor raised to min 5 words. material_generator + update_material use dynamic params instead of hardcoded "beginner".
- Context variety scoring: measure how many different sentence patterns each word appears in (not just count)
- Word pair co-occurrence tracking: detect word pairs that always appear together (e.g., كتاب+جميل) and actively break them apart
- LLM fine-tuning: collect rejected sentences as negative examples, use for prompt engineering or RLHF on sentence generation
- [DONE] Gemini Flash quality review gate: post-generation naturalness + translation accuracy check. Catches awkward, nonsensical, or mistranslated sentences before they reach users. Fail-closed since 2026-02-13 (rejects on Gemini unavailability). Integrated into both single-target and multi-target generation paths.

### Sentence Generation Pipeline Overhaul (2026-02-13)

#### Corpus-Based Sentence Sources
- Import Tatoeba Arabic-English pairs (~12.5K, CC-BY 2.0) as a sentence source — real human-written sentences matched against learner vocabulary
- Import BAREC graded sentences (69K, 19 readability levels, HuggingFace) — needs LLM diacritization + translation but provides difficulty-graded material
- FSI/DLI Arabic courses (public domain, US government) — structured learning content with English translations, thousands of sentences
- Hindawi E-Book Corpus (81.5M words, CC-BY 4.0) — children's literature subset for simpler material
- Efficient vocabulary matching: for any sentence source, classify tokens as known/function/unknown, apply ≥70% comprehensibility gate

#### Dormant Sentence Pool
- Don't discard LLM-generated sentences that fail vocabulary matching — store with `is_active=False` and periodically re-evaluate as vocabulary grows
- Same for corpus sentences: import ALL matching sentences at import time, mark dormant ones that don't yet meet comprehensibility gate
- As vocabulary grows, run a background job to "unlock" dormant sentences (flip is_active when ≥70% comprehensibility is reached)
- Track unlock rate: how many sentences become available per 100 new words learned?

#### Two-Pass "Generate Then Constrain" Strategy
- Research (SRS-Stories, EMNLP 2025) shows two-phase approach beats single-pass vocabulary-constrained generation
- Pass 1: Generate natural sentence with target word, NO vocabulary constraint
- Pass 2: Identify unknown words, ask LLM to rewrite replacing ONLY those words with known alternatives
- Preserves natural sentence structure from Pass 1 while achieving vocabulary compliance
- This is the single highest-impact change backed by academic evidence

#### OCR Textbook Sentence Extraction
- Modify OCR prompt to extract full sentences alongside individual words from textbook pages
- Textbook sentences are pedagogically designed to reuse vocabulary — high-quality bootstrap material
- Sentences need cleanup/diacritization after extraction but are inherently better calibrated than LLM-generated ones
- Could detect whether a scanned page is vocabulary (extract words) or exercise text (extract sentences) automatically

#### Story-to-Sentences Pipeline
- Generate LLM stories from word sets, then chop into individual review sentences
- Stories provide narrative coherence that isolated sentences lack (ref: networkedthought.substack.com "The Language Learning Holy Grail")
- Each story sentence becomes an independent review item while sharing a narrative thread
- Caveat: Storyfier (UIST 2023) found learners using generated stories performed worse at vocabulary recall — engaging plots may encourage reading for plot rather than deep word processing. Sentence-level review may be superior.

#### Vocabulary in LLM Prompts
- [DONE] Fix KNOWN_SAMPLE_SIZE mismatch: increased from 50 → 500. GPT-5.2 compliance jumped 57% → 88% with full vocab in benchmarking. See `research/sentence-investigation-2026-02-13/`.
- [DONE] POS-grouped vocabulary: organize known words by part of speech (NOUNS/VERBS/ADJECTIVES/OTHER). Scored 5.0/5 quality and 87% compliance in benchmarking. Implemented as `format_known_words_by_pos()` in llm.py.
- Scenario-based prompting: use existing `thematic_domain` data to add context hints ("at school", "at a restaurant") which naturally constrain vocabulary

#### Sentence Template Fallback
- Build ~30 Arabic syntactic templates (VSO, SVO, nominal) for deterministic sentence construction
- Use as fallback when LLM generation fails 3+ times for a word
- Templates like: `{SUBJ} {VERB} {OBJ} في {LOC}` filled from known vocabulary by POS
- 100% vocabulary compliance but lower naturalness — safety net, not primary approach
- Now that POS-grouped vocabulary is implemented, templates could leverage POS tags for slot filling
- Investigation report: `research/sentence-investigation-2026-02-13/recommendations.md`

#### Morphological Vocabulary Expansion
- Use CAMeL Tools Generator to expand known lemmas into all valid inflected forms before passing to LLM
- If learner knows root k-t-b, expand to: كتب، يكتب، كتاب، كتب، مكتبة، كاتب
- Dramatically increases usable vocabulary for the LLM while staying within "known" territory
- Already have `forms_json` on lemmas — this data is ready to use

#### Quality Gate Improvements
- [DONE] Change quality gate from fail-open to fail-closed (reject on Gemini unavailability instead of auto-pass) — implemented 2026-02-13
- Separate generation from translation: let LLM focus entirely on Arabic writing quality, translate in a cheap parallel Gemini Flash call
- Chain-of-thought sentence construction: guide LLM through explicit steps (pick scenario → choose pattern → select words → construct sentence)
- [DONE] Prompt overhaul: added explicit rules for indefinite noun starters, redundant pronouns, semantic coherence in compound sentences, beginner-level archaic word exclusion. Lowered temperature from 0.8 to 0.5. Reduced failure rate from 57% to ~10%.
- [DONE] Parallel on-demand generation: ThreadPoolExecutor(max_workers=8) for concurrent LLM calls during session building
- [DONE] Bulk sentence quality audit: `review_existing_sentences.py` script reviews all active sentences, retires failures. Supports --dry-run.
- [DONE] Cross-model quality review: switched quality gate from Gemini Flash self-review to Claude Haiku cross-model review. Self-review has blind spots (same model makes same mistakes). Benchmarked 3 models: Gemini 16%, Haiku strict 40%, Haiku relaxed 12.5%, GPT-5.2 97% (broken). Relaxed prompt focuses on grammar/translation errors, not scenario realism — avoids over-rejecting pedagogically valid sentences.

### Learning Algorithm Overhaul — Acquisition Phase & Focus Cohorts (2026-02-12)

#### Problem Statement
After importing ~100 textbook pages via OCR, 411 words entered the system with automatic rating=3 (Good). FSRS treated these as genuinely known (all 586 active words now show 30+ day stability), but actual review accuracy cratered to 25-46% on subsequent days. The system is spreading reviews across 586 words when the user barely recognizes most of them. 63% of active words have been seen only 0-2 times — well below the 8-12 meaningful encounters research says are needed for stable memory.

#### Acquisition Phase (Pre-FSRS Learning Steps)
- [DONE] Leitner 3-box acquisition system: Box 1 (4h), Box 2 (1d), Box 3 (3d). Two-phase advancement: box 1→2 always allowed (encoding), box 2+ gated on `acquisition_next_due` (consolidation). Graduate after box≥3 + times_seen≥5 + accuracy≥60% + reviews span ≥2 calendar days (GRADUATION_MIN_CALENDAR_DAYS=2). Implemented in `acquisition_service.py`.
- [DONE] Within-session repetition: acquisition words appearing only once get additional sentences. Implemented in `sentence_selector.py`.
- Research: FSRS S₀(Good) = 2.4 days, but a single "Good" for a textbook scan is NOT the same as genuine recall
- Research: "fewer than 6 spaced encounters → fewer than 30% recall after a week"

#### Focus Cohort System
- [DONE] MAX_COHORT_SIZE=100. Acquiring words always included, remaining filled by lowest-stability FSRS due words. Implemented in `cohort_service.py`, integrated into `sentence_selector.py build_session()`.
- Prevents the "spread too thin" problem where 586 words compete for ~100 reviews/day
- User said: "have a group of cards that we've consolidated... and then start adding more cards so that the group grows"

#### Session-Level Word Repetition
- [DONE] Within-session repetition: acquisition words get MIN_ACQUISITION_EXPOSURES=4 sentences each via multi-pass expanding intervals. Session expands up to MAX_ACQUISITION_EXTRA_SLOTS=15 extra cards.
- [DONE] Next-session recap endpoint: `POST /api/review/recap` returns sentence-level cards for last session's acquiring words (<24h ago). Frontend not yet implemented.
- [DONE] Wrap-up mini-quiz: `POST /api/review/wrap-up` returns word-level recall cards. Frontend not yet implemented.

#### Sentence Generation for Word Sets (Batch-Aware)
- Generate sentences targeting a SET of 3-5 focus words rather than individual target words
- "Create 10 sentences. Each must include at least 2 of these 5 focus words: X, Y, Z, W, V"
- This creates natural cross-reinforcement — seeing word A in a sentence with word B helps both
- More efficient than single-target generation (fewer LLM calls, more diverse sentences)
- The concept of "primary target word" becomes less important — what matters is the set of words the session is focusing on

#### OCR Import Options
- [DONE] **Import as encountered** (now the default): No FSRS card. ULK with knowledge_state="encountered" created. Words appear in Learn mode candidates with encountered_bonus=0.5.
- [DEFERRED] **Import as learned today**: Was the old behavior (FSRS card with Good rating). Removed because it inflated stability.
- [DEFERRED] **Import as learned N days ago**: FSRS card with backdated introduction. May add as option later.
- **Just track vocabulary**: Lemma entry only, no ULK at all. Purely for vocabulary tracking / readiness calculation. User decides later whether to learn.

#### Leech Auto-Management
- [DONE] Auto-suspend: times_seen≥5 AND accuracy<50% → suspend with `leech_suspended_at`. Implemented in `leech_service.py`.
- [DONE] Graduated reintroduction cooldowns: 3d (1st), 7d (2nd), 14d (3rd+) based on `leech_count`. Stats preserved on reintro (cumulative accuracy must genuinely improve). Fresh sentences + memory hooks generated.
- [DONE] Post-review single-word leech check: runs after every review with rating≤2.
- [DONE] Root-sibling interference guard: don't introduce words whose root siblings failed in last 7d.
- [DONE] `leech_count` tracking: incremented on each suspension, drives graduated cooldown delays.
- Track leech cycles: if a word is suspended and reintroduced 3+ times, flag for manual review (leech_count data now available)
- User said: "at that time I might have more hooks in my brain to connect it to, and it might stick better"

#### FSRS State Correction for OCR-Imported Words
- [DONE] `reset_ocr_cards.py`: Resets inflated FSRS cards from textbook_scan imports. 0 real reviews → reset to "encountered"; 1-2 with <50% accuracy → reset; 3+ → replay through FSRS. Supports --dry-run.
- [DONE] OCR import now creates ULK with knowledge_state="encountered" (no FSRS card, no submit_review). Words become Learn mode candidates.
- [DONE] Story completion creates "encountered" ULK for unknown words instead of FSRS cards.
- [DONE] `cleanup_review_pool.py`: Broader reset — ALL words with times_correct < 3 moved back to acquiring. Suspends junk via LLM. Retires incomprehensible sentences (<50% known).

#### Comprehensibility Gate & On-Demand Generation (2026-02-12)
- [DONE] Comprehensibility gate in sentence_selector: skip sentences where <70% of content words are known/learning/acquiring. Prevents showing unreadable sentences.
- [DONE] No word-only fallback cards: due words without sentences get on-demand generation or are skipped.
- [DONE] On-demand sentence generation: MAX_ON_DEMAND_PER_SESSION=10 synchronous LLM calls during session building. Uses current vocabulary for fresher, better-calibrated sentences than pre-generated pool.
- [DONE] Import quality gate: `import_quality.py` — LLM batch filter for junk words on import paths.
- [DONE] Variant→canonical review credit redirect: sentence reviews now credit the canonical lemma, not the variant. Variant forms tracked in variant_stats_json on canonical's ULK for diagnostics.
- [DONE] Deterministic variant ULK cleanup: suspend variant ULK records, merge stats into canonical. Replaces LLM-based junk detection which was incorrectly re-discovering variants.
- [DONE] Quality gate on all import paths: OCR, story import, Duolingo. Wiktionary/AVP skipped (no ULK created).
- [DONE] Fixed story_service variant detection: was calling detect functions without mark_variants().
- [DONE] Variant resolution in sentence_selector: sentences containing variant forms correctly cover canonical due words.
- Variant-aware statistics display: show aggregated stats across all variant forms on the word detail page. "You've seen this word as: كتاب (5x), الكتاب (3x), كتابي (1x)"
- Adaptive comprehensibility threshold: start at 70%, increase to 80% as vocabulary grows. Early learners need more i+1, advanced need less scaffolding.
- Sentence regeneration trigger: when cleanup retires many sentences, auto-regenerate for words below MIN_SENTENCES=2.
- Pre-warm sentence cache: after cleanup, generate sentences for all active words in background (not during session building).

#### Topical Learning Cycles (Phase 4)
- [DONE] Group words by thematic domain (food, family, school, etc.) and cycle through topics
- [DONE] Each cycle focuses on one domain: introduce up to 15 words (MAX_TOPIC_BATCH), auto-advance when exhausted/depleted (MIN_TOPIC_WORDS=5)
- [DONE] Prevents mixing too many unrelated words (cognitive interference)
- [DONE] Uses existing `thematic_domain` on lemmas from `backfill_themes.py` — 20 domains, all 1610 lemmas tagged
- [DONE] LearnerSettings singleton table, topic_service.py, domain filtering in word_selector, settings API + frontend topic display
- Could auto-select next topic based on story readiness or user preference

#### Story Difficulty Display + Suspend/Activate (Phase 5)
- Show estimated difficulty level on story list cards
- [DONE] Allow suspend/reactivate of stories: toggle via pause/play button on story cards, also available in story reader. Suspended stories appear dimmed with "Suspended" badge. POST /api/stories/{id}/suspend toggles between active↔suspended.
- Story difficulty auto-selection: pick stories where readiness is 85-95%
- Link to story from word detail page when word was encountered in a story but missed

#### Themed Sentence Generation (Phase 6)
- Generate sentences targeting a SET of 3-5 thematically related words rather than individual targets
- "Create 10 sentences about food. Each must include at least 2 of these 5 focus words: X, Y, Z, W, V"
- Natural cross-reinforcement — seeing word A in context with thematically related word B helps both
- More efficient than single-target generation (fewer LLM calls, more diverse sentences)

#### Story Link on Word Detail When Missed
- When a word was encountered in a story and later missed in review, show a link back to the story on the word detail page
- Helps learner reconnect with the original context where they first saw the word
- Uses existing `source_story_id` on Lemma model

#### A/B Testing Framework (Single-Subject)
- Research says: n-of-1 trials need ~400 observations per condition, 4-5 crossover periods, linear regression with AR(1) covariance
- With ~100-200 reviews/day, need 2-4 weeks per experiment
- Design: assign words randomly to condition A/B at introduction. Track recall at days 1, 3, 7, 14.
- First experiment idea: "Acquisition phase with 3x in-session repetition" (A) vs "standard FSRS scheduling" (B)
- Track: accuracy at day 1, day 3, day 7, day 14. If A shows >15% better retention at day 7, adopt.
- Implementation: add `experiment_group` field to ULK, log experiment assignment in interaction logs
- Caveat: interference between groups (seeing word from group A might help group B word from same root)

#### Sparkline Enhancement: Show Inter-Review Gaps
- [DONE] Backend returns `last_review_gaps` (hours between consecutive reviews). Frontend sparkline uses variable gap widths: <1h→1px, same-day→2px, 1-3d→4px, 3-7d→6px, >7d→9px. Clustered dots = cramming, spread dots = real spacing.
- User said: "it doesn't say anything about the gap between attempts"

#### Response Time as Signal
- Already capturing response_ms in ReviewLog — never used for scheduling
- Slow response on a "correct" answer may indicate fragile knowledge
- Decreasing response time across reviews = fluency signal
- Could use as secondary input to FSRS difficulty parameter or to decide acquisition graduation

#### Session Design for Variable Practice Time
- User has unpredictable practice time (5 min to 2 hours)
- Sessions should be designed as "micro-completable units" — every 2-3 cards is a meaningful chunk
- Front-load the most important reviews (acquisition words, lapsed words)
- If user only does 2 cards, they should be the 2 most valuable cards possible
- Longer sessions can include more review/consolidation items and new word introductions

### Ideas from Arabic Learning Research Deep Dive (2026-02-12)

#### Coverage-Based Progress Tracking
- Show user their estimated text coverage % based on Masrai & Milton (2016) curves: 1K lemmas = 79%, 5K = 89%, 9K = 95%
- This is more meaningful than raw word count ("you can read 89% of any Arabic text" vs "you know 5,000 words")
- Track separately for different registers (news, literary, religious) if frequency data supports it

#### AVP A1 Curriculum Integration
- Import Arabic Vocabulary Profile A1 list (1,750 items, expert-validated by 71 teachers) as reference curriculum
- Cross-reference against current word list to identify A1 gaps
- Show A1 completion percentage as a milestone metric
- AVP uses multi-dialectal cross-checking which helps select vocabulary that transfers across dialects

#### Root-Aware FSRS Stability Boost
- Research: learners rely on roots in 87.5% of encounters with unknown words
- When a new word shares a root with 2+ known words, boost initial FSRS stability by ~30%
- Root familiarity at 30-60% coverage is the sweet spot for introducing new root family members
- Research: root awareness accounts for substantial variance in reading outcomes (Cambridge study)
- The 500 most productive roots cover 80% of daily vocabulary -- prioritize these

#### OSMAN Readability Integration
- Available via `pip install textstat` → `textstat.osman(text)`. Low effort but limited value for short sentences (5-14 words) — primarily measures word-level complexity (syllable count, Faseeh markers, long words), which we already control via max_words and LLM difficulty hints.
- OSMAN is Arabic-specific, accounts for syllable types, works with/without diacritics, validated on 73K parallel sentences
- Combined difficulty = OSMAN score + unknown word density + morphological density
- Open source: github.com/drelhaj/OsmanReadability

#### Pre-Listening Vocabulary Flash
- Research (Elkhafaifi 2005): prelistening activities significantly improve listening comprehension
- Question preview > vocabulary preview > nothing (all significantly different)
- Before playing audio, show 2-3 key vocabulary words from the sentence as a preview
- Also: repeated listening is effective -- encourage replay before revealing text

#### Verb Form Progression Gating
- Form I accounts for ~60-70% of all verb usage in MSA
- Recommended learning order: Form I -> II, IV -> V, VIII -> X, III -> VI, VII -> IX
- Gate derived form introduction on Form I mastery (70%+ accuracy on Form I reviews)
- Form IX is effectively optional (colors/defects only, <0.5% of usage)
- Track verb form distribution in user's vocabulary as an analytics metric

#### English Loanwords in Arabic as Easy Wins
- Modern Arabic has many recognizable loanwords: computer, internet, television, film, democracy
- These are immediately recognizable through script and can accelerate early learning
- Flag these in the UI with a "loanword" badge to boost learner confidence
- Low priority for Arabic->English direction cognates (script barrier + semantic drift make them less useful)

#### Diacritics Strategy Validation
- Midhwah (2020, Modern Language Journal): VT groups outperformed UVT across ALL proficiency levels
- Abu-Rabia: diacritics improve comprehension for native speakers of all ages and skill levels
- No evidence that early diacritics hinder later reading of unvowelized text
- Current "always show diacritics" approach is strongly research-validated
- Future: optional "reading challenge" mode without diacritics as a separate exercise (not default)

#### Narrow Reading for Vocabulary Recycling
- Research supports "narrow reading" (multiple texts on same topic) for vocabulary consolidation
- Arabic MSA-to-dialect overlap: Levantine 63%, Gulf 55-60%, Egyptian 50-55%, Moroccan 33-40%
- Topic-based story generation would naturally recycle domain vocabulary
- Could track vocabulary "domain coverage" (e.g., 85% of food vocabulary, 40% of politics)

#### Listening Anxiety Mitigation
- Elkhafaifi (2005): listening anxiety and FL learning anxiety are separate but related, both correlate negatively with achievement
- Listening practice should be low-stakes and scaffolded
- Slow speech mode (0.7x) + learner pauses aligns with research
- Consider a "listening confidence" metric visible to user to track progress and reduce anxiety

#### Arabic-Specific Sentence Difficulty Model
- Beyond unknown word count, Arabic sentence difficulty depends on:
  - Morphological density (how many clitics/affixes per word)
  - Root familiarity of unknown words (known root = easier)
  - Verb form complexity (Form I easier than Form X)
  - Sentence length (eye-tracking shows fixation per content word)
- Weight these factors in sentence selection algorithm
- Research: morphological density impacts reading comprehension independently of vocabulary coverage

#### INN University Arabic Heritage Language Research
- Jonas Yassin Iversen (Professor) and Lana Amro (PhD candidate) at INN Hamar research Arabic heritage language education in Scandinavia
- Key finding: Norwegian supplementary (weekend school) model leads Arabic students to **hide their language learning from peers**, while Swedish mainstream integration fosters pride — relevant to Alif as a private self-directed tool
- Translanguaging (using L1+L2 together) validated as productive pedagogy in digital Arabic education — supports our English glosses + transliteration approach
- Amro's PhD specifically studies digital Arabic language learning with translanguaging
- DIALOGUES Erasmus+ project (2025–2027) on languages, literacies, and learning in a digital age
- **Full writeup**: [`research/inn-arabic-heritage-language.md`](research/inn-arabic-heritage-language.md)

#### 19-Level Readability Corpus (BAREC)
- BAREC (ACL 2025): 69K sentences, 19 readability levels, CC-BY-SA. Pilot study (2024, 10.6K segments) evolved into this.
- Investigated 2026-02-12: 28.8K sentences in 5-14w target range, but only ~50% diacritized (density 0.176 vs 0.8 for full). Levels 1-3 are mostly junk (headers, fragments). Usable diacritized subset: ~3,700 sentences at levels 5-10.
- Sources: Emarati curriculum, Hindawi literature, Majed magazine, Wikipedia, religious texts.
- Not practical as drop-in sentence source (needs diacritization + has context-dependent excerpts), but useful for difficulty calibration.
- BERT-based models available for automatic readability assessment (87.5% QWK from BAREC shared task 2025)
- HuggingFace: CAMeL-Lab/BAREC-Shared-Task-2025-sent
