from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import sessionmaker

from app.models import (
    Lemma,
    Sentence,
    SentenceWord,
    Story,
    StoryWord,
    UserLemmaKnowledge,
)
from app.services.sentence_eligibility import (
    CORPUS_BLOCKED_SENTINEL,
    CORPUS_CLAIM_SENTINEL,
    CORPUS_QUALITY_REJECTED_SENTINEL,
)
from app.services.llm import MOMO_PUBLISHED_ARABIC_REVIEW_CONTEXT
from scripts.update_material import (
    salvage_due_dense_inactive_sentences,
    step_reactivate_book_sentences,
)


def _lemma(db, lemma_id, arabic):
    lemma = Lemma(
        lemma_id=lemma_id,
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        gloss_en=arabic,
        gates_completed_at=datetime.now(timezone.utc),
    )
    db.add(lemma)
    db.flush()
    return lemma


def _active(db, lemma):
    db.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="known",
        source="study",
        fsrs_card_json={"due": datetime.now(timezone.utc).isoformat()},
    ))


def _sentence(db, sid, words):
    sent = Sentence(
        id=sid,
        arabic_text=" ".join(w.lemma_ar for w in words),
        english_translation="test",
        is_active=False,
        mappings_verified_at=datetime.now(timezone.utc),
        target_lemma_id=words[0].lemma_id,
    )
    db.add(sent)
    db.flush()
    for i, lemma in enumerate(words):
        db.add(SentenceWord(
            sentence_id=sid,
            position=i,
            surface_form=lemma.lemma_ar,
            lemma_id=lemma.lemma_id,
        ))
    db.flush()
    return sent


def test_salvage_due_dense_reactivates_only_quality_approved(monkeypatch, db_session):
    l1 = _lemma(db_session, 1, "كتاب")
    l2 = _lemma(db_session, 2, "قلم")
    l3 = _lemma(db_session, 3, "بيت")
    for lemma in (l1, l2, l3):
        _active(db_session, lemma)
    sent = _sentence(db_session, 1, [l1, l2, l3])
    sent.source = "corpus"
    sent.kind = "momo_book"
    db_session.commit()
    review_inputs: list[dict] = []

    def approve(sentences):
        review_inputs.extend(sentences)
        return [
            SimpleNamespace(natural=True, translation_correct=True)
            for _ in sentences
        ]

    monkeypatch.setattr(
        "app.services.llm.review_sentences_quality",
        approve,
    )

    count = salvage_due_dense_inactive_sentences(
        db=db_session,
        target_lemma_ids={l1.lemma_id, l2.lemma_id},
        known_lemma_ids={l1.lemma_id, l2.lemma_id, l3.lemma_id},
        budget=5,
        dry_run=False,
    )

    assert count == 1
    db_session.refresh(sent)
    assert sent.is_active is True
    assert review_inputs == [
        {
            "arabic": sent.arabic_text,
            "english": sent.english_translation,
            "review_context": MOMO_PUBLISHED_ARABIC_REVIEW_CONTEXT,
        }
    ]


def test_due_dense_salvage_does_not_use_incomplete_approval(
    monkeypatch,
    db_session,
):
    target_a = _lemma(db_session, 1, "كتاب")
    target_b = _lemma(db_session, 2, "قلم")
    for lemma in (target_a, target_b):
        _active(db_session, lemma)
    sentence = _sentence(db_session, 1, [target_a, target_b])
    db_session.commit()

    monkeypatch.setattr(
        "app.services.llm.review_sentences_quality",
        lambda _sentences: [
            SimpleNamespace(
                natural=True,
                translation_correct=True,
                review_completed=False,
            )
        ],
    )

    count = salvage_due_dense_inactive_sentences(
        db=db_session,
        target_lemma_ids={target_a.lemma_id, target_b.lemma_id},
        known_lemma_ids={target_a.lemma_id, target_b.lemma_id},
        budget=5,
        dry_run=False,
    )

    assert count == 0
    db_session.refresh(sentence)
    assert sentence.is_active is False


def _approve_quality(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm.review_sentences_quality",
        lambda sentences: [
            SimpleNamespace(natural=True, translation_correct=True) for _ in sentences
        ],
    )


