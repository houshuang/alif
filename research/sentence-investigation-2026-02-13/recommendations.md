# Sentence Pipeline Recommendations

**Date**: 2026-02-13
**Based on**: Corpus evaluation (Tatoeba + BAREC) and LLM benchmark (3 models x 6 strategies, 213 sentences)

---

## 1. Executive Summary

The biggest problem with the current sentence pipeline is not the model -- it is the vocabulary window. GPT-5.2 with the current `KNOWN_SAMPLE_SIZE=50` achieves only 57% vocabulary compliance. Simply showing the LLM the full 196-word vocabulary jumps compliance to 88%. This is a one-line fix with a 31-percentage-point improvement, and it should be deployed immediately.

Beyond that fix, the benchmark reveals that Gemini Flash is the best model for this task. It matches or exceeds GPT-5.2 on quality (4.89 vs 4.63 average) while delivering substantially higher vocabulary compliance (84% vs 74% overall), at roughly half the cost and latency. The best single combination in the benchmark is Gemini Flash + Strategy D (set-cover batch): 4.97/5 quality with 92% compliance. The highest compliance overall is Gemini Flash + Strategy E (two-pass rewrite) at 93%, though with a quality dip to 4.78/5.

Corpora (Tatoeba, BAREC) are not viable as a primary sentence source at 196 words -- only 35 sentences pass the comprehensibility gate across both corpora combined. BAREC becomes a meaningful supplement around 500-1000 words, but LLM generation will remain the primary path for the foreseeable future.

## 2. Model Comparison

Ranked by quality x compliance (the product that matters for production):

| Rank | Model | Avg Quality | Avg Compliance | Quality x Compliance | Avg Latency | Relative Cost |
|------|-------|-------------|----------------|---------------------|-------------|---------------|
| 1 | **Gemini Flash** | 4.89/5 | 84% | **4.11** | 1,867ms | ~$0.003/sentence |
| 2 | Claude Sonnet | 4.94/5 | 70% | 3.46 | 3,115ms | ~$0.015/sentence |
| 3 | GPT-5.2 | 4.63/5 | 74% | 3.43 | 1,910ms | ~$0.015/sentence |

Key observations:

- **Gemini Flash wins decisively.** It has the best compliance by a wide margin (84% vs 74% vs 70%), competitive quality, the lowest latency, and the lowest cost. The compliance gap means fewer retry loops, which multiplies the cost and latency advantage.
- **Claude Sonnet has the highest raw quality** (4.94) but the lowest compliance (70%). It is also the slowest and most expensive. Not recommended as primary.
- **GPT-5.2 is worst on quality** (4.63) across all models tested. It produces the most grammar errors, gender mismatches, and incoherent sentences (all 5 "word salad" sentences in the bottom-10 came from GPT-5.2). Its only advantage is familiarity -- it is the current production model.

## 3. Strategy Comparison

Ranked by the quality x compliance product, averaged across all models:

| Rank | Strategy | Avg Quality | Avg Compliance | Quality x Compliance | Tokens (in+out) | Notes |
|------|----------|-------------|----------------|---------------------|-----------------|-------|
| 1 | **D: Set-cover batch** | 4.81 | 85% | **4.09** | 322 | Lowest token usage; generates 5-10 sentences per call |
| 2 | **E: Two-pass rewrite** | 4.57 | 93% | **4.25** | 611 | Highest compliance; quality dip on GPT-5.2 |
| 3 | A-pos: POS-grouped | 5.00 | 87% | 4.35 | 3,297 | Only tested on GPT-5.2 (n=5), small sample |
| 4 | A-full: Full vocab | 4.91 | 77% | 3.78 | 3,425 | Full 196 words, baseline prompt |
| 5 | B: Arabic-only first | 4.85 | 73% | 3.54 | 2,121 | Marginal improvement over baseline |
| 6 | C: Relaxed vocabulary | 4.85 | 71% | 3.44 | 2,068 | Actively worse compliance than baseline |
| 7 | A: Baseline (50 words) | 4.81 | 68% | 3.27 | 2,048 | Current production approach |

Best model+strategy combinations:

| Combination | Quality | Compliance | Q x C | n |
|-------------|---------|------------|-------|---|
| **Gemini Flash + D** | 4.97 | 92% | **4.57** | 10 |
| Gemini Flash + E | 4.78 | 93% | 4.45 | 15 |
| GPT-5.2 + A-pos | 5.00 | 87% | 4.35 | 5 |
| GPT-5.2 + A-full | 4.80 | 88% | 4.22 | 5 |
| Claude Sonnet + C | 4.98 | 72% | 3.59 | 15 |

Strategy D (set-cover batch) deserves particular attention: it uses only 322 tokens per sentence (vs 2,048 for baseline), meaning it is roughly 6x cheaper per sentence while achieving higher compliance. The batch approach naturally constrains the LLM to the vocabulary because it must cover specific target words simultaneously.

