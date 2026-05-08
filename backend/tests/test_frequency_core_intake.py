from datetime import datetime, timezone

from app.models import FrequencyCoreEntry, Lemma, UserLemmaKnowledge
from app.services.frequency_core_intake import intake_frequency_core_gaps


def _core_entry(rank: int, form: str) -> FrequencyCoreEntry:
    return FrequencyCoreEntry(
        core_rank=rank,
        lemma_id=None,
        lemma_key=f"missing:{form}",
        display_form=form,
        score=100.0 - rank,
        confidence_tier="low",
        gap_status="unmapped",
    )


def test_frequency_core_intake_resolves_existing_lemma(db_session):
    lemma = Lemma(
        lemma_ar="كِتَاب",
        lemma_ar_bare="كتاب",
        gloss_en="book",
        pos="noun",
        gates_completed_at=datetime.now(timezone.utc),
    )
    entry = _core_entry(1, "الكتاب")
    db_session.add_all([lemma, entry])
    db_session.commit()

    stats = intake_frequency_core_gaps(
        db_session,
        limit=1,
        max_rank=10,
        create_missing=False,
    )
    db_session.refresh(entry)

    assert stats["resolved_existing"] == 1
    assert stats["created"] == 0
    assert entry.lemma_id == lemma.lemma_id
    assert entry.lemma_key == f"lemma:{lemma.lemma_id}"
    assert entry.gap_status is None


def test_frequency_core_intake_creates_gated_lemma_without_ulk(db_session, monkeypatch):
    import app.services.frequency_core_intake as intake

    entry = _core_entry(1, "منتدى")
    db_session.add(entry)
    db_session.commit()

    def fake_classify(analyses):
        return {
            1: {
                "core_rank": 1,
                "action": "create",
                "lemma_ar": "مُنْتَدَى",
                "lemma_ar_bare": "منتدى",
                "gloss_en": "forum",
                "pos": "noun",
                "root": None,
                "word_category": "standard",
                "confidence": "high",
                "reason": "standard dictionary noun",
            }
        }

    def fake_import_quality(items):
        return items, 0

    def fake_quality_gates(db, lemma_ids, **kwargs):
        now = datetime.now(timezone.utc)
        (
            db.query(Lemma)
            .filter(Lemma.lemma_id.in_(lemma_ids))
            .update({Lemma.gates_completed_at: now}, synchronize_session="fetch")
        )
        db.commit()
        return {"stamped": len(lemma_ids), "variants": 0, "finalize": {}}

    monkeypatch.setattr(intake, "_classify_unmapped_entries", fake_classify)
    monkeypatch.setattr(intake, "_classify_import_quality", fake_import_quality)
    monkeypatch.setattr(intake, "run_quality_gates", fake_quality_gates)

    stats = intake_frequency_core_gaps(db_session, limit=1, max_rank=10)
    db_session.refresh(entry)

    lemma = db_session.query(Lemma).filter(Lemma.lemma_id == entry.lemma_id).one()
    ulk_count = db_session.query(UserLemmaKnowledge).filter(
        UserLemmaKnowledge.lemma_id == lemma.lemma_id
    ).count()

    assert stats["created"] == 1
    assert stats["mapped_ranks"] == [1]
    assert lemma.source == "frequency_core"
    assert lemma.gates_completed_at is not None
    assert ulk_count == 0


def test_frequency_core_intake_rejects_uncertain_creation(db_session, monkeypatch):
    import app.services.frequency_core_intake as intake

    entry = _core_entry(1, "قسم")
    db_session.add(entry)
    db_session.commit()

    monkeypatch.setattr(
        intake,
        "_classify_unmapped_entries",
        lambda analyses: {
            1: {
                "core_rank": 1,
                "action": "create",
                "lemma_ar": "قِسْم",
                "lemma_ar_bare": "قسم",
                "gloss_en": "section",
                "pos": "noun",
                "root": None,
                "word_category": "standard",
                "confidence": "medium",
                "reason": "homograph needs context",
            }
        },
    )

    stats = intake_frequency_core_gaps(db_session, limit=1, max_rank=10)
    db_session.refresh(entry)

    assert stats["rejected"] == 1
    assert stats["created"] == 0
    assert entry.lemma_id is None
    assert entry.gap_status == "needs_manual_review"


def test_frequency_core_intake_rejects_candidates_needing_cleanup(monkeypatch):
    import app.services.frequency_core_intake as intake
    import app.services.import_quality as import_quality

    monkeypatch.setattr(
        import_quality,
        "classify_lemmas",
        lambda items: (
            [
                {
                    "arabic": "المنتدى",
                    "english": "forum",
                    "word_category": "standard",
                    "cleaned_arabic": "منتدى",
                }
            ],
            [],
        ),
    )

    accepted, rejected = intake._classify_import_quality(
        [
            {
                "core_rank": 1,
                "lemma_ar": "المنتدى",
                "lemma_ar_bare": "المنتدى",
                "gloss_en": "forum",
            }
        ]
    )

    assert accepted == []
    assert rejected == 1


def test_frequency_core_intake_retries_manual_review_rows_without_starving_fresh_rows(db_session):
    lemma = Lemma(
        lemma_ar="كِتَاب",
        lemma_ar_bare="كتاب",
        gloss_en="book",
        pos="noun",
        gates_completed_at=datetime.now(timezone.utc),
    )
    blocked = _core_entry(1, "قسم")
    blocked.gap_status = "needs_manual_review"
    next_entry = _core_entry(2, "الكتاب")
    db_session.add_all([lemma, blocked, next_entry])
    db_session.commit()

    stats = intake_frequency_core_gaps(
        db_session,
        limit=1,
        max_rank=10,
        create_missing=False,
    )
    db_session.refresh(blocked)
    db_session.refresh(next_entry)

    assert stats["scanned"] == 2
    assert stats["resolved_existing"] == 1
    assert stats["skipped"] == 1
    assert blocked.lemma_id is None
    assert next_entry.lemma_id == lemma.lemma_id


def test_frequency_core_intake_can_disable_manual_review_retry(db_session):
    lemma = Lemma(
        lemma_ar="كِتَاب",
        lemma_ar_bare="كتاب",
        gloss_en="book",
        pos="noun",
        gates_completed_at=datetime.now(timezone.utc),
    )
    blocked = _core_entry(1, "قسم")
    blocked.gap_status = "needs_manual_review"
    next_entry = _core_entry(2, "الكتاب")
    db_session.add_all([lemma, blocked, next_entry])
    db_session.commit()

    stats = intake_frequency_core_gaps(
        db_session,
        limit=1,
        max_rank=10,
        retry_limit=0,
        create_missing=False,
    )
    db_session.refresh(blocked)
    db_session.refresh(next_entry)

    assert stats["scanned"] == 1
    assert stats["resolved_existing"] == 1
    assert blocked.lemma_id is None
    assert next_entry.lemma_id == lemma.lemma_id
