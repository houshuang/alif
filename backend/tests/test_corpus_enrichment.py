from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.models import (
    ActivityLog,
    Lemma,
    Sentence,
    SentenceWord,
    UserLemmaKnowledge,
)
from app.services.corpus_enrichment import (
    CORPUS_BLOCKED_SENTINEL,
    CORPUS_CLAIM_SENTINEL,
    CORPUS_QUALITY_REJECTED_SENTINEL,
    CorpusScope,
    activate_prepared_corpus_sentences,
    enrich_corpus_sentences,
    generate_corpus_enrichment_batch,
    has_arabic_diacritics,
    _project_diacritics_onto_source,
    plan_corpus_activation,
    plan_corpus_enrichment,
    plan_corpus_enrichment_report,
    recover_scoped_legacy_claims,
)
from app.services.llm import (
    MOMO_PUBLISHED_ARABIC_REVIEW_CONTEXT,
    SentenceReviewResult,
)
from app.services.sentence_validator import TokenMapping
from app.services.transliteration import transliterate_arabic


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


def test_legacy_requeue_filter_excludes_every_corpus_lifecycle_sentinel(
    db_session,
):
    from scripts.reenrich_corpus_post_step4c import (
        CORPUS_LIFECYCLE_EXCLUSION_SQL,
        CORPUS_LIFECYCLE_SENTINEL_PARAMS,
    )

    rows = [
        Sentence(
            arabic_text=f"sentence-{index}",
            source="corpus",
            is_active=False,
            mappings_verified_at=stamp,
        )
        for index, stamp in enumerate([
            CORPUS_CLAIM_SENTINEL,
            CORPUS_BLOCKED_SENTINEL,
            CORPUS_QUALITY_REJECTED_SENTINEL,
            NOW,
        ])
    ]
    db_session.add_all(rows)
    db_session.flush()

    eligible_ids = set(db_session.execute(
        text(
            "SELECT id FROM sentences WHERE source='corpus' "
            "AND is_active=0 AND mappings_verified_at IS NOT NULL"
            + CORPUS_LIFECYCLE_EXCLUSION_SQL
        ),
        CORPUS_LIFECYCLE_SENTINEL_PARAMS,
    ).scalars())

    assert eligible_ids == {rows[-1].id}


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


def test_service_rejects_invalid_ceiling_before_preparation(db_session):
    with patch(
        "app.services.corpus_enrichment.plan_corpus_enrichment_report"
    ) as preflight:
        with pytest.raises(ValueError, match="ceiling"):
            enrich_corpus_sentences(
                db_session,
                kind="momo_book",
                limit=1,
                activate_limit=0,
                active_ceiling=-1,
                now=NOW,
                write_activity=False,
            )
    preflight.assert_not_called()


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


def test_harakat_projection_preserves_observed_momo_source_layout():
    source = (
        "لاحظت أنهم أناس لطاف ، فقد كانوا أنفسهم فقراء ويعرفون الحياة ."
    )
    proposed = (
        "لَاحَظْتُ أَنَّهُمْ أُنَاسٌ لِطَافٌ، فَقَدْ كَانُوا "
        "أَنْفُسَهُمْ فُقَرَاءَ وَيَعْرِفُونَ الْحَيَاةَ."
    )

    assert _project_diacritics_onto_source(source, proposed) == (
        "لَاحَظْتُ أَنَّهُمْ أُنَاسٌ لِطَافٌ ، فَقَدْ كَانُوا "
        "أَنْفُسَهُمْ فُقَرَاءَ وَيَعْرِفُونَ الْحَيَاةَ ."
    )


def test_harakat_projection_preserves_quotes_whitespace_and_newlines():
    source = '" وتريدين  البقاء هنا ؟\nثم سكتت .'
    proposed = "وَتُرِيدِينَ الْبَقَاءَ هُنَا؟ ثُمَّ سَكَتَتْ."

    assert _project_diacritics_onto_source(source, proposed) == (
        '" وَتُرِيدِينَ  الْبَقَاءَ هُنَا ؟\nثُمَّ سَكَتَتْ .'
    )


def test_harakat_projection_preserves_exact_momo_maqsura_spellings():
    source = 'فأسرعت مومو مؤكدة بقولها : " إننى هنا في بيتى " .'
    proposed = (
        'فَأَسْرَعَتْ مَوْمُو مُؤَكِّدَةً بِقَوْلِهَا : " '
        'إِنَّنِى هُنَا فِي بَيْتِى " .'
    )

    assert _project_diacritics_onto_source(source, proposed) == proposed


def test_harakat_projection_preserves_source_tatweel_only():
    assert _project_diacritics_onto_source(
        "كـتب .",
        "كَتَبَ.",
    ) == "كَـتَبَ ."
    assert _project_diacritics_onto_source(
        "كتب .",
        "كَـتَبَ.",
    ) == "كَتَبَ ."


def test_harakat_projection_accepts_canonical_decomposed_hamza_only():
    decomposed_hamza = "ا\u0654\u064eن\u0651\u064e"

    assert _project_diacritics_onto_source("أن", decomposed_hamza) == "أَنَّ"
    assert _project_diacritics_onto_source("ان", decomposed_hamza) is None


@pytest.mark.parametrize(
    ("source", "proposed"),
    [
        ("إننى هنا في بيتى", "إِنَّنِي هُنَا فِي بَيْتِي"),
        ("هذا", "ه\u0670َذَا"),
        ("كل ما", "كُلَّمَا"),
        ("عبد الله", "عَبْدُاللهِ"),
        ("سنة ١٩٧٣", "سَنَةُ 1973"),
        ("سنة ١٩٧٣", "سَنَةُ ١٩٧٤"),
        ("Momo هنا", "MOMO هُنَا"),
        ("مدد", "مَدَّ"),
        ("كتب", "\u064eكَتَبَ"),
        ("كتب", "ك\u064e\u064eتَبَ"),
        ("كتب", "ك\u064e\u064fتَبَ"),
        ("كتب", "ك\u0651\u0652تَبَ"),
    ],
)
def test_harakat_projection_rejects_identity_and_mark_mutations(
    source,
    proposed,
):
    assert _project_diacritics_onto_source(source, proposed) is None


def test_harakat_projection_preserves_numeric_separator_identity():
    assert _project_diacritics_onto_source(
        "بلغ ١،٥ درجة",
        "بَلَغَ ١.٥ دَرَجَةً",
    ) is None
    assert _project_diacritics_onto_source(
        "بلغ ١،٢ ٣،٤ درجة",
        "بَلَغَ ١،٢،٣ ٤ دَرَجَةً",
    ) is None


def test_harakat_projection_merges_compatible_existing_marks():
    assert _project_diacritics_onto_source("كَتب", "كَتَبَ") == "كَتَبَ"


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
    future = _sentence(db_session, 30, lemma_ids=[2])
    urgent = _sentence(db_session, 10, lemma_ids=[1])
    acquiring = _sentence(db_session, 20, lemma_ids=[3])
    other = _sentence(db_session, 40, kind="other_book", lemma_ids=[1])
    future.arabic_text = "بَيْتٌ"
    urgent.arabic_text = "كِتَابٌ"
    acquiring.arabic_text = "قَلَمٌ"
    other.arabic_text = "كِتَابٌ"
    db_session.commit()

    plan = plan_corpus_enrichment(
        db_session,
        kind="momo_book",
        sentence_ids=[10, 20, 30, 40],
        limit=10,
        now=NOW,
    )

    assert [candidate.sentence_id for candidate in plan] == [10, 30, 20]


