"""Tests for the LLM-driven sentence generator (PR #4).

Strategy: ``material_generator`` calls Claude via ``subprocess.run`` in
``_call_cli``. We patch that one function (via monkeypatching the module-level
``subprocess.run``) so tests run offline. Each test sets up:

  - some Lemma + ULK rows so the warm-cache picker has work to do,
  - canned Claude responses (one Sonnet call + one Haiku call per batch),
  - asserts on what got written to the DB.

The picker's own coverage lives in ``test_sentence_selector.py``; here we only
care about the generation/verification/write-side.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services import material_generator as mg


@dataclass
class _FakeProc:
    stdout: str
    stderr: str = ""
    returncode: int = 0


def _envelope(structured: dict) -> str:
    return json.dumps({"structured_output": structured, "result": ""})


def _seed_lemma(
    db,
    *,
    form: str,
    bare: str | None = None,
    gloss: str = "x",
    canonical: int | None = None,
    word_category: str | None = None,
    pos: str | None = None,
    example_src: str | None = None,
    example_en: str | None = None,
) -> Lemma:
    lemma = Lemma(
        language_code="el",
        lemma_form=form,
        lemma_bare=bare if bare is not None else form,
        gloss_en=gloss,
        source="test",
        canonical_lemma_id=canonical,
        word_category=word_category,
        pos=pos,
        example_src=example_src,
        example_en=example_en,
    )
    db.add(lemma)
    db.flush()
    return lemma


def _seed_acquiring(db, lemma_id: int, *, box: int = 1) -> UserLemmaKnowledge:
    ulk = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state="acquiring",
        acquisition_box=box,
        acquisition_next_due=datetime.now(timezone.utc),
        acquisition_started_at=datetime.now(timezone.utc),
    )
    db.add(ulk)
    db.flush()
    return ulk


def _seed_known(
    db,
    lemma_id: int,
    *,
    source: str = "reading_intake",
    knowledge_origin: str | None = None,
    fsrs_card_json: dict | None = None,
) -> UserLemmaKnowledge:
    kwargs = dict(
        lemma_id=lemma_id,
        knowledge_state="known",
        source=source,
        knowledge_origin=knowledge_origin,
    )
    if fsrs_card_json is not None:
        kwargs["fsrs_card_json"] = fsrs_card_json
    ulk = UserLemmaKnowledge(**kwargs)
    db.add(ulk)
    db.flush()
    return ulk


def _seed_known_scaffold(db, **kwargs) -> Lemma:
    lemma = _seed_lemma(db, **kwargs)
    _seed_known(db, lemma.lemma_id)
    return lemma


def test_snapshot_known_pool_flags_unconfirmed_scaffold(tmp_db):
    with tmp_db() as db:
        a = _seed_lemma(db, form="alpha", bare="alpha")          # assumed, no card, unconfirmed
        _seed_known(db, a.lemma_id, knowledge_origin="cognate_known")
        b = _seed_lemma(db, form="beta", bare="beta")            # assumed but already confirmed
        ulk_b = _seed_known(db, b.lemma_id, knowledge_origin="pre_known")
        ulk_b.confirmed_at = datetime.now(timezone.utc)
        c = _seed_lemma(db, form="gamma", bare="gamma")          # retrieval-verified (has card)
        _seed_known(db, c.lemma_id, fsrs_card_json={"due": "2026-01-01"})
        d = _seed_lemma(db, form="delta", bare="delta")          # acquiring (not known)
        _seed_acquiring(db, d.lemma_id)
        db.flush()

        pool = {p["lemma_id"]: p["unconfirmed_scaffold"]
                for p in mg._snapshot_known_pool(db, "el", set())}
        assert pool[a.lemma_id] is True
        assert pool[b.lemma_id] is False
        assert pool[c.lemma_id] is False
        assert pool[d.lemma_id] is False


def test_sample_weighted_prefers_unconfirmed_scaffold(monkeypatch):
    monkeypatch.setattr("random.uniform", lambda lo, hi: 1.0)  # kill jitter
    pool = [
        {"lemma_id": 1, "lemma_form": "conf", "lemma_bare": "conf", "pos": None,
         "frequency_rank": None, "unconfirmed_scaffold": False},
        {"lemma_id": 2, "lemma_form": "unconf", "lemma_bare": "unconf", "pos": None,
         "frequency_rank": None, "unconfirmed_scaffold": True},
    ]
    # equal sentence-coverage; only the unconfirmed boost breaks the tie
    out = mg._sample_known_words_weighted(pool, {1: 0, 2: 0}, sample_size=1, language_code="el")
    assert out == ["unconf"]


# ─── Pure-function tests for sentence_validator ──────────────────────────────


def test_validator_rejects_missing_target(tmp_db):
    from app.services.sentence_validator import validate_sentence
    res = validate_sentence(
        text="το βιβλίο είναι μεγάλο",
        target_bare="τραπεζι",
        known_bare_forms={"βιβλιο", "ειναι", "μεγαλο"},
        function_word_bares={"το"},
        language_code="el",
    )
    assert not res.valid
    assert any("target_missing" in i for i in res.issues)


def test_validator_passes_when_all_words_known(tmp_db):
    from app.services.sentence_validator import validate_sentence
    res = validate_sentence(
        text="το βιβλίο είναι μεγάλο",
        target_bare="βιβλιο",
        known_bare_forms={"βιβλιο", "ειμαι", "μεγαλο"},
        function_word_bares={"το"},
        language_code="el",
    )
    assert res.valid
    assert res.target_present


def test_tokenize_display_keeps_punctuation_rows(tmp_db):
    from app.services.sentence_validator import is_punctuation_surface, tokenize_display
    tokens = tokenize_display("το βιβλίο είναι μεγάλο.", "el")
    assert tokens[-1][1] == "."
    assert is_punctuation_surface(tokens[-1][1])


def test_greek_surface_fallback_maps_adjective_target(tmp_db):
    from app.services.lemma_quality import FUNCTION_WORD_SETS
    from app.services.sentence_validator import (
        build_lemma_lookup,
        map_tokens_to_lemmas,
        tokenize_display,
        validate_sentence,
    )

    with tmp_db() as db:
        target = _seed_lemma(
            db,
            form="στρωμένος",
            bare="στρωμενος",
            gloss="made",
            pos="adjective",
        )
        _seed_lemma(db, form="κρεβάτι", bare="κρεβατι", gloss="bed", pos="noun")
        _seed_lemma(db, form="είναι", bare="ειμαι", gloss="to be", pos="verb")
        db.commit()
        target_id = target.lemma_id
        lookup = build_lemma_lookup(db, "el")

    assert lookup["στρωμενο"] == target_id

    text = "το κρεβάτι είναι στρωμένο."
    validation = validate_sentence(
        text=text,
        target_bare="στρωμενος",
        known_bare_forms=set(lookup.keys()),
        function_word_bares=FUNCTION_WORD_SETS["el"],
        language_code="el",
        lemma_lookup=lookup,
        target_lemma_id=target_id,
    )
    assert validation.valid
    assert validation.target_present

    mappings = map_tokens_to_lemmas(
        tokens=tokenize_display(text, "el"),
        lemma_lookup=lookup,
        language_code="el",
        target_lemma_id=target_id,
        target_bare="στρωμενος",
    )
    target_mappings = [m for m in mappings if m.surface_form == "στρωμένο"]
    assert len(target_mappings) == 1
    assert target_mappings[0].lemma_id == target_id
    assert target_mappings[0].is_target is True


def test_greek_surface_fallback_maps_middle_passive_verb_target(tmp_db):
    from app.services.lemma_quality import FUNCTION_WORD_SETS
    from app.services.sentence_validator import (
        build_lemma_lookup,
        map_tokens_to_lemmas,
        tokenize_display,
        validate_sentence,
    )

    with tmp_db() as db:
        target = _seed_lemma(
            db,
            form="συντελούμαι",
            bare="συντελουμαι",
            gloss="to be carried out",
            pos="verb",
        )
        _seed_lemma(db, form="δουλειά", bare="δουλεια", gloss="work", pos="noun")
        db.commit()
        target_id = target.lemma_id
        lookup = build_lemma_lookup(db, "el")

    assert lookup["συντελειται"] == target_id

    text = "η δουλειά συντελείται σήμερα."
    validation = validate_sentence(
        text=text,
        target_bare="συντελουμαι",
        known_bare_forms=set(lookup.keys()),
        function_word_bares=FUNCTION_WORD_SETS["el"],
        language_code="el",
        lemma_lookup=lookup,
        target_lemma_id=target_id,
    )
    assert validation.valid
    assert validation.target_present

    mappings = map_tokens_to_lemmas(
        tokens=tokenize_display(text, "el"),
        lemma_lookup=lookup,
        language_code="el",
        target_lemma_id=target_id,
        target_bare="συντελουμαι",
    )
    target_mappings = [m for m in mappings if m.surface_form == "συντελείται"]
    assert len(target_mappings) == 1
    assert target_mappings[0].lemma_id == target_id
    assert target_mappings[0].is_target is True


# ─── Generator tests with mocked Claude ──────────────────────────────────────


@pytest.fixture
def fake_claude(monkeypatch):
    """Patch ``subprocess.run`` inside material_generator to return canned
    responses based on a script. The script is a list of `_FakeProc` instances
    consumed in call order.

    Scripts an exact single-provider (Claude) sequence, so pin failover off —
    provider failover is covered in test_llm_cli.py.
    """
    monkeypatch.setenv("POLYGLOT_LLM_FALLBACK", "0")
    monkeypatch.delenv("POLYGLOT_LLM_PROVIDER", raising=False)
    state = {"script": [], "calls": []}

    def fake_run(cmd, capture_output=False, text=False, timeout=None):
        state["calls"].append(cmd)
        if not state["script"]:
            raise AssertionError("Unexpected extra Claude call")
        return state["script"].pop(0)

    monkeypatch.setattr(mg.subprocess, "run", fake_run)
    return state


def _gen_response(targets_and_text: list[tuple[int, str, str]]) -> _FakeProc:
    """Build a fake Sonnet generation response.

    ``targets_and_text`` = ``[(target_index, sentence_text, english), ...]``.
    """
    return _FakeProc(stdout=_envelope({
        "sentences": [
            {"target_index": tid, "text": text, "english": english}
            for tid, text, english in targets_and_text
        ],
    }))


def _verify_response(verdicts: list[dict] | None = None) -> _FakeProc:
    """Fake Haiku verification response."""
    return _FakeProc(stdout=_envelope({"decisions": verdicts or []}))


def _verify_ok_response(positions: tuple[int, ...] = (1, 2, 3)) -> _FakeProc:
    return _verify_response([
        {"sentence_index": 0, "position": pos, "verdict": "ok"}
        for pos in positions
    ])


def _verify_ok_response_for_candidates(
    count: int,
    positions: tuple[int, ...] = (1, 2, 3),
) -> _FakeProc:
    return _verify_response([
        {"sentence_index": sentence_index, "position": pos, "verdict": "ok"}
        for sentence_index in range(count)
        for pos in positions
    ])


def _quality_response(reviews: list[dict] | None = None) -> _FakeProc:
    if reviews is None:
        reviews = [
            {"id": 0, "natural": True, "translation_correct": True, "reason": "ok"},
        ]
    return _FakeProc(stdout=_envelope({
        "reviews": reviews,
    }))


def test_batch_generate_happy_path(tmp_db, fake_claude):
    """One target, one valid sentence, no verifier complaints → 1 row written
    with mappings_verified_at stamped."""
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
        # Scaffold lemmas must be in the learner's engaged vocabulary.
        _seed_known_scaffold(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_known_scaffold(db, form="είναι", bare="ειμαι", gloss="to be")
        db.commit()
        target_id = target.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "το βιβλίο είναι μεγάλο.", "The book is big.")]),
        _verify_ok_response(),
        _quality_response(),
    ]

    result = mg.batch_generate_material(
        language_code="el",
        lemma_ids=[target_id],
        sentences_per_target=1,
    )
    assert result["generated"] == 1
    assert result["words_covered"] == 1
    assert result["words_failed"] == []

    with tmp_db() as db:
        sentences = db.query(Sentence).all()
        assert len(sentences) == 1
        s = sentences[0]
        assert s.text == "το βιβλίο είναι μεγάλο."
        assert s.target_lemma_id == target_id
        assert s.source == "llm"
        assert s.mappings_verified_at is not None
        assert s.quality_reviewed_at is not None
        assert s.quality_natural is True
        assert s.quality_translation_correct is True
        assert s.quality_reason == "ok"
        assert s.is_active is True

        words = db.query(SentenceWord).filter(SentenceWord.sentence_id == s.id).all()
        # Content tokens must be mapped; function words may have lemma_id=None
        # (they live in FUNCTION_WORD_SETS rather than as DB lemmas — matches
        # sentence_harvest's behaviour).
        assert words
        from app.services.lemma_quality import FUNCTION_WORD_SETS
        from app.services.sentence_validator import is_punctuation_surface, normalize_bare
        funcs = FUNCTION_WORD_SETS["el"]
        assert any(w.lemma_id is None and is_punctuation_surface(w.surface_form) for w in words)
        assert words[-1].surface_form == "."
        assert words[-1].lemma_id is None
        for w in words:
            if w.lemma_id is None:
                if is_punctuation_surface(w.surface_form):
                    continue
                assert normalize_bare(w.surface_form, "el") in funcs, \
                    f"unmapped content word: {w.surface_form}"
        target_words = [w for w in words if w.is_target_word]
        assert len(target_words) == 1
        assert target_words[0].lemma_id == target_id


def test_verification_failure_discards_all(tmp_db, fake_claude):
    """If the Haiku call returns no structured output, the candidate must be
    dropped — Hard Invariant 'verification failure ≠ success'."""
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
        _seed_known_scaffold(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_known_scaffold(db, form="είναι", bare="ειμαι", gloss="to be")
        db.commit()
        target_id = target.lemma_id

    # Verifier returns non-zero exit — `_call_cli` returns None → discard all.
    fake_claude["script"] = [
        _gen_response([(0, "το βιβλίο είναι μεγάλο", "The book is big.")]),
        _FakeProc(stdout="", stderr="boom", returncode=1),
    ]

    result = mg.batch_generate_material(
        language_code="el",
        lemma_ids=[target_id],
        sentences_per_target=1,
    )
    assert result["generated"] == 0
    assert result["words_failed"] == [target_id]

    with tmp_db() as db:
        assert db.query(Sentence).count() == 0


def test_verifier_wrong_verdict_discards_candidate(tmp_db, fake_claude):
    """If Haiku flags any position as 'wrong', that candidate is rejected."""
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
        _seed_known_scaffold(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_known_scaffold(db, form="είναι", bare="ειμαι", gloss="to be")
        db.commit()
        target_id = target.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "το βιβλίο είναι μεγάλο", "The book is big.")]),
        _verify_response([
            {"sentence_index": 0, "position": 1, "verdict": "wrong",
             "correct_lemma": "βιβλιά", "reason": "test"},
            {"sentence_index": 0, "position": 2, "verdict": "ok"},
            {"sentence_index": 0, "position": 3, "verdict": "ok"},
        ]),
    ]

    result = mg.batch_generate_material(
        language_code="el",
        lemma_ids=[target_id],
        sentences_per_target=1,
    )
    assert result["generated"] == 0

    with tmp_db() as db:
        assert db.query(Sentence).count() == 0


def test_verifier_wrong_same_lemma_does_not_discard_candidate(tmp_db, fake_claude):
    """Verifier sometimes says wrong while proposing the same lemma. That is
    not a content correction and should not lower generation yield."""
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
        _seed_known_scaffold(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_known_scaffold(db, form="είναι", bare="ειμαι", gloss="to be")
        db.commit()
        target_id = target.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "το βιβλίο είναι μεγάλο", "The book is big.")]),
        _verify_response([
            {"sentence_index": 0, "position": 1, "verdict": "wrong",
             "correct_lemma": "βιβλίο", "reason": "same lemma"},
            {"sentence_index": 0, "position": 2, "verdict": "ok"},
            {"sentence_index": 0, "position": 3, "verdict": "ok"},
        ]),
        _quality_response(),
    ]

    result = mg.batch_generate_material(
        language_code="el",
        lemma_ids=[target_id],
        sentences_per_target=1,
    )
    assert result["generated"] == 1


def test_verifier_wrong_noncontent_position_does_not_discard_candidate(tmp_db, fake_claude):
    """Wrong verdicts on function-word mappings should not reject an otherwise
    valid generated sentence; function words are not retrieval targets."""
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
        _seed_known_scaffold(db, form="είναι", bare="ειμαι", gloss="to be")
        _seed_lemma(
            db,
            form="κοντά",
            bare="κοντα",
            gloss="near",
            word_category="function_word",
        )
        db.commit()
        target_id = target.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "το βιβλίο είναι κοντά.", "The book is nearby.")]),
        _verify_response([
            {"sentence_index": 0, "position": 1, "verdict": "ok"},
            {"sentence_index": 0, "position": 2, "verdict": "ok"},
            {"sentence_index": 0, "position": 3, "verdict": "wrong",
             "correct_lemma": "κοντά", "reason": "function word nit"},
        ]),
        _quality_response(),
    ]

    result = mg.batch_generate_material(
        language_code="el",
        lemma_ids=[target_id],
        sentences_per_target=1,
    )
    assert result["generated"] == 1


def test_verifier_does_not_require_decisions_for_noncontent_positions(tmp_db, fake_claude):
    """Non-content tokens are scaffold, not retrieval material. They should not
    create expected verifier positions that can fail the whole batch."""
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
        _seed_known_scaffold(db, form="είναι", bare="ειμαι", gloss="to be")
        _seed_lemma(
            db,
            form="κοντά",
            bare="κοντα",
            gloss="near",
            word_category="function_word",
        )
        db.commit()
        target_id = target.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "το βιβλίο είναι κοντά.", "The book is nearby.")]),
        _verify_response([
            {"sentence_index": 0, "position": 1, "verdict": "ok"},
            {"sentence_index": 0, "position": 2, "verdict": "ok"},
            # No decision for position 3 / κοντά.
        ]),
        _quality_response(),
    ]

    result = mg.batch_generate_material(
        language_code="el",
        lemma_ids=[target_id],
        sentences_per_target=1,
    )
    assert result["generated"] == 1


def test_verifier_skips_surface_function_word_mapped_to_content_lemma(tmp_db, monkeypatch):
    """The surface form can be scaffold even when the lemmatizer maps it to a
    content lemma row, e.g. μακριά -> μακρύς. Do not verify scaffold tokens."""
    with tmp_db() as db:
        far = _seed_lemma(db, form="μακρύς", bare="μακρυς", gloss="far")
        db.flush()

        def fail_call_llm(**kwargs):
            raise AssertionError("surface function word should not be verified")

        monkeypatch.setattr(mg, "_call_llm", fail_call_llm)
        result = mg.verify_sentence_mappings_llm(
            "el",
            [{
                "text": "μακριά.",
                "mappings": [
                    mg.Mapping(position=0, surface_form="μακριά", lemma_id=far.lemma_id),
                ],
            }],
            {far.lemma_id: far},
        )

    assert result == [[]]


def test_wrong_verdict_surface_function_word_does_not_discard_candidate(tmp_db):
    """A verifier nit about a scaffold surface should not reject the sentence,
    even if that token has been attached to a content DB lemma."""
    with tmp_db() as db:
        far = _seed_lemma(db, form="μακρύς", bare="μακρυς", gloss="far")
        db.flush()
        lemma_by_id = {far.lemma_id: far}
        mapping = mg.Mapping(position=3, surface_form="μακριά", lemma_id=far.lemma_id)

        assert not mg._wrong_verdict_rejects_candidate(
            mg.VerifyDecision(
                sentence_index=0,
                position=3,
                verdict="wrong",
                correct_lemma="μακριά",
                reason="surface scaffold nit",
            ),
            [mapping],
            lemma_by_id,
            language_code="el",
            function_words=mg.FUNCTION_WORD_SETS["el"],
        )


def test_glossless_target_is_skipped(tmp_db, fake_claude):
    """Hard Invariant gloss gate at the entry point — target with empty gloss
    never reaches generation."""
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="")
        _seed_acquiring(db, target.lemma_id)
        db.commit()
        target_id = target.lemma_id

    # No script entries — if generation runs, the fake will raise.
    result = mg.batch_generate_material(
        language_code="el",
        lemma_ids=[target_id],
        sentences_per_target=1,
    )
    assert result["generated"] == 0
    assert result["words_failed"] == [target_id]
    assert fake_claude["calls"] == []


def test_canonical_redirect_at_entry(tmp_db, fake_claude):
    """If a caller passes a variant lemma_id, the generated Sentence's
    target_lemma_id points at the canonical."""
    with tmp_db() as db:
        canonical = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        variant = _seed_lemma(db, form="βιβλίο-var", bare="βιβλιο",
                              gloss="book", canonical=canonical.lemma_id)
        _seed_acquiring(db, canonical.lemma_id)
        _seed_known_scaffold(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_known_scaffold(db, form="είναι", bare="ειμαι", gloss="to be")
        db.commit()
        canonical_id = canonical.lemma_id
        variant_id = variant.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "το βιβλίο είναι μεγάλο", "The book is big.")]),
        _verify_ok_response(),
        _quality_response(),
    ]

    result = mg.batch_generate_material(
        language_code="el",
        lemma_ids=[variant_id],
        sentences_per_target=1,
    )
    assert result["generated"] == 1

    with tmp_db() as db:
        sentences = db.query(Sentence).all()
        assert len(sentences) == 1
        # target_lemma_id resolved through canonical chain.
        assert sentences[0].target_lemma_id == canonical_id


def test_unmapped_token_drops_candidate(tmp_db, fake_claude):
    """If validation can't find a DB lemma for every content token, the
    candidate is rejected — we don't write SentenceWord rows with NULL
    lemma_ids from generation (only book/corpus imports may do that)."""
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
        # No scaffold lemmas seeded — every content token besides the target
        # is unmapped, so deterministic validation fails before the verifier.
        db.commit()
        target_id = target.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "βιβλίο μυστηριώδες κρυφό", "mysterious hidden book")]),
        # The verifier should never be called because deterministic
        # validation killed the only candidate. If it does get called, this
        # response prevents the test from hanging — but it's still wrong.
        _verify_response(),
    ]

    result = mg.batch_generate_material(
        language_code="el",
        lemma_ids=[target_id],
        sentences_per_target=1,
    )
    assert result["generated"] == 0


def test_content_word_outside_engaged_pool_drops_candidate(tmp_db, fake_claude):
    """Generation may map against the full lemma DB, but non-target content
    must still be in the learner's engaged scaffold vocabulary."""
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
        _seed_known_scaffold(db, form="είναι", bare="ειμαι", gloss="to be")
        _seed_lemma(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        db.commit()
        target_id = target.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "το βιβλίο είναι μεγάλο", "The book is big.")]),
    ]

    result = mg.batch_generate_material(
        language_code="el",
        lemma_ids=[target_id],
        sentences_per_target=1,
    )
    assert result["generated"] == 0
    assert result["words_failed"] == [target_id]
    assert len(fake_claude["calls"]) == 1


