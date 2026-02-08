# Arabic Morphological Analysis: Open Source Tools & Libraries

*Comprehensive survey -- last updated February 2026*

This document covers open-source tools for Arabic morphological analysis, root extraction, lemmatization, conjugation generation, and stemming. Focus is on **Modern Standard Arabic (MSA / fusha)**.

---

## Table of Contents

1. [Comprehensive Toolkits (Multi-feature)](#1-comprehensive-toolkits)
2. [Root Extraction](#2-root-extraction)
3. [Morphological Analysis](#3-morphological-analysis)
4. [Lemmatization](#4-lemmatization)
5. [Conjugation Generation](#5-conjugation-generation)
6. [Stemming](#6-stemming)
7. [Supporting Libraries & Resources](#7-supporting-libraries--resources)
8. [Transformer-Based Models](#8-transformer-based-models)
9. [Quality & Accuracy Comparisons](#9-quality--accuracy-comparisons)
10. [Summary Recommendation Matrix](#10-summary-recommendation-matrix)

---

## 1. Comprehensive Toolkits

These are full-stack toolkits that cover multiple aspects of Arabic morphological processing.

### CAMeL Tools

**The most actively maintained and comprehensive Arabic NLP toolkit available today.**

- **URL:** https://github.com/CAMeL-Lab/camel_tools
- **Language:** Python
- **License:** MIT
- **Stars:** ~522
- **Latest Version:** v1.5.7 (September 2025)
- **Python:** 3.8 - 3.12 (also requires Rust compiler)
- **Install:** `pip install camel-tools` + `camel_data -i all`
- **Status:** Actively maintained by CAMeL Lab at NYU Abu Dhabi

**Capabilities:**
- Morphological analysis and disambiguation (using CALIMA databases)
- Morphological generation (word form generation from features)
- Lemmatization
- POS tagging
- Dialect identification (MSA + dialects)
- Named entity recognition
- Sentiment analysis
- Diacritization
- Tokenization and segmentation
- Transliteration (Buckwalter, Safe Buckwalter, etc.)

**Morphological databases included:**
- `calima-msa-r13` -- Modern Standard Arabic
- `calima-egy-r13` -- Egyptian Arabic

**Quality:** State-of-the-art for MSA morphological analysis. The underlying CALIMA Star database extends SAMA with 40K+ lemmas. Consistently outperforms other tools on several benchmarks (Nafis dataset: 0.68 accuracy, Sharaye: 0.81 accuracy in segmentation comparison studies).

**Notes:** This is the spiritual successor to MADAMIRA. The CAMeL Lab actively publishes research improving the toolkit. Requires downloading ~2-3 GB of model data.

---

### CamelMorph (companion to CAMeL Tools)

**The largest open-source MSA morphological analyzer and generator.**

- **URL:** https://github.com/CAMeL-Lab/camel_morph
- **Language:** Python
- **License:** MIT (code), CC-BY 4.0 (data)
- **Stars:** ~15
- **Status:** Active (LREC-COLING 2024 release)

**Capabilities:**
- Morphological analysis (input word -> all possible analyses)
- Morphological generation (features -> word forms)
- Reinflection
- 100K+ lemmas
- ~1.45 billion possible analyses, ~535 million unique diacritizations
- ~36% less out-of-vocabulary rate than SAMA on a 10-billion-word corpus

**Quality:** Order-of-magnitude larger than SAMA. Integrates seamlessly with CAMeL Tools.

---

### SinaTools

**Strong recent contender with high accuracy on lemmatization and POS tagging.**

- **URL:** https://github.com/SinaLab/sinatools
- **Language:** Python
- **License:** MIT
- **Stars:** ~31
- **Install:** `pip install sinatools` (requires Python 3.11.11)
- **Status:** Active (published at ACLING 2024)

**Capabilities:**
- Lemmatization (90.5% accuracy)
- POS tagging (93.8% accuracy)
- Root tagging
- Named entity recognition
- Corpus processing
- Diacritic-aware word matching
- Text stripping methods

**Quality:** Claims to outperform all similar tools on lemmatization and POS tagging benchmarks. Processing speed: 33K tokens/sec. Published evaluation shows strong results.

**Related tool -- Alma:** The lemmatizer/POS tagger component of SinaTools, also available separately at https://sina.birzeit.edu/alma/. Achieved 88% F1 on LDC Arabic Treebank and 90% F1 on Salma corpus. Outperformed Farasa, MADAMIRA, and Camelira in both lemmatization and POS tagging.

---

### Farasa

**Fast and accurate Arabic NLP toolkit from QCRI.**

- **URL:** https://farasa.qcri.org/
- **GitHub (Python wrapper):** https://github.com/MagedSaeed/farasapy
- **Alternative wrapper:** https://github.com/OpenITI/pyFarasa
- **Language:** Java (core), Python wrapper available
- **License:** MIT (Python wrapper); **research use only** (core Java toolkit)
- **Stars:** ~139 (farasapy)
- **Install:** `pip install farasapy` (requires Java installed)
- **Status:** Maintained, but core toolkit license restricts commercial use

**Capabilities:**
- Word segmentation (98%+ accuracy)
- Stemming
- Lemmatization
- POS tagging
- Diacritization
- Named entity recognition
- Dependency parsing
- Spell checking

**Quality:** Segmentation accuracy on par with or better than MADAMIRA and Stanford. Processes 1 billion words in < 5 hours. However, lemmatization accuracy is lower than Alma/SinaTools in benchmarks (e.g., 0.59 accuracy on Nafis dataset). Very fast but sacrifices some accuracy for speed.

**Important caveat:** The core Farasa binaries are **restricted to research/academic use**. The Python wrapper is MIT but wraps proprietary Java JARs. Not suitable for commercial applications without permission from QCRI.

---

### Qalsadi

**Rule-based Arabic morphological analyzer and lemmatizer ecosystem.**

- **URL:** https://github.com/linuxscout/qalsadi
- **Language:** Python
- **License:** GPL-2.0+
- **Stars:** ~42
- **Latest Version:** 0.5.1 (July 2025)
- **Install:** `pip install qalsadi`
- **Status:** Actively maintained by Taha Zerrouki

**Capabilities:**
- Full morphological analysis (vocalized and unvocalized text)
- Lemmatization
- Word frequency data for modern Arabic
- Verb conjugation analysis (via Qutrub integration)
- Multiple output formats (table, CSV, JSON, XML)
- Caching support (memory, pickle, pickledb, CodernityDB)

**Quality:** Production/stable status. Rule-based approach means consistent behavior. Part of a larger ecosystem of tools by the same author (PyArabic, Tashaphyne, Qutrub, Mishkal, Arramooz).

---

### Stanza (Stanford NLP)

**General-purpose multilingual NLP toolkit with Arabic support.**

- **URL:** https://github.com/stanfordnlp/stanza
- **Language:** Python
- **License:** Apache 2.0
- **Stars:** ~7,700
- **Latest Version:** v1.11.0 (October 2025)
- **Install:** `pip install stanza`
- **Status:** Very actively maintained

**Capabilities for Arabic:**
- Tokenization and sentence segmentation
- Lemmatization
- POS tagging
- Morphological feature tagging
- Dependency parsing
- Named entity recognition
- Multi-word token expansion

**Quality:** Neural pipeline trained on Universal Dependencies Arabic-PADT treebank. Good general accuracy but not specialized for Arabic morphology. Does not provide root extraction or detailed morphological decomposition. Best used when you need a multilingual pipeline rather than deep Arabic-specific analysis.

---

### SAFAR Framework

**Large-scale Java-based Arabic NLP platform.**

- **URL:** http://arabic.emi.ac.ma/safar/
- **Language:** Java
- **License:** Open source (specific license varies by component)
- **Status:** Academic project, 50+ integrated tools

**Capabilities:**
- Morphological analysis (wraps Alkhalil, BAMA, MADAMIRA)
- Stemming (multiple algorithms)
- Lemmatization
- Morphological generation
- Syntactic analysis
- Semantic analysis

**Quality:** More of a framework/platform than a single tool. Integrates many third-party analyzers behind a unified Java API. Useful for researchers who want to compare different approaches.

---

### MADAMIRA

**The classic reference tool, now largely superseded by CAMeL Tools.**

- **URL:** Previously distributed via Columbia University
- **Language:** Java
- **License:** Restricted (academic license required)
- **Status:** No longer actively developed; superseded by CAMeL Tools

**Capabilities:**
- Morphological analysis and disambiguation
- Lemmatization
- POS tagging
- Diacritization
- Tokenization
- NER
- Base-phrase chunking

**Quality:** Was the gold standard for years. Combined MADA + AMIRA systems. Still cited in many papers but CAMeL Tools is the recommended successor.

---

## 2. Root Extraction

### Tashaphyne

**Light stemmer that uniquely provides both stemming and root extraction simultaneously.**

- **URL:** https://github.com/linuxscout/tashaphyne
- **Language:** Python
- **License:** GPL-3.0
- **Stars:** ~103
- **Install:** `pip install tashaphyne`
- **Status:** Maintained by Taha Zerrouki

**Capabilities:**
- Light stemming + root extraction in a single pass
- Word segmentation (all possible decompositions)
- Word normalization
- Customizable affix lists
- Data-independent (no external dictionary required)
- Finite state automaton-based

**Quality:** Tashaphyne 0.4 outperformed Khoja, ISRI, Motaz/Light10, Farasa, and Assem stemmers in comparative studies. Distinctive in providing root extraction alongside stemming, which most light stemmers do not do.

---

### Qutuf

**Expert-system-based morphological analyzer with root extraction.**

- **URL:** https://github.com/Qutuf/Qutuf
- **Language:** Python (65%), HTML (35%)
- **License:** Not specified
- **Stars:** ~132
- **Latest Release:** Qutuf 2.0 (June 2019)
- **Status:** Not recently updated (last release 2019)

**Capabilities:**
- Root extraction (heavy stemming)
- Light stemming
- POS tagging (rule-based expert system)
- Cliticization parsing via finite state automata
- Uses AlKhalil Morpho Sys database
- JSON, XML, HTML output formats

**Quality:** Well-documented academic project. The expert-system approach provides explainable results. However, has not been updated since 2019.

---

### CAMeL Tools (Morphological Analyzer)

CAMeL Tools' morphological analyzer provides root information as part of its full morphological analysis output. See [CAMeL Tools](#camel-tools) above.

---

### Buckwalter Arabic Morphological Analyzer (BAMA)

**The foundational analyzer that many modern tools build upon.**

- **URL (v1):** https://catalog.ldc.upenn.edu/LDC2002L49
- **URL (v2):** https://catalog.ldc.upenn.edu/LDC2004L02
- **Open-source reimplementation:** https://sourceforge.net/projects/aramorpher/
- **Language:** Perl (original), C++ (Aramorpher)
- **License:** GPL v2 (v1.0); LDC membership required (v2.0)
- **Status:** Historical/foundational -- not actively developed

**Capabilities:**
- Morphological analysis producing root, pattern, POS, gloss
- Concatenative morphology decomposition

**Quality:** The original reference standard. Version 1.0 is freely available under GPL. All modern tools (SAMA, CALIMA, CAMeL Tools) descend from this work. The Aramorpher reimplementation on SourceForge provides a C++ interface.

---

## 3. Morphological Analysis

### AlKhalil Morpho Sys 2

**High-coverage Arabic morphosyntactic analyzer.**

- **URL:** https://sourceforge.net/projects/alkhalil/
- **Language:** Java
- **License:** Open source (via SourceForge)
- **Status:** Available, academic project

**Capabilities:**
- Processes vocalized, partially vocalized, and unvocalized text
- Provides all possible vowelized forms
- Outputs: vocalization, grammatical category, roots, patterns, proclitics, enclitics
- Root database + vocalized patterns + clitic tables

**Quality:** Won a competition for Arabic morphological systems in 2010. Version 2 exceeds 99% word coverage. Database used by Qutuf and other tools.

---

### AraComLex

**Finite-state morphological transducer for MSA.**

- **URL:** https://sourceforge.net/projects/aracomlex/ and https://github.com/mohammedattia/AraComLex
- **Language:** XFST / foma (finite state)
- **License:** GPL v3
- **Status:** Academic project, version 2.0 available

**Capabilities:**
- Full morphological analysis via finite state transducers
- Generation from morphological specifications
- Corpus-based: tuned to contemporary MSA usage
- Generated a 9-million-word spell-checking dictionary

**Quality:** Built from a corpus of 1+ billion words. Eliminates archaic Classical Arabic entries not used in modern MSA. Requires foma or Xerox xfst compiler.

---

## 4. Lemmatization

### Alma

**Currently the fastest and most accurate open-source Arabic lemmatizer.**

- **URL:** https://sina.birzeit.edu/alma/
- **Language:** Python (part of SinaTools)
- **License:** MIT (as part of SinaTools)
- **Status:** Active (2024)

**Performance:**
- 88% F1 on LDC Arabic Treebank (339K tokens)
- 90% F1 on Salma corpus (34K tokens)
- POS accuracy: 93.8%
- Speed: 32K tokens/sec (339K tokens in 10 seconds)
- Outperforms Farasa, MADAMIRA, and Camelira

**Distinctive feature:** Returns unambiguous (fully diacritized) lemmas, unlike many tools that strip diacritics. Uses Qabas lexicographic database with frequency-ordered morphological solutions.

---

### CAMeL Tools Lemmatizer

Uses CALIMA Star and CamelMorph databases. See [CAMeL Tools](#camel-tools).

---

### Farasa Lemmatizer

Available via farasapy. See [Farasa](#farasa). Reported 97.32% accuracy in its own benchmarks, though independent evaluations show lower numbers.

---

### Stanza Arabic Lemmatizer

Neural lemmatizer trained on UD treebanks. See [Stanza](#stanza-stanford-nlp).

---

## 5. Conjugation Generation

### Qutrub

**The primary open-source Arabic verb conjugator.**

- **URL:** https://github.com/linuxscout/qutrub
- **PyPI library:** `pip install libqutrub`
- **Language:** Python
- **License:** GPL
- **Stars:** ~98
- **Status:** Maintained by Taha Zerrouki

**Capabilities:**
- Conjugates Arabic verbs across all tenses: past, present, future, imperative
- Active and passive voice
- Subjunctive and jussive moods
- Confirmed (energetic) forms
- All persons and numbers (14 pronoun forms)
- Transitive / intransitive distinction
- Six primary conjugation modes (Bab Tasrif) using diacritical patterns
- Multiple output formats: HTML, CSV, TeX, XML, JSON, GUI tables

**Quality:** The most established open-source Arabic verb conjugator. Generic approach (does not rely on classified verb models), which means it works with any verb but may not handle every irregular case perfectly. Web demo available at https://tahadz.com/qutrub.

**Limitations:** Does not explicitly enumerate Forms I-X in the way a classical grammar textbook does. It conjugates based on the vocalized verb input rather than root + form number.

---

### arabic-conjugation (robaleman)

**Programmatic verb form generation from triliteral roots.**

- **URL:** https://github.com/robaleman/arabic-conjugation
- **Language:** Python
- **License:** Not specified
- **Status:** Small project, limited maintenance

**Capabilities:**
- Generate conjugations from a root (e.g., "k-t-b") + form number
- Supports Forms I-XV
- All tenses, persons, genders, numbers, moods, voices
- `generate()` for specific forms, `generate_all()` for complete paradigm

**Limitations:** Currently only supports "sound" roots (no glottal stops or semi-vowels). Form I requires manual stem vowel specification. Small project without wide adoption.

---

### CamelMorph Generator

CAMeL Tools' morphological generator can produce word forms from feature specifications. See [CamelMorph](#camelmorph-companion-to-camel-tools).

---

### ACON (Arabic Conjugator)

- **URL:** http://acon.baykal.be/
- **Status:** Web-only tool, not a downloadable library
- **Quality:** Automated conjugator applying Arabic grammar rules. Useful as a reference but not embeddable.

---

## 6. Stemming

### ISRI Stemmer (via NLTK)

**The most widely used Arabic stemmer, included in NLTK.**

- **URL:** https://www.nltk.org/api/nltk.stem.isri.html
- **Language:** Python
- **License:** Apache 2.0 (NLTK)
- **Install:** `pip install nltk` then `from nltk.stem.isri import ISRIStemmer`
- **Status:** Stable, maintained as part of NLTK

**Approach:** Does NOT use a root dictionary. Falls back to normalization if root cannot be found.

**Quality:** Good for information retrieval. Performs better than Khoja on shorter title queries. Widely used due to NLTK integration. Light/aggressive stemming without dictionary lookup means some errors on irregular words.

```python
from nltk.stem.isri import ISRIStemmer
stemmer = ISRIStemmer()
stemmer.stem("يكتبون")  # -> كتب
```

---

### Khoja Stemmer

**Classical root-based Arabic stemmer.**

- **Language:** Java (original), Python ports available
- **License:** Open source
- **Status:** Historical -- original implementation from early 2000s

**Approach:** Uses a root dictionary. Aggressive stemming (85% reduction rate).

**Quality:** High reduction rate (85-90.9% with Lucene variant) but can be too aggressive. Errors on some word types. The basis for many subsequent stemmers.

---

### Tashaphyne

See [Tashaphyne under Root Extraction](#tashaphyne). Provides light stemming + root extraction. Outperforms Khoja, ISRI, Farasa, and Assem stemmers in comparative studies.

---

### Apache Lucene Arabic Analyzer

**Light stemmer built into Lucene/Solr/Elasticsearch.**

- **URL:** Built into Apache Lucene
- **Language:** Java
- **License:** Apache 2.0
- **Status:** Actively maintained (part of Lucene)

**Approach:** Light stemming based on "Light Stemming for Arabic Information Retrieval" by Larkey et al. Removes common prefixes/suffixes but does NOT extract roots.

**Third-party root-based extension:** https://github.com/msarhan/lucene-arabic-analyzer (uses AlKhalil database for root extraction, Apache 2.0 license)

**Quality:** Well-tested for IR use cases. The built-in light stemmer is conservative and safe. The root-based extension provides deeper analysis.

---

### Light10 / Motaz Light Stemmer

- **URL:** https://github.com/motazsaad/arabic-light-stemming-py (Python), also https://github.com/motazsaad/arabic-light-stemmer (command line)
- **Language:** Python
- **License:** Open source
- **Status:** Available but not frequently updated

**Quality:** A well-known light stemming algorithm. Removes definite articles, conjunctions, and common suffixes while maintaining minimum word length.

---

## 7. Supporting Libraries & Resources

### PyArabic

**Foundational Python library for Arabic text manipulation.**

- **URL:** https://github.com/linuxscout/pyarabic
- **Language:** Python
- **License:** GPL-3.0
- **Stars:** ~475
- **Install:** `pip install pyarabic`
- **Status:** Maintained by Taha Zerrouki

**Capabilities:**
- Arabic letter classification and detection
- Text tokenization
- Diacritics removal (all, except Shadda, tatweel, last_haraka)
- Letter normalization (ligatures, hamza variants)
- Number-to-word conversion
- Numerical phrase extraction

**Note:** This is NOT a morphological analyzer. It provides the text-processing primitives that tools like Qalsadi and Mishkal build upon.

---

### Arramooz

**Open-source Arabic dictionary for morphological analysis.**

- **URL:** https://github.com/linuxscout/arramooz
- **Language:** SQLite database + Python
- **License:** GPL-2.0
- **Stars:** ~147
- **Status:** Maintained by Taha Zerrouki

**Contents:**
- Stop words database
- Verb database (with conjugation patterns)
- Noun database
- Generated from manually collected data (Ayaspell spellchecker)

Used by Qalsadi, Mishkal, and other tools in the linuxscout ecosystem.

---

### Mishkal (Arabic Diacritizer)

**Rule-based Arabic text vocalization using morphological analysis.**

- **URL:** https://github.com/linuxscout/mishkal
- **Language:** Python
- **License:** GPL-3.0
- **Stars:** ~302
- **Install:** `pip install mishkal`
- **Status:** Maintained by Taha Zerrouki

Uses Qalsadi for morphological analysis, Arramooz dictionary, ArAnaSyn for syntax, and Asmai for semantics to determine correct diacritization. Relevant here because it demonstrates the full morphological analysis pipeline in action.

---

### arabic-reshaper

**Text rendering library (NOT morphological analysis).**

- **URL:** https://github.com/mpcabd/python-arabic-reshaper
- **Language:** Python
- **License:** MIT
- **Install:** `pip install arabic-reshaper`

Reshapes Arabic characters for display in applications that lack Arabic support. Handles RTL text, ligatures, and diacritics. Not related to morphological analysis but commonly encountered in Arabic NLP tool lists.

---

## 8. Transformer-Based Models

### AraBERT

- **URL:** https://github.com/aub-mind/arabert
- **Hugging Face:** https://huggingface.co/aubmindlab/bert-base-arabert
- **License:** Apache 2.0
- **Status:** Active

Pre-trained BERT model for Arabic. Can be fine-tuned for morphological tasks. AraBERT v1 & v2 use Farasa segmentation as preprocessing. Useful as a base model for building custom morphological classifiers.

---

### CAMeLBERT

- **URL:** https://huggingface.co/CAMeL-Lab/bert-base-arabic-camelbert-da
- **License:** Apache 2.0
- **Status:** Active

BERT models from the CAMeL Lab pre-trained on different Arabic varieties (MSA, dialectal, classical). Can be fine-tuned for morphological disambiguation tasks.

---

### AraT5

- **URL:** https://github.com/UBC-NLP/araT5
- **Hugging Face:** https://huggingface.co/UBC-NLP/AraT5-base
- **License:** Apache 2.0
- **Status:** Active

Text-to-text transformer for Arabic. Can be fine-tuned for lemmatization as a sequence-to-sequence task. Recent research shows promising results using AraT5 for transformer-based lemmatization.

---

## 9. Quality & Accuracy Comparisons

### Lemmatization Accuracy

| Tool | Dataset | Metric | Score |
|------|---------|--------|-------|
| Alma/SinaTools | LDC ATB (339K tokens) | F1 | 88% |
| Alma/SinaTools | Salma corpus (34K tokens) | F1 | 90% |
| Farasa | Self-reported | Accuracy | 97.3% |
| MADAMIRA | Various | -- | Reference standard |
| CAMeL Tools | Various | -- | On par with MADAMIRA |

Note: Direct comparisons are difficult due to inconsistent task definitions and lemma representation (especially regarding diacritization).

### Segmentation Accuracy

| Tool | Speed | Quality |
|------|-------|---------|
| Farasa | ~200M words/hour | 98%+ segmentation accuracy |
| MADAMIRA | ~2M words/hour | 98%+ segmentation accuracy |
| CAMeL Tools | Moderate | Competitive with MADAMIRA |

### Stemming Comparison

| Stemmer | Reduction Rate | Root Dictionary | Quality Notes |
|---------|---------------|-----------------|---------------|
| Khoja | 85-91% | Yes | Too aggressive for some tasks |
| ISRI (NLTK) | ~80% | No | Good for IR, no dictionary needed |
| Tashaphyne 0.4 | ~75% | No | Best in comparative study vs 6 others |
| Lucene Light | ~60% | No | Conservative, safe for IR |
| Farasa | ~70% | No | Fast but less accurate |

### Morphological Analysis Coverage

| Tool | Coverage | Vocabulary |
|------|----------|-----------|
| CamelMorph MSA | 99%+ | 100K+ lemmas, 1.45B analyses |
| AlKhalil 2 | 99%+ | Large rule-based database |
| SAMA | ~95% | 40K+ lemmas |
| Buckwalter v2 | ~90% | Historical reference |

---

## 10. Summary Recommendation Matrix

### For a new project in 2026, which tool should you use?

| Need | Recommended Tool | Why |
|------|-----------------|-----|
| **Full morphological analysis (MSA)** | CAMeL Tools | Most comprehensive, actively maintained, MIT license, largest database |
| **Fast lemmatization** | SinaTools / Alma | Best accuracy/speed tradeoff, MIT license |
| **Root extraction** | CAMeL Tools or Tashaphyne | CAMeL for accuracy, Tashaphyne for lightweight/no-data approach |
| **Verb conjugation generation** | Qutrub (libqutrub) | Only mature open-source conjugator, GPL |
| **Stemming for IR** | ISRI (NLTK) or Tashaphyne | ISRI for simplicity, Tashaphyne for better quality |
| **Quick integration (any language)** | Stanza | Apache 2.0, multilingual, easy setup |
| **Commercial use** | CAMeL Tools (MIT) or SinaTools (MIT) | Avoid Farasa (research only) and GPL tools |
| **Java projects** | AlKhalil Morpho Sys 2 | Best open-source Java morphological analyzer |
| **Morphological generation** | CamelMorph + CAMeL Tools | Only tool generating 1.45B+ word forms |
| **Lightweight, no external data** | Tashaphyne | No dictionary required, customizable affixes |

### The Taha Zerrouki (linuxscout) Ecosystem

Taha Zerrouki has built an interconnected set of tools that work together. If you use one, you benefit from the whole ecosystem:

- **PyArabic** -- text primitives (GPL-3.0)
- **Tashaphyne** -- stemming + root extraction (GPL-3.0)
- **Arramooz** -- morphological dictionary (GPL-2.0)
- **Qalsadi** -- morphological analyzer (GPL-2.0+)
- **Qutrub / libqutrub** -- verb conjugation (GPL)
- **Mishkal** -- diacritization (GPL-3.0)

All are GPL-licensed, which is a consideration for commercial projects.

### Key Observations

1. **CAMeL Tools is the clear leader** for comprehensive Arabic morphological analysis in Python. It is well-funded (NYU Abu Dhabi), actively maintained, MIT-licensed, and has the largest morphological database available.

2. **SinaTools/Alma is the accuracy leader** for lemmatization specifically, with strong published benchmarks.

3. **Farasa is fast but restrictive** -- the core toolkit is limited to research use, which rules it out for commercial applications.

4. **The linuxscout ecosystem** provides the most tools but everything is GPL, which limits commercial use.

5. **Verb conjugation generation is underserved** -- Qutrub is the only mature option, and it does not explicitly support the traditional Form I-X paradigm by form number. The robaleman/arabic-conjugation project does support form numbers but only handles sound roots.

6. **Root extraction as a standalone feature is rare** -- most tools provide it as part of broader morphological analysis. Tashaphyne is the best standalone option.

7. **For production systems**, the combination of **CAMeL Tools** (analysis, lemmatization, generation) + **Qutrub** (conjugation) provides the most complete coverage.

---

## Appendix: All Tools at a Glance

| Tool | Language | License | Last Active | GitHub Stars | Primary Use |
|------|----------|---------|-------------|-------------|-------------|
| [CAMeL Tools](https://github.com/CAMeL-Lab/camel_tools) | Python | MIT | 2025 | 522 | Full NLP toolkit |
| [CamelMorph](https://github.com/CAMeL-Lab/camel_morph) | Python | MIT / CC-BY 4.0 | 2024 | 15 | Morphological DB |
| [SinaTools](https://github.com/SinaLab/sinatools) | Python | MIT | 2024 | 31 | Lemmatization, POS |
| [Farasa](https://farasa.qcri.org/) / [farasapy](https://github.com/MagedSaeed/farasapy) | Java/Python | Research only / MIT | 2024 | 139 | Segmentation, NLP |
| [Qalsadi](https://github.com/linuxscout/qalsadi) | Python | GPL-2.0+ | 2025 | 42 | Morphological analysis |
| [Tashaphyne](https://github.com/linuxscout/tashaphyne) | Python | GPL-3.0 | Active | 103 | Stemming, root extraction |
| [Qutrub](https://github.com/linuxscout/qutrub) | Python | GPL | Active | 98 | Verb conjugation |
| [PyArabic](https://github.com/linuxscout/pyarabic) | Python | GPL-3.0 | 2023 | 475 | Text primitives |
| [Arramooz](https://github.com/linuxscout/arramooz) | Python/SQL | GPL-2.0 | Active | 147 | Morphological dictionary |
| [Mishkal](https://github.com/linuxscout/mishkal) | Python | GPL-3.0 | Active | 302 | Diacritization |
| [Qutuf](https://github.com/Qutuf/Qutuf) | Python | Unspecified | 2019 | 132 | Morphological analysis |
| [Stanza](https://github.com/stanfordnlp/stanza) | Python | Apache 2.0 | 2025 | 7,700 | Multilingual NLP |
| [NLTK ISRIStemmer](https://www.nltk.org/api/nltk.stem.isri.html) | Python | Apache 2.0 | Stable | -- | Stemming |
| [AlKhalil Morpho Sys](https://sourceforge.net/projects/alkhalil/) | Java | Open source | Stable | -- | Morphological analysis |
| [AraComLex](https://github.com/mohammedattia/AraComLex) | XFST/foma | GPL-3.0 | Stable | -- | FST morphology |
| [AraNLP](https://github.com/Maha-J-Althobaiti/AraNLP) | Java | Open source | 2014 | -- | Arabic text processing |
| [SAFAR](http://arabic.emi.ac.ma/safar/) | Java | Open source | Active | -- | NLP framework |
| [Buckwalter/Aramorpher](https://sourceforge.net/projects/aramorpher/) | Perl/C++ | GPL-2.0 | Historical | -- | Morphological analysis |
| [Lucene Arabic](https://lucene.apache.org/) | Java | Apache 2.0 | Active | -- | IR stemming |
| [arabic-conjugation](https://github.com/robaleman/arabic-conjugation) | Python | Unspecified | Small project | -- | Verb conjugation |
| [AraBERT](https://github.com/aub-mind/arabert) | Python | Apache 2.0 | Active | -- | Transformer model |
| [AraT5](https://github.com/UBC-NLP/araT5) | Python | Apache 2.0 | Active | -- | Transformer model |
| [Stanford CoreNLP](https://github.com/stanfordnlp/CoreNLP) | Java | GPL-2.0 | Active | -- | NLP toolkit |
