---
name: polyglot-mirror-alif
description: "Polyglot mirrors Alif's design and code by default. Divergence requires a specific Greek/Latin-driven reason — never a preference-driven one. Read Alif's equivalent file before designing any polyglot screen or service."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 56085f21-3580-4d8d-ab2a-c5e916288826
---

When working in `polyglot/` (the Greek/Latin sister app), ground both design and code in Alif. Polyglot is not a fresh design exercise — it is a port.

**Why**: Alif is the product of 100+ days of iteration with daily real-user testing (Stian himself). Every UI affordance, scheduling constant, gate, review semantic, payload shape, button label, and empty-state copy line in Alif has a history — a bug fix, a confusion someone hit, a feature that got cut, a thing that worked after several that didn't. Asking the user to re-decide questions Alif already answered (e.g. "should we use 1-4 ratings or a 3-signal model?", "should partial trigger a second step?") signals that I haven't read or understood Alif's existing design. The user got worried on 2026-05-20 because my AskUserQuestion call about polyglot sentence-review UX revealed I hadn't looked at Alif's review screen — those questions had already been settled in Alif (two-stage reveal, per-word tap cycling off/missed/confused, 3-signal model, middle-button label toggles between "Know All" and "Continue" based on marks).

**How to apply**:

1. **Before designing a polyglot screen or service**: read Alif's equivalent file *first*. Not just the docs — the actual TSX or service code. For the review screen, that's `frontend/app/index.tsx` (the sentence-review entry point), not `frontend/app/polyglot-review.tsx` (the transitional bare-word screen). For sentence-review API contract, `frontend/lib/api.ts` `submitSentenceReview`.
2. **Don't ask UX questions that Alif has already answered** unless I'm proposing a deliberate divergence. The question to ask is "does Alif do X?" — to myself, by reading — not "should polyglot do X?" — to the user.
3. **Mirror by default**: UI layout, button labels (including label-toggling tricks), scheduling constants (FSRS retention, Leitner intervals 4h/1d/3d, daily cap 30, leech window 8/<50%, cooldowns 7d/2d/4h), enum string values (`understood`/`partial`/`no_idea`), function names (`submit_sentence_review`, `_intro_shown_recently`), file structure.
4. **Specific defensible reasons to diverge** are language-driven, not preference-driven: cut Arabic-specific machinery (clitic stripping, awzān, tashkeel, Hindawi, CAMeL); add Greek-specific needs Alif doesn't have (simplemma, cognate links, accent restoration); drop fields polyglot's endpoints don't accept (no TTS → no `audio_play_count`).
5. **Before adding a feature Alif doesn't have**: ask which case applies — Alif doesn't *need* it (e.g. cognate auto-linking), or Alif hasn't yet implemented it (suspect — mirror Alif's absence instead of preempting its eventual design).
6. **Phase-2 alif_core extraction** is the eventual payoff. The more divergent polyglot becomes, the smaller the shared surface, the more we maintain two divergent systems forever.

Codified 2026-05-20 in `polyglot/CLAUDE.md` § "Ground design and code in Alif" and in root `CLAUDE.md`'s Polyglot bullet. See also [[feedback-check-prior-work-first]] (which is about Alif's *own* iterated areas — related but distinct).
