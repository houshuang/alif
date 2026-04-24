---
name: PR self-review and merge without user approval
description: User never reviews PRs — Claude must self-review thoroughly and merge without pausing to ask
type: feedback
originSessionId: 21cab0f0-2b3d-40c3-8d16-69b5be5e565f
---
Claude owns the entire PR lifecycle for non-trivial changes: create branch, commit, open PR, self-review, merge. Do NOT pause after opening a PR to ask whether to merge; do NOT ask for approval after self-review passes.

**Why:** User explicitly told me this (2026-04-17) after I paused and asked for merge approval on PR #36 (write-lock refactor). User's exact phrasing: "I never want to review, but you should always self-review." Pausing to ask is friction the user doesn't want. The self-review IS the gate.

**How to apply:**
- For Alif specifically, the rule is now in CLAUDE.md §7 (non-trivial branch workflow).
- For any codebase: when the user says to do work, and the work involves opening a PR as a self-review mechanism, merge immediately after self-review passes. Only pause if self-review surfaces a real issue that needs user input.
- Self-review means reading the actual diff (`gh pr diff <N>`), not just stats — trace through logic equivalence in refactors, check for silent deletions in append-only files, verify no schema drift.
- Deploy is a separate decision. Merging does NOT imply deploying. Wait for explicit "deploy" from user before running the deploy command.
- If in doubt (the change genuinely has risk the user should weigh in on), ask once, and remember the answer — but default to merge-after-self-review.