def test_legacy_claim_recovery_requires_exact_ids_and_is_limit_bounded(
    db_session,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    first = _sentence(
        db_session,
        1,
        lemma_ids=[1],
        verification=CORPUS_CLAIM_SENTINEL,
    )
    second = _sentence(
        db_session,
        2,
        lemma_ids=[1],
        verification=CORPUS_CLAIM_SENTINEL,
    )
    generic = _sentence(
        db_session,
        3,
        kind="other_book",
        lemma_ids=[1],
        verification=CORPUS_CLAIM_SENTINEL,
    )
    db_session.commit()

    with pytest.raises(ValueError, match="explicit sentence IDs"):
        recover_scoped_legacy_claims(
            db_session,
            CorpusScope.build(kind="momo_book"),
            limit=1,
        )

    recovered = recover_scoped_legacy_claims(
        db_session,
        CorpusScope.build(
            kind="momo_book",
            sentence_ids=[first.id, second.id, generic.id],
        ),
        limit=1,
    )
    db_session.refresh(first)
    db_session.refresh(second)
    db_session.refresh(generic)

    assert recovered == [1]
    assert first.mappings_verified_at is None
    assert second.mappings_verified_at == CORPUS_CLAIM_SENTINEL
    assert generic.mappings_verified_at == CORPUS_CLAIM_SENTINEL


def test_broad_enrichment_scope_never_recovers_a_claim_sentinel(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    current_claim = _sentence(
        db_session,
        4,
        lemma_ids=[1],
        verification=CORPUS_CLAIM_SENTINEL,
    )
    db_session.commit()

    result = enrich_corpus_sentences(
        db_session,
        kind="momo_book",
        limit=1,
        activate_limit=0,
        active_ceiling=1950,
        now=NOW,
        write_activity=False,
    )

    db_session.refresh(current_claim)
    assert result.recovered_legacy_claim_ids == []
    assert result.selected_ids == []
    assert current_claim.mappings_verified_at == CORPUS_CLAIM_SENTINEL


def test_exact_legacy_claim_dry_run_matches_bounded_live_selection(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _lemma(db_session, 2, "بيت", "house")
    _knowledge(db_session, 1, state="known", due=NOW + timedelta(days=2))
    _knowledge(db_session, 2, state="known", due=NOW - timedelta(days=2))
    future = _sentence(
        db_session,
        301,
        lemma_ids=[1],
        verification=CORPUS_CLAIM_SENTINEL,
    )
    urgent = _sentence(
        db_session,
        302,
        lemma_ids=[2],
        verification=CORPUS_CLAIM_SENTINEL,
    )
    future.arabic_text = "كِتَابٌ"
    urgent.arabic_text = "بَيْتٌ"
    db_session.commit()
    activity_before = db_session.query(ActivityLog).count()

    def prospective_mapper(*, tokens, **_kwargs):
        if "بَيْتٌ" in tokens:
            return _mappings(2)
        return _mappings(1)

    with (
        patch(
            "app.services.sentence_validator.build_comprehensive_lemma_lookup",
            return_value={},
        ),
        patch(
            "app.services.sentence_validator.map_tokens_to_lemmas",
            side_effect=prospective_mapper,
        ),
        patch(
            "app.services.sentence_validator.detect_proper_names",
            return_value=set(),
        ),
    ):
        dry_plan = plan_corpus_enrichment_report(
            db_session,
            sentence_ids=[future.id, urgent.id],
            limit=1,
            include_legacy_claims=True,
            now=NOW,
        )

    db_session.refresh(future)
    db_session.refresh(urgent)
    assert [candidate.sentence_id for candidate in dry_plan.candidates] == [
        urgent.id
    ]
    assert future.mappings_verified_at == CORPUS_CLAIM_SENTINEL
    assert urgent.mappings_verified_at == CORPUS_CLAIM_SENTINEL
    assert db_session.query(ActivityLog).count() == activity_before

    with (
        patch(
            "app.services.sentence_validator.build_comprehensive_lemma_lookup",
            return_value={},
        ),
        patch(
            "app.services.sentence_validator.map_tokens_to_lemmas",
            side_effect=prospective_mapper,
        ),
        patch(
            "app.services.sentence_validator.detect_proper_names",
            return_value=set(),
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=True,
                    translation_correct=True,
                    reason="good",
                )
            ],
        ),
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
            return_value=[{"disambiguation": [], "issues": []}],
        ),
    ):
        live = enrich_corpus_sentences(
            db_session,
            sentence_ids=[future.id, urgent.id],
            limit=1,
            activate_limit=0,
            active_ceiling=1950,
            now=NOW,
            write_activity=False,
        )

    db_session.refresh(future)
    db_session.refresh(urgent)
    assert live.preflight["candidates"][0]["sentence_id"] == urgent.id
    assert live.recovered_legacy_claim_ids == [urgent.id]
    assert live.selected_ids == [urgent.id]
    assert live.prepared_ids == [urgent.id]
    assert future.mappings_verified_at == CORPUS_CLAIM_SENTINEL
    assert _as_utc(urgent.mappings_verified_at) == NOW


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

    def verify_clean(batch, lemma_map, **_kwargs):
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
    quality_inputs: list[dict] = []

    def reject(inputs):
        quality_inputs.extend(inputs)
        return [
            SentenceReviewResult(
                natural=False,
                translation_correct=True,
                reason="fragment",
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
            return_value=_mappings(1),
        ),
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
            return_value=[{"disambiguation": [], "issues": []}],
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            side_effect=reject,
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
    assert (
        _as_utc(sentence.mappings_verified_at)
        == CORPUS_QUALITY_REJECTED_SENTINEL.replace(tzinfo=timezone.utc)
    )
    assert _as_utc(sentence.quality_reviewed_at) == NOW
    assert sentence.quality_natural is False
    assert sentence.is_active is False
    assert quality_inputs == [
        {
            "arabic": sentence.arabic_text,
            "english": sentence.english_translation,
            "review_context": MOMO_PUBLISHED_ARABIC_REVIEW_CONTEXT,
        }
    ]


def test_quality_rejection_does_not_overwrite_a_lost_claim(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 103, lemma_ids=[1])
    db_session.commit()
    concurrent_stamp = NOW - timedelta(minutes=5)

    def mutate_before_rejection(_inputs):
        db_session.query(Sentence).filter(
            Sentence.id == sentence.id
        ).update(
            {Sentence.mappings_verified_at: concurrent_stamp},
            synchronize_session=False,
        )
        db_session.commit()
        return [
            SentenceReviewResult(
                natural=False,
                translation_correct=True,
                reason="fragment",
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
            return_value=_mappings(1),
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            side_effect=mutate_before_rejection,
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
    assert result.quality_rejected_ids == []
    assert _as_utc(sentence.mappings_verified_at) == concurrent_stamp
    assert sentence.quality_reviewed_at is None
    assert result.diagnostics[-1]["reason"] == "claim_lost_quality_rejection"


def test_quality_pass_does_not_overwrite_or_deactivate_a_lost_claim(
    db_session,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 104, lemma_ids=[1])
    db_session.commit()
    concurrent_stamp = NOW - timedelta(minutes=4)
    concurrent_reviewed_at = NOW - timedelta(days=1)

    def mutate_before_pass(_inputs):
        db_session.query(Sentence).filter(
            Sentence.id == sentence.id
        ).update(
            {
                Sentence.mappings_verified_at: concurrent_stamp,
                Sentence.quality_reviewed_at: concurrent_reviewed_at,
                Sentence.quality_natural: False,
                Sentence.quality_translation_correct: False,
                Sentence.quality_reason: "other mutator",
                Sentence.is_active: True,
            },
            synchronize_session=False,
        )
        db_session.commit()
        return [
            SentenceReviewResult(
                natural=True,
                translation_correct=True,
                reason="pipeline pass",
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
            return_value=_mappings(1),
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            side_effect=mutate_before_pass,
        ),
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
        ) as verifier,
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
    assert result.prepared_ids == []
    assert _as_utc(sentence.mappings_verified_at) == concurrent_stamp
    assert _as_utc(sentence.quality_reviewed_at) == concurrent_reviewed_at
    assert sentence.quality_natural is False
    assert sentence.quality_translation_correct is False
    assert sentence.quality_reason == "other mutator"
    assert sentence.is_active is True
    assert result.diagnostics[-1]["reason"] == "claim_lost_quality_pass"
    verifier.assert_not_called()


@pytest.mark.parametrize(
    ("natural", "translation_correct"),
    [(True, True), (False, True)],
)
def test_quality_verdict_retries_when_reviewed_content_changes(
    db_session,
    natural,
    translation_correct,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 111, lemma_ids=[1])
    db_session.commit()

    def edit_content_while_quality_runs(_inputs):
        db_session.query(Sentence).filter(
            Sentence.id == sentence.id
        ).update(
            {
                Sentence.arabic_text: "نَصٌّ مُنَقَّحٌ.",
                Sentence.english_translation: "Manually revised text.",
            },
            synchronize_session=False,
        )
        db_session.commit()
        return [
            SentenceReviewResult(
                natural=natural,
                translation_correct=translation_correct,
                reason="stale verdict",
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
            return_value=_mappings(1),
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            side_effect=edit_content_while_quality_runs,
        ),
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
        ) as verifier,
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
    assert result.quality_rejected_ids == []
    assert result.prepared_ids == []
    assert sentence.arabic_text == "نَصٌّ مُنَقَّحٌ."
    assert sentence.english_translation == "Manually revised text."
    assert sentence.mappings_verified_at is None
    assert sentence.quality_reviewed_at is None
    assert sentence.quality_natural is None
    assert sentence.quality_translation_correct is None
    assert result.diagnostics[-1]["reason"] == (
        "content_changed_during_quality_review"
    )
    verifier.assert_not_called()


@pytest.mark.parametrize(
    ("natural", "translation_correct"),
    [(True, True), (False, True)],
)
@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [("kind", "other_book"), ("source", "book")],
)
def test_quality_verdict_retries_when_source_policy_provenance_changes(
    db_session,
    natural,
    translation_correct,
    changed_field,
    changed_value,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(
        db_session,
        112,
        kind="momo_book",
        lemma_ids=[1],
    )
    db_session.commit()

    def change_provenance_while_quality_runs(_inputs):
        db_session.query(Sentence).filter(
            Sentence.id == sentence.id
        ).update(
            {getattr(Sentence, changed_field): changed_value},
            synchronize_session=False,
        )
        db_session.commit()
        return [
            SentenceReviewResult(
                natural=natural,
                translation_correct=translation_correct,
                reason="verdict under stale source policy",
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
            return_value=_mappings(1),
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            side_effect=change_provenance_while_quality_runs,
        ),
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
        ) as verifier,
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
    assert result.quality_rejected_ids == []
    assert result.prepared_ids == []
    assert getattr(sentence, changed_field) == changed_value
    assert sentence.mappings_verified_at is None
    assert sentence.quality_reviewed_at is None
    assert sentence.quality_natural is None
    assert sentence.quality_translation_correct is None
    assert result.diagnostics[-1]["reason"] == (
        "content_changed_during_quality_review"
    )
    verifier.assert_not_called()


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


def test_published_context_is_scoped_to_each_momo_row_in_mixed_id_scope(
    db_session,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    momo = _sentence(
        db_session,
        120,
        kind="momo_book",
        lemma_ids=[1],
    )
    other = _sentence(
        db_session,
        121,
        kind="other_book",
        lemma_ids=[1],
    )
    same_kind_other_source = _sentence(
        db_session,
        122,
        kind="momo_book",
        lemma_ids=[1],
    )
    same_kind_other_source.source = "book"
    momo.arabic_text = "مُومُو هُنَا."
    momo.english_translation = "Momo is here."
    other.arabic_text = "الْكِتَابُ هُنَا."
    other.english_translation = "The book is here."
    same_kind_other_source.arabic_text = "هَذَا بَيْتٌ."
    same_kind_other_source.english_translation = "This is a house."
    db_session.commit()
    quality_batches: list[list[dict]] = []

    def capture_incomplete(inputs):
        quality_batches.append([dict(item) for item in inputs])
        return [
            SentenceReviewResult(
                natural=False,
                translation_correct=False,
                reason="provider unavailable",
                review_completed=False,
            )
            for _ in inputs
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
            return_value=_mappings(1),
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            side_effect=capture_incomplete,
        ),
    ):
        result = enrich_corpus_sentences(
            db_session,
            sentence_ids=[
                momo.id,
                other.id,
                same_kind_other_source.id,
            ],
            limit=3,
            activate_limit=0,
            active_ceiling=1950,
            now=NOW,
            write_activity=False,
        )

    assert result.retry_ids == [
        momo.id,
        other.id,
        same_kind_other_source.id,
    ]
    assert quality_batches == [
        [
            {
                "arabic": "مُومُو هُنَا.",
                "english": "Momo is here.",
                "review_context": (
                    MOMO_PUBLISHED_ARABIC_REVIEW_CONTEXT
                ),
            },
            {
                "arabic": "الْكِتَابُ هُنَا.",
                "english": "The book is here.",
            },
            {
                "arabic": "هَذَا بَيْتٌ.",
                "english": "This is a house.",
            },
        ],
    ]


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
            return_value=_mappings(1),
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
    # The local planning preflight runs once, but invalid enrichment output
    # stops the post-enrichment map/verifier pipeline.
    assert mapper.call_count == 1


def test_translation_only_enrichment_preserves_arabic_and_transliteration(
    db_session,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 106, lemma_ids=[1])
    original_arabic = sentence.arabic_text
    sentence.english_translation = None
    sentence.transliteration = "trusted-transliteration"
    db_session.commit()
    quality_inputs: list[dict] = []

    def quality_pass(inputs):
        quality_inputs.extend(inputs)
        return [
            SentenceReviewResult(
                natural=True,
                translation_correct=True,
                reason="good",
            )
        ]

    with (
        patch(
            "app.services.corpus_enrichment.generate_corpus_enrichment_batch",
            return_value={
                sentence.id: {
                    # This echo is deliberately unvocalized. It was not
                    # requested, so it must neither invalidate nor replace the
                    # already-good Arabic.
                    "diacritized": "إن الكتاب جديد.",
                    "translation": "The book is new.",
                }
            },
        ),
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
            side_effect=quality_pass,
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
    assert result.prepared_ids == [sentence.id]
    assert sentence.arabic_text == original_arabic
    assert sentence.transliteration == "trusted-transliteration"
    assert sentence.english_translation == "The book is new."
    assert quality_inputs == [
        {
            "arabic": original_arabic,
            "english": "The book is new.",
            "review_context": (
                MOMO_PUBLISHED_ARABIC_REVIEW_CONTEXT
            ),
        }
    ]


def test_diacritics_only_enrichment_preserves_existing_translation(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 107, lemma_ids=[1])
    sentence.arabic_text = "كتب الولد ."
    sentence.english_translation = "Trusted translation."
    sentence.transliteration = "stale-transliteration"
    db_session.commit()
    quality_inputs: list[dict] = []

    def quality_pass(inputs):
        quality_inputs.extend(inputs)
        return [
            SentenceReviewResult(
                natural=True,
                translation_correct=True,
                reason="good",
            )
        ]

    with (
        patch(
            "app.services.corpus_enrichment.generate_corpus_enrichment_batch",
            return_value={
                sentence.id: {
                    # Provider compacts the full stop. The projection must add
                    # only harakat and retain the source's exact layout.
                    "diacritized": "كَتَبَ الْوَلَدُ.",
                    # The provider volunteered a translation despite the
                    # existing field. It must be ignored.
                    "translation": "Untrusted replacement.",
                }
            },
        ),
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
            side_effect=quality_pass,
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
    assert result.prepared_ids == [sentence.id]
    assert sentence.arabic_text == "كَتَبَ الْوَلَدُ ."
    assert sentence.transliteration != "stale-transliteration"
    assert sentence.english_translation == "Trusted translation."
    assert quality_inputs == [
        {
            "arabic": "كَتَبَ الْوَلَدُ .",
            "english": "Trusted translation.",
            "review_context": (
                MOMO_PUBLISHED_ARABIC_REVIEW_CONTEXT
            ),
        }
    ]


def test_phase1_never_overwrites_concurrently_filled_missing_fields(
    db_session,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 110, lemma_ids=[1])
    sentence.english_translation = None
    sentence.transliteration = None
    db_session.commit()

    def fill_fields_while_provider_runs(_batch):
        db_session.query(Sentence).filter(
            Sentence.id == sentence.id
        ).update(
            {
                Sentence.english_translation: "Manually curated translation.",
                Sentence.transliteration: "manual-transliteration",
                Sentence.is_active: True,
            },
            synchronize_session=False,
        )
        db_session.commit()
        return {
            sentence.id: {
                "diacritized": "",
                "translation": "Provider replacement.",
            }
        }

    with (
        patch(
            "app.services.corpus_enrichment.generate_corpus_enrichment_batch",
            side_effect=fill_fields_while_provider_runs,
        ),
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
            "app.services.llm.review_sentences_quality",
        ) as quality,
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
        ) as verifier,
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
    assert result.translated_ids == []
    assert result.prepared_ids == []
    assert sentence.arabic_text == "إِنَّ الْكِتَابَ جَدِيدٌ."
    assert sentence.english_translation == "Manually curated translation."
    assert sentence.transliteration == "manual-transliteration"
    assert sentence.mappings_verified_at is None
    assert sentence.is_active is False
    assert sentence.quality_reviewed_at is None
    quality.assert_not_called()
    verifier.assert_not_called()


def test_translation_only_enrichment_rejects_concurrent_arabic_edit(
    db_session,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 111, lemma_ids=[1])
    sentence.english_translation = None
    sentence.transliteration = "stale-transliteration"
    db_session.commit()
    revised_arabic = "هٰذَا نَصٌّ مُحَرَّرٌ."

    def edit_arabic_while_provider_runs(_batch):
        db_session.query(Sentence).filter(
            Sentence.id == sentence.id
        ).update(
            {Sentence.arabic_text: revised_arabic},
            synchronize_session=False,
        )
        db_session.commit()
        return {
            sentence.id: {
                "diacritized": "",
                "translation": "Translation of the old Arabic.",
            }
        }

    with (
        patch(
            "app.services.corpus_enrichment.generate_corpus_enrichment_batch",
            side_effect=edit_arabic_while_provider_runs,
        ),
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
            "app.services.llm.review_sentences_quality",
        ) as quality,
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
        ) as verifier,
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
    assert result.translated_ids == []
    assert result.prepared_ids == []
    assert sentence.arabic_text == revised_arabic
    assert sentence.english_translation is None
    assert sentence.transliteration == (
        transliterate_arabic(revised_arabic) or ""
    )
    assert sentence.mappings_verified_at is None
    assert sentence.is_active is False
    quality.assert_not_called()
    verifier.assert_not_called()


def test_phase1_and_retry_do_not_overwrite_lost_claims(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    enriched_row = _sentence(db_session, 106, lemma_ids=[1])
    retry_row = _sentence(db_session, 107, lemma_ids=[1])
    for sentence in (enriched_row, retry_row):
        sentence.arabic_text = "كتب الولد"
        sentence.english_translation = None
    db_session.commit()
    concurrent_stamp = NOW - timedelta(minutes=3)

    def mutate_during_enrichment(_batch):
        for sentence, suffix in (
            (enriched_row, "one"),
            (retry_row, "two"),
        ):
            db_session.query(Sentence).filter(
                Sentence.id == sentence.id
            ).update(
                {
                    Sentence.arabic_text: f"نَصٌّ خَارِجِيٌّ {suffix}",
                    Sentence.english_translation: f"external {suffix}",
                    Sentence.transliteration: f"external-{suffix}",
                    Sentence.mappings_verified_at: concurrent_stamp,
                    Sentence.is_active: True,
                },
                synchronize_session=False,
            )
        db_session.commit()
        # The second row deliberately has no provider result, exercising the
        # retry-release CAS as well as the first row's scalar-write CAS.
        return {
            enriched_row.id: {
                "diacritized": "كَتَبَ الْوَلَدُ",
                "translation": "The boy wrote.",
            }
        }

    with (
        patch(
            "app.services.corpus_enrichment.generate_corpus_enrichment_batch",
            side_effect=mutate_during_enrichment,
        ),
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
            "app.services.llm.review_sentences_quality",
        ) as quality,
    ):
        result = enrich_corpus_sentences(
            db_session,
            kind="momo_book",
            limit=2,
            activate_limit=0,
            active_ceiling=1950,
            enrichment_batch_size=2,
            now=NOW,
            write_activity=False,
        )

    assert result.retry_ids == []
    assert result.translated_ids == []
    diagnostic_by_id = {
        row["sentence_id"]: row["reason"] for row in result.diagnostics
    }
    assert diagnostic_by_id == {
        enriched_row.id: "claim_lost_phase1_enrichment",
        retry_row.id: (
            "claim_lost_retry_enrichment_unavailable_or_invalid"
        ),
    }
    for sentence, suffix in (
        (enriched_row, "one"),
        (retry_row, "two"),
    ):
        db_session.refresh(sentence)
        assert sentence.arabic_text == f"نَصٌّ خَارِجِيٌّ {suffix}"
        assert sentence.english_translation == f"external {suffix}"
        assert sentence.transliteration == f"external-{suffix}"
        assert _as_utc(sentence.mappings_verified_at) == concurrent_stamp
        assert sentence.is_active is True
    quality.assert_not_called()


def test_final_mapping_write_does_not_overwrite_a_lost_claim(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 104, lemma_ids=[1])
    db_session.commit()

    def mutate_during_verification(_batch, _lemma_map, **_kwargs):
        db_session.query(Sentence).filter(
            Sentence.id == sentence.id
        ).update(
            {Sentence.mappings_verified_at: CORPUS_BLOCKED_SENTINEL},
            synchronize_session=False,
        )
        db_session.commit()
        return [{"disambiguation": [], "issues": []}]

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
            side_effect=mutate_during_verification,
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=True,
                    translation_correct=True,
                    reason="good",
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
    words = (
        db_session.query(SentenceWord)
        .filter(SentenceWord.sentence_id == sentence.id)
        .order_by(SentenceWord.position)
        .all()
    )
    assert result.prepared_ids == []
    assert sentence.mappings_verified_at == CORPUS_BLOCKED_SENTINEL
    assert [word.surface_form for word in words] == ["word-0"]
    assert result.diagnostics[-1]["reason"] == "claim_lost_final_mapping_write"


def test_mapping_verdict_never_replaces_words_for_concurrently_edited_text(
    db_session,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 112, lemma_ids=[1])
    sentence.transliteration = "stale transliteration"
    db_session.commit()

    def edit_text_during_verification(_batch, _lemma_map, **_kwargs):
        db_session.query(Sentence).filter(
            Sentence.id == sentence.id
        ).update(
            {
                Sentence.arabic_text: "نَصٌّ يَدَوِيٌّ جَدِيدٌ.",
                Sentence.english_translation: "New manual text.",
            },
            synchronize_session=False,
        )
        db_session.commit()
        return [{"disambiguation": [], "issues": []}]

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
            side_effect=edit_text_during_verification,
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=True,
                    translation_correct=True,
                    reason="good",
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
    words = (
        db_session.query(SentenceWord)
        .filter(SentenceWord.sentence_id == sentence.id)
        .order_by(SentenceWord.position)
        .all()
    )
    assert result.prepared_ids == []
    assert result.retry_ids == [sentence.id]
    assert sentence.arabic_text == "نَصٌّ يَدَوِيٌّ جَدِيدٌ."
    assert sentence.english_translation == "New manual text."
    assert sentence.transliteration == (
        transliterate_arabic("نَصٌّ يَدَوِيٌّ جَدِيدٌ.") or ""
    )
    assert sentence.mappings_verified_at is None
    assert sentence.quality_reviewed_at is None
    assert [word.surface_form for word in words] == ["word-0"]
    assert result.diagnostics[-1]["reason"] == (
        "content_changed_during_mapping_verification"
    )


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [("kind", "other_book"), ("source", "book")],
)
@pytest.mark.parametrize(
    "verifier_outcome",
    ["clean", "unavailable", "mapping_block"],
)
def test_post_quality_provenance_change_retries_before_mapping_disposition(
    db_session,
    changed_field,
    changed_value,
    verifier_outcome,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 123, lemma_ids=[1])
    db_session.commit()

    def change_provenance_during_verification(
        _batch,
        _lemma_map,
        **_kwargs,
    ):
        db_session.query(Sentence).filter(
            Sentence.id == sentence.id
        ).update(
            {getattr(Sentence, changed_field): changed_value},
            synchronize_session=False,
        )
        db_session.commit()
        if verifier_outcome == "unavailable":
            return None
        if verifier_outcome == "mapping_block":
            return [
                {
                    "disambiguation": [],
                    "issues": [
                        {
                            "position": 0,
                            "correct_lemma_ar": "مَفْقُودٌ",
                            "correct_gloss": "missing",
                            "correct_pos": "noun",
                        }
                    ],
                }
            ]
        return [{"disambiguation": [], "issues": []}]

    def fail_only_nonempty_corrections(issues, *_args, **_kwargs):
        return [0] if issues else []

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
            side_effect=change_provenance_during_verification,
        ),
        patch(
            "app.services.sentence_validator.apply_corrections",
            side_effect=fail_only_nonempty_corrections,
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=True,
                    translation_correct=True,
                    reason="good under original provenance",
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
    word = (
        db_session.query(SentenceWord)
        .filter(SentenceWord.sentence_id == sentence.id)
        .one()
    )
    expected_reason = (
        "content_changed_during_mapping"
        if verifier_outcome == "mapping_block"
        else "content_changed_during_mapping_verification"
    )
    assert result.retry_ids == [sentence.id]
    assert result.prepared_ids == []
    assert result.mapping_blocked_ids == []
    assert result.target_rejected_ids == []
    assert getattr(sentence, changed_field) == changed_value
    assert sentence.mappings_verified_at is None
    assert sentence.quality_reviewed_at is None
    assert sentence.quality_natural is None
    assert sentence.quality_translation_correct is None
    assert sentence.quality_reason is None
    assert sentence.is_active is False
    assert word.surface_form == "word-0"
    assert result.diagnostics[-1]["reason"] == expected_reason
    assert result.failure_reasons[expected_reason] == 1


def test_mapping_verdict_preserves_concurrent_sentence_word_repair(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 113, lemma_ids=[1])
    db_session.commit()

    def repair_word_during_verification(_batch, _lemma_map, **_kwargs):
        db_session.query(SentenceWord).filter(
            SentenceWord.sentence_id == sentence.id,
            SentenceWord.position == 0,
        ).update(
            {SentenceWord.surface_form: "manual-word-repair"},
            synchronize_session=False,
        )
        db_session.commit()
        return [{"disambiguation": [], "issues": []}]

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
            side_effect=repair_word_during_verification,
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=True,
                    translation_correct=True,
                    reason="good",
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
    word = (
        db_session.query(SentenceWord)
        .filter(SentenceWord.sentence_id == sentence.id)
        .one()
    )
    assert result.prepared_ids == []
    assert result.retry_ids == [sentence.id]
    assert sentence.mappings_verified_at is None
    assert _as_utc(sentence.quality_reviewed_at) == NOW
    assert sentence.quality_natural is True
    assert word.surface_form == "manual-word-repair"
    assert result.failure_reasons[
        "mapping_state_changed_during_verification"
    ] == 1


def test_stale_terminal_mapping_block_retries_after_concurrent_word_repair(
    db_session,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 114, lemma_ids=[1])
    db_session.commit()

    def repair_word_during_verification(_batch, _lemma_map, **_kwargs):
        db_session.query(SentenceWord).filter(
            SentenceWord.sentence_id == sentence.id,
            SentenceWord.position == 0,
        ).update(
            {SentenceWord.surface_form: "manual-terminal-repair"},
            synchronize_session=False,
        )
        db_session.commit()
        return [
            {
                "disambiguation": [],
                "issues": [
                    {
                        "position": 0,
                        "correct_lemma_ar": "مَفْقُودٌ",
                        "correct_gloss": "missing",
                        "correct_pos": "noun",
                    }
                ],
            }
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
            return_value=_mappings(1),
        ),
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
            side_effect=repair_word_during_verification,
        ),
        patch(
            "app.services.sentence_validator.apply_corrections",
            return_value=[0],
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=True,
                    translation_correct=True,
                    reason="good",
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
    word = (
        db_session.query(SentenceWord)
        .filter(SentenceWord.sentence_id == sentence.id)
        .one()
    )
    assert result.mapping_blocked_ids == []
    assert result.retry_ids == [sentence.id]
    assert sentence.mappings_verified_at is None
    assert _as_utc(sentence.quality_reviewed_at) == NOW
    assert word.surface_form == "manual-terminal-repair"
    assert result.failure_reasons[
        "mapping_state_changed_during_verification"
    ] == 1


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

    def correct_to_function(_issues, mappings, *_args, **_kwargs):
        mappings[0].lemma_id = 2
        return []

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
            "app.services.sentence_validator.apply_corrections",
            side_effect=correct_to_function,
        ) as corrector,
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
            return_value=[{"disambiguation": [], "issues": []}],
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=True,
                    translation_correct=True,
                    reason="good",
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
    assert result.target_rejected_ids == [sentence.id]
    assert result.mapping_blocked_ids == [sentence.id]
    assert _as_utc(sentence.mappings_verified_at) == (
        CORPUS_BLOCKED_SENTINEL.replace(tzinfo=timezone.utc)
    )
    assert _as_utc(sentence.quality_reviewed_at) == NOW
    assert sentence.quality_natural is True
    assert sentence.quality_translation_correct is True
    assert sentence.is_active is False
    assert corrector.call_args.kwargs["require_gated_lemmas"] is True


@pytest.mark.parametrize("retry_blocked", [False, True])
def test_target_suspension_during_external_phase_releases_for_retry(
    db_session,
    retry_blocked,
):
    _lemma(db_session, 1, "كتاب", "book")
    knowledge = _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(
        db_session,
        108,
        lemma_ids=[1],
        verification=CORPUS_BLOCKED_SENTINEL if retry_blocked else None,
        quality=(True, True) if retry_blocked else None,
    )
    db_session.commit()

    def suspend_during_verification(_batch, _lemma_map, **_kwargs):
        db_session.query(UserLemmaKnowledge).filter(
            UserLemmaKnowledge.lemma_id == knowledge.lemma_id
        ).update(
            {UserLemmaKnowledge.knowledge_state: "suspended"},
            synchronize_session=False,
        )
        db_session.commit()
        return [{"disambiguation": [], "issues": []}]

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
            side_effect=suspend_during_verification,
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=True,
                    translation_correct=True,
                    reason="good",
                )
            ],
        ),
    ):
        result = enrich_corpus_sentences(
            db_session,
            kind="momo_book",
            sentence_ids=[sentence.id] if retry_blocked else None,
            limit=1,
            activate_limit=0,
            active_ceiling=1950,
            retry_blocked=retry_blocked,
            now=NOW,
            write_activity=False,
        )

    db_session.refresh(sentence)
    assert result.retry_ids == [sentence.id]
    assert result.mapping_blocked_ids == []
    assert result.target_rejected_ids == []
    assert result.diagnostics[-1] == {
        "sentence_id": sentence.id,
        "disposition": "retry",
        "reason": "target_content_suspended",
        "positions": [],
    }
    if retry_blocked:
        assert sentence.mappings_verified_at == CORPUS_BLOCKED_SENTINEL
    else:
        assert sentence.mappings_verified_at is None


