---
name: Claude CLI migration lesson
description: Lesson learned — local code changes aren't deployed until pushed+deployed. CLI migration was committed but not deployed for 3 days (2026-04-03).
type: project
---

**Status: Resolved.** Claude CLI migration deployed and healthy since 2026-04-03.

**Lesson learned:** The CLI migration (2026-04-01) was committed locally but never deployed. Server ran old Gemini-first code for 3 days, causing 3,600+ wasted Gemini calls/week (expired key, 58.9% failure rate). GPT-5.2 fallback masked the issue.

**Why this matters:** Push-before-deploy rule exists for this reason. Always verify server is running the expected code after deploy.

**How to apply:** LLM health check script against `data/logs/llm_calls_*.jsonl` on server. Gemini calls should be OCR-only. Claude CLI success rate ~99.9%+.
