# Autoresearch Experiment Specs — 2026-03-23

These experiments can each be run independently via `/autoresearch`. Context from today's session analysis of 3,672 sentence reviews + 18,182 word reviews.

---

## Experiment 1: Comprehensibility Gate Threshold Sweep

**Type**: Grid sweep (parameter optimization)

**Background**: The comprehensibility gate requires ≥60% of scaffold words to be "known" before showing a sentence. Today's analysis found a massive jump: sentences with ≥80% known scaffold get **65.3%** understood vs 42.1% for 60-80%. Raising the gate could dramatically improve comprehension.

**Parameter to sweep**: `COMPREHENSIBILITY_THRESHOLD` in `backend/app/services/sentence_selector.py`

**Current value**: `0.6` (60%)

**Search space**: `[0.60, 0.65, 0.70, 0.75, 0.80]`

**Metric**: Run the 30-day simulation (`python3 scripts/simulate_sessions.py --days 30 --profile calibrated`) and measure:
- Primary: `mean_comprehension_rate` (% of sentences rated "understood")
- Secondary: `session_size` (sentences per session — too-strict gate means empty sessions)
- Secondary: `words_graduated` (learning throughput)

**Key files**:
- `backend/app/services/sentence_selector.py` — search for `COMPREHENSIBILITY_THRESHOLD` or the 0.6 literal in `_is_comprehensible()`
- `backend/app/simulation/` — simulation framework
- `backend/scripts/simulate_sessions.py` — runner

**Tradeoff**: Higher threshold → better comprehension but fewer eligible sentences → smaller sessions → slower word introduction. The sweet spot balances comprehension with throughput.

**What to change**: Single constant. The gate is in `_is_comprehensible()` in sentence_selector.py — it computes `known_count / total_scaffold` and checks `>= 0.6`.

---

## Experiment 2: Sentence Generation Prompt Optimization (Karpathy Loop)

**Type**: Creative (LLM-proposed changes)

**Background**: The sentence generation prompts in `backend/app/services/llm.py` have been manually tuned. With 14,067 generated sentences and comprehension data on 3,085 of them, we can now optimize prompts empirically.

**What to optimize**: The `SENTENCE_SYSTEM_PROMPT` and the user prompt template in `generate_sentence()` (llm.py lines 383-508)

**Evaluation function**: Generate 20 test sentences for a fixed set of 10 target words, validate each, and score:
- Validation pass rate (% passing deterministic validation)
- Quality gate pass rate (% passing Claude Haiku review)
- Mean word count (target: 6-10)
- Scaffold diversity (unique scaffold words / total scaffold)

**Test harness**: Write a script that:
1. Loads 10 target words + known vocabulary from DB
2. Generates 20 sentences with the current prompt
3. Validates each via `validate_sentence()`
4. Runs quality review via `review_sentences_quality()`
5. Returns composite score

**Key files**:
- `backend/app/services/llm.py` — lines 331-508: `ARABIC_STYLE_RULES`, `DIFFICULTY_STYLE_GUIDE`, `SENTENCE_SYSTEM_PROMPT`, `generate_sentence()`
- `backend/app/services/sentence_generator.py` — `generate_validated_sentence()` retry loop
- `backend/app/services/sentence_validator.py` — `validate_sentence()`

**Seed ideas for the LLM to try**:
- Remove/simplify ARABIC_STYLE_RULES (maybe the LLM already knows these?)
- Add few-shot examples of good sentences from the DB
- Change vocabulary format (currently POS-grouped, try flat list or frequency-ordered)
- Adjust temperature (currently 0.5)
- Add negative examples ("do NOT generate sentences like: ...")

**Important constraints**:
- Must use `model_override="gemini"` for generation (production model)
- Must use `model_override="claude_haiku"` for quality review
- The `KNOWN_SAMPLE_SIZE=500` must stay (critical for compliance)
- Changes to prompts only — don't modify validation logic

---

## Experiment 3: Known Words Sampling Strategy

**Type**: Grid sweep

**Background**: Currently, known words shown to the LLM are sampled with inverse-frequency weighting (`weight = max(0.05, 1.0 / (1 + count))`). Words appearing in many sentences get down-weighted. But we don't know if this is optimal.

**Parameters to sweep**:
- `MIN_WEIGHT`: `[0.01, 0.05, 0.10, 0.20]` (floor for sampling weight)
- `KNOWN_SAMPLE_SIZE`: `[300, 500, 700]` (how many words shown to LLM)
- Sampling strategy: `[inverse_freq, uniform, recency_weighted]`

