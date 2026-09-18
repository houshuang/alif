---
name: project_bookifier_discover_glossary
description: Bookifier consumes /api/discover as a glossary service (include_oov + distinctive); dialect/vulgar words are addable with register/dialect persisted
metadata: 
  node_type: memory
  type: project
  originSessionId: d963c2d8-a028-44b9-b3d9-5375a0f4aa44
  modified: 2026-07-21T09:33:17.332Z
---

The **Bookifier** bilingual-EPUB builder (`~/src/bookifier/bilingual/`) consumes Alif's
`/api/discover/words` as a glossary service for authentic (often dialectal/vulgar) Arabic
text — a different job from Dragoman's "learn next." Shipped 2026-06-13 (PRs #199, #200 +
follow-up `b9eea23`), driven by `bilingual/ALIF_DISCOVER_API_{FINDINGS,ENHANCEMENTS}.md`;
reply in `bilingual/ALIF_DISCOVER_API_RESPONSE.md`.

Glossary mode is **opt-in** so Dragoman's default is untouched:
- `include_oov=true` keeps out-of-vocab dialect/slang/vulgar words via a *conservative*
  clitic-stripped surface fallback (only unambiguous clitics; single-letter ب/ك/ل + ه/ك/ي
  NOT stripped — they collide with roots). OOV-mode also distrusts defective-root (O/#)
  CAMeL backoffs (وكسك→وكس). Default mode drops OOV — unchanged.
- `selection=distinctive` = TF-IDF lift (in-text count × inverse general freq).
- gloss is context-aware (example clause in), tags register/dialect, can correct a wrong
  CAMeL lemma, and glosses vulgar terms clinically (explicit prompt instruction).
- output adds surface_forms[], root, register, dialect, is_proper_noun, example_ar,
  lemma_source. In glossary mode proper nouns are KEPT+flagged (the gloss model over-tags
  vulgar nouns as proper), only dropped in learn-next mode.

**The user wants dialect/vulgar/OOV words addable to Alif**, not just glossed: `/add`
persists `register`/`dialect` (new `Lemma` columns, migration `c3e5f7a9b1d4`). Normal SRS
intro. See [[feedback_text_to_lemma_hardened_path]] — discover still routes identity
through `build_comprehensive_lemma_lookup`+`lookup_lemma`.

Gotchas: `كس`/`ناك` from the original report are now non-issues but for opposite reasons —
`كس` is already in vocab (lemma 799 "pussy"), `ناك` clitic-strips to `نا` "us" (lemma 2400)
in the shared lookup (a narrow edge, left as-is). Contract: `docs/discover-api-integration.md`.

**Bulk-import lessons (2026-07-21 Kalila 34-word import).** `/api/discover/add-batch` is
the sanctioned "put words straight into Box 1" route: `introduce_word(due_immediately=True,
enforce_daily_cap=False)`, no Story needed, canonical-resolution applied, gates + material
gen fire as a background task. Its existence check (`lookup_lemma_citation`) matches on the
**bare form**, which used to mis-resolve homograph senses silently (مَلِك "king" hit مَلَك
"angel" → already-known no-op; زَعَمَ verb enrolled masdar زَعْم). **Fixed structurally in
PR #219 (merged 2026-07-21)**: when the caller sends a gloss, the match is held to
`_candidate_matches_correction` and re-routed via `correct_mapping` or created new;
response field `sense_rerouted_from` records re-routes. So: always send gloss_en+pos in
batch adds (gloss-less adds skip the gate), and check `sense_rerouted_from` in responses.
Verify the fix is deployed on prod before relying on it. Pre-flight rehearsal pattern (still
useful for auditing a big list): `/tmp/claude/preflight_kalila36.py` in the 2026-07-21
session; experiment-log entry "Kalila wa-Dimna high-value vocab injection".
