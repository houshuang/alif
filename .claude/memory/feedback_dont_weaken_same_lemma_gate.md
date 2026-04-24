---
name: Never weaken the apply_corrections "same_lemma" rejection
description: The same_lemma path in apply_corrections is intentional hardening. Read the experiment log before touching any mapping-correction code.
type: feedback
originSessionId: 77a5635d-fbbc-4157-a4a2-b630376eabf3
---
The `same_lemma` rejection in `apply_corrections` (`backend/app/services/sentence_validator.py`) is INTENTIONAL HARDENING added over 8+ commits from 2026-03-17 to 2026-04-16 to close the wrong-lemma-sentence class of bugs. It reads like a downstream "bug" where the LLM verifier says "this mapping is wrong" but returns the same lemma_id and the sentence gets rejected — but that's exactly correct: it means the correct lemma isn't in the vocab DB, so rejecting the sentence is the right call.

**Why:** I (Claude) twice tried to "fix" this into an accept path because it looked like a false-failure blocking throughput. The user caught me both times. They finally made me add prominent in-code guards (docstring + inline comment) with pointers to the specific experiment-log entries (2026-03-17 "Stop Auto-Creating Lemmas", 2026-03-21 "85% bad-mappings problem", 2026-04-16 commit 25fc702 apply_corrections extraction).

**How to apply:** Before touching any mapping-correction / apply_corrections / same_lemma / correct_mapping code, read the in-code guard block AND the three experiment-log entries it points to. If throughput pressure seems to demand softening this gate, the answer is ALWAYS to fix upstream (Sonnet vocab discipline, self-correction on generation) — never downstream. Fixing downstream re-opens the 85%-bad-mappings class of bugs.
