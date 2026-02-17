# Lemma Mapping Audit — 2026-02-17

## Summary

Audited all 1,895 sentence_words across 300 active sentences. Found systemic issues in three categories: wrong lemma assignments (24+ sentence_words), wrong/misleading English glosses on correct lemmas (249+ sentence_words), and structural lemma quality issues (100+ lemmas).

## Root Cause: False al-Prefix Matching

The most impactful bug is in `lookup_lemma()` (sentence_validator.py:391). When a surface form doesn't match directly, it tries adding ال prefix. For short words, this produces false matches:

```
أَنْ → strip diacritics → أن → normalize_alef → ان
lookup tries: "ان" not found → tries "الان" → MATCHES الآن (now)!
```

This affects أن (that/to), أنّ (that), إنّ (indeed), and لأنّه (because, via clitic strip → ان). These are among the most common Arabic words and have no lemma entries, so they fall through to the false al-prefix match.

**Scale**: 17 sentence_words currently mapped to الآن/آن that should be أن/إن/أنّ/إنّ.

## Category 1: Wrong Lemma Assignments

| Surface form | Mapped to | Should be | Count | Cause |
|---|---|---|---|---|
| أَنْ | الآن (now) | أن (that/to) | 5 | Missing lemma + false al-prefix |
| أَنَّ | الآن (now) / آن (time) | أنّ (that) | 8 | Missing lemma + false al-prefix |
| إِنَّ | الآن (now) | إنّ (indeed) | 1 | Missing lemma + false al-prefix |
| لِأَنَّ/لِأَنَّهُ | الآن (now) | لأنّ (because) | 1 | Missing lemma + clitic strip + false al-prefix |
| فَقْدُ | قد (already) | فقد (loss) | 1 | False proclitic strip: ف+قد |
| الوضع | يضع (to put) | وَضْع (situation) | 1 | Missing noun lemma |
| بِيَدِهَا | باد (to perish) | يد (hand) | 1 | False clitic strip |
| وَجَدَتْ | يوجد (there is) | وَجَدَ (to find) | 1 | Wrong verb lemma |
| يَجْلِسُ | نجلس (we sit) | جَلَسَ (to sit) | 2 | Conjugated form stored as lemma |
| يَأْكُلُ | ونأكل (and we eat) | أَكَلَ (to eat) | 2 | Conjugated form stored as lemma |

## Category 2: Wrong/Misleading Glosses

| Lemma ID | Arabic | Current gloss | Should be | Sentence count |
|---|---|---|---|---|
| 95 | فِي | "with" | "in" | 134 |
| 11 | هٰذا | "that" | "this (masc.)" | 54 |
| 154 | مِن | "than" | "from; than" | 22 |
| 152 | عِنْد | "have" | "at, near; to have" | 9 |
| 115 | هَل | "is" | "is? (question particle)" | 25 |
| 1238 | وَرَقَة | "leaf" | "paper, sheet; leaf" | — |
| 116 | هُوَّ | "he" | "he" (also: shadda on lemma form is wrong) | 10 |

## Category 3: Structural Lemma Issues

### 3a. Lemmas stored with ال prefix (72 lemmas)

Many lemmas have ال baked into `lemma_ar_bare`. Some are legitimate (الله, الآن, اليوم — these genuinely include ال). But many are not: الطابق (floor), الحائط (wall), الكنبة (sofa), الجدار (wall), etc.

**Impact**: These get double-ال in the lookup dict (lookup adds "الالطابق") and waste space. For the user, tapping "الطابق" shows "the floor" instead of "floor" — minor but unprofessional.

**High-impact ones** (>10 active sentences): الطابق (65), اليوم (50), الثانوية (46), الآن (35), الحائط (34), الثاني (32), الله (24), الابتدائية (21), التعليم (20), النرويج (18).

### 3b. Possessive forms as separate lemmas (6 lemmas with active sentences)

| Lemma | Gloss | Active sentences | Should be variant of |
|---|---|---|---|
| أسرتي | my family | 19 | أُسْرة (family) |
| ابني | my son | 16 | اِبْن (son) |
| ابنك | your son | 12 | اِبْن (son) |
| عمي | my paternal uncle | 7 | عَمّ (uncle) |
| ملابسي | my clothes | 5 | ملابس (clothes) |
| عندك | you have | 6 | عند (at/have) |

### 3c. Verbs stored as conjugated forms (15 lemmas)

These were imported as present-tense or we-form instead of past 3ms (the Arabic dictionary form):

| Lemma ID | Stored as | Should be | Active sentences |
|---|---|---|---|
| 1765 | يَسْكُنُ (he lives) | سَكَنَ | 49 |
| 1618 | تَسْكُنُ (she lives) | سَكَنَ | 37 |
| 1731 | نَجْلِسُ (we sit) | جَلَسَ | 26 |
| 1609 | يَضَعُ (to put) | وَضَعَ | 22 |
| 1743 | يَقَعُ (is located) | وَقَعَ | 20 |
| 1764 | تَسْكُنِينَ (you live f.) | سَكَنَ | 12 |
| 1558 | يَكْتُبُونَ (they write) | كَتَبَ | 10 |
| 1556 | نَدْرُسُ (we study) | دَرَسَ | 9 |
| 1828 | يَلْبَس (to wear) | لَبِسَ | 9 |
| 1736 | نَأْكُل (we eat) | أَكَلَ | 5 |
| 2073 | يوجد (there is) | وَجَدَ/وُجِدَ | 1 |

**Note**: Some apparent false positives are actually correct — نَوَّمَ (Form II), تَكَرَّرَ (Form V), نَظَرَ (looking) are all valid past 3ms forms. Also نَمِر (tiger) is a noun, not a verb.

## Pipeline Gaps

1. **No disambiguation**: `lookup_lemma()` returns first match from clitic stripping candidates — no ranking, no context
2. **No CAMeL in sentence pipeline**: `find_best_db_match()` exists in morphology.py and is used for story import, but NOT for sentence_word mapping
3. **No LLM verification**: Sentence generation validates grammar and translation (Gemini quality gate) but never checks word-lemma mappings
4. **Aggressive al-prefix matching**: Adding ال to any 2-char word is dangerous (ان→الان, قد→القد, etc.)
5. **Missing common particles**: أن, إن, أنّ, إنّ are among the 50 most common Arabic words and have no lemma entries
6. **No forms_json for verb conjugations**: Only ~6 form types indexed; past tense conjugations (قرأت, قرأوا, etc.) not in lookup