@pytest.mark.parametrize(
    ("field", "concurrent_value"),
    [
        ("arabic_text", "نَصٌّ مُعَدَّلٌ"),
        ("english_translation", "Concurrently revised."),
        ("transliteration", "naṣṣun muʿaddalun"),
        ("source", "manual"),
        ("kind", "concurrent_kind"),
        ("mappings_verified_at", CORPUS_BLOCKED_SENTINEL),
        (
            "quality_reviewed_at",
            datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
        ),
        ("quality_natural", False),
        ("quality_translation_correct", False),
        ("quality_reason", "concurrent quality verdict"),
    ],
)
def test_due_dense_salvage_cas_preserves_concurrent_parent_mutation(
    monkeypatch,
    db_session,
    field,
    concurrent_value,
):
    target_a = _lemma(db_session, 1, "كتاب")
    target_b = _lemma(db_session, 2, "قلم")
    for lemma in (target_a, target_b):
        _active(db_session, lemma)
    sentence = _sentence(db_session, 1, [target_a, target_b])
    sentence.source = "corpus"
    sentence.kind = "momo_book"
    db_session.commit()
    original_value = getattr(sentence, field)

    def mutate_then_approve(sentences):
        ConcurrentSession = sessionmaker(bind=db_session.bind)
        with ConcurrentSession() as concurrent_db:
            concurrent_db.query(Sentence).filter(
                Sentence.id == sentence.id
            ).update(
                {getattr(Sentence, field): concurrent_value},
                synchronize_session=False,
            )
            concurrent_db.commit()
        return [
            SimpleNamespace(natural=True, translation_correct=True)
            for _ in sentences
        ]

    monkeypatch.setattr(
        "app.services.llm.review_sentences_quality",
        mutate_then_approve,
    )

    count = salvage_due_dense_inactive_sentences(
        db=db_session,
        target_lemma_ids={target_a.lemma_id, target_b.lemma_id},
        known_lemma_ids={target_a.lemma_id, target_b.lemma_id},
        budget=5,
        dry_run=False,
    )

    db_session.refresh(sentence)
    assert count == 0
    assert sentence.is_active is False
    assert getattr(sentence, field) != original_value


def test_due_dense_salvage_cas_does_not_claim_concurrent_activation(
    monkeypatch,
    db_session,
):
    target_a = _lemma(db_session, 1, "كتاب")
    target_b = _lemma(db_session, 2, "قلم")
    for lemma in (target_a, target_b):
        _active(db_session, lemma)
    sentence = _sentence(db_session, 1, [target_a, target_b])
    db_session.commit()

    def activate_then_approve(sentences):
        ConcurrentSession = sessionmaker(bind=db_session.bind)
        with ConcurrentSession() as concurrent_db:
            concurrent_db.query(Sentence).filter(
                Sentence.id == sentence.id
            ).update(
                {Sentence.is_active: True},
                synchronize_session=False,
            )
            concurrent_db.commit()
        return [
            SimpleNamespace(natural=True, translation_correct=True)
            for _ in sentences
        ]

    monkeypatch.setattr(
        "app.services.llm.review_sentences_quality",
        activate_then_approve,
    )

    count = salvage_due_dense_inactive_sentences(
        db=db_session,
        target_lemma_ids={target_a.lemma_id, target_b.lemma_id},
        known_lemma_ids={target_a.lemma_id, target_b.lemma_id},
        budget=5,
        dry_run=False,
    )

    db_session.refresh(sentence)
    assert count == 0
    assert sentence.is_active is True


def test_due_dense_salvage_all_cas_misses_release_writer_lock(
    monkeypatch,
    db_session,
):
    target_a = _lemma(db_session, 1, "كتاب")
    target_b = _lemma(db_session, 2, "قلم")
    for lemma in (target_a, target_b):
        _active(db_session, lemma)
    sentence = _sentence(db_session, 1, [target_a, target_b])
    db_session.commit()
    ConcurrentSession = sessionmaker(bind=db_session.bind)

    def mutate_then_approve(sentences):
        with ConcurrentSession() as concurrent_db:
            concurrent_db.query(Sentence).filter(
                Sentence.id == sentence.id
            ).update(
                {Sentence.kind: "concurrent_kind"},
                synchronize_session=False,
            )
            concurrent_db.commit()
        return [
            SimpleNamespace(natural=True, translation_correct=True)
            for _ in sentences
        ]

    monkeypatch.setattr(
        "app.services.llm.review_sentences_quality",
        mutate_then_approve,
    )

    count = salvage_due_dense_inactive_sentences(
        db=db_session,
        target_lemma_ids={target_a.lemma_id, target_b.lemma_id},
        known_lemma_ids={target_a.lemma_id, target_b.lemma_id},
        budget=5,
        dry_run=False,
    )

    assert count == 0
    assert db_session.in_transaction() is False
    with ConcurrentSession() as second_writer:
        updated = second_writer.query(Sentence).filter(
            Sentence.id == sentence.id
        ).update(
            {Sentence.quality_reason: "second writer acquired"},
            synchronize_session=False,
        )
        second_writer.commit()
    assert updated == 1

    db_session.expire_all()
    stored = db_session.get(Sentence, sentence.id)
    assert stored.is_active is False
    assert stored.kind == "concurrent_kind"
    assert stored.quality_reason == "second writer acquired"


