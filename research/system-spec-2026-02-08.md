# Alif System Specification

Status as of 2026-02-08. Written for evaluation of first real-world usage.

---

## 1. What Exists

### 1.1 Data State (Production)

| Table | Count | Notes |
|-------|-------|-------|
| roots | 23 | Hardcoded mapping from Duolingo import |
| lemmas | 196 | Filtered from 302 Duolingo lexemes (removed names, phrases) |
| user_lemma_knowledge | 196 | All state=learning, source=duolingo, times_seen=0 |
| sentences | 239 | All source=llm, 0 with audio, 0 ever shown |
| sentence_words | 1226 (~1691 total, 549 with null lemma_id) | Token mapping for sentences |
| review_log | 0 | No reviews yet |
| sentence_review_log | 0 | No reviews yet |
| grammar_features | 0 | **NOT SEEDED** - grammar tracking is non-functional |
| user_grammar_exposure | 0 | Empty |

**Sentence coverage**: 101/196 lemmas have sentences (avg 3.3 per lemma). 95 lemmas have NO sentences and will appear as word-only fallback cards.

**All 196 words are due now.** This is the cold-start scenario -- every word has an FSRS card created at import time, all immediately due.

### 1.2 Backend Services

#### FSRS Spaced Repetition (`fsrs_service.py`)
- Uses py-fsrs library with default parameters (no per-user optimization)
- Rating map: 1=Again, 2=Hard, 3=Good, 4=Easy
- State transitions: New -> Learning -> Review(known) -> Relearning(lapsed)
- Tracks: times_seen, times_correct, last_reviewed, fsrs_card_json
- **Assumption**: FSRS default parameters are reasonable for Arabic vocabulary. No data to tune yet.

#### Sentence Selector (`sentence_selector.py`)
- **Algorithm**: Greedy set cover maximizing due-word coverage per sentence
- **Scoring**: `(due_words_covered ^ 1.5) * difficulty_match_quality`
- **Difficulty matching**: Penalizes sentences where scaffold words are unstable (< 0.5 days stability for weakest due word < 0.5 stability)
- **Session ordering**: Easy bookends, hardest in middle (by min due-word stability)
- **Fallback**: Words without sentences get word-only cards appended to fill the session
- **Cooldown**: Sentences not reshown within 7 days (configurable)
- **Bug fixed today**: Cards with `stability: null` in FSRS JSON caused TypeError

#### Sentence Review Service (`sentence_review_service.py`)
- **Multi-word FSRS credit**: When reviewing a sentence, ALL words with FSRS cards get rated
  - "understood" -> all words get rating=3 (Good)
  - "partial" + missed_lemma_ids -> missed words get rating=1 (Again), rest get rating=3
  - "no_idea" -> all words get rating=1
- **Credit types**: primary (the target word), collateral (other words in the sentence)
- **Encounter tracking**: Words without FSRS cards get `total_encounters += 1`
- **Sentence tracking**: Updates `times_shown`, `last_shown_at`; logs to `sentence_review_log`

#### Word Selector (`word_selector.py`)
- **4-factor scoring**: frequency(40%) + root_familiarity(30%) + recency_bonus(20%) + grammar_pattern(10%)
- `freq_score = 1.0 / log2(rank + 2)` -- log scale, higher for frequent words
- `root_familiarity`: peaks when ~30-60% of root family is known (encourages root clustering)
- `recency_bonus = 0.2` when a sibling was introduced 1-3 days ago
- `grammar_pattern_score`: favors words with unlocked but low-comfort grammar features
- **Max 5 new words per session** (constant `MAX_NEW_PER_SESSION`)
- **Note**: Since all 196 words are already "introduced" (have ULK records), this won't recommend new words until the user finishes reviewing

#### Sentence Generator (`sentence_generator.py`)
- LLM generation with retry (max 3 attempts)
- Validation: checks target word present, all other words known
- Sends known_words sample (up to 50) in prompt
- Logs each attempt to JSONL

#### Sentence Validator (`sentence_validator.py`)
- Rule-based clitic stripping: 10 proclitics, 10 enclitics
- Taa marbuta handling (ة -> ت before suffixes)
- Alef normalization (أ/إ/آ -> ا)
- Function word set (~60 common particles/pronouns)
- **Known limitation**: Rule-based, not morphologically aware. Some conjugated forms won't match.