Strategy E (two-pass rewrite) achieves the highest compliance (93%) but at a quality cost, particularly with GPT-5.2 which produces incoherent "word salad" when forced to rewrite with strict constraints. With Gemini Flash, Strategy E quality is still reasonable (4.78/5).

**Strategy B (Arabic-only first pass) and Strategy C (relaxed vocabulary) do not justify their additional complexity.** Neither meaningfully improves over the baseline.

## 4. Critical Bug: KNOWN_SAMPLE_SIZE

The current production setting `KNOWN_SAMPLE_SIZE = 50` randomly selects only 50 of the learner's 196 known words to include in the LLM prompt. This means the LLM literally does not know 75% of the learner's vocabulary and cannot use those words, causing systematic compliance failures.

Impact measured in the benchmark (Strategy A, same prompt, same model):

| Model | 50 words | Full 196 words | Delta |
|-------|----------|----------------|-------|
| GPT-5.2 | 57% compliance | 88% compliance | **+31pp** |
| Gemini Flash | 80% compliance | 78% compliance | -2pp |
| Claude Sonnet | 68% compliance | 65% compliance | -3pp |

The GPT-5.2 result is dramatic: compliance goes from 57% (nearly half the sentences fail) to 88% (nearly all pass). This single change would eliminate roughly half of all retry loops in production.

Interestingly, Gemini Flash and Claude Sonnet show no improvement or slight degradation with the full vocabulary. This suggests these models are already good at inferring what an Arabic learner might know, while GPT-5.2 specifically benefits from seeing the complete word list. However, since GPT-5.2 is the current production model, this fix has enormous immediate value.

**The most common compliance failure is wa-prefixed conjunction forms** (وواسعة, وجميل, وواسع, ولكنه). These are words the learner knows (واسع, جميل, لكن) with the conjunction و attached. The validator's clitic stripping should handle these; the fact that they appear as "unknown" suggests the validator is not stripping the و-prefix for these forms, or the LLM is generating forms not in the known-forms list. This is a separate validator issue worth investigating.

**Recommendation**: Change `KNOWN_SAMPLE_SIZE` from 50 to the full vocabulary size (remove the sampling entirely, or set it to 500+). At 196 words, the token overhead is ~1,200 extra input tokens per call, costing roughly $0.001 extra. This is negligible.

## 5. Corpus Viability

### Current state (196 words): Not viable as primary source

| Corpus | Passing sentences | Needed (2/word) | Coverage |
|--------|-------------------|-----------------|----------|
| Tatoeba | 13 | 392 | 3.3% |
| BAREC | 22 | 392 | 5.6% |
| Combined | 35 | 392 | 8.9% |

### Projected growth

| Vocab size | Tatoeba | BAREC | Combined |
|------------|---------|-------|----------|
| 196 | 13 | 22 | 35 |
| ~400 | 54 | 104 | 158 |
| ~1,000 | 106 | 267 | 373 |

At ~1,000 words, the combined pool (~373 sentences) roughly equals the minimum need (2 per word = ~2,000 needed, so ~19% coverage). Still insufficient as a sole source, but meaningful as a supplement.

### Quality comparison

BAREC significantly outperforms Tatoeba on naturalness (4.8/5 vs 4.3/5) because BAREC contains authentic published Arabic text, while Tatoeba consists of volunteer translations that sometimes sound stilted.

### Recommended hybrid approach

1. **Now (196 words)**: LLM generation only. Corpus matches are too sparse.
2. **At 400-500 words**: Begin BAREC integration as a "warm cache." For each target word, check BAREC first; generate with LLM only if no corpus match exists. Expected savings: ~25% of sentences from corpus.
3. **At 1,000+ words**: BAREC becomes a primary source for common words. LLM generation fills gaps for uncommon words and specific grammar targets. Expected savings: ~40-50% of sentences from corpus.

### BAREC integration requirements

BAREC sentences need LLM post-processing before use:
- Diacritization (BAREC has almost none)
- English translation
- Transliteration
- Estimated cost: ~$0.001 per sentence (one-time, amortized over all uses)

This is 10-50x cheaper than generating a new sentence from scratch.

## 6. Recommended Production Pipeline

### Phase 1: Quick fixes (deploy this week)

**Model**: Keep GPT-5.2 for now (change in Phase 2)
**Key changes**:
1. Set `KNOWN_SAMPLE_SIZE = 500` (effectively "all words")
2. Group vocabulary by POS in the prompt (A-pos strategy showed 5.0/5 quality, 87% compliance)
3. Fix quality gate to **fail-closed** (currently fails open -- if Gemini Flash is unavailable, all sentences pass without review)

Expected impact: Compliance from 57% to ~87%, quality gate catches remaining failures.

### Phase 2: Model + strategy switch (deploy within 2 weeks)

**Model**: Switch from GPT-5.2 to **Gemini Flash** as primary sentence generation model.
**Strategy**: Adopt **Strategy D (set-cover batch)** for batch generation (update_material.py, pregenerate), and **Strategy A with full vocabulary + POS grouping** for on-demand single-sentence generation.
**Fallback**: GPT-5.2 as secondary, Claude Sonnet as tertiary (current Gemini primary / GPT fallback / Claude tertiary cascade already exists in llm.py for general tasks; extend to sentence generation).

