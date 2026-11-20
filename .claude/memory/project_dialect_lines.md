---
name: project_dialect_lines
description: "Dialect tab (Layl TV episodes + Egyptian clips as single lines, grouped by expression) — where the content lives and how episodes get in"
metadata:
  node_type: memory
  type: project
  originSessionId: b7385c3c-1194-4fcd-babf-60beadeeeb32
  modified: 2026-11-20T14:34:50.170Z
---

Dialect Lines shipped 2026-11-20 (PR #333, OTA): the Arabic tab bar's "Dialect" tab; Explore and Podcast moved to More at Stian's request. First phone use pending; he wants to use it "when out and about", not while driving (no car mode, no spoken English).

**Content is outside the repo:** builder and README in `~/Dropbox/Arabic/dialect-lines/` (`build_lines.py` → `out/lines.json` + `laylNN.m4a`), uploaded to `/srv/media/dialect/` on alif (`backend/data/dialect` symlink). No app update needed for new episodes. Study packs per episode in `~/Dropbox/Arabic/Layl/epNN-study-pack/`; show list in `~/Dropbox/Arabic/SHOWS.md`.

**Recording pipeline (worked 2026-11-20):** BlackHole 2ch + `ffmpeg -f avfoundation -i ":BlackHole 2ch"` while Shahid plays; Soniox `stt-async-v5`; cross-check with Deepgram Nova-3, OpenAI and local MLX Whisper large-v3-turbo. Where the API keys live: `~/Dropbox/Arabic/dialect-lines/README.md` (kept out of this public repo). gpt-4o-transcribe needs 2-min chunks and no prompt (it echoes the prompt). Whisper/GPT hallucinate over music. Full comparison and lessons: `~/Dropbox/Arabic/ASR-NOTES.md` (Soniox 85% vs Deepgram 67%, GPT-4o 63%, Whisper 60% on ep. 30).

**Why:** Stian wants to study TV dialect atomically instead of scrolling TikToks.

**How to apply:** left out of v1 for his decision: expression re-listen schedule, expressions list, background/lock-screen audio (native build), speaker filter, repeat-after-me. Layl character facts: Rana is Nabil's wife (ep18 pack guessed wrong); Najm (Syrian actor) says مو. Related: [[project_egyptian_clips]].
