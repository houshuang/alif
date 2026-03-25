# Arabic Sentence Corpora for Language Learning

**Date**: 2026-02-21
**Purpose**: Survey of large-scale Arabic corpora that could supply pre-made sentences for Alif, reducing dependence on LLM generation.

---

## 1. Free/Open Arabic Corpora (100k+ sentences)

### 1.1 OSIAN (Open Source International Arabic News Corpus)

- **Size**: ~3.5 million articles, 37+ million sentences, ~1 billion tokens
- **Source**: International Arabic news websites (crawled from 22 Arabic countries)
- **License**: CC BY-NC 4.0 (non-commercial)
- **Diacritics**: No
- **Translations**: No (monolingual Arabic)
- **Text quality**: Formal MSA news text
- **NLP research**: Yes, integrated into CLARIN infrastructure
- **Download**: Via [Leipzig Corpora Collection](https://wortschatz.uni-leipzig.de/en/download/) (subset: 3.3M sentences for Tunisia alone) and [CLARIN](https://aclanthology.org/W19-4619/)
- **Format**: XML with metadata per article
- **Alif relevance**: HIGH. Enormous MSA sentence pool. News text is formal but grammatically clean. No diacritics is a downside but we can add via auto-tashkeel models. CC BY-NC is fine for personal use.

### 1.2 Abu El-Khair Corpus

- **Size**: 5+ million newspaper articles, 1.5 billion words, ~3 million unique words
- **Source**: Arabic newspapers spanning 14 years (pre-2016)
- **License**: Research use (check terms)
- **Diacritics**: No
- **Translations**: No
- **Text quality**: Formal MSA news
- **NLP research**: Yes, widely cited (arXiv:1611.04033)
- **Download**: [Hugging Face](https://huggingface.co/datasets/abuelkhair-corpus/arabic_billion_words) and [author's site](http://www.abuelkhair.net/)
- **Alif relevance**: MEDIUM. Massive but news-heavy. Sentences tend to be long and complex. Would need heavy filtering for learner-appropriate content.

### 1.3 Arabic Wikipedia

- **Size**: ~4.4 million sentences (2021 dump), ~75M tokens. Raw dump ~800MB compressed
- **Source**: Arabic Wikipedia articles
- **License**: CC BY-SA 3.0 (free)
- **Diacritics**: Occasional (inconsistent)
- **Translations**: No (but parallel via WikiMatrix, see below)
- **Text quality**: Formal MSA, encyclopedic. Quality varies; some articles are machine-translated stubs
- **NLP research**: Extensively used
- **Download**: [Wikimedia dumps](https://dumps.wikimedia.org/arwiki/) + extraction via [WikiExtractor](https://github.com/attardi/wikiextractor) or Gensim. Pre-extracted on [Leipzig Corpora Collection](https://corpora.uni-leipzig.de/en?corpusId=ara_wikipedia_2021)
- **Alif relevance**: MEDIUM. Good vocabulary coverage but encyclopedic style is dry for learners. Best filtered for shorter, simpler sentences.

### 1.4 WikiMatrix (Arabic-English parallel from Wikipedia)

- **Size**: Part of 135M mined parallel sentences across 1620 language pairs. Arabic-English pair likely millions of sentences.
- **Source**: Automatically mined from Arabic + English Wikipedia using LASER embeddings
- **License**: CC BY-SA (Wikipedia license)
- **Diacritics**: No
- **Translations**: YES (English parallel)
- **Text quality**: Mixed quality. Top-scored pairs are good; bottom pairs are noisy. Quality degrades rapidly below margin threshold ~1.04
- **NLP research**: Yes (Facebook/Meta FAIR, EACL 2021)
- **Download**: [GitHub LASER repo](https://github.com/facebookresearch/LASER/blob/main/tasks/WikiMatrix/README.md)
- **Alif relevance**: HIGH for translated pairs. We could filter by margin score (>1.06 for high quality) to get reliable Arabic-English sentence pairs. Would need quality filtering.

### 1.5 UN Parallel Corpus v1.0

- **Size**: ~20 million sentence pairs per language pair. Arabic-English: ~20M aligned sentences
- **Source**: Official UN documents (1990-2014), manually translated
- **License**: Free / public domain (UN documents)
- **Diacritics**: No
- **Translations**: YES (English, plus French, Spanish, Russian, Chinese)
- **Text quality**: Formal MSA, UN/diplomatic register. Very formal, often legalistic
- **NLP research**: Yes, standard MT benchmark
- **Download**: [UN DGACM](https://www.un.org/dgacm/en/content/uncorpus/download) — plain-text bitexts available
- **Alif relevance**: LOW for direct use. UN language is too formal/specialized for a language learner. Could extract simple sentences but the register is wrong. Translation quality is excellent though.

### 1.6 OpenSubtitles (via OPUS)

- **Size**: Arabic is one of 67 languages. Estimated several million Arabic sentences (exact count unavailable from search; total corpus is 3.35 billion sentence fragments across all languages)
- **Source**: Movie and TV show subtitles from opensubtitles.org
- **License**: Free for research (subtitles community-contributed)
- **Diacritics**: No
- **Translations**: YES (parallel with English and many other languages via subtitle alignment)
- **Text quality**: MIXED — some MSA, much dialectal Arabic (Egyptian, Gulf, Levantine). Colloquial, conversational. Short sentences typical of subtitles.
- **NLP research**: Extensively used
- **Download**: [OPUS OpenSubtitles v2024](https://opus.nlpl.eu/OpenSubtitles/corpus/version/OpenSubtitles)
- **Alif relevance**: MEDIUM. Short conversational sentences are great for learners, BUT heavy dialect mix is problematic for MSA-focused app. Would need dialect filtering. Translations available are a plus.

### 1.7 Tanzil / Quranic Arabic Corpus

- **Size**: 6,236 verses (Quran), ~77,000 words. The Quranic Arabic Corpus adds morphological annotation for every word.
- **Source**: Quran text
- **License**: Free (Tanzil: no restrictions for personal/research use)
- **Diacritics**: YES (fully vowelized)
- **Translations**: YES (multiple English translations available)
- **Text quality**: Classical Arabic, not MSA. Highly literary/religious register
- **NLP research**: Yes (morphological treebank, semantic ontology)
- **Download**: [Tanzil](https://tanzil.net/download/) (multiple formats: UTF-8 text, XML), [Quranic Arabic Corpus](https://corpus.quran.com/download/)
- **Alif relevance**: LOW for direct sentence use (Classical Arabic, not MSA). However, the fully diacritized text with morphological analysis could be useful for roots/patterns research. Many MSA learners are motivated by Quranic literacy, so could be a feature later.

### 1.8 Tashkeela Corpus

- **Size**: 75 million fully vocalized words from 97 books
- **Source**: Shamila Library (98.85%) + MSA texts (1.15%, ~868K words)
- **License**: Free / open source
- **Diacritics**: YES (fully vowelized — this is the main feature)
- **Translations**: No
- **Text quality**: Mostly Classical Arabic (Islamic texts from Shamila). Small MSA portion.
- **NLP research**: Yes, standard benchmark for auto-diacritization
- **Download**: [Kaggle](https://www.kaggle.com/datasets/linuxscout/tashkeela), [SourceForge](https://sourceforge.net/projects/tashkeela/)
- **Alif relevance**: MEDIUM-HIGH for diacritics. Even though content is mostly classical, the diacritized text is valuable for training/validating our tashkeel models. The MSA portion (~868K words) could yield diacritized sentences directly.

### 1.9 Tatoeba

- **Size**: ~67,464 Arabic sentences (as of 2026)
- **Source**: Community-contributed example sentences
- **License**: CC BY 2.0 FR
- **Diacritics**: Some (inconsistent, contributor-dependent)
- **Translations**: YES (many translated to English and other languages)
- **Text quality**: Mixed MSA/dialect, generally simple and conversational. Community quality varies.
- **NLP research**: Used by Helsinki-NLP Tatoeba Challenge
- **Download**: [Tatoeba downloads](https://tatoeba.org/en/downloads) (TSV format, bulk download available)
- **Alif relevance**: HIGH. These are exactly the kind of sentences we need: short, example-style, with translations. 67K is a solid base. Already used by Clozemaster. Main concern: quality is inconsistent, some sentences are dialectal, diacritics are spotty.

### 1.10 KSUCCA (King Saud University Corpus of Classical Arabic)

- **Size**: 50 million tokens
- **Source**: Classical Arabic texts (pre-Islamic era to 4th Hijri century)
- **License**: Free (downloadable from SourceForge)
- **Diacritics**: Partial (classical texts often have diacritics)
- **Translations**: No
- **Text quality**: Classical Arabic only
- **NLP research**: Yes, includes POS-tagged annotated version
- **Download**: [SourceForge](https://sourceforge.net/projects/ksucca-corpus/)
- **Alif relevance**: LOW. Classical Arabic is too far from MSA for our learner use case.

### 1.11 Arabic Gigaword (LDC)

- **Size**: Fifth edition has ~4 billion words from 9 Arabic news sources
- **Source**: Arabic newswire (AFP, Al-Ahram, Al-Hayat, etc.)
- **License**: LDC membership required ($$$). NOT free.
- **Diacritics**: No
- **Translations**: No
- **Text quality**: Formal MSA news
- **NLP research**: Standard NLP resource
- **Download**: [LDC catalog](https://catalog.ldc.upenn.edu/LDC2011T11)
- **Alif relevance**: LOW (paywall). Same domain as OSIAN/Abu El-Khair which are free.

### 1.12 Penn Arabic Treebank (PATB)

- **Size**: ~19,738 sentences, ~739K tokens
- **Source**: AFP newswire
- **License**: LDC membership required
- **Diacritics**: YES (vocalized verb forms in recent versions; partial overall)
- **Translations**: English glosses per word
- **Text quality**: Formal MSA news, fully parsed (POS, morphology, syntax trees)
- **NLP research**: Foundational resource for Arabic NLP
- **Download**: [LDC](https://catalog.ldc.upenn.edu/LDC2010T13) (not free)
- **Alif relevance**: LOW for sentence mining (too small, behind paywall). But the morphological annotations are gold-standard quality.

### 1.13 CCMatrix / CCAligned

- **Size**: CCMatrix: 4.5 billion parallel sentences total (661M aligned with English). Arabic-English pair likely tens of millions. CCAligned: 392M URL pairs across 137 languages.
- **Source**: Automatically mined from Common Crawl web data
- **License**: Free for research
- **Diacritics**: No
- **Translations**: YES (parallel English)
- **Text quality**: Very mixed (web-crawled). Needs heavy filtering. Quality varies enormously by margin score.
- **NLP research**: Yes (Facebook FAIR)
- **Download**: [CCMatrix on OPUS](https://opus.nlpl.eu/), [CCAligned](https://www.statmt.org/cc-aligned/)
- **Alif relevance**: MEDIUM. Enormous volume but quality is the concern. Would need aggressive filtering by margin score and sentence length. Could yield millions of usable Arabic-English pairs after filtering.

### 1.14 AMARA Corpus (TED Talk Subtitles)

- **Size**: Arabic-English: ~2.6M Arabic words, ~3.9M English words
- **Source**: Community-translated TED talk subtitles
- **License**: Free for research
- **Diacritics**: No
- **Translations**: YES (English + 19 other languages)
- **Text quality**: Spoken MSA/semi-formal. Educational content. Good register for learners.
- **NLP research**: Yes (IWSLT 2013)
- **Download**: Via OPUS or [research paper](https://aclanthology.org/2013.iwslt-papers.2/)
- **Alif relevance**: HIGH. Educational content with natural conversational MSA. TED talks cover diverse topics at accessible level. Translations available. Sentence quality should be good (human-translated subtitles).

### 1.15 Leipzig Corpora Collection (Arabic)

- **Size**: Multiple Arabic corpora available. Wikipedia 2021: 4.4M sentences. News corpora: up to 3.3M sentences each. Available in 10K, 30K, 100K, 300K, 1M sentence packs.
- **Source**: Wikipedia dumps + news crawls
- **License**: Free for research
- **Diacritics**: No
- **Translations**: No
- **Text quality**: Clean MSA (pre-processed, sentence-split)
- **NLP research**: Yes
- **Download**: [Leipzig Wortschatz](https://wortschatz.uni-leipzig.de/en/download/) — look under language selection. Also [Corpora portal](https://corpora.uni-leipzig.de)
- **Alif relevance**: MEDIUM-HIGH. Pre-processed into clean sentences with co-occurrence statistics. Good starting point for sentence mining. The 1M-sentence packs are convenient.

### 1.16 ANT Corpus (Arabic News Texts)

- **Size**: Large-scale Arabic news corpus
- **Source**: Arabic news websites
- **License**: Free
- **Download**: [antcorpus.github.io](https://antcorpus.github.io/)
- **Alif relevance**: MEDIUM. Another news corpus; useful as supplement.

---

## 2. Graded/Educational Arabic Text Collections

### 2.1 Hindawi Foundation / Arabic E-Book Corpus

- **Size**: 1,745 books, 81.5 million words
- **Source**: Hindawi Foundation publications (2008-2024)
- **Genres**: Fiction, non-fiction, children's literature, plays, poetry
- **License**: Unrestricted licenses only (free)
- **Diacritics**: Varies by book (children's books more likely vowelized)
- **Translations**: No
- **Format**: HTML + plain text, 420 MB total
- **Download**: [Swedish National Data Service](https://researchdata.se/en/catalogue/dataset/2024-145)
- **Alif relevance**: HIGH. Diverse genres including children's literature (simpler language). Fiction is more engaging than news for learners. Free and well-structured with metadata.

### 2.2 Leveled Reading Corpus of Modern Standard Arabic

- **Size**: 7 million tokens total: 1.4M tokens from UAE K-12 textbooks + 5.6M tokens from 129 fiction works
- **Source**: UAE curriculum (grades 1-12) + unabridged Arabic fiction
- **Levels**: Annotated with reading levels from Grade 1 to Post-secondary
- **License**: Academic (check paper for access)
- **Diacritics**: Likely yes for lower grades (UAE textbooks for young children are vowelized)
- **Translations**: No
- **Download**: Contact authors (NYU Abu Dhabi). Paper: [ACL Anthology](https://aclanthology.org/L18-1366/)
- **Alif relevance**: VERY HIGH. This is the gold standard for graded Arabic text. Grade-leveled content from actual textbooks + fiction, with reading level annotations. If we can access it, this enables direct difficulty-based sentence selection. The K-3 textbook content would be fully diacritized.

### 2.3 BAREC (Balanced Arabic Readability Evaluation Corpus)

- **Size**: 69,441 sentences, 1+ million words
- **Source**: 30 resources (textbooks, articles, literature)
- **Levels**: 19 readability levels (kindergarten to postgraduate)
- **License**: Open (fair use/public domain sources)
- **Diacritics**: Unknown
- **NLP research**: Yes (CAMeL Lab, NYU Abu Dhabi, Shared Task 2025)
- **Download**: [barec.camel-lab.com](http://barec.camel-lab.com) and [Hugging Face](https://huggingface.co/datasets/CAMeL-Lab/BAREC-Shared-Task-2025-sent)
- **Alif relevance**: VERY HIGH. Sentence-level readability annotations across 19 levels is exactly what we need for difficulty-graded sentence selection. Available on Hugging Face. This could replace or supplement our comprehensibility gate heuristic with empirically-graded sentences.

### 2.4 SAMER (Simplification of Arabic Masterpieces for Extensive Reading)

- **Size**: 159K words from 15 Arabic fiction novels, with two simplified versions per text
- **Readability lexicon**: 40K lemmas with 5-level readability scale (L1-L5)
- **Source**: Arabic fiction masterpieces
- **License**: Academic
- **Diacritics**: Word-level readability annotations
- **Download**: [SAMER Project](https://sites.google.com/nyu.edu/samer)
- **Alif relevance**: HIGH. The 40K-lemma readability lexicon is extremely valuable for our word difficulty estimation. Could map our lemmas to SAMER levels. The simplified parallel texts model (original + 2 simplified versions) is interesting for adaptive content.

### 2.5 Internet Archive Arabic Collections

- **Size**: 20M+ freely downloadable books/texts; Arabic Collections Online (ACO): 10,042 volumes
- **Source**: Multiple institutions (NYU, Princeton, Cornell, Columbia, AUC, AUB)
- **License**: Public domain / free access
- **Diacritics**: Varies (older texts more likely vowelized)
- **Download**: [Internet Archive Arabic](https://archive.org/details/booksbylanguage_arabic)
- **Alif relevance**: LOW-MEDIUM. Raw book scans would need OCR + processing. ACO is better curated. Useful for specific genre mining (children's books, textbooks) but high processing cost.

### 2.6 Arabic Learner Corpus (ALC)

- **Size**: 282,732 words in 1,585 materials from 942 students
- **Source**: Written essays and spoken recordings by Arabic learners in Saudi Arabia
- **License**: LDC (not free)
- **Diacritics**: No (learner-produced text)
- **Download**: [LDC](https://catalog.ldc.upenn.edu/LDC2015S10)
- **Alif relevance**: LOW. This is text produced BY learners, not FOR learners. Not useful for sentence mining.

### 2.7 Multidialectal Parallel Arabic Corpus (2025)

- **Size**: ~50,010 unique parallel sentences across MSA + 5 dialects (Saudi, Egyptian, Iraqi, Levantine, Moroccan)
- **Source**: LLM-generated, human-validated
- **License**: Open source (Zenodo)
- **Diacritics**: Unknown
- **Translations**: Parallel across dialects (MSA + 5 dialects)
- **Alif relevance**: MEDIUM. The MSA portion could be useful. Dialect parallels interesting for future dialect support.

---

## 3. Diacritics (Tashkeel) Summary

Sources with full diacritics are rare and precious for Alif:

| Corpus | Diacritized? | Size | Register |
|--------|-------------|------|----------|
| Tanzil/Quran | Full | 77K words | Classical |
| Tashkeela | Full | 75M words | 99% Classical, 1% MSA |
| PATB (LDC) | Partial (verbs) | 739K tokens | MSA news |
| UAE Textbooks (Leveled Corpus) | Likely (K-3) | ~500K tokens | Educational MSA |
| Children's books (Hindawi) | Likely | Unknown | Simple MSA |

**Key insight**: There is NO large, freely available, fully-diacritized MSA corpus. The best options are: (a) Tashkeela's small MSA portion (~868K words), (b) auto-diacritization of undiacritized corpora using models trained on Tashkeela, (c) children's/educational texts that are naturally vowelized.

---

## 4. Existing Systems for Corpus-Based Sentence Selection

### 4.1 Tatoeba

- **URL**: [tatoeba.org](https://tatoeba.org)
- **Arabic**: 67,464 sentences
- **Approach**: Community-contributed example sentences with translations
- **Selection**: Manual curation, no automatic grading
- **License**: CC BY 2.0 FR
- **Integration**: Powers Clozemaster. Bulk download available.

### 4.2 Clozemaster

- **URL**: [clozemaster.com](https://www.clozemaster.com)
- **Arabic**: ~50,000 sentences (sourced from Tatoeba)
- **Approach**: Cloze deletion (fill-in-the-blank) from corpus sentences, ordered by word frequency
- **Selection**: Frequency-based ordering using word frequency lists. "Fluency Fast Track" orders sentences from most common to least common missing words.
- **Strengths**: Simple, effective vocabulary acquisition through context
- **Limitations**: Tatoeba quality issues, no difficulty grading beyond frequency, no morphological awareness

### 4.3 LingQ

- **URL**: [lingq.com](https://www.lingq.com/en/learn-arabic-online/)
- **Arabic**: Mini Stories (Standard Arabic, Egyptian, Levantine, Palestinian), plus user-imported content
- **Approach**: Import-any-text model. Users highlight unknown words ("LingQs"), track vocabulary across texts.
- **Selection**: User-driven. Platform provides starter content but the model is "bring your own content."
- **Strengths**: Any text becomes a lesson. Vocabulary tracking across all content.
- **Limitations**: No automatic sentence selection. Limited built-in Arabic content. Dialect mixing.

### 4.4 ML_for_SLA (Research Project)

- **URL**: [GitHub](https://github.com/JonathanLaneMcDonald/ML_for_SLA)
- **Approach**: Neural network trained to identify "comprehensible input" sentences from unstructured text. Selects sentences where context strongly disambiguates unknown words.
- **Language**: Japanese (but approach is language-agnostic)
- **Results**: Extracted ~1.1M "high-quality" example sentences from 20K word lexicon. Top 100 examples per word for words in the 80th-99th frequency percentile.
- **Selection criteria**: Fewer unknowns = better. Richer context = better. Targets i+1 (single new word strongly contextualized by known words).
- **Alif relevance**: VERY HIGH conceptually. This is essentially what our `build_session()` comprehensibility gate does, but with a neural scoring model instead of heuristic. Could adopt similar approach with a corpus.

---

## 5. Academic Research on Sentence Selection for Vocabulary Learning

### 5.1 "Candidate Sentence Selection for Language Learning Exercises" (Pilan, Volodina, Borin, 2016)

- **Paper**: [arXiv:1706.03530](https://arxiv.org/abs/1706.03530), [ACL Anthology](https://aclanthology.org/2016.tal-3.4/)
- **Key findings**:
  - Hybrid system combining heuristics + machine learning for selecting pedagogically suitable sentences from L1 corpora
  - Two fundamental selection criteria: **linguistic complexity** and **context dependence** (whether the sentence is self-contained)
  - Teacher evaluation: top-ranked model selections performed at the **same level as dictionary examples**
  - Best model significantly outperformed both random corpus selections AND dictionary examples
  - 73% of selected sentences deemed understandable by evaluators
  - Logistic regression on linguistic features (sentence length, vocabulary level, syntactic complexity)
- **Alif relevance**: Direct template for building a sentence selector. Their criteria map well to our needs.

### 5.2 "NLP-based Approaches to Sentence Readability for L2 Learning" (Pilán et al.)

- **Key findings**: Semi-automatic sentence selection from native corpora for L2 learners. Lexical and syntactic factors most influence selection quality. CEFR-level annotation enables grading.
- **Alif relevance**: Could use similar readability features (sentence length, avg word frequency, syntactic depth) to filter corpus sentences.

### 5.3 Comprehensible Input Research (Krashen / Laufer)

- **Key threshold**: Learners need to understand **95-98%** of words in a text for effective acquisition (Laufer 1989). Alif currently uses 60% known scaffold, but the research suggests higher is better.
- **i+1 principle**: Optimal learning happens when exactly one new element is embedded in otherwise comprehensible context.
- **Alif relevance**: Our comprehensibility gate (60%) is well below the research-recommended 95%. This is because we're selecting FOR the unknown word, not for general reading. But for corpus sentence selection, we should target 90-95% known vocabulary.

### 5.4 GenAI vs. Corpus-Based Sentences for L2 Learning

- **Finding**: L2 learners found GenAI-based sentences more suitable than corpus-based sentences in 265 out of 400 pairwise comparisons.
- **Alif relevance**: Validates our LLM generation approach. But corpus sentences are free and instant, while LLM generation costs time/money. A hybrid approach (corpus first, LLM fallback) could be optimal.

### 5.5 BAREC / Fine-grained Arabic Readability (CAMeL Lab, 2025)

- **Paper**: [arXiv:2502.13520](https://arxiv.org/abs/2502.13520)
- **Key contribution**: 69K Arabic sentences with 19-level readability annotations. Sentence-level (not just document-level) difficulty scoring.
- **Alif relevance**: Could directly use these graded sentences OR train a readability classifier on this data to score any Arabic sentence.

---

## 6. Practical Recommendations for Alif

### Tier 1: Immediate Value (should integrate)

1. **Tatoeba** (67K sentences with translations) — direct import, filter for MSA, use translations as glosses
2. **BAREC** (69K sentences with readability levels) — Hugging Face download, use readability levels for difficulty-based selection
3. **AMARA/TED** (educational content with translations) — natural MSA, diverse topics

### Tier 2: High Value with Processing

4. **WikiMatrix** (Arabic-English parallel, filtered by margin score >1.06) — millions of translated pairs, needs quality filtering
5. **Hindawi E-Book Corpus** (81.5M words, fiction/children's lit) — extract sentences, classify by difficulty
6. **OSIAN** (37M sentences, MSA news) — filter for short/simple sentences, no translations
7. **Leipzig Corpora** (pre-processed sentence packs) — convenient 1M-sentence downloads

### Tier 3: Research/Future Value

8. **Tashkeela** (75M diacritized words) — valuable for tashkeel model training, small MSA portion usable
9. **Leveled Reading Corpus** (UAE textbooks, graded K-12) — gold standard if accessible
10. **SAMER readability lexicon** (40K lemmas with difficulty levels) — map to our lemma database
11. **CCMatrix** (billions of parallel sentences) — massive but needs aggressive quality filtering
12. **OpenSubtitles** (conversational, parallel) — needs dialect filtering

### Hybrid Pipeline Proposal

```
1. Load corpus sentences (Tatoeba + BAREC + AMARA)
2. For each sentence:
   a. Tokenize and match words against user's vocabulary
   b. Calculate comprehensibility score (% known words)
   c. If readability level available (BAREC), use directly
   d. If translation available, store as English gloss
3. For a given target word:
   a. Find sentences containing the word with comprehensibility >= 90%
   b. Rank by: readability level, sentence length (shorter better), translation availability
   c. If no corpus sentence meets criteria → fall back to LLM generation
4. Benefits: instant (no LLM latency), free, deterministic, reproducible
```

### Estimated Usable Sentence Pool

| Source | Raw sentences | After MSA filter | After length filter (<20 words) | With translations |
|--------|--------------|-------------------|-------------------------------|-------------------|
| Tatoeba | 67K | ~50K | ~45K | ~40K |
| BAREC | 69K | 69K (all MSA) | ~50K | No |
| AMARA/TED | ~100K est. | ~80K | ~60K | Yes |
| WikiMatrix (filtered) | ~5M est. | ~4M | ~2M | Yes |
| OSIAN (filtered) | 37M | 37M | ~10M | No |
| **Total usable** | | | **~12M** | **~2.1M** |

For comparison, Alif currently has ~600 active sentences (LLM-generated). Even the smallest corpus (Tatoeba, 45K filtered) would be a 75x increase.

---

## 7. Key URLs and Downloads

| Resource | URL |
|----------|-----|
| Tatoeba downloads | https://tatoeba.org/en/downloads |
| BAREC on Hugging Face | https://huggingface.co/datasets/CAMeL-Lab/BAREC-Shared-Task-2025-sent |
| OSIAN (Leipzig) | https://corpora.uni-leipzig.de |
| Hindawi E-Book Corpus | https://researchdata.se/en/catalogue/dataset/2024-145 |
| Tashkeela (Kaggle) | https://www.kaggle.com/datasets/linuxscout/tashkeela |
| Tanzil Quran | https://tanzil.net/download/ |
| UN Parallel Corpus | https://www.un.org/dgacm/en/content/uncorpus/download |
| WikiMatrix | https://github.com/facebookresearch/LASER |
| Abu El-Khair (HF) | https://huggingface.co/datasets/abuelkhair-corpus/arabic_billion_words |
| KSUCCA | https://sourceforge.net/projects/ksucca-corpus/ |
| SAMER Project | https://sites.google.com/nyu.edu/samer |
| Masader (500+ datasets catalog) | https://arbml.github.io/masader/ |
| OPUS (all corpora) | https://opus.nlpl.eu/ |
| OpenSubtitles | https://opus.nlpl.eu/OpenSubtitles/corpus/version/OpenSubtitles |
| Internet Archive Arabic | https://archive.org/details/booksbylanguage_arabic |
| Arabic Collections Online | https://dlib.nyu.edu/aco/ |

---

## Sources

- [OSIAN paper (ACL Anthology)](https://aclanthology.org/W19-4619/)
- [Abu El-Khair Corpus (arXiv)](https://arxiv.org/abs/1611.04033)
- [Tashkeela paper (ScienceDirect)](https://www.sciencedirect.com/science/article/pii/S2352340917300112)
- [WikiMatrix paper (EACL 2021)](https://aclanthology.org/2021.eacl-main.115/)
- [CCMatrix paper (arXiv)](https://arxiv.org/abs/1911.04944)
- [UN Parallel Corpus paper (LREC 2016)](https://aclanthology.org/L16-1561/)
- [AMARA Corpus paper (IWSLT 2013)](https://aclanthology.org/2013.iwslt-papers.2/)
- [Candidate Sentence Selection (arXiv)](https://arxiv.org/abs/1706.03530)
- [BAREC paper (arXiv)](https://arxiv.org/abs/2502.13520)
- [SAMER project](https://sites.google.com/nyu.edu/samer)
- [Leveled Reading Corpus (ACL Anthology)](https://aclanthology.org/L18-1366/)
- [Hindawi E-Book Corpus (ScienceDirect)](https://www.sciencedirect.com/science/article/pii/S235234092500188X)
- [Arabic Readability Assessment survey (ACM)](https://dl.acm.org/doi/10.1145/3571510)
- [Masader catalog (ACL Anthology)](https://aclanthology.org/2022.lrec-1.681/)
- [ML_for_SLA (GitHub)](https://github.com/JonathanLaneMcDonald/ML_for_SLA)
- [Comprehensible Input for Arabic (ResearchGate)](https://www.researchgate.net/publication/374634011)
- [Arabic NNLP Resources (GitHub)](https://github.com/NNLP-IL/Arabic-Resources)
