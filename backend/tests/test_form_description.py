"""The form line in the reading word panel, from CAMeL readings (copied from real output)."""
from app.services.form_description import describe_surface_form


def verb(diac, lex, asp, per, gen, num, vox="a", prc1="0"):
    return dict(diac=diac, lex=lex, pos="verb", asp=asp, per=per, gen=gen, num=num, vox=vox, prc1=prc1, enc0="0")


def noun(diac, lex, num="s", gen="m", enc0="0"):
    return dict(diac=diac, lex=lex, pos="noun", asp="na", per="na", gen=gen, num=num, vox="na", enc0=enc0)


def test_verb_tense_and_subject():
    readings = [verb("يَسْتَخْدِمُونَ", "ٱِسْتَخْدَم", "i", "3", "m", "p"),
                verb("يُسْتَخْدَمُونَ", "ٱِسْتَخْدَم", "i", "3", "m", "p", vox="p")]  # passive: vowels differ
    assert describe_surface_form("يَسْتَخْدِمُونَ", "اِسْتَخْدَمَ", analyses=readings) == "present · they (m.)"


def test_two_possible_subjects_are_both_named():
    readings = [verb("تَكْتُب", "كَتَب", "i", "2", "m", "s"), verb("تَكْتُب", "كَتَب", "i", "3", "f", "s"),
                verb("تُكْتَب", "كَتَب", "i", "3", "f", "s", vox="p")]
    assert describe_surface_form("تَكْتُبُ", "كَتَبَ", analyses=readings) == "present · you (m.) or she"


def test_omitted_fatha_before_alif_and_sukun_decide_the_reading():
    readings = [verb("كَتَبْناهُ", "كَتَب", "p", "1", "m", "p")]
    assert describe_surface_form("كَتَبْنَاهُ", "كَتَبَ", analyses=readings) == "past · we"
    readings = [verb("اقْتَرَبَت", "اِقْتَرَب", "p", "3", "f", "s"), verb("اقْتَرَبْتَ", "اِقْتَرَب", "p", "2", "m", "s")]
    assert describe_surface_form("اقْتَرَبَتْ", "اقْتَرَبَ", analyses=readings) == "past · she"


def test_future_passive_and_noun_forms():
    assert describe_surface_form("سَيَذْهَبُونَ", "ذَهَبَ", analyses=[
        verb("سَيَذْهَبُونَ", "ذَهَب", "i", "3", "m", "p", prc1="sa_fut")]) == "future · they (m.)"
    assert describe_surface_form("كُتِبَ", "كَتَبَ", analyses=[
        verb("كُتِبَ", "كَتَب", "p", "3", "m", "s", vox="p"), noun("كُتُب", "كِتاب", num="p")]) == "past · passive · he"
    assert describe_surface_form("المُعَلِّمَاتُ", "مُعَلِّم", analyses=[
        noun("المُعَلِّماتُ", "مُعَلِّم", num="p", gen="f")]) == "feminine · plural"


def test_attached_words_and_dictionary_forms_say_nothing():
    # بَيْتِهَا: only "her" is attached; the verb بَيَّت is a different word (shadda).
    readings = [noun("بَيْتِها", "بَيْت", enc0="3fs_poss"), verb("بَيَّتِّها", "بَيَّت", "p", "2", "f", "s")]
    assert describe_surface_form("بَيْتِهَا", "بَيْت", analyses=readings) is None
    # The dictionary form itself, even when a homograph reading would add "or you".
    readings = [verb("لَفَتَ", "لَفَت", "p", "3", "m", "s"), verb("لَفَتَّ", "لَفَت", "p", "2", "m", "s")]
    assert describe_surface_form("لَفَتَ", "لَفَتَ", analyses=readings) is None
    assert describe_surface_form("وَهَاجَرَ", "هَاجَرَ", analyses=[verb("وَهاجَرَ", "هاجَر", "p", "3", "m", "s")]) is None


def test_no_matching_reading_or_disagreeing_tense_gives_nothing():
    assert describe_surface_form("اُكْتُبْ", "كَتَبَ", analyses=[verb("أَكْتُب", "كَتَب", "i", "1", "m", "s")]) is None
    readings = [verb("تَعالَى", "تَعالَى", "p", "3", "m", "s"), verb("تَعالَ", "تَعالَى", "c", "2", "m", "s")]
    assert describe_surface_form("فَتَعالَى", "تَعَالَى", analyses=readings) is None
