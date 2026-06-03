---
name: feedback_always_deploy_from_main
description: Always deploy from main; verify the prod server is actually on main at the expected commit before/after every deploy
metadata: 
  node_type: memory
  type: feedback
  originSessionId: e2c67502-7394-4fd4-bcf7-0c9a1538afd2
---

Deploys MUST go through main, the same way every time: merge the feature branch to main → `git push` → on the server `git checkout main && git pull --ff-only` → confirm HEAD is the expected commit → restart the service. Never run prod off a feature branch, and never assume the server is on main.

**Why:** On 2026-05-29, after merging PR #174 (polyglot logging) to main, I ran `cd /opt/alif && git pull && systemctl restart polyglot-backend`. The server was silently checked out on an unmerged feature branch (`sh/textbook-inflected-verb-cleanup`, incl. a "WIP untested" commit) with its upstream misconfigured, so the pull did NOT advance to main and the restart ran stale code — the new columns never got added, but `systemctl is-active` said "active" so it looked fine. The user: "this is extremely problematic, you need to update your logic, we need to always deploy in the same way (merge to main etc)."

**How to apply:**
1. Before deploying, run on the server: `git branch --show-current` and `git log --oneline -1`. If it's not `main`, STOP and investigate the branch (see below) before proceeding.
2. After `git pull --ff-only`, re-check `git log --oneline -1` equals the commit you just pushed. A bare `git pull` on a non-main branch can try to merge the wrong upstream and silently no-op.
3. A green `systemctl is-active` does NOT prove the new code/schema deployed — verify the actual effect (e.g. new columns exist: `sqlite3 ... "SELECT name FROM pragma_table_info('t')"`).
4. If prod is found on a feature branch: diff it against main (`git diff origin/main origin/<branch>`). If the branch's work is already on main (squash-merged) or is only one-off scripts that have already run, `git checkout main` is safe (lossless). Otherwise merge the branch to main FIRST, then deploy from main. Back up the DB before any data-touching step.
5. Watch for untracked files left in the server working tree (e.g. an untracked `backend/app/services/*.py`) — they signal a prior scp-to-prod and can mask state. Related: [[feedback_no_scp_to_server_workdir]], [[project_llm_cli_monitoring]] (push-before-deploy), [[feedback_bg_task_exit_code_misleading]] (don't trust the status line, verify the effect).
