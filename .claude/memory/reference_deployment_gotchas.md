---
name: reference_deployment_gotchas
description: "Non-obvious deploy/LLM gotchas — valid model IDs, alembic on fresh DB, .env key names, TTS model"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 8328a0e8-2be9-44ef-9f97-381311ce4ea0
  modified: 2026-07-21T06:19:26.615Z
---

Deploy and LLM-config gotchas that aren't obvious from the code. (Deploy *procedure* lives in CLAUDE.md § Deployment; this is the gotcha layer.)

**Valid LLM model identifiers** (use these exact strings; wrong forms silently fall back):
- `gemini/gemini-3-flash-preview` (NOT `gemini-3-flash`), `gpt-5.2`, `gpt-5.5` (Codex / Polyglot primary), `claude-haiku-4-5`, `claude-opus-4-6`, `claude-sonnet-4-5-20250929`.
- Alif uses Claude CLI primary; Polyglot uses Codex `gpt-5.5`-primary + Claude-failover (`polyglot/app/services/llm_cli.py`). See [[feedback_codex_cli_free]].
- Anthropic API returns JSON wrapped in markdown fences — strip with regex before `json.loads()`. (CLI path: use `json_schema=` — see [[feedback_json_schema_cli]].)

**Alembic on a fresh DB**: if the initial migration is empty (`pass`), `create_all` then `stamp head`; or consolidate into one real migration. Set `ALIF_SKIP_MIGRATIONS=1` when generating new revisions.

**.env key naming**: code expects `ANTHROPIC_API_KEY` and `ELEVENLABS_API_KEY` (see `.env.example`), but the user's `.env` uses `ELEVENLABS_KEY` (no `_API`). The TTS-only key (`sk_2cea…`) can generate audio but NOT manage voices; the full-permission key (`sk_6952…`) is needed for voice-cloning API.

**TTS**: `eleven_multilingual_v2`, learner-tuned pauses (stability 0.85, similarity 0.75). SQLite `busy_timeout` 30s; `warm_sentence_cache` has a threading-lock concurrency guard; chat + story-detail commits are best-effort to survive lock contention.

**Frontend deploy** needs the Metro cache cleared, not just `systemctl restart alif-expo` — see [[feedback_expo_metro_cache_deploy]].

**Backend service env comes from `/opt/alif/.env`, NOT `/opt/alif/backend/.env`** (systemd `EnvironmentFile=/opt/alif/.env`, discovered 2026-07-20). Flags appended to `backend/.env` are invisible to the running service (pydantic settings reads it, but `os.getenv()` paths don't). Verify env took effect via `tr '\0' '\n' < /proc/$(systemctl show -p MainPID --value alif-backend)/environ`.

**Codex CLI on prod runs as root and had NO auth in `/root/.codex`** — every backend/cron codex call silently failed over to Claude CLI (Rule-13 masking) until 2026-07-20, when `ALIF_CODEX_HOME=/opt/alif/.codex` was added to `/opt/alif/.env` (the shim's `_codex_env()` honors it). If codex output quality looks like Claude, check this first.

**`pkill -f <script>` over ssh kills the ssh session itself** when the pattern appears in the remote command line — kill by PID instead.