def test_incomplete_verifier_response_discards_candidate(tmp_db, fake_claude):
    """A verifier response must cover every mapped content position."""
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
        _seed_known_scaffold(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_known_scaffold(db, form="είναι", bare="ειμαι", gloss="to be")
        db.commit()
        target_id = target.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "το βιβλίο είναι μεγάλο", "The book is big.")]),
        _verify_response([
            {"sentence_index": 0, "position": 1, "verdict": "ok"},
        ]),
    ]

    result = mg.batch_generate_material(
        language_code="el",
        lemma_ids=[target_id],
        sentences_per_target=1,
    )
    assert result["generated"] == 0

    with tmp_db() as db:
        assert db.query(Sentence).count() == 0


def test_quality_rejection_discards_candidate(tmp_db, fake_claude):
    """A sentence can pass token mapping but still fail the meaning gate."""
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
        _seed_known_scaffold(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_known_scaffold(db, form="είναι", bare="ειμαι", gloss="to be")
        db.commit()
        target_id = target.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "το βιβλίο είναι μεγάλο", "The book is big.")]),
        _verify_ok_response(),
        _quality_response([
            {
                "id": 0,
                "natural": False,
                "translation_correct": True,
                "reason": "forced vocabulary combination",
            },
        ]),
    ]

    result = mg.batch_generate_material(
        language_code="el",
        lemma_ids=[target_id],
        sentences_per_target=1,
    )
    assert result["generated"] == 0
    assert result["words_failed"] == [target_id]

    with tmp_db() as db:
        assert db.query(Sentence).count() == 0


