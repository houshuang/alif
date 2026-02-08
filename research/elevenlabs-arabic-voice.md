# ElevenLabs Arabic TTS: Voice, Model & Settings Research

Research for selecting the best ElevenLabs configuration for Arabic (MSA/fusha) text-to-speech aimed at language learners. Last updated: February 2026.

---

## Table of Contents

1. [Model Comparison](#1-model-comparison)
2. [Recommended Model](#2-recommended-model)
3. [Arabic Voices (MSA)](#3-arabic-voices-msa)
4. [Voice Recommendations](#4-voice-recommendations)
5. [Optimal Voice Settings](#5-optimal-voice-settings)
6. [API Configuration](#6-api-configuration)
7. [Known Issues & Workarounds](#7-known-issues--workarounds)
8. [Pronunciation Dictionaries](#8-pronunciation-dictionaries)
9. [Cost Comparison](#9-cost-comparison)
10. [Testing Plan](#10-testing-plan)

---

## 1. Model Comparison

ElevenLabs offers several models that support Arabic. Here is a comparison relevant to our use case (clear MSA pronunciation for learners).

| Model | ID | Arabic Support | Latency | Cost (credits/char) | Quality | Notes |
|-------|----|---------------|---------|---------------------|---------|-------|
| **Eleven v3** | `eleven_v3` | Yes (`ara`) | High (~1-3s) | 1 | Highest expressiveness, newest | Not designed for real-time; best for long-form narration. No Speaker Boost. |
| **Multilingual v2** | `eleven_multilingual_v2` | Yes (`ar-SA`, `ar-AE`) | High | 1 | Best emotional depth, life-like | Best text normalization (numbers, dates). Most battle-tested for non-English. |
| **Turbo v2.5** | `eleven_turbo_v2_5` | Yes | Medium (~300ms) | 0.5 | Good balance quality/speed | 3x faster than v2 for non-English. Good for conversational/real-time. |
| **Flash v2.5** | `eleven_flash_v2_5` | Yes | Low (~75ms) | 0.5 | Decent, lowest latency | May skip text normalization. Best for real-time agents. |
| **Flash v2 / Turbo v2** | `eleven_flash_v2` / `eleven_turbo_v2` | English only | Low/Medium | 0.5 | N/A for Arabic | Do NOT use for Arabic -- will produce English-accented output. |

### Key Differences for Arabic

- **Multilingual v2** has the best text normalization for numbers, dates, and symbols in non-English languages. This matters for Arabic where numeral reading rules are complex.
- **Eleven v3** supports 70+ languages (expanded from 29 in v2) and has the highest emotional range, but is still in research preview and PVCs are not fully optimized.
- **Turbo v2.5** is the current best balance for our use case: half the cost, good quality, acceptable latency.
- **Flash v2.5** is cheapest/fastest but may sacrifice pronunciation accuracy for speed.

---

## 2. Recommended Model

### Primary: `eleven_turbo_v2_5` (current choice in CLAUDE.md)

This is already configured in the project and remains a solid choice:
- Half the cost of Multilingual v2 (0.5 vs 1.0 credits/char)
- Good Arabic pronunciation quality
- Fast enough for on-demand generation
- Supports all voice settings (stability, similarity, speed, style)

### Secondary/Upgrade: `eleven_multilingual_v2`

Consider switching to this if:
- Pronunciation accuracy issues are found with Turbo v2.5
- Text contains numbers, dates, or mixed content that needs normalization
- Cost is not a primary concern (doubles the per-character cost)

### Future: `eleven_v3`

Monitor this model as it matures:
- Currently in research preview (launched June 2025)
- Supports audio tags for expressiveness (`[whispers]`, `[excited]`, etc.)
- Not optimized for real-time use -- higher latency
- PVC support is limited; use IVC or designed voices
- Stability slider works differently (Creative/Natural/Robust presets)
- When stable, may become the best option for educational audio

### Avoid

- `eleven_flash_v2` / `eleven_turbo_v2` -- English only, will add English accent to Arabic
- `eleven_flash_v2_5` -- too aggressive on latency optimization, may skip normalization steps that matter for correct Arabic pronunciation

---

## 3. Arabic Voices (MSA)

The following voices from the ElevenLabs voice library are tagged as **Modern Standard Arabic** (not dialectal). These are the most relevant for a language learning app.

### MSA Female Voices

| Voice | ID | Description |
|-------|----|-------------|
| **Asmaa** | `qi4PkV9c01kb869Vh7Su` | Young female, MSA accent, gentle conversational tone |
| **Abrar Sabbah** | `VwC51uc4PUblWEJSPzeo` | Female, suitable for multiple content types |
| **GHIZLANE** | `u0TsaWvt0v8migutHM3M` | Female, smooth, balanced, and tranquil |
| **Mona** | `tavIIPLplRB883FzWU0V` | Female, middle-aged |
| **Sana** | `mRdG9GYEjJmIzqbYTidv` | Female, soft quality with upbeat tone |

### MSA Male Voices

| Voice | ID | Description |
|-------|----|-------------|
| **Mohamed Ben** | `Qp2PG6sgef1EHtrNQKnf` | Young male, calming and engaging |
| **Chaouki** | `G1HOkzin3NMwRHSq60UI` | Deep, clear male, neutral Arabic accent. Ideal for documentaries. |
| **Anas** | `R6nda3uM038xEEKi7GFl` | Middle-aged male, gentle conversational tone |
| **Mo Wiseman** | `DPd861uv5p8migutHM3M` | Male, YouTube and audiobooks |
| **HMIDA** | `JjTirzdD7T3GMLkwdd3a` | Male, radio-suitable voice |
| **Mourad Sami** | `kERwN6X2cY8g1XbfzJsX` | Male, calm tone, good for books and news |
| **Wahab Arabic** | `ldeGOUQJqLGjlVgYn7YL` | Male, book narration |

### Other Dialect Voices (for reference, NOT recommended for MSA learner app)

| Dialect | Voices |
|---------|--------|
| Egyptian | Fathy Hammad, Haytham, Hoda, Hamza Abbas, Masry, Alice, Amr |
| Gulf | Fares |
| Kuwaiti | Abu Salem, Hasan |
| Levantine | Salma |
| Saudi | Raed |
| Moroccan | Ghizlane (Moroccan), Hamid |

---

## 4. Voice Recommendations

For a language learner app prioritizing **clear, slow, standard MSA pronunciation**:

### Top Pick: **Chaouki** (`G1HOkzin3NMwRHSq60UI`)
- Deep, clear male voice
- Described as "neutral Arabic accent" -- closest to textbook MSA
- Ideal for documentaries = clear, authoritative, measured pace
- Good for: sentence reading, vocabulary pronunciation

### Runner-up Male: **Mourad Sami** (`kERwN6X2cY8g1XbfzJsX`)
- Calm tone, described as good for books and news
- News-reader style = clear articulation, standard pronunciation

### Top Female Pick: **Asmaa** (`qi4PkV9c01kb869Vh7Su`)
- Explicitly tagged as "Arabic Modern Standard accent"
- Gentle conversational tone = not too fast, clear
- Good for: sentence reading, conversational examples

### Runner-up Female: **GHIZLANE** (`u0TsaWvt0v8migutHM3M`)
- Smooth, balanced, tranquil
- Good for: calm educational content

### Testing Strategy

All four voices above should be tested with the same diacritized Arabic sentences before committing to one. Test with:
1. Simple vocabulary words with all harakat
2. Sentences with shadda (gemination)
3. Sentences with tanwin (nunation: -an, -in, -un)
4. Words with hamza in different positions (initial, medial, final)
5. Long vowels vs short vowels contrast
6. Sun letters vs moon letters with definite article

---

## 5. Optimal Voice Settings

### For Language Learner Use Case

```json
{
  "stability": 0.85,
  "similarity_boost": 0.75,
  "style": 0.0,
  "speed": 0.8,
  "use_speaker_boost": true
}
```

### Parameter Details

**stability** (range: 0.0 - 1.0, default: 0.50)
- Controls randomness between generations
- **Set HIGH (0.80-0.90)** for learner content: consistent, predictable pronunciation
- Lower values add emotional variability -- undesirable for educational use
- Too high (>0.95) can sound monotone, but for word/sentence pronunciation this is acceptable

**similarity_boost** (range: 0.0 - 1.0, default: 0.75)
- Controls adherence to original voice characteristics
- **Keep at 0.75** (default): ensures clear, undistorted output
- Higher values (>0.85) can introduce audio artifacts
- Lower values may lose voice clarity

**style** (range: 0.0 - 1.0, default: 0.0)
- Amplifies speaker's style/expressiveness
- **Keep at 0.0** for educational content: no style exaggeration
- Uses extra compute and adds latency
- Any value above 0 is counterproductive for clear pronunciation

**speed** (range: 0.7 - 1.2, default: 1.0)
- **Set to 0.8 for sentences** (slow, learner-friendly)
- **Set to 0.75 for individual words** (extra slow for pronunciation focus)
- Do not go below 0.7 (minimum, may distort quality)
- At 1.0, native-speed Arabic is too fast for beginners
- Consider offering user-adjustable speed in the app

**use_speaker_boost** (boolean, default: true)
- Enhances overall clarity and similarity
- **Keep enabled** for maximum clarity
- Note: NOT available on Eleven v3

### Settings Comparison by Use Case

| Use Case | stability | similarity | style | speed |
|----------|-----------|------------|-------|-------|
| Word pronunciation | 0.90 | 0.75 | 0.0 | 0.75 |
| Sentence reading | 0.85 | 0.75 | 0.0 | 0.80 |
| Paragraph/story | 0.75 | 0.75 | 0.0 | 0.85 |
| Conversational example | 0.70 | 0.75 | 0.1 | 0.90 |

---

## 6. API Configuration

### Minimal Request (Python)

```python
import httpx

ELEVENLABS_API_KEY = "..."
VOICE_ID = "G1HOkzin3NMwRHSq60UI"  # Chaouki (MSA male)

async def generate_arabic_tts(text: str, speed: float = 0.8) -> bytes:
    """Generate Arabic TTS audio from diacritized text."""
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"

    response = await httpx.AsyncClient().post(
        url,
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "text": text,
            "model_id": "eleven_turbo_v2_5",
            "language_code": "ar",
            "voice_settings": {
                "stability": 0.85,
                "similarity_boost": 0.75,
                "style": 0.0,
                "speed": speed,
                "use_speaker_boost": True,
            },
            "apply_text_normalization": "on",
        },
        timeout=30.0,
    )
    response.raise_for_status()
    return response.content  # binary audio (mp3 by default)
```

### Important API Parameters

| Parameter | Value | Why |
|-----------|-------|-----|
| `model_id` | `eleven_turbo_v2_5` | Best quality/cost balance for Arabic |
| `language_code` | `ar` | Ensures Arabic text normalization rules apply |
| `apply_text_normalization` | `on` | Forces normalization (numbers, dates) -- important for Arabic |
| `output_format` | `mp3_44100_128` | Default, good quality. Use `mp3_22050_32` for smaller files |
| `seed` | (optional) | Set for reproducible output during testing |

### Output Format Options

| Format | Size | Quality | Use Case |
|--------|------|---------|----------|
| `mp3_44100_128` | Large | High | Default, best quality |
| `mp3_44100_64` | Medium | Good | Production app |
| `mp3_22050_32` | Small | Acceptable | Offline cache, bandwidth-limited |
| `pcm_16000` | Large | Lossless | Post-processing |

---

## 7. Known Issues & Workarounds

### Diacritics (Tashkeel) Handling

**Status: Generally works, but imperfect.**

- ElevenLabs models are trained on a mix of diacritized and undiacritized Arabic text
- When diacritics ARE present, the model generally respects them
- However, the model may occasionally override diacritics with its own predicted pronunciation, especially for common words where the model has strong priors
- No formal guarantee that all tashkeel marks will be read as written

**Workaround:** Always provide fully diacritized text. This gives the model the best chance of correct pronunciation. Our pipeline already diacritizes all text via CATT, so this is covered.

### Hamza Issues

- Hamzat al-wasl (connecting hamza, ء) at word beginnings is generally handled correctly
- Hamzat al-qat' (cutting hamza) is sometimes dropped or softened
- Hamza on carriers (أ إ ؤ ئ) may be read inconsistently
- **Workaround:** Test specific problematic words and add to pronunciation dictionary if needed

### Shadda (Gemination)

- Shadda (ّ) marking consonant doubling is usually pronounced correctly
- Occasionally the gemination is too subtle or skipped entirely on less common words
- **Workaround:** Ensure shadda is always present in the text. If a specific word consistently fails, use an alias in the pronunciation dictionary (e.g., spelling out the doubled consonant).

### Tanwin (Nunation)

- Tanwin fatHa (ًا), tanwin Damma (ٌ), tanwin kasra (ٍ) are generally handled
- The distinction between tanwin and actual nun can sometimes be confused
- **Workaround:** Ensure proper Unicode encoding of tanwin characters

### English Accent Bleed

- Default/premade voices (like "Rachel", "Adam") are English-native and WILL carry English accent into Arabic
- ALWAYS use Arabic-native voices from the voice library for Arabic content
- Using `language_code: "ar"` helps but does not fully prevent accent issues if the voice is English-native

### Language Switching

- On longer texts, the model may occasionally switch to reading Arabic text with English phonetics or vice versa
- More common with mixed-script text (Arabic + English in same request)
- **Workaround:** Keep requests short (single sentence or phrase). Never mix Arabic and English in the same TTS request.

### Text Normalization Gaps

- `apply_language_text_normalization` is currently only supported for Japanese, NOT Arabic
- General `apply_text_normalization` helps with numbers and dates but is not Arabic-specific
- Arabic numbers (Eastern Arabic numerals ٠١٢٣) may not be normalized correctly
- **Workaround:** Pre-process text to spell out numbers in Arabic words before sending to TTS

### Pronunciation Dictionary Limitations

- SSML phoneme tags (IPA/CMU) only work with English-only models (Flash v2, Turbo v2, English v1)
- For Arabic, you must use **alias tags** in pronunciation dictionaries instead
- Aliases replace a word with an alternative spelling that produces the desired pronunciation
- Up to 3 pronunciation dictionaries can be attached per request

---

## 8. Pronunciation Dictionaries

Since SSML phoneme tags do not work for Arabic, use pronunciation dictionaries with alias rules.

### Creating a PLS Dictionary for Arabic

```xml
<?xml version="1.0" encoding="UTF-8"?>
<lexicon version="1.0"
    xmlns="http://www.w3.org/2005/01/pronunciation-lexicon"
    alphabet="x-]]elevenlabs-alias"
    xml:lang="ar">

  <!-- Example: force correct hamza pronunciation -->
  <lexeme>
    <grapheme>مسؤول</grapheme>
    <alias>مسْئُول</alias>
  </lexeme>

  <!-- Example: ensure shadda is clear -->
  <lexeme>
    <grapheme>مُعَلِّم</grapheme>
    <alias>مُعَلْلِم</alias>
  </lexeme>

</lexicon>
```

### Workflow

1. Upload the .pls file via the ElevenLabs API (`POST /v1/pronunciation-dictionaries/add-from-file`)
2. Reference the dictionary ID in TTS requests via `pronunciation_dictionary_locators`
3. Build up the dictionary over time as pronunciation issues are discovered
4. Maximum 3 dictionaries per request

---

## 9. Cost Comparison

Based on typical Arabic sentence lengths for a learning app.

### Assumptions
- Average Arabic sentence: ~50 characters
- Average Arabic word: ~8 characters
- Starter plan: 30,000 credits/month ($5/month)
- Scale plan: 100,000 credits/month ($22/month)

### Per-Model Costs

| Model | Credits/char | Sentences/30k credits | Words/30k credits |
|-------|-------------|----------------------|-------------------|
| Turbo v2.5 | 0.5 | 1,200 sentences | 7,500 words |
| Flash v2.5 | 0.5 | 1,200 sentences | 7,500 words |
| Multilingual v2 | 1.0 | 600 sentences | 3,750 words |
| Eleven v3 | 1.0 | 600 sentences | 3,750 words |

### Recommendation

At 0.5 credits/char, **Turbo v2.5 gives 2x the output** of Multilingual v2 for the same cost. For a personal learning app generating a few dozen sentences per day, the Starter plan (30k credits) should be sufficient with Turbo v2.5.

---

## 10. Testing Plan

Before finalizing the voice and model choice, run these tests:

### Test Sentences (fully diacritized)

```
1. Basic: ذَهَبَ الطَّالِبُ إِلَى المَدْرَسَةِ
   (The student went to the school)
   Tests: sun letter assimilation (الطّ), kasra on ta marbuta

2. Tanwin: رَأَيْتُ كِتَابًا جَدِيدًا
   (I saw a new book)
   Tests: tanwin fatha, hamza on alif

3. Shadda: المُعَلِّمُ يُعَلِّمُ الأَطْفَالَ
   (The teacher teaches the children)
   Tests: shadda gemination, hamzat al-qat'

4. Hamza positions: سَأَلَ عَنْ شَيْءٍ مَسْؤُولٍ
   (He asked about a responsible thing)
   Tests: hamza in different positions

5. Long vs short vowels: كَتَبَ كَاتِبٌ كِتَابًا كَبِيرًا
   (A writer wrote a big book)
   Tests: short a vs long aa, short i vs long ii

6. Numbers: عِنْدِي ثَلَاثَةُ كُتُبٍ
   (I have three books)
   Tests: number pronunciation
```

### Testing Procedure

1. Generate each sentence with all 4 shortlisted voices (Chaouki, Mourad Sami, Asmaa, GHIZLANE)
2. Generate each with both `eleven_turbo_v2_5` and `eleven_multilingual_v2`
3. Compare at speed 0.8 and speed 1.0
4. Have an Arabic speaker evaluate pronunciation accuracy
5. Score each combination on: clarity, correctness, naturalness, pace

### Test Script Location

Create `backend/scripts/test_tts_voices.py` to automate the above comparisons.

---

## Sources

- [ElevenLabs Models Documentation](https://elevenlabs.io/docs/overview/models)
- [ElevenLabs Arabic TTS](https://elevenlabs.io/text-to-speech/arabic)
- [ElevenLabs Voice Settings](https://elevenlabs.io/docs/api-reference/voices/settings/get)
- [ElevenLabs Best Practices](https://elevenlabs.io/docs/overview/capabilities/text-to-speech/best-practices)
- [ElevenLabs Pronunciation Dictionaries](https://elevenlabs.io/docs/developers/guides/cookbooks/text-to-speech/pronunciation-dictionaries)
- [ElevenLabs TTS API Reference](https://elevenlabs.io/docs/api-reference/text-to-speech/convert)
- [ElevenLabs Troubleshooting](https://elevenlabs.io/docs/resources/troubleshooting)
- [ElevenLabs Arabic Voices List](https://json2video.com/ai-voices/elevenlabs/languages/arabic/)
- [ElevenLabs v3 Blog Post](https://elevenlabs.io/blog/eleven-v3)
- [ElevenLabs Language Tutor Voices](https://elevenlabs.io/voice-library/language-tutor)
- [Arabic Pronunciation Errors in AI Narration (Research Paper)](https://al-kindipublisher.com/index.php/fcsai/article/download/11517/10251)
- [ElevenLabs Speed Control](https://elevenlabs.io/docs/agents-platform/customization/voice/speed-control)
- [ElevenLabs Pricing](https://elevenlabs.io/pricing/api)
