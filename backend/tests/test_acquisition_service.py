"""Tests for acquisition_service — recovery-trigger counting and the daily intro cap.

First dedicated test file for this service, added after two production incidents:
- 2026-06-03: a bulk script bypassed the daily cap (227 promotions in one batch);
- 2026-06-10: unreviewable rows (proper names, generation-backed-off words) pinned
  the recovery trigger over its limit for weeks.
"""

from datetime import datetime, timedelta, timezone

from app.models import Lemma, ReviewLog, Sentence, SentenceReviewLog, UserLemmaKnowledge
from app.services.acquisition_service import (
    ACQUISITION_EPISODE_LEECH_REINTRO,
    ACQUISITION_EPISODE_NEW,
    DAILY_INTRO_CAP,
    RECOVERY_MID_INTRO_BUDGET,
    RECOVERY_MIN_SENTENCES_FOR_ANY_INTRO,
    _daily_intro_count,
    _recovery_backlog_counts,
    _recovery_mode_intro_budget,
    start_acquisition,
)


def _lemma(db, arabic, category=None, gloss="word"):
    lemma = Lemma(
        lemma_ar=arabic,
        lemma_ar_bare=arabic,
        gloss_en=gloss,
        pos="noun",
        word_category=category,
    )
    db.add(lemma)
    db.flush()
    return lemma


def _acquiring(db, lemma, box=1, times_seen=0, backoff_until=None, next_due=None):
    ulk = UserLemmaKnowledge(
        lemma_id=lemma.lemma_id,
        knowledge_state="acquiring",
        acquisition_box=box,
        times_seen=times_seen,
        acquisition_next_due=next_due,
        generation_backoff_until=backoff_until,
    )
    db.add(ulk)
    db.flush()
    return ulk


# --- _recovery_backlog_counts ---


def test_box1_counts_normal_unseen_word(db_session):
    _acquiring(db_session, _lemma(db_session, "كلمة"))
    box1, _ = _recovery_backlog_counts(db_session, datetime.now(timezone.utc))
    assert box1 == 1


def test_box1_excludes_proper_name(db_session):
    _acquiring(db_session, _lemma(db_session, "ثمينه", category="proper_name"))
    _acquiring(db_session, _lemma(db_session, "بوم", category="onomatopoeia"))
    _acquiring(db_session, _lemma(db_session, "كلمة"))
    box1, _ = _recovery_backlog_counts(db_session, datetime.now(timezone.utc))
    assert box1 == 1


def test_box1_excludes_generation_backed_off(db_session):
    now = datetime.now(timezone.utc)
    _acquiring(db_session, _lemma(db_session, "زرّ"), backoff_until=now + timedelta(days=7))
    _acquiring(db_session, _lemma(db_session, "كلمة"))
    box1, _ = _recovery_backlog_counts(db_session, now)
    assert box1 == 1


def test_box1_counts_word_with_expired_backoff(db_session):
    now = datetime.now(timezone.utc)
    _acquiring(db_session, _lemma(db_session, "كلمة"), backoff_until=now - timedelta(hours=1))
    box1, _ = _recovery_backlog_counts(db_session, now)
    assert box1 == 1


def test_box1_counts_seen_due_word_but_not_seen_future_word(db_session):
    """Failed/reintroduced Box-1 words remain recovery debt after first review."""
    now = datetime.now(timezone.utc)
    _acquiring(
        db_session,
        _lemma(db_session, "مستحق"),
        times_seen=8,
        next_due=now - timedelta(minutes=1),
    )
    _acquiring(
        db_session,
        _lemma(db_session, "لاحق"),
        times_seen=8,
        next_due=now + timedelta(hours=1),
    )

    box1, _ = _recovery_backlog_counts(db_session, now)
    assert box1 == 1


def test_box2_due_excludes_proper_name(db_session):
    now = datetime.now(timezone.utc)
    overdue = now - timedelta(hours=2)
    _acquiring(db_session, _lemma(db_session, "اسم", category="proper_name"),
               box=2, times_seen=3, next_due=overdue)
    _acquiring(db_session, _lemma(db_session, "كلمة"), box=2, times_seen=3, next_due=overdue)
    _, box2 = _recovery_backlog_counts(db_session, now)
    assert box2 == 1


def test_box2_due_still_counts_backed_off_word(db_session):
    # Box-2 words were served at least once — their practice debt is real even
    # while sentence generation is backing off.
    now = datetime.now(timezone.utc)
    _acquiring(db_session, _lemma(db_session, "كلمة"), box=2, times_seen=3,
               next_due=now - timedelta(hours=2), backoff_until=now + timedelta(days=7))
    _, box2 = _recovery_backlog_counts(db_session, now)
    assert box2 == 1


# --- daily intro cap ---


def _promote(db, lemma_id, **kwargs):
    """start_acquisition + mark the word as seen so unseen-box-1 debt doesn't
    trip the recovery trigger (limit 5) — these tests isolate the daily cap."""
    ulk = start_acquisition(db, lemma_id, **kwargs)
    if ulk.knowledge_state == "acquiring":
        ulk.times_seen = 1
        db.flush()
    return ulk


