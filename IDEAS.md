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
- Function words (في، من، على، و، ال) treated as always-known
- Sentence templates for quick generation: "the X is Y", "I went to the X"
- Pre-generate and cache validated sentences for offline use

### Sentence Sources
- LLM-generated sentences with vocabulary constraints
- Tatoeba corpus (8.5M Arabic-English pairs, CC BY 2.0)
- BAREC corpus (69K sentences across 19 readability levels)
- Quran (Tanzil corpus) — gold-standard diacritized text
- News articles segmented into sentences

### Difficulty Assessment
- SAMER lexicon: 40K lemmas with 5-level readability scale
- BAREC: 19-level sentence difficulty
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
- Future: add "Never show this sentence again" action to retire specific sentences from review
- Future: "Report pronunciation" to flag TTS audio quality issues
- Future: "Add personal note" per word/sentence for custom mnemonics

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

### Algorithm Optimization
- Use logged data to tune FSRS parameters per-user
- Identify words that are consistently hard → provide extra context/examples
- Detect if difficulty ratings are miscalibrated
- A/B test different presentation modes (with logging data)
- **Response time as difficulty signal**: response_ms is already captured for reading/listening reviews (stored in ReviewLog + SentenceReviewLog + JSONL logs) but never used. Possible uses: slow response → word is harder (could influence FSRS scheduling or sentence selection), decreasing response time over repeated reviews of same word = fluency/acquisition signal, analytics dashboard showing time-per-card trends. Caveat: response time is noisy (distracted vs. genuinely struggling), best as supplementary signal alongside ratings.
- [DONE] **Learn mode quiz timing gap**: `frontend/app/learn.tsx` hardcoded `response_ms: 0` for quiz reviews — fixed with `quizStartTime` ref that measures actual elapsed ms

---

## Technical Ideas

### Offline Architecture
- All review data in IndexedDB (web) / SQLite (mobile)
- Pre-sync: download next N days of review cards + sentences + audio
- Background sync when online: upload logs, download new content
- Service worker for web PWA caching
- Expo offline-first with AsyncStorage or expo-sqlite
- [DONE] Clear Cache button in More screen: flushes sessions, word lookups, stats, analytics from AsyncStorage

### Deployment
- Backend: Hetzner CAX11 ARM + Coolify (~$4/mo), git-push deploys
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
- Pre-load ~200 essential function words (particles, prepositions, conjunctions, demonstratives, pronouns)
- Teach function words in Phase 1 before any content words
- Exclude function words from "unknown word" count in sentence validation
- Function words should be marked in data model with is_function_word flag

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
- Graded text mode supporting 95-98% vocabulary coverage for extensive reading
- Narrow reading: offer multiple texts on the same topic to recycle vocabulary
- Story series with recurring characters/themes for context building
- Three-stage listening reveal: audio only -> Arabic text -> English translation
- Story difficulty auto-selection: pick stories where readiness is 85-95% for optimal learning
- Story audio: TTS for full story, sentence-by-sentence playback with highlighting
- Story sharing: export stories as formatted PDF with glossary of unknown words

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
- Automatic periodic rebalancing: integrate retire_sentences logic into update_material.py as Step 0
- Sentence quality scoring dashboard: show diversity metrics on analytics page
- Corpus diversity entropy: track Shannon entropy of word distribution across sentences over time
- Sentence length progression: as vocabulary grows, generate longer sentences (currently capped at 4-12 by word age)
- Context variety scoring: measure how many different sentence patterns each word appears in (not just count)
- Word pair co-occurrence tracking: detect word pairs that always appear together (e.g., كتاب+جميل) and actively break them apart
- LLM fine-tuning: collect rejected sentences as negative examples, use for prompt engineering or RLHF on sentence generation
