---
name: Target vs collateral words are equal — no distinction
description: User has repeatedly stated that target and collateral words in a sentence should be treated identically for learning, credit, and intro cards
type: feedback
---

Once a sentence is generated and scheduled, ALL words in it are equally important — both for difficulty assessment, learning credit, and intro card eligibility. The "target" vs "collateral" distinction is only useful during sentence generation (ensuring coverage), not during review or session presentation.

**Why:** The user's learning model is that every word encounter matters equally. A word seen 10 times collaterally IS learned. The system should not deprioritize words based on whether they were the generation target.

**How to apply:** Never skip intro cards, reduce credit, or deprioritize a word because it entered the pipeline collaterally. If a word is acquiring with times_seen=0 and has a sentence in the session, it should be eligible for an intro card regardless of how it was introduced. The CLAUDE.md line about "collateral introductions skip intro cards" contradicts this — the filter should be purely on times_seen and encounter count, not on introduction source.