def test_overgenerated_candidates_survive_quality_filter(tmp_db, fake_claude):
    """Extra generated candidates should stay alive through quality review;
    otherwise the first weak candidate can hide a later good one."""
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
        _seed_known_scaffold(db, form="είναι", bare="ειμαι", gloss="to be")
        _seed_known_scaffold(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_known_scaffold(db, form="καλό", bare="καλος", gloss="good")
        _seed_known_scaffold(db, form="παλιό", bare="παλιος", gloss="old")
        db.commit()
        target_id = target.lemma_id

    fake_claude["script"] = [
        _gen_response([
            (0, "το βιβλίο είναι μεγάλο", "The book is big."),
            (0, "το βιβλίο είναι καλό", "The book is good."),
            (0, "το βιβλίο είναι παλιό", "The book is old."),
        ]),
        _verify_ok_response_for_candidates(3),
        _quality_response([
            {"id": 0, "natural": False, "translation_correct": True, "reason": "weak"},
            {"id": 1, "natural": False, "translation_correct": True, "reason": "weak"},
            {"id": 2, "natural": True, "translation_correct": True, "reason": "ok"},
        ]),
    ]

    result = mg.batch_generate_material(
        language_code="el",
        lemma_ids=[target_id],
        sentences_per_target=1,
    )
    assert result["generated"] == 1

    with tmp_db() as db:
        sentence = db.query(Sentence).one()
        assert sentence.text == "το βιβλίο είναι παλιό"


def test_function_word_lemma_without_gloss_passes_gloss_gate(tmp_db, fake_claude):
    """Reading intake can create function-word Lemma rows without glosses."""
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
        _seed_lemma(db, form="το", bare="το", gloss="", word_category="function_word")
        _seed_known_scaffold(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_known_scaffold(db, form="είναι", bare="ειμαι", gloss="to be")
        db.commit()
        target_id = target.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "το βιβλίο είναι μεγάλο", "The book is big.")]),
        _verify_ok_response((0, 1, 2, 3)),
        _quality_response(),
    ]

    result = mg.batch_generate_material(
        language_code="el",
        lemma_ids=[target_id],
        sentences_per_target=1,
    )
    assert result["generated"] == 1


