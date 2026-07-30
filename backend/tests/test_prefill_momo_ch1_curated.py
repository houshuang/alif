import copy
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models import (
    ActivityLog,
    ContentFlag,
    Lemma,
    ReviewLog,
    Root,
    Sentence,
    SentenceWord,
    UserLemmaKnowledge,
)
from app.services.corpus_enrichment import (
    _project_diacritics_onto_source,
    has_arabic_diacritics,
)
from scripts import prefill_momo_ch1_curated_2026_07_30 as prefill


NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)
TEST_COMMIT = "a" * 40
VERIFIED_BACKUP = {
    "path": "/tmp/verified-backup.db",
    "sha256": "b" * 64,
    "size": 123,
    "device": 1,
    "inode": 2,
    "mtime_ns": 456,
    "quick_check": "ok",
    "scope_sha256": "c" * 64,
    "age_seconds_at_apply": 1.0,
}


def _seed_exact_scope(db) -> None:
    root = Root(root_id=90000, root="ت.س.ت", core_meaning_en="test")
    lemma = Lemma(
        lemma_id=90000,
        lemma_ar="تَجْرِبَة",
        lemma_ar_bare="تجربة",
        root_id=90000,
        gloss_en="test",
        gates_completed_at=NOW,
    )
    db.add_all([root, lemma])
    db.flush()
    db.add(
        UserLemmaKnowledge(
            lemma_id=90000,
            knowledge_state="known",
            times_seen=3,
            times_correct=3,
        )
    )
    db.add(
        ReviewLog(
            lemma_id=90000,
            rating=4,
            reviewed_at=NOW,
            sentence_id=None,
        )
    )

    for curated in prefill.CURATED_ROWS:
        sentence = Sentence(
            id=curated.sentence_id,
            arabic_text=curated.source_arabic,
            english_translation=None,
            transliteration=curated.source_transliteration,
            source="corpus",
            target_lemma_id=curated.target_lemma_id,
            times_shown=0,
            is_active=False,
            created_at=NOW,
            mappings_verified_at=None,
            quality_reviewed_at=None,
            quality_natural=None,
            quality_translation_correct=None,
            quality_reason=None,
            kind="momo_book",
        )
        db.add(sentence)
        db.flush()
        for position in range(curated.word_count):
            is_target = position == 0
            db.add(
                SentenceWord(
                    id=curated.sentence_id * 100 + position,
                    sentence_id=curated.sentence_id,
                    position=position,
                    surface_form=f"token-{curated.sentence_id}-{position}",
                    lemma_id=(
                        curated.target_lemma_id
                        if is_target
                        else 100000 + position
                    ),
                    is_target_word=is_target,
                    grammar_role_json=None,
                )
            )

    for sentence_id in prefill.EXCLUDED_IDS:
        db.add(
            Sentence(
                id=sentence_id,
                arabic_text=f"excluded {sentence_id}",
                english_translation=None,
                transliteration=f"excluded-{sentence_id}",
                source="corpus",
                target_lemma_id=777,
                times_shown=0,
                is_active=False,
                created_at=NOW,
                kind="momo_book",
            )
        )
        db.flush()
        db.add(
            SentenceWord(
                id=sentence_id * 100,
                sentence_id=sentence_id,
                position=0,
                surface_form="excluded",
                lemma_id=777,
                is_target_word=True,
            )
        )
    db.commit()


def _plan(db):
    return prefill.build_prefill_plan(db, git_commit=TEST_COMMIT)


def _apply(db, plan):
    return prefill.apply_prefill_plan(
        db,
        plan,
        plan_sha256="reviewed-plan",
        backup=VERIFIED_BACKUP,
        git_commit=TEST_COMMIT,
        commit=True,
    )


def _field_triple(sentence):
    return {
        "arabic_text": sentence.arabic_text,
        "english_translation": sentence.english_translation,
        "transliteration": sentence.transliteration,
    }


