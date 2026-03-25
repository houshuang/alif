# Corpus Evaluation Report

**Date**: 2026-02-13
**Objective**: Evaluate Tatoeba and BAREC as sentence sources for vocabulary-matched review.

## 1. Vocabulary Match Results

| Corpus | Vocab Size | Sentences in Range | Passing (>=70%) | Pass Rate | Avg Length |
|--------|-----------|-------------------|----------------|-----------|------------|
| Tatoeba | current (196) | 4954 | 13 | 0.3% | 5.2 |
| Tatoeba | medium (~400) | 4954 | 54 | 1.1% | 5.6 |
| Tatoeba | large (~1000) | 4954 | 106 | 2.1% | 5.7 |
| BAREC | current (196) | 29758 | 22 | 0.1% | 5.9 |
| BAREC | medium (~400) | 29758 | 104 | 0.3% | 6.6 |
| BAREC | large (~1000) | 29758 | 267 | 0.9% | 7.1 |

## 2. Sample Passing Sentences

### Tatoeba — Current Vocab (196 words)
  - هل أنتَ سعيد مع ذلك؟ — 100% comprehensible
    EN: Are you happy with that?
  - هل ذاك كلب أم قطة؟ — 75% comprehensible [unknown: ذاك]
    EN: Is that a cat or a dog?
  - يا لك من ولد جميل! — 75% comprehensible [unknown: لك]
    EN: You're such a cute boy.
  - يا لها من حديقة جميلة. — 75% comprehensible [unknown: لها]
    EN: What a beautiful garden!
  - يا له من كلب كبير! — 75% comprehensible [unknown: له]
    EN: What a big dog!
  - المرأة بلا رجل لا شيء. — 75% comprehensible [unknown: المرأة]
    EN: A woman without a man is nothing.
  - الولد الذي يغسل السيارة هو أخي. — 75% comprehensible [unknown: يغسل]
    EN: The boy washing the car is my brother.
  - أنا كبير بما فيه الكفاية. — 75% comprehensible [unknown: الكفاية]
    EN: I'm old enough.
  - فتحتُ الباب وخرجت من السيارة. — 75% comprehensible [unknown: وخرجت]
    EN: I opened the door and got out of the car.
  - هل القطة فوق الكرسي أم تحته؟ — 75% comprehensible [unknown: الكرسي]
    EN: Is the cat on the chair or under the chair?

### BAREC — Current Vocab (196 words)
  - في مدينة النخيل كل شيء جميل: — 75% comprehensible [unknown: النخيل]
  - مدرسةٌ جديدةٌ في بلدٍ جديد — 75% comprehensible [unknown: مدرسة]
  - عاش الأب والبنت والولد وزوجة الأب في بيت واحد. — 75% comprehensible [unknown: عاش, واحد]
  - فهذا بيت ليس فيه مطلوبي. — 75% comprehensible [unknown: مطلوبي]
  - أَنْتُما، هُنَّ، هُما، أنْتِ، الَّتي. — 100% comprehensible
  - الْأَبُ: بارَكَ اللَّهُ فيكَ يا بُنَيَّ وَفي مَدْرَسَتِكَ. — 71% comprehensible [unknown: بارك, مدرستك]
  - لقد صُدمتُ فيك يا رجل! — 75% comprehensible [unknown: صدمت]
  - ليس تماماً . فقط شيء ما غير غالي الثمن . — 75% comprehensible [unknown: الثمن]
  - في مدينة النخيل كل شيء جميل: — 75% comprehensible [unknown: النخيل]
  - هلْ هُناكَ إلهٌ غيرُ اللَّهِ؟ — 100% comprehensible

## 3. BAREC Readability Levels

Distribution of passing sentences by readability level (current vocab):

| Level | Passing | Total in Range |
|-------|---------|---------------|
| 1 | 0 | 6 |
| 2 | 0 | 3 |
| 3 | 0 | 21 |
| 4 | 5 | 164 |
| 5 | 0 | 724 |
| 6 | 1 | 579 |
| 7 | 1 | 2549 |
| 8 | 0 | 3603 |
| 9 | 8 | 1318 |
| 10 | 4 | 7222 |
| 11 | 1 | 1457 |
| 12 | 2 | 6208 |
| 13 | 0 | 1575 |
| 14 | 0 | 2583 |
| 15 | 0 | 884 |
| 16 | 0 | 402 |
| 17 | 0 | 273 |
| 18 | 0 | 84 |
| 19 | 0 | 103 |

