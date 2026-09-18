---
name: parallel-sessions-shared-checkout
description: Parallel Claude sessions share the alif working tree — verify current branch immediately before committing; a checkout/pull from another session can silently move HEAD off your feature branch
metadata: 
  node_type: memory
  type: feedback
  originSessionId: f83d1718-83ff-4128-b3b5-e1493721e157
---

The user runs multiple concurrent Claude sessions in the same `~/src/alif` checkout. On 2026-07-15, a parallel session ran `git checkout main && git pull` (merging its PRs #211/#212) mid-way through my feature work: my later `git commit` landed on **main** instead of my feature branch, and my earlier-created branch ref pointed at a stale commit, so `gh pr create` failed with "No commits between main and branch".

**Why:** branch creation at session start does not pin HEAD — any other session (or the user) can switch branches in the shared working tree at any time. Uncommitted file edits survive the switch, so nothing visibly breaks until refs are wrong.

**How to apply:**
- Run `git branch --show-current` in the same command as `git commit` (e.g. `git branch --show-current && git commit …`), and re-check before `git push`/`gh pr create`.
- If a commit lands on main by accident: `git checkout -B <feature-branch> <commit>`, `git branch -f main origin/main`, force-push the feature branch, then PR.
- After any unexplained ref weirdness, also re-run the test suites: mid-session pulls mean earlier test runs may have exercised different code than what you committed. Related: [[focused-sessions]] and CLAUDE.md Rule 12 (commit incrementally).
