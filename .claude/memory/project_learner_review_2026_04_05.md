---
name: Learner data review 2026-04-05
description: Comprehensive 2-week learning metrics review — vocabulary growth, pipeline balance, accuracy trends, key issues found
type: project
---

Full learner data review covering 2026-03-22 to 2026-04-05.

**Key metrics at review time:**
- 1,279 FSRS words (from 1,016 two weeks prior, +263)
- 4-week trajectory: 741 → 905 → 1,016 → 1,162 → 1,279 (~160/week)
- FSRS retention: 91.3% (healthy)
- 365 reviews/day avg across 15 active days
- Acquiring pipeline: 142 words (growing, net +74 in period)
- 59 lapsed, 28 suspended

**Critical issue: sentence pipeline deficit.** 90 of 142 acquiring words (63%) have no active sentence and literally cannot be reviewed. 58 never-reviewed words stuck in box 1. Root cause: textbook scans add 27-60 words/day directly to acquiring, bypassing the pipeline backlog gate (which only controls auto_intro). Sentence generation cron (3h) can't keep up.

**Why:** Textbook scans route directly to `acquiring` state, while the backlog gate only throttles `auto_intro`. The system has a throttle on one intake valve but a fire hose on another.

**How to apply:** This informs two potential changes: (1) textbook scans should respect the pipeline backlog gate, (2) sentence generation should trigger immediately after bulk imports. Also: user should pace scans to ~10-15 new words/day.

**Other findings:**
- April 1 had one anomalous 103-review/1%-accuracy session that crashed the day's stats — likely sync glitch
- Book-source words have lowest accuracy (81.4%), textbook_scan at 88.2%
- Study (manual) words retain best (97.5%), collateral also strong (94.9%)
- Session sizes very large (avg 66, 45/83 sessions at 60+)
- 16 new leech suspensions — system working correctly
- Quran: 21 verses studied, next at Surah 2 Ayah 15, promotion system active
