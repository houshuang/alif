# Review Modes

## Sentence-First Review (primary mode)
Reviews are sentence-centric: greedy set cover selects sentences that maximize due-word coverage per session.
1. `GET /api/review/next-sentences` assembles session via 6-stage pipeline (fetch due → candidate sentences → comprehension-aware recency filter → greedy set cover → easy/hard ordering → fallback word-only cards)
2. **Every word in a reviewed sentence gets a full FSRS card** — including previously unseen words. No more encounter-only tracking. Words without existing FSRS cards get auto-created knowledge records.
3. Ternary ratings: understood (rating=3 for all) / partial (tap to cycle: confused=rating 2 Hard, missed=rating 1 Again, rest=3) / no_idea (rating=1 for all)
4. **All words reviewed equally**: every word in the sentence gets an FSRS review based on the user's marking. The scheduling reason for selecting the sentence is irrelevant — unmarked words get rating=3, just like completing a story or scanning a textbook page. The `credit_type` field (primary/collateral) in review_log is purely metadata tracking which word triggered sentence selection; it does NOT affect ratings.
5. Falls back to word-only cards when no sentences available for uncovered due words
6. **Comprehension-aware recency**: sentences repeat based on last comprehension — understood: 7 day cooldown, partial: 2 day cooldown, no_idea: 4 hour cooldown
7. Inline intro candidates: up to 2 new words suggested at positions 4 and 8 in reading sessions (gated by 75% accuracy over last 20 reviews). **Not auto-introduced** — candidates are returned to the frontend for user to accept via Learn mode. No intro candidates in listening mode.

### Pre-Sentence Cards

Before sentences begin, the session may include informational cards (no FSRS review — pure re-exposure):

1. **Grammar lesson cards** — if the session contains a grammar pattern the user hasn't seen
2. **Reintro cards** — for struggling words (seen 3+ times, never recalled). Info-dense layout: large Arabic + English + transliteration hero, flow chips (POS, clickable root chip → `/root/{id}`, clickable pattern chip → `/pattern/{wazn}`), FormsStrip, etymology, memory hook mnemonic, root family with "View all" link. Single "Continue" button — no self-assessment. Backend: `_build_reintro_cards()`, max `MAX_REINTRO_PER_SESSION` (3).
3. **Experiment intro cards** — A/B test (active 2026-03). Words in the `intro_ab_card` group get an info card before their first-ever sentence review. Same layout as reintro cards. Logged via `POST /api/review/experiment-intro-ack`. Backend: `_build_experiment_intro_cards()`, built in `_with_fallbacks()` after all sentence selection — only for words actually covered by session sentences (not all due words).

Cards are shown in the order above, then sentences begin.

### Post-Session Cards

After all sentences are reviewed:

1. **Wrap-up cards** — mini-quiz for acquiring + missed words. Info-dense layout matching reintro cards: Arabic on front, then reveals English + flow chips (POS, clickable root → `/root/{id}`, clickable pattern → `/pattern/{wazn}`), FormsStrip with transliterations, etymology derivation, memory hook mnemonic. Got it / Missed buttons. Backend: `POST /api/review/wrap-up`, response includes `root_id`, `root_family`, `forms_translit`, `pattern_examples`.

## Reading Mode
1. User sees Arabic sentence (diacritized, large RTL text)
2. **Front phase**: user can tap non-function words to look them up (calls GET /api/review/word-lookup/{lemma_id}). Tapped words auto-marked as missed.
3. **Lookup panel** (WordInfoCard): Shows root (clickable → `/root/{id}`), root meaning, forms strip with per-form ALA-LC transliteration, surface form transliteration for conjugated forms, pattern link (clickable → `/pattern/{wazn}`), full etymology (derivation, loanwords, cultural note), full memory hooks (mnemonic, cognates, collocations, usage context, fun fact). Known root siblings shown as tappable pills → `/word/{id}`. If root has 2+ known siblings → prediction mode ("You know words from this root: X, Y. Can you guess the meaning?") before revealing English. Otherwise shows meaning immediately. Scrollable when content overflows.
4. **Front-phase actions**: "Know All" (understood) / "No idea" (no_idea) / "Show Translation" (reveal back). If any word tapped, "Know All" changes to "Continue" (partial).
5. **Back phase**: triple-tap words to cycle state: off → missed (red, rating 1 Again) → confused (yellow, rating 2 Hard) → off. Builds missed_lemma_ids + confused_lemma_ids. **Confusion analysis**: when a word transitions to yellow ("did not recognize"), `GET /api/review/confusion-help/{lemma_id}` fires asynchronously. Returns up to four analysis layers: (a) morphological decomposition (color-band clitics + stem + form label), (b) visually similar words (edit distance + rasm skeleton, orange "Easily confused" card), (c) phonetically similar words (emphatic/pharyngeal confusion, purple "Sounds similar" card with confused-pair pills like ص≈س), (d) prefix disambiguation hint (green "part of root" or blue "is prefix" card for words starting with و/ف/ب/ل/ك). All analysis includes encountered words in the similarity pool.
6. **Back-phase actions**: "Know All" (understood) / "Continue" (partial, if words marked) / "No idea" (no_idea)
7. **Back/Undo**: after submitting, can go back to previous card — undoes the review (restores pre-review FSRS state via backend undo endpoint), removes from sync queue if not yet flushed, restores word markings

