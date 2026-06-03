---
name: feedback-codex-cli-free
description: "Codex CLI is free via subscription (like Claude Max). Default to Codex for polyglot LLM work locally, not just in prod."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: ba0f3f41-1272-4e9a-b509-f4983892fa09
---

The Codex CLI (`codex` binary, ~/.codex auth) is free under the user's Codex
subscription, equivalent to Claude CLI being free under the Max plan. I'd been
treating Codex as metered/API-billed and Sonnet as the free default — wrong.

**Why:** Both CLI subprocesses are free, so the choice between them should be
on speed, prose quality, and production-consistency — not cost. Production
polyglot already uses Codex `gpt-5.5` as primary with Claude as live failover
(see `polyglot/CLAUDE.md` Hard Invariant 7); local should match.

**How to apply:**
- For any new polyglot script or background task, default to Codex. The
  `polyglot/.env` now sets `POLYGLOT_LLM_PROVIDER=codex`, so `llm_cli.provider()`
  picks Codex unless explicitly overridden.
- When asked "what LLM are you using?" in polyglot context, the answer is
  Codex `gpt-5.5` (via codex CLI), with Claude Sonnet 4.5 as the failover.
- For Alif (the Arabic backend), the rule is unchanged — Claude CLI primary
  per CLAUDE.md ("Always prefer Claude CLI"). This applies to polyglot only.
- Speed difference observed 2026-05-26 on LLPSI Latin generation:
  ~24s/call Codex vs ~84s/call Sonnet — ~3.5× faster for prose generation
  via the polyglot llm_cli subprocess path. See [[project-polyglot-latin-live]].
- Don't quote Codex pricing as a cost concern in tradeoff discussions.
