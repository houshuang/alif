# Algorithm & Design Implications

Actionable recommendations derived from vocabulary acquisition research. Each recommendation maps specific research findings to concrete changes in Alif's algorithm and design.

See [vocabulary-acquisition-research.md](./vocabulary-acquisition-research.md) for the full research backing.

---

## Priority Levels

- **P0 (Must-have)**: Strong research support, high impact, aligns with existing architecture
- **P1 (Should-have)**: Strong research support, moderate implementation effort
- **P2 (Nice-to-have)**: Moderate research support or high implementation effort

---

## 1. Context Diversity in Reviews (P0)

**Research**: Seeing a word in multiple different contexts improves retention more than repeated exposure in the same context (Pagan et al., 2019; Norman et al., 2023). Even 2 different contexts is dramatically better than 1.

**Current state**: We generate sentences per target word, but there is no mechanism to ensure a different sentence is shown on each review.

**Changes**:
- Generate and cache 4-8 sentences per target lemma (batch generation is already more efficient for LLM calls)
- Add a `shown_count` field to the Sentence model (or a join table `review_sentence_log`)
- On each FSRS review, select the least-shown sentence for that lemma
- When all cached sentences are exhausted after 8+ reviews, generate new ones
- Track `distinct_contexts_seen` per lemma in `UserLemmaKnowledge`

**Schema change**: Add `times_shown` column to `sentences` table. Add `sentence_id` to `ReviewLog` to track which sentence was shown for each review.

---

## 2. Root Awareness as First-Class Feature (P0)

**Research**: L2 learners organize Arabic lexicons by root (Freynik et al., 2017). Root priming is psychologically real. Combining reading with morphological awareness training produces the best vocabulary gains (Yuan & Tang, 2023).

**Current state**: Roots are tracked in the data model but not prominently surfaced or used in the learning algorithm.

**Changes**:
- Always display the root when showing a word (in the reveal phase and in word detail view)
- Track `root_familiarity_score` in a `UserRootKnowledge` table (count of known lemmas for that root, weighted by frequency)
- When selecting the next new word to introduce, bonus-score words whose root is already partially known (lower effort to learn, high yield)
- When introducing a word with a known root, show the known family members: "You know كِتَاب (book) from root ك.ت.ب. This word مَكْتَبَة (library) shares the same root."
- Add a "root family" view showing all words from a root, with known/unknown status

**New word selection algorithm**:
```
score(lemma) = frequency_weight * 0.4
             + root_familiarity_bonus * 0.3
             + user_need_score * 0.2
             + morphological_pattern_coverage * 0.1
```

Where `root_familiarity_bonus` is high when the root is partially known (some words known, but not this one).

---

## 3. Involvement Load Optimization (P0)

**Research**: Evaluation is the strongest component of the Involvement Load Hypothesis. Tasks that require the learner to evaluate meaning produce the best retention (Laufer & Hulstijn, 2001; meta-analysis 2022).

**Current state**: The review flow is "see sentence -> self-assess -> reveal." This is moderate involvement (load ~3).

**Changes**:
- Before revealing the translation, prompt: "Can you understand this sentence?" with options:
  - "Yes, I understand it" (proceed to reveal and verify)
  - "I understand most of it" (reveal, highlight the difficult part)
  - "I need help" (reveal immediately)
- The self-assessment after reveal becomes an evaluation task: "Was your understanding correct?"
- This two-step process (predict, then verify) increases involvement load by adding genuine search and evaluation
- Track prediction accuracy as an additional signal for FSRS (a correctly predicted "yes" = strong recall; an incorrectly predicted "yes" = overconfidence, schedule sooner)

---

## 4. Encounter Tracking (P0)

**Research**: 8-12 meaningful encounters are needed for basic receptive knowledge; 20-30 for deep knowledge. Fewer than 6 encounters = less than 30% retention after a week (Webb, 2007; Nation, 2014).

**Current state**: `times_seen` and `times_correct` are tracked on `UserLemmaKnowledge`, but no encounter counting for incidental exposures (words seen in sentences for other target words).

**Changes**:
- Log every word encountered in every sentence, not just the target word
- Add `total_encounters` and `distinct_contexts` fields to `UserLemmaKnowledge`
- When a word has been encountered 8+ times across diverse contexts without being a review target, consider it "passively acquired" and add it to the known set with a lower confidence
- Display encounter count in word detail view: "You've seen this word 12 times across 6 different contexts"
- Use encounter count as a signal for FSRS: words with many incidental encounters but few deliberate reviews may need less frequent deliberate review

---

## 5. Semantic Clustering Avoidance in Reviews (P1)

**Research**: Semantically similar words presented together cause interference errors (Tinkham, 1993, 1997). However, root family words may be an exception due to morphological transparency.

