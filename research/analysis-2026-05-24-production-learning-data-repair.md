# Production Learning Data Repair - 2026-05-24

Production database: `/opt/alif/backend/data/alif.db`.

This note documents the live data cleanup done across 2026-05-23 and
2026-05-24 after a seven-day Arabic learning-health review found bad lemma
display forms, stale acquisition state, variant ULKs, and due words with no
active review material.

No application code was changed in this repair pass. All mutations were backed
up first and applied directly to production SQLite in explicit transactions or
via existing one-shot scripts.

## Backups

Backups created before mutation:

- `/opt/alif-backups/alif_pre_al_display_fix_20260523_200054.db`
- `/opt/alif-backups/alif_pre_1_3_repairs_20260523_200347.db`
- `/opt/alif-backups/alif_pre_variant_merge_20260524_122813.db`
- `/opt/alif-backups/alif_pre_fast_intro_single_20260524_123114.db`
- `/opt/alif-backups/alif_pre_due_sentence_reactivate_20260524_1232.db`
- `/opt/alif-backups/alif_pre_clear_suspended_variant_fsrs_20260524_1233.db`

## 1. Lemma display forms with prefixed `al-`

Problem: a scan for display lemmas with article prefixes found seven rows where
the lemma display form incorrectly included the definite article. These were
display-form issues, not canonical-learning-state issues.

Updated:

| Before | After |
|---|---|
| `الغَلَاء` | `غَلَاء` |
| `المُسَاوَاة` | `مُسَاوَاة` |
| `الدِّفاع` | `دِفَاع` |
| `الإِسْكان` | `إِسْكَان` |
| `العَوْلَمَة` | `عَوْلَمَة` |
| `الإِذاعَة` | `إِذَاعَة` |
| `السَّلْطَنَة` | `سَلْطَنَة` |

Post-fix exact display-candidate scan returned zero rows.

## 2. Fast-intro acquisition promotion repairs

The existing recovery script
`backend/scripts/reset_fast_intro_promotions_2026_05_17.py` was used.

On 2026-05-23 it found and reset 13 residual candidates:

- `#575 ذَرَا`
- `#733 مُحِيطٌ`
- `#1320 سِتُّونَ`
- `#1422 خَوَى`
- `#1465 جِنْسِيّ`
- `#3135 جُحْرِي`
- `#3152 فَضْلَة`
- `#3232 دَاعَبَ`
- `#3235 اِنْحَدَرَ`
- `#3260 لَهَث`
- `#3262 مَهَل`
- `#3305 يَائِس`
- `#3597 كَتَمَ`

Breakdown:

- Reason: 13 `fast_correct_then_fail_low_acc`
- Old box: 11 from box 2, 2 from box 3
- Source: 9 `textbook_scan`, 2 `collateral`, 2 `book`

On 2026-05-24 the same dry-run found one new candidate and it was reset:

- `#2835 أَنْذَرَ`, source `frequency_core`, old box 2, ratings `333311333`

Final dry-run result:

```json
{
  "candidate_count": 0,
  "candidates": []
}
```

## 3. Stale acquisition state

Existing script:

```bash
ssh alif 'cd /opt/alif/backend && .venv/bin/python3 scripts/cleanup_stale_state.py --db data/alif.db --apply'
```

Applied:

- Cleared stale `acquisition_box` / `acquisition_next_due` on 9 non-acquiring
  rows.
- Cleared stale `graduated_at` on four currently acquiring rows:
  - `#182 تَاجٌ`
  - `#1104 مَنَّى`
  - `#2530 فَرَدَ`
  - `#2729 مُفْرَدَة`

Final dry-run:

```text
=== Stale acquisition_box: 0 words ===
=== Circular canonical references: 0 pairs ===
=== Conjugated variants acquiring with canonical already known: 0 ===
Total issues found: 0
```

## 4. Junk and Quranic variant cleanup

Two due/no-active rows were not sentence-material problems:

- `#2401 نِي` was a zero-review, non-standalone fragment. It was marked
  `word_category='junk'` and its ULK was suspended.
- `#2847 ءَامَنُواْ` was an inflected Quranic variant of `#2838 آمَنَ`.
  It was merged into `#2838`.

`#2847 -> #2838` merge impact:

- 7 `sentence_words` moved
- 5 `review_log` rows moved
- 6 `sentences.target_lemma_id` rows moved
- variant ULK deleted
- `lemmas.canonical_lemma_id` set to `2838`
- canonical `#2838 آمَنَ` ended at `known`, `times_seen=15`,
  `times_correct=11`

Verification showed no remaining `review_log`, `sentence_words`,
`sentences.target_lemma_id`, or ULK refs for `#2847`.

## 5. Variant ULK cleanup and canonical merges

The generic dry-run showed five overshadowed variant ULKs. Analysis found that
running `suspend_variant_ulks.py --apply` as-is would be too blunt because two
rows had meaningful learning history or incorrect canonical metadata.

Custom transaction applied instead.

### `#40 زَوْجة -> #184 زَوج`

Reason: `#40` was already linked as a variant, but had its own known ULK and
review history. The canonical gloss was too narrow (`husband`) for the merged
spouse/wife material.

Applied:

- moved 125 `sentence_words`
- moved 15 `sentences.target_lemma_id`
- moved 13 `review_log` rows
- merged ULK stats into `#184`
- deleted source ULK
- left `#40` as a variant with `canonical_lemma_id=184`
- updated `#184` gloss to `spouse; husband; wife`

Final canonical state:

- `#184 زَوج`: `known`, `times_seen=51`, `times_correct=49`

