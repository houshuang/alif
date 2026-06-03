---
name: feedback-no-book-sentences-for-acquiring
description: Acquiring (new/Box 1) lemmas should NEVER be reviewed via textbook fallback sentences — book sentences are too hard and ruin early learning.
metadata: 
  node_type: memory
  type: feedback
  originSessionId: ba0f3f41-1272-4e9a-b509-f4983892fa09
---

For both Alif and polyglot, **textbook / book corpus sentences must not be
served as review material for lemmas in the `acquiring` state**. Book
sentences are syntactically and lexically demanding; using them to practice
words still in Box 1 / 2 / 3 violates the comprehensibility ceiling and
ruins the learning experience.

User said this on 2026-05-26 (after seeing Eutropius sentences served for
freshly red-tapped Latin lemmas in their first polyglot Latin review
session): *"the book sentences are too complex to practice with - and I've
said before that I don't want to see them until in quite a while, when I
have learnt the words well"*. The "I've said before" matters — this is
durable preference, not a one-off.

**Why:** book sentences are written for fluent readers, not learners. They
assume a much broader vocabulary, more complex syntax, and idiom. For a
just-tapped-red word in Box 1, the learner needs scaffolded sentences with
mostly-known vocabulary around the target. That's exactly what
`material_generator.py:warm_sentence_cache` produces — LLM-generated
sentences with comprehensibility scoring. Book sentences are a *cheap
fallback* the picker can use only after a word is well-learned.

**How to apply:**
- The picker (polyglot: `sentence_selector.py:build_session`; Alif:
  equivalent in `sentence_selector.py` / `session_builder`) should refuse
  textbook-source sentences entirely when the due lemma's
  `knowledge_state == 'acquiring'`. Use `exclude_sources=
  {"textbook"}` at the picker call.
- Acceptable threshold for textbook fallback: at minimum, the lemma has
  an FSRS card (`fsrs_card_json IS NOT NULL` and `state in ('learning',
  'known', 'lapsed')`). Even better: only after the word has been correctly
  reviewed several times. The 2-textbook-per-session global cap stays as a
  secondary guard.
- If a lemma has no acceptable LLM sentence yet → skip it (put it in
  `skipped_due_lemmas` and wait for `warm_sentence_cache`). Showing nothing
  is better than showing a too-hard book sentence.
- When introducing a new language (Latin 2026-05-25 case), this matters
  most because the LLM sentence cache hasn't run yet at all. Don't let the
  picker fall back to book sentences as the "first review" experience.
- Related: see [[project-polyglot-latin-live]] and the picker's
  `TEXTBOOK_FALLBACK_MAX_PER_SESSION` constant.
