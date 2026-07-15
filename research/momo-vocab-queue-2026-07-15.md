# Momo vocabulary queue — full-book sweep (2026-07-15)

Source: full 313-page OCR of the user's Momo PDF (299 pages transcribed, 14 blank;
~37,800 Arabic tokens; text NOT in repo — `tmp/momo_full_2026-07-15.txt` locally).
Token→lemma map + gap data: `simdata_volume_2026-07-14/momo_full_tokenmap.json`,
`gap4.json`, `words_union.json` (prod /words probe of book thirds).

## Full-book coverage (2026-07-15 post-first-import snapshot)

- **Coverage now (function + known/learning): 87.8%** — sample estimate (87.3%) validated.
- With in-progress: 91.1%. Unmapped/OOV: 7.2%.
- **Learning every ≥4-occurrence gap word → 95.3%.**

## Imported — tranche 1 (2026-07-15, all ≥8 occurrences, 40 words)

24 new lemmas + 11 existing no-ULK lemmas introduced (source=`bookifier`), plus
yesterday's 27-word sample-based import. Highlights: رمادي (137×), ربما (49×),
دمية (29×), دائري (22×), مدرع (17×), سيجار (15×), نظارة/كناس (13×), رئاسة (33×),
وكيل (20×), عملاق (15×, gloss fixed from "bosom" to "giant").

## Tranche 2 — ✅ IMPORTED 2026-07-15 (user approved same-day; count 4–7)

**Done: 110 new lemmas direct-created + 28 existing introduced** (not the ~46 first
estimated in chat — the 4-count tail was long). Every word hand-vetted against
full-book context; ambiguous candidates grepped in the text before glossing
(طرقة=corridor, نصب=monument, وحدة=loneliness, صلب=steel, عامود=pillar of light);
dropped as ungroundable/artifacts: كوى, حام, مرار, دائر, راقد, عايش, مكنة, لاشيء.
Gloss fixes vs /words: قنديل (lamp + قنديل البحر jellyfish), دولاب (cupboard).
Post-import: 159 bookifier lemmas total, all gated/glossed; Box 1 = 303. Accepted
trade: leech-reintro admission (Box 1 < 20) delayed ~3 weeks.
Ops note: per-word `run_quality_gates(background_enrich=True)` in a loop exhausts
the SQLAlchemy pool (~15 words in) — create+introduce first, then ONE batched
gates call. Original staged plan below for the record.

### (superseded staging plan)

Direct-create for any word the fuzzy /add lookup would mis-resolve (see bug below).

From prod /words probe (glosses ready; two gloss fixes noted):
غناء، حديدي، قمامة، فظيع، تخليص، انطباع، أسطورة، جاد، لافتة، تصاعد، بهاء، ماسورة،
مقشة، أتقن، قديس، قنديل (gloss→"lamp, lantern", not jellyfish)، منعكس، خطيب، قدح،
أفشى، دولاب (gloss→"cupboard, wardrobe").

In-vocab no-ULK (introduce only):
شتى، درع، مسبق، كسب، نطق، فريد، فرار، شريط، أفاد، محكمة، باقي، منصة، اقترح، طال،
صرف، دائرة، حديد، فاض، تكرار، هادئ، حيّر، دافع، متعدد، رعب، موكب، شعاع، نزاع، اقتراح.

Self-gloss needed (unmapped 4–7, not in probe union — vet at import; some may be OCR
noise or should-be-function): حقيقي (fix for yesterday's حقيقي→حقيق collision)، كثيف،
بكى، حلاق، دوامة، دمدم، أومأ، أسرع، مرار، أفق، تحتم، صدفة، أنيق، أرضية، اقتصد، وفّر،
وجيز، سرعة، أحصى، اختصار، تشاور، نصب، بديهي، دفة، امتلأ، سحيق، اعتدل، مستودع، رداء،
داكن، قنديل… (full list = `gap4.json` unm4 minus tranche 1 minus exclusions).

Excluded as dubious/artifact: ايض (أيضًا function artifact), ولي، جداء، قدامى، ايم،
ثلاثمائة (numeral), مش، طرف "to be strange", قابلة، راوند, all proper names.

## Authentic-sentence corpus (rounds 1+2, 2026-07-15)

**243 hand-vetted Momo sentences imported** (`source='corpus'`, `kind='momo_book'`,
inactive until cron enrichment; acquiring-gate PR #211 deployed first):
- Round 1 (require bookifier lemma): 87 machine-accepted → 59 kept.
- Round 2 (filter relaxed): 265 new candidates → 184 kept. Drops: mid-clause
  fragments, OCR garble, translator footnote, thin two-word exchanges,
  meaning-changing OCR ambiguities.
- Funnel context: 1,646 raw candidates; ~54% lost to page-boundary truncation +
  missing terminals (inherent to per-page OCR extraction), 280 to the OOV tail.
  The importer is idempotent — re-run after vocabulary growth to harvest more.

## Bug evidence: /add fuzzy-lookup collisions (17 cases, 2 days)

`POST /api/discover/add` resolves `lemma_ar_bare` through a lookup that strips
non-clitic prefixes, silently matching NEW citation forms onto WRONG existing lemmas:
تالي→أَلَا (introduced the interjection; reverted), حقيقي→حَقِيق (introduced حقيق
instead of creating حقيقي), and 15 no-ops against known lemmas: لاحظ→حَظّ (لا as
negation!), كناس→نَاس (ك as preposition!), سيجار→جَار, رمادي→رَمَاد, صبي→صَبّ,
توقف→وَقَفَ, نظارة→نَاظِر, سحري→سَحَر, اصبح→صُبْح, تمتم→تَمّ, عاد→عَادِيّ,
عمق→عَمِيق, امير→مَارّ, ادرك→دَارّ, شرطة→شَرَطَ, حجري→حَجَرَ.
No learning-state damage (already_known short-circuits), but adds silently fail.
See IDEAS.md entry. Workaround used: server-side direct create with exact-bare check
(`/tmp/momo_direct_create.py` pattern) — candidate for a `strict=true` flag on /add.
