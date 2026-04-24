---
name: Use json_schema for CLI JSON responses
description: CLI models wrap JSON in explanation text — use --json-schema constrained decoding instead of json_mode=True to guarantee valid JSON
type: feedback
originSessionId: 556a0c93-ebd8-43cf-851c-5ce6763d2092
---
Always use `json_schema=` parameter (not `json_mode=True`) when calling `generate_completion()` for CLI models that need structured JSON responses.

**Why:** CLI models (Sonnet/Haiku via `claude -p`) return explanation text before JSON blocks. The old parser only checked `text.startswith("```")`, so fenced JSON after explanation text silently failed to parse. This caused ALL verification calls to fall through to API Haiku (weakest model), which missed obvious mapping errors. Bug discovered 2026-04-14 — 1,225 wasted LLM calls on a single day.

**How to apply:** When adding new `generate_completion()` calls that expect JSON from CLI models, define a JSON schema dict and pass it as `json_schema=`. The CLI uses `--json-schema` for constrained decoding — the model can ONLY produce valid JSON matching the schema. The `json_mode=True` fallback still works (improved text extraction) but is less reliable.
