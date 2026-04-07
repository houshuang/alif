---
name: Voice Cloning & TTS Setup
description: ElevenLabs voice clone details, voice IDs, PVC vs IVC, TTS alternatives research
type: project
---

## Voice Identity
- Current production voice: `G1HOkzin3NMwRHSq60UI` = **"Arabic Knight" PVC** (NOT stock "Chaouki")
- This is a Professional Voice Clone of @roots_of_knowledge Arabic teacher
- Source: youtube.com/@rootsofknowledge (55K TikTok followers, 25 years teaching Arabic)
- Original writeup: `research/voice-cloning-writeup-2026-03-01.html`

## Voice IDs
| ID | Name | Type | Status |
|----|------|------|--------|
| `G1HOkzin3NMwRHSq60UI` | Arabic Knight | PVC | **In production** |
| `zZplJlGYgfVjN9bBzAWS` | RootsOfKnowledge PVC | PVC | Pending verification (77 min, 15 files) |
| `CgiZNnLDkBFp39WsQkMb` | RootsOfKnowledge v2 | IVC | Ready (44 min curated) |
| `JzW9vpzYBpT1rNWw2hbd` | RootsOfKnowledge IVC | IVC | Original clone (old) |
| `5Spsi3mCH9e7futpnGE5` | Fares | PVC | Ready |
| `IK7YYZcSpmlkjKrQxbSn` | RAED | PVC | Ready |
| `OFHP1Qg30FPoNfkUFFlA` | Adam Narrator | PVC | Ready |
| `a1KZUXKFVFDOb33I1uqr` | Salma | PVC | Ready (female) |

## IVC vs PVC
- **IVC** (Instant Voice Clone): Maps audio onto generic multilingual model. Captures timbre/rhythm but model's own phoneme mappings override speaker's distinctions. That's why emphatics (ظ/ض) get flattened.
- **PVC** (Professional Voice Clone): Fine-tunes model weights on speaker's audio. Learns actual phoneme inventory. Should preserve emphatic distinctions.

## Known Issue: ظ/ض Confusion
User noticed نَظَرَ (nazara, to look) pronounced as "naddara" — the IVC/multilingual model confuses ظ (interdental fricative) with ض (dental stop). Most Arabic dialects have merged these, so training data may be dialect-biased. PVC should fix this.

## PVC API (Multi-Step)
1. `POST /v1/voices/pvc` — create metadata (returns voice_id)
2. `POST /v1/voices/pvc/{id}/samples` — upload audio (≤11MB each, must pass Arabic language detection)
3. `POST /v1/voices/pvc/{id}/verification` — manual verification (needs PNG/PDF)
4. `POST /v1/voices/pvc/{id}/train` — start async training

**Why:** ElevenLabs API key permissions matter. The old key (`ELEVENLABS_KEY` in .env) only had TTS permission. New key with full permissions needed for voice management.

**How to apply:** When switching voices, update `DEFAULT_VOICE_ID` in `tts.py` and `ARABIC_VOICE_POOL`. One-line change + deploy.

## TTS Alternatives (2026-03-22)
- Google Chirp 3 HD: 15 male Arabic voices, $30/M chars (8x cheaper), demo at cloud.google.com/text-to-speech
- Azure Neural: 16 male Arabic voices, $16/M chars (15x cheaper), 78% pronunciation error reduction, ar-SA-HamedNeural
- Full research: `research/tts-alternatives-2026-03-22.md`
