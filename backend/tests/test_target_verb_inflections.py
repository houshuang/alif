"""The target check accepts a verb target's own conjugations (2026-11-18).

Before, ``validate_sentence`` matched only the target's bare (± clitics), so
اِسْتَحْيَتِ for اِسْتَحْيَا or كَنَسَتِ for كَنَسَ failed with "Target word not
found". Only the target lemma's own inflections may count.
"""

from app.services.sentence_validator import (
    build_lemma_lookup,
    map_tokens_to_lemmas,
    refresh_target_mapping_flags,
    tokenize_display,
    validate_sentence,
    validate_sentence_multi_target,
    verb_target_inflections,
)


class _Lemma:
    def __init__(self, lemma_id, lemma_ar, lemma_ar_bare, pos, forms_json=None):
        self.lemma_id = lemma_id
        self.lemma_ar = lemma_ar
        self.lemma_ar_bare = lemma_ar_bare
        self.pos = pos
        self.forms_json = forms_json
        self.gates_completed_at = object()
        self.word_category = None


ISTAHYA = _Lemma(
    2759, "اسْتَحْيَا", "استحيا", "verb",
    {
        "present": "يَسْتَحْيِي",
        "past_3fs": "اِسْتَحْيَتْ",
        "past_3p": "اِسْتَحْيَوْا",
        "past_1s": "اِسْتَحْيَيْتُ",
        "past_3fp": "اِسْتَحْيَيْنَ",
        "present_3fp": "يَسْتَحْيِينَ",
        "present_3mp": "يَسْتَحْيُونَ",
        "imperative": "اِسْتَحْيِ",
        "masdar": "اِسْتِحْيَاء",
        "active_participle": "مُسْتَحْيٍ",
        "verb_form": "X",
    },
)
RAMA = _Lemma(10, "رَمَى", "رمى", "verb", {"present": "يَرْمِي", "past_1s": "رَمَيْتُ"})
DAA = _Lemma(11, "دَعَا", "دعا", "verb", {"present": "يَدْعُو", "past_1s": "دَعَوْتُ"})
BAQIYA = _Lemma(12, "بَقِيَ", "بقي", "verb", {"present": "يَبْقَى", "past_1s": "بَقِيتُ"})
KANASA = _Lemma(
    13, "كَنَسَ", "كنس", "verb",
    {"present": "يَكْنُسُ", "past_3fs": "كَنَسَتْ", "past_1s": "كَنَسْتُ"},
)
# Same root as رَمَى, different lemma.
IRTAMA = _Lemma(20, "اِرْتَمَى", "ارتمى", "verb", {"present": "يَرْتَمِي"})
MARMA = _Lemma(21, "مَرْمًى", "مرمى", "noun")
# Different words whose skeletons resemble weak-verb forms.
BAQIYYA = _Lemma(22, "بَقِيَّة", "بقية", "noun")
BADA_A = _Lemma(
    23, "بَدَأَ", "بدا", "verb", {"present": "يَبْدَأُ", "past_1s": "بَدَأْتُ"}
)
SAMMA = _Lemma(24, "سَمَّى", "سمى", "verb", {"present": "يُسَمِّي", "past_1s": "سَمَّيْتُ"})
# As in production: the noun registers اسمي as a stored form.
ISM = _Lemma(25, "اِسْم", "اسم", None, {"variant_اسمي": "اسمي"})
BINT = _Lemma(30, "بِنْت", "بنت", "noun")
WALAD = _Lemma(31, "وَلَد", "ولد", "noun")

LEMMAS = [
    ISTAHYA, RAMA, DAA, BAQIYA, KANASA, IRTAMA, MARMA, BAQIYYA, BADA_A,
    SAMMA, ISM, BINT, WALAD,
]


def _lookup():
    return build_lemma_lookup(LEMMAS)


def _found(sentence, lemma, lookup=None):
    lookup = lookup or _lookup()
    return validate_sentence(
        sentence,
        lemma.lemma_ar_bare,
        set(),
        known_lemma_lookup=lookup,
        comprehensive_lemma_lookup=lookup,
        target_lemma_id=lemma.lemma_id,
    ).target_found