def test_single_coverage_salvaged_for_deficit_word(monkeypatch, db_session):
    # One due word with zero reviewable coverage; its only retired sentence
    # covers just that 1 due word — the old >=2 rule would strand it forever.
    target = _lemma(db_session, 1, "تراث")
    scaffold_a = _lemma(db_session, 2, "قلم")
    scaffold_b = _lemma(db_session, 3, "بيت")
    for lemma in (target, scaffold_a, scaffold_b):
        _active(db_session, lemma)
    sent = _sentence(db_session, 1, [target, scaffold_a, scaffold_b])
    db_session.commit()
    _approve_quality(monkeypatch)

    # Without the deficit hint, a single due-hit sentence is NOT salvaged.
    assert salvage_due_dense_inactive_sentences(
        db=db_session,
        target_lemma_ids={target.lemma_id},
        known_lemma_ids={scaffold_a.lemma_id, scaffold_b.lemma_id},
        budget=5,
        dry_run=False,
    ) == 0

    # With the word flagged in deficit, the single-coverage sentence is rescued.
    count = salvage_due_dense_inactive_sentences(
        db=db_session,
        target_lemma_ids={target.lemma_id},
        known_lemma_ids={scaffold_a.lemma_id, scaffold_b.lemma_id},
        budget=5,
        dry_run=False,
        deficit_lemma_ids={target.lemma_id},
    )
    assert count == 1
    db_session.refresh(sent)
    assert sent.is_active is True


def test_due_dense_salvage_never_reactivates_durable_corpus_dispositions(
    monkeypatch,
    db_session,
):
    target_a = _lemma(db_session, 1, "كتاب")
    target_b = _lemma(db_session, 2, "قلم")
    for lemma in (target_a, target_b):
        _active(db_session, lemma)
    blocked = _sentence(db_session, 1, [target_a, target_b])
    rejected = _sentence(db_session, 2, [target_a, target_b])
    claimed = _sentence(db_session, 3, [target_a, target_b])
    blocked.mappings_verified_at = CORPUS_BLOCKED_SENTINEL
    rejected.mappings_verified_at = CORPUS_QUALITY_REJECTED_SENTINEL
    claimed.mappings_verified_at = CORPUS_CLAIM_SENTINEL
    db_session.commit()

    monkeypatch.setattr(
        "app.services.llm.review_sentences_quality",
        lambda _sentences: (_ for _ in ()).throw(
            AssertionError("durable dispositions must not reach quality review")
        ),
    )

    count = salvage_due_dense_inactive_sentences(
        db=db_session,
        target_lemma_ids={target_a.lemma_id, target_b.lemma_id},
        known_lemma_ids={target_a.lemma_id, target_b.lemma_id},
        budget=5,
        dry_run=False,
    )

    assert count == 0
    db_session.refresh(blocked)
    db_session.refresh(rejected)
    db_session.refresh(claimed)
    assert blocked.is_active is False
    assert rejected.is_active is False
    assert claimed.is_active is False


def test_due_dense_salvage_defers_reviewed_authentic_rows_to_governor(
    monkeypatch,
    db_session,
):
    target_a = _lemma(db_session, 1, "كتاب")
    target_b = _lemma(db_session, 2, "قلم")
    for lemma in (target_a, target_b):
        _active(db_session, lemma)
    governed = _sentence(db_session, 1, [target_a, target_b])
    governed.source = "corpus"
    governed.quality_reviewed_at = datetime.now(timezone.utc)
    governed.quality_natural = True
    governed.quality_translation_correct = True
    generated = _sentence(db_session, 2, [target_a, target_b])
    generated.source = "llm"
    generated.arabic_text = "نص مولد"
    generated.quality_reviewed_at = datetime.now(timezone.utc)
    generated.quality_natural = True
    generated.quality_translation_correct = True
    legacy_null_source = _sentence(db_session, 3, [target_a, target_b])
    legacy_null_source.arabic_text = "نص قديم"
    legacy_null_source.quality_reviewed_at = datetime.now(timezone.utc)
    legacy_null_source.quality_natural = True
    legacy_null_source.quality_translation_correct = True
    db_session.commit()

    reviewed_arabic: list[str] = []

    def approve(sentences):
        reviewed_arabic.extend(row["arabic"] for row in sentences)
        return [
            SimpleNamespace(natural=True, translation_correct=True)
            for _ in sentences
        ]

    monkeypatch.setattr(
        "app.services.llm.review_sentences_quality",
        approve,
    )

    count = salvage_due_dense_inactive_sentences(
        db=db_session,
        target_lemma_ids={target_a.lemma_id, target_b.lemma_id},
        known_lemma_ids={target_a.lemma_id, target_b.lemma_id},
        budget=5,
        dry_run=False,
    )

    db_session.refresh(governed)
    db_session.refresh(generated)
    db_session.refresh(legacy_null_source)
    assert count == 2
    assert governed.is_active is False
    assert generated.is_active is True
    assert legacy_null_source.is_active is True
    assert reviewed_arabic == [
        generated.arabic_text,
        legacy_null_source.arabic_text,
    ]