**Current state**: FSRS review order is purely by due date. No consideration of semantic relationships.

**Changes**:
- When building a review session, avoid scheduling semantically similar words back-to-back (e.g., do not review "red" immediately after "blue")
- Exception: root family words CAN appear in the same session but should not be consecutive
- Implementation: tag lemmas with a coarse semantic category (from SAMER or manual tagging). When ordering the review queue, insert a minimum gap of 3 items between same-category words
- For new word introduction: do NOT introduce semantically similar new words on the same day. Space them by at least 2-3 days.

---

## 6. Interleaving in Reviews, Blocking for Introduction (P1)

**Research**: Interleaving enhances long-term retention (Bjork & Bjork, 2011). But initial blocked practice may help for low-achieving learners (Hwang, 2025).

**Current state**: No explicit interleaving or blocking strategy.

**Changes**:
- **Introduction phase**: When teaching a new root family, introduce 2-3 members in sequence (blocked) over 1-2 days. Highlight the root relationship. This builds the morphological schema.
- **Review phase**: Fully interleave across roots, semantic categories, and parts of speech. Never group root family members consecutively in reviews.
- Implementation: the review queue builder should maximize diversity across consecutive items (different root, different POS, different semantic field)

---

## 7. Fluency Mode (P1)

**Research**: Nation's Four Strands requires 25% of learning time on fluency development -- encountering only familiar material at speed. This is neglected in most SRS apps.

**Current state**: No fluency mode. All interactions involve unknown or partially known words.

**Changes**:
- Add a "Fluency Reading" mode: present short texts (3-5 sentences) where ALL words are known
- No vocabulary testing; the goal is rapid comprehension
- Track reading speed (time per sentence or characters per minute)
- Show speed metrics and improvement over time
- Source material: previously completed stories, or generate easy texts from the top frequency band of known words
- Can also use audio: listen to a passage at normal speed with all known words, testing real-time comprehension

---

## 8. Frequency + Root-Based Curriculum Ordering (P1)

**Research**: Frequency-based ordering is effective for the first 3,000 lemmas but has diminishing returns after that. Root knowledge provides a vocabulary multiplier (10 common roots -> 100+ words).

**Current state**: Frequency rank exists on the Lemma model but is not used for curriculum ordering.

**Changes**:
- Phase 1 (0-1,500 lemmas): Strict frequency ordering, with root family grouping within each frequency band
- Phase 2 (1,500-3,500 lemmas): Frequency + root completion (prefer words that complete a partially known root family) + user needs (imported texts)
- Phase 3 (3,500+ lemmas): Primarily text-driven (import a text, learn its unknown words) + domain narrowing
- Display to user: "You know N of M words in the ك.ت.ب family" with visual progress

**New word queue algorithm**:
```
For each candidate lemma not yet in learning:
  base_score = 1.0 / log(frequency_rank + 1)   # frequency boost
  root_bonus = known_siblings / total_siblings   # root completion
  need_bonus = 1.0 if in_imported_text else 0.0  # user need
  pattern_bonus = 0.2 if pattern_is_known else 0  # morphological pattern

  final_score = base_score + root_bonus * 0.5 + need_bonus * 2.0 + pattern_bonus
```

---

## 9. Coverage-Aware Text Generation (P1)

**Research**: 95% coverage is the minimum threshold for comprehension; 98% is optimal (Hu & Nation, 2000).

**Current state**: The sentence generator targets exactly 1 unknown word, which is good for individual sentences. But no coverage calculation exists for longer texts.

**Changes**:
- For story/text generation, calculate coverage percentage against user's known vocabulary BEFORE presenting
- Display coverage to user: "You know 94% of the words in this text (12 unknown words out of 200)"
- Warn if coverage is below 95%; suggest prerequisites
- For text import: automatically identify unknown words, calculate coverage, and offer a "preparation plan" (learn these 15 words first, then read the text)
- Add a coverage calculator endpoint: `POST /analyze/coverage` taking Arabic text and returning coverage stats

---

## 10. Narrow Reading / Topic Mode (P1)

**Research**: Reading thematically related texts produces significantly greater vocabulary gains than reading unrelated texts (Kang, 2015; Krashen, 2004). Topic coherence naturally recycles specialized vocabulary.

**Current state**: No topic organization for generated content.

**Changes**:
- Add a "Topics" feature: learner selects a topic (travel, food, news, religion, history, etc.)
- Generate 5-10 thematically related short texts per topic, each building on shared vocabulary
- Track which topics the user has engaged with; suggest new topics based on vocabulary gaps
- When generating texts for a topic, ensure key topic vocabulary recurs across multiple texts (minimum 3 occurrences per key word across the topic set)
- Combine with root instruction: "In this travel topic, you'll learn the root س.ف.ر (travel): سَفَر (travel/journey), مُسَافِر (traveler), سَفَارَة (embassy)"

