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
LEMMA_ROOTS: single lemmas moved to a root (created when missing) or to no root.
NEW_ROOT_MEANINGS: core meaning for roots this plan creates.
MEANING_OVERRIDES: roots whose stored meaning described a word that moved out.

Reviewed by a second model (Fable) on 2026-11-25; its corrections are applied.
"""

# (loser_root, winner_root)
MERGES: list[tuple[str, str]] = [
    # hamza-seat spellings of one root
    ("ش.ي.ء", "ش.ي.أ"),
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
    ("ب.ط.ء", "ب.ط.أ"),
    ("ا.ش.ر", "أ.ش.ر"),
    ("ل.ج.ا", "ل.ج.أ"),
    ("ل.ج.ء", "ل.ج.أ"),
    ("م.ل.ا", "م.ل.أ"),
    ("م.ل.ء", "م.ل.أ"),
    ("خ.ب.ا", "خ.ب.أ"),
    ("خ.ب.ء", "خ.ب.أ"),
    # hollow/weak radical written as alif, or a derived-stem letter taken as a radical
    ("آ.ن.و", "أ.و.ن"),     # اَلْآنَ: Wehr files آن, الآن, آنذاك, أوان under أ.و.ن
    ("أ.ي.ن", "أ.و.ن"),     # آنٌ "time" (only lemma); أ.ي.ن reads as أَيْنَ "where"
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
    ("غ.ل.ا", "غ.ل.و"),        # غَلَاء from غَلَا يَغْلُو; غ.ل.ي is "boil"
    ("ذ.ا.ع", "ذ.ي.ع"),        # إِذَاعَة
    ("ع.ص.ا", "ع.ص.و"),        # عَصَا
    ("ا.ن.س", None),           # empty
]

# (lemma_id, lemma_ar, target root or None)
LEMMA_ROOTS: list[tuple[int, str, str | None]] = [
    # rootless studied words with a clear, useful root
    (3185, "وَفْقاً", "و.ف.ق"),
    (2123, "أَخِيرًا", "أ.خ.ر"),
    (4195, "مِرَارًا", "م.ر.ر"),
    (4075, "حِينَئِذٍ", "ح.ي.ن"),
    (4221, "حِينَئِذٍ", "ح.ي.ن"),
    (2583, "حِينَذاك", "ح.ي.ن"),
    (2617, "آنَذاك", "أ.و.ن"),
    (5001, "إِثْر", "أ.ث.ر"),
    (2526, "طَوَال", "ط.و.ل"),
    (2669, "طالَما", "ط.و.ل"),
    (2033, "حَوْل", "ح.و.ل"),
    (2032, "نَحْو", "ن.ح.و"),
    (3359, "عَبْرَ", "ع.ب.ر"),
    (2301, "صَّائِمَاتِ", "ص.و.م"),
    (2034, "دُون", "د.و.ن"),
    (2177, "سِوَى", "س.و.ي"),
    (4527, "سِيَّمَا", "س.و.ي"),
    # misfiled
    (14, "غَالِي", "غ.ل.و"),         # "expensive"; مَغْلِيّ "boiled" stays in غ.ل.ي
    (4265, "مُؤْنَة", "م.و.ن"),       # "provisions", not a sibling of "now"
    (1003, "اسْتٌ", "س.ت.ه"),         # "rump", not أ.س.س
    (162, "بِلا", None),              # بِ + لا, not ب.ل.و
    (3285, "عِنْدَئِذ", None),        # consistent with عِنْد staying rootless
    (1943, "صُومَالِيّ", None),       # "Somali": from a name, not ص.و.م "fast"
]

NEW_ROOT_MEANINGS: dict[str, str] = {
    "غ.ل.و": "being expensive; excess, going beyond bounds",
    "ن.ح.و": "direction, toward; manner; grammar",
    "م.و.ن": "provisions, supplies; supporting a household",
    "س.ت.ه": "buttocks, rump",
    "ع.ص.و": "stick, staff",
    "ط.م.أ.ن": "calm, reassurance, peace of mind",
}

MEANING_OVERRIDES: dict[str, str] = {
    "أ.و.ن": "time, moment; now",                  # was written for مُؤْنَة
    "غ.ل.ي": "boiling",                            # غَالِي moved to غ.ل.و
    "ش.ي.أ": "thing; to want, will",               # gains شَيْء, أَشْياء
}

# Kept separate on purpose (they collide once hamza seats are folded):
#   ر.ئ.ي (رِئَة "lung") vs ر.أ.ي "see": formally one root in Lane, but no semantic link.
#   ه.ي.ء (هَيْئَة): not merged with ه.ي.ا, whose هَيَّا (interjection) loses its root above.
# Left as is: ع.ن.د keeps عِنَاد; عِنْد and رُبَّمَا stay rootless (siblings would mislead).