def test_book_page_reactivation_skips_durable_corpus_dispositions(db_session):
    known = _lemma(db_session, 1, "كتاب")
    _active(db_session, known)
    story = Story(
        title_ar="كتاب",
        title_en="Book",
        body_ar="كتاب",
        source="book_ocr",
        status="active",
    )
    db_session.add(story)
    db_session.flush()
    db_session.add(
        StoryWord(
            story_id=story.id,
            position=0,
            surface_form="كتاب",
            lemma_id=known.lemma_id,
            is_function_word=False,
            page_number=1,
        )
    )
    ordinary = Sentence(
        arabic_text="كتاب",
        source="book",
        story_id=story.id,
        page_number=1,
        is_active=False,
        mappings_verified_at=None,
    )
    blocked = Sentence(
        arabic_text="كتاب محظور",
        source="corpus",
        story_id=story.id,
        page_number=1,
        is_active=False,
        mappings_verified_at=CORPUS_BLOCKED_SENTINEL,
    )
    rejected = Sentence(
        arabic_text="كتاب مرفوض",
        source="corpus",
        story_id=story.id,
        page_number=1,
        is_active=False,
        mappings_verified_at=CORPUS_QUALITY_REJECTED_SENTINEL,
    )
    claimed = Sentence(
        arabic_text="كتاب قيد المعالجة",
        source="corpus",
        story_id=story.id,
        page_number=1,
        is_active=False,
        mappings_verified_at=CORPUS_CLAIM_SENTINEL,
    )
    governed = Sentence(
        arabic_text="كِتَابٌ مُرَاجَعٌ",
        source="book",
        story_id=story.id,
        page_number=1,
        is_active=False,
        mappings_verified_at=datetime.now(timezone.utc),
        quality_reviewed_at=datetime.now(timezone.utc),
        quality_natural=True,
        quality_translation_correct=True,
    )
    generated = Sentence(
        arabic_text="جملة مولدة",
        source="llm",
        story_id=story.id,
        page_number=1,
        is_active=False,
        mappings_verified_at=datetime.now(timezone.utc),
        quality_reviewed_at=datetime.now(timezone.utc),
        quality_natural=True,
        quality_translation_correct=True,
    )
    db_session.add_all([
        ordinary,
        blocked,
        rejected,
        claimed,
        governed,
        generated,
    ])
    db_session.commit()

    assert step_reactivate_book_sentences(db_session) == 2

    for sentence in (
        ordinary,
        blocked,
        rejected,
        claimed,
        governed,
        generated,
    ):
        db_session.refresh(sentence)
    assert ordinary.is_active is True
    assert blocked.is_active is False
    assert rejected.is_active is False
    assert claimed.is_active is False
    assert governed.is_active is False
    assert generated.is_active is True


def test_reviewable_coverage_counts_includes_collateral(db_session):
    from app.services.sentence_eligibility import reviewable_coverage_counts

    target = _lemma(db_session, 1, "كتاب")
    collateral = _lemma(db_session, 2, "قلم")
    for lemma in (target, collateral):
        _active(db_session, lemma)
    sent = _sentence(db_session, 1, [target, collateral])
    sent.is_active = True  # reviewable requires active
    db_session.commit()

    counts = reviewable_coverage_counts(db_session)
    # The collateral word is credited coverage even though it is not the target.
    assert counts.get(target.lemma_id) == 1
    assert counts.get(collateral.lemma_id) == 1
    # A word with no sentence is absent (deficit = 0).
    assert counts.get(999, 0) == 0
