"""Novel sprint: reading targets steer intake without touching memory state.

Covers the three seams added on 2026-10-04:
- learning_policy: the sprint switch raises the daily budget and shortens the
  recovery ladder, but only on top of the maintenance package;
- word_selector: an open target is the top priority tier, ordered by in-text count;
- automatic_attention: a targeted rare word stays `maintain` instead of being
  staged as reading support, and a retired target stops protecting it.
"""
import importlib
from datetime import datetime, timezone

from app.models import Lemma, ReadingTarget, UserLemmaKnowledge
from app.services import learning_policy
from app.services.automatic_attention import refresh_attention
from app.services.word_selector import select_next_words
from tests.test_automatic_attention import NOW, word


def _lemma(db, arabic, gloss, freq=None):
    lemma = Lemma(lemma_ar=arabic, lemma_ar_bare=arabic, gloss_en=gloss, pos="noun",
                  frequency_rank=freq, gates_completed_at=datetime.now(timezone.utc))
    db.add(lemma)
    db.flush()
    return lemma


def _target(db, lemma_id, count, program="rijal_fi_al_shams", chapter=1):
    db.add(ReadingTarget(lemma_id=lemma_id, program=program, chapter=chapter, text_count=count))
    db.flush()


def test_sprint_policy_is_layered_on_maintenance(monkeypatch):
    monkeypatch.setenv(learning_policy.LOW_ENERGY_MAINTENANCE_ENV, "1")
    monkeypatch.setenv(learning_policy.NOVEL_SPRINT_ENV, "1")
    assert learning_policy.novel_sprint_enabled()
    assert learning_policy.active_daily_intro_cap() == 8
    assert learning_policy.active_recovery_ladder() == (20, 60, 4)
    assert learning_policy.active_learning_policy_version() == "novel_sprint_v1"

    monkeypatch.setenv(learning_policy.NOVEL_SPRINT_ENV, "0")
    assert learning_policy.active_daily_intro_cap() == 2
    assert learning_policy.active_recovery_ladder() == (40, 100, 1)
    assert learning_policy.active_learning_policy_version() == "low_energy_maintenance_v1"

    # Without the maintenance package the sprint switch is ignored: legacy ladder.
    monkeypatch.setenv(learning_policy.LOW_ENERGY_MAINTENANCE_ENV, "0")
    monkeypatch.setenv(learning_policy.NOVEL_SPRINT_ENV, "1")
    assert not learning_policy.novel_sprint_enabled()
    assert learning_policy.active_daily_intro_cap() == 30
    assert learning_policy.active_recovery_ladder() == (40, 100, 8)
    assert learning_policy.active_learning_policy_version() == "legacy"


def test_acquisition_constants_follow_the_sprint_ladder(monkeypatch):
    monkeypatch.setenv(learning_policy.LOW_ENERGY_MAINTENANCE_ENV, "1")
    monkeypatch.setenv(learning_policy.NOVEL_SPRINT_ENV, "1")
    import app.services.acquisition_service as acq
    try:
        importlib.reload(acq)
        assert acq.DAILY_INTRO_CAP == 8
        assert acq.RECOVERY_MIN_SENTENCES_FOR_ANY_INTRO == 20
        assert acq.RECOVERY_MIN_SENTENCES_FOR_FULL_BUDGET == 60
        assert acq.RECOVERY_MID_INTRO_BUDGET == 4
        assert acq.RECOVERY_FULL_INTRO_BUDGET == 8
    finally:
        monkeypatch.delenv(learning_policy.NOVEL_SPRINT_ENV, raising=False)
        importlib.reload(acq)
        assert acq.DAILY_INTRO_CAP == 2


def test_open_target_is_the_top_intake_tier_ordered_by_text_count(db_session):
    db = db_session
    common = _lemma(db, "كتاب", "book", freq=10)          # strong frequency prior
    rare_a = _lemma(db, "شط", "riverbank", freq=37635)
    rare_b = _lemma(db, "غصة", "lump in the throat", freq=66995)
    _target(db, rare_a.lemma_id, count=8)
    _target(db, rare_b.lemma_id, count=3)
    db.commit()

    result = select_next_words(db, count=5)
    ids = [r["lemma_id"] for r in result]
    assert ids[:2] == [rare_a.lemma_id, rare_b.lemma_id]
    assert ids[2] == common.lemma_id
    assert result[0]["score_breakdown"]["priority_tier"] == "reading_target"
    assert result[1]["score_breakdown"]["priority_tier"] == "reading_target"
    assert result[2]["score_breakdown"]["priority_tier"] != "reading_target"


def test_equal_count_targets_prefer_the_earlier_chapter(db_session):
    db = db_session
    later = _lemma(db, "غسق", "dusk", freq=50000)
    earlier = _lemma(db, "وهج", "glow", freq=50000)
    _target(db, later.lemma_id, count=1, chapter=2)
    _target(db, earlier.lemma_id, count=1, chapter=1)
    # A more frequent word in a later chapter still beats a one-off in chapter 1.
    frequent_later = _lemma(db, "تزوج", "to marry", freq=50000)
    _target(db, frequent_later.lemma_id, count=5, chapter=2)
    db.commit()
    ids = [r["lemma_id"] for r in select_next_words(db, count=5)]
    assert ids == [frequent_later.lemma_id, earlier.lemma_id, later.lemma_id]


def test_retired_target_no_longer_steers_selection(db_session):
    db = db_session
    common = _lemma(db, "كتاب", "book", freq=10)
    rare = _lemma(db, "شط", "riverbank", freq=37635)
    _target(db, rare.lemma_id, count=8)
    db.query(ReadingTarget).update({ReadingTarget.retired_at: datetime.now(timezone.utc)})
    db.commit()
    result = select_next_words(db, count=5)
    assert result[0]["lemma_id"] == common.lemma_id
    assert all(r["score_breakdown"]["priority_tier"] != "reading_target" for r in result)


def test_targeted_rare_word_stays_maintained_by_attention_refresh(db_session):
    db = db_session
    lemma = word(db, rank=60000, with_card=False)   # rare, never introduced
    db.add(UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state="encountered",
                              source="novel_sprint"))
    _target(db, lemma.lemma_id, count=5)
    db.commit()

    refresh_attention(db, now=NOW)
    k = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).one()
    assert k.attention_disposition == "maintain"
    assert k.attention_reason.endswith(":reading_target")
    assert k.knowledge_state == "encountered" and k.fsrs_card_json is None

    # Retiring the target hands the word back to the ordinary rules, which stage
    # a rare, never-introduced word as reading support.
    db.query(ReadingTarget).update({ReadingTarget.retired_at: NOW})
    db.commit()
    refresh_attention(db, now=NOW)
    assert k.attention_disposition == "reading_support"


def test_target_row_never_creates_cards_or_knowledge_by_itself(db_session):
    db = db_session
    lemma = _lemma(db, "شط", "riverbank", freq=37635)
    _target(db, lemma.lemma_id, count=8)
    db.commit()
    refresh_attention(db, now=NOW)
    k = db.query(UserLemmaKnowledge).filter_by(lemma_id=lemma.lemma_id).first()
    # The refresh may stage an encountered row for a targeted word, but never a card.
    assert k is None or (k.knowledge_state == "encountered" and k.fsrs_card_json is None)
