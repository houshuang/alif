# Arabic Learning App: Architecture Research

Research compiled 2026-02-08. This document covers existing open source tools, NLP libraries, spaced repetition systems, text difficulty assessment, word knowledge tracking, and -- critically -- architectural patterns for combining LLM generation with deterministic vocabulary validation.

---

## Table of Contents

1. [Existing Open Source Arabic Learning Tools](#1-existing-open-source-arabic-learning-tools)
2. [Arabic NLP Libraries for Root/Morphology Analysis](#2-arabic-nlp-libraries-for-rootmorphology-analysis)
3. [Spaced Repetition Systems](#3-spaced-repetition-systems)
4. [Text Difficulty Assessment](#4-text-difficulty-assessment)
5. [Word Knowledge Tracking Data Model](#5-word-knowledge-tracking-data-model)
6. [Arabic Morphology and the Root System](#6-arabic-morphology-and-the-root-system)
7. [Frequency Lists and CEFR-Graded Vocabulary](#7-frequency-lists-and-cefr-graded-vocabulary)
8. [Sentence Corpora and Datasets](#8-sentence-corpora-and-datasets)
9. [LLM + Deterministic Tools Architecture](#9-llm--deterministic-tools-architecture)
10. [Sentence Generation with Constraints](#10-sentence-generation-with-constraints)
11. [Recommended Architecture](#11-recommended-architecture)
12. [Implementation Roadmap](#12-implementation-roadmap)

---

## 1. Existing Open Source Arabic Learning Tools

### ArabicDialectHub

- **Repo**: https://github.com/saleml/arabic-dialect-hub
- **License**: MIT
- **Paper**: [ArabicDialectHub: A Cross-Dialectal Arabic Learning Resource and Platform](https://arxiv.org/html/2601.22987)
- **Stack**: React 18 + TypeScript, Netlify CDN
- **What it does**: 552 phrases across 6 Arabic varieties (Moroccan Darija, Lebanese, Syrian, Emirati, Saudi, MSA). Phrases were LLM-generated and validated by native speakers. Features include translation exploration, adaptive quizzing with algorithmic distractor generation, cloud-synchronized progress tracking, and cultural context cards.
- **Relevance**: Demonstrates LLM-generated content validated by humans, adaptive quiz generation, progress tracking. Good reference for quiz UI patterns.

### OpenArabic (edenmind)

- **Repo**: https://github.com/edenmind/OpenArabic
- **License**: Open source
- **Stack**: Microservice mesh providing language services (tashkeel, text-to-speech, lemmatization) to a backend API serving frontend clients. GitOps deployment with Flux2/Flagger.
- **What it does**: Reading platform with short bilingual texts and vocabulary quizzes. Focuses on MSA through Islamic texts (Quran, Hadith, Fiqh, Tafsir, etc.).
- **Relevance**: Microservice architecture for Arabic NLP services is directly applicable. Shows how to decompose tashkeel, TTS, and lemmatization into independent services.

### ArabEngo

- **Repo**: https://github.com/michaelsboost/ArabEngo
- **Status**: Discontinued (2018)
- **What it does**: Simple open source language learning template for English speakers learning Arabic.
- **Relevance**: Limited -- discontinued and basic, but shows early open source attempts.

### LangSeed

- **Repo**: https://github.com/simedw/langseed
- **Live**: https://langseed.com
- **Stack**: Elixir/Phoenix, Gemini API
- **What it does**: Defines new words using only vocabulary the learner already knows, with emojis bridging semantic gaps. Generates fill-in-the-blank and yes/no questions at the learner's level. Supports Chinese, Swedish, English.
- **Relevance**: Directly relevant architecture pattern. The core concept -- LLM constrained to use only known vocabulary -- is exactly the "i+1" generation problem. Does not support Arabic yet, but the architecture is transferable.

### Clozemaster

- **Site**: https://www.clozemaster.com/languages/learn-arabic-online
- **License**: Proprietary (but uses open data)
- **What it does**: Cloze deletion exercises from the Tatoeba dataset (8.5M sentence pairs). Sentences ordered by word frequency. Arabic is supported.
- **Relevance**: Demonstrates frequency-ordered cloze testing at scale. Their approach of sourcing sentences from Tatoeba and ordering by frequency is a proven pattern.

### Wordpecker

- **Repo**: https://github.com/baturyilmaz/wordpecker-app
- **What it does**: Personalized language learning combining Duolingo-style lessons with user-curated vocabulary lists. Users add words from books, articles, or videos and revisit them through interactive quizzes and LLM-generated lessons.
- **Relevance**: Shows the pattern of LLM-generated lessons personalized to user vocabulary.

---

## 2. Arabic NLP Libraries for Root/Morphology Analysis

These are essential for the deterministic side of the architecture: given a word in a generated sentence, determine its root, lemma, and whether the user "knows" it.

### CAMeL Tools (Primary Recommendation)

- **Repo**: https://github.com/CAMeL-Lab/camel_tools
- **Docs**: https://camel-tools.readthedocs.io
- **Paper**: [CAMeL Tools: An Open Source Python Toolkit for Arabic NLP](https://aclanthology.org/2020.lrec-1.868.pdf)
- **Developed by**: CAMeL Lab, NYU Abu Dhabi
- **License**: MIT
- **Install**: `pip install camel-tools` then `camel_data -i light` (for morphology only)

**Key capabilities for this project:**

```python
from camel_tools.morphology.database import MorphologyDB
from camel_tools.morphology.analyzer import Analyzer

db = MorphologyDB.builtin_db()
analyzer = Analyzer(db, backoff='NOAN_PROP')

# Analyze a word -- returns list of possible analyses
analyses = analyzer.analyze('كتب')
# Each analysis contains:
#   root  -> 'ك.ت.ب'
#   lex   -> lemma
#   pos   -> part of speech
#   prc0/prc1/prc2/prc3 -> proclitics
#   enc0  -> enclitic
#   form_gen, form_num -> gender, number
#   ... full morphological feature set
```

**Why it is the best choice:**
- Handles MSA and multiple dialects (Egyptian, Gulf, Levantine, Maghrebi)
- Returns root, lemma, POS, full morphological decomposition
- Has disambiguation (picks the most likely analysis in context)
- Has a morphological generator (given features, generate the word form)
- Active development by a major NLP lab
- Includes tokenization, diacritization, transliteration

### Qalsadi

- **Repo**: https://github.com/linuxscout/qalsadi
- **What it does**: Morphological analysis using a lexical database. Handles vocalized/unvocalized text. Returns root, lemma, POS, morphological features.
- **Relevance**: Alternative to CAMeL Tools, lighter weight. Part of a family of Arabic NLP tools by the same author (Taha Zerrouki).

### Qutuf

- **Repo**: https://github.com/Qutuf/Qutuf
- **What it does**: Arabic Morphological Analyzer (root extraction, stemming) and POS tagger implemented as an expert system.
- **Relevance**: Rule-based rather than statistical, which can be more predictable for deterministic checking.

### wordfreq

- **Repo**: https://github.com/rspeer/wordfreq
- **Install**: `pip install wordfreq`
- **Arabic support**: Yes, with 5 large frequency lists from Wikipedia, subtitles, news, books, web content.
- **Note**: Data is a snapshot through ~2021, no longer being updated.

```python
from wordfreq import word_frequency, zipf_frequency
zipf_frequency('كتاب', 'ar')  # Returns Zipf-scale frequency
```

### CAMeL Arabic Frequency Lists

- **Repo**: https://github.com/CAMeL-Lab/Camel_Arabic_Frequency_Lists
- **What it does**: Frequency lists from the same lab that makes CAMeL Tools.
- **Relevance**: Can be used to assign frequency ranks to words, which correlates with difficulty level.

---

## 3. Spaced Repetition Systems

### FSRS (Free Spaced Repetition Scheduler) -- Recommended

- **Organization**: https://github.com/open-spaced-repetition
- **Python library**: https://github.com/open-spaced-repetition/py-fsrs (`pip install fsrs`)
- **Algorithm paper**: Based on DSR (Difficulty, Stability, Retrievability) model
- **Also available in**: JavaScript, Rust, Go, Elixir, Swift, and more
- **Used by**: Anki (via fsrs4anki plugin), Mochi Cards

```python
from fsrs import Scheduler, Card, Rating

scheduler = Scheduler()
card = Card()

# After reviewing a card:
card, review_log = scheduler.review_card(card, Rating.Good)

# Check retrievability (probability of recall):
retrievability = scheduler.get_card_retrievability(card)

# Cards have states: Learning, Review, Relearning
# Cards and ReviewLogs are JSON-serializable via to_json()/from_json()
```

**Why FSRS over SM-2 (Anki's legacy):**
- Empirically shown to be more efficient (fewer reviews for same retention)
- Adapts to individual memory patterns
- Supports advance/delayed reviews
- Parameters can be optimized from user's own review history via `Optimizer` class
- Open source with implementations in every major language

### What to Track per Card

For Arabic specifically, cards should track knowledge at multiple granularity levels:

| Level | Example | Rationale |
|-------|---------|-----------|
| **Root** | ك.ت.ب | Knowing a root gives partial knowledge of all derived words |
| **Lemma** | كِتَاب (book) | The dictionary form -- core vocabulary unit |
| **Word form** | كُتُب (books) | Specific inflection (broken plural in this case) |
| **Verb form/wazn** | Form II كَتَّبَ | Verb pattern -- knowing the pattern gives predictability |
| **Conjugation** | كَتَبْتُ (I wrote) | Specific person/tense/mood combination |

### Existing Anki Decks for Arabic

- **Lingualism MSA Verb Conjugation Drills**: 25 verbs x 60 conjugations = 1,500 cards, each with audio. Available in Arabic-English and English-Arabic (3,000 total cards).
- **Lingualism Arabic Learner's Dictionary**: Flashcards organized by root.
- **Lingualism Egyptian Arabic Verb Conjugation Drills**: Multiple sets covering common Egyptian verbs.
- **Frequency-based decks**: Available on https://anki-decks.com/anki-decks/arabic/

---

## 4. Text Difficulty Assessment

### BAREC Corpus and Shared Task

- **Dataset**: https://huggingface.co/datasets/CAMeL-Lab/BAREC-Shared-Task-2025-sent
- **Paper**: [A Large and Balanced Corpus for Fine-grained Arabic Readability Assessment](https://arxiv.org/abs/2502.13520)
- **What it is**: 69,441 sentences, 1M+ words, manually annotated across 19 readability levels (from kindergarten to postgraduate). Inter-annotator agreement: 81.8% QWK.
- **Level schemes**: Available in 19-level, 7-level, 5-level, and 3-level granularity.
- **License**: Publicly available.

The 19 levels use Abjad-order naming: 1-alif, 2-ba, 3-jim, ... 19-qaf.

### SAMER Lexicon

- **Paper**: [The SAMER Arabic Text Simplification Corpus](https://arxiv.org/html/2404.18615v1)
- **What it is**: A 40K-lemma lexicon with a 5-level readability scale (L1=easy to L5=specialist). Includes a Google Docs add-on for word-level difficulty visualization.
- **Levels**: L1 (Grade 1, age 6), L2 (Grade 2-3, age 7-8), L3 (Grade 4-5, age 9-10), L4 (Grade 6-8, age 11-14), L5 (specialist, age 15+).
- **Relevance**: Can be used to tag each word in a generated sentence with its difficulty level. Essential for the constraint checker.

### OSMAN Readability Tool

- **Repo**: https://github.com/drelhaj/OsmanReadability
- **What it does**: Open source tool for computing Arabic text readability scores.
- **Relevance**: Can score entire texts or passages for difficulty.

### Approaches to Arabic Readability

Research ([Strategies for Arabic Readability Modeling](https://arxiv.org/html/2407.03032v1)) shows that effective Arabic readability assessment combines:

1. **Lexical features**: Word frequency, word length, rare word ratio
2. **Syntactic features**: Sentence length, clause depth, coordination ratio
3. **Morphological features**: Type-token ratio, verb complexity
4. **ML classifiers**: Trained on BAREC or SAMER data to predict level

For this app, a practical approach is:
- Use SAMER lexicon for word-level difficulty
- Use word frequency (from wordfreq or CAMeL frequency lists) as a proxy for difficulty
- Combine with sentence-level heuristics (length, number of unknown words)

---

## 5. Word Knowledge Tracking Data Model

### Core Data Model

```
┌──────────────────────────────────────────────────────────┐
│                        USER                              │
│  user_id, created_at, settings                           │
└──────────────┬───────────────────────────────────────────┘
               │ 1:N
               ▼
┌──────────────────────────────────────────────────────────┐
│                  USER_ROOT_KNOWLEDGE                     │
│  user_id, root (e.g. 'ك.ت.ب'), familiarity_score,       │
│  first_seen, last_reviewed, review_count                 │
│  ──────────────────────────────────────────────────       │
│  Tracks: does the user recognize this root family?       │
└──────────────┬───────────────────────────────────────────┘
               │ 1:N
               ▼
┌──────────────────────────────────────────────────────────┐
│                  USER_LEMMA_KNOWLEDGE                    │
│  user_id, lemma_id, root, knowledge_state,               │
│  fsrs_card (JSON), last_reviewed, times_seen,            │
│  times_correct, source (explicit_study | encountered)    │
│  ──────────────────────────────────────────────────       │
│  knowledge_state: new | learning | known | lapsed        │
│  ──────────────────────────────────────────────────       │
│  Tracks: does the user know this specific word?          │
└──────────────┬───────────────────────────────────────────┘
               │ 1:N
               ▼
┌──────────────────────────────────────────────────────────┐
│              USER_FORM_KNOWLEDGE                         │
│  user_id, lemma_id, form_type (e.g. 'verb_form_II'),     │
│  inflection (e.g. 'past_3ms'), knowledge_state,          │
│  fsrs_card (JSON)                                        │
│  ──────────────────────────────────────────────────       │
│  Tracks: does the user know this specific inflection?    │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│                    LEMMA (reference)                      │
│  lemma_id, lemma_ar, lemma_en, root, pos,                │
│  frequency_rank, difficulty_level (SAMER L1-L5),         │
│  verb_form (I-X for verbs), cefr_estimate                │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│               ROOT_FAMILY (reference)                    │
│  root, core_meaning_en, word_count,                      │
│  common_lemmas[] (ordered by frequency)                  │
└──────────────────────────────────────────────────────────┘
```

### Knowledge State Transitions

```
    ┌─────┐   study/encounter    ┌──────────┐   FSRS review     ┌───────┐
    │ new ├────────────────────►│ learning  ├──────────────────►│ known │
    └─────┘                     └─────┬─────┘                   └───┬───┘
                                      │                             │
                                      │ fail                   fail │
                                      ▼                             ▼
                                ┌──────────┐                  ┌─────────┐
                                │  new     │◄─────────────────┤ lapsed  │
                                └──────────┘   after timeout   └─────────┘
```

### Key Design Decisions

1. **Root-level knowledge propagation**: When a user learns a word with root ك.ت.ب, their familiarity_score for that root increases. When generating sentences, words sharing a known root can be treated as "partially known" (the user can likely guess the meaning from context + root knowledge).

2. **Implicit vs explicit knowledge**: Track whether a word was explicitly studied (flashcard) or implicitly encountered (appeared in a sentence and was understood). Both contribute to knowledge but with different confidence levels.

3. **FSRS card per lemma**: Each lemma the user is actively studying has an FSRS Card object (JSON-serialized). The scheduler determines when it needs review.

4. **Efficient "is this word known?" lookup**: For the sentence validator, you need a fast set lookup. Maintain a materialized set of known lemma IDs per user (updated on each review). Given a sentence, morphologically analyze each word to get its lemma, then check set membership. This must be fast (sub-100ms for a sentence).

---

## 6. Arabic Morphology and the Root System

### The 10 Verb Forms (Awzan)

Arabic verbs derive from triliteral roots (3 consonants) placed into patterns. The 10 major forms each carry semantic modifications:

| Form | Pattern (f-3-l) | Meaning shift | Example (root: k-t-b) |
|------|-----------------|---------------|----------------------|
| I | fa3ala | Basic | كَتَبَ kataba (he wrote) |
| II | fa33ala | Intensive/causative | كَتَّبَ kattaba (he made write) |
| III | faa3ala | Reciprocal/attempt | كَاتَبَ kaataba (he corresponded with) |
| IV | af3ala | Causative | أَكْتَبَ aktaba (he dictated) |
| V | tafa33ala | Reflexive of II | تَكَتَّبَ (not commonly used for k-t-b) |
| VI | tafaa3ala | Reciprocal reflexive | تَكَاتَبَ takaataba (they corresponded) |
| VII | infa3ala | Passive/reflexive | اِنْكَتَبَ inkataba (it was written) |
| VIII | ifta3ala | Reflexive/middle | اِكْتَتَبَ iktataba (he subscribed) |
| IX | if3alla | Colors/defects | (rare, not used for k-t-b) |
| X | istaf3ala | Seeking/requesting | اِسْتَكْتَبَ istaktaba (he asked to write) |

### Teaching Implications

- **Root families are learning multipliers**: Just 10 common roots can yield 100+ high-frequency words ([Arabic Roots](https://www.arabicroots.org/)).
- **Form patterns are productive**: Once a learner knows Form II = intensive/causative, they can predict the meaning of new Form II verbs from their root.
- **Learning order matters**: Teach high-frequency roots first, then expand through forms. Start with Form I, then II, III, V, VIII (most common augmented forms).

### Recommended Learning Progression

```
Phase 1: Core Vocabulary (A1-A2)
  - Top 100 roots by frequency
  - Form I only (basic verbs)
  - Common nouns and adjectives from these roots
  - Basic function words (particles, pronouns, prepositions)

Phase 2: Expanding Patterns (A2-B1)
  - Forms II, III, V, VIII (most productive)
  - Broken plural patterns (most common ones)
  - Top 300 roots
  - Verbal nouns (masdar) for known forms

Phase 3: Full Derivational Knowledge (B1-B2)
  - All 10 forms for known roots
  - Active/passive participles
  - Pattern recognition across new roots
  - Less common roots up to top 1000

Phase 4: Advanced (B2-C1)
  - Rare forms (IX, XI-XV)
  - Abstract/specialized vocabulary
  - Dialectal variants
  - Full morphological awareness
```

---

## 7. Frequency Lists and CEFR-Graded Vocabulary

### Kelly Project Arabic Word List

- **Source**: https://spraakbanken.gu.se/en/projects/kelly and https://ssharoff.github.io/kelly/
- **License**: CC BY-NC-SA 2.0
- **What it provides**: Frequency lists with ipm (instances per million) and CEFR level (A1-C2) for each word.
- **Languages**: Arabic, Chinese, English, Greek, Italian, Norwegian, Polish, Russian, Swedish.
- **Format**: Excel files.
- **Relevance**: Ready-made CEFR-tagged Arabic word list. Can be directly imported as seed data for the LEMMA reference table.

### Buckwalter & Parkinson Frequency Dictionary

- **Source**: [A Frequency Dictionary of Arabic](https://www.routledge.com/A-Frequency-Dictionary-of-Arabic-Core-Vocabulary-for-Learners/Buckwalter-Parkinson/p/book/9780415444347)
- **What it provides**: 5,000 most frequent MSA words from a 30M-word corpus, organized by frequency and indexable by root.
- **Note**: Not open source (published by Routledge), but the most authoritative Arabic frequency resource.

### wordfreq Python Library

- **Repo**: https://github.com/rspeer/wordfreq
- **Arabic coverage**: 5 large frequency lists
- **Usage**: Quick lookup of word frequency without needing a local database.
- **Limitation**: Snapshot through ~2021, no longer updated.

### CAMeL Arabic Frequency Lists

- **Repo**: https://github.com/CAMeL-Lab/Camel_Arabic_Frequency_Lists
- **Relevance**: From the same team as CAMeL Tools, ensuring compatibility.

### Practical CEFR Mapping Strategy

Since no single authoritative Arabic CEFR word list exists, combine sources:

1. Start with Kelly Project list (has CEFR tags)
2. Fill gaps with frequency rank from CAMeL/wordfreq
3. Map frequency ranks to CEFR levels heuristically:

| Frequency Rank | Estimated CEFR |
|----------------|---------------|
| 1-500 | A1 |
| 501-1500 | A2 |
| 1501-3000 | B1 |
| 3001-5000 | B2 |
| 5001-10000 | C1 |
| 10000+ | C2 |

4. Refine with SAMER difficulty levels for validation.

---

## 8. Sentence Corpora and Datasets

### Tatoeba

- **Source**: https://tatoeba.org/en/downloads
- **License**: CC BY 2.0 FR
- **Arabic-English**: Available as dedicated dataset on [Hugging Face](https://huggingface.co/datasets/ymoslem/Tatoeba-EN-AR)
- **Total**: 8.5M sentence pairs across 414 languages
- **Quality**: Contributed by volunteers; short, simple sentences. Quality varies. Good for beginner-intermediate content.
- **Use**: Seed corpus for cloze exercises, example sentences for vocabulary.

### BAREC Readability Corpus

- **Source**: https://huggingface.co/datasets/CAMeL-Lab/BAREC-Shared-Task-2025-sent
- **What it provides**: 69K sentences annotated with readability levels. Can be used to source sentences at specific difficulty levels.

### SAMER Simplification Corpus

- **What it provides**: 159K words from Arabic novels at L5 difficulty, with simplified versions at L3 and L4. Useful for understanding how to simplify text for learners.

---

## 9. LLM + Deterministic Tools Architecture

This is the core architectural challenge: use an LLM to generate natural, contextually interesting Arabic sentences, but deterministically verify that the sentence meets vocabulary constraints (e.g., exactly one unknown word, all other words are in the user's known set).

### The Problem

LLMs are probabilistic. If you prompt an LLM to "generate a sentence using only these known words plus one new word," it will frequently:
- Use synonyms of known words that are not in the known set
- Use different inflections that the user hasn't learned
- Occasionally hallucinate that a word is in the known set
- Use function words or particles not explicitly in the known set

**You cannot trust the LLM to respect vocabulary constraints.** A deterministic checker is essential.

### Architecture Pattern: Generate-Then-Validate with Retry

```
┌─────────────────────────────────────────────────────────────┐
│                    SENTENCE GENERATION PIPELINE             │
│                                                             │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────────┐  │
│  │          │    │              │    │                  │  │
│  │  PROMPT  ├───►│  LLM CALL    ├───►│  DETERMINISTIC   │  │
│  │ BUILDER  │    │  (generate)  │    │  VALIDATOR       │  │
│  │          │    │              │    │                  │  │
│  └──────────┘    └──────────────┘    └────────┬─────────┘  │
│       ▲                                       │            │
│       │                                       │            │
│       │         ┌──────────────┐              │            │
│       │         │  VALIDATION  │◄─────────────┘            │
│       └─────────┤  RESULT      │                           │
│     (retry      │              │                           │
│      with       │  pass: emit  │                           │
│      feedback)  │  fail: retry │                           │
│                 └──────────────┘                           │
└─────────────────────────────────────────────────────────────┘
```

### Detailed Pipeline

```
Step 1: BUILD PROMPT
  Inputs:
    - Target word (the ONE unknown word to teach)
    - User's known vocabulary set (lemma IDs)
    - Desired difficulty level / sentence length
    - Optional: topic/context preference
  Output:
    - System prompt + user prompt for LLM

Step 2: LLM GENERATION
  - Call LLM (Claude, GPT-4, Gemini, or open-source Arabic LLM)
  - Request: Arabic sentence containing the target word,
    where all other words should be familiar to the learner
  - Also request: English translation, word-by-word gloss
  - Temperature: 0.7-0.9 (enough variety, not too random)

Step 3: DETERMINISTIC VALIDATION
  a) Tokenize the Arabic sentence
  b) For each token:
     - Run CAMeL Tools morphological analysis
     - Extract lemma and root
     - Check if lemma is in user's known_lemmas set
     - Check if lemma is the target word
  c) Validation rules:
     - Exactly 1 word should map to the target lemma: PASS
     - 0 words map to target lemma: FAIL (target word missing)
     - >1 unknown words: FAIL (too many unknowns)
     - All words are known (target is already known): FAIL
     - Grammatically malformed (optional check): FAIL
  d) Output: PASS with analysis, or FAIL with reason

Step 4: RETRY OR EMIT
  If FAIL:
    - Append failure reason to prompt context
    - List the specific unknown words that should be avoided
    - Retry (max 3-5 attempts)
  If PASS:
    - Emit sentence with full morphological annotation
    - Store for presentation to user

Step 5 (optional): QUALITY SCORING
  - Rate sentence naturalness (can use a second LLM call)
  - Rate pedagogical value (does context help guess the target word?)
  - Filter out low-quality sentences even if they pass validation
```

### The Validator in Detail

```python
# Pseudocode for the deterministic validator

def validate_sentence(
    arabic_sentence: str,
    target_lemma: str,
    known_lemmas: set[str],
    analyzer: CamelAnalyzer,
    function_words: set[str]  # common particles, pronouns, etc.
) -> ValidationResult:

    tokens = tokenize(arabic_sentence)
    unknown_words = []
    target_found = False

    for token in tokens:
        # Skip punctuation
        if is_punctuation(token):
            continue

        # Morphological analysis
        analyses = analyzer.analyze(token)

        if not analyses:
            # Unknown to morphological analyzer
            unknown_words.append(token)
            continue

        # Check if ANY analysis matches a known lemma
        token_known = False
        token_is_target = False

        for analysis in analyses:
            lemma = analysis['lex']
            root = analysis['root']

            if lemma == target_lemma:
                token_is_target = True
                target_found = True
                break

            if lemma in known_lemmas:
                token_known = True
                break

            if lemma in function_words:
                token_known = True
                break

            # Root-based partial knowledge check
            if root in known_roots and is_predictable_form(analysis):
                token_known = True
                break

        if not token_known and not token_is_target:
            unknown_words.append(token)

    if not target_found:
        return Fail("Target word not found in sentence")
    if len(unknown_words) > 0:
        return Fail(f"Unknown words: {unknown_words}")
    return Pass(analysis_results)
```

### Function Words Handling

Arabic has a set of ~200-300 high-frequency function words (particles, pronouns, prepositions, conjunctions, demonstratives) that should be treated as "always known" after the earliest stages:

- Conjunctions: و (and), أو (or), لكن (but), ثم (then)
- Prepositions: في (in), من (from), إلى (to), على (on), عن (about)
- Pronouns: أنا, أنت, هو, هي, نحن, etc.
- Demonstratives: هذا, هذه, ذلك, تلك
- Question words: ما, من, أين, كيف, لماذا, متى
- Negation: لا, ليس, لم, لن, ما
- Common verbs: كان (was), يكون (is)

These should be pre-loaded as "known" or tracked separately.

### Alternative Pattern: Constrained Decoding

Instead of generate-then-validate, constrained decoding restricts the LLM's vocabulary at each generation step. This is theoretically more efficient (no retries) but:

- Requires access to model weights (not possible with API-based LLMs like Claude)
- Limited to open-source models
- Can produce unnatural text if constraints are too tight
- Much harder to implement

**Recommendation**: Use generate-then-validate for API-based LLMs. Consider constrained decoding only if using a self-hosted open-source Arabic LLM (e.g., Jais, AceGPT).

### Alternative Pattern: Retrieve-Then-Adapt

Instead of generating from scratch, retrieve a sentence from a corpus (Tatoeba, BAREC) and have the LLM adapt it:

```
1. Query corpus for sentences containing the target word
2. Filter to sentences where most words are in user's known set
3. If a near-match is found (only 1-2 extra unknowns):
   - Ask LLM to rephrase, substituting unknown words with known ones
   - Validate the result
4. If no near-match, fall back to generation
```

This hybrid approach is often faster and produces more natural results because the LLM is editing rather than creating from scratch.

---

## 10. Sentence Generation with Constraints

### The i+1 Principle (Krashen's Input Hypothesis)

Research on comprehensible input establishes that learners need to understand 95-98% of words in a text to acquire new vocabulary from context:

- **95% coverage** (~1 unknown word per 20): Adequate comprehension ([Laufer & Ravenhorst-Kalovski](https://gianfrancoconti.com/2025/02/27/why-the-input-we-give-our-learners-must-be-95-98-comprehensible-in-order-to-enhance-language-acquisition-the-theory-and-the-research-evidence/))
- **98% coverage** (~1 unknown word per 50): Optimal comprehension

For a typical 8-12 word Arabic sentence, this means **exactly 1 unknown word** (or 0 unknowns with one word being reinforced).

### Prompt Engineering for Constrained Generation

```
SYSTEM PROMPT:
You are an Arabic language tutor generating practice sentences.
Rules:
1. Generate a single Arabic sentence containing the word {target_word}
2. The sentence MUST use ONLY words from this approved vocabulary list:
   {known_vocabulary_sample}
3. Keep the sentence natural and contextually meaningful
4. Sentence length: {min_words}-{max_words} words
5. Provide the English translation
6. The sentence should provide enough context to guess the
   meaning of {target_word}

IMPORTANT: Do NOT use any Arabic word whose dictionary form (lemma)
is not in the approved list, except for {target_word}.
Common function words (و، في، من، إلى، على، هو، هي، أنا، هذا، ذلك، ال)
are always permitted.

USER PROMPT:
Target word: {target_word} ({target_word_english})
Topic preference: {topic}
Generate the sentence.
```

### Optimizing the Known Vocabulary in the Prompt

You cannot send 5,000 known words in every prompt. Strategies:

1. **Category sampling**: Send ~200 most relevant known words, grouped by semantic field related to the target word's domain.
2. **Frequency-biased sampling**: Always include top 500 frequency words (the LLM likely uses these naturally) and sample from the rest.
3. **Negative constraints**: Instead of listing all known words, list words the LLM should NOT use (the unknown words). This works when the known set is large and the unknown set is manageable.
4. **Few-shot examples**: Provide 2-3 example sentences that successfully use only known vocabulary, so the LLM calibrates its register.

### Batch Generation for Efficiency

Instead of generating one sentence at a time, generate 5-10 candidate sentences per target word, validate all of them, and keep the ones that pass. This amortizes LLM latency and gives you variety.

```
Generate 5 different sentences for target word: {target_word}
Each sentence should:
- Be in a different context/scenario
- Use different subsets of the known vocabulary
- Vary in length from 6 to 15 words
```

---

## 11. Recommended Architecture

### High-Level System Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                         CLIENT (Mobile/Web)                       │
│                                                                    │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────────┐ │
│  │ Reader  │  │ Flashcard│  │ Quiz     │  │ Progress Dashboard│ │
│  │ View    │  │ Review   │  │ Engine   │  │                    │ │
│  └────┬────┘  └────┬─────┘  └────┬─────┘  └────────┬───────────┘ │
└───────┼────────────┼────────────┼───────────────────┼─────────────┘
        │            │            │                   │
        ▼            ▼            ▼                   ▼
┌────────────────────────────────────────────────────────────────────┐
│                          API GATEWAY                               │
└───────┬────────────┬────────────┬───────────────────┬─────────────┘
        │            │            │                   │
        ▼            ▼            ▼                   ▼
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────────────┐
│ Content  │  │ Review   │  │ Quiz     │  │ Progress/Analytics   │
│ Service  │  │ Service  │  │ Service  │  │ Service              │
└────┬─────┘  └────┬─────┘  └────┬─────┘  └───────────────────────┘
     │              │             │
     ▼              ▼             ▼
┌────────────────────────────────────────────────────────────────────┐
│                      CORE SERVICES LAYER                          │
│                                                                    │
│  ┌──────────────────────┐  ┌────────────────────────────────────┐ │
│  │  SENTENCE GENERATOR  │  │  MORPHOLOGICAL ANALYSIS SERVICE   │ │
│  │                      │  │                                    │ │
│  │  - Prompt builder    │  │  - CAMeL Tools wrapper            │ │
│  │  - LLM caller        │  │  - Tokenization                   │ │
│  │  - Retry logic       │  │  - Lemma extraction               │ │
│  │  - Quality scorer    │  │  - Root extraction                 │ │
│  └──────────┬───────────┘  │  - POS tagging                    │ │
│             │              │  - Diacritization                  │ │
│             ▼              └──────────────┬─────────────────────┘ │
│  ┌──────────────────────┐                │                        │
│  │ VOCABULARY VALIDATOR │◄───────────────┘                        │
│  │                      │                                         │
│  │  - Known word check  │                                         │
│  │  - Function word DB  │                                         │
│  │  - Root knowledge    │                                         │
│  │  - Difficulty score  │                                         │
│  └──────────────────────┘                                         │
│                                                                    │
│  ┌──────────────────────┐  ┌────────────────────────────────────┐ │
│  │  SRS SCHEDULER       │  │  KNOWLEDGE TRACKER                │ │
│  │  (py-fsrs)           │  │                                    │ │
│  │  - Card scheduling   │  │  - User vocab state               │ │
│  │  - Review timing     │  │  - Root familiarity               │ │
│  │  - Optimization      │  │  - Word encounter log             │ │
│  └──────────────────────┘  │  - Known lemma set (fast lookup)  │ │
│                             └────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘
        │                    │                    │
        ▼                    ▼                    ▼
┌──────────────┐  ┌──────────────────┐  ┌─────────────────────────┐
│  LLM API     │  │  PostgreSQL      │  │  Redis                  │
│  (Claude/    │  │                  │  │                         │
│   GPT/       │  │  - Users         │  │  - Known lemma sets     │
│   Gemini)    │  │  - Vocabulary    │  │  - Session cache        │
│              │  │  - Reviews       │  │  - Generated sentence   │
│              │  │  - Sentences     │  │    cache                │
│              │  │  - Progress      │  │                         │
└──────────────┘  └──────────────────┘  └─────────────────────────┘
```

### Technology Stack Recommendations

| Component | Recommendation | Rationale |
|-----------|---------------|-----------|
| **Backend** | Python (FastAPI) | Best Arabic NLP library support (CAMeL Tools), FSRS, data science ecosystem |
| **Frontend** | React/React Native or Flutter | Cross-platform mobile + web |
| **Database** | PostgreSQL | JSON support for FSRS cards, full-text search, mature |
| **Cache** | Redis | Fast set membership checking for known vocabulary |
| **Morphology** | CAMeL Tools | Most comprehensive, actively maintained, handles dialects |
| **SRS** | py-fsrs | Modern, efficient, open source, JSON-serializable |
| **LLM** | Claude API (primary), with fallback to Gemini | Best instruction following for constrained generation |
| **Readability** | SAMER lexicon + BAREC model | Word-level and sentence-level difficulty |
| **Frequency data** | Kelly list + CAMeL frequency lists | CEFR tags + frequency ranks |
| **Sentence corpus** | Tatoeba Arabic-English | CC-licensed, good for beginner-intermediate |

### Data Flow: Learning a New Word

```
1. SCHEDULER selects next word to learn
   └─► Picks highest-priority word from user's learning queue
       (based on FSRS scheduling, frequency rank, root family coverage)

2. SENTENCE GENERATOR creates an i+1 sentence
   └─► Builds prompt with target word + user's known vocabulary
   └─► Calls LLM to generate sentence
   └─► VALIDATOR checks all words against known set
   └─► Retry if validation fails (max 3 attempts)
   └─► Falls back to corpus retrieval if generation fails

3. CLIENT presents sentence to user
   └─► Arabic sentence with tashkeel (diacritics)
   └─► Target word highlighted
   └─► Tap any word to see gloss, root, form
   └─► Audio playback (TTS)

4. USER interacts
   └─► Attempts to understand from context
   └─► Reveals meaning if needed
   └─► Rates understanding (Again/Hard/Good/Easy)

5. KNOWLEDGE TRACKER updates
   └─► FSRS card updated for target word
   └─► Known lemma set updated in Redis
   └─► Root familiarity score updated
   └─► Encounter logged for all words in sentence

6. Repeat from step 1
```

---

## 12. Implementation Roadmap

### Phase 1: Foundation (Weeks 1-4)

- Set up PostgreSQL schema with the data model from Section 5
- Import Kelly word list and CAMeL frequency lists into LEMMA table
- Build ROOT_FAMILY table from CAMeL Tools morphological database
- Implement CAMeL Tools morphological analysis service (wrap as API)
- Build the known vocabulary validator (Section 9 pseudocode)
- Implement basic FSRS integration with py-fsrs

### Phase 2: Generation Pipeline (Weeks 5-8)

- Build the sentence generation prompt system
- Implement generate-then-validate loop with retry
- Build Tatoeba sentence retrieval as fallback
- Add SAMER-based difficulty scoring
- Build batch generation and caching system
- Test with 100 target words, measure validation pass rate

### Phase 3: User-Facing App (Weeks 9-14)

- Build API endpoints (FastAPI)
- Build mobile/web client with reader view and flashcard review
- Implement word-tap morphological popup
- Build progress dashboard
- Add function word bootstrap (pre-load A1 function words)
- User onboarding: placement test or manual level selection

### Phase 4: Refinement (Weeks 15-20)

- Add TTS (text-to-speech) for Arabic sentences
- Add diacritization service (CAMeL Tools)
- Implement root-family learning paths
- Add quiz/exercise generation (cloze, multiple choice)
- Optimize FSRS parameters from user review data
- Add dialectal support (Egyptian, Levantine) via CAMeL Tools dialect models

---

## Key Sources and References

### Open Source Projects
- [ArabicDialectHub](https://github.com/saleml/arabic-dialect-hub) - Cross-dialectal learning platform (MIT)
- [OpenArabic](https://github.com/edenmind/OpenArabic) - Arabic reading platform with microservice architecture
- [LangSeed](https://github.com/simedw/langseed) - Language learning using only known vocabulary
- [CAMeL Tools](https://github.com/CAMeL-Lab/camel_tools) - Arabic NLP toolkit (MIT)
- [py-fsrs](https://github.com/open-spaced-repetition/py-fsrs) - FSRS spaced repetition in Python
- [Qalsadi](https://github.com/linuxscout/qalsadi) - Arabic morphological analyzer
- [OSMAN Readability](https://github.com/drelhaj/OsmanReadability) - Arabic text readability tool
- [wordfreq](https://github.com/rspeer/wordfreq) - Word frequency database for 40+ languages
- [Wordpecker](https://github.com/baturyilmaz/wordpecker-app) - LLM-generated lessons from user vocabulary
- [Awesome Arabic NLP](https://github.com/Curated-Awesome-Lists/awesome-arabic-nlp) - Curated resource list
- [Arabic NLP Tools List](https://github.com/linuxscout/arabicnlptoolslist) - Comprehensive inventory

### Research Papers and Datasets
- [BAREC Readability Corpus](https://huggingface.co/datasets/CAMeL-Lab/BAREC-Shared-Task-2025-sent) - 69K annotated sentences
- [SAMER Text Simplification Corpus](https://arxiv.org/html/2404.18615v1) - 40K-lemma readability lexicon
- [Arabic Readability Modeling Strategies](https://arxiv.org/html/2407.03032v1)
- [Fine-grained Arabic Readability Annotation Guidelines](https://arxiv.org/html/2410.08674v3)
- [Automatic Difficulty Classification of Arabic Sentences](https://aclanthology.org/2021.wanlp-1.11.pdf)
- [CAMeL Tools Paper](https://aclanthology.org/2020.lrec-1.868.pdf)

### Vocabulary and Frequency Resources
- [Kelly Project](https://spraakbanken.gu.se/en/projects/kelly) - CEFR-tagged frequency lists (CC BY-NC-SA 2.0)
- [Buckwalter & Parkinson Frequency Dictionary](https://www.routledge.com/A-Frequency-Dictionary-of-Arabic-Core-Vocabulary-for-Learners/Buckwalter-Parkinson/p/book/9780415444347) - 5K most frequent MSA words
- [Arabic Roots Frequency Dictionary](https://www.arabicroots.org/)
- [CAMeL Arabic Frequency Lists](https://github.com/CAMeL-Lab/Camel_Arabic_Frequency_Lists)
- [Tatoeba Sentence Corpus](https://tatoeba.org/en/downloads) - CC BY 2.0

### Language Learning Theory
- [Comprehensible Input and 95% Coverage](https://gianfrancoconti.com/2025/02/27/why-the-input-we-give-our-learners-must-be-95-98-comprehensible-in-order-to-enhance-language-acquisition-the-theory-and-the-research-evidence/)
- [Input Hypothesis (Wikipedia)](https://en.wikipedia.org/wiki/Input_hypothesis)
- [Clozemaster's Comprehensible Input Approach](https://www.clozemaster.com/blog/comprehensible-input-clozemaster-mirrors-natural-acquisition/)

### Architecture Patterns
- [Constrained Decoding for LLMs](https://mbrenndoerfer.com/writing/constrained-decoding-structured-llm-output)
- [LMQL Constraints](https://lmql.ai/docs/language/constraints.html)
- [Awesome LLM Constrained Decoding](https://github.com/Saibo-creator/Awesome-LLM-Constrained-Decoding)
- [LLM Retry Mechanisms](https://apxml.com/courses/prompt-engineering-llm-application-development/chapter-7-output-parsing-validation-reliability/implementing-retry-mechanisms)
- [Evaluator Reflect-Refine Loop (AWS)](https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-patterns/evaluator-reflect-refine-loop-patterns.html)
- [FSRS Algorithm Wiki](https://github.com/open-spaced-repetition/fsrs4anki/wiki/abc-of-fsrs)