def test_preparation_uses_only_gated_lookup_and_corrections(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 109, lemma_ids=[1])
    db_session.commit()

    with (
        patch(
            "app.services.sentence_validator.build_comprehensive_lemma_lookup",
            return_value={},
        ) as build_lookup,
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
            "app.services.sentence_validator.apply_corrections",
            return_value=[],
        ) as correct,
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=True,
                    translation_correct=True,
                    reason="good",
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

    assert result.prepared_ids == [sentence.id]
    assert build_lookup.call_count == 2
    assert all(
        call.kwargs == {"require_gated": True}
        for call in build_lookup.call_args_list
    )
    assert correct.call_args.kwargs["require_gated_lemmas"] is True


def test_zero_activation_limit_skips_activation_planner(db_session):
    with patch(
        "app.services.corpus_enrichment.activate_prepared_corpus_sentences"
    ) as activate:
        result = enrich_corpus_sentences(
            db_session,
            kind="momo_book",
            limit=0,
            activate_limit=0,
            active_ceiling=1950,
            now=NOW,
            write_activity=False,
        )

    activate.assert_not_called()
    assert result.active_before == 0
    assert result.active_ceiling == 1950
    assert result.activation_capacity == 0


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


def test_preflight_skips_known_unmapped_without_writes_or_external_calls(
    db_session,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 112, lemma_ids=[1])
    db_session.commit()
    unmapped = TokenMapping(
        position=0,
        surface_form="مَفْقُودٌ",
        lemma_id=None,
        is_target=False,
        is_function_word=False,
    )

    with (
        patch(
            "app.services.sentence_validator.map_tokens_to_lemmas",
            return_value=[unmapped],
        ),
        patch(
            "app.services.corpus_enrichment.generate_corpus_enrichment_batch",
        ) as enrich,
        patch(
            "app.services.llm.review_sentences_quality",
        ) as quality,
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
        ) as verify,
        patch(
            "app.services.corpus_enrichment.log_activity",
        ) as activity,
    ):
        result = enrich_corpus_sentences(
            db_session,
            kind="momo_book",
            limit=1,
            activate_limit=0,
            active_ceiling=1950,
            now=NOW,
            write_activity=True,
        )

    db_session.refresh(sentence)
    assert result.selected_ids == []
    assert result.preflight_skipped_ids == [sentence.id]
    assert result.mapping_blocked_ids == []
    assert result.preflight["skipped_unmapped_ids"] == [sentence.id]
    assert sentence.mappings_verified_at is None
    assert sentence.quality_reviewed_at is None
    assert sentence.quality_natural is None
    assert sentence.quality_translation_correct is None
    assert sentence.quality_reason is None
    assert result.diagnostics == []
    enrich.assert_not_called()
    quality.assert_not_called()
    verify.assert_not_called()
    logged = activity.call_args.kwargs["detail"]
    assert logged["preflight_skipped_ids"] == [sentence.id]


