---
name: project-walk-episodes
description: "Walk episodes (hands-off two-host Arabic audio, daily cron) — state, where things live, what is still unanswered"
metadata:
  node_type: memory
  type: project
  originSessionId: b204d7af-e240-472b-90f4-0ac3ff0dd2c5
  modified: 2026-11-21T12:17:37.170Z
---

Walk episodes went live 2026-11-21 (PRs #339, #340): daily two-host Arabic conversation for Stian's walk, written only from firm words (FSRS stability ≥ threshold), in the Podcast screen and a private podcast feed. Successor to the March 2026 podcasts ([[project-podcast-system]]), which he stopped using after a week.

**Where:** generator `backend/scripts/generate_walk_episode.py` (`write`/`voice`/`daily`); shared logic `app/services/walk_episodes.py`; cron `deploy/alif-walk-daily.sh` at 04:00; state `data/podcasts/walk-state.json` on prod; log `/var/log/alif-walk-daily.log`; research and sources `research/walk-audio-2026-11/README.md`; experiment-log entries "2026-11-21 (2)" and "(3)".

**Why it is shaped this way:** on 2026-11-21 he knew 2,690 words in print but 2,786 of 2,841 scheduled words had never been heard; research says listening alone teaches new words poorly, so audio carries no new words and gives no FSRS credit.

**How to apply:**
- The daily job is the only unattended speech spend. It stops after four episodes with no in-app completion or rating; do not remove that rule. If he says episodes stopped arriving, check ratings first.
- Still unanswered by Stian (asked 2026-11-21): too easy/right/too hard, pace, ElevenLabs vs Gemini voice sample, whether "no new words in audio" is acceptable, and why he dropped the March podcasts. Topic list is the assistant's choice until he adds his own.
- I cannot hear audio: pronunciation and pacing were never checked by ear.
- Readout (after ≥10 episodes heard): reading-review results of repeated weak words vs the held-out half (`in_audio_arm`).

**Bridge episodes (pilot 2026-11-21, PR #341, Stian's idea):** `backend/scripts/generate_bridge_episode.py` wraps an authentic recording: fragment → hard words with English → simple-Arabic retelling → fragment again. First episode: Al Jazeera *Rumooz* on Ibn Khaldun (17.8 min from 2.7 min of source). Made by hand per recording: transcribe locally with Soniox (key in `~/src/otak/scripts/transcribe_podcast.py`), upload audio + tokens to alif `/tmp`, run `prepare` then `voice`. He has not yet said which script order works; variants and candidate sources are listed in IDEAS.md under "Bridge episodes".
