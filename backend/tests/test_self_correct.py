"""Smoke tests for the self-correcting sentence generator.

We can't unit-test the full CLI session here (requires `claude` binary +
auth + network). These tests verify the helpers that build the work_dir
inputs — vocab files, the validator script — using a temp DB.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.services.sentence_self_correct import (
    BATCH_SCHEMA,
    BATCH_SYSTEM_PROMPT,
    SCHEMA,
    SYSTEM_PROMPT,
    _load_active_lemma_rows,
    _write_batch_files,
    _write_validator_script,
    _write_vocab_files,
)


def _make_test_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE lemmas (
            lemma_id INTEGER PRIMARY KEY,
            lemma_ar TEXT, lemma_ar_bare TEXT, gloss_en TEXT, pos TEXT,
            forms_json TEXT, canonical_lemma_id INTEGER
        );
        CREATE TABLE user_lemma_knowledge (
            id INTEGER PRIMARY KEY,
            lemma_id INTEGER REFERENCES lemmas(lemma_id),
            knowledge_state TEXT
        );
        INSERT INTO lemmas VALUES
            (1, 'كِتَابٌ', 'كتاب', 'book', 'noun', NULL, NULL),
            (2, 'قَرَأَ', 'قرا', 'to read', 'verb', '{"present":"يَقْرَأُ"}', NULL),
            (3, 'كَبِيرٌ', 'كبير', 'big', 'adj', NULL, NULL),
            (4, 'بَيْتٌ', 'بيت', 'house', 'noun', NULL, NULL),
            (5, 'سَيَّارَةٌ', 'سيارة', 'car', 'noun', NULL, NULL);
        INSERT INTO user_lemma_knowledge (lemma_id, knowledge_state) VALUES
            (1, 'known'), (2, 'learning'), (3, 'known'),
            (4, 'acquiring'), (5, 'known');
    """)
    conn.commit()
    conn.close()


def test_load_active_lemma_rows_filters_states():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "test.db")
        _make_test_db(db)
        rows = _load_active_lemma_rows(db)
        assert len(rows) == 5
        states = {r["knowledge_state"] for r in rows}
        assert states == {"known", "learning", "acquiring"}


def test_write_vocab_files_skips_target_and_caps_sample():
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "test.db")
        _make_test_db(db)
        rows = _load_active_lemma_rows(db)

        work_dir = os.path.join(tmp, "work")
        _write_vocab_files(
            rows, work_dir,
            target_lemma_id=1,  # كِتَابٌ
            target_word="كِتَابٌ",
            target_translation="book",
            prompt_sample_size=10,
        )

        prompt = (Path(work_dir) / "vocab_prompt.txt").read_text()
        assert "TARGET WORD (must appear in every sentence): كِتَابٌ" in prompt
        # Target lemma's gloss should not appear as a supporting word
        # (it does appear in the TARGET line, but not in the POS groups).
        # Check by looking after the TARGET line.
        post_target = prompt.split("\n", 2)[2] if prompt.count("\n") >= 2 else ""
        assert "كِتَابٌ (book)" not in post_target

        # Acquiring lemma should be highlighted
        assert "CURRENTLY LEARNING" in prompt
        assert "بَيْتٌ" in prompt  # acquiring

        # Lookup TSV must contain ALL forms (not just the sampled subset)
        # so the validator can classify any word Sonnet writes.
        lookup_text = (Path(work_dir) / "vocab_lookup.tsv").read_text()
        for bare in ("كتاب", "قرا", "كبير", "بيت", "سيارة"):
            assert bare in lookup_text, f"missing {bare} in lookup"


def test_validator_script_executes_against_real_validator():
    """The validator wrapper script imports from the project; ensure it runs."""
    import subprocess
    import sys

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "test.db")
        _make_test_db(db)
        rows = _load_active_lemma_rows(db)

        work_dir = os.path.join(tmp, "work")
        _write_vocab_files(rows, work_dir, 1, "كِتَابٌ", "book")
        _write_validator_script(work_dir)

        result = subprocess.run(
            [sys.executable, os.path.join(work_dir, "validator.py"),
             "كِتَابٌ كَبِيرٌ", "كتاب"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"validator stderr: {result.stderr}"
        out = json.loads(result.stdout)
        assert out["valid"] is True, f"unexpected: {out}"
        assert out["target_found"] is True


def test_schema_is_well_formed():
    """Catch typos in the schema dict."""
    assert "sentences" in SCHEMA["properties"]
    item_schema = SCHEMA["properties"]["sentences"]["items"]
    assert {"arabic", "english", "transliteration"}.issubset(set(item_schema["required"]))
    # System prompt should mention the validator
    assert "validator.py" in SYSTEM_PROMPT


def test_batch_schema_is_well_formed():
    """Batch output schema groups sentences under `results[].target_lemma_id`."""
    assert "results" in BATCH_SCHEMA["properties"]
    results_schema = BATCH_SCHEMA["properties"]["results"]["items"]
    assert {"target_lemma_id", "sentences"}.issubset(set(results_schema["required"]))
    assert "validator.py" in BATCH_SYSTEM_PROMPT
    # Anti-anaphor guard — the rules A-E fix caused the 67%→95% quality jump
    # and must not be silently dropped in a future prompt refactor.
    assert "3rd-person subject" in BATCH_SYSTEM_PROMPT or "3rd-person" in BATCH_SYSTEM_PROMPT
    assert "bare definite" in BATCH_SYSTEM_PROMPT.lower() or "definite-article" in BATCH_SYSTEM_PROMPT


def test_write_batch_files_excludes_all_targets_and_emits_targets_json():
    """Multi-target batch must keep ALL target lemmas out of supporting vocab."""
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "test.db")
        _make_test_db(db)
        rows = _load_active_lemma_rows(db)

        work_dir = os.path.join(tmp, "work")
        targets = [
            {
                "target_lemma_id": 1, "target_word": "كِتَابٌ",
                "target_bare": "كتاب", "target_translation": "book",
                "example_ar": "", "example_en": "",
            },
            {
                "target_lemma_id": 2, "target_word": "قَرَأَ",
                "target_bare": "قرا", "target_translation": "to read",
                "example_ar": "", "example_en": "",
            },
        ]
        _write_batch_files(rows, work_dir, targets, prompt_sample_size=10)

        prompt = (Path(work_dir) / "vocab_prompt.txt").read_text()
        # Neither target word's gloss line should appear as supporting vocab.
        assert "كِتَابٌ (book)" not in prompt
        assert "قَرَأَ (to read)" not in prompt

        # targets.json is the source Sonnet reads per-target metadata from.
        targets_written = json.loads((Path(work_dir) / "targets.json").read_text())
        assert len(targets_written) == 2
        assert {t["target_lemma_id"] for t in targets_written} == {1, 2}

        # Lookup TSV still contains ALL forms so the validator sees every word.
        lookup_text = (Path(work_dir) / "vocab_lookup.tsv").read_text()
        for bare in ("كتاب", "قرا", "كبير", "بيت", "سيارة"):
            assert bare in lookup_text
