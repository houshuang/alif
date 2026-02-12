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

## Reading Mode
1. User sees Arabic sentence (diacritized, large RTL text)
2. **Front phase**: user can tap non-function words to look them up (calls GET /api/review/word-lookup/{lemma_id}). Tapped words auto-marked as missed.
3. **Lookup panel**: Shows root, root meaning. If root has 2+ known siblings → prediction mode ("You know words from this root: X, Y. Can you guess the meaning?") before revealing English. Otherwise shows meaning immediately.
4. Taps "Show Answer" to reveal: English translation, transliteration, root info for missed words
5. **Back phase**: triple-tap words to cycle state: off → missed (red, rating 1 Again) → confused (yellow, rating 2 Hard) → off. Builds missed_lemma_ids + confused_lemma_ids
6. Rates: Got it (understood) / Continue (partial, if words marked) / I have no idea (no_idea)
7. **Back/Undo**: after submitting, can go back to previous card — undoes the review (restores pre-review FSRS state via backend undo endpoint), removes from sync queue if not yet flushed, restores word markings

## Listening Mode (real TTS via expo-av)
1. Audio plays via ElevenLabs TTS (speed 0.7x, multilingual_v2 model)
2. Tap to reveal Arabic text — tap words you didn't catch
3. Tap to reveal English translation + transliteration
4. Rate comprehension
5. Listening-ready filter: non-due words must have times_seen ≥ 3 AND FSRS stability ≥ 7 days

## Learn Mode
1. **Pick phase**: Shows 5 candidate words one at a time — Arabic, English, transliteration, POS, verb/noun/adj forms table, example sentence, root + sibling count, TTS play button
2. Actions per word: Learn (introduces, starts acquisition), Skip, Never show (suspend)
3. Selection algorithm: 40% frequency + 30% root familiarity (peaks at 30-60% of root known) + 20% recency bonus (sibling introduced 1-3 days ago) + 10% grammar pattern coverage + encountered bonus (0.5 for words seen in textbook/story)
4. **Quiz phase**: After introducing words, polls for generated sentences (20s timeout). Sentence quiz or word-only fallback. Got it → rating 3, Missed → rating 1.
5. **Done phase**: Shows count introduced, quiz accuracy, CEFR level

## Story Mode
1. **Generate**: LLM generates micro-fiction (2-12 sentences) using known vocabulary, random genre
2. **Import**: Paste any Arabic text → morphological analysis + LLM batch translation creates Lemma entries for unknown words (`source="story_import"`, `source_story_id` set). Proper nouns (personal/place names) detected by LLM are marked as function words with `name_type` instead of creating Lemma entries. Unknown vocab words become Learn mode candidates with `story_bonus` priority. No ULK created — Learn mode handles introduction.
3. **Reader**: Word-by-word Arabic with tap-to-lookup (shows gloss, transliteration, root, POS). Arabic/English tab toggle. Actions at end of scroll (not fixed bottom bar).
4. **Completion flow**: Complete (creates "encountered" ULK for unknown words — no FSRS card; submits real review only for words with active FSRS cards), Skip (only rates looked-up words), Too Difficult (same as skip)
5. **List view**: Cards with readiness indicators (green ≤3 unknown, orange, red), generate + import modals
