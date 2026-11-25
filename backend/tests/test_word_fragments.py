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
import pytest

from app.services import word_fragments as wf


@pytest.fixture(autouse=True)
def _retest_trial_on(monkeypatch):
    """These tests cover the retry-card arms, which are switched off in production."""
    monkeypatch.setattr(wf, "RETEST_AB_ACTIVE", True)


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
    monkeypatch.setattr(wf, "followup_arm", lambda lemma_id, missed_at: wf.SENTENCE_ARM)
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


# ── fragment_followup_v1 ────────────────────────────────────────────────────

def _miss_in_followup_arm(db, lemma_id, arm, hours_from=20):
    for minutes in range(hours_from * 60, hours_from * 60 + 600, 7):
        when = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        if wf.followup_arm(lemma_id, when) == arm:
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


def _due_word_with_sentence(db, lemma_id=1):
    from app.models import SentenceWord

    db.add(Lemma(lemma_id=lemma_id, lemma_ar="كِتَابٌ", lemma_ar_bare="كتاب", gloss_en="book",
                 pos="noun", gates_completed_at=datetime.now(timezone.utc)))
    due = datetime.now(timezone.utc) - timedelta(hours=2)
    db.add(UserLemmaKnowledge(
        lemma_id=lemma_id, knowledge_state="known", source="study", times_seen=10, times_correct=8,
        fsrs_card_json={"due": due.isoformat(), "stability": 2.0, "state": 2, "step": None,
                        "difficulty": 5.0, "last_review": (due - timedelta(days=2)).isoformat(),
                        "card_id": 1},
    ))
    sentence = Sentence(id=500, arabic_text="كتاب", english_translation="book",
                        target_lemma_id=lemma_id, mappings_verified_at=datetime.now(timezone.utc))
    db.add(sentence)
    db.flush()
    db.add(SentenceWord(sentence_id=500, position=0, surface_form="كتاب", lemma_id=lemma_id))
    db.commit()


def test_phrase_arm_followup_replaces_the_sentence_card(db_session):
    from app.services.sentence_selector import build_session

    _due_word_with_sentence(db_session)
    _miss_in_followup_arm(db_session, 1, wf.PHRASE_ARM)
    db_session.add(WordFragment(lemma_id=1, arabic_text="كِتَابٌ جَدِيدٌ", english_translation="a new book", target_surface="كِتَابٌ"))
    db_session.commit()

    result = build_session(db_session, limit=5, log_events=False, allow_intro_mutations=False)

    assert [f["lemma_id"] for f in result["followup_fragments"]] == [1]
    assert result["followup_fragments"][0]["fragment"]["arabic"] == "كِتَابٌ جَدِيدٌ"
    assert 500 not in {item["sentence_id"] for item in result["items"]}


def test_every_miss_gets_a_phrase_followup_since_the_trial_ended():
    when = datetime.now(timezone.utc)
    assert wf.FOLLOWUP_AB_ACTIVE is False
    assert {wf.followup_arm(lemma_id, when) for lemma_id in range(1, 60)} == {wf.PHRASE_ARM}
    assert {wf.followup_trial_arm(lemma_id, when) for lemma_id in range(1, 60)} == {
        wf.PHRASE_ARM, wf.SENTENCE_ARM,
    }


def test_sentence_arm_and_missing_stock_keep_the_sentence_card(db_session, monkeypatch):
    from app.services.sentence_selector import build_session

    monkeypatch.setattr(wf, "FOLLOWUP_AB_ACTIVE", True)
    _due_word_with_sentence(db_session)
    _miss_in_followup_arm(db_session, 1, wf.SENTENCE_ARM)
    db_session.add(WordFragment(lemma_id=1, arabic_text="a", english_translation="a", target_surface="a"))
    db_session.commit()
    result = build_session(db_session, limit=5, log_events=False, allow_intro_mutations=False)
    assert result["followup_fragments"] == []
    assert 500 in {item["sentence_id"] for item in result["items"]}


def test_last_required_success_stays_a_sentence(db_session):
    from app.services.missed_word_recovery import open_misses
    from app.services.sentence_selector import build_session

    _due_word_with_sentence(db_session)
    when = _miss_in_followup_arm(db_session, 1, wf.PHRASE_ARM, hours_from=60)
    db_session.add(WordFragment(lemma_id=1, arabic_text="a", english_translation="a", target_surface="a"))
    for hours in (30, 20):  # two correct phrases since the miss
        db_session.add(ReviewLog(
            lemma_id=1, rating=3, review_mode="fragment", is_acquisition=False,
            reviewed_at=datetime.now(timezone.utc) - timedelta(hours=hours), fsrs_log_json={},
        ))
    db_session.commit()

    miss = open_misses(db_session, datetime.now(timezone.utc))[1]
    assert (miss.successes, miss.remaining) == (1, 1)
    result = build_session(db_session, limit=5, log_events=False, allow_intro_mutations=False)
    assert result["followup_fragments"] == []
    assert 500 in {item["sentence_id"] for item in result["items"]}


def test_session_endpoint_returns_followup_cards_and_accepts_the_answer(client, db_session):
    _due_word_with_sentence(db_session)
    _miss_in_followup_arm(db_session, 1, wf.PHRASE_ARM)
    fragment = WordFragment(lemma_id=1, arabic_text="كِتَابٌ جَدِيدٌ", english_translation="a new book", target_surface="كِتَابٌ")
    db_session.add(fragment)
    db_session.commit()

    session = client.get("/api/review/next-sentences?limit=5&prefetch=true").json()
    card = session["followup_fragment_cards"][0]
    assert card["lemma_id"] == 1 and card["fragment"]["id"] == fragment.id

    response = client.post("/api/review/submit-sentence", json={
        "sentence_id": None, "primary_lemma_id": 1, "comprehension_signal": "understood",
        "missed_lemma_ids": [], "session_id": session["session_id"], "review_mode": "fragment",
        "fragment_id": fragment.id, "parent_card_type": "followup_fragment",
    })
    assert response.status_code == 200
    db_session.expire_all()
    row = db_session.query(ReviewLog).filter(ReviewLog.review_mode == "fragment").one()
    assert (row.lemma_id, row.rating) == (1, 3)
    assert db_session.query(WordFragment).one().times_shown == 1
    # Answered: the next build does not offer the phrase again within the guard.
    again = client.get("/api/review/next-sentences?limit=5&prefetch=true").json()
    assert again["followup_fragment_cards"] == []


def test_generation_serves_the_phrase_followup_arm(db_session, monkeypatch):
    monkeypatch.setattr(wf, "RETEST_AB_ACTIVE", False)
    _lemma(db_session, 1, "كِتَابٌ", "كتاب", "book")
    _lemma(db_session, 2, "جَدِيدٌ", "جديد", "new", pos="adj")
    _miss_in_followup_arm(db_session, 1, wf.PHRASE_ARM, hours_from=1)
    monkeypatch.setattr(
        "app.services.llm.generate_completion",
        lambda **kw: {"words": [{"lemma_id": 1, "fragments": [{"arabic": "كِتَابٌ جَدِيدٌ", "english": "a new book"}]}]},
    )
    monkeypatch.setattr(
        "app.services.llm.review_sentences_quality",
        lambda sents: [SimpleNamespace(natural=True, translation_correct=True, review_completed=True) for _ in sents],
    )
    assert wf.generate_fragments_for_lemmas([1])["stored"] == 1
    assert wf.stock_followup_fragments()["targets"] == 0  # one unshown phrase is in stock
