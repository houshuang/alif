from types import SimpleNamespace

from app.services.lemma_vocalization import lexical_diacritic_count, needs_vocalization, validate_proposal


def _lemma(ar: str, pos: str = "adj"):
    return SimpleNamespace(lemma_ar=ar, pos=pos, gloss_en="test")


def test_case_ending_only_still_needs_vocalization():
    assert lexical_diacritic_count("محظوظةً") == 0
    assert needs_vocalization(_lemma("محظوظةً"))


def test_lexical_diacritics_do_not_need_vocalization():
    assert lexical_diacritic_count("مَحْظُوظَة") > 0
    assert not needs_vocalization(_lemma("مَحْظُوظَة"))
    assert not needs_vocalization(_lemma("الْغُلَام"))


def test_single_letter_particles_keep_their_mark():
    assert lexical_diacritic_count("وَ") == 1
    assert not needs_vocalization(_lemma("وَ", pos="conj"))


def test_validation_rejects_case_ending_only_proposals():
    assert not validate_proposal("محظوظةً", "محظوظة")
    assert validate_proposal("مَحْظُوظَة", "محظوظة")
