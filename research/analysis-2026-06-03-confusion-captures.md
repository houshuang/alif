# Word-Confusion Capture Analysis (Arabic)

**Date:** 2026-06-03
**Data:** `confusion_captures` table, prod (`alif.db`), 36 captures, 2026-05-27 → 2026-06-03.
**Feature:** Confusion-capture picker (PR #167) — when a word is rated Hard/Again, the user
either taps the word they confused it with from an algorithm-ranked list (`suggested_pick`)
or types its English meaning (`free_text`). Candidate ranking lives in
`backend/app/services/confusion_service.py::find_similar_words`.

## Dataset shape
- **36 captures, 36 distinct failed lemmas** (no word confused twice yet — dataset too young
  for recurrence/leech signal).
- **21 `suggested_pick` / 15 `free_text`.**
- **All 36 are rating=2 (Hard); zero rating=1 (no_idea).** Every capture is a *partial-recognition*
  event — "I knew roughly what this was but mixed it up with X" — which is exactly the
  high-value confusion signal. (Worth confirming the picker is even offered on no_idea.)
- Span: 1 week, ~5/day.

## Finding 1 — Picker **precision** is strong
For the 21 `suggested_pick` rows, where did the user's actual pick sit in the algorithm's
ordered candidate list?

| metric | value |
|---|---|
| pick was in the offered list | **21 / 21 (100%)** |
| pick at rank 0 (top guess) | 12 / 21 (57%) |
| pick in top-3 | 18 / 21 (86%) |
| mean rank | 1.05 |
| median rank | 0 |

When the right word is in the list, it is ranked well. Two outliers:
- **id17 قانِت (devout) → قانِص (hunter)** — rank 9/11. Final ت/ص differ; rasm groups put ت and ص
  in different buckets and there is no ت/ص phonetic link, so the only signal is length+onset.
- **id2 ضعُف (weak) → ضيف (guest)** — rank 3/5.

## Finding 2 — Picker **recall** is the real gap (the headline)
The 15 `free_text` rows are the cases where the user *bypassed* the picker and typed a meaning.
I resolved every typed English meaning to its real Arabic lemma (all 15 resolve to a word the
user already has as **"known"**), then checked whether the algorithm had offered it.

**The actually-confused word was in the offered list in only 3 of 15 cases** (id23 أداة, id24 زهر,
id36 سيارة). **The other 12 the picker missed entirely** — which is *why* the user fell back to typing.

Running the real `find_similar_words` with no truncation against prod vocab shows where each
missed word actually ranked:

| id | failed → confused | meaning | rank (untruncated) | why missed |
|---|---|---|---|---|
| 1 | حرث → ورث | plow / inherit | 4 / 378 | strong (ed1, rhyme) but **early 5-slot cap** truncated it |
| 13 | نام → صام | sleep / fast | 9 / 380 | ed1 **rhyme just past the 8-visual cutoff** |
| 22 | بث → بحث | broadcast / search | 8 / 386 | ed1 insert, short word, huge cohort → **at the cutoff** |
| 5 | وضع → واضح | situation / clear | 51 / 82 | weak visual (ed2, no rhyme); شبه-root و.ض.* |
| 21 | حساب → حاسوب | bill / computer | 49 / 123 | **same root** but length-gap penalty buried it |
| 28 | مبدأ → معبد | principle / temple | 23 / 65 | **anagram** (م,ب,د reshuffled) — not modeled |
| 25 | أنذر → انحدر | warn / flow-down | 52 / 177 | weak near-rasm |
| 31 | ربح → ربيع | profited / spring | 215 / 234 | shared onset only — essentially meaning |
| 6 | جُحْري → جرح | burrow-adj / wound | 38 / 50 | **data quality**: lemma stored is nisba جُحْرِي, not جُحْر; masks the جحر↔جرح metathesis |
| 18 | سادة → صياد | gentlemen / hunter | NOT MATCHED | س/ص sibilant near-miss, ed3 too far |
| 16 | ربطة → ضفدع | tie / frog | NOT MATCHED | **pure meaning miss**, no form relation |
| 29 | التجأ → تاج | seek-refuge / crown | NOT MATCHED | partial letter overlap, ed4 — meaning miss |

The 3 the picker *did* surface (id23/24/36) all ranked 3–6 — i.e. inside the visible top-8 — so the
user could have tapped them but typed anyway (a small UI-discoverability note, not an algorithm bug).

## Finding 3 — Confusion mechanism taxonomy (n=36)

| n | mechanism | examples |
|---|---|---|
| 12 | **same-root derivation** | مستقبل/استقبال, خبر/أخبر, تحدث/حدث, قدِم/قدَم, صياد/صيد, شهير/شهري, أداء/أداة, مقبل/مقابل, اختفى/أخفى, سيرة/سيارة |
| 6 | near-rasm (1 dot) | ضعف/ضيف, هتف/هدف, قانت/قانص, إذاعة/أداة, وافق/وقف, أنذر/انحدر |
| 5 | rhyme (shared ending, diff onset) | حرث/ورث, نام/صام, ظهر/زهر, نادل/جادل, حدّد/هدّد |
| 4 | other near-form | جحري/جرح, استماع/اجتمع, مبدأ/معبد, ربح/ربيع |
| 3 | phonetic (emphatic/sibilant س/ص) | وضع/واضح, وظيفة/نظيف, سادة/صياد |
| 2 | dots only (rasm dist 0) | عذل/عدل, عمه/عمة |
| 2 | one-letter insert/delete | أصلاً/إصلاح, بث/بحث |
| 2 | no form relation (meaning miss) | ربطة/ضفدع, التجأ/تاج |

**~33% of all confusions are same-root derivational** — the learner confuses *which derived form
of a known root* means what (Form I vs IV vs VIII, participle vs maṣdar, etc.). This is a
distinctive learner-stage signal: roots are known, pattern-meaning mapping is not yet automatic.
Pedagogically this argues for **root-family contrast cards** (show خبر / أخبر / خبير together with
their pattern labels). Only **2 of 36** are pure meaning misses with no form relation — i.e. nearly
every "Hard" the user flags is a *form/derivation* mix-up, not a blank.

## Recommendations for `confusion_service.py` (priority order)

1. **Guarantee ed≤1 visual matches into the visible top-8.** id1/id13/id22 were the three highest-value
   misses and all were ed=1 matches ranked 4/9/8 — lost to dot-variant neighbors at the cutoff. Either
   raise the visual `max_results` 8→10, or force any candidate with `edit_distance==1` (or rhyme+ed1)
   to the front regardless of the dot-variant cohort. Cheapest, highest-yield fix.
2. **Reduce the length-gap penalty when `same_root`.** id21 (حساب/حاسوب) and the general same-root
   pattern (33% of data) get buried by `len_gap*2` when the derived form adds a long vowel
   (حاسوب, سيارة, استقبال). Same-root + ed≤2 should rank near the top.
3. **Model non-adjacent transposition (anagram).** `_is_adjacent_transposition` only catches one
   adjacent swap; id28 مبدأ/معبد is a 2-position reshuffle (same letter multiset). Add a
   `sorted(a)==sorted(b)` signal with a strong score bonus.
4. **Data quality:** ensure base-noun lemmas exist alongside nisba/derived forms (جُحْر vs جُحْري),
   so metathesis pairs aren't masked by a trailing ـِي.
5. **(Low) widen phonetic pass for sibilant س/ص pairs** (id18/id5/id15) — currently the ±1-length /
   ed≤2 cutoffs drop them; some of these are partly meaning-driven so payoff is uncertain.

The two pure meaning-misses (frog, crown) are **correctly** not surfaced by a form-similarity ranker —
they validate keeping the `free_text` fallback rather than trying to force them into the picker.

## Finding 4 — Most "Hard" flags are *form recognition*, not word-identity confusion

The 36 captures are the tip of the iceberg. The full `was_confused=True` signal is **943 word-reviews**;
only 36 produced a typed/picked capture. So 907 times the user flagged "I tripped on this word" but did
*not* name another word.

**Methodological caveat (user):** the confusion picker only shipped 2026-05-27 (PR #167). Yellow marks
*before* that don't reliably mean "confused with another word" — they could be forgot-then-recognized-on-
translation, a grammar stumble, etc. So intent is only readable on the 125 **post-picker** flags; the 818
pre-picker flags are used purely descriptively (what *form* the word was in, regardless of why it was hard).

Classifying the surface form the user actually saw against the dictionary lemma:

| bucket | post-picker (n=88) | pre-picker (n=801) |
|---|---|---|
| **surface ≠ dictionary form (any inflection)** | **85%** | **79%** |
| noun/adj inflection (plural/fem/dual/derived) | 24% | 16% |
| definite ال / case-ending only (**trivial**) | 23% | 29% |
| verb: present tense (ya-/ta-/na-/a-) | 15% | 14% |
| preposition/conjunction proclitic (bi-/li-/ka-/wa-/fa-) | 15% | 14% |
| verb: other conjugation (past person/mood/passive) | 14% | 12% |
| dictionary form (pure identity-recall) | 6% | 7% |
| noun+pronoun enclitic | 5% | 6% |

Median response time on confused words is **37.6s vs 23.4s** on clean-Good — ~60% longer, consistent with
on-the-fly parsing effort.

**Reading this with the caveat:** the ~25% "ال / case-only" bucket is likely *forgetting* (ال is trivial),
not a morphology problem — those deserve normal SRS, not a grammar lesson. But that still leaves the
majority on genuinely non-trivial morphology: **verb conjugation ≈ 28%** (present + other), **noun/adj
inflection ≈ 24%** (incl. broken plurals أساتِذة←أُسْتاذ, أَسْماء←اِسْم), prepositions, and enclitics.

The hardest sub-class is **derivational nominalization the system maps back to a verb lemma** — the surface
looks like a different word entirely:

| user saw | credited as lemma | relation |
|---|---|---|
| التَّخْطِيطِ "the planning" | خَطَّط "to plan" | maṣdar (verbal noun) |
| اللَّاعِبُ "the player" | لَعِبَ "to play" | active participle |
| المَسْرُوقَةِ "the stolen (f)" | سَرَقَ "to steal" | passive participle |
| اهْتِمَامِهِ "his interest" | اِهْتَمَّ "to care" | maṣdar + pronoun |
| بِزِيَارَةِ "with a visit" | زَارَ "to visit" | maṣdar + preposition |
| يُفْسِدُ "he spoils" | أَفْسَدَ "to spoil" | Form-IV present |

## What we can do pedagogically

The morphological bridge **already exists**: `WordInfoCard` (lib/review/WordInfoCard.tsx:530) renders a
clitic→stem→suffix color-band decomposition with form labels ("verbal noun / present tense / plural"),
computed by `confusion_service.decompose_surface`. **But it's pull, not push** — it only shows when the user
taps a word (`getConfusionHelp`, app/index.tsx:1104). The data says 85% of Hard flags are form-related and
the engine can already explain the form; it just isn't shown at the moment of difficulty. Concrete moves:

1. **Push the form bridge on a confused/Hard mark (branch on the cause).** When a word is rated confused and
   `surface_bare != lemma_bare` and it's not ال/case-only, auto-reveal the existing decomposition band
   (or a one-line "يُفْسِدُ = present of أَفْسَدَ 'to spoil'"). If surface == dictionary or ال-only, skip it —
   that's recall, handle with SRS timing, not a grammar card. Highest leverage, mostly wiring existing pieces.
2. **Name the pattern (wazn), not just the form key.** Extend `FORM_KEY_LABELS`/pattern naming for the hard
   derivational cases — active participle (فاعِل), passive participle (مَفْعول), maṣdar patterns — so
   التخطيط/اللاعب/المسروقة get an explicit "this is the X-pattern of root خ.ط.ط" rather than a bare stem.
3. **Root-derivation family card.** Periodically (or on a captured same-root confusion) show one root's
   derivation map: لَعِبَ→لَعِب→لاعِب→مَلْعَب with glosses + pattern labels. Directly teaches the verb↔noun
   derivation that 24%+ of misses turn on, and answers the 33% same-root picker confusions from Finding 3.
4. **Pattern grammar features + micro-lessons.** Use the existing `grammar_features` system: tag sentences
   with verb-form (I–X) and participle/maṣdar patterns; after the user stumbles on a pattern N times, slot a
   tiny lesson. Priorities from the data: present-tense conjugation, maṣdar nominalization, broken plurals.
5. **Contrast drills for captured pairs.** On a `suggested_pick`/`free_text` capture, schedule a generated
   sentence pair using both confusables (قدِم/قدَم, شهير/شهري, خبر/أخبر) so the distinction is seen in context.
6. **Broken-plural links.** Surface singular↔broken-plural explicitly (أُسْتاذ↔أساتِذة) on intro/contrast cards.

## Caveats
- n=36 captures (and 125 post-picker yellow marks) are small and single-user; treat percentages as directional.
- Pre-picker yellow marks (818) conflate forgetting / grammar / confusion — used only for the surface-form
  distribution, never for intent.
- `free_text` → Arabic resolution was done by hand (gloss → most-plausible known lemma) and verified
  against prod vocab; all 15 resolved cleanly to a "known" lemma, but a couple (frog/spring/crown)
  may be meaning slips rather than the specific lemma named here.
- Re-run after ≥100 captures to get recurrence (same word confused repeatedly = a confusion-leech).
