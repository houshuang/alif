# Alif — Research Index

## Algorithm Redesign (2026-02-12) — START HERE

After OCR import of ~100 textbook pages caused accuracy to crash from 78% to 25%, a comprehensive research effort was conducted to redesign the learning algorithm. **Read these files in this order:**

| # | File | What It Contains |
|---|------|-----------------|
| 1 | [learner-profile-2026-02-12.md](learner-profile-2026-02-12.md) | **User interview**: goals, motivations, learning style, trust breakers, constraints. The "why" behind all design decisions. |
| 2 | [learning-algorithm-redesign-2026-02-12.md](learning-algorithm-redesign-2026-02-12.md) | **Original plan**: production data diagnosis, initial literature review, proposed algorithm changes (8 sections), implementation phases (7 phases), deep research questions. |
| 3 | [deep-research-compilation-2026-02-12.md](deep-research-compilation-2026-02-12.md) | **Deep research compilation** (8 parallel agents): FSRS-6 internals, cognitive science, Arabic-specific learning, session design, sentence-centric SRS, leech management, N-of-1 experimental design, codebase change points. Includes synthesized algorithm proposal and master reference list. |
| 4 | [experiment-log.md](experiment-log.md) | **Running experiment log**: entry for 2026-02-12 has root cause analysis, hypotheses H13-H16, and deep research summary. |

### Key Decisions Already Made (from user interview)
- **Sentences always** — never isolated word flashcards
- **Full automation** — algorithm decides everything, user just engages honestly
- **Reading focus only** — listening is deferred
- **North star metric**: genuinely known words growing week over week (not FSRS-inflated)
- **Motivational engine**: Story import → targeted practice → fluent reading ("the magic moment")
- **Variable sessions**: 3-30 cards, must front-load highest-impact items

### Proposed Three-Phase Word Lifecycle
```
ENCOUNTERED → ACQUIRING (Leitner 3-box: 4h→1d→3d) → FSRS-6 scheduling
```

### Top Research Findings Informing Design
1. FSRS has no native acquisition phase — all commercial apps add one
2. 8-12 meaningful encounters needed for stable vocabulary
3. Sleep consolidation mandatory — first review must be next day
4. Semantic clustering (root siblings together) IMPEDES learning
5. 85% session accuracy optimizes both learning and motivation
6. 3 within-session retrievals is the sweet spot
7. Self-assessment unreliable — word-tapping is the critical corrective
8. Failed retrieval ("no_idea") has genuine learning value

---

## Cross-Project Learning Science Research

| File | Contents |
|------|----------|
| [~/src/research/notes/alif-vocabulary-coverage-research.md](../../research/notes/alif-vocabulary-coverage-research.md) | **Vocabulary coverage thresholds and pre-loading deep dive** (March 2026): Laufer 1989, Hu & Nation 2000, Nation 2006, Schmitt et al. 2011, Kremmel et al. 2023 replication, Boudelaa & Marslen-Wilson 2013 (Arabic psycholinguistics). Key finding: the 98% figure was **extrapolated via regression** (n=66), not directly tested; Schmitt et al. (n=661) found a linear relationship with no threshold. Three distinct thresholds (95% = minimal comprehension, 98% = incidental acquisition). Type vs. token distinction: sort pre-loading queue by **token frequency within the specific target text**. Arabic root-family partial credit model: L2 learners rely on roots 87.5% of time; ~50-70% partial access to warm unknowns. 7 concrete recommendations: Reading Readiness score, cold-unknown-first pre-loading, 7-10 items per session, don't chase 100%. |
| [~/src/research/notes/bjork-desirable-difficulties-srs-research.md](../../research/notes/bjork-desirable-difficulties-srs-research.md) | **Bjork Desirable Difficulties deep dive** (March 2026): Read actual papers — Bjork & Bjork (2020), (2014), "Forgetting as a Friend", Karpicke & Roediger 2008 *Science*, Richland et al. 2009 pretesting paper. Storage vs. retrieval strength mechanics. Critical finding: Wozniak's empirical data puts peak acquisition at **80% retention (20% forgetting)**, not 90% — FSRS default may be too conservative. Pretesting effect d=1.1, testing effect d=4.03 (dropping items reduces 1-week retention 80%→33%), interleaving 63% vs 20%. 10 specific Alif improvements: two-tier retention target, formal pretesting before Story/Goal Mode, root-interleaving within sessions, warm-up formalization, progressive tashkeel fading, collateral credit guard. |
| [~/src/research/notes/atomic-deep-analogue-digital-research.md](../../research/notes/atomic-deep-analogue-digital-research.md) | **Atomic↔Deep / Analogue↔Digital framework** (March 2026): Comprehensive literature review on the micro/deep cycle from the Alif blog post. Covers Bjork desirable difficulties + New Theory of Disuse (storage vs. retrieval strength), Schmidt Noticing Hypothesis (priming mechanism for pre-study), Laufer & Hulstijn Involvement Load Hypothesis (depth of processing per learning event), Nation's 98% vocabulary coverage threshold, Pellicer-Sánchez 2021 pre-reading vocabulary instruction, tools landscape (jpdb.io pre-loading, Refold/AJATT methodology, LingQ/Migaku sentence mining). Includes "Implications for Alif" sections growing as 5 deep-dive subagents complete research. **Read this for SLA foundations of Alif's prestudy and reading-session design.** |

---

## Earlier Research (2026-02-08)

Initial technology research conducted before app development.

