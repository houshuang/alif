# Arabic Diacritization (Tashkeel) - Comprehensive Research

> Research compiled February 2026 for language learning app development.
> Focus: Modern Standard Arabic (MSA / fusha) diacritization tools and models.

## Table of Contents

1. [Background](#background)
2. [Evaluation Metrics](#evaluation-metrics)
3. [Accuracy Benchmark Summary](#accuracy-benchmark-summary)
4. [Deep Learning Models](#deep-learning-models)
5. [Rule-Based and Hybrid Systems](#rule-based-and-hybrid-systems)
6. [Production-Ready Libraries](#production-ready-libraries)
7. [LLM-Based Diacritization](#llm-based-diacritization)
8. [Partial / Selective Diacritization](#partial--selective-diacritization)
9. [Evaluation and Verification Tools](#evaluation-and-verification-tools)
10. [Datasets and Corpora](#datasets-and-corpora)
11. [API Services](#api-services)
12. [Practical Recommendations for a Language Learning App](#practical-recommendations-for-a-language-learning-app)

---

## Background

Arabic text is typically written without short vowels (harakat). Diacritization (tashkeel) is the process of adding these marks back:

| Mark | Name | Arabic | Transliteration |
|------|------|--------|-----------------|
| َ | Fatha | فَتْحَة | a |
| ُ | Damma | ضَمَّة | u |
| ِ | Kasra | كَسْرَة | i |
| ْ | Sukun | سُكُون | (no vowel) |
| ّ | Shadda | شَدَّة | (gemination) |
| ً | Tanwin Fath | تَنْوِين فَتْح | -an |
| ٌ | Tanwin Damm | تَنْوِين ضَمّ | -un |
| ٍ | Tanwin Kasr | تَنْوِين كَسْر | -in |

For a language learning app, diacritization is critical because learners cannot correctly pronounce or understand words without vowel marks. Native speakers infer vowels from context, but learners need explicit marks.

---

## Evaluation Metrics

All systems are evaluated using two standard metrics:

- **DER (Diacritization Error Rate)**: Percentage of characters with incorrectly predicted diacritics. Lower is better.
- **WER (Word Error Rate)**: Percentage of words with at least one diacritization error. Lower is better.

Both metrics are reported in two modes:
- **With case ending (CE)**: Includes the final vowel of each word (i3rab), which is the hardest part.
- **Without case ending**: Excludes the last character's diacritic; this is easier and more practical for many applications.

The standard evaluation library is `diacritization-evaluation` on PyPI:
```bash
pip install diacritization-evaluation
```
Repository: https://github.com/almodhfer/diacritization_evaluation

---

## Accuracy Benchmark Summary

This table consolidates results across multiple published benchmarks. Numbers vary by dataset and evaluation protocol; treat as approximate guides. All values are percentages (lower is better).

### On the Tashkeela (Fadel) Test Set

| System | Type | DER (w/ CE) | WER (w/ CE) | DER (no CE) | WER (no CE) |
|--------|------|-------------|-------------|-------------|-------------|
| **SukounBERT.v2** | BERT-based | 0.92 | 1.91 | -- | -- |
| **Fine-Tashkeel (ByT5)** | Seq2Seq Transformer | 0.95 | 2.49 | -- | -- |
| **Sadeed (corrected)** | Decoder-only LM (1.5B) | 1.24 | 2.94 | 0.76 | 1.74 |
| **SUKOUN** | BERT-based | 1.16 | 3.34 | 0.96 | 1.96 |
| **D3 (Deep Diacritization)** | Hierarchical RNN | 1.83 | 5.34 | 1.48 | 3.11 |
| **Shakkala** | B-LSTM (Keras) | 2.88 | 6.37 | -- | -- |
| **Shakkelha** | RNN/FFNN ensemble | ~3.0 | ~7.0 | -- | -- |
| **CBHG** | Tacotron-encoder | ~3.5 | ~8.0 | -- | -- |
| **Mishkal** | Rule-based | 13.78 | 21.92 | -- | -- |
| **Farasa** | SVM + dictionary | 21.43 | 58.88 | -- | -- |
| **MADAMIRA** | Morphological | ~12 | ~30 | -- | -- |

### On the WikiNews Test Set

| System | DER (w/ CE) | WER (w/ CE) | DER (no CE) | WER (no CE) |
|--------|-------------|-------------|-------------|-------------|
| **CATT ED** | 6.07 | -- | 3.74 | -- |
| **CATT EO** | 5.43 | -- | 3.11 | -- |
| **FRRNN (Darwish)** | 3.7 | 6.0 | 0.9 | 2.9 |
| **Sadeed** | 5.25 | 14.64 | 3.11 | 8.44 |
| **CATT (Alasmary)** | 5.96 | 20.06 | 3.63 | 11.31 |

### On SadeedDiac-25 Benchmark (Mixed MSA + Classical)

| System | DER (w/ CE) | WER (w/ CE) | Hallucination Rate |
|--------|-------------|-------------|-------------------|
| **Claude 3.7 Sonnet** | 1.39 | 4.67 | 0.82% |
| **GPT-4** | 3.86 | 5.27 | 1.02% |
| **Sadeed** | 7.29 | 13.74 | 7.19% |
| **Aya-8B** | 25.63 | 47.49 | 5.78% |
| **ALLaM-7B** | 50.36 | 70.34 | 36.51% |

**Important caveat on benchmarks**: The Sadeed paper (2025) revealed that 34.6% of the Fadel test samples are fully present in common training sets, and 68.12% exhibit similarity > 0.5. This means many published DER/WER numbers on the Fadel benchmark are inflated by data leakage. The SadeedDiac-25 benchmark was created to address this.

---

## Deep Learning Models

### 1. CATT (Character-based Arabic Tashkeel Transformer)

**The current state-of-the-art open-source model for Arabic diacritization.**

- **Paper**: "CATT: Character-based Arabic Tashkeel Transformer" (ArabicNLP 2024)
- **Repository**: https://github.com/abjadai/catt
- **License**: Apache 2.0 (commercial use allowed)
- **Architecture**: Two variants:
  - Encoder-Only (EO): 6 layers, 512 dim, 16 heads -- faster inference
  - Encoder-Decoder (ED): 3+3 layers -- higher accuracy
- **Base**: Pretrained character-based BERT + Noisy-Student training
- **Installation**: `pip install catt-tashkeel`
- **ONNX export**: Supported via `export_to_onnx.py`

```python
from catt_tashkeel import CATTEncoderDecoder, CATTEncoderOnly

eo = CATTEncoderOnly()
ed = CATTEncoderDecoder()

text = "وقالت مجلة نيوزويك الامريكية في تقريرها"
print(eo.do_tashkeel(text, verbose=False))
print(ed.do_tashkeel_batch([text], verbose=False))
```

**Strengths**: Best open-source accuracy, Apache 2.0 license, pip-installable, ONNX support.
**Weaknesses**: Relatively new, smaller community. Performance on MSA text that differs from training data can vary.

---

### 2. Sadeed

- **Paper**: "Sadeed: Advancing Arabic Diacritization Through Small Language Model" (2025)
- **Repository**: https://github.com/misraj-ai/Sadeed
- **HuggingFace datasets**: https://huggingface.co/datasets/Misraj/Sadeed_Tashkeela
- **Architecture**: Fine-tuned decoder-only LM from Kuwain 1.5B
- **Model size**: ~1.5B parameters
- **Training**: 3 epochs on 53M words, 8x A100 GPUs
- **Benchmark**: Introduced SadeedDiac-25 (1,200 paragraphs, 50% MSA / 50% Classical)
- **License**: Not clearly stated in repository

**Reported accuracy** (Fadel corrected): DER 1.24%, WER 2.94% (with CE).

**Strengths**: Strong accuracy, introduced important new benchmark, addresses data leakage issues in prior work.
**Weaknesses**: Large model (1.5B params), model weights may not be publicly downloadable yet, heavy compute requirements for inference.

---

### 3. Fine-Tashkeel (ByT5-based)

- **Paper**: "Fine-Tashkeel: Finetuning Byte-Level Models for Accurate Arabic Text Diacritization" (2023)
- **HuggingFace**: https://huggingface.co/basharalrfooh/Fine-Tashkeel
- **Architecture**: ByT5 (byte-level T5 transformer, seq2seq)
- **License**: MIT
- **Training**: Fine-tuned on Tashkeela for 13,000 steps
- **Framework**: PyTorch / HuggingFace Transformers

```python
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

model_name = "basharalrfooh/Fine-Tashkeel"
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForSeq2SeqLM.from_pretrained(model_name)

text = "كيف الحال"
input_ids = tokenizer(text, return_tensors="pt").input_ids
outputs = model.generate(input_ids, max_new_tokens=128)
print(tokenizer.decode(outputs[0], skip_special_tokens=True))
```

**Reported accuracy**: DER 0.95%, WER 2.49% (on Tashkeela test set, likely with data leakage concerns).
**Strengths**: MIT license, on HuggingFace, simple API, handles unseen words at >76% accuracy.
**Weaknesses**: ByT5 models are slow for inference (byte-level processing), model size can be large, Tashkeela benchmark numbers may be inflated.

---

### 4. Deep Diacritization (D2/D3)

- **Paper**: "Deep Diacritization: Efficient Hierarchical Recurrence for Improved Arabic Diacritization" (WANLP 2020)
- **Repository**: https://github.com/BKHMSI/deep-diacritization
- **License**: AGPL-3.0
- **Architecture**: Two-level recurrence hierarchy (word + character level) with cross-level attention
- **D3 variant**: Accepts partially diacritized input as priors (unique feature)
- **Accuracy**: DER 1.83%, WER 5.34% (with CE, Fadel set)

**Strengths**: D3's partial diacritization support is unique and valuable for language learning (teacher can add some diacritics, model completes the rest). Relatively efficient architecture.
**Weaknesses**: AGPL license restricts commercial use without open-sourcing your app. Pretrained weights via Google Drive links.

---

### 5. Shakkala

- **Repository**: https://github.com/Barqawiz/Shakkala
- **License**: MIT (must credit "Shakkala" as base model)
- **Architecture**: RNN/LSTM, Keras/TensorFlow
- **Installation**: `pip install shakkala`
- **Framework**: TensorFlow 2.9.3

```python
from shakkala import Shakkala
sh = Shakkala(version=2)
input_int = sh.prepare_input(input_text)
model, graph = sh.get_model()
logits = model.predict(input_int)[0]
predicted_harakat = sh.logits_to_text(logits)
final_output = sh.get_final_text(input_text, predicted_harakat)
```

**Reported accuracy**: DER 2.88%, WER 6.37%.
**Strengths**: Mature project, pip-installable, MIT license, well-documented.
**Weaknesses**: Older architecture (LSTM), TensorFlow dependency, not state-of-the-art accuracy.

**PyTorch/ONNX ports available**:
- PyTorch: https://github.com/nipponjo/arabic-vocalization
- ONNX: https://github.com/nipponjo/arabic_vocalizer (MIT license)

```python
# ONNX version - very lightweight
from arabic_vocalizer import vocalize
result = vocalize(input_text, model='shakkala')
```

---

### 6. Shakkelha

- **Repository**: https://github.com/AliOsm/shakkelha
- **Paper**: "Neural Arabic Text Diacritization: State-of-the-Art Results" (EMNLP-IJCNLP 2019)
- **Architecture**: Multiple models (RNN, CRF-RNN, FFNN variants)
- **Also available as ONNX**: https://github.com/nipponjo/arabic_vocalizer

---

### 7. 2SDiac

- **Paper**: "Take the Hint: Improving Arabic Diacritization with Partially-Diacritized Text" (Interspeech 2023)
- **Architecture**: BiLSTM + self-attention with Guided Learning
- **Model size**: <5M parameters (very small)
- **Accuracy**: 3.3% WER (no CE), 31% relative improvement over prior work
- **Key innovation**: Accepts partial diacritics as hints, improving all predictions

**Strengths**: Tiny model (<5M params), fast inference, low latency, supports partial input diacritics.
**Weaknesses**: No public GitHub repository found. Published at Interspeech but code availability unclear.

---

### 8. CBHG / Arabic_Diacritization

- **Repository**: https://github.com/almodhfer/Arabic_Diacritization
- **Architecture**: 4 models (Baseline BiLSTM, Seq2Seq+Attention, Tacotron-based, CBHG)
- **Framework**: PyTorch
- **License**: Not specified
- **Best model**: CBHG achieves the best WER and DER

---

### 9. Hareef

- **Repository**: https://github.com/mush42/hareef
- **License**: MIT
- **Architecture**: Two models implemented:
  - **Sarf**: Deep GRU + transformer encoder layers
  - **CBHG**: Based on published CBHG architecture
- **Framework**: PyTorch Lightning
- **Features**: ONNX export, standardized evaluation, config-driven training
- **Requirements**: Python 3.10+

**Strengths**: Clean codebase, MIT license, ONNX export, used as basis for libtashkeel.

---

### 10. SukounBERT

- **Paper**: "BERT-Based Arabic Diacritization" (Expert Systems with Applications, 2024)
- **Architecture**: BERT-based, multi-phase approach
- **Accuracy**: DER 0.92%, WER 1.91% (morphological), DER 1.14%, WER 3.34% (syntactic)
- **Training data**: Sukoun Corpus -- 5.2M lines, 71M tokens
- **SukounBERT.v2**: >55% relative DER/WER reduction over leading models

**Critically**: Data and model are "available on request" only. Not fully open-source. No public GitHub repository found.

---

### 11. Ad-Dabit-Al-Lughawi (PTCAD)

- **Paper**: "Arabic Text Diacritization In The Age Of Transfer Learning" (2024)
- **Architecture**: Two-phase (pre-finetuning + finetuning) token classification on BERT
- **Accuracy**: 20-30% WER reduction over prior SOTA; outperforms GPT-4 on ATD tasks
- **Key innovation**: Pre-finetuning on Classical Arabic, POS tagging, segmentation as MLM tasks

**Status**: Published in Expert Systems with Applications journal. Code availability unclear.

---

## Rule-Based and Hybrid Systems

### Mishkal

- **Repository**: https://github.com/linuxscout/mishkal
- **PyPI**: `pip install mishkal` (v0.4.1, last updated July 2021)
- **License**: GPL-3.0
- **Approach**: Multi-stage rule-based pipeline:
  1. Morphological analysis (Qalsadi analyzer + Arramooz dictionary)
  2. Word frequency weighting
  3. Syntactic analysis (ArAnaSyn)
  4. Semantic analysis (Asmai)
  5. Probability-based selection
- **Accuracy**: DER 13.78%, WER 21.92% (Fadel benchmark)

```python
import mishkal.tashkeel
vocalizer = mishkal.tashkeel.TashkeelClass()
result = vocalizer.tashkeel("تطلع الشمس صباحا")
# Output: 'تَطْلُعُ الشَّمْسُ صَبَاحًا'
```

**Features**:
- `--reduced` flag for reduced/partial diacritization (adds only essential marks)
- `--strip` to remove all diacritics
- `--syntax` / `--semantic` to toggle analysis modules
- Web server mode, JSON API, GUI, console interfaces

**Strengths**: No ML model needed, no GPU, deterministic, supports reduced tashkeel mode, comprehensive Arabic morphology stack. Great for understanding *why* a diacritic was chosen.
**Weaknesses**: GPL-3.0 license (copyleft), significantly lower accuracy than neural models, not maintained actively.

---

### Farasa

- **Website**: https://farasa.qcri.org/
- **Developer**: Qatar Computing Research Institute (QCRI)
- **Approach**: SVM-ranking segmentation + dictionary lookups
- **Accuracy**: DER 21.43%, WER 58.88% (Fadel benchmark) -- poor for diacritization specifically
- **License**: Free API (registration required), not fully open-source
- **API**: RESTful, free, with Python/Java/JS/curl examples

**Strengths**: Very fast, comprehensive Arabic NLP toolkit (segmentation, NER, POS, etc.).
**Weaknesses**: Diacritization accuracy is notably lower than neural approaches. The diacritizer is not the strongest component. API-dependent.

---

### CAMeL Tools

- **Repository**: https://github.com/CAMeL-Lab/camel_tools
- **Developer**: CAMeL Lab, NYU Abu Dhabi
- **PyPI**: `pip install camel-tools`
- **License**: MIT
- **Approach**: Hybrid neuro-symbolic. Morphological analyzer generates candidate analyses, classifier ranks them.
- **Features**: Morphological analysis/generation, dialect ID, NER, sentiment, transliteration
- **Requirements**: Python 3.8-3.12, Rust compiler, CMake

```python
from camel_tools.disambig.mle import MLEDisambiguator

mle = MLEDisambiguator.pretrained()
sentence = 'الطفلان أكلا الطعام'.split()
disambig = mle.disambiguate(sentence)
diacritized = [d.analyses[0].analysis['diac'] for d in disambig]
print(' '.join(diacritized))
```

**Data download**: `camel_data -i light` (morphology only) or `camel_data -i all`

**Strengths**: MIT license, comprehensive NLP suite, morphological databases for MSA (calima-msa-r13) and Egyptian (calima-egy-r13), well-maintained, academic backing.
**Weaknesses**: Diacritization accuracy is not as strong as dedicated neural models. Complex installation (Rust compiler needed). Heavier than pure ML approaches.

---

### Pipeline-Diacritizer

- **Repository**: https://github.com/Hamza5/Pipeline-diacritizer
- **PyPI**: `pip install pipeline-diacritizer`
- **License**: MIT
- **Paper**: "Multi-components system for automatic Arabic diacritization" (ECIR 2020)
- **Approach**: Deep learning + rule-based + statistical corrections pipeline
- **Framework**: TensorFlow

---

## Production-Ready Libraries

### libtashkeel

- **Repository**: https://github.com/mush42/libtashkeel
- **License**: MIT
- **Language**: Rust core with Python, C/C++, WASM bindings
- **Model**: ONNX (derived from Hareef project, trained on MSA)

```python
from pylibtashkeel import tashkeel
result = tashkeel("إن روعة اللغة العربية لا تتبدى إلا لعشاقها")
# Output: 'إِنَّ رَوْعَةَ اللُّغَةِ الْعَرَبِيَّةِ لَا تَتَبَدَّى إِلَّا لِعُشَّاقِهَا'
```

**Installation** (requires Rust):
```bash
cd pylibtashkeel
python3 -m venv .venv && source .venv/bin/activate
pip install maturin && maturin build --release --strip
pip install ./target/wheels/pylibtashkeel*.whl
```

**Strengths**: MIT license, cross-platform (Rust/Python/C++/WASM), ONNX inference, used by Piper TTS for Arabic. Ideal for production deployment where you need a lightweight, compiled library.
**Weaknesses**: Requires Rust toolchain to build, no pip install (yet), accuracy not benchmarked publicly.

---

### arabic_vocalizer (ONNX)

- **Repository**: https://github.com/nipponjo/arabic_vocalizer
- **License**: MIT
- **Models**: Shakkala and Shakkelha ported to ONNX format
- **Installation**: `pip install git+https://github.com/nipponjo/arabic_vocalizer.git`

```python
from arabic_vocalizer import vocalize
result = vocalize("السلام عليكم", model='shakkala')
```

**Strengths**: Extremely simple API, ONNX (no TF/PyTorch needed), MIT license, lightweight.
**Weaknesses**: Uses older Shakkala/Shakkelha models, not state-of-the-art accuracy.

---

### Rababa

- **Repository**: https://github.com/interscript/rababa
- **License**: Open source (part of Interscript)
- **Languages**: Python and Ruby bindings
- **Supports**: Arabic and Hebrew diacritization
- **Architecture**: Neural network models with ONNX inference

**Strengths**: Supports both Arabic and Hebrew, Ruby bindings (unique), integrates with Interscript transliteration.

---

## LLM-Based Diacritization

### Evaluation of General-Purpose LLMs

Based on the Kentoseth evaluation (March 2025) and the Sadeed paper (2025):

| Model | Typical DER Range | Notes |
|-------|-------------------|-------|
| **Claude 3.7 Sonnet** | 1.4% | Best on SadeedDiac-25 benchmark |
| **Gemini 2.0 Pro** | 0-40% | Highly variable; excellent on some texts, poor on others |
| **GPT-4** | 3.9-20% | Moderate; prone to hallucination on some texts |
| **Mistral Saba** | 1.7-40% | Surprisingly good on classical texts |
| **LLama 3.3 70B** | 53-74% | Very poor; often worse than no diacritization |
| **Aya-8B** | 25.6% | Moderate open model |
| **ALLaM-7B** | 50.4% | Poor; high hallucination rate (36.5%) |

**Key findings**:
- LLMs are highly inconsistent across text types and genres
- Proprietary models (Claude, GPT-4) significantly outperform open LLMs
- Hallucination is a real problem: LLMs sometimes change words, not just add diacritics
- LLMs are too expensive and slow for production diacritization at scale
- Specialized models consistently outperform general LLMs for this task

---

## Partial / Selective Diacritization

For a language learning app, partial diacritization is valuable: you may want to diacritize only ambiguous words, or only vocabulary the learner is studying.

### Tools with Partial Diacritization Support

| Tool | Partial Mode | Details |
|------|-------------|---------|
| **Mishkal** | `--reduced` flag | Adds only essential marks, skipping obvious ones |
| **D3 (Deep Diacritization)** | Input priors | Accepts partially diacritized text and improves it |
| **2SDiac** | Guided Learning | Trained to leverage partial input diacritics at various masking levels |
| **CAMeL Tools** | Per-word control | Disambiguate individual words, giving you control over which to diacritize |

### Implementing Selective Diacritization

No existing tool does exactly "diacritize only ambiguous words" out of the box. Strategies:

1. **Full diacritization + selective display**: Diacritize everything, then strip marks from "easy" words in the UI. Use word frequency lists to determine which words a learner likely knows.

2. **CAMeL Tools morphological analysis**: Analyze each word to see how many valid diacritized forms exist. Words with only one form are unambiguous; show diacritics only on multi-form words.

3. **D3/2SDiac approach**: Pre-diacritize known words, let the model fill in the rest. The model uses the given diacritics as context clues.

4. **Mishkal reduced mode**: Use the `--reduced` flag which already implements a form of "minimal necessary" diacritization.

---

## Evaluation and Verification Tools

### diacritization-evaluation

- **PyPI**: `pip install diacritization-evaluation`
- **Repository**: https://github.com/almodhfer/diacritization_evaluation
- **Calculates**: DER and WER, with and without case endings

```python
from diacritization_evaluation import der, wer

# From text strings
der_score = der.calculate_der(reference_text, hypothesis_text)
wer_score = wer.calculate_wer(reference_text, hypothesis_text)

# Options: with_case_ending=True/False
```

### Comparison Approach

For a language learning app, you can build a verification pipeline:
1. Diacritize text with your primary model
2. Cross-check with a second model (e.g., primary=CATT, secondary=Mishkal)
3. Where they disagree, flag for human review or use a morphological analyzer to check validity
4. Use CAMeL Tools morphological analyzer to verify each diacritized form exists in the dictionary

---

## Datasets and Corpora

### Tashkeela Corpus

- **Source**: https://www.kaggle.com/datasets/linuxscout/tashkeela
- **Also**: https://sourceforge.net/projects/tashkeela/
- **Size**: 75.6M words (97 books from Shamela Library + web-crawled MSA)
- **Composition**: 98.85% classical Arabic (Islamic texts), 1.15% MSA
- **Download**: ~183 MB compressed
- **Creator**: Taha Zerrouki (linuxscout)

### Tashkeela Processed (Fadel Benchmark)

- **Repository**: https://github.com/AliOsm/arabic-text-diacritization
- **Split**: 50K train / 2.5K val / 2.5K test lines (~2.3M words total)
- **Selection**: Lines with >80% diacritic-to-character ratio
- **Warning**: 34.6% of test samples found in common training sets (data leakage)

### SadeedDiac-25

- **HuggingFace**: https://huggingface.co/datasets/Misraj/Sadeed_Tashkeela
- **Size**: 1,200 paragraphs (40-50 words each)
- **Composition**: 50% MSA + 50% Classical Arabic
- **Quality**: Expert-reviewed diacritization
- **Purpose**: Fairer evaluation across diverse genres and complexity

### Arabic Treebank (ATB-3)

- **Source**: LDC (Linguistic Data Consortium) -- requires license
- **Used by**: Many research systems for MSA diacritization
- **Quality**: High-quality linguist-annotated MSA text

### Sukoun Corpus

- **Size**: 5.2M lines, 71M tokens
- **Sources**: Classical Arabic, MSA, dictionaries, poetry, contextual sentences
- **Availability**: "On request" from authors

---

## API Services

| Service | Type | Cost | Notes |
|---------|------|------|-------|
| **Farasa API** | REST | Free (registration required) | https://farasa.qcri.org/ |
| **RDI Tashkeel** | REST + On-premise | Paid (enterprise) | https://rdi-eg.ai/arabic-tashkeel-online/ |
| **Zyla Arabic Diacritization API** | REST | Paid (per-request) | Bearer token auth |
| **Sahehly** | Web | Freemium (1500 chars/day free) | Spelling + grammar + tashkeel |
| **Tashkil.net** | Web | Free | Online diacritization tool |

---

## Practical Recommendations for a Language Learning App

### Recommended Architecture

**Primary approach**: Use CATT (Apache 2.0) as the main diacritization engine.

```
User Text Input
    |
    v
[CATT Encoder-Decoder] -- primary diacritization
    |
    v
[Confidence check / morphological validation via CAMeL Tools]
    |
    v
[Cache diacritized text in database]
    |
    v
[Selective display layer in UI -- show/hide marks per learner level]
```

### Why CATT?

1. **License**: Apache 2.0 (commercial-friendly)
2. **Accuracy**: State-of-the-art among truly open-source models
3. **Ease of use**: `pip install catt-tashkeel`, simple Python API
4. **ONNX export**: Can deploy without PyTorch in production
5. **Batch inference**: Can process multiple texts efficiently

### Alternative Tiers

| Scenario | Recommended Tool | Reason |
|----------|-----------------|--------|
| Best accuracy, cost no object | Claude API / GPT-4 | Best on SadeedDiac-25 but expensive |
| Best open-source accuracy | CATT (Apache 2.0) | State-of-the-art, commercial-friendly |
| Lightweight / edge deployment | libtashkeel (Rust/ONNX) or arabic_vocalizer | Small footprint, fast inference |
| HuggingFace integration | Fine-Tashkeel (ByT5) | Standard transformers API, MIT license |
| Morphological understanding | CAMeL Tools + Mishkal | Know *why* a form was chosen |
| Partial diacritization | D3 or 2SDiac approach | Accept hints from teachers/learners |
| No ML dependency | Mishkal | Pure rule-based, deterministic |

### Deployment Considerations

1. **Latency**: For real-time typing, use CATT EO (faster) or ONNX models. For batch processing (e.g., preparing lesson content), use CATT ED (more accurate).

2. **Model size vs. accuracy tradeoff**:
   - 2SDiac: <5M params (if code becomes available)
   - Shakkala/ONNX: ~10-50MB
   - CATT: ~100-200MB (estimated from architecture)
   - Fine-Tashkeel: ~300MB+ (ByT5-small)
   - Sadeed: ~3GB+ (1.5B params)

3. **Caching strategy**: Arabic vocabulary is finite. Cache diacritized forms of common words/phrases in a database. For a language learning app with curated content, you can pre-diacritize all lesson materials and only need real-time diacritization for user-generated content.

4. **Quality assurance for learning content**: For educational content, always have a human Arabic linguist review critical materials. Use automated diacritization as a first pass, then human review. The morphological analyzer in CAMeL Tools can flag potentially incorrect forms.

5. **Case endings (i3rab)**: For language learners, case endings are important for grammar understanding but are the hardest to predict. Consider a setting that lets learners toggle case ending display. Most native Arabic content omits case endings even when other diacritics are present.

### Key Risk: Benchmark Inflation

Many published DER/WER numbers are inflated due to data leakage between training and test sets (as documented by the Sadeed paper). Real-world performance on novel, diverse MSA text will be worse than published numbers. Always test on your own representative data before committing to a model.

---

## License Summary

| Tool | License | Commercial Use |
|------|---------|----------------|
| CATT | Apache 2.0 | Yes |
| Fine-Tashkeel | MIT | Yes |
| Shakkala | MIT (with attribution) | Yes |
| arabic_vocalizer | MIT | Yes |
| libtashkeel | MIT | Yes |
| Hareef | MIT | Yes |
| Pipeline-Diacritizer | MIT | Yes |
| CAMeL Tools | MIT | Yes |
| Deep Diacritization (D3) | AGPL-3.0 | Restricted |
| Mishkal | GPL-3.0 | Restricted (copyleft) |
| Farasa | Free API, proprietary | API use only |
| SukounBERT | On request | Unknown |
| Sadeed | Not stated | Unknown |
