# Arabic Language Learning & NLP: Datasets and Corpora

A comprehensive survey of freely (and some paid) available datasets, corpora, dictionaries, and tools for Arabic language learning and NLP, with a focus on Modern Standard Arabic (MSA / fusha).

**Last updated:** 2026-02-08

---

## Table of Contents

1. [Meta-Catalogues](#1-meta-catalogues)
2. [Word Frequency Lists](#2-word-frequency-lists)
3. [Machine-Readable Dictionaries](#3-machine-readable-dictionaries)
4. [Root-Word Databases](#4-root-word-databases)
5. [Morphological Databases & Tools](#5-morphological-databases--tools)
6. [Diacritized (Vowelized) Corpora](#6-diacritized-vowelized-corpora)
7. [Parallel Corpora (Arabic-English)](#7-parallel-corpora-arabic-english)
8. [Treebanks](#8-treebanks)
9. [Large MSA Text Corpora](#9-large-msa-text-corpora)
10. [Graded / Readability-Classified Resources](#10-graded--readability-classified-resources)
11. [Semantic Resources (WordNet / Ontology)](#11-semantic-resources-wordnet--ontology)
12. [Quranic & Classical Arabic Corpora](#12-quranic--classical-arabic-corpora)
13. [Diacritization & Morphological Tools (Software)](#13-diacritization--morphological-tools-software)
14. [Dialect Resources](#14-dialect-resources)
15. [Wiktionary & Wikipedia Derived Data](#15-wiktionary--wikipedia-derived-data)

---

## 1. Meta-Catalogues

Before diving into individual resources, these catalogues index hundreds of Arabic NLP datasets.

### Masader (ARBML)

- **URL:** <https://arbml.github.io/masader/>
- **GitHub:** <https://github.com/ARBML/masader>
- **What:** The largest public catalogue for Arabic NLP and speech datasets. 600+ datasets, each annotated with 25+ metadata attributes (language variety, domain, task, license, size, etc.)
- **Access:** Web interface, or programmatic via HuggingFace: `load_dataset('arbml/masader')`
- **License:** GPL-3.0 (the catalogue itself)

### NNLP-IL Arabic Resources List

- **GitHub:** <https://github.com/NNLP-IL/Arabic-Resources>
- **What:** Comprehensive curated list of Arabic NLP resources, tools, datasets, and corpora.

### Awesome Arabic NLP

- **GitHub:** <https://github.com/Curated-Awesome-Lists/awesome-arabic-nlp>
- **What:** Extensive collection of resources, tools, datasets, and best practices for Arabic NLP.

---

## 2. Word Frequency Lists

### CAMeL Lab Arabic Frequency Lists

- **GitHub:** <https://github.com/CAMeL-Lab/Camel_Arabic_Frequency_Lists>
- **What:** Frequency lists derived from the pretraining data of CamelBERT models. Massive scale: 17.3 billion tokens total, 16.1M unique word types.
- **MSA file:** `MSA_freq_lists.tsv.zip` -- 11.4M unique MSA word types with frequencies
- **Format:** TSV (word in Arabic script + frequency count)
- **Other varieties:** Classical Arabic (2.4M types), Dialectal Arabic (6.7M types), Mixed (16.1M types)
- **Quality:** Very high quality; derived from curated pretraining datasets. Undiacritized.
- **License:** Check repository LICENSE.txt

### KELLY Project Arabic Word List

- **URL:** <https://spraakbanken.gu.se/en/projects/kelly>
- **Leeds mirror:** <http://corpus.leeds.ac.uk/serge/kelly/>
- **What:** Pedagogically-oriented frequency lists for language learners, developed for 9 languages including Arabic. Words ranked by frequency (instances per million) and tagged with suggested CEFR levels (A1-C2).
- **Format:** Excel (.xlsx)
- **License:** CC BY-NC-SA 2.0
- **Quality:** Small but high-quality curated list aimed at learners. One of the few resources that maps Arabic words to CEFR levels.

### Wiktionary Frequency Lists for Arabic

- **URL:** <https://en.wiktionary.org/wiki/Wiktionary:Frequency_lists/Arabic>
- **What:** Links to 50K+ word frequency lists based on OpenSubtitles (CC BY-SA-4.0) and lists from Leeds University's Centre for Translation Studies (CC BY-2.5).
- **Quality:** Useful starting point, but no morphological or diacritical annotations.

### Buckwalter & Parkinson: A Frequency Dictionary of Arabic

- **Internet Archive:** <https://archive.org/details/AFrequencyDictionaryOfArabic>
- **Publisher:** Routledge (2011)
- **What:** 5,000 most frequent MSA words + dialect coverage. Originally a paid book, but full text available on Internet Archive.
- **Format:** Text (scanned/OCR)
- **Quality:** Excellent pedagogical resource. Frequency counts from a balanced corpus.
- **Note:** The digital scan may require OCR cleanup for machine use.

### Abu El-Khair Corpus Word Frequency Lists

- **URL:** <http://www.abuelkhair.net/index.php/en/arabic/abu-el-khair-corpus>
- **HuggingFace:** <https://huggingface.co/datasets/abuelkhair-corpus/arabic_billion_words>
- **What:** Top 30,000 word forms for each of 10 news domains + master frequency list, derived from 5M+ articles / 1.5B words.
- **Format:** Excel (.xlsx)
- **License:** Free for research

### Top 50,000 Arabic Words (modernstandardarabic.com)

- **URL:** <http://www.modernstandardarabic.com/top-50000-arabic-words/>
- **What:** 50,000 most common Arabic words ranked by frequency in media/publications, with English translations.
- **Note:** Premium/paid resource for the full list.

### ArTenTen (Lexical Computing / Sketch Engine)

- **URL:** <https://www.lexicalcomputing.com/arabic-word-frequency-list-for-download/>
- **What:** Frequency lists generated from ArTenTen, a large web corpus from Arab-country websites. Enriched with POS tags.
- **Note:** Free sample lists available; full access requires Sketch Engine subscription.

---

## 3. Machine-Readable Dictionaries

### Kaikki.org Arabic Dictionary (Wiktionary Extract)

- **URL:** <https://kaikki.org/dictionary/Arabic/index.html>
- **Raw data:** <https://kaikki.org/dictionary/rawdata.html>
- **What:** Complete machine-readable extraction of all Arabic entries from English Wiktionary.
- **Size:** 57,592 distinct words, 384.8 MB download
- **Format:** JSONL (one JSON object per line per sense)
- **Fields:** Word form, definitions/glosses, part of speech, etymology, pronunciations, translations, inflections, examples, etc.
- **License:** CC BY-SA 3.0 (inherited from Wiktionary)
- **Updates:** Weekly re-extraction from latest Wiktionary dumps
- **Tool:** Generated by [wiktextract](https://github.com/tatuylonen/wiktextract)
- **Quality:** Excellent breadth. Covers both MSA and dialects. Includes roots, verb forms, plurals. Variable depth per entry.

### Arramooz (Arabic Dictionary for Morphological Analysis)

- **GitHub:** <https://github.com/linuxscout/arramooz>
- **SourceForge:** <https://sourceforge.net/projects/arramooz/>
- **What:** Open-source Arabic dictionary designed for morphological analysis. Covers verbs, nouns, and stop words. Derived from Ayaspell Arabic spellchecker.
- **Format:** Tab-separated text, SQL database, XML, StarDict, Python+SQLite
- **Python API:** <https://github.com/linuxscout/arramooz-pysqlite>
- **License:** GPL-2.0
- **Quality:** Good for NLP integration. Includes word frequency data (`data/nouns/wordfreq.csv`).

### Ejtaal / Arabic Almanac (Hans Wehr, Lane's Lexicon, etc.)

- **Web app:** <https://ejtaal.net/aa/>
- **GitHub:** <https://github.com/ejtaal/mr>
- **What:** Compilation of major Arabic-English dictionaries searchable by root: Hans Wehr (4th ed.), Lane's Lexicon, al-Mawrid, Hava, Hinds/Badawi (Egyptian). Page images with root-based lookup.
- **Format:** HTML/JavaScript app (downloadable as zip for offline use). Dictionary pages are scanned images, not structured data.
- **License:** Varies by dictionary. The web interface code is open source.
- **Quality:** Gold-standard dictionaries but stored as page images, not structured text. Best for human lookup, not programmatic extraction.

### ARALEX (Lexical Database for MSA)

- **Paper:** <https://link.springer.com/article/10.3758/BRM.42.2.481>
- **URL:** Historically at `www.mrc-cbu.cam.ac.uk:8081/aralex.online/login.jsp` (may be down)
- **What:** Lexical database based on a 40M word MSA corpus. Provides token/type frequencies for roots, word patterns, bigrams, trigrams.
- **License:** GNU-like license
- **Quality:** Excellent for psycholinguistic research. Useful frequency data organized by root patterns (wazn). Status of online availability uncertain.

### Buckwalter Arabic Morphological Analyzer (BAMA)

- **v1.0 (free):** <https://catalog.ldc.upenn.edu/LDC2002L49> (also on SourceForge as AraMorph: <https://sourceforge.net/projects/aramorph/>)
- **v2.0 (paid):** <https://catalog.ldc.upenn.edu/LDC2004L02>
- **SAMA v3.1 (paid):** <https://catalog.ldc.upenn.edu/LDC2010L01>
- **What:** The foundational Arabic morphological analyzer. Contains lexicons with ~83,000 entries of Arabic prefixes, suffixes, and stems. Perl + data files.
- **License:** v1.0 is GPL-2.0 (free). v2.0 and SAMA require LDC membership (paid).
- **Quality:** Industry standard. The underlying lexicon data is extremely valuable for root/pattern analysis.

---

## 4. Root-Word Databases

### Arabic Roots and Derivatives (SourceForge)

- **URL:** <https://sourceforge.net/projects/arabicrootsandderivatives/>
- **What:** Database of Arabic roots and their derivatives (voweled + unvoweled) with stems. Extracted from the classical dictionary "Taj al-Arus" (تاج العروس من جواهر القاموس).
- **Size:** 142,000+ records, 10,000+ roots
- **Format:** MySQL database dump (`KhorsiCorpus.sql.bz2`, 1.3 MB compressed)
- **License:** CC BY-SA 3.0
- **Quality:** Beta stage (last updated 2013). Classical Arabic focus. Very useful for root-to-derivative mapping.

### Root Words of Quran

- **URL:** <https://rootwordsofquran.com/>
- **What:** Every word in the Quran mapped to its root, with frequency counts.
- **Quality:** Complete for Quranic vocabulary. Limited to ~1,700 unique roots appearing in the Quran.

### Aratools Dictionary

- **URL:** <https://aratools.com/>
- **What:** Arabic-English dictionary with root-based lookup. Each entry includes English translation, word class, and Arabic root.
- **Format:** Web interface (no bulk download apparent)

### Living Arabic Project

- **URL:** <https://www.livingarabic.com/>
- **What:** Database searchable by Arabic word, English word, and Arabic root. Covers classical Arabic and dialects.
- **Format:** Web interface

---

## 5. Morphological Databases & Tools

### CAMeL Tools

- **GitHub:** <https://github.com/CAMeL-Lab/camel_tools>
- **Paper:** <https://aclanthology.org/2020.lrec-1.868/>
- **What:** Full Arabic NLP Python toolkit from NYU Abu Dhabi. Includes morphological analysis, disambiguation, POS tagging, NER, sentiment analysis, dialect identification, tokenization, diacritization.
- **Morphological DBs included:** `calima-msa-r13` (MSA) and `calima-egy-r13` (Egyptian Arabic)
- **License:** MIT
- **Requirements:** Python 3.8-3.12 (64-bit), Rust compiler
- **Quality:** State of the art. Actively maintained. The `calima-msa` database is effectively a modernized Buckwalter lexicon.

### KALIMAT Corpus

- **What:** 20,291 MSA documents from Omani newspaper Al Watan, with summaries, named entities, POS tags, and morphological analysis.
- **Quality:** Well-annotated MSA news corpus.

### OSIAN (Open Source International Arabic News)

- **Paper:** <https://aclanthology.org/W19-4619/>
- **What:** 3.5M articles, 37M sentences, ~1B tokens from 32 international Arabic news sources (CNN Arabic, Al Jazeera, DW, RT, etc.). Each word annotated with lemma and POS.
- **Format:** XML with metadata per article
- **License:** CC BY-NC 4.0
- **Access:** Via CLARIN infrastructure
- **Quality:** Large-scale, lemmatized, POS-tagged MSA corpus. Used for training AraBERT.

### Khaleej-2004 and Watan-2004 (Arabic Corpus on SourceForge)

- **URL:** <https://sourceforge.net/projects/arabiccorpus/>
- **What:** Khaleej-2004 (5,690 docs, 4 topics) + Watan-2004 (20,291 docs, 6 topics). Useful for text classification.
- **License:** Free for research

---

## 6. Diacritized (Vowelized) Corpora

### Tashkeela

- **SourceForge:** <https://sourceforge.net/projects/tashkeela/>
- **Kaggle:** <https://www.kaggle.com/datasets/linuxscout/tashkeela>
- **Paper:** <https://www.sciencedirect.com/science/article/pii/S2352340917300112>
- **What:** ~75 million fully vocalized Arabic words from 97 books. The largest freely available diacritized corpus.
- **Breakdown:** 98.85% classical/Islamic texts (from Shamela Library), 1.15% MSA (Al Jazeera, al-kalema.org, etc.)
- **Format:** Plain text and XML files organized by source
- **License:** Free / open
- **Quality:** Excellent for training diacritization models. Heavy classical bias. The MSA portion is small (~868K words) but still useful.

### Tashkeela Benchmark (Cleaned Subset)

- **GitHub:** <https://github.com/AliOsm/arabic-text-diacritization>
- **What:** Cleaned benchmark extracted from Tashkeela: 55K lines, ~2.3M words. Standard train/dev/test splits.
- **Format:** Text files
- **Quality:** Well-preprocessed. Standard benchmark for diacritization research.

### Tanzil Quran Text

- **URL:** <https://tanzil.net/download/>
- **What:** The complete Quran text in multiple formats, fully diacritized. Version 1.1 (Feb 2021).
- **Format:** Plain text, XML, SQL. Multiple transcription styles.
- **License:** Free (with attribution)
- **Quality:** Gold-standard diacritization. ~77K words. Classical Arabic, not MSA.

### Sadeed Tashkeela (HuggingFace)

- **HuggingFace:** <https://huggingface.co/datasets/Misraj/Sadeed_Tashkeela>
- **What:** A curated diacritized Arabic dataset on HuggingFace.

---

## 7. Parallel Corpora (Arabic-English)

### United Nations Parallel Corpus (UNPC) v1.0

- **Official download:** <https://www.un.org/dgacm/en/content/uncorpus/download>
- **OPUS mirror:** <https://opus.nlpl.eu/UNPC/>
- **Paper:** Ziemski et al. (2016), LREC
- **What:** Manually translated UN documents (1990-2014) for all 6 official UN languages.
- **Size:** ~20 million Arabic-English sentence pairs
- **License:** Free with attribution to the United Nations
- **Quality:** Very high quality (professional human translation). Formal/legal register.

### OPUS Collection

- **URL:** <https://opus.nlpl.eu/>
- **What:** The largest collection of freely available parallel corpora. Key Arabic-English resources include:
  - **OpenSubtitles:** ~30M+ Arabic-English sentence pairs from movie/TV subtitles. Informal register.
  - **MultiUN:** ~20M sentence pairs from UN documents.
  - **CCAligned / CCMatrix:** Web-crawled parallel data using LASER embeddings. Very large but noisier.
  - **WikiMatrix:** Parallel sentences mined from Wikipedia.
  - **Tatoeba:** Small but high-quality sentence pairs.
  - **QED:** Educational video subtitles.
- **Format:** Various (TMX, Moses format, JSONL). Downloadable via OpusTools Python package.
- **License:** Varies by sub-corpus (mostly CC or public domain)
- **Quality:** Ranges from excellent (UN, Tatoeba) to noisy (web-crawled).

### MADAR Parallel Corpus

- **Download:** <https://camel.abudhabi.nyu.edu/madar-parallel-corpus/>
- **Internet Archive:** <https://archive.org/details/madar-dialectical-corpus-all-by-w>
- **What:** Parallel sentences covering dialects of 25 Arab cities + MSA + English + French. Travel domain.
- **License:** Non-commercial research use
- **Quality:** Unique resource for dialect-MSA-English comparison.

---

## 8. Treebanks

### UD Arabic-PADT (Universal Dependencies)

- **GitHub:** <https://github.com/UniversalDependencies/UD_Arabic-PADT>
- **UD page:** <https://universaldependencies.org/treebanks/ar_padt/index.html>
- **What:** Based on the Prague Arabic Dependency Treebank. 7,664 sentences, 282,384 tokens. Newswire domain. Full morphological and syntactic annotation in UD format.
- **Format:** CoNLL-U
- **License:** CC BY-NC-SA 3.0
- **Quality:** High. Standard UD annotations. Freely downloadable. The most accessible Arabic treebank.

### UD Arabic-NYUAD

- **GitHub:** <https://github.com/UniversalDependencies/UD_Arabic-NYUAD>
- **What:** Based on the Penn Arabic Treebank. Annotations released without underlying text due to LDC licensing.
- **Note:** Requires separate purchase of PATB data from LDC to reconstruct full treebank.

### UD Arabic-PUD

- **GitHub:** <https://github.com/UniversalDependencies/UD_Arabic-PUD>
- **What:** Parallel Universal Dependencies treebank. 1,000 sentences translated from English.
- **License:** CC BY-SA 4.0

### Penn Arabic Treebank (PATB) -- PAID

- **LDC:** <https://catalog.ldc.upenn.edu/LDC2005T20> (Part 3), and other parts
- **What:** The gold standard Arabic treebank. Multiple parts covering newswire. Full constituency parse trees + morphological annotation.
- **License:** LDC membership required (paid)
- **Quality:** Highest quality. Used extensively in Arabic NLP research.

### Prague Arabic Dependency Treebank (PADT) 1.0

- **Download:** <http://hdl.handle.net/11858/00-097C-0000-0001-4872-3>
- **LDC:** <https://catalog.ldc.upenn.edu/LDC2004T23>
- **What:** ~212,500 tokens of MSA newswire with multi-level annotation (morphology, analytical syntax, tectogrammatical).
- **License:** Free via LINDAT/CLARIN repository
- **Quality:** Very high. The basis for UD Arabic-PADT.

---

## 9. Large MSA Text Corpora

### Abu El-Khair Corpus (1.5 Billion Words)

- **Paper:** <https://arxiv.org/abs/1611.04033>
- **HuggingFace:** <https://huggingface.co/datasets/abuelkhair-corpus/arabic_billion_words>
- **What:** 5M+ newspaper articles from 10 major Arabic news sources across 8 countries, collected over 14 years.
- **Size:** 1.5 billion words, ~3M unique word forms
- **Format:** UTF-8 and Windows CP-1256, marked up in SGML and XML
- **License:** Free for research
- **Quality:** The largest freely available MSA corpus. Good variety across countries and time periods.

### OSIAN Corpus

- **Paper:** <https://aclanthology.org/W19-4619/>
- **Size:** ~1 billion tokens, 3.5M articles, 37M sentences
- **License:** CC BY-NC 4.0
- (See Section 5 for details)

### Hindawi Foundation Arabic E-Book Corpus

- **URL:** <https://researchdata.se/en/catalogue/dataset/2024-145>
- **What:** 1,745 books (81.5M words) published by Hindawi Foundation (2008-2024). Covers non-fiction, novels, children's literature, poetry, plays.
- **License:** Freely available (Hindawi publishes all content openly)
- **Quality:** High-quality edited literary and non-fiction MSA. Excellent for building diverse reading corpora.

### Shamela Corpus

- **Paper:** <https://arxiv.org/abs/1612.08989>
- **KITAB download (Zenodo):** See KITAB project: <https://kitab-project.org/First-Open-Access-Release-of-Our-Arabic-Corpus/>
- **What:** ~1 billion words across 14 centuries of Arabic texts (6,000+ texts). Drawn from Al-Maktaba Al-Shamela.
- **Size:** KITAB release: 1,859 authors, 4,288 titles, 755M words
- **License:** Open access (via Zenodo)
- **Quality:** Primarily classical Arabic. Cleaned and processed with morphological analyzer. Some texts are diacritized.

### Arabic Gigaword -- PAID

- **LDC:** <https://catalog.ldc.upenn.edu/LDC2011T11> (5th edition)
- **What:** Comprehensive archive of Arabic newswire text. Multiple editions.
- **License:** LDC membership required
- **Quality:** Gold standard for large-scale MSA news text.

### TALAA Corpus

- **What:** Free general Arabic corpus from daily newspaper websites. 14M+ words, 57,827 articles.
- **License:** Free

---

## 10. Graded / Readability-Classified Resources

### Arabic Vocabulary Profile (AVP) -- A1 CEFR

- **URL:** <https://lailafamiliar.github.io/A1-AVP-dataset/>
- **Authors:** Familiar, L.; Atanassova, G.; Soliman, R. (2025)
- **What:** 1,625 lexical items across 11 grammatical categories (nouns, verbs, adjectives, adverbs, particles, pronouns, etc.) with English translations, validated by 71 expert teachers of Arabic as a second language.
- **CEFR coverage:** A1 only (A2 in progress)
- **Format:** Interactive web app with embedded data (extractable from page source)
- **Quality:** The only rigorously validated CEFR-profiled Arabic vocabulary list available.

### KELLY Project Arabic List (with CEFR levels)

- (See Section 2 above)
- **Includes:** CEFR level annotations (A1-C2) for each word
- **Quality:** Corpus-based assignment of CEFR levels using pedagogical criteria

### BAREC (Balanced Arabic Readability Evaluation Corpus)

- **Paper:** <https://arxiv.org/abs/2502.13520>
- **What:** 69,441 sentences (1M+ words) annotated across 19 readability levels (kindergarten to postgraduate). The largest Arabic readability corpus.
- **Annotation:** Manually annotated at sentence level. High inter-annotator agreement (81.8% QWK).
- **Levels:** 19 fine-grained levels that map to 7, 5, and 3-level collapsed versions. Aligned to school grades and Arabi21 guidelines.
- **Quality:** State of the art. Published 2025.

### SAMER Arabic Text Simplification Corpus

- **Paper:** <https://aclanthology.org/2024.lrec-main.1398/>
- **What:** First manually annotated Arabic parallel corpus for text simplification. 159K words from 15 Arabic fiction novels (mostly 1865-1955). Includes readability level annotations at document and word levels, plus two simplified parallel versions targeting different learner levels.
- **License:** Publicly available
- **Quality:** Unique resource combining readability assessment with actual simplified text versions.

### DARES 2.0

- **What:** Concept-based readability training dataset for Saudi educational texts, grades 1-12.
- **Quality:** Curriculum-aligned. Good for educational applications.

### Arabi21 Framework

- **What:** The Arab Thought Foundation funded the leveling of 9,000+ children's books. The Arabi21 framework provides Arabic-specific CEFR-like level descriptors.
- **Note:** Framework documentation exists; not clear how much of the actual leveled book data is freely downloadable.

---

## 11. Semantic Resources (WordNet / Ontology)

### Arabic Ontology (Birzeit University)

- **Portal:** <https://ontology.birzeit.edu>
- **Paper:** <https://arxiv.org/abs/2205.09664>
- **What:** A formal Arabic wordnet with ontologically clean content. ~1,800 well-investigated concepts + 16,000 partially validated concepts. 150 Arabic-multilingual lexicons.
- **Mappings:** Fully mapped to Princeton WordNet, Wikidata
- **Access:** Searchable web portal. Data availability for bulk download unclear -- contact SinaLab.
- **Quality:** Ontologically rigorous. Best Arabic WordNet available.

### Arabic WordNet (SourceForge)

- **URL:** <https://sourceforge.net/projects/awnbrowser/>
- **What:** Multi-lingual concept dictionary mapped to Princeton WordNet v2.0. Supports Arabic-English search.
- **License:** Free download
- **Quality:** Older project. Less actively maintained than Birzeit's Arabic Ontology.

### SinaLab NLP Resources

- **URL:** <https://sina.birzeit.edu/resources/>
- **What:** Collection of Arabic NLP resources from Birzeit University including the Arabic Ontology, corpora, and tools.

---

## 12. Quranic & Classical Arabic Corpora

### Quranic Arabic Corpus

- **URL:** <https://corpus.quran.com/>
- **Download:** <https://corpus.quran.com/download/>
- **What:** Complete morphological annotation of all 77,430 words in the Quran. Each word tagged with part of speech and multiple morphological features. Also includes syntactic treebank and semantic ontology.
- **Format:** Downloadable data (requires email for access)
- **License:** GNU General Public License
- **Quality:** Gold standard. Every word manually annotated. Invaluable for studying Arabic morphology.

### Quran Morphology (GitHub)

- **GitHub:** <https://github.com/mustafa0x/quran-morphology>
- **What:** Morphological data from the Quranic Arabic Corpus in a more accessible format.

### Tanzil Quran Text

- (See Section 6)

### Shamela Library / KITAB Corpus

- (See Section 9)
- **Note:** The Shamela library itself (<https://shamela.ws/>) contains 10,000+ classical Arabic texts browsable online.

---

## 13. Diacritization & Morphological Tools (Software)

These are NLP tools (not datasets), but they contain or depend on valuable lexical databases and can generate annotated data.

### CAMeL Tools

- (See Section 5)
- **Diacritization:** Includes diacritization module for MSA and Egyptian

### Farasa

- **URL:** <https://farasa.qcri.org/>
- **Python wrapper:** <https://github.com/MagedSaeed/farasapy>
- **What:** Fast Arabic text processing toolkit from QCRI. Segmentation, lemmatization, POS tagging, diacritization, dependency parsing, NER, spell-checking.
- **License:** Free for academic/research use. RESTful API available.
- **Quality:** State of the art in Arabic segmentation. Diacritization module available.

### Mishkal

- **GitHub:** <https://github.com/linuxscout/mishkal>
- **PyPI:** <https://pypi.org/project/mishkal/>
- **What:** Open-source Arabic text diacritizer (rule-based). Uses Arramooz dictionary and Qalsadi morphological analyzer. Console, desktop, and web interfaces.
- **License:** Open source (GPL)
- **Quality:** Rule-based, so less accurate than neural approaches (DER ~13.78%), but transparent and customizable.

### Tashaphyne (Arabic Light Stemmer)

- **GitHub:** <https://github.com/linuxscout/tashaphyne>
- **PyPI:** Available on PyPI
- **What:** Arabic light stemmer and segmentor. Supports prefix/suffix removal, root extraction. Customizable affix lists.
- **License:** Open source
- **Quality:** Good for lightweight stemming without requiring full morphological analysis.

### Shakkala (Deep Learning Diacritizer)

- **What:** Character-level deep learning diacritization system using bidirectional LSTM.
- **Quality:** DER ~2.88%, WER ~6.37% -- significantly better than rule-based systems.

### Stanza (Stanford NLP)

- **URL:** <https://stanfordnlp.github.io/stanza/>
- **What:** Stanford's Python NLP library with Arabic support. Tokenization, POS tagging, lemmatization, dependency parsing.
- **License:** Apache 2.0

---

## 14. Dialect Resources

### MADAR Parallel Corpus

- (See Section 7)
- **Covers:** 25 city dialects + MSA + English + French

### NADI Shared Tasks

- **URL:** <https://nadi.dlnlp.ai/2025/>
- **What:** Annual shared task for Arabic dialect identification and processing. Releases dialect-labeled datasets each year.

### Palm Dataset

- **HuggingFace:** <https://huggingface.co/datasets/UBC-NLP/palm>
- **What:** 10 Arabic dialects + MSA, built by 44 native speakers from 15 countries.

### AraDiCE

- **HuggingFace:** <https://huggingface.co/datasets/QCRI/AraDiCE>
- **What:** Arabic dialect capabilities benchmark. Accepted at COLING 2025.

---

## 15. Wiktionary & Wikipedia Derived Data

### Kaikki.org / Wiktextract

- (See Section 3 for full details)
- **Arabic entries:** 57,592 distinct words
- **Format:** JSONL, 384.8 MB
- **How to use:** Download JSONL, parse with any JSON library. Each entry contains definitions, POS, etymology, inflections, translations.

### Arabic Wikipedia Dumps

- **Wikimedia downloads:** <https://dumps.wikimedia.org/arwiki/>
- **GitHub tool:** <https://github.com/leilaouahrani/ArabicCGW-Dump> (Arabic Corpus Generator from Wikipedia)
- **GitHub tool:** <https://github.com/motazsaad/arwikiExtracts> (Arabic Wikipedia text extracts)
- **Kaggle:** <https://www.kaggle.com/datasets/abedkhooli/arabic-wiki-data-dump-2018>
- **Gensim:** `gensim.corpora.wikicorpus` can process Arabic Wikipedia dumps directly
- **What:** Arabic Wikipedia contains 1M+ articles. Can be processed into plain text corpus.
- **Quality:** Good MSA with some dialect mixing. Useful for building frequency lists, training language models, etc.

### Wiktionary Frequency Lists

- (See Section 2)

---

## Summary Table: Quick Reference

| Resource | Type | Size | Free? | Format | MSA? |
|----------|------|------|-------|--------|------|
| CAMeL Frequency Lists | Frequency | 11.4M MSA types | Yes | TSV | Yes |
| KELLY Arabic | Frequency + CEFR | ~9K words | Yes | Excel | Yes |
| Kaikki.org Arabic | Dictionary | 57.6K words | Yes | JSONL | Yes |
| Arramooz | Dictionary | Verbs+Nouns+Stops | Yes | SQL/XML/TSV | Yes |
| Buckwalter v1 | Morph. Analyzer | 83K entries | Yes (GPL) | Perl+data | Yes |
| Arabic Roots & Derivs | Root DB | 142K records, 10K roots | Yes (CC) | MySQL | Classical |
| Tashkeela | Diacritized | 75M words | Yes | Text/XML | Mostly Classical |
| Tanzil Quran | Diacritized | 77K words | Yes | Text/XML/SQL | Classical |
| UNPC | Parallel | 20M sent. pairs | Yes | Various | Yes |
| OPUS OpenSubtitles | Parallel | 30M+ sent. pairs | Yes | Various | Mixed |
| UD Arabic-PADT | Treebank | 282K tokens | Yes (CC) | CoNLL-U | Yes |
| Abu El-Khair | Text corpus | 1.5B words | Yes | SGML/XML | Yes |
| OSIAN | Text corpus | 1B tokens | CC BY-NC | XML | Yes |
| Hindawi E-Books | Text corpus | 81.5M words | Yes | Text | Yes |
| Shamela/KITAB | Text corpus | 755M words | Yes | Various | Classical |
| Quranic Corpus | Morphological | 77K words | Yes (GNU) | Various | Classical |
| AVP A1 | CEFR vocab | 1,625 items | Yes | Web/JS | Yes |
| BAREC | Readability | 69K sentences | TBD | Text | Yes |
| SAMER | Simplification | 159K words | Yes | Text | Yes |
| Arabic Ontology | WordNet | 16K+ concepts | Web access | Various | Yes |
| CAMeL Tools | NLP toolkit | -- | Yes (MIT) | Python | Yes |
| Farasa | NLP toolkit | -- | Academic | Java/API | Yes |
| Masader | Catalogue | 600+ datasets | Yes | Web/HF | -- |

---

## Recommended Starting Points

For building an Arabic language learning application, the most immediately useful freely available resources are:

1. **Dictionary data:** Kaikki.org Wiktionary extract (57K entries in JSONL with definitions, POS, inflections)
2. **Frequency data:** CAMeL Lab MSA Frequency Lists (11.4M types from 17.3B tokens)
3. **Root mapping:** Arabic Roots and Derivatives on SourceForge (142K entries, 10K roots)
4. **Morphological analysis:** CAMeL Tools (Python, MIT license, includes MSA lexicon)
5. **Diacritization training data:** Tashkeela corpus (75M diacritized words)
6. **CEFR vocabulary:** KELLY Arabic word list (frequency + CEFR levels) + AVP A1 dataset
7. **Parallel text:** UN Parallel Corpus (20M sentence pairs, high quality)
8. **Treebank/syntax:** UD Arabic-PADT (282K tokens, free, CoNLL-U format)
9. **Readability levels:** BAREC corpus (69K sentences across 19 difficulty levels)
10. **Literary texts:** Hindawi E-Book Corpus (81.5M words across genres)

---

## Notes on LDC Resources (Paid)

The Linguistic Data Consortium (LDC) at University of Pennsylvania hosts many high-quality Arabic resources that require membership fees:

- **Arabic Gigaword** (5th ed.) -- Large-scale newswire corpus
- **Penn Arabic Treebank** (Parts 1-3) -- Gold-standard syntactic annotation
- **Buckwalter Morphological Analyzer v2.0 / SAMA v3.1** -- Enhanced morphological lexicons
- **Arabic Broadcast News/Conversations** -- Speech corpora

LDC membership costs vary; some universities have institutional access. For free alternatives, the resources listed above cover most of the same ground.
