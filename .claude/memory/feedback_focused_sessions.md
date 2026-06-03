---
name: feedback_focused_sessions
description: Prefer focused sessions — multi-feature sessions repeatedly blow the context window
metadata:
  type: feedback
---

Long sessions that touch multiple features (UI + backend + deploy + data analysis) frequently blow the context window — ~20 sessions had to be continued from context overflow.

**Why:** Alif spans a Python backend, an Expo frontend, deploy/ops, and data analysis; pulling all four into one session exhausts context before the work finishes, and the compaction/handoff loses fidelity.

**How to apply:** Prefer focused sessions. If a task has 4+ distinct parts, suggest breaking it into separate sessions up front. Commit working intermediate states early (CLAUDE.md Rule #12) so a mid-session compaction doesn't lose work.
