# LLM Arabic Sentence Generation Benchmark

Date: 2026-02-13
Total sentences: 213

## Summary Table: Model x Strategy

| Model | Strategy | Sentences | Avg Quality | Avg Naturalness | Avg Coherence | Avg Diacritics | Vocab Compliance | Avg Tokens (in+out) | Avg Time (ms) |
|-------|----------|-----------|-------------|-----------------|---------------|----------------|------------------|---------------------|---------------|
| claude-sonnet | A (Baseline (50 words)) | 15 | 4.82 | 4.73 | 5.00 | 4.73 | 68% | 2440 | 3106 |
| claude-sonnet | A-full (Baseline (full 196 words)) | 5 | 4.93 | 5.00 | 5.00 | 4.80 | 65% | 4034 | 3885 |
| claude-sonnet | B (Arabic-only first pass) | 15 | 5.00 | 5.00 | 5.00 | 5.00 | 71% | 2506 | 3802 |
| claude-sonnet | C (Relaxed vocabulary) | 15 | 4.98 | 4.93 | 5.00 | 5.00 | 72% | 2461 | 2986 |
| claude-sonnet | D (Set-cover batch) | 8 | 5.00 | 5.00 | 5.00 | 5.00 | 71% | 428 | 1605 |
| gemini-flash | A (Baseline (50 words)) | 15 | 4.84 | 4.67 | 4.87 | 5.00 | 80% | 1855 | 1633 |
| gemini-flash | A-full (Baseline (full 196 words)) | 5 | 5.00 | 5.00 | 5.00 | 5.00 | 78% | 3168 | 1597 |
| gemini-flash | B (Arabic-only first pass) | 15 | 4.87 | 4.60 | 5.00 | 5.00 | 83% | 1928 | 2557 |
| gemini-flash | C (Relaxed vocabulary) | 15 | 4.98 | 5.00 | 5.00 | 4.93 | 77% | 1876 | 1458 |
| gemini-flash | D (Set-cover batch) | 10 | 4.97 | 4.90 | 5.00 | 5.00 | 92% | 285 | 665 |
| gemini-flash | E (Two-pass rewrite) | 15 | 4.78 | 4.53 | 4.93 | 4.87 | 93% | 716 | 2711 |
| gpt-5.2 | A (Baseline (50 words)) | 15 | 4.78 | 4.73 | 4.73 | 4.87 | 57% | 1848 | 1907 |
| gpt-5.2 | A-full (Baseline (full 196 words)) | 5 | 4.80 | 5.00 | 5.00 | 4.40 | 88% | 3074 | 1698 |
| gpt-5.2 | A-pos (Baseline POS-grouped) | 5 | 5.00 | 5.00 | 5.00 | 5.00 | 87% | 3297 | 1637 |
| gpt-5.2 | B (Arabic-only first pass) | 15 | 4.69 | 4.67 | 4.80 | 4.60 | 64% | 1927 | 2484 |
| gpt-5.2 | C (Relaxed vocabulary) | 15 | 4.60 | 4.60 | 4.60 | 4.60 | 62% | 1868 | 1673 |
| gpt-5.2 | D (Set-cover batch) | 10 | 4.50 | 4.20 | 4.50 | 4.80 | 89% | 275 | 1085 |
| gpt-5.2 | E (Two-pass rewrite) | 15 | 4.36 | 4.20 | 4.27 | 4.60 | 93% | 506 | 2286 |

## Top 10 Best Sentences

**1. [gpt-5.2] Strategy A** (score: 15/15, known: 67%)
> اَلْصالُونُ كَبيرٌ وَواسِعٌ.
> The living room is big and roomy.
> *Judge: Simple and correct nominal sentence.*

**2. [gpt-5.2] Strategy A** (score: 15/15, known: 67%)
> اَلْصالُونُ كَبيرٌ وَواسِعٌ.
> The living room is big and roomy.
> *Judge: Identical to sentence 1, perfectly correct.*

**3. [gpt-5.2] Strategy A** (score: 15/15, known: 67%)
> اَلْصالُونُ كَبِيرٌ وَواسِعٌ.
> The living room is big and roomy.
> *Judge: Correct use of kasra for the long vowel 'ya'.*

**4. [gpt-5.2] Strategy A** (score: 15/15, known: 50%)
> بَيْتُنا قَدِيمٌ، وَلَكِنَّهُ جَمِيلٌ.
> Our house is old, but it is beautiful.
> *Judge: Natural contrastive sentence with correct grammar.*

**5. [gpt-5.2] Strategy A** (score: 15/15, known: 50%)
> بَيْتُنا واسِعٌ، وَفيهِ صالونٌ.
> Our house is roomy, and it has a living room.
> *Judge: Clear and grammatically sound.*

**6. [gpt-5.2] Strategy A** (score: 15/15, known: 75%)
> بَيْتُ أُمِّي واسِعٌ وَقَدِيمٌ.
> My mother’s house is roomy and old.
> *Judge: Standard possessive construction (Idafa).*

