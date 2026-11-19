"""Part-of-speech check: change planning and the import gate."""
from app.models import Lemma
from app.services import lemma_pos_check
from app.services.lemma_pos_check import check_new_lemmas, plan_changes


def _p(pos, proposed, conflict=False):
    return {"lemma_id": 1, "lemma_ar": "x", "gloss_en": "g", "pos": pos,
            "proposed_pos": proposed, "conflict": conflict, "note": ""}


def test_open_class_disagreement_is_corrected():
    changes, left = plan_changes([_p("noun", "verb"), _p(None, "adj")])
    assert [c["new_pos"] for c in changes] == ["verb", "adj"]
    assert not left


def test_label_spelling_is_tidied_without_a_disagreement():
    changes, left = plan_changes([_p("adjective", "adj")])
    assert changes[0]["new_pos"] == "adj" and changes[0]["kind"] == "label_spelling"
    assert not left


def test_conflict_and_function_classes_are_left_alone():
    changes, left = plan_changes([
        _p("verb", "noun", conflict=True),
        _p("noun", "prep"),
        _p("particle", "adv"),
        _p("noun", "noun"),
    ])
    assert not changes
    assert [x["why"] for x in left] == [
        "form_gloss_conflict", "outside_open_classes", "outside_open_classes",
    ]


def _lemma(db, ar, gloss, pos):
    lemma = Lemma(lemma_ar=ar, lemma_ar_bare=ar, gloss_en=gloss, pos=pos, source="test")
    db.add(lemma)
    db.flush()
    return lemma


def test_gate_corrects_labels_and_logs_conflicts(db_session, monkeypatch):
    verb = _lemma(db_session, "تسلق", "to climb", "noun")
    odd = _lemma(db_session, "وصل", "to arrive", "verb")
    name = _lemma(db_session, "سليم", "Saleem", "noun_prop")
    db_session.commit()

    def fake(rows):
        assert name.lemma_id not in {r["lemma_id"] for r in rows}
        return [
            {**r, "proposed_pos": "verb" if r["lemma_id"] == verb.lemma_id else "noun",
             "conflict": r["lemma_id"] == odd.lemma_id, "note": ""}
            for r in rows
        ]

    monkeypatch.setattr(lemma_pos_check, "audit_rows", fake)
    summary = check_new_lemmas(db_session, [verb.lemma_id, odd.lemma_id, name.lemma_id])

    assert summary == {"checked": 2, "changed": 1, "conflicts": 1}
    assert db_session.get(Lemma, verb.lemma_id).pos == "verb"
    assert db_session.get(Lemma, odd.lemma_id).pos == "verb"
