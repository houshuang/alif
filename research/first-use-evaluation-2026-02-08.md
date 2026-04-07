# First-Use Evaluation Plan

Date: 2026-02-08 (evening) through 2026-02-09 (evening)

Usage plan: 2-3 review sessions tonight, 2-3 sessions tomorrow morning, 2-3 sessions tomorrow evening. Ask Claude to evaluate tomorrow night.

---

## 1. Usage Sessions to Complete

### Session 1: Tonight (reading mode, ~10 min)
- Open the app in Expo Go via tunnel URL
- Do a full reading review session (10 cards)
- Pay attention to: sentence quality, word difficulty, UI flow

### Session 2: Tonight (listening mode, ~10 min)
- Switch to listening mode
- **BLOCKED**: ElevenLabs returns 402 (payment required) for library voices. Audio will fall back to a 2-second timer — no real TTS audio will play.
- **Skip this session** unless ElevenLabs account is upgraded first.
- If upgraded: first use will be slow (TTS generation per sentence, ~2-3s each), subsequent sessions faster (cached audio)

### Session 3: Tomorrow morning (reading mode, ~10 min)
- Words reviewed tonight should NOT reappear (FSRS scheduling)
- New batch of 10 sentences from the remaining ~186 due words
- Pay attention to: are the sentences different enough? Do they get harder?

### Session 4: Tomorrow morning (second session, reading mode)
- Continue reducing the due pile
- By now you should have ~20-30 words reviewed

### Sessions 5-6: Tomorrow evening
- Some words from session 1 (tonight) might be due again (FSRS learning steps)
- Mix of returning words and first-time words
- Note whether returning words feel easier or the same

---

## 2. Specific Hypotheses to Test

### H1: Sentence comprehensibility
**Prediction**: Most sentences will be fully comprehensible except for the target word. You know all 196 Duolingo words, so the "all other words known" constraint should hold.

**What could go wrong**:
- Clitic-attached forms that the validator missed (e.g., وَكِتَابُهُ = "and his book")
- Words that appear in conjugated forms not in the lemma lookup
- The validator treats ~60 particles as known, but some may be unfamiliar to you

**How to evaluate**: For each sentence, note whether there were words you didn't recognize besides the target. Count how many sentences had 0, 1, or 2+ unexpected unknowns.

### H2: Rating distribution
**Prediction**: For a Duolingo import (words you already "know" somewhat), you should rate roughly:
- "Got it" (understood): ~60-70% of sentences (you know these words from Duolingo)
- "Partial" (some missed): ~20-30%
- "No idea": ~5-10%

**Alternative hypothesis**: If you haven't used Duolingo recently, more words may have decayed, pushing toward 40-50% "got it."

**How to evaluate**: The session results screen shows counts. Log these after each session.