**7. [gpt-5.2] Strategy A** (score: 15/15, known: 33%)
> سَيّارَتُكِ جَدِيدَةٌ وَواسِعَةٌ.
> Your car is new and roomy.
> *Judge: Correct feminine agreement for 'car'.*

**8. [gpt-5.2] Strategy A** (score: 15/15, known: 33%)
> سَيّارَتُكِ جَديدَةٌ وَواسِعَةٌ.
> Your car is new and roomy.
> *Judge: Consistent with sentence 7.*

**9. [gpt-5.2] Strategy A** (score: 15/15, known: 33%)
> سَيّارَتُكِ جَديدَةٌ وَواسِعَةٌ.
> Your car is new and roomy.
> *Judge: Consistent with sentence 7.*

**10. [gpt-5.2] Strategy A** (score: 15/15, known: 75%)
> أُمِّي جَمِيلَةٌ وَوِشَاحُهَا أَزْرَقُ.
> My mother is pretty, and her scarf is blue.
> *Judge: Correct; note that 'azraqu' is diptote (no tanween).*


## Bottom 10 Worst Sentences

**1. [gpt-5.2] Strategy D** (score: 5/15, known: 80%)
> فِي اَلْمَدِينَةِ بَيْتٌ كَبِيرٌ وَجَمِيلَةٌ جَارَةٌ.
> In the city, there is a big house, and a pretty neighbor.
> Unknown words: وجميلة
> *Judge: Word salad; the second half of the sentence is ungrammatical and lacks clear meaning.*

**2. [gpt-5.2] Strategy E** (score: 5/15, known: 100%)
> أُمّي اَلْمَدِينَةَ فِي اَلْبَحْرِ.
> My mother [the] city in the sea.
> *Judge: Nonsensical word salad; lacks a verb or logical link.*

**3. [gpt-5.2] Strategy E** (score: 5/15, known: 100%)
> أَنْتِ تَماماً اَلْمَدِينَةَ فِي اَلْبَحْرِ.
> You absolutely [love] the city in the sea.
> *Judge: Nonsensical; words do not form a logical thought.*

**4. [gpt-5.2] Strategy E** (score: 6/15, known: 100%)
> أَنْتِ جَميلةُ اَلْمَدِينَةِ عِنْدَ بَحْرٍ.
> You are the city's beauty by a sea.
> *Judge: Grammatically awkward and semantically unclear.*

**5. [gpt-5.2] Strategy C** (score: 8/15, known: 75%)
> وِشاحُ أُمِّي أَزْرَقُ وَجَمِيلَةٌ.
> My mother’s scarf is blue and pretty.
> Unknown words: وجميلة
> *Judge: Grammatically incorrect; 'jamila' (feminine) refers to the scarf (masculine) or is misplaced.*

**6. [gpt-5.2] Strategy C** (score: 8/15, known: 75%)
> وِشاحُ أُمِّي أَزْرَقُ وَجَمِيلَةٌ.
> My mother’s scarf is blue and pretty.
> Unknown words: وجميلة
> *Judge: Duplicate of sentence 4; gender mismatch in adjectives.*

**7. [gemini-flash] Strategy E** (score: 9/15, known: 100%)
> هَذَا الْبَيْتُ جَمِيلٌ وَ كَبيرة جِدًّا.
> This house is beautiful and very big.
> *Judge: Gender mismatch: 'kabira' (feminine) refers to 'bayt' (masculine).*

**8. [gpt-5.2] Strategy A** (score: 10/15, known: 50%)
> اَلْمَدِينَةُ جَمِيلَةٌ، وَاَلْبَحْرُ فِيهَا.
> The city is beautiful, and the sea is in it.
> Unknown words: والبحر, فيها
> *Judge: Same as 11, with extra vowel on the definite article.*

**9. [gpt-5.2] Strategy A** (score: 11/15, known: 50%)
> اَلْمَدِينَةُ جَمِيلَةٌ، وَالبَحْرُ فِيهَا.
> The city is beautiful, and the sea is in it.
> Unknown words: والبحر, فيها
> *Judge: Grammatically correct but feels incomplete (The sea is in it... and?).*

**10. [gpt-5.2] Strategy B** (score: 11/15, known: 50%)
> اَلْمَدِينَةُ جَمِيلَةٌ، وَاَلْبَحْرُ فِيهَا.
> The city is beautiful, and the sea is in it.
> Unknown words: والبحر, فيها
> *Judge: Slightly redundant; 'the sea is in it' is grammatically correct but less common than 'it has a sea'.*


## Per-Strategy Analysis

