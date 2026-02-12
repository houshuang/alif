# Learning Algorithm Redesign — Research & Implementation Plan

> Date: 2026-02-12
> Status: Research phase. No code changes yet.
> Context: Post-OCR import of ~100 textbook pages created 411 words with inflated FSRS state. Accuracy cratered from 78% to 25%. Need fundamental algorithm rethink.

---

## Part 1: Current State Diagnosis

### Production Data (as of 2026-02-12)

| Metric | Value | Concern |
|--------|-------|---------|
| Total tracked words | 592 | — |
| From textbook scans | 411 (69%) | Bulk import with false "Good" signal |
| From Duolingo | 149 (25%) | Older, better reviewed |
| From Learn mode | 32 (5%) | Best quality signal |
| Words seen 0-2 times | **367 (63%)** | Barely any memory trace |
| Words seen exactly once | 214 | Single encounter = near-zero learning |
| Words never seen | 11 | Not yet scheduled |
| FSRS stability 30+ days | **586 (100%)** | ALL words falsely show high stability |
| Leeches (3+ failures) | 20 | Need intervention |

### Accuracy Trend (Post-OCR)

| Date | Reviews | Accuracy | Context |
|------|---------|----------|---------|
| Feb 8 | 180 | 78% | Pre-OCR, organic reviews |
| Feb 9 | 259 | 63% | Some new words entering |
| Feb 10 | 1,254 | 99% | **OCR bulk import** (auto Good ratings) |
| Feb 11 | 48 | **46%** | Real reviews — accuracy halved |
| Feb 12 | 8 | **25%** | Real reviews — still declining |

### How FSRS State Got Corrupted

1. `process_textbook_page()` calls `submit_review(rating=3)` for every OCR-extracted word
2. FSRS sets initial stability S₀(Good) = 2.3 days
3. When these words appear in sentence reviews and user doesn't mark them (reading holistically), they get another Good rating
4. Stability compounds: 2.3d → 5d → 12d → 30d+ over just a few reviews
5. Result: FSRS thinks user knows all 586 words. User actually knows ~150-200.

### Session Patterns

- Highly variable: 3 to 35 cards per session
- Many micro-sessions: 3-4 cards (picking up phone briefly)
- Some deep sessions: 30-35 cards (train ride)
- Inter-review gaps within sessions: 0-6 minutes
- Between sessions: 12-48 hours
- User reviews 50-200 words per day on active days

---

## Part 2: Literature Review Findings

### 2.1 Encounters Needed for Vocabulary Acquisition

**Sources**: Uchihara et al. 2019 meta-analysis, Laufer & Nation 2012, Conti 2025

- **8-12 meaningful encounters** to establish stable mental representation
- **<6 encounters → <30% recall after a week**
- **10+ encounters → 80%+ recall**
- **20-30 spaced repetitions** for long-term embedding
- Only **11% of variance** in word learning explained by encounter frequency — context diversity, retrieval effort, and modality also matter
- Diminishing returns beyond ~20 encounters
- **The first 2 encounters determine learning to the largest extent** (PMC4277705)
- A single exposure produces **minimal durable learning**

### 2.2 FSRS Cold-Start Limitations

**Sources**: Expertium blog, FSRS Algorithm wiki, Anki forums

**Default initial stability values (FSRS-6):**
| Rating | Stability | Meaning |
|--------|-----------|---------|
| Again (1) | 0.212 days (~5h) | Complete failure |
| Hard (2) | 1.293 days | Struggled |
| Good (3) | 2.307 days | Normal pass |
| Easy (4) | 8.296 days | Very confident |

**Critical limitation**: FSRS was NOT designed to model short-term memory. Quote from Expertium: "While neither FSRS-5 nor FSRS-6 have a proper model of short-term memory, they have a crude heuristic."

**FSRS has NO native learning phase.** It relies on the host application (e.g., Anki) to handle acquisition via "learning steps." Anki's default learning steps (1m, 10m) happen OUTSIDE FSRS. Our system goes directly from first encounter → FSRS scheduling, skipping this entirely.

**FSRS-5 same-day review stability progression**: 1.87 min → 13.88 min → 6.26 hours → 1.08 days

**Expertium recommends**: A single learning step of 15-30 minutes, then let FSRS determine graduation interval. Multiple same-day learning steps provide negligible benefit.

### 2.3 Spacing and Initial Acquisition

**Sources**: Pimsleur 1967, Smolen et al. 2016, Wozniak (SM-2)

