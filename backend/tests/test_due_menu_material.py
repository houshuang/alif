"""Due-menu generation stocks sentences that review several due words at once."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models import Lemma, UserLemmaKnowledge
from app.services import material_generator as mg

LETTERS = "جحخدذرزسشصضطظعغق"


def _word(db, i, due=True, state="known"):
    bare = LETTERS[i % 16] + LETTERS[(i // 16) % 16] + LETTERS[(i * 7 + 3) % 16] + "م"
    lemma = Lemma(lemma_ar=bare, lemma_ar_bare=bare, gloss_en=f"w{i}", pos="noun",
                  gates_completed_at=datetime.now(timezone.utc))
    db.add(lemma)
    db.flush()
    when = datetime.now(timezone.utc) + timedelta(days=-3 if due else 30)
    db.add(UserLemmaKnowledge(
        lemma_id=lemma.lemma_id, knowledge_state=state, source="study",
        fsrs_card_json=json.dumps({"due": when.isoformat(), "stability": 20.0}),
    ))
    return lemma.lemma_id, bare


def _stub_pipeline(monkeypatch, make_sentences):
    seen = {}

    def generate(priority_words, supporting_words, **kw):
        seen["priority"] = [w["lemma_id"] for w in priority_words]
        seen["support"] = [w["lemma_id"] for w in supporting_words]
        seen["kw"] = kw
        return make_sentences(priority_words)

    written = []
    monkeypatch.setattr("app.services.llm.generate_sentences_due_menu", generate)
    monkeypatch.setattr(
        "app.services.llm.review_sentences_quality",
        lambda sents: [SimpleNamespace(natural="bad" not in s["english"], translation_correct=True,
                                       reason="", review_completed=True) for s in sents],
    )
    monkeypatch.setattr(mg, "write_multi_target_sentence", lambda db, res, mappings: written.append(res))
    return seen, written


def _sentence(words, english="ok"):
    return SimpleNamespace(arabic=" ".join(w["arabic"] for w in words), english=english, transliteration="")


def test_menu_holds_due_words_and_keeps_dense_sentences(db_session, monkeypatch):
    due = dict(_word(db_session, i) for i in range(50))
    safe = dict(_word(db_session, 100 + i, due=False) for i in range(10))
    db_session.commit()
    bare_to_id = {bare: lid for lid, bare in {**due, **safe}.items()}

    def make(priority):
        return [
            _sentence(priority[0:5]),             # five due words: kept
            _sentence(priority[5:7]),             # two due words: too thin
            _sentence(priority[7:12], "bad"),     # fails the quality gate
            _sentence(priority[12:20]),           # eight due words: over the card cap
        ]

    seen, written = _stub_pipeline(monkeypatch, make)
    monkeypatch.setattr(
        mg, "validate_multi_target_sentences_batch",
        lambda db, candidates, lookup: [
            (res, [SimpleNamespace(lemma_id=bare_to_id[t]) for t in res.arabic.split()])
            for res, _found in candidates
        ],
    )

    stats = mg.generate_due_menu_material(max_calls=1)

    assert set(seen["priority"]) == set(due)
    assert set(seen["support"]) <= set(safe)
    assert seen["kw"]["min_priority"] == 4 and seen["kw"]["max_priority"] == 6
    assert stats["calls"] == 1 and stats["returned"] == 4
    assert stats["valid"] == 3 and stats["quality_passed"] == 2
    assert stats["generated"] == 1 and stats["due_words_covered"] == 5
    assert len(written) == 1
    assert written[0].primary_target_lemma_id in due
    assert written[0].target_lemma_ids == [written[0].primary_target_lemma_id]


def test_dry_run_writes_nothing(db_session, monkeypatch):
    due = dict(_word(db_session, i) for i in range(45))
    db_session.commit()
    bare_to_id = {bare: lid for lid, bare in due.items()}
    seen, written = _stub_pipeline(monkeypatch, lambda priority: [_sentence(priority[0:4])])
    monkeypatch.setattr(
        mg, "validate_multi_target_sentences_batch",
        lambda db, candidates, lookup: [
            (res, [SimpleNamespace(lemma_id=bare_to_id[t]) for t in res.arabic.split()])
            for res, _found in candidates
        ],
    )
    stats = mg.generate_due_menu_material(max_calls=1, dry_run=True)
    assert stats["generated"] == 1 and written == []


def test_no_call_when_pool_is_thin_or_stock_is_full(db_session, monkeypatch):
    for i in range(45):
        _word(db_session, i)
    db_session.commit()
    seen, _ = _stub_pipeline(monkeypatch, lambda priority: [])

    monkeypatch.setattr(mg, "DUE_MENU_STOCK_TARGET", 0)
    assert mg.generate_due_menu_material()["calls"] == 0
    monkeypatch.setattr(mg, "DUE_MENU_STOCK_TARGET", 60)
    monkeypatch.setattr(mg, "DUE_MENU_MIN_POOL", 100)
    assert mg.generate_due_menu_material()["calls"] == 0
    assert seen == {}


def _miss(db, lemma_id, hours_ago):
    from app.models import ReviewLog

    db.add(ReviewLog(
        lemma_id=lemma_id, rating=1, review_mode="reading", is_acquisition=False,
        reviewed_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago), fsrs_log_json={},
    ))


def test_recently_missed_words_lead_the_menu(db_session, monkeypatch):
    due = dict(_word(db_session, i) for i in range(50))
    missed = sorted(due)[40:43]
    for offset, lemma_id in enumerate(missed):
        _miss(db_session, lemma_id, hours_ago=5 + offset)
    db_session.commit()
    seen, _ = _stub_pipeline(monkeypatch, lambda priority: [])

    stats = mg.generate_due_menu_material(max_calls=1)

    assert seen["priority"][:3] == missed
    assert [w["lemma_id"] for w in seen["kw"]["recently_missed_words"]] == missed
    assert stats["missed_uncovered"] == 3


def test_full_stock_still_generates_for_enough_missed_words(db_session, monkeypatch):
    due = dict(_word(db_session, i) for i in range(50))
    for lemma_id in sorted(due)[:mg.DUE_MENU_MISSED_MIN_FOR_EXTRA_CALL]:
        _miss(db_session, lemma_id, hours_ago=5)
    db_session.commit()
    _stub_pipeline(monkeypatch, lambda priority: [])
    monkeypatch.setattr(mg, "DUE_MENU_STOCK_TARGET", 0)

    assert mg.generate_due_menu_material()["calls"] == 1