### Strategy A: Baseline (50 words)
- **Sentences**: 45
- **Avg quality**: 4.81/5 (nat: 4.71, coh: 4.87, dia: 4.87)
- **Vocab compliance**: 68%
- **Avg tokens**: 2048
- **Avg latency**: 2216ms
- **Most common unknown words**: وواسعة(9), وجميل(8), تسكن(6), وواسع(4), وفيه(4), سيارتك(3), والبحر(3), فيها(3), ووشاحها(3), وجميلة(3)

### Strategy A-full: Baseline (full 196 words)
- **Sentences**: 15
- **Avg quality**: 4.91/5 (nat: 5.00, coh: 5.00, dia: 4.73)
- **Vocab compliance**: 77%
- **Avg tokens**: 3425
- **Avg latency**: 2393ms
- **Most common unknown words**: وجميل(3), وواسعة(2), لكنها(1), وفيه(1), ومشهورة(1), والحديقة(1), سيارتي(1), وسريعة(1), ولكنها(1)

### Strategy A-pos: Baseline POS-grouped
- **Sentences**: 5
- **Avg quality**: 5.00/5 (nat: 5.00, coh: 5.00, dia: 5.00)
- **Vocab compliance**: 87%
- **Avg tokens**: 3297
- **Avg latency**: 1637ms
- **Most common unknown words**: وواسع(1), وجميلة(1)

### Strategy B: Arabic-only first pass
- **Sentences**: 45
- **Avg quality**: 4.85/5 (nat: 4.76, coh: 4.93, dia: 4.87)
- **Vocab compliance**: 73%
- **Avg tokens**: 2121
- **Avg latency**: 2947ms
- **Most common unknown words**: وجميل(7), وواسعة(6), وواسع(4), تسكن(4), ولكنه(3), سيارتك(3), والبحر(3), وممتازة(3), وجميلة(3), وزوجتي(3)

### Strategy C: Relaxed vocabulary
- **Sentences**: 45
- **Avg quality**: 4.85/5 (nat: 4.84, coh: 4.87, dia: 4.84)
- **Vocab compliance**: 71%
- **Avg tokens**: 2068
- **Avg latency**: 2039ms
- **Most common unknown words**: وجميلة(8), وواسعة(7), وواسع(6), وجميل(6), ولكنه(5), سيارتك(3), وباردة(3), عندها(3), بيتنا(1), وهي(1)

### Strategy D: Set-cover batch
- **Sentences**: 28
- **Avg quality**: 4.81/5 (nat: 4.68, coh: 4.82, dia: 4.93)
- **Vocab compliance**: 85%
- **Avg tokens**: 322
- **Avg latency**: 1084ms
- **Most common unknown words**: وفيها(3), وواسع(2), قديمة(2), وجميلة(2), وجميل(2), والبحر(1), وواسعة(1), وفيه(1), سيارتان(1), يسكن(1)

### Strategy E: Two-pass rewrite
- **Sentences**: 30
- **Avg quality**: 4.57/5 (nat: 4.37, coh: 4.60, dia: 4.73)
- **Vocab compliance**: 93%
- **Avg tokens**: 611
- **Avg latency**: 2498ms
- **Most common unknown words**: وجميلة(4), وواسع(2)

## Model Comparison

| Model | Avg Quality | Avg Naturalness | Avg Coherence | Avg Diacritics | Avg Vocab Compliance | Avg Latency |
|-------|-------------|-----------------|---------------|----------------|----------------------|-------------|
| claude-sonnet | 4.94 | 4.91 | 5.00 | 4.91 | 70% | 3115ms |
| gemini-flash | 4.89 | 4.75 | 4.96 | 4.96 | 84% | 1867ms |
| gpt-5.2 | 4.63 | 4.56 | 4.64 | 4.69 | 74% | 1910ms |

## Strategy A: 50-word vs Full Vocabulary

**claude-sonnet**: 50-word quality=14.5/15 vocab=68% tokens=2440 | Full quality=14.8/15 vocab=65% tokens=4034

**gemini-flash**: 50-word quality=14.5/15 vocab=80% tokens=1855 | Full quality=15.0/15 vocab=78% tokens=3168

**gpt-5.2**: 50-word quality=14.3/15 vocab=57% tokens=1848 | Full quality=14.4/15 vocab=88% tokens=3074

## POS-Grouped Vocabulary Test (GPT-5.2)
- Flat list: quality=14.4/15, vocab=88%
- POS-grouped: quality=15.0/15, vocab=87%

## Recommendations

### Best combinations (quality x compliance):
1. **gemini-flash + Strategy D**: quality=4.97/5, vocab=92%, combined=4.55 (n=10)
1. **gemini-flash + Strategy E**: quality=4.78/5, vocab=93%, combined=4.46 (n=15)
1. **gpt-5.2 + Strategy A-pos**: quality=5.00/5, vocab=87%, combined=4.33 (n=5)
1. **gpt-5.2 + Strategy A-full**: quality=4.80/5, vocab=88%, combined=4.24 (n=5)
1. **gpt-5.2 + Strategy E**: quality=4.36/5, vocab=93%, combined=4.07 (n=15)