"""Debt-breakdown stats section (2026-07-25).

Splits the FSRS due stock into urgent / mid / mature by stability, counts the
untouched 14-day tail, and reports the 7-day backlog trend so the stats panel
shows direction of travel instead of one immovable number.
"""

import json
from datetime import datetime, timedelta, timezone

from app.models import Lemma, ReviewLog, UserLemmaKnowledge
from app.routers.stats import _get_debt_breakdown


def _due_word(db, arabic, state="known", stability=50.0, days_overdue=5):
    lemma = Lemma(lemma_ar=arabic, lemma_ar_bare=arabic, gloss_en="w", pos="noun")
    db.add(lemma)
    db.flush()
    due = datetime.now(timezone.utc) - timedelta(days=days_overdue)
    db.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state=state,
        fsrs_card_json={"due": due.isoformat(), "stability": stability},
    ))
    db.flush()
    return lemma


def test_buckets_are_exclusive_and_complete(db_session):
    _due_word(db_session, "كتاب", state="known", stability=45.0)     # mature
    _due_word(db_session, "قلم", state="known", stability=15.0)      # mid
    _due_word(db_session, "باب", state="known", stability=2.0)       # urgent (low stability)
    _due_word(db_session, "شمس", state="lapsed", stability=90.0)     # urgent (lapsed trumps stability)

    out = _get_debt_breakdown(db_session, datetime.now(timezone.utc))
    assert out.fsrs_due_total == 4
    assert out.urgent == 2
    assert out.mid == 1
    assert out.mature == 1
    assert out.urgent + out.mid + out.mature == out.fsrs_due_total


def test_untouched_counts_only_unreviewed(db_session):
    a = _due_word(db_session, "كتاب")
    _due_word(db_session, "قلم")
    db_session.add(ReviewLog(
        lemma_id=a.lemma_id, rating=3,
        reviewed_at=datetime.now(timezone.utc) - timedelta(days=3),
        review_mode="reading",
    ))
    db_session.flush()

    out = _get_debt_breakdown(db_session, datetime.now(timezone.utc))
    assert out.fsrs_due_total == 2
    assert out.untouched_14d == 1


def test_function_words_excluded(db_session):
    import app.routers.stats as stats_mod

    _due_word(db_session, "في")  # function word
    _due_word(db_session, "كتاب")
    stats_mod._func_word_ids_cache = None  # module cache persists across tests
    try:
        out = _get_debt_breakdown(db_session, datetime.now(timezone.utc))
    finally:
        stats_mod._func_word_ids_cache = None
    assert out.fsrs_due_total == 1


def test_not_due_words_ignored(db_session):
    lemma = Lemma(lemma_ar="غد", lemma_ar_bare="غد", gloss_en="w", pos="noun")
    db_session.add(lemma)
    db_session.flush()
    future = datetime.now(timezone.utc) + timedelta(days=3)
    db_session.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="known",
        fsrs_card_json={"due": future.isoformat(), "stability": 10.0},
    ))
    db_session.flush()
    out = _get_debt_breakdown(db_session, datetime.now(timezone.utc))
    assert out.fsrs_due_total == 0
