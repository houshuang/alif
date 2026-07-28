from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.corpus_enrichment import (
    CORPUS_CLAIM_SENTINEL,
    CorpusScope,
    activate_prepared_corpus_sentences,
    enrich_corpus_sentences,
    generate_corpus_enrichment_batch,
    has_arabic_diacritics,
    plan_corpus_activation,
    plan_corpus_enrichment,
    recover_scoped_legacy_claims,
)
from app.services.llm import SentenceReviewResult
from app.services.sentence_validator import TokenMapping


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _lemma(
    db,
    lemma_id: int,
    arabic: str,
    gloss: str,
    *,
    category: str | None = None,
    canonical_id: int | None = None,
    function_override: bool | None = None,
) -> Lemma:
    row = Lemma(
        lemma_id=lemma_id,
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        gloss_en=gloss,
        pos="noun",
        word_category=category,
        canonical_lemma_id=canonical_id,
        function_word_override=function_override,
        gates_completed_at=NOW,
    )
    db.add(row)
    db.flush()
    return row


def _knowledge(
    db,
    lemma_id: int,
    *,
    state: str,
    due: datetime | None = None,
) -> UserLemmaKnowledge:
    row = UserLemmaKnowledge(
        lemma_id=lemma_id,
        knowledge_state=state,
        acquisition_box=1 if state == "acquiring" else None,
        acquisition_next_due=due if state == "acquiring" else None,
        fsrs_card_json=(
            {"due": due.isoformat(), "stability": 3.0}
            if state != "acquiring" and due is not None
            else None
        ),
    )
    db.add(row)
    db.flush()
    return row


def _sentence(
    db,
    sentence_id: int,
    *,
    kind: str = "momo_book",
    lemma_ids: list[int | None],
    verification: datetime | None = None,
    active: bool = False,
    target_id: int | None = None,
    quality: tuple[bool, bool] | None = None,
) -> Sentence:
    row = Sentence(
        id=sentence_id,
        arabic_text="إِنَّ الْكِتَابَ جَدِيدٌ.",
        english_translation="The book is new.",
        source="corpus",
        kind=kind,
        is_active=active,
        target_lemma_id=target_id or next(
            (lemma_id for lemma_id in lemma_ids if lemma_id is not None),
            None,
        ),
        mappings_verified_at=verification,
        quality_reviewed_at=NOW if quality is not None else None,
        quality_natural=quality[0] if quality is not None else None,
        quality_translation_correct=quality[1] if quality is not None else None,
    )
    db.add(row)
    db.flush()
    for position, lemma_id in enumerate(lemma_ids):
        db.add(
            SentenceWord(
                sentence_id=row.id,
                position=position,
                surface_form=f"word-{position}",
                lemma_id=lemma_id,
                is_target_word=lemma_id == row.target_lemma_id,
            )
        )
    db.flush()
    return row


def _mappings(*lemma_ids: int) -> list[TokenMapping]:
    return [
        TokenMapping(
            position=position,
            surface_form=f"surface-{position}",
            lemma_id=lemma_id,
            is_target=False,
            is_function_word=False,
        )
        for position, lemma_id in enumerate(lemma_ids)
    ]


def test_scope_is_required_before_candidate_query(db_session):
    with pytest.raises(ValueError, match="requires"):
        plan_corpus_enrichment(db_session)

    with pytest.raises(ValueError, match="requires"):
        CorpusScope.build()

    with pytest.raises(ValueError, match="ceiling"):
        plan_corpus_activation(
            db_session,
            kind="momo_book",
            active_ceiling=-1,
        )


def test_service_requires_separate_preparation_and_activation_invocations(
    db_session,
):
    with pytest.raises(ValueError, match="separate invocations"):
        enrich_corpus_sentences(
            db_session,
            kind="momo_book",
            limit=1,
            activate_limit=1,
            active_ceiling=1950,
            now=NOW,
            write_activity=False,
        )


