---
name: Prefer in-session work over claude -p subprocess
description: When the task is a text transformation I can do directly (vocalization, translation, alignment, classification), write the output to a file myself instead of shelling out to `claude -p`. Subprocess-CLI is for scripts the USER runs standalone, not for work I'm doing in context.
type: feedback
originSessionId: f97acdb4-65d1-4c94-9faf-e5602512c4d4
---
Prefer in-session output over `claude -p` subprocesses for any text-transform task I have in context.

**Why:** 2026-04-22 session wasted 1-2 hours on 4 failed attempts to vocalize classical Arabic via the CLI (Sonnet timeouts, Haiku 2-3min/batch × 34 batches, sandbox blocking the CLI from writing `~/.claude.json`). When the user said "just do it yourself — I AM Claude after all", I vocalized 5 paragraphs directly in one Write call in ~5 minutes with zero failures. Same for sentence-pair alignment: 177 pairs produced directly, zero LLM roundtrips.

**How to apply:**
- If the task is "vocalize this", "translate this", "align these sentences", "classify these items", "gloss these words" — and I have the input text in context — write the output directly via Write/Edit. Don't delegate to `claude -p`.
- Reserve subprocess-CLI (`claude -p` via `bookify_arabic.py` ingest, `import_scaffold_lemmas.py`, etc.) for scripts the user runs standalone, for background cron jobs, or for per-item cost-logged work at scale (hundreds+ items).
- If you DO need `claude -p` in-session: pass `dangerouslyDisableSandbox: true` (sandbox blocks `/Users/stian/.claude.json` writes, which the CLI needs every call). Probe with `echo hi | timeout 30 claude -p --output-format text` first; the generic `Claude CLI failed` error hides real causes.
- Prompt-length matters: Sonnet CLI has 240s timeout. Classical-Arabic vocalization roughly doubles char count, so 2K+ input = risk of timeout. If I must use CLI, keep output <2K chars.

**Signals that I've started down the wrong path:**
- Building scripts that batch items for `claude -p` processing when I can just do them directly.
- Running monitors to watch batch completion for 15+ minutes instead of outputting text to files.
- Repeatedly tuning batch size / timeout / model (sonnet → haiku → sonnet) when the issue is "use the LLM in context, not the subprocess".
