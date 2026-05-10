import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.models import Lemma, Sentence, StoryWord, UserLemmaKnowledge
from app.services.passage_generator import (
    PassageGenerationError,
    _eligible_passage_words,
    _rank_targets_for_passage,
    generate_maintenance_passage_agentic,
    store_maintenance_passage,
)
from app.services.sentence_selector import (
    SentenceCandidate,
    _group_maintenance_passages,
)


def _seed_lemma(db, lemma_id, arabic, bare, gloss, state="known", box=None):
    lemma = Lemma(
        lemma_id=lemma_id,
        lemma_ar=arabic,
        lemma_ar_bare=bare,
        gloss_en=gloss,
        pos="noun",
    )
    db.add(lemma)
    db.flush()
    db.add(UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state=state,
        acquisition_box=box,
        introduced_at=datetime.now(timezone.utc),
        source="study",
    ))
    db.flush()
    return lemma


def test_eligible_passage_words_excludes_box1_acquisition(db_session):
    _seed_lemma(db_session, 1, "كِتَاب", "كتاب", "book", state="known")
    _seed_lemma(db_session, 2, "بَيْت", "بيت", "house", state="acquiring", box=1)
    _seed_lemma(db_session, 3, "وَلَد", "ولد", "boy", state="acquiring", box=2)
    db_session.commit()

    eligible = _eligible_passage_words(db_session)

    assert {w["lemma_id"] for w in eligible} == {1, 3}


def test_store_maintenance_passage_creates_story_and_sentence_rows(db_session):
    words = [
        _seed_lemma(db_session, 1, "كِتَاب", "كتاب", "book"),
        _seed_lemma(db_session, 2, "بَيْت", "بيت", "house"),
        _seed_lemma(db_session, 3, "وَلَد", "ولد", "boy"),
    ]
    db_session.commit()

    target_words = [
        {"lemma_id": w.lemma_id, "arabic": w.lemma_ar, "english": w.gloss_en, "pos": w.pos}
        for w in words[:2]
    ]
    eligible_words = [
        {"lemma_id": w.lemma_id, "arabic": w.lemma_ar, "english": w.gloss_en, "pos": w.pos}
        for w in words
    ]
    generated = {
        "title_ar": "ذِكْرَى صَغِيرَةٌ",
        "title_en": "A small memory",
        "style_tag": "nostalgic",
        "sentences": [
            {"arabic": "كِتَابٌ بَيْتٌ.", "english": "A book, a house."},
            {"arabic": "وَلَدٌ كِتَابٌ.", "english": "A boy, a book."},
            {"arabic": "بَيْتٌ وَلَدٌ.", "english": "A house, a boy."},
        ],
    }

    story = store_maintenance_passage(
        db_session,
        generated,
        target_words=target_words,
        eligible_words=eligible_words,
        quality_gate=False,
    )

    assert story.format_type == "maintenance_passage"
    assert story.metadata_json["style_tag"] == "nostalgic"
    sentences = db_session.query(Sentence).filter(Sentence.story_id == story.id).all()
    assert len(sentences) == 3
    assert {s.source for s in sentences} == {"passage"}
    story_words = db_session.query(StoryWord).filter(StoryWord.story_id == story.id).all()
    assert {sw.sentence_index for sw in story_words} == {0, 1, 2}


def test_store_maintenance_passage_rejects_no_shared_anchor(db_session):
    words = [
        _seed_lemma(db_session, 1, "كِتَاب", "كتاب", "book"),
        _seed_lemma(db_session, 2, "بَيْت", "بيت", "house"),
        _seed_lemma(db_session, 3, "وَلَد", "ولد", "boy"),
        _seed_lemma(db_session, 4, "مَدْرَسَة", "مدرسة", "school"),
        _seed_lemma(db_session, 5, "قَلَم", "قلم", "pen"),
        _seed_lemma(db_session, 6, "بَاب", "باب", "door"),
    ]
    db_session.commit()
    target_words = [
        {"lemma_id": w.lemma_id, "arabic": w.lemma_ar, "english": w.gloss_en, "pos": w.pos}
        for w in words[:2]
    ]
    eligible_words = [
        {"lemma_id": w.lemma_id, "arabic": w.lemma_ar, "english": w.gloss_en, "pos": w.pos}
        for w in words
    ]
    generated = {
        "title_ar": "أَمْثِلَةٌ",
        "title_en": "Examples",
        "style_tag": "informative",
        "sentences": [
            {"arabic": "كِتَابٌ بَيْتٌ.", "english": "A book, a house."},
            {"arabic": "وَلَدٌ مَدْرَسَةٌ.", "english": "A boy, a school."},
            {"arabic": "قَلَمٌ بَابٌ.", "english": "A pen, a door."},
        ],
    }

    try:
        store_maintenance_passage(
            db_session,
            generated,
            target_words=target_words,
            eligible_words=eligible_words,
            quality_gate=False,
        )
    except PassageGenerationError as exc:
        assert "repeated content-word anchor" in str(exc)
    else:
        raise AssertionError("Expected disconnected passage to be rejected")