def test_activation_only_does_not_recover_preparation_claims(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(
        db_session,
        1,
        state="known",
        due=NOW - timedelta(hours=1),
    )
    stranded = _sentence(
        db_session,
        19,
        lemma_ids=[1],
        verification=CORPUS_CLAIM_SENTINEL,
        target_id=1,
    )
    db_session.commit()

    result = enrich_corpus_sentences(
        db_session,
        kind="momo_book",
        limit=0,
        activate_limit=0,
        active_ceiling=1950,
        now=NOW,
        write_activity=False,
    )

    db_session.refresh(stranded)
    assert result.recovered_legacy_claim_ids == []
    assert stranded.mappings_verified_at == CORPUS_CLAIM_SENTINEL


def test_diacritic_gate_requires_substantial_vowel_coverage():
    assert has_arabic_diacritics("كَتَبَ الْوَلَدُ")
    assert not has_arabic_diacritics("كَتب الولد")
    assert not has_arabic_diacritics("كتب الولد")
    assert not has_arabic_diacritics(None)


def test_duplicate_enrichment_id_is_rejected():
    sentence = type("CorpusInput", (), {"id": 10, "arabic_text": "كتب"})()
    with patch(
        "app.services.llm.generate_completion",
        return_value={
            "sentences": [
                {
                    "id": 10,
                    "diacritized": "كَتَبَ",
                    "translation": "He wrote.",
                },
                {
                    "id": 10,
                    "diacritized": "كُتِبَ",
                    "translation": "It was written.",
                },
                {
                    "id": 10,
                    "diacritized": "كَتَبَ",
                    "translation": "He wrote.",
                },
            ]
        },
    ):
        assert generate_corpus_enrichment_batch([sentence]) == {}


def test_plan_intersects_kind_and_ids_and_orders_by_demand(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _lemma(db_session, 2, "بيت", "house")
    _lemma(db_session, 3, "قلم", "pen")
    _knowledge(db_session, 1, state="known", due=NOW - timedelta(days=2))
    _knowledge(db_session, 2, state="known", due=NOW + timedelta(days=2))
    _knowledge(db_session, 3, state="acquiring", due=NOW - timedelta(hours=1))
    _sentence(db_session, 30, lemma_ids=[2])
    _sentence(db_session, 10, lemma_ids=[1])
    _sentence(db_session, 20, lemma_ids=[3])
    _sentence(db_session, 40, kind="other_book", lemma_ids=[1])
    db_session.commit()

    plan = plan_corpus_enrichment(
        db_session,
        kind="momo_book",
        sentence_ids=[10, 20, 30, 40],
        limit=10,
        now=NOW,
    )

    assert [candidate.sentence_id for candidate in plan] == [10, 30, 20]


def test_legacy_claim_recovery_is_confined_to_exact_scope(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    momo = _sentence(
        db_session,
        1,
        lemma_ids=[1],
        verification=CORPUS_CLAIM_SENTINEL,
    )
    generic = _sentence(
        db_session,
        2,
        kind="other_book",
        lemma_ids=[1],
        verification=CORPUS_CLAIM_SENTINEL,
    )
    db_session.commit()

    recovered = recover_scoped_legacy_claims(
        db_session,
        CorpusScope.build(kind="momo_book"),
    )
    db_session.refresh(momo)
    db_session.refresh(generic)

    assert recovered == [1]
    assert momo.mappings_verified_at is None
    assert generic.mappings_verified_at == CORPUS_CLAIM_SENTINEL


def test_success_repairs_canonical_target_once_and_stays_inactive(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _lemma(db_session, 2, "كُتُب", "books", canonical_id=1)
    # A real function word used as the provisional imported target.
    _lemma(db_session, 3, "إن", "indeed", function_override=True)
    _knowledge(db_session, 1, state="known", due=NOW - timedelta(days=1))
    sentence = _sentence(
        db_session,
        101,
        lemma_ids=[3, 2, 2],
        target_id=3,
    )
    db_session.commit()

    mappings = _mappings(3, 2, 2)
    mappings[0].is_function_word = True

    def verify_clean(batch, lemma_map):
        assert not db_session.new
        assert not db_session.dirty
        assert not db_session.deleted
        return [{"disambiguation": [], "issues": []}]

    def quality_clean(inputs):
        assert not db_session.new
        assert not db_session.dirty
        assert not db_session.deleted
        return [
            SentenceReviewResult(
                natural=True,
                translation_correct=True,
                reason="faithful and natural",
            )
        ]

    with (
        patch(
            "app.services.sentence_validator.build_comprehensive_lemma_lookup",
            return_value={},
        ),
        patch(
            "app.services.sentence_validator.detect_proper_names",
            return_value=set(),
        ),
        patch(
            "app.services.sentence_validator.map_tokens_to_lemmas",
            return_value=mappings,
        ),
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
            side_effect=verify_clean,
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            side_effect=quality_clean,
        ),
    ):
        result = enrich_corpus_sentences(
            db_session,
            kind="momo_book",
            limit=1,
            activate_limit=0,
            active_ceiling=1950,
            now=NOW,
            write_activity=False,
        )

    db_session.refresh(sentence)
    words = (
        db_session.query(SentenceWord)
        .filter(SentenceWord.sentence_id == sentence.id)
        .order_by(SentenceWord.position)
        .all()
    )

    assert result.prepared_ids == [sentence.id]
    assert result.activated_ids == []
    assert sentence.is_active is False
    assert sentence.target_lemma_id == 1
    assert _as_utc(sentence.mappings_verified_at) == NOW
    assert [word.position for word in words if word.is_target_word] == [1]
    assert words[1].lemma_id == 2  # surface mapping remains the stored variant


def test_completed_quality_rejection_is_terminal_and_inactive(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 102, lemma_ids=[1])
    db_session.commit()

    with (
        patch(
            "app.services.sentence_validator.build_comprehensive_lemma_lookup",
            return_value={},
        ),
        patch(
            "app.services.sentence_validator.detect_proper_names",
            return_value=set(),
        ),
        patch(
            "app.services.sentence_validator.map_tokens_to_lemmas",
            return_value=_mappings(1),
        ),
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
            return_value=[{"disambiguation": [], "issues": []}],
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=False,
                    translation_correct=True,
                    reason="fragment",
                )
            ],
        ),
    ):
        result = enrich_corpus_sentences(
            db_session,
            kind="momo_book",
            limit=1,
            activate_limit=0,
            active_ceiling=1950,
            now=NOW,
            write_activity=False,
        )

    db_session.refresh(sentence)
    assert result.quality_rejected_ids == [sentence.id]
    assert _as_utc(sentence.mappings_verified_at) == NOW
    assert _as_utc(sentence.quality_reviewed_at) == NOW
    assert sentence.quality_natural is False
    assert sentence.is_active is False


