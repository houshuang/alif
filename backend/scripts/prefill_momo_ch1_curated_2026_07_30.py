"""Atomically prefill the seven reviewed Chapter 1 *Momo* corpus rows.

This is a deliberately one-purpose bridge into the normal corpus preparation
pipeline.  It writes only reviewed Arabic, English, and transliteration for the
fixed seven-row cohort.  It does not perform QA, mapping verification, target
selection, vocabulary creation, activation, or learner-state work.

The workflow is hash-pinned and dry-run by default::

    cd /opt/alif/backend
    .venv/bin/python3 scripts/prefill_momo_ch1_curated_2026_07_30.py
    .venv/bin/python3 scripts/prefill_momo_ch1_curated_2026_07_30.py \
        --plan --plan-file /tmp/momo_ch1_curated_prefill.json
    .venv/bin/python3 scripts/prefill_momo_ch1_curated_2026_07_30.py \
        --apply --plan-file /tmp/momo_ch1_curated_prefill.json \
        --expected-plan-sha256 <printed-plan-sha256> \
        --expected-manifest-sha256 <printed-manifest-sha256> \
        --backup-path /opt/alif-backups/<fresh-online-backup>.db \
        --backup-sha256 <backup-sha256>

Apply verifies a fresh mode-0600 SQLite backup, holds the shared material lock,
acquires ``BEGIN IMMEDIATE`` before live validation, compare-and-sets all seven
rows, verifies scoped and global invariants, and records one same-transaction
ActivityLog.  Any drift aborts the entire operation.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, text  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    ActivityLog,
    ConfusionCapture,
    ContentFlag,
    Lemma,
    ReviewLog,
    Root,
    Sentence,
    SentenceGrammarFeature,
    SentenceReviewLog,
    SentenceWord,
    UserLemmaKnowledge,
    WordReviewEvidence,
)
from app.services.corpus_enrichment import (  # noqa: E402
    _PROJECTABLE_HARAKAT,
    _project_diacritics_onto_source,
    has_arabic_diacritics,
)


PLAN_VERSION = 1
MANIFEST_VERSION = 1
DEFAULT_PLAN_FILE = Path("/tmp/alif_momo_ch1_curated_prefill_20260730.json")
BACKUP_MAX_AGE_SECONDS = 2 * 60 * 60
REPO_ROOT = Path(__file__).resolve().parents[2]
MATERIAL_UPDATE_LOCK = Path(
    os.environ.get(
        "ALIF_UPDATE_MATERIAL_LOCK",
        "/tmp/alif-update-material.lock",
    )
)

APPROVED_IDS = (52133, 52195, 52134, 52135, 52198, 52199, 52136)
EXCLUDED_IDS = (52194, 52196, 52197)


@dataclass(frozen=True)
class CuratedRow:
    sentence_id: int
    source_arabic: str
    source_transliteration: str
    target_lemma_id: int
    word_count: int
    arabic: str
    english: str
    transliteration: str


CURATED_ROWS = (
    CuratedRow(
        sentence_id=52133,
        source_arabic=(
            "لاحظت أنهم أناس لطاف ، فقد كانوا أنفسهم فقراء ويعرفون الحياة ."
        ),
        source_transliteration=(
            "lāḥẓt nhm nās lṭāf ، fqd kānwā nfshm fqrāʾ "
            "wīʿrfūn al-ḥyāa ."
        ),
        target_lemma_id=4248,
        word_count=10,
        arabic=(
            "لَاحَظَتْ أَنَّهُمْ أُنَاسٌ لِطَافٌ ، فَقَدْ كَانُوا "
            "أَنْفُسُهُمْ فُقَرَاءَ وَيَعْرِفُونَ الْحَيَاةَ ."
        ),
        english=(
            "She noticed that they were kind people, for they themselves "
            "were poor and knew what life was like."
        ),
        transliteration=(
            "lāḥaẓat annahum unāsun liṭāfun, fa-qad kānū anfusuhum "
            "fuqarāʾa wa-yaʿrifūna al-ḥayāta."
        ),
    ),
    CuratedRow(
        sentence_id=52195,
        source_arabic='فأجابت مومو " نعم " .',
        source_transliteration='fʾjābt mūmū " nʿm " .',
        target_lemma_id=2217,
        word_count=3,
        arabic='فَأَجَابَتْ مُومُو " نَعَمْ " .',
        english='Momo replied, “Yes.”',
        transliteration='fa-ajābat mūmū: “naʿam.”',
    ),
    CuratedRow(
        sentence_id=52134,
        source_arabic='" وتريدين البقاء هنا ؟',
        source_transliteration='" wtrīdīn al-bqāʾ hnā ؟',
        target_lemma_id=4326,
        word_count=3,
        arabic='" وَتُرِيدِينَ الْبَقَاءَ هُنَا ؟',
        english='“And you want to stay here?”',
        transliteration='wa-turīdīna al-baqāʾa hunā?',
    ),
    CuratedRow(
        sentence_id=52135,
        source_arabic='فأسرعت مومو مؤكدة بقولها : " إننى هنا في بيتى " .',
        source_transliteration=(
            'fʾsrʿt mūmū mʾkda bqūlhā : " nnā hnā fī bītā " .'
        ),
        target_lemma_id=4287,
        word_count=8,
        arabic=(
            'فَأَسْرَعَتْ مُومُو مُؤَكِّدَةً بِقَوْلِهَا : '
            '" إِنَّنِى هُنَا فِي بَيْتِى " .'
        ),
        english='Momo quickly affirmed, saying, “I am at home here.”',
        transliteration=(
            'fa-asraʿat mūmū muʾakkidatan bi-qawlihā: '
            '“innanī hunā fī baytī.”'
        ),
    ),
    CuratedRow(
        sentence_id=52198,
        source_arabic='" أنت تقولين أن اسمك مومو ، أليس كذلك ؟',
        source_transliteration='" nt tqūlīn n smk mūmū ، al-ys kdhlk ؟',
        target_lemma_id=408,
        word_count=7,
        arabic='" أَنْتِ تَقُولِينَ أَنَّ اسْمَكِ مُومُو ، أَلَيْسَ كَذَلِكِ ؟',
        english='“You say that your name is Momo, isn’t that right?”',
        transliteration=(
            'anti taqūlīna anna ismaki mūmū, a-laysa kadhāliki?'
        ),
    ),
    CuratedRow(
        sentence_id=52199,
        source_arabic='" أنت سميت نفسك هكذا ؟',
        source_transliteration='" nt smīt nfsk hkdhā ؟',
        target_lemma_id=501,
        word_count=4,
        arabic='" أَنْتِ سَمَّيْتِ نَفْسَكِ هَكَذَا ؟',
        english='“You named yourself that?”',
        transliteration='anti sammayti nafsaki hākadhā?',
    ),
    CuratedRow(
        sentence_id=52136,
        source_arabic="فنظرت إليه مومو في فزع .",
        source_transliteration="fnẓrt līh mūmū fī fzʿ .",
        target_lemma_id=4240,
        word_count=5,
        arabic="فَنَظَرَتْ إِلَيْهِ مُومُو فِي فَزَعٍ .",
        english="Momo looked at him in alarm.",
        transliteration="fa-naẓarat ilayhi mūmū fī fazaʿin.",
    ),
)

# This literal is updated only after independent content review.  Runtime
# validation compares it with the canonical JSON hash of CURATED_ROWS, so an
# accidental edit cannot become authorized merely by printing a new hash.
APPROVED_MANIFEST_SHA256 = (
    "b43dbd00062fb6d797de2531b4895727899a54863ddddf2422e3f2dc1d522281"
)

_ARABIC_LETTER_RANGES = (
    (0x0621, 0x063A),
    (0x0641, 0x064A),
    (0x066E, 0x06D3),
)
_REQUIRED_BACKUP_TABLES = {
    "activity_log",
    "confusion_captures",
    "content_flags",
    "lemmas",
    "review_log",
    "roots",
    "sentence_grammar_features",
    "sentence_review_log",
    "sentence_words",
    "sentences",
    "user_lemma_knowledge",
    "word_review_evidence",
}


def _canonical_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    kwargs: dict[str, Any] = {
        "ensure_ascii": False,
        "sort_keys": True,
    }
    if pretty:
        kwargs["indent"] = 2
    else:
        kwargs["separators"] = (",", ":")
    return (json.dumps(value, **kwargs) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _manifest_payload() -> dict[str, Any]:
    return {
        "manifest_version": MANIFEST_VERSION,
        "approved_ids": list(APPROVED_IDS),
        "excluded_ids": list(EXCLUDED_IDS),
        "rows": [asdict(row) for row in CURATED_ROWS],
    }


MANIFEST_SHA256 = _sha256_json(_manifest_payload())


def _strip_projectable_harakat(value: str) -> str:
    return "".join(char for char in value if char not in _PROJECTABLE_HARAKAT)


def _contains_arabic_letter(value: str) -> bool:
    return any(
        start <= ord(char) <= end
        for char in value
        for start, end in _ARABIC_LETTER_RANGES
    )


def validate_manifest() -> str:
    """Validate the reviewed manifest and return its canonical SHA-256."""
    errors: list[str] = []
    ids = tuple(row.sentence_id for row in CURATED_ROWS)
    if ids != APPROVED_IDS:
        errors.append(f"manifest IDs/order {ids} != approved {APPROVED_IDS}")
    if len(set(ids)) != len(ids):
        errors.append("manifest contains duplicate sentence IDs")
    if set(APPROVED_IDS) & set(EXCLUDED_IDS):
        errors.append("approved and excluded scopes overlap")

    for row in CURATED_ROWS:
        text_fields = {
            "source_arabic": row.source_arabic,
            "source_transliteration": row.source_transliteration,
            "arabic": row.arabic,
            "english": row.english,
            "transliteration": row.transliteration,
        }
        for field, value in text_fields.items():
            if not value or not value.strip():
                errors.append(f"{row.sentence_id}: {field} is blank")
            if unicodedata.normalize("NFC", value) != value:
                errors.append(f"{row.sentence_id}: {field} is not NFC")
        if _strip_projectable_harakat(row.arabic) != row.source_arabic:
            errors.append(
                f"{row.sentence_id}: curated Arabic changes source layout/content"
            )
        if (
            _project_diacritics_onto_source(row.source_arabic, row.arabic)
            != row.arabic
        ):
            errors.append(
                f"{row.sentence_id}: curated Arabic fails source projection"
            )
        if not has_arabic_diacritics(row.arabic):
            errors.append(
                f"{row.sentence_id}: curated Arabic lacks substantial tashkil"
            )
        if _contains_arabic_letter(row.transliteration):
            errors.append(
                f"{row.sentence_id}: reviewed transliteration contains Arabic letters"
            )
        if row.word_count <= 0 or row.target_lemma_id <= 0:
            errors.append(f"{row.sentence_id}: invalid word/target metadata")

    if MANIFEST_SHA256 != APPROVED_MANIFEST_SHA256:
        errors.append(
            "canonical manifest hash is not the independently approved pin: "
            f"computed={MANIFEST_SHA256}, approved={APPROVED_MANIFEST_SHA256}"
        )
    if errors:
        raise RuntimeError("invalid curated manifest:\n" + "\n".join(errors))
    return MANIFEST_SHA256


def _try_acquire_material_update_lock():
    MATERIAL_UPDATE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = MATERIAL_UPDATE_LOCK.open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def _release_material_update_lock(handle) -> None:
    try:
        fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_sqlite_readonly(
    path: Path,
    *,
    immutable: bool = False,
) -> sqlite3.Connection:
    query = "?mode=ro&immutable=1" if immutable else "?mode=ro"
    connection = sqlite3.connect(path.as_uri() + query, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _sqlite_scope_snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    missing = sorted(_REQUIRED_BACKUP_TABLES - tables)
    if missing:
        raise RuntimeError(
            "database is not an Alif backup; missing tables: "
            + ", ".join(missing)
        )

    scope_ids = (*APPROVED_IDS, *EXCLUDED_IDS)
    placeholders = ",".join("?" for _ in scope_ids)
    parents = [
        dict(row)
        for row in connection.execute(
            f"SELECT * FROM sentences WHERE id IN ({placeholders}) "
            "ORDER BY id",
            scope_ids,
        ).fetchall()
    ]
    words = [
        dict(row)
        for row in connection.execute(
            f"SELECT * FROM sentence_words "
            f"WHERE sentence_id IN ({placeholders}) "
            "ORDER BY sentence_id, id",
            scope_ids,
        ).fetchall()
    ]
    scoped_artifacts = {
        table: [
            dict(row)
            for row in connection.execute(
                f"SELECT * FROM {table} "
                f"WHERE sentence_id IN ({placeholders}) ORDER BY id",
                scope_ids,
            ).fetchall()
        ]
        for table in (
            "review_log",
            "sentence_review_log",
            "word_review_evidence",
            "sentence_grammar_features",
            "confusion_captures",
            "content_flags",
        )
    }
    schema = [
        dict(row)
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND name IN "
            f"({','.join('?' for _ in _REQUIRED_BACKUP_TABLES)}) "
            "ORDER BY name",
            tuple(sorted(_REQUIRED_BACKUP_TABLES)),
        ).fetchall()
    ]
    inventory_counts = {
        table: int(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        )
        for table in ("roots", "lemmas", "sentences", "sentence_words")
    }
    return {
        "schema": schema,
        "user_version": int(
            connection.execute("PRAGMA user_version").fetchone()[0]
        ),
        "inventory_counts": inventory_counts,
        "parents": parents,
        "words": words,
        "scoped_artifacts": scoped_artifacts,
    }


def _validate_backup_preimages(snapshot: dict[str, Any]) -> None:
    parents = {
        int(row["id"]): row for row in snapshot.get("parents", [])
    }
    words_by_sentence: dict[int, list[dict[str, Any]]] = {}
    for word in snapshot.get("words", []):
        words_by_sentence.setdefault(int(word["sentence_id"]), []).append(word)

    expected_scope = set(APPROVED_IDS) | set(EXCLUDED_IDS)
    if set(parents) != expected_scope:
        raise RuntimeError(
            "backup does not contain the exact ten-row reviewed scope"
        )

    errors: list[str] = []
    for curated in CURATED_ROWS:
        parent = parents[curated.sentence_id]
        words = words_by_sentence.get(curated.sentence_id, [])
        checks = {
            "source_arabic": parent.get("arabic_text")
            == curated.source_arabic,
            "source_transliteration": parent.get("transliteration")
            == curated.source_transliteration,
            "english_null": parent.get("english_translation") is None,
            "source": parent.get("source") == "corpus",
            "kind": parent.get("kind") == "momo_book",
            "inactive": not bool(parent.get("is_active")),
            "target": parent.get("target_lemma_id")
            == curated.target_lemma_id,
            "mapping_unstamped": parent.get("mappings_verified_at") is None,
            "quality_unstamped": parent.get("quality_reviewed_at") is None,
            "quality_natural_null": parent.get("quality_natural") is None,
            "quality_translation_null": parent.get(
                "quality_translation_correct"
            )
            is None,
            "quality_reason_null": parent.get("quality_reason") is None,
            "word_count": len(words) == curated.word_count,
            "all_words_mapped": all(
                word.get("lemma_id") is not None for word in words
            ),
            "one_target": sum(
                bool(word.get("is_target_word")) for word in words
            )
            == 1,
            "target_matches": [
                word.get("lemma_id")
                for word in words
                if word.get("is_target_word")
            ]
            == [curated.target_lemma_id],
            "no_grammar_roles": all(
                word.get("grammar_role_json") in (None, "null")
                for word in words
            ),
        }
        failed = sorted(name for name, passed in checks.items() if not passed)
        if failed:
            errors.append(f"{curated.sentence_id}: {failed}")
    for table, rows in snapshot.get("scoped_artifacts", {}).items():
        approved_rows = [
            row for row in rows if int(row["sentence_id"]) in APPROVED_IDS
        ]
        if approved_rows:
            errors.append(
                f"approved scope contains {len(approved_rows)} {table} rows"
            )
    if errors:
        raise RuntimeError(
            "backup lacks exact prefill rollback preimages:\n"
            + "\n".join(errors)
        )


def inspect_backup(
    backup_path: Path,
    expected_sha256: str,
    *,
    target_database_path: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Verify a fresh, private, integrity-checked SQLite backup."""
    try:
        resolved = backup_path.expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"backup does not exist: {backup_path}") from exc
    info = resolved.stat()
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"backup is not a regular file: {resolved}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeError(
            f"backup must be mode 0600 (or stricter): {resolved} is "
            f"{oct(stat.S_IMODE(info.st_mode))}"
        )
    target = Path(target_database_path).expanduser().resolve(strict=True)
    try:
        same_file = os.path.samefile(resolved, target)
    except OSError as exc:
        raise RuntimeError(
            f"cannot compare backup with live target: {exc}"
        ) from exc
    if same_file:
        raise RuntimeError(
            "backup path aliases the live target database by inode"
        )
    backup_sidecars = [
        Path(str(resolved) + suffix)
        for suffix in ("-wal", "-shm", "-journal")
    ]
    present_sidecars = [str(path) for path in backup_sidecars if path.exists()]
    if present_sidecars:
        raise RuntimeError(
            "backup must be a standalone online-backup image; found sidecars: "
            + ", ".join(present_sidecars)
        )

    observed_sha256 = _hash_file(resolved)
    if observed_sha256 != expected_sha256:
        raise RuntimeError(
            "backup SHA-256 mismatch: "
            f"expected {expected_sha256}, got {observed_sha256}"
        )

    current_time = time.time() if now is None else now
    age_seconds = current_time - info.st_mtime
    if age_seconds < -300 or age_seconds > BACKUP_MAX_AGE_SECONDS:
        raise RuntimeError(
            f"backup is not fresh: age_seconds={age_seconds:.1f}, "
            f"maximum={BACKUP_MAX_AGE_SECONDS}"
        )

    try:
        connection = _open_sqlite_readonly(resolved, immutable=True)
        try:
            quick_check = [
                str(row[0])
                for row in connection.execute("PRAGMA quick_check").fetchall()
            ]
            backup_snapshot = _sqlite_scope_snapshot(connection)
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"backup is not a healthy SQLite database: {exc}") from exc
    if quick_check != ["ok"]:
        raise RuntimeError(f"backup quick_check failed: {quick_check}")
    _validate_backup_preimages(backup_snapshot)

    try:
        target_connection = _open_sqlite_readonly(target)
        try:
            target_snapshot = _sqlite_scope_snapshot(target_connection)
        finally:
            target_connection.close()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(
            f"live target is not a healthy Alif SQLite database: {exc}"
        ) from exc
    if backup_snapshot != target_snapshot:
        raise RuntimeError(
            "backup does not correspond to the current live Alif inventory/scope"
        )

    # Detect replacement or mutation during integrity/correspondence checks.
    final_info = resolved.stat()
    final_sha256 = _hash_file(resolved)
    final_sidecars = [str(path) for path in backup_sidecars if path.exists()]
    if (
        final_info.st_dev != info.st_dev
        or final_info.st_ino != info.st_ino
        or final_info.st_size != info.st_size
        or final_info.st_mtime_ns != info.st_mtime_ns
        or final_sha256 != observed_sha256
        or final_sidecars
    ):
        raise RuntimeError("backup changed while it was being verified")

    return {
        "path": str(resolved),
        "sha256": observed_sha256,
        "size": info.st_size,
        "device": info.st_dev,
        "inode": info.st_ino,
        "mtime_ns": info.st_mtime_ns,
        "quick_check": "ok",
        "scope_sha256": _sha256_json(backup_snapshot),
        "age_seconds_at_apply": round(age_seconds, 3),
    }


