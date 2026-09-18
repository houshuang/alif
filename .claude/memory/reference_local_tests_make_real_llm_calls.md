---
name: reference-local-tests-make-real-llm-calls
description: "Alif's \"fast\" pytest suite makes real claude/codex CLI and OpenAI API calls on Stian's Mac; stub the CLIs and clear keys or a 2-minute suite takes 30+ minutes and spends quota."
metadata: 
  node_type: memory
  type: reference
  originSessionId: bdd48af5-f43d-4f05-a489-a14093ea8dab
  modified: 2026-09-17T18:35:29.376Z
---

Running `cd backend && python3 -m pytest` locally spawns real `claude -p` and
`codex exec` subprocesses (both are on PATH), and when those fail it falls
through to the paid OpenAI chain, because the login shell exports
`OPENAI_API_KEY` and `GEMINI_API_KEY`. CLAUDE.md's "~2 min" holds only where no
LLM CLI or key is reachable. Observed 2026-09-17: 68 tests took 349 s, a full
suite ran 30+ minutes at ~0% CPU (blocked on HTTPS), with three concurrent
`codex exec` children.

Run it like this instead — a stub dir whose `claude`/`codex` exit 127, plus
cleared keys, so any unmocked call fails fast:

```sh
printf '#!/bin/sh\nexit 127\n' > "$TMPDIR/nocli/claude"   # + same for codex, chmod +x
PATH="$TMPDIR/nocli:$PATH" env -u OPENAI_API_KEY -u GEMINI_API_KEY \
  -u ANTHROPIC_API_KEY OPENAI_KEY= GEMINI_KEY= ANTHROPIC_API_KEY= \
  python -m pytest -q
```

Inside the Bash sandbox the same calls hang on blocked network instead, which
looks identical to a hung test. `ps` is blocked in the sandbox; check with
`dangerouslyDisableSandbox` plus `lsof -p <pid> -a -i` and `sample <pid> 1` —
see [[reference_macos_sim_perf_gotchas]].

The repo-side fix (mock generation in the fast suite) is filed in `IDEAS.md`
under the 2026-09-17 entry.
