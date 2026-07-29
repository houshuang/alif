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

**2026-07-28 follow-up — activation never ran.** All 243 rows remain inactive,
untranslated, unverified, and un-quality-reviewed. This is not a slow queue:
`update_material.py` keeps Step A2 off unless `--run-corpus-enrichment` or
`ALIF_RUN_CRON_CORPUS_ENRICHMENT=1` is supplied, while production's
`/opt/alif-update-material.sh` enables only pregeneration + lemma enrichment.
The every-three-hour cron log contains 578 explicit Step-A2 skips. All 243
sentences now touch active vocabulary and are eligible, but do **not** simply
enable the then-deployed PR #231 step: it is unscoped/unordered, 1,707 older
corpus rows are stranded under its unrecovered claim sentinel, remapping erases
target flags, and the active pool is already above its retirement target. Add
a Momo-only 10–20-row runner with sentinel recovery, target repair, translation
QA, and demand-aware activation; use a copied-snapshot sample and human review
before any bounded production backfill.

**2026-07-28 implementation status — code only.** The scoped runner and its
pipeline hardening are now implemented on an isolated branch: exact kind/ID
scope, shared flock, in-scope sentinel recovery, deterministic bounded claims,
substantial tashkīl/translation/mapping QA, canonical target repair, durable
authentic-quality gating, and separate default-zero demand-aware activation
with an acquiring-content block and pool ceiling. Preparation and activation
must run in separate invocations. At that July 28 checkpoint, copied-snapshot
rehearsal and deployment were still pending; activation capacity was already
zero at the observed pool count. See
`analysis-2026-07-28-learning-update.md`.

**2026-07-28 PR #232 validation — rehearsal complete, production corpus untouched.**
A disposable working copy derived from the immutable snapshot received three
reviewed temporary lemmas. Rows 52182 and 52316 prepared cleanly; 52352 received
a terminal naturalness rejection. Nothing activated and learner tables were
unchanged. A full deterministic replay classifies 235/243 rows as
inventory-complete. The remaining eight rows contain nine standalone OCR-spaced
`و` tokens; they need separately confirmed source normalization and exact-ID
blocked-row retry, not vocabulary backfill. Jan 1 is now a transient claim,
Jan 2 a durable inventory/mapping block, and Jan 3 a durable linguistic-QA
rejection. The PR #232 code release leaves corpus cron disabled and performs no
production corpus or learner-data mutation.

**2026-07-29 inventory follow-up — exactly three persistent dictionary rows.**
PR #234 (`e20148b7`) added the three reviewed rehearsal gaps as scaffold lemmas:
#4530 `كُلِّيّ` “total/overall,” #4531 `إِلٰه` “god/deity,” and #4532
`فَعَلَ` “to do.” They are gated canonical rows linked to existing reviewed
roots #198 `ك.ل.ل`, #809 `ء.ل.ه`, and #103 `ف.ع.ل`; no duplicate roots were
created. The exact operation created no ULK, ReviewLog, FCE, or SentenceWord
rows. All 243 *Momo* sentences remain inactive, untranslated,
mapping-unverified, and quality-unreviewed, with the active pool exactly at its
1,950 ceiling and corpus cron still disabled. Raw `فعل` still needs contextual
verification because the first lookup candidate can be known noun #207
`فِعْل`; dictionary presence does not relax the mapping gate. Backup:
`/opt/alif-backups/alif_pre_pr234_momo_inventory_20260729.db`
(`sha256=fd1a9eeeb81f36a80242e899c1172d3fbf1b4d325ea4722de45dffa2e61f3183`);
ActivityLog #3879. Preparation/retry and activation remain separate decisions.

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