Cost projection:
- Current: ~$0.03/sentence (GPT-5.2, ~2,000 tokens, multiple retries at 57% compliance)
- Phase 1: ~$0.02/sentence (GPT-5.2, ~3,200 tokens, fewer retries at ~87% compliance)
- Phase 2: ~$0.005/sentence (Gemini Flash Strategy D, ~322 tokens, 92% compliance)
- Net savings: ~85% cost reduction per sentence

### Phase 3: Corpus integration (at 500 words, ~3-4 months)

1. Import BAREC into a local `corpus_sentences` table (one-time)
2. Pre-compute vocabulary compliance scores for each corpus sentence against the learner's current vocabulary
3. During `build_session()`, check corpus first before triggering LLM generation
4. Run LLM post-processing (diacritics + translation) on corpus matches, cache results
5. Quality gate applies equally to corpus and generated sentences

### Quality gate architecture (all phases)

```
Sentence candidate (LLM or corpus)
  -> Rule-based vocabulary validation (>=70% known content words)
  -> Gemini Flash quality review (naturalness + translation accuracy)
  -> FAIL-CLOSED: if quality review unavailable, reject sentence
  -> Store if passes both gates
```

The fail-closed change is important. The current fail-open design means that when Gemini Flash has an outage or rate-limit, every generated sentence passes unreviewed. Given that GPT-5.2 produces "word salad" sentences (5/15 quality scores in the benchmark), unreviewed sentences can actively harm learning.

## 7. Implementation Priority

Ordered by impact per effort:

| Priority | Change | Impact | Effort | Details |
|----------|--------|--------|--------|---------|
| **1** | Increase `KNOWN_SAMPLE_SIZE` to 500 | +31pp compliance for GPT-5.2 | 5 minutes | Change one constant in `sentence_generator.py` |
| **2** | Fix quality gate to fail-closed | Prevents word-salad sentences reaching learners | 30 minutes | Change `review_sentences_quality()` return on failure |
| **3** | POS-group vocabulary in prompt | +87% compliance, +5.0/5 quality (on GPT-5.2) | 2 hours | Group known words by noun/verb/adj/etc in prompt template |
| **4** | Switch to Gemini Flash for generation | +20pp compliance, -50% latency, -80% cost | 4 hours | Change model in `generate_sentences()`, update prompt, test |
| **5** | Adopt Strategy D for batch generation | 6x fewer tokens, highest compliance | 8 hours | New batch prompt, update `update_material.py` and `material_generator.py` |
| **6** | Add clitic-awareness to validator | Eliminate false failures on و-prefixed forms | 4 hours | Improve clitic stripping in `sentence_validator.py` |
| **7** | Investigate Two-pass (Strategy E) for on-demand | 93% compliance for JIT generation | 4 hours | Two-pass prompt for `generate_validated_sentences()` |
| **8** | BAREC corpus integration | 10-50x cheaper per sentence (at 500+ words) | 2-3 days | Schema, import script, lookup integration, post-processing |

## 8. Quick Wins (can implement today)

### 1. KNOWN_SAMPLE_SIZE = 500

File: `backend/app/services/sentence_generator.py`, line 39.

Change `KNOWN_SAMPLE_SIZE = 50` to `KNOWN_SAMPLE_SIZE = 500`.

Also update the same constant in `backend/app/services/story_service.py` (currently 80) and all scripts that import it.

Token cost increase: ~1,200 extra input tokens per call = ~$0.001. Compliance improvement: +31pp on GPT-5.2 (57% to 88%).

### 2. Quality gate fail-closed

File: `backend/app/services/llm.py`, function `review_sentences_quality()`.

Change the exception handler from returning all-pass results to returning all-fail results. When the quality gate is unavailable, no unreviewed sentences should reach learners.

```python
# Current (fail-open):
except Exception:
    return [SentenceReviewResult(natural=True, translation_correct=True, reason="review unavailable")]

# Recommended (fail-closed):
except Exception:
    return [SentenceReviewResult(natural=False, translation_correct=False, reason="quality review unavailable")]
```

The sentence generator will retry with a new sentence, which is the correct behavior.

### 3. POS-grouped vocabulary prompt

Instead of sending a flat list of "known words: كبير, بيت, سيارة, ...", group them:

```
Known nouns: بيت, سيارة, مدينة, قطة, كلب, ...
Known verbs: ذهب, أكل, شرب, قرأ, ...
Known adjectives: كبير, جميل, صغير, جديد, ...
Known other: في, على, من, هذا, تلك, ...
```

This tested at 5.0/5 quality and 87% compliance on GPT-5.2 (n=5, small sample but directionally strong). The POS grouping helps the LLM construct grammatically correct sentences by making part-of-speech relationships explicit.

---

*Data sources: 213 generated sentences across 3 models and 6 strategies; 34,712 corpus sentences (4,954 Tatoeba + 29,758 BAREC) evaluated against current and simulated vocabularies.*
