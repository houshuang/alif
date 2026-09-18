---
name: reference-local-tests-make-real-llm-calls
description: "Alif's fast pytest suite is hermetic since 2026-09-18 (provider guard); a BACKSTOP line in the pytest summary means a new provider path bypassed it."
metadata: 
  node_type: memory
  type: reference
  originSessionId: bdd48af5-f43d-4f05-a489-a14093ea8dab
  modified: 2026-09-18T12:00:00.000Z
---

Until 2026-09-18 a plain `cd backend && python3 -m pytest` on Stian's Mac spawned
real `claude -p`/`codex exec` and fell through to the paid OpenAI chain (keys
came from the shell and from `backend/.env`): 396 calls from 127 tests, 30+
minute runs. `backend/tests/provider_guard.py` (installed by `conftest.py`) now
blanks credentials, stubs the three provider runners, and disables
provider-bound daemon threads; the suite runs in ~45 s with zero provider
calls, no stub PATH or `env -u` needed. Details in CLAUDE.md § Testing.

If the pytest summary prints a red `BACKSTOP` line, some code reached a
`claude`/`codex` spawn or a non-loopback socket without going through
`limbic…claude_cli.generate`, `codex_cli.generate_via_codex_cli` or
`litellm.completion`. Extend the guard; don't mock around it. A new
background thread that calls an LLM must use `background_threads.start_daemon`.

Inside the Bash sandbox, `bind()` on a local socket fails with EPERM, and `ps` is
blocked; see [[reference_macos_sim_perf_gotchas]].
