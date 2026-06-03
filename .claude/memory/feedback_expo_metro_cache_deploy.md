---
name: feedback_expo_metro_cache_deploy
description: "Frontend deploys need the Metro cache cleared, not just alif-expo restart"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: cad9c2d2-24e4-4a6f-85ce-d610dc476af9
---

`systemctl restart alif-expo` (the documented frontend-deploy step) restarts the
Expo/Metro dev server but does NOT clear Metro's transform cache, so it can keep
serving a STALE JS bundle after a `git pull` — frontend code changes silently
don't appear. Symptom (2026-05-25, Latin launch): the Latin Stats screen showed
Greek's acquisition count (145) even though the deployed source read the active
language and the backend correctly returned 0 — a cached pre-fix bundle.

**Why:** Metro caches transforms in `/tmp/metro-*`, `/tmp/haste-map-*`,
`frontend/node_modules/.cache`, `frontend/.expo`. The systemd unit runs plain
`npx expo start --port 8081` (no `-c`), so a restart reuses the cache. The
browser/Expo client also caches the bundle.

**How to apply:** after deploying frontend changes, clear the cache then restart:
`ssh alif "rm -rf /tmp/metro-* /tmp/haste-map-* /opt/alif/frontend/node_modules/.cache /opt/alif/frontend/.expo; systemctl restart alif-expo"`, and tell the user to hard-reload (Cmd+Shift+R) / fully reload the Expo client. First load rebuilds cold (~30-60s) then serves fresh. Backend (`alif-backend`/`polyglot-backend`) restarts don't have this issue — it's Metro-specific. Related: [[project_ios_dev_build_gotchas]].
