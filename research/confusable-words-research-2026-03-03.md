# Visually Similar / Confusable Words in Language Learning: Research Report

**Date**: 2026-03-03
**Focus**: Approaches to handling visual confusion between similar items in SRS/language learning, with emphasis on Arabic

---

## Table of Contents

1. [Existing Approaches in SRS/Language Learning Apps](#1-existing-approaches-in-srslanguage-learning-apps)
2. [Arabic-Specific Visual Similarity](#2-arabic-specific-visual-similarity)
3. [Similarity Metrics for Arabic Words](#3-similarity-metrics-for-arabic-words)
4. [Contrastive Learning / Interference Theory](#4-contrastive-learning--interference-theory)
5. [Practical Implementations and Tools](#5-practical-implementations-and-tools)
6. [Synthesis and Recommendations for Alif](#6-synthesis-and-recommendations-for-alif)

---

## 1. Existing Approaches in SRS/Language Learning Apps

### Anki: Leech Detection (Reactive, Manual)

Anki's approach is entirely **reactive** -- it detects leeches (cards forgotten 8+ times) and suspends them, but has no built-in mechanism to detect *why* a card is a leech or whether it's confused with a specific other card.

**The Anki manual explicitly acknowledges interference**: "Some leeches are caused by 'interference'. For example, an English learner may have recently learned the words 'disappoint' and 'disappear'. As they look similar, the learner may find themselves confusing the two when trying to answer."

**Recommended manual strategies** (from [Control-Alt-Backspace's leech guide](https://controlaltbackspace.org/leech/)):
- **Suspend one of the pair** until the other is firmly learned, then unsuspend
- **Create a third "comparison card"** explicitly asking to distinguish between the confusable items -- "Leeches often occur when you don't have a clear handle on the difference between two words. But if you add another card asking yourself to distinguish between them, suddenly all three cards may become easy."
- **Reframe the question** to include disambiguating context
- **Add a mnemonic** highlighting the distinguishing feature

**Key insight**: The comparison card technique is the closest thing to a "confusion pair" feature in Anki, but it's entirely manual.

Sources:
- [Anki Leeches Manual](https://docs.ankiweb.net/leeches.html)
- [Dealing with Leeches - Control-Alt-Backspace](https://controlaltbackspace.org/leech/)
- [Treating Leeches - Polyglossic](https://www.polyglossic.com/anki-leeches-strategies/)

### AnkiFuzzy: Automated Similar Card Detection

[AnkiFuzzy](https://github.com/cjdduarte/AnkiFuzzy) is an Anki addon that detects similar cards using text-based similarity:
- **Token sort ratio** (default threshold: 70) -- similarity with words sorted
- **Partial ratio** (default threshold: 85) -- substring similarity

Cards are tagged as Identical, Similar, or Needs Confirmation. Very similar cards can be auto-suspended.

**Limitation**: Purely text-based; does not account for visual/glyph similarity (e.g., dot-only differences in Arabic would have low text similarity scores since the Unicode codepoints are different).

Source: [AnkiFuzzy on AnkiWeb](https://ankiweb.net/shared/info/1665515487)

### AnnA (Anki Neuronal Appendix): Semantic Spacing

[AnnA](https://github.com/thiswillbeyourgithub/AnnA_Anki_neuronal_Appendix) is the most sophisticated existing tool for handling interference in SRS:

- **Vectorizes all cards** using either subword TF-IDF (using LLM tokenization) or sentence-transformer embeddings
- **Builds a distance matrix** of semantic similarity between all cards
- **Modifies review scheduling** to ensure semantically similar cards are never reviewed on the same day
- **Tracks recent reviews** to avoid scheduling a card too close to a recently-reviewed similar card

**Algorithm**: Progressively adds cards to the daily queue, comparing each candidate against previously queued items and cards reviewed in the past X days via cosine similarity in the embedding space.

**Key design decision**: AnnA separates similar items in time rather than presenting them together. This aligns with the interference theory approach of temporal separation.

Source: [AnnA GitHub](https://github.com/thiswillbeyourgithub/AnnA_Anki_neuronal_Appendix)

### WaniKani: Built-in Visually Similar Kanji

WaniKani has the most mature "confusion pair" feature in production:

- **Built-in "Visually Similar Kanji" section** on each kanji page
- **Curated similarity data** combining multiple sources
- **Shows similar kanji during reviews** so learners can explicitly compare

**Niai userscript** enhances this with a multi-source scoring system:
1. Old Similar Kanji Script Database (score 0.4)
2. Yeh and Li Dataset -- radical-based analysis
3. **Stroke Distance Database** -- measures stroke transformations needed between kanji
4. Keisei Database -- phonetic compound analysis (score 0.5)
5. Manual curated additions (score 0.8)

**Niai's pixel-based approach**: Calculates similarity scores based on shared pixels between rendered kanji characters. More shared pixels = higher score; unshared pixels and "grayness" differences reduce the score.

**This is directly analogous to what we need for Arabic**: a multi-signal similarity system combining structural (radical/root), visual (rendered glyph), and curated data.

Sources:
- [WaniKani Niai Userscript](https://community.wanikani.com/t/userscript-niai-%E4%BC%BC%E5%90%88%E3%81%84-visually-similar-kanji/23325)
- [WaniKani Similar Kanji Feature Request](https://community.wanikani.com/t/feature-request-the-visually-similar-kanji-feature/46001)
- [WaniKani Visually Similar Vocabulary Discussion](https://community.wanikani.com/t/visually-similar-vocabulary/63567)

### Skritter: Mnemonic-Based Differentiation

Skritter addresses character confusion through **pedagogical content** rather than algorithmic scheduling:
- Blog posts explicitly teaching "little BIG differences" between similar characters
- Curated decks of confusable character groups (e.g., 入/八/人)
- Focus on **mnemonics** to help distinguish similar characters

Source: [Skritter Blog - Little Big Differences](https://blog.skritter.com/2023/12/little-big-differences-with-chinese-characters/)

### LECTOR: LLM-Enhanced Semantic Interference Modeling (2025)

The most recent and theoretically sophisticated approach. [LECTOR](https://arxiv.org/abs/2508.03275) (2025) is an algorithm that:

- **Uses LLMs to compute semantic similarity matrices** between all vocabulary items
- **Modifies the forgetting curve** based on interference from semantically similar recently-reviewed items
- **Tracks per-learner "semantic sensitivity"** -- how much a specific learner is affected by interference
- **Outperforms FSRS, SM-2, and other baselines** (90.2% vs 88.4% for best baseline)

The core insight: the effective half-life of a memory is modulated by (a) mastery level, (b) semantic interference from similar items, and (c) personalized sensitivity. Items with high mutual interference need greater temporal separation.

**Limitation**: Designed for semantic similarity (meaning-based confusion), not visual/orthographic similarity. But the mathematical framework could be adapted for visual interference.

Source: [LECTOR paper on arXiv](https://arxiv.org/abs/2508.03275)

---

## 2. Arabic-Specific Visual Similarity

### The Dot System: Core of Arabic Visual Confusion

Arabic has a unique orthographic feature: **approximately 80% of all letters share their base form with at least one other letter**, differentiated only by dots (i'jam). The 28 letters of the Arabic alphabet reduce to approximately **18 skeletal forms (rasm)**.

### Complete Rasm Group Mapping

The Arabic letters cluster into the following skeletal groups (letters sharing the same undotted form):

| Rasm Group | Letters | Distinguishing Feature |
|---|---|---|
| **ba group** | ب (ba), ت (ta), ث (tha) | 1 dot below / 2 dots above / 3 dots above |
| **ba group (positional)** | ن (nun), ي (ya) | Same as ba in initial/medial; distinct in final/isolated |
| **jim group** | ج (jim), ح (ha), خ (kha) | 1 dot below / no dots / 1 dot above |
| **dal group** | د (dal), ذ (dhal) | No dot / 1 dot above |
| **ra group** | ر (ra), ز (zay) | No dot / 1 dot above |
| **sin group** | س (sin), ش (shin) | No dots / 3 dots above |
| **sad group** | ص (sad), ض (dad) | No dot / 1 dot above |
| **ta group** | ط (ta), ظ (dha) | No dot / 1 dot above |
| **ayn group** | ع (ayn), غ (ghayn) | No dot / 1 dot above |
| **fa group** | ف (fa), ق (qaf) | 1 dot above / 2 dots above (positional overlap in initial/medial) |
| **Unique forms** | ا (alif), ل (lam), م (mim), ه (ha), و (waw), ك (kaf) | No dot-based confusion partners |

**Critical insight for word-level confusion**: A single-letter dot difference in a word creates a pair of words with identical skeletons. For example:
- بنت (bint, "girl") vs. ثنت (no common meaning) -- different dots on first letter
- كتب (kataba, "he wrote") and similar skeleton words
- حبر (hibr, "ink") vs. خبر (khabar, "news") -- jim-group confusion
- عالم ('aalim, "scholar/world") vs. غالم -- ayn/ghayn confusion

### Research on Arabic Letter Confusion in L2 Learners

**Boudelaa & Marslen-Wilson (2020)** created the landmark **Arabic Letter Similarity Matrix**:
- Based on a **40-million-word corpus**
- Measured confusability in **three domains**: visual (human ratings), auditory (phonetic feature analysis), motoric (stroke feature analysis)
- Found that visual similarity is the **primary driver** of confusion in Arabic reading

**Key finding**: Both L2 learners and experienced Arabic readers can process diacritical dots quickly, but under time pressure or with degraded input (small text, poor rendering), dot discrimination breaks down -- especially for the ba-group (ب/ت/ث/ن/ي) and jim-group (ج/ح/خ).

Sources:
- [Arabic Letter Similarity Matrix - Springer](https://link.springer.com/article/10.3758/s13428-020-01353-z)
- [Visual Similarity in Arabic Letter Identification - Cambridge](https://www.cambridge.org/core/journals/language-and-cognition/article/visual-similarity-effects-in-the-identification-of-arabic-letters-evidence-with-masked-priming/848E6071EBBAA5570699C35CB1EB5DB0)
- [Arabic Spelling Errors and Visual-Orthographic Features - Springer](https://link.springer.com/article/10.1007/s11145-025-10731-y)

### The "Dotless Arabic" Research Line

Al-Shaibani & Ahmad (2025) published **"Dotless Arabic Text for Natural Language Processing"** in Computational Linguistics (MIT Press), demonstrating that:
- Removing dots reduces vocabulary by **up to 50%**
- NLP tasks (classification, NER, translation) achieve **comparable performance** on dotless text
- This implies that **context resolves most dot-based ambiguity** for machines -- but the challenge for human L2 learners is that they lack this contextual fluency

The paper's positional mapping rules are important:
- **Nun (ن)**: In initial/medial position, maps to the dotless form of ba (ب) -- same skeleton
- **Qaf (ق)**: In initial/medial position, maps to the dotless form of fa (ف) -- same skeleton

Sources:
- [Dotless Arabic Text for NLP - MIT Press](https://direct.mit.edu/coli/article/51/2/557/124350/Dotless-Arabic-Text-for-Natural-Language)
- [Read Without Dots - arXiv](https://arxiv.org/html/2312.16104v1)

### Transposed Letter Effects in Arabic

Perea et al. (2019) found that Arabic has **unique constraints on letter transposition effects** compared to European languages:
- Transposed-letter priming works when **root letter order is preserved** but fails when root letters are transposed
- Arabic's **position-dependent allography** (letters change shape by position) provides additional disambiguation cues that European scripts lack
- Letter **connectedness** affects processing: connected letter groups in cursive Arabic are processed as chunks

**Implication for Alif**: Root-letter integrity is cognitively special in Arabic. Words that share roots but differ by a single radical may be processed differently from words sharing a skeleton but having unrelated roots.

Sources:
- [Transposed Letter Priming in Arabic - PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC6532566/)
- [Letter Connectedness in Arabic - Sage](https://journals.sagepub.com/doi/abs/10.1177/1747021820926155)

---

## 3. Similarity Metrics for Arabic Words

### 3.1 Standard Levenshtein / Edit Distance

The baseline metric. For Arabic, standard Levenshtein treats all character substitutions equally:
- ب→ت (one dot difference) costs the same as ب→م (completely different shape)
- This is **clearly inadequate** for measuring visual confusability

### 3.2 Weighted Edit Distance with Calligraphic Similarity

Arabic spell-checking research has developed **weighted edit distance** where substitution costs reflect visual similarity:

**Key paper**: Noaman & Sarhan built confusion matrices from 163,452 error pairs in the Qatar Arabic Language Bank (QALP). Findings:
- **65% of Arabic typographical errors** are character permutation errors
- These errors correlate with both **keyboard proximity** and **calligraphic similarity**
- Substitution cost between visually similar letters (e.g., ب↔ت) is **much lower** than between dissimilar letters

**Implementation approach**: Define a substitution cost matrix where:
- Same rasm group, different dots: cost 0.2-0.3 (e.g., ب↔ت, ج↔ح↔خ)
- Same rasm group, position-dependent: cost 0.4-0.5 (e.g., ن↔ب in medial form)
- Different rasm groups: cost 1.0 (standard edit distance)

Sources:
- [Arabic Spelling Correction via Confusion Matrix - ResearchGate](https://www.researchgate.net/publication/303696701_Automatic_Arabic_Spelling_Errors_Detection_and_Correction_Based_on_Confusion_Matrix-_Noisy_Channel_Hybrid_System)
- [Arabic Inter-character Proximity and Similarity - ResearchGate](https://www.researchgate.net/publication/271906657_The_Impact_of_Arabic_Inter-character_Proximity_and_Similarity_on_Spell-Checking)
- [Weighted Edit Distance for Arabic Spellchecking - ResearchGate](https://www.researchgate.net/publication/306063500_The_filtered_combination_of_the_weighted_edit_distance_and_the_Jaro-Winkler_distance_to_improve_spellchecking_Arabic_texts)

### 3.3 Skeletal Form (Rasm) Comparison

**The most promising Arabic-specific metric.** Convert both words to their dotless rasm form using a standard mapping, then compare:

```python
from rasmipy import rasmify

word_a = "حبر"  # hibr (ink)
word_b = "خبر"  # khabar (news)

rasm_a = rasmify(word_a)  # identical rasm
rasm_b = rasmify(word_b)  # identical rasm

# If rasm_a == rasm_b, these words are visual confusables
```

**Scoring approach**:
- `rasm_similarity = 1.0 - (levenshtein(rasm_a, rasm_b) / max(len(rasm_a), len(rasm_b)))`
- Words with rasm_similarity >= 0.8 are strong visual confusables
- Words with identical rasm (1.0) are **maximal confusables**

### 3.4 Root Similarity

Words sharing 2 of 3 root radicals may be confusable, especially when the differing radical is from the same rasm group:
- Root ك-ت-ب (write) vs. root ك-ن-ب -- differ by ت↔ن (same rasm group)
- This creates systematic confusion families that extend beyond single word pairs

### 3.5 Phonological Similarity

Not directly visual, but phonological confusability compounds visual confusion:
- ص/س (emphatic/non-emphatic) -- different rasm groups but L2 learners confuse the sounds
- ط/ت (emphatic/non-emphatic) -- different rasm AND same rasm-group partner confusion
- ح/ه (pharyngeal/glottal) -- hard for L2 learners to distinguish aurally

### 3.6 ML-Based Approaches

**EffOCR's character embedding approach** (Carlson, Bryan & Dell, 2023) offers a template:
- Render each character (or word) as an image using a standard font
- Encode using a vision model trained with **Supervised Contrastive (SupCon) loss**
- Compare embeddings via **cosine similarity** (threshold 0.82 in their work)
- Use **FAISS** for efficient nearest-neighbor lookup

**For Arabic word visual similarity**: Render Arabic words in a standard font (e.g., Noto Naskh Arabic), extract image embeddings, compute cosine similarity. This would capture not just dot differences but also positional allography, ligature effects, and overall visual gestalt.

**AraVec** word embeddings could complement visual similarity with semantic similarity -- words that are both visually AND semantically similar would be the highest confusion risk.

Sources:
- [EffOCR paper - arXiv](https://arxiv.org/abs/2304.02737)
- [AraVec - GitHub](https://github.com/bakrianoo/aravec)

---

## 4. Contrastive Learning / Interference Theory

### The Core Question: Together or Apart?

The research presents a nuanced picture, not a simple answer.

### Interference Theory: Space Them Apart

**Proactive interference** (prior learning disrupts new) and **retroactive interference** (new learning disrupts prior) are the primary mechanisms by which similar items cause leeches.

**Nakata & Suzuki (2019)** studied 133 Japanese university students learning English vocabulary:
- Semantically related items caused **more interference errors** than unrelated items
- Spacing (vs. massing) was **1.6x more effective** overall
- Spacing particularly helped unrelated items, but also reduced interference for related items
- **Conclusion**: "Presenting new words in semantically related sets may hinder receptive vocabulary acquisition due to interference"

**Kroll & Stewart (1994)** found that L1-to-L2 translation was **slower when semantically similar words were blocked together** than when randomly mixed.

Source: [Nakata & Suzuki 2019 - Cambridge](https://www.cambridge.org/core/journals/studies-in-second-language-acquisition/article/effects-of-massing-and-spacing-on-the-learning-of-semantically-related-and-unrelated-words/F58BA8D70385603B9C42E408BFCB8A10)

### Interleaving: Show Them Together (for Discrimination)

**Kornell & Bjork (2008)** -- the foundational interleaving study:
- Participants learned painting styles of 12 artists
- **Interleaving** (mixing paintings from different artists) led to better style identification than blocking
- Participants **believed blocking was better** even though interleaving produced superior results
- Mechanism: interleaving highlights **between-category differences**

Source: [Kornell & Bjork 2008 - Sage](https://journals.sagepub.com/doi/abs/10.1111/j.1467-9280.2008.02127.x)

### The Carvalho & Goldstone Resolution: It Depends on Similarity

**Carvalho & Goldstone (2014)** provided the key nuance:

> **Interleaving is better for HIGH-similarity categories** (increases between-category discrimination).
> **Blocking is better for LOW-similarity categories** (increases within-category feature abstraction).

Applied to Arabic confusable words:
- For words sharing the same rasm (HIGH visual similarity): **interleaving/contrastive presentation helps** -- it forces the learner to attend to the distinguishing dots
- For words with different skeletons but same meaning field: **spacing apart helps** -- avoids unnecessary interference

Source: [Carvalho & Goldstone 2014 - Frontiers](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2014.00936/full)

### The Discriminative Contrast Hypothesis

Birnbaum et al. (2013) and others argue that interleaving works via **discriminative contrast**: juxtaposing items from different categories highlights the features that distinguish them.

For Arabic: showing حبر (ink) and خبر (news) in close succession would force attention to the dot on خ vs. the absence of a dot on ح. This is exactly the kind of contrastive learning that should help with dot-based confusion.

Source: [Why Interleaving Enhances Inductive Learning - Springer](https://link.springer.com/article/10.3758/s13421-012-0272-7)

### Desirable Difficulties Framework (Bjork)

The broader framework suggests that difficulties that **slow initial learning but enhance long-term retention** are beneficial:
- **Interleaving** is a desirable difficulty for similar items
- **Retrieval practice** (testing) is more effective than restudying
- **Varied contexts** help (seeing confusable words in different sentences)

But there's a critical caveat for **low-achieving / novice learners**:

**Hwang (2025)** found that for low-achieving adolescents, interleaving was an **"undesirable difficulty"** -- they lacked the foundational knowledge to benefit from cross-category comparison. This was supported by **Libersky et al. (2025)** who found mixed results for L2 vocabulary interleaving.

**Implication for Alif**: Contrastive presentation of confusable words should be reserved for learners who have **basic familiarity with both words** (e.g., both in "acquiring" or "known" state). Presenting confusable pairs to a novice who hasn't learned either word would likely cause harmful interference.

Sources:
- [Desirable Difficulties in Vocabulary Learning - PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC4888598/)
- [Undesirable Difficulty of Interleaved Practice - Wiley](https://onlinelibrary.wiley.com/doi/10.1111/lang.12659)
- [Effects of Interleaving and Rest on L2 Vocabulary - Sage](https://journals.sagepub.com/doi/10.1177/02676583251338768)

### Synthesis: A Two-Phase Strategy

Based on the research, the optimal strategy for confusable Arabic words is:

1. **Phase 1 (Learning)**: Learn one word first. Suspend the confusable partner. Use spacing to solidify the first word.
2. **Phase 2 (Discrimination)**: Once the first word is stable (e.g., FSRS stability > 10 days), introduce the confusable partner with **contrastive sentences** that highlight the difference.
3. **Phase 3 (Maintenance)**: Ensure confusable pairs are **never reviewed in the same session** unless it's an explicit comparison card. Use AnnA-style temporal separation.

---

## 5. Practical Implementations and Tools

### 5.1 Rasm Extraction Libraries

**rasmipy** (Python) -- the most mature option:
```python
pip install rasmipy
from rasmipy import rasmify
rasmify('الفَاتِحَة')  # Returns: 'الڡاٮحه'
```
- Strips diacritics and maps dotted letters to skeletal forms
- Handles positional variants (nun/qaf in initial/medial)
- Also available as REST service and Docker container
- **GitHub**: https://github.com/telota/rasmipy
- **PyPI**: https://pypi.org/project/rasmipy/

**PyQuran** (Python) -- Quranic analysis toolkit with rasm support:
```python
pip install pyquran
```
- "Without Dots" mode that strips dots from text
- Alphabetical system abstraction supporting modern and old rasm
- Word frequency tables and morphology analysis
- **GitHub**: https://github.com/hci-lab/PyQuran

**niqatless** (JavaScript/npm):
- `npm install niqatless`
- Demo: https://niqatless.io
- Example: 'هذا نص' becomes 'هدا ٮص'
- **GitHub**: https://github.com/ODNA/niqatless

**dotless** (JavaScript) by MohsenAlyafei:
- Simple function to remove dots (tanqeet)
- Demo: https://tanqeet.mohsenalyafei.online/
- **GitHub**: https://github.com/MohsenAlyafei/dotless

### 5.2 Arabic NLP Toolkits

**CAMeL Tools** (Python) -- comprehensive Arabic NLP:
```python
pip install camel-tools
```
- Morphological analysis, disambiguation, diacritization
- Could be used to find words sharing morphological features
- **GitHub**: https://github.com/CAMeL-Lab/camel_tools

**Tashaphyne** (Python) -- Arabic light stemmer:
- Useful for root extraction and morphological decomposition
- **GitHub**: https://github.com/linuxscout/tashaphyne

### 5.3 Word Similarity and Spell-Checking

**pyspellchecker** with Arabic support:
```python
from spellchecker import SpellChecker
spell = SpellChecker(language='ar')
```
- Uses Levenshtein distance (edit distance <= 2) for candidate generation
- **Limitation**: No weighted edit distance for dot-based similarity
- **PyPI**: https://pypi.org/project/pyspellchecker/

### 5.4 Word Embeddings

**AraVec** -- Pre-trained Arabic word embeddings:
- Multiple models trained on different corpora (Wikipedia, Twitter, web)
- Useful for semantic similarity (complementing visual similarity)
- **GitHub**: https://github.com/bakrianoo/aravec

### 5.5 Visual Character Similarity (Render-and-Compare)

No off-the-shelf Arabic implementation exists, but the approach from **EffOCR** can be adapted:
1. Render Arabic words using a standard font (e.g., Noto Naskh Arabic)
2. Encode with a pretrained vision model or simple pixel comparison
3. Compute cosine similarity between image embeddings

The Niai kanji similarity script uses a simpler version: raw pixel overlap after rendering characters at a fixed size.

Source: [EffOCR - arXiv](https://arxiv.org/abs/2304.02737)

### 5.6 Confusion Matrix Data

The **Qatar Arabic Language Bank (QALP)** contains 163,452 error pairs that could be mined for empirical confusion frequencies between specific Arabic character pairs.

The **Boudelaa & Marslen-Wilson Arabic Letter Similarity Matrix** (2020) provides human-rated visual confusability scores for all Arabic letter pairs in all three domains (visual, auditory, motoric).

---

## 6. Synthesis and Recommendations for Alif

### What We Should Build

Based on this research, here's a concrete feature design:

#### 6.1 Visual Similarity Scoring Function

Implement a multi-signal similarity score for any pair of Arabic words:

```python
def visual_similarity(word_a: str, word_b: str) -> float:
    """Returns 0.0 (no similarity) to 1.0 (identical skeleton)."""
    rasm_a = rasmify(strip_tashkeel(word_a))
    rasm_b = rasmify(strip_tashkeel(word_b))

    # Exact rasm match = maximal confusability
    if rasm_a == rasm_b:
        return 1.0

    # Weighted edit distance on rasm forms
    # (already accounts for dot differences since dots are stripped)
    rasm_distance = levenshtein(rasm_a, rasm_b)
    max_len = max(len(rasm_a), len(rasm_b))

    return 1.0 - (rasm_distance / max_len)
```

This simple function captures the core insight: words that share a skeleton (after dot removal) are maximally confusable.

#### 6.2 Confusable Pair Detection (Precomputation)

For all words in the learner's vocabulary:
1. Compute rasm form for each word
2. Group words by rasm form -- any group with 2+ words contains confusable pairs
3. For non-identical rasm forms, compute pairwise similarity; flag pairs above threshold (e.g., 0.7)
4. Store results in a `confusable_pairs` table: `(lemma_id_a, lemma_id_b, similarity_score, similarity_type)`

#### 6.3 Scheduling Modifications

**During learning (acquisition phase)**:
- When a word enters acquisition, check if any of its confusable partners are also in acquisition
- If so, **suspend the less-familiar partner** until the first word graduates from acquisition
- This implements the "learn one first" principle from interference research

**During review (FSRS phase)**:
- Never schedule confusable pairs in the same session (AnnA-style separation)
- If a confusable word becomes a leech (repeated lapses), check if its partner was recently reviewed -- this would indicate interference rather than genuine forgetting

**Contrastive review mode** (new):
- Once both words in a confusable pair are in FSRS review with stability > 10 days, occasionally present them in a **contrastive sentence pair**
- Example: "حبر الكاتب جميل" (The writer's ink is beautiful) vs. "خبر اليوم مهم" (Today's news is important)
- The learner must identify which word is which -- this is the "comparison card" technique from the Anki leech research

#### 6.4 Confusable Word Info Display

When showing word info for any word, include a **"Visually Similar Words"** section showing:
- All words sharing the same or similar rasm
- The distinguishing feature highlighted (e.g., "differs from خبر by: ح has no dot, خ has one dot above")
- A mnemonic hint if available

#### 6.5 Leech Analysis Enhancement

When a word becomes a leech (repeated failures):
1. Check its confusable pairs
2. Check if confusable partners were recently reviewed
3. If correlation found, flag as "interference leech" rather than "knowledge gap leech"
4. Apply interference-specific intervention (suspend partner, add contrastive card)

### Priority Ranking

1. **Rasm-based similarity scoring** -- low effort, high value, enables everything else
2. **Confusable pair precomputation** -- one-time computation per vocabulary expansion
3. **Session builder: no same-rasm pairs in same session** -- prevents interference
4. **Word info: visually similar section** -- educational value
5. **Contrastive review mode** -- highest pedagogical value but most implementation effort
6. **Leech analysis** -- valuable but requires accumulation of failure data
