# Experiment: Passage vs Individual Sentence Reading Speed

**Date**: 2026-03-03 (design)
**Status**: Designed, not yet run
**Linked from**: `experiment-log.md`

---

## Motivation

We want to explore using 3-5 sentence passages as review cards instead of (or alongside) individual sentences. The main unknown is **reading speed**: how much faster is reading connected sentences vs disconnected ones?

Current baseline: **33.5s median per individual sentence** (from March 3 analysis, 14-day window).

This experiment measures the speed factor and comprehension accuracy, so we can model the efficiency tradeoff before committing to building a full passage review mode.

---

## Hypotheses

**H1 (Speed)**: Reading 4 connected sentences as a single passage takes less total time than reading 4 individual unrelated sentences, because:
- Prior context reduces parsing cost for subsequent sentences (characters, setting, vocabulary prime the reader)
- No between-card overhead (swipe, card load, mental context reset)
- Estimate: 1.5–2.5× faster per sentence-equivalent

**H2 (Comprehension)**: Comprehension accuracy is equivalent or better for passages, because:
- Surrounding context provides semantic scaffolding for weaker words
- Connected narrative aids retention of sentence meaning

**H3 (Enjoyment)**: Passages feel more enjoyable and "puzzle-like" than isolated sentences.

---

## Design

### Quick version (no code changes, ~15 minutes)

Uses the existing story reader for passages and existing review data for individual baselines.

#### Materials

Generate **5 micro-stories** of exactly 4 sentences each via the existing story generation pipeline:

- **Vocabulary constraint**: Only FSRS words with stability ≥ 7 days (comfortable vocabulary)
- **Challenge words**: Each story includes exactly **1 word** from the stability 1–7d tier (not acquiring — these are graduated but still fragile). This simulates the "one slightly tricky word per passage" scenario.
- **Sentence length**: 5-8 words per sentence (matching current generation params)
- **No acquiring words, no encountered words, no box-1 words**

This produces 20 sentences of connected text and 5 embedded challenge words.

#### Procedure

1. **Generate stories** via script (details below)
2. **Read stories** in the story reader: read each mini-story, tap words you don't know, tap Complete. The app records `reading_time_ms` per story.
3. **Same session, do a normal review**: review ~20 individual sentences at similar difficulty. The app records `response_ms` per sentence.

#### Measurements

| Metric | Passage condition | Individual condition |
|--------|-------------------|---------------------|
| Time per sentence | `reading_time_ms / 4` per story | `response_ms` per card |
| Challenge word detection | Tapped = detected | Tapped/marked = detected |
| Subjective feel | Self-report after | Self-report after |

#### Analysis

```
speed_ratio = median(individual_response_ms) / median(passage_per_sentence_ms)
```

If `speed_ratio > 1.5`, passages are meaningfully faster. If `speed_ratio ≈ 1.0`, there's no speed benefit and the only gain is enjoyment/depth.

---

### Proper A/B version (requires passage card component, ~15 minutes to run)

This is the cleaner design for if we decide to build the passage card.

#### Materials

Generate **8 passages** of 4 sentences each (32 sentences total), same vocabulary constraints as above.

#### Conditions

- **Condition A (Individual)**: Passages 1–4 decomposed into 16 shuffled sentence cards, shown in the normal review flow
- **Condition B (Passage)**: Passages 5–8 shown as 4 passage cards, each displaying all 4 sentences as a block

Counterbalanced: which passages go to which condition is randomized.

#### Passage card interaction model

The passage card renders identically to the review flow's `SentenceReadingCard`, but with 4 sentences stacked vertically instead of 1:

```
┌──────────────────────────────────┐
│  ذَهَبَ أَحْمَدُ إِلَى السُّوقِ    │  sentence 1
│  وَجَدَ تُفَّاحًا أَحْمَرَ كَبِيرًا   │  sentence 2
│  سَأَلَ البَائِعَ عَنِ الثَّمَنِ     │  sentence 3
│  اِشْتَرَى ثَلَاثَ تُفَّاحَاتٍ      │  sentence 4
├──────────────────────────────────┤
│  [translation hidden / revealed]  │
├──────────────────────────────────┤
│  [No idea]  [Continue]            │
└──────────────────────────────────┘
```

