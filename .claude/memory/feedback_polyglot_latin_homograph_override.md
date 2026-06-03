---
name: feedback_polyglot_latin_homograph_override
description: "Polyglot Latin LatinCy homograph mis-lemmatization — fix at la.py override, not per-screen; multi-mapping is a discovery not a fix signal"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: eb09ac2c-9fee-409b-aa74-f6005e9a4c00
---

LatinCy confidently assigns a citation lemma that **contradicts the POS/gender it tags on the same token**: `pilum` → lemma `pilus` (masc "hair") while tagging Gender=Neut (neuter lemma = `pilum`/javelin); `tantum` → adjective `tantus` while tagging pos=ADV. These recur in every context and the sentence-context verifier misses them (a wrong-sense mapping to a *valid* lemma looks structurally fine).

**Why:** Caught 2026-06-01 (PR #184) when a review card glossed `pilum` as "hair" (the "#468" on the card was `pilus`'s frequency rank, not a lemma id). 22 stored sentence_words had `pilum→pilus`.

**How to apply:**
- Fix at the **lemmatizer chokepoint** — `polyglot/app/services/languages/la.py` `_LEMMA_OVERRIDES` + `lemma_override()` applied inside `_analyze_token` — NOT in any single screen. One fix covers reading intake, generation validator, and on-demand tap lookup.
- Key the override on LatinCy's **own POS/Gender tag** (reliable even when its lemma isn't), so a genuine masc-acc `pilum` (= a hair) is left as `pilus`. Add a row per confirmed case; extend the table, not callers.
- **"Surface mapped to >1 lemma" is a DISCOVERY signal, never a FIX signal.** `pila` legitimately maps to both ball (fem) and javelins (neut-pl) by context — it carries no lemma↔tag contradiction, so it is deliberately NOT in the override. Only the contradiction is the fix signal.
- Discovery + repair tool: `scripts/audit_homograph_mappings.py` (audit/apply). It also reports duplicate lemma rows + glossless mapping targets (separate cleanup classes). Back up the DB before `--apply`.
- A fully general gender/POS-contradiction guard is blocked on storing per-lemma morphology (tracked in IDEAS.md 2026-06-01).

See [[feedback_polyglot_validator_forms_json_equivalent]] (PR #165 observed-surface augmentation — now guarded against re-ingesting override-cluster surfaces) and [[feedback_check_prior_work_first]].