def test_manifest_is_exact_reviewed_seven_and_source_preserving():
    assert prefill.validate_manifest() == prefill.APPROVED_MANIFEST_SHA256
    assert tuple(row.sentence_id for row in prefill.CURATED_ROWS) == (
        52133,
        52195,
        52134,
        52135,
        52198,
        52199,
        52136,
    )
    assert set(prefill.APPROVED_IDS).isdisjoint(prefill.EXCLUDED_IDS)

    by_id = {row.sentence_id: row for row in prefill.CURATED_ROWS}
    for row in prefill.CURATED_ROWS:
        assert (
            prefill._strip_projectable_harakat(row.arabic)
            == row.source_arabic
        )
        assert (
            _project_diacritics_onto_source(row.source_arabic, row.arabic)
            == row.arabic
        )
        assert has_arabic_diacritics(row.arabic)
        assert row.english.strip()
        assert row.transliteration.strip()
        assert not prefill._contains_arabic_letter(row.transliteration)

    assert "لَاحَظَتْ" in by_id[52133].arabic
    assert "أَنْفُسُهُمْ" in by_id[52133].arabic
    assert "إِنَّنِى" in by_id[52135].arabic
    assert "innanī" in by_id[52135].transliteration
    assert "baytī" in by_id[52135].transliteration
    assert "أَنْتِ" in by_id[52198].arabic
    assert "أَنَّ" in by_id[52198].arabic
    assert "a-laysa" in by_id[52198].transliteration
    assert "al-ysa" not in by_id[52198].transliteration
    assert "سَمَّيْتِ" in by_id[52199].arabic


def test_plan_is_read_only_and_pins_parent_children_and_artifacts(db_session):
    _seed_exact_scope(db_session)
    before = {
        row.sentence_id: _field_triple(
            db_session.get(Sentence, row.sentence_id)
        )
        for row in prefill.CURATED_ROWS
    }

    plan = _plan(db_session)

    assert plan["manifest_sha256"] == prefill.APPROVED_MANIFEST_SHA256
    assert plan["script_sha256"] == prefill._script_sha256()
    assert plan["update_count"] == 7
    assert [row["sentence_id"] for row in plan["rows"]] == list(
        prefill.APPROVED_IDS
    )
    assert all(
        all(count == 0 for count in row["artifacts"].values())
        for row in plan["rows"]
    )
    assert [len(row["words"]) for row in plan["rows"]] == [
        row.word_count for row in prefill.CURATED_ROWS
    ]
    assert (
        db_session.query(ActivityLog)
        .filter(ActivityLog.event_type == "momo_ch1_curated_prefill")
        .count()
        == 0
    )
    assert {
        row.sentence_id: _field_triple(
            db_session.get(Sentence, row.sentence_id)
        )
        for row in prefill.CURATED_ROWS
    } == before


