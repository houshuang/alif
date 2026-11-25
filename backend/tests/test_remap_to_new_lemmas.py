import pytest

from scripts.remap_to_new_lemmas import spells_headword


@pytest.mark.parametrize(
    "surface,headword",
    [
        ("بِالسَّعَادَةِ.", "سَعادَة"),
        ("الإِجَابَةِ", "إِجابَة"),
        ("التَّلِّ", "تَلّ"),
        ("مَوْضُوعٌ", "مَوْضُوع"),
        ("الدِّينِيِّ.", "دِينِيّ"),
        ("بِٱسْتِخْدَامِ", "اِسْتِخْدام"),
        ("فكرة", "فِكْرَة"),
        ("لِلذَّهَابِ", "ذَهاب"),
        ("دَعْوَتِهِ", "دَعْوَة"),
        ("أَسَاسِيٌّ", "أَساسِيّ"),
    ],
)
def test_surface_that_spells_the_headword_moves(surface, headword):
    assert spells_headword(surface, headword)


@pytest.mark.parametrize(
    "surface,headword",
    [
        ("فَأَجَابَهُ", "إِجابَة"),
        ("أجابته", "إِجابَة"),
        ("نَفْسِي", "نَفْسِيّ"),
        ("بِتِلْكَ", "تَلّ"),
        ("أَتَمَّ", "تامّ"),
        ("دَيْنِي", "دِينِيّ"),
        ("بداخلي", "داخِلِيّ"),
        ("حُلْواً", "حَلّ"),
        ("عُدَّتَها", "عِدَّة"),
    ],
)
def test_other_word_with_similar_letters_stays(surface, headword):
    assert not spells_headword(surface, headword)
