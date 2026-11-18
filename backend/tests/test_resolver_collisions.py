"""Running-text resolver: match provenance and the 2026-11-18 collision repairs.

The mechanisms come from 50 wrong matches on real book pages
(``tests/fixtures/resolver_collisions_2026_11_18.json``,
``research/analysis-2026-11-18-resolver-collisions.md``). Production lemma ids
do not exist here, so each mechanism gets a small seeded vocabulary. CAMeL is
replaced by a stub wherever its answer matters, so the tests do not depend on
the analyser's database.
"""

import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import Lemma, Story, StoryWord, UserLemmaKnowledge
from app.services import ocr_service
from app.services import sentence_validator as sv
from app.services import story_service
from app.services.sentence_validator import (
    MATCH_LAYER_CAMEL,
    MATCH_LAYER_CLITIC,
    MATCH_LAYER_FORMS,
    MATCH_LAYER_FUNCTION,
    MATCH_LAYER_GENERATED,
    MATCH_LAYER_LEMMA,
    LemmaMatchProvenance,
    TokenMapping,
    _generate_verb_conjugations,
    _mapping_provenance_tag,
    build_lemma_lookup,
    lookup_lemma,
    lookup_lemma_id,
    map_tokens_to_lemmas,
)

FIXTURE = Path(__file__).parent / "fixtures" / "resolver_collisions_2026_11_18.json"


def _lemma(lemma_id, lemma_ar, bare, pos="noun", forms=None):
    return SimpleNamespace(
        lemma_id=lemma_id,
        lemma_ar=lemma_ar,
        lemma_ar_bare=bare,
        pos=pos,
        forms_json=forms,
    )


def _camel_says(monkeypatch, answers):
    """Make CAMeL return ``answers[word]`` (a lemma id) and nothing else."""

    def fake(word, _lookup):
        return answers.get(word)

    monkeypatch.setattr(sv, "_camel_disambiguate", fake)


def _resolve(surface, lookup):
    provenance = LemmaMatchProvenance()
    return lookup_lemma_id(surface, lookup, provenance), provenance


class TestFixture:
    def test_fixture_shape(self):
        doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
        rows = doc["rows"]
        wrong = [row for row in rows if row["verdict"] == "different"]
        assert len(rows) == 188
        assert len(wrong) == 50
        assert set(Counter(row["mechanism"] for row in wrong)) == set(
            doc["mechanisms"]
        )
        # The right lemma existed in ten of the fifty; the repairs reach five
        # of those, and every CAMeL last-resort match is now labelled a guess.
        assert sum(1 for row in wrong if row["word_in_text_lemma_id"]) == 10
        assert (
            sum(
                1
                for row in wrong
                if row["word_in_text_lemma_id"]
                and row["after_lemma_id"] == row["word_in_text_lemma_id"]
            )
            == 5
        )
        assert all(
            row["after_layer"] == MATCH_LAYER_CAMEL
            for row in wrong
            if row["mechanism"].startswith("M3")
        )


class TestProvenance:
    @pytest.fixture
    def lookup(self):
        return build_lemma_lookup(
            [
                _lemma(1, "كِتَاب", "كتاب", forms={"plural": "كُتُب"}),
                _lemma(
                    2, "دَرَسَ", "درس", pos="verb", forms={"present": "يَدْرُسُ"}
                ),
                _lemma(3, "فِي", "في", pos="prep"),
            ]
        )

    def test_lemma_key(self, lookup):
        lemma_id, provenance = _resolve("الكتاب", lookup)
        assert lemma_id == 1
        assert provenance.layer == MATCH_LAYER_LEMMA
        assert not provenance.is_guess

    def test_forms_json_key(self, lookup):
        lemma_id, provenance = _resolve("كتب", lookup)
        assert (lemma_id, provenance.layer) == (1, MATCH_LAYER_FORMS)

    def test_generated_key(self, lookup):
        lemma_id, provenance = _resolve("يدرسون", lookup)
        assert (lemma_id, provenance.layer) == (2, MATCH_LAYER_GENERATED)

    def test_clitic_reports_the_stem_key_kind(self, lookup, monkeypatch):
        _camel_says(monkeypatch, {})
        lemma_id, provenance = _resolve("وكتابها", lookup)
        assert lemma_id == 1
        assert provenance.layer == MATCH_LAYER_CLITIC
        assert provenance.key_source == MATCH_LAYER_LEMMA

    def test_function_word(self, lookup):
        lemma_id, provenance = _resolve("في", lookup)
        assert (lemma_id, provenance.layer) == (3, MATCH_LAYER_FUNCTION)

    def test_camel_last_resort_is_a_guess(self, lookup, monkeypatch):
        _camel_says(monkeypatch, {"وزارة": 2})
        lemma_id, provenance = _resolve("وزارة", lookup)
        assert lemma_id == 2
        assert provenance.layer == MATCH_LAYER_CAMEL
        assert provenance.is_guess

    def test_no_match_has_no_layer(self, lookup, monkeypatch):
        _camel_says(monkeypatch, {})
        lemma_id, provenance = _resolve("وزارة", lookup)
        assert lemma_id is None
        assert provenance.layer is None

    def test_existing_call_shapes_are_unchanged(self, lookup):
        assert lookup_lemma_id("الكتاب", lookup) == 1
        assert lookup_lemma("كتاب", lookup) == 1
        alternatives: list[int] = []
        flag = [False]
        assert (
            lookup_lemma(
                "كتاب", lookup, out_alternatives=alternatives, out_via_clitic=flag
            )
            == 1
        )
        assert alternatives == [] and flag == [False]

    def test_plain_dict_lookup_counts_as_lemma_keys(self):
        provenance = LemmaMatchProvenance()
        assert lookup_lemma("كتاب", {"كتاب": 7}, out_provenance=provenance) == 7
        assert provenance.layer == MATCH_LAYER_LEMMA


