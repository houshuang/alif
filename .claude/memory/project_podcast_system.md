---
name: Podcast passive listening system
description: Personalized Arabic listening podcast system using Alif's SRS data + ElevenLabs TTS, launched 2026-03-22
type: project
---

Podcast system for passive Arabic listening practice (walking, no phone interaction).

**Why:** User's key insight from Michel Thomas: memory management during passive listening is what matters. Alif has the SRS data to do this algorithmically — no other audio course can personalize to the learner's exact vocabulary state.

**How to apply:** When working on podcast features, remember: (1) 95% known word threshold for listening (much higher than reading's 60%), (2) 5-8 new words per 30-min episode, (3) segment-level caching makes regeneration cheap, (4) Arabic/English generated as separate TTS calls to avoid accent bleed.

**Current state (2026-03-22):**
- Sampler with 6 formats generated and deployed (15.6 min, playable in Podcast tab)
- Backend: `podcast_service.py`, `routers/podcast.py`, `scripts/generate_podcast_sampler.py`
- Frontend: `podcast.tsx` with expo-av player, background audio
- ElevenLabs: Chaouki voice for both Arabic (0.75x) and English (1.0x), Multilingual v2 model
- Cost: ~10K chars per 15-min episode. Creator plan ($11/mo) supports ~3 full episodes/month

**Pending user feedback:** Which of the 6 formats feels best on a walk? Story Breakdown and Hybrid scored highest in research.