def _git_commit() -> str:
    status = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout.strip():
        raise RuntimeError(
            "tracked worktree is dirty; refusing to plan/apply a data write"
        )
    branch = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if branch != "main":
        raise RuntimeError(
            f"data operation requires the deployed main branch, got {branch!r}"
        )
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    commit = result.stdout.strip()
    if len(commit) != 40:
        raise RuntimeError(f"unexpected git commit: {commit!r}")
    return commit


def _script_sha256() -> str:
    return _hash_file(Path(__file__).resolve())


def _database_path(db) -> str:
    database = db.get_bind().url.database
    if not database or database == ":memory:":
        return str(database or ":memory:")
    return str(Path(database).expanduser().resolve())


def _stable_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _sentence_snapshot(sentence: Sentence) -> dict[str, Any]:
    return {
        column.name: _stable_value(getattr(sentence, column.name))
        for column in Sentence.__table__.columns
    }


def _word_snapshot(db, sentence_id: int) -> list[dict[str, Any]]:
    words = (
        db.query(SentenceWord)
        .filter(SentenceWord.sentence_id == sentence_id)
        .order_by(SentenceWord.id)
        .all()
    )
    return [
        {
            column.name: _stable_value(getattr(word, column.name))
            for column in SentenceWord.__table__.columns
        }
        for word in words
    ]


