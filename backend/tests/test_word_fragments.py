"""fragment_retest_v1: short phrases as the same-day retry after a miss."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models import (
    Lemma,
    ReviewLog,
    Sentence,
    UserLemmaKnowledge,
    WordFragment,
    WordRetestAssignment,
)
from app.services import word_fragments as wf


def _lemma(db, lemma_id, arabic, bare, english, state="known", pos="noun"):
    db.add(Lemma(
        lemma_id=lemma_id, lemma_ar=arabic, lemma_ar_bare=bare, gloss_en=english,
        pos=pos, gates_completed_at=datetime.now(timezone.utc),
    ))
    db.add(UserLemmaKnowledge(
        lemma_id=lemma_id, knowledge_state=state, source="study",
        fsrs_card_json=json.dumps({"due": datetime.now(timezone.utc).isoformat(), "stability": 40.0}),
    ))


def _miss(db, lemma_id, minutes_ago=10, rating=1):
    sentence = Sentence(arabic_text="x", english_translation="x", target_lemma_id=lemma_id)
    db.add(sentence)
    db.flush()
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    db.add(ReviewLog(
        lemma_id=lemma_id, rating=rating, review_mode="reading", is_acquisition=False,
        sentence_id=sentence.id, reviewed_at=when, fsrs_log_json={},
    ))
    db.flush()
    return when


def _miss_in_arm(db, lemma_id, arm):
    """Seed misses until the hash lands in the wanted arm."""
    for minutes in range(10, 400, 7):
        when = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        if wf.retest_arm(lemma_id, when) == arm:
            sentence = Sentence(arabic_text="x", english_translation="x", target_lemma_id=lemma_id)
            db.add(sentence)
            db.flush()
            db.add(ReviewLog(
                lemma_id=lemma_id, rating=1, review_mode="reading", is_acquisition=False,
                sentence_id=sentence.id, reviewed_at=when, fsrs_log_json={},
            ))
            db.commit()
            return when
    raise AssertionError("no timestamp found for arm")


def test_arm_is_stable_and_both_arms_occur():
    base = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    arms = [wf.retest_arm(7, base + timedelta(minutes=i)) for i in range(200)]
    assert arms == [wf.retest_arm(7, base + timedelta(minutes=i)) for i in range(200)]
    assert 60 < arms.count(wf.FRAGMENT_ARM) < 140


def test_word_card_arm_never_gets_a_fragment(db_session):
    _lemma(db_session, 1, "كِتَابٌ", "كتاب", "book")
    _miss_in_arm(db_session, 1, wf.WORD_CARD_ARM)
    db_session.add(WordFragment(lemma_id=1, arabic_text="كِتَابٌ جَدِيدٌ", english_translation="a new book", target_surface="كِتَابٌ"))
    db_session.commit()

    arm, fragment = wf.assign_retest(db_session, 1)

    assert (arm, fragment) == (wf.WORD_CARD_ARM, None)
    row = db_session.query(WordRetestAssignment).one()
    assert (row.arm, row.delivered, row.fragment_id) == (wf.WORD_CARD_ARM, wf.WORD_CARD_ARM, None)


def test_fragment_arm_delivers_least_shown_and_records_it(db_session):
    _lemma(db_session, 1, "كِتَابٌ", "كتاب", "book")
    _miss_in_arm(db_session, 1, wf.FRAGMENT_ARM)
    db_session.add(WordFragment(lemma_id=1, arabic_text="a", english_translation="a", target_surface="a", times_shown=1))
    db_session.add(WordFragment(lemma_id=1, arabic_text="b", english_translation="b", target_surface="b"))
    db_session.commit()

    arm, fragment = wf.assign_retest(db_session, 1)

    assert arm == wf.FRAGMENT_ARM and fragment.arabic_text == "b" and fragment.times_shown == 1
    row = db_session.query(WordRetestAssignment).one()
    assert (row.delivered, row.fragment_id) == (wf.FRAGMENT_ARM, fragment.id)
    # A second delivery for the same miss reuses the row.
    wf.assign_retest(db_session, 1)
    assert db_session.query(WordRetestAssignment).count() == 1


def test_fragment_arm_without_stock_falls_back_and_says_so(db_session):
    _lemma(db_session, 1, "كِتَابٌ", "كتاب", "book")
    _miss_in_arm(db_session, 1, wf.FRAGMENT_ARM)

    arm, fragment = wf.assign_retest(db_session, 1)

    assert arm == wf.FRAGMENT_ARM and fragment is None
    row = db_session.query(WordRetestAssignment).one()
    assert (row.arm, row.delivered) == (wf.FRAGMENT_ARM, wf.WORD_CARD_ARM)


def test_switch_off_returns_word_cards(db_session, monkeypatch):
    _lemma(db_session, 1, "كِتَابٌ", "كتاب", "book")
    _miss_in_arm(db_session, 1, wf.FRAGMENT_ARM)
    monkeypatch.setenv("ALIF_FRAGMENT_RETEST", "0")

    assert wf.assign_retest(db_session, 1) == (wf.WORD_CARD_ARM, None)
    assert db_session.query(WordRetestAssignment).count() == 0


def test_generation_keeps_only_checked_fragments(db_session, monkeypatch):
    _lemma(db_session, 1, "كِتَابٌ", "كتاب", "book")
    _lemma(db_session, 2, "جَدِيدٌ", "جديد", "new", pos="adj")
    _lemma(db_session, 3, "غَرِيبٌ", "غريب", "strange", state="acquiring", pos="adj")
    _miss_in_arm(db_session, 1, wf.FRAGMENT_ARM)

    def completion(**kw):
        assert kw["json_schema"] and kw["task_type"] == "word_fragment_gen"
        return {"words": [{"lemma_id": 1, "fragments": [
            {"arabic": "كِتَابٌ جَدِيدٌ", "english": "a new book"},        # kept
            {"arabic": "كِتَابٌ غَرِيبٌ", "english": "a strange book"},    # companion not known
            {"arabic": "كِتَابٌ", "english": "a book"},                    # one word
            {"arabic": "جَدِيدٌ جَدِيدٌ", "english": "new new"},            # target missing
            {"arabic": "الْكِتَابُ الْجَدِيدُ", "english": "bad translation"},  # judge rejects
        ]}]}

    monkeypatch.setattr("app.services.llm.generate_completion", completion)
    monkeypatch.setattr(
        "app.services.llm.review_sentences_quality",
        lambda sents: [SimpleNamespace(natural=True, translation_correct="bad" not in s["english"],
                                       review_completed=True) for s in sents],
    )

    stats = wf.generate_fragments_for_lemmas([1])

    assert stats["returned"] == 5 and stats["valid"] == 2 and stats["stored"] == 1
    db_session.expire_all()
    row = db_session.query(WordFragment).one()
    assert row.arabic_text == "كِتَابٌ جَدِيدٌ" and row.target_surface == "كِتَابٌ"
    assert row.transliteration


def test_generation_skips_the_word_card_arm(db_session, monkeypatch):
    _lemma(db_session, 1, "كِتَابٌ", "كتاب", "book")
    _miss_in_arm(db_session, 1, wf.WORD_CARD_ARM)
    monkeypatch.setattr(
        "app.services.llm.generate_completion",
        lambda **kw: (_ for _ in ()).throw(AssertionError("no call expected")),
    )
    assert wf.generate_fragments_for_lemmas([1])["targets"] == 0


def test_checkpoint_endpoint_attaches_the_fragment(client, db_session):
    _lemma(db_session, 1, "كِتَابٌ", "كتاب", "book", state="lapsed")
    _miss_in_arm(db_session, 1, wf.FRAGMENT_ARM)
    db_session.add(WordFragment(lemma_id=1, arabic_text="كِتَابٌ جَدِيدٌ", english_translation="a new book", target_surface="كِتَابٌ"))
    db_session.commit()

    card = client.post("/api/review/wrap-up", json={
        "seen_lemma_ids": [], "missed_lemma_ids": [1], "checkpoint": True,
    }).json()["cards"][0]

    assert card["retest_arm"] == wf.FRAGMENT_ARM
    assert card["fragment"]["arabic"] == "كِتَابٌ جَدِيدٌ"
    assert card["extra_support"] is False

    plain = client.post("/api/review/wrap-up", json={
        "seen_lemma_ids": [], "missed_lemma_ids": [1],
    }).json()["cards"][0]
    assert plain["fragment"] is None and plain["retest_arm"] is None
