# Arabic Language Processing: Free APIs & Web Services

Comprehensive survey of free and freemium APIs, libraries, and web services for Arabic (MSA) language processing. Last updated: February 2026.

---

## Table of Contents

1. [Morphological Analysis](#1-morphological-analysis)
2. [Diacritization / Tashkeel](#2-diacritization--tashkeel)
3. [Translation](#3-translation)
4. [Text-to-Speech](#4-text-to-speech)
5. [Dictionary / Lookup](#5-dictionary--lookup)
6. [NLP APIs with Arabic Support (Cloud)](#6-nlp-apis-with-arabic-support-cloud)
7. [BERT / Transformer Models on HuggingFace](#7-bert--transformer-models-on-huggingface)
8. [Self-Hostable Python Libraries](#8-self-hostable-python-libraries)
9. [Transliteration](#9-transliteration)
10. [Summary Comparison Table](#10-summary-comparison-table)

---

## 1. Morphological Analysis

### Farasa (QCRI)

The state-of-the-art Arabic NLP toolkit from Qatar Computing Research Institute. Provides segmentation, lemmatization, POS tagging, NER, diacritization, dependency parsing, constituency parsing, and spell-checking via a RESTful Web API.

- **Website:** https://farasa.qcri.org/
- **Auth:** Free registration required. API key sent to registered email.
- **Cost:** Completely free for academic/research use. Contact support@farasa.qcri.org for commercial.
- **Rate limits:** Limitations on text size and request frequency (exact numbers not published).
- **Language support:** Python, Java, JavaScript, cURL snippets provided.

**API Endpoints:**

| Module | Endpoint |
|--------|----------|
| Segmentation | `https://farasa.qcri.org/webapi/seg/` |
| Lemmatization | `https://farasa.qcri.org/webapi/lemmatization/` |
| POS Tagging | `https://farasa.qcri.org/webapi/pos/` |
| Diacritization | `https://farasa.qcri.org/webapi/diacritize/` |
| Seq2seq Diacritization | `https://farasa-api.qcri.org/msa/webapi/diacritizeV2` |
| NER | `https://farasa.qcri.org/webapi/ner/` |
| Spellcheck | `https://farasa.qcri.org/webapi/spellcheck/` |

**Example (Python):**
```python
import requests, json

api_key = "YOUR_API_KEY"
text = "يُشار إلى أن اللغة العربية"

# Lemmatization
url = "https://farasa.qcri.org/webapi/lemmatization/"
payload = {"text": text, "api_key": api_key}
response = requests.post(url, data=payload)
result = json.loads(response.text)
print(result)

# Diacritization
url = "https://farasa.qcri.org/webapi/diacritize/"
payload = {"text": text, "api_key": api_key}
response = requests.post(url, data=payload)
result = json.loads(response.text)
print(result)
```

**Example (cURL):**
```bash
curl --header "Content-Type: application/json" \
  -d '{"text":"يُشار إلى أن اللغة العربية", "api_key":"YOUR_KEY"}' \
  https://farasa.qcri.org/webapi/lemmatization/
```

**Python wrapper (farasapy):**
```bash
pip install farasapy
```
```python
from farasa.segmenter import FarasaSegmenter
from farasa.pos import FarasaPOSTagger
from farasa.ner import FarasaNamedEntityRecognizer
from farasa.diacritizer import FarasaDiacritizer
from farasa.stemmer import FarasaStemmer
from farasa.lemmatizer import FarasaLemmatizer

segmenter = FarasaSegmenter()
result = segmenter.segment("يُشار إلى أن اللغة العربية")
```

- **GitHub (farasapy):** https://github.com/MagedSaeed/farasapy

---

### Qutrub - Arabic Verb Conjugator

Open-source Arabic verb conjugation tool by Taha Zerrouki. Conjugates verbs across all tenses and pronouns.

- **Web API:** `http://qutrub.arabeyes.org/api`
- **Auth:** None required
- **Cost:** Free (GPLv3)
- **Method:** GET

**Parameters:**

| Param | Description | Values |
|-------|-------------|--------|
| `verb` | Arabic verb (vowel-marked recommended) | e.g. `كتب` |
| `haraka` | Present tense vowel | `a` (fatha), `u` (damma), `i` (kasra) |
| `trans` | Transitivity | `0` (intransitive), `1` (transitive) |

**Example:**
```
http://qutrub.arabeyes.org/api?verb=كتب&haraka=u&trans=1
```

**Response:** JSON with `verb_info`, `result` (conjugation table by pronoun/tense), and `suggest` (alternative conjugations).

**Python library:**
```bash
pip install libqutrub
```
```python
import libqutrub.conjugator
result = libqutrub.conjugator.conjugate(
    verb="كَتَبَ",
    future_type="فتحة",
    transitive=True,
    display_format="DICT"
)
```

- **GitHub:** https://github.com/linuxscout/qutrub

---

## 2. Diacritization / Tashkeel

### Mishkal - Arabic Text Vocalization

Open-source rule-based Arabic diacritizer by Taha Zerrouki. Uses morphological analysis (Qalsadi), light stemming (Tashaphyne), verb conjugation (Qutrub), and syntax analysis to produce diacritized text.

- **Live API:** `http://tahadz.com/mishkal/ajaxGet`
- **Self-hosted:** `http://127.0.0.1:8080` (when running locally)
- **Auth:** None
- **Cost:** Free (GPLv3)

**Installation:**
```bash
pip install mishkal
# OR
git clone https://github.com/linuxscout/mishkal.git
pip install -r mishkal/requirements.txt
```

**Web server:**
```bash
python3 interfaces/web/mishkal-webserver
# Serves on 0.0.0.0:8080
```

**API call (JavaScript/jQuery):**
```javascript
$.getJSON("http://tahadz.com/mishkal/ajaxGet", {
  text: "السلام عليكم",
  action: "TashkeelText"
}, function(data) {
  console.log(data.result);
  // "السّلامُ عَلَيكُمْ"
});
```

**Python library usage:**
```python
import mishkal.tashkeel

vocalizer = mishkal.tashkeel.TashkeelClass()
text = "تطلع الشمس صباحا"
result = vocalizer.tashkeel(text)
print(result)  # "تَطْلُعُ الشَّمْسُ صَبَاحًا"
```

**Response format:**
```json
{
  "result": "السّلامُ عَلَيكُمْ",
  "order": "0"
}
```

- **GitHub:** https://github.com/linuxscout/mishkal

---

### RDI Tashkeel API

Commercial Arabic diacritization API from RDI (Research & Development International), an Egyptian company. Available as online API and on-premises SDK.

- **Website:** https://rdi-eg.ai/product/tashkeel-online-api/
- **Online demo:** https://rdi-eg.ai/arabic-tashkeel-online/
- **Auth:** Registration required; API key issued after signup
- **Cost:** Multiple pricing plans (contact for details; free trial likely available)
- **On-premises:** Also available as a self-hosted SDK for offline use

---

### Zyla Arabic Diacritization API

Hosted diacritization API available on the Zyla API Hub marketplace.

- **Endpoint:** `https://zylalabs.com/api/812/arabic+diacritization+api/569/diacritic`
- **Auth:** Bearer token in Authorization header
- **Cost:** 7-day free trial, then $0.016/request
- **Docs:** https://zylalabs.com/api-marketplace/nlp/arabic+diacritization+api/812

---

### Tashkil.net

Free online diacritization tool with reportedly high accuracy.

- **Website:** https://www.tashkil.net/
- **API access:** No public developer API documented
- **Cost:** Free (web interface only)

---

### Sadeed (HuggingFace Model)

A compact fine-tuned language model (1.5B params, based on Kuwain) for Arabic diacritization. Published April 2025.

- **Paper:** https://arxiv.org/abs/2504.21635
- **Dataset:** https://huggingface.co/datasets/Misraj/Sadeed_Tashkeela
- **Usage:** Self-hosted via HuggingFace Transformers; feed undiacritized text and the model generates diacritized output
- **Cost:** Free (model weights available on HuggingFace)

---

## 3. Translation

### Google Cloud Translation API

- **Endpoint:** `https://translation.googleapis.com/language/translate/v2`
- **Free tier:** 500,000 characters/month (never expires, resets monthly)
- **Paid:** $20 per million characters after free tier
- **Auth:** API key or OAuth2; requires Google Cloud project
- **Arabic support:** Full support for `ar` (Arabic) among 130+ languages
- **Docs:** https://cloud.google.com/translate/pricing

**Example:**
```bash
curl -X POST \
  "https://translation.googleapis.com/language/translate/v2?key=YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"q": "Hello world", "source": "en", "target": "ar", "format": "text"}'
```

---

### Microsoft Azure Translator

- **Endpoint:** `https://api.cognitive.microsofttranslator.com/translate`
- **Free tier (F0):** 2,000,000 characters/month
- **Auth:** Subscription key from Azure portal
- **Arabic support:** Full support (`ar`)
- **Docs:** https://azure.microsoft.com/en-us/pricing/details/translator/
- **Note:** Service stops at 2M chars; resumes next billing month

**Example:**
```bash
curl -X POST \
  "https://api.cognitive.microsofttranslator.com/translate?api-version=3.0&from=en&to=ar" \
  -H "Ocp-Apim-Subscription-Key: YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '[{"Text": "Hello world"}]'
```

---

### DeepL API

- **Endpoint:** `https://api-free.deepl.com/v2/translate`
- **Free tier:** 500,000 characters/month, up to 2 API keys, 1 glossary
- **Paid (Pro):** $5.49/month base + $25 per million characters
- **Auth:** API key (free registration at deepl.com)
- **Arabic support:** Yes (added relatively recently, among 36 languages)
- **Docs:** https://support.deepl.com/hc/en-us/articles/360021200939

**Example:**
```bash
curl -X POST "https://api-free.deepl.com/v2/translate" \
  -H "Authorization: DeepL-Auth-Key YOUR_KEY" \
  -d "text=Hello world&source_lang=EN&target_lang=AR"
```

---

### LibreTranslate (Self-Hosted)

Free and open-source machine translation API. Can be self-hosted for unlimited usage.

- **Public endpoint:** `https://libretranslate.com/translate` (rate-limited)
- **Self-hosted:** Unlimited when running your own instance
- **Auth:** API key required on public instance; optional on self-hosted
- **Cost:** Free (AGPL-3.0 license)
- **Arabic support:** Yes (loads all languages by default; use `--load-only ar,en` to reduce memory)

**Installation (Docker):**
```bash
docker run -ti --rm -p 5000:5000 libretranslate/libretranslate
```

**Example:**
```bash
curl -X POST "http://localhost:5000/translate" \
  -H "Content-Type: application/json" \
  -d '{"q": "Hello world", "source": "en", "target": "ar"}'
```

- **GitHub:** https://github.com/LibreTranslate/LibreTranslate

---

### MyMemory Translation API

World's largest collaborative translation memory. No registration required for basic usage.

- **Endpoint:** `https://api.mymemory.translated.net/get`
- **Free tier:** 5,000 characters/day (anonymous), 50,000 characters/day (with email)
- **Auth:** None required (optional `de` param for email, `key` for registered users)
- **Arabic support:** Yes (`ar|en`, `en|ar`)
- **Max request size:** 500 bytes UTF-8

**Example:**
```bash
curl "https://api.mymemory.translated.net/get?q=مرحبا+بالعالم&langpair=ar|en"
```

**Parameters:**

| Param | Description |
|-------|-------------|
| `q` | Text to translate (max 500 bytes) |
| `langpair` | Source and target (`en|ar`) |
| `de` | Your email (raises limit to 50K chars/day) |
| `key` | API key for private TM access |
| `mt` | Include machine translation (`1`/`0`) |

- **Docs:** https://mymemory.translated.net/doc/spec.php

---

### Lingvanex Translation & Dictionary API

- **Endpoint:** Via RapidAPI or direct API
- **Free tier:** Sign-up bonus characters for testing
- **Paid:** $5 per million characters (cloud); $50/month self-hosted unlimited
- **Auth:** API key
- **Arabic support:** Yes
- **Docs:** https://lingvanex.com/products/translationapi/

---

## 4. Text-to-Speech

### Google Cloud Text-to-Speech

- **Endpoint:** `https://texttospeech.googleapis.com/v1/text:synthesize`
- **Free tier:**
  - Standard voices: 4 million characters/month
  - WaveNet voices: 1 million characters/month
  - New users: additional $300 in credits
- **Arabic support:** `ar-XA` (Modern Standard Arabic / ar-001). Multiple male and female voices.
- **Auth:** API key or OAuth2; requires Google Cloud project
- **Docs:** https://cloud.google.com/text-to-speech/pricing

**Example:**
```bash
curl -X POST \
  "https://texttospeech.googleapis.com/v1/text:synthesize?key=YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {"text": "مرحبا بالعالم"},
    "voice": {"languageCode": "ar-XA", "ssmlGender": "FEMALE"},
    "audioConfig": {"audioEncoding": "MP3"}
  }'
```

---

### Microsoft Azure Speech Service

- **Free tier (F0):** 500,000 characters/month for neural voices
- **Arabic support:** `ar-SA` (Saudi Arabia), `ar-EG` (Egypt), and other regional variants
- **Auth:** Azure subscription key
- **Docs:** https://azure.microsoft.com/en-us/pricing/details/speech/

---

### Klaam (Open Source - ARBML)

Open-source Arabic speech recognition, classification, and text-to-speech library.

- **Cost:** Free (MIT license)
- **Models:**
  - MSA Speech Recognition: `wav2vec2-large-xlsr-53-arabic`
  - Egyptian Speech Recognition: `wav2vec2-large-xlsr-53-arabic-egyptian`
  - Dialect Classification (EGY, NOR, LAV, GLF, MSA): `wav2vec2-large-xlsr-dialect-classification`
  - TTS: FastSpeech2

**Installation:**
```bash
git clone https://github.com/ARBML/klaam.git
cd klaam && bash install.sh
```

**Usage:**
```python
from klaam import SpeechRecognition, TextToSpeech, SpeechClassification

# Speech Recognition (MSA)
model = SpeechRecognition(lang='msa')
text = model.transcribe('audio.wav')

# Speech Classification (dialect detection)
clf = SpeechClassification()
dialect = clf.classify('audio.wav')

# Text-to-Speech (requires model paths)
tts = TextToSpeech(preprocess_path, model_config_path,
                   train_config_path, vocoder_config_path,
                   speaker_weights_path)
audio = tts.synthesize("مرحبا بالعالم")
```

- **GitHub:** https://github.com/ARBML/klaam

---

### Other Free TTS Services (Web Only, No Developer API)

| Service | URL | Notes |
|---------|-----|-------|
| ttsMP3.com | https://ttsmp3.com/text-to-speech/Arabic/ | Free, download as MP3, uses AWS Polly |
| Crikk | https://crikk.com/text-to-speech/arabic/ | Free, unlimited, no signup |
| ElevenLabs | https://elevenlabs.io/text-to-speech/arabic | Free trial; API available on paid plans |

---

## 5. Dictionary / Lookup

### Ejtaal.net / Arabic Almanac

Web interface providing access to major Arabic dictionaries including Hans Wehr (4th ed.), Lane's Lexicon, al-Mawrid, Hava, and 20+ root-based dictionaries in 6 languages.

- **Website:** https://ejtaal.net/aa/
- **Programmatic access:** No formal REST API. URL-based search via `https://ejtaal.net/aa/#q=ROOT` (e.g., `#q=bqr`)
- **Data source:** https://github.com/ejtaal/mr (open-source dictionary data on GitHub)
- **Cost:** Free

---

### Aratools Arabic-English Dictionary

Online dictionary with 80,000+ stems. Provides root-based translation, verb conjugation, POS info, and morphological prefix/suffix analysis.

- **Website:** https://aratools.com/
- **Core library:** `aratools-core` Python library (private git repository as of Sept 2025)
- **Apps:** iOS (App Store), Android (Google Play), Chrome Extension
- **API:** Developer API documentation mentioned but not publicly accessible
- **Cost:** Free (web and apps)

---

### Lingvanex Dictionary API

Programmatic dictionary lookup for Arabic among other languages.

- **Endpoint:** Via Lingvanex API
- **Auth:** API key
- **Cost:** Free tier with sign-up bonus; paid plans from $5/million chars
- **Docs:** https://lingvanex.com/services/arabic-dictionary-api/

---

### Hans Wehr Dictionary App (Open Source)

A Flutter-based Hans Wehr Dictionary app with open-source code.

- **GitHub:** https://github.com/GibreelAbdullah/HansWehrDictionary
- **Data format:** Structured dictionary data that could be used programmatically
- **Cost:** Free

---

## 6. NLP APIs with Arabic Support (Cloud)

### Google Cloud Natural Language API

Entity analysis, sentiment analysis, syntax analysis, and content classification.

- **Endpoint:** `https://language.googleapis.com/v2/documents:analyzeSentiment` (and similar)
- **Arabic support:** Yes (for entity analysis, syntax analysis; sentiment may be limited)
- **Free tier:** 5,000 units/month per feature
- **Auth:** API key or OAuth2
- **Docs:** https://cloud.google.com/natural-language/pricing

---

### Amazon Comprehend

Entity recognition, key phrase extraction, sentiment analysis, language detection, topic modeling.

- **Arabic support:** Yes (`ar`) - added November 2019
- **Free tier:** 50,000 units/month for first 12 months (new AWS customers)
- **Auth:** AWS credentials (IAM)
- **Docs:** https://aws.amazon.com/comprehend/pricing/

**Example (Python boto3):**
```python
import boto3
client = boto3.client('comprehend', region_name='us-east-1')

response = client.detect_sentiment(
    Text="هذا المنتج ممتاز جداً",
    LanguageCode="ar"
)
print(response['Sentiment'])  # POSITIVE
```

---

### Microsoft Azure AI Language (Text Analytics)

Sentiment analysis, NER, key phrase extraction, language detection.

- **Arabic support:** Yes (for sentiment analysis and other features)
- **Free tier (F0):** 5,000 transactions/month, max 500 chars per request
- **Auth:** Azure subscription key
- **Docs:** https://azure.microsoft.com/en-us/pricing/details/cognitive-services/language-service/

---

## 7. BERT / Transformer Models on HuggingFace

These models are available for free on HuggingFace. They can be used via the HuggingFace Inference API (free tier with monthly credits) or self-hosted with the `transformers` library.

**HuggingFace Inference API free tier:** Monthly credits included for all users; 20x credits with Pro ($9/month). Focused on CPU inference for smaller models.

### AraBERT

Pre-trained BERT model for Arabic by AUB (American University of Beirut).

- **Model:** `aubmindlab/bert-base-arabert`
- **Tasks:** Sentiment analysis, NER, question answering, text classification
- **HuggingFace:** https://huggingface.co/aubmindlab/bert-base-arabert

```python
from transformers import pipeline

classifier = pipeline("sentiment-analysis", model="aubmindlab/bert-base-arabert")
result = classifier("هذا الكتاب رائع")
```

---

### CAMeLBERT

Collection of BERT models pre-trained on different Arabic variants (MSA, Dialectal, Classical).

- **MSA Sentiment:** `CAMeL-Lab/bert-base-arabic-camelbert-msa-sentiment`
- **DA Sentiment:** `CAMeL-Lab/bert-base-arabic-camelbert-da-sentiment`
- **Base MSA:** `CAMeL-Lab/bert-base-arabic-camelbert-msa`
- **Tasks:** Sentiment analysis, NER, POS tagging, dialect identification
- **HuggingFace:** https://huggingface.co/CAMeL-Lab

```python
from transformers import pipeline

sentiment = pipeline(
    "text-classification",
    model="CAMeL-Lab/bert-base-arabic-camelbert-msa-sentiment"
)
result = sentiment("هذا المطعم سيء جداً")
# [{'label': 'negative', 'score': 0.98}]
```

---

### Falcon Arabic Models (TII)

State-of-the-art Arabic LLMs from the Technology Innovation Institute (Abu Dhabi).

- **Falcon-Arabic** (May 2025): Specialized Falcon 3 adaptation with Arabic-specific morphology and diacritics processing
- **Falcon-H1-Arabic** (January 2026): Hybrid Mamba-Transformer architecture. Available in 3B, 7B, and 34B.
- **HuggingFace:** https://huggingface.co/tiiuae

---

### Swan Arabic Embeddings

Arabic embedding models for semantic search and retrieval.

- **Swan-Small:** Based on ARBERTv2
- **Swan-Large:** Based on ArMistral
- **Tasks:** Semantic similarity, information retrieval, clustering

---

## 8. Self-Hostable Python Libraries

### CAMeL Tools (NYU Abu Dhabi)

The most comprehensive open-source Python toolkit for Arabic NLP.

- **License:** MIT
- **Features:** Morphological analysis & generation, POS tagging, lemmatization, diacritization, dialect identification, NER, sentiment analysis
- **Morphological databases:** `calima-msa-r13` (MSA), `calima-egy-r13` (Egyptian)

**Installation:**
```bash
# macOS
brew install cmake boost
pip install camel-tools

# Apple Silicon
CMAKE_OSX_ARCHITECTURES=arm64 pip install camel-tools

# Download data
camel_data -i light    # Morphology only (~300MB)
camel_data -i defaults # Default models
camel_data -i all      # Everything
```

**Usage examples:**
```python
# Morphological Analysis
from camel_tools.morphology.analyzer import Analyzer
analyzer = Analyzer.builtin_analyzer('calima-msa-r13')
analyses = analyzer.analyze("كتب")
for a in analyses:
    print(a['lex'], a['pos'], a['root'])

# Diacritization
from camel_tools.tagger.default import DefaultTagger
tagger = DefaultTagger.pretrained('calima-msa-r13')
diacritized = tagger.tag("يكتب الولد الدرس".split())

# Dialect Identification
from camel_tools.dialectid import DialectIdentifier
did = DialectIdentifier.pretrained()
predictions = did.predict(["شلونك", "إزيك", "كيف حالك"])
```

- **GitHub:** https://github.com/CAMeL-Lab/camel_tools
- **Docs:** https://camel-tools.readthedocs.io/

---

### Stanza (Stanford NLP)

Full neural NLP pipeline with Arabic support. Provides tokenization, MWT expansion, lemmatization, POS tagging, morphological features, dependency parsing, and NER.

- **License:** Apache 2.0
- **Arabic models:** Trained on Universal Dependencies treebanks (PADT, etc.)

**Installation:**
```bash
pip install stanza
```

**Usage:**
```python
import stanza

# Download Arabic models (first time only)
stanza.download('ar')

nlp = stanza.Pipeline('ar')
doc = nlp("يكتب الطالب الدرس في الصف")

for sentence in doc.sentences:
    for word in sentence.words:
        print(f"{word.text}\t{word.lemma}\t{word.upos}\t{word.feats}")
```

- **GitHub:** https://github.com/stanfordnlp/stanza
- **Arabic models info:** https://stanfordnlp.github.io/stanza/available_models.html

---

### spaCy + Stanza Integration

spaCy does not ship native Arabic models, but can use Stanza models through the `spacy-stanza` bridge, giving you the spaCy API with Stanza's Arabic models.

```bash
pip install spacy spacy-stanza stanza
```

```python
import stanza
import spacy_stanza

stanza.download("ar")
nlp = spacy_stanza.load_pipeline("ar")

doc = nlp("يكتب الطالب الدرس")
for token in doc:
    print(token.text, token.lemma_, token.pos_, token.dep_)
```

There is also a `camelTokenizer` spaCy extension that integrates CAMeL Tools morphological tokenization into the spaCy pipeline.

- **GitHub:** https://github.com/explosion/spacy-stanza

---

### Tashaphyne - Arabic Light Stemmer

Arabic light stemmer and segmenter. Removes prefixes and suffixes and generates all possible segmentations.

- **License:** GPLv3

```bash
pip install tashaphyne
```

```python
from tashaphyne.stemming import ArabicLightStemmer

stemmer = ArabicLightStemmer()
word = "والمدرسين"
stemmer.light_stem(word)
print(stemmer.get_stem())    # Root/stem
print(stemmer.get_prefix())  # Prefix
print(stemmer.get_suffix())  # Suffix
```

- **GitHub:** https://github.com/linuxscout/tashaphyne

---

### PyArabic - Arabic Text Utilities

Low-level utilities for Arabic text manipulation: normalization, tokenization, diacritics handling, number conversion, transliteration.

- **License:** GPL

```bash
pip install pyarabic
```

```python
import pyarabic.araby as araby

text = "بِسْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ"

# Strip diacritics
clean = araby.strip_tashkeel(text)
print(clean)  # "بسم الله الرحمن الرحيم"

# Check if character is Arabic
print(araby.is_arabicword("كتاب"))  # True

# Normalize (hamza, ligature normalization)
normalized = araby.normalize_hamza("إسلام")
```

- **GitHub:** https://github.com/linuxscout/pyarabic

---

### Qutuf - Morphological Analyzer & POS Tagger

Arabic morphological analyzer using the AlKhalil Morpho Sys database. Rule-based POS tagging as an expert system.

- **License:** Apache 2.0
- **GitHub:** https://github.com/Qutuf/Qutuf

---

### AraMorph / Buckwalter Morphological Analyzer

The classic Arabic morphological analyzer (originally by Tim Buckwalter). Contains ~83,000 lexicon entries of prefixes, suffixes, and stems.

- **Perl:** https://sourceforge.net/projects/aramorph/
- **Java (for Lucene):** AraMorph Java port
- **Python:** `pip install pyaramorph`
- **C#:** https://sourceforge.net/projects/aramorphnet/

---

## 9. Transliteration

### Yamli Smart Arabic Keyboard API

Converts Latin (Arabizi) input to Arabic script in real-time using statistical modeling.

- **API:** https://www.yamli.com/api/
- **Docs:** https://www.yamli.com/api/docs/
- **Cost:** Free for website integration
- **Usage:** JavaScript widget that converts text input fields to accept Arabic via Latin transliteration
- **Auth:** None for basic widget; register for advanced features

**Integration:**
```html
<script src="https://www.yamli.com/api/js/yamli.js"></script>
<script>
  yamli.init({ startMode: "onOrUserLang" });
  yamli.yamlify("myTextarea");
</script>
```

---

## 10. Summary Comparison Table

### Morphological Analysis / NLP Toolkits

| Tool | Type | Cost | Auth | Key Features |
|------|------|------|------|-------------|
| **Farasa** | REST API | Free (academic) | API key | Segmentation, lemma, POS, NER, diacritize, parse |
| **CAMeL Tools** | Python lib | Free (MIT) | None | Morphology, POS, lemma, diacritize, NER, sentiment, DID |
| **Stanza** | Python lib | Free (Apache 2.0) | None | Tokenize, POS, lemma, dependency parse, NER |
| **Tashaphyne** | Python lib | Free (GPL) | None | Light stemming, segmentation |
| **Qutuf** | Java lib | Free (Apache 2.0) | None | Morphological analysis, POS tagging |
| **AraMorph** | Perl/Java/Python | Free | None | Classic Buckwalter morphological analysis |

### Diacritization

| Tool | Type | Cost | Accuracy | Notes |
|------|------|------|----------|-------|
| **Farasa** | REST API | Free | High | Part of full NLP suite |
| **Mishkal** | REST API / Python | Free (GPL) | Good | Self-hostable, rule-based |
| **RDI Tashkeel** | REST API | Paid (trial available) | High | On-premises option available |
| **Zyla API** | REST API | $0.016/req (7-day trial) | Unknown | Hosted marketplace |
| **Sadeed** | HF Model | Free | High | 1.5B param model, self-hosted |

### Translation

| Service | Free Tier | Auth | Notes |
|---------|-----------|------|-------|
| **Azure Translator** | 2M chars/month | Azure key | Best free tier |
| **Google Translate API** | 500K chars/month | API key | Never expires |
| **DeepL** | 500K chars/month | API key | High quality |
| **MyMemory** | 5K-50K chars/day | None/email | No signup needed |
| **LibreTranslate** | Unlimited (self-hosted) | Optional | AGPL-3.0, run your own |
| **Lingvanex** | Sign-up bonus | API key | $5/M chars after |

### Text-to-Speech

| Service | Free Tier | Arabic Variant | Notes |
|---------|-----------|----------------|-------|
| **Google Cloud TTS** | 1M chars/month (WaveNet) | ar-XA (MSA) | Best free tier, multiple voices |
| **Azure Speech** | 500K chars/month | ar-SA, ar-EG, etc. | Neural voices |
| **Klaam** | Unlimited (self-hosted) | MSA | Open source, FastSpeech2 |

### Cloud NLP (Entity/Sentiment/Syntax)

| Service | Free Tier | Arabic Support | Features |
|---------|-----------|----------------|----------|
| **Google NL API** | 5K units/month | Yes | Entity, sentiment, syntax, classification |
| **AWS Comprehend** | 50K units/month (12 mo) | Yes | Entity, sentiment, key phrases, topics |
| **Azure Text Analytics** | 5K txn/month | Yes | Sentiment, NER, key phrases |

---

## Recommended Stack for an Arabic Learning App

For a web/mobile application focused on MSA, here is a practical combination of free services:

1. **Morphology & root extraction:** CAMeL Tools (self-hosted Python) or Farasa API
2. **Diacritization:** Mishkal (self-hosted, free) or Farasa API
3. **Verb conjugation:** Qutrub API (`http://qutrub.arabeyes.org/api`)
4. **Translation:** Azure Translator (2M chars/month free) or Google Translate (500K chars/month)
5. **Text-to-speech:** Google Cloud TTS (1M WaveNet chars/month free)
6. **Dictionary lookup:** Ejtaal.net data (GitHub) + Hans Wehr app data
7. **Sentiment / NER:** CAMeLBERT models on HuggingFace or AWS Comprehend
8. **Transliteration:** Yamli API (free widget)
9. **Text utilities:** PyArabic for normalization, diacritics stripping, etc.
