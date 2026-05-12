from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Lemma, Sentence, SentenceWord, Story, UserLemmaKnowledge
from app.services.passage_generator import store_maintenance_passage
from scripts.promote_hindawi_passage import (
    PromotionError,
    _build_generated_payload,
    _extract_window,
    _find_duplicate_story,
    _infer_proper_names,
    _select_target_ids,
)
from scripts.rank_hindawi_passages import LemmaContext, LemmaInfo


def _lemma(
    lemma_id: int,
    arabic: str,
    bare: str,
    *,
    gloss: str | None = "gloss",
    pos: str = "noun",
    rank: int = 100,
    word_category: str | None = None,
) -> Lemma:
    return Lemma(
        lemma_id=lemma_id,
        lemma_ar=arabic,
        lemma_ar_bare=bare,
        gloss_en=gloss,
        pos=pos,
        frequency_rank=rank,
        word_category=word_category,
    )


def _ulk(lemma_id: int, state: str, due: datetime | None = None) -> UserLemmaKnowledge:
    return UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state=state,
        fsrs_card_json={"due": due.isoformat()} if due else None,
    )


def _context(*lemmas: Lemma, states: dict[int, str]) -> LemmaContext:
    return LemmaContext(
        infos={
            lemma.lemma_id: LemmaInfo(
                lemma_id=lemma.lemma_id,
                arabic=lemma.lemma_ar,
                bare=lemma.lemma_ar_bare,
                gloss=lemma.gloss_en or "",
                pos=lemma.pos or "",
                rank=lemma.frequency_rank,
                word_category=lemma.word_category,
                canonical_lemma_id=lemma.canonical_lemma_id,
            )
            for lemma in lemmas
        },
        states=states,
    )


def _target_word_dict(lemma: Lemma, state: str) -> dict:
    return {
        "lemma_id": lemma.lemma_id,
        "arabic": lemma.lemma_ar,
        "arabic_bare": lemma.lemma_ar_bare,
        "english": lemma.gloss_en or "",
        "pos": lemma.pos or "",
        "state": state,
    }


def test_extract_window_uses_one_based_sentence_offsets():
    assert _extract_window(["s1", "s2", "s3", "s4"], 2, 3) == ["s2", "s3", "s4"]

    with pytest.raises(PromotionError):
        _extract_window(["s1", "s2"], 1, 3)


def test_build_generated_payload_pairs_arabic_and_translations():
    payload = _build_generated_payload(
        "عنوان",
        "Title",
        ["جملة أولى.", "جملة ثانية."],
        ["First sentence.", "Second sentence."],
    )

    assert payload["style_tag"] == "hindawi_authentic"
    assert payload["sentences"] == [
        {"arabic": "جملة أولى.", "english": "First sentence."},
        {"arabic": "جملة ثانية.", "english": "Second sentence."},
    ]


def test_infer_proper_names_from_single_quoted_words():
    runtime = {
        "normalize_alef": lambda text: text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا"),
        "strip_diacritics": lambda text: text.translate(str.maketrans("", "", "ًٌٍَُِّْ")),
        "strip_punctuation": lambda text: text.replace("«", "").replace("»", "").replace(":", ""),
        "strip_tatweel": lambda text: text.replace("ـ", ""),
        "_is_function_word": lambda text: text in {"في", "من"},
    }

    names = _infer_proper_names(
        [
            "قالَتْ «لَيْلَى»: «إِنَّها هُنَا.»",
            "قالَ: «هَلْ هِيَ فِي مَنْزِلِها؟»",
        ],
        runtime,
    )

    assert names == {"ليلى"}