class TestAgreeingCliticParse:
    """M1: ابنها → أَب although both stems said اِبْن."""

    def test_agreeing_lemma_stem_beats_an_unrelated_camel_reading(
        self, monkeypatch
    ):
        lookup = build_lemma_lookup(
            [_lemma(158, "أَب", "اب"), _lemma(79, "اِبْن", "ابن")]
        )
        _camel_says(monkeypatch, {"ابنها": 158})
        lemma_id, provenance = _resolve("ابنها", lookup)
        assert lemma_id == 79
        assert provenance.layer == MATCH_LAYER_CLITIC

    def test_camel_keeps_its_override_between_rows_of_one_lexeme(
        self, monkeypatch
    ):
        # دُرُوس kept as its own lemma next to دَرْس whose plural is دروس:
        # credit must stay where it went before the repair.
        lookup = build_lemma_lookup(
            [
                _lemma(10, "دَرْس", "درس", forms={"plural": "دُرُوس"}),
                _lemma(11, "دُرُوس", "دروس"),
            ]
        )
        _camel_says(monkeypatch, {"دروسه": 10})
        lemma_id, provenance = _resolve("دروسه", lookup)
        assert lemma_id == 10
        assert provenance.alternatives == [11]
        assert not provenance.is_guess

    def test_whole_word_with_accusative_alif_beats_the_clitic_parse(
        self, monkeypatch
    ):
        lookup = build_lemma_lookup(
            [_lemma(20, "لَيْل", "ليل"), _lemma(21, "يَلّا", "يلا", pos="expr")]
        )
        _camel_says(monkeypatch, {"ليلا": 20})
        lemma_id, provenance = _resolve("لَيْلًا", lookup)
        assert lemma_id == 20
        assert provenance.is_guess
        assert provenance.alternatives == [21]

    def test_short_stem_does_not_outrank_camel(self, monkeypatch):
        lookup = build_lemma_lookup(
            [_lemma(30, "أُذُن", "اذن"), _lemma(31, "إِذ", "اذ", pos="particle")]
        )
        # اذن itself is a key, so use a word that only strips down to اذ.
        _camel_says(monkeypatch, {"اذكم": 30})
        lemma_id, provenance = _resolve("اذكم", lookup)
        assert lemma_id == 30
        assert provenance.is_guess

    def test_agreeing_stems_without_camel_still_resolve(self, monkeypatch):
        lookup = build_lemma_lookup([_lemma(79, "اِبْن", "ابن")])
        _camel_says(monkeypatch, {})
        assert _resolve("ابنها", lookup)[0] == 79