# ─── Warm-cache tests ────────────────────────────────────────────────────────


def test_warm_cache_fills_only_below_target(tmp_db, fake_claude, monkeypatch):
    """warm_sentence_cache picks lemmas with fewer than ACTIVE_TARGET generated
    quality-approved sentences. Lemmas already meeting the generated target are
    skipped."""
    monkeypatch.setattr(mg, "COVERAGE_GEN_ENABLED", False)  # retrieval-phase test
    with tmp_db() as db:
        needy = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, needy.lemma_id)
        already_full = _seed_lemma(db, form="σπίτι", bare="σπιτι", gloss="house")
        _seed_acquiring(db, already_full.lemma_id)
        _seed_known_scaffold(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_known_scaffold(db, form="είναι", bare="ειμαι", gloss="to be")
        # Three already-existing sentences for the "full" lemma so it meets
        # ACTIVE_TARGET (default 3).
        for _ in range(mg.ACTIVE_TARGET):
            s = Sentence(
                language_code="el",
                text="το σπίτι είναι μεγάλο",
                source="llm",
                target_lemma_id=already_full.lemma_id,
                is_active=True,
                mappings_verified_at=datetime.now(timezone.utc),
                quality_natural=True,
                quality_translation_correct=True,
            )
            db.add(s)
            db.flush()
            db.add(SentenceWord(
                sentence_id=s.id,
                position=1,
                surface_form="σπίτι",
                lemma_id=already_full.lemma_id,
            ))
        db.commit()
        needy_id = needy.lemma_id
        full_id = already_full.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "το βιβλίο είναι μεγάλο", "The book is big.")]),
        _verify_ok_response(),
        _quality_response(),
    ]

    result = mg.warm_sentence_cache(language_code="el", max_lemmas=10,
                                    sentences_per_target=1)
    assert result["gap_count"] == 1
    assert result["generated"] == 1

    with tmp_db() as db:
        for_needy = db.query(Sentence).filter(
            Sentence.target_lemma_id == needy_id,
            Sentence.source == "llm",
        ).count()
        for_full = db.query(Sentence).filter(
            Sentence.target_lemma_id == full_id,
            Sentence.source == "llm",
        ).count()
        assert for_needy == 1
        assert for_full == mg.ACTIVE_TARGET


