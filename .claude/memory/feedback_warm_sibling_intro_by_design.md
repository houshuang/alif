---
name: ""
metadata: 
  node_type: memory
  originSessionId: 865a0174-18e5-4950-99e6-b12a5fa628f1
---

When the user reports seeing a "NEW WORD" intro card for a word they're sure they already know, the most likely cause is **root-family warm-word introduction**, NOT the `total_encounters`-suppression bug.

Concrete case (2026-06-05): card for verb ٱكْتَشَف *iktashafa* "to discover" (lemma 3803, source=frequency_core, `times_seen=0`, `total_encounters=0`, 0 review_logs — genuinely new as a scheduling unit). What the user actually knew was the **masdar** اِكْتِشاف "discovery" (lemma 2614, **known**, 19 reviews) + sibling noun كَشْف (lemma 3533, known). All same root (1150). The intro card displays the known masdar in its "v. noun" box, which is why it feels so familiar.

**Why:** Alif schedules at the lemma level; a Form-VIII verb and its (predictable) masdar are **separate lemmas with separate FSRS cards** and do not share credit. Warm words (known root family → Boudelaa & Marslen-Wilson 50-70% semantic access) are *intentionally* introduced as cheap high-ROI wins (`IDEAS.md:2272`, experiment-log 4370). The only root-sibling guard blocks siblings that recently **failed** (`IDEAS.md:2173`) — there is no "sibling already known → soften intro" guard.

**How to apply:** Before assuming a bug, check whether the shown lemma is a *distinct* root-family member (query `root_id`, compare `times_seen`/`total_encounters` per lemma). If `total_encounters=0` it's a warm sibling, working as intended — the user decided on 2026-06-05 to **leave this as-is** (Tier 0 graduates it on first correct review anyway). The genuinely-suspicious case is the *exact same lemma* reappearing as a fresh intro despite `total_encounters ≥ 5` (that's the real `sentence_selector.py:2146` bookkeeping bug — audit only if the user hits that). Related: [[project_lemma_decomposition_audit]].