---

## 11. Diacritics Strategy (P1)

**Research**: Diacritized text consistently helps L2 learners at all levels. No evidence of harmful dependency (Midhwah, 2020).

**Current state**: Always show diacritics. This is correct.

**Changes (optional, P2 nice-to-have)**:
- Add an advanced "diacritics fade" mode for users who want to practice reading undiacritized text
- Progressive levels: full diacritics -> vowels only on unknown words -> no diacritics
- Default remains full diacritics; fade is opt-in
- When diacritics are faded, tap-to-reveal shows full diacritization

---

## 12. Dual Coding / Multimedia Glosses (P2)

**Research**: Text + picture glosses outperform text-only (Paivio, 1971; multimedia gloss research). Audio adds another encoding channel.

**Current state**: Audio via ElevenLabs TTS is planned. No visual glosses.

**Changes**:
- For concrete nouns (objects, animals, food, places): add simple icons or images to the gloss view
- Source: use a small set of royalty-free icons mapped to common semantic categories, or use AI image generation
- Implementation priority: audio first (already planned), images second
- For abstract words: use example scenarios or analogies instead of images

---

## 13. Prediction Calibration Signal (P2)

**Research**: Retrieval practice is most effective with feedback. Learners who predict before revealing and then check their prediction show better calibration and retention.

**Current state**: FSRS rating is purely self-reported after reveal.

**Changes**:
- Before reveal, record the user's confidence: "I think I understand" / "Not sure" / "No idea"
- After reveal, record the FSRS rating as usual
- Track calibration: how often does "I think I understand" match a Good/Easy rating?
- Use miscalibration (overconfidence) as a signal: if the user often says "I understand" but then rates Hard/Again, their self-assessment is unreliable -- schedule more frequent reviews for their "Good" items
- This data can also feed into FSRS parameter optimization

---

## 14. Variable Task Types (P2)

**Research**: Varying learning conditions is a "desirable difficulty" (Bjork). Variable retrieval contexts produce better learning than constant retrieval (PNAS, 2024).

**Current state**: Single review mode (see Arabic sentence, self-assess, reveal).

**Changes -- add task variation across reviews**:
- **Standard**: See Arabic sentence with target word -> guess meaning -> reveal
- **Listening**: Hear sentence (audio only) -> guess meaning -> reveal Arabic text + translation
- **Cloze**: See sentence with target word blanked -> choose from 4 options
- **Context inference**: See sentence in English with ONE Arabic word embedded -> infer meaning
- **Root connection**: "This word shares a root with كِتَاب. What might مَكْتَبَة mean?"

FSRS determines WHEN to review; the task type selector determines HOW:
- New/Learning cards: use Standard mode (most scaffolding)
- Review cards with low stability: alternate between Standard and Listening
- Review cards with high stability: use harder modes (Cloze, Context inference, Root connection) to maintain engagement and deepen knowledge

---

## 15. Receptive Depth Tracking (P2)

**Research**: Receptive vocabulary depth is a stronger predictor of reading proficiency than breadth (multiple studies). Knowing a word "deeply" means recognizing it across contexts, collocations, and morphological forms.

**Current state**: Knowledge tracking is binary per lemma (known/unknown via FSRS state).

**Changes**:
- Track depth dimensions per lemma:
  - `form_recognized`: Can recognize the written form (basic)
  - `meaning_recalled`: Can recall the English meaning
  - `context_flexible`: Has seen in 4+ different sentence contexts
  - `audio_recognized`: Can recognize from audio alone
  - `morphologically_connected`: Root and pattern are known
  - `collocations_known`: Common word combinations are recognized
- A word moves from "known (shallow)" to "known (deep)" as more dimensions are checked off
- Display depth visually (e.g., a bar that fills up across dimensions)
- Prioritize depth-building for high-frequency words over breadth-building for low-frequency words

---

## Summary: Implementation Order

### Phase 1 (Immediate -- next sprint)
1. Context diversity in reviews (generate multiple sentences, show different one each time)
2. Root awareness display (show root on reveal, root family view)
3. Encounter tracking (log all words in every sentence, not just target)
4. Involvement load optimization (two-step predict-then-verify flow)

### Phase 2 (Next month)
5. Frequency + root-based curriculum ordering (new word selection algorithm)
6. Coverage-aware text generation (coverage calculator, preparation plans)
7. Semantic clustering avoidance in review queue
8. Interleaving strategy (block introduction, interleave reviews)

### Phase 3 (Next quarter)
9. Fluency mode (speed reading of known material)
10. Narrow reading / topic mode
11. Variable task types (listening, cloze, root connection)

### Phase 4 (Future)
12. Dual coding / multimedia glosses
13. Prediction calibration signal
14. Receptive depth tracking
15. Diacritics fade mode