**Pimsleur's memory schedule** (graduated interval recall):
5 seconds → 25 seconds → 2 minutes → 10 minutes → 1 hour → 5 hours → 1 day → 5 days → 25 days → 4 months → 2 years

Key insight: **the first 4 intervals are within-session** (seconds to minutes). The first inter-session gap is 1 day.

**Reconsolidation research** (Smolen et al.): Inverted-U relationship between spacing and retention. For 7-day retention test, ~3-day spacing optimal. For 35-day test, ~8-day spacing optimal. Spacing too short OR too long relative to desired retention is suboptimal.

**Massed vs distributed for acquisition**:
- For initial acquisition: massed and distributed produce **equivalent** immediate performance
- For retention: distributed is **dramatically superior**
- Practical implication: initial concentrated exposure followed by expanding intervals is optimal
- At least one **overnight sleep consolidation** should occur between acquisition and first spaced review

**Optimal initial schedule** (synthesized from literature):
1. Day 0: 3-4 exposures (introduction + within-session retrieval)
2. Day 1: 1-2 retrieval attempts (**most critical review**)
3. Day 3-4: 1 retrieval attempt
4. Day 7-10: 1 retrieval attempt
5. After this: FSRS takes over

### 2.4 Within-Session Spacing

**Sources**: Karpicke & Bauernschmidt, Storm/Bjork/Storm 2010, Kornell 2009

**Expanding retrieval practice** (Karpicke): lag 0 → lag 1 → lag 5 → lag 9 (gap = number of intervening items)

- **Expanding** (0-1-5-9): Better for short-term retention and initial success
- **Equal** (5-5-5-5): Better for long-term retention
- **Massed** (0-0-0-0): Worst for both

When initial retrieval is likely to **fail** (brand new vocabulary), expanding spacing loses its advantage. Use slightly compressed initial gaps.

**Kornell 2009**: One large flashcard stack (natural spacing) was more effective than four small stacks (massing). 90% of participants learned more with spacing; 72% **believed** massing was better.

**Optimal session layout for 10 cards with 3 new items**:
```
Position 1: Review card (warm-up)
Position 2: NEW CARD A
Position 3: Review card
Position 4: NEW CARD B
Position 5: Review card
Position 6: RE-QUIZ A (gap of 3)
Position 7: NEW CARD C
Position 8: Review card
Position 9: RE-QUIZ B (gap of 4)
Position 10: Review card (easy, end positive)
```

### 2.5 Interleaving vs Blocking

**Sources**: Bjork lab, Hwang 2025, Libersky et al. 2025

- **Interleaving helps** for discrimination tasks (distinguishing similar items)
- **Blocking helps** for initial acquisition of difficult material (especially for weaker learners)
- **Hybrid approach**: Block during initial encoding (Learn mode), interleave during practice (review sessions)
- **Arabic-specific risk**: Words from the same root share 3 consonants — high interference. Avoid introducing two root-siblings in the same session.

### 2.6 Retrieval Practice vs Recognition

**Sources**: Karpicke & Roediger 2008, Conti 2025, Stewart et al. 2024

- Active recall produces **150% better long-term retention** than passive re-exposure
- Dropping items from **test cycles** drastically reduced recall; dropping from **study cycles** had minimal impact
- Receptive vocabulary is ~40% larger than productive vocabulary
- Even **multiple-choice** is dramatically better than pure self-assessment
- For reading-focused apps: recognition IS the target skill, but adding retrieval effort still helps

**Current gap in Alif**: Sentence review is pure passive recognition (see Arabic → self-assess comprehension). No verification of understanding.

### 2.7 Leech Management

**Sources**: Anki manual, Hacking Chinese

- Anki's leech threshold: 8 lapses (we should use 6 for Arabic due to script difficulty)
- Leeches take **on average 10x** as many repetitions as normal cards
- Two primary causes: **interference** (similar items competing) and **poor encoding** (insufficient context)
- Fix for interference: **suspend one, learn the other first**, reintroduce later
- Fix for poor encoding: add images, mnemonics, more distinctive sentence contexts
- Arabic interference risk: verb forms I-X from same root, similar letter shapes, vowel pattern differences

### 2.8 A/B Testing with N=1

**Sources**: PMC3118090, PMC6787650

**Feasibility**: Yes, using between-word randomization.

**Design**: Randomly assign each new word to condition A or B at introduction time. Track 7-day retention as primary metric.