#### TTS (`tts.py` + router)
- ElevenLabs REST API, model `eleven_turbo_v2_5`
- Default voice: "Chaouki" (MSA male, clear accent), speed=0.8
- SHA256 content-hash caching to `data/audio/`
- **On-demand endpoint**: `GET /api/tts/speak/{text}` generates+caches and returns audio
- **Logging**: Every TTS request logged with `text_length`, `cache_hit`, `latency_ms`, `success`, `error`
- **Current state**: 0 audio files cached. ElevenLabs returns **402 (payment_required)** for library voices on the current free plan. Listening mode falls back to 2-second timer until account is upgraded.

#### LLM Service (`llm.py`)
- LiteLLM wrapper: Gemini Flash -> GPT-5.2 -> Claude Haiku fallback chain
- JSON mode with markdown fence stripping
- All calls logged to `llm_calls_YYYY-MM-DD.jsonl`

#### Grammar Service (`grammar_service.py`)
- 24 features across 5 categories (number, gender, verb_tense, verb_form, syntax)
- 5-tier progression system with comfort scoring
- Comfort formula: `(exposure + accuracy) * decay` where decay has 30-day half-life
- **NON-FUNCTIONAL**: Grammar features table is empty (seed not run on production). Grammar pattern scoring returns 0.1 for all words.

### 1.3 Frontend (Expo React Native)

#### Review Screen (`index.tsx`) -- PRIMARY
- **Sentence-first mode**: Calls `/api/review/next-sentences?limit=10&mode={reading|listening}`
- **Reading flow**: See Arabic sentence -> "Show Answer" -> tap missed words -> rate (Got it / Continue / No idea)
- **Listening flow**: Audio plays -> "Reveal Arabic" -> tap missed -> "Show Translation" -> rate
- **TTS**: Uses `expo-av`, URL from `audio_url` field or generates via `/api/tts/speak/{text}`
- **Fallback**: If no sentence session, falls back to legacy word-only cards
- **Session tracking**: Client generates session_id, tracks response_ms per card
- **Results**: Shows summary after session (total, got it, missed, no idea counts)

#### Learn Screen (`learn.tsx`)
- 5 phases: loading -> pick -> intro -> quiz -> done
- Fetches 5 word candidates, shows Arabic + English + root info
- Tap to introduce (creates FSRS card, shows root family)
- Optional quiz after introducing words
- **Note**: All 196 words already have FSRS cards, so `/api/learn/next-words` will return empty until there are unintroduced words.

#### Words Screen (`words.tsx`)
- Browse all 200 words with search + filter (All/New/Learning/Known)
- Tap word -> detail view with root family, review stats

#### Stats Screen (`stats.tsx`)
- CEFR level estimate, learning pace, daily activity chart
- All zeros initially until reviews happen

### 1.4 Interaction Logging

**What IS logged (JSONL to `data/logs/interactions_YYYY-MM-DD.jsonl`):**

| Event | Fields | When |
|-------|--------|------|
| `session_start` | session_id, review_mode, total_due_words, covered_due_words, sentence_count, fallback_count | When session is built |
| `sentence_selected` | session_id, sentence_id, selection_order, score, due_words_covered, remaining_due | Each sentence selected by greedy cover |
| `review` | lemma_id, rating, response_ms, session_id, review_mode, comprehension_signal | Legacy single-word review |
| `sentence_review` | sentence_id, lemma_id, comprehension_signal, missed_lemma_ids, response_ms, session_id, review_mode, words_reviewed, collateral_count | Sentence-level review submission |
| `tts_request` | text_length, cache_hit, latency_ms, success, error | Every TTS audio generation attempt |
| `word_introduced` | lemma_id | When a word is introduced via Learn screen |

**What is NOT logged:**
- Session end/complete events (can be inferred: count `sentence_review` events per session_id vs items in `session_start`)
- Learn screen browsing (which candidates were shown, which were skipped)
- Stats/words screen visits
- Frontend errors
- Card-level timing (only response_ms per submission, not time-to-first-tap or think-time)
- Which words were tapped as "missed" vs which were just not tapped

**Also logged separately:**
- `llm_calls_YYYY-MM-DD.jsonl`: Every LLM API call with model, success, timing
- `sentence_gen_YYYY-MM-DD.jsonl`: Sentence generation attempts with validation results

### 1.5 Database Logging (in addition to JSONL)

| Table | What it captures |
|-------|-----------------|
| `review_log` | Every word-level FSRS review: lemma_id, rating, response_ms, session_id, review_mode, comprehension_signal, sentence_id, credit_type, fsrs_log_json |
| `sentence_review_log` | Every sentence-level review: sentence_id, session_id, comprehension, response_ms, review_mode |
| `user_lemma_knowledge` | Running state: knowledge_state, times_seen, times_correct, total_encounters, last_reviewed |

