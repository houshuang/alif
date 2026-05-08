from datetime import datetime, timedelta

from app.models import Lemma, Sentence, SentenceWord, UserLemmaKnowledge
from app.services.material_generator import (
    ACQUIRING_RESCUE_SENTENCE_TARGET,
    acquiring_material_gaps,
    active_sentence_counts_by_lemma,
)


def _lemma(db, arabic: str, gloss: str = "x", freq: int | None = None) -> Lemma:
    lemma = Lemma(
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        gloss_en=gloss,
        frequency_rank=freq,
    )
    db.add(lemma)
    db.flush()
    return lemma


def _acquiring(db, lemma: Lemma, *, due=None, started=None) -> None:
    db.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="acquiring",
        acquisition_box=1,
        acquisition_next_due=due,
        acquisition_started_at=started,
    ))


def _sentence(db, lemma_ids: list[int | None], *, active: bool = True, target_id=None) -> Sentence:
    sent = Sentence(
        arabic_text="جملة قصيرة",
        english_translation="a short sentence",
        source="llm",
        target_lemma_id=target_id,
        is_active=active,
        mappings_verified_at=datetime.utcnow(),
    )
    db.add(sent)
    db.flush()
    for pos, lemma_id in enumerate(lemma_ids):
        db.add(SentenceWord(
            sentence_id=sent.id,
            position=pos,
            surface_form=f"w{pos}",
            lemma_id=lemma_id,
            is_target_word=lemma_id == target_id,
        ))
    db.flush()
    return sent


def test_active_sentence_counts_by_lemma_counts_collateral_reviewable_sentences(db_session):
    target = _lemma(db_session, "هَدَفَ", "to aim")
    collateral = _lemma(db_session, "دَوْرٌ", "role")
    unmapped = _lemma(db_session, "سَوِيّ", "except")

    _sentence(db_session, [target.lemma_id, collateral.lemma_id], target_id=target.lemma_id)
    _sentence(db_session, [unmapped.lemma_id, None], target_id=unmapped.lemma_id)
    _sentence(db_session, [collateral.lemma_id], active=False, target_id=collateral.lemma_id)
    db_session.commit()

    counts = active_sentence_counts_by_lemma(
        db_session,
        [target.lemma_id, collateral.lemma_id, unmapped.lemma_id],
    )

    assert counts[target.lemma_id] == 1
    assert counts[collateral.lemma_id] == 1
    assert unmapped.lemma_id not in counts


def test_acquiring_material_gaps_prioritizes_overdue_zero_material_words(db_session):
    now = datetime.utcnow()
    overdue_zero = _lemma(db_session, "دَوْرٌ", "role", freq=200)
    overdue_unmapped_only = _lemma(db_session, "هَمَسَ", "to whisper", freq=300)
    overdue_one_collateral = _lemma(db_session, "شَنَّ", "to launch", freq=100)
    future_zero = _lemma(db_session, "غَلَّفَ", "to wrap", freq=50)
    learning_zero = _lemma(db_session, "تَلَا", "to follow", freq=10)
    full_coverage = _lemma(db_session, "زِينَة", "decoration", freq=20)

    _acquiring(
        db_session,
        overdue_zero,
        due=now - timedelta(hours=2),
        started=now - timedelta(days=2),
    )
    _acquiring(
        db_session,
        overdue_unmapped_only,
        due=now - timedelta(hours=1),
        started=now - timedelta(days=1),
    )
    _acquiring(
        db_session,
        overdue_one_collateral,
        due=now - timedelta(hours=3),
        started=now - timedelta(days=3),
    )
    _acquiring(db_session, future_zero, due=now + timedelta(days=5))
    db_session.add(UserLemmaKnowledge(
        lemma_id=learning_zero.lemma_id,
        knowledge_state="learning",
    ))
    _acquiring(db_session, full_coverage, due=now - timedelta(hours=4))

    _sentence(db_session, [overdue_unmapped_only.lemma_id, None], target_id=overdue_unmapped_only.lemma_id)
    _sentence(db_session, [overdue_one_collateral.lemma_id], target_id=None)
    for _ in range(ACQUIRING_RESCUE_SENTENCE_TARGET):
        _sentence(db_session, [full_coverage.lemma_id], target_id=full_coverage.lemma_id)
    db_session.commit()

    gaps = acquiring_material_gaps(db_session, limit=10)
    ids = [g["lemma_id"] for g in gaps]

    assert ids[:3] == [
        overdue_zero.lemma_id,
        overdue_unmapped_only.lemma_id,
        overdue_one_collateral.lemma_id,
    ]
    assert future_zero.lemma_id in ids
    assert learning_zero.lemma_id not in ids
    assert full_coverage.lemma_id not in ids

    by_id = {g["lemma_id"]: g for g in gaps}
    assert by_id[overdue_zero.lemma_id]["needed"] == ACQUIRING_RESCUE_SENTENCE_TARGET
    assert by_id[overdue_one_collateral.lemma_id]["needed"] == ACQUIRING_RESCUE_SENTENCE_TARGET - 1
    assert by_id[overdue_zero.lemma_id]["tier"] == 0


def test_backfill_dry_run_overrides_backoff_for_acquiring_rescue(db_session, capsys):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from update_material import step_backfill_sentences

    now = datetime.utcnow()
    lemma = _lemma(db_session, "دَوْرٌ", "role")
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="acquiring",
        acquisition_box=1,
        acquisition_next_due=now - timedelta(hours=1),
        generation_failed_count=3,
        generation_backoff_until=now + timedelta(days=6),
    ))
    db_session.commit()

    generated = step_backfill_sentences(
        db_session,
        dry_run=True,
        model="claude_sonnet",
        delay=0.0,
        max_sentences=ACQUIRING_RESCUE_SENTENCE_TARGET,
    )

    out = capsys.readouterr().out
    assert "Overriding backoff for 1 acquiring material rescue gaps" in out
    assert generated == ACQUIRING_RESCUE_SENTENCE_TARGET