def test_mapping_correction_block_preserves_early_qa_and_position_diagnostic(
    db_session,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 113, lemma_ids=[1])
    db_session.commit()
    call_order: list[str] = []

    def quality_pass(_inputs):
        call_order.append("quality")
        assert not db_session.new
        assert not db_session.dirty
        assert not db_session.deleted
        return [
            SentenceReviewResult(
                natural=True,
                translation_correct=True,
                reason="good Arabic and translation",
            )
        ]

    def verify_missing(_batch, _lemma_map, **_kwargs):
        call_order.append("verify")
        assert not db_session.new
        assert not db_session.dirty
        assert not db_session.deleted
        return [
            {
                "disambiguation": [],
                "issues": [
                    {
                        "position": 0,
                        "correct_lemma_ar": "مفقود",
                        "correct_gloss": "missing",
                        "correct_pos": "adjective",
                        "explanation": "wrong sense",
                    }
                ],
            }
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
            side_effect=lambda **_kwargs: _mappings(1),
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            side_effect=quality_pass,
        ),
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
            side_effect=verify_missing,
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
    assert call_order == ["quality", "verify"]
    assert result.mapping_blocked_ids == [sentence.id]
    assert sentence.mappings_verified_at == CORPUS_BLOCKED_SENTINEL
    assert _as_utc(sentence.quality_reviewed_at) == NOW
    assert sentence.quality_natural is True
    assert sentence.quality_translation_correct is True
    assert result.diagnostics[0]["positions"][0] == {
        "position": 0,
        "surface_form": "surface-0",
        "current_lemma_id": 1,
        "proposed_lemma": "مفقود",
        "proposed_gloss": "missing",
        "proposed_pos": "adjective",
    }