def test_apply_changes_only_three_fields_and_one_provenance_log(db_session):
    _seed_exact_scope(db_session)
    plan = _plan(db_session)
    parents_before = {
        item["sentence_id"]: copy.deepcopy(item["before"])
        for item in plan["rows"]
    }
    words_before = {
        item["sentence_id"]: copy.deepcopy(item["words"])
        for item in plan["rows"]
    }
    excluded_before = prefill._sha256_json(
        prefill._excluded_scope_snapshot(db_session)
    )

    result = _apply(db_session, plan)

    assert result["updated_ids"] == list(prefill.APPROVED_IDS)
    assert result["excluded_ids_untouched"] == list(prefill.EXCLUDED_IDS)
    for curated in prefill.CURATED_ROWS:
        sentence = db_session.get(Sentence, curated.sentence_id)
        expected = parents_before[curated.sentence_id]
        expected.update(
            {
                "arabic_text": curated.arabic,
                "english_translation": curated.english,
                "transliteration": curated.transliteration,
            }
        )
        assert prefill._sentence_snapshot(sentence) == expected
        assert (
            prefill._word_snapshot(db_session, curated.sentence_id)
            == words_before[curated.sentence_id]
        )
        assert has_arabic_diacritics(sentence.arabic_text)
        assert sentence.english_translation.strip()
        assert sentence.transliteration == curated.transliteration

    assert (
        prefill._sha256_json(prefill._excluded_scope_snapshot(db_session))
        == excluded_before
    )
    logs = (
        db_session.query(ActivityLog)
        .filter(ActivityLog.event_type == "momo_ch1_curated_prefill")
        .all()
    )
    assert len(logs) == 1
    detail = logs[0].detail_json
    assert detail["manifest_sha256"] == prefill.APPROVED_MANIFEST_SHA256
    assert detail["script_sha256"] == prefill._script_sha256()
    assert detail["sentence_activation_changed"] == 0
    assert detail["mapping_verification_stamps_changed"] == 0
    assert detail["quality_review_stamps_changed"] == 0
    assert detail["target_bookkeeping_changed"] == 0
    assert detail["sentence_words_changed"] == 0
    assert detail["lemmas_changed"] == 0
    assert detail["user_lemma_knowledge_changed"] == 0
    assert detail["review_history_changed"] == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("arabic_text", "drifted"),
        ("english_translation", "already translated"),
        ("is_active", True),
        ("target_lemma_id", 999),
        ("mappings_verified_at", NOW),
        ("quality_reviewed_at", NOW),
    ],
)
def test_parent_drift_aborts_all_seven(db_session, field, value):
    _seed_exact_scope(db_session)
    plan = _plan(db_session)
    sentence = db_session.get(Sentence, prefill.APPROVED_IDS[-1])
    setattr(sentence, field, value)
    db_session.commit()

    with pytest.raises(RuntimeError, match="no rows were changed"):
        _apply(db_session, plan)

    assert (
        db_session.query(ActivityLog)
        .filter(ActivityLog.event_type == "momo_ch1_curated_prefill")
        .count()
        == 0
    )
    for curated in prefill.CURATED_ROWS[:-1]:
        sentence = db_session.get(Sentence, curated.sentence_id)
        assert sentence.arabic_text == curated.source_arabic
        assert sentence.english_translation is None


def test_child_or_artifact_drift_aborts_atomically(db_session):
    _seed_exact_scope(db_session)
    plan = _plan(db_session)
    word = (
        db_session.query(SentenceWord)
        .filter(SentenceWord.sentence_id == prefill.APPROVED_IDS[-1])
        .first()
    )
    word.grammar_role_json = {"role": "drift"}
    db_session.add(
        ContentFlag(
            content_type="sentence_arabic",
            sentence_id=prefill.APPROVED_IDS[-1],
            status="pending",
        )
    )
    db_session.commit()

    with pytest.raises(RuntimeError, match="no rows were changed"):
        _apply(db_session, plan)

    for curated in prefill.CURATED_ROWS:
        sentence = db_session.get(Sentence, curated.sentence_id)
        assert sentence.arabic_text == curated.source_arabic
        assert sentence.english_translation is None


@pytest.mark.parametrize(
    "tamper",
    ["desired", "manifest", "script", "drop", "duplicate"],
)
def test_tampered_plan_is_rejected_before_write(db_session, tamper):
    _seed_exact_scope(db_session)
    plan = _plan(db_session)
    if tamper == "desired":
        plan["rows"][0]["desired"]["english_translation"] = "tampered"
    elif tamper == "manifest":
        plan["manifest_sha256"] = "0" * 64
    elif tamper == "script":
        plan["script_sha256"] = "0" * 64
    elif tamper == "drop":
        plan["rows"].pop()
    else:
        plan["rows"][-1] = copy.deepcopy(plan["rows"][0])

    with pytest.raises(RuntimeError, match="no rows were changed"):
        _apply(db_session, plan)

    assert all(
        db_session.get(Sentence, row.sentence_id).english_translation is None
        for row in prefill.CURATED_ROWS
    )


def test_successful_plan_cannot_be_reapplied(db_session):
    _seed_exact_scope(db_session)
    plan = _plan(db_session)
    _apply(db_session, plan)

    with pytest.raises(RuntimeError, match="no rows were changed"):
        _apply(db_session, plan)

    assert (
        db_session.query(ActivityLog)
        .filter(ActivityLog.event_type == "momo_ch1_curated_prefill")
        .count()
        == 1
    )