## 4. LLM Quality Review

### Tatoeba Quality (20 random passing sentences)

**Average naturalness**: 4.3/5
**Distribution**: 1/5: 1, 3/5: 1, 4/5: 3, 5/5: 8

- [5/5] الولد الذي يغسل السيارة هو أخي. — The boy washing the car is my brother. (accurate)
  Note: Perfectly natural Modern Standard Arabic (MSA).
- [5/5] يا له من كلب كبير! — What a big dog! (accurate)
  Note: Standard exclamation structure.
- [4/5] أنا كبير بما فيه الكفاية. — I'm old enough. (accurate)
  Note: Accurate, though 'كبير في السن' is more specific for age, 'كبير' alone is acceptable.
- [3/5] هل أنتَ سعيد مع ذلك؟ — Are you happy with that? (accurate)
  Note: Slightly literal translation from English; 'بذلك' or 'بهذا' would be more natural than 'مع ذلك'.
- [5/5] يا لك من ولد جميل! — You're such a cute boy. (accurate)
  Note: Natural idiomatic expression.
- [4/5] المرأة بلا رجل لا شيء. — A woman without a man is nothing. (accurate)
  Note: Grammatically correct and clear.
- [5/5] فتحتُ الباب وخرجت من السيارة. — I opened the door and got out of the car. (accurate)
  Note: Perfectly natural.
- [5/5] يا لها من حديقة جميلة. — What a beautiful garden! (accurate)
  Note: Standard exclamation structure.
- [5/5] هل القطة فوق الكرسي أم تحته؟ — Is the cat on the chair or under the chair? (accurate)
  Note: Natural use of pronouns to avoid repetition.
- [4/5] سمّينا إبننا على إسم جدي. — We named my son after my grandfather. (inaccurate)
  Note: The Arabic says 'our son' (إبننا) while the English says 'my son'.

### BAREC Quality (20 random passing sentences)

**Average naturalness**: 4.8/5
**Distribution**: 3/5: 1, 4/5: 2, 5/5: 17

- [5/5] ولكن حكاياتك جميلة يا أمي.. → وَلَكِنَّ حِكَايَاتِكِ جَمِيلَةٌ يَا أُمِّي..
  EN: But your stories are beautiful, mother..
  CEFR: A2
- [5/5] وكان الرجل قوي البنية وطويل القدّ، → وَكَانَ الرَّجُلُ قَوِيَّ الْبِنْيَةِ وَطَوِيلَ الْقَدِّ،
  EN: The man was of strong build and tall stature,
  CEFR: B2
- [5/5] فقال الغريب: وأنا مثلك يا أخي؛ → فَقَالَ الْغَرِيبُ: وَأَنَا مِثْلُكَ يَا أَخِي؛
  EN: The stranger said: And I am like you, my brother;
  CEFR: A2
- [5/5] يا لحظ هذا البيت الكبير.. → يَا لَحَظِّ هَذَا الْبَيْتِ الْكَبِيرِ..
  EN: Oh, the luck of this big house..
  CEFR: B1
- [5/5] هُوَ، هِيَ، هَذا، أَنْتِ، نَحْنُ. → هُوَ، هِيَ، هَذَا، أَنْتِ، نَحْنُ.
  EN: He, she, this, you (fem.), we.
  CEFR: A1
- [3/5] ليس تماماً . فقط شيء ما غير غالي الثمن . → لَيْسَ تَمَاماً. فَقَطْ شَيْءٌ مَا غَيْرُ غَالِي الثَّمَنِ.
  EN: Not exactly. Just something that is not expensive.
  CEFR: B1
- [5/5] في مدينة النخيل كل شيء جميل: → فِي مَدِينَةِ النَّخِيلِ كُلُّ شَيْءٍ جَمِيلٌ:
  EN: In the city of palms, everything is beautiful:
  CEFR: A2