def test_daily_intro_cap_defers_promotion(db_session):
    for i in range(DAILY_INTRO_CAP):
        ulk = _promote(db_session, _lemma(db_session, f"كلمة{i}").lemma_id)
        assert ulk.knowledge_state == "acquiring"

    over_cap = _promote(db_session, _lemma(db_session, "زيادة").lemma_id)
    assert over_cap.knowledge_state == "encountered"


def test_daily_intro_cap_bypass_flag(db_session):
    for i in range(DAILY_INTRO_CAP):
        _promote(db_session, _lemma(db_session, f"كلمة{i}").lemma_id)

    bypassed = _promote(
        db_session, _lemma(db_session, "زيادة").lemma_id, enforce_daily_cap=False
    )
    assert bypassed.knowledge_state == "acquiring"


def test_daily_intro_count_uses_episode_kind_and_legacy_fallback(db_session):
    """A book-sourced leech restart is not a new word despite its provenance."""
    now = datetime.now(timezone.utc)
    rows = [
        UserLemmaKnowledge(
            lemma_id=_lemma(db_session, "جديد").lemma_id,
            knowledge_state="acquiring",
            acquisition_started_at=now,
            acquisition_episode_kind=ACQUISITION_EPISODE_NEW,
            source="book",
        ),
        UserLemmaKnowledge(
            lemma_id=_lemma(db_session, "معاد").lemma_id,
            knowledge_state="acquiring",
            acquisition_started_at=now,
            acquisition_episode_kind=ACQUISITION_EPISODE_LEECH_REINTRO,
            source="book",
        ),
        # Historical fallback: episode kind did not exist when this row started.
        UserLemmaKnowledge(
            lemma_id=_lemma(db_session, "قديم").lemma_id,
            knowledge_state="acquiring",
            acquisition_started_at=now,
            acquisition_episode_kind=None,
            source="leech_reintro",
        ),
        # A first suspension increments leech_count without replacing the
        # original acquisition_started_at. It must not retroactively erase a
        # legitimate new-word start from the cap.
        UserLemmaKnowledge(
            lemma_id=_lemma(db_session, "أصلي").lemma_id,
            knowledge_state="suspended",
            acquisition_started_at=now,
            acquisition_episode_kind=None,
            source="book",
            leech_count=1,
        ),
    ]
    db_session.add_all(rows)
    db_session.flush()

    assert _daily_intro_count(
        db_session,
        now.replace(hour=0, minute=0, second=0, microsecond=0),
    ) == 2


def test_recovery_trigger_throttles_in_cold_db(db_session):
    # With no sentence practice today, promotions stop once unseen box-1 debt
    # reaches the recovery limit — well before the daily cap.
    promoted = 0
    for i in range(DAILY_INTRO_CAP):
        ulk = start_acquisition(db_session, _lemma(db_session, f"كلمة{i}").lemma_id)
        if ulk.knowledge_state == "acquiring":
            promoted += 1
    assert promoted < DAILY_INTRO_CAP


def _add_primary_reading_reviews(db, ratings):
    lemma = _lemma(db, f"مراجعة{db.query(ReviewLog).count()}")
    now = datetime.now(timezone.utc)
    for rating in ratings:
        db.add(ReviewLog(
            lemma_id=lemma.lemma_id,
            rating=rating,
            reviewed_at=now,
            review_mode="reading",
            credit_type="primary",
        ))
    db.flush()


def _activate_recovery(db, now):
    for i in range(5):
        _acquiring(
            db,
            _lemma(db, f"دين{i}"),
            box=1,
            times_seen=5,
            next_due=now - timedelta(hours=1),
        )


def test_recovery_accuracy_ignores_easy_collateral_reviews(db_session):
    now = datetime.now(timezone.utc)
    _activate_recovery(db_session, now)
    _add_primary_reading_reviews(db_session, [3] * 20 + [1] * 20)

    collateral_lemma = _lemma(db_session, "جانبي")
    for _ in range(200):
        db_session.add(ReviewLog(
            lemma_id=collateral_lemma.lemma_id,
            rating=3,
            reviewed_at=now,
            review_mode="reading",
            credit_type="collateral",
        ))
    db_session.flush()

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    assert _recovery_mode_intro_budget(db_session, now, today_start) == 0


def test_recovery_volume_counts_cards_not_passage_child_sentences(db_session):
    now = datetime.now(timezone.utc)
    _activate_recovery(db_session, now)
    _add_primary_reading_reviews(
        db_session,
        [3] * (RECOVERY_MIN_SENTENCES_FOR_ANY_INTRO - 1),
    )

    sentence = Sentence(arabic_text="فقرة", english_translation="passage")
    db_session.add(sentence)
    db_session.flush()
    # These emulate child rows written for the sentences inside one or more
    # passages. They are not additional answered cards.
    for i in range(100):
        db_session.add(SentenceReviewLog(
            sentence_id=sentence.id,
            comprehension="understood",
            reviewed_at=now,
            client_review_id=f"passage-child-{i}",
        ))
    db_session.flush()

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    assert _recovery_mode_intro_budget(db_session, now, today_start) == 0

    _add_primary_reading_reviews(db_session, [3])
    assert (
        _recovery_mode_intro_budget(db_session, now, today_start)
        == RECOVERY_MID_INTRO_BUDGET
    )
