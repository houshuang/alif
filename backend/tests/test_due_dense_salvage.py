from datetime import datetime, timezone
from types import SimpleNamespace

from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from scripts.update_material import salvage_due_dense_inactive_sentences


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
    db_session.commit()

    monkeypatch.setattr(
        "app.services.llm.review_sentences_quality",
        lambda _sentences: [SimpleNamespace(natural=True, translation_correct=True)],
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


def _approve_quality(monkeypatch):
    monkeypatch.setattr(
        "app.services.llm.review_sentences_quality",
        lambda sentences: [
            SimpleNamespace(natural=True, translation_correct=True) for _ in sentences
        ],
    )


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
