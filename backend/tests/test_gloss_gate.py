"""The gloss-widening gate (app/services/gloss_gate.py)."""
from app.services import gloss_gate
from app.services import preread_service as ps


def test_rules_refuse_format_noops_idioms_and_lost_meanings():
    rc = gloss_gate.rule_check
    assert rc("to hide, to conceal", "to hide, conceal") == "no_value"
    assert rc("names, nouns", "nouns; names") == "no_value"
    assert rc("next, following", "next, following; (bi-t-tālī) consequently") == "idiom"
    assert rc("vessel, container", "circumstance") == "drops_meaning"
    assert rc("ten", "عشرة ten") == "format"
    assert rc("horn", "century; horn") is None
    # A partly dropped meaning goes to the judge, which is told which.
    assert rc("vessel, container, receptacle", "circumstance; vessel, container") is None
    assert gloss_gate.dropped_senses("vessel, container, receptacle",
                                     "circumstance; vessel, container") == ["receptacle"]
    # Stems count as kept: transform ~ transformation.
    assert gloss_gate.dropped_senses("to transform; transfer",
                                     "converting; transformation; transfer") == []


def test_judge_decides_survivors_and_a_missing_verdict_refuses(db_session, monkeypatch):
    seen = {}

    def fake_completion(**kw):
        seen["prompt"] = kw["prompt"]
        return {"verdicts": [{"index": 1, "approve": True, "reason": "ok"},
                             {"index": 2, "approve": False, "reason": "other_word", "note": "حياة"}]}

    monkeypatch.setattr("app.services.llm.generate_completion", fake_completion)
    out = gloss_gate.review_widenings([
        {"index": 0, "lemma_ar": "أَخْفَى", "old_gloss": "to hide, to conceal", "new_gloss": "to hide, conceal"},
        {"index": 1, "lemma_ar": "قَرْن", "pos": "noun", "old_gloss": "horn", "new_gloss": "century; horn",
         "surface": "القرن", "context": "في القرن الماضي"},
        {"index": 2, "lemma_ar": "حَيّ", "pos": "adj", "old_gloss": "alive", "new_gloss": "life; alive"},
        {"index": 3, "lemma_ar": "رَأْي", "old_gloss": "opinion", "new_gloss": "opinion, view; mind"},
    ])
    assert out[0] == {"approved": False, "reason": "no_value", "stage": "rule"}
    assert out[1]["approved"] is True
    assert out[2]["approved"] is False and out[2]["reason"] == "other_word"
    assert out[3]["approved"] is False and out[3]["stage"] == "error"  # no verdict given
    assert "في القرن الماضي" in seen["prompt"]


def test_judge_failure_approves_nothing(monkeypatch):
    def boom(**kw):
        raise RuntimeError("cli down")

    monkeypatch.setattr("app.services.llm.generate_completion", boom)
    out = gloss_gate.review_widenings([
        {"index": 0, "lemma_ar": "قَرْن", "old_gloss": "horn", "new_gloss": "century; horn"}])
    assert out[0]["approved"] is False


def test_check_senses_marks_broader_verdicts(monkeypatch):
    def fake_completion(**kw):
        if kw.get("task_type") == "gloss_gate":
            return {"verdicts": [{"index": 0, "approve": True, "reason": "ok"}]}
        return {"mismatches": [{"index": 0, "verdict": "broader", "gloss_en": "century; horn"}]}

    monkeypatch.setattr("app.services.llm.generate_completion", fake_completion)
    out = ps.check_senses("في القرن الماضي", [
        {"index": 0, "lemma_ar": "قَرْن", "gloss_en": "horn", "pos": "noun", "forms": ["القرن"]}])
    assert out[0]["verdict"] == "broader" and out[0]["approved"] is True


def test_check_senses_flags_guessed_matches_in_the_prompt(monkeypatch):
    seen = {}

    def fake(**kw):
        seen["prompt"] = kw["prompt"]
        return {"mismatches": []}

    monkeypatch.setattr("app.services.llm.generate_completion", fake)
    ps.check_senses("وزارة", [{"index": 0, "lemma_ar": "زَارَ", "gloss_en": "to visit",
                               "forms": ["وزارة"], "guess": True}])
    assert "زَارَ (?) — to visit [in the pages: وزارة] [guessed match]" in seen["prompt"]