### H3: Response time patterns
**Prediction**: Response time should be:
- Fastest for words you genuinely know well (~3-5s to read, comprehend, and rate)
- Moderate for words you recognize but need to think about (~8-15s)
- Slowest for "no idea" (you'll stare at it, try to figure it out, then give up: ~20-30s)

**How to evaluate**: The backend logs `response_ms` per review. We'll analyze the distribution by comprehension_signal.

### H4: Sentence selector covers different words per session
**Prediction**: Each session of 10 should cover 10-13 different due words (greedy set cover). Across 3 sessions tonight, you should cover ~30-40 distinct words.

**How to evaluate**: Check interaction logs for `sentence_selected` events. Each has `due_words_covered` count.

### H5: Word-only fallback cards
**Prediction**: Since 95/196 lemmas have no sentences, ~5-6 items per session will be word-only (just the Arabic word, no sentence context). These will feel less engaging than sentence cards.

**How to evaluate**: Count sentence vs word-only cards per session (logged in `session_start` as `sentence_count` and `fallback_count`).

### H6: Listening mode latency
**Prediction**: First listening session will have noticeable delays (2-3s per sentence for TTS generation). Second session should be faster for returning sentences.

**BLOCKED**: ElevenLabs returns 402 on the current free plan. Listening mode will only show a 2-second timer fallback with no real audio. This hypothesis cannot be tested until the account is upgraded. TTS request logging is in place (logs `tts_request` events with cache_hit, latency_ms, success) for when it becomes available.

**How to evaluate**: Subjective experience once TTS works. Backend logs `tts_request` events with latency for quantitative analysis.

### H7: FSRS scheduling produces reasonable intervals
**Prediction**: After rating words tonight:
- "Got it" (rating=3) words should be scheduled 1-3 days out
- "Partial" (mixed ratings) words should come back within hours to 1 day
- "No idea" (rating=1) words should return within minutes to hours

**How to evaluate**: Tomorrow morning, check how many words are due. Should be a mix of tonight's failures (soon) and none of tonight's successes (not yet due).

### H8: Collateral credit works
**Prediction**: Words that appear as non-target words in reviewed sentences get collateral FSRS credit. If word X appears in 3 different sentences as a scaffold word and gets rated=3 each time, it should have times_seen=3 even though it was never the primary target.

**How to evaluate**: After sessions, check `review_log` for entries with `credit_type=collateral`. These should exist and show rating=3 for understood sentences.

---

## 3. Data We Need to Capture

### Per session (from interaction logs)
- [ ] `session_start`: total_due_words, covered_due_words, sentence_count, fallback_count
- [ ] `sentence_review` events: comprehension_signal distribution, response_ms values, missed_lemma_ids
- [ ] Number of distinct lemmas reviewed
- [ ] Session duration (first event to last event timestamp)

### Per word (from review_log)
- [ ] Rating distribution: how many 1s (Again) vs 3s (Good)
- [ ] credit_type distribution: how many primary vs collateral reviews
- [ ] response_ms by rating (faster for known words?)
- [ ] Which words got "no_idea" -- are these genuinely hard or sentence-context issues?

### Cross-session (tomorrow evaluation)
- [ ] FSRS scheduling: how many words returned as due between sessions?
- [ ] Accuracy change: is accuracy higher on words seen before?
- [ ] Coverage: after N sessions, what fraction of 196 words have been reviewed at least once?
- [ ] Sentence reuse: were any sentences shown twice? (should not be, due to 7-day cooldown)

### Qualitative observations to note
- [ ] Were any sentences grammatically wrong or nonsensical?
- [ ] Were any translations inaccurate?
- [ ] Were any diacritics wrong or missing?
- [ ] Was the UI flow smooth? Any friction points?
- [ ] Did the reading/listening mode toggle work?
- [ ] Did tapping "missed" words feel intuitive?
- [ ] Were word-only fallback cards useful or annoying?
- [ ] Was the session length (10 cards) too short, too long, or right?

---

## 4. Evaluation Queries (for tomorrow night)

These are the queries I'll run against the production database after 24 hours of usage:

### Basic usage stats
```sql
SELECT COUNT(*) as total_reviews FROM review_log;
SELECT COUNT(DISTINCT session_id) as sessions FROM review_log;
SELECT COUNT(DISTINCT lemma_id) as words_reviewed FROM review_log;
```

### Rating distribution
```sql
SELECT rating, COUNT(*) FROM review_log GROUP BY rating;
SELECT comprehension_signal, COUNT(*) FROM review_log GROUP BY comprehension_signal;
SELECT credit_type, COUNT(*) FROM review_log GROUP BY credit_type;
```

### Response time by signal
```sql
SELECT comprehension_signal,
       AVG(response_ms) as avg_ms,
       MIN(response_ms) as min_ms,
       MAX(response_ms) as max_ms
FROM review_log
WHERE credit_type = 'primary'
GROUP BY comprehension_signal;
```

### FSRS scheduling check
```sql
SELECT knowledge_state, COUNT(*)
FROM user_lemma_knowledge
GROUP BY knowledge_state;

-- Words that moved from learning -> known
SELECT COUNT(*) FROM user_lemma_knowledge
WHERE knowledge_state = 'known';
```

### Collateral credit
```sql
SELECT credit_type, COUNT(*), AVG(rating)
FROM review_log
WHERE credit_type IS NOT NULL
GROUP BY credit_type;
```

### Session coverage and completion
```sql
-- Reviews per session (should be ~10 if sessions completed)
SELECT session_id, COUNT(*) as reviews,
       SUM(CASE WHEN credit_type = 'primary' THEN 1 ELSE 0 END) as primary_reviews,
       SUM(CASE WHEN credit_type = 'collateral' THEN 1 ELSE 0 END) as collateral_reviews
FROM review_log
GROUP BY session_id;

-- From interaction logs: parse session_start events
-- Check covered_due_words / total_due_words ratio per session
```

### Word stability distribution (after reviews)
```sql
SELECT
  CASE
    WHEN json_extract(fsrs_card_json, '$.stability') IS NULL THEN 'null'
    WHEN json_extract(fsrs_card_json, '$.stability') < 0.5 THEN '<0.5d'
    WHEN json_extract(fsrs_card_json, '$.stability') < 1 THEN '0.5-1d'
    WHEN json_extract(fsrs_card_json, '$.stability') < 3 THEN '1-3d'
    WHEN json_extract(fsrs_card_json, '$.stability') < 7 THEN '3-7d'
    ELSE '>7d'
  END as stability_bucket,
  COUNT(*) as word_count
FROM user_lemma_knowledge
WHERE fsrs_card_json IS NOT NULL
GROUP BY stability_bucket;
```

### Words reviewed vs never-reviewed
```sql
SELECT
  CASE WHEN times_seen > 0 THEN 'reviewed' ELSE 'never_seen' END as status,
  COUNT(*) as count
FROM user_lemma_knowledge
GROUP BY status;
```

### Missed words analysis
```sql
-- Which words were most often missed?
SELECT rl.lemma_id, l.lemma_ar, l.gloss_en,
       COUNT(*) as times_missed
FROM review_log rl
JOIN lemmas l ON rl.lemma_id = l.lemma_id
WHERE rl.rating = 1
GROUP BY rl.lemma_id
ORDER BY times_missed DESC
LIMIT 20;
```

---

## 5. Questions the Data Should Answer

1. **Is the sentence quality good enough?** (qualitative + unknown word count)
2. **Is FSRS scheduling reasonable for this vocabulary?** (interval distribution)
3. **Does collateral credit create meaningful learning?** (do scaffold words progress faster?)
4. **Is 10 items per session the right number?** (fatigue, engagement)
5. **How fast does the due pile shrink?** (words covered per session, return rate)
6. **Are word-only fallbacks worth keeping?** (do they feel useful or annoying?)
7. **Is listening mode viable without pre-cached audio?** (latency, quality)
8. **Are the research-backed features (easy bookends, difficulty matching) noticeable?** (subjective)
9. **What's the biggest pain point?** (whatever stands out most)
10. **What should we build next?** (based on what's missing or broken)

---

## 6. What to Record During Usage

After each session, note in a text file or message:
1. Mode used (reading/listening)
2. How many cards felt easy vs hard
3. Any sentences that were wrong/confusing (screenshot if possible)
4. Any UI issues or friction
5. Overall enjoyment (1-5 scale)
6. Whether you want to keep going or are tired
7. Approximate session duration

This subjective data plus the automated logging will give us a complete picture for evaluation.
