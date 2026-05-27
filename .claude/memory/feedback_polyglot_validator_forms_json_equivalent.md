---
name: feedback-polyglot-validator-forms-json-equivalent
description: "When polyglot's sentence validator rejects valid inflections, augment its known-bare set + lemma_lookup with PageWord/SentenceWord rows for engaged lemmas — the data-driven equivalent of Alif's forms_json."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: a79bcf10-0c81-4b99-aa0f-cd3e492809f7
---

When polyglot validation fails on a perfectly good inflected form (e.g. `sagittam` rejected for target `sagitta`), don't reach for "loosen the validator" or "build a Latin morphology generator from scratch." The deterministic mapping you need is already in the DB: `PageWord.lemma_id` (post-quality-gate) and `SentenceWord.lemma_id` (post-LLM-verifier) for every surface form the user has actually encountered.

**Why:** Alif solves the same shape of problem with `forms_json` populated per lemma at creation time plus algorithmic verb conjugation in `build_lemma_lookup` Pass 3. Polyglot has no morphological generator for Latin (LatinCy parses, doesn't generate) and historically the validator at generation time called `_lemmatize_to_bare(surface)` independently — which means it got the same wrong answer the page tokenizer had been corrected for. The quality-gate-corrected mappings in `PageWord`/`SentenceWord` are the ground truth and they were sitting in the database unused at validation. Fix shipped 2026-05-26 PR #165: `_observed_surfaces_for_lemmas(db, language_code, lemma_ids)` augments `known_bare_forms` and `lemma_lookup` in `batch_generate_material`. Same structural pattern as Alif, data-driven from observation instead of pre-stored.

**How to apply:**
- When investigating any "this valid inflected form is being rejected" complaint in polyglot, first check whether the lemmatizer (LatinCy / simplemma) gives the right base. Run `_lemmatize_to_bare(surface, language_code)` and compare to the lemma's `lemma_bare`. If it's wrong, the validator can't recover via lemmatization.
- Look for the surface form in `PageWord.surface_form` and `SentenceWord.surface_form`. If it's there with the right `lemma_id`, the augmented validator should already accept it (since PR #165). If not, the user genuinely hasn't encountered that inflection yet — same blind spot Alif's `forms_json` has for un-listed inflections.
- The fix is generic across el/grc/la — don't gate it on language. Greek benefits too: the el-specific `_el_surface_bares_for_lemma` inflection generator misses common verb forms (`μιλάει`, `κρατάει`, `ακούω`) and comparative `πιο`; the observed-surface map covers these without new code.
- Worth a future follow-up but not urgent: an algorithmic Latin inflection generator (1st-5th declensions + conjugations) for cold-start coverage on lemmas the user hasn't read yet. The observed-surface map only helps for already-encountered inflections.

Related: [[feedback-polyglot-mirror-alif]] — same broader principle ("read Alif's equivalent first before designing"). The validator gap is a specific instance where polyglot was missing a structural piece Alif has had for months.
