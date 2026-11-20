---
name: project-codex-switched-off
description: "Codex routing on prod alif + polyglot: switched off 2026-11-15, switched back on 2026-11-18; where the switches live"
metadata:
  node_type: memory
  type: project
  originSessionId: 1b0b8c97-9af4-4ae1-8fe2-29579594aaaa
  modified: 2026-11-18T10:07:39.196Z
---

Codex routing on prod was switched off on 2026-11-15 (Codex token revoked, no credit) and
**switched back on 2026-11-18** at Stian's request, after `codex login` worked again on alif.

Switches, current values:
- `/opt/alif/.env`: `ALIF_AUDIT_PROVIDER=codex` (alif-backend and polyglot-backend load it)
- `/opt/alif/polyglot/.env`: `POLYGLOT_LLM_PROVIDER=codex`
- root crontab: the `ALIF_AUDIT_PROVIDER=claude` / `POLYGLOT_LLM_PROVIDER=claude` prefixes are
  removed (cron does not load the systemd env files, so a prefix there overrides .env)
- Kept: `ALIF_CLAUDE_HAIKU_CLI_MODEL=sonnet` in `/opt/alif/.env` and on the alif cron line; it
  only affects the Claude fallback (Claude CLI on haiku took 52–68 s per structured call)
- Backups of the switch-back: `*.bak-20261118_100705` next to each file, `/root/crontab.bak-20261118_100705`

Verified 2026-11-18: a `claude_haiku` call logged `codex_cli/gpt-6.1-sol`.

Login revoked again 2026-11-18 ~21:00 (401 "invalidated oauth token"); Stian renewed it
2026-11-19. Since PR #326 a 401 starts the 6-hour Codex cool-down in `codex_cli.py`, so a dead
login costs one failed call per process, not 30 s on every call. Dense due-word sentences
are written by Codex first since 2026-11-19 (`gpt_then_sonnet`; `ALIF_DUE_MENU_WRITER=claude_sonnet`
restores Sonnet), so that one generation call does route through Codex.

**Why:** a dead Codex token makes every audit call wait ~30 s for Codex and then fall back, so
switch off the same way if `codex login status` fails again.
**How to apply:** ordinary sentence and story generation (`claude_sonnet`) never route through Codex;
when the Claude CLI on the server hits its limit, generation stops regardless of this switch.
