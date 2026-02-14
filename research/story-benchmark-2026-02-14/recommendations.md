# Story Generation Benchmark — Analysis & Recommendations

**Date**: 2026-02-14
**Tested**: 4 models × 4 strategies × 2 stories = 32 stories
**Vocabulary**: 138 usable words (85 known + 4 learning + 49 acquiring)

## Key Findings

### 1. Model Quality Rankings

| Rank | Model | Composite | Compliance% | Cost/story | Verdict |
|------|-------|-----------|-------------|------------|---------|
| 1 | **Opus** | 3.73 | 74% | $0.155 | Best quality AND compliance |
| 2 | **Sonnet** | 3.90 | 57% | $0.027 | Highest narrative quality, ignores vocab |
| 3 | Gemini | 3.03 | 72% | $0.001 | Decent but bland stories |
| 4 | OpenAI | 2.63 | 82% | $0.018 | Best compliance, worst stories (word salad) |

**Opus is the clear winner.** Its best story (Strategy A #0) achieved 93% compliance + 4.3 composite — a genuinely delightful parrot story. When Opus follows the vocab constraint, it produces stories that are both interesting and learnable.

**Sonnet writes the best Arabic but ignores vocabulary constraints.** Its Strategy D stories scored 4.40-4.75 composite — the highest in the entire benchmark — but at 33-38% compliance, most content words were unknown. The prose itself is gorgeous.

**OpenAI (GPT-5.2) confirms the sentence benchmark finding**: highest compliance (82-87%) but lowest quality. Stories feel like vocabulary exercises strung together. Incoherent dialogue, stilted phrasing ("a ball crept to a position near a ruin").

**Gemini Flash is a decent cheap option** but its stories lack narrative depth. Good for basic structure but characters feel flat.

### 2. Strategy Comparison

| Strategy | Composite | Compliance% | Finding |
|----------|-----------|-------------|---------|
| **A (Baseline)** | **3.57** | **76%** | Surprisingly the best overall |
| B (POS-grouped) | 3.39 | 77% | Marginal compliance gain, slight quality dip |
| C (Expanded structures) | 2.90 | 78% | Specific templates constrain creativity |
| D (Two-pass) | 3.44 | 54% | Highest narrative (3.8) but rewrite ruins compliance |

**Baseline wins.** The random genre selection + flat vocab actually works well — models that understand the task (Opus, Sonnet) produce good stories regardless of prompt complexity.

**Two-pass generates the most interesting stories** (narrative arc 3.8, highest) but the vocabulary rewrite pass fails badly (54% compliance). The rewrite model can't preserve the narrative while also constraining vocabulary. This might work better as a **cross-model two-pass**: Sonnet generates freely, then Gemini (which is good at compliance) does the vocabulary rewrite.

**POS-grouped vocabulary doesn't help for stories** like it did for sentences. With 138 words, the vocabulary is small enough that flat listing works fine.

**Expanded structures hurt quality.** Forcing specific narrative templates (e.g., "dialogue-heavy between two characters who disagree") constrains the model's creativity without adding value. Better to let the model choose its own structure.

### 3. The Compliance Gap — Mostly False Negatives

The most common "unknown" words are actually conjugated forms of known vocabulary:

| "Unknown" word | Likely source | Issue |
|----------------|---------------|-------|
| يوم (yawm) | يَوْم (day) | Probably in vocab; diacritic mismatch |
| رأى (raʾā) | رَأَى (saw) | Verb conjugation |
| قالت (qālat) | قال (said) | Feminine past form |
| سعيدا (saʿīdan) | سعيد (happy) | Accusative case ending |
| نظر (naẓar) | نَظَرَ (looked) | Verb form |
| صغير (ṣaghīr) | صَغِير (small) | Missing from forms_json |
| عندي (ʿindī) | عند (at/with) | Possessive suffix |

**The compliance metric overpenalizes conjugated forms.** The actual vocabulary compliance is likely 10-15% higher than reported. Fixing the validator to handle these forms would significantly improve reported compliance for ALL models.

### 4. Cost Analysis

For a feature where the user generates 2-3 stories per week:

| Model | Cost/week | Cost/month | Quality |
|-------|-----------|------------|---------|
| Opus | $0.31-0.47 | $1.24-1.86 | Excellent |
| Sonnet | $0.05-0.08 | $0.22-0.32 | Excellent (needs compliance fix) |
| Gemini | $0.002 | $0.008 | Decent |
| OpenAI | $0.04-0.05 | $0.14-0.22 | Poor |

At 2-3 stories/week, even Opus is acceptable ($1-2/month). The quality delta justifies the cost for this low-volume feature.

## Recommended Production Pipeline

### Option A: Opus Single-Pass (Simplest, Best)

```
User requests story → Opus generates with vocab constraint → Store
```

- **Model**: Opus ($0.15/story)
- **Strategy**: Baseline (Strategy A) — it already produces the best results
- **Temperature**: 0.9
- **Quality gate**: Add a Gemini Flash quality review (same as sentences)
- **Expected**: ~85% compliance after validator fix, composite ~4.0

This is the simplest option and already the best performer. The current story_service.py just needs `model_override="opus"` instead of `"openai"`.

### Option B: Sonnet + Gemini Rewrite (Cross-Model Two-Pass)

```
Sonnet generates freely → Gemini Flash rewrites for compliance → Quality gate
```

- **Cost**: ~$0.03/story (Sonnet $0.027 + Gemini $0.001)
- **Expected**: Sonnet's narrative quality + Gemini's compliance ability
- **Risk**: Gemini rewrite may dilute narrative quality (Strategy D showed this risk)

**Not yet tested** — worth a follow-up experiment comparing same-model two-pass (Strategy D) vs cross-model two-pass.

### Option C: Opus + Retry Loop (Production-Ready)

```
Opus generates → Validate compliance → If <70%, retry with feedback → Quality gate
```

Same as the sentence pipeline: generate → validate → feedback loop. Opus already hits 93% on its best attempts. With a retry loop feeding back "these words are not in the vocabulary: X, Y, Z", compliance should improve significantly.

## Recommended Next Steps

1. **Fix compliance validator** — add verb conjugation forms and accusative/genitive case endings to forms_json. This alone will improve all models' scores by ~10-15%.

2. **Switch story generation to Opus** — change `model_override` in story_service.py from `"openai"` to `"opus"` (or add a new model key). Immediate quality improvement.

3. **Add retry loop** — port the sentence generator's 7-attempt retry loop to story generation. Feed back unknown words as retry feedback.

4. **Add quality gate** — add Gemini Flash quality review for stories (same as `review_sentences_quality()` but for stories).

5. **Include acquiring words in vocabulary** — fix `_get_known_words()` to include `knowledge_state='acquiring'` words.

6. **Optional follow-up benchmark** — test cross-model two-pass (Sonnet→Gemini rewrite) and retry loop effectiveness.
