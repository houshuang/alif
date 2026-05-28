"""Tests for the root-showcase generation pipeline.

Covers:
- WAZN_TO_FAMILY classification in both root_showcase_candidates and root_showcase
- build_palette_for_root filters (canonical only, gated only, non-proper-name only,
  introduced-state only)
- generate_and_store_showcases_for_root happy path with mocked LLM
- Per-sentence acceptance gate (≥3 distinct palette lemmas required)
- root_focus_id + kind stamping on persisted sentences
- _source_bonus_for_sentence selector boost for kind='root_showcase'
- target_lemma_id stamping to the most-due palette lemma
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models import Lemma, Root, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.root_showcase import (
    WAZN_TO_FAMILY,
    _build_palette_due_ranking,
    build_palette_for_root,
    generate_and_store_showcases_for_root,
)
from app.services.llm import RootShowcaseSentenceResult
from app.services.sentence_selector import _source_bonus_for_sentence


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
                gated=True, canonical_id=None, word_category=None,
                ulk_state="known"):
    """Helper: insert a minimal Lemma row with knobs the palette filter cares about.

    Defaults to ULK state='known' so the lemma is palette-eligible. Pass
    ulk_state=None to skip ULK creation entirely, or 'encountered' / 'new'
    to test the introduced-state filter.
    """
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
    if ulk_state is not None:
        db.add(UserLemmaKnowledge(
            lemma_id=l.lemma_id,
            knowledge_state=ulk_state,
        ))
        db.flush()
    return l


def test_palette_excludes_encountered_and_unstudied_lemmas(db_session):
    """build_palette_for_root must exclude lemmas in 'encountered' / 'new' /
    no-ULK states — they need a proper intro card before being showcase-eligible.
    This rule guards against the Phase 3 gap-fill creating a new lemma that
    immediately appears in a showcase as the user's first encounter (which
    would bypass the intro-card UX)."""
    db = db_session
    root = Root(root="ك.ت.ب", core_meaning_en="writing")
    db.add(root)
    db.flush()
    # All four have correct shape (gated, canonical, gloss); ULK state varies
    _make_lemma(db, lemma_ar="كَتَبَ", root_id=root.root_id, gloss="to write", wazn="form_1", ulk_state="known")
    _make_lemma(db, lemma_ar="كاتِب", root_id=root.root_id, gloss="writer", wazn="fa'il", ulk_state="acquiring")
    _make_lemma(db, lemma_ar="مَكْتَب", root_id=root.root_id, gloss="office", wazn="maf'al", ulk_state="lapsed")
    # These three must be EXCLUDED:
    _make_lemma(db, lemma_ar="كِتاب", root_id=root.root_id, gloss="book", wazn="fi'al", ulk_state="encountered")
    _make_lemma(db, lemma_ar="كُتُب", root_id=root.root_id, gloss="books", wazn="fu'ul", ulk_state="new")
    _make_lemma(db, lemma_ar="مَكْتُوب", root_id=root.root_id, gloss="written", wazn="maf'ul", ulk_state=None)
    db.commit()

    palette = build_palette_for_root(db, root.root_id)

    surfaces = {p["arabic"] for p in palette}
    assert surfaces == {"كَتَبَ", "كاتِب", "مَكْتَب"}, (
        "Only known/acquiring/lapsed lemmas should be palette-eligible; "
        f"got {surfaces}"
    )


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

        def fake_validate(db, result, lemma_lookup, target_bares, **kwargs):
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


def test_source_bonus_boosts_root_showcase():
    """Selector must give kind='root_showcase' a meaningful bonus or showcases
    barely surface — they compete on equal footing with every other LLM sentence
    for their primary target's review slot."""
    showcase = Sentence(source="llm", kind="root_showcase", arabic_text="x")
    plain_llm = Sentence(source="llm", arabic_text="y")
    book = Sentence(source="book", arabic_text="z")
    passage = Sentence(source="passage", arabic_text="w")

    assert _source_bonus_for_sentence(showcase) == 1.8
    assert _source_bonus_for_sentence(showcase) > _source_bonus_for_sentence(book)
    assert _source_bonus_for_sentence(showcase) > _source_bonus_for_sentence(plain_llm)
    # But still below passages — those are the strongest collateral-credit card
    assert _source_bonus_for_sentence(showcase) < _source_bonus_for_sentence(passage)


