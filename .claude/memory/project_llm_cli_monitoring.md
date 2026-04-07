---
name: Claude CLI migration monitoring
description: Monitor Claude CLI LLM health after migration — llm.py was never deployed until 2026-04-03, need to verify Gemini fallback calls drop to zero
type: project
---

Claude CLI migration (2026-04-01) was only committed locally — the server ran old code with Gemini still in the API fallback chain for 3 days, causing 3,600+ wasted Gemini calls/week (expired key, 58.9% failure rate). Fixed and deployed 2026-04-03.

**Why:** The migration looked complete locally but was never deployed. Discovered during a routine LLM health check. The system still worked (GPT-5.2 caught the fallback after Gemini failed) but added unnecessary latency and noise.

**How to apply:** In the next session or two, re-run the LLM health check to verify:
1. Gemini calls in `llm_calls` logs drop to near-zero (only OCR tasks should remain)
2. Claude CLI success rate stays at ~99.9%+
3. GPT-5.2 fallback calls decrease significantly (most were triggered by Gemini-first-then-fail path)
4. Total API cost stays near $0 for alif (petrarca costs are separate)

Check command: `scp /tmp/claude/llm_health.py alif:/tmp/ && ssh alif 'docker cp ... && docker exec ...'` or write a fresh analysis script against `data/logs/llm_calls_*.jsonl`.
