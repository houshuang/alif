# Alif Scheduling System — Complete Reference

> This document describes the entire learning pipeline: how words enter the system,
> how they progress through acquisition and long-term retention, how sessions are
> assembled, how reviews are processed, and how supporting systems (leeches, cohorts,
> topics, grammar, listening) interact. It also identifies where the current
> implementation diverges from the research and stated intentions.
>
> **Last updated**: 2026-02-13
> **Canonical location**: `docs/scheduling-system.md`
> **Keep this document up to date with every algorithm change.**

---

## Table of Contents

1. [Design Philosophy](#1-design-philosophy)
2. [Word Lifecycle Overview](#2-word-lifecycle-overview)
3. [Entry Points — How Words Enter the System](#3-entry-points)
4. [Phase 1: Encountered](#4-phase-1-encountered)
5. [Phase 2: Acquiring (Leitner 3-Box)](#5-phase-2-acquiring)
6. [Phase 3: FSRS-6 (Long-Term Retention)](#6-phase-3-fsrs-6)
7. [Focus Cohort — Controlling Review Bandwidth](#7-focus-cohort)
8. [Session Building — The Core Algorithm](#8-session-building)
9. [Review Processing — How Ratings Flow](#9-review-processing)
10. [Sentence Pipeline — Generation, Validation, Retirement](#10-sentence-pipeline)
11. [Leech Management](#11-leech-management)
12. [Topical Learning Cycles](#12-topical-learning-cycles)
13. [Grammar Tracking](#13-grammar-tracking)
14. [Listening Mode](#14-listening-mode)
15. [Story Mode & Reading](#15-story-mode)
16. [Learn Mode — Manual Word Introduction](#16-learn-mode)
17. [Offline & Sync](#17-offline--sync)
18. [Key Constants Reference](#18-key-constants-reference)
19. [Divergences: Implementation vs. Research/Intentions](#19-divergences)

---

## 1. Design Philosophy

### North Star Metric

> "The number of words I genuinely know is significantly increasing week over week."

Not review count. Not streak days. Not inflated FSRS stability. Genuine, testable
reading vocabulary growing steadily.

### Core Principles

1. **Sentences always** — Never show bare word flashcards in review. Every review
   card is a sentence. If a due word has no comprehensible sentence, generate one
   on-demand or skip the word.

2. **Full automation** — The algorithm decides everything: which words to review,
   when to introduce new words, when to suspend leeches, when to reintroduce them.
   The user's only job is to show up and engage honestly with each card.

3. **Reading focus only** — No production/writing exercises. Receptive vocabulary
   through contextual reading comprehension.

4. **Variable session length** — Must handle 3-card micro-sessions (phone pickup) and
   30-card deep sessions (train ride) equally well. Front-load the most valuable cards
   so even a 2-card session is maximally useful.

5. **Comprehensibility gate** — Sentences must be comprehensible (≥70% known content
   words). Showing unreadable sentences wastes time and damages motivation.

### Research Foundation

| Finding | Value | Source |
|---------|-------|--------|
| Encounters needed for stable vocab | 8-12 meaningful | Uchihara 2019 meta-analysis |
| Optimal training accuracy | ~85% | Wilson et al. 2019 (Nature) |
| Within-session retrievals sweet spot | 3 | Nakata 2017 |
| Working memory capacity | ~4 chunks | Cowan 2001 |
| Semantic clustering effect | **Impedes** learning | Tinkham 1993/97 |
| Testing vs. restudying at 1 week | 10% vs 52% forgotten | Karpicke & Roediger 2006 |
| Lexical integration requires | Sleep (overnight) | J. Neuroscience 2013 |
| Arabic text coverage at 95% | ~9,000 lemmas | Masrai & Milton 2016 |
| Leech cards vs normal | ~10x repetitions needed | Hacking Chinese |

---

## 2. Word Lifecycle Overview

```
                    ┌─────────────────────────────────────────────┐
                    │              WORD LIFECYCLE                  │
                    └─────────────────────────────────────────────┘

  ┌──────────┐     ┌──────────────────┐     ┌─────────────────────┐
  │  NEW     │     │  ENCOUNTERED     │     │  ACQUIRING          │
  │          │────>│                  │────>│  Leitner 3-box      │
  │ No ULK   │     │ ULK exists       │     │  Box 1: 4h          │
  │ record    │     │ No FSRS card     │     │  Box 2: 1d          │
  │          │     │ No reviews       │     │  Box 3: 3d          │
  └──────────┘     │ Passive vocab    │     │                     │
                    └──────────────────┘     │ Graduation:         │
                              │              │  box≥3 + seen≥5     │
                              │              │  + accuracy≥60%     │
                     Learn mode /            └────────┬────────────┘
                     Auto-intro                       │
                                              Graduates to FSRS
                                                      │
                                                      ▼
                              ┌──────────────────────────────────┐
                              │         FSRS-6 SCHEDULING         │
                              │                                    │
                              │  ┌──────────┐  ┌────────┐        │
                              │  │ LEARNING │─>│ KNOWN  │        │
                              │  │(initial) │  │(stable)│        │
                              │  └──────────┘  └────┬───┘        │
                              │       ▲             │             │
                              │       │         Lapse (fail)      │
                              │       │             │             │
                              │       │        ┌────▼───┐        │
                              │       └────────│ LAPSED │        │
                              │                └────────┘        │
                              └──────────────────────────────────┘
                                        │              │
                                   Leech detected   Leech reintro
                                   (seen≥8,acc<40%)  (after 14d)
                                        │              │
                                        ▼              │
                              ┌──────────────┐         │
                              │  SUSPENDED   │─────────┘
                              │  (leech)     │
                              └──────────────┘
```

### Knowledge States in Code

| State | `knowledge_state` | Has FSRS Card? | Has Acquisition Box? | Reviews? |
|-------|-------------------|----------------|----------------------|----------|
| New | *(no ULK record)* | No | No | No |
| Encountered | `"encountered"` | No | No | No |
| Acquiring | `"acquiring"` | No | Yes (1/2/3) | Yes (Leitner) |
| Learning | `"learning"` | Yes | No | Yes (FSRS) |
| Known | `"known"` | Yes | No | Yes (FSRS) |
| Lapsed | `"lapsed"` | Yes | No | Yes (FSRS) |
| Suspended | `"suspended"` | Maybe | No | No |

**State transitions in code**: `fsrs_service.py` line ~135 maps FSRS states. The
stability floor check (line ~140) relabels "known" → "lapsed" if stability < 1.0 day.

---

## 3. Entry Points

Words enter the system through multiple paths. Each path determines the initial
knowledge state:

### 3.1 Learn Mode (Manual Introduction)

**Path**: User picks word in Learn mode → `POST /api/learn/introduce`
**Initial state**: `acquiring` (box 1)
**Source**: `"study"`
**Code**: `word_selector.py:introduce_word()` → `acquisition_service.py:start_acquisition()`

The user sees 5 candidates ranked by the word selection algorithm, picks words to
learn, and each enters acquisition immediately.

### 3.2 Auto-Introduction (During Session Building)

**Path**: `build_session()` detects room for more acquiring words → auto-introduces
**Initial state**: `acquiring` (box 1, `due_immediately=True`)
**Source**: `"auto_intro"`
**Code**: `sentence_selector.py:_auto_introduce_words()`

**Gating conditions**:
- Current acquiring count < `MAX_ACQUIRING_WORDS` (30)
- Recent accuracy ≥ `AUTO_INTRO_ACCURACY_FLOOR` (70%) over last 10+ reviews
- Selects highest-frequency encountered words

### 3.3 OCR / Textbook Scan

**Path**: `POST /api/ocr/scan-pages?start_acquiring=true|false` → Gemini Vision extraction
**Initial state**: `acquiring` (box 1, `due_immediately=True`) when `start_acquiring=true`;
`encountered` (no FSRS card) when `start_acquiring=false` (default)
**Source**: `"textbook_scan"`
**Code**: `ocr_service.py`

With the `start_acquiring` toggle enabled, scanned words go straight into Leitner
box 1 for immediate follow-up review. Without it, words are parked as encountered
and become Learn mode candidates with an `encountered_bonus` of 0.5. Variant words
detected post-import are reset from acquiring back to encountered.

### 3.4 Story Import

**Path**: `POST /api/stories/import` → morphological analysis + LLM translation
**Initial state**: New lemma created with `source="story_import"`, no ULK
**On story completion**: `encountered` ULK created
**Code**: `story_service.py:import_story()`, `complete_story()`

Unknown words in stories become Learn mode candidates with a `story_bonus` of 1.0
(the strongest boost). Proper nouns are detected and marked as function words with
`name_type` instead of creating learning entries.

### 3.5 Duolingo Import

**Path**: `python3 scripts/import_duolingo.py`
**Initial state**: Depends on import configuration. Originally created FSRS cards;
now should create `encountered` ULK.
**Source**: `"duolingo"`

### 3.6 Story Completion (Collateral Credit)

**Path**: User completes a story → `POST /api/stories/{id}/complete`
**Effect on unknown words**: Creates `encountered` ULK (no FSRS card)
**Effect on known words**: Real FSRS review submitted (rating=3)
**Code**: `story_service.py:complete_story()`

### 3.7 Sentence Review (Collateral Credit)

**Path**: Word appears in a reviewed sentence but has no existing ULK
**Effect**: Word auto-introduced into acquisition (Leitner box 1, `due_immediately=False`)
via `start_acquisition(source="collateral")`, then gets its first acquisition review
**Code**: `sentence_review_service.py` → `acquisition_service.py:start_acquisition()`

Words discovered through sentence context are routed through the standard acquisition
pipeline (Leitner 3-box) rather than getting FSRS cards directly. The 4-hour delay
(`due_immediately=False`) ensures dedicated follow-up in the next session.
**Encountered words** are explicitly *skipped* — they must be formally introduced first.

---

## 4. Phase 1: Encountered

```
┌─────────────────────────────────────────────────────────────────┐
│ ENCOUNTERED                                                      │
│                                                                  │
│ • ULK exists with knowledge_state="encountered"                  │
│ • No FSRS card (fsrs_card_json = NULL)                          │
│ • No acquisition box                                             │
│ • total_encounters may be incremented                            │
│ • Contributes to story readiness calculations                    │
│ • Counts as "passive vocab" for comprehensibility gate           │
│ • Appears in Learn mode candidates with encountered_bonus=0.5    │
│ • Appears in auto-intro pool (highest frequency first)           │
│                                                                  │
│ EXIT: Learn mode introduce OR auto-intro during session building │
└─────────────────────────────────────────────────────────────────┘
```

**Key detail**: Encountered words count as "passive vocabulary" for the
comprehensibility gate in sentence selection. A sentence containing an encountered
word won't have that word counted against the 70% known-word threshold. This prevents
the chicken-and-egg problem where no sentences are comprehensible because too many
words are "unknown."

---

## 5. Phase 2: Acquiring (Leitner 3-Box)

**Code**: `backend/app/services/acquisition_service.py`

### Box System

```
           ┌─── Correct (rating≥3) ───┐
           │                           │
     ┌─────▼─────┐              ┌─────▼─────┐              ┌──────▼─────┐
     │  BOX 1    │   Correct    │  BOX 2    │   Correct    │  BOX 3    │
     │  4 hours  │─────────────>│  1 day    │─────────────>│  3 days   │
     │           │              │           │              │           │
     └─────▲─────┘              └─────┬─────┘              └─────┬─────┘
           │                          │                          │
           │      Fail (rating=1)     │      Fail (rating=1)     │
           └──────────────────────────┘──────────────────────────┘
                      Reset to Box 1

     Hard (rating=2): Stay in current box, reschedule with same interval
     Exception: words with 0% accuracy use shorter retries:
       - Rating 2 + never correct: retry in 10 minutes
       - Rating 1 + never correct: retry in 5 minutes
       Normal intervals resume after first correct answer.
```

### Constants

```python
BOX_INTERVALS = {1: 4h, 2: 1d, 3: 3d}
GRADUATION_MIN_REVIEWS = 5
GRADUATION_MIN_ACCURACY = 0.60
```

### Graduation Criteria

A word graduates from acquisition to FSRS when **all** conditions are met:
1. `acquisition_box >= 3`
2. `times_seen >= 5`
3. Cumulative accuracy (`times_correct / times_seen`) >= 60%

**Important**: Graduation fires regardless of the current review's rating. If a word
is in box 3 with 5 reviews and 65% accuracy, it graduates even if the current review
is rated "Again". This was a deliberate fix — see experiment-log.md.

### On Graduation

```python
def _graduate(ulk, now):
    ulk.knowledge_state = "learning"
    ulk.acquisition_box = None
    ulk.acquisition_next_due = None
    ulk.graduated_at = now
    # Create initial FSRS card with Good rating
    card = Card()
    new_card, _ = scheduler.review_card(card, Rating.Good, now)
    ulk.fsrs_card_json = new_card.to_dict()
```

The initial FSRS stability after graduation is S₀(Good) ≈ 2.3 days. This is
appropriate because by this point the word has been seen 5+ times with 60%+ accuracy
over 4+ days — a genuine learning signal.

### Within-Session Repetition

Acquiring words get aggressive repetition within each session. The session builder
ensures each acquiring word appears at least `MIN_ACQUISITION_EXPOSURES` = 4 times
across different sentences, using a multi-pass expanding interval approach:

```
Session with 3 acquiring words (A, B, C):

  [1] Easy review (warm-up)
  [2] Sentence with word A          ← 1st exposure
  [3] Sentence with word B          ← 1st exposure
  [4] Due review (FSRS word)
  [5] Sentence with word A          ← 2nd exposure (gap=2)
  [6] Sentence with word C          ← 1st exposure
  [7] Due review (FSRS word)
  [8] Sentence with word B          ← 2nd exposure (gap=4)
  [9] Sentence with word C          ← 2nd exposure (gap=2)
  [10] Easy review (positive end)
  ... up to 15 extra slots for additional exposures
```

**Code**: `sentence_selector.py` lines 673-707, after greedy set cover.

---

## 6. Phase 3: FSRS-6 (Long-Term Retention)

**Code**: `backend/app/services/fsrs_service.py`
**Library**: py-fsrs ≥ 6.0.0 (FSRS-6 algorithm with same-day review support via w17-w19)

### How FSRS Works

FSRS (Free Spaced Repetition Scheduler) models memory as a function of two variables:
- **Stability (S)**: How many days until there's a 90% chance of forgetting.
  Higher = more durable memory.
- **Difficulty (D)**: How inherently hard the card is. Range 0-10.

After each review, FSRS updates both values based on the rating:

| Rating | Name | Meaning | Effect on S | Effect on D |
|--------|------|---------|-------------|-------------|
| 1 | Again | Complete failure | Reset to ~S₀ | Increase |
| 2 | Hard | Recalled with difficulty | Modest increase | Slight increase |
| 3 | Good | Normal recall | Standard increase | No change |
| 4 | Easy | Effortless recall | Large increase | Decrease |

### Initial Stability Values (FSRS-6 Defaults)

```
S₀(Again) = 0.212 days (~5 hours)
S₀(Hard)  = 1.293 days
S₀(Good)  = 2.307 days
S₀(Easy)  = 8.296 days
```

### State Mapping

```python
FSRS State.Learning   → knowledge_state = "learning"
FSRS State.Review     → knowledge_state = "known"
FSRS State.Relearning → knowledge_state = "lapsed"
```

**Stability floor**: If FSRS reports `State.Review` (known) but stability < 1.0 day,
the code overrides to `"lapsed"`. This catches cases where FSRS says a word is in the
review state but the memory is actually fragile.

### Review Submission Flow

```
submit_review(lemma_id, rating_int)
    │
    ├── Fetch or create ULK
    ├── Parse existing FSRS card (or create new)
    ├── Snapshot pre-review state (for undo)
    │
    ├── scheduler.review_card(card, rating, now)
    │   └── Returns (new_card, review_log)
    │
    ├── Apply stability floor: known + S<1.0 → lapsed
    │
    ├── Update ULK:
    │   ├── fsrs_card_json = new_card.to_dict()
    │   ├── knowledge_state = mapped state
    │   ├── times_seen += 1
    │   └── times_correct += 1 (if rating ≥ 3)
    │
    └── Create ReviewLog with pre-review snapshot
```

---

## 7. Focus Cohort — Controlling Review Bandwidth

**Code**: `backend/app/services/cohort_service.py`

### Problem It Solves

Without a cohort system, 500+ words compete for ~100 reviews/day. Each word gets
reviewed roughly once per 5-6 days. Research shows this is insufficient — words need
2-3 reviews/day during the consolidation window.

### How It Works

```
MAX_COHORT_SIZE = 100

┌────────────────────────────────────────────┐
│            FOCUS COHORT (≤100)             │
│                                            │
│  ┌──────────────────────────────────────┐  │
│  │ ALL acquiring words (always included)│  │
│  │ (Leitner boxes 1-3)                 │  │
│  └──────────────────────────────────────┘  │
│                                            │
│  ┌──────────────────────────────────────┐  │
│  │ Remaining slots filled by FSRS      │  │
│  │ due words, sorted by LOWEST         │  │
│  │ stability first (most fragile)      │  │
│  └──────────────────────────────────────┘  │
│                                            │
└────────────────────────────────────────────┘

         Words outside cohort:
         NOT reviewed this session
         (even if technically "due")
```

### Algorithm

1. Query all non-suspended, non-encountered ULK records
2. All `acquiring` words go into the cohort unconditionally
3. All FSRS words where `due_date <= now` become candidates
4. Sort FSRS candidates by stability ascending (lowest = most fragile)
5. Fill remaining slots (`MAX_COHORT_SIZE - acquiring_count`) from sorted list
6. Return the set of lemma_ids

**Effect**: During session building, `build_session()` filters due_lemma_ids through
the cohort. Words due but outside the cohort are ignored for this session.

---

## 8. Session Building — The Core Algorithm

**Code**: `backend/app/services/sentence_selector.py:build_session()`
**Endpoint**: `GET /api/review/next-sentences?limit=10&mode=reading`

This is the most complex piece of the system. It assembles a session of sentence-based
review cards, optimizing for due-word coverage, comprehensibility, and difficulty
progression.

### Complete Pipeline

```
build_session(db, limit=10, mode="reading")
    │
    ▼
┌─────────────────────────────────────────────┐
│ STAGE 1: Classify All Words                  │
│                                              │
│ Load all non-suspended ULK records           │
│ For each word, determine:                    │
│   • Is it acquiring? Check acquisition_due   │
│   • Is it FSRS due? Check card.due           │
│   • Map stability (pseudo for acquiring:     │
│     box1→0.1, box2→0.5, box3→2.0)           │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ STAGE 2: Focus Cohort Filter                 │
│                                              │
│ due_lemma_ids ∩ cohort_ids                   │
│ Words due but outside cohort → ignored       │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ STAGE 3: Auto-Introduce (if room)            │
│                                              │
│ Guards (all must pass):                      │
│   1. acquiring_count < 30                    │
│   2. box_1_count < MAX_BOX1_WORDS (8)        │
│   3. recent accuracy ≥ 70%                   │
│ Slots = min(accuracy_band, 30-acq, 8-box1)  │
│ Select top frequency encountered words       │
│ start_acquisition(due_immediately=True)      │
│ Add to due_lemma_ids                         │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ STAGE 4: Fetch Candidate Sentences           │
│                                              │
│ Find active sentences containing ≥1 due word │
│ Apply comprehension-aware recency filters:   │
│   • Never shown: always eligible             │
│   • understood: 7-day cooldown               │
│   • partial: 2-day cooldown                  │
│   • grammar_confused: 1-day cooldown         │
│   • no_idea: 4-hour cooldown                 │
│   • no record: 7-day cooldown               │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ STAGE 5: Score Each Candidate                │
│                                              │
│ For each sentence:                           │
│   1. Count due words covered                 │
│   2. Comprehensibility gate (≥70% known)     │
│   3. Difficulty match quality (DMQ)          │
│   4. Grammar fit (0.8-1.1)                   │
│   5. Diversity (1/(1+times_shown))           │
│   6. Scaffold freshness                      │
│                                              │
│   Score = covered^1.5 × DMQ × gfit          │
│           × diversity × freshness            │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ STAGE 6: Greedy Set Cover                    │
│                                              │
│ While due words remain AND under limit:      │
│   Pick sentence with highest score           │
│   Remove covered words from remaining set    │
│   Recalculate scores for remaining candidates│
│   Add to selected list                       │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ STAGE 7: Acquisition Repetition              │
│                                              │
│ For each acquiring word in selected:         │
│   If appearances < MIN_ACQUISITION_EXPOSURES │
│   (4): find additional sentences             │
│   Session can grow up to +15 extra slots     │
│   Multi-pass: target_count 2→3→4             │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ STAGE 8: Ordering (Easy Bookends)            │
│                                              │
│ Easiest sentence (highest stability) → first │
│ 2nd easiest → last                           │
│ Hardest sentences → middle                   │
│ (Serial position effect: strong start/end)   │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ STAGE 9: On-Demand Generation (if needed)    │
│                                              │
│ If uncovered due words remain:               │
│   Generate sentences via LLM (parallelized)  │
│   Using current vocabulary as scaffold       │
│   Store in DB for future reuse               │
│   Add to session items                       │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ STAGE 10: Fill Phase (if undersized)         │
│                                              │
│ If len(items) < limit:                       │
│   Auto-introduce MORE words with relaxed     │
│   caps (acquiring≤50, box1≤15) since the     │
│   user clearly wants to keep learning.       │
│   Skip pre-generation (on-demand handles it) │
│   Generate on-demand sentences for fill words│
│   Ensures sessions stay full when the user   │
│   has reviewed everything due.               │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ STAGE 11: Build Response                     │
│                                              │
│ For each selected sentence:                  │
│   • primary_lemma_id (target or first due)   │
│   • word_dicts with gloss, stability, root   │
│   • grammar features                         │
│   • audio_url                                │
│   • is_intro flag for auto-introduced words  │
│                                              │
│ Also: reintro_cards for struggling words      │
│ Also: intro_candidates for Learn mode        │
└─────────────────────────────────────────────┘
```

### Sentence Pre-Warming

When the frontend is 3 cards from the end of a session, it fires two parallel requests:

1. **`POST /api/review/warm-sentences`** (returns 202): Background task pre-generates
   sentences for focus cohort words and likely auto-introduction candidates that have
   < 2 active sentences. Sentences persist in DB regardless of whether the prefetched
   session is used.

2. **`GET /api/review/next-sentences?prefetch=true`**: Builds a full session and caches
   it in AsyncStorage for instant load on the next session request.

**Staleness**: Cached sessions expire after 30 minutes. If the user returns after a break,
the stale cache is discarded and `build_session()` runs fresh — but pre-generated sentences
from the warm-up are already in DB, so on-demand generation is rarely needed.

### Scoring Deep Dive

#### Comprehensibility Gate

For each sentence, count content words (non-function-words) and check how many are
in a "known" state (known, learning, lapsed, acquiring, or encountered):

```
comprehensibility = known_content_words / total_content_words
if comprehensibility < 0.70: SKIP this sentence
```

**Encountered words count as passive vocab** — they're not "learned" but the user has
seen them and has some recognition.

#### Difficulty Match Quality (DMQ)

Measures whether scaffold words (non-target words) are stable enough to support
comprehension of the weakest due word:

```
If weakest_stability < 0.5 days:
    Scaffolds need average stability ≥ 1 day → DMQ = 1.0
    Else → DMQ = 0.3 (heavy penalty)

If weakest_stability 0.5–3.0 days:
    Scaffolds need average > weakest → DMQ = 1.0
    Else → DMQ = 0.5

If weakest_stability > 3.0 days:
    DMQ = 1.0 (any scaffold is fine)
```

#### Scaffold Freshness

Penalizes sentences whose scaffold words have been over-reviewed (prevents the same
"easy" sentences from being reused forever):

```
For each scaffold word:
    freshness = min(1.0, FRESHNESS_BASELINE / times_seen)
    where FRESHNESS_BASELINE = 8

Overall = geometric_mean(per_word_freshness)
Floor at 0.3
```

#### Grammar Fit

```
For each grammar feature in the sentence:
    If never seen by learner: 0.8
    If low comfort: 1.0
    If moderate comfort: 1.0
    If high comfort: 1.1
Aggregate via mean → multiplier 0.8–1.1
```

### Variant Resolution

Sentences may contain variant forms (e.g., كتابي "my book" is a variant of كتاب
"book"). The session builder resolves variants to their canonical lemma:

```
Sentence contains word with lemma_id=42 (variant)
Lemma 42 has canonical_lemma_id=17 (canonical)
If lemma_id=17 is in due_lemma_ids:
    This sentence covers the canonical word
```

This ensures sentences with variant surface forms correctly satisfy the scheduling
requirements of their canonical lemma.

---

## 9. Review Processing — How Ratings Flow

**Code**: `backend/app/services/sentence_review_service.py`
**Endpoint**: `POST /api/review/submit-sentence`

### Ternary Rating System

The user rates each sentence with one of three signals:

| Signal | Meaning | Word Ratings |
|--------|---------|-------------|
| `understood` | Got the whole sentence | All words → rating 3 (Good) |
| `partial` | Got some, missed some | Missed → 1, Confused → 2, Rest → 3 |
| `no_idea` | Didn't understand at all | All words → rating 1 (Again) |
| `grammar_confused` | Words known but grammar confusing | All words → rating 3 |

### Front-Phase Word Marking

During the "front phase" (before revealing the answer), the user can:
- **Tap** a word to look it up → automatically marked as **missed** (rating 1)
- **Triple-tap** on back phase to cycle: off → missed (red) → confused (yellow) → off

### Complete Review Flow

```
User submits: {sentence_id, comprehension_signal, missed_lemma_ids, confused_lemma_ids}
    │
    ▼
┌─────────────────────────────────────────────┐
│ 1. Collect all word lemma_ids from sentence  │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ 2. Categorize each word                      │
│    • Function word? → SKIP (no FSRS credit)  │
│    • Variant? → Redirect to canonical        │
│    • Encountered? → SKIP (needs intro first) │
│    • Acquiring? → Route to acquisition       │
│    • Suspended? → SKIP                       │
│    • Normal? → Route to FSRS                 │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ 3. Determine per-word rating                 │
│    Based on comprehension_signal +           │
│    missed/confused lists                     │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ 4. Submit reviews                            │
│    • Acquiring → submit_acquisition_review() │
│    • FSRS → submit_review()                  │
│    • Dedup after variant→canonical redirect  │
│    • Track variant_stats_json on canonical   │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│ 5. Post-review checks                        │
│    • Leech check for words with rating ≤ 2   │
│    • Grammar exposure recording              │
│    • Update sentence.times_shown             │
│    • Update sentence comprehension/shown_at  │
└─────────────────────────────────────────────┘
```

### All Words Get Equal Credit

This is a deliberate design choice, backed by research: **every non-function word in
a reviewed sentence gets a full review**, regardless of why the sentence was selected.
The `credit_type` field (`"primary"` or `"collateral"`) is metadata only — it tracks
which word caused the sentence to be selected, but both receive identical FSRS
treatment.

### Undo System

Every review creates a pre-review snapshot stored in `fsrs_log_json`:
```json
{
    "pre_card": {...},
    "pre_times_seen": 5,
    "pre_times_correct": 3,
    "pre_knowledge_state": "learning"
}
```

`POST /api/review/undo-sentence` restores this snapshot, effectively rolling back
the FSRS state to before the review.

---

## 10. Sentence Pipeline — Generation, Validation, Retirement

### Generation

**Code**: `backend/app/services/sentence_generator.py`, `material_generator.py`

Sentences are generated by LLM (GPT-5.2) in two modes:

**Single-target** (7-attempt retry loop):
```
For each target word:
    1. Build vocabulary context (known words as available scaffold)
    2. Request 1-2 sentences from GPT-5.2 with:
       - Full diacritics required
       - Difficulty hint (simple/beginner/intermediate)
       - max_words limit (5-14, based on word maturity)
       - Rejected words from previous failures
    3. Rule-based validation:
       - Tokenize → strip diacritics → strip clitics → match known forms
       - Check target word is present
       - Check comprehensibility threshold
    4. Gemini Flash quality review gate:
       - Checks naturalness (would a native speaker say this?)
       - Checks translation accuracy
       - Fails open (if Gemini unavailable, sentence passes)
    5. On failure: feed validation errors / quality review feedback as retry prompt
    6. Store: Sentence + SentenceWord entries + optional TTS audio
```

**Multi-target** (groups of 2-4 words, 3-attempt retry loop):
```
1. Group words via group_words_for_multi_target() (avoids same-root pairs)
2. Request 4 sentences from LLM, each must include ≥2 target words
3. Validate via validate_sentence_multi_target():
   - At least 1 target found AND no unknown content words
4. Assign Sentence.target_lemma_id to target with fewest existing sentences
5. All found targets get SentenceWord.is_target_word=True
6. Falls back to single-target if multi-target fails
```

Multi-target is tried first in both on-demand and cron paths. Benefits: fewer LLM
calls, denser review material, natural cross-reinforcement between words.

### Dynamic Difficulty

`get_sentence_difficulty_params()` adjusts sentence complexity based on word maturity:

| Stage | Condition | max_words | Difficulty |
|-------|-----------|-----------|------------|
| Brand new | <2h since intro, seen <3x | 7 | simple |
| Same-day | <24h, seen <6x | 9 | simple |
| First week | <7d, seen <10x | 11 | beginner |
| Established | ≥7d or seen 10+x | 14 | intermediate |

### On-Demand vs Pre-Generated

Two paths for sentence availability:

1. **Pre-generated (warm cache)**: `scripts/update_material.py` runs on cron,
   generating sentences for words prioritized by FSRS due date. Target: `MIN_SENTENCES=2`
   per word. Pool cap: ~300 active sentences.

2. **On-demand (JIT)**: During `build_session()`, if a due word has no comprehensible
   sentence, generate one synchronously. Up to `MAX_ON_DEMAND_PER_SESSION=10`. Uses
   current vocabulary for better-calibrated scaffolding than pre-generated pool.

**Philosophy**: JIT is the primary strategy; pre-generated pool is a warm cache to
avoid latency. On-demand sentences use the learner's current vocabulary state, making
them fresher and more appropriate than sentences generated days earlier.

### Validation

**Code**: `backend/app/services/sentence_validator.py`

Rule-based validation pipeline:
1. Tokenize Arabic text
2. Strip diacritics (fatha, damma, kasra, sukun, shadda, tanwin)
3. Strip clitics:
   - Proclitics: و، ف، ب، ل، ك، وال، بال، فال، لل، كال
   - Enclitics: ه، ها، هم، هن، هما، كم، كن، ك، نا، ني
   - Taa marbuta: ة → ت
4. Match against known forms (lemma_ar_bare + forms_json entries)
5. 60+ hardcoded function words treated as always-known

### Retirement

Old, low-diversity sentences are retired via `is_active=False`. The retirement script
(`scripts/retire_sentences.py`) deactivates sentences with overexposed scaffold words
or low comprehension history.

---

## 11. Leech Management

**Code**: `backend/app/services/leech_service.py`

### Detection

A word becomes a leech when:
- `times_seen >= 5` AND `accuracy < 50%`

The leech check runs after every review where the rating ≤ 2.

### Lifecycle

```
Normal word → Leech detected → Auto-suspended
    │                               │
    │                         leech_suspended_at set
    │                               │
    │                         14 days pass...
    │                               │
    │                         Auto-reintroduced
    │                         → acquisition box 1
    │                         (fresh start)
    │                               │
    └───────────────────────────────┘
```

### Root-Sibling Interference Guard

**Code**: `word_selector.py:_get_recently_failed_roots()`

When selecting new words to introduce, the algorithm checks if any root sibling got
rating=1 in the last 7 days. If so, the word is skipped. This prevents the semantic
clustering effect (Tinkham 1993/97) where learning two similar words simultaneously
causes interference.

---

## 12. Topical Learning Cycles

**Code**: `backend/app/services/topic_service.py`

### How It Works

Words are grouped into 20 thematic domains (food, family, school, nature, etc.). The
system cycles through topics to reduce cognitive interference from mixing unrelated
vocabulary.

```
MAX_TOPIC_BATCH = 15    # words per topic cycle
MIN_TOPIC_WORDS = 5     # minimum available before auto-advancing

Topic cycle:
1. Select active topic (stored in LearnerSettings)
2. word_selector filters candidates to active topic's domain
3. Introduce up to 15 words from this topic
4. When < 5 words remain available, auto-advance to next topic
5. Topic history tracked in topic_history_json
```

### Integration with Word Selection

When a topic is active, `select_next_words()` filters candidates to that
`thematic_domain`. If no domain candidates are available, it falls back to the general
pool.

---

## 13. Grammar Tracking

**Code**: `backend/app/services/grammar_service.py`

### 8-Tier Progression System

Grammar features are organized into 8 tiers (0-7), each with unlock requirements:

| Tier | Key Features | Unlock Requirement |
|------|-------------|-------------------|
| 0 | singular, masculine, present, Form I, definite article | Always |
| 1 | feminine, past, idafa, prepositions, subject pronouns | 10+ known words |
| 2 | sound plural, negation, imperative, attached pronouns | Tier 1 comfort ≥ 0.3 |
| 3 | broken plural, Form II/III, passive, active participle | Tier 2 comfort ≥ 0.35 |
| 4 | dual, comparative, Form IV/V, masdar, relative clauses | Tier 3 comfort ≥ 0.4 |
| 5 | Form VI/VII/VIII, hollow/defective verbs, conditionals | Tier 4 comfort ≥ 0.45 |
| 6 | Form IX/X, assimilated verbs, diminutive, hal clause | Tier 5 comfort ≥ 0.5 |
| 7 | nisba, tanwin, emphatic negation, oath, vocative | Tier 6 comfort ≥ 0.5 |

### Comfort Score Formula

```
comfort = (exposure + accuracy) × decay

exposure = min(log₂(times_seen + 1) / log₂(31), 0.6)   # caps at 0.6
accuracy = (times_correct / times_seen) × 0.4            # caps at 0.4
decay    = 0.5^(days_since_last_seen / 30)               # half-life 30 days
```

### Impact on Scheduling

Grammar features affect sentence selection via the `grammar_fit` multiplier (0.8-1.1)
in the session builder. They also influence the word selection algorithm via the
`grammar_pattern_score` (10% weight).

---

## 14. Listening Mode

**Code**: `backend/app/services/listening.py`

### Listening Readiness Filter

A word is "listening-ready" when its scaffold is stable enough that the user can
focus on aural recognition rather than reading:

```
times_seen >= 3    (MIN_REVIEWS_FOR_LISTENING)
stability >= 7d    (MIN_LISTENING_STABILITY_DAYS)
```

### Sentence Scoring for Listening

```
confidence = min_word_confidence × 0.6 + avg_word_confidence × 0.4
```

Per-word confidence:
| Condition | Confidence |
|-----------|-----------|
| No knowledge record | 0.0 |
| Lapsed | 0.1 |
| Seen < 3 times | 0.2 |
| Stability < 1d | 0.3 |
| Stability < 7d | 0.5 |
| Stability < 30d | 0.7 |
| Stability ≥ 30d | 0.7 + accuracy × 0.3 |

### How Listening Sessions Differ

In the session builder, listening mode applies additional filters:
- Scaffold words must be "listening-ready" (times_correct ≥ 1, last_review ≥ 3,
  last_rating ≥ 3)
- Uses `last_listening_shown_at` / `last_listening_comprehension` for recency
- No auto-introduction candidates in listening mode

---

## 15. Story Mode

**Code**: `backend/app/services/story_service.py`

### Two Paths

1. **Generate**: LLM creates micro-fiction (2-12 sentences) using known vocabulary
2. **Import**: User pastes Arabic text → morphological analysis + LLM translation

### Word Discovery Pipeline

```
Import text
    │
    ├── Tokenize and analyze morphology (CAMeL Tools)
    ├── Match against existing lemmas
    ├── Create new Lemma entries for unknown words
    │   └── source="story_import", source_story_id set
    ├── Run variant detection (detect_variants_llm + mark_variants)
    ├── Run import quality gate (filter_useful_lemmas)
    ├── Detect proper nouns → mark as function words with name_type
    └── Calculate readiness_pct for the story
```

### Completion Flow

When the user completes a story:
- Unknown words → `encountered` ULK (no FSRS card)
- Words with active FSRS cards → real review submitted (rating=3)
- Words become Learn mode candidates with `story_bonus=1.0`

---

## 16. Learn Mode — Manual Word Introduction

**Code**: `backend/app/services/word_selector.py`
**Endpoint**: `GET /api/learn/next-words?count=5`

### Selection Algorithm

Each candidate word is scored across four dimensions plus bonuses:

```
total_score = frequency × 0.4
            + root_familiarity × 0.3
            + recency_bonus × 0.2
            + grammar_pattern × 0.1
            + story_bonus        (flat +1.0)
            + encountered_bonus  (flat +0.5)
```

#### Frequency Score (40%)
```
score = 1.0 / log₂(frequency_rank + 2)
Unknown frequency → 0.3
```

#### Root Familiarity Score (30%)
```
ratio = known_siblings / total_siblings

If known == 0: score = 0.0         (no bootstrapping hook)
If known == total: score = 0.1     (root fully covered, low priority)
Else: score = ratio × (1 - ratio) × 4.0

Peak when ~30-60% of root is known (sweet spot for root bootstrapping)
```

#### Recency Bonus (20%)
```
If a root sibling was introduced 1-3 days ago AND root_score > 0:
    bonus = 0.2
Else: 0.0
```

#### Grammar Pattern Score (10%)
```
For each grammar feature on the word:
    If unlocked but never seen by learner: 1.0
    If unlocked with low comfort: 1 - comfort (high value)
Average across features
```

### Introduction Flow

```
POST /api/learn/introduce {lemma_id}
    │
    ├── If suspended → reactivate (fresh FSRS card, "learning")
    ├── If encountered → start_acquisition("acquiring", box 1)
    ├── If new → start_acquisition("acquiring", box 1)
    ├── If already learning/known → return already_known=true
    │
    └── Background: generate_material_for_word()
        (up to 3 sentences + audio)
```

---

## 17. Offline & Sync

### Frontend Offline Queue

Reviews are queued in AsyncStorage when offline:
```typescript
// lib/sync-queue.ts
enqueue({type: "sentence", payload: {...}, client_review_id: "uuid"})
```

When connectivity returns, the queue flushes via `POST /api/review/sync` in bulk.
Deduplication is handled server-side via `client_review_id`.

### Session Caching

The frontend pre-fetches and caches review sessions per mode in AsyncStorage
(`lib/offline-store.ts`). Each mode (reading, listening) has its own cache. Sessions
are invalidated after use or after a configurable timeout.

---

## 18. Key Constants Reference

### Session Building (`sentence_selector.py`)

| Constant | Value | Purpose |
|----------|-------|---------|
| `MIN_ACQUISITION_EXPOSURES` | 4 | Min times an acquiring word should appear per session |
| `MAX_ACQUISITION_EXTRA_SLOTS` | 15 | Max extra cards for acquisition repetition |
| `MAX_AUTO_INTRO_PER_SESSION` | 10 | Ceiling for auto-intro (reached at ≥92% accuracy) |
| `AUTO_INTRO_ACCURACY_FLOOR` | 0.70 | Pause auto-intro if accuracy below this |
| Adaptive intro bands | 0→4→7→10 | Slots at <70%/70-85%/85-92%/≥92% accuracy |
| `MAX_ACQUIRING_WORDS` | 30 | Don't auto-intro if this many words already acquiring (normal phase) |
| `MAX_ACQUIRING_CEILING` | 50 | Extended acquiring cap during fill phase |
| `MAX_BOX1_WORDS` | 8 | Don't auto-intro if this many words in Leitner box 1 (normal phase) |
| `MAX_BOX1_WORDS_FILL` | 15 | Extended box 1 cap during fill phase |
| `FRESHNESS_BASELINE` | 8 | Reviews before scaffold freshness penalty kicks in |
| `MAX_ON_DEMAND_PER_SESSION` | 10 | Cap for JIT sentence generation |
| `MAX_REINTRO_PER_SESSION` | 3 | Struggling word reintro card limit |
| `STRUGGLING_MIN_SEEN` | 3 | Threshold for struggling classification |

### Comprehension-Aware Recency Cutoffs

| Last Comprehension | Cooldown |
|-------------------|----------|
| `understood` | 7 days |
| `partial` | 2 days |
| `grammar_confused` | 1 day |
| `no_idea` | 4 hours |
| No record | 7 days |

### Acquisition (`acquisition_service.py`)

| Constant | Value | Purpose |
|----------|-------|---------|
| `BOX_INTERVALS[1]` | 4 hours | Box 1 review interval |
| `BOX_INTERVALS[2]` | 1 day | Box 2 review interval |
| `BOX_INTERVALS[3]` | 3 days | Box 3 review interval |
| `GRADUATION_MIN_REVIEWS` | 5 | Min reviews before graduation |
| `GRADUATION_MIN_ACCURACY` | 0.60 | Min accuracy for graduation |

### Cohort (`cohort_service.py`)

| Constant | Value | Purpose |
|----------|-------|---------|
| `MAX_COHORT_SIZE` | 100 | Max words in active review pool |

### Leech (`leech_service.py`)

| Constant | Value | Purpose |
|----------|-------|---------|
| Detection threshold | times_seen≥5, accuracy<50% | When to suspend |
| Reintroduction delay | 14 days | Time before auto-reintro |
| Reintro target | Acquisition box 1 | Fresh start after suspension |

### Topic (`topic_service.py`)

| Constant | Value | Purpose |
|----------|-------|---------|
| `MAX_TOPIC_BATCH` | 15 | Words per topic cycle |
| `MIN_TOPIC_WORDS` | 5 | Auto-advance when below this |
| Number of domains | 20 | Thematic categories |

### Listening (`listening.py`)

| Constant | Value | Purpose |
|----------|-------|---------|
| `MIN_REVIEWS_FOR_LISTENING` | 3 | Min reviews before listening eligible |
| `MIN_LISTENING_STABILITY_DAYS` | 7 | Min FSRS stability for listening |
| `MAX_LISTENING_WORDS` | 10 | Max words per listening sentence |

### FSRS

| Parameter | Value | Purpose |
|-----------|-------|---------|
| S₀(Again) | 0.212d | Initial stability for "Again" |
| S₀(Good) | 2.307d | Initial stability for "Good" |
| Stability floor | 1.0d | Below this, "known" → "lapsed" |

---

## 19. Divergences: Implementation vs. Research/Intentions

This section tracks where the current implementation deviates from what the research
shows is productive or what the original design documents specified. Each item should
be addressed or explicitly accepted as a conscious trade-off.

### 19.1 ~~Target Accuracy of 85%~~ — RESOLVED

**Research says**: Optimal training accuracy is ~85% (Wilson et al. 2019). Below 70%,
motivation suffers. Above 95%, learning stalls.

**Implemented**: `_intro_slots_for_accuracy()` in `sentence_selector.py` maps recent
2-day accuracy to a graduated auto-introduction rate:

| Accuracy | Slots | Behavior |
|----------|-------|----------|
| <70% | 0 | Pause (struggling) |
| 70-85% | 4 | Normal rate |
| 85-92% | 7 | Increased rate |
| ≥92% | 10 | Maximum (`MAX_AUTO_INTRO_PER_SESSION`) |

Default: 4 slots when <10 reviews available (conservative for new/returning users).
Accuracy and computed slots are logged in interaction events for analysis.

### 19.2 Session Length Adaptation — Not Implemented

**Research says**: Sessions should be 10-20 items, 10-20 minutes. After 3 consecutive
"Again" ratings, insert 5 easy review items as a "cognitive rest stop." If session
extends beyond 20 minutes, show only review items (no new introductions in overtime).

**Original plan said**: "Track rolling accuracy over the last 10 items during a
session; if it drops below 75%, automatically pause new word introductions."

**Current implementation**: Sessions are built as a fixed batch based on `limit`
parameter. There is no intra-session adaptation. The session is assembled once and
delivered to the frontend — the backend doesn't know if the user is struggling
mid-session.

**Gap**: Sessions are pre-built, not adaptive. The frontend could implement
client-side adaptation but currently does not.

### 19.3 ~~Batch Sentence Generation for Word Sets~~ — RESOLVED

**Implemented**: Multi-target sentence generation via `generate_sentences_multi_target()`
in `llm.py`. Words are grouped into sets of 2-4 (avoiding same-root pairs) via
`group_words_for_multi_target()`. Each generated sentence must contain at least 2 target
words. Validation via `validate_sentence_multi_target()` checks all targets found +
no unknown words.

Used in two paths:
- **On-demand** (`_generate_on_demand()` in `sentence_selector.py`): groups uncovered
  words and tries multi-target first, falls back to single-target. All LLM calls run
  in parallel via `ThreadPoolExecutor(max_workers=8)` — DB storage remains sequential.
  Validation accepts encountered words (passive vocab) so GPT isn't rejected for using
  common words the learner has seen.
- **Cron** (`step_backfill_sentences()` in `update_material.py`): groups words needing
  sentences, multi-target first, single-target for leftovers.

Storage: `Sentence.target_lemma_id` gets the target with fewest existing sentences;
all targets marked via `SentenceWord.is_target_word=True`. Session builder already
discovers coverage via `SentenceWord` join — no schema change needed.

### 19.4 Wrap-Up Quiz — Backend Done, Frontend Incomplete

**Research says**: Within-session recall testing strengthens encoding. 3 retrievals
per session is the sweet spot (Nakata 2017).

**Original plan said**: Wrap-up quiz with word-level recall cards for acquiring +
missed words at end of session.

**Current implementation**: `POST /api/review/wrap-up` endpoint exists and works.
Frontend has basic integration but the wrap-up flow is not prominent or polished.

**Gap**: Feature exists but may not be reaching users effectively.

### 19.5 ~~Next-Session Recap~~ — RESOLVED

**Implemented**: Frontend calls `getRecapItems()` at session start (`index.tsx:269-299`).
Fetches last session's words from AsyncStorage, filters to within 24h, calls
`POST /api/review/recap`, and prepends recap sentence cards to the new session.
Recap cards display a "Recap" badge. Sleep consolidation check is active.

### 19.6 Forced Day-1 Review — Partially Implemented

**Research says**: Words need review within 24 hours (after one sleep cycle) for
consolidation. "Forced day-1 review regardless of box position."

**Current implementation**: Acquisition box 1 has a 4-hour interval, so the word
*will* come up for review within 4h. But there's no explicit "must appear in
tomorrow's first session" constraint. If the user doesn't open the app within the
4-hour window, the review just waits.

**Gap**: Soft guarantee via box timing, not a hard constraint.

### 19.7 Root-Aware FSRS Stability Boost — Not Implemented

**Research says**: When a new word shares a root with 2+ known words, boost initial
FSRS stability by ~30%. Root awareness accounts for substantial variance in reading
outcomes.

**Original plan said**: Root-sibling bootstrapping.

**Current implementation**: Root familiarity affects *word selection* (30% weight in
learn mode), but after a word graduates from acquisition, its initial FSRS stability
is always S₀(Good) = 2.3 days regardless of root knowledge.

**Gap**: Root knowledge influences which words are introduced but not how fast they
advance through FSRS.

### 19.8 Response Time as Difficulty Signal — Not Used

**Research says**: Slow response on a "correct" answer may indicate fragile knowledge.
Decreasing response time = fluency signal.

**Current implementation**: `response_ms` is captured and stored in ReviewLog and
SentenceReviewLog. It is never used for scheduling decisions.

**Gap**: Data is collected but not utilized. Could inform FSRS difficulty or
acquisition graduation.

### 19.9 A/B Testing Framework — Not Implemented

**Research says**: N-of-1 between-item experiments are feasible with 80-100 words per
condition, 3-4 weeks duration.

**Original plan said**: "Add `experiment_group` field to ULK, log experiment
assignment in interaction logs."

**Current implementation**: No experiment framework exists.

**Gap**: Cannot validate algorithmic changes empirically.

### 19.10 Sentence Context Quality Labels — Not Implemented

**Research says**: New words need "informative context" (surrounding words help infer
meaning). Mature words need "opaque context" (must recall from memory — a desirable
difficulty).

**Original plan said**: "Tag generated sentences with context informativeness."

**Current implementation**: Sentence generation uses difficulty hints but does not
distinguish between informative and opaque contexts. The difficulty progression
(simple → intermediate) is a proxy but doesn't explicitly target context
informativeness.

**Gap**: Missing a principled approach to context-dependent sentence selection.

### 19.11 Expertise Reversal — Not Implemented

**Research says**: As the learner advances, reduce scaffolding. Offer transliteration
as tap-to-reveal. Reduce diacritization on well-known words.

**Current implementation**: All Arabic text is always fully diacritized. Transliteration
is always shown on reveal. No adaptation to proficiency level.

**Gap**: Acceptable for a beginner but should evolve as vocabulary grows.

### 19.12 ~~Leech Detection Threshold~~ — RESOLVED

**Implemented**: Tightened to match the original plan:
- `LEECH_MIN_REVIEWS`: 8 → 5
- `LEECH_MAX_ACCURACY`: 0.40 → 0.50

Leeches now caught after 5 reviews at <50% accuracy (was 8 reviews at <40%).
Matches the "5+ lapses OR <50% accuracy" specification.

### 19.13 Focus Cohort Size — Deferred for Data Analysis

**Research says**: Smaller cohorts (30-50) allow more intensive review per word.

**Original plan said**: Initially 30-50, then adjusted to 25-40 in subsequent
experiments.

**Current implementation**: `MAX_COHORT_SIZE = 100`. This was expanded to handle the
case where many words were graduating from acquisition and needed FSRS review slots.

**Status**: Deferred. Need to analyze actual due-word counts vs cohort utilization
before deciding on a target size. If typical due count is <50, reducing the cohort
has no practical effect. If >50, a smaller cohort would prioritize fragile words
more aggressively. See IDEAS.md for analysis plan.

### 19.14 credit_type Discrepancy — Documented as Metadata, Originally Planned as Signal

**Original design**: `credit_type` was meant to differentiate how primary vs
collateral words are rated differently.

**Current implementation**: `credit_type` is metadata only. All words in a sentence
get identical treatment based on the user's ternary rating. This is actually correct
per the current research understanding — the user's marking is the signal, not the
scheduling reason.

**Status**: Conscious and correct decision. Not a divergence.

### 19.15 Grammar-Gated Sentence Selection — Minimal Impact

**Research says**: Grammar tier progression should gate which grammar features appear
in sentences.

**Current implementation**: Grammar features affect sentence selection via a 0.8-1.1
multiplier. Sentences with unknown grammar features get a 0.8 penalty but are not
excluded. The impact is marginal compared to the coverage^1.5 and comprehensibility
terms.

**Gap**: Grammar progression exists but has weak scheduling influence.

### 19.16 Pre-Listening Vocabulary Flash — Not Implemented

**Research says**: Pre-listening activities significantly improve comprehension
(Elkhafaifi 2005). Question preview > vocabulary preview > nothing.

**Current implementation**: Listening mode goes straight to audio playback with no
vocabulary pre-flash.

**Gap**: Could improve listening comprehension with a simple UI addition.

---

## Appendix A: Data Flow Diagrams

### A.1 Review Session Lifecycle

```
Frontend                          Backend
   │                                │
   │  GET /review/next-sentences    │
   │ ─────────────────────────────> │
   │                                ├── build_session()
   │                                │   ├── classify words
   │                                │   ├── focus cohort filter
   │                                │   ├── auto-introduce
   │                                │   ├── fetch candidates
   │                                │   ├── score + set cover
   │                                │   ├── acquisition repetition
   │                                │   ├── order (easy bookends)
   │                                │   └── on-demand generation
   │  <───────────────────────────  │
   │  {items, session_id, ...}      │
   │                                │
   │  (user reviews each card)      │
   │                                │
   │  POST /review/submit-sentence  │
   │ ─────────────────────────────> │
   │  {sentence_id,                 ├── submit_sentence_review()
   │   comprehension_signal,        │   ├── per-word rating
   │   missed_lemma_ids,            │   ├── variant→canonical
   │   confused_lemma_ids}          │   ├── acquiring→Leitner
   │                                │   ├── FSRS→submit_review
   │                                │   ├── leech check
   │  <───────────────────────────  │   └── grammar exposure
   │  {word_results}                │
   │                                │
   │  (after all cards done)        │
   │                                │
   │  POST /review/wrap-up          │
   │ ─────────────────────────────> │
   │  {seen_lemma_ids,              ├── Acquiring + missed words
   │   missed_lemma_ids}            │   → word-level recall quiz
   │  <───────────────────────────  │
   │  {cards}                       │
```

### A.2 Word Introduction Pipeline

```
           Learn Mode          Auto-Introduction       OCR/Story Import
               │                      │                       │
               ▼                      ▼                       ▼
        introduce_word()    _auto_introduce_words()    create ULK
               │                      │              (encountered)
               │                      │                       │
               ▼                      ▼                       │
        start_acquisition()   start_acquisition()             │
        (box 1, scheduled)   (box 1, due_immediately)         │
               │                      │                       │
               │                      │        Learn mode / auto-intro
               ▼                      ▼                       │
        generate_material()   generate_material()             │
        (background task)    (background task)                 │
               │                      │                       ▼
               └──────────┬───────────┘              start_acquisition()
                          │
                          ▼
                   ACQUIRING (box 1)
                          │
                   4h → 1d → 3d
                   (5+ reviews, 60%+ accuracy)
                          │
                          ▼
                   FSRS "learning"
                   (S₀ = 2.3 days)
```

---

## Appendix B: Example Scenarios

### B.1 New User — First Session

1. User imports 50 textbook words via OCR → all become `encountered`
2. User opens Learn mode → sees 5 top-frequency candidates (sorted by
   frequency×0.4 + root×0.3 + encountered_bonus×0.5)
3. User introduces 3 words → each enters acquisition box 1 (due in 4h)
4. User opens review → `build_session()` runs:
   - 3 acquiring words (due immediately since `due_immediately=True`)
   - 0 FSRS words (none graduated yet)
   - Auto-intro adds up to 4 from encountered pool (conservative, <10 reviews)
   - Generates on-demand sentences for each acquiring word
   - Session: ~12 cards (3 acquiring × 4 exposures each + any auto-intros)
5. After 4 hours: words move to box 2 if rated Good (next due: 1 day)

### B.2 Established Learner — Typical Day

1. Open app → `build_session(limit=10)` runs:
   - 5 acquiring words due (boxes 1-3)
   - Cohort has 100 words; 30 FSRS words are due
   - Cohort filter keeps 25 lowest-stability FSRS words + 5 acquiring
   - Auto-intro: accuracy is 82% (70-85% band → 4 slots), acquiring count is 12 (<30), adds up to 4 encountered
   - Greedy set cover selects 10 sentences covering 15 due words
   - Acquisition repetition adds 8 extra cards for acquiring words (4 exposures each)
   - Session: 18 cards, easy bookend ordering
2. User reviews: understood=12, partial=4, no_idea=2
3. Each sentence → per-word ratings:
   - Understood sentences: all words get rating 3
   - Partial sentences: missed words get 1, confused get 2, rest get 3
   - No_idea sentences: all words get 1
4. Acquiring words checked for graduation (box ≥3, seen ≥5, accuracy ≥60%)
5. Post-review leech check on all rating-1/2 words

### B.3 Leech Lifecycle

1. Word كَتَبَ has been reviewed 6 times with only 2 correct (33% accuracy)
2. After a rating=1 review: `check_single_word_leech()` fires
3. times_seen=6 ≥ 5 AND accuracy=33% < 50% → **leech detected**
4. Word suspended: `leech_suspended_at` = now, `knowledge_state` = "suspended"
5. 14 days later: `check_leech_reintroductions()` finds it
6. Word reintroduced: box=1, `acquisition_next_due` = now + 4h, state = "acquiring"
7. Goes through acquisition phase again with fresh context
8. If it leeches again 3+ times → should be flagged for manual review (not yet
   implemented)

---

## Appendix C: Maintaining This Document

When making changes to the scheduling system, update this document:

1. **New constants**: Add to Section 18
2. **Algorithm changes**: Update the relevant section's description and diagrams
3. **New divergences discovered**: Add to Section 19
4. **Divergences resolved**: Move from Section 19 to a "Resolved" subsection or remove
5. **New entry points**: Add to Section 3
6. **New review modes**: Add a new section

Also update:
- `CLAUDE.md` for architectural changes
- `research/experiment-log.md` for algorithm changes
- `IDEAS.md` for new ideas discovered during implementation
