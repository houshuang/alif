# Corpus-Based Sentence Selection vs LLM Generation: Feasibility Analysis

**Date**: 2026-02-21
**Purpose**: Determine whether large-scale corpus sentence selection can replace or supplement LLM sentence generation in Alif, with analysis of technical feasibility, cost trajectory, and multi-user implications.
**Depends on**: `arabic-sentence-corpora-2026-02-21.md` (corpus survey), `sentence-investigation-2026-02-13/` (benchmark data)

---

## Executive Summary

**Corpus selection is feasible and should be built — but as a hybrid system, not a replacement.** At the current vocabulary size (196 lemmas), corpus hit rates are too low (~0.1-0.3% of sentences pass the comprehensibility gate). But this improves exponentially with vocabulary growth: at 1,000 lemmas, a 500K-sentence corpus covers ~30% of requests; at 3,000 lemmas, ~80%. The system architecture is straightforward — an inverted index by lemma_id feeding into the existing comprehensibility gate — and the hardest parts (clitic stripping, lemma mapping, scoring) are already built.

For single-user use, LLM generation via Claude CLI (free) works fine indefinitely. The corpus investment pays off primarily for: (a) eliminating latency (instant vs 1-30s), (b) authentic/natural sentences, and (c) multi-user scalability where per-request LLM costs become prohibitive.

**Recommended approach**: Build the corpus pipeline at ~500-1,000 lemmas. Start with BAREC (69K graded) + Tatoeba (12K translated) + AMARA/TED (~100K translated). Expand to Hindawi books + WikiMatrix at ~2,000 lemmas.

---

## 1. The Core Technical Question

Given:
- A corpus of N sentences (100K to 10M+)
- A target lemma to practice
- The learner's current known vocabulary (set of lemma_ids)

Can we find a sentence that:
1. Contains the target lemma (or a conjugated/cliticized form)
2. Has ≥60% known scaffold words (comprehensibility gate)
3. Has appropriate difficulty (not too easy, not too hard)
4. Is natural, grammatical, and pedagogically useful

...without calling an LLM?

**Answer: Yes, steps 1-3 are pure computation. Step 4 requires pre-filtering (one-time) but not per-request LLM calls.**

---

## 2. System Architecture

### 2.1 Pre-Processing Pipeline (One-Time Per Corpus)

Every corpus sentence passes through this pipeline once, offline:

```
Raw sentence
    │
    ├─ 1. Sentence split (if from books/articles)
    │     spaCy or simple regex on ، . ! ? ؟
    │
    ├─ 2. Length filter: 4-20 words (discard headlines, fragments, run-ons)
    │
    ├─ 3. Quality filter: discard if >30% Latin chars, all-caps, URL-heavy
    │
    ├─ 4. Auto-diacritize (if source lacks diacritics)
    │     Fine-Tashkeel (open source, ~2.5% WER) — see §4.1
    │
    ├─ 5. Tokenize + lemmatize
    │     Same pipeline as current: normalize → strip clitics → bare form → lookup
    │     Uses build_comprehensive_lemma_lookup() from sentence_validator.py
    │
    ├─ 6. Map every token → lemma_id
    │     REJECT sentence if any content word fails to map
    │     Store: {position: int, surface_form: str, lemma_id: int}
    │
    ├─ 7. Compute lemma_id set (the set of all lemma_ids in this sentence)
    │     This is the key for fast comprehensibility checks
    │
    ├─ 8. Auto-translate (if no translation exists)
    │     Claude CLI batch (free) or Gemini Flash (~$0.01/1K sentences)
    │
    └─ 9. Store in corpus_sentences table with inverted index
```

### 2.2 Runtime Selection (Per Request, No LLM)

```
Request: find_sentence(target_lemma_id=42, known_lemma_ids={1,2,...,196})
    │
    ├─ 1. INDEX LOOKUP: O(1)
    │     SELECT * FROM corpus_sentences
    │     WHERE sentence_id IN (
    │       SELECT sentence_id FROM corpus_sentence_lemmas
    │       WHERE lemma_id = 42
    │     )
    │     → candidates: 0 to thousands
    │
    ├─ 2. COMPREHENSIBILITY FILTER: O(|candidates| × |lemma_set|)
    │     For each candidate:
    │       scaffold = sentence.lemma_ids - {target} - function_words
    │       known = scaffold ∩ known_lemma_ids
    │       IF len(known) / len(scaffold) >= 0.60: PASS
    │     → passing: 0 to hundreds
    │     (With bitset representation: one AND + popcount per sentence)
    │
    ├─ 3. SCORING: same as current session_selector.py
    │     score = coverage^1.5 × difficulty_match × diversity × freshness
    │     → best sentence
    │
    └─ 4. FALLBACK: if no passing sentence → LLM generate (same as today)
```

