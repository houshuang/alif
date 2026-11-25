"""Reviewed plan for merging split root families (2026-11-25).

Roots are keyed by their exact string, so the same radicals written with a
different hamza seat (أ.م.ن / ا.م.ن / ء.م.ن) or a hollow/weak radical written
as plain alif (ج.ا.ز for ج.و.ز) became separate families. Each entry below was
checked against the lemmas attached in production. Root strings are unique, so
entries are keyed by string; the script refuses to run if a string is missing.

MERGES: loser root -> winner root. Lemmas and sentence root focus move to the
winner; the loser row is deleted.
RENAMES: a root whose spelling is wrong but which has no existing correct row.
REPOINT: a misspelled root whose lemmas move to a correct root (created when
missing) or to no root (loanwords, interjections); the old row is deleted.
ASSIGN: rootless studied words that have a clear root.
"""

# (loser_root, winner_root)
MERGES: list[tuple[str, str]] = [
    # hamza-seat spellings of one root
    ("ش.ي.أ", "ش.ي.ء"),
    ("م.ر.ا", "م.ر.أ"),
    ("س.و.ء", "س.و.أ"),
    ("ر.ء.ي", "ر.أ.ي"),
    ("ق.ر.ء", "ق.ر.أ"),
    ("س.ء.ل", "س.أ.ل"),
    ("ر.ئ.س", "ر.أ.س"),
    ("ر.ء.س", "ر.أ.س"),
    ("ء.ل.ه", "أ.ل.ه"),
    ("ا.س.ف", "أ.س.ف"),
    ("ا.و.ب", "أ.و.ب"),
    ("ه.د.ء", "ه.د.أ"),
    ("ا.س.س", "أ.س.س"),
    ("ئ.م.ر", "أ.م.ر"),
    ("ء.م.ن", "أ.م.ن"),
    ("ا.م.ن", "أ.م.ن"),
    ("ن.ب.ء", "ن.ب.أ"),
    ("ا.ل.ف", "أ.ل.ف"),
    ("ء.ل.ك", "أ.ل.ك"),
    ("ا.ك.د", "أ.ك.د"),
    ("ء.ك.د", "أ.ك.د"),
    ("م.ئ.ي", "م.أ.ي"),
    ("ب.ؤ.س", "ب.أ.س"),
    ("ا.ل.م", "أ.ل.م"),
    ("ه.ز.ء", "ه.ز.أ"),
    ("ا.ث.ر", "أ.ث.ر"),
    ("خ.ط.ئ", "خ.ط.أ"),
    ("ب.ط.أ", "ب.ط.ء"),
    ("ا.ش.ر", "أ.ش.ر"),
    ("ل.ج.ا", "ل.ج.أ"),
    ("ل.ج.ء", "ل.ج.أ"),
    ("م.ل.ا", "م.ل.أ"),
    ("م.ل.ء", "م.ل.أ"),
    ("خ.ب.ا", "خ.ب.أ"),
    ("خ.ب.ء", "خ.ب.أ"),
    # hollow/weak radical written as alif, or a derived-stem letter taken as a radical
    ("آ.ن.و", "أ.ي.ن"),     # اَلْآنَ joins آنٌ "time"
    ("ا.ت.ج.ه", "و.ج.ه"),   # اتَّجَهَ is Form VIII of و.ج.ه
    ("ا.ل.ق", "ل.ق.ي"),     # أَلْقَى is Form IV of ل.ق.ي
    ("ا.ج.ب", "و.ج.ب"),     # إِيجابِيّ from وَجَبَ
]

# (old_root, new_root)
RENAMES: list[tuple[str, str]] = [
    ("ا.ز.ي.ح", "ز.ي.ح"),     # أَزَاحَ
]

# Misspelled roots: every lemma moves to the target root (created if missing),
# or loses its root when the target is None; the old row is then deleted.
# (old_root, target_root or None)
REPOINT: list[tuple[str, str | None]] = [
    ("ر.ا.ح", "ر.و.ح"),        # اِرْتاحَ
    ("ك.ا.س", "ك.أ.س"),        # كَأْس
    ("س.ر.ا", None),           # إِسْرَائِيلِيّ: from a name, no root
    ("ع.ا.ن", "ع.ن.ي"),        # مُعَانَاة from عانَى
    ("ي.ا.س", "ي.أ.س"),        # يَائِس
    ("ج.ا.ز", None),           # جَاز "jazz": loanword
    ("ط.م.ا", "ط.م.أ.ن"),      # اطْمَأَنَّ
    ("ه.ي.ا", None),           # هَيَّا: interjection
    ("ب.ا.ل", "ب.ل.و"),        # مُبالٍ, لامُبالاة from بالَى
    ("غ.ل.ا", "غ.ل.ي"),        # غَلَاء
    ("ذ.ا.ع", "ذ.ي.ع"),        # إِذَاعَة
    ("ع.ص.ا", "ع.ص.و"),        # عَصَا
    ("ا.ن.س", None),           # empty
]

# Rootless studied words with a clear root. (lemma_id, lemma_ar, root)
ASSIGN: list[tuple[int, str, str]] = [
    (3185, "وَفْقاً", "و.ف.ق"),
    (2123, "أَخِيرًا", "أ.خ.ر"),
    (4195, "مِرَارًا", "م.ر.ر"),
    (4075, "حِينَئِذٍ", "ح.ي.ن"),
    (4221, "حِينَئِذٍ", "ح.ي.ن"),
    (2583, "حِينَذاك", "ح.ي.ن"),
    (2617, "آنَذاك", "أ.ي.ن"),
]

# Kept separate on purpose:
#   ر.ئ.ي (رِئَة "lung") vs ر.أ.ي "see" — under review.
#   ه.ي.ء (هَيْئَة) vs ه.ي.ا (هَيَّا, interjection, set to no root above).
# Known misfiling left for review: اسْتٌ "rump" under أ.س.س (should be س.ت.ه).