def test_select_target_ids_prefers_due_repeated_nouns_and_skips_proper_names(db_session):
    now = datetime.now(timezone.utc)
    said = _lemma(1, "قَالَ", "قال", pos="verb", rank=10)
    wolf = _lemma(2, "ذِئْبٌ", "ذئب", pos="noun", rank=900)
    layla = _lemma(
        3,
        "لَيْلَى",
        "ليلى",
        gloss=None,
        pos="noun_prop",
        word_category="proper_name",
    )
    future = _lemma(4, "بَيْتٌ", "بيت", pos="noun", rank=200)
    db_session.add_all([said, wolf, layla, future])
    db_session.add_all([
        _ulk(1, "known", now - timedelta(days=2)),
        _ulk(2, "known", now - timedelta(hours=1)),
        _ulk(3, "known", now - timedelta(days=10)),
        _ulk(4, "known", now + timedelta(days=1)),
    ])
    db_session.flush()

    context = _context(
        said,
        wolf,
        layla,
        future,
        states={1: "known", 2: "known", 3: "known", 4: "known"},
    )
    selected, candidates = _select_target_ids(
        db_session,
        Counter({1: 2, 2: 2, 3: 4, 4: 2}),
        {
            1: {"قالَ"},
            2: {"الذِّئْبُ"},
            3: {"لَيْلَى"},
            4: {"بَيْتٌ"},
        },
        context,
        max_targets=1,
    )

    assert selected == [2]
    assert [candidate.lemma_id for candidate in candidates] == [2, 1, 4]


def test_select_target_ids_rejects_explicit_words_not_in_window(db_session):
    lemma = _lemma(1, "ذِئْبٌ", "ذئب")
    db_session.add(lemma)
    db_session.add(_ulk(1, "known", datetime.now(timezone.utc) - timedelta(days=1)))
    db_session.flush()
    context = _context(lemma, states={1: "known"})

    with pytest.raises(PromotionError, match="not an active, eligible content word"):
        _select_target_ids(
            db_session,
            Counter({1: 1}),
            {1: {"ذِئْبٌ"}},
            context,
            explicit_target_ids=[999],
        )


def test_find_duplicate_story_matches_exact_maintenance_body(db_session):
    story = Story(
        title_ar="عنوان",
        body_ar="سطر أول\nسطر ثان",
        source="maintenance",
        format_type="maintenance_passage",
    )
    db_session.add(story)
    db_session.flush()

    assert _find_duplicate_story(db_session, "سطر أول\nسطر ثان").id == story.id
    assert _find_duplicate_story(db_session, "سطر آخر") is None


def test_store_passage_keeps_proper_name_clickable_but_inert(db_session):
    wolf = _lemma(10, "ذِئْبٌ", "ذئب", gloss="wolf", pos="noun")
    house = _lemma(11, "مَنْزِلٌ", "منزل", gloss="house", pos="noun")
    db_session.add_all([wolf, house])
    db_session.add_all([
        _ulk(10, "known", datetime.now(timezone.utc) - timedelta(days=1)),
        _ulk(11, "known", datetime.now(timezone.utc) + timedelta(days=1)),
    ])
    db_session.flush()

    generated = {
        "title_ar": "اختبار",
        "title_en": "Test",
        "style_tag": "hindawi_authentic",
        "sentences": [
            {"arabic": "الذِّئْبُ مَعَ «لَيْلَى».", "english": "The wolf is with Layla."},
            {"arabic": "الذِّئْبُ هُنَا مَعَ «لَيْلَى».", "english": "The wolf is here with Layla."},
            {"arabic": "الذِّئْبُ فِي مَنْزِلٍ.", "english": "The wolf is in a house."},
        ],
    }

    story = store_maintenance_passage(
        db_session,
        generated,
        target_words=[_target_word_dict(wolf, "known")],
        eligible_words=[_target_word_dict(wolf, "known"), _target_word_dict(house, "known")],
        quality_gate=False,
        proper_names={"ليلى"},
    )

    # Fetch by surface instead of story relationship; Sentence rows are not
    # exposed through Story.
    name_rows = (
        db_session.query(SentenceWord, Lemma)
        .join(Sentence, Sentence.id == SentenceWord.sentence_id)
        .join(Lemma, Lemma.lemma_id == SentenceWord.lemma_id)
        .filter(
            Sentence.story_id == story.id,
            SentenceWord.surface_form.like("%لَيْلَى%"),
        )
        .all()
    )
    assert name_rows
    assert {lemma.word_category for _sw, lemma in name_rows} == {"proper_name"}
    for _sw, lemma in name_rows:
        assert (
            db_session.query(UserLemmaKnowledge)
            .filter(UserLemmaKnowledge.lemma_id == lemma.lemma_id)
            .first()
            is None
        )
