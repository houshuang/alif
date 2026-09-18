"""CAMeL rank map used by run_quality_gates() and the discover API."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import BASE_DIR
from app.services import lemma_quality


@pytest.fixture
def rank_file(tmp_path, monkeypatch):
    def write(rows: list[tuple[str, int]]) -> Path:
        path = tmp_path / "MSA_freq_lists.tsv"
        path.write_text("".join(f"{word}\t{count}\n" for word, count in rows), encoding="utf-8")
        monkeypatch.setattr(lemma_quality, "_CAMEL_CACHE", path)
        monkeypatch.setattr(lemma_quality, "_rank_map", None)
        return path

    yield write
    lemma_quality._rank_map = None


def test_frequency_file_lives_in_backend_data():
    assert lemma_quality._CAMEL_CACHE == Path(BASE_DIR) / "data" / "MSA_freq_lists.tsv"


def test_ranks_merge_alef_variants_and_stop_at_rare_counts(rank_file, monkeypatch):
    monkeypatch.setattr(lemma_quality, "_RANK_MAP_MIN_COUNT", 10)
    rank_file([
        ("في", 1000),
        ("أصبح", 400),
        ("كتاب", 300),
        ("اصبح", 250),   # merges with أصبح → 650, above كتاب
        ("نادر", 9),     # below the cutoff: never read
    ])

    ranks = lemma_quality._load_rank_map()

    assert ranks == {"في": 1, "اصبح": 2, "كتاب": 3}


def test_rank_map_keeps_only_the_head(rank_file, monkeypatch):
    monkeypatch.setattr(lemma_quality, "RANK_MAP_MAX_RANK", 2)
    rank_file([("في", 1000), ("من", 900), ("على", 800)])

    assert lemma_quality._load_rank_map() == {"في": 1, "من": 2}


def test_assign_frequency_rank_uses_the_map(rank_file):
    rank_file([("في", 1000), ("أصبح", 400), ("الكتاب", 300)])

    became = SimpleNamespace(lemma_ar_bare="أَصْبَحَ", frequency_rank=None)
    book = SimpleNamespace(lemma_ar_bare="كتاب", frequency_rank=None)  # found via ال
    unknown = SimpleNamespace(lemma_ar_bare="زعلان", frequency_rank=None)

    assert lemma_quality.assign_frequency_rank(became) and became.frequency_rank == 2
    assert lemma_quality.assign_frequency_rank(book) and book.frequency_rank == 3
    assert not lemma_quality.assign_frequency_rank(unknown) and unknown.frequency_rank is None


def test_missing_file_assigns_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(lemma_quality, "_CAMEL_CACHE", tmp_path / "absent.tsv")
    monkeypatch.setattr(lemma_quality, "_rank_map", None)

    lemma = SimpleNamespace(lemma_ar_bare="كتاب", frequency_rank=None)
    assert not lemma_quality.assign_frequency_rank(lemma)
    lemma_quality._rank_map = None


def test_backfill_fills_only_missing_ranks(db_session, rank_file):
    from app.models import ActivityLog, Lemma, UserLemmaKnowledge
    from scripts.backfill_missing_frequency_ranks import backfill_missing_frequency_ranks

    rank_file([("في", 1000), ("أصبح", 400), ("كتاب", 300)])
    became = Lemma(lemma_ar="أَصْبَحَ", lemma_ar_bare="أصبح", pos="verb", gloss_en="to become")
    ranked = Lemma(lemma_ar="كِتَاب", lemma_ar_bare="كتاب", pos="noun", gloss_en="book", frequency_rank=77)
    slang = Lemma(lemma_ar="زَعْلَان", lemma_ar_bare="زعلان", pos="adj", gloss_en="upset")
    db_session.add_all([became, ranked, slang])
    db_session.flush()
    db_session.add(UserLemmaKnowledge(lemma_id=became.lemma_id, knowledge_state="known"))
    db_session.commit()

    preview = backfill_missing_frequency_ranks(db_session, dry_run=True)
    db_session.refresh(became)
    assert preview["assigned"] == 1 and became.frequency_rank is None

    summary = backfill_missing_frequency_ranks(db_session, dry_run=False)
    db_session.refresh(became)
    db_session.refresh(ranked)
    db_session.refresh(slang)
    assert (became.frequency_rank, ranked.frequency_rank, slang.frequency_rank) == (2, 77, None)
    assert summary["active_bands"] == {"<=1k": 1}
    assert db_session.query(ActivityLog).filter_by(event_type="frequency_rank_backfill").count() == 1
