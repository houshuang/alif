---
name: feedback-lemma-deletion-fks
description: "When writing a script that deletes alif Lemma rows, enumerate FKs that point AT Lemma (not just FKs on Lemma) — ReviewLog has NOT NULL, plus 6 other nullable FK sites."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: acece251-5bce-450c-a7c0-d91c867a58f6
---

When writing any alif script that hard-deletes `Lemma` rows, enumerate every FK column in `backend/app/models.py` that points AT `lemmas.lemma_id`, not just FKs declared on the Lemma table. Otherwise the `DELETE FROM lemmas` will fail with `FOREIGN KEY constraint failed`.

The full list (as of 2026-05-20):
- `UserLemmaKnowledge.lemma_id` (unique, NOT NULL) — delete
- `ReviewLog.lemma_id` (NOT NULL) — delete (this one is the usual surprise — review history blocks deletion)
- `SentenceWord.lemma_id` (nullable) — set NULL
- `StoryWord.lemma_id` (nullable) — set NULL
- `Sentence.target_lemma_id` (nullable, indexed) — set NULL
- `FrequencyCoreEntry.lemma_id` (nullable, indexed) — set NULL
- `ContentFlag.lemma_id` (nullable) — set NULL
- `QuranicVerseWord.lemma_id` (nullable) — set NULL
- `Lemma.canonical_lemma_id` (nullable, self-FK from variants) — set NULL

**Why:** Caught 2026-05-20 in `cleanup_numeric_ocr_lemmas.py`. I checked FK columns on Lemma (canonical_lemma_id only) but forgot ReviewLog (NOT NULL) + Sentence.target_lemma_id were pointing AT lemma_id from other tables. First --apply rolled back with `FOREIGN KEY constraint failed`. The `cleanup_ocr_lemma_corruption_2026_05_15.py` docstring already documents this exact trap ("suspension alone wasn't enough... followed up with a one-off SQL pass that deleted ReviewLogs + ULK + Lemma rows in dependency order"). Should have read that line before writing.

**How to apply:** Before writing or running a Lemma-deletion script, grep `models.py` for `ForeignKey("lemmas.lemma_id")` and account for every hit. SQLite FK enforcement is on in alif, so missing one fails the transaction (which is the safe outcome — transaction rolls back, data is intact). See `[[reference_arabic_educational_pages]]` style or the script catalog entry for `cleanup_numeric_ocr_lemmas.py` for the working dependency-order pattern.