def test_apply_write_boundary_blocks_concurrent_writer(db_session, monkeypatch):
    _seed_exact_scope(db_session)
    plan = _plan(db_session)
    original_validate = prefill._validate_live_rows
    concurrent = sessionmaker(bind=db_session.bind)()
    blocked = False

    def validate_then_attempt_write(*args, **kwargs):
        nonlocal blocked
        original_validate(*args, **kwargs)
        concurrent.connection().exec_driver_sql("PRAGMA busy_timeout=0")
        try:
            concurrent.query(Sentence).filter(
                Sentence.id == prefill.EXCLUDED_IDS[0]
            ).update(
                {Sentence.arabic_text: "concurrent"},
                synchronize_session=False,
            )
            concurrent.commit()
        except OperationalError:
            concurrent.rollback()
            blocked = True

    monkeypatch.setattr(prefill, "_validate_live_rows", validate_then_attempt_write)
    try:
        result = _apply(db_session, plan)
    finally:
        concurrent.close()

    assert blocked is True
    assert result["updated"] == 7
    assert (
        db_session.get(Sentence, prefill.EXCLUDED_IDS[0]).arabic_text
        != "concurrent"
    )


def test_apply_lock_is_shared_and_nonblocking(tmp_path, monkeypatch):
    lock_path = tmp_path / "material-update.lock"
    monkeypatch.setattr(prefill, "MATERIAL_UPDATE_LOCK", lock_path)

    first = prefill._try_acquire_material_update_lock()
    assert first is not None
    try:
        assert prefill._try_acquire_material_update_lock() is None
    finally:
        prefill._release_material_update_lock(first)

    second = prefill._try_acquire_material_update_lock()
    assert second is not None
    prefill._release_material_update_lock(second)


def _online_backup(source_path: Path, backup_path: Path) -> str:
    source = sqlite3.connect(source_path)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    backup_path.chmod(0o600)
    return hashlib.sha256(backup_path.read_bytes()).hexdigest()


def test_backup_inspection_requires_private_fresh_matching_alif_file(
    db_session,
    tmp_path,
):
    _seed_exact_scope(db_session)
    live = Path(db_session.bind.url.database)
    backup = tmp_path / "backup.db"
    digest = _online_backup(live, backup)
    now = backup.stat().st_mtime

    info = prefill.inspect_backup(
        backup,
        digest,
        target_database_path=str(live),
        now=now,
    )

    assert info["sha256"] == digest
    assert info["quick_check"] == "ok"
    assert info["path"] == str(backup.resolve())
    assert info["scope_sha256"]

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        prefill.inspect_backup(
            backup,
            "0" * 64,
            target_database_path=str(live),
            now=now,
        )

    backup.chmod(0o644)
    with pytest.raises(RuntimeError, match="mode 0600"):
        prefill.inspect_backup(
            backup,
            digest,
            target_database_path=str(live),
            now=now,
        )


