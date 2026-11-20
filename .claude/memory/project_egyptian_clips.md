---
name: project_egyptian_clips
description: How Egyptian TikTok clips get into the Alif app; content is outside the public repo and the build lives in ~/Documents
metadata:
  node_type: memory
  type: project
  originSessionId: 5734fc38-df6e-4adc-8997-433b18c0fb11
  modified: 2026-11-08T19:08:00.348Z
---

**To pick up:** read the handoff named below and its `.record.md`. Paused 2026-11-08 with 25 clips live and deployed (steps: Words, Listen, Blind, TikTok). Next: Stian tries the first clips at 0.5×/0.6×. If still too hard, build a gentler on-ramp; if they work, process more clips from `clips.tsv`. Still unanswered: what confused him about the removed second tab.

Egyptian Clips first shipped in the Alif iOS app on 2026-11-08 (PR #294, More → Egyptian Clips). The user wants this inside the app, not as a separate website; the phone is not on Tailscale, so Tailscale links do not reach it.

First real use (2026-11-08): too fast, hard to match speech to text, Shadow tab confusing. Handoff: `~/Documents/tiktok-egyptian-pilot/SESSION-HANDOFF-20261108-egyptian-clips.md`. The rebuild (PR #297) uses phrases, word highlighting, a Words step and slowest-first order; if the slowest clips (~2.1 words/s) stay too hard, add a graded on-ramp rather than more TikToks.

To add clips: add to `~/Documents/tiktok-egyptian-pilot/build/batch3.py` (lines reference TikTok caption cue ranges; Scribe output goes in `build/scribe.json`), run `build.py` there, and scp `clips.json` plus `video/<id>.mp4` to `/srv/media/egy-clips/` on alif (`backend/data/clips` is a symlink to it). No app update is needed for new clips.

**Why:** the repo is public and clips and transcripts are third-party content for private use, so they must stay out of Git and behind the private API capability.

**How to apply:** download TikTok with a video format (`b[vcodec^=h264]`), because yt-dlp's `audio` format is the music track. Reconcile TikTok's `ara-SA` captions with ElevenLabs Scribe; Alif's Gemini key had no credit on 2026-11-07. `~/Documents/tiktok-egyptian-pilot/clips.tsv` indexes the remaining candidates. Skip adverts.
