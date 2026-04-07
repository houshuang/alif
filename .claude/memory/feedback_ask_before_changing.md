---
name: Ask before changing design decisions
description: Don't change intentional architecture decisions (like API vs CLI for chat speed) without asking first
type: feedback
---

Don't change intentional design decisions without asking. The chat endpoint uses direct Anthropic API (`model_override="anthropic"`) instead of Claude CLI because interactive chat needs to be fast — CLI subprocess startup adds latency. This was a deliberate choice.

**Why:** User corrected me (2026-04-06) after I changed `model_override="anthropic"` to `model_override="claude_haiku"` without asking. The user has stated this preference before.

**How to apply:** When diagnosing bugs, investigate the actual cause rather than changing the architecture. If a fix involves changing an intentional design pattern, ask first.
