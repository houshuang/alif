---
name: no-scp-to-server-workdir
description: "Don't scp test files into the server's git working tree — leaves untracked files that block later git pull"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 88efa212-a992-4a58-a5d9-abfcf8f5e395
---

**Don't `cp /tmp/X /opt/alif/backend/...` to test changes on the server.** If the same file later lands in main (via PR merge), `git pull` aborts with "untracked working tree files would be overwritten." You have to manually `rm` the file before pulling, and if you don't notice, `systemctl restart` runs on stale code — silently masking the deploy.

**Why:** The pull abort is a hard failure, but the subsequent restart commands in the same chained shell command still execute. You can deploy successfully-looking commands but end up running the old code.

**How to apply:**
- For backend test runs on server: use `--prefix /tmp/...` or symlink, or test in `/tmp/claude/` directly and only invoke the existing server source. Don't overwrite `/opt/alif/backend/app/...`.
- If you must test server-side with modified source: `git stash` the test changes on server before `git pull`, or use a git worktree (`git worktree add /tmp/alif-test`).
- If a deploy command chain shows a `git pull` abort, **stop and fix immediately** — don't let the rest of the chain (pip install, systemctl restart) run, because they'll act on stale code.
- After the user reports the feature isn't working post-deploy, the first thing to check is `git status` on the server for stale untracked files.

Incident 2026-05-27: confusion-capture PR #167 deploy aborted git pull, restart ran on old code, user couldn't see new picker. Caught only because I checked `systemctl is-active` (active doesn't mean current code) and verified the pull output.
