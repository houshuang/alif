# Deep Research Compilation — Learning Algorithm Redesign

> Date: 2026-02-12
> Source: 8 parallel research agents covering FSRS internals, cognitive science, Arabic learning, session design, sentence-centric SRS, leech management, experimental design, and codebase analysis.
> Purpose: Inform the algorithm redesign for Alif's vocabulary learning system.

---

## Table of Contents

1. [Executive Summary: Top 25 Findings](#executive-summary)
2. [FSRS & SRS Algorithm Internals](#fsrs-internals)
3. [Cognitive Science of Memory & Learning](#cognitive-science)
4. [Arabic-Specific Learning Research](#arabic-specific)
5. [Session Design & Optimization](#session-design)
6. [Sentence-Centric SRS Research](#sentence-centric)
7. [Leech Management & Interference](#leech-management)
8. [Experimental Design (N-of-1)](#experimental-design)
9. [Codebase Change Points](#codebase-analysis)
10. [Synthesized Algorithm Proposal](#synthesis)
11. [Master Reference List](#references)

---

## 1. Executive Summary: Top 25 Findings {#executive-summary}

### The Most Important Numbers

| Finding | Value | Source |
|---------|-------|--------|
| Encounters needed for stable vocabulary | 8-12 meaningful | Uchihara 2019 meta-analysis |
| First 2 encounters determine learning most | PMC4277705 | |
| Incidental vocab pickup from reading | 9-18% | Cambridge meta-analysis 2023 |
| Text coverage for adequate comprehension | 98% (1 unknown per 50 words) | Hu & Nation 2000 |
| Effect of glossing on vocab learning | Large (g=1.40) | Abraham 2008 |
| Testing vs restudying: forgetting after 1wk | 10% vs 52% | Karpicke & Roediger 2006 |
| Optimal training accuracy for learning rate | ~85% | Wilson et al. 2019 (Nature Comms) |
| Working memory capacity for new items | ~4 chunks | Cowan 2001 |
| Leeches consume vs normal cards | 10x repetitions | Hacking Chinese |
| Arabic 95% text coverage requires | ~9,000 lemmas | Masrai & Milton 2016 |
| Arabic 79% text coverage requires | ~1,000 lemmas | Masrai & Milton 2016 |
| Full lexical integration after learning | ~1 week (requires sleep) | J. Neuroscience 2013 |
| FSRS-6 initial stability (Good rating) | 2.31 days | FSRS defaults |
| Optimized FSRS beats default in | 84% of cases | FSRS benchmark |
| Optimal gap/retention-interval ratio | 10-20% | Cepeda 2008 |
| Semantic clustering IMPEDES learning | vs unrelated words | Tinkham 1993/1997 |

### The 10 Most Actionable Insights

1. **FSRS has NO native acquisition phase.** Every commercial app (WaniKani, Memrise, Duolingo) uses a separate deterministic acquisition phase (hours→days) before SRS takes over. Alif skips this entirely.

2. **Sleep consolidation is mandatory.** Words aren't truly consolidated until after at least one overnight sleep cycle. Same-day-only reviews cannot achieve what overnight consolidation does (lexical integration, cross-modal transfer).

3. **Self-assessment is unreliable.** Learners' predictions of their performance are uncorrelated with actual performance (Karpicke & Roediger 2008). The word-tapping feature is the critical corrective signal.

4. **Failed retrieval enhances learning.** Kornell, Hays & Bjork (2009) showed that attempting and failing to retrieve an answer before seeing it produces better retention than just studying. "no_idea" ratings have genuine learning value.

5. **Root-family siblings should NOT be introduced simultaneously.** Semantic clustering research (Tinkham 1993/97) shows related words impede learning when presented together. Space root siblings apart.

6. **3 within-session retrievals is the sweet spot.** Nakata (2017): 5-7 retrievals produce highest absolute scores, but 1 retrieval is most time-efficient. 3 retrievals balances learning and efficiency.

7. **Context diversity improves transfer but only with retrieval practice.** Simply reading a word in different sentences doesn't help. Being forced to retrieve meaning in different contexts does (van den Broek 2022).

8. **The 85% accuracy rule maximizes both learning and motivation.** Below 70%, motivation suffers. Above 95%, learning stalls. Target ~85% session accuracy.

9. **py-fsrs 6.x supports same-day reviews natively.** The w17/w18/w19 parameters model within-session stability growth. Upgrade from v4.x to get this.

10. **N-of-1 between-item experiments are feasible.** 80-100 words per condition, 3-4 weeks, Bayesian Beta-Binomial analysis. Can detect 8-10pp differences in retention.

---

## 2. FSRS & SRS Algorithm Internals {#fsrs-internals}

### 2.1 FSRS-6 Parameters (w0-w20)

FSRS-6 uses 21 parameters. Defaults derived from ~10,000 users:

```
[0.212, 1.293, 2.307, 8.296, 6.413, 0.833, 3.019, 0.001,
 1.872, 0.167, 0.796, 1.484, 0.061, 0.263, 1.648, 0.601,
 1.873, 0.543, 0.091, 0.066, 0.154]
```

**Initial stability (w0-w3):**
- Again: 0.212 days (~5 hours)
- Hard: 1.293 days
- Good: 2.307 days
- Easy: 8.296 days

**Same-day review formula (w17-w19, new in FSRS-6):**
```
S'(S, G) = S × exp(w17 × (G - 3 + w18)) × S^(-w19)
```
Progression example: 1.87min → 13.88min → 6.26h → 1.08d

**Retrievability (FSRS-6 trainable power law):**
```
R(t, S) = (1 + factor × t/S)^(-w20)
where factor = 0.9^(-1/w20) - 1
```

### 2.2 py-fsrs 6.x API

Current version: **6.3.0**. Key changes from v4:
- Learning steps support: `Scheduler(learning_steps=(timedelta(minutes=1), timedelta(minutes=10)))`
- Same-day review modeling via w17-w19
- Built-in optimizer: `pip install "fsrs[optimizer]"`
- `Card.step` attribute tracks learning/relearning step index

**Alif currently uses `fsrs>=4.0.0`** — should upgrade to `>=6.0.0`.

### 2.3 FSRS Optimizer

```python
from fsrs import Optimizer, Scheduler
optimizer = Optimizer(review_logs)
optimal_params = optimizer.compute_optimal_parameters()  # 21-tuple
optimal_retention = optimizer.compute_optimal_retention(optimal_params)
```

- **Minimum data**: ~100 reviews for pretrain (first 4 params only), ~1,000 for full optimization
- **Improvement**: Optimized beats default in 84% of cases
- **Pretrain mode**: Only optimizes w0-w3 (initial stability), keeps rest at defaults

### 2.4 Anki Learning Steps + FSRS

Learning steps are a **separate scheduling layer on top of FSRS**:
1. New card enters Learning state (step=0)
2. Good/Easy advances through steps (1m, 10m by default)
3. After final step, card **graduates** to Review state
4. FSRS computes initial stability from first rating
5. Rating Again returns to step=0

**Expertium recommends**: Single learning step of 15-30 minutes. Multiple short steps provide negligible benefit with FSRS-6.

### 2.5 Comparison: FSRS vs Leitner vs SuperMemo vs HLR

| Aspect | FSRS-6 | Leitner | SM-17 | Duolingo HLR |
|--------|--------|---------|-------|-------------|
| Parameters | 21 (optimizable) | 0 (fixed boxes) | Matrix-based | Per-word features |
| Personalization | Per-card S, D | None | Per-item | Per-word half-life |
| Acquisition phase | Via learning_steps | Natural (Box 1) | First forgetting curve | Continuous model |
| Simplicity | Complex | Trivial | Very complex | Medium |
| Published accuracy | 99.6% > SM-2 | N/A | N/A | 45% < HLR error |

### 2.6 Commercial App Acquisition Phases

**WaniKani** (9-stage SRS):
- Apprentice 1-4: 4h → 8h → 1d → 2d (acquisition phase)
- Guru → Master → Enlightened → Burned (long-term SRS)

**Memrise** (fixed ladder):
- 4h → 12h → 24h → 6d → 12d → 48d → 96d → 6mo
- Wrong at any stage: reset to 4h

**Common pattern**: All use deterministic, short-interval acquisition (hours→days) before adaptive SRS.

### 2.7 Source-Differentiated Scheduling

No native FSRS support, but cognitively well-justified:

```python
SOURCE_STABILITY_MULTIPLIER = {
    "study": 1.0,        # Active Learn mode
    "auto_intro": 0.8,   # Auto-introduced
    "collocate": 0.7,    # Learned by association
    "duolingo": 1.5,     # Previously learned
    "encountered": 0.5,  # Passive encounter (OCR)
    "textbook_scan": 0.3 # Saw on page (weakest)
}
```

---

## 3. Cognitive Science of Memory & Learning {#cognitive-science}

### 3.1 Memory Consolidation

- New words initially stored as hippocampal episodic traces
- During sleep: reactivated and redistributed to neocortex (Active Systems model)
- **NREM slow-wave sleep**: memory reactivation and stabilization
- **REM sleep**: synaptic refinement and integration
- Sleep spindle density correlates with overnight vocabulary retention
- Full lexical integration (word competing in lexical decisions) takes ~1 week
- "The rich get richer" — larger vocabularies consolidate new words better

**Key implication**: First review MUST occur after sleep. Same-day-only repetition cannot substitute for overnight consolidation.

### 3.2 Encoding Variability

- Different contexts create multiple retrieval cues
- **But**: Simply varying sentence context without retrieval practice does NOT improve retention (PMC4088266)
- **With retrieval**: Variable contexts + forced recall → better learning than constant contexts (PubMed 21286980)
- **Critical distinction**: Encoding variability helps ONLY when combined with retrieval practice

### 3.3 Desirable Difficulties (Bjork)

Four primary desirable difficulties:
1. **Spacing** (distributing practice)
2. **Interleaving** (mixing categories)
3. **Testing/retrieval** (recalling vs re-reading)
4. **Generation** (producing vs recognizing)

Generation effect: ~d=0.40 (moderate)
Interleaving: g=0.67 for perceptual categories

**Boundary**: Difficulty becomes undesirable when learner cannot succeed at all. Low-achieving learners need initial blocking before interleaving (Hwang 2025).

### 3.4 The Testing Effect

Roediger & Karpicke 2006:
- After 1 week: restudied group forgot **52%**, tested group forgot only **10%**
- Testing appears WORSE short-term but DRAMATICALLY better long-term
- Self-assessment ≠ retrieval practice. Recall > recognition for retention.

### 3.5 Spacing Effect (Cepeda 2006/2008)

Optimal gap as proportion of desired retention interval:

| Desired Retention | Optimal Gap | Gap/Delay Ratio |
|-------------------|-------------|-----------------|
| 1 week | 1-2 days | ~20-40% |
| 1 month | 3-7 days | ~10-20% |
| 3 months | 7-14 days | ~10-15% |
| 1 year | 21-35 days | ~5-10% |

### 3.6 Cognitive Load & Working Memory

- Working memory: ~4 chunks (Cowan 2001), not 7±2
- **Word span: ~5 items**
- L2 working memory further reduced (novel phonological forms can't be chunked)
- Arabic increases intrinsic load: root-pattern system, diacritics, clitics, script

**Practical limit**: 3-5 truly new words per session

### 3.7 Interference

- **Proactive**: Old knowledge disrupts new learning
- **Retroactive**: New learning disrupts old recall
- Driven by similarity — phonological and semantic
- Arabic root-pattern system: partial consonantal overlap creates significant interference (PMC3856529)
- Root priming is automatic regardless of semantic transparency

### 3.8 Storage Strength vs Retrieval Strength (Bjork)

| Bjork | FSRS | Key Property |
|-------|------|-------------|
| Storage Strength | Stability (S) | Only accumulates, never decreases |
| Retrieval Strength | Retrievability (R) | Fluctuates with time |

**Central paradox**: Storage strength gains are GREATEST when retrieval strength is LOW. Reviewing something you can barely remember produces the largest learning gains.

→ FSRS's 90% retrievability target may be conservative. Allowing R to drop to 80-85% could produce larger stability gains per review.

### 3.9 Metacognitive Illusions

- Foresight bias: learners overestimate performance by ~15pp for difficult items (Koriat & Bjork 2006)
- Processing fluency ≠ learning (understanding in context ≠ knowing the word)
- Lower-proficiency learners overestimate more (Dunning-Kruger)
- Students' predictions uncorrelated with actual performance (Karpicke & Roediger 2008)

### 3.10 The 85% Rule

Wilson et al. 2019 (Nature Communications):
- Optimal error rate for learning: ~15.87% (accuracy ~84.13%)
- Training at this rate produces exponential improvements in learning speed
- Too easy (>95%): nothing left to learn
- Too hard (<70%): approaching chance, no learning signal

---

## 4. Arabic-Specific Learning Research {#arabic-specific}

### 4.1 Key Numbers

| Metric | Value |
|--------|-------|
| FSI Category V (hardest) | 2,200 hours to proficiency |
| 79% text coverage | ~1,000 lemmas |
| 89% coverage | ~5,000 lemmas |
| 95% coverage | ~9,000 lemmas |
| 98% coverage | ~14,000 lemmas |
| Trilateral roots (Hans Wehr) | 2,967 |
| Roots for 80% daily vocab | ~500 |
| AVP A1 validated list | 1,750 items |
| MSA-dialect lexical overlap | 33-63% |

### 4.2 Diacritics: Always Show

**Midhwah (2020, Modern Language Journal, n=54)**: Vowelized textbook groups outperformed unvowelized at ALL proficiency levels on speed, accuracy, AND comprehension. No evidence diacritics hinder later unvowelized reading. **Alif's always-show policy is strongly supported.**

### 4.3 Root Awareness

- Root awareness accounts for substantial variance in reading outcomes
- L2 learners rely on roots in 87.5% of encounters with unknown words
- Even non-native speakers organize L2 Arabic lexicons around root morphemes
- Root effects are "reliably present throughout the recognition process"

**However**: Introducing multiple root siblings simultaneously causes interference (semantic clustering effect, Section 6).

### 4.4 Arabic Morphological Complexity

- Form I verbs: ~60-70% of all verb usage
- Recommended form learning order: I → II/IV → V/VIII → X/III → VI/VII/IX
- Morphological density independently impacts reading comprehension beyond vocabulary coverage
- Arabic's clitic system means "known" words can be hard to recognize with attached clitics

### 4.5 Listening

- Listening anxiety is a separate phenomenon from general FL anxiety (Elkhafaifi 2005)
- Pre-listening vocabulary preview significantly improves comprehension
- All participants improved after second exposure to listening passages
- Alif's listening-ready filter (times_seen ≥ 3, stability ≥ 7d) is appropriate scaffolding

### 4.6 CAMeL Tools Validation

Benchmark (Noor-Ghateh 2023): CAMeL achieves highest accuracy across datasets (0.68-0.81), outperforming Farasa (0.59-0.81). Correct choice for Alif.

### 4.7 Arabic Frequency Lists

Best available for MSA learners:
- **CAMeL MSA corpus** (12.6B tokens) — currently used in Alif
- **Aralex** (Boudelaa & Marslen-Wilson 2010) — 40K entries with psycholinguistic variables
- **Kelly Project** — CEFR-aligned, currently used in Alif
- **AVP A1** (2025) — newest, expert-validated by 71 teachers, 1,750 items

---

## 5. Session Design & Optimization {#session-design}

### 5.1 Optimal Session Length

- **Sweet spot: 10-20 items, 10-20 minutes**
- Below 5 items: insufficient within-session spacing
- Above 30 items/30 minutes: cognitive fatigue degrades encoding
- Baddeley & Longman (1978): shorter, distributed sessions dramatically more efficient
- Anki community: 20 new/day → ~200 reviews/day is upper limit of sustainability

### 5.2 Micro-Sessions (2-5 items)

- Effective IF they include re-quiz with intervening items
- Duolingo found making lessons shorter actually hurt learning metrics
- Minimum viable: enough items for temporal spacing between retrievals of same item
- A 2-item session with no re-quiz = massed practice (worst)

### 5.3 Session Ordering (Serial Position Effect)

- Items at beginning (primacy) and end (recency) recalled best
- Middle items get least effective encoding
- **Alif's easy-bookend ordering is well-supported:**
  1. Easy items first (warm-up, build self-efficacy)
  2. Hard items in middle (focused attention, not yet fatigued)
  3. Easy items last (recency effect, positive emotional state)

### 5.4 Within-Session Retrieval Spacing

**Karpicke & Roediger 2007**: Expanding retrieval (1→2→4→7) vs equal spacing (3→3→3→3):
- Expanding: better immediate recall
- Equal: better on 2-day delayed test
- **Key finding**: Placement of FIRST retrieval matters more than schedule type

**Nakata 2015** (L2 specifically): Limited but significant advantage for expanding spacing.

**Nakata 2017**: 3-5 retrievals per session optimal. 1 retrieval most time-efficient. Diminishing returns after 3.

**Pimsleur within-session**: 5s → 25s → 2min → 10min (first 4 intervals within one lesson)

### 5.5 New:Review Ratios

- No research directly testing optimal ratio
- Ratio is emergent from: available time, retention target, queue depth
- **Anki default**: 20 new/day (generates ~200 reviews)
- Nakata & Webb (2016): Spacing matters more than set size
- **Practical**: 15 minutes → prioritize reviews. 45 minutes → can afford new items.

### 5.6 Interleaving

- **For established words**: Interleaving (mixing categories) outperforms blocking on delayed tests
- **For new words**: Initial blocking helps, then interleave (Hwang 2025)
- **Semantically similar** words benefit most from interleaving
- Arabic root siblings = high semantic similarity → benefit from interleaving AFTER initial learning

### 5.7 Adaptive Difficulty

No direct research on increasing vs decreasing difficulty within sessions. Best model:
1. **Warm-up** (easy, build momentum)
2. **Peak** (hardest items, attention highest)
3. **Cool-down** (easy, recency effect, positive exit)

### 5.8 Gamification

Sailer & Homner 2020 meta-analysis:
- Cognitive learning outcomes: g=0.49 (real learning effect, not just engagement)
- Novelty effect wears off at ~week 4, partially recovers weeks 6-10
- Streaks: 2.3x more likely to engage daily after 7+ day streak
- **Tie rewards to mastery, not participation**

---

## 6. Sentence-Centric SRS Research {#sentence-centric}

### 6.1 No Published Sentence-Level SRS Studies

No rigorous studies directly compare sentence-level to word-level SRS. The "sentence mining" community operates on i+1 principle (one unknown per sentence) but without controlled studies.

**Webb 2007**: Word pairs more effective for form-meaning connection, but sentences provide additional grammar/collocation/usage knowledge.

### 6.2 The Comprehension-Retrieval Paradox

**Van den Broek (2018)**: "Context enhances comprehension but retrieval enhances retention."
- Informative sentence context → better immediate understanding but worse long-term retention
- Uninformative context → forces retrieval → better retention
- **In narrative context**: Informative story context led to higher posttest results

**Implication**: Alif's sentence-first model (read Arabic → self-assess) is comprehension-first. Adding retrieval (e.g., recall meaning before reveal) would strengthen retention.

### 6.3 Text Coverage & Unknown Word Density

- **98%** coverage (1 unknown per 50 words): adequate comprehension (Hu & Nation 2000)
- **95%** (1 unknown per 20): minimally acceptable
- **80%** (1 unknown per 5): comprehension inadequate
- Sentence mining consensus: exactly 1 unknown element per sentence (i+1)
- For 10-word Arabic sentence: 0-1 unknown words ideal

### 6.4 Sentence Re-Reading vs New Sentences

- Diverse contexts → better generalization to new contexts (Norman 2023)
- Same context → better for familiar contexts
- **Optimal**: Different sentences on different reviews when word is being learned; only recycle misunderstood sentences

### 6.5 Noticing Hypothesis (Schmidt)

- "Subliminal language learning is impossible" — conscious attention required
- Tap-to-lookup = explicit act of noticing → strongly supported
- Click-to-translate glosses: large effect (d=1.40) on vocabulary learning

### 6.6 Collateral Word Learning

- Giving FSRS credit (rating 3) to all unmarked words is a reasonable heuristic
- For already-known words: reading them in context IS a legitimate review
- For brand-new words: creating FSRS cards is more aggressive than research strictly supports
- The credit_type metadata distinction is useful for future analysis

### 6.7 Self-Assessment Accuracy

- Ternary self-assessment (understood/partial/no_idea) is appropriately simple
- Beginners will over-rate comprehension
- Word-tapping catches specific gaps that global self-assessment misses
- "no_idea" may be the most accurately calibrated rating (total confusion is hard to mistake)

---

## 7. Leech Management & Interference {#leech-management}

### 7.1 Leech Detection

- **Anki default**: 8 lapses. Experienced users recommend **4-6**.
- FSRS developer: Use lapses, NOT difficulty parameter, for leech detection
- **2.5% of material can consume 50% of study time** (SuperMemo data)
- Leeches take ~10x as many repetitions as normal cards

**Recommended for Alif**: 5+ lapses OR accuracy < 50% over last 10 reviews

### 7.2 Root-Family Interference

**Critical finding**: Semantic clustering (learning related words together) IMPEDES learning (Tinkham 1993/97, replicated multiple times).

- Root siblings are inherently semantically related
- Interference worsened with more repetitions for related words
- **Thematic clustering** (words connected by scenario) HELPS — unlike semantic clustering
- Root siblings should be taught within thematic contexts (stories), not as semantic groups

**Optimal spacing for root siblings**: Wait until first sibling has FSRS stability > 7 days before introducing next. Alif's word_selector root familiarity score (30%, peaking at 30-60%) is well-aligned.

### 7.3 Error-Driven Learning

**Kornell, Hays & Bjork (2009)**: Attempted-and-failed retrieval → better later performance than just studying.

- Even items NOT successfully retrieved on pretest showed learning benefit
- Mechanism: failed retrieval activates semantic network, priming correct answer
- Retrieval effort hypothesis: difficult, failed retrieval may provide MORE learning than easy success
- **"no_idea" ratings have genuine learning value** — don't over-penalize

### 7.4 Productive Failure (Kapur)

Meta-analysis (Sinha & Kapur 2021): PF outperforms instruction-first for conceptual understanding (d=0.36). Higher fidelity → d=0.58.

Maps to "no_idea" scenario: struggle → see answer → better encoding.

### 7.5 Leech Treatment Pipeline

1. **Detect**: 5+ lapses OR accuracy < 50% over last 10 reviews
2. **Diagnose**: Interference (confusable pair)? Missing knowledge? Bad context?
3. **Treat**:
   - Interference: Suspend weaker word, teach other first
   - Missing knowledge: Add morphological/root context
   - Bad context: Generate new sentences
4. **Reintroduce**: Set due date 3-7 days after treatment, preserve FSRS history (don't reset card)
5. **Monitor**: If recurs, escalate (different mnemonic, different context, explicit root teaching)

### 7.6 Confusable Pairs

- **Contrasting** similar words eventually helps but only for skilled readers (Baxter 2021)
- Strategy: Suspend one → master the other → reintroduce with explicit discrimination
- Track which words are confused with each other (within-session error patterns)

### 7.7 Morphological Awareness as Leech Prevention

- Root awareness reduces arbitrary form-meaning associations
- Understanding root + pattern creates durable, connected memory traces
- Arabic's root system is especially suited to explicit morphological instruction for adults
- **Root prediction feature is well-supported** by research

### 7.8 Vocabulary Size and Interference Thresholds

| Words Known | Interference Level | Focus |
|-------------|-------------------|-------|
| 0-500 | Low | High-frequency vocabulary |
| 500-1,000 | Moderate | Similar roots begin overlapping |
| 1,000-2,000 | High (the "plateau") | Leech management critical |
| 2,000+ | Dominant | Discrimination strategies essential |

---

## 8. Experimental Design (N-of-1) {#experimental-design}

### 8.1 Between-Item Alternating Treatments Design

Best design for comparing SRS algorithms in a single-user app:
- **Unit of randomization**: Individual vocabulary items (lemma_id)
- **Both conditions run simultaneously** on different words
- No washout needed, no temporal carryover
- Natural within-subject control

### 8.2 Bayesian Beta-Binomial Analysis

```
Prior: Beta(0.5, 0.5)  (Jeffrey's non-informative)
After k successes in n trials: Beta(0.5 + k, 0.5 + n - k)
Compare: P(p_A > p_B | data)
ROPE: [-0.05, 0.05] for practical equivalence
```

### 8.3 Sample Size

- **50 words/condition minimum**, 80-100 recommended
- With ICC=0.3 and 5 reviews/word: effective n~114 per condition
- **Detectable effects at 80% power:**

| Difference | Words/condition needed |
|------------|----------------------|
| 15pp | ~50 |
| 10pp | ~100 |
| 8pp | ~150 |
| 5pp | ~400+ |

### 8.4 Stopping Rules

1. Minimum: 3 reviews/word in both conditions before analysis
2. Check weekly
3. Stop for superiority: P(A>B) > 0.975 AND ≥5 reviews/word
4. Stop for equivalence: 95% HDI within ROPE [-0.05, 0.05]
5. Maximum: 6 weeks

### 8.5 Confound Control

- Stratified block randomization by: frequency_rank bin, POS, CEFR level
- Block-randomize root families (all siblings in same condition)
- Include time-trend covariate in analysis
- Balanced introduction schedule (equal new words/condition/week)

### 8.6 Non-Stationarity

- Random between-item assignment is primary defense (both conditions affected equally)
- Include day/review_number as covariate
- Counterbalanced introduction batches
- Accept that cross-item transfer is symmetric with proper randomization

### 8.7 Multiple Experiments

- **Start single-factor** (one experiment at a time)
- Maximum 2 concurrent experiments on non-overlapping word sets
- 2×2 factorial possible but reduces words/cell to ~25 (underpowered for interactions)

### 8.8 First Experiment Recommendation

**"Acquisition phase vs FSRS-only":**
- Group A: 3 within-session exposures + forced day-1 review before FSRS
- Group B: Current approach (straight to FSRS)
- Primary metric: Accuracy at first review after 7+ days
- 80-100 words per group, 3-4 weeks

---

## 9. Codebase Change Points {#codebase-analysis}

### 9.1 Key Files and Where Changes Go

| Feature | Primary File(s) | Key Functions | New Schema |
|---------|-----------------|---------------|------------|
| **Acquisition phase** | `fsrs_service.py:submit_review()` L108, `word_selector.py:introduce_word()` L276 | New `submit_acquisition_step()`, check before FSRS call | `ULK.acquisition_step`, `acquisition_graduated_at` |
| **Focus cohort** | `sentence_selector.py:build_session()` L199, `word_selector.py:select_next_words()` L182 | New `get_focus_cohort()`, modify scoring | `ULK.cohort_entered_at`, `cohort_exited_at` |
| **Session repetition** | `sentence_selector.py` Stage 4 (greedy set cover) L481 | Change `remaining_due -= covered` to decrement-count | Transient (session-scoped) |
| **OCR reform** | `ocr_service.py:process_textbook_page()` L442 | Remove `submit_review(rating=3)` calls at L529/558/615 | Possibly `ULK.knowledge_state="encountered"` |
| **Batch sentence gen** | `sentence_generator.py`, `llm.py` | New `generate_multi_target_sentence()` | Multi-target via SentenceWord.is_target_word |
| **Leech management** | `sentence_review_service.py`, `sentence_selector.py` | Extend struggling detection L240 | `ULK.leech_count`, `is_leech` |
| **A/B testing** | `fsrs_service.py` L13 (module-level Scheduler) | Per-experiment Scheduler instances | `ReviewLog.experiment_id`, `ULK.experiment_group` |

### 9.2 Critical Code Points

**`sentence_selector.py:build_session()`** (lines 199-605) — 6-stage pipeline:
- Stage 1 (L219-248): Fetch due words → **INSERT acquisition/cohort filtering here**
- Stage 3 (L316-479): Score candidates → **INSERT cohort boost factor here**
- Stage 4 (L481-518): Greedy set cover → **MODIFY for word repetition**
- Score formula (L471): `(len(due_covered) ** 1.5) * dmq * gfit * diversity * freshness`

**`fsrs_service.py:submit_review()`** (L108-203):
- L152-163: FSRS scheduling → **INSERT acquisition check before FSRS call**
- L13: Module-level `scheduler = Scheduler()` → **Must become configurable for A/B testing**

**`ocr_service.py:process_textbook_page()`** (L442-699):
- L529, 558, 615: `submit_review(rating_int=3)` → **Remove for track-only mode**

### 9.3 Test Coverage Gaps

- No tests for word progression through learning stages
- No session-level word repetition tests
- No focus cohort or acquisition phase tests
- No leech auto-suspension tests
- No A/B testing infrastructure tests
- OCR tests validate the WRONG behavior (confirm rating=3 is "expected")
- No full review lifecycle integration test

### 9.4 Migration Needed

New fields on `UserLemmaKnowledge`:
- `acquisition_step` (Integer, nullable)
- `acquisition_graduated_at` (DateTime, nullable)
- `cohort_entered_at` (DateTime, nullable)
- `leech_count` (Integer, default 0)

New fields on `ReviewLog`:
- `experiment_id` (String, nullable)
- `is_acquisition` (Boolean, default False)

---

## 10. Synthesized Algorithm Proposal {#synthesis}

### 10.1 Three-Phase Word Lifecycle

```
ENCOUNTERED → ACQUIRING → LEARNING/KNOWN/LAPSED
     ↓              ↓              ↓
  No FSRS    Leitner-like     FSRS scheduling
  No reviews   3-box system     Standard reviews
  Track only   4h → 1d → 3d    Expanding intervals
```

**Phase 1: Encountered** (OCR imports, story words)
- Lemma exists in DB, no ULK record or ULK with `knowledge_state="encountered"`
- Appears in Learn mode candidates with appropriate scoring
- No FSRS card, no reviews
- Contributes to story readiness and text coverage calculations

**Phase 2: Acquiring** (after user chooses to learn in Learn mode)
- Leitner-like 3-box system: Box 1 (4h) → Box 2 (1d) → Box 3 (3d)
- Appears 2-3x per session at expanding positions
- Forced day-1 review regardless of box position
- Graduation: 5+ reviews AND accuracy ≥ 60%
- On graduation: Create FSRS card with initial stability based on acquisition performance

**Phase 3: FSRS** (after graduation from acquisition)
- Standard FSRS-6 scheduling
- Focus cohort membership determines review priority
- Leech detection at 5+ lapses or <50% accuracy

### 10.2 Focus Cohort

- Rolling window of **30-50 words** getting intensive treatment
- Entry: word introduced via Learn mode (auto-enters cohort)
- Exit: FSRS stability > 7 days AND 5+ reviews AND accuracy > 60%
- **Words outside cohort don't appear as due** (except as sentence scaffold)
- Effect: each word reviewed 2-3x/day instead of once/6-days

### 10.3 Session Template (10 cards)

```
[1] Easy review (warm-up, bookend)
[2] ACQUIRING word A — first exposure
[3] ACQUIRING word B — first exposure
[4] Due review (cohort word)
[5] ACQUIRING word A — re-quiz (gap=2)
[6] ACQUIRING word C — first exposure
[7] Due review (cohort word)
[8] ACQUIRING word B — re-quiz (gap=4)
[9] ACQUIRING word C — re-quiz (gap=2)
[10] Easy review (positive end, bookend)
```

### 10.4 Sentence Generation: Multi-Target

Instead of one sentence per target word:
```
Generate 8 sentences. Each must include at least 2 of these focus words:
كتاب، مدرسة، قرأ، جديد، كبير
Each word should appear in at least 2 different sentences.
```

### 10.5 OCR Import Reform

Three modes on import screen:
1. **"Track only"** (new default): Lemma entry, no ULK/FSRS. Words appear in Learn candidates.
2. **"Mark as studied today"**: Current behavior (for pages just studied)
3. **"Mark as encountered"**: ULK with `knowledge_state="encountered"`, no FSRS card

### 10.6 Leech Auto-Management

Detection → Diagnosis → Treatment → Reintroduction → Monitoring

- Detection: 5+ lapses OR <50% accuracy (last 10)
- Auto-suspend with `leech_suspended_at` timestamp
- Auto-reintroduce after 14 days with fresh acquisition phase
- Root-sibling interference guard: don't introduce if sibling failed in last 7 days
- Escalation: 3+ leech cycles → flag for manual review

### 10.7 Target Accuracy: 85%

- Adjust new word introduction rate to maintain ~85% session accuracy
- Below 75%: pause new introductions, focus on consolidation
- Above 92%: increase introduction rate
- Track rolling 20-review accuracy window

---

## 11. Master Reference List {#references}

### Memory & Cognition
- Cepeda et al. (2006). Distributed practice in verbal recall tasks. *Psychological Bulletin*. [PubMed](https://pubmed.ncbi.nlm.nih.gov/16719566/)
- Cepeda et al. (2008). Spacing effects in learning. *Psychological Science*. [PDF](https://laplab.ucsd.edu/articles/Cepeda%20et%20al%202008_psychsci.pdf)
- Cowan (2001). Magical number 4 in short-term memory. *BBS*. [PubMed](https://pubmed.ncbi.nlm.nih.gov/11515286/)
- Karpicke & Roediger (2006/2008). Test-enhanced learning. *Science*. [PDF](http://psychnet.wustl.edu/memory/wp-content/uploads/2018/04/Karpicke-Roediger-2008_Sci.pdf)
- Koriat & Bjork (2006). Illusions of competence. *Memory & Cognition*. [PDF](https://bjorklab.psych.ucla.edu/wp-content/uploads/sites/13/2016/07/Koriat_Bjork_2006_MC.pdf)
- Kornell, Hays & Bjork (2009). Unsuccessful retrieval enhances learning. [PubMed](https://pubmed.ncbi.nlm.nih.gov/19586265/)
- Wilson et al. (2019). The 85% rule. *Nature Communications*. [Link](https://www.nature.com/articles/s41467-019-12552-4)
- Bjork & Bjork (1992). A new theory of disuse. [PDF](https://bjorklab.psych.ucla.edu/wp-content/uploads/sites/13/2016/07/RBjork_EBjork_1992.pdf)
- Bjork & Kroll (2015). Desirable difficulties in vocabulary learning. [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC4888598/)

### Vocabulary Learning
- Uchihara, Webb & Yanagisawa (2019). Repetition and incidental vocabulary learning. *Language Learning*. [Wiley](https://onlinelibrary.wiley.com/doi/abs/10.1111/lang.12343)
- Hu & Nation (2000). Unknown vocabulary density and reading comprehension. [PDF](https://www.wgtn.ac.nz/lals/resources/paul-nations-resources/paul-nations-publications/publications/documents/2000-Hu-Density-and-comprehension.pdf)
- Waring & Takaki (2003). At what rate do learners learn words from graded readers? [ERIC](https://files.eric.ed.gov/fulltext/EJ759833.pdf)
- Van den Broek et al. (2018/2022). Context and retrieval in vocabulary learning. [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC9285746/)
- Norman et al. (2023). Context diversity and word learning. [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC10280660/)
- Laufer & Hulstijn (2001). Involvement Load Hypothesis. *Language Learning*. [Wiley](https://onlinelibrary.wiley.com/doi/abs/10.1111/0023-8333.00164)
- Tinkham (1993/1997). Semantic clustering impedes learning. [SAGE](https://journals.sagepub.com/doi/10.1191/026765897672376469)
- Abraham (2008). Computer-mediated glosses meta-analysis. [Taylor & Francis](https://www.tandfonline.com/doi/abs/10.1080/09588220802090246)
- Nakata (2015). Expanding vs equal spacing for L2 vocabulary. *SSLA*. [Cambridge](https://www.cambridge.org/core/journals/studies-in-second-language-acquisition/article/abs/effects-of-expanding-and-equal-spacing-on-second-language-vocabulary-learning/D1D796306985C52F9BE7A1200AC50DB9)
- Nakata (2017). Within-session repeated retrieval. *SSLA*. [Cambridge](https://www.cambridge.org/core/journals/studies-in-second-language-acquisition/article/abs/does-repeated-practice-make-perfect-the-effects-of-withinsession-repeated-retrieval-on-second-language-vocabulary-learning/F14BA8A576CD2563D14CEA46E35D842E)
- Nakata & Webb (2016). Vocabulary set size. *SSLA*. [Cambridge](https://www.cambridge.org/core/journals/studies-in-second-language-acquisition/article/abs/does-studying-vocabulary-in-smaller-sets-increase-learning/E17B75ABAE1300734AF014C363D59FBC)

### Arabic-Specific
- Midhwah (2020). Arabic diacritics and L2 reading. *Modern Language Journal*. [Wiley](https://onlinelibrary.wiley.com/doi/10.1111/modl.12642)
- Masrai & Milton (2016/2019). Arabic vocabulary size and coverage. [JALLR](http://www.jallr.com/index.php/JALLR/article/view/213)
- Elkhafaifi (2005). Listening anxiety in Arabic. *Modern Language Journal*. [Wiley](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-4781.2005.00275.x)
- Noor-Ghateh (2023). Arabic NLP tool benchmark. [arXiv](https://arxiv.org/html/2307.09630)
- Abu-Rabia. Arabic vowels and reading comprehension. [Springer](https://link.springer.com/article/10.1023/A:1023291620997)
- AVP A1 Dataset. [Link](https://lailafamiliar.github.io/A1-AVP-dataset/)

### SRS Algorithms
- FSRS-6 Algorithm. [Wiki](https://github.com/open-spaced-repetition/fsrs4anki/wiki/The-Algorithm)
- Expertium Technical Explanation. [Blog](https://expertium.github.io/Algorithm.html)
- FSRS Benchmark. [Link](https://expertium.github.io/Benchmark.html)
- py-fsrs. [GitHub](https://github.com/open-spaced-repetition/py-fsrs)
- Settles & Meeder (2016). Half-Life Regression. [PDF](https://research.duolingo.com/papers/settles.acl16.pdf)
- SM-17 Algorithm. [SuperMemo](https://supermemo.guru/wiki/Algorithm_SM-17)
- WaniKani SRS Stages. [Link](https://knowledge.wanikani.com/wanikani/srs-stages/)

### Session Design
- Baddeley & Longman (1978). Training postal workers. *Ergonomics*. [PDF](https://gwern.net/doc/psychology/spaced-repetition/1978-baddeley.pdf)
- Karpicke & Roediger (2007). Expanding retrieval practice. *M&C*. [PubMed](https://pubmed.ncbi.nlm.nih.gov/17576148/)
- Storm, Bjork & Storm (2010). Expanding vs uniform retrieval. [PDF](https://bjorklab.psych.ucla.edu/wp-content/uploads/sites/13/2016/07/Storm_Bjork_Storm_2010.pdf)
- Hwang (2025). Undesirable difficulty of interleaving. *Language Learning*. [Wiley](https://onlinelibrary.wiley.com/doi/10.1111/lang.12659)
- Sailer & Homner (2020). Gamification meta-analysis. [Springer](https://link.springer.com/article/10.1007/s10648-019-09498-w)

### Leech Management
- Anki Leeches Manual. [Link](https://docs.ankiweb.net/leeches.html)
- Control-Alt-Backspace: Dealing with Leeches. [Link](https://controlaltbackspace.org/leech/)
- Sense et al. (2016). Rate of forgetting is stable. *ToCS*. [Wiley](https://onlinelibrary.wiley.com/doi/full/10.1111/tops.12183)
- Zaidi et al. (2020). Adaptive forgetting curves. [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC7334729/)

### Experimental Design
- Lillie et al. (2011). The N-of-1 clinical trial. [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC3118090/)
- Dallery et al. (2013). Single-case experimental designs. [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC3636286/)
- Kruschke (2018). ROPE framework. [SAGE](https://journals.sagepub.com/doi/10.1177/2515245918771304)
- Oleson (2010). Bayesian credible intervals for N-of-1. [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC3307549/)
- Rafferty et al. (2019). MAB in education experiments. [JEDM](https://jedm.educationaldatamining.org/index.php/JEDM/article/view/357)
- Baayen et al. (2008). Mixed-effects models. [ScienceDirect](https://www.sciencedirect.com/science/article/pii/S0749596X07001398)
