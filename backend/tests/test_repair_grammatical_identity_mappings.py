from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models import ActivityLog, Lemma, Sentence, SentenceWord
from scripts import repair_grammatical_identity_mappings_2026_07_29 as repair_script
from scripts.repair_grammatical_identity_mappings_2026_07_29 import (
    apply_repair_plan,
    build_repair_plan,
    exact_particle_identity,
)


NOW = datetime(2026, 7, 29, tzinfo=timezone.utc)


def test_apply_lock_is_shared_and_nonblocking(tmp_path, monkeypatch):
    lock_path = tmp_path / "material-update.lock"
    monkeypatch.setattr(repair_script, "MATERIAL_UPDATE_LOCK", lock_path)

    first = repair_script._try_acquire_material_update_lock()
    assert first is not None
    try:
        assert repair_script._try_acquire_material_update_lock() is None
    finally:
        repair_script._release_material_update_lock(first)

    second = repair_script._try_acquire_material_update_lock()
    assert second is not None
    repair_script._release_material_update_lock(second)


def _lemma(db, ar: str, bare: str, gloss: str) -> Lemma:
    lemma = Lemma(
        lemma_ar=ar,
        lemma_ar_bare=bare,
        gloss_en=gloss,
        pos="particle",
        gates_completed_at=NOW,
    )
    db.add(lemma)
    db.flush()
    return lemma


def _identity_inventory(db) -> dict[str, Lemma]:
    return {
        "أَنْ": _lemma(db, "أَنْ", "ان", "that; to"),
        "أَنَّ": _lemma(db, "أَنَّ", "انّ", "that"),
        "إِنْ": _lemma(db, "إِنْ", "ان", "if"),
        "إِنَّ": _lemma(db, "إِنَّ", "انّ", "indeed; that"),
    }


def _sentence_word(
    db,
    *,
    surface: str,
    lemma_id: int | None,
    active: bool = False,
    sentence_target_lemma_id: int | None = None,
    is_target: bool = False,
) -> tuple[Sentence, SentenceWord]:
    sentence = Sentence(
        arabic_text=f"{surface} الطَّرِيقَ طَوِيلٌ.",
        english_translation="The road is long.",
        source="llm",
        is_active=active,
        target_lemma_id=sentence_target_lemma_id,
        mappings_verified_at=NOW,
        quality_reviewed_at=NOW,
        quality_natural=True,
        quality_translation_correct=True,
    )
    db.add(sentence)
    db.flush()
    word = SentenceWord(
        sentence_id=sentence.id,
        position=0,
        surface_form=surface,
        lemma_id=lemma_id,
        is_target_word=is_target,
    )
    db.add(word)
    db.flush()
    return sentence, word


@pytest.mark.parametrize(
    ("surface", "identity"),
    [
        ("أَنْ", "أَنْ"),
        ("أَنَّ", "أَنَّ"),
        ("إِنْ", "إِنْ"),
        ("إِنَّ", "إِنَّ"),
        ("بِأَنْ", "أَنْ"),
        ("بأَنَّ", "أَنَّ"),
        ("وَإِنْ", "إِنْ"),
        ("وإِنَّ", "إِنَّ"),
        ("وَأَنْ", "أَنْ"),
        ("وَأَنَّ", "أَنَّ"),
    ],
)
def test_exact_particle_identity_accepts_only_deterministic_forms(
    surface,
    identity,
):
    assert exact_particle_identity(surface) == identity


@pytest.mark.parametrize(
    "surface",
    ["أن", "ان", "بأن", "بان", "وإن", "وأن", "لِأَنَّ", "لأنَّ"],
)
def test_exact_particle_identity_rejects_ambiguous_or_lexical_forms(surface):
    assert exact_particle_identity(surface) is None


def test_plan_repairs_exact_collateral_rows_and_preserves_boundaries(db_session):
    identities = _identity_inventory(db_session)
    _sentence_word(
        db_session,
        surface="أَنَّ",
        lemma_id=identities["أَنْ"].lemma_id,
        active=True,
    )
    _sentence_word(
        db_session,
        surface="وَإِنْ",
        lemma_id=identities["أَنْ"].lemma_id,
    )
    # Ambiguous bare form and lexical لأنّ are deliberately untouched.
    _sentence_word(
        db_session,
        surface="بأن",
        lemma_id=identities["أَنْ"].lemma_id,
    )
    _sentence_word(
        db_session,
        surface="لِأَنَّ",
        lemma_id=identities["أَنْ"].lemma_id,
    )
    # Already-correct exact row is idempotently absent.
    _sentence_word(
        db_session,
        surface="إِنَّ",
        lemma_id=identities["إِنَّ"].lemma_id,
    )
    db_session.commit()

    plan = build_repair_plan(db_session)

    assert plan["repair_count"] == 2
    assert plan["target_sensitive_count"] == 0
    assert plan["counts_by_identity"] == {"أَنَّ": 1, "إِنْ": 1}
    assert plan["counts_by_state"] == {"active": 1, "inactive": 1}


