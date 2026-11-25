# Which entry gets the credit? Book tokens against the dictionary lemma (2026-11-25)

**Question.** The learner reported that the accounting felt wrong: words he had seen hundreds
of times were listed as missing, and participles and verbal nouns were folded into verbs.
After `book_frequency_v1` went live the "not in Alif" list contained فكرة, شركة, موضوع and
مجتمع, which could not be right for a vocabulary of 5,000 entries. This note measures, token
by token, which entry Alif credits for a word in real book text.

**Method.** `research/word-credit-2026-11-25/credit_diag.py` runs every token of the book
sample (Hindawi, Kalima and Green Library sentences of BAREC, 266,211 tokens) through the
production resolver in strict mode, exactly as `build_book_frequency.py` does, and compares
the credited entry with CAMeL's own context-free lemma for the token. The comparison ignores
vowel marks, hamza seats and final ى/ي. Database: the production snapshot of 2026-11-25
morning. CAMeL's lemma is itself wrong for some tokens (it reads تراه as راوند), so a
disagreement is a candidate, not a verdict; the lists were read by hand before anything was
created.

## Findings

- Of 138,067 content tokens the resolver linked to an entry, 110,677 (80%) went to the entry
  CAMeL names. The other 20% went to a different entry.
- Grouped by the dictionary lemma, 986 words with at least 12 occurrences were either
  unresolved or credited to another entry: 30,305 tokens, 11% of all running words.
  - **810 words (21,927 tokens) have no entry at all.** `words_without_entry.tsv`.
  - **176 words (8,378 tokens) have an entry, but another entry gets the credit.**
    `entry_exists_credit_elsewhere.tsv`. Most are a singular and a plural or feminine stored
    as two entries (بلد and بِلاد, يوم and أيّام, كبير and كبيرة), which is harmless for
    counting. Some are real errors: 87 tokens of عاد "to return" go to وَعَدَ "to promise",
    74 tokens of بدأ go to أبدًا, 45 of وجد go to جادّ.
- 289 entries with at least 20 book tokens get less than half of them from their own word
  (`credit_per_entry.tsv`: tokens, tokens that agree, entry, what the rest really are).

What the words without an entry are:

| Kind | Examples | Where their tokens went |
|---|---|---|
| Common nouns | فكرة, شركة, ثورة, شأن, أصل, حلّ, خدمة, وسيلة | unresolved, or a look-alike: فكرة → كُرَة "ball" (ف + كرة), شأن → شَنَّ, ثورة → ثَوْر "bull", حلّ → حالَ |
| Verbal nouns | تفكير, معرفة, استخدام, اهتمام, شعور, وصول, محاولة, اختلاف | the verb (فكّر, عرف, استخدم…) through its stored forms |
| Participles used as nouns or adjectives | مجتمع, موضوع, موجود, معروف, واقع, قارئ, قائم | the verb (اجتمع, وضع, وجد, عرف, وقع, قرأ, قام) |
| Adjectives in ـيّ | اقتصادي, علمي, نفسي, أساسي, إسلامي, رئيسي, حالي | unresolved, or the base noun |
| Verbs | خاف, كفى, أظهر, أثار, أخرج, قطع, تكلّم | unresolved, or another verb of the root (أظهر → ظهر, خاف → خَفِيَ) |

This is the grouping the learner found confusing. It is not a rule that participles belong to
the verb: the verb's stored forms catch the token only because the derived word has no entry
of its own. Where the entry exists (مسافر), it wins.

**Check.** On a copy of the database, 19 of these words were inserted as plain entries and
the lookup rebuilt. Their tokens, plurals and conjugations moved to the new entries
(المجتمعات → مُجْتَمَع, الموضوعات and مواضيع → مَوْضُوع, يخاف and خافت → خافَ, شئون and بشأن →
شَأْن, فكرة → فِكْرَة and no longer "ball"). Two unvocalised homographs stayed on the older
entry (آخر on آخِر "last", قابل on قابِل "capable"); in vocalised app sentences the tashkeel
separates them.

**Frequent leeches.** Of 89 suspended words with at least 18 book tokens, 66 get most of
their tokens from their own word: those are real difficulty, mostly verbs. For 23 the count
is inflated by another word (انتبه by the noun انتباه, جادّ by وجد, خطب by خطبة, تعوّد by
عاد), so their rank overstates them and their sentences may show a different word.

## What was done on 2026-11-25

- 421 of the words without an entry (at least 17 occurrences, read and glossed by hand, names
  and CAMeL misreadings removed) were created through the quality gates with
  `scripts/stage_word_list.py` (`data/word_lists/book_gap_words_2026-11-25.tsv`), with
  variant detection off so the new entries are not folded back. The book list was rebuilt
  afterwards.
- رأى received 17 missing forms (suffixed stems such as رآ and يرا, jussive ير): 189 tokens
  of the verb had been unresolved.

## Not done

- The remaining words without an entry at 12–16 occurrences (about 390), and everything
  below.
- Sentences already stored keep their old mapping. A sentence where مجتمع is mapped to
  اجتمع still credits the verb until it is remapped and verified again.
- The resolver's own errors where the right entry exists (عاد, بدأ, وجد above; ورأى read as
  رَأْيٌ; يرون as رانَ), and a general way to resolve suffixed and jussive forms of weak verbs.
- Duplicate entries for one word (بلد and بِلاد both canonical), and entries whose headword
  is a plural or feminine.
- The knowledge already credited to an entry for another word's tokens is left as it is.