def test_store_maintenance_passage_allows_connector_sentence_without_target(db_session):
    words = [
        _seed_lemma(db_session, 1, "كِتَاب", "كتاب", "book"),
        _seed_lemma(db_session, 2, "وَلَد", "ولد", "boy"),
        _seed_lemma(db_session, 3, "بَيْت", "بيت", "house"),
        _seed_lemma(db_session, 4, "صَغِير", "صغير", "small"),
    ]
    db_session.commit()
    target_words = [
        {"lemma_id": words[0].lemma_id, "arabic": words[0].lemma_ar, "english": words[0].gloss_en, "pos": words[0].pos},
        {"lemma_id": words[1].lemma_id, "arabic": words[1].lemma_ar, "english": words[1].gloss_en, "pos": words[1].pos},
    ]
    eligible_words = [
        {"lemma_id": w.lemma_id, "arabic": w.lemma_ar, "english": w.gloss_en, "pos": w.pos}
        for w in words
    ]
    generated = {
        "title_ar": "بَيْتٌ صَغِيرٌ",
        "title_en": "A small house",
        "style_tag": "nostalgic",
        "sentences": [
            {"arabic": "كِتَابٌ فِي بَيْتٍ.", "english": "A book is in a house."},
            {"arabic": "بَيْتٌ صَغِيرٌ.", "english": "A small house."},
            {"arabic": "وَلَدٌ فِي بَيْتٍ.", "english": "A boy is in a house."},
        ],
    }

    story = store_maintenance_passage(
        db_session,
        generated,
        target_words=target_words,
        eligible_words=eligible_words,
        quality_gate=False,
    )

    sentences = db_session.query(Sentence).filter(Sentence.story_id == story.id).order_by(Sentence.id).all()
    assert len(sentences) == 3
    assert sentences[1].target_lemma_id == 3
    assert story.metadata_json["target_lemma_ids"] == [1, 2]


def test_store_maintenance_passage_rejects_forced_target_packing(db_session):
    words = [
        _seed_lemma(db_session, 1, "كِتَاب", "كتاب", "book"),
        _seed_lemma(db_session, 2, "وَلَد", "ولد", "boy"),
        _seed_lemma(db_session, 3, "بَيْت", "بيت", "house"),
        _seed_lemma(db_session, 4, "قَلَم", "قلم", "pen"),
    ]
    db_session.commit()
    target_words = [
        {"lemma_id": w.lemma_id, "arabic": w.lemma_ar, "english": w.gloss_en, "pos": w.pos}
        for w in words[:3]
    ]
    eligible_words = [
        {"lemma_id": w.lemma_id, "arabic": w.lemma_ar, "english": w.gloss_en, "pos": w.pos}
        for w in words
    ]
    generated = {
        "title_ar": "أَمْثِلَةٌ",
        "title_en": "Examples",
        "style_tag": "informative",
        "sentences": [
            {"arabic": "كِتَابٌ فِي بَيْتٍ.", "english": "A book is in a house."},
            {"arabic": "وَلَدٌ فِي بَيْتٍ.", "english": "A boy is in a house."},
            {"arabic": "قَلَمٌ فِي بَيْتٍ.", "english": "A pen is in a house."},
        ],
    }

    try:
        store_maintenance_passage(
            db_session,
            generated,
            target_words=target_words,
            eligible_words=eligible_words,
            quality_gate=False,
        )
    except PassageGenerationError as exc:
        assert "too many review target words" in str(exc)
    else:
        raise AssertionError("Expected packed multi-target passage to be rejected")


def _candidate(sentence_id, source, story_id, due_ids):
    return SentenceCandidate(
        sentence_id=sentence_id,
        sentence=SimpleNamespace(source=source, story_id=story_id),
        due_words_covered=set(due_ids),
    )


