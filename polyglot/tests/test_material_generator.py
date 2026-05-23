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


def _seed_lemma(db, *, form: str, bare: str | None = None, gloss: str = "x",
                canonical: int | None = None, word_category: str | None = None) -> Lemma:
    lemma = Lemma(
        language_code="el",
        lemma_form=form,
        lemma_bare=bare if bare is not None else form,
        gloss_en=gloss,
        source="test",
        canonical_lemma_id=canonical,
        word_category=word_category,
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


# ─── Generator tests with mocked Claude ──────────────────────────────────────


@pytest.fixture
def fake_claude(monkeypatch):
    """Patch ``subprocess.run`` inside material_generator to return canned
    responses based on a script. The script is a list of `_FakeProc` instances
    consumed in call order.
    """
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


def test_batch_generate_happy_path(tmp_db, fake_claude):
    """One target, one valid sentence, no verifier complaints → 1 row written
    with mappings_verified_at stamped."""
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
        # Scaffold lemma — the validator needs every non-function token to
        # resolve to *something* in the DB.
        _seed_lemma(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_lemma(db, form="είναι", bare="ειμαι", gloss="to be")
        db.commit()
        target_id = target.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "το βιβλίο είναι μεγάλο.", "The book is big.")]),
        _verify_ok_response(),
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
        _seed_lemma(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_lemma(db, form="είναι", bare="ειμαι", gloss="to be")
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
        _seed_lemma(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_lemma(db, form="είναι", bare="ειμαι", gloss="to be")
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
        _seed_lemma(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_lemma(db, form="είναι", bare="ειμαι", gloss="to be")
        db.commit()
        canonical_id = canonical.lemma_id
        variant_id = variant.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "το βιβλίο είναι μεγάλο", "The book is big.")]),
        _verify_ok_response(),
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


def test_incomplete_verifier_response_discards_candidate(tmp_db, fake_claude):
    """A verifier response must cover every mapped content position."""
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
        _seed_lemma(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_lemma(db, form="είναι", bare="ειμαι", gloss="to be")
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


def test_function_word_lemma_without_gloss_passes_gloss_gate(tmp_db, fake_claude):
    """Reading intake can create function-word Lemma rows without glosses."""
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
        _seed_lemma(db, form="το", bare="το", gloss="", word_category="function_word")
        _seed_lemma(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_lemma(db, form="είναι", bare="ειμαι", gloss="to be")
        db.commit()
        target_id = target.lemma_id

    fake_claude["script"] = [
        _gen_response([(0, "το βιβλίο είναι μεγάλο", "The book is big.")]),
        _verify_ok_response((0, 1, 2, 3)),
    ]

    result = mg.batch_generate_material(
        language_code="el",
        lemma_ids=[target_id],
        sentences_per_target=1,
    )
    assert result["generated"] == 1


# ─── Warm-cache tests ────────────────────────────────────────────────────────


def test_warm_cache_fills_only_below_target(tmp_db, fake_claude):
    """warm_sentence_cache picks lemmas with fewer than ACTIVE_TARGET active
    sentences. Lemmas already meeting the target are skipped."""
    with tmp_db() as db:
        needy = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, needy.lemma_id)
        already_full = _seed_lemma(db, form="σπίτι", bare="σπιτι", gloss="house")
        _seed_acquiring(db, already_full.lemma_id)
        _seed_lemma(db, form="μεγάλο", bare="μεγαλο", gloss="big")
        _seed_lemma(db, form="είναι", bare="ειμαι", gloss="to be")
        # Three already-existing sentences for the "full" lemma so it meets
        # ACTIVE_TARGET (default 3).
        for _ in range(mg.ACTIVE_TARGET):
            s = Sentence(
                language_code="el",
                text="το σπίτι είναι μεγάλο",
                source="manual",
                target_lemma_id=already_full.lemma_id,
                is_active=True,
                mappings_verified_at=datetime.now(timezone.utc),
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
        assert for_full == 0


def test_warm_cache_no_op_when_no_gaps(tmp_db, fake_claude):
    """No acquiring/learning/known lemmas → no LLM calls, no rows written."""
    with tmp_db() as db:
        # Nothing seeded.
        db.commit()

    result = mg.warm_sentence_cache(language_code="el", max_lemmas=10)
    assert result["gap_count"] == 0
    assert result["generated"] == 0
    assert fake_claude["calls"] == []


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


def test_warm_cache_counts_harvested_sentenceword_coverage(tmp_db, fake_claude):
    """Harvested textbook sentences have SentenceWord coverage but no target_lemma_id."""
    with tmp_db() as db:
        target = _seed_lemma(db, form="βιβλίο", bare="βιβλιο", gloss="book")
        _seed_acquiring(db, target.lemma_id)
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

    result = mg.warm_sentence_cache(language_code="el", max_lemmas=10)
    assert result["gap_count"] == 0
    assert result["generated"] == 0
    assert fake_claude["calls"] == []