## Listening Mode (real TTS via expo-av)
1. Audio plays via ElevenLabs TTS (speed 0.7x, multilingual_v2 model)
2. Tap to reveal Arabic text — tap words you didn't catch
3. Tap to reveal English translation + transliteration
4. Rate comprehension
5. Listening-ready filter: non-due words must have times_seen ≥ 3 AND FSRS stability ≥ 7 days

## Learn Mode
1. **Pick phase**: Shows 5 candidate words one at a time with info-dense scrollable card. **Hero section**: large Arabic word, English gloss, transliteration, flow chips (POS, CEFR level, frequency rank, tappable root chip → `/root/{id}`, tappable pattern chip → `/pattern/{wazn}`), FormsStrip with transliterations. **Info sections** (scrollable, only shown if data exists): memory hook (mnemonic), etymology (derivation + pattern), cross-language cognates, root family with "View all" link, pattern examples (up to 4 sibling words with knowledge-state dots) with "View all" link, usage context, fun fact/cultural note. No play button (word-level TTS disabled).
2. Actions per word: Learn (introduces, starts acquisition), Skip, Never show (suspend) — fixed at bottom
3. Selection algorithm: 40% frequency + 30% root familiarity (peaks at 30-60% of root known) + 20% recency bonus (sibling introduced 1-3 days ago) + 10% grammar pattern coverage + encountered bonus (0.5 for words seen in textbook/story)
4. **Done phase**: Shows count introduced, CEFR level. No quiz — words get proper reviews through sentence-first review.

## Story Mode
1. **Generate**: LLM generates micro-fiction (2-12 sentences) using known vocabulary, random genre
2. **Import**: Paste any Arabic text → morphological analysis + LLM batch translation creates Lemma entries for unknown words (`source="story_import"`, `source_story_id` set). Proper nouns (personal/place names) detected by LLM are marked as function words with `name_type` instead of creating Lemma entries. Unknown vocab words become Learn mode candidates with `story_bonus` priority. No ULK created — Learn mode handles introduction.
3. **Reader**: Word-by-word Arabic with tap-to-lookup (shows gloss, transliteration, root, POS). Arabic/English tab toggle. Actions at end of scroll (not fixed bottom bar).
4. **Completion flow**: Complete (creates "encountered" ULK for unknown words — no FSRS card; submits real review only for words with active FSRS cards), Skip (only rates looked-up words), Too Difficult (same as skip)
5. **List view**: Cards with readiness indicators (green ≤3 unknown, orange, red), generate + import modals

## Quran Reading Mode
Verse-by-verse Quran reading interleaved with review sessions. Not a separate mode — verse cards appear among regular sentence cards with a distinct gold-accented design.

1. **Front**: Arabic verse (Uthmani tashkeel, Scheherazade font) pinned at top. Attribution bar shows surah name + verse number. Optional tap-to-lookup on individual words (shows lemma, gloss, root chip, POS badge). "Show Translation" button.
2. **Back**: Arabic stays visible, English translation (Sahih International) appears below gold divider. Looked-up words shown as summary pills. Three rating buttons: "Got it" (green) / "Partially" (amber) / "Not yet" (red).
3. **SRS**: Simple level-based (not FSRS). Level 0=unseen, 1-7=learning, 8=graduated. "Got it" advances level with intervals: 4h→12h→1d→3d→7d→21d→graduated. "Partially" drops 1 level, due in 2h. "Not yet" resets to level 1, due immediately.
4. **Gating**: 3 new verses per session. Only introduced when non-understood backlog < 20. Only lemmatized verses can be introduced (ensures word data available for lookup).
5. **Lemmatization**: Lazy — `lemmatize_quran_verses()` processes next 20 verses when <10 lemmatized unseen remain. Uses existing pipeline (tokenize → lemma lookup → CAMeL fallback → LLM batch translation for unknowns).
6. **Pipeline protection**: Quran-only lemmas created with `source="quran"`, ULK `state="encountered"`. They never auto-enter acquiring, never get intro cards, never get FSRS cards.
7. **Interleaving**: `buildInterleavedSession()` in `index.tsx` distributes verse slots at evenly-spaced positions among sentence + intro card slots.
8. **Data**: 6236 verses from risan/quran-json CDN. Sequential from Al-Fatihah. Tables: `quranic_verses`, `quranic_verse_words`.
