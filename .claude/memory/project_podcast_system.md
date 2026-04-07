---
name: Podcast passive listening system
description: Personalized Arabic listening podcast system using Alif's SRS data + ElevenLabs TTS, launched 2026-03-22
type: project
---

Podcast system for passive Arabic listening practice (walking, no phone interaction).

**Why:** User's key insight from Michel Thomas: memory management during passive listening is what matters. Alif has the SRS data to do this algorithmically — no other audio course can personalize to the learner's exact vocabulary state.

**How to apply:** When working on podcast features, remember: (1) 95% known word threshold for listening (much higher than reading's 60%), (2) 5-8 new words per 30-min episode, (3) segment-level caching makes regeneration cheap, (4) Arabic/English generated as separate TTS calls to avoid accent bleed.

**Current state (2026-04-07):**
- 4 formats in production: story, book, ci (comprehensible input), repetition
- Backend: `podcast_service.py`, `routers/podcast.py`, generation scripts in `scripts/`
- Frontend: podcast tab with expo-av player, completion tracking
- ElevenLabs: PVC voice (Arabic Knight), Multilingual v2 model
- Auto-generation cron maintains >=4 unheard episodes
- `generate_repetition_podcasts.py` for acquiring-word-targeted episodes (3-4x repetition)
