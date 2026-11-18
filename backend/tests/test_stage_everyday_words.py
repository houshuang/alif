"""Everyday-word staging: targets for unlearned words, no duplicates, no homograph lemmas."""
from datetime import datetime, timezone

from app.models import Lemma, ReadingTarget, UserLemmaKnowledge
from scripts import stage_everyday_words as sew


def _lemma(db, ar, gloss, state=None, canonical=None):
    lemma = Lemma(lemma_ar=ar, lemma_ar_bare=sew.strip_diacritics(ar), gloss_en=gloss, pos="noun",
                  canonical_lemma_id=canonical, gates_completed_at=datetime.now(timezone.utc))
    db.add(lemma)
    db.flush()
    if state:
        db.add(UserLemmaKnowledge(lemma_id=lemma.lemma_id, knowledge_state=state, source="study"))
    return lemma.lemma_id


def test_stage_classifies_and_targets(db_session, monkeypatch):
    monkeypatch.setattr(sew, "WORDS", {"t": [
        ("أَصْفَر", "adj", "yellow"),            # missing: created
        ("نَجَّار", "noun", "carpenter"),         # learned: left alone
        ("بُومَة", "noun", "owl"),               # exists, never met: targeted
        ("قَاضٍ", "noun", "judge"),              # stored with final ya, learned
        ("قُفْل", "noun", "lock"),               # same spelling, other meaning
        ("سَمَكَة", "noun", "fish"),             # variant of a learned canonical
    ]})
    _lemma(db_session, "نَجَّارٌ", "carpenter", "known")
    owl = _lemma(db_session, "بُومَةٌ", "owl")
    _lemma(db_session, "قَاضِي", "judge", "known")
    _lemma(db_session, "قَفَلَ", "to return from a journey", "known")
    fish = _lemma(db_session, "سَمَك", "fish", "known")
    _lemma(db_session, "سَمَكَة", "a fish", canonical=fish)
    db_session.commit()
    monkeypatch.setattr(
        "app.services.lemma_quality.run_quality_gates",
        lambda db, ids, **kw: [setattr(db.get(Lemma, i), "gates_completed_at", datetime.now(timezone.utc)) for i in ids],
    )

    dry = sew.stage(apply=False)
    assert [e["word"] for e in dry["created"]] == ["أَصْفَر"]
    assert db_session.query(ReadingTarget).count() == 0

    report = sew.stage(apply=True)
    assert [e["word"] for e in report["created"]] == ["أَصْفَر"]
    assert [e["lemma_id"] for e in report["targeted_existing"]] == [owl]
    assert sorted(e["word"] for e in report["learned"]) == sorted(["نَجَّار", "قَاضٍ", "سَمَكَة"])
    assert [e["word"] for e in report["sense_conflict"]] == ["قُفْل"]

    db_session.expire_all()
    targets = db_session.query(ReadingTarget).filter_by(program="everyday_core").all()
    yellow = db_session.query(Lemma).filter_by(gloss_en="yellow").one()
    assert {t.lemma_id for t in targets} == {owl, yellow.lemma_id}
    assert yellow.source == "everyday_core"
    states = dict(db_session.query(UserLemmaKnowledge.lemma_id, UserLemmaKnowledge.knowledge_state).all())
    assert states[owl] == "encountered" and states[yellow.lemma_id] == "encountered"

    again = sew.stage(apply=True)
    assert again["created"] == []
    assert db_session.query(ReadingTarget).count() == 2
