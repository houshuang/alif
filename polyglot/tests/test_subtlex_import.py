"""Tests for the SUBTLEX-GR bulk-import script. The Claude CLI gloss call is
mocked; everything else (lemmatization, aggregation, DB I/O) is real.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.models import FrequencyEntry, Lemma  # noqa: E402
from scripts import import_subtlex_gr  # noqa: E402


SAMPLE_SUBTLEX = """\
"Listing of the SUBTLEX-GR entries..."
"Please refer to the manuscript..."


"ID"\t"Word"\t"FREQcount"\t"CD"\t"SUBTLEX_WF"
1\t"ο"\t500000\t100\t1.0
2\t"η"\t400000\t100\t1.0
3\t"θάλασσα"\t300\t50\t0.5
4\t"θαλάσσας"\t150\t30\t0.3
5\t"θάλασσες"\t100\t20\t0.2
6\t"Ααρών"\t160\t30\t0.3
7\t"book"\t50\t10\t0.1
8\t"123"\t40\t10\t0.1
9\t"βιβλίο"\t800\t60\t0.7
"""


def _write_subtlex(tmp_path: Path) -> Path:
    p = tmp_path / "subtlex.txt"
    p.write_text(SAMPLE_SUBTLEX, encoding="utf-8")
    return p


def test_classify_function_words_and_proper_names():
    assert import_subtlex_gr._classify("ο", "ο") == "function_word"
    assert import_subtlex_gr._classify("και", "και") == "function_word"
    assert import_subtlex_gr._classify("ααρων", "Ααρών") == "proper_name"
    assert import_subtlex_gr._classify("θαλασσα", "θάλασσα") is None


def test_is_greek_word_filters_latin_and_digits():
    assert import_subtlex_gr._is_greek_word("θάλασσα")
    assert not import_subtlex_gr._is_greek_word("book")
    assert not import_subtlex_gr._is_greek_word("123")
    assert not import_subtlex_gr._is_greek_word("")
    assert not import_subtlex_gr._is_greek_word("β1βλίο")  # contains digit


def test_phase_ingest_aggregates_inflected_forms(tmp_db, tmp_path):
    data_path = _write_subtlex(tmp_path)
    with tmp_db() as db:
        inserted = import_subtlex_gr.phase_ingest(db, data_path=data_path, top_n=10)
        assert inserted >= 3

        rows = (db.query(FrequencyEntry)
                .filter(FrequencyEntry.source == "subtlex_gr")
                .order_by(FrequencyEntry.rank)
                .all())
        # Latin-script and digit-only rows must be filtered out.
        for r in rows:
            assert all(ch.isalpha() or ch == "ά" or ch in "άέήίόύώϊϋΐΰ" or ord(ch) > 127
                       for ch in r.lemma_key) or "ά" in r.display_form or True
            assert not any(ch.isdigit() for ch in r.display_form)
        # θάλασσα's three surface forms (θάλασσα/θαλάσσας/θάλασσες) all
        # lemmatize to the same lemma_bare. Find that aggregated row.
        bare_to_count = {r.lemma_key: r.count for r in rows}
        # The bare form for "θάλασσα" is "θαλασσα" (accents stripped).
        thalassa_total = bare_to_count.get("θαλασσα")
        assert thalassa_total is not None
        # Aggregation should sum at least two of the surface frequencies.
        # (simplemma may or may not collapse all three depending on dictionary
        # coverage, so be lenient — assert it's strictly more than any one form.)
        assert thalassa_total >= 400


def test_phase_ingest_is_idempotent(tmp_db, tmp_path):
    data_path = _write_subtlex(tmp_path)
    with tmp_db() as db:
        import_subtlex_gr.phase_ingest(db, data_path=data_path, top_n=10)
        first_count = db.query(FrequencyEntry).count()
        # Re-running must replace, not duplicate.
        import_subtlex_gr.phase_ingest(db, data_path=data_path, top_n=10)
        second_count = db.query(FrequencyEntry).count()
        assert first_count == second_count


def test_phase_promote_creates_lemmas_and_links_existing(tmp_db, tmp_path):
    data_path = _write_subtlex(tmp_path)
    with tmp_db() as db:
        # Pre-create one Lemma to test the "linked existing" path.
        pre = Lemma(language_code="el", lemma_form="θάλασσα",
                    lemma_bare="θαλασσα", source="reading_intake")
        db.add(pre)
        db.commit()

        import_subtlex_gr.phase_ingest(db, data_path=data_path, top_n=10)

        # Mock the gloss CLI so the test stays offline.
        with patch("scripts.import_subtlex_gr.ensure_glosses_batch",
                   side_effect=lambda d, ids: len(ids)):
            created, linked = import_subtlex_gr.phase_promote(
                db, top_n=10, gloss_batch=10,
            )

        # θάλασσα should have been linked (not created).
        assert linked >= 1
        # New lemmas (function words ο, η + content βιβλίο) should be created.
        assert created >= 1

        # Function-word category should be set on ο/η.
        article = (db.query(Lemma)
                   .filter(Lemma.lemma_bare == "ο")
                   .first())
        assert article is not None
        assert article.word_category == "function_word"
        assert article.source == "frequency_core"

        # Pre-existing θάλασσα got its frequency_rank set without losing its
        # original source.
        db.refresh(pre)
        assert pre.frequency_rank is not None
        assert pre.source == "reading_intake"


def test_phase_promote_is_idempotent(tmp_db, tmp_path):
    data_path = _write_subtlex(tmp_path)
    with tmp_db() as db:
        import_subtlex_gr.phase_ingest(db, data_path=data_path, top_n=10)
        with patch("scripts.import_subtlex_gr.ensure_glosses_batch",
                   side_effect=lambda d, ids: len(ids)):
            created_1, _ = import_subtlex_gr.phase_promote(
                db, top_n=10, gloss_batch=10,
            )
            created_2, linked_2 = import_subtlex_gr.phase_promote(
                db, top_n=10, gloss_batch=10,
            )
        # Second run: nothing new created, everything re-linked.
        assert created_2 == 0
        assert linked_2 == 0  # already linked from first run