def test_warm_cache_no_op_when_no_gaps(tmp_db, fake_claude):
    """No acquiring/learning/known lemmas → no LLM calls, no rows written."""
    with tmp_db() as db:
        # Nothing seeded.
        db.commit()

    result = mg.warm_sentence_cache(language_code="el", max_lemmas=10)
    assert result["gap_count"] == 0
    assert result["generated"] == 0
    assert fake_claude["calls"] == []


def test_warm_cache_skips_assumed_known_cognates_without_card(tmp_db, fake_claude, monkeypatch):
    """Cognate-known rows are scaffold vocabulary, not *retrieval* targets, until
    the learner misses them and they enter acquisition/FSRS. (Lever B coverage,
    disabled here, separately plants them — see the coverage tests below.)"""
    monkeypatch.setattr(mg, "COVERAGE_GEN_ENABLED", False)
    with tmp_db() as db:
        cognate = _seed_lemma(db, form="φιλοσοφία", bare="φιλοσοφια", gloss="philosophy")
        _seed_known(
            db,
            cognate.lemma_id,
            source="cognate",
            knowledge_origin="cognate_known",
            fsrs_card_json=None,
        )
        db.commit()

    result = mg.warm_sentence_cache(language_code="el", max_lemmas=10)
    assert result["gap_count"] == 0
    assert result["generated"] == 0
    assert fake_claude["calls"] == []


