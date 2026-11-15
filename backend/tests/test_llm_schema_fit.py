"""Shape repair and CLI model routing for structured LLM calls."""
from app.services import llm

GLOSS_SCHEMA = {"type": "object", "properties": {"glosses": {"type": "array"}}}


def test_bare_list_is_wrapped_in_the_schema_object():
    assert llm._fit_schema([{"index": 0}], GLOSS_SCHEMA) == {"glosses": [{"index": 0}]}


def test_objects_and_ambiguous_schemas_are_left_alone():
    assert llm._fit_schema({"glosses": []}, GLOSS_SCHEMA) == {"glosses": []}
    two = {"type": "object", "properties": {"a": {"type": "array"}, "b": {"type": "array"}}}
    assert llm._fit_schema([1], two) == [1]
    assert llm._fit_schema([1], None) == [1]


def test_haiku_alias_can_be_routed_to_another_cli_model(monkeypatch):
    seen = {}

    def fake_cli(**kw):
        seen["model"] = kw["model"]
        return {"ok": True}

    monkeypatch.setenv("ALIF_AUDIT_PROVIDER", "claude")
    monkeypatch.setenv("ALIF_CLAUDE_HAIKU_CLI_MODEL", "sonnet")
    monkeypatch.setattr(llm, "_generate_via_claude_cli", fake_cli)
    monkeypatch.setattr(llm, "claude_cli_temporarily_disabled", lambda: False)
    assert llm.generate_completion("p", model_override="claude_haiku") == {"ok": True}
    assert seen["model"] == "sonnet"


def test_api_fallback_mentions_json_and_wraps_list(monkeypatch):
    sent = {}

    class Msg:
        content = '[{"index": 0, "gloss_en": "x"}]'

    class Resp:
        choices = [type("C", (), {"message": Msg})]

    def fake_completion(**kw):
        sent.update(kw)
        return Resp()

    monkeypatch.setattr(llm.litellm, "completion", fake_completion)
    monkeypatch.setattr(llm, "_get_api_key", lambda cfg: "key")
    model = llm.MODELS[0]["name"]
    out = llm.generate_completion("give glosses", model_override=model, json_schema=GLOSS_SCHEMA)
    assert out == {"glosses": [{"index": 0, "gloss_en": "x"}]}
    assert "json" in sent["messages"][0]["content"].lower()
