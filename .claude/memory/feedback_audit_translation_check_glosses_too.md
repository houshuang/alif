---
name: feedback_audit_translation_check_glosses_too
description: "When investigating a \"translation\" complaint in Polyglot, check BOTH sentences.translation_en AND lemmas.gloss_en"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 3cd8268d-a261-4ae8-971f-974a343e1de0
---

When the user reports a "translation" quality issue in Polyglot, the complaint may refer to the SENTENCE translation (`Sentence.translation_en`) **or** the lemma GLOSS (`Lemma.gloss_en`) — both render on the lookup card and the user doesn't necessarily distinguish them. An audit that probes only one will miss the other.

**Why:** Caught 2026-05-26. The user complained "the translation is just three words with no comma." First-pass audit (`research/polyglot-latin-philology-and-translation-audit-2026-05-26.md`) ran a `length(translation_en) < 30` probe only against `sentences` for the Eutropius story, got `tiny n=0`, and concluded the complaint didn't match the data. Wrong — the actual hit was `lemmas.gloss_en` of acquiring lemmas (`excidium → "demolition setting of the sun"`, `exordium → "beginning introduction foundation"`) rendered next to the Latin word on the lookup card. Required a follow-up investigation + PR #157 to surface and fix.

**How to apply:** Any audit/probe script for a polyglot translation complaint must run the length / formatting / quality check on **both** tables:
```sql
-- sentences
SELECT s.text, s.translation_en, length(s.translation_en) FROM sentences s
  JOIN pages p ON p.id=s.page_id JOIN stories st ON st.id=p.story_id
 WHERE st.language_code=:lang ...;
-- lemma glosses (the lookup-card surface)
SELECT l.lemma_form, l.gloss_en, length(l.gloss_en) FROM lemmas l
 WHERE l.language_code=:lang AND l.gloss_en IS NOT NULL ...;
```
Also: when the user has tapped specific lemmas recently (check `user_lemma_knowledge` for newly-acquired rows in the session window), prioritise inspecting THOSE glosses — they're the ones the user actually saw on the lookup card.

Related: [[project_polyglot_latin_live]], [[feedback_verify_before_recommending]], [[feedback_check_prior_work_first]].
