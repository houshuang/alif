---
name: reference-server-claude-token
description: "Where the alif server's Claude CLI auth lives (CLAUDE_CODE_OAUTH_TOKEN since 2026-11-18) and how to swap accounts"
metadata:
  node_type: memory
  type: reference
  originSessionId: 1b0b8c97-9af4-4ae1-8fe2-29579594aaaa
  modified: 2026-11-18T13:12:13.625Z
---

Since 2026-11-18 the server's Claude CLI authenticates with a 1-year `CLAUDE_CODE_OAUTH_TOKEN`
(from `claude setup-token` on the Mac, new account), not `/root/.claude/.credentials.json`
(old account, kept; the env var overrides it).

- Set in `/opt/alif/.env` (alif-backend + polyglot-backend via EnvironmentFile) and
  `/etc/environment` (cron and ssh via pam_env). Backups `*.bak-20261118_131134`.
- Swap accounts: Stian runs `claude setup-token` locally, replace the line in both files,
  `systemctl restart alif-backend polyglot-backend`. Test with `claude -p "reply ok"` in a fresh ssh.
- "You've hit your weekly limit" from the server = the account behind this token is exhausted.
- Don't copy the Mac's keychain credentials to the server: both sides would refresh the same
  rotating session and log each other out.

Related: [[project-codex-switched-off]] (Codex routing; sentence generation never uses Codex).

**2026-11-25:** the token account hit its weekly limit (resets 2026-11-29 03:00 UTC). At Stian's
request the `CLAUDE_CODE_OAUTH_TOKEN` line is commented out in both files, so the server uses its
own stored login (`/root/.claude/.credentials.json`), which worked. Backups `*.bak-20261125_142413`.
To go back, uncomment the line in both files and restart the two backends. `/etc/environment`
also feeds the other projects' cron jobs on the server.