- Words are individually tappable (same tap-to-mark cycling: off → missed → confused → off)
- Timer starts on card appear, stops on submit (same as individual cards)
- Submit sends one `response_ms` for the whole passage
- Each word in the passage gets FSRS/acquisition review credit (same as current per-sentence logic, but applied to all words across all 4 sentences)

#### Procedure

1. Generate materials
2. Run a special review session (flagged `review_mode = "experiment"`)
3. Session alternates: 4 individual → 1 passage → 4 individual → 1 passage → ... (interleaved to control for fatigue/warmup)
4. Post-session: which format did you prefer?

#### Measurements

| Metric | How | N |
|--------|-----|---|
| Time per sentence-equivalent | Individual: `response_ms`. Passage: `response_ms / 4` | 16 per condition |
| Words marked per sentence-equivalent | Count of missed+confused indices per card (÷4 for passages) | 16 per condition |
| Comprehension signal | `understood / partial / no_idea` per card | 16 per condition |
| Challenge word detection rate | Was the embedded fragile word tapped? | 4 per condition |

#### Analysis

- Mann-Whitney U test on per-sentence-equivalent times (non-parametric, small N)
- Bootstrap 95% CI on the speed ratio
- Count of marked words as proxy for comprehension accuracy
- Subjective preference as tiebreaker

---

## Generation Script

For the quick version, adapt the existing story generation with custom vocabulary filtering:

```python
# Pseudocode for generate_experiment_stories.py

# 1. Get FSRS words with stability ≥ 7d
strong_words = get_words_with_stability(min_stability_days=7)

# 2. Get 5 "challenge words" — stability 1-7d, graduated
challenge_words = get_words_with_stability(min_stability_days=1, max_stability_days=7)
random.shuffle(challenge_words)
challenge_words = challenge_words[:5]

# 3. For each challenge word, generate a 4-sentence mini-story
for i, challenge in enumerate(challenge_words):
    story = generate_story(
        known_words=strong_words,
        must_include=[challenge],      # the one "harder" word
        num_sentences=4,
        difficulty="beginner",
        max_words_per_sentence=8,
    )
    save_as_story(story, title=f"Experiment {i+1}")
```

The generation uses the existing `STORY_SYSTEM_PROMPT` with a modified instruction: "Write exactly 4 sentences" and the vocabulary list filtered to strong words plus the one challenge word.

---

## What the results tell us

| Speed ratio | Interpretation | Action |
|-------------|---------------|--------|
| **< 1.2** | Passages aren't meaningfully faster | Passages only for enjoyment — don't change review flow |
| **1.2–1.8** | Moderate speed gain | Hybrid mode: passages for FSRS maintenance, singles for acquisition |
| **1.8–2.5** | Large speed gain | Passages as primary FSRS review mode post-backlog |
| **> 2.5** | Very large gain | Passages may be viable for acquisition too (with 2-3 targets per passage) |

For comprehension:
- If accuracy drops significantly in passages → the cognitive budget model needs to be more conservative (fewer challenge words per passage)
- If accuracy is equivalent → the 95% coverage threshold from Hu & Nation (2000) holds for passage-level i+1

---

## Confounds and limitations

1. **Interaction model differs** (quick version only): story reader uses tap-to-lookup, review uses tap-to-mark. Mitigated by comparing pure reading time, not interaction time.

2. **Practice effect**: individual sentences done first might warm up the reader. Mitigated in A/B version by interleaving conditions.

3. **N=1**: no between-subject generalization. But we only need to optimize for one learner. Use within-subject comparisons and treat this as an N-of-1 pilot.

4. **Generated vs pool sentences**: individual sentences come from the pre-generated pool (possibly stale topics), while stories are freshly generated. Mitigated by using similar vocabulary constraints.

5. **Story reader overhead**: the story reader has a different layout (full-screen scrollable text vs card-based). This is a real difference that would exist in production too, so it's a feature not a bug.

---

## Estimated time to run

- **Quick version**: ~5 min to generate stories + ~10 min to read 5 stories + ~10 min normal review = **25 min total**
- **A/B version**: ~30 min to build passage card + ~5 min to generate + ~15 min to run = **50 min total**

## Recommendation

Run the **quick version first**. If the speed ratio is > 1.5, invest in building the passage card component for the proper A/B test. If the ratio is near 1.0, the case for passages is purely about enjoyment and depth of processing, which is still valuable but doesn't need speed data to justify.