**Latency**: Steps 1-3 take <10ms even with 1M sentences (inverted index + bitset ops). Compare to 1-30s for LLM generation.

### 2.3 Data Model Extension

```sql
-- New table: pre-processed corpus sentences
CREATE TABLE corpus_sentences (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,          -- 'barec', 'tatoeba', 'hindawi', etc.
    arabic_text TEXT NOT NULL,
    arabic_diacritized TEXT,       -- auto-diacritized if source lacks it
    english_translation TEXT,
    readability_level INTEGER,     -- BAREC 1-19, or estimated
    lemma_id_set TEXT NOT NULL,    -- JSON array of lemma_ids for fast filtering
    word_count INTEGER,
    is_valid BOOLEAN DEFAULT 1,    -- passes quality checks
    created_at TIMESTAMP
);

-- Inverted index: which sentences contain which lemmas
CREATE TABLE corpus_sentence_lemmas (
    sentence_id INTEGER REFERENCES corpus_sentences(id),
    lemma_id INTEGER,
    position INTEGER,
    surface_form TEXT,
    is_function_word BOOLEAN DEFAULT 0,
    PRIMARY KEY (sentence_id, position)
);
CREATE INDEX idx_csl_lemma ON corpus_sentence_lemmas(lemma_id);

-- When a corpus sentence is "promoted" to active use, copy to existing Sentence table
-- This keeps the current session_service.py unchanged
```

**Key insight**: Corpus sentences live in a separate table and are "promoted" into the existing `Sentence` + `SentenceWord` tables when selected for a session. This means `session_service.py` and `sentence_validator.py` need zero changes — they operate on the same `Sentence` model they always have.

---

## 3. Vocabulary Growth & Corpus Hit Rate

### 3.1 The Math

The probability that a random corpus sentence passes the comprehensibility gate depends on:
- **V**: learner's vocabulary size (lemma count)
- **T**: total unique lemmas in the language (~14,000 for 98% coverage)
- **L**: sentence length (in content words, excluding function words)
- **k**: comprehensibility threshold (0.60)

For a random sentence of length L, the probability that ≥k fraction of words are known:

```
P(pass) = Σ(i=⌈kL⌉ to L) C(L,i) × (V/T)^i × (1-V/T)^(L-i)
```

This is a binomial distribution. But real corpus sentences aren't random — word frequency follows Zipf's law, and high-frequency words disproportionately appear. So actual hit rates are better than the naive calculation.

### 3.2 Empirical Projections

From the Feb 13 benchmark (BAREC + Tatoeba, 196 lemmas):

| Corpus | Total | Passing (196 lemmas) | Hit Rate |
|--------|-------|---------------------|----------|
| Tatoeba | 4,954 | 13 | 0.26% |
| BAREC | 29,758 | 22 | 0.07% |

Extrapolating using Zipf's law (coverage grows as V^0.7 due to diminishing returns on less frequent words):

| Learner Vocab | Est. Hit Rate (69K BAREC) | Passing Sentences | With 500K Corpus |
|:---:|:---:|:---:|:---:|
| 200 | 0.1% | ~70 | ~500 |
| 500 | 1.5% | ~1,000 | ~7,500 |
| 1,000 | 5% | ~3,500 | ~25,000 |
| 2,000 | 15% | ~10,000 | ~75,000 |
| 3,000 | 30% | ~21,000 | ~150,000 |
| 5,000 | 55% | ~38,000 | ~275,000 |

**At 1,000 lemmas with a 500K corpus, you have ~25,000 comprehensible sentences — more than enough for any session.**

### 3.3 Per-Word Coverage

The harder question is whether there's a comprehensible sentence for each *specific target word*. Common words (frequency rank 1-500) appear in many sentences, so finding matches is easy. Rare words (rank 3000+) may appear in only a handful of corpus sentences, and those sentences may contain too many other unknowns.

