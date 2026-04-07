---
name: Intro card overload — fixed with interleaving
description: Intro cards must be interleaved among sentences, never front-loaded. Fixed 2026-03-30. Dynamic cap min(10, 5 + backlog//10).
type: feedback
---

**Status: Fixed (2026-03-30).** Intro cards are now interleaved among review sentences (2 first, then 1 every 3 sentences) via `buildInterleavedSession()`. Dynamic cap: `min(10, 5 + unintro_backlog // 10)`.

**Why:** 25 front-loaded intro cards felt overwhelming — user had to flip through all before any actual review. Fix: distribute through session + cap.

**How to apply:** Never front-load intro cards. If changing intro card logic, preserve the interleaving behavior in `buildInterleavedSession()` (frontend/app/index.tsx).
