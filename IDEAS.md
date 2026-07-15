# Alif — Master Ideas File

## 🟢 [DONE 2026-07-15 — Fix A shipped+deployed+remediated; follow-ups open below] `/api/discover/add` fuzzy lookup mis-resolves new citation forms — 17 documented collisions
During the Momo vocab intake, `/api/discover/add` with bare `تالي` resolved through
the comprehensive lemma lookup to **أَلَا (id 691)** — a false collision (likely
clitic-strip or normalization path), which wrongly promoted the interjection to
acquiring (reverted same day; see ActivityLog `vocab_import` 2026-07-15 and
`research/analysis-2026-07-14-momo-readiness-volume-sweep.md` Part 4). If التالي in
running text mis-maps the same way, review sentences containing it credit the wrong
lemma. Check `build_comprehensive_lemma_lookup` collision handling for tail-of-word
matches; add a regression test for تالي/التالي once fixed. Also: تالي itself (core
"next/following") is still absent from the vocabulary — add it properly after the
lookup fix.

**2026-07-15 update — systemic, 17-case dataset.** The full-book Momo sweep hit the
same bug 16 more times in one batch: the add-path lookup strips non-clitic prefixes
and matches wrong lemmas — لاحظ→حَظّ (لا treated as negation), كناس→نَاس (ك as
preposition), سيجار→جَار, رمادي→رَمَاد, اصبح→صُبْح, امير→مَارّ, حقيقي→حَقِيق (this
one actually introduced the wrong lemma), etc. Full table in
`research/momo-vocab-queue-2026-07-15.md`. All no-ops against known lemmas EXCEPT
تالي and حقيقي (wrong introductions). Fix candidates: (a) `strict=true` flag on /add
that requires exact-bare or camel-lemma match, (b) validate that a stripped prefix is
a real clitic for the POS, (c) reject resolutions where the matched lemma's length <
~60% of the query. The 17 pairs are a ready-made regression test set. Workaround in
use: server-side direct create with exact-bare check (see momo_direct_create.py
pattern in the queue doc).

**2026-07-15 investigation DONE** — findings in
`research/spec-2026-07-15-lookup-clitic-collision.md` §7 + fixtures in
`research/lookup-collision-findings-2026-07-15.json`. Headline: hypothesis
refuted — 16/18 collisions come from the **CAMeL last-resort layer**
(`find_best_db_match` greedily scans unranked analyses), only 2 involve clitic
stripping. 63 more future collisions found among unlinked FCE forms (فستان→سِتّ);
62 lemmas have corrupt `lemma_ar_bare` currently papered over by the fuzzy layer.
Blast radius small: 5 reviewable mis-mapped SentenceWords + 116 inactive.
Recommended: Fix A = citation-strict lookup mode for /add (MLE whole-word gate +
ال-prefix-only clitic strip + no CAMeL last resort; scores 18/18 bad, keeps all
بالمكتبة-class good); Fix B (separate) = MLE-gate the shared-path last resort
(retains 94% of legit layer-4 rescues). Fix not yet implemented.