@pytest.mark.parametrize(
    "invalid_reason",
    [
        "contradictory_verdict",
        "undeclared_disambiguation_position",
    ],
)
def test_invalid_verifier_row_retries_without_blocking_clean_peer(
    db_session,
    invalid_reason,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    first = _sentence(db_session, 114, lemma_ids=[1])
    second = _sentence(db_session, 115, lemma_ids=[1])
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
            side_effect=lambda **_kwargs: _mappings(1),
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=True,
                    translation_correct=True,
                    reason="good",
                ),
                SentenceReviewResult(
                    natural=True,
                    translation_correct=True,
                    reason="good",
                ),
            ],
        ),
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
            return_value=[
                {
                    "disambiguation": [],
                    "issues": [],
                    "invalid_reason": invalid_reason,
                    "invalid_positions": [0],
                },
                {"disambiguation": [], "issues": []},
            ],
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
    assert result.retry_ids == [first.id]
    assert result.prepared_ids == [second.id]
    assert result.mapping_blocked_ids == []
    assert first.mappings_verified_at is None
    assert first.quality_natural is True
    assert _as_utc(second.mappings_verified_at) == NOW
    assert result.diagnostics[0]["reason"] == (
        f"mapping_verifier_{invalid_reason}"
    )


def test_prospective_preflight_skips_unmapped_and_uses_remapped_demand(
    db_session,
):
    _lemma(db_session, 1, "قديم", "old")
    _lemma(db_session, 2, "جديد", "new")
    _lemma(db_session, 3, "خامد", "inert")
    _knowledge(db_session, 1, state="known", due=NOW - timedelta(days=5))
    _knowledge(db_session, 2, state="known", due=NOW - timedelta(days=1))
    blocked = _sentence(db_session, 116, lemma_ids=[1])
    remapped = _sentence(db_session, 117, lemma_ids=[3])
    blocked.arabic_text = "مَفْقُودٌ"
    remapped.arabic_text = "جَدِيدٌ"
    db_session.commit()

    def prospective_mapper(*, tokens, **_kwargs):
        if "مَفْقُودٌ" in tokens:
            return [
                TokenMapping(0, "مَفْقُودٌ", None, False, False)
            ]
        return [TokenMapping(0, "جَدِيدٌ", 2, False, False)]

    with (
        patch(
            "app.services.sentence_validator.map_tokens_to_lemmas",
            side_effect=prospective_mapper,
        ),
        patch(
            "app.services.sentence_validator.detect_proper_names",
            return_value=set(),
        ),
    ):
        report = plan_corpus_enrichment_report(
            db_session,
            kind="momo_book",
            limit=1,
            now=NOW,
        )

    assert report.skipped_unmapped_ids == [blocked.id]
    assert [candidate.sentence_id for candidate in report.candidates] == [
        remapped.id
    ]
    assert report.candidates[0].demand.content_lemma_ids == frozenset({2})
    detail = report.detail()
    assert detail["risk_metrics"]["guaranteed_incomplete_rows"] == 1
    assert detail["risk_metrics"]["stored_mapping_changed_rows"] == 2