class TestAlAddLayer:
    """M2: فحين → لَفَحَ through the generated key الفحين."""

    def _lookup(self):
        return build_lemma_lookup(
            [
                _lemma(
                    3256, "لَفَحَ", "لفح", pos="verb", forms={"present": "يَلْفَحُ"}
                ),
                _lemma(2068, "حِينَ", "حين", pos="particle"),
            ]
        )

    def test_generated_key_is_not_a_definite_form(self, monkeypatch):
        lookup = self._lookup()
        # Put the old impossible form back so the layer itself is under test.
        lookup.set_if_new("الفحين", 3256, "conj:الفحين", source=MATCH_LAYER_GENERATED)
        _camel_says(monkeypatch, {})
        lemma_id, provenance = _resolve("فحين", lookup)
        assert lemma_id == 2068
        assert provenance.layer == MATCH_LAYER_CLITIC

    def test_direct_only_lookup_skips_it_too(self):
        lookup = self._lookup()
        lookup.set_if_new("الفحين", 3256, "conj:الفحين", source=MATCH_LAYER_GENERATED)
        assert sv.lookup_lemma_direct("فحين", lookup) is None

    def test_al_add_still_serves_other_keys(self):
        lookup = build_lemma_lookup([_lemma(1, "كِتَاب", "كتاب")])
        lookup.set_if_new("الشمس", 5, "الشمس")
        assert lookup_lemma("شمس", lookup) == 5


class TestGeneratedConjugations:
    """M2 at the source: only combinations Arabic has."""

    def test_no_first_person_prefix_with_plural_or_dual_suffix(self):
        forms = _generate_verb_conjugations("لفح", "يلفح")
        for impossible in (
            "الفحين", "الفحون", "الفحان",
            "نلفحون", "نلفحان", "نلفحين", "نلفحن", "نلفحي",
            "يلفحين", "يلفحي",
        ):
            assert impossible not in forms

    def test_real_forms_are_kept(self):
        forms = _generate_verb_conjugations("كتب", "يكتب")
        for real in (
            "يكتب", "تكتب", "اكتب", "نكتب",
            "يكتبون", "يكتبان", "يكتبن",
            "تكتبون", "تكتبان", "تكتبين", "تكتبن", "تكتبي",
            "اكتبي", "اكتبن",
            "كتبت", "كتبوا", "كتبنا",
        ):
            assert real in forms


class TestCollisionStems:
    """M4: a homograph stem was decided by insertion order, silently."""

    def test_siblings_of_a_collision_stem_are_reported(self, monkeypatch):
        lookup = build_lemma_lookup(
            [
                _lemma(1185, "آمَرَ", "امر", pos="verb"),
                _lemma(2501, "أَمْر", "امر"),
            ]
        )
        _camel_says(monkeypatch, {})
        lemma_id, provenance = _resolve("لأمر", lookup)
        assert lemma_id == 1185
        assert provenance.alternatives == [2501]

    def test_map_tokens_carries_alternatives_and_layer(self, monkeypatch):
        lookup = build_lemma_lookup(
            [
                _lemma(1185, "آمَرَ", "امر", pos="verb"),
                _lemma(2501, "أَمْر", "امر"),
            ]
        )
        _camel_says(monkeypatch, {"وزارة": 2501})
        mappings = map_tokens_to_lemmas(["لأمر", "وزارة"], lookup, 0, "")
        assert mappings[0].alternative_lemma_ids == [2501]
        assert mappings[0].match_layer == MATCH_LAYER_CLITIC
        assert not mappings[0].via_camel
        assert mappings[1].lemma_id == 2501
        assert mappings[1].via_camel
        assert mappings[1].match_layer == MATCH_LAYER_CAMEL


class TestVerifierTag:
    def test_guess_is_labelled_for_the_verifier(self):
        guess = TokenMapping(0, "وزارة", 1, False, False, via_camel=True)
        clitic = TokenMapping(0, "بالكتاب", 1, False, False, via_clitic=True)
        plain = TokenMapping(0, "كتاب", 1, False, False)
        assert "morphological guess" in _mapping_provenance_tag(guess)
        assert _mapping_provenance_tag(clitic) == " [via clitic stripping]"
        assert _mapping_provenance_tag(plain) == ""

    def test_prompt_carries_the_tag(self, monkeypatch):
        captured = {}

        def fake_completion(prompt, **_kwargs):
            captured["prompt"] = prompt
            return {"corrections": []}

        monkeypatch.setattr("app.services.llm.generate_completion", fake_completion)
        lemma = SimpleNamespace(
            lemma_id=1, lemma_ar="زَارَ", lemma_ar_bare="زار", pos="verb",
            gloss_en="to visit",
        )
        sv.verify_and_correct_mappings_llm(
            "وزارة",
            "a ministry",
            [TokenMapping(0, "وزارة", 1, False, False, via_camel=True)],
            {1: lemma},
        )
        assert "morphological guess" in captured["prompt"]


