---
name: CAMeL MLE feminine ة misread as 3ms_poss
description: Known CAMeL MLE failure — feminine noun ending ة is routinely misread as 3ms possessive pronoun suffix ـه. Any LLM gate over MLE output must explicitly check for this.
type: feedback
originSessionId: c6c3ab9f-7809-4607-bd98-10bfba80af1d
---
CAMeL MLE (morphological lexicon) frequently misreads the feminine noun ending ة (tā marbūṭa, U+0629) as a 3ms possessive pronoun suffix ـه. The decomposition it proposes splits a feminine noun X-ة into masculine stem X + fake `enc0: 3ms_poss`. The resulting "canonical" is often a DIFFERENT Arabic dictionary lemma — commonly same root with a related-but-distinct meaning — making it superficially plausible.

**Why:** The Alif decomposition audit (Step 3, 2026-04-24) created 33 new canonicals via an LLM verdict gate. Spot-check revealed the gate systematically approved this failure mode on 22 of the 33 — including سِتَارَة (curtain) → سَتّار (one who conceals), نَامُوسِيَّة (mosquito net) → نامُوس (law), شَارِحَة (colon ":") → شارِح (explainer), تِينَة (one fig) → تِين (figs). The gate failed because the stripped stem is itself a valid Arabic word, and the LLM rationalized a bridging gloss ("curtain; one who conceals") instead of rejecting the decomposition. Re-gating with an explicit warning caught all 22 (PR #49).

**Tell:** In the Alif decomposition-classification JSON: `lemma_ar_bare` ends in `ة` or `ه` AND `clitic_signals == {"enc0": "3ms_poss"}` (no prefix clitics). Also suspicious: singulative/collective pairs (تِينَة/تِين), feminine/masculine nisba pairs (إسبانِيَّة/إسبانِيّ), any time the orphan is itself morphologically complete.

**How to apply:**
- Any LLM gate over CAMeL MLE output MUST explicitly describe the ة→3ms_poss failure in the system prompt with worked examples, and instruct the model to default toward rejection when the orphan ends in ة AND the only clitic signal is `enc0: 3ms_poss`.
- Singulative/collective pairs and masculine/feminine nisba adjective pairs are SEPARATE lemmas in this system. Don't let the LLM collapse them.
- When auditing MLE output generally, compute the ة-ending + enc0-only pattern upfront and budget for a high bogus rate in that slice.
- Reuse the stricter prompt from `backend/scripts/regate_step3_created_canonicals.py` as the template for any future CAMeL-MLE audit.
