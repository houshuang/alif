---
name: cluster-detection-limit
description: "Root/gloss-based confusion clustering misses what the user actually confuses — don't propose features that rely on it"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 88efa212-a992-4a58-a5d9-abfcf8f5e395
---

Algorithmic detection of confusable-word clusters (by shared root, gloss keyword overlap, surface prefix) systematically **fails to capture the confusions the user actually experiences**. When the user marks a word "yellow" (uncertain/wrong), the word they were *thinking of* is usually NOT one of the algorithmically-clustered candidates — it's some phonologically-similar or context-bound competitor that doesn't share root or gloss.

**Why:** Real confusion is multi-modal — surface phonology, sentence context, recency of last encounter, even visual rendering. None of these are captured by structural lemma metadata.

**How to apply:** Do NOT propose features that depend on auto-detected confusion clusters (e.g., "schedule cluster-mates together", "show contrastive pairs", "warn during intro"). They'll target the wrong pairs. Any future interference-aware feature must use **observed co-confusion signal** — e.g., when user marks word A wrong on a sentence, log which other words they were considering / which prior session word B was active. Without that signal, "cluster-aware scheduling" is theater. Discussed 2026-05-27 during a stuck-words analysis: the talaq cluster (root طلق) was perceived as a problem but data showed it was fine; meanwhile the user's real confusions live in pairs the root-grouping never surfaces.

**Empirical confirmation (2026-06-01, 21 captures, PR #179).** First `confusion_captures` analysis: 14 `suggested_pick` / 7 `free_text`, capture rate 10.5%. Re-ran the live matcher on all 7 free-text cases — the word the user typed was **in vocab in all 7** (every miss was ranking, not candidate-pool). Three miss-patterns: (1) **rhyme** نام/صام, حرث/ورث (shared rime, different onset — ranked just below cutoff); (2) **metathesis** جحر/جرح (same letters reordered, plain Levenshtein=2); (3) **semantic/contextual priming** سادة→"hunter", ربطة→"frog" — unreachable by ANY surface metric (this is the residue that proves this memory). Patterns (1)+(2) FIXED in `confusion_service.py` `find_similar_words` (`_shares_rime` −6 / `_is_adjacent_transposition` −12, reasons "rhymes"/"letters swapped"). Pattern (3) confirms: ~3/7 confusions live entirely outside spelling space.

**Pedagogy (don't redo this analysis): all 21 captures are a *specific single word*, zero fuzzy groups.** The "group I can't pin down" feeling = dense derivational families confused *bidirectionally* (مستقبل↔استقبال↔استقبل, all ق-ب-ل+است-). Dominant mode = minimal pairs the visual matcher already nails. **Observed-pair contrastive cards are now justified** (we have observed pairs) but deferred until ~50 captures per the experiment plan — when building them, use the `confusion_captures` rows ONLY, never algorithmic clusters.