def _artifact_counts(db, sentence_id: int) -> dict[str, int]:
    models = {
        "review_log": (ReviewLog, ReviewLog.sentence_id),
        "sentence_review_log": (
            SentenceReviewLog,
            SentenceReviewLog.sentence_id,
        ),
        "word_review_evidence": (
            WordReviewEvidence,
            WordReviewEvidence.sentence_id,
        ),
        "sentence_grammar_features": (
            SentenceGrammarFeature,
            SentenceGrammarFeature.sentence_id,
        ),
        "confusion_captures": (
            ConfusionCapture,
            ConfusionCapture.sentence_id,
        ),
        "content_flags": (ContentFlag, ContentFlag.sentence_id),
    }
    return {
        name: int(
            db.query(func.count(model.id))
            .filter(column == sentence_id)
            .scalar()
            or 0
        )
        for name, (model, column) in models.items()
    }


def _global_invariant_counts(db) -> dict[str, int]:
    return {
        "sentences": int(db.query(func.count(Sentence.id)).scalar() or 0),
        "sentence_words": int(
            db.query(func.count(SentenceWord.id)).scalar() or 0
        ),
        "active_sentences": int(
            db.query(func.count(Sentence.id))
            .filter(Sentence.is_active.is_(True))
            .scalar()
            or 0
        ),
        "target_words": int(
            db.query(func.count(SentenceWord.id))
            .filter(SentenceWord.is_target_word.is_(True))
            .scalar()
            or 0
        ),
        "roots": int(db.query(func.count(Root.root_id)).scalar() or 0),
        "lemmas": int(db.query(func.count(Lemma.lemma_id)).scalar() or 0),
        "user_lemma_knowledge": int(
            db.query(func.count(UserLemmaKnowledge.id)).scalar() or 0
        ),
        "review_log": int(db.query(func.count(ReviewLog.id)).scalar() or 0),
        "sentence_review_log": int(
            db.query(func.count(SentenceReviewLog.id)).scalar() or 0
        ),
        "word_review_evidence": int(
            db.query(func.count(WordReviewEvidence.id)).scalar() or 0
        ),
        "sentence_grammar_features": int(
            db.query(func.count(SentenceGrammarFeature.id)).scalar() or 0
        ),
        "confusion_captures": int(
            db.query(func.count(ConfusionCapture.id)).scalar() or 0
        ),
        "content_flags": int(
            db.query(func.count(ContentFlag.id)).scalar() or 0
        ),
    }