class TestUnverifiedWriters:
    def test_textbook_scan_refuses_a_guess(self, monkeypatch):
        lookup = build_lemma_lookup([_lemma(195, "زَارَ", "زار", pos="verb")])
        _camel_says(monkeypatch, {"وزارة": 195})
        assert ocr_service._lookup_vocabulary_match(None, "وزارة", lookup) is None
        assert ocr_service._lookup_vocabulary_match(None, "زار", lookup) == 195
        # base lemma first, then the surface
        assert ocr_service._lookup_vocabulary_match("زار", "زرنا", lookup) == 195

    def _story_with_words(self, db, body, monkeypatch, **kwargs):
        visit = Lemma(lemma_ar="زَارَ", lemma_ar_bare="زار", pos="verb", gloss_en="to visit")
        house = Lemma(lemma_ar="بَيْت", lemma_ar_bare="بيت", pos="noun", gloss_en="house")
        db.add_all([visit, house])
        db.flush()
        db.add(UserLemmaKnowledge(lemma_id=visit.lemma_id, knowledge_state="known"))
        story = Story(body_ar=body, source="imported", status="active")
        db.add(story)
        db.flush()
        lookup = build_lemma_lookup([visit, house])
        _camel_says(monkeypatch, {"وزارة": visit.lemma_id})
        monkeypatch.setattr(
            story_service,
            "find_best_db_match",
            lambda *_a, **_k: {"lex_bare": "زار"},
        )
        story_service._create_story_words(
            db, story, body, lookup, {visit.lemma_id: "known"}, **kwargs
        )
        db.flush()
        words = (
            db.query(StoryWord)
            .filter(StoryWord.story_id == story.id)
            .order_by(StoryWord.position)
            .all()
        )
        return visit, house, words

    def test_story_import_leaves_a_guess_unmapped(self, db_session, monkeypatch):
        visit, house, words = self._story_with_words(
            db_session, "وزارة البيت", monkeypatch
        )
        assert [w.lemma_id for w in words] == [None, house.lemma_id]
        assert words[0].is_known_at_creation is False

    def test_generated_passages_may_keep_the_guess(self, db_session, monkeypatch):
        visit, house, words = self._story_with_words(
            db_session,
            "وزارة البيت",
            monkeypatch,
            trust_morphological_guesses=True,
        )
        assert [w.lemma_id for w in words] == [visit.lemma_id, house.lemma_id]


def _camel_analyses(monkeypatch, analyses):
    """Make CAMeL's analyser return ``analyses[word]`` (a list of lex strings)."""

    def fake(word):
        return [{"lex": lex} for lex in analyses.get(word, [])]

    monkeypatch.setattr("app.services.morphology.analyze_word_camel", fake)


# Seven fixture rows whose CAMeL headword is a word the vocabulary lacks and
# which coincides with a conjugation, imperative, masdar, participle or
# elative key of another lemma (mechanism M3a).
_HEADWORD_ON_DERIVED_KEY = [
    # surface, CAMeL headword, lemma that owned the coinciding key
    ("تتعرف", "تَعَرَّف", _lemma(389, "عَرَفَ", "عرف", "verb", {"present": "يَعْرِفُ"})),
    ("سيتقدم", "تَقَدَّم", _lemma(561, "قَدَمَ", "قدم", "verb", {"present": "يَقْدُمُ"})),
    ("أمضت", "أَمْضَى", _lemma(2444, "ماضِي", "ماضي", "adj", {"elative": "أَمْضَى"})),
    ("تقارير", "تَقْرِير", _lemma(2090, "قَرّ", "قر", "verb", {"masdar": "تَقْرِير"})),
    ("خاطفة", "خاطِف", _lemma(3577, "خَطَف", "خطف", "verb", {"active_participle": "خَاطِف"})),
    ("باندفاعة", "ٱِنْدِفاع", _lemma(3454, "اِنْدَفَعَ", "اندفع", "verb", {"masdar": "اِنْدِفَاع"})),
    ("أُسقطا", "أَسْقَط", _lemma(3522, "سَقَطَ", "سقط", "verb", {"imperative": "اُسْقُطْ"})),
]


