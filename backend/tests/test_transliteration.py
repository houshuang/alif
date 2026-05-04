"""Regression tests for ALA-LC transliteration.

Focused on cases that previously failed: bare ya/waw without explicit
kasra/damma should still be inferred as long ī/ū (mirroring the bare-alif
inference for long ā).
"""
from app.services.transliteration import transliterate_arabic


def test_bare_ya_after_consonant_long_i():
    assert transliterate_arabic("حَديقة") == "ḥadīqa"
    assert transliterate_arabic("قَديم") == "qadīm"
    assert transliterate_arabic("جَميلة") == "jamīla"
    assert transliterate_arabic("لَطيفة") == "laṭīfa"


def test_bare_waw_after_consonant_long_u():
    assert transliterate_arabic("بَلوزة") == "balūza"
    assert transliterate_arabic("تَنّورة") == "tannūra"


def test_word_initial_hamza_carrier_long_i():
    assert transliterate_arabic("إيجار") == "ījār"
    assert transliterate_arabic("إِيجار") == "ījār"


def test_explicit_kasra_ya_still_long_i():
    assert transliterate_arabic("اِسْمي") == "ismī"
    assert transliterate_arabic("جَدّي") == "jaddī"


def test_explicit_damma_waw_still_long_u():
    assert transliterate_arabic("صَالُونٌ") == "ṣālūn"
    assert transliterate_arabic("مَكْتُوب") == "maktūb"


def test_consonant_ya_with_sukun_not_long_i():
    """ya with sukun after fatha is the glide /j/, not long ī."""
    assert transliterate_arabic("بَيْت") == "bayt"
    assert transliterate_arabic("بَيْضاء") == "bayḍāʾ"


def test_consonant_waw_with_sukun_not_long_u():
    """waw with sukun after fatha is the glide /w/, not long ū."""
    assert transliterate_arabic("جَوْعان") == "jawʿān"


def test_geminate_ya_not_long_i():
    """Shadda on ya = nisba/geminate, handled by separate logic."""
    assert transliterate_arabic("بُنِّيّ") == "bunnī"


def test_bare_alif_still_long_a():
    """Don't regress the existing bare-alif logic."""
    assert transliterate_arabic("طاوِلة") == "ṭāwila"
    assert transliterate_arabic("واسِع") == "wāsiʿ"


def test_al_prefix_with_bare_ya():
    assert transliterate_arabic("اَلْحَديقة") == "al-ḥadīqa"


def test_empty_and_no_arabic():
    assert transliterate_arabic("") == ""
    assert transliterate_arabic("hello") == "hello"


def test_ya_with_own_vowel_is_consonant_glide():
    """kasra + ya(+fatha) + alif = consonant ya, not long ī."""
    assert transliterate_arabic("سِيَاسَة") == "siyāsa"
    assert transliterate_arabic("رِيَال") == "riyāl"
    assert transliterate_arabic("هِيَام") == "hiyām"
    assert transliterate_arabic("أَغْبِيَاء") == "aghbiyāʾ"


def test_waw_with_own_vowel_is_consonant_glide():
    """damma + waw(+fatha) + alif = consonant waw, not long ū."""
    assert transliterate_arabic("كُوَالَا") == "kuwālā"


def test_bare_ya_before_alif_is_glide():
    """ya followed by alif (with no diacritics on ya) is consonantal —
    Arabic doesn't allow two adjacent long vowels. Without this rule
    حَالِياً becomes ḥālīā instead of ḥāliyā."""
    assert transliterate_arabic("حَالِياً") == "ḥāliyā"


def test_bare_waw_before_alif_is_glide():
    """damma + waw + alif (no diacritic on waw) is consonant w, not long ū."""
    assert transliterate_arabic("مَارِهُوانَا") == "mārihuwānā"