---

## 2. Algorithms vs Research Alignment

### What Aligns Well

| Research Recommendation | Implementation Status |
|------------------------|----------------------|
| **i+1 (one unknown word per sentence)** | Sentence generator validates exactly 1 target word, all others known |
| **FSRS over SM-2** | Using py-fsrs with full scheduling |
| **Root-based learning** | Root family display on introduce, root_familiarity_score in word selector |
| **Easy bookends in sessions** | `_order_session()` puts easiest first and second, hardest in middle |
| **Collateral credit** | All words in a reviewed sentence get FSRS updates, not just the target |
| **Multiple sentences per word** | Avg 3.3 sentences per lemma for context diversity |
| **Sentence difficulty scaling** | `difficulty_match_quality` penalizes fragile scaffolds; `get_sentence_difficulty_params` scales max_words by word maturity |
| **Function words as always-known** | ~60 function words exempted from validation |
| **Full diacritization** | Always shown on all Arabic text |
| **Binary comprehension signal** | understood/partial/no_idea maps cleanly to FSRS ratings |
| **Max 5 new words per session** | Aligns with research recommendation of 5 for Arabic (Cowan's 3-5 chunks) |

### Partially Aligned (Implemented but Weaker Than Research Recommends)

| Research Recommendation | Gap |
|------------------------|-----|
| **Scaffold stability > 14 days** | Thresholds are much lower (0.5 and 3.0 days). Research says scaffold words should be genuinely well-known, not just recently seen. For cold-start (all words at stability 0), this is moot -- everything is equally fragile. |
| **Flashcard-first then sentences** | Learn screen does flashcard intro, but the Review screen shows sentences directly. There's no "flashcard phase" in the review flow. |
| **No two new items back-to-back** | The system is sentence-based, not word-based sessions. No explicit interleaving of new vs review sentences. |
| **Encounter tracking** | `total_encounters` field exists but is only incremented for words without FSRS cards. Collateral credit updates FSRS but doesn't increment a separate encounter counter. |
| **Context diversity rotation** | Sentences have `times_shown` and 7-day cooldown, but there's no explicit "show least-shown sentence" logic. |

### Not Implemented (Research Strongly Recommends)

| Research Recommendation | Status |
|------------------------|--------|
| **Adaptive session pacing** (stop new introductions when accuracy < 75%) | Not implemented. No rolling accuracy tracking. |
| **Within-session spacing for failed items** (re-show after 5-10 intervening items) | Not implemented. Failed items just get FSRS rating=1. |
| **New:review ratio enforcement** (1:4 to 1:6) | Not tracked. The greedy set cover doesn't distinguish new vs review words. |
| **Session time limit** (20 min default, no new items in overtime) | Not implemented. |
| **Response time as overload signal** | Logged but not acted upon. |
| **Semantic clustering avoidance** | Not implemented. |
| **Grammar-aware sentence selection** | Grammar features not seeded, so not functional. |
| **Expertise reversal** (reduce scaffolding as words mature) | Not implemented. Same UI for all words. |
| **Fluency mode** (all-known material for speed practice) | Not implemented. |

---

## 3. Known Issues for Tonight's Usage

1. **Grammar features not seeded**: The `grammar_features` table is empty. Grammar pattern scoring returns 0.1 for all words. This doesn't break anything but means grammar-aware features are inert.

2. **ElevenLabs 402 (payment required)**: The free plan cannot use library voices via API. Listening mode falls back to a 2-second timer instead of real audio. All 239 sentences have `audio_url = NULL`. The code path for on-demand generation (`/api/tts/speak/{text}`) is implemented and will work once the account is upgraded.

3. **Cold start: all 196 words due simultaneously**: The sentence selector will build sessions covering ~10-13 due words per session out of 196 total. You'll need ~15-20 sessions to cycle through all words once. Each session shows 10 items.

4. **95 words have no sentences**: These appear as word-only fallback cards (just the Arabic word, no sentence context). The session builder appends these after sentence items to fill the session limit.

5. **549/1691 sentence_words have NULL lemma_id**: These are function words or unmatched tokens. They display as-is but can't be tapped for "missed" tracking since there's no lemma to rate.

6. **Learn screen won't work**: All 196 words already have ULK records, so `/api/learn/next-words` returns empty. The Learn tab will show "no words to learn."