| File | Contents |
|------|----------|
| [arabic-morphology-tools.md](arabic-morphology-tools.md) | Root extraction, lemmatization, conjugation, stemming libraries |
| [arabic-datasets-corpora.md](arabic-datasets-corpora.md) | Word frequency lists, dictionaries, root databases, treebanks, corpora |
| [arabic-apis-services.md](arabic-apis-services.md) | Free APIs for morphology, diacritization, TTS, translation, NLP |
| [arabic-diacritization.md](arabic-diacritization.md) | Tashkeel tools, deep learning models, accuracy benchmarks |
| [arabic-learning-architecture.md](arabic-learning-architecture.md) | Architecture patterns, data models, LLM+tools pipeline, roadmap |

## Corpus & Sentence Research

| File | Contents |
|------|----------|
| [corpus-vs-llm-feasibility-2026-02-21.md](corpus-vs-llm-feasibility-2026-02-21.md) | **Feasibility analysis**: corpus selection vs LLM generation. Architecture, cost projections, vocabulary growth hit rates, phased implementation plan. The strategic decision doc. |
| [arabic-sentence-corpora-2026-02-21.md](arabic-sentence-corpora-2026-02-21.md) | Survey of 17 Arabic corpora for sentence mining: sizes, licenses, diacritics, translations, integration recommendations |

## Typography & Reading

| File | Contents |
|------|----------|
| [arabic-ligatures-reference.html](arabic-ligatures-reference.html) | Interactive reference: all 28 letter forms, mandatory/special ligatures, font comparison (Amiri/Scheherazade/Noto), tricky letter pairs with IPA, live font preview |

## Analysis Reports

| File | Contents |
|------|----------|
| [analysis-2026-02-09.md](analysis-2026-02-09.md) | Early learning data analysis |
| [analysis-2026-02-10.md](analysis-2026-02-10.md) | OCR import analysis |
| [analysis-2026-02-11.md](analysis-2026-02-11.md) | Post-OCR accuracy analysis |
| [vocabulary-acquisition-research.md](vocabulary-acquisition-research.md) | General vocabulary acquisition research |
| [cognitive-load-language-learning.md](cognitive-load-language-learning.md) | Cognitive load in language learning |
| [algorithm-implications.md](algorithm-implications.md) | Earlier algorithm design notes |
| [variant-detection-spec.md](variant-detection-spec.md) | LLM-confirmed variant detection specification |

---

## Key Recommendations

### Core NLP Stack (all MIT-licensed, Python)

| Need | Tool | Why |
|------|------|-----|
| Morphological analysis, lemmatization, root extraction | **CAMeL Tools** (NYU Abu Dhabi) | Most comprehensive, MIT license, 100K+ lemmas, actively maintained |
| High-accuracy lemmatization | **SinaTools / Alma** | 90% F1, fastest (32K tok/s), MIT |
| Diacritization | **CATT** (Apache 2.0) | Best open-source accuracy, pip-installable, ONNX-exportable |
| Verb conjugation | **Qutrub** | Only mature open-source conjugator (GPL) |
| Spaced repetition | **py-fsrs** | Modern FSRS algorithm, better than SM-2 |

### Key Datasets (all free)

| Need | Dataset | Details |
|------|---------|---------|
| Word frequencies | **CAMeL Lab MSA Frequency Lists** | 11.4M types from 17.3B tokens |
| CEFR-level vocabulary | **KELLY Project** | Arabic word list with CEFR annotations |
| Root→derivatives mapping | **Arabic Roots & Derivatives** (SourceForge) | 142K records, 10K+ roots, CC BY-SA |
| Machine-readable dictionary | **Kaikki.org Wiktionary** | 57K Arabic entries, structured JSONL, weekly updates |
| NLP-oriented dictionary | **Arramooz** | SQL/XML/TSV formats, open source |
| Diacritized text | **Tashkeela** | 75M fully vowelized words |
| Sentence difficulty | **BAREC** | 69K sentences across 19 readability levels |
| Readability-scored vocab | **SAMER** | 40K lemmas with 5-level readability |
| Parallel sentences | **UN Parallel Corpus** | 20M Arabic-English pairs |
| Sentence pairs | **Tatoeba** | 8.5M pairs, CC BY 2.0 |

### Architecture: LLM + Deterministic Tools

The recommended pattern is **generate-then-validate**:

```
User knows words {W} →
  LLM generates sentence with target word T →
    Deterministic validator:
      1. CAMeL Tools: tokenize + lemmatize every word
      2. Check each lemma against user's known set {W}
      3. Verify exactly 1 unknown word (the target T)
    → If valid: present to user
    → If invalid: retry with feedback to LLM (max 3 attempts)
```

### Data Model (simplified)

Track knowledge at three levels:
- **Root level** — the 3/4-letter consonant root (e.g. ك-ت-ب)
- **Lemma level** — base dictionary form (e.g. كَتَبَ, كِتَاب, كَاتِب)
- **Form level** — specific conjugation/inflection (e.g. يَكْتُبُونَ)

Use FSRS scheduling independently at each level. Root mastery = aggregate of its lemmas.

### Suggested Tech Stack

- **Backend**: Python / FastAPI
- **Database**: PostgreSQL + Redis (for fast known-word lookups)
- **NLP**: CAMeL Tools + CATT + Qutrub
- **LLM**: Claude API (sentence generation, explanations)
- **Frontend**: React/TypeScript (or similar)

### Free API Highlights

- **Farasa REST API** — free morphology/diacritization (research use)
- **Azure Translator** — 2M chars/month free
- **Google Cloud TTS** — 1M chars/month free (MSA voices)
- **LibreTranslate** — self-hostable, unlimited
- **HuggingFace Inference** — free tier for AraBERT/CAMeLBERT models

### Risk: Diacritization Accuracy

Published benchmarks are inflated by data leakage (34.6% of a standard test set leaks into training data). Always test on your own MSA content. For educational materials, pre-diacritize and have human review.