- [5/5] كم أنا سعيد كم أنا سعيد → كَمْ أَنَا سَعِيدٌ كَمْ أَنَا سَعِيدٌ
  EN: How happy I am, how happy I am
  CEFR: A1
- [5/5] مدرسة جديدة في بلد جديد → مَدْرَسَةٌ جَدِيدَةٌ فِي بَلَدٍ جَدِيدٍ
  EN: A new school in a new country
  CEFR: A1
- [4/5] فهذا بيت ليس فيه مطلوبي. → فَهَذَا بَيْتٌ لَيْسَ فِيهِ مَطْلُوبِي.
  EN: For this is a house that does not contain what I seek.
  CEFR: B2

## 5. Dormant Pool Analysis

How many sentences that FAIL current vocab would pass at larger vocabulary sizes:

| Corpus | Failed at 196 | Would Pass at ~400 | Would Pass at ~1000 |
|--------|--------------|-------------------|---------------------|
| Tatoeba | 4941 | 41 (0.8%) | 93 (1.9%) |
| BAREC | 29736 | 82 (0.3%) | 245 (0.8%) |

This shows how the usable pool grows as the learner's vocabulary expands.

## 6. Assessment: Viability as Sentence Source

### Tatoeba
- **Pool size**: 4954 sentences in 5-14 word range
- **Current match**: 13 sentences at 196-word vocab
- **Quality**: Average naturalness 4.3/5
- **Advantage**: Comes with English translations
- **Limitation**: Volunteer-contributed; quality varies; many short/formulaic sentences

### BAREC
- **Pool size**: 29758 sentences in 5-14 word range
- **Current match**: 22 sentences at 196-word vocab
- **Quality**: Average naturalness 4.8/5
- **Advantage**: 19-level readability annotations; large pool; authentic text
- **Limitation**: No translations (need LLM); no diacritics (need LLM); readability labels may not align with vocabulary

## 7. Key Findings & Recommendations

### Match rates are extremely low
At 196 words, only **0.3%** of Tatoeba and **0.1%** of BAREC sentences pass the 70% comprehensibility gate. Even at a simulated 1000-word vocabulary, only 2.1% and 0.9% pass respectively. This is the fundamental limitation of corpus-based approaches for a small-vocabulary learner.

### Quality comparison
- **BAREC** scores significantly higher on naturalness (4.8 vs 4.3) — these are authentic published Arabic texts vs volunteer translations
- **Tatoeba** has the advantage of pre-existing English translations, saving an LLM call
- Both corpora need diacritization via LLM (Tatoeba has some diacritics; BAREC has almost none)

### Dormant pool growth is sublinear
Tripling the vocabulary from 196 to ~1000 words only increases the pass rate by ~7x for Tatoeba (13 → 106) and ~12x for BAREC (22 → 267). This means even at 1000 words, the usable pool is still small (~370 sentences total across both corpora).

### Practical implications
1. **Corpora alone cannot replace LLM generation** for this app's needs. With 196 words, we get ~35 usable sentences total. The app needs 2+ sentences per word (392+), and they need to be varied.
2. **BAREC is the better long-term investment** — higher quality, larger pool, readability annotations could help with difficulty targeting. But it needs LLM post-processing (diacritization + translation).
3. **Hybrid approach is viable**: use corpus sentences as a quality baseline, supplemented by LLM generation for vocabulary gaps. Corpus sentences could serve as "warm cache" — pre-validated high-quality sentences ready to use.
4. **As vocabulary grows past 500-1000 words**, corpus matching becomes increasingly viable. At that point, BAREC's 30K sentences become a meaningful pool.

### Cost comparison (per usable sentence)
- **Corpus (BAREC)**: ~$0.001 (one-time LLM call for diacritics + translation)
- **Corpus (Tatoeba)**: ~$0.0005 (one-time LLM call for diacritics only)
- **LLM generation**: ~$0.01-0.05 per sentence (generation + validation + quality review)
- Corpus sentences are 10-50x cheaper per sentence when they match vocabulary
