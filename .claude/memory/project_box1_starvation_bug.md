---
name: Box-1 starvation bug — never-seen words
description: 19 acquiring box-1 words have 0 reviews despite being due for days. Greedy set-cover prioritizes FSRS-heavy sentences, starving single-target acquiring sentences. Pipeline backlog gate (65 > 40) suppresses new intros, creating a vicious cycle.
type: project
---

**Finding (2026-03-14):** 40 box-1 words all due, 19 with literally zero reviews (oldest 14 days). All have 3 target sentences each. Root cause: greedy set-cover scores `(overlap_count ** 1.5)` — sentences covering multiple FSRS-due words always outscore single-target acquiring sentences. With 10-sentence limit, new words never get picked.

**Why:** The scoring function doesn't distinguish between "word reviewed yesterday" and "word never seen in 14 days." Both are just "due."

**How to apply:** Need a score boost for never-reviewed acquiring words in sentence_selector.py's greedy scoring. Also watch pipeline snapshots — if box-1 stays at 40+ with many 0-review words, the fix hasn't worked.

**Related metrics at time of discovery:**
- 65 acquiring (backlog gate active, threshold=40)
- 392 encountered waiting
- 691 FSRS words, only 20 due at any time
- Sessions: 82% FSRS reviews, 18% acquisition
- Accuracy: 93-98% (should allow max intro rate)
- Graduation rate: 333 in 14 days (pipeline CAN drain when words get surfaced)