Estimated per-word match rates (500K corpus, 1,000 known lemmas):

| Word Frequency Rank | Sentences Containing It | Comprehensible Matches | Coverage |
|:---:|:---:|:---:|:---:|
| 1-100 (very common) | 5,000-50,000 | 250-2,500 | ~100% |
| 100-500 | 500-5,000 | 25-250 | ~95% |
| 500-1,000 | 100-500 | 5-25 | ~80% |
| 1,000-3,000 | 20-100 | 1-5 | ~50% |
| 3,000+ (rare) | 1-20 | 0-1 | ~20% |

**This is where LLM fallback remains essential**: for rare words and unusual vocabulary combinations, no pre-existing corpus will have the right sentence. LLM generation handles the long tail perfectly.

---

## 4. The Metadata Gap: What Corpora Don't Give You

### 4.1 Diacritization

**Problem**: Most large corpora have no diacritics. Learners need them.

**Solution landscape** (ranked by quality):

| Tool | Type | Word Error Rate | Open Source | Speed |
|------|------|:---:|:---:|:---:|
| Sukoun | BERT-based | 1.9% | Yes | Fast |
| Fine-Tashkeel | Fine-tuned ByT5 | 2.5% | Yes (HuggingFace) | Fast |
| Shakkala | BiLSTM | 6.4% | Yes | Fast |
| Mishkal | Rule-based | 22-40% | Yes | Very fast |
| Gemini Flash | LLM | ~3% | No (API) | 1-2s |

**Recommendation**: Fine-Tashkeel for bulk processing (free, fast, 2.5% WER). At 2.5% WER on a 10-word sentence, ~78% of sentences have perfect diacritics. For the remaining 22%, most errors are on case endings (least important for learners). This is acceptable quality for a language learning app — especially since Alif already has tashkeel fading that hides diacritics for known words.

**Cost**: $0 (runs locally on GPU or CPU). Processing 500K sentences takes ~2-4 hours on a laptop GPU.

### 4.2 Translation

**Problem**: Only ~30% of available corpus sentences come with English translations.

**Solution options**:

| Approach | Cost per 100K sentences | Quality | Speed |
|----------|:---:|:---:|:---:|
| Claude CLI (Sonnet, free) | $0 | Very good | ~8 hours |
| Claude CLI (Haiku, free) | $0 | Good | ~4 hours |
| Gemini Flash API | ~$1-5 | Good | ~30 min |
| Google Translate API | ~$30 | Adequate | ~5 min |
| Pre-translated (Tatoeba, WikiMatrix) | $0 | Variable | N/A |

**Recommendation**: Process pre-translated corpora first (Tatoeba, WikiMatrix, AMARA). For untranslated corpora (BAREC, Hindawi, OSIAN), use Claude CLI in batch mode — free and good quality, just slow. This is a one-time cost.

### 4.3 Lemma Mapping

**Problem**: Every token in every sentence must map to a lemma_id in our database.

**This is already solved**: `build_comprehensive_lemma_lookup()` + `map_tokens_to_lemmas()` handles this. The pipeline strips clitics, normalizes, and looks up in priority order: direct match → al-prefix variant → forms_json inflections → CAMeL disambiguation.

**Sentences with unmapped content words are rejected.** This is fine — we'd rather have 300K clean sentences than 500K partially-mapped ones. The rejection rate depends on our vocabulary coverage:

| Learner Vocab | Unmappable Content Words | Sentence Rejection Rate |
|:---:|:---:|:---:|
| 200 | ~95% of all lemmas | ~99% |
| 1,000 | ~85% | ~90% (but we only care about sentences passing comprehensibility anyway) |
| 5,000 | ~50% | ~40% |

**Key insight**: The mapping rejection and comprehensibility gate are correlated — a sentence that passes the 60% gate by definition has most of its words mapped. The unmapped words are just the unknown scaffold, which is acceptable.

Wait — this requires rethinking. We need lemma_ids for ALL tokens (not just known ones) to build `SentenceWord` entries. Options:

