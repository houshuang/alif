"""Tests for the root-showcase generation pipeline.

Covers:
- WAZN_TO_FAMILY classification in both root_showcase_candidates and root_showcase
- build_palette_for_root filters (canonical only, gated only, non-proper-name only)
- generate_and_store_showcases_for_root happy path with mocked LLM
- Per-sentence acceptance gate (≥3 distinct palette lemmas required)
- root_focus_id + kind stamping on persisted sentences
"""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.models import Lemma, Root, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.root_showcase import (
    WAZN_TO_FAMILY,
    build_palette_for_root,
    generate_and_store_showcases_for_root,
)
from app.services.llm import RootShowcaseSentenceResult


def test_wazn_family_mapping_covers_canonical_categories():
    """Every entry in WAZN_TO_FAMILY maps to a meaningful family."""
    # All form_1..form_10 verbs map to verb_I..verb_X
    for i in range(1, 11):
        assert WAZN_TO_FAMILY[f"form_{i}"] == f"verb_{['I','II','III','IV','V','VI','VII','VIII','IX','X'][i-1]}"
    # Active participle family
    assert WAZN_TO_FAMILY["fa'il"] == "agent"
    assert WAZN_TO_FAMILY["mustaf'il"] == "agent"
    # Passive participle family
    assert WAZN_TO_FAMILY["maf'ul"] == "patient"
    # Place / instrument
    assert WAZN_TO_FAMILY["maf'al"] == "place_or_time"
    assert WAZN_TO_FAMILY["mif'al"] == "instrument"
    # Masdar variants for Form I
    assert WAZN_TO_FAMILY["fi'ala"] == "masdar_I"
    assert WAZN_TO_FAMILY["fu'l"] == "masdar_I"
    # Form II/IV/VIII/X masdars
    assert WAZN_TO_FAMILY["taf'il"] == "masdar_II"
    assert WAZN_TO_FAMILY["if'al"] == "masdar_IV"
    assert WAZN_TO_FAMILY["ifti'al"] == "masdar_VIII"
    assert WAZN_TO_FAMILY["istif'al"] == "masdar_X"


def test_root_showcase_candidates_wazn_table_matches_root_showcase_table():
    """The two WAZN_TO_FAMILY tables must stay in sync — they encode the
    same morphology semantics. If one drifts, the analyzer and generator
    will disagree about which derivations are 'missing'."""
    from scripts.root_showcase_candidates import WAZN_TO_FAMILY as candidates_table
    assert WAZN_TO_FAMILY == candidates_table


def _make_lemma(db, *, lemma_ar, root_id, gloss, pos="noun", wazn=None,
                gated=True, canonical_id=None, word_category=None):
    """Helper: insert a minimal Lemma row with knobs the palette filter cares about."""
    l = Lemma(
        lemma_ar=lemma_ar,
        lemma_ar_bare=lemma_ar,
        root_id=root_id,
        pos=pos,
        gloss_en=gloss,
        wazn=wazn,
        canonical_lemma_id=canonical_id,
        word_category=word_category,
        gates_completed_at=datetime.now(timezone.utc) if gated else None,
    )
    db.add(l)
    db.flush()
    return l


def test_build_palette_filters_canonical_gated_non_proper_name(db_session):
    """build_palette_for_root must exclude variants, ungated lemmas, and proper names."""
    db = db_session
    root = Root(root=".ك.ت.ب", core_meaning_en="writing")
    db.add(root)
    db.flush()

    canonical = _make_lemma(db, lemma_ar="كَتَبَ", root_id=root.root_id, gloss="to write", pos="verb", wazn="form_1")
    _make_lemma(db, lemma_ar="كاتِب", root_id=root.root_id, gloss="writer", pos="noun", wazn="fa'il")
    _make_lemma(db, lemma_ar="مَكْتَب", root_id=root.root_id, gloss="office", pos="noun", wazn="maf'al")
    # Variant -> filtered out
    _make_lemma(db, lemma_ar="كُتُبِي", root_id=root.root_id, gloss="my books", canonical_id=canonical.lemma_id)
    # Ungated -> filtered out
    _make_lemma(db, lemma_ar="كَتْب", root_id=root.root_id, gloss="writing", gated=False)
    # Proper name -> filtered out
    _make_lemma(db, lemma_ar="كَتْبٌ", root_id=root.root_id, gloss="Katb (place name)", word_category="proper_name")
    # No gloss -> filtered out (the showcase prompt requires glosses)
    _make_lemma(db, lemma_ar="كاتِبَة", root_id=root.root_id, gloss=None, wazn="fa'il")

    db.commit()
    palette = build_palette_for_root(db, root.root_id)

    assert len(palette) == 3
    surfaces = {p["arabic"] for p in palette}
    assert surfaces == {"كَتَبَ", "كاتِب", "مَكْتَب"}
    # family is populated for known wazns
    families = {p["family"] for p in palette}
    assert "agent" in families
    assert "verb_I" in families
    assert "place_or_time" in families