def _excluded_scope_snapshot(db) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for sentence_id in EXCLUDED_IDS:
        sentence = db.get(Sentence, sentence_id)
        rows.append(
            {
                "sentence_id": sentence_id,
                "parent": (
                    _sentence_snapshot(sentence) if sentence is not None else None
                ),
                "words": _word_snapshot(db, sentence_id),
                "artifacts": _artifact_counts(db, sentence_id),
            }
        )
    return {"ids": list(EXCLUDED_IDS), "rows": rows}


def _desired_snapshot(row: CuratedRow) -> dict[str, str]:
    return {
        "arabic_text": row.arabic,
        "english_translation": row.english,
        "transliteration": row.transliteration,
    }


def _collect_plan_row(db, curated: CuratedRow) -> dict[str, Any]:
    sentence = db.get(Sentence, curated.sentence_id)
    if sentence is None:
        raise RuntimeError(f"sentence {curated.sentence_id} is missing")
    parent = _sentence_snapshot(sentence)
    words = _word_snapshot(db, curated.sentence_id)
    artifacts = _artifact_counts(db, curated.sentence_id)

    checks = {
        "source_arabic": sentence.arabic_text == curated.source_arabic,
        "source_transliteration": (
            sentence.transliteration == curated.source_transliteration
        ),
        "english_null": sentence.english_translation is None,
        "source": sentence.source == "corpus",
        "kind": sentence.kind == "momo_book",
        "inactive": not bool(sentence.is_active),
        "target": sentence.target_lemma_id == curated.target_lemma_id,
        "mapping_unstamped": sentence.mappings_verified_at is None,
        "quality_unstamped": sentence.quality_reviewed_at is None,
        "quality_natural_null": sentence.quality_natural is None,
        "quality_translation_null": (
            sentence.quality_translation_correct is None
        ),
        "quality_reason_null": sentence.quality_reason is None,
        "audio_null": sentence.audio_url is None,
        "never_shown": int(sentence.times_shown or 0) == 0,
        "word_count": len(words) == curated.word_count,
        "all_words_mapped": all(word["lemma_id"] is not None for word in words),
        "one_target": sum(bool(word["is_target_word"]) for word in words) == 1,
        "target_matches": [
            word["lemma_id"] for word in words if word["is_target_word"]
        ]
        == [curated.target_lemma_id],
        "no_grammar_roles": all(
            word["grammar_role_json"] is None for word in words
        ),
        "zero_artifacts": all(count == 0 for count in artifacts.values()),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise RuntimeError(
            f"sentence {curated.sentence_id} is not prefill-safe: {failed}"
        )

    desired = _desired_snapshot(curated)
    return {
        "sentence_id": curated.sentence_id,
        "before": parent,
        "before_sha256": _sha256_json(parent),
        "words": words,
        "words_sha256": _sha256_json(words),
        "artifacts": artifacts,
        "desired": desired,
        "desired_sha256": _sha256_json(desired),
    }


def build_prefill_plan(db, *, git_commit: str | None = None) -> dict[str, Any]:
    """Build the exact read-only plan; no database state is changed."""
    manifest_sha256 = validate_manifest()
    code_commit = git_commit or _git_commit()
    rows = [_collect_plan_row(db, row) for row in CURATED_ROWS]
    excluded = _excluded_scope_snapshot(db)
    return {
        "plan_version": PLAN_VERSION,
        "manifest_version": MANIFEST_VERSION,
        "manifest_sha256": manifest_sha256,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": code_commit,
        "script_sha256": _script_sha256(),
        "database_path": _database_path(db),
        "scope": {
            "source": "corpus",
            "kind": "momo_book",
            "sentence_ids": list(APPROVED_IDS),
            "excluded_ids": list(EXCLUDED_IDS),
        },
        "update_count": len(rows),
        "rows": rows,
        "excluded_scope_sha256": _sha256_json(excluded),
        "invariant_counts_at_plan": _global_invariant_counts(db),
    }


def _validate_plan_header(
    db,
    plan: dict[str, Any],
    *,
    git_commit: str,
) -> None:
    errors: list[str] = []
    if plan.get("plan_version") != PLAN_VERSION:
        errors.append("plan_version")
    if plan.get("manifest_version") != MANIFEST_VERSION:
        errors.append("manifest_version")
    if plan.get("manifest_sha256") != validate_manifest():
        errors.append("manifest_sha256")
    if plan.get("git_commit") != git_commit:
        errors.append("git_commit")
    if plan.get("script_sha256") != _script_sha256():
        errors.append("script_sha256")
    if plan.get("database_path") != _database_path(db):
        errors.append("database_path")
    expected_scope = {
        "source": "corpus",
        "kind": "momo_book",
        "sentence_ids": list(APPROVED_IDS),
        "excluded_ids": list(EXCLUDED_IDS),
    }
    if plan.get("scope") != expected_scope:
        errors.append("scope")

    rows = plan.get("rows")
    if not isinstance(rows, list):
        rows = []
        errors.append("rows")
    row_ids = [row.get("sentence_id") for row in rows if isinstance(row, dict)]
    if row_ids != list(APPROVED_IDS):
        errors.append("row_ids")
    if plan.get("update_count") != len(APPROVED_IDS):
        errors.append("update_count")

    manifest_by_id = {row.sentence_id: row for row in CURATED_ROWS}
    for item in rows:
        if not isinstance(item, dict):
            errors.append("row_shape")
            continue
        sentence_id = item.get("sentence_id")
        curated = manifest_by_id.get(sentence_id)
        if curated is None:
            errors.append(f"row_{sentence_id}_not_manifest")
            continue
        desired = _desired_snapshot(curated)
        checks = {
            "before_hash": item.get("before_sha256")
            == _sha256_json(item.get("before")),
            "words_hash": item.get("words_sha256")
            == _sha256_json(item.get("words")),
            "desired": item.get("desired") == desired,
            "desired_hash": item.get("desired_sha256")
            == _sha256_json(desired),
            "artifacts": isinstance(item.get("artifacts"), dict)
            and all(
                value == 0 for value in item.get("artifacts", {}).values()
            ),
        }
        errors.extend(
            f"row_{sentence_id}_{name}"
            for name, passed in checks.items()
            if not passed
        )
    if errors:
        raise RuntimeError(
            "prefill plan header is invalid; no rows were changed: "
            + ", ".join(errors)
        )


def _validate_live_rows(db, plan: dict[str, Any]) -> None:
    errors: list[str] = []
    for item, curated in zip(plan["rows"], CURATED_ROWS, strict=True):
        try:
            live = _collect_plan_row(db, curated)
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        checks = {
            "before": live["before"] == item["before"],
            "before_hash": live["before_sha256"] == item["before_sha256"],
            "words": live["words"] == item["words"],
            "words_hash": live["words_sha256"] == item["words_sha256"],
            "artifacts": live["artifacts"] == item["artifacts"],
        }
        failed = sorted(name for name, passed in checks.items() if not passed)
        if failed:
            errors.append(f"sentence {curated.sentence_id} drifted: {failed}")

    excluded_hash = _sha256_json(_excluded_scope_snapshot(db))
    if excluded_hash != plan.get("excluded_scope_sha256"):
        errors.append("excluded rows drifted since planning")
    if errors:
        raise RuntimeError(
            "prefill plan drifted; no rows were changed:\n" + "\n".join(errors)
        )


def _apply_one_cas(db, curated: CuratedRow) -> None:
    updated = (
        db.query(Sentence)
        .filter(
            Sentence.id == curated.sentence_id,
            Sentence.source == "corpus",
            Sentence.kind == "momo_book",
            Sentence.arabic_text == curated.source_arabic,
            Sentence.transliteration == curated.source_transliteration,
            Sentence.english_translation.is_(None),
            Sentence.is_active.is_(False),
            Sentence.target_lemma_id == curated.target_lemma_id,
            Sentence.mappings_verified_at.is_(None),
            Sentence.quality_reviewed_at.is_(None),
            Sentence.quality_natural.is_(None),
            Sentence.quality_translation_correct.is_(None),
            Sentence.quality_reason.is_(None),
            Sentence.audio_url.is_(None),
            func.coalesce(Sentence.times_shown, 0) == 0,
        )
        .update(
            {
                Sentence.arabic_text: curated.arabic,
                Sentence.english_translation: curated.english,
                Sentence.transliteration: curated.transliteration,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise RuntimeError(
            f"sentence {curated.sentence_id} failed compare-and-set"
        )


def _verify_after(
    db,
    plan: dict[str, Any],
    *,
    before_invariants: dict[str, int],
) -> tuple[dict[str, str], dict[str, str]]:
    before_hashes: dict[str, str] = {}
    after_hashes: dict[str, str] = {}
    errors: list[str] = []
    for item, curated in zip(plan["rows"], CURATED_ROWS, strict=True):
        sentence = db.get(Sentence, curated.sentence_id)
        if sentence is None:
            errors.append(f"sentence {curated.sentence_id} disappeared")
            continue
        after = _sentence_snapshot(sentence)
        expected = dict(item["before"])
        expected.update(_desired_snapshot(curated))
        if after != expected:
            errors.append(f"sentence {curated.sentence_id} changed beyond 3 fields")
        words = _word_snapshot(db, curated.sentence_id)
        artifacts = _artifact_counts(db, curated.sentence_id)
        if words != item["words"]:
            errors.append(f"sentence {curated.sentence_id} child mappings changed")
        if artifacts != item["artifacts"]:
            errors.append(f"sentence {curated.sentence_id} artifacts changed")
        before_fields = {
            field: item["before"][field]
            for field in (
                "arabic_text",
                "english_translation",
                "transliteration",
            )
        }
        after_fields = _desired_snapshot(curated)
        before_hashes[str(curated.sentence_id)] = _sha256_json(before_fields)
        after_hashes[str(curated.sentence_id)] = _sha256_json(after_fields)

    if _sha256_json(_excluded_scope_snapshot(db)) != plan.get(
        "excluded_scope_sha256"
    ):
        errors.append("excluded rows changed")
    after_invariants = _global_invariant_counts(db)
    if after_invariants != before_invariants:
        errors.append(
            f"global invariant counts changed: before={before_invariants}, "
            f"after={after_invariants}"
        )
    if errors:
        raise RuntimeError(
            "post-prefill invariant failure; rolling back:\n" + "\n".join(errors)
        )
    return before_hashes, after_hashes


def apply_prefill_plan(
    db,
    plan: dict[str, Any],
    *,
    plan_sha256: str,
    backup: dict[str, Any],
    git_commit: str,
    commit: bool = True,
) -> dict[str, Any]:
    """Apply one reviewed plan atomically after every live precondition passes."""
    db.commit()
    db.expire_all()
    try:
        if db.get_bind().dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        _validate_plan_header(db, plan, git_commit=git_commit)
        _validate_live_rows(db, plan)
        before_invariants = _global_invariant_counts(db)

        for curated in CURATED_ROWS:
            _apply_one_cas(db, curated)
        db.flush()
        db.expire_all()

        before_hashes, after_hashes = _verify_after(
            db,
            plan,
            before_invariants=before_invariants,
        )
        result = {
            "updated": len(APPROVED_IDS),
            "updated_ids": list(APPROVED_IDS),
            "excluded_ids_untouched": list(EXCLUDED_IDS),
            "fields_changed": [
                "arabic_text",
                "english_translation",
                "transliteration",
            ],
            "plan_sha256": plan_sha256,
            "manifest_sha256": MANIFEST_SHA256,
            "git_commit": git_commit,
            "script_sha256": _script_sha256(),
            "backup": backup,
            "before_field_hashes": before_hashes,
            "after_field_hashes": after_hashes,
            "invariant_counts": before_invariants,
        }
        db.add(
            ActivityLog(
                event_type="momo_ch1_curated_prefill",
                summary=(
                    "Prefilled reviewed Arabic, English, and transliteration "
                    "for exactly seven inactive Momo Chapter 1 rows"
                ),
                detail_json={
                    **result,
                    "script": (
                        "prefill_momo_ch1_curated_2026_07_30.py"
                    ),
                    "plan_version": PLAN_VERSION,
                    "manifest_version": MANIFEST_VERSION,
                    "source_projection_validated": len(APPROVED_IDS),
                    "sentence_activation_changed": 0,
                    "mapping_verification_stamps_changed": 0,
                    "quality_review_stamps_changed": 0,
                    "target_bookkeeping_changed": 0,
                    "sentence_words_changed": 0,
                    "lemmas_changed": 0,
                    "user_lemma_knowledge_changed": 0,
                    "review_history_changed": 0,
                    "word_review_evidence_changed": 0,
                },
            )
        )
        db.flush()
        if commit:
            db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def _plan_bytes(plan: dict[str, Any]) -> bytes:
    return _canonical_json_bytes(plan, pretty=True)


def _load_hashed_plan(
    plan_file: Path,
    expected_plan_sha256: str,
) -> tuple[dict[str, Any], str]:
    raw = plan_file.read_bytes()
    observed = _sha256_bytes(raw)
    if observed != expected_plan_sha256:
        raise RuntimeError(
            "reviewed plan hash mismatch: "
            f"expected {expected_plan_sha256}, got {observed}"
        )
    return json.loads(raw), observed


def _write_plan_file(
    plan_file: Path,
    raw: bytes,
    *,
    target_database_path: str,
) -> Path:
    """Create a new private plan without following or overwriting any path."""
    expanded = plan_file.expanduser()
    if expanded.is_symlink():
        raise RuntimeError(f"plan path may not be a symlink: {expanded}")
    try:
        parent = expanded.parent.resolve(strict=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"plan parent directory does not exist: {expanded.parent}"
        ) from exc
    resolved = parent / expanded.name
    database = Path(target_database_path).expanduser().resolve()
    prohibited = {
        database,
        Path(str(database) + "-wal"),
        Path(str(database) + "-shm"),
        Path(str(database) + "-journal"),
    }
    if resolved in prohibited:
        raise RuntimeError("plan path may not be the live database or a sidecar")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(resolved, flags, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(
            f"refusing to overwrite existing plan path: {resolved}"
        ) from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            resolved.unlink()
        except OSError:
            pass
        raise
    return resolved


def _print_plan_summary(plan: dict[str, Any], plan_sha256: str | None) -> None:
    print(
        json.dumps(
            {
                "mode": "plan" if plan_sha256 else "dry_run",
                "database_path": plan["database_path"],
                "git_commit": plan["git_commit"],
                "script_sha256": plan["script_sha256"],
                "manifest_sha256": plan["manifest_sha256"],
                "plan_sha256": plan_sha256,
                "approved_ids": plan["scope"]["sentence_ids"],
                "excluded_ids": plan["scope"]["excluded_ids"],
                "eligible": plan["update_count"],
                "writes": 0,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--plan",
        action="store_true",
        help="write the exact read-only plan to --plan-file",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="apply an existing reviewed plan atomically",
    )
    parser.add_argument(
        "--plan-file",
        type=Path,
        default=DEFAULT_PLAN_FILE,
        help=f"plan path (default: {DEFAULT_PLAN_FILE})",
    )
    parser.add_argument(
        "--expected-plan-sha256",
        help="required with --apply; exact reviewed plan-file hash",
    )
    parser.add_argument(
        "--expected-manifest-sha256",
        help="required with --apply; exact reviewed code-manifest hash",
    )
    parser.add_argument(
        "--backup-path",
        type=Path,
        help="required with --apply; fresh online SQLite backup",
    )
    parser.add_argument(
        "--backup-sha256",
        help="required with --apply; SHA-256 of --backup-path",
    )
    args = parser.parse_args()

    if args.apply and os.environ.get("ALIF_CORPUS_ENRICH_PROVIDER"):
        parser.error(
            "--apply requires ALIF_CORPUS_ENRICH_PROVIDER to be unset"
        )

    lock_handle = None
    db = None
    try:
        code_commit = _git_commit()
        if args.apply:
            required = {
                "--expected-plan-sha256": args.expected_plan_sha256,
                "--expected-manifest-sha256": (
                    args.expected_manifest_sha256
                ),
                "--backup-path": args.backup_path,
                "--backup-sha256": args.backup_sha256,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                parser.error("--apply requires " + ", ".join(missing))
            if args.expected_manifest_sha256 != validate_manifest():
                raise RuntimeError(
                    "explicit manifest hash does not match the approved manifest"
                )
            plan, plan_sha256 = _load_hashed_plan(
                args.plan_file,
                args.expected_plan_sha256,
            )
            db = SessionLocal()
            lock_handle = _try_acquire_material_update_lock()
            if lock_handle is None:
                raise RuntimeError(
                    "another material writer holds "
                    f"{MATERIAL_UPDATE_LOCK}; no rows were changed"
                )
            backup = inspect_backup(
                args.backup_path,
                args.backup_sha256,
                target_database_path=_database_path(db),
            )
            result = apply_prefill_plan(
                db,
                plan,
                plan_sha256=plan_sha256,
                backup=backup,
                git_commit=code_commit,
                commit=True,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return

        db = SessionLocal()
        plan = build_prefill_plan(db, git_commit=code_commit)
        if args.plan:
            raw = _plan_bytes(plan)
            _write_plan_file(
                args.plan_file,
                raw,
                target_database_path=plan["database_path"],
            )
            plan_sha256 = _sha256_bytes(raw)
            _print_plan_summary(plan, plan_sha256)
        else:
            _print_plan_summary(plan, None)
    finally:
        if db is not None:
            db.close()
        if lock_handle is not None:
            _release_material_update_lock(lock_handle)


if __name__ == "__main__":
    main()