def test_preflight_overfetch_never_mutates_more_than_selected_limit(
    db_session,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    skipped = [
        _sentence(db_session, sentence_id, lemma_ids=[1])
        for sentence_id in (121, 122, 123)
    ]
    selected = _sentence(db_session, 124, lemma_ids=[1])
    for sentence in skipped:
        sentence.arabic_text = "مَفْقُودٌ"
    selected.arabic_text = "كِتَابٌ"
    db_session.commit()

    def prospective_mapper(*, tokens, **_kwargs):
        if "مَفْقُودٌ" in tokens:
            return [TokenMapping(0, "مَفْقُودٌ", None, False, False)]
        return _mappings(1)

    with (
        patch(
            "app.services.sentence_validator.build_comprehensive_lemma_lookup",
            return_value={},
        ),
        patch(
            "app.services.sentence_validator.map_tokens_to_lemmas",
            side_effect=prospective_mapper,
        ),
        patch(
            "app.services.sentence_validator.detect_proper_names",
            return_value=set(),
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=True,
                    translation_correct=True,
                    reason="good",
                )
            ],
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

    for sentence in skipped:
        db_session.refresh(sentence)
        assert sentence.mappings_verified_at is None
        assert sentence.quality_reviewed_at is None
    db_session.refresh(selected)
    assert result.preflight_skipped_ids == [121, 122, 123]
    assert result.selected_ids == [selected.id]
    assert result.prepared_ids == [selected.id]
    assert _as_utc(selected.mappings_verified_at) == NOW


def test_preflight_cursor_reaches_valid_row_beyond_blocked_window(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    blocked = [
        _sentence(db_session, sentence_id, lemma_ids=[1])
        for sentence_id in (201, 202, 203, 204)
    ]
    valid = _sentence(db_session, 205, lemma_ids=[1])
    for sentence in blocked:
        sentence.arabic_text = "مَفْقُودٌ"
    valid.arabic_text = "كِتَابٌ"
    db_session.commit()

    def prospective_mapper(*, tokens, **_kwargs):
        if "مَفْقُودٌ" in tokens:
            return [TokenMapping(0, "مَفْقُودٌ", None, False, False)]
        return _mappings(1)

    with (
        patch(
            "app.services.sentence_validator.build_comprehensive_lemma_lookup",
            return_value={},
        ),
        patch(
            "app.services.sentence_validator.map_tokens_to_lemmas",
            side_effect=prospective_mapper,
        ),
        patch(
            "app.services.sentence_validator.detect_proper_names",
            return_value=set(),
        ),
        patch(
            "app.services.corpus_enrichment.generate_corpus_enrichment_batch",
        ) as enrich,
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=True,
                    translation_correct=True,
                    reason="good",
                )
            ],
        ) as quality,
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
            return_value=[{"disambiguation": [], "issues": []}],
        ) as verify,
    ):
        first = enrich_corpus_sentences(
            db_session,
            kind="momo_book",
            limit=1,
            activate_limit=0,
            active_ceiling=1950,
            now=NOW,
            write_activity=True,
        )
        assert first.selected_ids == []
        assert first.preflight["rows_preflighted"] == 4
        assert first.preflight["cursor_end_id"] == 204
        quality.assert_not_called()
        verify.assert_not_called()

        second = enrich_corpus_sentences(
            db_session,
            kind="momo_book",
            limit=1,
            activate_limit=0,
            active_ceiling=1950,
            now=NOW,
            write_activity=True,
        )

    db_session.refresh(valid)
    assert second.preflight["cursor_start_after_id"] == 204
    assert second.preflight["rows_preflighted"] == 4
    assert second.preflight["cursor_wrapped"] is True
    assert second.selected_ids == [valid.id]
    assert second.prepared_ids == [valid.id]
    assert _as_utc(valid.mappings_verified_at) == NOW
    enrich.assert_not_called()
    quality.assert_called_once()
    verify.assert_called_once()


def test_unresolved_proper_name_skips_without_creating_lemma(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    sentence = _sentence(db_session, 118, lemma_ids=[1])
    sentence.arabic_text = "بِيتَر"
    db_session.commit()
    lemma_count = db_session.query(Lemma).count()

    def proper_mapper(*, proper_names, **_kwargs):
        return [
            TokenMapping(
                0,
                "بِيتَر",
                None,
                False,
                False,
                is_proper_name=bool(proper_names),
            )
        ]

    with (
        patch(
            "app.services.sentence_validator.map_tokens_to_lemmas",
            side_effect=proper_mapper,
        ),
        patch(
            "app.services.sentence_validator.detect_proper_names",
            return_value={"بيتر"},
        ),
        patch(
            "app.services.llm.review_sentences_quality",
        ) as quality,
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
    assert result.preflight_skipped_ids == [sentence.id]
    assert result.mapping_blocked_ids == []
    assert sentence.mappings_verified_at is None
    assert db_session.query(Lemma).count() == lemma_count
    quality.assert_not_called()


def test_exact_blocked_retry_recovers_only_named_row_and_reprepares(
    db_session,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    retried = _sentence(
        db_session,
        119,
        lemma_ids=[1],
        verification=CORPUS_BLOCKED_SENTINEL,
        quality=(True, True),
    )
    untouched = _sentence(
        db_session,
        120,
        lemma_ids=[1],
        verification=CORPUS_BLOCKED_SENTINEL,
        quality=(True, True),
    )
    legacy_claim = _sentence(
        db_session,
        121,
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
            side_effect=lambda **_kwargs: _mappings(1),
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=True,
                    translation_correct=True,
                    reason="still good",
                )
            ],
        ),
        patch(
            "app.services.sentence_validator.batch_verify_sentences",
            return_value=[{"disambiguation": [], "issues": []}],
        ),
    ):
        result = enrich_corpus_sentences(
            db_session,
            sentence_ids=[retried.id, legacy_claim.id],
            limit=1,
            activate_limit=0,
            active_ceiling=1950,
            retry_blocked=True,
            now=NOW,
            write_activity=False,
        )

    db_session.refresh(retried)
    db_session.refresh(untouched)
    db_session.refresh(legacy_claim)
    assert result.recovered_blocked_ids == [retried.id]
    assert result.prepared_ids == [retried.id]
    assert _as_utc(retried.mappings_verified_at) == NOW
    assert untouched.mappings_verified_at == CORPUS_BLOCKED_SENTINEL
    assert legacy_claim.mappings_verified_at == CORPUS_CLAIM_SENTINEL