def test_backup_inspection_rejects_non_alif_wrong_or_stale_file(
    db_session,
    tmp_path,
):
    _seed_exact_scope(db_session)
    live = Path(db_session.bind.url.database)
    bad = tmp_path / "bad.db"
    bad.write_text("not sqlite", encoding="utf-8")
    bad.chmod(0o600)
    digest = hashlib.sha256(bad.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match="healthy SQLite"):
        prefill.inspect_backup(
            bad,
            digest,
            target_database_path=str(live),
            now=bad.stat().st_mtime,
        )

    backup = tmp_path / "stale.db"
    digest = _online_backup(live, backup)
    stale_now = backup.stat().st_mtime + prefill.BACKUP_MAX_AGE_SECONDS + 1
    with pytest.raises(RuntimeError, match="not fresh"):
        prefill.inspect_backup(
            backup,
            digest,
            target_database_path=str(live),
            now=stale_now,
        )

    unrelated = tmp_path / "unrelated.db"
    connection = sqlite3.connect(unrelated)
    try:
        connection.execute("CREATE TABLE proof (id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()
    unrelated.chmod(0o600)
    unrelated_digest = hashlib.sha256(unrelated.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match="not an Alif backup"):
        prefill.inspect_backup(
            unrelated,
            unrelated_digest,
            target_database_path=str(live),
            now=unrelated.stat().st_mtime,
        )


def test_backup_must_correspond_and_not_alias_live_inode(db_session, tmp_path):
    _seed_exact_scope(db_session)
    live = Path(db_session.bind.url.database)
    backup = tmp_path / "drifted.db"
    _online_backup(live, backup)
    connection = sqlite3.connect(backup)
    try:
        connection.execute(
            "INSERT INTO roots (root_id, root) VALUES (?, ?)",
            (999999, "غ.ل.ط"),
        )
        connection.commit()
    finally:
        connection.close()
    backup.chmod(0o600)
    drifted_digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match="does not correspond"):
        prefill.inspect_backup(
            backup,
            drifted_digest,
            target_database_path=str(live),
            now=backup.stat().st_mtime,
        )

    alias = tmp_path / "live-hardlink.db"
    os.link(live, alias)
    alias_digest = hashlib.sha256(alias.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match="aliases the live target"):
        prefill.inspect_backup(
            alias,
            alias_digest,
            target_database_path=str(live),
            now=alias.stat().st_mtime,
        )

    with_sidecar = tmp_path / "with-sidecar.db"
    sidecar_digest = _online_backup(live, with_sidecar)
    Path(str(with_sidecar) + "-wal").write_bytes(b"stale-wal")
    with pytest.raises(RuntimeError, match="standalone online-backup"):
        prefill.inspect_backup(
            with_sidecar,
            sidecar_digest,
            target_database_path=str(live),
            now=with_sidecar.stat().st_mtime,
        )


def test_hashed_plan_loader_rejects_tampering(tmp_path):
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps({"safe": True}), encoding="utf-8")
    digest = hashlib.sha256(plan_file.read_bytes()).hexdigest()

    plan, observed = prefill._load_hashed_plan(plan_file, digest)
    assert plan == {"safe": True}
    assert observed == digest

    plan_file.write_text(json.dumps({"safe": False}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="plan hash mismatch"):
        prefill._load_hashed_plan(plan_file, digest)


def test_plan_writer_is_exclusive_and_never_targets_database(tmp_path):
    database = tmp_path / "live.db"
    database.write_bytes(b"database")
    plan = tmp_path / "plan.json"

    written = prefill._write_plan_file(
        plan,
        b'{"safe":true}\n',
        target_database_path=str(database),
    )

    assert written == plan
    assert plan.read_bytes() == b'{"safe":true}\n'
    assert stat_mode(plan) == 0o600
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        prefill._write_plan_file(
            plan,
            b"replacement",
            target_database_path=str(database),
        )
    with pytest.raises(RuntimeError, match="live database"):
        prefill._write_plan_file(
            database,
            b"destructive",
            target_database_path=str(database),
        )
    assert database.read_bytes() == b"database"


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_git_pin_requires_clean_tracked_main_checkout(monkeypatch):
    def runner(outputs):
        values = iter(outputs)

        def fake_run(*args, **kwargs):
            return SimpleNamespace(stdout=next(values))

        return fake_run

    monkeypatch.setattr(
        prefill.subprocess,
        "run",
        runner([" M backend/scripts/prefill.py\n"]),
    )
    with pytest.raises(RuntimeError, match="tracked worktree is dirty"):
        prefill._git_commit()

    monkeypatch.setattr(
        prefill.subprocess,
        "run",
        runner(["", "sh/feature\n"]),
    )
    with pytest.raises(RuntimeError, match="requires the deployed main branch"):
        prefill._git_commit()

    monkeypatch.setattr(
        prefill.subprocess,
        "run",
        runner(["", "main\n", "d" * 40 + "\n"]),
    )
    assert prefill._git_commit() == "d" * 40
