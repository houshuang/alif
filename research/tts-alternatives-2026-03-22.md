# TTS Alternatives for MSA Arabic — 2026-03-22

## Current Setup (baseline)
- ElevenLabs `eleven_multilingual_v2`, Chaouki voice (MSA male)
- Speed 0.7, stability 0.85, similarity 0.75
- Cost: ~$240/M characters (Pro plan overage)
- Quality: Good MSA but multilingual model means Arabic isn't primary focus

## Provider Comparison

| Provider | Male MSA Voices | Price/M chars | Tashkeel | Latency | Quality |
|---|---|---|---|---|---|
| ElevenLabs (current) | 1 (Chaouki) | ~$240 | Passable | 400-800ms | Good |
| Google Chirp 3 HD | 15 | $30 | Unknown | 150-300ms | Excellent (latest gen) |
| Google WaveNet | 2 | $16 | Unknown | 150-300ms | Good |
| Azure Neural | 16 | $16 | Enhanced | 200-400ms | Good (78% error reduction) |
| Amazon Polly Neural | 1 (Gulf only) | $19.20 | Unknown | ~200ms | OK |
| OpenAI TTS | 0 | $15-30 | Poor | 80-150ms | Not viable for Arabic |
| Habibi (OSS) | Yes | Free (GPU cost) | Not required | Unknown | Promising |
| Arabic-F5-TTS-v2 | Yes | Free (GPU cost) | Required | Unknown | Unknown |

## Detailed Analysis

### 1. Google Cloud TTS — STRONG CONTENDER
- 44 Arabic voices total: 4 Standard, 4 WaveNet, ~30 Chirp 3 HD (15 male HD)
- Chirp 3 HD is their latest generative model with emotional/stylistic nuance
- Free tier: 1M WaveNet chars/month, 4M Standard/month
- SSML support (phoneme tags for pronunciation control)
- **8x cheaper than ElevenLabs** at Chirp HD tier

### 2. Azure Speech Services — STRONG CONTENDER
- 32 Arabic neural voices across 12 dialects (16 male voices)
- ar-SA (Saudi) voices: Hamed (male) — closest to formal MSA
- ar-EG (Egypt) voices: Shakir (male)
- **78% reduction in word-level pronunciation errors** from recent update
- Enhanced diacritics/tashkeel prediction via fine-tuned NLP model
- Free tier 0.5M chars/month
- **15x cheaper than ElevenLabs**

### 3. Amazon Polly — LIMITED
- Only 1 male Arabic voice: Zayd (Gulf Arabic ar-AE, neural only)
- No MSA male voice — **dealbreaker**

### 4. OpenAI TTS — NOT RECOMMENDED
- Limited Arabic support, English-optimized
- Multiple sources confirm Arabic is "not viable" quality
- Only 13 voices total (none Arabic-specific)

### 5. Open Source

**Habibi (F5-TTS based)**: Claims to outperform ElevenLabs v3 on speaker similarity (0.809 vs 0.567). MSA WER 7.83. Doesn't require tashkeel. CC BY-NC 4.0 — non-commercial only.

**Arabic-F5-TTS-v2**: 300h MSA training data. **Requires full tashkeel** (perfect for Alif since we already have it). Non-commercial license. Self-hostable.

## Recommendations (ranked)

1. **Google Cloud Chirp 3 HD** — Best balance of quality, price, voice variety. 15 male Arabic voices at $30/M (8x cheaper). Latest generative model.
2. **Azure Speech Services** — Best value. $16/M with 16 male Arabic voices. Explicit tashkeel improvements.
3. **Google Cloud WaveNet** — Budget option. $16/M, proven quality, 2 male voices.
4. **Habibi/Arabic-F5-TTS-v2 (future)** — Monitor for commercial licensing. Arabic-F5-TTS-v2 is perfect for Alif since it requires tashkeel.

## Suggested Next Step
Run a blind A/B comparison: generate the same 10 sentences with ElevenLabs Chaouki, Google Chirp 3 HD (best male voice), and Azure ar-SA-HamedNeural. Evaluate emphatics (ظ/ض/ط/ص), tashkeel rendering, and naturalness.