**2026-07-15 Fix A SHIPPED (PR #212) + DEPLOYED + REMEDIATED**: `lookup_lemma_citation()`
in `sentence_validator.py` (deterministic V7 — the MLE whole-word gate proved
redundant once single-letter strips + CAMeL last resort were removed, scored
identically: 18/18 bad, 36/63 FCE, all good cases), wired into
`/api/discover/add`(+`add-batch`). Remediation same day via
`scripts/remap_collision_mismaps.py`: 121 mis-mapped SentenceWords → 4 residue
(116 remapped, 5 active sentences reverified — 3 re-stamped / 2 positions nulled);
تالي re-added as #4374 via the fixed endpoint, حقيقي already #4304. Full outcome:
spec-2026-07-15 §8. Remaining open items from this bug family:
(a) **Fix B** — MLE-gate the shared-path CAMeL last resort + make layer 4 report
alternatives (deferred; verification gate is holding, measure first);
(b) **FCE intake** (`frequency_core_intake._resolve_existing`) still uses the fuzzy
path + `resolve_existing_lemma` for display-form linking — 63 known future
collisions when intake reaches those ranks; needs its own linking-policy pass;
(c) ~~corrupt/divergent `lemma_ar_bare` fields~~ **DONE 2026-07-15** (PR #215 +
prod apply, backup `alif_pre_bare_repair_20260715_095834.db`): 42 bares rewritten
(old keys stashed in `forms_json.old_bare_form` except 10 curated wrong-word keys
احتجاب/طن/زق… deliberately removed) + 21 `citation_alias` entries (adverbials غَداً,
clitic displays بِبُطْء, defective-participle convention, loanword spellings) + 4
proper-name displays cleaned. Census 104→37, all intentional (35 shadowed + لـ/سـ
particles). Standing check: `repair_corrupt_bares.py --census`. Audit stays at
`research/corrupt-bare-audit-2026-07-15.json`;
(d) ~~post-deploy remap+reverify~~ DONE 2026-07-15 — 4-row residue remains:
3 reviewable ستين→سِتّ (LLM verifier passes the number-family adjacency; clean
fix = import ستون with oblique ستين in forms_json, then re-run
`remap_collision_mismaps.py --fix`) + 1 inactive تفوق→فَاقَ;
(e) ~~re-add تالي + حقيقي~~ DONE 2026-07-15 (#4374, #4304).

> This file tracks ALL ideas for the project. Never delete ideas. Mark as [DEFERRED], [REJECTED], or [DONE] with reasoning. Every agent should add new ideas discovered during work.

---

## 🟢 [DONE 2026-07-10] Verified Mac backup transport for Alif and Koigen

The existing 09:00 launchd pull now snapshots the live Alif SQLite database through
SQLite's online backup API, verifies it before atomic publication, and fixes the
retention-status bug that made a successful run report exit 1. The same job pulls the
newest Koigen durable-state bundle from alif, verifies it both before and after transfer,
rejects stale/corrupt/partial snapshots, publishes by atomic rename, and delegates local
retention to Koigen's verified 35-day/minimum-seven policy. FileVault protects the Mac
copy. Time Machine remains unconfigured, so the Mac is the sole off-host copy for now;
monitor snapshot age and keep a recorded restore drill.

## 🟢 [LIVE 2026-07-09 — PR #207] Return recovery: repair state leaks before new pedagogy

The first vacation break exposed a 1,197-word actionable due backlog and three production
correctness leaks that directly slow genuine learning. Approved order:

1. Make the struggling-word reintro Continue acknowledgement-only. It currently writes an
   FSRS Good despite being labelled pure re-exposure; 62 zero-correct lemmas crossed into
   FSRS and 40 later leeched.
2. Separate immutable learning provenance (`textbook_scan`, `book`, `frequency_core`, ...)
   from the current acquisition episode (`new`, `leech_reintro`). Count all actionable due
   Box-1 words in recovery; rate-limit recycled leeches independently from net-new intake.
3. Gate recovery intake on primary sentence retrieval, not the collateral-heavy blended
   ReviewLog accuracy, and count learner cards rather than passage child rows.
4. Make mature duplicate auto-skip due-aware; make speculative session prefetch read-only.
5. Repair due-cleared and transition metrics, then establish cold primary recall by gap and
   graduation reason before changing Tier E/Tier 0 or mature-collateral credit.

PR #208 now runs a small exact-surface form experiment for yellow marks (Hard remains
correct: failed recognition with retained familiarity). The remaining curriculum step is to
select an authorized contemporary literary text as the primary new-word lane while retaining
a smaller Quran/classical lane. No production history repair without a reviewed dry-run.

Shipped in PR #207 and deployed as `ada614a`: acknowledgement-only reintro; explicit acquisition
episode kind; due-aware Box-1 recovery debt; primary-card recovery evidence; read-only,
intro-safe speculative sessions; due/canonical/acquisition-safe frontend skipping; honest
cleared/transitions/cold-recall metrics. Historical meaningful-source reintro episodes remain
ambiguous by design (no backfill). The completed follow-up simulation supported a strict
due-FSRS recovery trigger and bounded daily leech-reintro lane, now live in PR #208 below.
Prefetch remains ULK/introduction-read-only, while existing JIT mapping hardening remains
allowed.

## 🟢 [LIVE 2026-07-10 — PR #208] Return recovery tuning + exact-form pilot

The approved follow-up to PR #207 was independently reviewed, merged, and deployed from
`main` as `13b25e3`. Production verification found strict main-lane FSRS debt 912,
actionable Box 1 / due Box 2 at 136 / 17, and the intended true-new intake budget of zero.
No schema migration, data backfill, cache rebuild, or cron change was required.

**[LIVE] Evidence-backed recovery capacity**

- Add strict main-lane FSRS due ≥750 to the existing earned 0/8/30 true-new budget. This
  threshold is above normal active-day debt (343–439; observed high 576), above a roughly
  two-sparse-day checkpoint (672), and below a five-day break (806).
- Cap leech reintroduction at 8/UTC day; close at actionable Box 1 ≥20, due Box 2 ≥30, or
  strict main-lane FSRS due ≥750. Enforce remaining Box-1 headroom inside the batch.
- Judge `leech_reintro` treatment from `acquisition_started_at`, with five fresh reviews
  before a verdict. Production logs showed 102/161 treated episodes re-suspended on review
  one, including 76 first reviews rated Good.

**[LIVE] Yellow exact-surface N-of-1 pilot**

- Deterministic 50/50, reading-only, non-acquisition FSRS yellow events; non-trivial
  conjugation/inflection only; require a different reviewable sentence with exactly the
  normalized form and permit only one unresolved episode per lemma to prevent cross-arm
  contamination.
- Treatment changes one already-due sentence representation, not workload/rating/due date.
- Record both first-next-primary any-form intention-to-treat and different-sentence
  exact-form outcomes; integrate undo and pause during acquisition.
- Review delivery after 4–5 active weeks and retention/safety after 8–10 active weeks.

**[LIVE] Curriculum safety**

- Reader-visible imported stories stay weak +10 unless explicitly marked
  `metadata_json.curriculum_role="primary"`; only an intentionally chosen target text gets
  +195. Existing active imports are not silently treated as contemporary curriculum.

**[TODO — learner input required] Activate the literature lane**

- Obtain a selected/authorized Arabic text or chapter. Current evidence favors the opening
  of *Men in the Sun* (84% baseline; ~96.4% after 150 targeted gaps) for contemporary
  reading. Run hardened mapping + readiness analysis before marking it primary.
- Keep *The Collared Dove* as a secondary classical candidate after hardened reimport.
- Only after a real target exists, implement a 4:1 contemporary/classical-or-Quran split
  over **earned new intake**; due reviews remain obligation-driven. Do not re-enable Quran
  verse cards implicitly.

Full evidence: `research/analysis-2026-07-09-return-recovery-next-phase.md`.

## 🟢 [LIVE 2026-06-13 — PR #199] Dragoman → Alif vocabulary discovery from external Arabic text

`/api/discover/{words,add,add-batch}` (`backend/app/routers/discover.py`). The Dragoman polyglot magazine sends a block of Arabic prose; `/words` returns the highest-value lemmas **not yet in Alif** (frequency-ranked, glossed), and the reader's "add to Alif" buttons POST chosen words back to `/add[-batch]`, which find-or-creates the canonical lemma and introduces it **immediately, bypassing `DAILY_INTRO_CAP`** (explicit user add). Quality gates + material generation run in a background task; lemmas tagged `source="dragoman"`. Word identity goes through the hardened lookup path (`build_comprehensive_lemma_lookup` + `lookup_lemma` — clitics + variants→canonical) so already-known words (incl. clitic-attached) are correctly excluded. Deployed + verified live 2026-06-13.

**[TODO] Follow-up — store the source sentences as a reusable corpus.** The newspaper/essay sentences Dragoman sends are authentic reading material and would make excellent review material *once the newly-added word is known* (better than LLM-generated). But do NOT bolt this onto the discover endpoints and do NOT trust the discovery-time CAMeL lemmatization as the stored mapping. Instead:
- Route ingestion through the existing corpus path (`book_import_service` / `create_book_sentences`), which already persists `SentenceWord` rows with `lemma_id IS NULL` for not-yet-known surfaces (the sanctioned storage gate), tagged by a new `source` (e.g. `"dragoman_corpus"`).
- Let the cron healing/reverify pipeline (`fix_null_lemma_ids` + `reverify_all_active_sentences`) do the real mapping + stamp `mappings_verified_at` — the discovery lemmatization is a hint, not a verified mapping. Stored sentences stay non-reviewable (corpus sentinel) until verified.
- Reality check: newspaper Arabic is dense with unknowns, so most stored sentences sit inactive until vocab catches up (like the Hindawi backlog at line ~1210) — a long-term authentic-corpus investment, not an immediate win. That's why it's a separate feature, not part of word discovery.

## 🔵 [TODO 2026-06-10] Intro-cap bypass guardrails + acquiring-backlog drain (from state-of-project review)

From `research/analysis-2026-06-10-state-of-project.md`: the W22–W24 north-star collapse was
caused by an unlogged server one-off (`/tmp/complete_tiers.py`, 2026-06-03) bulk-promoting 227
words with `enforce_daily_cap=False` — 17% re-suspended within a week.

1. **Log-on-bypass**: `start_acquisition()` should `log_activity()` whenever
   `enforce_daily_cap=False` is passed, so bulk mutations are visible in the same dashboards
   that track their consequences. Plus a first dedicated pytest for the daily cap
   (acquisition_service has no test file).
2. **Drain the backlog**: run `demote_inert_acquiring.py` over the 138-word acquiring pool
   (127 overdue, 85 Box-1) and let the cap re-promote at ≤30/day.
3. **Judge-gate leech reintro** (consumer #2 of the Part C word-value judge below) is now
   time-sensitive: 135 suspended words hit their 3–30d reintro timers in W25–W27; without the
   gate the suspension wave replays.
4. **Commit the due-coverage refill recipe** (`/tmp/refill_deficit.py` from 2026-05-29) as
   `backend/scripts/refill_due_coverage.py` + cron step; 51/611 due words currently have zero
   reviewable sentences, several being inflected-form artefact lemmas (نَدْرُسُ, يَكْتُبُونَ)
   that should be retired via the judge, not regenerated (one has generation_failed_count=28).
5. **Review-analysis hygiene**: segment weekly retention by difficulty band (675 cards at
   difficulty ≥7) when judging Lever 1 / at-risk-bias effects, instead of the blended number.

**Ground-truth correction (same day, `analysis-2026-06-10-two-week-ground-truth.md`):** the
"collapse" headline was a flow-metric artifact — known stock grew +89/week and the new-word
funnel converts at 43% in ≤2 weeks. Items 1–5 above stand, with two additions and a re-order
(unpin throttle first):

6. **Unpin the recovery throttle from phantom words** (now top priority, smallest change):
   exactly 9 Box-1 words have times_seen=0 — 2 proper-name artifacts (نَجَحَت, ثَمِينَه,
   stuck since May 4: selector filters them but nothing demotes the acquiring rows) + 7
   generation-dead rares (رَخَّ genfail=28; ذَكَرِيّ has a wrong gloss). With
   `RECOVERY_BOX1_UNREVIEWED_LIMIT=5` this alone keeps the intro budget in earned-recovery
   mode permanently ("intros run 5–11/day not 30"). Fix: exclude `proper_name` +
   generation-dead (genfail ≥ N / max backoff) from `_recovery_backlog_counts`; demote those
   rows to encountered/retired.
7. **North-star instrumentation**: the headline metric must be the **known-word stock curve**
   (weekly count of known/learning), never grad−suspension flow — suspensions are 3–14d
   cooldowns of mostly never-known words and cost only ~5% of review volume. Add the stock
   curve to the stats screen / weekly review template.
8. **Watch item (~W26)**: understood% dipped 60% → 45–55% after the June-3 flood (no_idea ≈ 0,
   so stretch not drowning). If it hasn't recovered toward ~60% as the cohort graduates,
   revisit the at-risk scaffold-bias multipliers (2026-06-06 change).

## 🔵 [TODO 2026-06-10] Code-health cleanup queue (from state-of-project review)

Audit details in `research/analysis-2026-06-10-state-of-project.md` Part 3. In order:
1. `backend/scripts/archive/` sweep — move ~60 completed date-coded one-off backfills
   (zero risk; `ls scripts/` is part of the mandatory Rule-14 reads, so decluttering pays daily).
2. Dead-service deletion after import check: `soniox_service.py`, `chimera_audit.py`,
   `bare_shape_check.py`, `pattern_enrichment.py`, `pipeline_tiers.py`, `grammar_tagger.py`.
3. Tests for the invariant-bearing untested services: `acquisition_service`,
   `sentence_eligibility`, `canonical_resolution`, `mapping_rescue`.
4. Frontend Stats type drift: rename `known_words/learning_words/new_words` →
   wire-format `known/learning/new` in `types.ts` + drop the hand mapping in `api.ts:335–368`
   (`streak_days` is declared but never sent at all).
5. Split `app/index.tsx` (5,284 LOC): extract the four card components into `lib/review/`.
6. Extract validator normalization helpers + `FUNCTION_WORD_GLOSSES` into a leaf module —
   only alongside other validator work (iterated-area caution).

## 🔴 [INITIATIVE 2026-06-03] Rebuild the frequency core on properly-lemmatized sources

**Root finding.** The frequency core's quality problems all trace to **un-lemmatized source data**.
Of the 7 designed-in sources, only 3 are loaded: `camel` (97%, raw morphological-analyzer SURFACE
counts), `news` (98%), `hindawi` (34%, **children's books** — not classical lit). The
learner-grade *lemmatized* sources are **0% populated despite columns/loaders/weights existing**:
`buckwalter` (Buckwalter & Parkinson *A Frequency Dictionary of Arabic* — canonical 5k-lemma learner
list, sense-disambiguated), `kelly` (Leeds Kelly M3, CEFR-tagged), `artenten`, and `islamic` (Quran).
Because counts are surface-form, they land on the wrong lemma: آمر "to command" sits at **#10**
carrying the frequency of the noun أمر "matter" (which itself sits at #1384); نَزَّلنَا (inflection)
ranked top-1000. This is the recurring lemmatization/data-cleanup pain, at the curriculum's root.

**Plan (in order):**
1. **Deep-research sweep (LAUNCHED 2026-06-03, wf_bc3c3923)** — vet/locate high-quality LEMMATIZED
   Arabic frequency lists (Buckwalter&Parkinson, Kelly, Aralex, arTenTen, SUBTLEX-AR, Quranic Arabic
   Corpus) for lemmatization quality, classical/Quran coverage, licensing, obtainability.
2. **Rebuild the core** from the best lemmatized sources + reweight away from children's-lit/news.
   A lemmatized source natively fixes the homograph/inflection conflation class.
3. **Quran-frequency track (#1 from chat) — ✅ DONE 2026-06-03 (PR forthcoming).** Computed from the
   **Quranic Arabic Corpus v0.4** (corpus.quran.com, GPL — genuinely lemmatized), NOT our own
   `QuranicVerseWord` corpus (only 40/6,236 verses lemmatized — dead end). New
   `app/services/quran_frequency.py` maps QAC lemmas → Alif rows (Quran-aware normalization +
   POS-aware homograph disambiguation), populates `islamic_rank` (weight 150→700, penalty-exempt),
   and drives a separate "Quran Core" stats track. 57.7% lemma / 84.7% token coverage; unmapped
   residue = honest gaps. See experiment-log 2026-06-03.
4. **Homograph conflation fixes (#2 from chat)** — partly addressed for the Quran track via QAC
   POS-disambiguation (أَمَرَ verb vs أَمْر noun → different lemmas). The general MSA-side fix
   (re-attach surface counts to the right lemma) still folds into the value-judge / lemma-quality work.
5. **Classical/medieval LITERARY track (beyond Quran) — ❌ NO-GO for now (researched 2026-06-03).**
   `research/analysis-2026-06-03-classical-literary-frequency-track.md`: no off-the-shelf lemmatized
   classical frequency data exists; OpenITI is a usable raw-text base (CC-BY-NC-SA, ~2.25B words) but
   ships no lemmatization, and **no validated classical Arabic lemmatizer exists** (CAMeL is MSA-only;
   arabiCorpus untagged + retiring 2027; Shamela "already lemmatized" claims refuted). Deferred. The
   viable build path is an LLM-in-context lemmatization pass over OpenITI samples (mirroring
   polyglot's Greek/Latin approach) — revisit only if we invest in that pipeline + benchmark it.

**Shipped already (2026-06-03):** variant→canonical remap (inflected-form entries, PR #193) + invariant
guard; low-tier completion (199 promoted, 32 leeches reintroduced); stats honesty (function-word/variant
exclusion, PR #190); adaptive bands + tier-complete collapse (PR #194 + follow-up).

---

## 🔵 [DESIGN READY 2026-06-03] LLM word-value judge (Part C of the growth+maintenance program)

**Why.** Frequency rank is the wrong sole signal for what to learn. "kissing" (rank ~15000) is
wanted because the user reads erotic stories; "juhri" / OCR garbage is an artefact to drop forever.
The supply wall at rank 2000–3000 is **224 `frequency_core_entries` flagged `needs_manual_review`**
(of 225 un-imported ≤3000) — the conservative first-pass judge `_classify_unmapped_entries`
(`frequency_core_intake.py`) punted on them (requires `confidence=high` + morphological relation;
anything needing sense/context → `action=skip`). This blocks the user's 2500→3000 goal.

**Key design insight (grounded in the code):** do NOT build a parallel service. Extend the existing
intake judge with a **richer, artefact-aware second pass** over the `needs_manual_review` backlog,
and reuse the same verdict shape for the leech + ordering consumers.

**Verdict schema (one judge):** `{is_artefact: bool, is_real_word: bool, usefulness: high|med|low,
provenance: freq|book|ocr|story, recommended_action: map|create|exclude|defer, lemma_ar/bare/gloss,
reason}`.

**Three consumers:**
1. **Intake (highest value, do first):** `judge_needs_manual_review_entries()` — second pass that
   (a) sets `excluded_reason='artefact'` on OCR/junk so they stop being retried, (b) for real,
   useful, context-dependent words makes a best-effort map/create (slightly more permissive than the
   first pass but still `_related_to_source`-checked and routed through `run_quality_gates`),
   (c) `defer` only when genuinely unresolvable. Run over rank ≤2500 first, then ≤3000.
2. **Leech reintro (`leech_service.py`):** before timer-based reintro, judge: artefact → drop
   permanently; hard-but-real low-freq → event-driven reintro (only on re-encounter, not timer);
   useful → steeper chronic escalation. Pairs with the existing low-priority 60-day cap.
3. **Intro ordering (`word_selector.py`):** provenance/usefulness boosts so a user-content word
   (story/OCR) outranks raw frequency, and a hard word doesn't overshadow easier ones.

**Caveat:** leech *timing* params can't be validated by the simulator (no per-word difficulty); use
real-data backtest. Artefact-filtering + intake are data-driven and shippable without the sim.

**Status:** design ready, branch not yet cut — its own focused session (new LLM subsystem + a prod
intake run). Parts A (deficit fix) + B (throttle) shipped & deployed 2026-06-03 (PR #189).

**Add a 4th consumer — `proper_name_vs_content` (folds in here; do NOT build separate proper-name infra).**
Audit 2026-06-03 (`scripts/audit_function_proper_words.py`, triggered by the reading-readiness work): the function-word
and proper-name **gates are sound** (FUNCTION_WORDS=224 is comprehensive; the `before_insert`
`pos=noun_prop → word_category=proper_name` listener + `word_category` filters stop *new* leaks). The
residual problem is **not leaks needing more gates — it's mis-categorization in both directions**, which
is a homograph/sense judgement, exactly the judge's job:
- **False-positive suppression (the real risk).** CAMeL mis-tags common **loanwords** as `noun_prop`
  (papaya/jeans/pullover/tango/Viking/cathedral/hello — 7 found, currently `cat=None` so still learnable,
  but the insert-listener would suppress any future such word as a fake "proper name"). Plus content
  mis-tagged `proper_name`: شديد "severe" (fixed deterministically 2026-06-03) and the genuinely
  **ambiguous name↔word homographs** صالح/قادر/بكر/منتش/نجحت (a blanket rule fails — needs the judge).
- **Under-detection.** Only ~46 lemmas are tagged `proper_name` out of ~4,000; the reading-readiness OOV
  was full of un-tagged names entering as content from corpus/book imports. The judge should flag these.
- **Legacy ULK residue (low priority, cosmetic):** 46 function-word lemmas + ~20 genuine-name lemmas
  carry old `known`/`acquiring` ULK rows (pre-filter Duolingo/leak residue). They mostly reflect reality
  (the user does know كان، محمد) so they're not harmful — at most a stats-honesty `suspend` pass; let the
  judge decide which to suspend rather than a blanket sweep.

Verdict-schema add: `proper_name: bool` (with the existing `is_real_word`/`reason`). Wire into the same
intake/quality-gate pass; the insert-listener should defer to the judge's verdict over CAMeL's
`noun_prop` when they disagree. Lesson recorded: a deterministic "backfill word_category from pos" pass
was inspected and **rejected** — it would have suppressed the 7 loanwords. Inspect before any blanket flip.

## 🔵 [TODO 2026-06-03] Stats display honesty (Part D)
"Top frequency gaps" in the stats currently lists function words (ال), merged compounds (اليوم→يوم),
and suspended leeches — not genuine missing content. Classify gaps: exclude function/merged/
`excluded_reason` artefacts, show suspended separately, so "% of top-N covered" is an honest
denominator for the 2500→3000 goal. Small; backend `learning_analysis.py` frequency-coverage section
+ the frontend stats screen.

---

## 🟡 [ANALYSIS DONE 2026-06-03] Confusion + Hard-flag pedagogy — wins & experiments

First full analysis of the confusion-capture data (the planned pass from the PR #167 entry below) **plus**
the broader `was_confused` / `variant_stats_json` corpus. Full write-up:
`research/analysis-2026-06-03-confusion-captures.md`. Headline findings:

- **Picker precision is strong, recall is the gap.** 21/21 suggested-picks were in the offered list
  (57% rank-0, median 0). But of 15 free-text captures, the actually-confused word had been **missed
  12/15** — that's why the user typed. Misses: ed≤1 matches truncated at the 8-visual cutoff (نام/صام
  rank 9, بث/بحث rank 8), same-root forms buried by the `len_gap*2` penalty (حساب/حاسوب rank 49), one
  un-modeled anagram (مبدأ/معبد), one nisba data-quality mask (جُحْري vs جُحْر), 2 pure meaning-misses.
- **Most "Hard" flags are *form recognition*, not word-identity confusion.** Of 943 `was_confused`
  reviews only 36 named another word; on the 125 post-picker flags, **85% were on a non-dictionary
  (inflected/clitic) form**, only 6% were the bare dictionary form. Confused words took ~60% longer
  (37.6s vs 23.4s). Verb conjugation ~28%, noun/adj inflection ~24%, ال/case ~23% (likely just
  forgetting — *don't* grammar-treat these), prepositions ~15%. Hardest sub-class: derivational
  nominalization mapped back to a verb (التخطيط→خطط, اللاعب→لعب, المسروقة→سرق).
- **Mechanism taxonomy (n=36):** 33% same-root derivation, 42% other visual (dots/rasm/rhyme/insert),
  8% phonetic (س/ص), 6% pure meaning.
- **The morphology bridge already exists** in `WordInfoCard` (clitic→stem→suffix color bands via
  `decompose_surface`, lib/review/WordInfoCard.tsx:530) but is **pull (tap), not push**.
- **`variant_stats_json` already collects per-surface `{seen,missed,confused,form_key}`** on the
  canonical ULK — exactly the per-form confusion signal — but is **write-only; nothing reads it**, and
  it is **not query-ready**: only 7% of confused surfaces carry a `form_key` (the tagger
  `_match_surface_form` only matches strings already in `forms_json` after stripping ال). Running the
  stronger `decompose_surface` recovers ~45% of the untagged into real form types; ~55% (irregulars,
  broken plurals, true identity) resist without CAMeL or richer `forms_json`.

### OBVIOUS WINS (data-backed, mostly reuse existing code)

1. **[BIGGEST] ✅ SHIPPED 2026-06-03 (PR #192).** Push the morphology bridge on a Hard/confused mark,
   branching on cause. When a word is rated confused and `surface_bare != lemma_bare` and it's not
   ال/case-only, reveal the `WordInfoCard` decomposition ("يُفْسِدُ = present of أَفْسَدَ 'to spoil'"). If
   surface == dictionary or ال-only → skip (that's recall). Implemented as
   `confusion_service.classify_surface_morphology()` — closing the verb-tense/forms-not-in-`forms_json`
   gap the color bands missed (~45%→ most inflected forms) and storing `category`/`form_key` on
   `variant_stats_json` so per-form confusion is queryable (folds in win #4). Bundled measurement:
   `morph_category` logged per yellow mark. Effect is encoding-feedback + spurious-lapse prevention +
   data plumbing — NOT a direct retention lever (see the mechanism analysis); the scheduling
   experiments below are what it unblocks.
2. **Confusion-picker recall fixes** (`confusion_service.find_similar_words`): (a) force `edit_distance==1`
   visual matches into the visible top-8, or raise visual `max_results` 8→10; (b) shrink the `len_gap*2`
   penalty when `same_root`; (c) add a non-adjacent anagram signal (`sorted(a)==sorted(b)`). Each maps to
   a specific documented miss.
3. **Name the pattern (wazn) in the decomposition label** — extend `FORM_KEY_LABELS`/pattern naming so
   active participle (فاعِل), passive participle (مَفْعول), maṣdar patterns are spelled out instead of a bare
   stem. The derivational cases are the hardest and currently the least explained.
4. **Classify `variant_stats_json` at WRITE time with `decompose_surface`** (not the weak
   `_match_surface_form`); store `form_key` + clitic-type per entry. Then "which form is most confused
   across all lemmas" becomes a real `GROUP BY` instead of compute-on-read over raw strings.
5. **Add `surface_form` column to `ConfusionCapture`** — cheap; makes explicit captures self-contained
   (today the form is only join-recoverable via `sentence_id`).

### EXPERIMENTS (need design + measurement; payoff uncertain)

- **Reason chips on the confused card** — one tap: "mixed up with another word" (→ picker) / "didn't
  recognize the form" (→ records `reason=grammar`, auto-expands decomposition) / "just forgot it"
  (→ `reason=recall`). Gives the *causal* label the surface form can't, and unifies win #1 with data
  capture. Risk: friction lowers capture rate — A/B vs current.
- **Root-derivation family card** (لَعِبَ→لَعِب→لاعِب→مَلْعَب with pattern labels). Teaches the verb↔noun
  derivation 24%+ of misses turn on; also answers the 33% same-root picker confusions. Ties into the
  in-progress **Root-showcase sentences** idea below.
- **Pattern grammar-features + micro-lessons** via the existing `grammar_features` system: tag verb-form
  (I–X) / participle / maṣdar patterns, slot a tiny lesson after N stumbles on a pattern. Data-driven
  priorities: present conjugation, maṣdar nominalization, broken plurals.
- **Contrastive pairing / contrast drills** for captured confusion pairs (قدِم/قدَم, شهير/شهري) — schedule
  both in one generated sentence or adjacent cards. (Already floated in the PR #167 entry below; now
  data-supported for the same-root cohort.)
- **Per-form scaffolding/leech**: when `variant_stats_json[form].confused` is high while the canonical is
  "known", schedule a sentence using *that* form or surface its derivation. Blocked on win #4 (queryable
  data) and on more volume — at 448 confused events, mostly count=1, it's enough to prioritize *which
  patterns to teach*, not to gate individual cards.

### ENABLERS / DATA QUALITY

- **CAMeL morphology** (already a dependency) to classify the ~55% of confused surfaces `decompose_surface`
  can't, including irregular/broken-plural forms — pushes form-tagging toward complete.
- **Base-noun lemmas alongside nisba/derived forms** (جُحْر vs جُحْري) so metathesis/identity pairs aren't masked.
- **wazn backfill** (see Root-showcase entry's `Lemma.wazn`-NULL caveat) — helps pattern naming (win #3),
  root-family cards, and per-form analysis alike.

### CAVEATS carried forward
- n=36 captures / 125 post-picker flags, single-user — percentages are directional.
- Pre-2026-05-27 `was_confused`/`confused` counts are noisy (predate the picker; could be forget/grammar/
  confusion) — use only for surface-form distribution, never intent.

---

## 🔵 [TODO 2026-06-01] Polyglot Latin: general gender/POS-contradiction lemma guard

`la.py` `_LEMMA_OVERRIDES` (branch `sh/polyglot-latin-homograph-guard`) corrects LatinCy lemma errors whose hallmark is that the assigned lemma's morphological class **contradicts the POS/Gender LatinCy tagged on the same token** (`pilum`: tagged Neut, lemma'd to masculine `pilus`). Today this is a curated per-surface table. The general version would, for any token, detect when the assigned lemma's declension gender/POS disagrees with the tag and look up a same-stem homograph lemma of the matching class — catching the whole class without enumeration. **Blocked on data**: we don't store per-lemma gender/morph-class on `Lemma` rows. When we do (or can derive it from `forms_json`), generalize the override into a deterministic guard and shrink the table. Until then, extend `_LEMMA_OVERRIDES` per confirmed case and run `scripts/audit_homograph_mappings.py` as the discovery sweep.

## 🟡 [IN PROGRESS 2026-05-27] Root-showcase sentences — pack multi-derivation sentences from one root

User idea: generate sentences that exploit as many forms of a single Arabic root as possible (e.g. *الكاتب كتب كتبًا للكتّاب في المكتب في المكتبة* for ك-ت-ب, or *المحاسب معي الحاسوب حسّب الحاسب* for ح-س-ب). The pedagogical wager: contrived wordplay sentences are memorable *precisely because* they're contrived, and they directly reinforce Arabic's root-pattern system — every reviewed surface form earns its own lemma credit (FOUNDATIONAL rule), so a single showcase sentence yields N reviews instead of 1.

**5-phase implementation on branch `sh/root-showcase-sentences`:**

1. **Phase 1 (read-only analysis)** — `backend/scripts/root_showcase_candidates.py` ranks roots by `(known+acquiring lemmas) × √productivity_score` with a ≥3-lemma palette floor, flags missing canonical wazn families. Output: `research/root-showcase-candidates-<date>.{json,html}`. Top roots from today's snapshot: ق.ب.ل (18 lemmas, 56% known), ك.ت.ب (10, 100%), ج.م.ع (11, 82%), ب.ن.ي (9, 78%), ع.ل.م (10, 90%), د.ر.س (8, 100%).
2. **Phase 2 (schema)** — `Sentence.root_focus_id` (FK→roots) + `Sentence.kind` (e.g. `'root_showcase'`). Alembic migration.
3. **Phase 3 (gap-fill)** — for selected roots, ask LLM (with the *actual lemma palette* — `Lemma.wazn` is NULL on ~50% of gated lemmas, so the wazn-family diagnostic from Phase 1 over-counts gaps) to propose canonical Arabic forms for genuinely missing derivations. Route through `run_quality_gates()` so new lemmas get variant detection + enrichment + `gates_completed_at`. Dry-run by default.
4. **Phase 4 (multi-target gen)** — `generate_root_showcase_sentences()` in `sentence_generator.py`. Sonnet (NOT Codex — A/B 2026-05-26 showed Codex weaker on Arabic naturalness). Prompt explicitly says wordplay/parallelism welcome, redundancy IS the point. Routes through existing `validate_multi_target_sentence` + `write_multi_target_sentence` (lock-discipline-safe, extracted from 2026-04-17 incident). Stamps `root_focus_id` + `kind='root_showcase'`. TTS lazy.
5. **Phase 5** — tests + branch + self-review PR per CLAUDE.md Rule #7.

Showcase sentences drop into the regular session pool — each surface form earns review credit per the FOUNDATIONAL "every word in every sentence" rule. No new card type needed for v1. A dedicated "Root Showcase" review mode is a future enhancement.

**Key design tension** the wazn-NULL caveat exposes: `Lemma.wazn` was supposed to be the ground truth for "which derivations under root X already exist," but it's only ~50% populated. Phase 3's LLM has to reason from the actual lemma glosses+forms instead. This is fine for the immediate feature, but it suggests a separate backfill pass to fill missing `wazn` values is worth doing — would help everything downstream (Explore tab pattern browsing, root family rendering on intro cards, this feature, etc.).

---

## 🟢 [LIVE 2026-05-27 — PR #167] Confusion capture: ground-truth what user actually confuses with what

User observed that real confusions don't match algorithmic clusters (same-root, gloss-keyword overlap, surface-prefix). Algorithm-driven cluster-aware features would target the wrong pairs. Built a passive capture layer: on yellow ("did not recognize") taps, a small collapsed "Confused with another word?" link below WordInfoCard expands into 5 similar/phonetic candidate chips + a free-text input. Each capture stored to new `confusion_captures` table with explicit `capture_method` ('suggested_pick' | 'free_text') and `candidates_shown_json` (so later analysis can answer "did the algorithm guess right?"). No scheduling change yet — pure data collection. Migration `f8a9b0c1d234`. Tests `TestConfusionCapture` in `test_sentence_review.py`.

After ~50+ captures, an ad-hoc Claude analysis pass will: (a) compute capture rate among yellow taps, (b) compute `suggested_pick` vs `free_text` ratio (algorithm-precision proxy), (c) batch-resolve free-text entries against the lemma DB to compute candidate-hit rate (% of confusions where the actual word was in `candidates_shown_json`). Low candidate-hit rate empirically confirms the user's claim and unlocks replacement strategies. High candidate-hit rate means current similarity heuristics are mostly OK and the issue is elsewhere (e.g., user attention / context cues).

> **Analysis done 2026-06-03** (at 36 captures) → see the top entry "Confusion + Hard-flag pedagogy — wins & experiments" and `research/analysis-2026-06-03-confusion-captures.md`. Verdict: precision high, recall is the gap; and most Hard flags are *form recognition*, not word confusion.

**Downstream ideas (pre-data, not yet decided):**
- Contrastive pairing in `sentence_selector`: if A and B are observed-confused often, schedule them together with ≥5 cards between.
- Cluster-aware intro: when introducing a new word that has a stuck observed-confusion partner, show a comparison-card variant of the intro.
- Interference-weighted leech detection: drop or extend cooldown for words that re-fail specifically alongside their confusion partner.

All three are speculative until the data lands.

---

## 🟢 [LIVE 2026-05-26 — PR #158 + #159] Alif hybrid Codex provider for audit + enrichment

Audit + enrichment pipelines flipped from Claude Haiku CLI to Codex `gpt-5.5`
CLI. Generation (Sonnet sentence-gen, Opus story-gen) stays on Claude. Default
is Codex; `ALIF_AUDIT_PROVIDER=claude` is the global escape hatch. Server has
`CODEX_HOME=/opt/alif/.codex` in `/opt/alif/.env`; verified routing live with
a `codex_cli/gpt-5.5` analytics row in `llm_calls_2026-05-26.jsonl`.

The flip was justified by two A/Bs run today
(`research/codex-vs-claude-{sentence-gen,enrichment-arabic}-2026-05-26.md`):
on the audit + enrichment surface Codex was strictly better — canonical Arabic
pattern names 9/9 vs Haiku 5/9, fact-checked cultural notes 10/10 vs 3/10,
full diacritization (Haiku drops indicative mood vowels), 1.7× faster. On
sentence generation Codex was weaker on Arabic naturalness under vocab
constraint (37/40 semantic coherence vs Claude's 40/40), so generation
explicitly stays on Claude. The Latin philology complaint that originally
triggered this investigation was prompt+parser bugs (closed by PR #157), not
a provider issue.

Auto-flipped via the `claude_haiku` alias routing in `generate_completion`:
all ~20 audit + enrichment call sites (`lemma_enrichment`, `pattern_enrichment`,
`flag_evaluator`, `book_import_service`, `grammar_tagger`, `frequency_core_intake`,
`import_quality`, `passage_generator`, `quran_service`). Plus one explicit
migration: `lemma_vocalization.vocalize_batch` was bypassing
`generate_completion` via direct `claude_code.generate_structured` — rewired
to go through the routing.

**Follow-ups (not done):**
- **Per-task provider override.** Today's escape hatch is global —
  `ALIF_AUDIT_PROVIDER=claude` reverts every audit/enrichment site at once. If
  Codex regresses on one specific task (e.g. flag classification), we'd want
  to revert just that task without losing Codex on the other 19. Idea: per-
  `task_type` overrides via env (`ALIF_TASK_<TASK>_PROVIDER=claude`) or a
  small config dict consulted by `_audit_provider(task_type)`. Don't build
  until a real regression demands it — premature config.
- **Vocalization-specific A/B.** The lemma_vocalization migration was
  blessed-by-association — the enrichment A/B showed Codex more faithful to
  full diacritization (the Achilles' heel of Haiku), and vocalization IS
  diacritization. But a dedicated 20-lemma A/B (claude vs codex on
  unvocalized lemmas, validated against the existing `validate_proposal`
  letter-match check) would close the loop. Cheap.
- **Limbic Codex adapter.** Codex calls don't enter
  `limbic.cerebellum.cost_log` (no adapter today). Codex is free under the
  user's subscription, so cost isn't the issue — it's volume analytics. A
  thin adapter writing `script="codex-cli"` rows would unify the dashboards
  with Claude CLI's existing rows. Polyglot has the same gap; build once,
  use twice.
- **Forms field completeness audit.** The A/B noted Codex tends to fill
  more nominal fields (`plural`, `dual`, `sound_m_plural`) than Haiku, which
  is more conservative per the "only include fields you are confident about"
  sub-rule. Mostly a Codex win — more useful for learners — but if Codex
  starts fabricating duals/plurals that don't exist for a particular noun
  class, the more-conservative Haiku behavior was actually safer. Monitor
  forms_json quality over the first month and flag if Codex is hallucinating
  rare forms.

---

## ✅ [DONE 2026-05-25] Polyglot: redesign the reader to mirror sentence-review

The reader (`frontend/app/polyglot.tsx`) is polyglot's *primary* UX but lacked
three affordances the sentence-review card has. Fixed so the reader works the
same for Greek, Latin, and any future polyglot language:
- **Show English** — full-page English translation revealed below the foreign
  text (page-scale "Show Translation"). Lazy + cached on new `Page.translation_en`
  / `Page.translated_at` via `reading_intake.ensure_page_translation`
  (`llm_cli.call_text`, sonnet/gpt-5.5, write-lock-safe). Endpoint
  `GET /api/texts/{sid}/pages/{n}/translation`; frontend prefetches on page load
  so the reveal is instant; toggle (not one-way) since the reader is re-readable.
- **Instant Next** — advancing enqueues the green sweep and flushes in the
  background (never awaited) while a page cache + next-page prefetch make the
  advance instant. Fixes "submission took forever" (old path awaited both the
  flush and the next page's server-side tokenization).
- **Back navigation** — `Prev` re-reads earlier pages; per-page red/yellow marks
  are restored from AsyncStorage; editing a mark there updates the server live
  via `markWord`. The green sweep is one-time per page via a deterministic
  `client_review_id = pr:{storyId}:{pageNumber}` so back-then-forward never
  double-counts. Last page → "Finish".

See `polyglot/CLAUDE.md` Hard Invariant 6 § "Reader UX redesign (2026-05-25)".

**Follow-ups (not done):**
- **[OPEN]** Pre-warm page translations in the cron (`warm_pages_ahead` already
  warms pages ahead — generate `translation_en` for those pages too) so the
  first "Show English" is instant even without the client prefetch. Bounded cost
  (only buffer pages). Deferred to keep this change client-prefetch-only.
- **[DONE 2026-05-26 — PR #152]** Sentence-aligned translation (English
  interleaved under each sentence) replaces the whole-page block. Reveal is now
  per-sentence; source is harvested `Sentence.translation_en` (cron-filled,
  with `ensure_page_translation` lazy-filling any NULL on first Reveal in one
  batched call). Reveal is one-way; footer mirrors polyglot-review.tsx's
  asymmetric spacer pattern (`[Prev] [Know all] [Reveal]` → `[Prev] [Next]
  [empty]`) so a double-tap can't skip the English.

  Side issue spotted in prod (not blocking — pre-existing data): the cron's
  sentence splitter occasionally treats "Kal." as end-of-sentence (Eutropius I.1
  page 1 → "...Palatine Hill on the eleventh day before the Kalends." + "of
  May, in the third year..."). Harmless to the reader (the gap is just where
  one English unit ends and the next begins under the next foreign sentence)
  but worth tightening when we revisit `sentence_harvest.py` — add Latin
  abbreviation exceptions (`Kal.`, `Id.`, `Non.`, `a.d.`) to the boundary
  detector.

## ✅ [DONE 2026-05-26 — PR #157] Polyglot Latin: three lookup-card quality fixes from the philology+translation audit

Audit: `research/polyglot-latin-philology-and-translation-audit-2026-05-26.md`
(philology = cron-cadence gap, translation = no Eutropius match). A follow-up
investigation surfaced what the user actually saw — the "translation is just
three words with no comma" complaint maps onto the *gloss* of an acquiring
lemma (e.g. `excidium → "demolition setting of the sun"`,
`exordium → "beginning introduction foundation"`), shown on the lookup card
because the enrichment had not yet run (the cron-cadence gap from the audit).
Three fixable code paths:

1. **Roma Aeterna gloss parser concatenates senses without a separator.**
   `polyglot/scripts/parse_roma_aeterna_apkg.py` line 34 replaces every HTML
   tag with one space, so `<div>demolition</div><div>setting of the sun</div>`
   becomes `"demolition setting of the sun"`. The TSV at
   `data/vocab/roma_aeterna.tsv` is the artifact (source .apkg is offline);
   `frequency_entries` + `lemmas.gloss_en` are propagated verbatim by
   `scripts/import_latin_vocab.py` (no comma stripping happens at import).
   Affected rows on prod: ~30–80 acquiring/learning lemmas with obviously
   run-on senses (spot examples: `excidium`, `exordium`, `administer`,
   `aeneus`, `anxius`, `finitimus`). Fix the parser so block-level tags
   (`<div>`, `<br>`, `<p>`, `<li>`) emit `"; "` rather than `" "`; that
   prevents re-occurrence on any future reparse. To repair existing DB rows,
   run a one-shot pass (extend `scripts/regloss_lemmas.py` or add a sibling)
   that detects "multi-word, comma-less, not starting with `to `/`I `/`the `,
   no `(` `)` `1.` `2.`" and re-glosses via Codex/Claude — scoped to studied
   lemmas first (acquiring/learning/lapsed, plus the nearest frequency
   neighbors). This is a different bug class from the 2026-05-26 wrong-meaning
   regloss (`regloss_lemmas.py`); that one trusted the form and fixed wrong
   meanings, this one trusts each sense and re-inserts comma separators.

2. **Lemma-philology QUOTES prompt allows meta-commentary translations.** Of
   the 7 acquiring lemmas manually enriched in the audit, `fere` came back
   with one 734-char Caesar passage whose `translation_en` was
   `"[Context: the passage contains 'omnibus fere annis' illustrating fere
   with quantities]"` — a meta-comment, not a translation. Per the 2026-05-21
   user spec, the Haiku verifier skips quotes (memory hooks, mild inaccuracy
   fine), so this can't be caught downstream. Tighten the QUOTES block in
   `polyglot/app/services/lemma_philology.py` (around line 193) with: "the
   `translation_en` field MUST be a faithful English rendering of `text`. Do
   NOT use meta-commentary like `[Context: ...]`, `[This passage illustrates
   ...]`, or `[The line shows ...]`. If a quote doesn't fit in ≤25 words,
   choose a shorter quote." Optional belt-and-braces: a structural post-check
   that rejects translations starting with `[`.

3. **`Kal.` / `Non.` / `Id.` / `a.d.` abbreviation splitter** — see the
   sibling note above, already tracked.

**Data-side companion (not a code fix):** the 7 manually-enriched acquiring
lemmas show 3/7 with zero collocations (`excidium`, `exiguus`, `latrocinor`)
vs 4-5 for the others. Could be verifier-stripped or genuinely sparse; worth
a spot-check on the next batch but not blocking.

## 🟢 [LIVE 2026-05-25] Polyglot: Latin as a second language (PR #140, deployed)

Latin alongside Modern Greek. Learner finished LLPSI Part 1 (Familia Romana);
goal: verify which words are still known (seed as assumed-known → confirm by
collateral exposure), grow gradually, read Eutropius. Full design + audits +
production validation: `research/polyglot-latin-design-2026-05-25.md`.

**Shipped + deployed to prod:** language-scoped acquisition pacing aggregates
(fixed a cross-language cap/recovery coupling found in the audit); LatinCy
`la_core_web_lg` provider (simplemma fallback) behind the lemma-quality safety
net; unified macron-free / canonical display + citation-form canonicalization
(facere→facio) at import; language-keyed philology eras; Latin generation
function-words/scaffold (validator unchanged — lemmatizer-driven matching handles
Latin inflection); `scripts/import_latin_vocab.py` + parsers; frontend `"la"`
enablement; language-aware cron loop. **Seeded in prod:** 1,585 LLPSI
assumed-known + 2,518 Roma Aeterna learn-frontier + Eutropius Book I (20 pages);
cron `POLYGLOT_LANGUAGES=el la`.

**[DONE]** seed data · Eutropius reading text · deploy + LatinCy model install ·
cron lane enabled.

**Remaining follow-ups:**
- **[DONE 2026-05-25]** **LatinCy quality-gate prompt**: `lemma_quality.py`'s
  gate prompt is now language-aware (`_LANG_PITFALLS` / `_LANG_CITATION_HINT`,
  rendered by the extracted `_build_prompt`). The Latin block warns about the
  sentence-initial→PROPN failure mode, homograph flips (malum/malus, bellum/
  bellus, populus/populus), unreduced inflections (`miliario`↛`miliarium`), and
  fused `-ne`/`-ve` enclitics; the Latin citation hint enforces the no-macron /
  u-i / 1sg-nominative display policy (the generic "with diacritics" hint is
  Greek-only now). Mirrors the Greek homograph rule + Arabic feminine-ة CAMeL
  lesson: name the upstream lemmatizer's known systematic errors explicitly so
  the gate hunts for them. Each language's block is isolated so Greek pitfalls
  don't leak into Latin prompts (regression: `test_build_prompt_is_language_specific`).
- **More reading texts**: only Eutropius Book I imported; add Books II–X, Nepos,
  or Einhard via `/api/texts/paste` (split into pages).
- **DCC core** (optional): never needed — LLPSI chapter order is the frequency
  backbone. Drop `data/vocab/dcc_core.tsv` + re-run the importer if wanted.
- **Leech sweeps** (`check_and_manage_leeches`, `check_leech_reintroductions`)
  are global across languages — per-lemma-correct but could be language-scoped.
  Low priority.

## 🧹 [TRIAGE 2026-05-22] Five abandoned 2026-03-21 experiment PRs — evaluated against production data

Closed PRs #21–#25 (2-month-old, all conflicting). Re-evaluated each *idea* (not the stale code) against dev logs + production data (44,741 reviews). Full analysis: `research/analysis-2026-05-22-stale-pr-ideas.md`. **Do not re-propose the four below without new data.**

- **[REJECTED] Dynamic session sizing by accuracy** (was PR #22, idea below at "Dynamic Session Limit"). Sessions already run median 11 / mean 12 sentence-reviews (above the proposed base of 10); session size does not rise with accuracy buckets; low-accuracy sessions are *smaller* (users self-quit). No ceiling to relieve.
- **[REJECTED] Response-time / fluency signal** (was PR #23). Hypothesis falsified: next-review lapse after a slow-correct review is 10.5% vs 12.7% after fast-correct — slowness predicts *fewer* lapses, not more. Only 17% coverage; the captured `response_ms` is whole-sentence reading time (25s median), not word recognition.
- **[REJECTED] Familiar-encountered comprehensibility gate** (was PR #25, idea below at "Comprehensibility Gate — Count Familiar Encountered Words as Known"). Only 2 encountered words have ≥8 encounters; exactly 1 sentence would become newly eligible. The 2026-03-18 collateral auto-introduction flow already drains encountered words (only 85 exist). Dead-zone is empty.
- **[DEFERRED] Rasm confusable-pair session exclusion** (was PR #24, idea below at "Session Builder: No Same-Rasm Pairs in Same Session"). Directionally real (same-rasm co-occurrence: 2.71% was_confused vs 1.75% clean) but tiny absolute magnitude (9 confused reviews); literature on interleaving-vs-spacing for confusables is mixed. Not worth session-builder complexity now.
- **[DONE — disabled] Mnemonic regeneration → memory-hook quality gate → mnemonics turned OFF** (was PR #21). The regeneration mechanism was ~80% already shipped. Reframed to *quality-gating* since hooks are mostly low-quality. Two calibration rounds (49 + 45 user ratings, `research/eval-hook-quality-2026-05-22.html` + `-batch2-`) showed **the quality boundary is not reliably learnable**: held-out Cohen's κ = **−0.12** (worse than chance), and the criteria *inverted* across sessions (tight-sound/picturable hooks rejected in batch 2, loose/abstract ones accepted). Likely real driver is subjective "semantic inevitability," which an LLM critic can't judge consistently and which isn't stable across the user's own sessions. **Decision (2026-05-22): turn mnemonics OFF.** Generation gated behind `ALIF_MEMORY_HOOKS_ENABLED` (default off) at the `memory_hooks.py` chokepoint; display gated behind `frontend/lib/feature-flags.ts` `SHOW_MNEMONIC_HOOKS=false`. Cognates/collocations/usage/fun-fact are unaffected (not part of the quality problem). Reversible via the two flags. Full write-up: `research/analysis-2026-05-22-stale-pr-ideas.md`.

---

## ✅ [DONE 2026-05-25] Polyglot: offline queue for the reader (page-advance cache + auto-send)

The reader's page-advance (`apply_page_review`) was a **direct fetch** — offline it failed and the page outcome was lost. Now cached offline and auto-sent on reconnect, like Alif. (Alif's `frontend/lib/sync-queue.ts` is hardwired to Alif's `BASE_URL` + endpoints, so it couldn't carry polyglot actions.)

Built (branch `sh/polyglot-offline-reader`, works for Greek and Latin):
- New `frontend/lib/polyglot-sync-queue.ts` (mirror of sync-queue.ts) posting to `POLYGLOT_BASE_URL`; single entry type `page_review`. Flushes on mount + on reconnect (`syncEvents.on("online")` from `net-status.ts`). Retry with attempts, drop after 8 — but a pure network error does NOT burn an attempt (offline a while ≠ data loss); only real HTTP 4xx/5xx responses count.
- Page-advance is now **self-contained + idempotent**: request `{unknown_lemma_ids, encountered_lemma_ids, client_review_id, session_id}`. `apply_page_review` applies the reds/yellows itself (online they already ran via per-tap `markWord` → the word is already `acquiring`, so the failure isn't re-recorded; offline it enrols/sets them on replay). New `page_review_log` table keyed on `client_review_id` makes the whole page idempotent (one advance = many per-word ReviewLog rows, which can't dedup the page alone). Legacy `tapped_lemma_ids` (red+yellow union) still accepted for exclusion-only.
- Per-tap `markWord` stays a best-effort online call for live gloss only (not queued; the page submit is authoritative).

Not built (separate, larger piece): **offline page *rendering***. `getPage` needs server-side tokenization, so advancing into an unprocessed page is still online-only; offline, the outcome is queued and the reader stays on the current page until reconnect. If we want true offline reading, prefetch + cache `PageView` for the next N pages in AsyncStorage.

---

## 🟢 [PARTIAL 2026-05-25] Polyglot: actively surface un-confirmed assumed-known words for verification

The scaffold-confirmation engine (PR #138) *records* a green collateral exposure of an assumed-known word as verification evidence (`confirmed_at`, `clean_exposures`, no FSRS card). Confirmation was initially **passive** — only when an assumed word incidentally landed in a shown sentence. With ~1,477 unconfirmed words that's slow. Follow-up (user picked "active, but secondary"): bias generation toward never-confirmed assumed words so verifying the pool becomes steady background progress — **never** at the cost of genuine retrieval targets.

- **[DONE 2026-05-25, branch `sh/polyglot-confirm-surfacing-flow`]** Up-weight `confirmed_at IS NULL` + no-card known lemmas in `material_generator._sample_known_words_weighted` (`unconfirmed_scaffold` flag threaded through `_snapshot_known_pool`, `UNCONFIRMED_SCAFFOLD_BOOST=2.5`).
- **[DONE 2026-05-25, same branch]** Weekly conversion time-series — `stats._flow_history` (8 weeks of confirmed / gaps_discovered / graduated / new_lemmas), response key `flow_history`. Ports Alif's `acquisition_pipeline.flow_history` shape.
- **[DONE 2026-06-01, branch `sh/polyglot-confirm-diversity`]** Picker pressure toward unconfirmed assumed words — landed as a port of Alif's selection-side diversity (`SESSION_SCAFFOLD_DECAY=0.5` + `_scaffold_freshness`, which favours `times_seen=0` = still-unconfirmed scaffold) PLUS a dedicated reserved confirmation sweep (`CONFIRMATION_SESSION_SHARE=0.30`, greedy set-cover over reviewable sentences maximising distinct never-confirmed coverage). Verified on a prod-DB copy: one 15-card session now confirms 30 (Greek) / 35 (Latin) distinct known words.
- **[DONE 2026-06-01, branch `sh/polyglot-coverage-generation`]** Two generation-side levers — the *planter* to the confirmation sweep's *harvester*. The data motivating both: the sweep only set-covers *existing* reviewable sentences, so on a prod-DB copy it asymptotes at ~59% (Greek, 651/1110) / ~24% (Latin, 177/730) of never-confirmed assumed-knowns — the remaining 459 Greek / 553 Latin sit in **zero** reviewable sentences (mostly LLPSI/Roma-Aeterna seed vocab never given a generated sentence), structurally unreachable by selection alone.
  - **Lever A — post-gen over-exposure preference.** `material_generator._scaffold_overexposure_count` + `DIVERSITY_SENTENCE_THRESHOLD=10` (mirrors Alif's `_check_scaffold_diversity`). The batch generator over-requests candidates then trims to `sentences_per_target`; Lever A re-ranks the surplus quality-passed buffer so fresher-scaffold candidates win the trim. **Divergence from Alif:** no 7× reject+retry loop — polyglot generation is batched, not a single-target retry loop, so this is a *ranking*, never a *rejection*, and cannot reduce yield. Grows corpus diversity so the sweep reaches the long tail.
  - **Lever B — coverage generation (the higher-value lever, per the zero-coverage numbers).** New bounded warm-cache phase: `_coverage_lemmas_missing_material` selects never-confirmed assumed-known content lemmas (`known`, no card, `confirmed_at IS NULL`) in fewer than `COVERAGE_TARGET=1` reviewable sentences and feeds them to the existing verified `batch_generate_material` as targets, so a later reading session confirms them as collateral. Disjoint from retrieval gaps by knowledge state; runs *after* them with its own `COVERAGE_MAX_LEMMAS` budget (cron 24/run), so it never starves retrieval. Env: `POLYGLOT_COVERAGE_GEN` (default on), `POLYGLOT_COVERAGE_MAX_LEMMAS`, `POLYGLOT_COVERAGE_TARGET`, `POLYGLOT_COVERAGE_SENTENCES_PER_TARGET`; CLI `--coverage-max-lemmas`; wired into `deploy/polyglot-update-material.sh`.
- **[OPEN, stretch]** A dedicated "verify what you know" sweep mode that batches high-frequency unconfirmed words into quick comprehensible sentences.
- **[OPEN, view]** Surface `flow_history` + the `exposure_confirmed`/`assumed_unconfirmed` tiers in the redesigned stats page (mockups pending vote).

See experiment-log 2026-05-25 and `polyglot/CLAUDE.md` Hard Invariant 6.

---

## 🔵 [OPEN 2026-05-21] Polyglot enrichment verifier may be over-flagging

Round 1 of the Haiku fact-verify pass (PR #114) flagged 5 of 10 backfilled lemmas as `done_flagged`. At least one of those (#685 όλος) is a verifier disagreement about scholarly form (`*solh₂-` vs `*solw-`), where both PIE reconstructions are defensible in current literature. The verifier prompt deliberately errs on "flag when unsure" but may be too aggressive — a 50% flag rate undermines the signal.

**Possible refinements**:
- Lower the verifier's sensitivity: only flag when the claim is *demonstrably* wrong (a published source contradicts it), not when it's "one of two competing reconstructions."
- Add a third verdict `ok_minor_quibble` that still writes `done` but logs the note for offline review.
- Calibrate by sampling 20 more lemmas and checking which `done_flagged` cases are real errors vs. verifier pedantry. Tune the prompt accordingly.

Don't tune until we have ~30+ enrichments to look at — sample of 10 isn't enough signal.

---

## 🔵 [OPEN 2026-05-21] Surface `done_flagged` enrichment as "verified with concerns" in UI

When a lemma has `enrichment_status='done_flagged'`, the enrichment is still written + a `_verifier_note` field is attached. The lemma detail page currently renders it identically to `done` — the learner has no signal that the verifier raised an issue. Worth surfacing:
- Small subtle indicator in the detail page header ("verified with concerns" with a tooltip showing the verifier's note).
- The lookup card could just render normally (the user is in flow; don't disrupt).
- Optional: a "report this enrichment" link that flips status to `failed` + queues re-enrichment.

---

## 🔵 [OPEN 2026-05-24] Polyglot: deterministic transliteration-distance filter for cognate auto-marks

Both `cognate_detector.py` and the `recheck_low_cognates.py` second pass share a blind spot: the LLM accepts "etymological link + matching English gloss" as recognizable without ever checking that the Greek surface form actually transliterates to anything like the proposed cognate. πτυχίο "survived" the recheck despite sharing zero letters with English "diploma" (different roots, same gloss). The fix is a **deterministic Greek-surface → Latin transliteration distance check** (e.g. romanize the Greek bare form, edit-distance against the candidate cognate, gate on a threshold) applied as a hard filter *before* any cognate auto-mark — no LLM judgment in the loop. This is required before lowering `UserProfile.cognate_auto_mark_threshold` below `medium`: the `low` tier was form-blind and ~50–72% false-positive (2026-05-21 SUBTLEX-GR audit; all 258 low survivors dropped). Until this filter exists, run bulk cognate imports at `--threshold medium` only. See `polyglot/CLAUDE.md` Hard Invariant 6 and `polyglot/scripts/recheck_low_cognates.py` docstring.

---

## ✅ [DONE 2026-05-21] Polyglot: pre-existing lemma_quality test failure

`tests/test_lemma_quality.py::test_partial_batch_failure_leaves_page_unverified` was failing on main. Real bug — transient Haiku failures during force re-runs (or after an earlier successful pass) carried a stale `mappings_verified_at` stamp even when one of the new batches returned `None`, violating the "verification failure ≠ success" invariant.

Root cause: the function correctly avoided **adding** a stamp on partial failure, but never **cleared** the pre-existing page stamp or per-word `verified_at` from a prior successful pass. The per-word filter already honored `force=True` ("re-verify from scratch"); the page-level stamp didn't.

Fix: in `verify_page_mappings`, after building the `interesting` list and before the batch loop, NULL `page.mappings_verified_at` + `page.quality_gate_failures` and reset `verified_at` on every word about to be re-batched. Natural fall-through then leaves the page in the correct unverified state on any partial failure. Per-word `verified_at` for tokens whose new batch succeeds gets re-stamped by `_apply_verdict`. All 13 lemma_quality tests + 26 reading_intake/sentence_harvest tests still green.

---

## 🔵 [OPEN 2026-05-21] Polyglot: import more Greek vocab (16 of 33 unknown words today are genuinely missing)

Today's sentence-gen audit: 33 unique words flagged "unknown" by validator. 16 are genuinely missing from the 5,690-lemma DB. Examples: `αιγών` (goats gen.pl), `αυτοφυή` (autochthonous), `δασικός` (forest, adj.), `εκτροφής` (breeding gen.), `ευρήματα` (findings), `οριοθετηθεί` (be delimited), `ποικιλία` (variety), `σιτηρό` (cereal), `συστηματικής` (systematic gen.).

These are common-enough modern Greek words but missing from the SUBTLEX-GR top-3k frequency-list import. Worth a follow-up frequency-list pass that pulls 3k–10k SUBTLEX-GR entries (or supplements with the National Corpus of Greek / Hellenic National Corpus) to fill the gap. With more vocab in the DB, the picker's lookup succeeds for more inflected forms and the validator's rejection rate drops further.

---

## 🟢 [IN PROGRESS 2026-05-19] Polyglot — multilingual sister app (Modern Greek primary)

Built `polyglot/` as a sibling backend for Modern Greek, Ancient Greek, and Latin. Decision: fork-then-converge — Phase 1 is a separate Python package + venv + SQLite + systemd; Phase 2 (after ~6 weeks of dogfooding) extracts a shared `alif_core/` package. Frontend (`frontend/`) talks to both backends; user picks language via a Globe tab. Detailed gate-by-gate Alif comparison + project rules in `polyglot/CLAUDE.md`.

Reading-as-mapping is the MVP UX:
- Lazy PDF intake (textbook imports in <1s, pages tokenized on first view)
- Tap unknowns → bottom-bar lookup + auto-gloss when marked unknown
- Next-page presumes everything you didn't tap is known (huge intermediate-learner accelerator)
- Modern↔Ancient Greek cognate auto-linking + 'encountered'-state propagation (semantic-drift-aware)
- External L1 cognate detection (en/no/de/fr/it/es) — opt-in

Lemmatization stack:
- simplemma (pure-Python dictionary lemmatizer; works for Modern Greek + Latin + others)
- LLM quality gate (`lemma_quality.py`) using Claude CLI with `--json-schema` constrained decoding; catches homograph/POS/proper-noun errors simplemma misses. On test page 11 of the Greek high-school history textbook, made 10 real corrections (χώρα/χωρώ, Τίγρης proper-noun, etc).
- Tiny gloss generation on-demand (when user marks unknown) — Haiku, ~2s.

Test coverage: 30 backend tests + tsc-clean frontend. Imported `22-0021-02_Istoria-tou-Archaiou-Kosmou_A-Lykeiou_Vivlio-Mathiti.pdf` (298 pages, modern Greek prose on ancient world history) as the seed text.

What's deferred (next-session work): sentence review, FSRS scheduling, acquisition Leitner 3-box, session builder, audio, Ancient Greek lemmatization (OdyCy wire-up), Latin POS via LatinCy. Schema fields are in place on UserLemmaKnowledge so the port is mechanical when the time comes. See `polyglot/NEXT_SESSION.md`.

## ✅ [DONE 2026-05-18] Treat textbook imports as high-priority new words

Textbook OCR is now vocabulary intake, not proof of knowledge. Future scanned
unknown words create/refresh `encountered` ULKs with `source="textbook_scan"`,
no FSRS card, and no card-only `textbook_preserve_intro` exception. The old
`preserve_known` and `start_acquiring` request flags remain accepted but are
ignored for learning state.

The high priority remains: `source="textbook_scan"` is preserved when a scanned
word later enters acquisition, and `word_selector.py` ranks textbook-scan
learning provenance at +220, ahead of ordinary book/story/collateral/wiki
candidates. Legacy known textbook-preserve rows are not retroactively demoted;
their old card-only intro path is simply no longer rendered.

Follow-up: tomorrow, after any new scan, verify scan-created ULKs are
`encountered` with no FSRS card; within a few days, confirm textbook candidates
still rise quickly when the recovery intro budget opens, without bypassing the
daily/new-word caps.

## ✅ [DONE 2026-05-17] Acquisition working-memory gate + recovery intro budget

**2026-07-09 correction:** The historical 4/8 recovery budgets below were later raised to
8/30, and the evidence gate now counts primary reading cards/accuracy rather than sentence
child rows plus blended word reviews. Box-1 debt now also includes due previously-seen words,
and mature auto-skip protects every due/acquisition obligation. See the top return-recovery
entry for current behavior.

Prod audit of 2026-05-13 through 2026-05-17 found the aggressive intake was creating acquisition debt faster than sentence practice could consolidate it: 107 net-new acquisitions in five days, 125 current acquiring words, 70 due in Box 2, and 74 of 83 current Box-2 words whose first acquisition review happened less than two minutes after an intro card.

Fix (branch `sh/acquisition-working-memory-gate`):

1. Correct reviews inside `FAST_GRAD_INTRO_GAP` after an intro card no longer promote Box 1 -> 2 or trigger Tier 0/1/2 graduation. They still count as exposure and return after `FAST_INTRO_RETRY_INTERVAL=30m`.
2. Recovery-mode intro budget activates only under real acquisition overload (Box-1 unreviewed >=5 or due Box-2 >=30). Normal days still allow the full `DAILY_INTRO_CAP=30`; overload days require practice first: 0 before 40 sentence reviews, 4 after 40 with acceptable accuracy, 8 after 100 with >=85% accuracy.
3. `introduce_word()` and `_auto_introduce_words()` now preserve deferred starts as `encountered` and do not count them as introduced.
4. Frontend auto-skip excludes acquiring primary words and `acquisition_repeat` cards, so repetition sentences remain visible.
5. Added `reset_fast_intro_promotions_2026_05_17.py` to reset current Box-2/3 fast-promotion debt to Box 1 due now while preserving review history.

Follow-up checks are documented in `research/analysis-2026-05-17-acquisition-recovery.md`: tomorrow, the reset script dry-run should stay at 0/low and Box-2 due should fall; by 2026-05-20, due Box 2 should be below 30 or clearly declining. If not, temporarily lower `RECOVERY_FULL_INTRO_BUDGET` from 8 to 6.

## 🔵 [OPEN 2026-05-17] Admin queue for incompatible same-bare correction proposals

The 2026-05-17 sense-aware correction resolver stops wrong same-bare homographs from being accepted as repairs, but it still leaves a manual curation problem. Rejected verifier proposals like `شال / shawl / noun`, `أمين / trustworthy / adj`, `موضوع / topic / noun`, and `تأمل / contemplation / noun` are exactly the missing vocabulary/homograph entries the system needs.

Build a small admin queue over `mapping_corrections_*.jsonl`, `mapping_reverify_failures_*.jsonl`, and `rescue_proposals_*.jsonl`:

- Group by `(normalized Arabic bare, proposed_pos, proposed_gloss)` with example sentences and current wrong lemma(s).
- Mark whether a compatible lemma already exists, whether an FCE row exists, and whether the proposal is a proper name/function word.
- Approve creates/imports through the existing `import_scaffold_lemmas.py` / `run_quality_gates()` path, with `ALLOW_HOMOGRAPH` required when the bare already exists with a different sense.
- Dismiss noisy POS-only overcalls so they stop polluting the queue.

This closes the loop after fail-closed verification: bad mappings stay hidden immediately, and repeated legitimate proposals become curated vocabulary instead of recurring flags.

---

## ✅ [DONE 2026-05-15] Enforce daily intro cap at chokepoint + smooth intro cards per session

Prod prompted by 39 intros today (28 textbook_scan, 9 collateral, 1 book, 1 quran, 0 via the official `_auto_introduce_words` path that was the only one checking the daily cap). User saw sessions with 10-15 intro cards crammed up front because `_build_intro_cards` had an explicit "first-time intro cards are not capped" and `_ensure_session_words_have_intro_state` mass-promoted every encountered word at session-build time.

Fix (branch `sh/intro-cap-enforcement`):
1. **`start_acquisition()` enforces the daily cap (30/day) for every caller** — single chokepoint. When the cap is hit, new ULKs are created as `encountered` instead of `acquiring`; existing encountered ULKs are left in place. `leech_reintro` bypasses (re-introduction of known words).
2. **`_ensure_session_words_have_intro_state` per-session cap of 6** — cold promoter no longer mass-promotes; the rest stay encountered until a later session.
3. **`_build_intro_cards` first-time cards capped at `INTRO_NEW_CARDS_PER_SESSION = 6`** — was uncapped.
4. **Comprehensibility gate counts `is_fresh_today` acquiring words as unknown** — sentence-with-many-just-promoted-words no longer reads as comprehensible. Fresh = acquired today, `times_correct == 0`; clears as soon as you successfully review the word once.
5. Callers in `sentence_review_service.py` check `ulk.knowledge_state == "acquiring"` after `start_acquisition` to decide between acquisition-review and a quiet `total_encounters` bump.

Reasoning notes saved in 2026-05-15 entry in `research/experiment-log.md`. Tests added: `test_acquisition.py::test_daily_cap_*` (5 cases).

---

## 🔵 [OPEN 2026-05-15] Vocalized-aware lemma identity for homographs

Today's Form I/II/IV cleanup on the ن.ز.ل root revealed a structural limitation: `lemma_ar_bare` is the unit of dedup (via `build_lemma_lookup`), but `strip_diacritics` removes shadda — so نَزَلَ (Form I "descend") and نَزَّلَ (Form II "send down") collapse to the same bare key `نزل`. They are dictionary-distinct verbs with different patterns, glosses, and conjugation paradigms, but the lookup can't tell them apart.

The codebase already supports homograph **storage** — `Lemma.lemma_ar_bare` has no DB unique constraint, and `import_scaffold_lemmas.py:ALLOW_HOMOGRAPH` opts curated entries into homograph creation (e.g. قَدِمَ "come" vs قَدَمَ "precede"). The 2026-05-15 fix demonstrated this for ن.ز.ل by creating three distinct canonicals (#3569, #3587, #3588) all with bare `نزل`. But `build_lemma_lookup` still hits a collision on the bare key: lookup returns whichever homograph was inserted first, so future imports of the other forms misroute.

Three possible fixes:

1. **Vocalized-aware lookup.** Keep `lemma_ar_bare` as today but also index by `lemma_ar` (stripped of case-ending nunation only). The lookup takes both the input surface and a CAMeL-resolved lex, and picks the homograph whose vocalized form best matches the lex (e.g. shadda presence/absence). Probably 1-2 day change touching `build_lemma_lookup`, `lookup_lemma`, `find_best_db_match`, and a few callers.
2. **Separate canonical-key column.** Add `Lemma.canonical_key` that includes whatever distinguishes homographs (e.g. shadda-preserved consonantal skeleton, or verb form roman numeral). Migrations + scripts + lookup rewiring. Bigger, more invasive.
3. **Accept manual homograph entries only.** Don't auto-import a second form-of-same-bare; require an `ALLOW_HOMOGRAPH` curation pass. Limits the bug but doesn't fix the import paths.

Reference: see `research/experiment-log.md:2026-05-15: Quran + OCR lemma canonicalization rewrite` for the incident, `backend/scripts/split_nzl_homograph_2026_05_15.py` for the current workaround.

---

## ✅ [DONE 2026-05-15, PR #79] Rare-word warning + per-word suspend on intro cards

Shipped: `ReintroCardOut.frequency_rank` + `frequency_source_count` populated by FCE join on canonical lemma_id. Yellow banner on intro card fires when rank > 3000, rank null, OR `broad_source_count ≤ 1`. "Suspend this word" button posts to `POST /api/words/{id}/suspend` with `{frequency_rank, source: "rare_word_banner"}` payload — endpoint resolves canonical, cascades `is_active=False` to sentences targeting variant or canonical, logs the rank into the `word_suspended` interaction event for analytics.

Discovered during the work: `broad_source_count` is noisy as a primary signal (even `#408 قال` "to say" at rank 1 has count=1). The banner copy renders the rank ("rank #1449, only in 1 frequency list") so the user can tell at a glance whether a warning is real. Threshold may want tuning after live use.

Follow-ups left open:
- The OCR/text mapper has systemic noun-vs-verb homograph confusion on classical-style sentences (#1379 noun → verb fix exposed that #34564 alone had مَلَك→angel-instead-of-king, نَفْس→self-instead-of-breath, قِطْعَة→piece-instead-of-verb-to-cut). Worth a broader audit. See 2026-05-15 experiment-log entry.
- Bucket-1 audit confirmed 18 of 19 active-learning lemmas missing from FCE are correctly excluded function words. `أَم` (disjunctive "or") was the one straggler — added to `FUNCTION_WORDS` in the PR squash.

---

## 🔵 [OPEN 2026-05-15] Scaffold canonicals for orphaned NULL surfaces post-chimera

After the 2026-05-15 chimera deletion (#2307 آنِسَة/نسي + #3450 + #3452, see scripts-catalog.md), 8 sentence_words have `lemma_id=NULL` because no clean canonical exists yet for the underlying words. They concentrate in:

- نَسِيَ "to forget" (Form I) and أَنْسَى "to make forget" (Form IV) — surfaces like نَسِيَ, نَسِيتُ, تَنْسَى, يَنْسَى, أَنْسَ, أَنْسَاهُ
- اِشْتَدَّ "to intensify" (Form VIII) — surfaces like اِشْتَدَّتْ, يَشْتَدُّ
- إِنْسَان "human" — root ا.ن.س, surfaces like إِنْسَانًا
- نِسْيَان "forgetfulness" (masdar of نَسِيَ) — surfaces like النِّسْيَانُ
- أُنْس "intimacy, familiarity" — root ا.ن.س, surface like أُنْسِي
- أَنَاس "people" — surface أناس

Quick fix: add to `import_scaffold_lemmas.py:SCAFFOLD_WORDS` with diacritized form + gloss + pos for each. After re-import, `fix_null_lemma_ids.remap_unmapped_sentence_words` picks them up automatically and the affected sentences activate. ~6-8 entries, 5-min curation.

---

## 🔵 [OPEN 2026-05-15] Review queue for letter-drift vocalization proposals

The 2026-05-15 tashkeel backfill rejected 15 of 132 unvocalized lemmas because the LLM's proposal changed letters, not just diacritics — e.g. restoring a dropped hamza or fixing an OCR-corrupted character. The strict letter-match validator in `lemma_vocalization.validate_proposal()` blocks these by design (silent letter mutation via the vocalization path would let unverified corrections slip in), so those lemmas remain unvocalized.

Several rejections are probably *correct* fixes the LLM is volunteering. Worth building a small review queue:

- Re-run the vocalize prompt for the 15 (and any future rejections), but capture the proposal even when letters changed.
- Surface them in More → Admin alongside the existing flag-resolution UI: original lemma_ar, proposed vocalized form, diff highlights, gloss, root.
- Tap-to-approve commits both the letter change and the vocalization in one step; tap-to-reject marks the lemma as "vocalization deferred — needs better data" so it stops being retried.

Not urgent — the runtime gate already covers new imports, and the residuals only show up as ugly translits on whichever cards happen to surface them.

---

## 🟢 [DONE 2026-05-13] Two regression fixes — variant re-admit + select_next_words perf

After the frequency-core supply fix went out, sessions still showed 0 intros and `build_session` was taking 3.4s. Two underlying bugs:

1. **Variant re-admit** (`word_selector.py`): the suspended-lemma re-admit paths (book_pages / story_lemmas / ULK.source=textbook_scan) had no `canonical_lemma_id.is_(None)` filter, so the 36 deliberately-suspended variants (per `suspend_variant_ulks.py` 2026-05-06) kept being surfaced as intro candidates. `introduce_word` canonical-resolved each to an already-known root, returned `already_known=True`, and `_auto_introduce_words` skipped them — final return: empty list.

2. **`select_next_words` perf**: `root_family` and `pattern_examples` were computed eagerly for ~750 scored candidates per call. `scored[:count]` then threw away 95% of that work. Deferred to a post-sort pass that only fills the ~15 returned candidates. Cumulative time: 2.3s → 0.38s. End-to-end `build_session`: 3.4s → 1.5s.

Pattern lesson: both were "compute eagerly, throw away most of it" — common when prototype code accretes features without revisiting the per-candidate cost. Watch for it in other scoring paths.

Open: `build_session` is still at 1.5s. The remaining time is in FSRS card parsing + comprehensibility gate + sentence scoring against due lemmas. Not a regression, but worth profiling on a quieter day.

---

## 🟢 [DONE 2026-05-13] Version the cron wrapper, restore intro supply chain

After diagnosing a 5x drop in intro rate (from ~30/day to ~5/day) that all three intro gates failed to explain, traced the cause to the 2026-05-12 cost-consolidation push:

1. Step C of `update_material.py` (which calls `frequency_core_intake`) became opt-in via `ALIF_RUN_CRON_PREGENERATION` (default off). Cron stopped invoking it.
2. Even when invoked, intake's `DEFAULT_MAX_RANK = 1000` caps it at top-1000 FCE rows — but every top-1000 row is already mapped. The unmapped rows at ranks 1000–2000 (19) and 2000–3000 (777) were invisible to the script.

Fixed by versioning the cron wrapper under `deploy/alif-update-material.sh` (previously only existed on the server, never in the repo) with `ALIF_RUN_CRON_PREGENERATION=1`, `ALIF_RUN_CRON_LEMMA_ENRICHMENT=1`, `ALIF_FREQ_CORE_INTAKE_MAX_RANK=3000`, `ALIF_FREQ_CORE_INTAKE_LIMIT=10` baked in via `:-` defaults so per-instance overrides still work. Manual one-shot run after the fix created 5 new high-frequency lemmas (`أكّد`, `سبيل`, `صحيفة`, `إطار`, `تطوير`). Pool now has 21 candidates ready.

Followups:
- Watch the supply-chain audit in 5–7 days; verify frequency_core intros are back to 25–30/day.
- The drought was diagnosed by reading the gates one layer at a time. The diagnostic scripts at `/tmp/claude/diagnose_intro_*.py` should probably be promoted to `backend/scripts/` if intro-rate questions come up again.
- Server-side config drift is a known failure mode now. Anything else living at `/opt/*` outside the repo should get the same `deploy/` treatment.

---

## 🟢 [DONE 2026-05-13] Lazy mapping rescue in warm_sentence_cache

Added `app/services/mapping_rescue.py` and hooked it into `warm_sentence_cache`. For each gap lemma the warm cache identifies, the rescue pulls stale-verified sentences for that lemma, batch-verifies them, applies confident corrections, and re-stamps survivors with a fresh `mappings_verified_at`. Verifier-proposed lemmas that don't exist in the DB are gated by the frequency-core list: if the bare form has an FCE row whose `lemma_id` is NULL we create the lemma and route through `run_quality_gates`; otherwise we log the proposal and leave the sentence stale.

Trust-recovery move. Drains the 184 stranded sentences surfaced by `research/post-consolidation-audit-2026-05-13.md` lazily as demand picks them up, rather than running a global re-verification sweep. The frequency-core gate prevents the verifier from hallucinating lemmas into existence.

Open follow-ups:
- Periodic surface-form roll-up of `rescue_proposals_*.jsonl` to see which "needs-lemma" forms keep coming back, feeding a manual import queue.
- A dedicated `/api/admin/rescue-stats` endpoint or activity-log surface so the daily rescue counts are visible in the More tab instead of only in logs.

---

## 🟢 [DONE 2026-05-11] Persist sentence quality and demote unreviewed legacy LLM rows

Triggered by sentence `44415`, where due-word coverage selected a short but
nonsensical LLM sentence. The fix is not a length gate: short sentences remain
eligible when they make sense. Instead, sentence quality review metadata is
stored on `sentences`, failed reviewed LLM rows are skipped by the selector,
approved rows score normally, and legacy unreviewed LLM rows stay available only
as fallback material. The existing review script can now audit specific IDs or
active unreviewed LLM rows and persist retire/approval decisions.

Follow-up idea: add a small recurring health job that samples recently selected
unreviewed LLM rows, runs the quality gate, and reports the fail rate before
they reach review sessions.

---

## 🟡 Learning projections + confusor telemetry layer (2026-05-10)

Prod analysis of 39,773 word reviews / 7,945 sentence reviews found stable
predictors of lemma difficulty:

- verbs and 11+ form lemmas are much harder than nouns/adjectives;
- root-family support helps after 2-3 known siblings but can also create
  same-root confusions;
- target-heavy exposure is a warning signal, while collateral exposure is a
  strong-projection signal;
- scaffold unknown count is a large sentence-level predictor: understood drops
  from 70.8% at 0 unknown scaffolds to 56.7% at 1 and 47.5% at 2;
- repeated partials on the same sentence need a sentence-level leech path.

Idea: build an offline/read-side `learning_projections` layer for canonical
lemmas. It should cache projection band, risk reasons, likely confusors, expected
remaining acquisition burden, and recommended treatment. Use it first in intro,
word detail, confusion help, and wrap-up. Only later let it lightly influence
`sentence_selector`.

Instrumentation needed before serious pair modeling:

- log pair-level confusion candidates ("confused with X");
- persist `selection_info`, unknown scaffold count, due/collateral lemma IDs,
  sentence source/length, card index, and intro-to-first-review delay;
- populate observed `difficulty_score` or a separate sentence difficulty table.

2026-05-10 follow-up: first reversible implementation should keep scheduling
unchanged, but make confusion help form-aware and log the candidate lemma IDs it
actually showed. `variant_stats_json` should also store a matched form key when
the exposed surface maps to `forms_json`, so later analysis can tell "bad lemma"
from "bad conjugation/form."

Observed prod sanity check: for `أَعَدَّ` / "to prepare", the form-aware list
now surfaces `أَعْطَى` / "to give" as a short-verb neighbor, while `ضَاعَف` /
"to double" is eligible but ranks lower, and `عادَةً` / "usually" still does
not qualify by spelling/form rules. Add an explicit "I thought it was..."
confusor picker/search if candidate telemetry shows the real confusor is often
outside the automatic list.

2026-05-11 post-deploy audit: mini-story attribution is healthy at the card and
story level. Seven grouped/passage reviews since 2026-05-10 credited 117/117
expected schedulable lemmas, and the latest four-sentence mini-story credited
13/13. Remaining limitation: `ReviewLog.sentence_id` records the primary
sentence only, so per-word analysis can recover `source="passage"` and
`story_id`, but not the exact intra-passage sentence that exposed a word. Add a
review context table or `source_sentence_ids_json` before doing fine-grained
sentence-level causal attribution inside mini-stories. Also add a recurring
health check that grouped reviews have `words_reviewed == len(word_ratings)` and
no expected-vs-actual lemma-credit mismatches.

Report: `research/analysis-2026-05-10-lemma-learning-projections.md`.
Design spec: `research/spec-2026-05-10-learning-projection-interventions.md`.

2026-06-03 passage-efficacy re-run follow-up: the `parent_card_type` attribution
gap above is now partly closed — the offline sync-replay path was dropping
`parent_card_type` from `sentence_review` logs, so it was `null` everywhere
(fixed in #188). Open idea: **clip idle time from `response_ms` at submit.** 4 of
13 passage cards in the 26-day window were left open 30–105 min (phone-down,
not reading), so any mean-based time metric is polluted and the analysis had to
idle-filter (<20 min) + use medians. Record active-read time (pause on
background/blur, resume on focus) or cap `response_ms` client-side so time
metrics are trustworthy without filtering. Applies to all card types, not just
passages. Report: `research/analysis-2026-06-03-passage-efficacy.md`.
Post-deploy audit: `research/analysis-2026-05-11-post-deploy-learning-health.md`.

---

## 🟡 Hindawi reading-pack unlocker (2026-05-10)

Prod analysis of Hindawi `children.stories` shows a split:

- Imported sentence pool is close now: 6,427/6,465 corpus sentences have <=3
  unknown content items under the practical reading band (`known`, `learning`,
  `acquiring`, `lapsed`). Top 100 mapped missing lemmas cover 76.2% of mapped
  missing-token occurrences.
- Whole raw books are not close yet: even with CAMeL on selected short books,
  likely-easiest candidates such as `لَيْلَى وَالذِّئْبُ` are around 80-82%
  active coverage and still capped by 12-15% unmapped surfaces.

Idea: build a **Hindawi Reading Pack** flow before trying full-book unlock:
query verified corpus/book sentences (including inactive rows), re-run the
quality gate, rank by current coverage and missing-lemma gain, and output a
20-50 sentence reading pack plus a small pre-study list. For full books, add a
book-specific unmapped-surface audit/import queue. First pilot target:
`لَيْلَى وَالذِّئْبُ`. Analysis:
`research/analysis-2026-05-10-hindawi-reading-path.md`.

---

## 🟢 [DONE 2026-05-06] Broadened clitic-leftover audit — 95 lemmas, 88 cleaned

Started as the "my X" cohort (35 lemmas) but broadened to all proclitics
(و, ف, ب, ل, ك, ال, وال, بال, فال, كال, لل) + enclitics (ـي, ـنا, ـك, ـه, ـها,
ـهم, ـهن, ـكم, ـكن, ـني, ـهما) using a two-signal audit (bare-form clitic
shape + matching English gloss prefix). Found 95 hits in prod:

  * **75 ALREADY_LINKED** — `canonical_lemma_id` set by 2026-04-27 decomposition
    audit, but 31 had stale sentence_words / review_log / target_lemma_id /
    UserLemmaKnowledge refs left pointing at the compound. Cleaned via
    `merge_or_drop_orphan_ulk` (preserves FSRS state).
  * **13 ORPHAN_NO_CANON** — no canonical link. 7 mapped onto existing
    canonicals via alef/hamza variant lookup; 6 needed brand-new canonicals
    created (ميثاق, طغيان, تجارة, اتقى, افسد, مجال) with full LLM enrichment.
  * **7 FALSE_POS_VERB** — ل-initial verbs whose English "to V" infinitive
    looks like the "to/for X" proclitic gloss. Skipped.

Concrete trigger: lemma #2652 `مَجالِي` "my field" appeared as a New Word
intro card because the canonical `مجال` didn't exist; book/corpus sentences
containing the bare form `مجال` got mapped to the dirty compound.

Implementation: `backend/scripts/cleanup_clitic_leftovers.py` (idempotent,
three phases). Reuses the `merge_orphan_into_canonical` primitive from
`apply_step4c_link_survivors.py`. See `research/experiment-log.md`
2026-05-06 (latest) for the full writeup.

`ENCLITICS` in `sentence_validator.py` deliberately still excludes ـي —
adding it would over-strip defective verbs (قاضي) and relational adjectives
(عربي). The audit's two-signal design (clitic shape AND gloss prefix)
sidesteps that ambiguity, which is why it can safely surface ـي leftovers
without false positives. See section B below for the prior idea about
adding ـي to ENCLITICS — keeping it as a [REJECTED] alternative.

---

## 🟢 [DONE 2026-05-06, PR #72] Proper-name filter leak — `pos='noun_prop'` vs `word_category` drift

Filters keyed on `word_category=='proper_name'` but CAMeL-driven imports populated
only `pos='noun_prop'`. 101 lemmas leaked through (incl. Thameena, Al-Razi,
Bakr, Zakariya). Fixed with a `before_insert` listener on `Lemma` (forces
`word_category='proper_name'` when `pos='noun_prop'` and category is NULL) +
LLM-driven backfill of the 101 dirty rows (12 → proper_name, 82 → pos noun,
7 loanword junk left). See `research/experiment-log.md` 2026-05-06 (later).

---

## 🟡 Lookup gaps surfaced by sentence-eligibility-gate backfill (2026-05-05)

Three real morphology / vocabulary gaps surfaced when remapping the 8 active
sentences still unmapped after the eligibility-gate backfill. None are simple
fixes — each warrants its own focused task.

### A. Missing function-word lemma `ل`

`لِي` (li-ya = "to me") fails to remap. After the `ل` proclitic is stripped,
the residue `ي` is len<2 and gets discarded. There's also no Lemma row for the
preposition `ل` itself. **Fix idea**: ensure every `FUNCTION_WORD_GLOSSES` key
has a corresponding Lemma row (one-shot script). The `_strip_clitics` len<2
guard is correct (prevents matching very short stems against false roots) — the
real fix is that `ل` should match directly without clitic stripping when the
surface form IS just `لي`.

### B. [REJECTED 2026-05-06] Missing 1st-person possessive `ي` in ENCLITICS

`شُهْرَتِي` (my fame) cannot strip the trailing `ي`. ENCLITICS includes object
suffixes (هما, هم, ها, ه, نا, ني, ك, كم, كن) but the bare 1st-person possessive
`ي` is absent. Adding it would also help `كتابي`, `بيتي`, etc.

**Rejected** in favor of the gloss-driven audit (see top entry, 2026-05-06).
Adding ـي to ENCLITICS would over-strip defective verbs (قاضي, ماضي), relational
adjectives (عربي, طبي, رياضي), and dual oblique inflections at every import.
The pre-2026-04-24 cohort of ـي leftovers was cleaned as a one-time data fix
via `cleanup_clitic_leftovers.py` (95 lemma audit). The active import path
relies on CAMeL morphology + `resolve_existing_lemma()` for proper handling.

### C. [DONE 2026-05-05] Alef-maksura ↔ ya asymmetry in lookup

`إِلَيْهَا` strips `ها` to give `الي` (regular ya, U+064A) but the lemma is
keyed `الى` (alef-maksura, U+0649) at lemma_id 454. They're different keys.
**Fix**: `build_lemma_lookup` now has a Pass 1b that, for every lemma whose
normalized bare ends in ى, indexes a ي-final variant via `set_if_new`. The
separate pass (rather than inlining in Pass 1) ensures real ي-final lemmas
(e.g. موسيقي "musical") always claim their own key before a ى-final lemma's
ya-variant (موسيقى "music") can fill it. 28 ى-final lemmas, 25 add a new
ي-variant, 3 silently no-op on collision (موسيقى/علي/منى). Inverse direction
(ي → ى) not done — riskier and no observed gap that needed it.

### D. Plural / verbal-noun gaps

`الْمُشَاهَدَاتِ` (the observations) — only the verb `شاهَدَ` (to watch) is in
the DB; the verbal noun `مشاهدة` and its plural `مشاهدات` aren't imported.
Similar for `شهرة` (fame). **Fix idea**: a one-shot script that scans all
`is_active=1` book/corpus sentences for unmapped tokens, classifies them via
LLM, and either auto-imports common derived nouns or flags for manual review.
Distinct from proper-name auto-create because these are real vocabulary the
user should learn (with quality gates).

When any of A–D is fixed, the runtime gate auto-activates the affected
sentences on the next cron pass — no manual intervention needed.

---

## 🟢 [DONE 2026-05-04] Aggressive frequency-core acquisition experiment

User goal shifted from unlocking one specific book to unlocking general Arabic
reading as fast as possible. Implemented a gated 30-new-words/day experiment
with an honest frequency-core curriculum, main/slow review lanes, due-dense
multi-target generation, inactive-sentence salvage, and source-label provenance
cleanup. Follow-up after 48h: decide whether 30/day is sustainable, reduce to
20/day, or roll back intro constants while keeping the correctness fixes.

Docs:
- `research/aggressive-vocab-experiment-2026-05-04.md`
- `research/sentence-generation-prompt-experiments-2026-05-04.md`
- `docs/frequency-core-curriculum.md`
- `docs/aggressive-acquisition-runbook.md`

Deployed 2026-05-05 with CAMeL only (5,000 rows). Top 100 learned 86%,
top 500 78%, top 1,000 68%, top 5,000 29%.

Follow-ups discovered during deploy:
- **Kelly source unreachable**: Leeds corpus server (`corpus.leeds.ac.uk`)
  timed out from both local and server. Re-attempt later or mirror the file.
- **[DONE 2026-05-05] `learned_prefix_count` semantics differ between dry-run
  and API**: the builder's dry-run printout used "highest rank ever learned"
  while the API's `_compute_frequency_core_progress` correctly uses "continuous
  prefix from rank 1." Fixed by adding a `prefix_locked` flag in `print_summary`
  that freezes `prefix` on the first non-learned row, mirroring the API's
  `break`-on-gap. Verified against 6 synthetic states (incl. the 90/gap/50
  scenario from the original report) — both halves now agree.
- **[DONE 2026-05-07] High-frequency unmapped lemmas**: rank #1 منتدى (forum),
  #2 قسم (section), #13 عملية (operation), #16 المنتدى — these are top-20
  forum/web frequencies CAMeL captures but Alif's vocabulary never imported.
  Implemented as a capped cron intake valve instead of a one-shot bulk import:
  `frequency_core_intake.py` handles up to 5 top-1,000 unmapped rows per material
  generation run, tries deterministic existing-lemma lookup first, and only
  creates high-confidence standard vocabulary through import-quality +
  `run_quality_gates(background_enrich=False)`. Conservative rejects get
  `gap_status="needs_manual_review"` so cron does not retry them forever. It
  creates no ULK rows; normal candidate selection and material generation
  introduce the words later.

---

## 🔴 Generation pipeline — three concurrent bugs (2026-05-03)

Found while drilling into the 21-day learning review. The 211 words in 7-day backoff and 12 acquiring words with no active sentence are caused by:

1. **`lemma_ar_bare` corruption on textbook_scan imports** — bare form is a different morphological word than `lemma_ar` (verb root vs noun, plural vs singular, form V vs form I, or sometimes a wholly different lemma). Fix: audit + repair script, ~50–100 lemmas.
2. **Validator demands exact bare-form match on the target** — doesn't accept any inflection. Fix: replace target check in `validate_sentence` with a `lookup_lemma()` resolution to `target_lemma_id`. ~5 LOC.
3. **Step A2 corpus enrichment kills sentences on first verifier disagreement** — 22.4% kept (1,846/8,250). `same_lemma` is not actionable feedback yet triggers permanent deactivation. Fix: soften `apply_corrections` callsite *in enrichment only*, without weakening the gate for fresh LLM generation (where the same_lemma rejection is intentional hardening — see `feedback_dont_weaken_same_lemma_gate`).
4. (observability) **New self-correct batch path emits no success events.** Add `batch_self_correct_returned/_validated/_rejected` events. ~10 LOC.
5. (cleanup) **Drain the 172-entry backoff list** once 1+2 land. — *2026-05-04 update: now expected to drain naturally via backoff-aware multi-target (see entry below); manual drain probably unnecessary.*
6. (deployed 2026-05-04) **Backoff-aware multi-target** (replaces planned C1 retry): backed-off lemmas can ride along as collateral in multi-target groups (≤1 per group of ≤4 healthy peers). A multi-target success auto-resets `generation_failed_count`. Self-correct paths still exclude backed-off lemmas. Driven by the observation that #2307 (named hard-core failure) succeeded twice as multi-target collateral while consistently empty-failing in single-word fallback. See `research/experiment-log.md` 2026-05-04 entry.
7. (deployed 2026-05-20) **Phase-2 caller bug + orthographic widening + watchdog** (partially addresses #1, #2, #4): the multi-target generation path was passing `strip_diacritics(lemma_ar)` to the validator instead of `lemma_ar_bare`, which dropped the implicit ya from defective participles (طَاغٌ → "طاغ" vs stored bare "طاغي"). Fixed at 5 caller sites in `material_generator.py` / `sentence_generator.py` / `update_material.py`. Validator gained `final_alef_variants()` for word-final ا ↔ ى swap (orthographic only, not morphological — mirrors `build_lemma_lookup` Pass 1b). Added `pipeline_watchdog.check_and_alert()` to flag stuck lemmas in <24h via ActivityLog. Caught 2026-05-20: three lemmas burned 461 validation failures over a week with zero acceptances. The broader item-1 audit (bulk lemma_ar_bare corruption repair) and item-2 reframe of the target check are NOT in scope of this fix; only the specific stuck-target shape is.
8. (deployed 2026-05-21) **Chimera cleanup + prevention** (closes item-1, completes item-4): comprehensive cleanup of 7 historical chimera lemmas (4 Form V verbs stored with 3-letter root bare, 3 defective `ـٍ` participles missing explicit ya), plus systemic prevention. New `bare_shape_check.py` runs as `run_quality_gates` Gate 1b — auto-corrects Form V/VI/VII/VIII/X root-bare and defective-ya patterns at import time before lemmas ship to the pipeline. `pipeline_watchdog.py` gained a soft tier (`pipeline_target_struggling`, ≥15 fails / <15% accepts) that catches lemmas the strict 0-accept gate misses. New `chimera_audit.py` runs DB-wide structural scan every cron pass (warm_sentence_cache Phase 7), emitting `chimera_audit_findings` ActivityLog on changes. Lemma #65 was a separate cross-meaning chimera (gloss=laptop / Arabic=repentance) repaired in-place by relabeling. The #2522 عَالٍ ↔ #2208 عَالٍ homograph remains as a follow-up merge candidate (out of scope; needs ReviewLog + SentenceWord reassignment).

See `research/generation-pipeline-investigation-2026-05-03.md` for full evidence and dependency-ordered fixes.

---

## 🟢 [DONE 2026-04-30] Session intro-card and benchmark coverage corrections

Follow-up to the 2026-04-27 learner-data fixes. The end-of-session intro exclusion and backend card cap could still let a user see new words before an intro card. Fixed by scanning every non-function word in returned session items, promoting cold session scaffolds before card construction, uncapping first-time sentence-bound intro cards, and matching frontend intro placement through `canonical_lemma_id`. Also extended the duplicate veto to normalized Arabic text and the pregenerated fill path, and replaced exact-string Al-Kitaab benchmark matching with the sentence-validator lemma/form lookup. Current prod snapshot: Al-Kitaab Part 1 coverage recalculates to 268/364 (73.6%), with remaining rows in `research/alkitaab_part1_missing_2026-04-30.tsv`.

## 🟢 [DONE 2026-04-27] Learner-data-driven session quality fixes (PR sh/learning-data-fixes)

Four issues surfaced from production data audit:
1. **Tier-0 working-memory false positives** — fast-grad fired within 30 seconds of intro card 4/6 times. Fixed with `FAST_GRAD_INTRO_GAP = 10 min` gate.
2. **Near-duplicate sentences in same session** — 8 high-Jaccard pairs in 7d (worst was 1.0 on lemma sets). Fixed with `JACCARD_VETO_THRESHOLD = 0.7` hard veto in greedy + acquisition-repeat loops.
3. **Intro card overload** — sessions had up to 10 intros (40% density). Lowered `INTRO_CARDS_MAX` 10→6, ramp slowed to `+1/15`.
4. **End-of-session intro flood** — 9/17 sessions placed intros in final 25%. Frontend now reorders sentences (intro-bearing front-middle, no-intro wind-down) and excludes intros from final 20%.

Future: re-evaluate `FAST_GRAD_INTRO_GAP` and `JACCARD_VETO_THRESHOLD` after 1-2 weeks of data.

---

## 🟡 Lemma Decomposition Pipeline — Phase 1 + Phase 2 Steps 1-4b done 2026-04-24, Steps 4c + 5-8 OPEN

User-found bug: a reading card showed **#2862 وَتَرَكَهُم "and left them"** (root 305, source=`quran`) as a single atomic verb lemma. Correct decomposition is و (proclitic "and") + تَرَكَ (verb "he left") + هُمْ (enclitic "-them"). The compound form was stored in `lemmas` instead of the canonical تَرَكَ. User flagged: *"this whole pipeline needs investigation."*

**Phase 1 complete (2026-04-24)** — read-only audit. Reports:
- `research/decomposition-audit-2026-04-24.md` — methodology + per-import-path table + Phase 2 sequence + risks
- `research/decomposition-classification-2026-04-24.json` — every lemma classified (bucket + tier + canonical resolution + ULK history)
- `scripts/audit_lemma_decomposition.py` — re-runnable

**Phase 1 quantified findings (vs original estimates below)**:
- Buggy paths narrowed from "audit all 11" to **2 specific files**: `app/services/quran_service.py:732-768` (primary) + `scripts/backfill_function_word_lemmas.py:111-122` (low-risk hygiene). 9 of 11 lemma-creation sites already correct.
- 144 HIGH-tier compounds with canonical-in-DB (593 reviews), 4 MEDIUM, 13 LOW
- 102 orphan compounds (canonical missing from DB; 385 reviews) — Phase 2 must backfill canonicals first
- **1,271 reviews on non-canonical lemmas — 4.5× the original estimate**
- #2862 confirmed orphan (canonical تَرَكَ not in DB)
- 9/10 known offenders caught; the miss (#430 تشَرَّفنا) is a different bug class (verb-conjugation duplicate, not clitic compound)

**Phase 2 Step 1 — DONE (2026-04-24, PR #46).** Both buggy import paths now call `resolve_existing_lemma()` before creating Lemma rows, matching the pattern used by the 9 already-correct paths (e.g. `story_service.py:305,348,508`). Bleed is stopped — new compounds will resolve to canonicals (when canonical exists), or surface as orphans for Step 3 to backfill. Tests in `backend/tests/test_lemma_dedup_imports.py` cover direct-match, clitic-strip, and create-when-new.

**Phase 2 Step 2 — DONE (2026-04-24).** DB backup: `/opt/alif-backups/alif_pre_decomposition_20260424_131904.db`.

**Phase 2 Step 3 — DONE (2026-04-24, PR #47).** Backfill for 102 orphan canonicals. Claude Haiku batch calls as combined verdict gate + enrichment. Result: 33 created (#3139-#3171), 67 flagged `mle_error`, 2 `already_canonical`. Artifact: `research/decomposition-backfill-progress-2026-04-24.json`. Script: `backend/scripts/backfill_decomposition_orphan_canonicals.py`.

**Phase 2 Step 4a — DONE (2026-04-24, PRs #49 + #50).** Spot-check of Step 3's 33 canonicals surfaced a systematic CAMeL MLE failure the original gate missed: feminine ة misread as 3ms_poss. Re-gated all 33 with stricter prompt → **22 bogus_mle_error deleted, 11 confirmed_valid linked** to their canonicals with full ULK merge. User's screenshot case #2862 وَتَرَكَهُم now correctly points at canonical #3148 تَرَك. Scripts: `regate_step3_created_canonicals.py`, `apply_step4a_regate_deletions.py`, `apply_step4a_link_survivors.py`. Verdicts: `research/decomposition-regate-2026-04-24.json`.

**Phase 2 Step 4b — DONE (2026-04-24, PR #51).** Added `lemmas.decomposition_note` (nullable JSON) column via Alembic `aa7h8i9j0k12` (initial chain-off collided with `b4e1f07a2c18` from another PR; fixed with one-line re-parent). Tagged 89 orphans with `{mle_misanalysis: true, reason, source_artifact, tagged_at, phase: "step4b"}`: 22 from 4a-prime `bogus_mle_error` + 67 from Step 3 `mle_error`. Script: `backend/scripts/tag_mle_misanalysis_orphans.py` (dry-run default, refuses to overwrite existing notes, one ActivityLog per run). Query flagged rows: `WHERE json_extract(decomposition_note, '$.mle_misanalysis') = 1`. ActivityLog 1506 confirms on prod.

**Phase 2 Steps 4c, 5-8 still OPEN.** Sequence: re-gate + migrate 144 HIGH-tier with vetted stricter prompt (expect ~40-60 additional `mle_misanalysis` tags via the same column) → manual MEDIUM/LOW (17 entries) → re-enrich Hindawi corpus → re-gloss root #305 ت.ر.ك → verify next Quran surah import.

**New lesson (2026-04-24 during 4b)**: always run `ssh alif "cd /opt/alif/backend && .venv/bin/alembic heads"` BEFORE writing a new migration. Alembic can have two heads even when git has no merge conflict, because two branches each chained off the same parent. Fix is a one-line `down_revision` swap — cheap to prevent by checking upfront.

---

### Original investigation notes (2026-04-23, kept for reference)

### Scope of the problem (verified against prod 2026-04-23)

**Confirmed compound lemmas with real review history** (10 that appear in my regex audit + hand-verification):

| lemma_id | compound | gloss | reviews | state | source |
|---|---|---|---|---|---|
| #1638 | وَلَكِنْ | but (و+لكن) | **104** | known | auto_intro |
| #1469 | اَلْيَوْمَ | today (ال+يَوْم) | **99** | known | textbook_scan |
| #1468 | اَلْآنَ | now (ال+آن) | 43 | known | book |
| #1806 | لَها | to her (ل+ها) | 35 | known | book |
| #430 | تشَرَّفنا | nice to meet you (تَشَرَّفَ+نا) | 12 | known | auto_intro |
| #1608 | أُعَرِّفُكُمْ | I introduce you (عَرَّفَ+كُم) | 7 | acquiring | textbook_scan |
| #1732 | وَنَأْكُلُ | and we eat (و+نَأْكُل) | 5 | known | textbook_scan |
| **#2862** | **وَتَرَكَهُم** | and left them (و+تَرَكَ+هم) | 2 | learning | **quran** |
| #1692 | فِيهَا | in it (في+ها) | 0 | encountered | book |
| #2874 | خَلَقَكُم | created you (خَلَقَ+كُم) | 0 | encountered | quran |

**Top 4 offenders represent ~280 reviews that should have been credited to canonical forms** — the user has been consolidating compound surface forms instead of the underlying lemmas.

**Orphan compounds in Quran source**: ~70 of the 92 source=`quran` lemmas look compound. Examples:
- ءَامَنَّا (آمَنَ+نا — "we believed")
- شَيَطِينِهِم (شَيَاطِين+هم — "their devils")
- فَاتَّقُواْ (ف+اتَّقَى — "then fear")
- يَأَيُّهَا (يَا+أَيُّ+ها — vocative particle)
- خَلَقَكُم (خَلَقَ+كُم — "created you")

Most haven't been auto-introduced yet so review impact is low so far, but they'll keep getting promoted as the user reads Quran verses.

### Root cause

**`backend/app/services/quran_service.py:732-773` (`_import_unknown_lemmas` or equivalent)** uses exact-string dedup only:

```python
existing_bare_set = {normalize_alef(l.lemma_ar_bare) for l in all_lemmas}
...
if bare_norm in existing_bare_set:
    continue
lemma = Lemma(lemma_ar=surface, lemma_ar_bare=bare, source="quran", ...)
db.add(lemma)
```

It never calls `resolve_existing_lemma()` — the clitic-aware dedup from `sentence_validator.py:1624` that's used in `story_service.py:305,348,508`. The CLAUDE.md claim "Import dedup: all scripts use `resolve_existing_lemma()`" is **wrong for the Quran path** (and possibly textbook_scan, avp_a1, etc. — needs per-path audit).

Decomposition infrastructure that *already exists* and should be called:
- `sentence_validator.py:337` — `_strip_clitics(bare_form) -> list[str]`
- `sentence_validator.py:1624` — `resolve_existing_lemma(bare, lookup)` — tries stem + clitic-stripped stem
- `confusion_service.py:74` — `decompose_surface(surface, lemma_bare, forms)`

### Connection to the earlier enrichment-failure analysis (same session, 2026-04-23)

The Hindawi corpus enrichment had ~80% failure rate (6,431 of 6,465 corpus sentences inactive). Top blocker surface forms included وَرَاقَبَ, عَلَيْهِ, إِلَيْهِ — the same decomposition-failure pattern on the *enrichment* side. The tokenizer's `map_tokens_to_lemmas` in the enrichment path *does* call clitic-aware logic, but if the target canonical تَرَكَ / رَاقَبَ doesn't exist in the DB (because the import pipeline stored وَتَرَكَهُم / وَرَاقَبَ as lemmas instead), the enrichment has nothing to match to. **Two ends of the same broken pipeline.**

### Secondary bug on the same card — gloss conflation

The screenshot's root card showed ت.ر.ك glossed as *"related to Turkic peoples, Turkey, leaving, and abandoning things"*. That's the LLM enrichment conflating two unrelated roots:
- ت.ر.ك (taraka, "to leave")
- تُرْك (Turk/Turkey)

The shared letters ت-ر-ك confused the enrichment. Separate issue from decomposition, but visible on the same card. Lives in root gloss generation (probably in `lemma_quality.py` or the root enrichment path).

### Investigation + remediation plan (1-2 sessions)

**Phase 1 — Audit (read-only, half a session)**
1. Use CAMeL Tools morphology (not regex — my regex has false positives like فَهِمَ ends in "هم", وَجَدَ starts with "و" but these are real lemmas). Classify all 2 905 lemmas as: canonical / compound-with-canonical-in-db / orphan-compound / ambiguous. Write the classification to a JSON file in `research/`.
2. Per-import-path audit: does each of the 7 import scripts (`import_quran.py`, `import_wiktionary.py`, `import_avp_a1.py`, `import_duolingo.py`, `import_hindawi.py`, `import_michel_thomas.py`, `import_scaffold_lemmas.py`) call `resolve_existing_lemma` before `db.add(Lemma)`? Plus `quran_service.py`, `story_service.py`, `book_import_service.py`, `lemma_quality.py`, `material_generator.py` (flag_autocreate path), `sentence_validator.py` (mapping_correction path).
3. Produce a categorized report in `research/decomposition-audit-2026-XX-XX.md`.

**Phase 2 — Remediation (full session)**
1. **Fix the import paths** — patch `quran_service.py` + any other outliers to call `resolve_existing_lemma()` before creating, and to run clitic-stripping to try multiple candidate stems. Add a test case per path.
2. **Backfill canonicals** — for orphan compounds that don't have a canonical in DB yet (e.g. #2862 needs تَرَكَ created), import the canonical forms first via `import_scaffold_lemmas.py` pattern.
3. **Migrate existing compounds**:
   - For each compound with `canonical_in_db=True`: set `canonical_lemma_id` to redirect, **migrate `user_lemma_knowledge` review history** to the canonical (sum times_seen, times_correct, take max(stability), earliest(introduced_at), latest(last_reviewed)). Merge FSRS state carefully — a card with stability 90d on compound + stability 30d on canonical becomes stability ≈ 90d on canonical after merge (the user *has* seen this word 104 times, just under a compound spelling).
   - For orphan compounds after canonical is created: same migration.
4. **Cleanup**: leave compound rows in place but with `canonical_lemma_id` set (per the existing variant pattern — see `canonical_lemma_id` usage in `word_selector._resolve_to_canonical`). Don't hard-delete — preserves audit trail and any legacy references.
5. **Fix gloss conflation**: audit root glosses for homograph conflation, re-enrich root 305 (ت.ر.ك) and similar affected roots.

**Phase 3 — Verify**
1. Re-run the enrichment on the 6 431 inactive corpus sentences (clear `mappings_verified_at`, let cron re-try). Expect success rate to climb materially as canonicals are now present.
2. Spot-check 10 randomly-sampled new Quran verse imports — confirm all words decompose.
3. Check that the top-15 frequency gaps from the learning analysis aren't actually just decomposition-failures in disguise.

### Risks / discipline

- **Don't act piecemeal** — halfway state (compounds redirected but imports still creating more) produces churn. Do the import fix *and* cleanup in one session.
- **Review-history merge is irreversible** — backup DB before Phase 2 step 3.
- **Watch for the `same_lemma` gate in apply_corrections** (feedback_dont_weaken_same_lemma_gate.md). Once compounds redirect to canonicals, some existing `apply_corrections` events might now be "same lemma" — that's the *correct* behavior post-fix, not a bug.
- **Gloss enrichment in Phase 2 step 5**: run only after Phase 2 step 1-4 complete so the root's lemma list reflects real vocabulary, not compound ghosts.

### Evidence left behind this session
- Experiment-log 2026-04-23 entry (this investigation).
- Memory: `feedback_lemma_decomposition_audit.md` (high-priority flag so next session picks up).
- User is waiting for Max plan reset before working on it.

---

## Bookify Arabic — Reading Aid PDFs (redesigned 2026-04-22)
`backend/scripts/bookify_arabic.py` — take an Arabic chapter, identify lemmas not yet in the user's Alif vocabulary, and render a paginated PDF reader with preface vocab + two-tier highlighted body. Kalila wa Dimna باب الحمامة المطوقة shipped as pilot. Session report: `research/bookify-kalila-dove-2026-04-22.html`.

- [DONE] Script v0 — `ingest` + `render` subcommands; compound function-word prefix check (ف+لم avoids "film"); clitic folding (الجرذ/للجرذ/والجرذ → جرذ); `frequency_rank ≤ 1000` fallback.
- [DONE 2026-04-22] Full redesign: Scheherazade New font bundled in `backend/data/fonts/` (via `file://` URL, no system install); A4 landscape bilingual with sentence-pair rows (AR right · EN left on every page); A5 portrait glossary; two-tier highlighting (`.tok.new` saffron solid for preface words, `.tok.new-dim` faint gray dotted for other unfamiliar); title page + colophon; `translate_paragraphs` per-paragraph.
- [DONE 2026-04-22] Auto-import: `bookify_arabic.py introduce <json> --top 25 [--dry-run]` imports top-N preface lemmas into Alif as `source='scaffold'` + `UserLemmaKnowledge` rows (`knowledge_state='encountered'`, `source='book'`). Idempotent. 19 new lemmas `#3120–#3138` seeded to prod for Kalila dove; activity logged.
- [TODO] **`introduce` should also register a Story + StoryWord rows** (confirmed 2026-04-23). Without a Story in `book_pages` / `story_lemmas`, `word_selector` scores the introduced lemmas at priority_bonus=0 (`scaffold` not in `_SOURCE_TIER_BONUS`), so they sit as `encountered` indefinitely while active book_ocr stories monopolize the 200-tier. Pattern to mirror: `book_import_service.py:491-502` (Story row with `source='book_ocr'`, `status='active'`, `page_count`) + StoryWord rows with `page_number` from first-occurrence paragraph. Manually backfilled Kalila as Story #31 on 2026-04-23 to prove this works — all 19 lemmas immediately jumped to ranks 1–19 in `select_next_words()` with scores 192–199. Teach `introduce` to do it automatically so the next chapter doesn't need manual surgery.
- [TODO] Large-paragraph translation (>2500 chars) still fails via in-ingest CLI due to Sonnet 240s timeout. Fix: split paragraph into halves when large, or fall back to per-sentence with in-context direct alignment. (Current workaround: do alignment in-session directly.)
- [DONE 2026-04-22] Per-page footnotes via WeasyPrint `--format footnotes` — empirical: full Kalila chapter (432 distinct unfamiliar lemmas → 432 first-occurrence footnotes) renders in 30s into 37 A5 pages, ~12 footnotes/page, beautiful Scheherazade shaping. Artifact: `backend/data/kalila_dove.footnotes.pdf`. Earlier "WeasyPrint chokes >100 footnotes" worry was wrong (untested guess); WeasyPrint's CSS3 float-solver scales fine. Spike of paged.js as replacement: rejected — it silently truncates 85-95% of body content on our long-paragraph layout (4 pages emitted vs WeasyPrint's 37). Findings: `research/bookify-renderer-spike-2026-04-22.html`.
- [TODO] Support more source pipelines: Hindawi books (user has HuggingFace parquet imported), LAL Arabic PDFs (Gemini OCR or archive.org HTML), plain uploaded text files.
- [TODO] Format coverage — missing: facing-page PDF (AR even pages, EN odd), EPUB export for Kindle (bookifier-style, RTL + Scheherazade), interactive HTML reader (hover-to-gloss, click-to-add-to-Alif).
- [TODO] Per-lemma gloss instead of per-surface — currently `طَوَّقَ` tags as "passive participle" because that's the surface; `رَأَى` glossed as "I saw" not "to see". Fix at enrichment time, re-run quality gates on the 19 new imports.
- [TODO] Homograph cleanups beyond فلم: آن ("time" / "that"), ملك ("angel" / "king"). Could use CAMeL MADAMIRA or one-shot claude -p disambiguation.
- [TODO] Layl / TV transcript pipeline: same ingest stage but source = ASR transcript. Lebanese dialect vocab not in Alif; mixed French/English code-switch.

---

## Generation Pipeline — Lock & Waste (2026-04-17)
- [DONE] Missing-lemma candidate tracker: `apply_corrections` now tags each failed position `same_lemma | not_found`; `scripts/missing_lemma_candidates.py` aggregates from `mapping_corrections_*.jsonl`. Run periodically → curate into `import_scaffold_lemmas.py`.
- [DONE] Refactor `enrich_corpus_sentences` (`backend/scripts/update_material.py:156`) + `store_multi_target_sentence` (`backend/app/services/material_generator.py:658`) to the 3-phase write-lock pattern. Fixed 2026-04-17 (branch `sh/write-lock-refactor`): split `store_multi_target_sentence` into `validate_multi_target_sentence` (LLM + read-only DB) + `write_multi_target_sentence` (pure write); added per-iteration `db.commit()` in `enrich_corpus_sentences`, `create_book_sentences`, and `_verify_new_story_mappings`. All 7 `apply_corrections` sites now audited — 4 were already clean (dedicated `correction_db` sessions in `generate_material_for_word`, `batch_generate_material`, plus explicit phase structure in `verify_sentence_mappings`). 863 tests pass.
- [DONE] Per-lemma generation backoff (2026-04-17, branch `sh/generation-backoff`): added `UserLemmaKnowledge.generation_failed_count` + `generation_backoff_until` (migration `b4e1f07a2c18`). After 3 consecutive 0-result generation runs a lemma is skipped for 7d; any later success resets the counter. Filters applied in `step_backfill_sentences` (`words_needing`) and `_warm_sentence_cache_impl` (`gap_word_ids`). Does not weaken the verification gate — just stops re-hitting the same wall. 869 tests pass.
- [DONE] Agentic generation prototype shipped 2026-04-20 as the default path: `sentence_self_correct.generate_sentences_self_correct_batch()` opens one tool-enabled Sonnet CLI session per batch of ~10 lemmas; Sonnet drafts, runs `validator.py` via Bash, surgically swaps unknown words, re-validates until `needed_per_target` sentences exist per target. Measured 95% naturalness (Haiku-v2 rerank), 1.90 sentences/lemma, $0.085/lemma. Legacy path behind `ALIF_USE_LEGACY_BATCH=1`. The original "multi-turn regresses throughput" risk did not materialize because batching amortizes per-turn cached-token replay across targets; N=10 is the sweet spot and N=15 regressed quality to 56%.

## Arabic Text Storage — Follow-ups (2026-04-17)
- [DONE] Unify `sentences.arabic_text`/`arabic_diacritized` into a single diacritized `arabic_text` column (migration `a8c2d3e4f501`)
- [DONE] Fix Hindawi sentence splitter so terminal `.»` `!»` `?»` `؟»` stay intact (PR #35)
- [DONE] Dropped legacy `arabic_diacritized` key from `SentenceReviewItem` and `BookPageSentenceOut` schemas; frontend types + `book-page.tsx` now read `arabic_text` directly.
- [DONE] Dialogue-aware Hindawi splitter (PR #38, 2026-04-17): PR #35 preserved `.»` but still split on internal `.!?؟` inside unclosed `«...»`, leaving ~17% orphan guillemets. New splitter tracks «/» depth, suppresses internal-terminator splits while depth > 0. Empirically measured on children corpus: orphan rate 26%→1.6%, missing-terminal rate 98%→5.6%.
- [DONE] Hindawi reimport (2026-04-17): deleted 10,748 old-splitter sentences (all inactive + 5 broken active with orphan guillemets); kept 33 cleanly-enriched active. Reimported 6,432 new inactive sentences via dialogue-aware splitter, from 165 of 167 children books, 1,813 distinct lemmas covered. 33 active + 6,432 inactive = 6,465 total corpus sentences.

---

## Spanish Pilot — Next Steps (2026-04-15)
- [DONE] Standalone `spanish-pilot/` prototype with 120 lemmas + 150 sentences, ported Alif scheduler, deployed on Hetzner port 3100
- [TODO] Teacher feedback: is self-grade sufficient or need MC-only enforcement?
- [TODO] If pilot succeeds: multi-user + auth + Postgres rewrite (UserLemmaKnowledge.lemma_id currently unique globally, needs composite key per student)
- [TODO] Scale to full A1-B1 vocabulary (~500 lemmas) with past tense + common irregular verbs
- [TODO] Add TTS audio (ElevenLabs `eleven_multilingual_v2` supports Spanish; need cost estimate for 60 students × 20 sentences/day)
- [TODO] Teacher dashboard: which students progressing, who stuck, who hasn't reviewed
- [TODO] Per-student daily TTS/LLM budget caps (prevent one student draining API budget)
- [TODO] Align content with specific textbook the school uses (teacher can provide word list → seed_lemmas.json)
- [IDEA] A/B test: randomize half the class to self-grade, half to MC-only, compare retention after 2 weeks
- [IDEA] Word-level audio: generate isolated word pronunciation for intro cards (cheaper than full sentence TTS)
- [IDEA] Export student progress as CSV for teacher grade book integration
- [IDEA] If multi-language works, generalize to a configurable engine: `alif-core` with language-specific content plugins

---

## Mapping Verification (2026-04-14)
- [DONE] Fix JSON parse bug — CLI models' structured output was being silently discarded, falling back to weak API Haiku
- [DONE] Add `--json-schema` constrained decoding to verification calls
- [DONE] Add 12 missing dual function words (لهما, بهما, etc.)
- [TODO] Use `json_schema` in other `generate_completion` callers that use `json_mode=True` — same parse bug may affect sentence generation, enrichment, etc.
- [TODO] Add `mappings_verified_at IS NOT NULL` filter to sentence_selector as safety net — currently all active sentences are verified, but a future import path could break this
- [TODO] Re-verify LLM-generated sentences from Apr 12-14 that were verified during the broken period (343 sentences)
- [TODO] Audit `test_next_sentences_endpoint` (20 min!) — likely makes real LLM calls through quality gates; should mock or restructure

---

## Lapse Recovery Tuning Follow-ups (2026-04-13)
- [DONE] `desired_retention=0.95` + `LAPSED_BOOST=3.0` + tightened overdue escalation (0.5d/6x) — see experiment log
- [TODO] **CHECK 2026-04-20**: Re-run `replay_fsrs.py` on fresh DB, count lapses in last 7d with no follow-up. Expected drop: 85 → <40. If still >60, investigate selector diversity penalties.
- [TODO] Periodic FSRS calibration: run `optimize_fsrs.py` monthly, compare `optimal_retention` to current setting, alert on drift >0.02. Could wire as cron step.
- [TODO] Reconsider the `stability < 1.0 → lapsed` override in `fsrs_service.py:131-132` now that intervals are shorter under 0.95 retention — may catch too many freshly-graduated cards as "lapsed" and trigger premature mnemonic regeneration.
- [DEFERRED] Full optimizer weight deployment (w0..w20). Replay showed near-identical post-lapse recovery and the optimizer was fit to incidental-review signal (84% of reviews happen before scheduler's intended due date). Revisit if review mix changes (e.g., listening mode becomes primary).
- [IDEA] "Peak stability" tracking: store `max_stability` per card, use it as a prior when scoring post-lapse recovery urgency. A card that *was* 90d recovers on a different trajectory than one that never exceeded 5d.

---

## Learning Progress Deep Analysis Follow-ups (2026-04-11)
- [DONE] Raise focus cohort cap 200→2000 to unblock 111 silently excluded FSRS words
- [TODO] **CHECK 2026-04-18**: Evaluate cohort cap change effect — overdue count, session build time, review distribution. Decide whether to remove cohort entirely.
- [DONE] Fix FSRS cards with stuck difficulty — replay script (`scripts/repair_fsrs_cards.py`) + periodic cron step G3
- [DONE] Greedy algorithm acquisition starvation — added overdue escalation multiplier (up to 4x for severely overdue words)
- [TODO] Acquisition inflow gate — new introductions (19.4/day) slightly exceed graduation (17.6/day). Consider pausing auto-intro when backlog >100

---

## Vocabulary Gap Detection (2026-04-09)
- [DONE] Mine `correction_failed` logs — found 6,871 failures across 2 root causes
- [DONE] Auto-import script (`scripts/import_scaffold_lemmas.py`) — imported 3 genuinely missing lemmas; 36 others already existed but `correct_mapping()` couldn't find them
- [DONE] Function word collision resolution — `lookup_lemma_direct()` now checks collision table
- [DONE] Make `correct_mapping()` bare-form search more robust — added fallback via `build_comprehensive_lemma_lookup()` that handles alef/hamza normalization mismatches + tanwin alif stripping

---

## Verse-by-Verse Text Reading Mode (2026-03-30)
- [DONE] Sequential reading through the Quran one verse at a time
- [DONE] Verse = unit of scheduling (simple level-based SRS, not FSRS)
- [DONE] Mixed into daily sessions, micro-interactions
- [DONE] New verses released in reading order (~3/day), reviewed out-of-order via SRS
- [DONE] Start from Al-Fatihah, sequential through Quran, 6236 verses imported
- [DONE] Tap-to-lookup on individual words with full WordInfoCard (root/pattern/navigation)
- [DONE] ALA-LC transliteration on card back (via transliterate_arabic(), not risan/quran-json phonetic data)
- [DONE] Ta maftouha fallback in lemmatization (رحمت→رحمة, نعمت→نعمة, etc.)
- [DONE] Gold accent design distinguishing from regular review cards
- [DONE] Words absorbed through repeated verse exposure auto-promote to acquiring (threshold: 3 distinct understood verses with srs_level >= 2)
- Motivational loop: master surah → unlock recitation listening
- Classical Arabic vocabulary directly relevant to user's literary goals
- Pre-teaching: cautious — possibly only flag "too hard" verses, not pre-teach words
- Inspired by user's earlier Anki Greek NT experience (see Substack post)
- [ ] Improve Quran lemmatization to use LLM for all words (not just unresolved ones) — current rule-based pipeline misses many mappings
- [ ] Handle clitic-attached content words in Quran (e.g., قلوبهم→قلب, ربهم→رب) — clitics on Quranic words need same stripping as sentence words
- [ ] Quran audio: per-verse recitation playback (Husary/Minshawi recordings freely available)

## Session Presentation (2026-03-30)
- [DONE] Interleave intro cards with review sentences (not front-loaded)
- [DONE] Reintro cards (struggling words) now get sentences in the session — previously they were excluded from sentence selection, so the teaching card was orphaned with no practice
- Consider capping intro cards per session (e.g., 15) if interleaving alone isn't enough
- Consider changing intro card filter from `times_seen == 0` to `experiment_intro_shown_at is None` so words don't lose intro card eligibility after being reviewed in sentences

---

## Story Audio & Voice Cloning (2026-03-23)
- [DONE] Story archive system (archived_at, toggle endpoint, frontend section)
- [DONE] Story format diversity (standard/long/breakdown/arabic_explanation)
- [DONE] Story audio generation with voice rotation (3-voice pool, deterministic)
- [DONE] times_heard passive listening tracking
- [DONE] Auto-generate cron (Step H, keeps ≥3 active non-archived stories)
- [DONE] Batch generation script (generate_stories_batch.py)
- [DONE] TTS alternatives research (Google Chirp 3 HD 8x cheaper, Azure 15x cheaper)
- [DONE] RootsOfKnowledge PVC creation (77 min, pending ElevenLabs verification)
- [DONE] RootsOfKnowledge IVC v2 (44 min curated audio, voice_id CgiZNnLDkBFp39WsQkMb)
- [ ] Switch to PVC voice once training completes (swap voice_id in tts.py)
- [ ] A/B test Google Chirp 3 HD vs ElevenLabs PVC for Arabic emphatics quality
- [ ] A/B test Azure ar-SA-HamedNeural for emphatic consonant handling
- [ ] Whisper transcription to strip English from mixed-language files for PVC training
- [ ] Story audio player with progress bar and speed control
- [ ] Auto-generate audio for new stories in cron (Step H.5)
- [ ] Use times_heard in listening readiness scoring (weight passive exposure)
- [ ] Consider Google Chirp 3 HD for cost reduction ($30/M vs ~$240/M)

---

## Podcast / Passive Listening (2026-03-22)
- [DONE] Sampler with 6 format variants generated and playable in app
- [ ] Listen to sampler, choose best format(s)
- [DONE] Repetition-focused episodes targeting acquiring words (3-4x per word, 15 sentences, breakdown audio). Script: `generate_repetition_podcasts.py`
- [ ] Cron job: daily episode generation based on SRS state
- [ ] Log podcast listening as FSRS review credit (words heard = partial review)
- [ ] Word-synced transcript in frontend player (tap word for detail)
- [ ] Two-voice dialogue format (different ElevenLabs voices for characters)
- [ ] Request stitching (`previous_request_ids`) for smoother segment transitions
- [ ] Episode series: multi-day story arc across episodes
- [ ] Speed control in player (0.7x, 1.0x, 1.2x)
- [ ] Offline download: pre-fetch podcast for airplane/subway mode
- [ ] Integration with Apple/Android podcast apps (RSS feed)
- [ ] Consider ElevenLabs Creator plan upgrade ($11/mo) for 3+ episodes/month

---

## Tashkeel Fading — Phase-Aware (2026-03-20)
- [DONE] Frontend: hide tashkeel on front (reading), restore on back (verification) — `index.tsx` SentenceReadingCard + SentenceListeningCard
- [ ] Enable "fade" mode on production with 60-day threshold (awaiting user confirmation)
- [ ] After 1 week: review user experience — lower threshold to 30 days if comfortable
- [ ] Consider visual indicator (subtle dot/underline) showing which words had tashkeel removed, so user knows to pay extra attention

## Frequency Gap Analysis (2026-03-20)
- Top-300 coverage: 79/~110 non-function words learned (good)
- Notable gaps: كان (lapsed), مثل (like), أخبار (news), موقع (site), مجموعة (group), صلى (to pray), ضد (against)
- [ ] Auto-import missing high-frequency words (top 300) into encountered state
- [ ] Investigate كان lapse — most important Arabic verb

---

## Monitoring & Check-ins

- [2026-03-10] CHECK: Lapse rate for fast-graduated words (tier 0/1/2). Expected ≤2%. If higher, tighten tier 0 (require rating ≥ 4) or tier 1 (require 4+ reviews). Query: words graduated since 2026-03-03 with times_seen at graduation ≤ 4, check current knowledge_state for "lapsed".
- [2026-03-10] RESULT: 242 graduated, 3 lapsed (1.2%). Tier 0: 2/45 (4.4%), Tier 1: 0/31, Tier 2: 0/24, Tier 3: 1/142 (0.7%). All within ≤2% target. Tier 0 lapses (أتى, لدى) graduated just 1 day ago. No action needed. Recheck in 1 week.
- [2026-03-13] CHECK: Verb conjugation + tier lifecycle verification (deployed 2026-03-10). Check rejection rate, pool size (~600-800?), tier-4 count (~0?), tier-1 undersupply (0?).
- [2026-03-17] CHECK: Tiered graduation lapse rate recheck (2 weeks of data).
- [2026-03-18] DEPLOYED: Encountered words earn collateral credit. 312 words backfilled to FSRS. Known count ~757 → ~1,010.
- [2026-03-21] CHECK: Collateral credit flow health check. Run the verification query below. Expected: encountered count dropping, graduation rate 20-30/day, no function words in acquiring, collateral introductions visible in interaction logs.
- [2026-03-25] CHECK: Lapse rate for backfilled words. The 312 retroactively graduated words should have low lapse rate (<5%). Query: `SELECT COUNT(*) FROM user_lemma_knowledge WHERE source IN ('story_import','duolingo','textbook_scan') AND knowledge_state = 'lapsed' AND graduated_at >= '2026-03-18'`. If high, FSRS stability from replayed history may be too optimistic.

---

## Session Quality — Revisit After Collateral Fix Settles (target: 2026-03-25)

### Dynamic Session Limit
- Current: fixed `limit=10` in `build_session()`
- Problem: at 96% accuracy with fast completion, 10 sentences is too few
- Proposal: `base=10; if accuracy_2d >= 0.90: base=14; if accuracy_2d >= 0.95: base=18`
- Gives high-performing learners bigger, more challenging sessions
- File: `sentence_selector.py:build_session()`

### Increase Intro Reserve Fraction
- Current: `INTRO_RESERVE_FRACTION=0.2` → 2 reserved intro slots per session (with limit=10)
- Problem: even with collateral flow, explicit auto-intro is throttled to 2 slots
- Proposal: increase to 0.3 (3 slots), AND decouple from accuracy_slots
- Combined with dynamic limit: 18 * 0.3 = 5 reserved intro slots
- File: `sentence_selector.py`

### Comprehensibility Gate — Count Familiar Encountered Words as Known
- Current: encountered words count as NOT known for the 60% gate
- Problem: words with 8+ encounters are practically known — excluding them makes sentences too easy and limits collateral exposure
- Proposal: in `sentence_validator.py`, count encountered words with `total_encounters >= 8` as known for the comprehensibility gate
- Effect: allows harder sentences with more encountered scaffold → more collateral introductions → virtuous cycle
- Requires: careful tuning — if threshold too low, sentences become incomprehensible

### Variant Decision Verification
- Current: variant detection runs once at import time; no verification of variant→canonical links
- Problem: morphologically related but semantically distinct words (صيادية "dish" → صياد "hunter") get merged as variants, and the mapping verification pipeline doesn't catch it because it operates at sentence_word→lemma level, not lemma→canonical level
- Proposal: add a variant verification pass that checks gloss coherence between each variant and its canonical. Flag pairs where glosses have zero overlap for LLM review.
- Could run as a background task or one-time audit script
- File: `variant_detection.py`

---

## Unvowelized Reading & Morphological Disambiguation (2026-03-27)

### Homograph Warning in Word Info
- When a word has a high-frequency homograph (same unvowelized form, different meaning), surface it explicitly in word info: "In unvowelized text this looks identical to X — context cue: Y"
- Examples: مَلِكٌ (king) / مَلَكٌ (angel), حَالٌ (state) / حَالَ (he transformed)
- `confusion_service.py` already has rasm infrastructure; homograph detection could be added there
- Triggered in word detail screen and WordInfoCard

### Newspaper Reading Mode for Stories
- Toggle in story reader that strips ALL diacritics from words above a stability threshold (e.g. >30d)
- Simulates actual Arabic newspaper/book reading — the highest-value real-world skill
- Threshold could be per-story (trigger when >80% of words known) rather than per-word

### Pattern Anchor View (Consonant Skeleton)
- In learn/word info cards, show the unvowelized consonant skeleton alongside the full voweled form
- Trains "rasm recognition" — the ability to identify a word from its bare skeleton as real readers do

### Verb Form Derivation Bridge [DONE 2026-03-27]
- [DONE] Root family now includes `wazn`/`wazn_meaning` per sibling (`get_root_family()`)
- [DONE] `LearnCandidate` now includes `root_family` (was missing)
- [DONE] Reintro card Pattern section shows "causative/intensive of عَلِمَ — to know" for Form II+ verbs
- [DONE] ExperimentIntro card root family shows form labels (F2, F3…) + derivation bridge
- [DONE] Learn card Root Family section shows derivation bridge for Form II+ verbs

---

## Confusable Words / Visual Similarity

### Rasm-Based Confusable Pair Detection
- Compute rasm (dotless skeleton) for all vocabulary; words sharing a rasm are maximal confusables (e.g., بنت/بيت, حبر/خبر)
- Use `rasmipy` library or custom rasm extraction (prototype at `/tmp/claude/arabic_similarity.py`)
- Store in `confusable_pairs` table: `(lemma_id_a, lemma_id_b, similarity_score, similarity_type)`
- Research: `research/confusable-words-research-2026-03-03.md`

### Session Builder: No Same-Rasm Pairs in Same Session
- Prevents interference — AnnA (Anki) and research (Carvalho & Goldstone 2014) show spacing similar items reduces confusion
- Constraint: if word A's rasm matches word B's rasm, don't put both in the same session

### Confusable Word Info Display [DONE]
- [DONE] In WordInfoCard, show visually similar words when marking yellow ("did not recognize")
- [DONE] Highlight distinguishing feature (edit distance + rasm skeleton diff positions)
- [DONE] Also shows morphological decomposition (clitics + stem + form label) for complex surface forms
- WaniKani's "Visually Similar Kanji" feature was the model — implemented via `confusion_service.py`

### Contrastive Review Mode (Future)
- Once both words in a confusable pair are stable (FSRS stability > 10d), present contrastive sentence pairs
- "حبر الكاتب جميل" vs "خبر اليوم مهم" — learner identifies which is which
- Research shows interleaving is better for HIGH-similarity items (Carvalho & Goldstone 2014)
- ONLY when both words are well-established — presenting confusables to novices causes harmful interference

### Leech Interference Analysis
- When a word becomes a leech, check if its confusable partner was recently reviewed
- If correlation found → "interference leech" rather than "knowledge gap leech"
- Intervention: suspend partner temporarily, add contrastive card

---

## Core Learning Model

### Word Knowledge Tracking
- Track at three levels: root, lemma (base form), conjugation/inflected form
- Primary tracking at lemma level, root familiarity derived from its lemmas
- Conjugation-level tracking deferred to Phase 2 [DEFERRED — reduces MVP complexity]
- When user clicks a word: show root, base form, translation. User marks known/unknown
- Imported words get partial credit (not full "known" status) — need verification through review
- [DONE] Knowledge score (0-100) per word: 70% FSRS stability (log-scaled, measures memory durability) + 30% accuracy, scaled by confidence ramp (diminishing returns on review count). Stability dominates because it only grows through successful spaced repetition.
- [DONE] Al- prefix deduplication: "الكلب" and "كلب" are the same lemma. Import strips ال before dedup check. Merged 14 duplicates in existing data.

### Focus Cohort Size Analysis
- Current MAX_COHORT_SIZE=100. Research recommends 30-50 for 2-3 reviews/word/day. Need data to decide.
- Write a script that queries production DB: count of FSRS-due words per day over last 2 weeks, cohort utilization (how many due words are outside the cohort), average reviews per word per day.
- If typical due count is <50, reducing cohort has no practical effect. If >50, smaller cohort prioritizes fragile words more aggressively.
- Decision deferred until data analysis completed.

### Spaced Repetition
- Use FSRS algorithm (py-fsrs), superior to SM-2
- Reading-focused: user sees Arabic → tries to comprehend → reveals translation → rates
- No production exercises (no typing Arabic, no translation to Arabic)
- Self-assessed: trust user not to cheat since no gamification pressure
- Could track response time as implicit difficulty signal
- Consider separate FSRS cards for recognition vs. recall if we ever add production

### Root-Based Learning
- Learning KTB root → Maktaba, Maktab, Kataba etc. are highly productive
- [DONE] Identify morphological patterns (e.g., how to form "place of doing X" = maf3al) — wazn column on Lemma, pattern decomposition in learn/review cards, /api/patterns endpoints
- Verb form patterns (Form I-X) as learning accelerators
- Group kitchen appliances, professions, etc. by pattern — partially done (wazn column enables grouping)
- Root family exploration UI: show all known/unknown words from a root
- Prioritize roots by "productivity" (number of common derivatives)

### Curriculum Design
- Structure learning by word frequency + domain
- Use CAMeL MSA Frequency Lists (11.4M types from 17.3B tokens)
- KELLY project for CEFR-level word mapping
- Learning progression: A1 (top 100 roots, Form I only) → C1 (all forms, dialectal variants)
- Domain-based modules (food, family, politics, religion, etc.)

---

## Sentence Generation & Validation

### LLM + Deterministic Validation
- Generate-then-validate pattern: LLM generates sentence → CAMeL Tools lemmatizes every word → check against known-word DB → verify exactly 1 unknown word
- Retry loop with feedback to LLM (max 3 attempts)
- [DONE] All words are now learnable — no function word exclusions. Particles, prepositions etc. get full FSRS tracking.
- Sentence templates for quick generation: "the X is Y", "I went to the X"
- Pre-generate and cache validated sentences for offline use

### Per-Word Contextual Translations
- Currently the LLM returns only sentence-level data (arabic, english, transliteration). Per-word glosses come from the Lemma table or hardcoded `FUNCTION_WORD_GLOSSES`. Words without either have no gloss.
- **Idea**: Ask the LLM to return per-word contextual translations during generation. Solves missing glosses AND adds learning value (context-specific meanings for polysemous words like عين = eye vs spring).
- Requires `gloss_en` column on `SentenceWord` (currently only `StoryWord` has one), modified LLM prompt, and matching logic between LLM word keys and tokenized surface forms.
- Interim option: lemma-based backfill (covers ~95% of words without LLM changes, no contextual value).
- **Full writeup**: [`research/per-word-contextual-translations.md`](research/per-word-contextual-translations.md)
- **Timing**: Revisit during sentence generation redesign.

### Sentence Sources
- LLM-generated sentences with vocabulary constraints
- Tatoeba corpus (~67K Arabic sentences with translations, CC BY 2.0)
- BAREC corpus (69K sentences across 19 readability levels, on Hugging Face)
- Quran (Tanzil corpus) — gold-standard diacritized text
- News articles segmented into sentences
- **Corpus-first pipeline** — search corpus for sentences matching target word + comprehensibility threshold, fall back to LLM only when no corpus match found. Instant, free, deterministic. See `research/arabic-sentence-corpora-2026-02-21.md`
- **Tier 1 corpora for immediate integration**: Tatoeba (67K, translated), BAREC (69K, graded), AMARA/TED (educational, translated)
- **Tier 2 for processing**: WikiMatrix (millions of Arabic-English pairs, filter by margin score >1.06), Hindawi E-Book Corpus (81.5M words, fiction/children's), OSIAN (37M MSA news sentences)
- **Tashkeela** (75M fully diacritized words, 99% classical) — useful for tashkeel model training, small MSA portion extractable
- **SAMER readability lexicon** — 40K lemmas with 5-level difficulty could map to our lemma difficulty estimates
- **Leveled Reading Corpus** (UAE K-12 textbooks + fiction, graded by grade level) — gold standard if accessible (NYU Abu Dhabi)
- **CCMatrix/CCAligned** (billions of web-mined parallel sentences) — needs aggressive quality filtering but enormous volume
- **OpenSubtitles** (conversational Arabic, parallel translations) — needs dialect filtering for MSA-only use
- **Hybrid approach validated by research**: GenAI sentences preferred by learners in 66% of pairwise comparisons, but corpus sentences are free/instant. Optimal: corpus for bulk, LLM for gaps.
- **Corpus selection architecture** (feasibility confirmed, see `research/corpus-vs-llm-feasibility-2026-02-21.md`): inverted index by lemma_id → comprehensibility filter → scoring → LLM fallback. Pre-process: diacritize (Fine-Tashkeel, 2.5% WER, free), lemmatize (existing pipeline), translate (Claude CLI batch, free). Promotes corpus sentences into existing Sentence table — zero changes to session_service.py.
- **Phased corpus rollout**: Phase 1 at ~500 lemmas (BAREC+Tatoeba, 2-3 days), Phase 2 at ~1,000 (add Hindawi+WikiMatrix, 500K+ sentences), Phase 3 at ~2,000 or multi-user (1M+ sentences, 80% corpus hit rate). Multi-user cost: <$15/mo for 100 users vs $60-600 LLM-only.
- **Auto-diacritization pipeline**: Fine-Tashkeel (HuggingFace, open source, ~2.5% WER) for bulk corpus processing. Most errors are case endings (least critical for learners). Alif's tashkeel fading hides diacritics on known words anyway.
- **SAMER readability integration**: 40K-lemma readability lexicon (5 levels) could replace/supplement heuristic difficulty scoring. Map SAMER levels → Alif lemmas for per-word difficulty.
- **Corpus-sourced vocabulary growth**: When corpus import encounters unmapped words, create Lemma entries in "encountered" state. Corpus becomes a vocabulary discovery source, not just a sentence source.
- [DONE] **Hindawi children's books import** (2026-04-11): `scripts/import_hindawi.py` imports sentences from Hindawi E-Book Corpus (1,745 books, CC-BY-4.0, HuggingFace). First run: 167 children's books → 4,590 fully-mapped sentences covering 1,122/1,794 lemmas (62%). Proper name detection (static list + book-concentration heuristic). Translation on-demand via cron step A2. `source="corpus"` gets 1.3x scoring bonus.
- Expand corpus import to other Hindawi categories (novels, detective fiction, travel literature) as vocabulary grows
- Import additional corpora: AMARA/TED talks (educational MSA with translations), WikiMatrix (millions of Arabic-English pairs)

### Difficulty Assessment
- [DONE] SAMER lexicon: 40K lemmas with 5-level readability scale — backfilled to cefr_level (1365/1610 matched), auto-runs in update_material.py cron. TSV at backend/data/samer.tsv on server (not in git, license: non-commercial/no redistribution).
- BAREC: 19-level sentence difficulty — investigated as sentence source, but only ~50% diacritized and many are context-dependent excerpts. Not a drop-in replacement for LLM generation. ~3,700 fully diacritized usable sentences at levels 5-10 in 5-14w range.
- Word frequency rank as proxy for difficulty
- Sentence difficulty = function of (unknown words, grammar complexity, length)

---

## Text Processing Features

### Text Import & Analysis
- Paste any Arabic text → extract all words → analyze with CAMeL Tools
- Show: total words, unique lemmas, known/unknown breakdown, difficulty score
- Create training plan: learn unknown words in frequency order until text is readable
- Track progress toward "ready to read" target text

### Text Rewriting
- Rewrite text to a desired difficulty level using LLM
- Replace unknown words with known synonyms where possible
- Simplify grammar while preserving meaning
- Output both simplified and original for comparison

### Glossing
- Generate interlinear glosses for any text
- Annotate only unknown words (based on user's knowledge)
- Export glossed text as PDF for offline reading
- Progressive glossing: reduce annotations as knowledge grows

---

## Audio & Listening

### Text-to-Speech
- ElevenLabs for high-quality audio generation
- Google Cloud TTS (1M chars/month free) as fallback for MSA
- ARBML/Klaam for self-hosted open-source option
- Generate audio per-sentence and for full texts
- Cache all generated audio
- Use Duolingo CDN audio URLs as fallback for words that were imported from Duolingo (already have per-word audio URLs in the export)
- Audio filename keyed by SHA256 of (text + voice_id) for deterministic caching
- Consider pre-generating audio for all sentences during off-peak hours to avoid API latency during reviews

### Listening Practice Modes
- Listen-only mode: hear sentence, try to understand, then see text
- Read-along mode: see text + hear audio simultaneously
- Sentence-by-sentence: practice individual sentences, then full story
- Speed control: slow down audio for beginners
- Minimal pair practice: distinguish similar-sounding words

### Story Mode
- Generate stories with controlled vocabulary (LLM + validation)
- Progressive difficulty: each story slightly harder than the last
- Story series: recurring characters/themes for context building
- Record which stories have been "mastered" (all words known + comprehension)

---

## Duolingo Import
- 302 lexemes exported with diacritics and audio URLs
- Many inflected forms (كَلْبِك، كَلْبَك، كَلْبي from كَلْب)
- [DONE] Includes proper nouns, country/city names to filter — handled by `word_category` classification (proper_name/onomatopoeia) with deprioritized scheduling
- Audio URLs from Duolingo CDN — could potentially cache these
- Import as "learning" state, not "known" — verify through review cycle

---

## Diacritization (Tashkeel)

### Tools
- CATT (Apache 2.0) — best open-source accuracy, pip-installable
- Mishkal — rule-based, good for simple cases
- CAMeL Tools — built-in diacritization
- Risk: published benchmarks inflated by 34.6% data leakage

### Application
- Diacritize all displayed Arabic text by default
- Option to hide diacritics for advanced practice
- Partial diacritization: only show diacritics on difficult/ambiguous words
- Pre-diacritize and cache for lesson content
- Human review for critical educational materials

---

## UI / UX Ideas

### Review Interface
- Large Arabic text (32pt+), RTL-aligned
- Tap to reveal translation, root, morphological info
- Four-button rating: Again / Hard / Good / Easy
- Progress indicator: cards remaining, streak, session stats
- Night mode for comfortable reading
- [DONE] Removed redundant missed word summary below transliteration — words already highlighted red/yellow in sentence
- [DONE] Root family in word info card filters out self (no longer shows looked-up word as its own sibling)
- [DONE] Root meaning text wraps properly (flexShrink) instead of overflowing card
- [DONE] Back/undo in review: go back to previous card after submitting, undo the review (restores pre-review FSRS state from snapshots in fsrs_log_json). Handles both sync queue (not yet flushed) and backend (already flushed) cases. Idempotent.

### Word Detail View
- Show: Arabic (diacritized), English gloss, root, POS
- All known words from same root
- Verb conjugation table (via Qutrub)
- Example sentences using this word
- Audio pronunciation
- Frequency rank / difficulty level
- [DONE] Suspend/reactivate + flag translation actions (via ActionMenu)

### Action Menu
- [DONE] Generic "⋯" action menu replacing AskAI FAB across all screens (review, learn, story, word detail)
- [DONE] Consolidates: Ask AI, Suspend word, Flag content (translation/Arabic/transliteration)
- [DONE] Sentence info debug modal: shows sentence ID, source, difficulty score, times shown, review history, and per-word FSRS difficulty/stability/accuracy. Accessible from "..." menu during review.
- Future: add "Never show this sentence again" action to retire specific sentences from review
- Future: "Report pronunciation" to flag TTS audio quality issues
- [DONE] LLM-generated memory hooks per word: mnemonic, cognates (11 languages), collocations, usage context, fun fact. JIT on introduction. `memory_hooks_json` on Lemma. Personal notes still future.
- Future: "Add personal note" per word/sentence for custom mnemonics (in addition to LLM-generated hooks)

### Content Quality
- [DONE] Flag system: user flags suspicious content → background LLM (GPT-5.2) evaluates and auto-fixes
- [DONE] Activity log: tracks flag resolutions, batch job results, backfills
- Future: periodic quality sweep — run all glosses through LLM evaluation proactively
- Future: track which import source produces the most flags → surface data quality insights
- Future: crowd-source corrections if multi-user (far future)

### Word List Browser
- Filter by: knowledge state, POS, root, frequency, source
- Sort by: due date, frequency, alphabetical
- Search by Arabic or English
- Bulk operations: mark known, mark for review, delete
- [DONE] Sort by review status: failed words first (red tint + border), then passed (green), then unseen
- [DONE] Show review stats per word: "Seen 3x · 2 correct · 1 failed" with colored counts
- [DONE] "Reviewed" filter chip, knowledge score display, refresh on tab focus
- [DONE] Search icon + clear button, horizontally scrollable filter chips, full state names in badges
- [DONE] Two-column compact grid layout with review sparklines on word cards
- [DONE] Category tabs: Vocabulary / Function / Names (with proper noun rendering)
- [DONE] Smart filter tabs: Leeches (high review, low accuracy), Struggling (recent failures), Recent (newly learning), Solid (high score), Next Up (learn algorithm candidates)
- [DONE] Next Up tab: shows learn algorithm's top 20 candidates with score breakdown (frequency, root familiarity, known siblings)
- Shared design system: extract common card/button/badge styles into theme.ts or shared components to prevent screens drifting apart visually

### Text Reader View
- Display Arabic text with word-level tap interactions
- Color-code words: known (green), learning (yellow), unknown (red)
- Tap unknown word → add to learning queue
- Show difficulty score for the text
- Track reading progress

---

## Data & Analytics

### Interaction Logging
- Log every interaction in JSONL format
- Fields: timestamp, event type, lemma/word ID, rating, response time, context, session ID
- Append-only log files, partitioned by date
- Essential for: algorithm tuning, learning curve analysis, identifying problem words
- [DONE] JSONL events: session_start, sentence_selected, sentence_review, tts_request, legacy_review
- [DONE] DB tables: review_log (per-word with credit_type, sentence context), sentence_review_log (per-sentence)
- [DONE] Fixed: /sync endpoint (offline queue) now also writes JSONL logs (was only writing to DB)
- [DONE] Enriched logging: ai_ask logs question text, quiz_review logs comprehension_signal, sentence_review logs per-word rating map + audio_play_count + lookup_count, story complete/skip/too_difficult logs word counts + reading_time_ms, review_word_lookup logs word details + root
- [DONE] Test data separation: interaction logger skips when TESTING env var is set (conftest.py sets it); logger tests use autouse fixture to temporarily re-enable
- [DONE] FSRS stability floor: cards labeled "known" with stability < 1.0 get relabeled to "lapsed"
- [DONE] Session ID consistency: backend now generates full UUIDs (was 8-char truncated), frontend uses backend's session_id instead of replacing it. Reviews now correlate to session_start events in logs.
- [DONE] Story event logging: all story log_interaction calls now use proper keyword args (story_id, surface_form, position) instead of embedding in context string
- [DONE] ULK provenance tracking: source field on UserLemmaKnowledge distinguishes study (Learn mode), auto_intro (inline review), collocate (sentence gen), duolingo (import), encountered (collateral credit in sentence review). introduce_word() accepts source param.

### Analytics Dashboard
- Words learned over time (cumulative)
- Review accuracy by category (POS, frequency band, root family)
- Time per review card
- Retention curves
- Root coverage: % of top-N roots mastered
- Predicted vocabulary size
- [DONE] CEFR arrival prediction: "~X days at this week's pace" on the CEFR card (weekly + today's pace extrapolation)
- [DONE] Book pages equivalent: total words reviewed / 200 = pages read this week
- [DONE] Unique words recognized: distinct lemmas with correct ratings this week, with delta vs. prior week
- [DONE] Story completion prediction: "~Xd until ready" per active story based on word graduation rate

### Simulation-Driven Analysis
- [DONE] Multi-day simulation framework: drives real services against DB copy, profiles (beginner/strong/casual/intensive), freezegun time control
- Run simulations after algorithm changes to predict impact before deploying
- Compare simulation outcomes across profiles to find "sweet spot" parameters
- Use simulation CSV output to generate matplotlib charts (review load curves, state transition Sankey diagrams)
- Add "adversarial" profiles: always-wrong student, always-skip student, binge-then-vanish student
- Simulate specific scenarios: e.g., vary MAX_ACQUIRING_WORDS (now 40), test different graduation thresholds

### Algorithm Optimization
- Use logged data to tune FSRS parameters per-user
- Identify words that are consistently hard → provide extra context/examples
- Detect if difficulty ratings are miscalibrated
- A/B test different presentation modes (with logging data)
- **Response time as difficulty signal**: response_ms is already captured for reading/listening reviews (stored in ReviewLog + SentenceReviewLog + JSONL logs) but never used. Possible uses: slow response → word is harder (could influence FSRS scheduling or sentence selection), decreasing response time over repeated reviews of same word = fluency/acquisition signal, analytics dashboard showing time-per-card trends. Caveat: response time is noisy (distracted vs. genuinely struggling), best as supplementary signal alongside ratings.
- [DONE] **Learn mode quiz timing gap**: `frontend/app/learn.tsx` hardcoded `response_ms: 0` for quiz reviews — fixed with `quizStartTime` ref that measures actual elapsed ms

---

## Technical Ideas

### Import Enrichment Pipeline
- [DONE] Automatic enrichment (forms, etymology, memory hooks, transliteration) after book/story import via background task
- [DONE] Cron catch-all for unenriched lemmas (update_material.py Step E)
- [DONE] Dictionary-form gloss prompt (no more contextual "she woke up" glosses)
- Frequency rank lookup during import (would need CAMeL frequency data bundled or accessible)
- CEFR level estimation during import (currently only via SAMER backfill)

### Frontend Testing
- [DONE] Jest + ts-jest test infrastructure with mocks for AsyncStorage, expo-constants, netinfo
- [DONE] Sync queue tests (enqueue/remove/pending/dedup)
- [DONE] Offline store tests (mark/unmark reviewed, session cache, invalidation, story lookups)
- [DONE] Smart filter logic tests (leech/struggling/recent/solid detection with boundary cases)
- [DONE] API interaction tests (sentence review submit/undo, word lookup caching, story ops, learn mode, flagging, offline fallback)
- Component-level tests with React Testing Library (render review cards, word list, story reader in various states)
- Snapshot tests for key UI states (empty, loading, error, populated)
- E2E tests with Detox or Maestro for critical user flows (review session, learn flow, story import)

### Offline Architecture
- All review data in IndexedDB (web) / SQLite (mobile)
- Pre-sync: download next N days of review cards + sentences + audio
- Background sync when online: upload logs, download new content
- Service worker for web PWA caching
- Expo offline-first with AsyncStorage or expo-sqlite
- [DONE] Clear Cache button in More screen: flushes sessions, word lookups, stats, analytics from AsyncStorage
- [DONE] Server-side sync glitch detection: reject batches with >10 sub-500ms reviews from one session (prevents FSRS corruption from rapid-fire sync bugs, April 2026 incident)
- [ ] Frontend debounce/guard against rapid-fire review submissions — server-side detection catches the symptom, but the root cause (a stuck gesture or auto-advance loop) is still possible

### Deployment
- [DONE] Backend: Hetzner Helsinki, direct docker-compose (Coolify removed — too complex for single-user app)
- Fly.io as alternative (~$7-8/mo with persistent volume for SQLite)
- Pre-process everything server-side, client only needs processed data
- Consider edge functions for simple lookups

### Data Sources to Integrate
- CAMeL Lab MSA Frequency Lists (11.4M types)
- KELLY project (CEFR-tagged Arabic)
- Arabic Roots & Derivatives DB (142K records, 10K+ roots, CC BY-SA)
- [DONE] Kaikki.org Wiktionary (57K Arabic entries, JSONL) — import_wiktionary.py streams the 385MB JSONL, filters nouns/verbs/adj, imports top N
- [DONE] AVP A1 dataset (~800 validated A1 Arabic words) — import_avp_a1.py scrapes from lailafamiliar.github.io
- Arramooz dictionary (SQL/XML/TSV)
- Tashkeela (75M diacritized words)
- UN Parallel Corpus (20M pairs)
- Buckwalter Morphological Analyzer (83K entries, GPL-2.0)

### API Strategy
- Farasa REST API — free morphology/diacritization (research use only)
- Azure Translator — 2M chars/month free
- Google Cloud TTS — 1M chars/month free (MSA voices)
- LibreTranslate — self-hostable, unlimited
- HuggingFace Inference — free tier for AraBERT/CAMeLBERT

---

## Patterns from Other Projects

### From Bookifier (content-hash caching, glossed PDFs)
- **Content-hash caching for LLM outputs**: Cache sentence translations and generated sentences by SHA256 hash of (input + model + prompt_version). Avoids regenerating identical content. Use SQLite cache table with content_hash as primary key.
- **WeasyPrint for glossed PDFs**: Generate Arabic reading PDFs with CSS page footnotes for glosses. WeasyPrint supports `float: footnote` CSS for scholarly annotations. Perfect for annotating unknown words in a text.
- **Stage-based processing**: Independent pipeline stages (extract → translate → annotate → assemble) with JSON intermediate outputs. Each stage can be inspected/adjusted independently.
- **Vocabulary extraction per paragraph**: When processing a text, extract 2-3 difficult words per paragraph with definitions. Store in vocabulary_json field.
- **Bilingual EPUB generation**: Side-by-side original + translation with highlighted vocabulary and clickable glossary anchors.
- **Rate limiting with Bottleneck**: Use bottleneck library pattern for API rate limiting (max concurrent + min time between requests).

### From Comenius (production schema, ingestion, offline sync)
- **Drizzle ORM schema**: Normalized: Languages → Lemmas → Senses → Surface Forms → Inflections → Sentence Tokens. Consider adopting for Phase 2.
- **Book Bundle protocol**: Server queries only lemmas/inflections relevant to a specific text. Client syncs only what's needed. Critical for keeping mobile app lightweight.
- **Gemini JSON schema enforcement**: `responseMimeType: 'application/json'` + `temperature: 0` + `topK: 1` for deterministic, validated JSON output from LLM.
- **SM-2 scheduler as pure function**: Immutable `advanceReviewState(state, outcome, now)` — same pattern for our FSRS wrapper. Pure, testable, no side effects.
- **AsyncStorage + interaction queue**: Buffer offline changes, sync when online. Silent background sync.
- **Intl.Segmenter for sentence splitting**: Native API with fallback regex. Add Arabic punctuation (U+061F, U+060C, U+061B).

### From NRK/Kulturperler (LiteLLM, multi-model, logging)
- **LiteLLM unified API**: Single `call_with_search()` function wrapping Gemini + GPT with automatic fallback, retry, and exponential backoff.
- **API call logging**: Log every LLM call with provider, response time, success/failure, prompt hash. Essential for cost tracking and debugging.
- **Proposal-based data changes**: For curated content, use a proposal → review → apply workflow instead of direct edits.

### From Ninjaord (ElevenLabs patterns)
- **REST API over SDK**: Direct fetch to `https://api.elevenlabs.io/v1` with `xi-api-key` header. Simpler, fewer dependencies.
- **Audio provider fallback**: ElevenLabs → Browser Web Speech API fallback chain.
- **Voice selection UI**: Load voices from API, filter by language, let user pick and test.

### Sentence Validation Improvements (discovered during implementation)
- [DONE] **Suffix/clitic handling in validator**: Rule-based clitic stripping implemented in sentence_validator.py. Handles proclitics (و، ف، ب، ل، ك، وال، بال، فال، لل، كال), enclitics (ه، ها، هم، هن، هما، كم، كن، ك، نا، ني), and taa marbuta (ة→ت). CAMeL Tools will improve accuracy further.
- **Morphological pattern matching**: Instead of exact bare form matching, match words by root + pattern. E.g., if user knows "كتاب" (kitāb), they likely can parse "كتب" (kutub, plural) and "مكتبة" (maktaba, library). This requires root extraction from CAMeL Tools.
- **Sentence difficulty scoring**: Beyond word-level validation, score sentences by syntactic complexity (clause depth, verb forms used, agreement patterns). Could use sentence length + unknown-word ratio as simple proxy.
- **Multi-sentence generation**: Generate 2-3 variant sentences per target word in one LLM call to reduce API calls and provide variety.
- [DONE] **Negative examples in prompt**: Include words the LLM should NOT use (recently failed unknown words from previous attempts) to make retries more effective — implemented as `rejected_words` param in `generate_sentences_batch()`, fed back from validation failures in `update_material.py`

## Future / Speculative Ideas

- Dialect support: track MSA vs. Levantine/Egyptian/Gulf vocabulary separately
- Reading difficulty predictor: given a URL, estimate how ready the user is to read it
- Browser extension: highlight unknown words on any Arabic webpage
- Anki export: generate Anki decks from the app's word database
- Social features: share word lists, compare progress (far future, if ever)
- Handwriting recognition: practice writing Arabic letters (contradicts reading-only focus, but useful for letter learning)
- Grammar drills: sentence transformation exercises (passive, negation, etc.)
- Cloze deletion: show sentence with one word blanked, user guesses from context
- [REMOVED] Collocations: reactive collocate auto-introduction — was auto-introducing words during sentence generation. Removed because it flooded the user with 24 unfamiliar words in one evening (Feb 8 2026), cratering next-day comprehension to 10% understood. Word introduction should be user-driven via Learn mode only.
- Collocations — proactive: build explicit prerequisite graph so collocated words are learned together (e.g. يوم before day-name words). Could be auto-discovered from generation failures or manually curated. Better approach than reactive auto-introduction which flooded the user.
- Collocations — suggestion-based: when sentence generation fails due to unknown collocate, surface the collocate as a "suggested next word" in Learn mode rather than auto-introducing it. Track which target words are blocked by which collocates.
- Arabic-to-Arabic definitions: as level increases, use Arabic definitions instead of English
- Morphological pattern drills: given root + pattern → predict meaning
- Spaced reading: schedule re-reading of texts at increasing intervals
- Vocabulary prediction: estimate total passive vocabulary from tested sample (like a placement test)

### Ideas from Agent Knowledge Systems Research (2026-02-21)

#### Research Document Management
- Semantic search index over `research/*.md` — embed all docs, query "what does the research say about X?" via vector similarity. Could use Claude CLI + local embedding or a lightweight library like chromadb.
- Research claim extraction: run Ars Contexta's "Reduce" step on existing research docs — extract atomic claims with source attribution, making research searchable at claim level instead of document level.
- Automated freshness checker: cron script comparing CLAUDE.md claims against actual codebase (file existence, function signatures, config values). Flags staleness.

#### CLAUDE.md Improvements
- Scoped sections with activation markers (`<!-- scope: backend -->`) so agents working on frontend don't load backend-specific rules. Or split into CLAUDE-backend.md / CLAUDE-frontend.md.
- Temporal tracking: when design decisions change, archive old decisions (not delete). Experiment log partially does this; CLAUDE.md overwrites.
- Contradiction detection: periodic scan for CLAUDE.md assertions that conflict with codebase reality.

#### Agent Memory Improvements
- Memory consolidation sessions: periodic "sleep-time" passes (Letta's pattern) where agent reviews MEMORY.md, prunes outdated entries, consolidates related items.
- Memory quality feedback: after each session, agent rates which memories were useful. High-utility memories get priority; low-utility get archived.
- Cross-session knowledge transfer: identify learnings from Alif that apply generally (deployment patterns, SQLite gotchas, etc.) and surface them in user-level `~/.claude/CLAUDE.md`.

#### Research Hub Enhancements
- Progressive disclosure: Layer 1 (one-line summary, always visible) → Layer 2 (key findings, click to expand) → Layer 3 (full analysis) → Layer 4 (raw sources). Currently only 2 levels.
- Research claim graph: visualize connections between research docs as a knowledge graph. Which findings support/contradict each other?
- Spaced resurfacing of research claims: periodically re-present key findings for human review — catch stale or superseded knowledge. Inspired by Andy Matuschek's spaced repetition of notes.

### Ideas from Personal Research Knowledge System Design (2026-02-22)

#### Claim-Based Knowledge Management
- Claim extraction agent: LLM processes any source (article, paper, opinion piece) → structured claims with source attribution, confidence, type (factual/causal/evaluative). Store in Obsidian as typed markdown files with structured frontmatter.
- Contradiction detection pipeline: embed each new claim, semantic search against existing claims, LLM classifies relationship (supports/contradicts/refines/extends). Surface contradictions for human review.
- Bi-temporal claim tracking (from Zep/Graphiti): every claim has `t_valid_from`/`t_valid_until` (when true in world) AND `t_created`/`t_expired` (when known to system). Never delete, always deprecate.
- IBIS-structured research questions: Issue (research question) → Positions (competing answers) → Arguments (evidence pro/con). Maps naturally to academic research debates.
- Argdown argument maps: LLM generates Argdown plain-text syntax for argument visualization. Obsidian plugin renders. Most agent-friendly argumentation format.

#### Research Capture & Monitoring
- Readwise Reader + MCP as universal capture layer ($10/mo): articles, PDFs, podcasts, newsletters. All highlights auto-sync to knowledge base. Agent can both read and write via MCP.
- Zotero + MCP for academic papers: zotero-mcp (590+ stars) adds vector-based similarity search over paper library. Free.
- RSS monitoring pipeline: n8n (self-hosted, free) + Claude/Gemini summarization. Monitor Norwegian news (Utdanningsnytt, Klassekampen), academic journals. Auto-tag and write to inbox.
- Semantic Scholar API as research infrastructure: 225M papers, citation graphs, SPECTER2 embeddings, free API. Multiple MCP servers exist.
- Periodic digest agent: cron-driven scan for new connections, contradictions, newly dense topic clusters. Produces notification feed.

#### Rich Knowledge Interfaces
- gwern-style recursive popups: hover any link → preview with annotation/abstract. Popups contain hoverable links. "Iceberg" pages — simple surface, arbitrary depth. Best progressive disclosure pattern for knowledge browsing.
- Epistemic status markers (from Maggie Appleton's digital garden): Seedling → Budding → Evergreen on every claim/note. Trust calibration for both human and agent readers.
- Andy Matuschak-style stacked panes: clicking a link opens target in new pane to right, preserving exploration chain. See 3-5 notes simultaneously.
- BERTopic clustering for automatic topic discovery: periodically re-cluster claims by embedding. Generate Maps of Content. Useful at 1K+ claims.
- Quartz for publishing Obsidian vault as browseable website with graph view, backlinks, search.

### Ideas from Memory Hook Research (2026-02-22)

#### Overgenerate-and-Rank Mode
- [DEFERRED] Generate 5+ keyword candidates per word, rank by imageability × phonetic overlap × coherence, keep best. Lee et al. EMNLP 2024 showed this outperforms single-shot. Higher token cost but dramatically better quality. Could be a "high quality" backfill mode.

#### Mnemonic Image Generation
- [DEFERRED] Use text-to-image (DALL-E, Midjourney) to generate actual images for the interactive scenes. SmartPhone (AIED 2023) showed combining verbal + visual mnemonics improves retention. Would add visual card to Learn Mode.

#### Mnemonic Quality Feedback Loop
- Track which words the user struggles with despite having a mnemonic. If a word gets ≥3 "no_idea" ratings with an existing hook, flag for regeneration with a note "previous mnemonic didn't stick."
- SMART (Balepur EMNLP 2024) finding: what students think helps and what actually helps disagree (0.4-0.5 agreement). Track observed learning outcomes, not self-reported preferences.

#### PhoniTale-Style IPA Matching
- [DEFERRED] Use IPA-based phonological adaptation instead of relying on the LLM for keyword selection. PhoniTale (EMNLP 2025) showed this works better for typologically distant language pairs like Arabic-English. Would require IPA transcriptions of all Arabic words (could generate via LLM or use existing pronunciation dictionaries).

### Ideas from Arabic Linguistic Challenges Research (2026-02-08)

#### Root Explorer UI
- [PARTIAL] Root Explorer as a first-class feature: tap any root to see a tree/map of all derivatives organized by pattern type (agent nouns, place nouns, verb forms, etc.) — backend API done (`/api/patterns/roots/{root_id}/tree`), frontend explorer screen TODO
- Color-code words in reader view by root family (subtle background tint) to build unconscious root awareness
- "Root discovery" celebrations: when user learns 3rd word from a new root, show root family and how many more words they can now partially understand
- Root productivity ranking: prioritize teaching high-productivity roots (most common derivatives) first

#### Pattern-Based Learning Acceleration
- Pattern bonus in SRS: after user knows N words following same wazn (e.g., maf'al = place), reduce initial difficulty for new words with that pattern
- Verb form semantic labels in UI: always show "Form II = intensive/causative" next to verb form number
- Broken plural pattern grouping: review broken plurals in pattern clusters (fu'ul, af'al, etc.) rather than individually
- Masdar (verbal noun) pattern teaching: Forms II-X have predictable masdars; only Form I masdars need individual memorization

#### Diacritics Training System
- 4-level progressive diacritics mode: (a) full tashkeel, (b) no case endings, (c) ambiguous/unknown words only, (d) bare text
- Diacritics independence assessment: periodically present undiacritized versions of known words to measure reading ability without crutch
- Partial diacritization based on user knowledge: show tashkeel only on words the user has not yet mastered

#### Phonological Training for Listening
- Minimal pair exercises for emphatic consonants: ص/س, ض/د, ط/ت, ظ/ذ
- Pharyngeal consonant training: ع/ا and ح/ه discrimination drills
- Sun/moon letter assimilation highlighting: visually show assimilation of lam in definite article
- Confusion pair tracking: when user confuses two phonologically similar words, link them and schedule targeted review of both

#### Grammar Concept Tagging
- [DONE] Tag every sentence with grammar concepts it illustrates — implemented via grammar_tagger.py (LLM-based) and sentence_grammar_features table
- [DONE] Grammar concept progression: 5-tier system (Tier 0 always available → Tier 4 requiring comfort ≥ 0.5) with comfort score formula based on exposure, accuracy, and recency decay
- [DONE] Grammar familiarity tracking: user_grammar_exposure table tracks times_seen, times_correct, comfort_score per feature

#### Register-Aware Content
- Tag all content by MSA register (news, literary, religious, academic, everyday)
- Register selection in onboarding: let user choose primary interest
- Register-specific frequency ranks: a word common in news Arabic may be rare in literary Arabic
- Gradual register expansion as proficiency grows

#### Conjugation Transparency
- Regular conjugations of known verbs should NOT count as separate vocabulary items
- Track conjugation pattern familiarity separately (does user recognize 3rd person feminine plural?)
- Only create explicit review cards for irregular verb forms (hollow, defective, doubled, hamzated)
- Mini conjugation tables available on tap for any verb in context

#### Function Word Bootstrap
- [DONE] **All words are now learnable**: FUNCTION_WORDS set emptied — prepositions, pronouns, conjunctions, demonstratives all get full FSRS tracking. No words excluded from sentence generation or review credit. FUNCTION_WORD_FORMS kept for clitic analysis prevention, FUNCTION_WORD_GLOSSES kept as fallback.
- [DONE] **Grammar particle info**: 12 core particles (في، من، على، إلى، عن، مع، ب، ل، ك، و، ف، ال) have rich grammar info (meaning, examples, grammar notes) shown in WordInfoCard via `grammar-particles.ts`.
- [REJECTED] Exclude function words from "unknown word" count — all words should be treated equally. The learner wants to track their knowledge of all words including particles.
- [REJECTED] Pre-load as Phase 1 — automated introduction handles this naturally via frequency-based ordering.

#### Writing System Features
- Hamzat al-wasl vs al-qat' visual distinction in reading mode (gray out hamzat al-wasl to show it's elided)
- Ta' marbuta pronunciation context indicator (show when /t/ is pronounced vs. silent)
- Letter similarity overlay: option to highlight dot-differentiated letter pairs for beginners
- Font selection prioritizing maximum letter distinctiveness (especially ة/ه, ى/ي)

#### Story and Extensive Reading Mode
- [DONE] Generated story mode: LLM generates 4-8 sentence stories using only known vocabulary, validates all words
- [DONE] Imported story mode: paste any Arabic text, app analyzes known/unknown words, calculates readiness percentage, tracks learning progress toward reading the story
- [DONE] Story reading UI: full-screen Arabic with word-level tapping, fixed translation panel, Arabic/English tab toggle
- [DONE] Story completion: complete (FSRS credit for all words), skip (no effect), too difficult (mark for later)
- [DONE] Story list with readiness indicators (green/yellow/red), generate + import buttons
- [DONE] Story list design polish: bottom-sheet modals, larger Arabic titles (24px), icon badges, refined card layout
- [DONE] Story reader declutter: moved Complete/Skip/Too Hard from fixed bottom bar to end of scroll content, maximizing reading space
- [DONE] Completed stories archival: completed stories shown in collapsed group at bottom of stories list, expandable on tap with chevron indicator. Keeps active/suspended stories prominent.
- [DONE] Morphological fallback for story word lookup: CAMeL Tools analysis resolves conjugated forms (قالت→قال) that clitic stripping misses
- [DONE] Story import creates Lemma entries for unknown words (CAMeL + LLM translation). No ULK — words become Learn mode candidates with story_bonus priority. Completes the import → learn → read pipeline.
- [DONE] Story completion auto-creates ULK: all words get FSRS credit, not just words with existing knowledge records
- [DONE] Word provenance: word detail screen shows "From story: [title]" / "From textbook scan" badge with tap-to-navigate
- [DONE] Expand forms_json to include all verb conjugation paradigms — `_generate_verb_conjugations()` in Pass 3 of `build_lemma_lookup()` generates ~33 conjugation forms per verb from past base + present stem. Sound verbs fully covered; weak verbs partial.
- Graded text mode supporting 95-98% vocabulary coverage for extensive reading
- Narrow reading: offer multiple texts on the same topic to recycle vocabulary
- Story series with recurring characters/themes for context building
- Three-stage listening reveal: audio only -> Arabic text -> English translation
- Story difficulty auto-selection: pick stories where readiness is 85-95% for optimal learning
- Story audio: TTS for full story, sentence-by-sentence playback with highlighting
- Story sharing: export stories as formatted PDF with glossary of unknown words
- [DONE] **Switch story generation to Claude Opus** — benchmark showed Opus produces 4.3 composite (vs 2.6 OpenAI). Implemented with retry loop and compliance validation. `model_override="opus"` in story_service.py.
- Cross-model two-pass story pipeline: Sonnet generates freely (best narrative quality), Gemini Flash rewrites for vocabulary compliance — not yet tested but promising given that Sonnet scored 4.75 composite (highest) but only 33% compliance
- [DONE] **Story retry loop** — MAX_STORY_RETRIES=3, feeds back unknown words as correction prompt. Compliance threshold 70%.
- Story quality gate: add Gemini Flash review (grammar + translation accuracy) like sentences have
- [DONE] Expand forms_json with verb conjugation paradigms — solved via `_generate_verb_conjugations()` Pass 3. Remaining gap: weak verb irregular stems (قلت from قال, مشيت from مشى) and noun inflections (sound feminine plural ـات, sound masculine plural ـون/ـين).
- Recurring character universe for stories: pre-define characters (سمير، ليلى، عمر) with traits. Models produce more coherent stories with established characters.
- [DONE] **Include acquiring words in story vocabulary** — `_get_known_words()` now includes knowledge_state='acquiring'. Acquiring words highlighted as reinforcement targets in prompt.

#### Claude Code CLI as Free LLM API (2026-02-14)
- [DONE] **`claude -p` wrapper** (`claude_code.py`): `--tools ""` + `--json-schema` + `--no-session-persistence` for reliable structured output via Max plan
- [DONE] **Story generation via `claude -p`** (`scripts/generate_story_claude.py`): local/free story generation with compliance validation and retry
- [DONE] **Validator-in-the-loop sentence generation** (`scripts/generate_sentences_claude.py`): Claude reads vocab file, generates sentences, runs `validate_sentence_cli.py` to self-validate, and self-corrects — all in one session. Uses `generate_with_tools()` with `--tools "Read,Bash"`.
- [DONE] **Batch sentence quality audit** (`scripts/audit_sentences_claude.py`): Claude reads active sentences + vocabulary, reviews each for grammar/translation/compliance, runs validator, and outputs retire/fix/ok report.
- [DONE] **Tool-enabled sessions** (`generate_with_tools()` in `claude_code.py`): `--tools "Read,Bash"` + `--dangerously-skip-permissions` + `--add-dir` for multi-turn agentic sessions with file reading and script execution.
- Use `claude -p` for **batch etymology generation** — Opus may produce richer etymologies than Gemini Flash, free with Max plan
- Use `claude -p` for **grammar lesson generation** — currently uses Gemini, Opus might produce more pedagogically sound lessons
- Use `claude -p` for **story-to-sentences pipeline** — generate a story, then chop into review sentences, all with Opus quality
- Use `claude -p` with `--model sonnet` for **high-volume tasks** where Opus quality isn't needed but free access matters (e.g. batch translations)
- Consider **Claude Code SDK** (`@anthropic-ai/claude-code`) for Node.js integration if CLI subprocess overhead becomes an issue
- Install `claude` on Hetzner server via `claude setup-token` for server-side free generation without API costs

### Ideas from Cognitive Load Theory Research (2026-02-08)

#### Sentence Difficulty Scaling by Word Maturity
- [DONE] Tie sentence complexity directly to FSRS stability of the target word — implemented in sentence_selector.py difficulty matching formula (stability < 1d → scaffold stability > 7d; stability 1-7d → scaffold avg > 14d; stability > 7d → mixed OK)
- Store a "sentence difficulty tier" (1-5) on generated sentences and select appropriate tier based on the target word's FSRS state
- [DONE] Sentence validator should check not just that surrounding words are "known" but that they have FSRS stability > 14 days for sentences containing new words — implemented via difficulty_match_quality scoring in greedy set cover
- Consider using CAMeL token count (after clitic separation) rather than raw word count for sentence length targets, since Arabic agglutination makes raw word count misleading

#### Comprehension-Aware Sentence Recency
- [DONE] Sentences the user struggled with should reappear sooner: replaced fixed 7-day cooldown with comprehension-based cutoffs — "understood" sentences wait 4 days (was 7d, reduced 2026-02-18 due to sentence pool depletion), "partial" wait 2 days, "no_idea" wait 4 hours. Uses last_comprehension column on Sentence model, checked in sentence_selector.py candidate filtering.

#### All Words Get FSRS Credit in Sentence Reviews
- [DONE] Every word seen in a sentence now gets a full FSRS card and enters the normal review process. Previously, words without existing knowledge records only got encounter tracking (total_encounters increment). Now fsrs_service.submit_review() auto-creates UserLemmaKnowledge records for unknown words, and sentence_review_service calls submit_review() for every lemma_id in the sentence (not just those with existing cards).

#### Adaptive Session Pacing
- Track rolling accuracy over the last 10 items during a session; if it drops below 75%, automatically pause new word introductions and show only easy review items until accuracy recovers above 85%
- Track response time as a cognitive load signal: if average response time increases beyond 2x the learner's rolling average, treat it as overload even if accuracy is maintained
- Default to 5 new words per 20-minute session for Arabic (conservative, research-backed); allow learner to adjust but show guidance about cognitive load tradeoffs
- After 3 consecutive "Again" ratings on different items: insert 5 easy review items as a cognitive "rest stop"
- If learner continues a session beyond 20 minutes, show only review items (no new introductions in "overtime")
- Session-level accuracy trend tracking: compare first-half vs. second-half accuracy; if second half degrades, suggest shorter sessions in settings

#### New/Review Item Interleaving
- [DONE] Inline intro candidates in review sessions: build_session() suggests up to 2 intro candidates at positions 4 and 8, gated by accuracy > 75% over last 20 reviews and minimum 4 review items. Reading mode only (no intros in listening). **Candidates are suggestions only** — not auto-introduced at session fetch time. User must accept via Learn mode.
- Never show two new word introductions back-to-back -- always interleave with 4-6 review items between new introductions
- Start each session with 3-4 easy review items (FSRS stability > 30 days) as warm-up before any new items
- End each session with 3-4 easy review items for positive session closure (recency effect protects motivation)
- Distribute new items evenly throughout the session rather than front-loading them
- Maintain a 1:4 to 1:6 ratio of new to review items throughout the session

#### Flashcard-First Introduction Flow
- [DONE] Auto-generate sentences + audio on word introduction: /api/learn/introduce now triggers background generation of up to 3 sentences + TTS audio when a word is introduced
- [DONE] Quiz results now feed FSRS: learn-mode quiz "Got it" → rating 3, "Missed" → rating 1 via /api/learn/quiz-result endpoint
- Introduce new words initially as isolated flashcards (word + transliteration + gloss + root + audio) before embedding them in sentences -- isolated word pairs have low element interactivity (Sweller), allowing form-meaning mapping before the higher-load sentence processing task
- First sentence review should come only after the initial flashcard introduction succeeds (rated Good or Easy)
- Consider a two-step learning flow: step 1 = flashcard with root info, step 2 = simple sentence with strong context clues, step 3 = varied sentence contexts in subsequent reviews

#### Within-Session Spacing for Failed Items
- If a word is rated "Again," re-show it after 5-10 intervening items rather than immediately -- leverages spacing effect even within a single session
- If the same word fails twice in one session, do not show it again in that session; let FSRS schedule it for the next session to avoid frustration and wasted working memory

#### Expertise Reversal Awareness
- As the learner advances, progressively reduce scaffolding: offer transliteration as tap-to-reveal rather than always-visible, increase default sentence complexity, reduce auto-display of root/morphology info
- Optionally reduce diacritization on well-known words (FSRS stability > 60 days) as an advanced reading challenge
- Track when scaffolding reduction is appropriate based on accuracy patterns, not just vocabulary count

#### Sentence Context Quality Labels
- Distinguish between "informative context" (sentence provides clues to word meaning) and "opaque context" (word must be recalled from memory) -- both are useful at different learning stages
- For newly introduced words: generate sentences with informative context (the surrounding words should help the learner infer the target word's meaning)
- For mature words: generate sentences with opaque context (the learner must recall the meaning from memory, not from contextual clues) -- this is a desirable difficulty that strengthens long-term retention
- Tag generated sentences with context informativeness so the appropriate type can be selected based on word maturity

#### Generation/Prediction Effect
- [DONE] Before revealing a new word's meaning, offer the learner a chance to predict it from root knowledge or morphological patterns — implemented as front-phase word lookup during sentence review: tapping an unknown word checks if it has 2+ known siblings from the same root, and if so shows root + siblings with "Can you guess?" prompt before revealing meaning. Uses GET /api/review/word-lookup/{lemma_id} endpoint with root family + knowledge state.
- Only use prediction prompts when the learner has relevant prior knowledge (known root, known pattern, known cognate); uninformed guessing has no benefit

### Ideas from Confused/Misread State & CAMeL Tools Integration (2026-02-09)

#### Confused/Misread Review State
- [DONE] Triple-tap word marking during review: off → confused (yellow, FSRS rating 2 Hard) → missed (red, rating 1 Again) → off cycle
- [DONE] Backend `confused_lemma_ids` field in sentence review submission, flows through offline sync queue
- Track confusion patterns: which words get confused with which? Could build confusion pairs for targeted review
- Show "frequently confused" words in word detail view
- If a word has a high confusion rate (>30% of encounters marked confused), consider generating sentences that specifically contrast it with the confusable word

#### CAMeL Tools Integration
- [DONE] Replaced morphology.py stub with real CAMeL Tools analyzer (graceful fallback to stub when not installed)
- [DONE] Added `canonical_lemma_id` to Lemma model for variant tracking
- [DONE] Added `variant_stats_json` to UserLemmaKnowledge — tracks per-variant-form seen/missed/confused counts
- [DONE] Root family display filters out variants (canonical_lemma_id IS NOT NULL)
- [DONE] Learn mode word selection filters out variants
- [DONE] Cleanup script: `scripts/cleanup_lemma_variants.py` using CAMeL Tools to detect possessives, inflected forms, definite-form duplicates
- [DONE] DB-aware variant disambiguation: cleanup script now iterates ALL CAMeL analyses (not just top-ranked) and picks the one whose lex matches a lemma already in the DB. Eliminates false positives like سمك→سم (fish→poison) and غرفة→غرف (room→rooms) without needing a large hardcoded never-merge list (reduced from 22 entries to 2). Helper `find_best_db_match()` in morphology.py is reusable for other disambiguation tasks.
- Variant difficulty scheduling: if a specific variant form has a high miss rate (e.g., بنتي missed >50% of encounters), prefer sentences containing that variant to strengthen recognition
- [DONE] CAMeL Tools MLE disambiguator integrated: `get_best_lemma_mle()` in morphology.py, used by OCR pipeline. Single-word MLE for now; sentence-level disambiguation (passing full sentence context) is a future enhancement
- [DONE] Import pipeline improvement: all three import scripts (duolingo, wiktionary, avp_a1) now run CAMeL Tools variant detection as a post-import pass — new lemmas are checked against all existing DB lemmas, variants get `canonical_lemma_id` set immediately. Shared logic in `app/services/variant_detection.py`.

### OCR / Textbook Scanner (2026-02-09)
- [DONE] Gemini Vision OCR for Arabic text extraction from images
- [DONE] Textbook page scanning: upload photos of textbook pages, extract vocabulary words, import new lemmas, mark existing as seen with encounter count increment
- [DONE] Batch upload support: multiple pages at once with immediate response and background processing
- [DONE] Upload history view: list of batch uploads with per-page results (new/existing word counts), expandable to see individual words
- [DONE] Story OCR import: upload image of Arabic text in story import modal, extract text via Gemini Vision, populate text field for standard story import flow
- [DONE] Post-OCR variant detection: after importing new lemmas from textbook scans, run CAMeL Tools variant detection to catch possessives/inflected forms
- [DONE] OCR base_lemma fix: use CAMeL Tools base_lemma from Step 2 morphology for DB lookup (was being computed but ignored, causing conjugated forms to be imported as separate lemmas)
- [DONE] OCR prompt hardening: Step 1 now explicitly requests dictionary base forms, not conjugated/possessive forms
- [DONE] Leech identification script: `scripts/identify_leeches.py` finds high-review low-accuracy words with optional auto-suspend
- Leech auto-detection in FSRS: automatically flag words after N consecutive failures (beyond the current struggling-word re-intro cards)
- [DONE] Root validation guard: shared `is_valid_root()` in morphology.py rejects garbage roots (Latin letters, `#` placeholders, wrong length). Applied to all import paths (OCR, Wiktionary, backfill_roots). Cleanup script fixed 133 affected lemmas from prior OCR imports.
- [DONE] Auto-backfill root meanings: `backfill_root_meanings()` in morphology.py uses LLM to fill empty `core_meaning_en` on roots. Called automatically from all import paths after new root creation.
- [DONE] Dark image auto-enhancement: Pillow brightness/contrast boost for images with mean brightness < 120. Empty OCR results retry with `gemini-2.5-flash-preview` thinking model. Recovered 3 previously empty pages on test book.
- OCR confidence scoring: have Gemini rate its confidence per word, flag low-confidence extractions for user review
- Textbook progress tracking: track which textbook/chapter pages have been scanned, show coverage progress
- OCR for handwritten Arabic: test Gemini Vision on handwritten notes (likely lower accuracy but worth exploring)
- Scan-to-story pipeline: detect whether a scanned page is vocabulary (extract words) or continuous text (extract as story) automatically
- [DONE] Multi-page story scanning: scan multiple pages and stitch the extracted text together as one story — implemented as book import pipeline with parallel OCR + sentence extraction

### Book Import / Children's Book OCR (2026-02-14)
- [DONE] Multi-page OCR pipeline: photograph cover + content pages → parallel Gemini Vision OCR → LLM cleanup/diacritize/segment → LLM translate → Story + Sentences
- [DONE] Cover metadata extraction: Gemini Vision extracts title, author, series, level from cover/title page photo
- [DONE] Book sentences captured as `source="book"` with `story_id` FK, used in review when comprehensible (≥70% known gate)
- [DONE] 1.3x source_bonus in sentence scoring: book sentences preferred over LLM-generated when both cover same due words
- [DONE] Natural learning progression: initially LLM sentences fill gaps → as vocabulary grows, book sentences become comprehensible and replace LLM
- [DONE] Per-page tracking: `page_number` column on Sentence and StoryWord models. Each page processed individually through cleanup_and_segment (not merged). Enables per-page readiness and word prioritization.
- [DONE] Page-based word prioritization: `_book_page_bonus()` in word_selector.py — earlier pages get higher learning priority (page 1 → +1.0, decaying by 0.2 per page, min 0.2). Integrates with existing scoring formula.
- [DONE] Per-page readiness UI: `_get_book_stats()` in story_service returns page readiness (unique new lemmas per page, how many learned). Frontend shows colored pills per page ("p1 ✓", "p2: 3") on book story cards.
- [DONE] Sentences seen/total tracking: book stories show how many of their sentences have appeared in review sessions.
- [DONE] CAMeL morphology resolution for unmapped words: `_resolve_unmapped_via_camel()` resolves conjugated verb forms (e.g. ذَهَبَتْ→ذهب) to existing lemmas during sentence creation.
- [DONE] Image persistence: uploaded book page images saved to `data/book-uploads/<timestamp>/` for retry on failed imports.
- [DONE] Book page detail screen: clickable page pills navigate to `/book-page?storyId=X&page=N` showing already-known word count, new words list (arabic, transliteration, english, status pill), and sentences with seen/unseen indicators. Words tappable → word detail page.
- [DONE] StoryWord surface→lemma fallback lookup: after `_import_unknown_words` creates lemmas, `create_book_sentences` uses StoryWord mappings to resolve conjugated/affixed forms that CAMeL misses. Sentences with remaining unmapped tokens kept (not skipped).
- Page ordering UI: drag-to-reorder page thumbnails before import (currently insertion-order only)
- Progress polling: for longer books (30+ pages), switch from sync to async import with SSE/polling progress updates
- Book library view: separate section in stories list for imported books with page count, sentence count, cover thumbnail
- Re-OCR individual pages: if a page had poor OCR quality, allow re-scanning just that page
- Book series tracking: if multiple books from same series imported, group them and track series progress
- Readability level estimation: use book metadata + vocabulary analysis to estimate CEFR/SAMER level
- Pre-reading vocabulary prep: before starting a book, show a "prep session" of the most critical unknown words
- Book sentence audio: generate TTS for book sentences to enable listening practice with authentic book content
- Archive.org integration: browse/download Arabic children's books directly from Archive.org (Karim Series, Simplified Reading Series, etc.)
- [DONE] Stop skipping sentences with unmapped words: sentences with unmapped tokens are now kept (was: dropped entirely). Added StoryWord surface→lemma fallback lookup after CAMeL resolution. Remaining unmapped tokens get `lemma_id=None` in SentenceWord — sentence still usable for reviewing mapped words.

### Sentence Diversity & Corpus Quality (2026-02-11)
- [DONE] Scaffold freshness penalty: penalize sentences whose scaffold words are over-reviewed
- [DONE] Post-generation diversity rejection: deterministic rejection of sentences with overexposed scaffold words
- [DONE] Sentence retirement: soft-delete old low-diversity sentences via is_active flag
- [DONE] Starter diversity in LLM prompts: discourage هل-default and محمد overuse
- [DONE] ALWAYS_AVOID_NAMES: proper nouns always in avoid list
- [DONE] Sentence pipeline cap: originally hard 300 cap, evolved through 600→800→1000, now replaced by tier-based lifecycle (2026-03-10). Tier-4 sentences actively retired, safety valve at 2000. Pool bounded by review urgency (~200 tier 1-3 words), not vocabulary size.
- Automatic periodic rebalancing: integrate retire_sentences logic into update_material.py as Step 0
- [DONE] Vocabulary-diverse generation: prompt marks "CURRENTLY LEARNING" words, instructs Claude to use acquiring words as supporting vocabulary and vary usage across sentences. Stale sentence rotation via `rotate_stale_sentences.py`.
- Sentence quality scoring dashboard: show diversity metrics on analytics page
- Corpus diversity entropy: track Shannon entropy of word distribution across sentences over time
- [DONE] Sentence length progression: dynamic difficulty via `get_sentence_difficulty_params()` — brand new 5-7 words, same-day 6-9, first week 8-11, established 11-14. Floor raised to min 5 words. material_generator + update_material use dynamic params instead of hardcoded "beginner".
- Context variety scoring: measure how many different sentence patterns each word appears in (not just count)
- Word pair co-occurrence tracking: detect word pairs that always appear together (e.g., كتاب+جميل) and actively break them apart
- LLM fine-tuning: collect rejected sentences as negative examples, use for prompt engineering or RLHF on sentence generation
- [DONE] Gemini Flash quality review gate: post-generation naturalness + translation accuracy check. Catches awkward, nonsensical, or mistranslated sentences before they reach users. Fail-closed since 2026-02-13 (rejects on Gemini unavailability). Integrated into both single-target and multi-target generation paths.
- [DONE] LLM word-lemma mapping verification: `verify_word_mappings_llm()` in sentence_validator.py sends word→lemma pairs to Gemini Flash for correctness check. Enabled via `VERIFY_MAPPINGS_LLM=1`. Sentences with flagged mappings are skipped. **TODO (late Feb 2026)**: monitor skip rate in logs — search for "LLM flagged mapping issues" in backend logs. If >20% of generated sentences are being skipped, the check may be too aggressive and needs tuning.
- [DONE] CAMeL disambiguation in mapping pipeline: `lookup_lemma()` uses `_camel_disambiguate()` (via `find_best_db_match()`) when clitic stripping is ambiguous or as last resort. Al-prefix length guard prevents false matches on short stems.
- [DONE] Lookup dict collision handling (B5): `LemmaLookupDict` tracks collisions, `lookup_lemma()` uses hamza-sensitive matching + CAMeL fallback to disambiguate. A6 batch re-map can now be safely attempted.
- [DONE] LLM disambiguation for ambiguous mappings: `disambiguate_mappings_llm()` in sentence_validator.py. When `lookup_lemma()` produces multiple candidates (collisions or clitic ambiguity), batches them into one Gemini Flash call with sentence context. Runs at generation time before verify_word_mappings_llm.
- [DONE] Flag auto-create lemma: `_auto_create_lemma()` in flag_evaluator.py. When flag evaluation identifies correct lemma not in DB, creates it as `source=flag_autocreate` with encountered state. Future enrichment cron picks them up.
- [DONE] Flag bulk propagation: `_propagate_mapping_fix()` in flag_evaluator.py. After fixing a word_mapping flag, finds other sentences with same bad mapping and LLM-verifies each before fixing (max 50, Claude Haiku).

### Sentence Generation Pipeline Overhaul (2026-02-13)

#### Corpus-Based Sentence Sources
- Import Tatoeba Arabic-English pairs (~12.5K, CC-BY 2.0) as a sentence source — real human-written sentences matched against learner vocabulary
- Import BAREC graded sentences (69K, 19 readability levels, HuggingFace) — needs LLM diacritization + translation but provides difficulty-graded material
- FSI/DLI Arabic courses (public domain, US government) — structured learning content with English translations, thousands of sentences
- Hindawi E-Book Corpus (81.5M words, CC-BY 4.0) — children's literature subset for simpler material
- Efficient vocabulary matching: for any sentence source, classify tokens as known/function/unknown, apply ≥70% comprehensibility gate

#### Dormant Sentence Pool
- Don't discard LLM-generated sentences that fail vocabulary matching — store with `is_active=False` and periodically re-evaluate as vocabulary grows
- Same for corpus sentences: import ALL matching sentences at import time, mark dormant ones that don't yet meet comprehensibility gate
- As vocabulary grows, run a background job to "unlock" dormant sentences (flip is_active when ≥70% comprehensibility is reached)
- Track unlock rate: how many sentences become available per 100 new words learned?

#### Two-Pass "Generate Then Constrain" Strategy
- Research (SRS-Stories, EMNLP 2025) shows two-phase approach beats single-pass vocabulary-constrained generation
- Pass 1: Generate natural sentence with target word, NO vocabulary constraint
- Pass 2: Identify unknown words, ask LLM to rewrite replacing ONLY those words with known alternatives
- Preserves natural sentence structure from Pass 1 while achieving vocabulary compliance
- This is the single highest-impact change backed by academic evidence

#### OCR Textbook Sentence Extraction
- Modify OCR prompt to extract full sentences alongside individual words from textbook pages
- Textbook sentences are pedagogically designed to reuse vocabulary — high-quality bootstrap material
- Sentences need cleanup/diacritization after extraction but are inherently better calibrated than LLM-generated ones
- Could detect whether a scanned page is vocabulary (extract words) or exercise text (extract sentences) automatically

#### Story-to-Sentences Pipeline
- Generate LLM stories from word sets, then chop into individual review sentences
- Stories provide narrative coherence that isolated sentences lack (ref: networkedthought.substack.com "The Language Learning Holy Grail")
- Each story sentence becomes an independent review item while sharing a narrative thread
- Caveat: Storyfier (UIST 2023) found learners using generated stories performed worse at vocabulary recall — engaging plots may encourage reading for plot rather than deep word processing. Sentence-level review may be superior.

#### Vocabulary in LLM Prompts
- [DONE] Fix KNOWN_SAMPLE_SIZE mismatch: increased from 50 → 500. GPT-5.2 compliance jumped 57% → 88% with full vocab in benchmarking. See `research/sentence-investigation-2026-02-13/`.
- [DONE] POS-grouped vocabulary: organize known words by part of speech (NOUNS/VERBS/ADJECTIVES/OTHER). Scored 5.0/5 quality and 87% compliance in benchmarking. Implemented as `format_known_words_by_pos()` in llm.py.
- Scenario-based prompting: use existing `thematic_domain` data to add context hints ("at school", "at a restaurant") which naturally constrain vocabulary

#### Sentence Template Fallback
- Build ~30 Arabic syntactic templates (VSO, SVO, nominal) for deterministic sentence construction
- Use as fallback when LLM generation fails 3+ times for a word
- Templates like: `{SUBJ} {VERB} {OBJ} في {LOC}` filled from known vocabulary by POS
- 100% vocabulary compliance but lower naturalness — safety net, not primary approach
- Now that POS-grouped vocabulary is implemented, templates could leverage POS tags for slot filling
- Investigation report: `research/sentence-investigation-2026-02-13/recommendations.md`

#### Morphological Vocabulary Expansion
- Use CAMeL Tools Generator to expand known lemmas into all valid inflected forms before passing to LLM
- If learner knows root k-t-b, expand to: كتب، يكتب، كتاب، كتب، مكتبة، كاتب
- Dramatically increases usable vocabulary for the LLM while staying within "known" territory
- Already have `forms_json` on lemmas — this data is ready to use

#### Quality Gate Improvements
- [DONE] Change quality gate from fail-open to fail-closed (reject on Gemini unavailability instead of auto-pass) — implemented 2026-02-13
- Separate generation from translation: let LLM focus entirely on Arabic writing quality, translate in a cheap parallel Gemini Flash call
- Chain-of-thought sentence construction: guide LLM through explicit steps (pick scenario → choose pattern → select words → construct sentence)
- [DONE] Prompt overhaul: added explicit rules for indefinite noun starters, redundant pronouns, semantic coherence in compound sentences, beginner-level archaic word exclusion. Lowered temperature from 0.8 to 0.5. Reduced failure rate from 57% to ~10%.
- [DONE] Parallel on-demand generation: ThreadPoolExecutor(max_workers=8) for concurrent LLM calls during session building
- [DONE] Bulk sentence quality audit: `review_existing_sentences.py` script reviews all active sentences, retires failures. Supports --dry-run.
- [DONE] Cross-model quality review: switched quality gate from Gemini Flash self-review to Claude Haiku cross-model review. Self-review has blind spots (same model makes same mistakes). Benchmarked 3 models: Gemini 16%, Haiku strict 40%, Haiku relaxed 12.5%, GPT-5.2 97% (broken). Relaxed prompt focuses on grammar/translation errors, not scenario realism — avoids over-rejecting pedagogically valid sentences.

### Learning Algorithm Overhaul — Acquisition Phase & Focus Cohorts (2026-02-12)

#### Problem Statement
After importing ~100 textbook pages via OCR, 411 words entered the system with automatic rating=3 (Good). FSRS treated these as genuinely known (all 586 active words now show 30+ day stability), but actual review accuracy cratered to 25-46% on subsequent days. The system is spreading reviews across 586 words when the user barely recognizes most of them. 63% of active words have been seen only 0-2 times — well below the 8-12 meaningful encounters research says are needed for stable memory.

#### Acquisition Phase (Pre-FSRS Learning Steps)
- [DONE] Leitner 3-box acquisition system: Box 1 (4h), Box 2 (1d), Box 3 (3d). Two-phase advancement: box 1→2 always allowed (encoding), box 2+ gated on `acquisition_next_due` (consolidation). Graduate after box≥3 + times_seen≥5 + accuracy≥60% + reviews span ≥2 calendar days (GRADUATION_MIN_CALENDAR_DAYS=2). Implemented in `acquisition_service.py`.
- [DONE] Within-session repetition: acquisition words appearing only once get additional sentences. Implemented in `sentence_selector.py`.
- Research: FSRS S₀(Good) = 2.4 days, but a single "Good" for a textbook scan is NOT the same as genuine recall
- Research: "fewer than 6 spaced encounters → fewer than 30% recall after a week"

#### Focus Cohort System
- [DONE] MAX_COHORT_SIZE=100. Acquiring words always included, remaining filled by lowest-stability FSRS due words. Implemented in `cohort_service.py`, integrated into `sentence_selector.py build_session()`.
- Prevents the "spread too thin" problem where 586 words compete for ~100 reviews/day
- User said: "have a group of cards that we've consolidated... and then start adding more cards so that the group grows"

#### Session-Level Word Repetition
- [DONE] Within-session repetition: acquisition words get MIN_ACQUISITION_EXPOSURES=4 sentences each via multi-pass expanding intervals. Session expands up to MAX_ACQUISITION_EXTRA_SLOTS=15 extra cards.
- [REMOVED] Next-session recap: was redundant with within-session repetition (MIN_ACQUISITION_EXPOSURES=4) and bypassed sentence recency filter, causing same-sentence catch-up. Backend endpoint still exists but frontend no longer calls it.
- [DONE] Wrap-up mini-quiz: `POST /api/review/wrap-up` returns word-level recall cards. Frontend not yet implemented.

#### Sentence Generation for Word Sets (Batch-Aware)
- Generate sentences targeting a SET of 3-5 focus words rather than individual target words
- "Create 10 sentences. Each must include at least 2 of these 5 focus words: X, Y, Z, W, V"
- This creates natural cross-reinforcement — seeing word A in a sentence with word B helps both
- More efficient than single-target generation (fewer LLM calls, more diverse sentences)
- The concept of "primary target word" becomes less important — what matters is the set of words the session is focusing on

#### OCR Import Options
- [DONE] **Import as encountered** (now the default): No FSRS card. ULK with knowledge_state="encountered" created. Words appear in Learn mode candidates with encountered_bonus=0.5.
- [DEFERRED] **Import as learned today**: Was the old behavior (FSRS card with Good rating). Removed because it inflated stability.
- [DEFERRED] **Import as learned N days ago**: FSRS card with backdated introduction. May add as option later.
- **Just track vocabulary**: Lemma entry only, no ULK at all. Purely for vocabulary tracking / readiness calculation. User decides later whether to learn.

#### Leech Auto-Management
- [DONE] Auto-suspend: times_seen≥5 AND accuracy<50% → suspend with `leech_suspended_at`. Implemented in `leech_service.py`.
- [DONE] Graduated reintroduction cooldowns: 3d (1st), 7d (2nd), 14d (3rd+) based on `leech_count`. Stats preserved on reintro (cumulative accuracy must genuinely improve). Fresh sentences + memory hooks generated.
- [DONE] Post-review single-word leech check: runs after every review with rating≤2.
- [DONE] Root-sibling interference guard: don't introduce words whose root siblings failed in last 7d.
- [DONE] `leech_count` tracking: incremented on each suspension, drives graduated cooldown delays.
- Track leech cycles: if a word is suspended and reintroduced 3+ times, flag for manual review (leech_count data now available)
- User said: "at that time I might have more hooks in my brain to connect it to, and it might stick better"

#### FSRS State Correction for OCR-Imported Words
- [DONE] `reset_ocr_cards.py`: Resets inflated FSRS cards from textbook_scan imports. 0 real reviews → reset to "encountered"; 1-2 with <50% accuracy → reset; 3+ → replay through FSRS. Supports --dry-run.
- [DONE] OCR import now creates ULK with knowledge_state="encountered" (no FSRS card, no submit_review). Words become Learn mode candidates.
- [DONE] Story completion creates "encountered" ULK for unknown words instead of FSRS cards.
- [DONE] `cleanup_review_pool.py`: Broader reset — ALL words with times_correct < 3 moved back to acquiring. Suspends junk via LLM. Retires incomprehensible sentences (<50% known).

#### Comprehensibility Gate & On-Demand Generation (2026-02-12)
- [DONE] Comprehensibility gate in sentence_selector: skip sentences where <70% of content words are known/learning/acquiring. Prevents showing unreadable sentences.
- [DONE] No word-only fallback cards: due words without sentences get on-demand generation or are skipped.
- [DONE] On-demand sentence generation: MAX_ON_DEMAND_PER_SESSION=10 synchronous LLM calls during session building. Uses current vocabulary for fresher, better-calibrated sentences than pre-generated pool.
- [DONE] Import quality gate: `import_quality.py` — LLM batch filter for junk words on import paths.
- [DONE] Variant→canonical review credit redirect: sentence reviews now credit the canonical lemma, not the variant. Variant forms tracked in variant_stats_json on canonical's ULK for diagnostics.
- [DONE] Deterministic variant ULK cleanup: suspend variant ULK records, merge stats into canonical. Replaces LLM-based junk detection which was incorrectly re-discovering variants.
- [DONE] Quality gate on all import paths: OCR, story import, Duolingo. Wiktionary/AVP skipped (no ULK created).
- [DONE] Fixed story_service variant detection: was calling detect functions without mark_variants().
- [DONE] Variant resolution in sentence_selector: sentences containing variant forms correctly cover canonical due words. Display uses original lemma_id (not canonical) so tapping always shows correct word.
- [DONE] Root validation guard in variant detection: rejects candidate pairs with different root_id before LLM. Prevents worst false variants (كلية→أكل, أميرة→مار, شباك→شب). Audited 191 existing variants, cleared 28 false ones.
- Variant-aware statistics display: show aggregated stats across all variant forms on the word detail page. "You've seen this word as: كتاب (5x), الكتاب (3x), كتابي (1x)"
- [RESOLVED 2026-03-10] **FSRS scaffold over-review**: Resolved itself as vocabulary grew. At 623 known words, 84 words have >30 reviews with >10d stability — harmless. Average known word: 20 reviews. Pool large enough that no word gets disproportionately hammered. No action needed.
- [DONE] **Sentence utilization**: Was 41% never-shown (229/560). Fixed Feb 16: hard 300 cap enforcement (Step 0 in update_material.py), rotation in cron, warm cache cap check. Retired 260 excess → 300 active, 30% never-shown. JIT fills gaps with current vocabulary.
- Adaptive comprehensibility threshold: start at 70%, increase to 80% as vocabulary grows. Early learners need more i+1, advanced need less scaffolding.
- Sentence regeneration trigger: when cleanup retires many sentences, auto-regenerate for words below MIN_SENTENCES=2.
- Pre-warm sentence cache: after cleanup, generate sentences for all active words in background (not during session building).

#### Topical Learning Cycles (Phase 4)
- [DONE] Group words by thematic domain (food, family, school, etc.) and cycle through topics
- [DONE] Each cycle focuses on one domain: introduce up to 15 words (MAX_TOPIC_BATCH), auto-advance when exhausted/depleted (MIN_TOPIC_WORDS=5)
- [DONE] Prevents mixing too many unrelated words (cognitive interference)
- [DONE] Uses existing `thematic_domain` on lemmas from `backfill_themes.py` — 20 domains, all 1610 lemmas tagged
- [DONE] LearnerSettings singleton table, topic_service.py, domain filtering in word_selector, settings API + frontend topic display
- Could auto-select next topic based on story readiness or user preference

#### Story Difficulty Display + Suspend/Activate (Phase 5)
- Show estimated difficulty level on story list cards
- [DONE] Allow suspend/reactivate of stories: toggle via pause/play button on story cards, also available in story reader. Suspended stories appear dimmed with "Suspended" badge. POST /api/stories/{id}/suspend toggles between active↔suspended.
- Story difficulty auto-selection: pick stories where readiness is 85-95%
- Link to story from word detail page when word was encountered in a story but missed

#### Themed Sentence Generation (Phase 6)
- Generate sentences targeting a SET of 3-5 thematically related words rather than individual targets
- "Create 10 sentences about food. Each must include at least 2 of these 5 focus words: X, Y, Z, W, V"
- Natural cross-reinforcement — seeing word A in context with thematically related word B helps both
- More efficient than single-target generation (fewer LLM calls, more diverse sentences)

#### Story Link on Word Detail When Missed
- When a word was encountered in a story and later missed in review, show a link back to the story on the word detail page
- Helps learner reconnect with the original context where they first saw the word
- Uses existing `source_story_id` on Lemma model

#### A/B Testing Framework (Single-Subject)
- Research says: n-of-1 trials need ~400 observations per condition, 4-5 crossover periods, linear regression with AR(1) covariance
- With ~100-200 reviews/day, need 2-4 weeks per experiment
- Design: assign words randomly to condition A/B at introduction. Track recall at days 1, 3, 7, 14.
- First experiment idea: "Acquisition phase with 3x in-session repetition" (A) vs "standard FSRS scheduling" (B)
- Track: accuracy at day 1, day 3, day 7, day 14. If A shows >15% better retention at day 7, adopt.
- Implementation: add `experiment_group` field to ULK, log experiment assignment in interaction logs
- Caveat: interference between groups (seeing word from group A might help group B word from same root)

#### Sparkline Enhancement: Show Inter-Review Gaps
- [DONE] Backend returns `last_review_gaps` (hours between consecutive reviews). Frontend sparkline uses variable gap widths: <1h→1px, same-day→2px, 1-3d→4px, 3-7d→6px, >7d→9px. Clustered dots = cramming, spread dots = real spacing.
- User said: "it doesn't say anything about the gap between attempts"

#### Response Time as Signal
- Already capturing response_ms in ReviewLog — never used for scheduling
- Slow response on a "correct" answer may indicate fragile knowledge
- Decreasing response time across reviews = fluency signal
- Could use as secondary input to FSRS difficulty parameter or to decide acquisition graduation

#### Session Design for Variable Practice Time
- User has unpredictable practice time (5 min to 2 hours)
- Sessions should be designed as "micro-completable units" — every 2-3 cards is a meaningful chunk
- Front-load the most important reviews (acquisition words, lapsed words)
- If user only does 2 cards, they should be the 2 most valuable cards possible
- Longer sessions can include more review/consolidation items and new word introductions
- [DONE] **Background session refresh**: when app resumes after 15+ min gap, fetches fresh session in background and seamlessly swaps remaining cards on next advance. Data showed 48% abandonment rate and -22% comprehension on stale resumptions.
- Reduce staleness threshold further? Data analysis (2026-02-15) showed 15 min catches all problematic cases, but could experiment with 10 min if stale sessions remain an issue
- Consider adaptive session sizes based on user's typical completion patterns (~9.4 reviews per session average) — smaller sessions (8-10) may have higher completion rates than current 15-20

### Ideas from Arabic Learning Research Deep Dive (2026-02-12)

#### Coverage-Based Progress Tracking
- Show user their estimated text coverage % based on Masrai & Milton (2016) curves: 1K lemmas = 79%, 5K = 89%, 9K = 95%
- This is more meaningful than raw word count ("you can read 89% of any Arabic text" vs "you know 5,000 words")
- Track separately for different registers (news, literary, religious) if frequency data supports it

#### AVP A1 Curriculum Integration
- Import Arabic Vocabulary Profile A1 list (1,750 items, expert-validated by 71 teachers) as reference curriculum
- Cross-reference against current word list to identify A1 gaps
- Show A1 completion percentage as a milestone metric
- AVP uses multi-dialectal cross-checking which helps select vocabulary that transfers across dialects

#### Root-Aware FSRS Stability Boost
- Research: learners rely on roots in 87.5% of encounters with unknown words
- When a new word shares a root with 2+ known words, boost initial FSRS stability by ~30%
- Root familiarity at 30-60% coverage is the sweet spot for introducing new root family members
- Research: root awareness accounts for substantial variance in reading outcomes (Cambridge study)
- The 500 most productive roots cover 80% of daily vocabulary -- prioritize these

#### OSMAN Readability Integration
- Available via `pip install textstat` → `textstat.osman(text)`. Low effort but limited value for short sentences (5-14 words) — primarily measures word-level complexity (syllable count, Faseeh markers, long words), which we already control via max_words and LLM difficulty hints.
- OSMAN is Arabic-specific, accounts for syllable types, works with/without diacritics, validated on 73K parallel sentences
- Combined difficulty = OSMAN score + unknown word density + morphological density
- Open source: github.com/drelhaj/OsmanReadability

#### Pre-Listening Vocabulary Flash
- Research (Elkhafaifi 2005): prelistening activities significantly improve listening comprehension
- Question preview > vocabulary preview > nothing (all significantly different)
- Before playing audio, show 2-3 key vocabulary words from the sentence as a preview
- Also: repeated listening is effective -- encourage replay before revealing text

#### Verb Form Progression Gating
- Form I accounts for ~60-70% of all verb usage in MSA
- Recommended learning order: Form I -> II, IV -> V, VIII -> X, III -> VI, VII -> IX
- Gate derived form introduction on Form I mastery (70%+ accuracy on Form I reviews)
- Form IX is effectively optional (colors/defects only, <0.5% of usage)
- Track verb form distribution in user's vocabulary as an analytics metric

#### English Loanwords in Arabic as Easy Wins
- Modern Arabic has many recognizable loanwords: computer, internet, television, film, democracy
- These are immediately recognizable through script and can accelerate early learning
- Flag these in the UI with a "loanword" badge to boost learner confidence
- Low priority for Arabic->English direction cognates (script barrier + semantic drift make them less useful)

#### Diacritics Strategy Validation
- Midhwah (2020, Modern Language Journal): VT groups outperformed UVT across ALL proficiency levels
- Abu-Rabia: diacritics improve comprehension for native speakers of all ages and skill levels
- No evidence that early diacritics hinder later reading of unvowelized text
- Current "always show diacritics" approach is strongly research-validated
- Future: optional "reading challenge" mode without diacritics as a separate exercise (not default)

#### Narrow Reading for Vocabulary Recycling
- Research supports "narrow reading" (multiple texts on same topic) for vocabulary consolidation
- Arabic MSA-to-dialect overlap: Levantine 63%, Gulf 55-60%, Egyptian 50-55%, Moroccan 33-40%
- Topic-based story generation would naturally recycle domain vocabulary
- Could track vocabulary "domain coverage" (e.g., 85% of food vocabulary, 40% of politics)

#### Listening Anxiety Mitigation
- Elkhafaifi (2005): listening anxiety and FL learning anxiety are separate but related, both correlate negatively with achievement
- Listening practice should be low-stakes and scaffolded
- Slow speech mode (0.7x) + learner pauses aligns with research
- Consider a "listening confidence" metric visible to user to track progress and reduce anxiety

#### Arabic-Specific Sentence Difficulty Model
- Beyond unknown word count, Arabic sentence difficulty depends on:
  - Morphological density (how many clitics/affixes per word)
  - Root familiarity of unknown words (known root = easier)
  - Verb form complexity (Form I easier than Form X)
  - Sentence length (eye-tracking shows fixation per content word)
- Weight these factors in sentence selection algorithm
- Research: morphological density impacts reading comprehension independently of vocabulary coverage

#### INN University Arabic Heritage Language Research
- Jonas Yassin Iversen (Professor) and Lana Amro (PhD candidate) at INN Hamar research Arabic heritage language education in Scandinavia
- Key finding: Norwegian supplementary (weekend school) model leads Arabic students to **hide their language learning from peers**, while Swedish mainstream integration fosters pride — relevant to Alif as a private self-directed tool
- Translanguaging (using L1+L2 together) validated as productive pedagogy in digital Arabic education — supports our English glosses + transliteration approach
- Amro's PhD specifically studies digital Arabic language learning with translanguaging
- DIALOGUES Erasmus+ project (2025–2027) on languages, literacies, and learning in a digital age
- **Full writeup**: [`research/inn-arabic-heritage-language.md`](research/inn-arabic-heritage-language.md)

#### Audio Course Import via Soniox Transcription (2026-02-14)
- Michel Thomas Egyptian Arabic Foundation (8 CDs, 118 tracks, ~8h) available as MP3
- **Pipeline**: Soniox STT (16.2% WER Arabic, native code-switching EN↔AR) → extract Arabic segments → LLM classify Egyptian vs MSA → import words + sentences
- **Code ready**: `soniox_service.py` (REST API wrapper), `scripts/import_michel_thomas.py` (5-phase pipeline: transcribe → extract → classify → import → verify)
- **Blocked on**: valid Soniox API key. Get one at console.soniox.com. Cost: ~$0.10 for CD1, ~$0.80 for all 8 CDs.
- **Egyptian Arabic overlap**: ~60-70% of beginner vocabulary shared with MSA. LLM classification filters Egyptian-only words (عايز, دلوقتي). MSA equivalents provided for bridgeable words.
- **Words imported as "learning"**: user already learned these through audio course, skip Leitner → straight to FSRS with Rating.Good
- **Reusable pattern**: same pipeline works for any language course audio (Pimsleur, Assimil, etc.)
- Could extend to Pimsleur Arabic, Assimil Arabic, or any other audio course with mixed L1+L2 instruction
- Could build a generic "audio course importer" UI: upload audio files → transcribe → review extracted vocabulary → import
- Speaker diarization could isolate teacher (authoritative Arabic) vs student (possibly incorrect) for quality filtering
- Word-level timestamps from Soniox enable future feature: link each imported word/sentence back to exact audio timestamp for replay

#### 19-Level Readability Corpus (BAREC)
- BAREC (ACL 2025): 69K sentences, 19 readability levels, CC-BY-SA. Pilot study (2024, 10.6K segments) evolved into this.
- Investigated 2026-02-12: 28.8K sentences in 5-14w target range, but only ~50% diacritized (density 0.176 vs 0.8 for full). Levels 1-3 are mostly junk (headers, fragments). Usable diacritized subset: ~3,700 sentences at levels 5-10.
- Sources: Emarati curriculum, Hindawi literature, Majed magazine, Wikipedia, religious texts.
- Not practical as drop-in sentence source (needs diacritization + has context-dependent excerpts), but useful for difficulty calibration.
- BERT-based models available for automatic readability assessment (87.5% QWK from BAREC shared task 2025)
- HuggingFace: CAMeL-Lab/BAREC-Shared-Task-2025-sent