def test_warm_cache_includes_fsrs_known_card(tmp_db, fake_claude, monkeypatch):
    """A known word with an FSRS card is a review target and should get
    generated material when below coverage."""
    monkeypatch.setattr(mg, "COVERAGE_GEN_ENABLED", False)  # retrieval-phase test
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_known(
            db,
            target.lemma_id,
            fsrs_card_json={"due": datetime.now(timezone.utc).isoformat()},
        )
        _seed_known_scaffold(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_known_scaffold(db, form="είναι", bare="ειμαι", gloss="to be")
        db.commit()
        target_id = target.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "το βιβλίο είναι μεγάλο.", "The book is big.")]),
        _verify_ok_response(),
        _quality_response(),
    ]

    result = mg.warm_sentence_cache(language_code="el", max_lemmas=10,
                                    sentences_per_target=1)
    assert result["gap_count"] == 1
    assert result["generated"] == 1

    with tmp_db() as db:
        assert db.query(Sentence).filter(
            Sentence.target_lemma_id == target_id,
            Sentence.source == "llm",
        ).count() == 1


def test_generation_prompt_rejects_catalog_fragments():
    target = mg.GenTarget(
        lemma_id=1,
        lemma_form="βιβλίο",
        lemma_bare="βιβλιο",
        gloss_en="book",
        pos="noun",
    )
    prompt = mg._gen_prompt("el", [target], ["είμαι", "μεγάλο"], 1)
    assert "worth reading" in prompt
    assert "standalone complete thought" in prompt
    assert "colon-separated vocabulary lists" in prompt
    assert "comma chains" in prompt
    assert "target surface form exactly as written" in prompt
    assert "No surreal personification" in prompt
    assert "Allowed function words outside the pool" in prompt
    assert "όταν" in prompt
    assert "παρά" in prompt
    assert "κάπου" in prompt


def test_generation_prompt_includes_target_examples_and_candidate_depth():
    target = mg.GenTarget(
        lemma_id=1,
        lemma_form="μνήμη",
        lemma_bare="μνημη",
        gloss_en="memory",
        pos="noun",
        example_src="η μνήμη μένει",
        example_en="the memory remains",
    )
    prompt = mg._gen_prompt("el", [target], ["είμαι", "σπίτι"], 5)
    assert "produce exactly 5" in prompt
    assert "intended-sense example: η μνήμη μένει -> the memory remains" in prompt
    assert "common scaffold words" in prompt
    assert "first words in the known-word pool are safe scaffolding" in prompt


def test_compute_avoid_words_keeps_high_utility_scaffold_available():
    pool = [
        {"lemma_id": 1, "lemma_form": "είμαι"},
        {"lemma_id": 2, "lemma_form": "βιβλίο"},
    ]
    counts = {1: 10, 2: 10, 3: 1, 4: 1}
    assert mg._compute_avoid_words(pool, counts, "el") == ["βιβλίο"]


def test_known_sample_forces_high_utility_scaffold_words():
    sample = ["βιβλίο", "μεγάλο"]
    pool = [
        {"lemma_id": 1, "lemma_form": "βιβλίο", "lemma_bare": "βιβλιο"},
        {"lemma_id": 2, "lemma_form": "μεγάλο", "lemma_bare": "μεγαλο"},
        {"lemma_id": 3, "lemma_form": "είναι", "lemma_bare": "ειμαι"},
    ]
    result = mg._ensure_high_utility_scaffold_words(sample, pool, 2, "el")
    assert "είναι" in result
    assert len(result) == 2


def test_known_sample_prefers_common_scaffold_before_rare_diversity(monkeypatch):
    monkeypatch.setattr(mg, "COMMON_SCAFFOLD_SAMPLE_SIZE", 2)
    pool = [
        {"lemma_id": 1, "lemma_form": "σπάνιο", "frequency_rank": 9000},
        {"lemma_id": 2, "lemma_form": "άνθρωπος", "lemma_bare": "ανθρωπος", "frequency_rank": 20},
        {"lemma_id": 3, "lemma_form": "δρόμος", "lemma_bare": "δρομος", "frequency_rank": 30},
        {"lemma_id": 4, "lemma_form": "μαργαρίτα", "frequency_rank": 7000},
        {"lemma_id": 5, "lemma_form": "πλαστός", "frequency_rank": 8000},
    ]
    result = mg._sample_known_words_weighted(pool, {}, sample_size=4, language_code="el")
    assert result[:2] == ["άνθρωπος", "δρόμος"]


def test_wrong_verdict_with_function_word_correction_is_ignored():
    from app.services.sentence_validator import Mapping

    lemma = Lemma(
        lemma_id=1,
        language_code="el",
        lemma_form="μακριά",
        lemma_bare="μακρια",
        gloss_en="far",
    )
    decision = mg.VerifyDecision(
        sentence_index=0,
        position=2,
        verdict="wrong",
        correct_lemma="κοντά",
    )
    should_reject = mg._wrong_verdict_rejects_candidate(
        decision,
        mappings=[
            Mapping(position=2, surface_form="μακριά", lemma_id=1),
        ],
        lemma_by_id={1: lemma},
        language_code="el",
        function_words={"κοντα"},
    )
    assert should_reject is False


# ─── Book-sentence translation ───────────────────────────────────────────────


def _translate_response(mapping: dict[int, str]) -> _FakeProc:
    """Fake Haiku translation response: {sentence_id: english}."""
    return _FakeProc(stdout=_envelope({
        "translations": [{"id": sid, "english": eng} for sid, eng in mapping.items()],
    }))