def test_palette_due_ranking_orders_by_acquisition_then_fsrs(db_session):
    """_build_palette_due_ranking returns timestamps so min() picks the most-due
    (smallest timestamp). Acquisition next_due wins over FSRS due when both
    exist, since acquiring lemmas are the higher-priority review state."""
    db = db_session
    root = Root(root="ك.ت.ب", core_meaning_en="writing")
    db.add(root)
    db.flush()
    now = datetime.now(timezone.utc)

    l_overdue = _make_lemma(db, lemma_ar="A", root_id=root.root_id, gloss="a", ulk_state=None)
    l_due_soon = _make_lemma(db, lemma_ar="B", root_id=root.root_id, gloss="b", ulk_state=None)
    l_fsrs_far = _make_lemma(db, lemma_ar="C", root_id=root.root_id, gloss="c", ulk_state=None)
    l_no_due = _make_lemma(db, lemma_ar="D", root_id=root.root_id, gloss="d", ulk_state=None)

    db.add(UserLemmaKnowledge(
        lemma_id=l_overdue.lemma_id,
        knowledge_state="acquiring",
        acquisition_next_due=now - timedelta(days=3),
    ))
    db.add(UserLemmaKnowledge(
        lemma_id=l_due_soon.lemma_id,
        knowledge_state="acquiring",
        acquisition_next_due=now + timedelta(hours=1),
    ))
    # FSRS-state lemma with due date 30 days out
    far_iso = (now + timedelta(days=30)).isoformat()
    db.add(UserLemmaKnowledge(
        lemma_id=l_fsrs_far.lemma_id,
        knowledge_state="known",
        fsrs_card_json={"due": far_iso, "stability": 30.0},
    ))
    # No ULK at all
    db.commit()

    ranks = _build_palette_due_ranking(
        db, [l_overdue.lemma_id, l_due_soon.lemma_id, l_fsrs_far.lemma_id, l_no_due.lemma_id]
    )

    # Overdue acquiring lemma is most-due (smallest timestamp)
    assert ranks[l_overdue.lemma_id] < ranks[l_due_soon.lemma_id]
    assert ranks[l_due_soon.lemma_id] < ranks[l_fsrs_far.lemma_id]
    # Lemma with no ULK is absent from the ranking (caller's min() should fall
    # back to the _DUE_RANK_DEFAULT sentinel via .get())
    assert l_no_due.lemma_id not in ranks
    # Picking the most-due actually-targeted lemma
    from app.services.root_showcase import _DUE_RANK_DEFAULT
    targeted = {l_due_soon.lemma_id, l_overdue.lemma_id, l_no_due.lemma_id}
    most_due = min(targeted, key=lambda lid: ranks.get(lid, _DUE_RANK_DEFAULT))
    assert most_due == l_overdue.lemma_id


def _stub_validate_multi_target(*, trust_palette_mappings: bool):
    """Drive material_generator.validate_multi_target_sentence with everything
    inside mocked. Returns the corrections list that reached apply_corrections.

    The function imports its dependencies inside (sentence_validator helpers),
    so we patch at the sentence_validator module, not material_generator.
    """
    from unittest.mock import MagicMock
    from app.services import material_generator
    from app.services.sentence_validator import TokenMapping

    mappings = [
        TokenMapping(position=0, surface_form="كَتَبَ", lemma_id=101,
                     is_target=True, is_function_word=False),
        TokenMapping(position=1, surface_form="فِي", lemma_id=200,
                     is_target=False, is_function_word=False),
        TokenMapping(position=2, surface_form="كَاتِب", lemma_id=102,
                     is_target=True, is_function_word=False),
    ]
    fake_corrections = [
        {"position": 0, "correct_lemma_ar": "X", "correct_gloss": "x", "correct_pos": "verb"},
        {"position": 1, "correct_lemma_ar": "Y", "correct_gloss": "y", "correct_pos": "prep"},
        {"position": 2, "correct_lemma_ar": "Z", "correct_gloss": "z", "correct_pos": "noun"},
    ]

    received_corrections: list[dict] = []
    def fake_apply(corrections, mappings, db, **kwargs):
        received_corrections.extend(corrections)
        return []

    db_mock = MagicMock()
    db_mock.query.return_value.filter.return_value.all.return_value = []

    result = MagicMock()
    result.arabic = "كَتَبَ في كَاتِب"
    result.english = "wrote in writer"
    result.transliteration = ""
    result.primary_target_lemma_id = 101

    with patch("app.services.sentence_validator.tokenize_display",
               return_value=["كَتَبَ", "في", "كَاتِب"]), \
         patch("app.services.sentence_validator.map_tokens_to_lemmas",
               return_value=mappings), \
         patch("app.services.sentence_validator.verify_and_correct_mappings_llm",
               return_value=fake_corrections), \
         patch("app.services.sentence_validator.apply_corrections",
               side_effect=fake_apply):
        material_generator.validate_multi_target_sentence(
            db_mock, result,
            lemma_lookup={"كتب": 101, "كاتب": 102, "في": 200},
            target_bares={"كتب": 101, "كاتب": 102},
            trust_palette_mappings=trust_palette_mappings,
        )

    return received_corrections


def test_trust_palette_mappings_drops_palette_position_corrections():
    """When trust_palette_mappings=True, verifier corrections targeting
    is_target=True positions are dropped before apply_corrections sees them.
    Scaffold-position corrections (is_target=False) are preserved — the
    verifier's general gate still applies to them."""
    received = _stub_validate_multi_target(trust_palette_mappings=True)
    # Only the scaffold-position correction (position 1) reaches apply_corrections
    positions = [c["position"] for c in received]
    assert positions == [1], (
        f"Expected only scaffold position 1 to reach apply_corrections, got {positions}"
    )


def test_trust_palette_mappings_default_false_preserves_existing_behavior():
    """Default trust_palette_mappings=False — all corrections pass through.
    Regression guard so regular multi-target generation behavior is unchanged."""
    received = _stub_validate_multi_target(trust_palette_mappings=False)
    positions = [c["position"] for c in received]
    assert positions == [0, 1, 2], (
        f"Expected all 3 corrections to reach apply_corrections, got {positions}"
    )
