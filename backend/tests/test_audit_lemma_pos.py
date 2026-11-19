"""Change planning for scripts/audit_lemma_pos.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from audit_lemma_pos import plan_changes


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