def test_build_palette_too_small_returns_short_circuit_result(db_session):
    """A root with <3 canonical gated lemmas can't sustain a showcase; the
    orchestrator must short-circuit before calling the LLM (LLM is expensive)."""
    db = db_session
    root = Root(root=".س.ا.ل", core_meaning_en="asking")
    db.add(root)
    db.flush()
    _make_lemma(db, lemma_ar="سَأَل", root_id=root.root_id, gloss="he asked", pos="verb", wazn="form_1")
    _make_lemma(db, lemma_ar="سُؤال", root_id=root.root_id, gloss="question", pos="noun", wazn="fu'al")
    db.commit()

    with patch("app.services.root_showcase.generate_root_showcase_sentences") as mock_gen:
        result = generate_and_store_showcases_for_root(db, root.root_id, count=3)

    mock_gen.assert_not_called()
    assert result.persisted == 0
    assert result.generated == 0
    assert "palette_too_small" in result.rejected_reasons


def test_generate_requires_three_palette_lemmas_per_sentence(db_session):
    """A generated candidate that lands fewer than 3 palette targets in the
    final mapping must be rejected — that's the showcase contract."""
    db = db_session
    root = Root(root=".ك.ت.ب", core_meaning_en="writing")
    db.add(root)
    db.flush()
    # Five canonical lemmas — enough palette
    for ar, gloss, wazn, pos in [
        ("كَتَبَ", "to write", "form_1", "verb"),
        ("كاتِب", "writer", "fa'il", "noun"),
        ("كِتاب", "book", "fi'al", "noun"),
        ("مَكْتَب", "office", "maf'al", "noun"),
        ("مَكْتَبَة", "library", "maf'ala", "noun"),
    ]:
        _make_lemma(db, lemma_ar=ar, root_id=root.root_id, gloss=gloss, wazn=wazn, pos=pos)
    db.commit()

    # Mock LLM to return one good (3-palette) sentence and one bad (1-palette)
    # The bad one has Arabic that won't tokenize to the palette at all — the
    # validator's lemma_lookup will return unmapped, so it'll be rejected upstream
    # of our 3-lemma gate, with reason validation_failed.
    fake_sentences = [
        RootShowcaseSentenceResult(
            arabic="كَتَبَ كاتِب كِتاب",  # 3 palette lemmas
            english="A writer wrote a book.",
            transliteration="kataba kātib kitāb",
            palette_lemmas_used=["كَتَبَ", "كاتِب", "كِتاب"],
        ),
        RootShowcaseSentenceResult(
            arabic="بَيْت كَبير جَميل",  # 0 palette lemmas, all-foreign
            english="A big beautiful house.",
            transliteration="bayt kabīr jamīl",
            palette_lemmas_used=["كَتَبَ"],
        ),
    ]

    with patch(
        "app.services.root_showcase.generate_root_showcase_sentences",
        return_value=fake_sentences,
    ), patch(
        "app.services.root_showcase.validate_multi_target_sentence"
    ) as mock_validate, patch(
        "app.services.root_showcase.review_sentences_quality",
        return_value=[],
    ):
        # Stub validator: first sentence returns 3 mappings with is_target=True;
        # second returns None (validation failed)
        from app.services.sentence_validator import TokenMapping

        def fake_validate(db, result, lemma_lookup, target_bares):
            if "كَتَبَ" in result.arabic and "كاتِب" in result.arabic:
                ms = []
                for i, (sf, lid) in enumerate([
                    ("كَتَبَ", target_bares["كَتَبَ"]),
                    ("كاتِب", target_bares["كاتِب"]),
                    ("كِتاب", target_bares["كِتاب"]),
                ]):
                    m = TokenMapping(
                        position=i, surface_form=sf, lemma_id=lid,
                        is_target=True, is_function_word=False,
                    )
                    ms.append(m)
                return ms
            return None

        mock_validate.side_effect = fake_validate

        result = generate_and_store_showcases_for_root(
            db, root.root_id, count=2, quality_review=False,
        )

    assert result.generated == 2
    # Only the first sentence passed both validation and the ≥3 gate
    assert result.persisted == 1
    assert len(result.sentence_ids) == 1

    sent = db.query(Sentence).filter(Sentence.id == result.sentence_ids[0]).first()
    assert sent is not None
    assert sent.root_focus_id == root.root_id
    assert sent.kind == "root_showcase"
    assert sent.mappings_verified_at is not None
    sw_count = db.query(SentenceWord).filter(SentenceWord.sentence_id == sent.id).count()
    assert sw_count == 3
