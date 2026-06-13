"""Regression guard for definite-article-in-display-headword (2026-06-13).

Scans stored in-text surface forms (الْكَهْف, السَّمَاوِيّ) as the diacritized
display headword while the bare form correctly dropped ال, so the intro card
showed "the cave" instead of "cave". `strip_display_definite_article` removes the
article from display only when it is safe (bare doesn't carry ال, and the result
still normalizes back to the stored bare).
"""

import pytest

from app.services.lemma_quality import strip_display_definite_article


@pytest.mark.parametrize(
    "lemma_ar,lemma_ar_bare,expected",
    [
        # Moon letter: article + sukun on lam
        ("الْكَهْف", "كهف", "كَهْف"),
        ("الْمَغِيب", "مغيب", "مَغِيب"),
        ("المَتاع", "متاع", "مَتاع"),
        # Sun letter: shadda on the first root consonant must also drop
        ("السَّمَاوِيّ", "سماوي", "سَمَاوِيّ"),
        ("السَّرج", "سرج", "سَرج"),
        # Hamza-seat first letter, bare normalizes alef
        ("الْإِينَاس", "ايناس", "إِينَاس"),
    ],
)
def test_strips_definite_article_from_display(lemma_ar, lemma_ar_bare, expected):
    assert strip_display_definite_article(lemma_ar, lemma_ar_bare) == expected


@pytest.mark.parametrize(
    "lemma_ar,lemma_ar_bare",
    [
        # Genuinely article-initial lemmas — bare carries ال, must be left alone
        ("اللَّه", "الله"),
        ("الَّذِي", "الذي"),
        ("الْآن", "الان"),
        # No article present at all
        ("كَهْف", "كهف"),
        ("سَمَاوِيّ", "سماوي"),
        # Stripping would diverge from the stored bare → refuse (safety net).
        # Here the bare is the adjective خصوصي, not the article-stripped noun.
        ("الْخُصُوصِيَّة", "خصوصي"),
    ],
)
def test_leaves_headword_untouched(lemma_ar, lemma_ar_bare):
    assert strip_display_definite_article(lemma_ar, lemma_ar_bare) == lemma_ar


def test_empty_inputs_are_safe():
    assert strip_display_definite_article("", "كهف") == ""
    assert strip_display_definite_article("الْكَهْف", "") == "الْكَهْف"