def test_incomplete_quality_review_releases_claim_for_retry(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 103, lemma_ids=[1])
    db_session.commit()

    with (
        patch(
            "app.services.sentence_validator.build_comprehensive_lemma_lookup",
            return_value={},
        ),
        patch(
            "app.services.sentence_validator.detect_proper_names",
            return_value=set(),
        ),
        patch(
            "app.services.sentence_validator.map_tokens_to_lemmas",
            return_value=_mappings(1),
        ),
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
            return_value=[{"disambiguation": [], "issues": []}],
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=False,
                    translation_correct=False,
                    reason="provider unavailable",
                    review_completed=False,
                )
            ],
        ),
    ):
        result = enrich_corpus_sentences(
            db_session,
            kind="momo_book",
            limit=1,
            activate_limit=0,
            active_ceiling=1950,
            now=NOW,
            write_activity=False,
        )

    db_session.refresh(sentence)
    assert result.retry_ids == [sentence.id]
    assert sentence.mappings_verified_at is None
    assert sentence.quality_reviewed_at is None
    assert sentence.is_active is False


def test_undiacritized_enrichment_output_releases_claim(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 105, lemma_ids=[1])
    sentence.arabic_text = "كتب الولد"
    db_session.commit()

    with (
        patch(
            "app.services.corpus_enrichment.generate_corpus_enrichment_batch",
            return_value={
                sentence.id: {
                    "diacritized": "كتب الولد",
                    "translation": "The boy wrote.",
                }
            },
        ),
        patch(
            "app.services.sentence_validator.map_tokens_to_lemmas",
        ) as mapper,
    ):
        result = enrich_corpus_sentences(
            db_session,
            kind="momo_book",
            limit=1,
            activate_limit=0,
            active_ceiling=1950,
            now=NOW,
            write_activity=False,
        )

    db_session.refresh(sentence)
    assert result.retry_ids == [sentence.id]
    assert sentence.mappings_verified_at is None
    assert sentence.arabic_text == "كتب الولد"
    mapper.assert_not_called()


def test_terminal_target_rejection_persists_fail_closed_verdict(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _lemma(
        db_session,
        2,
        "إن",
        "indeed",
        function_override=True,
    )
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 104, lemma_ids=[1])
    db_session.commit()

    with (
        patch(
            "app.services.sentence_validator.build_comprehensive_lemma_lookup",
            return_value={},
        ),
        patch(
            "app.services.sentence_validator.detect_proper_names",
            return_value=set(),
        ),
        patch(
            "app.services.sentence_validator.map_tokens_to_lemmas",
            return_value=_mappings(2),
        ),
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
            return_value=[{"disambiguation": [], "issues": []}],
        ),
    ):
        result = enrich_corpus_sentences(
            db_session,
            kind="momo_book",
            limit=1,
            activate_limit=0,
            active_ceiling=1950,
            now=NOW,
            write_activity=False,
        )

    db_session.refresh(sentence)
    assert result.target_rejected_ids == [sentence.id]
    assert result.mapping_rejected_ids == [sentence.id]
    assert _as_utc(sentence.mappings_verified_at) == NOW
    assert _as_utc(sentence.quality_reviewed_at) == NOW
    assert sentence.quality_natural is False
    assert sentence.quality_translation_correct is False
    assert sentence.is_active is False