**These constants are in**: `backend/app/services/sentence_generator.py` lines 38-43

**Metric**: Generate 30 sentences per configuration (10 target words × 3 each) and measure validation pass rate. Higher pass rate = the LLM is using the provided vocabulary better.

**Key functions**:
- `sample_known_words_weighted()` — sentence_generator.py line 67
- `get_avoid_words()` — sentence_generator.py line 97

---

## Experiment 4: Scaffold Diversity Threshold

**Type**: Grid sweep

**Background**: `DIVERSITY_SENTENCE_THRESHOLD=10` rejects sentences where a scaffold word appears in 10+ existing sentences. This prevents the same words (e.g., طالب appeared in 31/304 sentences before the fix) from dominating.

**Parameter**: `DIVERSITY_SENTENCE_THRESHOLD` in `sentence_generator.py` line 42

**Search space**: `[5, 8, 10, 15, 20]`

**Metric**: Run generation for 20 target words, measure:
- Validation+diversity pass rate (how often first attempt passes)
- Unique scaffold words across all generated sentences (higher = more diverse)
- Retry count (lower = less wasted LLM calls)

**Tradeoff**: Lower threshold → more diverse but more retries (harder to find valid sentences). Higher threshold → easier generation but repetitive scaffold.

---

## Experiment 5: Response Time as Difficulty Signal

**Type**: Creative (analysis + threshold tuning)

**Background**: Today's analysis found a sharp cliff: <30s response → 85% understood, 30-60s → 47% understood. Sentences where users take >30s are demonstrably too hard. We could use this to:
1. Flag sentences where response_ms > 30000 as "too hard"
2. Retire them or lower their selection score
3. Generate replacements at easier difficulty

**What to build**: A new post-review hook in `sentence_review_service.py` that marks sentences as "difficulty_flagged" when response_ms exceeds a threshold, and a corresponding penalty in `sentence_selector.py` scoring.

**Key files**:
- `backend/app/services/sentence_review_service.py` — `submit_sentence_review()`
- `backend/app/services/sentence_selector.py` — `compute_sentence_diversity_score()`
- `backend/app/models.py` — may need a new column on Sentence

**Parameters to sweep**: Response time threshold `[20000, 25000, 30000, 40000]` ms

**Metric**: Simulate impact on session comprehension by replaying historical reviews — how many sentences would be flagged, and what's the comprehension rate of the remaining pool?

---

## Data Available for All Experiments

**Production DB** (on Hetzner server):
- 14,067 sentences (778 active), 94,098 sentence_words (99% mapped)
- 3,991 sentence-level reviews with comprehension signals
- 21,010 word-level reviews with FSRS ratings
- 3,940 sentence reviews joinable to 18,182 word reviews

**Logs** (on server at `/app/data/logs/`):
- `generation_pipeline_*.jsonl` — NEW, logs every generation attempt with full context
- `llm_calls_*.jsonl` — 26,164 entries (model, timing, task_type)
- `mapping_corrections_*.jsonl` — 627 entries
- `interactions_*.jsonl` — 17 days of user interactions

**Analysis script**: `backend/scripts/analyze_sentence_quality.py` — run with `--db path` to get fresh stats

**Simulation**: `python3 scripts/simulate_sessions.py --days 30 --profile calibrated`

---

## Priority Order

1. **Experiment 1** (comprehensibility gate) — highest expected impact, simplest change, can use simulation
2. **Experiment 5** (response time signal) — strong empirical basis, actionable
3. **Experiment 2** (prompt optimization) — Karpathy loop, needs generation logging to accumulate first
4. **Experiment 3** (sampling strategy) — moderate impact, easy to measure
5. **Experiment 4** (diversity threshold) — fine-tuning, lower priority

---

## How to Access Production Data

```bash
# Copy DB locally for analysis
scp alif:/opt/alif/data/alif.db /tmp/alif-analysis.db

# Or run scripts on server
ssh alif "docker exec -w /app alif-backend-1 python3 scripts/analyze_sentence_quality.py"

# Copy generation logs
scp alif:/opt/alif/data/logs/generation_pipeline_*.jsonl /tmp/
```

**IMPORTANT**: All ssh commands require `dangerouslyDisableSandbox: true`.