def test_plan_excludes_target_sensitive_rows(db_session):
    identities = _identity_inventory(db_session)
    _sentence_word(
        db_session,
        surface="أَنَّ",
        lemma_id=identities["أَنْ"].lemma_id,
        sentence_target_lemma_id=identities["أَنْ"].lemma_id,
        is_target=True,
    )
    db_session.commit()

    plan = build_repair_plan(db_session)

    assert plan["repair_count"] == 0
    assert plan["target_sensitive_count"] == 1


def test_apply_changes_only_planned_lemma_ids_and_logs(db_session):
    identities = _identity_inventory(db_session)
    sentence, word = _sentence_word(
        db_session,
        surface="بِأَنَّ",
        lemma_id=identities["أَنْ"].lemma_id,
        active=True,
    )
    db_session.commit()
    plan = build_repair_plan(db_session)
    original_stamp = sentence.mappings_verified_at
    original_active = sentence.is_active
    original_target = sentence.target_lemma_id

    result = apply_repair_plan(
        db_session,
        plan,
        plan_sha256="test-plan",
        commit=True,
    )
    db_session.refresh(word)
    db_session.refresh(sentence)

    assert result["updated"] == 1
    assert word.lemma_id == identities["أَنَّ"].lemma_id
    assert sentence.is_active is original_active
    assert sentence.target_lemma_id == original_target
    assert sentence.mappings_verified_at == original_stamp
    log = (
        db_session.query(ActivityLog)
        .filter(
            ActivityLog.event_type
            == "grammatical_identity_mapping_repair"
        )
        .one()
    )
    assert log.detail_json["ambiguous_bare_forms_changed"] == 0
    assert log.detail_json["sentence_activation_changed"] == 0
    assert log.detail_json["review_history_changed"] == 0

    # A second census is empty: the repair is idempotent.
    second_plan = build_repair_plan(db_session)
    assert second_plan["repair_count"] == 0


def test_apply_aborts_all_rows_when_any_precondition_drifted(db_session):
    identities = _identity_inventory(db_session)
    _, first = _sentence_word(
        db_session,
        surface="أَنَّ",
        lemma_id=identities["أَنْ"].lemma_id,
    )
    _, second = _sentence_word(
        db_session,
        surface="إِنَّ",
        lemma_id=identities["أَنْ"].lemma_id,
    )
    db_session.commit()
    plan = build_repair_plan(db_session)

    second.surface_form = "إن"
    db_session.commit()

    with pytest.raises(RuntimeError, match="no rows were changed"):
        apply_repair_plan(
            db_session,
            plan,
            plan_sha256="drifted-plan",
            commit=True,
        )
    db_session.rollback()
    db_session.refresh(first)
    db_session.refresh(second)
    assert first.lemma_id == identities["أَنْ"].lemma_id
    assert second.lemma_id == identities["أَنْ"].lemma_id


def test_apply_write_boundary_blocks_post_validation_writer(
    db_session,
    monkeypatch,
):
    identities = _identity_inventory(db_session)
    _, word = _sentence_word(
        db_session,
        surface="أَنَّ",
        lemma_id=identities["أَنْ"].lemma_id,
    )
    db_session.commit()
    plan = build_repair_plan(db_session)
    original_validate = repair_script._validate_repair_rows
    concurrent_session = sessionmaker(bind=db_session.bind)()
    blocked = False

    def validate_then_attempt_concurrent_write(*args, **kwargs):
        nonlocal blocked
        validated = original_validate(*args, **kwargs)
        concurrent_session.connection().exec_driver_sql(
            "PRAGMA busy_timeout=0"
        )
        try:
            concurrent_session.query(SentenceWord).filter(
                SentenceWord.id == word.id
            ).update(
                {SentenceWord.lemma_id: identities["إِنْ"].lemma_id},
                synchronize_session=False,
            )
            concurrent_session.commit()
        except OperationalError:
            concurrent_session.rollback()
            blocked = True
        return validated

    monkeypatch.setattr(
        repair_script,
        "_validate_repair_rows",
        validate_then_attempt_concurrent_write,
    )
    try:
        result = apply_repair_plan(
            db_session,
            plan,
            plan_sha256="serialized-plan",
            commit=True,
        )
    finally:
        concurrent_session.close()

    db_session.refresh(word)
    assert blocked is True
    assert result["updated"] == 1
    assert word.lemma_id == identities["أَنَّ"].lemma_id