**Sample sizes needed**:
- ~50 words per condition (100 total)
- 4 weeks of reviews per word to measure 7-day retention
- Total timeline: 8-12 weeks for one experiment
- Use Bayesian Beta-Binomial updating: Prior Beta(1,1), stop when P(A>B) > 0.95

**Challenges**:
- Carryover: can't unlearn something
- Non-stationarity: learner improves over time
- Word difficulty confounding: mitigate by randomization

**Best first experiment**: "Acquisition phase (3 within-session exposures)" vs "current approach (single introduction then FSRS)"

---

## Part 3: Proposed Algorithm Changes

### 3.1 IMMEDIATE: FSRS State Correction (backfill script)

**What**: Reset FSRS cards for textbook_scan words that don't have genuine review evidence.

**Logic**:
```
For each ULK where source = 'textbook_scan':
  Count "real reviews" = reviews where review_mode != 'textbook_scan'
  If real_reviews <= 2:
    Reset fsrs_card to fresh Card() with due = now
    Set knowledge_state = 'learning'
    Set stability to 0 (fresh card)
  If real_reviews >= 3 but accuracy < 40%:
    Reset stability to 0.5 days
    Set knowledge_state = 'lapsed'
```

**Expected impact**: Immediately fixes the false stability problem. ~350 words will become properly "due" and enter the acquisition pipeline.

### 3.2 Acquisition Phase (Pre-FSRS Learning Steps)

**What**: A new state between introduction and FSRS scheduling.

**New knowledge_state**: `"acquiring"` (added to the existing new/learning/known/lapsed/suspended states)

**Entry criteria**: Any word with < 5 genuine reviews OR accuracy < 50%

**Behavior during acquisition**:
- Appear 2-3x per session in different sentences
- Within-session spacing: positions N, N+3, N+7 (expanding retrieval)
- NOT scheduled by FSRS — scheduled by the acquisition manager
- Next-day: must appear at least 1x regardless of FSRS due date

**Graduation criteria**: 5+ genuine reviews AND accuracy ≥ 60%

**On graduation**: Create/update FSRS card with appropriate initial rating based on acquisition performance.

**Implementation touches**:
- `models.py`: Add "acquiring" to knowledge_state enum
- `fsrs_service.py`: New `get_acquiring_words()` function
- `sentence_selector.py`: Modified `build_session()` to include acquisition words
- `sentence_review_service.py`: Track genuine vs auto reviews

### 3.3 Focus Cohort System

**What**: A rolling window of ~30-50 words that get intensive treatment.

**Cohort selection**: Use existing `word_selector.py` scoring (frequency + root familiarity + recency bonus) to pick the best candidates from the full pool.

**Cohort rules**:
- Maximum cohort size: 40 words
- Entry: when a slot opens, best-scoring word from pool enters
- Exit: word reaches stability > 7 days AND 5+ reviews AND 60%+ accuracy
- Words outside cohort don't appear in review sessions (except as scaffold in sentences)
- Acquiring words automatically in cohort

**Why this matters**: With 586 words and ~100 reviews/day, each word gets reviewed once per 6 days. With 40 words and ~100 reviews/day, each word gets reviewed 2-3x per day. The math is dramatically different.

### 3.4 Session-Level Word Repetition

**What**: Modify `build_session()` to deliberately repeat acquisition words.

**Current behavior**: Greedy set cover selects sentences maximizing due-word coverage. Each sentence selected independently.

**New behavior**:
1. Identify acquisition words that need repetition this session (max 5)
2. For each, find 2-3 sentences containing that word
3. Interleave these into the session at expanding positions
4. Fill remaining session slots with normal due-word sentences

**Session template** (10 cards, 3 acquisition words A/B/C):
```
[1] Easy review (warm-up)
[2] Sentence with A (first exposure)
[3] Sentence with B (first exposure)
[4] Review sentence
[5] Sentence with A (re-quiz, gap=2)
[6] Sentence with C (first exposure)
[7] Review sentence
[8] Sentence with B (re-quiz, gap=4)
[9] Sentence with C (re-quiz, gap=2)
[10] Easy review (positive end)
```

### 3.5 OCR Import Reform

**Current**: All OCR words get `submit_review(rating=3)` → FSRS card → "known"

**New options on import screen**:
1. **"Track vocabulary only"** (new default): Creates Lemma entry, no ULK/FSRS card. Words appear in Learn mode candidates and contribute to story readiness calculations.
2. **"Mark as studied today"**: Current behavior. Good for pages just studied.
3. **"Mark as encountered weeks ago"**: Creates FSRS card but with `rating=2` (Hard) instead of Good, and backdates introduction. Appropriate for textbook pages read 1-2 weeks ago.

