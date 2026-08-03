import hashlib
import sqlite3

import pytest

from scripts.repair_momo_ch5_reader_mappings import (
    CONTEXT_GLOSSES,
    EXPECTED,
    EXPECTED_METADATA,
    EXPECTED_TITLE,
    EXPECTED_WORD_COUNT,
    EXISTING,
    NEW,
    STORY_ID,
    _new_positions,
    _validate_backup,
)


def _make_reviewed_backup(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE stories (
                id INTEGER PRIMARY KEY,
                title_ar TEXT NOT NULL
            );
            CREATE TABLE story_words (
                story_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                surface_form TEXT NOT NULL,
                lemma_id INTEGER,
                PRIMARY KEY (story_id, position)
            );
            CREATE TABLE lemmas (
                lemma_id INTEGER PRIMARY KEY,
                lemma_ar TEXT NOT NULL,
                gloss_en TEXT NOT NULL,
                pos TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO stories (id, title_ar) VALUES (?, ?)",
            (STORY_ID, EXPECTED_TITLE),
        )
        rows = [
            (STORY_ID, position, f"token-{position}", None)
            for position in range(EXPECTED_WORD_COUNT)
        ]
        for position, (surface, lemma_id) in EXPECTED.items():
            rows[position] = (STORY_ID, position, surface, lemma_id)
        conn.executemany(
            "INSERT INTO story_words "
            "(story_id, position, surface_form, lemma_id) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.executemany(
            "INSERT INTO lemmas (lemma_id, lemma_ar, gloss_en, pos) "
            "VALUES (?, ?, ?, ?)",
            [
                (lemma_id, lemma_ar, gloss, pos)
                for lemma_id, (lemma_ar, gloss, pos) in EXPECTED_METADATA.items()
            ],
        )


def test_repair_manifest_has_exact_disjoint_coverage():
    new_positions = [
        position
        for _arabic, _gloss, _pos, positions in NEW.values()
        for position in positions
    ]
    mapping_positions = set(EXISTING) | set(new_positions)

    assert len(EXPECTED) == 94
    assert len(NEW) == 47
    assert len(mapping_positions) == 83
    assert len(new_positions) == len(set(new_positions))
    assert set(EXISTING).isdisjoint(new_positions)
    assert set(new_positions) == _new_positions()
    assert mapping_positions <= set(EXPECTED)
    assert set(CONTEXT_GLOSSES) <= set(EXPECTED)
    assert set(EXPECTED_METADATA) == {
        214, 378, 443, 1725, 1794, 1812, 2354, 3471, 3498
    }
    assert all(0 <= position < EXPECTED_WORD_COUNT for position in EXPECTED)


def test_repair_manifest_new_identities_are_complete_and_unique():
    identities = [(arabic, gloss, pos) for arabic, gloss, pos, _positions in NEW.values()]

    assert len(identities) == len(set(identities))
    assert all(
        arabic.strip() and gloss.strip() and pos.strip()
        for arabic, gloss, pos in identities
    )
    assert all(positions for _arabic, _gloss, _pos, positions in NEW.values())


def test_validate_backup_accepts_only_exact_reviewed_preimage(tmp_path):
    backup = tmp_path / "momo-before.db"
    _make_reviewed_backup(backup)

    expected_digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    assert _validate_backup(backup) == expected_digest

    with sqlite3.connect(backup) as conn:
        conn.execute(
            "UPDATE story_words SET surface_form = ? "
            "WHERE story_id = ? AND position = ?",
            ("corrupted", STORY_ID, min(EXPECTED)),
        )

    with pytest.raises(RuntimeError, match="Backup preimage mismatch"):
        _validate_backup(backup)


def test_validate_backup_rejects_missing_story_word(tmp_path):
    backup = tmp_path / "momo-incomplete.db"
    _make_reviewed_backup(backup)

    with sqlite3.connect(backup) as conn:
        conn.execute(
            "DELETE FROM story_words WHERE story_id = ? AND position = ?",
            (STORY_ID, EXPECTED_WORD_COUNT - 1),
        )

    with pytest.raises(RuntimeError, match="does not contain the reviewed"):
        _validate_backup(backup)
