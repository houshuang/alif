# Arabic Learning App — Research Summary

Research conducted 2026-02-08. Five detailed reports are linked below.

## Reports

| File | Contents |
|------|----------|
| [arabic-morphology-tools.md](arabic-morphology-tools.md) | Root extraction, lemmatization, conjugation, stemming libraries |
| [arabic-datasets-corpora.md](arabic-datasets-corpora.md) | Word frequency lists, dictionaries, root databases, treebanks, corpora |
| [arabic-apis-services.md](arabic-apis-services.md) | Free APIs for morphology, diacritization, TTS, translation, NLP |
| [arabic-diacritization.md](arabic-diacritization.md) | Tashkeel tools, deep learning models, accuracy benchmarks |
| [arabic-learning-architecture.md](arabic-learning-architecture.md) | Architecture patterns, data models, LLM+tools pipeline, roadmap |

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