### 3.6 Batch-Aware Sentence Generation

**Current**: `generate_validated_sentence()` takes a single target word. Generates one sentence per word.

**New**: `generate_session_sentences()` takes a set of 3-5 focus words. Generates 8-10 sentences where each focus word appears in 2-3 sentences.

**LLM prompt change**:
```
Instead of: "Generate a sentence using the word كتاب"
New: "Generate 8 sentences. Each sentence must include at least 2 of these words:
كتاب، مدرسة، قرأ، جديد، كبير. Each word should appear in at least 2 different sentences."
```

**Benefits**:
- Natural cross-reinforcement
- Fewer LLM calls (1 batch call vs 5 individual)
- Better sentence diversity (LLM sees the full set)
- De-emphasizes "primary target" concept

### 3.7 Leech Auto-Management

**Thresholds**:
- **Leech detection**: 6+ reviews with <30% accuracy
- **Auto-suspend**: Move to "suspended" state with `leech_suspended_at` timestamp
- **Auto-reintroduce**: After 14 days, reactivate with fresh FSRS card
- **Leech escalation**: If suspended and reintroduced 3+ times, flag for manual review
- **Root-sibling interference guard**: Don't introduce a new word from a root if a sibling was failed in last 7 days

### 3.8 A/B Testing Infrastructure

**Schema change**: Add `experiment_group` (nullable VARCHAR) to `UserLemmaKnowledge`

**Assignment**: At `introduce_word()` time, randomly assign to "A" or "B" (or null for non-experimental words)

**First experiment**: "acquisition_v1"
- Group A: Acquisition phase (3 within-session exposures, forced day-1 review)
- Group B: Current FSRS-only approach
- Primary metric: Accuracy at first review after 7+ days
- Analysis: Bayesian Beta-Binomial, stop at P(A>B) > 0.95

**Logging**: Experiment assignment logged in `interaction_logger.py` as `experiment_assigned` event.

---

## Part 4: Implementation Phases

### Phase 1: Emergency Data Fix (Day 1)
- [ ] Backfill script to reset textbook_scan FSRS cards
- [ ] Log the action to ActivityLog
- [ ] Verify accuracy improves in next review session

### Phase 2: Acquisition Phase (Days 2-4)
- [ ] Add "acquiring" knowledge_state to models
- [ ] Migration for new state
- [ ] `get_acquiring_words()` in fsrs_service.py
- [ ] Modified `build_session()` for acquisition word repetition
- [ ] Forced day-1 review logic
- [ ] Graduation logic (5+ reviews, 60%+ accuracy)
- [ ] Tests for all new logic
- [ ] Frontend: no changes needed (acquisition words appear as normal sentence cards)

### Phase 3: Focus Cohort (Days 3-5)
- [ ] `get_focus_cohort()` function using word_selector scoring
- [ ] Modified `get_due_cards()` to filter by cohort membership
- [ ] Cohort graduation logic
- [ ] API endpoint to view/manage cohort (optional)
- [ ] Tests

### Phase 4: OCR Import Reform (Day 5-6)
- [ ] Add import mode parameter to `/api/ocr/scan-pages`
- [ ] Frontend: import mode selector on scanner screen
- [ ] "Track only" mode: create Lemma, skip ULK/FSRS
- [ ] "Encountered weeks ago" mode: backdated Hard rating
- [ ] Tests

### Phase 5: Batch Sentence Generation (Days 6-8)
- [ ] `generate_session_sentences()` for word sets
- [ ] LLM prompt for multi-target generation
- [ ] Validation for multi-target sentences
- [ ] Integration with `build_session()`
- [ ] Tests

### Phase 6: Leech Management (Day 8-9)
- [ ] Auto-suspend on leech detection
- [ ] `leech_suspended_at` and `reintroduce_after` fields
- [ ] Cron/background task for auto-reintroduction
- [ ] Root-sibling interference guard in word_selector
- [ ] Tests

### Phase 7: A/B Testing (Day 9-10)
- [ ] `experiment_group` field on ULK
- [ ] Random assignment at introduction
- [ ] Experiment logging
- [ ] Analysis script (Bayesian Beta-Binomial)
- [ ] Dashboard widget (optional)

---

## Part 5: Deep Research Questions (for swarm investigation)