def _seed_book_sentence(db, lemma_id: int, *, surface: str = "βιβλίο",
                        text: str = "το βιβλίο είναι μεγάλο",
                        translation_en: str | None = None) -> Sentence:
    s = Sentence(
        language_code="el",
        text=text,
        source="textbook",
        translation_en=translation_en,
        is_active=True,
        mappings_verified_at=datetime.now(timezone.utc),
    )
    db.add(s)
    db.flush()
    db.add(SentenceWord(
        sentence_id=s.id, position=0, surface_form=surface, lemma_id=lemma_id,
    ))
    db.flush()
    return s


def test_translate_fills_untranslated_book_sentence(tmp_db, fake_claude):
    """A NULL-translation book sentence covering an active-study lemma gets its
    translation_en filled by the Haiku pass."""
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, lemma.lemma_id)
        s = _seed_book_sentence(db, lemma.lemma_id)
        db.commit()
        sid = s.id

    fake_claude["script"] = [_translate_response({sid: "The book is big."})]

    result = mg.translate_untranslated_sentences(language_code="el", max_sentences=50)
    assert result["pending"] == 1
    assert result["translated"] == 1

    with tmp_db() as db:
        row = db.query(Sentence).filter(Sentence.id == sid).first()
        assert row.translation_en == "The book is big."


def test_translate_skips_sentence_with_no_active_lemma(tmp_db, fake_claude):
    """A book sentence whose only lemma is never-engaged (no ULK) is not a
    pickable fallback, so it's not worth a Claude call — nothing pending."""
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        # No ULK → not in active study.
        _seed_book_sentence(db, lemma.lemma_id)
        db.commit()

    result = mg.translate_untranslated_sentences(language_code="el")
    assert result["pending"] == 0
    assert result["translated"] == 0
    assert fake_claude["calls"] == []


def test_translate_skips_already_translated(tmp_db, fake_claude):
    """Idempotent: sentences that already have a translation are not re-sent."""
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, lemma.lemma_id)
        _seed_book_sentence(db, lemma.lemma_id, translation_en="Already done.")
        db.commit()

    result = mg.translate_untranslated_sentences(language_code="el")
    assert result["pending"] == 0
    assert fake_claude["calls"] == []


def test_translate_batch_failure_writes_nothing(tmp_db, fake_claude):
    """LLM failure (non-zero exit) → translations dropped, translation_en stays
    NULL (verification-failure-≠-success applied to translation)."""
    with tmp_db() as db:
        lemma = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, lemma.lemma_id)
        s = _seed_book_sentence(db, lemma.lemma_id)
        db.commit()
        sid = s.id

    fake_claude["script"] = [_FakeProc(stdout="", stderr="boom", returncode=1)]

    result = mg.translate_untranslated_sentences(language_code="el")
    assert result["pending"] == 1
    assert result["translated"] == 0

    with tmp_db() as db:
        row = db.query(Sentence).filter(Sentence.id == sid).first()
        assert row.translation_en is None


# ─── Lever A: post-gen over-exposure preference ──────────────────────────────


def test_scaffold_overexposure_count_counts_distinct_nontarget_overused():
    from app.services.sentence_validator import Mapping

    counts = {2: 12, 3: 1, 4: 10}  # lemmas 2 and 4 are over-exposed (>=10)
    mappings = [
        Mapping(position=0, surface_form="το", lemma_id=None),     # function word
        Mapping(position=1, surface_form="X", lemma_id=1),         # the target
        Mapping(position=2, surface_form="A", lemma_id=2),         # over-exposed
        Mapping(position=3, surface_form="B", lemma_id=3),         # fresh
        Mapping(position=4, surface_form="C", lemma_id=4),         # over-exposed
        Mapping(position=5, surface_form="A2", lemma_id=2),        # dup of 2 — counts once
    ]
    n = mg._scaffold_overexposure_count(
        mappings, target_lemma_id=1, sentence_counts=counts, threshold=10,
    )
    assert n == 2