1. **Map against full lemma database** (not just user's known words): We have ~1,400 lemmas in the database already. Any word not in our database gets `lemma_id=NULL` — but the rule says all SentenceWords must have lemma_ids.

2. **Relax the rule for corpus sentences**: Allow `lemma_id=NULL` for scaffold words that aren't in our database (like the book import exception). The comprehensibility gate ensures the user knows enough context.

3. **Import new lemmas on demand**: When a corpus sentence has an unmapped word, create a new `Lemma` entry in "encountered" state. This grows the vocabulary database organically.

**Option 3 is the most elegant**: it's what the book import pipeline already does. The corpus becomes a source of new vocabulary, not just sentences.

---

## 5. Cost Analysis: LLM vs Corpus at Scale

### 5.1 Single User (Current Situation)

| | LLM Generation (Claude CLI) | Corpus Selection |
|--|:---:|:---:|
| Per-sentence cost | $0 (free via Max plan) | $0 |
| Per-sentence latency | 15-30s (Sonnet) | <10ms |
| Quality | Very good (87% compliance) | Pre-validated |
| Maintenance | None | One-time setup (~1 day) |

**Verdict**: For single user with Max plan, LLM generation is fine. Corpus adds speed but not cost savings.

### 5.2 Multi-User (Future Scenario)

Assumptions: 100 users, 20 sentences/day each = 2,000 requests/day = 60,000/month.

| | LLM (Gemini Flash) | LLM (Claude API) | Corpus + LLM Fallback |
|--|:---:|:---:|:---:|
| Monthly requests | 60,000 | 60,000 | ~12,000 LLM + 48,000 corpus |
| Monthly LLM cost | ~$60-180 | ~$300-900 | ~$12-36 |
| Infrastructure | API keys | API keys | SQLite index + API keys |
| Latency (p50) | 1-2s | 1-3s | <10ms |
| Latency (p99) | 5-10s | 10-30s | 1-2s (LLM fallback) |

**Verdict**: Corpus reduces LLM costs by ~80% at scale. More importantly, it eliminates the latency bottleneck — sentences are instant.

### 5.3 One-Time Corpus Processing Cost

| Step | 500K Sentences | Cost | Time |
|------|:---:|:---:|:---:|
| Download + clean | — | $0 | 2h |
| Diacritize (Fine-Tashkeel) | 500K | $0 | 3h (GPU) |
| Lemmatize + map | 500K | $0 | 1h |
| Translate (Claude CLI) | ~350K untranslated | $0 | 8h |
| Index + store | 500K | $0 | 10min |
| **Total** | | **$0** | **~14h (mostly automated)** |

With Gemini Flash instead of Claude CLI for translation: ~$5-15 total, done in 1h.

---

## 6. Quality Comparison

### 6.1 Naturalness

| Source | Naturalness (1-5) | Notes |
|--------|:---:|:--|
| LLM-generated (Gemini) | 4.89 | Occasionally formulaic |
| LLM-generated (Sonnet) | 4.94 | Best quality but slowest |
| BAREC corpus | 4.80 | From textbooks/literature — authentic |
| Tatoeba | 4.30 | Crowd-sourced, uneven quality |
| News corpora (OSIAN) | 3.50 est. | Formal, dry, often complex |
| Subtitle corpora | 3.00 est. | Colloquial, often dialectal |

**Corpus sentences are authentic but not always pedagogically optimal.** An LLM can be told "write a sentence using these 5 words at A2 difficulty" — a corpus can only offer what it has.

### 6.2 Vocabulary Targeting

LLM generation's killer advantage: it creates sentences with exactly the target word embedded in known context. Corpus selection finds sentences that *happen* to contain the target word in *hopefully* comprehensible context.

At low vocabulary sizes, LLM wins decisively. At high vocabulary sizes (2,000+), the corpus catches up because almost any sentence is comprehensible.

### 6.3 Research on Learner Preferences

A study comparing GenAI vs corpus sentences found learners preferred AI-generated sentences 66% of the time (265/400 pairwise comparisons). However, the comparison was against raw corpus sentences without difficulty filtering. A well-selected corpus sentence (right difficulty, right length, good context) may perform better.

**The Pilan et al. (2016) result is encouraging**: their hybrid heuristic+ML selector produced sentences rated at the same level as dictionary examples, and significantly better than random corpus selections. This suggests that *selection quality* matters more than *source quality*.

---

## 7. Implementation Phases

### Phase 0: Now (196 lemmas) — No Change

Keep current LLM generation. Corpus hit rate is too low to justify the infrastructure investment.

**Action**: None. Focus on vocabulary growth.

### Phase 1: At ~500 Lemmas — Corpus Warm Cache

**Goal**: Import BAREC + Tatoeba as supplementary sentence pool.

**Work items**:
1. Download BAREC from Hugging Face (69K sentences, graded)
2. Download Tatoeba Arabic-English pairs (~12K translated)
3. Write import script: tokenize → lemmatize → map → store in `corpus_sentences` table
4. Add inverted index (`corpus_sentence_lemmas`)
5. Add corpus lookup step before LLM generation in `material_service.py`
6. Pre-compute diacritizations for BAREC (Fine-Tashkeel)
7. Generate missing translations (Claude CLI batch)

**Estimated effort**: 2-3 days of development, ~$0 ongoing cost.
**Expected result**: ~1,000-2,000 usable corpus sentences. LLM fallback for the rest.

### Phase 2: At ~1,000 Lemmas — Primary Corpus

**Goal**: Expand corpus to 500K+ sentences. Corpus becomes primary source.

**Additional sources**:
- Hindawi E-Book Corpus (sentence-split, diacritize, translate)
- WikiMatrix filtered pairs (margin score >1.06)
- AMARA/TED translated subtitles

**Work items**:
1. Hindawi download + sentence splitting pipeline
2. WikiMatrix download + quality filtering
3. AMARA integration
4. Optimize inverted index for 500K+ sentences
5. Add readability scoring (BAREC-trained model or SAMER lexicon integration)

**Estimated effort**: 1 week of development, ~$5-15 for Gemini Flash translation.
**Expected result**: ~25,000 usable corpus sentences at 1,000 lemmas. LLM fallback <50%.

### Phase 3: At ~2,000 Lemmas or Multi-User Decision — Full Pipeline

**Goal**: 1M+ sentences, corpus handles 80%+ of requests.

**Additional sources**:
- OSIAN news (filtered for short/simple)
- Leipzig Corpora Collection
- Anna's Archive novels (OCR + extraction pipeline)
- Saudi IEN textbook extraction

**Work items**:
1. Large-scale import pipeline with parallel processing
2. Dialect detection + filtering (for OpenSubtitles, mixed sources)
3. Duplicate detection across sources
4. Quality scoring model (trained on BAREC labels)
5. User-facing "sentence source" indicators (corpus vs generated)
6. Multi-user infrastructure (shared corpus, per-user known set)

**Estimated effort**: 2-3 weeks of development.
**Expected result**: 80%+ corpus hit rate. LLM costs drop to <$15/month for 100 users.

### Phase 4: Multi-User Launch — Content Platform

**Goal**: User-imported content (like LingQ model) + curated corpus.

**Features**:
- Import any Arabic text (paste, URL, PDF)
- Automatic difficulty assessment
- Automatic sentence extraction + lemma mapping
- "Library" of pre-processed Arabic books and articles
- Shared corpus grows as users import content

---

## 8. Risks and Mitigations

### Risk: Corpus sentences too formal/dry for engagement
**Mitigation**: Prioritize Hindawi fiction and children's lit over news corpora. Mix corpus and LLM sources in sessions (source_bonus scoring already supports this).

### Risk: Auto-diacritization errors confuse learners
**Mitigation**: Fine-Tashkeel at 2.5% WER means ~78% of 10-word sentences are perfect. For remaining sentences, most errors are case endings (which Alif already fades). Flag sentences with low diacritization confidence for manual review or LLM verification.

### Risk: Translation quality from auto-translate
**Mitigation**: Claude CLI translation quality is comparable to professional for simple MSA sentences. Add a confidence score and flag low-confidence translations. Pre-translated corpora (Tatoeba, WikiMatrix, AMARA) avoid this entirely.

### Risk: Lemma mapping gaps cause silent errors
**Mitigation**: Reject sentences with unmapped content words. This is the existing approach and works well. At scale, unmapped words can be flagged for manual review to grow the lemma database.

### Risk: Dialect contamination from OpenSubtitles/mixed sources
**Mitigation**: Run dialect detection (CAMeL Tools has a dialect identification model) on import. Only import sentences classified as MSA with high confidence.

### Risk: Investing too early before vocabulary justifies it
**Mitigation**: Phase 1 is minimal (2-3 days, $0 cost) and provides data on actual hit rates. Abort or accelerate based on real numbers.

---

## 9. Comparison with Existing Systems

| System | Approach | Arabic Support | Sentences | Personalized |
|--------|----------|:---:|:---:|:---:|
| **Clozemaster** | Corpus-only (Tatoeba) | Yes (~50K) | Static, frequency-ordered | No |
| **LingQ** | User-imported text | Yes (limited) | User-selected | Somewhat |
| **Migaku** | Browser extension + tracking | Yes | From web browsing | Yes |
| **MorphMan** | Anki reordering | No Arabic | Anki cards only | Yes (strict i+1) |
| **ML_for_SLA** | Neural selection from corpus | No Arabic | 1.1M (Japanese) | Yes |
| **Alif (current)** | LLM generation | Yes | ~600 active | Fully personalized |
| **Alif (proposed)** | Corpus + LLM fallback | Yes | 100K-1M+ corpus | Fully personalized |

**Alif's advantage**: Full morphological awareness (clitic stripping, root-pattern system) + FSRS-integrated difficulty matching + LLM fallback for the long tail. No existing system combines corpus selection with Arabic morphological awareness AND spaced repetition.

---

## 10. Conclusion

### Should we build this?

**Yes, starting at ~500 lemmas.** The infrastructure is modest (2-3 days for Phase 1), the cost is near-zero, and it provides:
- Instant sentence retrieval (<10ms vs 1-30s)
- Authentic, natural sentences from real Arabic text
- Foundation for multi-user scalability
- Growing sentence pool as vocabulary expands

### Is LLM generation still needed?

**Yes, indefinitely.** LLM generation handles:
- Rare words with no corpus matches
- Specific vocabulary combinations (set cover for multiple target words)
- New vocabulary at the edge of the learner's knowledge
- On-demand requests where no pre-computed sentence exists

The long-term ratio shifts from 100% LLM → ~20% LLM as vocabulary and corpus grow, but it never reaches 0%.

### When to build it?

| Phase | Trigger | Investment | Payoff |
|:---:|:--|:---:|:--|
| 1 | 500 lemmas | 2-3 days, $0 | ~1-2K corpus sentences, real hit rate data |
| 2 | 1,000 lemmas | 1 week, ~$10 | 25K+ corpus sentences, corpus becomes primary |
| 3 | Multi-user decision | 2-3 weeks | 80%+ corpus, <$15/mo for 100 users |

### What makes this NOT a typical "build vs buy" question?

The key insight is that **we already have 90% of the infrastructure**. The comprehensibility gate, lemma mapping, clitic stripping, session scoring — all exist and work. The corpus pipeline is "just" a new data source feeding into the existing machinery. The incremental work is:

1. A pre-processing script (one-time)
2. An inverted index (one table + one query)
3. A "try corpus first" check (one function call before LLM generation)

This isn't a new system — it's a new data source.

---

## Sources

- Alif internal: `sentence-investigation-2026-02-13/` (corpus evaluation, 0.1-0.3% hit rate at 196 lemmas)
- Alif internal: `arabic-sentence-corpora-2026-02-21.md` (corpus survey, 16 sources catalogued)
- Pilan, Volodina, Borin (2016): "Candidate Sentence Selection for Language Learning Exercises" — hybrid heuristic+ML selector matched dictionary example quality
- Laufer (1989): 95% vocabulary coverage threshold for comprehension
- Hu & Nation (2000): 98% threshold for unassisted reading
- BAREC Shared Task (2025): 69K sentences, 19 readability levels, CAMeL Lab
- SAMER Project: 40K Arabic lemmas with 5-level readability annotations
- Fine-Tashkeel: Fine-tuned ByT5, 2.5% WER, [HuggingFace](https://huggingface.co/basharalrfooh/Fine-Tashkeel)
- Sukoun: BERT-based diacritization, 1.9% WER
- ML_for_SLA: Neural comprehensible input selection, [GitHub](https://github.com/JonathanLaneMcDonald/ML_for_SLA)
- Masrai (2020): Arabic vocabulary coverage thresholds (1K=79%, 5K=89%, 9K=95%)
- GenAI vs corpus study: learners preferred AI sentences 66% of the time
- Set cover: NP-hard, greedy achieves ln(n) approximation — directly applicable to session building