### Research Track A: FSRS Parameter Optimization
1. What are the FSRS-6 default parameters and how do they compare to our actual review data? Can we run the FSRS optimizer on our 1,749 reviews?
2. How does py-fsrs handle the `same-day review` formula (w17/w18/w19)? Is it active in our version?
3. What would optimal initial stability values be for Arabic vocabulary specifically (script difficulty, root-pattern system)?
4. How does FSRS handle cards that get multiple reviews in quick succession (our sentence-centric model)?
5. Should we use different FSRS parameters for textbook_scan vs study vs duolingo words?

### Research Track B: Acquisition Phase Design
1. What exactly do Anki's learning steps do algorithmically? How do they interact with FSRS? Code-level analysis of py-fsrs and anki source.
2. What is the Leitner box system and how does it compare to what we're proposing? Is there a simpler implementation?
3. How does Duolingo handle newly introduced vocabulary? What's their spacing schedule for the first 5 encounters?
4. How does Memrise/Wanikani handle the acquisition→SRS transition? Any published research on their approaches?
5. What's the optimal number of within-session repetitions for Arabic vocabulary? Is there research specific to Semitic languages or non-Latin scripts?

### Research Track C: Session Optimization
1. How do commercial SRS apps (Anki, SuperMemo, Mnemosyne) compose sessions? Is there published research on session-level optimization vs card-level?
2. What does the "desirable difficulties" literature say about session fatigue and cognitive load accumulation? At what point do additional reviews become counterproductive within a session?
3. What's the research on "micro-sessions" (2-5 items)? Are they effective or is there a minimum viable session size?
4. How should the system adapt to variable session lengths (user might do 2 cards or 30 cards)?
5. What's the optimal ratio of new:review:re-quiz items in a session, and does this change with learner proficiency?

### Research Track D: Arabic-Specific Learning
1. What research exists on Arabic vocabulary acquisition by adult L1 English speakers? Any SRS studies?
2. How does the root-pattern morphological system affect vocabulary acquisition rates? Can root knowledge accelerate learning?
3. What's the role of diacritics in word recognition for beginners? Does diacritics-first → diacritics-reduced training improve reading speed?
4. Are there Arabic-specific frequency lists more relevant than CAMeL MSA for a learner context?
5. What published curricula exist for Arabic vocabulary progression (e.g., CEFR alignment)? How do they compare to our frequency-based approach?

### Research Track E: Sentence-Centric Review Model
1. Is there any published research on sentence-level vs word-level SRS? Our model is unusual.
2. What does comprehension research say about inferring unknown words from context? How reliable is the "didn't mark it = knows it" signal?
3. How many unknown words in a sentence is optimal for learning? Research on i+1 (Krashen) vs multiple unknowns.
4. Should sentence difficulty scale with word maturity differently than we currently do?
5. What's the research on re-reading the same sentence vs reading new sentences with the same target word?

### Research Track F: Experimental Design
1. What's the smallest detectable effect size with our data volume (~100-200 reviews/day)?
2. How should we handle the non-stationarity problem (learner improves over time)?
3. Are there published n-of-1 SRS experiments we can model our design on?
4. What statistical tests are appropriate for within-subject vocabulary learning comparisons?
5. How should we handle the root-family confound (words from the same root may have correlated learning rates)?

### Research Track G: Codebase Analysis
1. Read `sentence_selector.py` in detail: how exactly does `build_session()` work, and where should acquisition word repetition be inserted?
2. Read `fsrs_service.py` in detail: how does `submit_review()` create and update cards? What happens with multiple same-day reviews?
3. Read `word_selector.py` in detail: how does the scoring work, and how can we adapt it for focus cohort selection?
4. Read `sentence_generator.py`: how can we modify it for batch generation targeting multiple words?
5. Read the frontend review flow (`index.tsx`): what changes are needed to support acquisition-phase cards that repeat within a session?
6. Analyze the test suite: what existing tests cover the session assembly pipeline?

---

## Part 6: Success Metrics

### Short-term (1 week after Phase 1-3)
- Session accuracy > 60% (up from 25%)
- Words with stability < 1 day > 200 (reflecting corrected FSRS state)
- Average reviews per focus-cohort word per day > 2
- At least 10 words graduating from acquisition phase

### Medium-term (2 weeks after full implementation)
- Session accuracy > 70%
- 50+ words with genuine stability > 7 days
- Comprehension rate "understood" > 40%
- Leech count stable or declining

### Long-term (1 month)
- 100+ words with stability > 14 days
- Session accuracy > 75%
- A/B experiment reaching statistical significance
- Daily new-word introduction rate: 3-5 words
- Focus cohort turning over (words graduating, new ones entering)
