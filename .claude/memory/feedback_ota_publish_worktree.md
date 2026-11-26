---
name: feedback-ota-publish-worktree
description: publish-ios-update.sh needs a clean checkout on branch main; publishing from a worktree with --ignore-other-worktrees is safe only without pulling there
metadata:
  node_type: memory
  type: feedback
  originSessionId: 2da4fada-f0f3-48c0-b683-65041feaca08
  modified: 2026-11-26T09:57:38.414Z
---

`scripts/publish-ios-update.sh` refuses a dirty checkout and anything but branch `main` at
origin/main. When the primary checkout is dirty (other sessions' `.claude/memory` edits), publish
from a worktree: `git worktree add --detach … origin/main`, `cp -cR` node_modules, then
`git checkout --ignore-other-worktrees main` there and run the script.

**Never `git pull` / move `main` inside that worktree.** On 2026-11-26 a pull there advanced the
shared `main` ref while the primary checkout's index and files stayed at the old commit, so the
primary showed the reverse of the new PR as staged changes. Fixed by checking that those paths
equalled the old commit, then `git reset -- <paths>` and `git checkout HEAD -- <paths>`.

**Why:** branch refs are shared across worktrees; only index and files are per worktree.
**How to apply:** fast-forward the primary checkout first (`git merge --ff-only origin/main`,
which leaves unrelated dirty files alone), then create the publish worktree at that commit.
Related: [[feedback-parallel-sessions-shared-checkout]].
