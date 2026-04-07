---
name: Mapping correction pipeline review
description: Mapping correction pipeline deployed 2026-03-14, review cost/effectiveness by 2026-03-21
type: project
---

Mapping correction pipeline deployed 2026-03-14. Instead of discarding sentences with wrong lemmatizations, the pipeline now asks Gemini to suggest correct lemmas and fixes them inline.

**Why:** Deterministic clitic stripping errors meant some words (وصف, طائر, ذهب) could never get sentences — every attempt produced the same wrong mapping and was discarded.

**How to apply:**
- By 2026-03-21, check `data/logs/mapping_corrections_*.jsonl` on the server to evaluate cost and success rate
- Monitor user flag rate — should decrease if corrections are working
- If too costly or low success rate, consider switching to Claude Haiku (free via CLI) instead of Gemini for the verify+correct step
- Key logs: `ssh alif "docker exec alif-backend-1 cat /app/data/logs/mapping_corrections_$(date +%Y-%m-%d).jsonl"`
