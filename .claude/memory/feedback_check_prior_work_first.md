---
name: Check Prior Work Before Proposing Pipeline Fixes
description: For long-iterated areas (generation pipeline, FSRS, sentence selector, validator, lemma quality), always check git log + IDEAS.md + scripts-catalog before proposing fixes
type: feedback
originSessionId: ec5f17c3-056e-4a35-bb10-01c9880e21b9
---
For any investigation in a long-iterated area of the alif codebase — generation pipeline, FSRS scheduling, sentence selector, lemma quality, validator — always run these three reads BEFORE drafting recommendations:

1. `git log --since="3 months ago" --oneline -- <file>`
2. `grep -i <topic> IDEAS.md docs/scripts-catalog.md research/experiment-log.md`
3. `ls backend/scripts/ | grep -i <related-keyword>`

**Why:** caught 2026-05-03 about to weaken the `same_lemma` rejection in `apply_corrections` (load-bearing per 4 prior commits + a docstring guard at `sentence_validator.py:1437` + a separate memory `feedback_dont_weaken_same_lemma_gate`) and to re-add `lookup_lemma` to a target check that PR #42 had deliberately left exact-match. Also missed that `scripts/missing_lemma_candidates.py` and `scripts/import_scaffold_lemmas.py` already existed for the exact failure pattern I was diagnosing — both documented in `docs/scripts-catalog.md`. Wasted ~30 minutes proposing fixes that were already done, already rejected, or already shipped as scripts that just hadn't been run. Codified as Critical Rule #14 in CLAUDE.md.

**How to apply:** if a file has 10+ commits in the last 3 months, the most likely shape of an issue is "previous fix didn't fully close" or "operational gap (script exists, isn't being run)" — NOT "new bug." Default to that framing until evidence shows otherwise. Specific signals:

- `git log --oneline -- <file>` shows recurring commits with similar titles → the area has hard-won invariants. Read commit messages before changing code.
- A script in `backend/scripts/` matches the failure pattern → it probably already does what you'd build. Run it before reimplementing.
- An IDEA in `IDEAS.md` is marked `[DONE]` with the exact symptom you're seeing → check if it regressed or if the fix is partial.
- A docstring contains `DO NOT WEAKEN` / `INTENTIONAL` / explicit links to commits → that's a previous version of you having this same conversation. Listen.

**Red flag in your own draft:** if a recommendation rhymes with "soften this strict check," ask first whether the strict check is load-bearing. The 2026-05-03 mistake started exactly there.