def test_short_verifier_result_releases_whole_batch(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    first = _sentence(db_session, 110, lemma_ids=[1])
    second = _sentence(db_session, 111, lemma_ids=[1])
    db_session.commit()

    with (
        patch(
            "app.services.sentence_validator.build_comprehensive_lemma_lookup",
            return_value={},
        ),
        patch(
            "app.services.sentence_validator.detect_proper_names",
            return_value=set(),
        ),
        patch(
            "app.services.sentence_validator.map_tokens_to_lemmas",
            return_value=_mappings(1),
        ),
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
            return_value=[{"disambiguation": [], "issues": []}],
        ),
    ):
        result = enrich_corpus_sentences(
            db_session,
            kind="momo_book",
            limit=2,
            activate_limit=0,
            active_ceiling=1950,
            verification_batch_size=2,
            now=NOW,
            write_activity=False,
        )

    db_session.refresh(first)
    db_session.refresh(second)
    assert result.retry_ids == [110, 111]
    assert first.mappings_verified_at is None
    assert second.mappings_verified_at is None


def test_unexpected_exception_releases_recovered_and_new_claim(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(
        db_session,
        120,
        lemma_ids=[1],
        verification=CORPUS_CLAIM_SENTINEL,
    )
    db_session.commit()

    with (
        patch(
            "app.services.sentence_validator.build_comprehensive_lemma_lookup",
            return_value={},
        ),
        patch(
            "app.services.sentence_validator.detect_proper_names",
            return_value=set(),
        ),
        patch(
            "app.services.sentence_validator.map_tokens_to_lemmas",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        enrich_corpus_sentences(
            db_session,
            kind="momo_book",
            limit=1,
            activate_limit=0,
            active_ceiling=1950,
            now=NOW,
            write_activity=False,
        )

    db_session.refresh(sentence)
    assert sentence.mappings_verified_at is None
    assert sentence.is_active is False


def test_activation_blocks_any_acquiring_content_and_respects_capacity(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _lemma(db_session, 2, "بيت", "house")
    _knowledge(db_session, 1, state="known", due=NOW - timedelta(hours=1))
    _knowledge(
        db_session,
        2,
        state="acquiring",
        due=NOW + timedelta(days=1),
    )
    clean = _sentence(
        db_session,
        201,
        lemma_ids=[1],
        verification=NOW,
        target_id=1,
        quality=(True, True),
    )
    blocked = _sentence(
        db_session,
        202,
        lemma_ids=[1, 2],
        verification=NOW,
        target_id=1,
        quality=(True, True),
    )
    db_session.commit()

    scope = CorpusScope.build(kind="momo_book")
    diagnostic = plan_corpus_activation(
        db_session,
        kind=scope.kind,
        activate_limit=0,
        active_ceiling=1,
        now=NOW,
    )
    assert diagnostic.selected_ids == []
    assert diagnostic.eligible_ids == [clean.id]
    assert diagnostic.blocked_acquiring_ids == [blocked.id]

    plan = plan_corpus_activation(
        db_session,
        kind=scope.kind,
        activate_limit=20,
        active_ceiling=1,
        now=NOW,
    )
    assert plan.selected_ids == [clean.id]
    assert plan.blocked_acquiring_ids == [blocked.id]

    applied = activate_prepared_corpus_sentences(
        db_session,
        scope=scope,
        activate_limit=20,
        active_ceiling=1,
        now=NOW,
    )
    db_session.refresh(clean)
    db_session.refresh(blocked)
    assert applied.selected_ids == [clean.id]
    assert clean.is_active is True
    assert blocked.is_active is False

    capped = plan_corpus_activation(
        db_session,
        kind=scope.kind,
        activate_limit=20,
        active_ceiling=1,
        now=NOW,
    )
    assert capped.capacity == 0
    assert capped.selected_ids == []


def test_activation_rechecks_fresh_demand_after_planning(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _lemma(db_session, 2, "بيت", "house")
    newly_acquiring = _knowledge(
        db_session,
        1,
        state="known",
        due=NOW - timedelta(hours=1),
    )
    no_longer_due = _knowledge(
        db_session,
        2,
        state="known",
        due=NOW - timedelta(hours=1),
    )
    acquiring_sentence = _sentence(
        db_session,
        211,
        lemma_ids=[1],
        verification=NOW,
        target_id=1,
        quality=(True, True),
    )
    no_demand_sentence = _sentence(
        db_session,
        212,
        lemma_ids=[2],
        verification=NOW,
        target_id=2,
        quality=(True, True),
    )
    db_session.commit()

    original_plan = plan_corpus_activation

    def plan_then_change_demand(*args, **kwargs):
        plan = original_plan(*args, **kwargs)
        assert plan.selected_ids == [
            acquiring_sentence.id,
            no_demand_sentence.id,
        ]
        newly_acquiring.knowledge_state = "acquiring"
        newly_acquiring.acquisition_box = 1
        newly_acquiring.acquisition_next_due = NOW
        newly_acquiring.fsrs_card_json = None
        no_longer_due.fsrs_card_json = {
            "due": (NOW + timedelta(days=10)).isoformat(),
            "stability": 3.0,
        }
        db_session.commit()
        return plan

    with patch(
        "app.services.corpus_enrichment.plan_corpus_activation",
        side_effect=plan_then_change_demand,
    ):
        applied = activate_prepared_corpus_sentences(
            db_session,
            scope=CorpusScope.build(kind="momo_book"),
            activate_limit=2,
            active_ceiling=2,
            now=NOW,
        )

    db_session.refresh(acquiring_sentence)
    db_session.refresh(no_demand_sentence)
    assert applied.selected_ids == []
    assert applied.blocked_acquiring_ids == [acquiring_sentence.id]
    assert applied.no_fsrs_demand_ids == [no_demand_sentence.id]
    assert acquiring_sentence.is_active is False
    assert no_demand_sentence.is_active is False


def test_activation_coverage_is_counted_by_canonical_sentence(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _lemma(db_session, 2, "كُتُب", "books", canonical_id=1)
    _lemma(db_session, 3, "بيت", "house")
    _knowledge(db_session, 1, state="known", due=NOW - timedelta(hours=1))
    _knowledge(db_session, 3, state="known", due=NOW - timedelta(hours=1))

    # Existing reviewable coverage is stored against a surface variant of
    # lemma 1. The activation planner must credit it to canonical lemma 1.
    _sentence(
        db_session,
        300,
        kind="other_book",
        lemma_ids=[2],
        verification=NOW,
        active=True,
        target_id=1,
    )
    already_covered = _sentence(
        db_session,
        301,
        lemma_ids=[1],
        verification=NOW,
        target_id=1,
        quality=(True, True),
    )
    zero_coverage = _sentence(
        db_session,
        302,
        lemma_ids=[3],
        verification=NOW,
        target_id=3,
        quality=(True, True),
    )
    db_session.commit()

    plan = plan_corpus_activation(
        db_session,
        kind="momo_book",
        activate_limit=1,
        active_ceiling=2,
        now=NOW,
    )

    assert plan.eligible_ids == [already_covered.id, zero_coverage.id]
    assert plan.selected_ids == [zero_coverage.id]


def test_activation_rolls_back_all_rows_when_target_repair_raises(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _lemma(db_session, 2, "بيت", "house")
    _knowledge(db_session, 1, state="known", due=NOW - timedelta(hours=1))
    _knowledge(db_session, 2, state="known", due=NOW - timedelta(hours=1))
    first = _sentence(
        db_session,
        401,
        lemma_ids=[1],
        verification=NOW,
        target_id=1,
        quality=(True, True),
    )
    second = _sentence(
        db_session,
        402,
        lemma_ids=[2],
        verification=NOW,
        target_id=2,
        quality=(True, True),
    )
    db_session.commit()

    with (
        patch(
            "app.services.corpus_enrichment._target_choice",
            side_effect=[(1, 0), RuntimeError("target repair failed")],
        ),
        pytest.raises(RuntimeError, match="target repair failed"),
    ):
        activate_prepared_corpus_sentences(
            db_session,
            scope=CorpusScope.build(kind="momo_book"),
            activate_limit=2,
            active_ceiling=2,
            now=NOW,
        )

    db_session.refresh(first)
    db_session.refresh(second)
    assert first.is_active is False
    assert second.is_active is False