def test_blocked_retry_plan_excludes_named_ordinary_rows(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    ordinary = _sentence(
        db_session,
        121,
        lemma_ids=[1],
        verification=None,
    )
    blocked = _sentence(
        db_session,
        122,
        lemma_ids=[1],
        verification=CORPUS_BLOCKED_SENTINEL,
        quality=(True, True),
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
            side_effect=lambda **_kwargs: _mappings(1),
        ),
    ):
        report = plan_corpus_enrichment_report(
            db_session,
            sentence_ids=[ordinary.id, blocked.id],
            limit=1,
            include_legacy_claims=False,
            include_blocked=True,
            only_blocked=True,
            now=NOW,
        )

    assert report.rows_available == 1
    assert [candidate.sentence_id for candidate in report.candidates] == [
        blocked.id
    ]


def test_exact_blocked_retry_restores_blocker_after_transient_failure(
    db_session,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    blocked = _sentence(
        db_session,
        121,
        lemma_ids=[1],
        verification=CORPUS_BLOCKED_SENTINEL,
        quality=(True, True),
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
            side_effect=lambda **_kwargs: _mappings(1),
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[],
        ),
    ):
        result = enrich_corpus_sentences(
            db_session,
            sentence_ids=[blocked.id],
            limit=1,
            activate_limit=0,
            active_ceiling=1950,
            retry_blocked=True,
            now=NOW,
            write_activity=False,
        )

    db_session.refresh(blocked)
    assert result.recovered_blocked_ids == [blocked.id]
    assert result.retry_ids == [blocked.id]
    assert blocked.mappings_verified_at == CORPUS_BLOCKED_SENTINEL
    assert blocked.is_active is False


def test_exact_blocked_retry_restores_blocker_after_unexpected_failure(
    db_session,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW)
    blocked = _sentence(
        db_session,
        122,
        lemma_ids=[1],
        verification=CORPUS_BLOCKED_SENTINEL,
        quality=(True, True),
    )
    db_session.commit()
    mapping_calls = 0

    def fail_after_preflight(**_kwargs):
        nonlocal mapping_calls
        mapping_calls += 1
        if mapping_calls == 1:
            return _mappings(1)
        raise RuntimeError("retry mapping failed")

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
            side_effect=fail_after_preflight,
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=True,
                    translation_correct=True,
                    reason="still good",
                )
            ],
        ),
        pytest.raises(RuntimeError, match="retry mapping failed"),
    ):
        enrich_corpus_sentences(
            db_session,
            sentence_ids=[blocked.id],
            limit=1,
            activate_limit=0,
            active_ceiling=1950,
            retry_blocked=True,
            now=NOW,
            write_activity=False,
        )

    db_session.refresh(blocked)
    assert mapping_calls == 2
    assert blocked.mappings_verified_at == CORPUS_BLOCKED_SENTINEL
    assert blocked.is_active is False