### `#1592 تَهْتَمُّ -> #2237 اِهْتَمَّ`

Reason: `#1567 وَتَهْتَمُّ` pointed to `#1592 تَهْتَمُّ`, but decomposition
notes already identified `#1592` as an inflected present-form row. The better
dictionary-form canonical was existing row `#2237`.

Applied:

- moved 73 `sentence_words`
- moved 12 `sentences.target_lemma_id`
- moved 11 `review_log` rows
- merged ULK stats into `#2237`
- deleted source ULK
- left `#1592` as a variant with `canonical_lemma_id=2237`
- repointed `#1567 وَتَهْتَمُّ` directly to `#2237`
- updated `#2237` display form to `اِهْتَمَّ` and gloss to
  `to care, to be interested`

Final canonical state:

- `#2237 اِهْتَمَّ`: `known`, `times_seen=33`, `times_correct=28`

### Zero-history variant ULKs suspended

Suspended variant ULKs with no review history and no active refs:

- `#61 بَيْتي -> #181 بَيت`
- `#1567 وَتَهْتَمُّ -> #2237 اِهْتَمَّ`
- `#1691 كُتُبِي -> #228 كِتاب`
- `#1834 صَدِيقِي -> #1500 صَدِيق`
- `#2662 فُضَلَاتُه -> #3152 فَضْلَة`
- `#2907 مَلَئِكَةِ -> #1450 مَلَائِكَةٌ`
- `#2909 عَلِيمُ -> #2900 عَلِيمٞ`

After this, the broader variant invariant was clean:

```text
non_suspended_variant_ulks = 0
variant_acquisition_boxes = 0
```

### Stale FSRS cards on suspended variants

Found 21 already-suspended variant ULKs that still carried stale
`fsrs_card_json`. These cards could not schedule because the state was already
`suspended`, but they violated the invariant that variants should not own cards.

Cleared stale FSRS cards on:

`3, 21, 209, 318, 412, 457, 1242, 1306, 1515, 1541, 1555, 1569, 1693, 1849, 1886, 2287, 2288, 2289, 2290, 2291, 3139`

Final variant ULK state:

```text
knowledge_state  n   with_fsrs  with_box
---------------  --  ---------  --------
suspended        42  0          0
```

## 6. Due words with no active material

The first pass reactivated vetted material for due lapsed/learning words that
had no active sentence. Some of those sentence rows were inactive again during
the 2026-05-24 verification pass, so a final focused reactivation was applied.

Final reactivated verified sentences:

| Sentence | Lemma | Arabic |
|---:|---:|---|
| `43908` | `#622 صَفِرَ` | `مَسَحَ المُعَلِّمُ السَّبُّورَةَ، فَصَفِرَتْ سُطُورُهَا.` |
| `46795` | `#1315 حَرَثَ` | `أَجِيرُ الْمَزْرَعَةِ حَرَثَ الْأَرْضَ تَحْتَ شَمْسٍ لَافِحَةٍ.` |
| `46942` | `#3486 مُلْتَهِب` | `الطَّبِيبُ أَنْذَرَ المَرِيضَ بِأَنَّ حَنْجَرَتَهُ مُلْتَهِبَةٌ وَيَحْتَاجُ إِلَى دَوَاءٍ.` |
| `47067` | `#2826 رَزَقَ` | `اللهُ قَدِيرٌ يَرْزُقُ العِبَادَ بِرَحْمَةٍ.` |

All four were quality-reviewed with:

- `quality_natural=1`
- `quality_translation_correct=1`
- `is_active=1`

Final due/no-active query returned zero rows.

## Final verification

Final checks after all production repairs:

```text
suspend_variant_ulks.py --dry-run
=> No overshadowed variants found. Nothing to do.

reset_fast_intro_promotions_2026_05_17.py
=> candidate_count: 0

cleanup_stale_state.py --db data/alif.db
=> Total issues found: 0

due words without active material
=> 0

PRAGMA quick_check
=> ok
```

Additional invariant checks:

```text
non_suspended_variant_ulks = 0
variant_acquisition_boxes = 0
source_ref_rows_40_1592 = 0
```

## Operational lessons

1. Do not blindly apply `suspend_variant_ulks.py` when a variant has real review
   history. History-bearing variants should be merged into the canonical so
   review logs and learner statistics are preserved.
2. Canonical links are not enough; canonical metadata must also be checked.
   `زَوْجة -> زَوج` was only safe after widening the canonical gloss, and
   `وَتَهْتَمُّ -> تَهْتَمُّ` exposed that the intermediate canonical was itself
   an inflected form.
3. Suspended variant ULKs should not retain FSRS cards or acquisition boxes.
   The scheduler ignored them, but the stale cards made invariants harder to
   reason about.
4. The due/no-active query should stay in the routine health check. Sentence
   rows can become inactive again after a repair, so final verification should
   be run after all other mutations.

## Follow-up checks

Run after the next study block:

```bash
ssh alif 'cd /opt/alif/backend && .venv/bin/python3 scripts/suspend_variant_ulks.py --dry-run'
ssh alif 'cd /opt/alif/backend && PYTHONPATH=/opt/alif/backend DATABASE_URL=sqlite:////opt/alif/backend/data/alif.db .venv/bin/python3 scripts/reset_fast_intro_promotions_2026_05_17.py'
ssh alif 'cd /opt/alif/backend && .venv/bin/python3 scripts/cleanup_stale_state.py --db data/alif.db'
```

Expected:

- no overshadowed variant ULKs
- fast-intro candidate count remains zero or very low
- stale-state issues remain zero
- no due learning/lapsed/acquiring words lack active material
