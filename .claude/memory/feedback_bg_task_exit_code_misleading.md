---
name: feedback-bg-task-exit-code-misleading
description: "Background-task \"exit code 0\" notification can lie — always tail the output file before claiming green"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 804f4e51-36db-4774-8570-b08d59c3ba91
---

When a background `Bash` invocation completes, the system-reminder notification shows an exit code summary like "completed (exit code 0)." Don't trust this in isolation — it can be wrong.

Concrete case: 2026-05-21, polyglot full test sweep `bdktjmtyn` got "exit code 0" notification, but tailing the output file showed `1 failed, 218 passed`. I had already merged PR #114 by the time I noticed the discrepancy. The failure was in `tests/test_lemma_quality.py::test_partial_batch_failure_leaves_page_unverified` — pre-existing, not caused by my work, but the principle stands: I would have caught it pre-merge if I'd read the output.

**Why:** The harness reports exit code from the wrapper / process orchestration, which can succeed even when the wrapped command produced failure output. pytest specifically: a configuration or fixture-setup detail can make pytest exit 0 while still printing FAILED lines.

**How to apply:** Before merging a PR that depends on a "tests pass" claim from a background task, always `tail -5` the output file even when the notification says exit 0. Look for the pytest summary line — `N passed, N failed` is the source of truth, not the notification.