def test_group_maintenance_passages_does_not_bundle_unrelated_sentences():
    knowledge = {
        1: SimpleNamespace(knowledge_state="known"),
        2: SimpleNamespace(knowledge_state="known"),
        3: SimpleNamespace(knowledge_state="known"),
    }
    candidates = [
        _candidate(1, "llm", None, {1}),
        _candidate(2, "corpus", None, {2}),
        _candidate(3, "llm", None, {3}),
    ]

    groups = _group_maintenance_passages(candidates, knowledge)

    assert [[c.sentence_id for c in group] for group in groups] == [[1], [2], [3]]


def test_group_maintenance_passages_bundles_generated_story_rows():
    knowledge = {
        1: SimpleNamespace(knowledge_state="known"),
        2: SimpleNamespace(knowledge_state="learning"),
        3: SimpleNamespace(knowledge_state="lapsed"),
    }
    candidates = [
        _candidate(1, "passage", 10, {1}),
        _candidate(2, "passage", 10, {2}),
        _candidate(3, "passage", 10, {3}),
    ]

    groups = _group_maintenance_passages(candidates, knowledge)

    assert [[c.sentence_id for c in group] for group in groups] == [[1, 2, 3]]


def test_group_maintenance_passages_includes_connector_rows_without_due_words():
    knowledge = {
        1: SimpleNamespace(knowledge_state="known"),
        3: SimpleNamespace(knowledge_state="known"),
    }
    candidates = [
        _candidate(1, "passage", 10, {1}),
        _candidate(2, "passage", 10, set()),
        _candidate(3, "passage", 10, {3}),
    ]

    groups = _group_maintenance_passages(candidates, knowledge)

    assert [[c.sentence_id for c in group] for group in groups] == [[1, 2, 3]]


def test_agentic_passage_generation_sends_wide_target_pool(monkeypatch):
    captured = {}

    def fake_generate_with_tools(**kwargs):
        work_dir = Path(kwargs["work_dir"])
        captured["targets"] = json.loads((work_dir / "targets.json").read_text())
        captured["prompt"] = kwargs["prompt"]
        captured["model"] = kwargs["model"]
        return {
            "title_ar": "ذِكْرَى",
            "title_en": "A memory",
            "style_tag": "nostalgic",
            "premise": "A boy remembers a book in a small house.",
            "selected_target_lemma_ids": [1, 3, 5],
            "sentences": [
                {"arabic": "كِتَابٌ فِي بَيْتٍ.", "english": "A book in a house."},
                {"arabic": "وَلَدٌ يَرَى كِتَابًا.", "english": "A boy sees a book."},
                {"arabic": "بَيْتٌ صَغِيرٌ يَبْقَى.", "english": "A small house remains."},
            ],
        }

    monkeypatch.setattr("app.services.passage_generator._generate_agent_with_tools", fake_generate_with_tools)

    words = [
        {
            "lemma_id": i,
            "arabic": f"كِتَاب{i}",
            "arabic_bare": f"كتاب{i}",
            "english": f"word {i}",
            "pos": "noun",
            "state": "known",
        }
        for i in range(1, 9)
    ]

    result = generate_maintenance_passage_agentic(
        target_pool=words,
        known_words=words,
        style="nostalgic",
        sentence_count=3,
        feedback="Rejected because: disconnected examples",
    )

    assert result["selected_target_lemma_ids"] == [1, 3, 5]
    assert len(captured["targets"]) == 8
    assert captured["model"] == "sonnet"
    assert "Do not maximize target count" in captured["prompt"]
    assert "premise" in captured["prompt"]
    assert "Previous rejected draft/editor feedback" in captured["prompt"]


def test_rank_targets_for_passage_prefers_story_suitable_words():
    words = [
        {"lemma_id": 1, "arabic": "سَابِعَة", "english": "seventh (feminine)", "pos": "adj"},
        {"lemma_id": 2, "arabic": "حَصْبَة", "english": "measles", "pos": "noun"},
        {"lemma_id": 3, "arabic": "جُرَذ", "english": "rat", "pos": "noun"},
        {"lemma_id": 4, "arabic": "قَفَزَ", "english": "to jump", "pos": "verb"},
    ]

    ranked = _rank_targets_for_passage(words)

    assert [w["lemma_id"] for w in ranked][:2] == [3, 4]