def test_defective_verb_conjugations_are_the_target():
    for sentence in (
        "اِسْتَحْيَتِ الْبِنْتُ",
        "تَسْتَحْيِي الْبِنْتُ",
        "فَاسْتَحْيَتْ الْبِنْتُ",
        "يَسْتَحْيُونَ",
    ):
        assert _found(sentence, ISTAHYA), sentence
    for sentence in ("رَمَتِ الْبِنْتُ", "رَمَوْا", "يَرْمِي الْوَلَدُ", "لَمْ يَرْمِ الْوَلَدُ", "تَرْمِينَ"):
        assert _found(sentence, RAMA), sentence
    for sentence in ("دَعَوْا الْوَلَدَ", "دَعَتِ الْبِنْتُ", "دَعَوْتُ الْوَلَدَ", "يَدْعُو", "يَدْعُونَ"):
        assert _found(sentence, DAA), sentence
    for sentence in ("بَقِيَتِ الْبِنْتُ", "بَقُوا", "يَبْقَى الْوَلَدُ", "يَبْقَوْنَ", "لَمْ يَبْقَ"):
        assert _found(sentence, BAQIYA), sentence


def test_sound_verb_conjugation_is_the_target():
    assert _found("كَنَسَتِ الْبِنْتُ", KANASA)
    assert _found("يَكْنُسُ الْوَلَدُ", KANASA)


def test_without_target_id_the_old_bare_check_is_unchanged():
    lookup = _lookup()
    result = validate_sentence(
        "رَمَتِ الْبِنْتُ", "رمى", set(),
        known_lemma_lookup=lookup, comprehensive_lemma_lookup=lookup,
    )
    assert not result.target_found


def test_same_root_different_lemma_is_not_the_target():
    assert not _found("اِرْتَمَى الْوَلَدُ", RAMA)
    assert not _found("يَرْتَمِي الْوَلَدُ", RAMA)
    assert not _found("الْمَرْمَى", RAMA)


def test_unrelated_word_with_a_matching_skeleton_is_not_the_target():
    # بَقِيَّة "remainder" is not a form of بَقِيَ.
    assert not _found("الْبَقِيَّةُ", BAQIYA)
    # بَدَأَ looks final-weak after alef normalization (بدا) but is not:
    # its stored present يَبْدَأُ decides, so no بدت/بدوا are generated.
    assert "بدت" not in verb_target_inflections("بدا", BADA_A.forms_json)
    # اِسْمِي "my name" is also a generated 1s present of سَمَّى; the
    # resolver maps it to the noun, so it is not the target.
    assert not _found("اِسْمِي", SAMMA)


def test_inflections_exclude_masdar_and_participles():
    forms = verb_target_inflections("استحيا", ISTAHYA.forms_json)
    assert "استحياء" not in forms
    assert "مستحي" not in forms
    assert "استحيت" in forms and "تستحيي" in forms


def test_mapping_flags_and_integrity_accept_the_conjugated_target():
    lookup = _lookup()
    tokens = tokenize_display("اِسْتَحْيَتِ الْبِنْتُ")
    mappings = map_tokens_to_lemmas(tokens, lookup, ISTAHYA.lemma_id, "استحيا")
    assert mappings[0].lemma_id == ISTAHYA.lemma_id
    assert mappings[0].is_target
    assert refresh_target_mapping_flags(
        mappings, lookup, {"استحيا": ISTAHYA.lemma_id},
        required_target_ids={ISTAHYA.lemma_id},
    )


def test_integrity_still_rejects_a_conjugation_mapped_elsewhere():
    lookup = _lookup()
    tokens = tokenize_display("اِسْمِي")
    mappings = map_tokens_to_lemmas(tokens, lookup, SAMMA.lemma_id, "سمى")
    assert mappings[0].lemma_id == ISM.lemma_id
    assert not mappings[0].is_target
    assert not refresh_target_mapping_flags(
        mappings, lookup, {"سمى": SAMMA.lemma_id},
        required_target_ids={SAMMA.lemma_id},
    )


def test_multi_target_accepts_conjugated_targets():
    lookup = _lookup()
    result = validate_sentence_multi_target(
        "رَمَتِ الْبِنْتُ وَكَنَسَتْ",
        {"رمى": RAMA.lemma_id, "كنس": KANASA.lemma_id},
        set(),
        known_lemma_lookup=lookup,
        comprehensive_lemma_lookup=lookup,
    )
    assert result.targets_found == {"رمى": True, "كنس": True}