class TestStrictFallback:
    """Rank 3: for callers whose result nothing checks afterwards."""

    @pytest.mark.parametrize("surface, headword, owner", _HEADWORD_ON_DERIVED_KEY)
    def test_headword_of_a_missing_word_is_not_a_match(
        self, monkeypatch, surface, headword, owner
    ):
        lookup = build_lemma_lookup([owner])
        bare = sv.strip_diacritics(surface)
        _camel_analyses(monkeypatch, {bare: [headword]})
        provenance = LemmaMatchProvenance()
        assert (
            lookup_lemma_id(surface, lookup, provenance, strict_fallback=True)
            is None
        )
        assert provenance.layer is None

    @pytest.mark.parametrize("surface, headword, owner", _HEADWORD_ON_DERIVED_KEY)
    def test_default_path_keeps_the_wider_match(
        self, monkeypatch, surface, headword, owner
    ):
        # Generation and mapping verification rely on this and check it with
        # an LLM; the default must not change.
        lookup = build_lemma_lookup([owner])
        bare = sv.strip_diacritics(surface)
        _camel_analyses(monkeypatch, {bare: [headword]})
        provenance = LemmaMatchProvenance()
        assert lookup_lemma_id(surface, lookup, provenance) == owner.lemma_id
        assert provenance.is_guess
        mapping = map_tokens_to_lemmas([surface], lookup, 0, "")[0]
        assert mapping.lemma_id == owner.lemma_id and mapping.via_camel

    def test_headword_must_keep_its_hamza(self, monkeypatch):
        lookup = build_lemma_lookup(
            [_lemma(1774, "أَلْف", "الف"), _lemma(439, "آسِف", "اسف", "expr")]
        )
        _camel_analyses(
            monkeypatch, {"الفتى": ["آلِف"], "تأسف": ["تَأَسُّف", "أَسِف"]}
        )
        assert lookup_lemma_id("الفتى", lookup) == 1774
        assert lookup_lemma_id("تأسف", lookup) == 439
        assert lookup_lemma_id("الفتى", lookup, strict_fallback=True) is None
        assert lookup_lemma_id("تأسف", lookup, strict_fallback=True) is None

    def test_a_real_headword_still_matches_as_a_guess(self, monkeypatch):
        lookup = build_lemma_lookup([_lemma(7, "مُدِير", "مدير")])
        _camel_analyses(monkeypatch, {"مدراء": ["مُدِير"]})
        provenance = LemmaMatchProvenance()
        assert (
            lookup_lemma_id("مدراء", lookup, provenance, strict_fallback=True) == 7
        )
        assert provenance.is_guess

    def test_accusative_alif_resolves_without_camel(self, monkeypatch):
        lookup = build_lemma_lookup(
            [
                _lemma(1, "وَزِير", "وزير"),
                _lemma(2, "شَيْء", "شيء"),
                _lemma(20, "لَيْل", "ليل"),
                _lemma(21, "يَلّا", "يلا", "expr"),
            ]
        )
        _camel_analyses(monkeypatch, {})
        _camel_says(monkeypatch, {})
        for surface, lemma_id in (
            ("وزيراً", 1),
            ("وَزِيرًا", 1),
            ("شيئاً", 2),
            ("لَيْلًا", 20),
            ("ووزيراً", 1),
        ):
            provenance = LemmaMatchProvenance()
            assert (
                lookup_lemma_id(surface, lookup, provenance, strict_fallback=True)
                == lemma_id
            ), surface
            assert not provenance.is_guess
        # Without the mark there is no evidence the alif is a case ending.
        assert lookup_lemma_id("وزيرا", lookup, strict_fallback=True) is None
        # The default path has no such layer.
        assert lookup_lemma_id("وزيراً", lookup) is None

    def test_a_lemma_that_ends_in_the_alif_wins(self, monkeypatch):
        lookup = build_lemma_lookup(
            [
                _lemma(30, "طَبْع", "طبع"),
                _lemma(31, "طَبْعاً", "طبعا", "adv"),
                _lemma(32, "عَصًا", "عصا"),
            ]
        )
        _camel_analyses(monkeypatch, {})
        _camel_says(monkeypatch, {})
        assert lookup_lemma_id("طبعاً", lookup, strict_fallback=True) == 31
        assert lookup_lemma_id("وطبعاً", lookup, strict_fallback=True) == 31
        assert lookup_lemma_id("عَصًا", lookup, strict_fallback=True) == 32

    def test_unverified_callers_ask_for_it(self, db_session, monkeypatch):
        from app.routers import discover

        seen = []
        real = discover.lookup_lemma_id

        def spy(*args, **kwargs):
            seen.append(kwargs.get("strict_fallback"))
            return real(*args, **kwargs)

        monkeypatch.setattr(discover, "lookup_lemma_id", spy)
        discover.discover_words_in_text(db_session, "وزيراً", count=8)
        assert seen and all(seen)

        lookup = build_lemma_lookup([_lemma(1, "وَزِير", "وزير")])
        _camel_analyses(monkeypatch, {})
        # textbook scan: the scanned surface carries the accusative mark
        assert (
            ocr_service._lookup_vocabulary_match(None, "وزيرا", lookup, "وَزِيرًا")
            == 1
        )
        assert ocr_service._lookup_vocabulary_match(None, "وزيرا", lookup) is None
