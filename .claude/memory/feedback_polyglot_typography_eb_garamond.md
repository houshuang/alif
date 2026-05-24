---
name: feedback-polyglot-typography-eb-garamond
description: "Polyglot Greek font is EB Garamond, loaded via expo-google-fonts in _layout.tsx; never use italic in polyglot Greek typography"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: a27a1042-c1f5-42cd-8c11-a47854ca77b8
---

The Polyglot Greek typeface is **EB Garamond**, and Polyglot **must never use italic** in its Greek surfaces (sentence review, lookup card, lemma detail, stats). User directive 2026-05-22: "i like eb garamond, but let's not use italic ever."

**Why:** EB Garamond was chosen via design-explorer over Cormorant/GFS Didot/GFS Neohellenic/Noto Serif/Literata/Cardo/Gentium for sustained Greek reading on a phone (covers monotonic + most polytonic + Latin, i.e. all three Polyglot languages). Italic looked wrong; only the upright faces (400 + 600) are loaded, so any `fontStyle:"italic"` would just faux-slant the upright face.

**How to apply:**
- Reference the registered constant name as the family string: `EBGaramond_400Regular` (= `POLYGLOT_FONTS.greekBody`, sentence/translation reading) and `EBGaramond_600SemiBold` (= `POLYGLOT_FONTS.greekDisplay`, headline forms). With `@expo-google-fonts`, **weight is a separately-registered family, not a `fontWeight` prop**, and you must use the constant name — NOT the human name "EB Garamond" (that string is registered nowhere and silently falls back to Georgia/system; that exact bug — "Cormorant Garamond" named but never loaded in `useFonts` — was what made polyglot review look ugly before PR #126).
- Any new polyglot Greek face must be added to the single app-wide `useFonts` in `frontend/app/_layout.tsx`.
- Don't reintroduce `fontStyle:"italic"` on polyglot Greek text.
- All four polyglot Greek surfaces are on EB Garamond as of PR #126 (review/lookup/lemma-detail) + PR #127 (reader). The reader `frontend/app/polyglot.tsx` keeps a local `SERIF` const but it now points at `POLYGLOT_FONTS.greekBody`; its body prose is 21/34. See [[feedback-polyglot-mirror-alif]].

**Deploy note:** adding a new `@expo-google-fonts/*` dep needs it installed on the server, but `systemctl restart alif-expo` already runs `npm install` via `ExecStartPre`, so the standard frontend deploy (`git pull && systemctl restart alif-expo`) picks up new frontend deps automatically — no separate npm install step required. Verify a font change shipped by fetching the web bundle (`/node_modules/expo-router/entry.bundle?platform=web&dev=true`) and grepping for the font constant (e.g. `EBGaramond`).
