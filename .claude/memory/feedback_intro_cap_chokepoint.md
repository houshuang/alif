---
name: feedback-intro-cap-chokepoint
description: "Put system-wide caps inside the single function every caller goes through, not in one caller. The 30/day intro cap was bypassable for ~3 years because it lived in _auto_introduce_words instead of start_acquisition."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 2fdc3a88-21a5-478b-b99b-decfa1630333
---

System-wide caps must live at the chokepoint that every caller goes through, not in a single caller. Otherwise the cap silently rots as new callers proliferate.

**Why:** The 30/day intro cap (`DAILY_AUTO_INTRO_TARGET`) sat inside `_auto_introduce_words()` for the whole life of the algorithm-redesign experiment. Five other callers of `start_acquisition()` accumulated over time (OCR collateral-promotion, sentence-review collateral, Quran promotion, cold session-build promoter, manual `introduce_word`), all bypassing the cap. By 2026-05-15 the user was getting 39 intros/day with 0 going through the capped path — the official gate had become decorative. Fix on `sh/intro-cap-enforcement` moved the cap into `start_acquisition()` itself.

**How to apply:** Whenever you're tempted to add a guard at one specific call site, ask "is there a function every caller goes through?" If yes, put the guard *there*. If no, ask whether one *should* exist. A wrapper function with a free-pass parameter is worse than no chokepoint — easier to forget, easier to bypass with "just this once" callers. See `start_acquisition` in `acquisition_service.py` for the pattern (cap at function entry, fall back to a non-promoted state instead of raising, callers check resulting state).

Related: [[feedback-target-collateral-equal]] (every word in every sentence earns credit — collateral promotions are the SECOND-largest source of cap bypass), [[feedback-check-prior-work-first]] (long-iterated areas like the scheduler usually have N-1 fixes that didn't fully close the loop).