def test_lever_a_prefers_fresh_scaffold_when_trimming(tmp_db, fake_claude, monkeypatch):
    """Two equally-valid candidates for one target, one leaning on an
    over-exposed scaffold word. With sentences_per_target=1, the fresher
    candidate must be the one stored."""
    monkeypatch.setattr(mg, "DIVERSITY_SENTENCE_THRESHOLD", 1)
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
        _seed_known_scaffold(db, form="είναι", bare="ειμαι", gloss="to be")
        _seed_known_scaffold(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        saturated = _seed_known_scaffold(db, form="παλιό", bare="παλιος", gloss="old")
        # One existing verified sentence pushes παλιό to coverage 1 (== the
        # monkeypatched threshold), so any candidate using it is "over-exposed".
        prior = Sentence(
            language_code="el",
            text="το σπίτι είναι παλιό",
            source="llm",
            is_active=True,
            mappings_verified_at=datetime.now(timezone.utc),
        )
        db.add(prior)
        db.flush()
        db.add(SentenceWord(
            sentence_id=prior.id, position=3, surface_form="παλιό",
            lemma_id=saturated.lemma_id,
        ))
        db.commit()
        target_id = target.lemma_id

    fake_claude["script"] = [
        _gen_response([
            (0, "το βιβλίο είναι παλιό.", "The book is old."),    # over-exposed first
            (0, "το βιβλίο είναι μεγάλο.", "The book is big."),   # fresh second
        ]),
        _verify_ok_response_for_candidates(2),
        _quality_response([
            {"id": 0, "natural": True, "translation_correct": True, "reason": "ok"},
            {"id": 1, "natural": True, "translation_correct": True, "reason": "ok"},
        ]),
    ]

    result = mg.batch_generate_material(
        language_code="el",
        lemma_ids=[target_id],
        sentences_per_target=1,
    )
    assert result["generated"] == 1

    with tmp_db() as db:
        stored = db.query(Sentence).filter(
            Sentence.target_lemma_id == target_id,
            Sentence.source == "llm",
        ).all()
        assert len(stored) == 1
        # The fresh-scaffold candidate won the trim despite being generated 2nd.
        assert stored[0].text == "το βιβλίο είναι μεγάλο."


# ─── Lever B: coverage generation for unconfirmed assumed-known words ─────────


def _seed_confirmed_scaffold(db, **kwargs) -> Lemma:
    """Known, no FSRS card, but already confirmed by exposure — engaged scaffold
    that is NOT a coverage candidate."""
    lemma = _seed_lemma(db, **kwargs)
    ulk = _seed_known(db, lemma.lemma_id)
    ulk.confirmed_at = datetime.now(timezone.utc)
    db.flush()
    return lemma


def test_coverage_gap_selects_only_unconfirmed_assumed_known(tmp_db):
    with tmp_db() as db:
        # Eligible: known, no card, unconfirmed, zero coverage.
        wanted = _seed_lemma(db, form="νερό", bare="νερο", gloss="water")
        _seed_known(db, wanted.lemma_id)
        # Excluded: confirmed already.
        confirmed = _seed_lemma(db, form="φως", bare="φως", gloss="light")
        ulk_c = _seed_known(db, confirmed.lemma_id)
        ulk_c.confirmed_at = datetime.now(timezone.utc)
        # Excluded: has an FSRS card (retrieval target, not assumed-known).
        carded = _seed_lemma(db, form="δέντρο", bare="δεντρο", gloss="tree")
        _seed_known(db, carded.lemma_id, fsrs_card_json={"due": "2026-01-01"})
        # Excluded: acquiring (retrieval target).
        acq = _seed_lemma(db, form="πέτρα", bare="πετρα", gloss="stone")
        _seed_acquiring(db, acq.lemma_id)
        # Excluded: non-content function word.
        fw = _seed_lemma(db, form="και", bare="και", gloss="and",
                         word_category="function_word")
        _seed_known(db, fw.lemma_id)
        # Excluded: unconfirmed assumed-known but already at coverage target.
        covered = _seed_lemma(db, form="ουρανός", bare="ουρανος", gloss="sky")
        _seed_known(db, covered.lemma_id)
        s = Sentence(
            language_code="el", text="ο ουρανός είναι μεγάλος", source="llm",
            is_active=True, mappings_verified_at=datetime.now(timezone.utc),
        )
        db.add(s)
        db.flush()
        db.add(SentenceWord(sentence_id=s.id, position=1, surface_form="ουρανός",
                            lemma_id=covered.lemma_id))
        db.commit()
        wanted_id = wanted.lemma_id

    with tmp_db() as db:
        gaps = mg._coverage_lemmas_missing_material(
            db, "el", target_count=1, limit=50,
        )
    assert gaps == [wanted_id]


def test_warm_cache_coverage_phase_plants_unconfirmed_assumed_known(tmp_db, fake_claude):
    """With no retrieval gaps, the coverage phase generates a sentence targeting
    an unconfirmed assumed-known word so the sweep can later confirm it."""
    with tmp_db() as db:
        wanted = _seed_lemma(db, form="νερό", bare="νερο", gloss="water")
        _seed_known(db, wanted.lemma_id)  # unconfirmed assumed-known
        # Confirmed scaffold: engaged but neither a retrieval gap nor a coverage
        # candidate, so the only gap is `wanted`.
        _seed_confirmed_scaffold(db, form="είναι", bare="ειμαι", gloss="to be")
        _seed_confirmed_scaffold(db, form="καλό", bare="καλος", gloss="good")
        db.commit()
        wanted_id = wanted.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "το νερό είναι καλό.", "The water is good.")]),
        _verify_ok_response((1, 2, 3)),
        _quality_response(),
    ]

    result = mg.warm_sentence_cache(language_code="el", max_lemmas=10,
                                    coverage_max_lemmas=10)
    assert result["gap_count"] == 0
    assert result["coverage_gap_count"] == 1
    assert result["coverage_generated"] == 1

    with tmp_db() as db:
        assert db.query(Sentence).filter(
            Sentence.target_lemma_id == wanted_id,
            Sentence.source == "llm",
        ).count() == 1


def test_warm_cache_coverage_disabled_skips_assumed_known(tmp_db, fake_claude, monkeypatch):
    """POLYGLOT_COVERAGE_GEN=0 → no coverage phase, no LLM spend on scaffold."""
    monkeypatch.setattr(mg, "COVERAGE_GEN_ENABLED", False)
    with tmp_db() as db:
        wanted = _seed_lemma(db, form="νερό", bare="νερο", gloss="water")
        _seed_known(db, wanted.lemma_id)
        db.commit()

    result = mg.warm_sentence_cache(language_code="el", max_lemmas=10,
                                    coverage_max_lemmas=10)
    assert result["gap_count"] == 0
    assert result["coverage_gap_count"] == 0
    assert result["coverage_generated"] == 0
    assert fake_claude["calls"] == []


def test_warm_cache_does_not_count_harvested_sentenceword_coverage(tmp_db, fake_claude, monkeypatch):
    """Textbook sentences are review fallbacks; they do not satisfy the
    generated-material target."""
    monkeypatch.setattr(mg, "COVERAGE_GEN_ENABLED", False)  # retrieval-phase test
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
        _seed_known_scaffold(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_known_scaffold(db, form="είναι", bare="ειμαι", gloss="to be")
        for _ in range(mg.ACTIVE_TARGET):
            s = Sentence(
                language_code="el",
                text="το βιβλίο είναι μεγάλο",
                source="textbook",
                target_lemma_id=None,
                is_active=True,
                mappings_verified_at=datetime.now(timezone.utc),
            )
            db.add(s)
            db.flush()
            db.add(SentenceWord(
                sentence_id=s.id,
                position=1,
                surface_form="βιβλίο",
                lemma_id=target.lemma_id,
            ))
        db.commit()
        target_id = target.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "το βιβλίο είναι μεγάλο.", "The book is big.")]),
        _verify_ok_response(),
        _quality_response(),
    ]

    result = mg.warm_sentence_cache(language_code="el", max_lemmas=10,
                                    sentences_per_target=1)
    assert result["gap_count"] == 1
    assert result["generated"] == 1

    with tmp_db() as db:
        assert db.query(Sentence).filter(
            Sentence.target_lemma_id == target_id,
            Sentence.source == "llm",
        ).count() == 1