def test_blocked_retry_service_requires_exact_preparation_scope(db_session):
    with pytest.raises(ValueError, match="explicit sentence IDs"):
        enrich_corpus_sentences(
            db_session,
            kind="momo_book",
            limit=1,
            activate_limit=0,
            active_ceiling=1950,
            retry_blocked=True,
            now=NOW,
            write_activity=False,
        )

    with pytest.raises(ValueError, match="nonzero preparation"):
        enrich_corpus_sentences(
            db_session,
            sentence_ids=[1],
            limit=0,
            activate_limit=0,
            active_ceiling=1950,
            retry_blocked=True,
            now=NOW,
            write_activity=False,
        )


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
    mapping_calls = 0

    def fail_after_preflight(**_kwargs):
        nonlocal mapping_calls
        mapping_calls += 1
        if mapping_calls == 1:
            return _mappings(1)
        db_session.query(Sentence).filter(
            Sentence.id == sentence.id
        ).update(
            {Sentence.is_active: True},
            synchronize_session=False,
        )
        db_session.commit()
        raise RuntimeError("boom")

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
            side_effect=fail_after_preflight,
        ),
        patch(
            "app.services.llm.review_sentences_quality",
            return_value=[
                SentenceReviewResult(
                    natural=True,
                    translation_correct=True,
                    reason="good",
                )
            ],
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        enrich_corpus_sentences(
            db_session,
            sentence_ids=[sentence.id],
            limit=1,
            activate_limit=0,
            active_ceiling=1950,
            now=NOW,
            write_activity=False,
        )

    db_session.refresh(sentence)
    assert mapping_calls == 2
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


def test_activation_rechecks_live_capacity_after_planning(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW - timedelta(hours=1))
    candidate = _sentence(
        db_session,
        203,
        lemma_ids=[1],
        verification=NOW,
        target_id=1,
        quality=(True, True),
    )
    concurrent = _sentence(
        db_session,
        204,
        kind="other_book",
        lemma_ids=[1],
    )
    db_session.commit()
    original_plan = plan_corpus_activation

    def plan_then_consume_capacity(*args, **kwargs):
        plan = original_plan(*args, **kwargs)
        assert plan.selected_ids == [candidate.id]
        db_session.query(Sentence).filter(
            Sentence.id == concurrent.id
        ).update(
            {Sentence.is_active: True},
            synchronize_session=False,
        )
        db_session.commit()
        return plan

    with patch(
        "app.services.corpus_enrichment.plan_corpus_activation",
        side_effect=plan_then_consume_capacity,
    ):
        applied = activate_prepared_corpus_sentences(
            db_session,
            scope=CorpusScope.build(kind="momo_book"),
            activate_limit=1,
            active_ceiling=1,
            now=NOW,
        )

    db_session.refresh(candidate)
    db_session.refresh(concurrent)
    assert applied.selected_ids == []
    assert applied.active_before == 1
    assert applied.capacity == 0
    assert candidate.is_active is False
    assert concurrent.is_active is True
    assert db_session.query(Sentence).filter(
        Sentence.is_active.is_(True)
    ).count() == 1


def test_activation_slot_claim_closes_post_recount_capacity_race(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW - timedelta(hours=1))
    candidate = _sentence(
        db_session,
        205,
        lemma_ids=[1],
        verification=NOW,
        target_id=1,
        quality=(True, True),
    )
    concurrent = _sentence(
        db_session,
        206,
        kind="other_book",
        lemma_ids=[1],
    )
    db_session.commit()
    from app.services.corpus_enrichment import _begin_sqlite_write_boundary

    raced = False

    def consume_capacity_before_write_boundary(*args, **kwargs):
        nonlocal raced
        if not raced:
            raced = True
            db_session.query(Sentence).filter(
                Sentence.id == concurrent.id
            ).update(
                {Sentence.is_active: True},
                synchronize_session=False,
            )
            db_session.commit()
        return _begin_sqlite_write_boundary(*args, **kwargs)

    with patch(
        "app.services.corpus_enrichment._begin_sqlite_write_boundary",
        side_effect=consume_capacity_before_write_boundary,
    ):
        applied = activate_prepared_corpus_sentences(
            db_session,
            scope=CorpusScope.build(kind="momo_book"),
            activate_limit=1,
            active_ceiling=1,
            now=NOW,
        )

    db_session.refresh(candidate)
    db_session.refresh(concurrent)
    assert applied.selected_ids == []
    assert applied.active_before == 1
    assert applied.capacity == 0
    assert candidate.is_active is False
    assert concurrent.is_active is True
    assert db_session.query(Sentence).filter(
        Sentence.is_active.is_(True)
    ).count() == 1


def test_activation_invalidates_content_edited_after_fresh_reload(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW - timedelta(hours=1))
    candidate = _sentence(
        db_session,
        207,
        lemma_ids=[1],
        verification=NOW,
        target_id=1,
        quality=(True, True),
    )
    candidate.transliteration = "stale transliteration"
    db_session.commit()
    from app.services.corpus_enrichment import _begin_sqlite_write_boundary

    edited = False
    revised_arabic = "نَصٌّ مُعَدَّلٌ."

    def edit_content_before_write_boundary(*args, **kwargs):
        nonlocal edited
        if not edited:
            edited = True
            db_session.query(Sentence).filter(
                Sentence.id == candidate.id
            ).update(
                {
                    Sentence.arabic_text: revised_arabic,
                    Sentence.english_translation: "Revised manually.",
                },
                synchronize_session=False,
            )
            db_session.commit()
        return _begin_sqlite_write_boundary(*args, **kwargs)

    with patch(
        "app.services.corpus_enrichment._begin_sqlite_write_boundary",
        side_effect=edit_content_before_write_boundary,
    ):
        applied = activate_prepared_corpus_sentences(
            db_session,
            scope=CorpusScope.build(kind="momo_book"),
            activate_limit=1,
            active_ceiling=1,
            now=NOW,
        )

    db_session.refresh(candidate)
    assert applied.selected_ids == []
    assert candidate.is_active is False
    assert candidate.mappings_verified_at is None
    assert candidate.quality_reviewed_at is None
    assert candidate.quality_natural is None
    assert candidate.quality_translation_correct is None
    assert candidate.transliteration == (
        transliterate_arabic(revised_arabic) or ""
    )


@pytest.mark.parametrize(
    ("refreshed_artifact", "sentence_id"),
    [("quality", 209), ("mapping", 210)],
)
def test_activation_invalidates_content_artifacts_independently(
    db_session,
    refreshed_artifact,
    sentence_id,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW - timedelta(hours=1))
    candidate = _sentence(
        db_session,
        sentence_id,
        lemma_ids=[1],
        verification=NOW,
        target_id=1,
        quality=(True, True),
    )
    db_session.commit()
    from app.services.corpus_enrichment import _begin_sqlite_write_boundary

    refreshed_stamp = NOW + timedelta(minutes=1)
    revised_arabic = "هٰذَا نَصٌّ مُحَرَّرٌ."
    edited = False

    def edit_content_and_refresh_one_artifact(*args, **kwargs):
        nonlocal edited
        if not edited:
            edited = True
            values = {Sentence.arabic_text: revised_arabic}
            if refreshed_artifact == "quality":
                values.update(
                    {
                        Sentence.quality_reviewed_at: refreshed_stamp,
                        Sentence.quality_natural: True,
                        Sentence.quality_translation_correct: True,
                        Sentence.quality_reason: "reviewed edited content",
                    }
                )
            else:
                values[Sentence.mappings_verified_at] = refreshed_stamp
            db_session.query(Sentence).filter(
                Sentence.id == candidate.id
            ).update(values, synchronize_session=False)
            db_session.commit()
        return _begin_sqlite_write_boundary(*args, **kwargs)

    with patch(
        "app.services.corpus_enrichment._begin_sqlite_write_boundary",
        side_effect=edit_content_and_refresh_one_artifact,
    ):
        first = activate_prepared_corpus_sentences(
            db_session,
            scope=CorpusScope.build(kind="momo_book"),
            activate_limit=1,
            active_ceiling=1,
            now=NOW,
        )

    db_session.refresh(candidate)
    assert first.selected_ids == []
    assert candidate.is_active is False
    assert candidate.arabic_text == revised_arabic
    assert candidate.transliteration == (
        transliterate_arabic(revised_arabic) or ""
    )
    if refreshed_artifact == "quality":
        assert candidate.mappings_verified_at is None
        assert _as_utc(candidate.quality_reviewed_at) == refreshed_stamp
        assert candidate.quality_reason == "reviewed edited content"
    else:
        assert _as_utc(candidate.mappings_verified_at) == refreshed_stamp
        assert candidate.quality_reviewed_at is None
        assert candidate.quality_natural is None
        assert candidate.quality_translation_correct is None

    second = activate_prepared_corpus_sentences(
        db_session,
        scope=CorpusScope.build(kind="momo_book"),
        activate_limit=1,
        active_ceiling=1,
        now=NOW,
    )
    db_session.refresh(candidate)
    assert second.selected_ids == []
    assert candidate.is_active is False


def test_activation_invalidates_mapping_edited_before_write_boundary(
    db_session,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW - timedelta(hours=1))
    candidate = _sentence(
        db_session,
        208,
        lemma_ids=[1],
        verification=NOW,
        target_id=1,
        quality=(True, True),
    )
    db_session.commit()
    from app.services.corpus_enrichment import _begin_sqlite_write_boundary

    edited = False

    def edit_mapping_before_write_boundary(*args, **kwargs):
        nonlocal edited
        if not edited:
            edited = True
            db_session.query(SentenceWord).filter(
                SentenceWord.sentence_id == candidate.id
            ).update(
                {SentenceWord.surface_form: "تَعْدِيلٌ"},
                synchronize_session=False,
            )
            db_session.commit()
        return _begin_sqlite_write_boundary(*args, **kwargs)

    with patch(
        "app.services.corpus_enrichment._begin_sqlite_write_boundary",
        side_effect=edit_mapping_before_write_boundary,
    ):
        first = activate_prepared_corpus_sentences(
            db_session,
            scope=CorpusScope.build(kind="momo_book"),
            activate_limit=1,
            active_ceiling=1,
            now=NOW,
        )

    db_session.refresh(candidate)
    edited_word = (
        db_session.query(SentenceWord)
        .filter(SentenceWord.sentence_id == candidate.id)
        .one()
    )
    assert first.selected_ids == []
    assert candidate.is_active is False
    assert candidate.mappings_verified_at is None
    assert edited_word.surface_form == "تَعْدِيلٌ"

    second = activate_prepared_corpus_sentences(
        db_session,
        scope=CorpusScope.build(kind="momo_book"),
        activate_limit=1,
        active_ceiling=1,
        now=NOW,
    )
    db_session.refresh(candidate)
    assert second.selected_ids == []
    assert candidate.is_active is False
    assert candidate.mappings_verified_at is None


@pytest.mark.parametrize(
    ("refreshed_artifact", "sentence_id"),
    [("quality", 214), ("mapping", 215)],
)
def test_activation_slot_failure_invalidates_content_artifacts_independently(
    db_session,
    refreshed_artifact,
    sentence_id,
):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW - timedelta(hours=1))
    candidate = _sentence(
        db_session,
        sentence_id,
        lemma_ids=[1],
        verification=NOW,
        target_id=1,
        quality=(True, True),
    )
    db_session.commit()
    from app.services.corpus_enrichment import _target_choice

    refreshed_stamp = NOW + timedelta(minutes=2)
    revised_arabic = "نَصٌّ تَغَيَّرَ بَعْدَ اللَّقْطَةِ."
    edited = False

    def edit_after_parent_snapshot(*args, **kwargs):
        nonlocal edited
        if not edited:
            edited = True
            values = {Sentence.arabic_text: revised_arabic}
            if refreshed_artifact == "quality":
                values.update(
                    {
                        Sentence.quality_reviewed_at: refreshed_stamp,
                        Sentence.quality_natural: True,
                        Sentence.quality_translation_correct: True,
                        Sentence.quality_reason: "fresh quality only",
                    }
                )
            else:
                values[Sentence.mappings_verified_at] = refreshed_stamp
            db_session.query(Sentence).filter(
                Sentence.id == candidate.id
            ).update(values, synchronize_session=False)
            db_session.commit()
        return _target_choice(*args, **kwargs)

    with patch(
        "app.services.corpus_enrichment._target_choice",
        side_effect=edit_after_parent_snapshot,
    ):
        first = activate_prepared_corpus_sentences(
            db_session,
            scope=CorpusScope.build(kind="momo_book"),
            activate_limit=1,
            active_ceiling=1,
            now=NOW,
        )

    db_session.refresh(candidate)
    assert first.selected_ids == []
    assert candidate.is_active is False
    if refreshed_artifact == "quality":
        assert candidate.mappings_verified_at is None
        assert _as_utc(candidate.quality_reviewed_at) == refreshed_stamp
    else:
        assert _as_utc(candidate.mappings_verified_at) == refreshed_stamp
        assert candidate.quality_reviewed_at is None

    second = activate_prepared_corpus_sentences(
        db_session,
        scope=CorpusScope.build(kind="momo_book"),
        activate_limit=1,
        active_ceiling=1,
        now=NOW,
    )
    db_session.refresh(candidate)
    assert second.selected_ids == []
    assert candidate.is_active is False


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


def test_activation_rechecks_learning_state_after_slot_claim(db_session):
    _lemma(db_session, 1, "كتاب", "book")
    _knowledge(db_session, 1, state="known", due=NOW - timedelta(hours=1))
    candidate = _sentence(
        db_session,
        213,
        lemma_ids=[1],
        verification=NOW,
        target_id=1,
        quality=(True, True),
    )
    db_session.commit()
    from app.services.corpus_enrichment import _begin_sqlite_write_boundary

    concurrent_session = sessionmaker(bind=db_session.bind)()
    state_changed = False

    def acquire_before_write_boundary(*args, **kwargs):
        nonlocal state_changed
        if not state_changed:
            state_changed = True
            concurrent_knowledge = concurrent_session.get(
                UserLemmaKnowledge,
                1,
            )
            concurrent_knowledge.knowledge_state = "acquiring"
            concurrent_knowledge.acquisition_box = 1
            concurrent_knowledge.acquisition_next_due = NOW
            concurrent_knowledge.fsrs_card_json = None
            concurrent_session.commit()
        return _begin_sqlite_write_boundary(*args, **kwargs)

    try:
        with patch(
            "app.services.corpus_enrichment._begin_sqlite_write_boundary",
            side_effect=acquire_before_write_boundary,
        ):
            applied = activate_prepared_corpus_sentences(
                db_session,
                scope=CorpusScope.build(kind="momo_book"),
                activate_limit=1,
                active_ceiling=1,
                now=NOW,
            )
    finally:
        concurrent_session.close()

    db_session.refresh(candidate)
    assert state_changed is True
    assert applied.selected_ids == []
    assert applied.blocked_acquiring_ids == [candidate.id]
    assert candidate.is_active is False


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
